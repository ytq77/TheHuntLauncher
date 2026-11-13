// Preserve native death reload semantics for one authenticated same-map wipe.
// The native 4/2 constructor ignores the forced map and rebuilds it from the
// local player's last lamp. Only this transient descriptor is corrected; the
// own-world lamp, HP, death bits, save state and damage/readiness gates are not.
struct BbscWipeRespawnTarget {
    TheHuntVisibility::Context context;
    u64 global{}, actor{}, reload{}, role{}, ticket{}, descriptor{};
    u64 own_world_task{}, restore_type_record{};
    u64 map_root{}, parent{}, parent_wrapper{}, parent_frame{};
    s32 parent_phase{};
    u8 native_consumptions{};
    s32 slot{-1}, own_point{-1}, host_point{-1};
    u32 map{};
    s64 started{};
    bool applied{};
    bool native_own_world{};
    bool map_retired{};
};
std::mutex bbsc_wipe_target_mutex;
BbscWipeRespawnTarget bbsc_wipe_target;
// Updated with the accepted follow packet under bbsc_follow_mutex.
TheHuntVisibility::Context bbsc_wipe_follow_context;
constexpr s64 BbscWipeTargetMaxMs = 15'000;

void ClearBbscWipeRespawnTarget() {
    std::scoped_lock lock{bbsc_wipe_target_mutex};
    bbsc_wipe_target = {};
}

u32 BbscWipeLampMap(s32 point) {
    if (point < 0) return 0;
    const u32 area = static_cast<u32>(point / 100000);
    const u32 block = static_cast<u32>((point / 10000) % 10);
    // Native area 29 uses a separate destination rule. Do not guess its map,
    // or generalize the decimal-ID mapping to out-of-range/custom point IDs.
    if (area == 0 || area >= 100 || area == 29) return 0;
    return (area << 24) | (block << 16);
}

bool BbscWipeDescriptorLayoutMatches() {
    // Pinned 1.09 branches: native death marker, 4/2 point and point-to-map.
    // Do not include the existing finalize hook's overwritten prologue.
    static constexpr std::array<u8, 13> Pin0{0x8A,0x88,0x22,0x15,0x00,0x00,0x41,0x88,0x89,0x94,0x00,0x00,0x00};
    if (!HasMemoryAccess(image_base + 0x01944C20, Pin0.size(), MemoryProt::CpuRead) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01944C20), Pin0.data(), Pin0.size()) != 0) return false;
    static constexpr std::array<u8, 46> Pin1{0x41,0xC7,0x41,0x54,0x04,0x00,0x00,0x00,0x84,0xC9,0x0F,0x84,0xEC,0x00,0x00,0x00,0x41,0xC7,0x41,0x58,0x02,0x00,0x00,0x00,0x8B,0x80,0x28,0x15,0x00,0x00,0x41,0x89,0x81,0x80,0x00,0x00,0x00,0x83,0xF8,0xFF,0x0F,0x84,0x0C,0x01,0x00,0x00};
    if (!HasMemoryAccess(image_base + 0x01944DE9, Pin1.size(), MemoryProt::CpuRead) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01944DE9), Pin1.data(), Pin1.size()) != 0) return false;
    static constexpr std::array<u8, 120> Pin2{0x48,0x69,0xC1,0x89,0xB5,0xF8,0x14,0x48,0x89,0xC2,0x48,0xC1,0xEA,0x3F,0x48,0xC1,0xF8,0x2D,0x01,0xD0,0x48,0x98,0x48,0x69,0xD0,0x1F,0x85,0xEB,0x51,0x48,0xC1,0xFA,0x25,0x89,0xD6,0xC1,0xEE,0x1F,0x01,0xD6,0x6B,0xD6,0x64,0x29,0xD0,0x83,0xF8,0x1D,0x0F,0x84,0xB1,0x00,0x00,0x00,0x48,0x69,0xC9,0xAD,0x8B,0xDB,0x68,0x48,0x89,0xCA,0x48,0xC1,0xEA,0x3F,0x48,0xC1,0xF9,0x2C,0x01,0xD1,0x48,0x63,0xC9,0x48,0x69,0xD1,0x67,0x66,0x66,0x66,0x48,0xC1,0xFA,0x22,0x89,0xD6,0xC1,0xEE,0x1F,0x01,0xD6,0x6B,0xD6,0x0A,0x29,0xD1,0xC1,0xE0,0x18,0xC1,0xE1,0x10,0x81,0xE1,0x00,0x00,0xFF,0x00,0x09,0xC1,0x41,0x89,0x49,0x08,0xEB,0x6F};
    if (!HasMemoryAccess(image_base + 0x01944E3C, Pin2.size(), MemoryProt::CpuRead) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01944E3C), Pin2.data(), Pin2.size()) != 0) return false;
    return true;
}

