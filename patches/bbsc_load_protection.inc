// A local co-op guest's transition. No character flags, HP, packet or remote actor is written.
struct BbscLoadPadCallback {
    u64 generation{};
    s64 ms{};
    u32 count{};
    s32 control_245{-1}, pad_194{-1};
    bool context_matched{}, actor_matched{}, recorded{};
};

// Process totals are incremented before protection-ticket checks. Tags 0..2
// are the existing damage hooks (an independent callback control), 3 the
// original Pad branch, 4 the common branch head and 5 the alternate read.
// Tag 6 witnesses the alternate vector update and does not change these counters.
std::array<std::atomic<u64>, 6> bbsc_load_hook_hits{};
constexpr s64 BbscLoadPadFreshMs = 250;

struct BbscLoadProtectionState {
    u64 serial{}, global{}, role_epoch{}, room{}, placed_generation{}, pad_generation{};
    u64 placed_actor{}, pad_actor{}, stable_actor{};
    u32 owner{}, placed_map{}, rejected{};
    s32 slot{-1};
    s64 started{}, pad_ms{}, placed_map_ms{}, stable_since{}, stop_ms{};
    BbscLoadPadCallback pad_callback;
    std::array<u64, 6> hook_baseline{};
    std::array<u32, 3> input_entries{}, input_local{};
    BbscLoadPadCallback input_probe;
    s32 input_stage{-1};
    u32 ready_logs{}, stable_starts{}, stable_resets{};
    s64 ready_sample_ms{};
    std::array<s32, 25> ready_log_key{};
    bool pad_alternate{}, saw_session{}, leaving{}, stop_logged{};
};

std::mutex bbsc_load_protection_mutex;
BbscLoadProtectionState bbsc_load_protection;
u64 bbsc_load_protection_serial = 0;
std::atomic<bool> bbsc_load_protection_armed{false};
std::atomic<bool> bbsc_load_protection_hooks{false};

u64 BbscLoadProtectionRoleEpoch() {
    std::scoped_lock lock{bbsc_active_save_mutex};
    return bbsc_active_save_generation;
}

BbscLoadProtectionState ReadBbscLoadProtection() {
    if (!bbsc_load_protection_armed.load(std::memory_order_acquire)) return {};
    std::scoped_lock lock{bbsc_load_protection_mutex};
    return bbsc_load_protection;
}

bool BbscLoadProtectionSameRole(const BbscLoadProtectionState& scope, u64 global) {
    return scope.serial && scope.global == global && ReadablePointer(global, 0x1593) &&
           ReadValue<s32>(global, 0x1490) == scope.slot &&
           BbscLoadProtectionRoleEpoch() == scope.role_epoch;
}

bool BbscLoadProtectionRoomChanged(const BbscLoadProtectionState& scope, u64 room, u32 owner) {
    // Temporary loss of metadata during reload is not evidence of a new room.
    return (scope.room && room && scope.room != room) ||
           (scope.owner && owner && scope.owner != owner);
}

u64 ArmBbscLoadProtection(BbscLoadProtectionKind kind) {
    if (!bbsc_load_protection_hooks || !bbsc_active_save_hooks) return 0;
    const s32 session = GetSessionState();
    const u64 global = GetGlobalState();
    if (!ReadablePointer(global, 0x1593) ||
        (kind == BbscLoadProtectionKind::Join
             ? session == 3 || (session == 6 && GetLocalPlayerType() != 1)
             : session != 6 || GetLocalPlayerType() != 1 ||
                   ReadValue<u8>(global, 0x1592) != 0)) return 0;
    const s32 slot = ReadValue<s32>(global, 0x1490);
    if (slot < 0 || slot > 9) return 0;
    const u64 role = BbscLoadProtectionRoleEpoch();
    // Before the accepted summon changes native session state the relay can
    // still hold a prior selection. Bind after we enter the guest session.
    const u64 room = session == 6 ? BbscMod::RelayAuthRoom() : 0;
    const u32 owner = session == 6 ? BbscRelayHostForPackets() : 0;
    u64 serial = 0;
    {
        std::scoped_lock lock{bbsc_load_protection_mutex};
        auto& scope = bbsc_load_protection;
        if (scope.serial && scope.global == global && scope.slot == slot &&
            scope.role_epoch == role && !BbscLoadProtectionRoomChanged(scope, room, owner)) {
            if (scope.leaving) return 0;
        } else {
            scope = {};
            scope.global = global;
            scope.role_epoch = role;
            scope.slot = slot;
            scope.room = room;
            scope.owner = owner;
            scope.started = SteadyNowMs();
        }
        scope.serial = serial = ++bbsc_load_protection_serial;
        scope.saw_session |= session >= 4 && session <= 6;
        scope.placed_map = 0;
        scope.pad_ms = scope.placed_map_ms = scope.stable_since = 0;
        scope.pad_callback = {};
        for (size_t i = 0; i < scope.hook_baseline.size(); ++i)
            scope.hook_baseline[i] = bbsc_load_hook_hits[i].load(std::memory_order_relaxed);
        scope.input_entries = scope.input_local = {};
        scope.input_probe = {};
        scope.input_stage = -1;
        // A new ticket gets its own diagnosis; the original timeout still applies.
        scope.ready_sample_ms = 0;
        scope.ready_log_key = {};
        scope.ready_logs = scope.stable_starts = scope.stable_resets = 0;
        scope.stop_logged = false;
        scope.rejected = 0;
        bbsc_load_protection_armed.store(true, std::memory_order_release);
    }
    constexpr std::array Names{"join", "follow", "respawn"};
    LOG_INFO(Debug, "BBSC load protection: armed {} (transition {})", Names[static_cast<u32>(kind)],
             serial);
    ArmBbscDisplayGuest(serial);
    return serial;
}

