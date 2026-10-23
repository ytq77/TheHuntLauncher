// CUSA03173 1.09 common event 9198 arms flag 9185 for a paid Beckoning Bell search.
// Event 9183 consumes that flag and applies SpEffect 4680 (+1 Insight) on cancellation.
// BBSC's kit costs zero even when the optional vanilla free-bell switch is off.
// Disarm only that refund, and only after a bell's zero cost was actually written.
// Do not alter SpEffect 4680: other events use it for legitimate Insight grants.
std::atomic<bool> bbsc_zero_cost_bell_ready{false};

void PS4_SYSV_ABI BbscInsightRefundFlagHook(u64, const GuestRegisterSnapshot* registers) {
    if (registers == nullptr || static_cast<u32>(registers->rsi) != 9185 ||
        static_cast<u32>(registers->rdx) == 0 ||
        !bbsc_zero_cost_bell_ready.load(std::memory_order_acquire)) {
        return;
    }
    // SprjEventFlagMan::SetFlag(manager=rdi, flag=esi, on=edx).
    // The original function still runs, writing false and preserving its normal return value.
    const_cast<GuestRegisterSnapshot*>(registers)->rdx = 0;
    LOG_INFO(Debug, "BBSC free bell: suppressed unpaid Insight refund flag 9185 (cost verified zero)");
}

void InstallBbscInsightRefundGuard() {
    constexpr u64 SetFlagOffset = 0x013CFCC0;
    // mov r8d,edx; mov ecx,[rdi+1c] -- two whole non-relative instructions.
    static constexpr std::array<u8, 6> Prologue{0x41, 0x89, 0xD0, 0x8B, 0x4F, 0x1C};
    if (!InstallGuestCodeHook(reinterpret_cast<void*>(image_base + SetFlagOffset),
                              std::span<const u8>{Prologue}, 0, BbscInsightRefundFlagHook)) {
        LOG_ERROR(Debug, "BBSC free bell: refund guard signature mismatch; guard not installed");
        return;
    }
    LOG_INFO(Debug, "BBSC free bell: unpaid Insight refund guard installed");
}