bool BbscWipeOwnWorldLayoutMatches() {
    // OwnWorldStep's exact 1.09 writes: own-world=1, then restore PlayerData+A4.
    // The entry and completion hooks are outside this pinned block.
    static constexpr std::array<u8, 92> NativeOwnWorld{
        0xc6,0x80,0x92,0x15,0x00,0x00,0x01,0x48,0x8d,0x0d,0x0c,0x8f,0xbf,0x03,0x48,0x8b,
        0x09,0x48,0x8b,0x79,0x08,0x48,0x85,0xff,0x74,0x42,0x8b,0x8f,0xa4,0x00,0x00,0x00,
        0x83,0xf9,0x0d,0x77,0x37,0xba,0x06,0x30,0x00,0x00,0x0f,0xa3,0xca,0x73,0x2d,0x48,
        0x8d,0x05,0x3c,0x8f,0xbf,0x03,0x48,0x8b,0x00,0x8b,0x50,0x04,0x89,0x97,0xa4,0x00,
        0x00,0x00,0x0f,0xb6,0xb7,0xca,0x00,0x00,0x00,0x48,0x81,0xc7,0xd0,0x01,0x00,0x00,
        0xe8,0xd5,0xaa,0xb8,0xff,0x48,0x8b,0x05,0x06,0x44,0xc1,0x03};
    return HasMemoryAccess(image_base + 0x01942216, NativeOwnWorld.size(), MemoryProt::CpuRead) &&
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01942216),
                    NativeOwnWorld.data(), NativeOwnWorld.size()) == 0;
}

void ObserveBbscWipeOwnWorldStep(u64 task, bool completed) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const bool authenticated = BbscMod::RelayAuthVisibilityContext(context);
    const s64 now = SteadyNowMs();
    std::scoped_lock lock{bbsc_wipe_target_mutex};
    auto& scope = bbsc_wipe_target;
    if (!scope.ticket || scope.applied) return;
    const auto cancel = [&](std::string_view why) {
        LOG_INFO(Debug, "BBSC wipe target: native own-world handoff rejected ({})", why);
        scope = {};
    };
    if (!authenticated || context != scope.context || now < scope.started ||
        now - scope.started > BbscWipeTargetMaxMs) return cancel("context-or-time");
    if (!ReadablePointer(task, 0x151) || !BbscWipeOwnWorldLayoutMatches())
        return cancel("native-task-or-layout");
    const u64 global = GetGlobalState();
    if (global != scope.global || !ReadablePointer(global, 0x1593) ||
        GetSessionState() != 6 || GetLocalPlayerCharacter() != scope.actor ||
        !BbscOwnWorldReadyForGuest() || BbscLoadProtectionRoleEpoch() != scope.role ||
        bbsc_character_reload_generation.load() != scope.reload ||
        BbscLoadProtectionTicket() != scope.ticket ||
        ReadValue<s32>(global, 0x1490) != scope.slot || GetCharacterHp(scope.actor) != 0)
        return cancel("transition-identity");
    const u64 restore = ReadValue<u64>(image_base + 0x0553B188, 0);
    // ArmBbscRejoinV7/ArmBbscReinsertV8 already retained the guest's native
    // restore type. A real return to the player's own role is not this scope.
    if (!ReadablePointer(restore, 8) || ReadValue<s32>(restore, 4) != 1)
        return cancel("native-restore-type");
    if (!completed) {
        if (scope.native_own_world || GetLocalPlayerType() != 1 ||
            ReadValue<u8>(global, 0x1592) != 0 ||
            (scope.own_world_task && (scope.own_world_task != task || scope.restore_type_record != restore)))
            return cancel("native-step-origin");
        scope.own_world_task = task;
        scope.restore_type_record = restore;
        return;
    }
    if (scope.native_own_world || scope.own_world_task != task || scope.restore_type_record != restore ||
        GetLocalPlayerType() != 1 || ReadValue<u8>(global, 0x1592) != 1 ||
        ReadValue<u8>(global, 0x1520) != 1 || ReadValue<u8>(global, 0x1522) != 1 ||
        ReadValue<s32>(global, 0x1528) != scope.own_point ||
        ReadValue<u32>(global, 0x0C) != scope.map || ReadValue<u32>(global, 0x14C4) != scope.map ||
        GetCurrentPackedMap() != scope.map) return cancel("native-step-result");
    const u64 map_root = ReadValue<u64>(image_base + 0x0553B148, 0);
    if (!ReadablePointer(map_root, 0x28) || ReadValue<u64>(task, 0x118) != map_root)
        return cancel("native-map-owner");
    scope.map_root = map_root;
    scope.native_own_world = true;
    LOG_INFO(Debug, "BBSC wipe target: observed native own-world handoff (transition {})", scope.ticket);
}

