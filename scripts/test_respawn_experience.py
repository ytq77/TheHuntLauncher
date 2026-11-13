"""Extract production curtain and guest UI functions; mock only native boundaries."""
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'timing-tests';OUT.mkdir(exist_ok=True)
s=(ROOT/'.build/bbsc/emulator/src/core/bloodborne_re_zh_cn.cpp').read_text('utf-8')
def function(marker):
 a=s.index(marker);i=s.index('{',a);depth=1;j=i+1
 while depth:
  depth += (s[j]=='{')-(s[j]=='}');j+=1
 return s[a:j]
pre=r'''
#include <cassert>
#include <array>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <string_view>
#include <atomic>
#include <vector>
#include <iostream>
#include <optional>
#include <cmath>
#include <mutex>
#include <algorithm>
#include <type_traits>
#include "BBSC_TEST_VISIBILITY_WIRE"
using u16=uint16_t;using u64=uint64_t;using u32=uint32_t;using u8=uint8_t;using s32=int32_t;using s64=int64_t;
#define PS4_SYSV_ABI
#define LOG_INFO(...)
#define LOG_WARNING(...)
namespace MemoryProt { enum {CpuRead,CpuWrite}; }
std::vector<u8> image(0x1400000);
u64 image_base=reinterpret_cast<u64>(image.data());
constexpr u64 CandidateLocalStatePointerOffset=0x100;
constexpr u64 MenuManPointerOffset=0x5562878,NowLoadingSetStateOffset=0;
constexpr float BbscCurtainFadeOutS=.6f,BbscCurtainFadeInS=.8f;
constexpr s64 BbscCurtainHoldMs=2500,BbscCurtainShowWaitMs=1500,BbscCurtainCloseWaitMs=3000,BbscCurtainLimitMs=15000;
enum class BbscCurtainStep:u8{Idle,FadingOut,Loading,Closing,FadingIn};
struct Curtain{BbscCurtainStep step=BbscCurtainStep::Idle;s64 started_ms=0,step_ms=0;std::string why;bool shown=false;const char16_t* notice=nullptr;std::string_view notice_what;} bbsc_curtain;
struct {bool active=false;} bbsc_load_cover;
std::atomic<u64> bbsc_character_reload_generation{1};
u64 g=100,a=200,role=1;u32 mapid=10;int session=6,type=1,hp=0;bool revive=true,readable=true,writable=true;int mutate=0;
s64 now=10000;int opens=0,closes=0,fades=0,reloads=0,requests=0;u64 fade_plate=700000;std::map<u64,u64> mem;std::map<u64,float> floats;
std::vector<u64> unreadable;bool bbsc_spectate_active=false;
std::mutex bbsc_life_mutex;struct Life{u8 dead{};u32 sequence{};s64 received_ms{};};std::map<u32,Life> bbsc_peer_life;
constexpr s64 BbscLifeFreshMs=3000,BbscLifeDeadHoldMs=60000;
struct BbscTransform{std::array<float,4> position{},rotation{};};
u64 GetCharacterTransformBlock(u64){return 800000;}
TheHuntVisibility::Context roster;bool private_room=true,npid_ok=true,all_dead=false;
namespace BbscMod {bool RelayAuthPrivate(){return private_room;}u64 RelayAuthRoom(){return roster.room;}u32 RelayAuthOwner(){return roster.owner;}bool RelayAuthVisibilityContext(TheHuntVisibility::Context& out){out=roster;return roster.room!=0;}}
bool ReadBbscDisplayNpid(u64,std::array<u8,16>& npid){npid={};npid[0]='p';return npid_ok;}
bool BbscAllPartnersReportDead(){return all_dead;}
bool host_native_ready=false;int arms=0,revives=0;
void ArmBbscHostReadyPlacement(u64,u32,u64,u64){++arms;host_native_ready=false;}
bool BbscHostNativeReady(u64,u32,u64,u64){return host_native_ready;}

bool bbsc_kept_death_this_load=true;s64 bbsc_death_script_ms=0;
u64 GetGlobalState(){return g;}u64 GetLocalPlayerCharacter(){return a;}u32 GetCurrentPackedMap(){return mapid;}
s32 GetSessionState(){return session;}s32 GetLocalPlayerType(){return type;}s32 GetCharacterHp(u64 chr){return chr==300 ? static_cast<int>(mem[900000]):hp;}
s32 GetCharacterType(u64 chr){return static_cast<s32>(mem[chr+0x78]);}
u64 BbscLoadProtectionRoleEpoch(){return role;}
bool ReadablePointer(u64 p,size_t){return p&&readable&&std::find(unreadable.begin(),unreadable.end(),p)==unreadable.end();}bool HasMemoryAccess(u64,size_t,int prot){return prot==MemoryProt::CpuRead?readable:writable;}
template<class T>T ReadValue(u64 p,u64 off){if constexpr(std::is_same_v<T,float>)return floats[p+off];else return static_cast<T>(mem[p+off]);}
bool IsUsablePackedMap(u32 m){return m>0;}s64 SteadyNowMs(){return now;}
bool BbscCurtainActive(){return bbsc_curtain.step!=BbscCurtainStep::Idle;}
u64 BbscFadePlate(){return fade_plate;}
bool BbscFadeTo(float,float){++fades;return true;}
void BbscCurtainStepTo(BbscCurtainStep s,s64 t){bbsc_curtain.step=s;bbsc_curtain.step_ms=t;}
bool BbscRespawnInPlace(std::string_view){++revives;if(mutate==1)++role;if(mutate==2)++a;if(mutate==3)++g;if(mutate==4)++mapid;if(mutate==5)++bbsc_character_reload_generation;if(mutate==6)mem[g+0x1592]^=1;if(mutate==7)mem[g+8]=1;if(mutate==8)session=6;if(mutate==9)type=1;if(mutate==10)mem[900000]=0;if(mutate==11)bbsc_load_cover.active=true;if(mutate==12)mem[100000+0x804]=2;if(mutate==13)mem[image_base+0x556286B]=1;if(mutate==14)++roster.room;if(mutate==15)fade_plate=710000;hp=revive?100:0;return revive;}
bool BbscLoadingScreen(bool open){open?++opens:++closes;return true;}
void BbscAnnounce(const char16_t*,std::string_view){}
void RequestBbscDeathReload(std::string_view){++reloads;}
bool BbscDeathPenaltyEnabled(){return true;}
void NativeSetEffect(u64,s32 n){assert(n==2);++requests;}
'''
ui=(ROOT/'patches/bbsc_death_ui.inc').read_text('utf-8').replace('reinterpret_cast<SetEffect>(image_base + Offset)(0, 2);','NativeSetEffect(0, 2);')
host=(ROOT/'patches/bbsc_host_respawn.inc').read_text('utf-8')
parts=[function('bool BbscRespawnNativeScreenIdle() {'),function('std::optional<BbscTransform> ReadCharacterTransform('),function('void EndBbscDeathCurtain('),host,function('void TickBbscDeathCurtain() {'),ui]
post=r'''
void reset(){g=100;a=200;mapid=10;role=1;session=6;type=1;hp=0;revive=true;readable=writable=true;mutate=false;now=10000;opens=closes=fades=reloads=requests=0;mem.clear();floats.clear();fade_plate=700000;unreadable.clear();bbsc_host_respawn_wait={};bbsc_host_respawn_partner={};bbsc_peer_life.clear();all_dead=false;bbsc_spectate_active=false;private_room=npid_ok=true;roster={};arms=revives=0;host_native_ready=false;mem[image_base+MenuManPointerOffset]=100000;bbsc_load_cover={};bbsc_curtain={};bbsc_death_ui={};bbsc_kept_death_this_load=true;bbsc_death_script_ms=0;bbsc_character_reload_generation=1;}
void curtain(){bbsc_curtain.step=BbscCurtainStep::FadingOut;bbsc_curtain.started_ms=now-700;bbsc_curtain.step_ms=now-700;TickBbscDeathCurtain();}
void uiTick(){TickBbscGuestDeathUi(now,session,g,a,hp);}

void hostSetup(){
 reset();session=3;type=0;mem[g+0x1592]=1;
 roster={1,5,5,{}};TheHuntVisibility::Peer peer{6,7,8,{},true};peer.npid[0]='p';roster.peers.push_back(peer);
 mem[image_base+CandidateLocalStatePointerOffset]=200000;
 mem[200000+0x30]=2;mem[200000+0x38]=210000;mem[210000]=a;mem[210000+0x38]=300;
 mem[300+0x78]=1;mem[900000]=100;mem[a+0x58]=500000;mem[500000+0x38]=510000;
 for(int i=0;i<3;++i){floats[g+0x1450+i*4]=10+i;floats[800000+0x1E0+i*4]=10+i;}
 bbsc_curtain.started_ms=now-700;CaptureBbscHostRespawnPartner();
}
void hostReady(){host_native_ready=true;now+=17;TickBbscDeathCurtain();}
void hostTests(){
 // Native WorldChrMan is a sparse capacity table. Null slots are free entries,
 // not invalid actors; only a living, authenticated cooperator can be captured.
 for(int slot=1;slot<5;++slot){
  hostSetup();mem[200000+0x30]=5;
  for(int i=1;i<5;++i)mem[210000+i*0x38]=0;
  mem[210000+slot*0x38]=300;CaptureBbscHostRespawnPartner();
  curtain();assert(opens==0&&bbsc_host_respawn_wait.active&&revives==1);
  hostReady();assert(opens==0&&bbsc_curtain.step==BbscCurtainStep::FadingIn);
 }
 // Free capacity can also be observed after an already successful capture.
 hostSetup();mem[200000+0x30]=5;curtain();
 assert(opens==0&&bbsc_host_respawn_wait.active);hostReady();
 assert(opens==0&&bbsc_curtain.step==BbscCurtainStep::FadingIn);
 for(int invalid=0;invalid<5;++invalid){
  hostSetup();mem[200000+0x30]=5;
  if(invalid==0)mem[210000+0x38]=0; // only local plus empty capacity
  if(invalid==1)mem[210000]=mem[210000+0x38]=0; // no live actors
  if(invalid==2){mem[210000+2*0x38]=400;unreadable.push_back(400);}
  if(invalid==3){mem[210000+2*0x38]=400;mem[400+0x78]=2;}
  if(invalid==4)mem[200000+0x30]=1;
  CaptureBbscHostRespawnPartner();curtain();assert(opens==1&&!bbsc_host_respawn_wait.active);
 }
 hostSetup();curtain();assert(opens==0&&arms==1&&revives==1&&bbsc_host_respawn_wait.active);
 for(int i=0;i<4;++i){now+=17;TickBbscDeathCurtain();assert(opens==0&&revives==1);}
 hostReady();assert(bbsc_curtain.step==BbscCurtainStep::FadingIn&&opens==0);now+=800;TickBbscDeathCurtain();assert(!BbscCurtainActive());
 // Native partner list gap bridges only the exact pre-proven roster peer's fresh life.
 hostSetup();mem[200000+0x30]=1;bbsc_peer_life[6]={0,1,now};curtain();hostReady();assert(opens==0&&bbsc_curtain.step==BbscCurtainStep::FadingIn);
 hostSetup();mem[200000+0x30]=0;bbsc_peer_life[6]={0,1,now};curtain();hostReady();assert(opens==0&&bbsc_curtain.step==BbscCurtainStep::FadingIn);
 for(int mutation=1;mutation<=14;++mutation){hostSetup();mutate=mutation;curtain();if(bbsc_host_respawn_wait.active){now+=1500;TickBbscDeathCurtain();}assert(opens==(mutation==10?1:0));}
 for(int gate=0;gate<23;++gate){
  hostSetup();
  switch(gate){
  case 0:mem[900000]=0;break;case 1:mem[300+0x78]=2;break;
  case 2:mem[200000+0x30]=1;break;case 3:mem[200000+0x30]=65;break;
  case 4:mem[g+0x1592]=0;break;case 5:session=0;break;case 6:type=2;break;
  case 7:bbsc_curtain.why="wipe";break;case 8:bbsc_spectate_active=true;break;
  case 9:roster.peers[0].joined++;break;case 10:roster.peers[0].member++;break;
  case 11:roster.room++;break;case 12:roster.owner++;break;case 13:npid_ok=false;break;
  case 14:mem[200000+0x30]=1;bbsc_peer_life[9]={0,1,now};break;
  case 15:mem[200000+0x30]=1;bbsc_peer_life[6]={0,1,now-1};break;
  case 16:mem[200000+0x30]=1;bbsc_peer_life[6]={0,1,now+1};break;
  case 17:bbsc_peer_life[6]={1,1,now};break;case 18:all_dead=true;break;
  case 19:bbsc_host_respawn_partner={};break;case 20:mapid=0;break;
  case 21:mem[100000+0x804]=2;break;case 22:private_room=false;break;
  }
  curtain();if(bbsc_host_respawn_wait.active){now+=1500;TickBbscDeathCurtain();}assert(opens==((gate==21)?0:1));if(gate!=21)assert(bbsc_curtain.step==BbscCurtainStep::Loading);
 }
 for(int gate=0;gate<4;++gate){hostSetup();curtain();if(gate==0)mem[500000+0x114]=1;if(gate==1)mem[510000+0x48]=1;if(gate==2)mem[200000+0x68]=1;if(gate==3)mem[100000+0x2DC]=1;hostReady();assert(bbsc_host_respawn_wait.active&&opens==0);mem[500000+0x114]=mem[510000+0x48]=mem[200000+0x68]=mem[100000+0x2DC]=0;hostReady();assert(bbsc_curtain.step==BbscCurtainStep::FadingIn);}
 hostSetup();mem[200000+0x30]=1;bbsc_peer_life[9]={0,1,now};curtain();hostReady();assert(bbsc_host_respawn_wait.active&&opens==0);now+=1500;TickBbscDeathCurtain();assert(opens==1);
 hostSetup();curtain();++roster.room;fade_plate++;now+=17;TickBbscDeathCurtain();assert(opens==0&&closes==0&&fades==0&&bbsc_curtain.step==BbscCurtainStep::Idle);
 for(int plate_change=0;plate_change<3;++plate_change){hostSetup();if(plate_change==2)mutate=15;curtain();if(plate_change!=2)fade_plate=710000;if(plate_change==1){now+=1500;TickBbscDeathCurtain();}else hostReady();assert(opens==0&&closes==0&&fades==0&&!bbsc_host_respawn_wait.active&&bbsc_curtain.step==BbscCurtainStep::Idle);}
 // Replays host 23:13:39.738: native partner disappears at warp; next exact life arrives 500ms later.
 hostSetup();mem[200000+0x30]=1;curtain();assert(bbsc_host_respawn_wait.active&&opens==0);now+=500;bbsc_peer_life[6]={0,1,now};hostReady();assert(opens==0&&bbsc_curtain.step==BbscCurtainStep::FadingIn);
 hostSetup();mem[900000]=static_cast<u64>(-1);CaptureBbscHostRespawnPartner();curtain();assert(opens==1&&arms==0);
 // Deferred readiness, timeout, cancellation, and no double revival.
 hostSetup();curtain();now+=1499;TickBbscDeathCurtain();assert(opens==0&&revives==1);now++;TickBbscDeathCurtain();assert(opens==1&&revives==1);
 for(int change=0;change<14;++change){
  hostSetup();curtain();
  switch(change){case 0:++a;break;case 1:++g;break;case 2:++mapid;break;case 3:++role;break;
  case 4:++bbsc_character_reload_generation;break;case 5:++roster.room;break;case 6:++roster.peers[0].joined;break;
  case 7:mem[g+8]=1;break;case 8:bbsc_load_cover.active=true;break;case 9:mem[100000+0x804]=2;break;
  case 10:mem[image_base+0x556286B]=1;break;case 11:hp=0;break;case 12:bbsc_peer_life[6]={1,1,now};break;case 13:now-=1;break;}
  if(change==13){now-=100;TickBbscDeathCurtain();}else hostReady();const bool cancel=change<=5||(change>=7&&change<=10);assert(opens==(cancel?0:1)&&revives==1);if(cancel){const bool restore=change==3||change==5;assert(bbsc_curtain.step==(restore?BbscCurtainStep::FadingIn:BbscCurtainStep::Idle)&&fades==(restore?1:0));now+=20000;TickBbscDeathCurtain();assert(opens==0&&closes==0&&fades==(restore?1:0));}
 }
 for(int position=0;position<3;++position){hostSetup();curtain();if(position==0)floats[800000+0x1E0]+=1;if(position==1)floats[800000+0x1E0]=NAN;if(position==2)bbsc_host_respawn_wait.target[0]=NAN;hostReady();assert(opens==0&&bbsc_host_respawn_wait.active);now+=1500;TickBbscDeathCurtain();assert(opens==1);}
 hostSetup();curtain();bbsc_curtain={};TickBbscDeathCurtain();assert(!bbsc_host_respawn_wait.active&&opens==0);
 hostSetup();revive=false;curtain();assert(reloads==1&&opens==0&&!BbscCurtainActive()&&arms==0);
}

int main(){
 hostTests();
 reset();curtain();assert(bbsc_curtain.step==BbscCurtainStep::FadingIn&&opens==0&&fades==1);
 now+=800;TickBbscDeathCurtain();assert(!BbscCurtainActive());
 reset();session=3;type=0;curtain();assert(opens==1&&bbsc_curtain.step==BbscCurtainStep::Loading);
 reset();bbsc_load_cover.active=true;curtain();assert(opens==1);
 reset();mem[g+8]=1;curtain();assert(opens==1);
 reset();mem[100000+0x804]=2;curtain();assert(opens==1);
 reset();readable=false;curtain();assert(opens==1);
 reset();mapid=0;curtain();assert(opens==1);
 reset();mem[image_base+0x556286B]=1;curtain();assert(opens==1);
 reset();mem[g+0x1592]=1;curtain();assert(opens==1);
 for(int i=1;i<=7;++i){reset();mutate=i;curtain();assert(opens==1);}
 reset();revive=false;curtain();assert(reloads==1&&!BbscCurtainActive()&&opens==0);
 // Exact production native pin is copied from the supplied game in Python.
 PIN_COPY
 reset();uiTick();now+=500;uiTick();assert(requests==1);now+=100;uiTick();assert(requests==1);
 reset();bbsc_kept_death_this_load=false;uiTick();now+=500;uiTick();assert(requests==0);
 reset();bbsc_death_script_ms=now;uiTick();now+=500;uiTick();assert(requests==0);
 reset();mem[100000+0x108]=2;uiTick();mem[100000+0x108]=0;now+=500;uiTick();assert(requests==0);
 reset();mem[100000+0x15C8+16*4]=2;uiTick();mem[100000+0x15C8+16*4]=0;now+=500;uiTick();assert(requests==0);
 reset();mem[100000+0x15C8+4]=1;uiTick();now+=500;uiTick();assert(requests==0);
 reset();mem[100000+0x1724]=1;uiTick();now+=500;uiTick();assert(requests==0);
 for(int index:{-1,17}){reset();mem[100000+0x1610]=index;uiTick();now+=500;uiTick();assert(requests==0);}
 reset();writable=false;uiTick();now+=500;uiTick();assert(requests==0);
 reset();image[0x13360B0]^=1;uiTick();now+=500;uiTick();assert(requests==0);image[0x13360B0]^=1;
 reset();session=3;type=0;uiTick();now+=500;uiTick();assert(requests==0);
 reset();uiTick();now+=3100;uiTick();assert(requests==0);
 reset();uiTick();now+=500;++a;uiTick();assert(requests==0);
 reset();uiTick();now+=500;hp=100;uiTick();assert(requests==0&&bbsc_death_ui.actor==0);
 reset();uiTick();now+=500;mem[g+8]=1;uiTick();assert(requests==0&&bbsc_death_ui.actor==a);
 for(int unknown=0;unknown<6;++unknown){
  reset();uiTick();now+=500;uiTick();assert(requests==1);
  if(unknown==0)hp=-1;if(unknown==1)readable=false;if(unknown==2)a=0;if(unknown==3)g=0;if(unknown==4)type=-1;if(unknown==5)session=-1;
  now+=100;uiTick();assert(bbsc_death_ui.requested);
  hp=0;readable=true;a=200;g=100;type=1;session=6;now+=500;uiTick();assert(requests==1);
 }
 reset();hp=-1;uiTick();assert(bbsc_death_ui.since==0);hp=0;now+=4000;uiTick();assert(bbsc_death_ui.since==now&&requests==0);
 reset();uiTick();hp=-1;now+=4000;uiTick();hp=0;uiTick();assert(requests==0);
 for(int gate=0;gate<3;++gate){
  reset();uiTick();now+=500;uiTick();assert(requests==1);
  if(gate==0)bbsc_load_cover.active=true;if(gate==1)bbsc_curtain.step=BbscCurtainStep::FadingOut;if(gate==2)mem[g+8]=1;
  now+=100;uiTick();assert(bbsc_death_ui.requested);
  bbsc_load_cover.active=false;bbsc_curtain={};mem[g+8]=0;now+=500;uiTick();assert(requests==1);
 }
 reset();uiTick();bbsc_load_cover.active=true;now+=4000;uiTick();bbsc_load_cover.active=false;uiTick();assert(requests==0);
 std::cout<<"production curtain / death UI regressions passed\n";
}
'''
import re
pin=re.search(r'Pin\{([^}]+)\}',ui).group(1)
post=post.replace('PIN_COPY',f'const u8 pin[]={{ {pin} }};std::memcpy(image.data()+0x13360B0,pin,sizeof(pin));')
# Extract the actual sole production Arm tail, not a separate readiness model.
start=s.index('    bbsc_inplace_respawn_ms = SteadyNowMs();')
end=s.index('    BbscRestockFromStorage(why);',start)
tail=s[start:end]
assert tail.count('ArmBbscHostReadyPlacement(')==1
pre=pre.replace('bool BbscRespawnInPlace(', 's64 bbsc_inplace_respawn_ms{};\nbool BbscRespawnInPlace(',1)
pre=pre.replace('hp=revive?100:0;return revive;}', 'hp=revive?100:0;if(revive) {\n'+tail+'}return revive;}',1)
post=post.replace('assert(opens==1&&arms==0);','assert(opens==1&&arms==1);')
pre=pre.replace('BBSC_TEST_VISIBILITY_WIRE',(ROOT/'patches/bbsc_visibility_wire.h').as_posix())
p=OUT/'timing_runtime.cpp';p.write_text(pre+'\n'.join(parts)+post,encoding='utf-8')
exe=OUT/'timing_runtime.exe';clang=Path('C:/bbsc-dev/msys64/clang64/bin/clang++.exe')
subprocess.run([str(clang),'-std=c++23',str(p),'-o',str(exe)],check=True)
import os
env=os.environ.copy();env['PATH']=str(clang.parent)+os.pathsep+env.get('PATH','')
subprocess.run([str(exe)],check=True,env=env)

