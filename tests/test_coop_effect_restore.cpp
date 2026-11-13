#include <array>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <thread>

using u64 = std::uint64_t;
using s64 = std::int64_t;
using s32 = std::int32_t;
using u32 = std::uint32_t;
using u8 = std::uint8_t;
#define PS4_SYSV_ABI __attribute__((sysv_abi))
constexpr s64 BbscReinsertSettleMs = 2'500, BbscReinsertTimeoutMs = 120'000;
constexpr s32 BbscHpPenaltyEffectId = 9006;
bool bbsc_v8_pending = false, bbsc_spectate_active = false;
std::atomic<s64> bbsc_leave_event_ms{0};
std::atomic<u64> bbsc_character_reload_generation{0};
s64 bbsc_persistence_restore_at_ms = 0, now = 100;
s32 state = 0, role = 0, hp = 100, calls = 0, last_id = 0, logged = -2;
u64 image_base = 1, matching = 1;
bool install_effect = true, reenter_add = false;
void TickBbscCoopEffectRestore();

using Character = std::array<u8, 0x1D0>;
struct Instance {
    Character chr{};
    std::array<u8, 0x10> effects{};
    std::array<u8, 0x60> node{};
};
std::array<Instance, 3> instances;
std::array<u8, 0x1598> global;
std::array<u8, 0x3F8> vtable;
Instance* current = &instances[0];

u64 Address(const auto& bytes) { return reinterpret_cast<u64>(bytes.data()); }
bool ReadablePointer(u64 p, std::size_t n) {
    const auto fits = [=](const auto& bytes) { return Address(bytes) == p && n <= bytes.size(); };
    if (fits(global) || fits(vtable)) return true;
    for (const auto& i : instances)
        if (fits(i.chr) || fits(i.effects) || fits(i.node)) return true;
    return false;
}
template<class T> T ReadValue(u64 p, std::size_t offset) {
    T v{};
    assert(ReadablePointer(p, offset + sizeof(v)));
    std::memcpy(&v, reinterpret_cast<void*>(p + offset), sizeof(v));
    return v;
}
void Put(auto& bytes, std::size_t offset, auto value) {
    assert(offset + sizeof(value) <= bytes.size());
    std::memcpy(bytes.data() + offset, &value, sizeof(value));
}
void Give(Instance& i, s32 id) {
    Put(i.effects, 8, Address(i.node));
    Put(i.node, 0x40, id);
    Put(i.node, 0x48, Address(global));
}
u64 GetGlobalState() { return Address(global); }
u64 GetMatchingState() { return matching; }
u64 GetLocalPlayerCharacter() { return Address(current->chr); }
s32 GetSessionState() { return state; }
s32 GetLocalCharacterType() { return role; }
s32 GetCharacterHp(u64) { return hp; }
s64 SteadyNowMs() { return now; }
void PS4_SYSV_ABI Add(u64 p, s32 id, u64 source, s32 a, s32 b, s32 c,
                     float f, float g, float h, float i, float j, s32 z) {
    assert(p == GetLocalPlayerCharacter() && p == source);
    assert(a == 0 && b == 0 && c == 0 && z == 0);
    assert(f == 1 && g == 1 && h == 1 && i == 1 && j == 1);
    ++calls;
    last_id = id;
    if (reenter_add) TickBbscCoopEffectRestore();
    if (install_effect) Give(*current, id);
}
bool IsEbootCodeAddress(u64 p) { return p == reinterpret_cast<u64>(&Add); }
#define LOG_INFO(level, fmt, id, active, session) (logged = (active))
#include "../patches/bbsc_coop_effect_restore.inc"

void Step(s64 ms = 0) { now += ms; TickBbscCoopEffectRestore(); }
void Setup(bool host, bool with_effect = true) {
    state = 0;
    Step();
    state = host ? 3 : 6;
    global.fill(0);
    global[0x1592] = host;
    role = host ? 0 : 1;
    hp = 100;
    matching = 1;
    bbsc_v8_pending = bbsc_spectate_active = false;
    bbsc_leave_event_ms = bbsc_persistence_restore_at_ms = 0;
    bbsc_character_reload_generation = 0;
    calls = last_id = 0;
    logged = -2;
    install_effect = true;
    reenter_add = false;
    instances = {};
    for (auto& i : instances) {
        Put(i.chr, 0, Address(vtable));
        Put(i.chr, 0x1C8, Address(i.effects));
    }
    Put(vtable, 0x3F0, reinterpret_cast<u64>(&Add));
    current = &instances[0];
    if (with_effect) Give(*current, host ? 9000 : 9006);
    Step();
}

