// Only a retained, already inserted member recreated after the local host's
// real same-map reload gets a caught-up line. Never overwrite a role-table
// column: a genuinely new member may arrive during the same reload.
struct BbscRetainedJoinMember {
    u64 task{}, identity{}, reload{};
    s64 queued{};
    bool consumed{};
};
struct BbscRetainedJoinState {
    TheHuntVisibility::Context context;
    u64 global{}, role{}, reload{};
    u32 map{};
    s64 armed{};
    s64 host_reload_ms{};
    std::array<BbscRetainedJoinMember, 8> members{};
};
std::mutex bbsc_retained_join_mutex;
BbscRetainedJoinState bbsc_retained_join;
constexpr s64 BbscRetainedJoinCaptureMs = 120'000;
constexpr s64 BbscRetainedJoinConsumeMs = 10'000;

bool BbscRetainedJoinLayoutMatches() {
    static constexpr std::array<u8, 3> OwnerPin{0x49,0x89,0xFD};
    if (!ReadablePointer(image_base + 0x0196BA34, OwnerPin.size()) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x0196BA34), OwnerPin.data(), OwnerPin.size()) != 0) return false;
    static constexpr std::array<u8, 7> MemberPin{0x48,0x8D,0x80,0xD8,0x00,0x00,0x00};
    if (!ReadablePointer(image_base + 0x0196BB0A, MemberPin.size()) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x0196BB0A), MemberPin.data(), MemberPin.size()) != 0) return false;
    static constexpr std::array<u8, 43> CallerPin{0x49,0x0F,0xBE,0x85,0xFC,0x00,0x00,0x00,0x31,0xF6,0x48,0x83,0xF8,0x21,0x77,0x0F,0x48,0xC1,0xE0,0x07,0x48,0x8D,0x0D,0x65,0x1A,0xBD,0x03,0x8B,0x74,0x01,0x34,0x48,0x8D,0xBD,0x10,0xFF,0xFF,0xFF,0xE8,0xB5,0xBB,0x17,0x00};
    if (!ReadablePointer(image_base + 0x0196BCD0, CallerPin.size()) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x0196BCD0), CallerPin.data(), CallerPin.size()) != 0) return false;
    return true;
}

bool BbscRetainedJoinHostContext(TheHuntVisibility::Context& context, u64 global) {
    return BbscMod::RelayAuthVisibilityContext(context) && context.room && context.own &&
           context.own == context.owner && GetSessionState() == 3 && GetLocalPlayerType() == 0 &&
           global == GetGlobalState() && ReadablePointer(global, 0x1593) &&
           ReadValue<u8>(global, 0x1592) == 1;
}

void CaptureBbscRetainedJoinMembers() {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    BbscRetainedJoinState next;
    const u64 global = GetGlobalState();
    if (BbscRetainedJoinHostContext(next.context, global) && BbscRetainedJoinLayoutMatches()) {
        next.global = global;
        next.role = BbscLoadProtectionRoleEpoch();
        next.reload = bbsc_character_reload_generation.load();
        next.map = GetCurrentPackedMap();
        next.armed = SteadyNowMs();
        size_t count = 0;
        for (const u64 task : GetMultiPlayerInsTasks()) {
            const s32 mode = ReadValue<s32>(task, 0xE8);
            if (count == next.members.size()) break;
            // A new joining task at FirstSyncWait/CreateWait without an actor
            // is not a retained member. Dead actors still count as inserted.
            if ((mode == 1 || mode == 2) && ReadValue<s32>(task, 0x50) == 6 &&
                RemotePlayerExists(task) && IsValidMemberId(task + 0xC8)) {
                const u64 identity = ReadValue<u64>(task, 0xC8);
                if (identity) next.members[count++] = {task, identity};
            }
        }
    }
    std::scoped_lock lock{bbsc_retained_join_mutex};
    bbsc_retained_join = next;
}