controls={
 'reject-empty-capture-slot':('if (!actor) { ++empty_slots; continue; }','if (!actor) { ++empty_slots; }'),
 'reject-empty-context-slot':('if (!actor || actor == proof.actor) continue;','if (actor == proof.actor) continue;'),
'missing-plate-ownership-guard':('bool BbscHostRespawnOwnPlate(u64 plate) { return plate && BbscFadePlate() == plate; }','bool BbscHostRespawnOwnPlate(u64) { return true; }'),
'missing-pre-pending-native-check':('if (respawn_host && (!BbscHostRespawnOwnPlate(respawn_plate) || GetGlobalState()', 'if (false && (!BbscHostRespawnOwnPlate(respawn_plate) || GetGlobalState()'),
'orphan-owned-fade':('EndBbscDeathCurtain(now, "host readiness cancelled: restore owned fade");', 'bbsc_curtain = {};'),
'wrong-fade-identity':('BbscFadePlate() == plate &&', 'true &&'),
'old-host-delay':('if (same_map_host && BbscHostRespawnContext(false) &&','if (false && BbscHostRespawnContext(false) &&'),
'missing-pad-guard':('BbscHostNativeReady(wait.actor, wait.map, wait.reload, wait.role)','true'),
'missing-position-guard':('std::abs(actual->position[i] - target) <= 0.25f','true'),
'missing-partner-binding':('if (found == bbsc_peer_life.end()) return !require_alive || native_alive;','if (found == bbsc_peer_life.end()) return true;'),
'cancel-opens-native-menu':('bbsc_curtain = {};','bbsc_curtain = {}; BbscLoadingScreen(true);'),
'missing-timeout':('now - wait.since < BbscHostRespawnWaitLimitMs','true'),
}
for name,(before,after) in controls.items():
 text=p.read_text('utf-8');assert before in text
 q=OUT/(name+'.cpp');q.write_text(text.replace(before,after),encoding='utf-8');binary=OUT/(name+'.exe')
 subprocess.run([str(clang),'-std=c++23',str(q),'-o',str(binary)],check=True)
 result=subprocess.run([str(binary)],env=env,capture_output=True,text=True)
 (OUT/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
 assert result.returncode!=0,name+' negative control did not fail'
 print('Negative control detected:',name)




