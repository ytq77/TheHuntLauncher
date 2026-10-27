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
int suspends=0;bool persistent=true;
bool ReadablePointer(u64 p,size_t n){return p==reinterpret_cast<u64>(menu.data())&&n<=menu.size();}
int GetSessionState(){return 3;}
void SuspendBbscPersistence(std::string_view reason){assert(reason=="confirmed System menu exit");++suspends;persistent=false;}
#define LOG_INFO(...) ((void)0)
#define LOG_ERROR(...) ((void)0)
void BbscWorldRecoveryQuit(bool confirmed) { assert(confirmed); }
#include "../patches/bbsc_menu_quit.inc"
}
int main(int argc,char** argv){
 using namespace Xbyak::util;using namespace Core;
 assert(argc==2);const std::string_view mode=argv[1];
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
 assert(suspends==0&&persistent);
 assert(entry(reinterpret_cast<u64>(menu.data()))==42);
 u32 exit_flag=0;std::memcpy(&exit_flag,menu.data()+0x25C,4);
 assert(exit_flag==1&&suspends==1&&!persistent);
 GuestRegisterSnapshot invalid{};BbscMenuQuitConfirmed(0,&invalid);assert(suspends==1);
 std::cout<<"confirmed exit releases persistence; original flag and return preserved; idle/cancel does nothing\n";
}
