// Execute the actual observer against owned fake memory, never a game process.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <optional>
#include <sstream>
#include <span>
#include <string>
#include <thread>
using u8=std::uint8_t;using u32=std::uint32_t;using u64=std::uint64_t;using s64=std::int64_t;
u64 image_base=1;
std::array<u8,0x2000> global_bytes{};
std::array<u8,125> group_bytes{};
struct Slot { int slot=0; };
std::optional<Slot> bbsc_own_world_slot=Slot{};
std::optional<Slot> BbscActiveSaveSlot(){return Slot{};}
bool EnvFlagEnabled(const char* name){const char* value=std::getenv(name);return value&&std::string(value)=="1";}
u64 GetGlobalState(){return reinterpret_cast<u64>(global_bytes.data());}
bool ReadablePointer(u64 value,size_t){return value==GetGlobalState();}
int GetSessionState(){return 3;}
u32 GetCurrentPackedMap(){return 0x15000000;}
template<class T>T ReadValue(u64 value,size_t offset){T result;std::memcpy(&result,reinterpret_cast<void*>(value+offset),sizeof(result));return result;}
u64 BbscFlagGroupStorage(u32 key){return key==12417?reinterpret_cast<u64>(group_bytes.data()):0;}
struct LampTraceScope {
    const char* label;std::string before;s64 sampled=0;u64 operation=0;
    explicit LampTraceScope(const char* name) noexcept;
    ~LampTraceScope() noexcept;
};
#include "bbsc_lamp_trace.inc"
void early_return(){LampTraceScope trace{"early-return"};return;}
int main(){
    global_bytes[0x1592]=1;group_bytes.fill(0xA5);group_bytes[103]|=0x02;
    const auto original=group_bytes;
    LampTraceTick();
    {LampTraceScope trace{"unchanged"};}
    if(group_bytes!=original)return 10;
    early_return();
    {LampTraceScope trace{"test-operation"};group_bytes[103]&=static_cast<u8>(~0x02);}
    auto expected=original;expected[103]&=static_cast<u8>(~0x02);
    LampTraceCachedSave();LampTraceTick();
    if(group_bytes!=expected)return 11;
    return 0;
}