u64 BbscLoadProtectionTicket() {
    return ReadBbscLoadProtection().serial;
}

void MarkBbscLoadProtectionPlaced(u64 ticket, u32 map, u64 actor, u64 generation) {
    if (!ticket || !actor || !IsUsablePackedMap(map) || map != GetCurrentPackedMap() ||
        actor != GetLocalPlayerCharacter()) return;
    std::scoped_lock lock{bbsc_load_protection_mutex};
    auto& scope = bbsc_load_protection;
    if (scope.serial != ticket || scope.leaving ||
        generation != bbsc_character_reload_generation.load()) return;
    scope.placed_map = map;
    scope.placed_actor = actor;
    scope.placed_generation = generation;
    scope.pad_ms = scope.placed_map_ms = scope.stable_since = 0;
}

void MarkBbscLoadProtectionPlaced(u64 ticket, u32 map) {
    if (ticket) MarkBbscLoadProtectionPlaced(ticket, map, GetLocalPlayerCharacter(),
                                            bbsc_character_reload_generation.load());
}

bool BbscLoadProtectionContextMatches(const BbscLoadProtectionState& scope, u64 room, u32 owner) {
    return GetSessionState() != 3 && BbscLoadProtectionSameRole(scope, GetGlobalState()) &&
           !BbscLoadProtectionRoomChanged(scope, room, owner);
}

bool BbscLoadPadMapFresh(s64 checked, s64 now) {
    return checked && now >= checked && now - checked <= BbscLoadPadFreshMs;
}

BbscFollowLanding MarkBbscFollowNativeLanding(const BbscFollowLoadProtection& request, u32 map,
                                              u64 actor, u64 generation) {
    const auto scope = ReadBbscLoadProtection();
    // Retry only a missing transform in this exact landing, and never extend
    // the existing protection deadline.
    if (!request.ticket || request.ticket != scope.serial || scope.leaving ||
        SteadyNowMs() - scope.started >= BbscLoadCoverLimitMs ||
        generation != request.reload_before + 1 ||
        generation != bbsc_character_reload_generation.load() ||
        !BbscLoadProtectionContextMatches(scope, BbscMod::RelayAuthRoom(), BbscRelayHostForPackets()) ||
        !IsUsablePackedMap(map) || map != GetCurrentPackedMap() ||
        !actor || actor != GetLocalPlayerCharacter()) return BbscFollowLanding::Done;
    if (!ReadCharacterTransform(actor)) return BbscFollowLanding::Retry;
    MarkBbscLoadProtectionPlaced(request.ticket, map, actor, generation);
    return BbscFollowLanding::Done;
}

struct BbscLoadHostTask {
    s32 mode{-1}, cur{-1}, next{-1}, exit{-1};
    bool matched{};
};

bool BbscLoadProtectionPeer(u64 chr, bool ready_host, BbscLoadHostTask* observation = nullptr) {
    if (!ReadablePointer(chr, 0x400) || !IsValidMemberId(chr + 0x3D8)) return false;
    const s32 type = GetCharacterType(chr);
    if (ready_host ? type != 0 : (type != 0 && type != 1 && !IsInvaderType(type))) return false;
    using Equals = bool PS4_SYSV_ABI (*)(u64, u64);
    const auto equals = reinterpret_cast<Equals>(image_base + MemberIdEqualsOffset);
    for (const u64 task : GetMultiPlayerInsTasks()) {
        const auto mode = ReadValue<u8>(task, 0xE8);
        if ((ready_host ? mode != 2 : mode != 1 && mode != 2) ||
            !IsValidMemberId(task + 0xC8)) continue;
        const bool exit = (ReadValue<u8>(task, 0xED) & 2) != 0;
        if (ready_host) {
            // Create advances to 6; at 0x01E50830 the task can remain there
            // until exit. Require a settled task and the renderable, inserted actor.
            const s32 state = ReadValue<s32>(task, 0x50);
            const s32 next = ReadValue<s32>(task, 0x54);
            const bool settled = (state == 6 || state == 7) && state == next && !exit;
            if ((!settled && !observation) || !equals(chr + 0x3D8, task + 0xC8)) continue;
            if (observation) *observation = {mode, state, next, exit, true};
            if (settled) return true;
        } else if (!exit && equals(chr + 0x3D8, task + 0xC8)) {
            return true;
        }
    }
    return false;
}

