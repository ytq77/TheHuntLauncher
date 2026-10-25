// CUSA03173 1.09: 302110 -> 0x01BC22F0 builds the Stop Hunt Bell confirmation.
// Its confirmed action (vtable 0x05341300 + 0x10, 0x01BC6BE0) writes menu+0x474=1.
// Hook only that non-relative store, leaving the original stop-search action intact.
std::atomic<s64> bbsc_empty_host_cancel_ms{0};
std::atomic<u64> bbsc_empty_host_cancel_manager{0};

void PS4_SYSV_ABI BbscEmptyHostCancelConfirmed(u64, const GuestRegisterSnapshot* r) {
    if (r == nullptr || GetSessionState() != 3 ||
        r->rax != ReadValue<u64>(image_base + 0x05562878, 0)) {
        return;
    }
    const u64 manager = GetMatchingState();
    if (!ReadablePointer(manager, 0x128)) {
        return;
    }
    bbsc_empty_host_cancel_manager.store(manager, std::memory_order_relaxed);
    bbsc_empty_host_cancel_ms.store(SteadyNowMs(), std::memory_order_release);
    LOG_INFO(Debug, "BBSC empty-host cancel: Stop Hunt Bell confirmed; original search cancellation runs");
}

void InstallBbscEmptyHostCancelHook() {
    constexpr u64 offset = 0x01BC6BE7;
    // mov dword ptr [rax+0x474],1; whole instruction, no RIP-relative operands.
    constexpr std::array<u8, 10> store{0xC7,0x80,0x74,0x04,0x00,0x00,0x01,0x00,0x00,0x00};
    if (!InstallGuestCodeHook(reinterpret_cast<void*>(image_base + offset),
                              std::span<const u8>{store}, 0, BbscEmptyHostCancelConfirmed)) {
        LOG_ERROR(Debug, "BBSC empty-host cancel: confirmation signature mismatch; fix not installed");
        return;
    }
    LOG_INFO(Debug, "BBSC empty-host cancel: confirmed Stop Hunt Bell hook installed");
}

void TickBbscEmptyHostCancel() {
    const s64 requested = bbsc_empty_host_cancel_ms.load(std::memory_order_acquire);
    if (requested == 0) {
        return;
    }
    const u64 manager = GetMatchingState();
    const u64 global = GetGlobalState();
    const u64 player = GetLocalPlayerCharacter();
    const bool ready = ReadablePointer(manager, 0x128) &&
                       ReadablePointer(global, 0x1700) && ReadablePointer(player, 0x498);
    const u64 remote = ready ? ReadValue<u64>(global, 0x16F8) : 0;
    const bool remote_ready = ReadablePointer(remote, 0xA0);
    const BbscEmptyHostCancel::Snapshot snapshot{
        requested, SteadyNowMs(), GetSessionState(),
        ready && remote_ready ? GetBbscSessionMemberCount(global) : -1,
        ready ? RemotePlayerCount() : -1,
        manager == bbsc_empty_host_cancel_manager.load(std::memory_order_relaxed),
        ready && remote_ready,
        ready && ReadValue<u8>(global, 0x1592) != 0,
        ready && GetCharacterHp(player) > 0 && !bbsc_spectate_active,
        !ready || ReadValue<u8>(global, 0x08) != 0,
        ready && BbscLocalEffectNode(9000) != 0};
    const auto decision = BbscEmptyHostCancel::Evaluate(snapshot);
    if (decision == BbscEmptyHostCancel::Decision::Wait) {
        return;
    }
    bbsc_empty_host_cancel_ms.store(0, std::memory_order_release);
    if (decision != BbscEmptyHostCancel::Decision::EndRoom) {
        LOG_INFO(Debug, "BBSC empty-host cancel: room kept/unchanged (state {}, members {}, remotes {}, decision {})",
                 snapshot.session_state, snapshot.members, snapshot.remote_players,
                 static_cast<int>(decision));
        return;
    }
    // Same native Stop -> RemoteCleanup pair used by the existing partner-gone path.
    // Do not suspend persistence globally or write the session-state field directly.
    using Stop = u8 PS4_SYSV_ABI (*)(u64);
    using Cleanup = void PS4_SYSV_ABI (*)(u64);
    const u8 stopped = reinterpret_cast<Stop>(image_base + MultiPlayStopRequestOffset)(manager);
    if (stopped != 0) {
        reinterpret_cast<Cleanup>(image_base + WorldChrManRemoteCleanupOffset)(remote);
    }
    LOG_INFO(Debug, "BBSC empty-host cancel: confirmed search stopped with one member; native Stop -> {}, state {}",
             stopped, GetSessionState());
}
