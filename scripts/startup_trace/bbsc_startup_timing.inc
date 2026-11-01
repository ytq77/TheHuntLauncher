// Diagnostic only: no guest memory/register writes, background threads or waits.
// Added only by prepare_startup_trace.py, not by the normal release pipeline.
std::atomic<unsigned> bbsc_startup_trace_lines{0};
thread_local s64 bbsc_startup_trace_origin = 0;
thread_local s64 bbsc_startup_load_begin = 0;
thread_local unsigned bbsc_startup_load_number = 0;
thread_local bool bbsc_startup_first_character = false;

bool BbscStartupTraceWindow(s64 now) {
    if (!bbsc_startup_trace_origin) bbsc_startup_trace_origin = now;
    return now >= bbsc_startup_trace_origin && now - bbsc_startup_trace_origin <= 120'000;
}

void BbscStartupTraceMark(const char* stage) noexcept {
    try {
    const s64 now = SteadyNowMs();
    if (!BbscStartupTraceWindow(now) || !bbsc_startup_load_begin ||
        bbsc_startup_trace_lines.fetch_add(1, std::memory_order_relaxed) >= 64) return;
    LOG_INFO(Debug, "BBSC startup timing: load {} {} at +{} ms", bbsc_startup_load_number,
             stage, now - bbsc_startup_load_begin);
    } catch (...) { /* Observation must never unwind into the game. */ }
}

struct BbscStartupScope {
    u64 offset = 0;
    s64 begin = 0;
    BbscStartupScope(u64 site) {
        if (site != 0x01938F65 && site != 0x0178D9C0 && site != 0x01308DC0 &&
            site != 0x018BBA70) return;
        const s64 now = SteadyNowMs();
        if (!BbscStartupTraceWindow(now)) return;
        if (site == 0x01938F65) {
            bbsc_startup_load_begin = now;
            ++bbsc_startup_load_number;
            bbsc_startup_first_character = false;
            BbscStartupTraceMark("spawn-point hook begins");
        } else if (site == 0x018BBA70 && !bbsc_startup_first_character) {
            bbsc_startup_first_character = true;
            BbscStartupTraceMark("first character floor setup");
        }
        if (site != 0x018BBA70) { offset = site; begin = now; }
    }
    ~BbscStartupScope() noexcept {
        try {
        if (!begin) return;
        const s64 elapsed = SteadyNowMs() - begin;
        if (offset == 0x01938F65) BbscStartupTraceMark("spawn-point hook ends");
        if (elapsed >= 50 &&
            bbsc_startup_trace_lines.fetch_add(1, std::memory_order_relaxed) < 64) {
            LOG_INFO(Debug, "BBSC startup timing: own hook {:#x} took {} ms", offset, elapsed);
        }
        } catch (...) { /* A failed diagnostic cannot fail the game callback. */ }
    }
};

void PS4_SYSV_ABI BbscStartupNativeMarker(u64 tag, const GuestRegisterSnapshot*) {
    BbscStartupTraceMark(tag == 0 ? "native spawn resolver finished" :
                                   "native character-create begins");
}

void InstallBbscStartupTimingHooks() {
    // After native spawn resolution: mov rsi,[r13+0x168] (one non-relative instruction).
    constexpr std::array<u8, 7> resolved{0x49,0x8B,0xB5,0x68,0x01,0x00,0x00};
    // Native StageFlow character creation entry: push rbp; mov rbp,rsp; push r15.
    constexpr std::array<u8, 6> create{0x55,0x48,0x89,0xE5,0x41,0x57};
    const bool a = InstallGuestCodeHook(reinterpret_cast<void*>(image_base + 0x01939196),
        std::span<const u8>{resolved}, 0, BbscStartupNativeMarker);
    const bool b = InstallGuestCodeHook(reinterpret_cast<void*>(image_base + 0x01939470),
        std::span<const u8>{create}, 1, BbscStartupNativeMarker);
    LOG_INFO(Debug, "BBSC startup timing: diagnostic hooks resolver={} create={}; 120 s / 64 lines maximum", a, b);
}