bool BbscWipeParentLayoutMatches() {
    // The two ordered calls consume separately constructed descriptors. The
    // root-retirement hook itself is outside the unmodified comparison pin.
    static constexpr std::array<u8, 12> RootPin{0x48,0x8D,0x05,0x2B,0x2B,0xFF,0x03,0x4C,0x39,0x28,0x75,0x07};
    static constexpr std::array<u8, 5> FirstCall{0xE8,0xF6,0xEB,0xFF,0xFF};
    static constexpr std::array<u8, 5> SecondCall{0xE8,0xF1,0xEA,0xFF,0xFF};
    return HasMemoryAccess(image_base + 0x01548616, RootPin.size(), MemoryProt::CpuRead) &&
        HasMemoryAccess(image_base + 0x01929A85, FirstCall.size(), MemoryProt::CpuRead) &&
        HasMemoryAccess(image_base + 0x01929B8A, SecondCall.size(), MemoryProt::CpuRead) &&
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01548616), RootPin.data(), RootPin.size()) == 0 &&
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01929A85), FirstCall.data(), FirstCall.size()) == 0 &&
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01929B8A), SecondCall.data(), SecondCall.size()) == 0;
}

void ObserveBbscWipeParentTransition(u64 parent, u64 stack) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const bool authenticated = BbscMod::RelayAuthVisibilityContext(context);
    const s64 now = SteadyNowMs();
    std::scoped_lock lock{bbsc_wipe_target_mutex};
    auto& scope = bbsc_wipe_target;
    if (!scope.ticket || !scope.native_own_world || scope.applied) return;
    const auto reject = [&] { scope = {}; };
    if (!authenticated || context != scope.context || now < scope.started ||
        now - scope.started > BbscWipeTargetMaxMs || scope.parent ||
        !BbscWipeParentLayoutMatches() || !ReadablePointer(parent, 0xE0) ||
        stack < 0x10000 || !ReadablePointer(stack, 8) ||
        GetGlobalState() != scope.global || GetSessionState() != 6 || GetLocalPlayerType() != 1 ||
        GetLocalPlayerCharacter() != scope.actor || GetCharacterHp(scope.actor) != 0 ||
        BbscLoadProtectionRoleEpoch() != scope.role || BbscLoadProtectionTicket() != scope.ticket ||
        bbsc_character_reload_generation.load() != scope.reload || !BbscOwnWorldReadyForGuest() ||
        ReadValue<u64>(image_base + 0x0553B148, 0) != scope.map_root || GetCurrentPackedMap() != scope.map)
        return reject();
    const u64 wrapper = ReadValue<u64>(parent, 0x38);
    const u64 rules = ReadValue<u64>(image_base + 0x0553D6D0, 0);
    // Check the live ownership chain before its last references are released.
    if (!ReadablePointer(wrapper, 0x28) || ReadValue<s32>(wrapper, 0x0C) != 3 ||
        ReadValue<s32>(wrapper, 0x10) != 1 || ReadValue<u64>(wrapper, 0x20) != scope.own_world_task ||
        !ReadablePointer(scope.own_world_task, 0x120) || ReadValue<s32>(scope.own_world_task, 8) != 1 ||
        ReadValue<s32>(scope.own_world_task, 0x0C) != -1 ||
        ReadValue<u64>(scope.own_world_task, 0x118) != scope.map_root ||
        !ReadablePointer(rules, 0x88) || ReadValue<s32>(rules, 0x84) != 3)
        return reject();
    scope.parent = parent;
    scope.parent_wrapper = wrapper;
    scope.parent_frame = stack - 8; // Native entry's next push establishes RBP.
    scope.parent_phase = ReadValue<s32>(parent, 0x0C);
}

