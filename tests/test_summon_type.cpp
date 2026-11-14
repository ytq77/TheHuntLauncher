#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
using u8 = uint8_t;
using u64 = uint64_t;
using s32 = int32_t;
constexpr u64 image_base = 0x10000000;
constexpr u64 PlayerSlotManagerPointerOffset = 0x0553B130;
constexpr u64 slots = 0x100000;
constexpr u64 reload = 0x200000;
std::array<u8, 0x100> slot_data{}, reload_data{};
int session = 6, writes = 0;
bool readable = true, writable = true;
enum class MemoryProt { CpuWrite };
#define LOG_INFO(...) ((void)0)
int GetSessionState() { return session; }
bool ReadablePointer(u64 p, size_t n) {
    return readable && ((p == slots && n <= slot_data.size()) ||
                        (p == reload && n <= reload_data.size()));
}
bool HasMemoryAccess(u64 p, size_t n, MemoryProt) {
    return writable && p == slots + 0x8C && n == 4;
}
template<class T> T ReadValue(u64 p, size_t off) {
    if (p == image_base + PlayerSlotManagerPointerOffset) return static_cast<T>(slots);
    if (p == image_base + 0x0553D6D0) return static_cast<T>(reload);
    T value{};
    const auto& data = p == slots ? slot_data : reload_data;
    assert(off + sizeof(T) <= data.size());
    std::memcpy(&value, data.data() + off, sizeof(T));
    return value;
}
template<class T> void WriteValue(u64 p, size_t off, T value) {
    assert(p == slots && off == 0x8C && sizeof(T) == 4);
    ++writes;
    std::memcpy(slot_data.data() + off, &value, sizeof(T));
}
void Set(int state, s32 phase, s32 pending) {
    slot_data.fill(0xA5); reload_data.fill(0x5A);
    std::memcpy(slot_data.data() + 0x8C, &pending, 4);
    std::memcpy(reload_data.data() + 0x84, &phase, 4);
    session = state; readable = writable = true; writes = 0;
}
#include "../patches/bbsc_summon_type.inc"

// Recorded native order: ApplySummonType supplies 1/2/12/13, then StageFlow
// consumes pending type if != -1. Exercise the production guard, not a mirror.
s32 NativeCreate(s32 classified) {
    const auto pending = ReadValue<s32>(slots, 0x8C);
    return pending == -1 ? classified : pending;
}
int main() {
    for (s32 native : {1, 2, 12, 13}) {
        Set(6, 3, 0);
        assert(NativeCreate(native) == 0); // preview.18 recorded failure
        auto expected = slot_data;
        const s32 cleared = -1;
        std::memcpy(expected.data() + 0x8C, &cleared, 4);
        const auto untouched_reload = reload_data;
        PrepareBbscSummonedType();
        assert(NativeCreate(native) == native); // native retains each guest type
        assert(writes == 1 && slot_data == expected && reload_data == untouched_reload);
        PrepareBbscSummonedType(); assert(writes == 1); // one-shot
    }
    for (int state : {0, 1, 2, 3, 4, 5, 7, 8}) {
        Set(state, 3, 0); auto before = slot_data;
        PrepareBbscSummonedType(); assert(writes == 0 && slot_data == before);
    }
    for (s32 phase : {0, 1, 2, 4, 5}) {
        Set(6, phase, 0); auto before = slot_data;
        PrepareBbscSummonedType(); assert(writes == 0 && slot_data == before);
    }
    for (s32 pending : {-2, -1, 1, 2, 8, 12, 13}) {
        Set(6, 3, pending); auto before = slot_data;
        PrepareBbscSummonedType(); assert(writes == 0 && slot_data == before);
    }
    Set(6, 3, 0); readable = false;
    PrepareBbscSummonedType(); assert(writes == 0);
    Set(6, 3, 0); writable = false;
    PrepareBbscSummonedType(); assert(writes == 0);
    std::puts("PASS: recorded host override, native co-op/invader types, positive respawn overrides and unrelated states");
}
