#include <array>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <span>
#include "../patches/bbsc_empty_host_cancel_policy.h"
#define PS4_SYSV_ABI
using u8=std::uint8_t; using u64=std::uint64_t; using s64=std::int64_t;
struct GuestRegisterSnapshot { u64 rax; };
constexpr u64 image_base=0x10000000, global=0x300000, player=0x400000;
constexpr u64 remote=0x500000, menu=0x600000;
u64 manager=0x200000;
int state=3,members=1,characters=0,hp=100,stops=0,cleanups=0;
s64 now=1000;
bool ready=true,own=true,loading=false,searching=true,stop_ok=true;
bool bbsc_spectate_active=false;
u8 MockStop(u64 p) { assert(p==manager); ++stops; if(stop_ok) state=7; return stop_ok; }
void MockCleanup(u64 p) { assert(p==remote); ++cleanups; }
const u64 MultiPlayStopRequestOffset=reinterpret_cast<u64>(&MockStop)-image_base;
const u64 WorldChrManRemoteCleanupOffset=reinterpret_cast<u64>(&MockCleanup)-image_base;
int GetSessionState(){return state;}
u64 GetMatchingState(){return manager;}
u64 GetGlobalState(){return global;}
u64 GetLocalPlayerCharacter(){return player;}
int GetCharacterHp(u64 p){assert(p==player);return hp;}
int GetBbscSessionMemberCount(u64 p){assert(p==global);return members;}
int RemotePlayerCount(){return characters;}
s64 SteadyNowMs(){return now;}
bool ReadablePointer(u64 p,std::size_t){return ready && p!=0;}
u64 BbscLocalEffectNode(int id){assert(id==9000);return searching?0x9000:0;}
template<class T>T ReadValue(u64 p,std::size_t off){
    if(p==image_base+0x05562878 && off==0)return static_cast<T>(menu);
    if(p==global && off==0x16F8)return static_cast<T>(remote);
    if(p==global && off==0x1592)return static_cast<T>(own);
    if(p==global && off==8)return static_cast<T>(loading);
    assert(false);return 0;
}
#define LOG_INFO(category,...) ((void)0)
#define LOG_ERROR(category,...) ((void)0)
bool InstallGuestCodeHook(void* p,std::span<const u8> bytes,u64,
                         void(*)(u64,const GuestRegisterSnapshot*)){
    assert(reinterpret_cast<u64>(p)==image_base+0x01BC6BE7);
    const std::array<u8,10> expected{0xC7,0x80,0x74,0x04,0,0,1,0,0,0};
    assert(bytes.size()==expected.size());
    for(std::size_t i=0;i<bytes.size();++i)assert(bytes[i]==expected[i]);
    return true;
}
#include "../patches/bbsc_empty_host_cancel.inc"

void Reset(){
    manager=0x200000;state=3;members=1;characters=0;hp=100;stops=cleanups=0;
    now=1000;ready=own=searching=stop_ok=true;loading=bbsc_spectate_active=false;
    bbsc_empty_host_cancel_ms=0;bbsc_empty_host_cancel_manager=0;
}
void Confirm(){const GuestRegisterSnapshot r{menu};BbscEmptyHostCancelConfirmed(0,&r);}
void Complete(){now=1500;searching=false;TickBbscEmptyHostCancel();}
int main(){
    InstallBbscEmptyHostCancelHook();
    Reset();Complete();assert(stops==0); // Merely having an empty room does nothing.
    Reset();Confirm();now=1600;TickBbscEmptyHostCancel();assert(stops==0);
    searching=false;TickBbscEmptyHostCancel();assert(stops==1 && cleanups==1 && state==7);
    TickBbscEmptyHostCancel();assert(stops==1); // One native teardown per confirmation.
    Reset();Confirm();now=1100;searching=false;TickBbscEmptyHostCancel();assert(stops==0);
    now=1300;TickBbscEmptyHostCancel();assert(stops==1);
    Reset();Confirm();members=2;Complete();assert(stops==0 && cleanups==0);
    members=1;Complete();assert(stops==0); // Do not defer a kick until the guest disappears.
    Reset();Confirm();characters=1;Complete();assert(stops==0);
    for(int s:{0,1,2,4,5,6,7}){Reset();Confirm();state=s;Complete();assert(stops==0);}
    Reset();state=0;Confirm();state=3;Complete();assert(stops==0);
    Reset();Confirm();loading=true;Complete();loading=false;Complete();assert(stops==0);
    Reset();Confirm();hp=0;Complete();assert(stops==0);
    Reset();Confirm();own=false;Complete();assert(stops==0);
    Reset();Confirm();ready=false;Complete();assert(stops==0);
    Reset();Confirm();manager=0x222000;Complete();assert(stops==0);
    Reset();Confirm();members=-1;Complete();assert(stops==0);
    Reset();Confirm();now=12000;searching=false;TickBbscEmptyHostCancel();assert(stops==0);
    Reset();Confirm();stop_ok=false;Complete();assert(stops==1 && cleanups==0 && state==3);
    Reset();const GuestRegisterSnapshot wrong{0};BbscEmptyHostCancelConfirmed(0,&wrong);
    Complete();assert(stops==0);
}