void ObserveBbscWipeMapRetirement(u64 slot, u64 map_root) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const bool authenticated = BbscMod::RelayAuthVisibilityContext(context);
    const s64 now = SteadyNowMs();
    std::scoped_lock lock{bbsc_wipe_target_mutex};
    auto& scope = bbsc_wipe_target;
    if (!scope.ticket || !scope.native_own_world || scope.applied) return;
    // At this exact native store boundary, map internals and the old actor may
    // already be destroyed. Only compare the published root and live parent.
    if (!authenticated || context != scope.context || now < scope.started ||
        now - scope.started > BbscWipeTargetMaxMs || !scope.parent || scope.map_retired ||
        slot != image_base + 0x0553B148 || map_root != scope.map_root ||
        ReadValue<u64>(slot, 0) != scope.map_root || !BbscWipeParentLayoutMatches() ||
        GetGlobalState() != scope.global || GetSessionState() != 6 || GetLocalPlayerType() != 1 ||
        GetLocalPlayerCharacter() != 0 || !BbscOwnWorldReadyForGuest() ||
        BbscLoadProtectionRoleEpoch() != scope.role || BbscLoadProtectionTicket() != scope.ticket ||
        bbsc_character_reload_generation.load() != scope.reload ||
        !ReadablePointer(scope.parent, 0xE0) ||
        ReadValue<u64>(scope.parent, 0x38) != scope.parent_wrapper ||
        ReadValue<s32>(scope.parent, 0x0C) != scope.parent_phase) {
        scope = {};
        return;
    }
    scope.map_retired = true;
}

void ArmBbscWipeRespawnTarget(u64 global, const SummonPlacementDescriptor& to, u64 ticket) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    BbscWipeRespawnTarget next;
    TheHuntVisibility::Context context;
    BbscFollowMessage follow;
    bool follow_bound = false;
    const s64 now = SteadyNowMs();
    const u64 actor = GetLocalPlayerCharacter();
    const bool authenticated = BbscMod::RelayAuthVisibilityContext(context);
    {
        std::scoped_lock lock{bbsc_follow_mutex};
        follow = bbsc_follow_latest;
        follow_bound = authenticated && bbsc_wipe_follow_context == context;
    }
    std::string_view refused;
    if (!authenticated || !context.room || !context.own || !context.owner ||
        context.own == context.owner || !follow_bound) refused = "host-context";
    else if (!summon_reload_state_hook_installed || !BbscWipeDescriptorLayoutMatches())
        refused = "native-layout";
    else if (!ticket || BbscLoadProtectionTicket() != ticket ||
             GetSessionState() != 6 || GetLocalPlayerType() != 1 ||
             GetGlobalState() != global || !ReadablePointer(global, 0x1593) ||
             ReadValue<u8>(global, 0x1592) != 0 || ReadValue<u8>(global, 0x08) != 0)
        refused = "guest-transition";
    else if (!actor || GetCharacterHp(actor) != 0 || !bbsc_spectate_active ||
             ReadValue<u8>(global, 0x1520) != 1 || ReadValue<u8>(global, 0x1522) != 1)
        refused = "native-death";
    else if (!IsUsablePackedMap(to.packed_region) || GetCurrentPackedMap() != to.packed_region ||
             follow.packed_map != to.packed_region || BbscWipeLampMap(follow.lamp_point) != to.packed_region)
        refused = "host-lamp-map";
    else if (!follow.received_ms || now < follow.received_ms ||
             now - follow.received_ms > BbscFollowStaleMs || !ResolveLampPoint(follow.lamp_point))
        refused = "host-lamp-unavailable";
    else if (!BbscOwnWorldReadyForGuest()) refused = "own-world-baseline";
    else {
        const s32 slot = ReadValue<s32>(global, 0x1490);
        const s32 own_point = ReadValue<s32>(global, 0x1528);
        const u64 role = BbscLoadProtectionRoleEpoch();
        if (slot < 0 || slot > 9 || !role || !BbscWipeLampMap(own_point))
            refused = "native-origin";
        else {
            next.context = context;
            next.global = global; next.actor = actor;
            next.reload = bbsc_character_reload_generation.load();
            next.role = role; next.ticket = ticket; next.slot = slot;
            next.own_point = own_point; next.host_point = follow.lamp_point;
            next.map = to.packed_region; next.started = now;
        }
    }
    {
        std::scoped_lock lock{bbsc_wipe_target_mutex};
        bbsc_wipe_target = next;
    }
    if (!refused.empty())
        LOG_INFO(Debug, "BBSC wipe target: kept native death destination ({})", refused);
    else
        LOG_INFO(Debug, "BBSC wipe target: armed verified host lamp {} in map {:#x} (transition {})",
                 next.host_point, next.map, next.ticket);
}

