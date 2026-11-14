#include <windows.h>
#include <xbyak/xbyak.h>
#include "core/cpu_patches.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <span>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (false)
u64 image_base = 0;
namespace BbscMod {
u64 EbootAddress() { return image_base; }
void LogPlain(const char*) {}
}
namespace Core {
s64 SteadyNowMs() { return 5000; }
#define LOG_INFO(...) ((void)0)
#include "../scripts/startup_trace/bbsc_startup_timing.inc"
}
int main() {
    using namespace Xbyak::util;
    using namespace Core;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    auto* image = static_cast<u8*>(VirtualAlloc(nullptr, 0x01940000,
        MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE));
    CHECK(image); image_base = reinterpret_cast<u64>(image);
    constexpr std::array<u8, 7> resolved{0x49,0x8B,0xB5,0x68,0x01,0x00,0x00};
    constexpr std::array<u8, 6> create{0x55,0x48,0x89,0xE5,0x41,0x57};
    Xbyak::CodeGenerator finish(64, image + 0x01939196);
    finish.db(resolved.data(), resolved.size()); finish.ret();
    Xbyak::CodeGenerator begin(64, image + 0x01939470);
    begin.db(create.data(), create.size()); begin.pop(r15); begin.pop(rbp);
    begin.mov(rax, 0xAABBCCDD); begin.ret();
    Xbyak::CodeGenerator call_finish(64, image + 0x1000);
    call_finish.push(r13); call_finish.mov(r13, rdi);
    call_finish.mov(rax, image_base + 0x01939196); call_finish.call(rax);
    call_finish.pop(r13); call_finish.mov(rax, rsi); call_finish.ret();
    using Finish = u64 PS4_SYSV_ABI (*)(u64);
    using Begin = u64 PS4_SYSV_ABI (*)();
    auto run_finish = reinterpret_cast<Finish>(image + 0x1000);
    auto run_begin = reinterpret_cast<Begin>(image + 0x01939470);
    std::array<u8, 0x170> data{};
    constexpr u64 expected = 0x1122334455667788;
    std::memcpy(data.data()+0x168, &expected, 8);
    const auto before = data;
    CHECK(run_finish(reinterpret_cast<u64>(data.data())) == expected);
    CHECK(run_begin() == 0xAABBCCDD);
    bbsc_startup_trace_origin = bbsc_startup_load_begin = 1000;
    bbsc_startup_load_number = 1;
    InstallBbscStartupTimingHooks();
    CHECK(image[0x01939196] == 0xE9 && image[0x01939470] == 0xE9);
    CHECK(run_finish(reinterpret_cast<u64>(data.data())) == expected);
    CHECK(run_begin() == 0xAABBCCDD);
    CHECK(data == before && bbsc_startup_trace_lines == 2);
    std::puts("PASS: real native trampolines preserve resolver load, stack prologue, return values and data");
}