int main() {
    Setup(false);
    current = &instances[1];
    Give(*current, 9006); // Real guest positive: vanilla already restored it.
    Step();
    Step(3'000);
    assert(calls == 0);

    Setup(false);
    global[8] = 1; // A loading indicator alone is not a character rebuild.
    Step();
    Put(current->effects, 8, u64{0});
    global[8] = 0;
    Step();
    Step(3'000);
    assert(calls == 0);

    Setup(false);
    Put(current->effects, 8, u64{0});
    ++bbsc_character_reload_generation; // Trusted native ReloadFinish, same allocation.
    Step();
    Step(2'499);
    assert(calls == 0);
    Step(1);
    assert(calls == 1 && last_id == 9006);
    Step(); // Observe vanilla's active node in the new generation.
    Put(current->effects, 8, u64{0}); // Ordinary removal after that reload stays removed.
    Step();
    Step(3'000);
    assert(calls == 1);

    Setup(false);
    ++bbsc_character_reload_generation;
    Step(); // Native 9006 survived; no redundant add even with a rebuild generation.
    Step(3'000);
    assert(calls == 0);

    Setup(false);
    Put(current->effects, 8, u64{0});
    ++bbsc_character_reload_generation;
    Step();
    Step(2'000);
    ++bbsc_character_reload_generation; // A second rebuild restarts the stable window.
    Step();
    Step(2'499);
    assert(calls == 0);
    reenter_add = true;
    Step(1);
    assert(calls == 1);

    Setup(false);
    Put(current->effects, 8, u64{0});
    ++bbsc_character_reload_generation;
    Step();
    now += 2'500;
    std::array<std::thread, 8> callbacks;
    for (auto& thread : callbacks) thread = std::thread(TickBbscCoopEffectRestore);
    for (auto& thread : callbacks) thread.join();
    assert(calls == 1); // Concurrent callbacks still issue only one native add.

    Setup(true);
    global[8] = 1;
    current = &instances[1];
    Step(4'000);
    global[8] = 0;
    bbsc_v8_pending = true;
    Step(4'000);
    assert(calls == 0);
    bbsc_v8_pending = false;
    Step();
    Step(2'499);
    assert(calls == 0);
    Step(1);
    assert(calls == 0); // Never restart the host's recruitment after travel.
    Step(3'000);
    assert(calls == 0);

    Setup(false);
    current = &instances[1];
    Step();
    Step(2'500);
    assert(calls == 1 && last_id == 9006 && logged == 1);
    Step(); // Record the restored effect, then reload again without v8.
    current = &instances[2];
    Step();
    Step(2'500);
    assert(calls == 2 && last_id == 9006);

    Setup(true);
    Put(current->effects, 8, u64{0}); // Native Stop Hunt Bell.
    Step();
    current = &instances[1];
    Step();
    Step(3'000);
    assert(calls == 0);

    Setup(true, false); // Never rang the bell.
    current = &instances[1];
    Step();
    Step(3'000);
    assert(calls == 0);

    Setup(true);
    current = &instances[1];
    Give(*current, 9000); // Vanilla already restored it.
    Step();
    Step(3'000);
    assert(calls == 0);

    for (int guard = 0; guard < 7; ++guard) {
        Setup(false);
        current = &instances[1];
        Step();
        if (guard == 0) bbsc_persistence_restore_at_ms = now;
        if (guard == 1) bbsc_leave_event_ms = now;
        if (guard == 2) ++matching;
        if (guard == 3) state = 0;
        if (guard == 4) role = 0;
        if (guard == 5) Put(vtable, 0x3F0, u64{0x1234});
        if (guard == 6) Step(BbscReinsertTimeoutMs + 1);
        Step(3'000);
        assert(calls == 0);
    }

    Setup(false);
    current = &instances[1];
    Step();
    Put(current->effects, 8, u64{0x1234});
    Step(3'000);
    Put(current->effects, 8, u64{0});
    Step();
    assert(calls == 0);
    install_effect = false;
    Step(2'500);
    Step(3'000);
    assert(calls == 1 && logged == 0);
    std::cout << "PASS: guest restore only; no host bell restart; session, role and load guards; one native add\n";
}
