#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <thread>
#include <vector>
#define FMT_HEADER_ONLY
#include <fmt/format.h>
using u8=uint8_t;using u32=uint32_t;using u64=uint64_t;
constexpr size_t BbscWorldGroupBytes=125;
constexpr u64 WorldFlagManPointerOffset=0x100;
std::array<u8,0x200> image{};
std::array<u8,0x1700> global{};
std::array<u8,0x40> manager{};
std::vector<u8> bank(150000);
u64 image_base=reinterpret_cast<u64>(image.data());
std::map<u32,std::array<u8,125>> bbsc_own_world;
bool bbsc_own_world_dirty=true,missing=false,personal_dream_change=false,relocated=false;
std::array<u8,125> temporary_group{};
struct Slot{std::string user="save";int slot=0;};
std::optional<Slot>bbsc_own_world_slot=Slot{};
struct Dream{u64 held=0;}bbsc_dream_own;
int session=0,dream_writes=0;
std::filesystem::path root;
namespace Common::FS {
enum class PathType{CapturesDir};
std::filesystem::path GetUserPath(PathType){return root/"mod/captures";}
}
#define LOG_INFO(...) ((void)0)
#define LOG_WARNING(...) ((void)0)
template<class T>T ReadValue(u64 p,size_t off){T v;std::memcpy(&v,reinterpret_cast<void*>(p+off),sizeof(T));return v;}
template<class T>void put(u8* p,size_t off,T v){std::memcpy(p+off,&v,sizeof(T));}
bool ReadablePointer(u64 p,size_t){return p!=0;}
bool EnvFlagEnabled(const char* p){const char* v=std::getenv(p);return v&&v[0]=='1';}
u64 GetGlobalState(){return reinterpret_cast<u64>(global.data());}
int GetSessionState(){return session;}
int GetLocalPlayerType(){return 1;}
u64 SteadyNowMs(){return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
u64 BbscFlagGroupStorage(u32 key){return !missing&&key==1?reinterpret_cast<u64>(relocated?temporary_group.data():bank.data()+625):0;}
bool BbscHomeForOwnWorld(){return session==0&&global[0x1592]==1;}
void RestoreBbscOwnDreamState(std::string_view){
 ++dream_writes;bbsc_dream_own.held=0;
 if(personal_dream_change)bbsc_own_world.at(1)[100]|=4;
}
std::filesystem::path BbscOwnWorldPath(const Slot&){return root/"mod/own-world/own-save-0.bin";}
void WriteBbscOwnWorldFile(){
 auto p=BbscOwnWorldPath(Slot{});std::filesystem::create_directories(p.parent_path());
 std::ofstream out(p,std::ios::binary|std::ios::trunc);
 for(u32 v:{0x574F4242u,125u,static_cast<u32>(bbsc_own_world.size())})out.write(reinterpret_cast<char*>(&v),4);
 for(const auto& [key,bits]:bbsc_own_world){out.write(reinterpret_cast<const char*>(&key),4);out.write(reinterpret_cast<const char*>(bits.data()),125);}
}
#include "../patches/bbsc_world_recovery.inc"
int main(int argc,char**argv){
 assert(argc==3);root=argv[1];std::string mode=argv[2];
 {std::ifstream f(root/"before-bank.bin",std::ios::binary);f.read(reinterpret_cast<char*>(bank.data()),bank.size());assert(f);}
 std::array<u8,125> own{};
 {std::ifstream f(root/"target-group.bin",std::ios::binary);f.read(reinterpret_cast<char*>(own.data()),125);assert(f);}
 bbsc_own_world[1]=own;
 put(image.data(),WorldFlagManPointerOffset,reinterpret_cast<u64>(manager.data()));
 put(manager.data(),0x24,u32{1200});put(manager.data(),0x28,reinterpret_cast<u64>(bank.data()));
 global[0x1592]=1;bbsc_recovery_at_load=true;WriteBbscOwnWorldFile();
 const auto before=bank;
 std::copy(own.begin(),own.end(),bank.begin()+625);
 assert(BbscRecordWorldRecovery(BbscRecoveryCapture));
 bank=before;
 assert(ReadValue<u64>(reinterpret_cast<u64>(manager.data()),0x28)==reinterpret_cast<u64>(bank.data()));
 assert(bbsc_recovery_initial_indices.at(1)==5);
 if(mode=="pending"){
   auto pending=BbscOwnWorldPath(Slot{});pending+=".awaiting-save";
   std::ofstream(pending)<<"pending";
   assert(BbscRecoveryHasPendingSave());
 }else if(mode=="missing"){
   missing=true;bbsc_dream_own.held=1;
   assert(!BbscRestoreWorldTransaction("missing"));assert(bank==before&&dream_writes==0&&!bbsc_own_world.empty());
 }else if(mode=="late"){
   bbsc_recovery_at_load=false;
   assert(!BbscRestoreWorldTransaction("late"));assert(bank==before&&!bbsc_own_world.empty());
 }else if(mode=="quit"||mode=="quit-dream"||mode=="relocated-quit"){
   if(mode=="relocated-quit"){
     std::copy(bank.begin()+625,bank.begin()+750,temporary_group.begin());relocated=true;
   }
   if(mode=="quit-dream"){bbsc_dream_own.held=1;personal_dream_change=true;}
   session=6;global[0x1592]=0;BbscWorldRecoveryQuit(true);
   assert(bbsc_recovery_mode==BbscRecoveryQuit&&bank==before&&!bbsc_own_world.empty());
   assert(bbsc_recovery_rows.at(0).index==5);
   if(mode=="quit-dream")assert(dream_writes==1&&(bbsc_recovery_rows.at(0).after[100]&4));
   const auto saved_sequence=bbsc_recovery_sequence;
   BbscWorldRecoveryQuit(false);assert(bbsc_recovery_sequence==saved_sequence);
 }else{
   std::thread change;
   if(mode=="changed")change=std::thread([]{
       const auto path=BbscRecoveryFolder()/"requests/world-save-0.bin";
       u32 mode=0;
       const auto deadline=SteadyNowMs()+3000;
       while(mode!=BbscRecoveryRestore&&SteadyNowMs()<deadline){
         {std::ifstream input(path,std::ios::binary);input.seekg(52);input.read(reinterpret_cast<char*>(&mode),4);}
         std::this_thread::sleep_for(std::chrono::milliseconds(1));
       }
       if(mode==BbscRecoveryRestore)bank[625+99]^=4;
       else std::cerr<<"Restore request not observed; last mode="<<mode<<"\n";
   });
   const bool restored=BbscRestoreWorldTransaction("fixture load");
   if(change.joinable())change.join();
   if(mode=="changed"){
     assert(!restored&&bbsc_recovery_mode==BbscRecoveryBlocked&&!bbsc_own_world.empty());
     auto expected=before;expected[625+99]^=4;assert(bank==expected);
   }else{
     assert(restored&&bbsc_recovery_mode==BbscRecoveryApplied&&bbsc_own_world.empty());
     assert(std::equal(own.begin(),own.end(),bank.begin()+625));
     bank[625+100]^=1;const auto later=bank;
     assert(!BbscRestoreWorldTransaction("repeat")&&bank==later);
     std::ofstream(root/"memory-restored.ok")<<"ready";
     for(int attempt=0;attempt<500&&bbsc_recovery_waiting;++attempt){BbscRecoveryPollAck();std::this_thread::sleep_for(std::chrono::milliseconds(10));}
     assert(!bbsc_recovery_waiting&&bank==later);
   }
 }
 std::cout<<mode<<" passed\n";
}