void ApplyBbscWipeRespawnAtDescriptor(u64 descriptor, u64 global, u64 parent = 0,
                                    u64 caller = 0, u64 frame = 0, u64 stack = 0) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const bool authenticated = BbscMod::RelayAuthVisibilityContext(context);
    const s64 now = SteadyNowMs();
    const u64 role = BbscLoadProtectionRoleEpoch();
    const u64 ticket = BbscLoadProtectionTicket();
    bool follow_current = false;
    BbscFollowMessage follow;
    {
        std::scoped_lock lock{bbsc_follow_mutex};
        follow = bbsc_follow_latest;
        follow_current = authenticated && bbsc_wipe_follow_context == context &&
            bbsc_follow_latest.received_ms && now >= bbsc_follow_latest.received_ms &&
            now - bbsc_follow_latest.received_ms <= BbscFollowStaleMs;
    }
    std::scoped_lock lock{bbsc_wipe_target_mutex};
    auto& scope = bbsc_wipe_target;
    if (!scope.ticket) return;
    const auto cancel = [&](std::string_view why) {
        LOG_INFO(Debug, "BBSC wipe target: expired before descriptor ({})", why);
        scope = {};
    };
    if (!authenticated || context != scope.context || !follow_current ||
        now < scope.started || now - scope.started > BbscWipeTargetMaxMs)
        return cancel("context-or-time");
    // Constructor callbacks precede both consumers. For the witnessed native
    // lifecycle, wait for the exact parent call sites instead of spending the
    // authorization on a transient first construction.
    if (scope.native_own_world && !parent) return;
    if (global != scope.global || GetGlobalState() != global) return cancel("transition-global");
    if (GetSessionState() != 6) return cancel("transition-session");
    const u64 actor = GetLocalPlayerCharacter();
    // The completed old stage is destroyed before the native constructor.
    // 0x01915006 clears WorldChrMan+0x60; no new actor exists until later in
    // the new stage. Only this observed task may retire the captured actor.
    // Never dereference the released task or old actor in that interval.
    const bool native_own_world = scope.native_own_world && scope.map_retired && !actor &&
        parent && parent == scope.parent && ReadablePointer(parent, 0xE0) &&
        ReadValue<u64>(image_base + 0x0553B148, 0) == 0 &&
        ReadablePointer(global, 0x1593) && ReadValue<u8>(global, 0x1592) == 1;
    if (parent) {
        const u64 expected = scope.native_consumptions == 0 ? 0x01929A8A :
                             scope.native_consumptions == 1 ? 0x01929B8F : 0;
        if (!native_own_world || !expected || caller != image_base + expected ||
            frame != scope.parent_frame || frame < 0xD0 || descriptor != frame - 0xD0 ||
            stack > descriptor || descriptor - stack != 0x28 ||
            !BbscWipeParentLayoutMatches() || ReadValue<u64>(parent, 0x38) != 0 ||
            ReadValue<s32>(parent, 0x0C) != scope.parent_phase ||
            ReadValue<s32>(parent, 0x30) != (scope.native_consumptions == 0 ? 0 : 1) ||
            (scope.native_consumptions && (scope.descriptor != descriptor ||
                ReadValue<u32>(parent, 0x48) != scope.map || ReadValue<s32>(parent, 0xC0) != scope.host_point)))
            return cancel("native-parent-sequence");
    }
    if (GetLocalPlayerType() != 1) return cancel("transition-local-type");
    if (actor != scope.actor && !native_own_world) return cancel("transition-actor");
    if (bbsc_character_reload_generation.load() != scope.reload) return cancel("transition-reload");
    if (role != scope.role) return cancel("transition-save-generation");
    if (ticket != scope.ticket) return cancel("transition-ticket");
    if (!BbscOwnWorldReadyForGuest()) return cancel("transition-own-world-baseline");
    if (!ReadablePointer(global, 0x1593) || (ReadValue<u8>(global, 0x1592) != 0 && !native_own_world) ||
        ReadValue<s32>(global, 0x1490) != scope.slot ||
        ReadValue<u8>(global, 0x1520) != 1 || ReadValue<u8>(global, 0x1522) != 1 ||
        ReadValue<s32>(global, 0x1528) != scope.own_point ||
        ReadValue<u32>(global, 0x0C) != scope.map ||
        ReadValue<u32>(global, 0x14C4) != scope.map ||
        (!native_own_world && GetCurrentPackedMap() != scope.map) ||
        (!native_own_world && GetCharacterHp(scope.actor) != 0))
        return cancel("native-state");
    if (follow.packed_map != scope.map || follow.lamp_point != scope.host_point)
        return cancel("host-destination-changed");
    if (!ReadablePointer(descriptor, 0xA0) ||
        ReadValue<s32>(descriptor, 0x54) != 4 ||
        ReadValue<s32>(descriptor, 0x58) != 2 ||
        ReadValue<u8>(descriptor, 0x94) != 1) return cancel("not-death-descriptor");
    const u32 map = ReadValue<u32>(descriptor, 0x08);
    const s32 point = ReadValue<s32>(descriptor, 0x80);
    if (scope.applied && !native_own_world) {
        // Two existing trace callbacks may see this same descriptor. They must
        // not let a later constructor, even at a reused stack address, reuse it.
        if (scope.descriptor != descriptor || map != scope.map || point != scope.host_point)
            cancel("descriptor-reuse");
        return;
    }
    if (point != scope.own_point || map != BbscWipeLampMap(point) ||
        !BbscWipeDescriptorLayoutMatches()) return cancel("native-source");
    // One bounded write, with all intervening bytes copied unchanged. This is
    // byte-for-byte the native 4/2 descriptor built with the host lamp instead.
    auto bytes = ReadValue<std::array<u8, 0x7C>>(descriptor, 0x08);
    std::memcpy(bytes.data(), &scope.map, sizeof(scope.map));
    std::memcpy(bytes.data() + 0x78, &scope.host_point, sizeof(scope.host_point));
    struct Write { u64 descriptor; std::array<u8, 0x7C> bytes; } write{descriptor, bytes};
    // Pin the signed transport context through this single bounded memory write.
    // EffectGuard alone does not prevent Receive from publishing a new context.
    if (!BbscMod::RelayAuthUseVisibilityContext(scope.context, [](void* opaque) {
            const auto& write = *static_cast<const Write*>(opaque);
            return WriteValue(write.descriptor, 0x08, write.bytes);
        }, &write)) return cancel("context-or-descriptor-write");
    scope.applied = true; scope.descriptor = descriptor;
    if (native_own_world) ++scope.native_consumptions;
    LOG_INFO(Debug, "BBSC wipe target: preserved native death reload, destination {}->{}, map {:#x}->{:#x} (transition {})",
             point, scope.host_point, map, scope.map, scope.ticket);
}
