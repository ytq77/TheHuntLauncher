#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using u64 = uint64_t;
u64 EbootBase = 0, EbootSize = 4096;
struct GuardedSlot {
    u64 slot_offset, wrapper;
    void** original;
    const char* name;
    int rewritten = 0;
};
std::mutex guarded_mutex;
std::vector<GuardedSlot> guarded_slots;
#define LOG_INFO(...) ((void)0)

bool fail_protect = false, fail_write = false, change_after_read = false;
int reads = 0;
constexpr u64 external = 0x7fff00001230ULL, replacement = 0x7fff00004560ULL;
BOOL TestRead(HANDLE process, LPCVOID from, LPVOID to, SIZE_T size, SIZE_T* copied) {
    const BOOL ok = ReadProcessMemory(process, from, to, size, copied);
    if (change_after_read && ++reads == 1) {
        SIZE_T ignored = 0;
        WriteProcessMemory(process, const_cast<void*>(from), &replacement, 8, &ignored);
    }
    return ok;
}
BOOL TestProtect(LPVOID address, SIZE_T size, DWORD protect, PDWORD old) {
    if (fail_protect) return FALSE;
    return VirtualProtect(address, size, protect, old);
}
BOOL TestWrite(HANDLE process, LPVOID to, LPCVOID from, SIZE_T size, SIZE_T* copied) {
    if (fail_write) { *copied = 0; return FALSE; }
    return WriteProcessMemory(process, to, from, size, copied);
}

#define ReadProcessMemory TestRead
#define VirtualProtect TestProtect
#define WriteProcessMemory TestWrite
#ifdef LEGACY
#include "build/legacy_guard.inc"
#else
#include "../patches/bbsc_slot_guard.inc"
#endif
#undef ReadProcessMemory
#undef VirtualProtect
#undef WriteProcessMemory

void set(void* page, u64 value) {
    SIZE_T copied = 0;
    assert(WriteProcessMemory(GetCurrentProcess(), page, &value, 8, &copied) && copied == 8);
}
u64 get(void* page) {
    u64 value = 0; SIZE_T copied = 0;
    assert(ReadProcessMemory(GetCurrentProcess(), page, &value, 8, &copied) && copied == 8);
    return value;
}
LONG CALLBACK ExpectedLegacyFault(EXCEPTION_POINTERS* exception) {
    if (exception->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION &&
        exception->ExceptionRecord->ExceptionInformation[1] == EbootBase)
        ExitProcess(42); // Prove the fault without a WER dialog or a dump.
    return EXCEPTION_CONTINUE_SEARCH;
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string mode = argv[1];
    auto* page = VirtualAlloc(nullptr, 4096, MEM_RESERVE|MEM_COMMIT, PAGE_READWRITE);
    assert(page);
    EbootBase = reinterpret_cast<u64>(page);
    void* original = reinterpret_cast<void*>(0x1230);
    GuardedSlot guarded{0, 0x7fff00007890ULL, &original, "fixture"};
    set(page, external);
    DWORD old = 0;
#ifdef LEGACY
    AddVectoredExceptionHandler(1, ExpectedLegacyFault);
    assert(VirtualProtect(page, 4096, PAGE_NOACCESS, &old));
    guarded_slots.push_back(guarded);
    SlotGuardThread(nullptr);
#else
    if (mode == "noaccess" || mode == "unmapped") {
        if (mode == "noaccess") assert(VirtualProtect(page, 4096, PAGE_NOACCESS, &old));
        else { assert(VirtualFree(page, 0, MEM_RELEASE)); page = nullptr; }
        assert(!RefreshGuardedSlot(guarded));
        assert(original == reinterpret_cast<void*>(0x1230) && guarded.rewritten == 0);
    } else if (mode == "null" || mode == "guest" || mode == "already") {
        const u64 value = mode == "null" ? 0 : mode == "guest" ? EbootBase+128 : guarded.wrapper;
        set(page, value);
        assert(!RefreshGuardedSlot(guarded) && get(page) == value && guarded.rewritten == 0);
    } else if (mode == "protect-failure" || mode == "write-failure" || mode == "loader-change") {
        fail_protect = mode == "protect-failure";
        fail_write = mode == "write-failure";
        change_after_read = mode == "loader-change";
        assert(!RefreshGuardedSlot(guarded));
        assert(get(page) == (change_after_read ? replacement : external));
        assert(guarded.rewritten == 0);
        if (!fail_write) assert(original == reinterpret_cast<void*>(0x1230));
    } else if (mode == "normal" || mode == "readonly") {
        if (mode == "readonly") assert(VirtualProtect(page, 4096, PAGE_READONLY, &old));
        assert(RefreshGuardedSlot(guarded));
        assert(get(page) == guarded.wrapper && original == reinterpret_cast<void*>(external));
        assert(guarded.rewritten == 1 && !RefreshGuardedSlot(guarded));
        MEMORY_BASIC_INFORMATION info{};
        assert(VirtualQuery(page, &info, sizeof(info)));
        assert(info.Protect == (mode == "readonly" ? PAGE_READONLY : PAGE_READWRITE));
    } else if (mode == "race-protect" || mode == "race-decommit") {
        std::atomic<bool> stop = false;
        std::atomic<unsigned> changes = 0;
        std::thread mapper([&] {
            while (!stop.load()) {
                DWORD ignored;
                if (mode == "race-protect") {
                    VirtualProtect(page, 4096, PAGE_NOACCESS, &ignored);
                    VirtualProtect(page, 4096, PAGE_READWRITE, &ignored);
                } else {
                    VirtualFree(page, 4096, MEM_DECOMMIT);
                    VirtualAlloc(page, 4096, MEM_COMMIT, PAGE_READWRITE);
                }
                SIZE_T copied;
                WriteProcessMemory(GetCurrentProcess(), page, &external, 8, &copied);
                ++changes;
            }
        });
        for (int i=0; i<50000; ++i) RefreshGuardedSlot(guarded);
        stop = true;
        mapper.join();
        assert(changes > 0);
        std::cout << "mapping_changes=" << changes << " ";
    } else return 2;
    if (page) assert(VirtualFree(page, 0, MEM_RELEASE));
    std::cout << mode << " passed\n";
    return 0;
#endif
}
