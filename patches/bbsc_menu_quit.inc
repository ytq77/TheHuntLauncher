// The System menu's accepted Exit Game callback. The dialog-building path and
// Cancel do not execute this instruction: they must leave co-op untouched.
constexpr u64 BbscMenuQuitConfirmedOffset = 0x01C0D081;
constexpr std::array<u8, 10> BbscMenuQuitConfirmedBytes{
    0xC7,0x80,0x5C,0x02,0x00,0x00,0x01,0x00,0x00,0x00};

void PS4_SYSV_ABI BbscMenuQuitConfirmed(u64, const GuestRegisterSnapshot* registers) {
    if (!registers || !ReadablePointer(registers->rax, 0x260)) return;
    LOG_INFO(Debug, "BBSC System menu exit confirmed: session_state={}; releasing persistent-session patches before native exit", GetSessionState());
    // Let the game's existing confirmation/save/title transition do its work.
    // Do not kill the process, alter the save, or invoke a second exit sequence.
    BbscWorldRecoveryQuit(true);
    SuspendBbscPersistence("confirmed System menu exit");
}

void InstallBbscMenuQuitHook() {
    const bool ok = InstallGuestCodeHook(
        reinterpret_cast<void*>(image_base + BbscMenuQuitConfirmedOffset),
        std::span<const u8>{BbscMenuQuitConfirmedBytes}, 0, BbscMenuQuitConfirmed);
    if (ok) LOG_INFO(Debug, "BBSC System menu exit: confirmation hook ready at {:#x}", BbscMenuQuitConfirmedOffset);
    else LOG_ERROR(Debug, "BBSC System menu exit: confirmation signature/hook unavailable at {:#x}", BbscMenuQuitConfirmedOffset);
}