struct BbscLoadVisual {
    s32 permission{-1}, resource_state{-1}, resource_gate{-1}, model_enabled{-1};
    float opacity{-1.0f};
    bool Ready() const {
        return permission == 1 && resource_state == 4 && resource_gate == 1 &&
               model_enabled == 1 && std::isfinite(opacity) && opacity > 0.0f;
    }
    s32 ReasonKey() const {
        // Preserve unknown vs false and the native resource state; ignore
        // positive opacity changes that leave the gate unchanged.
        return (permission + 1) | ((resource_gate + 1) << 2) |
               ((model_enabled + 1) << 4) |
               ((std::isfinite(opacity) && opacity > 0.0f) ? 1 << 6 : 0);
    }
};

BbscLoadVisual ReadBbscLoadVisual(u64 chr) {
    BbscLoadVisual out;
    if (!ReadablePointer(chr, 0x1E6)) return out;
    // 1.09 0x018C9D30 combines SetDrawEnable with the resource's
    // state and dependency flags, then applies the result to the model.
    out.permission = (ReadValue<u8>(chr, 0x1E2) & 0x10) != 0;
    const u64 resource = ReadValue<u64>(chr, 0x18);
    if (ReadablePointer(resource, 0x38)) {
        out.resource_state = ReadValue<s32>(resource, 0x10);
        const u8 flags = ReadValue<u8>(resource, 0x20);
        const u64 dependency = ReadValue<u64>(resource, 0x30);
        out.resource_gate = !(flags & 1) &&
            (!(flags & 2) || (ReadablePointer(dependency, 0x19) &&
                              (ReadValue<u8>(dependency, 0x18) & 1)));
    }
    const u64 model_owner = ReadValue<u64>(chr, 0x48);
    const u64 model = ReadablePointer(model_owner, 0x20) ? ReadValue<u64>(model_owner, 0x18) : 0;
    if (ReadablePointer(model, 0x211)) {
        out.model_enabled = (ReadValue<u8>(model, 0x210) & 1) != 0;
        // 0x018C005F..0x018C00A9 applies the composed opacity to lane 3.
        out.opacity = ReadValue<float>(model, 0x18C);
    }
    return out;
}

struct BbscLoadScreen {
    s32 menu{-1}, request{-1};
    float alpha{-1.0f}, remaining{};
    bool plate_readable{}, cover{}, curtain{};
    bool Clear() const {
        // Native NowLoading.Close clears the request and writes state 3.
        return !cover && !curtain && (menu == 0 || menu == 3) && request == 0 &&
               plate_readable && alpha == 0.0f && remaining <= 0.0f;
    }
};

BbscLoadScreen ReadBbscLoadScreen() {
    BbscLoadScreen out;
    out.cover = bbsc_load_cover.active;
    out.curtain = BbscCurtainActive();
    const u64 plate = BbscFadePlate();
    const u64 menu = ReadValue<u64>(image_base + MenuManPointerOffset, 0);
    if (ReadablePointer(menu, 0x808)) out.menu = ReadValue<s32>(menu, 0x804);
    if (HasMemoryAccess(image_base + 0x0556286B, 1, MemoryProt::CpuRead))
        out.request = ReadValue<u8>(image_base + 0x0556286B, 0);
    if (plate) {
        out.plate_readable = true;
        out.alpha = ReadValue<float>(plate, 0x18);
        out.remaining = ReadValue<float>(plate, 0x38);
    }
    return out;
}

