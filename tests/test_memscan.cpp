#include <windows.h>
#include <array>
#include <cstdio>
#include <cstring>
#include <span>
#include <vector>

static void* race_page = nullptr;
static bool arm_race = false;
static SIZE_T TestVirtualQuery(LPCVOID address, PMEMORY_BASIC_INFORMATION info, SIZE_T size) {
    const auto result = ::VirtualQuery(address, info, size);
    if (arm_race && address == race_page && result) {
        arm_race = false;
        DWORD old;
        if (!VirtualProtect(race_page, 4096, PAGE_NOACCESS, &old)) ExitProcess(91);
    }
    return result;
}
#define VirtualQuery TestVirtualQuery
#include BBSC_SCAN_SOURCE
#undef VirtualQuery

static LONG CALLBACK FatalLikeHost(EXCEPTION_POINTERS* info) {
    if (info->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        std::fprintf(stderr, "Host VEH terminated on scanner access violation\n");
        ExitProcess(3);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
static void check(bool ok, const char* test) {
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", test); ExitProcess(90); }
    std::printf("PASS: %s\n", test);
}
int main(int argc, char**) {
    AddVectoredExceptionHandler(1, FatalLikeHost);
    constexpr size_t Size = 4 * 65536;
    auto* memory = static_cast<unsigned char*>(VirtualAlloc(nullptr, Size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    check(memory != nullptr, "allocation");
    const auto base = reinterpret_cast<std::uint64_t>(memory);
    constexpr std::array<std::uint8_t, 4> needle{'S','P','T',0};
    if (argc > 1) {
        std::memcpy(memory + 8192, needle.data(), needle.size());
        race_page = memory;
        arm_race = true;
        const auto hits = Core::Bbsc::FindInMemory(needle, base, base + Size, 4);
        check(hits == std::vector<std::uint64_t>{base + 8192}, "protection changes after VirtualQuery; remaining pages still searched");
    } else {
        for (size_t offset : {size_t(100), size_t(4094), size_t(65534)})
            std::memcpy(memory + offset, needle.data(), needle.size());
        const auto all = Core::Bbsc::FindInMemory(needle, base, base + Size, 10);
        check(all == std::vector<std::uint64_t>{base+100,base+4094,base+65534}, "matches across page and block boundaries without duplicates");
        check(Core::Bbsc::FindInMemory(needle, base, base+Size, 1) == std::vector<std::uint64_t>{base+100}, "max_hits");
        check(Core::Bbsc::FindInMemory(needle, base+101, base+65534, 10) == std::vector<std::uint64_t>{base+4094}, "lo/hi bounds");
        check(Core::Bbsc::FindInMemory({}, base, base+Size, 10).empty(), "empty pattern");
        DWORD old;
        VirtualProtect(memory+12288,4096,PAGE_NOACCESS,&old);
        std::memcpy(memory+16384,needle.data(),needle.size());
        const auto holes = Core::Bbsc::FindInMemory(needle,base,base+Size,10);
        check(holes == std::vector<std::uint64_t>{base+100,base+4094,base+16384,base+65534}, "existing unreadable page skipped");
    }
    VirtualFree(memory,0,MEM_RELEASE);
}
