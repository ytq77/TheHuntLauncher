// Execute the real hook trampoline concurrently, without a game or save files.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <atomic>
#include <cassert>
#include <cstring>
#include <iostream>
#include <thread>
#include <vector>
#include "core/cpu_patches.h"
namespace BbscMod {
u64 base=0;
u64 EbootAddress(){return base;}
void LogPlain(const char* text){std::cerr<<text<<"\n";}
}
static std::atomic<unsigned> observed{0};
void PS4_SYSV_ABI Guard(u64,const Core::GuestRegisterSnapshot* r){
 ++observed;
 if(r->rdi&1){
  auto* mutable_registers=const_cast<Core::GuestRegisterSnapshot*>(r);
  mutable_registers->rax=77;
  mutable_registers->skip_original=1;
 }
}
int main(){
 const size_t size=0x056D4000ULL+(8ULL<<20);
 auto* memory=static_cast<unsigned char*>(VirtualAlloc(nullptr,size,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE));
 assert(memory);BbscMod::base=reinterpret_cast<u64>(memory);
 // SysV: return input+5. The five-byte NOP is safe to relocate at function entry.
 const std::array<u8,10> code{0x0f,0x1f,0x44,0x00,0x00,0x48,0x8d,0x47,0x05,0xc3};
 auto* entry=memory+0x1000;std::memcpy(entry,code.data(),code.size());
 assert(Core::InstallGuestCodeHook(entry,std::span<const u8>{code.data(),5},0,Guard));
 using Function=u64 PS4_SYSV_ABI (*)(u64);
 const auto call=reinterpret_cast<Function>(entry);
 std::vector<std::thread> threads;
 for(unsigned n=0;n<8;++n)threads.emplace_back([call,n]{
  for(unsigned i=0;i<10000;++i){const u64 value=2*i+(n&1);assert(call(value)==((value&1)?77:value+5));}
 });
 for(auto& thread:threads)thread.join();
 assert(observed==80000);
 std::cout<<"PASS: actual trampoline restores state; 80000 concurrent calls cannot cross-cancel\n";
 // The hook generator owns code in this region until process termination.
}
