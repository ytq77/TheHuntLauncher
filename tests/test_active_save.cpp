// Compile the actual candidate policy against a synthetic native memory layout.
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <vector>
#define FMT_HEADER_ONLY
#include <fmt/format.h>
using u8=uint8_t;using u32=uint32_t;using u64=uint64_t;using s32=int32_t;
#define PS4_SYSV_ABI
#define LOG_INFO(...) ((void)0)
#define LOG_WARNING(...) ((void)0)
struct GuestRegisterSnapshot {u64 rdi=0,rsi=0,rdx=0,rsp=0,rbp=0,r14=0;};
struct BbscSaveSlot{std::string user;int slot=-1;};
std::vector<u8> image(0x0553B138);
std::array<u8,0x1760> global{};
std::array<u8,0x10> holder{};
std::array<u8,0x400> player{};
std::array<u8,84> role{};
u64 image_base=reinterpret_cast<u64>(image.data());
template<class T>void put(u64 address,size_t offset,T v){std::memcpy(reinterpret_cast<void*>(address+offset),&v,sizeof(v));}
template<class T>T ReadValue(u64 address,size_t offset){T v{};if(address)std::memcpy(&v,reinterpret_cast<void*>(address+offset),sizeof(v));return v;}
bool ReadablePointer(u64 address,size_t){return address!=0;}
u64 GetGlobalState(){return reinterpret_cast<u64>(global.data());}
using Callback=void(*)(u64,const GuestRegisterSnapshot*);
bool InstallGuestCodeHook(void*,std::span<const u8>,u64,Callback){return true;}
#include "../patches/bbsc_active_save.inc"
int main(int argc,char**argv){
 assert(argc==2);
 const std::filesystem::path root=argv[1];
 std::filesystem::create_directories(root);
 const auto file=root/"userdata0001";
 {std::ofstream stream(file,std::ios::binary);stream.seekp(0x140000-1);stream.put(0);}
 _putenv_s("BBSC_SAVE_DIR",root.string().c_str());
 put(image_base,0x0553B130,reinterpret_cast<u64>(holder.data()));
 put(reinterpret_cast<u64>(holder.data()),8,reinterpret_cast<u64>(player.data()));
 const auto manager=GetGlobalState();
 put(manager,0x1490,s32{1});put(manager,0x1751,u8{100});
 put(reinterpret_cast<u64>(role.data()),0,u32{0x41});
 u64 caller=image_base+0x0193F823;
 GuestRegisterSnapshot r{manager,reinterpret_cast<u64>(role.data()),0x140000,
     reinterpret_cast<u64>(&caller),reinterpret_cast<u64>(&caller)-8,1};
 bbsc_active_save_hooks=true;
 assert(!BbscNativeActiveSaveSlot()); // A scalar value alone is never authority.
 BbscActiveSaveLoadBegin(0,&r);assert(!BbscNativeActiveSaveSlot());
 r.r14=0;BbscActiveSaveLoadEnd(0,&r);assert(!BbscNativeActiveSaveSlot());
 r.r14=1;BbscActiveSaveLoadBegin(0,&r);BbscActiveSaveLoadEnd(0,&r);
 assert(BbscNativeActiveSaveSlot()->slot==1);
 const auto lineage=BbscNativeActiveSaveToken()->lineage;
 BbscActiveSaveLoadBegin(0,&r);BbscActiveSaveLoadEnd(0,&r);
 assert(BbscNativeActiveSaveToken()->lineage==lineage); // Same role may reload across maps.
 // An unrelated, newer save is ignored; no file timestamps are consulted.
 {std::ofstream stream(root/"userdata0000");stream<<"newer unrelated character";}
 assert(BbscNativeActiveSaveSlot()->slot==1);
 put(manager,0x1490,s32{0});assert(!BbscNativeActiveSaveSlot());
 put(manager,0x1490,s32{1});
 assert(!BbscNativeActiveSaveSlot()); // Restoring the scalar cannot resurrect a revoked load.
 BbscActiveSaveLoadBegin(0,&r);BbscActiveSaveLoadEnd(0,&r);
 assert(BbscNativeActiveSaveSlot()->slot==1);
 InvalidateBbscActiveSave();assert(!BbscNativeActiveSaveSlot());
 caller=image_base+0x123456;BbscActiveSaveLoadBegin(0,&r);BbscActiveSaveLoadEnd(0,&r);
 assert(!BbscNativeActiveSaveSlot());
 caller=image_base+0x01C2B453;put(manager,0x1751,u8{0});
 BbscActiveSaveLoadBegin(0,&r);BbscActiveSaveLoadEnd(0,&r);assert(!BbscNativeActiveSaveSlot());
 put(manager,0x1751,u8{100});
 BbscActiveSaveLoadBegin(0,&r);++r.rbp;BbscActiveSaveLoadEnd(0,&r);assert(!BbscNativeActiveSaveSlot());
 --r.rbp;BbscActiveSaveLoadBegin(0,&r);BbscActiveSaveLoadEnd(0,&r);
 assert(BbscNativeActiveSaveSlot()->slot==1);
 assert(BbscNativeActiveSaveToken()->lineage!=lineage);
 put(reinterpret_cast<u64>(holder.data()),8,u64{0});assert(!BbscNativeActiveSaveSlot());
 std::cout<<"PASS: successful native slot, failed load, title invalidation, source, stack and object guards; no timestamp inference\n";
}
