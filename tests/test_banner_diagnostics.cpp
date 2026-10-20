#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <string_view>
#include <vector>
#define FMT_HEADER_ONLY
#include <fmt/format.h>

using u8 = uint8_t;
using u16 = uint16_t;
using u32 = uint32_t;
using u64 = uint64_t;
constexpr u64 image_base = 0x10000000;
constexpr u64 BannerTextOffset = 0x100;
std::array<u8, 8192> memory{};
std::vector<std::string> logs;
std::map<std::pair<u32, u32>, u64> slots;
struct GuestRegisterSnapshot { u64 rsi; u64 rsp; };
#define PS4_SYSV_ABI
#define LOG_INFO(category, ...) logs.push_back(fmt::format(__VA_ARGS__))

bool ReadablePointer(u64 address, size_t bytes) {
    return address >= image_base && address - image_base <= memory.size() &&
           bytes <= memory.size() - (address - image_base);
}
template<class T> T ReadValue(u64 address, size_t offset) {
    T value{};
    if (ReadablePointer(address + offset, sizeof(T))) {
        std::memcpy(&value, memory.data() + address + offset - image_base, sizeof(T));
    }
    return value;
}
template<class T> void Put(u64 address, T value) {
    assert(ReadablePointer(address, sizeof(T)));
    std::memcpy(memory.data() + address - image_base, &value, sizeof(T));
}
u64 FindBbscMessageSlot(u32 category, u32 id, u64* fmg) {
    *fmg = image_base;
    const auto it = slots.find({category, id});
    return it == slots.end() ? 0 : it->second;
}
int session_state = 3;
int GetSessionState() { return session_state; }
#include "../patches/bbsc_banner_diagnostics.inc"

void Text(u64 address, std::u16string_view text) {
    for (size_t i = 0; i < text.size(); ++i) Put<u16>(address + i * 2, text[i]);
    Put<u16>(address + text.size() * 2, 0);
}

int main() {
    constexpr u64 object = image_base + BannerTextOffset;
    constexpr u64 heap = image_base + 0x400;
    Put<u64>(object, heap);
    Text(heap, u"?ActionEventInfo?");
    assert(BbscBannerText() == "?ActionEventInfo?"); // borrowed game text
    memory.fill(0);
    Put<u64>(object + 0x20, 5);
    Put<u64>(object + 0x28, 7);
    Text(object + 0x10, u"hello");
    assert(BbscBannerText() == "hello"); // seven-character inline storage
    memory.fill(0);
    Put<u64>(object + 0x10, heap);
    Put<u64>(object + 0x20, 17);
    Put<u64>(object + 0x28, 23);
    Text(heap, u"?ActionEventInfo?");
    assert(BbscBannerText() == "?ActionEventInfo?"); // owned heap storage
    Put<u64>(object + 0x20, 4);
    Text(heap, u"\u5171\u9e23\nA");
    assert(BbscBannerText() == "\\u5171\\u9e23 A"); // lossless Chinese diagnostics
    Put<u64>(object + 0x20, 25);
    assert(BbscBannerText().empty()); // invalid size/capacity
    Put<u64>(object, 0x42);
    assert(BbscBannerText().empty()); // invalid borrowed address
    Put<u64>(object, heap);
    Text(heap, std::u16string(200, u'x'));
    assert(BbscBannerText().size() == 120); // bounded read
    Put<u64>(object, image_base + memory.size() - 2);
    Put<u16>(image_base + memory.size() - 2, u'z');
    assert(BbscBannerText() == "z"); // unreadable next page
    BbscEventTextHook(0, nullptr);
    assert(logs.empty());
    constexpr u64 stack = image_base + 0x1000;
    Put<u64>(stack, image_base + 0x1387ca8);
    GuestRegisterSnapshot registers{0, stack};
    BbscEventTextHook(0, &registers);
    assert(logs.back().find("id=0 caller=0x1387ca8 category101=false category30=false") != std::string::npos);
    registers.rsi = 20000400;
    slots[{30, 20000400}] = image_base + 0x1100;
    Put<u64>(image_base + 0x1100, 0x500);
    BbscEventTextHook(0, &registers);
    assert(logs.back().find("category101=false category30=true") != std::string::npos);
    Put<u64>(image_base + 0x1100, 0);
    BbscEventTextHook(0, &registers);
    assert(logs.back().find("category101=false category30=false") != std::string::npos);
    assert(registers.rsi == 20000400); // existing nonzero IDs are never rewritten
    Put<u64>(image_base + 0x1100, 0x500);
    Text(image_base + 0x500, u"Sent cooperator <?leaveName?> back");
    registers.rsi = 0;
    const auto before_repair = memory;
    BbscEventTextHook(0, &registers);
    assert(registers.rsi == 20000400); // reproduce role-zero selected-player dismissal
    assert(memory == before_repair); // no roles, messages, stack or world state changed
    assert(logs.back().find("recovered empty role message") != std::string::npos);
    registers.rsi = 0;
    session_state = 6;
    BbscEventTextHook(0, &registers);
    assert(registers.rsi == 0); // a guest's departure is not the host's dismissal
    session_state = 3;
    Put<u64>(stack, image_base + 0x1387df8);
    BbscEventTextHook(0, &registers);
    assert(registers.rsi == 0); // another co-op banner is not rewritten
    Put<u64>(stack, image_base + 0x1387ca8);
    Put<u64>(image_base + 0x1100, 0);
    BbscEventTextHook(0, &registers);
    assert(registers.rsi == 0); // replacement text must actually be loaded
    Put<u64>(image_base + 0x1100, 0x500);
    Put<u16>(image_base + 0x500, 0);
    BbscEventTextHook(0, &registers);
    assert(registers.rsi == 0); // an empty replacement is not used
    slots[{101, 0}] = image_base + 0x1108;
    Put<u64>(image_base + 0x1108, 0x600);
    Text(image_base + 0x500, u"Sent cooperator <?leaveName?> back");
    BbscEventTextHook(0, &registers);
    assert(registers.rsi == 0); // a valid category-101 override takes priority
    slots.erase({101, 0});
    const auto count = logs.size();
    Put<u64>(stack, image_base + 0x1bfcad6);
    BbscEventTextHook(0, &registers);
    assert(logs.size() == count); // ordinary per-frame menu help is ignored
    std::puts("PASS: banner storage, bounds, Chinese text, role-zero dismissal and unchanged unrelated calls");
}
