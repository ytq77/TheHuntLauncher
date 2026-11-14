#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <span>
#include <string>
#include <vector>
#define FMT_HEADER_ONLY
#include <fmt/format.h>
using u8 = uint8_t; using u64 = uint64_t; using s64 = int64_t;
#define PS4_SYSV_ABI
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (false)
constexpr u64 image_base = 0x10000000;
struct GuestRegisterSnapshot { u64 rax, rbp, rsp; };
std::vector<std::string> logs;
bool fail_logging = false;
s64 now_ms = 1000;
s64 SteadyNowMs() { return now_ms; }
#define LOG_INFO(category, ...) do { if (fail_logging) throw 1; logs.push_back(fmt::format(__VA_ARGS__)); } while (false)
int installs = 0;
bool InstallGuestCodeHook(void* p, std::span<const u8> bytes, u64 tag,
                         void(*)(u64, const GuestRegisterSnapshot*)) {
    const std::array<u8, 7> resolved{0x49,0x8B,0xB5,0x68,0x01,0x00,0x00};
    const std::array<u8, 6> create{0x55,0x48,0x89,0xE5,0x41,0x57};
    const std::span<const u8> expected = tag == 0 ? std::span<const u8>(resolved) : std::span<const u8>(create);
    CHECK(reinterpret_cast<u64>(p) == image_base + (tag == 0 ? 0x01939196 : 0x01939470));
    CHECK(bytes.size() == expected.size());
    for (size_t i = 0; i < bytes.size(); ++i) CHECK(bytes[i] == expected[i]);
    ++installs;
    return true;
}
#include "../scripts/startup_trace/bbsc_startup_timing.inc"
int main() {
    InstallBbscStartupTimingHooks(); CHECK(installs == 2); logs.clear();
    { BbscStartupScope ignored(123); now_ms += 100; }
    CHECK(logs.empty());
    { BbscStartupScope load(0x01938F65); now_ms += 5; }
    CHECK(logs.size() == 2 && logs[1].find("+5 ms") != std::string::npos);
    const GuestRegisterSnapshot r{0xAA,0xBB,0xCC};
    now_ms += 4000; BbscStartupNativeMarker(0, &r);
    CHECK(logs.back().find("+4005 ms") != std::string::npos);
    CHECK(r.rax == 0xAA && r.rbp == 0xBB && r.rsp == 0xCC);
    now_ms += 2; BbscStartupNativeMarker(1, &r);
    { BbscStartupScope floor(0x018BBA70); }
    const auto before = logs.size();
    { BbscStartupScope floor_again(0x018BBA70); }
    CHECK(logs.size() == before);
    { BbscStartupScope tick(0x01308DC0); now_ms += 200; }
    CHECK(logs.back().find("took 200 ms") != std::string::npos);
    for (int i = 0; i < 100; ++i) BbscStartupNativeMarker(1, &r);
    CHECK(logs.size() <= 65); // installation message is outside the observation budget
    const auto capped = logs.size(); now_ms += 121000;
    { BbscStartupScope load(0x01938F65); }
    BbscStartupNativeMarker(1, &r);
    CHECK(logs.size() == capped);
    // A broken logging sink must not unwind through an observed native frame.
    now_ms = 2000; bbsc_startup_trace_lines = 0; fail_logging = true;
    BbscStartupNativeMarker(0, &r);
    { BbscStartupScope tick(0x01308DC0); now_ms += 200; }
    CHECK(r.rax == 0xAA && r.rbp == 0xBB && r.rsp == 0xCC);
    std::puts("PASS: native load markers, own-hook duration, unchanged registers, line/time bounds");
}