void TickBbscLoadProtection() {
    const auto scope = ReadBbscLoadProtection();
    if (!scope.serial) return;
    const s64 now = SteadyNowMs();
    const u64 global = GetGlobalState();
    const u64 room = BbscMod::RelayAuthRoom();
    const u32 owner = BbscRelayHostForPackets();
    const s32 session = GetSessionState();
    const bool has_global = ReadablePointer(global, 0x1593);
    const bool changed = (has_global && (global != scope.global ||
                         ReadValue<s32>(global, 0x1490) != scope.slot)) ||
                         BbscLoadProtectionRoleEpoch() != scope.role_epoch ||
                         BbscLoadProtectionRoomChanged(scope, room, owner);
    const bool same = has_global && !changed;
    const s32 native_load = has_global ? ReadValue<u8>(global, 0x08) : -1;
    const bool load_finished = same && native_load == 0;
    const auto screen = ReadBbscLoadScreen();
    const bool screen_clear = load_finished && screen.Clear();
    const bool own_world = load_finished && ReadValue<u8>(global, 0x1592) != 0;
    const bool leaving = scope.leaving || session == 7 ||
                         now - scope.started >= BbscLoadCoverLimitMs;
    const u64 actor = GetLocalPlayerCharacter();
    const auto generation = bbsc_character_reload_generation.load();
    const u64 control = ReadValue<u64>(actor, 0x58);
    const s32 role = GetLocalPlayerType();
    constexpr u32 ReadyLogLimit = 12;
    const bool sample = scope.ready_logs < ReadyLogLimit &&
                        (!scope.ready_sample_ms || now - scope.ready_sample_ms >= 1'000);
    const bool stop_due = leaving && load_finished && (session == 4 || session == 6) &&
                          (!scope.stop_ms || now - scope.stop_ms >= 5'000);
    const bool collect = sample || (!scope.stop_logged && stop_due);
    std::array<u64, 6> hits{}, window_hits{};
    s32 selected = 0;
    s64 update_rva = -1;
    if (collect) {
        for (size_t i = 0; i < hits.size(); ++i) {
            hits[i] = bbsc_load_hook_hits[i].load(std::memory_order_relaxed);
            window_hits[i] = hits[i] - scope.hook_baseline[i];
        }
        // The native dispatch selects control+0x280, otherwise +0x80; its
        // +0x48 virtual method is the Pad function only for this implementation.
        if (ReadablePointer(control, 0x288)) {
            u64 manipulator = ReadValue<u64>(control, 0x280);
            selected = manipulator ? 2 : 1;
            if (!manipulator) manipulator = ReadValue<u64>(control, 0x80);
            const u64 vtable = ReadValue<u64>(manipulator, 0);
            const u64 method = ReadValue<u64>(vtable, 0x48);
            if (IsEbootCodeAddress(method)) update_rva = static_cast<s64>(method - image_base);
            if (!manipulator) selected = 0;
        }
    }
    const bool room_ok = owner && (!BbscMod::RelayAuthPrivate() ||
                                  (room && owner != BbscMod::RelayAuthOwnId()));
    const bool placed = scope.placed_map != 0;
    const bool placed_actor = actor && scope.placed_actor == actor;
    const bool placed_map = placed && scope.placed_map == GetCurrentPackedMap();
    const bool placed_generation = placed && scope.placed_generation == generation;
    const bool confirm_pad_map = load_finished && placed_actor && placed_map && placed_generation && !leaving;
    const bool pad_actor = actor && scope.pad_ms && scope.pad_actor == actor;
    const bool pad_generation = scope.pad_ms && scope.pad_generation == generation;
    const s64 pad_age = scope.pad_ms ? now - scope.pad_ms : -1;
    const bool pad_fresh = pad_age >= 0 && pad_age <= BbscLoadPadFreshMs &&
                           (!scope.pad_alternate || BbscLoadPadMapFresh(scope.placed_map_ms, now));
    const auto& callback = scope.pad_callback;
    const s64 callback_age = callback.count ? now - callback.ms : -1;
    const bool callback_reload = callback.count && callback.generation == generation;
    const s32 death_latch = ReadablePointer(control, 0x116) ?
                            ReadValue<u16>(control, 0x114) & 1 : -1;
    const bool alive = GetCharacterHp(actor) > 0;
    const auto local_visual = load_finished ? ReadBbscLoadVisual(actor) : BbscLoadVisual{};
    const bool local_ready = screen_clear && !leaving && !own_world && session == 6 && room_ok &&
        role == 1 && placed && placed_actor && placed_map && placed_generation &&
        !bbsc_spectate_active && pad_actor && pad_generation && pad_fresh &&
        death_latch == 0 && alive && local_visual.Ready();
    u64 host = 0;
    BbscLoadVisual host_visual;
    BbscLoadHostTask task;
    bool host_transform = false, host_task = false;
    if (local_ready || (collect && load_finished && (session == 6 || stop_due))) {
        host = BbscRelayNativeHost();
        if (host) {
            host_visual = ReadBbscLoadVisual(host);
            host_transform = ReadCharacterTransform(host).has_value();
            if (collect || (host_visual.Ready() && host_transform))
                host_task = BbscLoadProtectionPeer(host, true, collect ? &task : nullptr);
        }
    }
    const bool ready = local_ready && host && host_visual.Ready() && host_transform && host_task;
    const bool display_ready=UpdateBbscDisplayGuest(scope.serial,generation,scope.role_epoch,actor,
        scope.placed_map,ready && scope.stable_actor==actor && scope.stable_since && now-scope.stable_since>=200);

    bool stop = false;
    u32 snapshot = 0, stable_starts = 0, stable_resets = 0;
    bool stable = false;
    std::string_view finished;
    {
        std::scoped_lock lock{bbsc_load_protection_mutex};
        auto& current = bbsc_load_protection;
        if (current.serial != scope.serial) return;
        // Only the game tick walks the map table. Discard alternate samples
        // if placement no longer matches or the previous confirmation expired.
        if (current.placed_map == scope.placed_map && current.placed_actor == scope.placed_actor &&
            current.placed_generation == scope.placed_generation) {
            if (current.pad_alternate && (!confirm_pad_map || !BbscLoadPadMapFresh(current.placed_map_ms, now)))
                current.pad_ms = 0;
            current.placed_map_ms = confirm_pad_map ? now : 0;
        }
        if (changed || session == 3 || (session == 6 && IsInvaderType(role))) {
            finished = "context changed";
        } else if (own_world && screen_clear && session == 0 && (leaving || current.saw_session)) {
            finished = "back in own world";
        } else {
            if (session == 6) {
                if (room) current.room = room;
                if (owner) current.owner = owner;
            }
            current.saw_session |= session >= 4 && session <= 6;
            current.leaving |= leaving;
            if (!ready || current.stable_actor != actor) {
                if (current.stable_since) ++current.stable_resets;
                current.stable_since = 0;
                current.stable_actor = actor;
            }
            if (ready && !current.leaving) {
                if (!current.stable_since) {
                    current.stable_since = now;
                    ++current.stable_starts;
                }
                else if (now - current.stable_since >= 200 && display_ready) finished = "ready";
            }
            if (current.leaving && load_finished && (session == 4 || session == 6) &&
                (!current.stop_ms || now - current.stop_ms >= 5'000)) {
                current.stop_ms = now;
                stop = true;
            }
        }
        if (collect) {
            stable = current.stable_since != 0;
            stable_starts = current.stable_starts;
            stable_resets = current.stable_resets;
        }
        if (sample) {
            // Deduplicate by the gate results and discrete native states, not
            // elapsed ages or fade/opacity values that change every frame.
            const std::array gates{same, !leaving, !own_world, room_ok,
                scope.room != 0, scope.owner != 0, room != 0, owner != 0,
                placed, placed_actor, placed_map, placed_generation, !bbsc_spectate_active,
                pad_actor, pad_generation, pad_fresh, callback.count != 0, alive,
                screen.plate_readable, screen.alpha == 0.0f, screen.remaining <= 0.0f,
                !screen.cover, !screen.curtain, local_visual.Ready(), host != 0,
                host_visual.Ready(), host_transform, host_task, task.matched};
            s32 blocked = 0;
            for (size_t i = 0; i < gates.size(); ++i)
                if (!gates[i]) blocked |= 1 << i;
            const s32 callback_key = callback.context_matched | (callback.actor_matched << 1) |
                                     (callback_reload << 2) | (callback.recorded << 3);
            s32 path_key = 0;
            for (size_t i = 0; i < scope.input_entries.size(); ++i) {
                path_key |= (window_hits[i + 3] != 0) << i;
                path_key |= (scope.input_entries[i] != 0) << (i + 3);
                path_key |= (scope.input_local[i] != 0) << (i + 6);
            }
            for (size_t i = 0; i < 3; ++i)
                path_key |= (window_hits[i] != 0) << (i + 9);
            const auto& probe = scope.input_probe;
            const s64 probe_age = probe.count ? now - probe.ms : -1;
            // 0 = no observation, 1 = fresh, 2 = stale. Exact age and raw
            // counts must not turn an unchanged probe into a new log reason.
            const s32 probe_age_class = !probe.count ? 0 :
                (probe_age >= 0 && probe_age <= 250 ? 1 : 2);
            const s32 probe_key = probe.context_matched | (probe.actor_matched << 1) |
                ((probe.count && probe.generation == generation) << 2) | (probe_age_class << 3);
            const std::array key{blocked, native_load, screen.menu, screen.request, session, role,
                callback.control_245, callback.pad_194, callback_key,
                death_latch, task.mode, task.cur, task.next,
                task.exit, static_cast<s32>(stable_starts), static_cast<s32>(stable_resets),
                local_visual.ReasonKey() | (host_visual.ReasonKey() << 8),
                (local_visual.resource_state == 4) | ((host_visual.resource_state == 4) << 1),
                path_key, selected, static_cast<s32>(update_rva),
                scope.input_stage, probe_key, probe.control_245, probe.pad_194};
            current.ready_sample_ms = now;
            if (!current.ready_logs || current.ready_log_key != key) {
                current.ready_log_key = key;
                snapshot = ++current.ready_logs;
            }
        }
        if (!finished.empty()) {
            current = {};
            bbsc_load_protection_armed.store(false, std::memory_order_release);
        }
    }
    const auto log_readiness = [&](std::string_view label) {
        LOG_INFO(Debug, "BBSC load protection: readiness {} transition {} "
                 "context[same={} session={} role={} own={} leaving={} room={}/{} owner={}/{} auth={}] "
                 "placement[set={} actor={} map={} reload={}] "
                 "screen[load={} request={} menu={} fade={} alpha={} remaining={} cover={} curtain={}] "
                 "pad_cb[count={} age_ms={} context={} actor={} reload={} control245={} pad194={} recorded={}] "
                 "pad_ready[sample={} actor={} reload={} age_ms={} fresh={} death={} alive={} spectate={}] "
                 "pad_paths[raw_normal={} raw_common={} raw_alternate={} "
                 "window_normal={} window_common={} window_alternate={} "
                 "ticket_normal={} ticket_common={} ticket_alternate={} "
                 "local_normal={} local_common={} local_alternate={} "
                 "control_apply={} control_send={} control_delta={} selected={} update_rva={:#x}] "
                 "pad_probe[stage={} age_ms={} context={} actor={} reload={} control245={} pad194={}] "
                 "local[draw={} resource={} gate={} model={} opacity={}] "
                 "host[unique={} draw={} resource={} gate={} model={} opacity={} transform={} "
                 "match={} mode={} cur={} next={} exit={} task_ready={}] stability[active={} starts={} resets={}]",
                 label, scope.serial,
                 same, session, role, own_world, leaving, scope.room != 0, room != 0,
                 scope.owner != 0, owner != 0, room_ok,
                 placed, placed_actor, placed_map, placed_generation,
                 native_load, screen.request, screen.menu, screen.plate_readable, screen.alpha,
                 screen.remaining, screen.cover, screen.curtain,
                 callback.count, callback_age, callback.context_matched, callback.actor_matched,
                 callback_reload, callback.control_245, callback.pad_194, callback.recorded,
                 scope.pad_ms != 0, pad_actor, pad_generation, pad_age, pad_fresh,
                 death_latch, alive, bbsc_spectate_active,
                 hits[3], hits[4], hits[5], window_hits[3], window_hits[4], window_hits[5],
                 scope.input_entries[0], scope.input_entries[1], scope.input_entries[2],
                 scope.input_local[0], scope.input_local[1], scope.input_local[2],
                 window_hits[0], window_hits[1], window_hits[2], selected, update_rva,
                 scope.input_stage, scope.input_probe.count ? now - scope.input_probe.ms : -1,
                 scope.input_probe.context_matched, scope.input_probe.actor_matched,
                 scope.input_probe.count && scope.input_probe.generation == generation,
                 scope.input_probe.control_245, scope.input_probe.pad_194,
                 local_visual.permission, local_visual.resource_state, local_visual.resource_gate,
                 local_visual.model_enabled, local_visual.opacity, host != 0, host_visual.permission,
                 host_visual.resource_state, host_visual.resource_gate, host_visual.model_enabled,
                 host_visual.opacity, host_transform, task.matched, task.mode, task.cur, task.next,
                 task.exit, host_task, stable, stable_starts, stable_resets);
    };
    if (snapshot) log_readiness(std::to_string(snapshot) + "/" + std::to_string(ReadyLogLimit));
    if (!finished.empty()) {
        LOG_INFO(Debug, "BBSC load protection: {} (transition {})", finished, scope.serial);
    }
    // StopRequest transitions guest state 4/6 to 7. Let the existing guest
    // session-end and stranded recovery paths run the return and save handling.
    if (stop && GetSessionState() == session) {
        const u64 manager = ReadValue<u64>(image_base + MatchingStatePointerOffset, 0);
        if (BbscLoadProtectionTicket() == scope.serial &&
            ReadablePointer(manager, 0x128) && BbscLoadProtectionSameRole(scope, GetGlobalState()) &&
            ReadValue<u8>(global, 0x08) == 0 && IsSprjLuaEventManReady()) {
            bool first = false;
            {
                std::scoped_lock lock{bbsc_load_protection_mutex};
                auto& current = bbsc_load_protection;
                if (current.serial == scope.serial && !current.stop_logged) {
                    current.stop_logged = true;
                    first = true;
                }
            }
            if (first) log_readiness("before-stop");
            using Stop = u8 PS4_SYSV_ABI (*)(u64);
            const auto result = reinterpret_cast<Stop>(image_base + MultiPlayStopRequestOffset)(manager);
            LOG_WARNING(Debug, "BBSC load protection: leaving unready guest (transition {}, Stop -> {})",
                        scope.serial, result);
        }
    }
}

void PS4_SYSV_ABI BbscLoadProtectionHook(u64 tag, const GuestRegisterSnapshot* registers) {
    if (!registers) return;
    RecordBbscHostReadyPad(tag,registers);
    if (tag < bbsc_load_hook_hits.size())
        bbsc_load_hook_hits[tag].fetch_add(1, std::memory_order_relaxed);
    const auto scope = ReadBbscLoadProtection();
    if (!scope.serial) return;
    const u64 room = BbscMod::RelayAuthRoom();
    const u32 owner = BbscRelayHostForPackets();
    const bool context_matched = BbscLoadProtectionContextMatches(scope, room, owner);
    const u64 local = context_matched ? GetLocalPlayerCharacter() : 0;
    if (tag >= 3 && tag <= 6) {
        const bool alternate_update = tag == 6;
        const auto generation = bbsc_character_reload_generation.load();
        const u64 pad = registers->r12;
        const bool pad_readable = ReadablePointer(pad, 0x195);
        const u64 pad_actor = pad_readable ? ReadValue<u64>(pad, 0x88) : 0;
        const bool actor_matched = local && pad_actor == local;
        const u64 control = ReadValue<u64>(pad_actor, 0x58);
        const s32 pad_194 = pad_readable ? ReadValue<u8>(pad, 0x194) : -1;
        const s32 control_245 = ReadablePointer(control, 0x246) ? ReadValue<u8>(control, 0x245) : -1;
        std::scoped_lock lock{bbsc_load_protection_mutex};
        auto& current = bbsc_load_protection;
        if (current.serial != scope.serial) return;
        const s64 now = SteadyNowMs();
        if (!alternate_update) {
            const auto index = static_cast<size_t>(tag - 3);
            ++current.input_entries[index];
            if (actor_matched) ++current.input_local[index];
            current.input_stage = static_cast<s32>(tag);
            current.input_probe = {generation, now, current.input_probe.count + 1, control_245, pad_194,
                                   context_matched, actor_matched, false};
            if (tag != 3) return; // Common head and early alternate read remain diagnostic only.
        }
        // The two successors of 0x015267B3..0x015267C6 use complementary predicates.
        // The alternate witness is at the vector update, before the zero-input split.
        const bool input_path = alternate_update
            ? control_245 > 0 && pad_194 == 0 && !current.leaving &&
              current.placed_actor == local && current.placed_generation == generation &&
              current.placed_map == scope.placed_map && BbscLoadPadMapFresh(current.placed_map_ms, now)
            : control_245 >= 0 && (control_245 == 0 || pad_194 > 0);
        const bool recorded = actor_matched && input_path && current.placed_map &&
                              generation == bbsc_character_reload_generation.load();
        current.pad_callback = {generation, now, current.pad_callback.count + 1, control_245, pad_194,
                                context_matched, actor_matched, recorded};
        if (recorded) {
            current.pad_actor = local;
            current.pad_generation = generation;
            current.pad_ms = now;
            current.pad_alternate = alternate_update;
        }
        return;
    }
    if (!local) return;

    // All three entry points take a damage module and attacker. Only 0x01A2A600
    // is common receive/apply; the other two stop the observed 0x14 and its delegated
    // 0x15 result path before they reach a native send.
    const u64 victim = ReadValue<u64>(registers->rdi, 8);
    const bool incoming = tag == 0 && victim == local;
    if (!incoming && (registers->rsi != local || victim == local)) return;
    const u32 bit = incoming ? 1 : 2;
    auto checked = scope;
    for (;;) {
        const bool reject = incoming || BbscLoadProtectionPeer(victim, false);
        const u64 live_room = BbscMod::RelayAuthRoom();
        const u32 live_owner = BbscRelayHostForPackets();
        if (!BbscLoadProtectionContextMatches(scope, live_room, live_owner) ||
            (room && live_room && room != live_room) || (owner && live_owner && owner != live_owner) ||
            GetLocalPlayerCharacter() != local) return;
        bool first;
        {
            std::scoped_lock lock{bbsc_load_protection_mutex};
            auto& current = bbsc_load_protection;
            if (!current.serial || current.global != scope.global || current.slot != scope.slot ||
                current.role_epoch != scope.role_epoch ||
                BbscLoadProtectionRoomChanged(current, room ? room : scope.room,
                                             owner ? owner : scope.owner) ||
                BbscLoadProtectionRoomChanged(current, live_room, live_owner)) return;
            if (current.serial != checked.serial) {
                // Same actor and room may re-arm while this call is in native
                // member matching. Recheck the hit under the new transition.
                checked = current;
                continue;
            }
            if (!reject) return;
            first = (current.rejected & bit) == 0;
            current.rejected |= bit;
            // The void damage entries can be skipped before their prologue.
            const_cast<GuestRegisterSnapshot*>(registers)->skip_original = 1;
        }
        if (first)
            LOG_INFO(Debug, "BBSC load protection: rejected {} (transition {})",
                     incoming ? "incoming damage" : "outgoing PvP damage", checked.serial);
        return;
    }
}

void InstallBbscLoadProtectionHooks() {
    static constexpr std::array<u8, 20> Apply{
        0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x41,0x54,0x53,0x48,0x89,0xD3,0x49,0x89,0xF7,0x49,0x89,0xFE};
    static constexpr std::array<u8, 27> Send{
        0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x81,0xE4,0xE0,0xFF,0xFF,0xFF,0x48,0x81,0xEC,0x20,0x01,0x00,0x00};
    static constexpr std::array<u8, 29> Delta{
        0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x81,0xEC,0x88,0x02,0x00,0x00,0x49,0x89,0xD6,0x48,0x89,0xF3,0x49,0x89,0xFC};
    static constexpr std::array<u8, 16> Pad{
        0xBE,0x48,0x00,0x00,0x00,0xBA,0x49,0x00,0x00,0x00,0x48,0x89,0xDF,0xFF,0x50,0x38};
    static constexpr std::array<u8, 20> PadCommon{
        0x49,0x8B,0x84,0x24,0x88,0x00,0x00,0x00,0x4C,0x8B,0x78,0x58,
        0x41,0x80,0xBF,0x45,0x02,0x00,0x00,0x00};
    static constexpr std::array<u8, 22> PadAlternateUpdate{
        0xC5,0x7A,0x51,0xC3,0xC5,0xE0,0x57,0xDB,0xC5,0x78,0x2E,0xC3,
        0xC4,0xC1,0x78,0x29,0x84,0x24,0xB0,0x00,0x00,0x00};
    const std::array<std::tuple<u64,std::span<const u8>,size_t>,6> sites{{
        {0x01A2A600, Apply, Apply.size()}, {0x01A33440, Send, Send.size()},
        {0x01A29E40, Delta, Delta.size()}, {0x0152694B, Pad, 13},
        {0x015267A7, PadCommon, PadCommon.size()}, {0x01528371, Pad, 13},
    }};
    for (const auto& [offset, pin, copy] : sites) {
        if (offset > MemoryPatcher::g_eboot_image_size ||
            pin.size() > MemoryPatcher::g_eboot_image_size - offset ||
            std::memcmp(reinterpret_cast<const void*>(image_base + offset), pin.data(), pin.size()) != 0) {
            LOG_ERROR(Debug, "BBSC load protection: signature unavailable at {:#x}", offset);
            return;
        }
    }
    for (size_t i = 0; i < sites.size(); ++i) {
        const auto& [offset, pin, copy] = sites[i];
        if (!InstallGuestCodeHook(reinterpret_cast<void*>(image_base + offset), pin.first(copy),
                                  i, BbscLoadProtectionHook)) {
            LOG_ERROR(Debug, "BBSC load protection: hook unavailable at {:#x}", offset);
            return;
        }
    }
    constexpr u64 AlternateOffset = 0x015285E0;
    if (AlternateOffset + PadAlternateUpdate.size() > MemoryPatcher::g_eboot_image_size ||
        !HasMemoryAccess(image_base + AlternateOffset, PadAlternateUpdate.size(), MemoryProt::CpuRead) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + AlternateOffset),
                    PadAlternateUpdate.data(), PadAlternateUpdate.size()) != 0) {
        LOG_WARNING(Debug, "BBSC load protection: alternate input witness disabled; signature unavailable at {:#x}",
                    AlternateOffset);
    } else if (!InstallGuestCodeHook(reinterpret_cast<void*>(image_base + AlternateOffset),
                                    PadAlternateUpdate, sites.size(), BbscLoadProtectionHook)) {
        LOG_WARNING(Debug, "BBSC load protection: alternate input witness disabled; hook unavailable at {:#x}",
                    AlternateOffset);
    }
    bbsc_load_protection_hooks = true; // All required hooks are installed, even if the alternate witness failed.
    LOG_INFO(Debug, "BBSC load protection: guest receive, send and control hooks ready");
}
