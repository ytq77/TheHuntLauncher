#include <array>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <span>
#define PS4_SYSV_ABI
using u8 = uint8_t;
using u32 = uint32_t;
using u64 = uint64_t;
struct GuestRegisterSnapshot { u64 rax, rbx, rcx, rdx, rsi, rdi, rflags; };
constexpr u64 image_base = 0x10000000;
int suppressed = 0;
#define LOG_INFO(category, ...) (++suppressed)
#define LOG_ERROR(category, ...) assert(false)
bool InstallGuestCodeHook(void*, std::span<const u8> bytes, u64,
                         void (*)(u64, const GuestRegisterSnapshot*)) {
    assert(bytes.size() == 6);
    return true;
}
#include "../patches/bbsc_insight_refund.inc"

int main() {
    const GuestRegisterSnapshot base{11, 22, 33, 1, 9185, 66, 77};
    auto r = base;
    BbscInsightRefundFlagHook(0, &r);
    assert(std::memcmp(&r, &base, sizeof(r)) == 0); // A paid/unverified bell retains its refund.
    bbsc_zero_cost_bell_ready = true;
    for (u64 flag : {9184, 9186, 9199, 4047, 6622}) {
        r = base; r.rsi = flag;
        const auto before = r;
        BbscInsightRefundFlagHook(0, &r);
        assert(std::memcmp(&r, &before, sizeof(r)) == 0);
    }
    r = base; r.rdx = 0;
    const auto clear = r;
    BbscInsightRefundFlagHook(0, &r);
    assert(std::memcmp(&r, &clear, sizeof(r)) == 0); // Native clear still works.
    for (int cycle = 0; cycle < 3; ++cycle) {
        r = base;
        BbscInsightRefundFlagHook(0, &r);
        auto expected = base; expected.rdx = 0;
        assert(std::memcmp(&r, &expected, sizeof(r)) == 0); // Only the refund's on/off argument changes.
    }
    assert(suppressed == 3);
    BbscInsightRefundFlagHook(0, nullptr);
}
