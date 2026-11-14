#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include <optional>
#include <string_view>
#include <cstdint>
using u64=std::uint64_t; using u32=std::uint32_t; using u8=std::uint8_t;
#define PS4_SYSV_ABI __attribute__((sysv_abi))
#define LOG_INFO(...) ((void)0)
enum class MemoryProt { CpuWrite };
std::array<u8,0x1800> global{};
u64 player=0x123456;
int session=0,type=0,hp=100,home_calls=0;
u32 map=0x18010000;
bool readable=true,writable=true,transform_ok=true;
struct Transform {std::array<float,4> position{-127.5f,-27.f,61.7f,1.f},rotation{0.f,1.2f,0.f,0.f};};
u64 GetGlobalState(){return reinterpret_cast<u64>(global.data());}
u64 GetLocalPlayerCharacter(){return player;}
int GetCharacterHp(u64 p){return p?hp:0;}
int GetSessionState(){return session;}
int GetLocalPlayerType(){return type;}
u32 GetCurrentPackedMap(){return map;}
bool IsUsablePackedMap(u32 m){return m!=0 && m!=0xffffffff;}
bool ReadablePointer(u64 p,size_t n){return readable&&p==GetGlobalState()&&n<=global.size();}
bool HasMemoryAccess(u64 p,size_t n,MemoryProt){return writable&&p>=GetGlobalState()&&p+n<=GetGlobalState()+global.size();}
template<class T>T ReadValue(u64 p,size_t n){T v{};std::memcpy(&v,reinterpret_cast<void*>(p+n),sizeof(v));return v;}
std::optional<Transform> ReadCharacterTransform(u64){return transform_ok?std::optional{Transform{}}:std::nullopt;}
void PS4_SYSV_ABI SendHome(){++home_calls;global[8]=1;}
u64 image_base=reinterpret_cast<u64>(&SendHome)-0x0138A4A0;
#include "../patches/bbsc_guest_return.inc"

void reset(){global.fill(0x5a);global[8]=0;global[0x1592]=1;session=0;type=0;hp=100;home_calls=0;
    map=0x18010000;readable=true;writable=true;transform_ok=true;bbsc_guest_return_origin={};}
void guest(){session=0;type=1;global[0x1592]=0;}
int main(){
    reset();const auto before=global;CaptureBbscGuestReturnOrigin();assert(bbsc_guest_return_origin.valid&&global==before);
    guest();auto expected=global;const u32 target=map;
    const Transform origin{};
    std::memcpy(expected.data()+0x14f0,&target,4);std::memcpy(expected.data()+0x1500,origin.position.data(),16);
    std::memcpy(expected.data()+0x1510,origin.rotation.data(),16);
    assert(RestoreBbscGuestReturnPlacement(GetGlobalState())&&global==expected);
    ReturnBbscGuestAfterSessionEnd();assert(home_calls==1&&global[8]==1);
    ReturnBbscGuestAfterSessionEnd();assert(home_calls==1); // no duplicate load request
    int guarded=0;
    for(auto mode: {"active-guest","host","own-world","readonly","unknown-origin","dead","loading"}){
        reset();CaptureBbscGuestReturnOrigin();guest();
        if(mode==std::string_view("active-guest"))session=6;
        else if(mode==std::string_view("host")){session=3;type=0;}
        else if(mode==std::string_view("own-world"))global[0x1592]=1;
        else if(mode==std::string_view("readonly"))writable=false;
        else if(mode==std::string_view("unknown-origin"))bbsc_guest_return_origin={};
        else if(mode==std::string_view("dead"))hp=0;
        else if(mode==std::string_view("loading"))global[8]=1;
        const auto intact=global;ReturnBbscGuestAfterSessionEnd();assert(home_calls==0&&global==intact);++guarded;
    }
    reset();CaptureBbscGuestReturnOrigin();map=0;CaptureBbscGuestReturnOrigin();assert(!bbsc_guest_return_origin.valid);
    reset();CaptureBbscGuestReturnOrigin();global[0x1592]=0;CaptureBbscGuestReturnOrigin();assert(!bbsc_guest_return_origin.valid);
    reset();CaptureBbscGuestReturnOrigin();transform_ok=false;CaptureBbscGuestReturnOrigin();assert(!bbsc_guest_return_origin.valid);
    std::cout<<"PASS: captured origin; exact return placement; immediate native home; no repeat load; "<<guarded<<" guards; stale capture rejected\n";
}