// The host's native death load reaches 0x01938F65 without the guest-only
// ReloadFinish callback. Observe the real stage task; never advance that shared
// character generation or accept an in-place reinsert as a reload. Another load
// invalidates this capture, even when the native task address is reused.
void ObserveBbscRetainedHostReload(u64 task) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const u64 global = GetGlobalState(), role = BbscLoadProtectionRoleEpoch();
    const u64 reload = bbsc_character_reload_generation.load();
    const s64 now = SteadyNowMs();
    const bool host = BbscRetainedJoinHostContext(context, global);
    std::scoped_lock lock{bbsc_retained_join_mutex};
    auto& state = bbsc_retained_join;
    if (!state.role) return;
    if (state.host_reload_ms || !host || state.context != context ||
        state.global != global || state.role != role || state.reload != reload ||
        !state.map || now < state.armed || now - state.armed > BbscRetainedJoinCaptureMs ||
        !ReadablePointer(task, 0xD8) || ReadValue<u32>(task, 0x58) != state.map ||
        ReadValue<s32>(task, 0xA4) != 3 || ReadValue<s32>(task, 0xA8) != 2 ||
        ReadValue<s32>(task, 0xD0) < 0) {
        state = {};
        return;
    }
    state.host_reload_ms = now;
}

// Call immediately before SetState(task,4), never merely when a death starts.
void QueueBbscRetainedJoinLine(u64 task) {
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const u64 global = GetGlobalState(), role = BbscLoadProtectionRoleEpoch();
    const u64 reload = bbsc_character_reload_generation.load();
    const u32 map = GetCurrentPackedMap();
    const s64 now = SteadyNowMs();
    const bool host = BbscRetainedJoinHostContext(context, global);
    std::scoped_lock lock{bbsc_retained_join_mutex};
    auto& state = bbsc_retained_join;
    if (!host || !state.role || state.context != context || state.global != global ||
        state.role != role || !state.map || state.map != map ||
        now < state.armed || now - state.armed > BbscRetainedJoinCaptureMs ||
        !state.host_reload_ms || now < state.host_reload_ms || reload != state.reload) return;
    for (auto& member : state.members) {
        if (member.task == task && !member.queued && !member.consumed &&
            IsValidMemberId(task + 0xC8) && ReadValue<u64>(task, 0xC8) == member.identity) {
            member.reload = reload;
            member.queued = now;
            return;
        }
    }
}

// CUSA03173 1.09: at message-builder caller 0x0196BCFB, R13 is
// the host-join producer. Its member ID is +0xD8 (native 0x0196BB0A),
// compared by 0x00CA1C70 to PlayerIns+0x3D8. This is NOT an insert task.
bool ConsumeBbscRetainedJoinLine(u64 producer, GuestRegisterSnapshot* registers, u32 replacement) {
    if (!registers || !replacement) return false;
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    TheHuntVisibility::Context context;
    const u64 global = GetGlobalState(), role = BbscLoadProtectionRoleEpoch();
    const u64 reload = bbsc_character_reload_generation.load();
    const u32 map = GetCurrentPackedMap();
    const s64 now = SteadyNowMs();
    if (!BbscRetainedJoinHostContext(context, global) || !BbscRetainedJoinLayoutMatches() ||
        !ReadablePointer(producer, 0x100) || !IsValidMemberId(producer + 0xD8)) return false;
    const auto tasks = GetMultiPlayerInsTasks();
    std::scoped_lock lock{bbsc_retained_join_mutex};
    auto& state = bbsc_retained_join;
    if (!state.role || !state.host_reload_ms || state.context != context || state.global != global ||
        state.role != role || state.map != map ||
        now < state.armed || now - state.armed > BbscRetainedJoinCaptureMs) return false;
    using MemberEquals = bool PS4_SYSV_ABI (*)(u64, u64);
    const auto equals = reinterpret_cast<MemberEquals>(image_base + MemberIdEqualsOffset);
    for (auto& member : state.members) {
        if (!member.queued || member.consumed || member.reload != reload ||
            now < member.queued || now - member.queued > BbscRetainedJoinConsumeMs ||
            std::find(tasks.begin(), tasks.end(), member.task) == tasks.end()) continue;
        if (IsValidMemberId(member.task + 0xC8) &&
            ReadValue<u64>(member.task, 0xC8) == member.identity &&
            equals(member.task + 0xC8, producer + 0xD8)) {
            struct Write { GuestRegisterSnapshot* registers; bool* consumed; u32 replacement; }
                write{registers, &member.consumed, replacement};
            // Keep the signed room/roster pinned through both the register
            // assignment and the once-only consumption. No callbacks, logging,
            // relay calls or additional locks inside the bounded write.
            return BbscMod::RelayAuthUseVisibilityContext(state.context, [](void* opaque) {
                const auto& write = *static_cast<const Write*>(opaque);
                write.registers->rsi = write.replacement;
                *write.consumed = true;
                return true;
            }, &write);
        }
    }
    return false;
}
