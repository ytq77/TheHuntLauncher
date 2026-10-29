#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
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
#define FMT_HEADER_ONLY
#include <fmt/format.h>
using u8=uint8_t;using u32=uint32_t;using u64=uint64_t;
#define PS4_SYSV_ABI
#define LOG_INFO(...) ((void)0)
#define LOG_WARNING(...) ((void)0)
struct GuestRegisterSnapshot {};
std::array<u8,512> image{};
std::array<u8,64> manager{};
std::vector<u8> bank(150000);
u64 image_base=reinterpret_cast<u64>(image.data());
constexpr u64 WorldFlagManPointerOffset=0x100;
using Callback=void(*)(u64,const GuestRegisterSnapshot*);
Callback installed=nullptr;
bool own=true;
std::filesystem::path folder;
constexpr u32 BbscRecoveryApplied=3;
struct Row {u32 key,index;std::array<u8,125> before{},after{};};
std::vector<Row> bbsc_recovery_rows;
u64 bbsc_recovery_sequence=100;
int bbsc_recovery_slot=1;
u32 bbsc_recovery_mode=BbscRecoveryApplied;
bool BbscHomeForOwnWorld(){return own;}
bool BbscWorldRecoveryEnabled(){return true;}
std::filesystem::path BbscRecoveryFolder(){return folder;}
bool BbscRecoveryWriteFile(const std::filesystem::path& path,std::span<const u8> bytes){
 std::filesystem::create_directories(path.parent_path());
 std::ofstream out(path,std::ios::binary);out.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());return bool(out);
}
bool InstallGuestCodeHook(void* p,std::span<const u8> bytes,u64,Callback callback){
 const std::array<u8,16> expected{0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x50,0x49,0x89,0xf6,0x49,0x89,0xff};
 assert(reinterpret_cast<u64>(p)==image_base+0x01399DE0&&std::equal(bytes.begin(),bytes.end(),expected.begin(),expected.end()));
 installed=callback;return true;
}
u64 SteadyNowMs(){return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
// Compile the exact post-preparation fragment used by the candidate DLL.
#include "../.build/bbsc/emulator/src/core/bbsc_save_proof.inc"
int main(int argc,char**argv){
 assert(argc==2);folder=argv[1];assert(!std::filesystem::exists(folder));
 _putenv_s("THEHUNT_WORLD_RECOVERY_TOKEN","0123456789abcdef0123456789abcdef");
 _putenv_s("THEHUNT_SAVE_PROOF_DIR","");
 auto ptr=reinterpret_cast<u64>(manager.data());std::memcpy(image.data()+0x100,&ptr,8);
 ptr=reinterpret_cast<u64>(bank.data());std::memcpy(manager.data()+0x28,&ptr,8);
 u32 count=1200;std::memcpy(manager.data()+0x24,&count,4);
 InstallBbscSaveProofHook();assert(installed&&bbsc_save_proof_hook);
 installed(0,nullptr);assert(bbsc_home_proof_sequence==0&&!std::filesystem::exists(folder));
 bbsc_recovery_rows.push_back(Row{1,5});bbsc_recovery_rows[0].after[0]=7;
 BbscHomeProofRestored();assert(bbsc_home_proof_sequence==0);
 bank[625]=7;const auto unchanged=bank;
 bbsc_recovery_mode=2;BbscHomeProofRestored();assert(bbsc_home_proof_sequence==0);bbsc_recovery_mode=3;
 own=false;BbscHomeProofRestored();assert(bbsc_home_proof_sequence==0);own=true;
 bbsc_recovery_rows.push_back(Row{2,1200});BbscHomeProofRestored();assert(bbsc_home_proof_sequence==0);
 bbsc_recovery_rows.back().index=0xffffffff;
 BbscHomeProofRestored();assert(bbsc_home_proof_sequence==1&&bank==unchanged);
 const auto first=bbsc_home_proof_folder;
 assert(std::filesystem::file_size(first/"restored-bank.bin")==150000);
 BbscHomeProofRestored();assert(bbsc_home_proof_sequence==1);
 installed(0,nullptr);assert(bbsc_home_proof_sequence==2&&bank==unchanged);
 installed(0,nullptr);assert(bbsc_home_proof_sequence==2);
 bank[625]=0;installed(0,nullptr);assert(bbsc_home_proof_sequence==3);
 const auto old_size=std::filesystem::file_size(first/"native.jsonl");
 bank[625]=7;++bbsc_recovery_sequence;
 BbscHomeProofRestored();assert(bbsc_home_proof_recovery==100&&bbsc_home_proof_sequence==3);
 const u64 acknowledged=100;
 BbscRecoveryWriteFile(folder/"acks/1.ack",std::span<const u8>{reinterpret_cast<const u8*>(&acknowledged),8});
 BbscHomeProofRestored();assert(bbsc_home_proof_recovery==101&&bbsc_home_proof_sequence==1&&bank==unchanged);
 assert(std::filesystem::file_size(first/"native.jsonl")==old_size);
 ptr=0x10000;std::memcpy(image.data()+0x100,&ptr,8);
 installed(0,nullptr);assert(bbsc_home_proof_sequence==1&&bank==unchanged);
 ptr=reinterpret_cast<u64>(manager.data());std::memcpy(image.data()+0x100,&ptr,8);
 for(int i=0;i<100;++i){bank[625]=u8(i);installed(0,nullptr);}
 assert(bbsc_home_proof_sequence==64);
 return 0;
}
