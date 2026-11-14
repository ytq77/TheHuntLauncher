// The System menu's accepted Exit Game callback. The dialog-building path and
// Cancel do not execute this instruction: they must leave co-op untouched.
constexpr u64 BbscMenuQuitConfirmedOffset = 0x01C0D081;
constexpr std::array<u8, 10> BbscMenuQuitConfirmedBytes{
    0xC7,0x80,0x5C,0x02,0x00,0x00,0x01,0x00,0x00,0x00};

void DiscardBbscIdleQuitWarpTarget() {
    // A kept death can run the native last-lamp selector (0x01332BC0) without
    // consuming its destination through a real reload. A valid last-lamp id
    // can remain too (observed: 2412952, auxiliary 101163). That id alone does
    // not make the request active: the first quit reloaded the map, while the
    // second quit with only global+0x0C cleared reached title normally.
    // Only discard this idle same-map case on an accepted quit. Active travel,
    // guests and live host sessions keep their existing teardown paths.
    if (GetSessionState() != 0) return;
    const u64 global = GetGlobalState();
    if (!ReadablePointer(global, 0x1593) || ReadValue<u8>(global, 0x08) != 0 ||
        ReadValue<u8>(global, 0x1592) == 0 || ReadValue<s32>(global, 0x10) != -1 ||
        ReadValue<s32>(global, 0x1538) != 0 || ReadValue<s32>(global, 0x153C) < -1) return;
    const u64 player = GetLocalPlayerCharacter();
    const u32 map = GetCurrentPackedMap();
    if (player == 0 || GetCharacterHp(player) <= 0 || map == 0 || map == 0xFFFFFFFF ||
        ReadValue<u32>(global, 0x0C) != map) return;
    if (WriteValue<u32>(global, 0x0C, 0xFFFFFFFF)) {
        LOG_INFO(Debug, "BBSC System menu exit: discarded inactive same-map warp target {:#x}; native save/title exit continues", map);
    }
}

void PS4_SYSV_ABI BbscMenuQuitConfirmed(u64, const GuestRegisterSnapshot* registers) {
    if (!registers || !ReadablePointer(registers->rax, 0x260)) return;
    LOG_INFO(Debug, "BBSC System menu exit confirmed: session_state={}; releasing persistent-session patches before native exit", GetSessionState());
    // Let the game's existing confirmation/save/title transition do its work.
    // Do not kill the process, alter the save, or invoke a second exit sequence.
    BbscWorldRecoveryQuit(true);
    DiscardBbscIdleQuitWarpTarget();
    SuspendBbscPersistence("confirmed System menu exit");
}

void InstallBbscMenuQuitHook() {
    const bool ok = InstallGuestCodeHook(
        reinterpret_cast<void*>(image_base + BbscMenuQuitConfirmedOffset),
        std::span<const u8>{BbscMenuQuitConfirmedBytes}, 0, BbscMenuQuitConfirmed);
    if (ok) LOG_INFO(Debug, "BBSC System menu exit: confirmation hook ready at {:#x}", BbscMenuQuitConfirmedOffset);
    else LOG_ERROR(Debug, "BBSC System menu exit: confirmation signature/hook unavailable at {:#x}", BbscMenuQuitConfirmedOffset);
}
