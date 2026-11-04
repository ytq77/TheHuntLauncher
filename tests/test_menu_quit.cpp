#include <windows.h>
#include <xbyak/xbyak.h>
#include "core/cpu_patches.h"
#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include <span>
#include <string_view>
u64 image_base=0;
namespace BbscMod {
u64 EbootAddress(){return image_base;}
void LogPlain(const char*){}
}
namespace Core {
std::array<u8,0x300> menu{};
std::array<u8,0x1800> global{};
std::array<u8,0x500> player{};
int session=3,hp=100;u32 current_map=0x17000000;
bool global_readable=true,global_writable=true;
int suspends=0;bool persistent=true;
bool ReadablePointer(u64 p,size_t n){
    return (p==reinterpret_cast<u64>(menu.data())&&n<=menu.size()) ||
           (global_readable&&p==reinterpret_cast<u64>(global.data())&&n<=global.size());
}
template<class T>T ReadValue(u64 p,size_t offset){T value{};std::memcpy(&value,reinterpret_cast<void*>(p+offset),sizeof(T));return value;}
template<class T>bool WriteValue(u64 p,size_t offset,T value){
    if(p!=reinterpret_cast<u64>(global.data())||!global_writable||offset+sizeof(T)>global.size())return false;
    std::memcpy(global.data()+offset,&value,sizeof(T));return true;
}
int GetSessionState(){return session;}
u64 GetGlobalState(){return reinterpret_cast<u64>(global.data());}
u64 GetLocalPlayerCharacter(){return reinterpret_cast<u64>(player.data());}
int GetCharacterHp(u64 p){assert(p==GetLocalPlayerCharacter());return hp;}
u32 GetCurrentPackedMap(){return current_map;}
void SuspendBbscPersistence(std::string_view reason){assert(reason=="confirmed System menu exit");++suspends;persistent=false;}
#define LOG_INFO(...) ((void)0)
#define LOG_ERROR(...) ((void)0)
void BbscWorldRecoveryQuit(bool confirmed) { assert(confirmed); }
#include "../patches/bbsc_menu_quit.inc"
}
int main(int argc,char** argv){
 using namespace Xbyak::util;using namespace Core;
 assert(argc==2);const std::string_view mode=argv[1];
 bool discard=false;
 WriteValue<u32>(GetGlobalState(),0x0C,0xFFFFFFFF);
 WriteValue<s32>(GetGlobalState(),0x10,-1);
 WriteValue<s32>(GetGlobalState(),0x153C,-1);
 global[0x1592]=1;
 if(mode!="valid"&&mode!="mismatch"){
    session=0;WriteValue<u32>(GetGlobalState(),0x0C,current_map);
    if(mode=="stale")discard=true;
    else if(mode=="active")global[8]=1;
    else if(mode=="different-map")WriteValue<u32>(GetGlobalState(),0x0C,0x18000000);
    else if(mode=="guest")session=6;
    else if(mode=="host")session=3;
    else if(mode=="foreign-world")global[0x1592]=0;
    else if(mode=="invalid-global")global_readable=false;
    else if(mode=="dead")hp=0;
    else if(mode=="unwritable")global_writable=false;
    else if(mode=="empty")WriteValue<u32>(GetGlobalState(),0x0C,0xFFFFFFFF);
    else if(mode=="pending-mode")WriteValue<s32>(GetGlobalState(),0x1538,2);
    else if(mode=="stale-lamp"){WriteValue<s32>(GetGlobalState(),0x153C,2302950);discard=true;}
    else if(mode=="observed-lamp"){
        current_map=0x18010000;WriteValue<u32>(GetGlobalState(),0x0C,current_map);
        WriteValue<s32>(GetGlobalState(),0x153C,2412952);WriteValue<s32>(GetGlobalState(),0x1540,101163);discard=true;
    }
    else if(mode=="active-lamp"){global[8]=1;WriteValue<s32>(GetGlobalState(),0x153C,2412952);}
    else if(mode=="invalid-point")WriteValue<s32>(GetGlobalState(),0x153C,-2);
    else if(mode=="cleared-lamp"){
        WriteValue<u32>(GetGlobalState(),0x0C,0xFFFFFFFF);
        WriteValue<s32>(GetGlobalState(),0x153C,2412952);WriteValue<s32>(GetGlobalState(),0x1540,101163);
    }
    else if(mode=="pending-info")WriteValue<s32>(GetGlobalState(),0x10,2302950);
    else if(mode=="unknown-map")current_map=0;
    else assert(false);
 }
 #ifdef EXPECT_ORIGINAL
 discard=false;
 #endif
 const auto before_global=global;
 const auto before_player=player;
 auto* image=static_cast<u8*>(VirtualAlloc(nullptr,0x01C10000,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE));
 assert(image);image_base=reinterpret_cast<u64>(image);
 auto* site=image+BbscMenuQuitConfirmedOffset;
 Xbyak::CodeGenerator body(64,site);body.db(BbscMenuQuitConfirmedBytes.data(),BbscMenuQuitConfirmedBytes.size());body.mov(eax,42);body.ret();
 Xbyak::CodeGenerator enter(64,image+0x1000);enter.mov(rax,rdi);enter.jmp(site,Xbyak::CodeGenerator::T_NEAR);
 using Entry=u64 PS4_SYSV_ABI(*)(u64);
 auto entry=reinterpret_cast<Entry>(image+0x1000);
 assert(entry(reinterpret_cast<u64>(menu.data()))==42);
 std::fill(menu.begin(),menu.end(),0);
 if(mode=="mismatch")site[3]^=1;
 InstallBbscMenuQuitHook();
 if(mode=="mismatch"){assert(site[0]!=0xe9&&suspends==0&&persistent);return 0;}
 assert(site[0]==0xe9);
 // Merely having the hook installed (dialog open/Cancel path) does nothing.
 assert(suspends==0&&persistent&&global==before_global);
 assert(entry(reinterpret_cast<u64>(menu.data()))==42);
 u32 exit_flag=0;std::memcpy(&exit_flag,menu.data()+0x25C,4);
 assert(exit_flag==1&&suspends==1&&!persistent);
 auto expected=before_global;
 if(discard){const u32 empty=0xFFFFFFFF;std::memcpy(expected.data()+0x0C,&empty,4);}
 assert(global==expected&&player==before_player);
 for(size_t i=0;i<menu.size();++i)assert(menu[i]==(i==0x25C?1:0));
 GuestRegisterSnapshot invalid{};BbscMenuQuitConfirmed(0,&invalid);assert(suspends==1);
 std::cout<<"PASS: "<<mode<<"; confirmed exit flag/return preserved; idle/cancel unchanged; discarded target="<<discard<<"\n";
}
