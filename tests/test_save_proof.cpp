#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <cassert>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <span>
#include <string>
#include <vector>
using u8=uint8_t;using u32=uint32_t;using u64=uint64_t;
#define PS4_SYSV_ABI
#define LOG_INFO(...) ((void)0)
struct GuestRegisterSnapshot {};
std::array<u8,512> image{};
std::array<u8,64> manager{};
std::vector<u8> bank(150000);
u64 image_base=reinterpret_cast<u64>(image.data());
constexpr u64 WorldFlagManPointerOffset=0x100;
using Callback=void(*)(u64,const GuestRegisterSnapshot*);
Callback installed=nullptr;
bool own=true;
bool BbscHomeForOwnWorld(){return own;}
bool InstallGuestCodeHook(void* p,std::span<const u8> bytes,u64,Callback callback){
 const std::array<u8,16> expected{0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x50,0x49,0x89,0xf6,0x49,0x89,0xff};
 assert(reinterpret_cast<u64>(p)==image_base+0x01399DE0&&std::equal(bytes.begin(),bytes.end(),expected.begin(),expected.end()));
 installed=callback;return true;
}
u64 SteadyNowMs(){return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
#include "../patches/bbsc_save_proof.inc"
int main(int argc,char**argv){
 assert(argc==2);std::filesystem::create_directories(argv[1]);
 _putenv_s("THEHUNT_SAVE_PROOF_DIR",argv[1]);
 auto ptr=reinterpret_cast<u64>(manager.data());std::memcpy(image.data()+0x100,&ptr,8);
 ptr=reinterpret_cast<u64>(bank.data());std::memcpy(manager.data()+0x28,&ptr,8);
 u32 count=1200;std::memcpy(manager.data()+0x24,&count,4);
 bank[100]=7;
 const auto unchanged=bank;
 std::ofstream file(std::filesystem::path(argv[1])/"expected-bank.bin",std::ios::binary);
 file.write(reinterpret_cast<char*>(bank.data()),bank.size());file.close();
 const std::array<u8,3> abc{'a','b','c'};
 assert(BbscSaveProofHash(abc)=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
 InstallBbscSaveProofHook();assert(installed&&bbsc_save_proof_hook);
 installed(0,nullptr);assert(bbsc_save_proof_sequence==0);
 own=false;BbscSaveProofAtLoad();assert(bbsc_save_proof_sequence==0);own=true;
 bank[100]^=1;BbscSaveProofAtLoad();assert(bbsc_save_proof_sequence==0);bank[100]^=1;
 BbscSaveProofAtLoad();assert(bbsc_save_proof_loaded&&bbsc_save_proof_sequence==1&&bank==unchanged);
 BbscSaveProofAtLoad();assert(bbsc_save_proof_sequence==1);
 installed(0,nullptr);assert(bbsc_save_proof_sequence==2&&bank==unchanged);
 installed(0,nullptr);assert(bbsc_save_proof_sequence==2);
 ptr=0x10000;std::memcpy(image.data()+0x100,&ptr,8);
 installed(0,nullptr);assert(bbsc_save_proof_sequence==2&&bank==unchanged);
 ptr=reinterpret_cast<u64>(manager.data());std::memcpy(image.data()+0x100,&ptr,8);
 for(int i=0;i<100;++i){bank[100]=u8(i);installed(0,nullptr);}
 assert(bbsc_save_proof_sequence==64);
 return 0;
}
