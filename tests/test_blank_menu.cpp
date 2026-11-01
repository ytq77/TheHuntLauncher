#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <span>
using u8 = uint8_t; using u32 = uint32_t; using u64 = uint64_t; using s32 = int32_t;
#define PS4_SYSV_ABI
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (false)
#define LOG_INFO(...) ((void)0)
#define LOG_ERROR(...) ((void)0)
constexpr u64 image_base = 0x10000000, global = 0x200000, player = 0x300000;
struct GuestRegisterSnapshot { u64 rax, rbp, rsp; };
int session = 6, slot_type = 1, chr_type = 1, hp = 100;
bool ready = true, loading = false, own = false, bbsc_spectate_active = false;
bool install_ok = true;
u32 goods = 11251;
int GetSessionState() { return session; }
int GetLocalPlayerType() { return slot_type; }
int GetLocalCharacterType() { return chr_type; }
u64 GetGlobalState() { return global; }
u64 GetLocalPlayerCharacter() { return player; }
int GetCharacterHp(u64 p) { CHECK(p == player); return hp; }
bool ReadablePointer(u64 p, size_t) { return ready && (p == global || p == player); }
template<class T> T ReadValue(u64 p, size_t off) {
    if (p == global && off == 8) return static_cast<T>(loading);
    if (p == global && off == 0x1592) return static_cast<T>(own);
    if (p == player && off == 0x1C0) return static_cast<T>(goods);
    CHECK(false); return {};
}
bool InstallGuestCodeHook(void* p, std::span<const u8> bytes, u64 tag,
                         void(*)(u64, const GuestRegisterSnapshot*)) {
    CHECK(reinterpret_cast<u64>(p) == image_base + 0x01BBC625 && tag == 0);
    constexpr std::array<u8, 6> expected{0x89,0x85,0xB4,0xDD,0xFF,0xFF};
    CHECK(bytes.size() == expected.size());
    for (size_t i = 0; i < expected.size(); ++i) CHECK(bytes[i] == expected[i]);
    return install_ok;
}
#include "../patches/bbsc_blank_menu.inc"

void Reset() {
    session = 6; slot_type = chr_type = 1; hp = 100; goods = 11251;
    ready = true; loading = own = bbsc_spectate_active = false;
}
void CheckMenu(u64 native_class, u64 expected) {
    GuestRegisterSnapshot r{native_class, 0xAA123456, 0xBB789ABC};
    BbscBlankMenuRole(0, &r);
    CHECK(r.rax == expected && r.rbp == 0xAA123456 && r.rsp == 0xBB789ABC);
}
int main() {
    InstallBbscBlankMenuHook(); install_ok = false; InstallBbscBlankMenuHook();
    Reset(); CheckMenu(0, 7); // type-1 guest with missing menu role
    // Never change an existing native class, including covenant/invader variants.
    for (u64 cls : std::array<u64, 8>{1, 7, 8, 18, 21, 22, 23, 0xFFFFFFFF}) CheckMenu(cls, cls);
    for (int state : {0, 1, 2, 3, 4, 5, 7, 8}) {
        Reset(); session = state; CheckMenu(0, 0);
    }
    for (int type : {-1, 0, 2, 8, 12, 13}) {
        Reset(); slot_type = type; CheckMenu(0, 0);
        Reset(); chr_type = type; CheckMenu(0, 0);
    }
    for (u32 id : std::array<u32, 6>{111, 4016, 4121, 11201, 4313, 0xFFFFFFFF}) {
        Reset(); goods = id; CheckMenu(0, 0);
    }
    Reset(); ready = false; CheckMenu(0, 0);
    Reset(); loading = true; CheckMenu(0, 0);
    Reset(); own = true; CheckMenu(0, 0);
    Reset(); hp = 0; CheckMenu(0, 0);
    Reset(); bbsc_spectate_active = true; CheckMenu(0, 0);
    BbscBlankMenuRole(0, nullptr);
    std::puts("PASS: missing cooperator Blank class repaired; native roles, hosts, invaders, loading, death and other items unchanged");
}
