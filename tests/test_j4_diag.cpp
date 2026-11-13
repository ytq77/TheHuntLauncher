// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the production include with synthetic NP functions, memory and clock. No game.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define BBSC_J4_DIAG_TEST
#include <windows.h>
#define PS4_SYSV_ABI __attribute__((sysv_abi))
#include <array>
#include <cassert>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <thread>
#include <set>
#include <string>
#include <vector>
namespace BbscMod {
inline std::uint64_t base = 0;
inline unsigned guarded = 0;
inline std::vector<std::string> logs;
std::uint64_t EbootAddress() { return base; }
inline std::mutex log_mutex;
void LogPlain(const char* text) { std::scoped_lock lock(log_mutex);logs.emplace_back(text); }
void LogJ4If(const char* text,bool (*accept)(void*),void* state) {
    std::scoped_lock lock(log_mutex);
    if (accept(state)) logs.emplace_back(text);
}

void GuardSlot(std::uint64_t,std::uint64_t,void**,const char*);
}
namespace RealSlotGuard {
using u64=std::uint64_t;
u64& EbootBase=BbscMod::base;
constexpr u64 EbootSize=0x056D4000;
struct GuardedSlot {u64 slot_offset,wrapper;void** original;const char* name;int rewritten=0;};
std::mutex guarded_mutex;
std::vector<GuardedSlot> guarded_slots;
#define LOG_INFO(...) ((void)0)
#include "../patches/bbsc_slot_guard.inc"
#undef LOG_INFO
}
namespace BbscMod {
void GuardSlot(std::uint64_t slot,std::uint64_t wrapper,void** original,const char* name) {
    ++guarded;RealSlotGuard::guarded_slots.push_back({slot,wrapper,original,name,0});
}
}
#include "../patches/bbsc_j4_diag.inc"
namespace BbscJ4Diag { U64 testNow=100;std::atomic<U64> testReads{0}; }
using namespace BbscJ4Diag;
static std::array<U64,6> arguments;
static unsigned invoked=0;
static S32 returnCode=0;
template<class T> U64 Word(T* p) {return reinterpret_cast<U64>(p);}
static S32 Result() {++invoked;SetLastError(1789);errno=95;return returnCode;}
#define ABI __attribute__((sysv_abi))
static S32 ABI MockActivate(S32 c,const void* p,S32* out) {
    arguments={U64(c),Word(p),Word(out)};
    if(out) *out=17;
    return Result();
}
static S32 ABI MockDeactivate(S32 c,S32 id) {arguments={U64(c),U64(id)};return Result();}
static S32 ABI MockMatch(U16 c,void* p,void* o,U32* id) {
    arguments={c,Word(p),Word(o),Word(id)};
    return Result();
}
static S32 ABI MockPing(U16 c,const void* p,const void* o,S32* id) {
    arguments={c,Word(p),Word(o),Word(id)};
    return Result();
}
static S32 ABI MockSignal(S32 c,S32 id,U32* s,U32* a,U16* p) {
    arguments={U64(c),U64(id),Word(s),Word(a),Word(p)};
    return Result();
}
static S32 ABI MockMatching(U16 c,U64 room,U16 m,S32* s,void* a,U16* p) {
    arguments={c,room,m,Word(s),Word(a),Word(p)};
    return Result();
}
static S32 ABI MockContext(const void* p,void* cb,void* a,S32* out) {
    arguments={Word(p),Word(cb),Word(a),Word(out)};
    return Result();
}
static S32 ABI MockRecv(S32 s,void* b,U64 n,S32 f,void* a,U32* l) {
    arguments={U64(s),Word(b),n,U64(f),Word(a),Word(l)};
    return Result();
}
static S32 ABI MockSend(S32 s,const void* b,U64 n,S32 f,const void* a,U32 l) {
    arguments={U64(s),Word(b),n,U64(f),Word(a),l};
    return Result();
}

#include "j4_channel_fixture.inc"
#include "j4_http_fixture.inc"
constexpr std::array<U64,FunctionCount> AllSlots{
    0x053E52B0,0x053E5148,0x053E5198,0x053E5190,0x053E51A0,0x053E52D0,
    0x053E51D0,0x053E5188,0x053E5170,0x053E5120,0x053E4F58,0x053E4F48};
static unsigned char* hostCode=nullptr;
static U64 Thunk(unsigned i,U64 destination,bool gameCall=false) {
    auto* p=gameCall ? reinterpret_cast<unsigned char*>(BbscMod::base+0x2000+i*64) : hostCode+i*64;
    size_t n=0;
    if(gameCall) {p[n++]=0x48;p[n++]=0x83;p[n++]=0xec;p[n++]=0x08;}
    p[n++]=0x48;p[n++]=0xb8;std::memcpy(p+n,&destination,8);n+=8;
    p[n++]=0xff;p[n++]=gameCall?0xd0:0xe0;
    if(gameCall) {p[n++]=0x48;p[n++]=0x83;p[n++]=0xc4;p[n++]=0x08;p[n++]=0xc3;}
    FlushInstructionCache(GetCurrentProcess(),p,n);
    return reinterpret_cast<U64>(p);
}
static size_t Lines(std::string_view term) {
    size_t count=0;
    for(const auto& line:BbscMod::logs) if(line.find(term)!=std::string::npos) ++count;
    return count;
}
static bool Contains(std::string_view a,std::string_view b) {
    for(const auto& line:BbscMod::logs)
        if(line.find(a)!=std::string::npos && line.find(b)!=std::string::npos) return true;
    return false;
}
static void Reset() {
    { std::scoped_lock lock(BbscMod::log_mutex);signalStates.clear(); }
    windows.clear(); activeWindows=0; BbscMod::logs.clear(); serial=0; everHost=false;
    getTotal=0;createJoinTotal=0; testNow=100;returnCode=0;
}
static void Pass(const char* name) {std::cout<<"PASS J4: "<<name<<'\n';}
int main(int argc,char** argv) {
    assert(argc==2 || argc==3);
    const std::filesystem::path fixture=argv[1];
    assert(!std::filesystem::exists(fixture));
    std::filesystem::create_directories(fixture);
    BbscMod::base=reinterpret_cast<U64>(VirtualAlloc(nullptr,EbootSize,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    assert(BbscMod::base);
    auto* manager=static_cast<char*>(VirtualAlloc(nullptr,4096,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    assert(manager);
    *reinterpret_cast<U64*>(BbscMod::base+Singleton)=reinterpret_cast<U64>(manager);
    hostCode=static_cast<unsigned char*>(VirtualAlloc(nullptr,4096,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE));
    assert(hostCode);
    std::array<U64,FunctionCount> originals{
        Word(&MockActivate),Word(&MockDeactivate),Word(&MockMatch),Word(&MockMatch),
        Word(&MockMatch),Word(&MockSignal),Word(&MockMatching),Word(&MockPing),
        Word(&MockMatch),Word(&MockContext),Word(&MockRecv),Word(&MockSend)};
    for(unsigned i=0;i<FunctionCount;++i) { originals[i]=Thunk(i,originals[i]);
        *reinterpret_cast<U64*>(BbscMod::base+AllSlots[i])=originals[i]; }
    Start(fixture/L"j4-diag.enable");
    assert(!enabled && !started && BbscMod::guarded==0 && BbscMod::logs.empty());
    for(unsigned i=0;i<FunctionCount;++i) assert(*reinterpret_cast<U64*>(BbscMod::base+AllSlots[i])==originals[i]);
    assert(!EnableFile(fixture)); // A directory cannot enable anything.
    Pass("absent switch starts no thread and replaces zero of all 12 slots");
    std::ofstream(fixture/L"j4-diag.enable").close();
    assert(EnableFile(fixture/L"j4-diag.enable"));

    J4ChannelFixture::Install();
    assert(BbscMod::guarded==3);
    enabled=true; InstallSlots();
    assert(BbscMod::guarded==FunctionCount);
    for(unsigned i=0;i<FunctionCount;++i) assert(*reinterpret_cast<U64*>(BbscMod::base+AllSlots[i])!=originals[i]);

    std::set<U64> registered;
    for(const auto& guard:RealSlotGuard::guarded_slots) assert(registered.insert(guard.slot_offset).second);
    // Simulate the loader relinking each channel slot; refresh must restore exactly one owner.
    for(const auto fn : {Send,Recv,SignalStatus})
        *reinterpret_cast<U64*>(BbscMod::base+AllSlots[fn])=originals[fn];
    for(int round=0;round<20;++round)
        for(auto& guard:RealSlotGuard::guarded_slots) RealSlotGuard::RefreshGuardedSlot(guard);
    for(const auto& guard:RealSlotGuard::guarded_slots)
        if(guard.slot_offset==AllSlots[Send] || guard.slot_offset==AllSlots[Recv] ||
           guard.slot_offset==AllSlots[SignalStatus]) assert(guard.rewritten==1);
    assert(J4ChannelFixture::original_sendto==reinterpret_cast<decltype(&MockSend)>(originals[Send]));
    assert(J4ChannelFixture::original_recvfrom==reinterpret_cast<decltype(&MockRecv)>(originals[Recv]));
    assert(J4ChannelFixture::original_connection_status==reinterpret_cast<decltype(&MockSignal)>(originals[SignalStatus]));
    const auto guardsBefore=BbscMod::guarded;
    auto refused=&MockSend;
    Swap(AllSlots[Send],&MockSend,refused,"existing-channel");
    assert(BbscMod::guarded==guardsBefore && refused==&MockSend &&
           *reinterpret_cast<U64*>(BbscMod::base+AllSlots[Send])==Word(&J4ChannelFixture::Sendto));
    assert(Lines("skipped: already wrapped by mod")==1);
    Pass("actual guard refresh 20 rounds: three existing channel plus nine diagnostic guards, no duplicate slots");
    Pass("Swap refuses an already mod-wrapped address without replacing it or registering a guard");

    std::array<unsigned char,64> request{}; U32 id=42,status=7,address=9,len=16;
    S32 conn=0, signedStatus=11; U16 port=20444;
    request[0]=57; request[8]=7; request[9]=0xd0;
    std::array<char,16> peer{'s','y','n','t','h','e','t','i','c'};

    // Focused selectors also let the review retain independent before-fix evidence.
    const std::string selected=argc==3 ? argv[2] : "";
    if(selected.empty() || selected=="active") {
        Reset();status=2;
        const auto before=invoked;
        assert(J4ChannelFixture::ConnectionStatus(8,17,&status,&address,&port)==0);
        assert((invoked==before+1 && arguments==std::array<U64,6>{8,17,Word(&status),Word(&address),Word(&port)}));
        assert(GetLastError()==1789 && errno==95);
        assert(windows.empty() && activeWindows==0 && serial==0);
        assert(Lines("signaling-active ")==1);
        assert(Contains("signaling-active ","ctx=8 connId=17") &&
               Contains("signaling-active ","port=20444") &&
               Contains("signaling-active ","t_ms=100") &&
               Contains("signaling-active ","utc_ms="));
        Pass("outside-window ACTIVE observation reaches original once and records connection/timestamps");
        for(int i=0;i<20;++i) {
            assert(J4ChannelFixture::ConnectionStatus(8,17,&status,&address,&port)==0);
            assert(GetLastError()==1789 && errno==95);
        }
        assert(Lines("signaling-active ")==1);
        status=1;J4ChannelFixture::ConnectionStatus(8,17,&status,&address,&port);
        assert(Lines("signaling-active ")==1);
        status=2;J4ChannelFixture::ConnectionStatus(8,17,&status,&address,&port);
        assert(Lines("signaling-active ")==2);
        J4ChannelFixture::ConnectionStatus(8,18,&status,&address,&port);
        assert(Lines("signaling-active ")==3);
        // Failed and unreadable responses must not reset a valid ACTIVE state.
        status=1;returnCode=-9;J4ChannelFixture::ConnectionStatus(8,17,&status,&address,&port);
        ObserveSignalStatus(nullptr,0,8,17,nullptr,nullptr);
        returnCode=0;status=2;J4ChannelFixture::ConnectionStatus(8,17,&status,&address,&port);
        assert(Lines("signaling-active ")==3);
        // The event path cannot acquire the window mutex, even with ACTIVE data.
        std::atomic<bool> activeDone{false};
        mutex.lock();
        std::thread signalThread([&]{ObserveSignalStatus(nullptr,0,8,19,&status,&port);activeDone=true;});
        for(int i=0;i<200 && !activeDone;++i) Sleep(1);
        const bool activeWhileWindowLocked=activeDone.load();
        mutex.unlock();signalThread.join();
        assert(activeWhileWindowLocked && Lines("signaling-active ")==4);
        // Many simultaneous polls share the same logger lock, hence one event.
        std::vector<std::thread> pollers;
        for(int i=0;i<8;++i) pollers.emplace_back([&]{
            for(int n=0;n<20;++n) ObserveSignalStatus(nullptr,0,8,20,&status,&port);
        });
        for(auto& thread:pollers) thread.join();
        assert(Lines("signaling-active ")==5);
        enabled=false;
        const auto disabledReads=testReads.load();
        ObserveSignalStatus(nullptr,0,8,21,&status,&port);
        assert(testReads==disabledReads && Lines("signaling-active ")==5);
        enabled=true;
        Pass("repeated ACTIVE is deduplicated; state changes re-arm; errors/nulls do not re-arm; shared log lock serializes concurrent polls");
        Pass("ACTIVE observation does not acquire the window mutex; disabled observer reads nothing");

    }
    if(selected.empty() || selected=="join") {
        Reset();
        for(S32 code:{S32(0),S32(123456),S32(-123456789)}) {
            returnCode=code;
            const auto before=invoked;
            assert(JoinRoom(0xfedc,request.data(),request.data(),&id)==code);
            assert((arguments==std::array<U64,6>{0xfedc,Word(request.data()),Word(request.data()),Word(&id)}));
            assert(invoked==before+1 && GetLastError()==1789 && errno==95);
            assert(Contains("joinroom-no-window ","roomId=57") &&
                   Contains("joinroom-no-window ","result="+std::to_string(code)));
        }
        assert(Lines("joinroom-no-window ")==3 && windows.empty() && serial==0);
        Pass("JoinRoom without Activate records original roomId and all result codes without opening a window");
        Reset();returnCode=0;
        assert(JoinRoom(1,nullptr,nullptr,nullptr)==0);
        assert(Contains("joinroom-no-window ","roomId=0") &&
               Contains("joinroom-no-window ","request40=<unreadable>"));
        assert(GetLastError()==1789 && errno==95);
        Reset();enabled=false;
        const auto disabledJoinReads=testReads.load();
        assert(JoinRoom(1,nullptr,nullptr,nullptr)==0);
        assert(testReads==disabledJoinReads && BbscMod::logs.empty());
        enabled=true;
        Reset();ActivateConnection(8,peer.data(),&conn);
        assert(JoinRoom(1,request.data(),nullptr,&id)==0);
        assert(Lines("joinroom-no-window ")==0 && Contains("function=sceNpMatching2JoinRoom","roomId=57"));
        assert(windows.front().end==1100 && GetLastError()==1789 && errno==95);
        Pass("JoinRoom null/disabled handling and existing-window diagnostics/tail remain intact");

    }
    if(selected.empty() || selected=="probe") {
        Reset();
        SetLastError(4321);errno=71;
        ObserveSignalStatus(nullptr,-37,8,17,nullptr,nullptr);
        assert(Lines("signal-status-no-window ")==1 &&
               Contains("signal-status-no-window ","ctx=8 connId=17") &&
               Contains("signal-status-no-window ","result=-37"));
        assert(GetLastError()==4321 && errno==71 && windows.empty());
        enabled=false;ObserveSignalStatus(nullptr,0,8,17,&status,&port);
        assert(Lines("signal-status-no-window ")==1);enabled=true;
        Pass("unconditional no-window status probe logs even errors/nulls and remains disabled without switch");
    }
    if(selected.empty() || selected=="claim") {
        Reset();
        J4HttpFixture::request_urls[55]="http://fixture.invalid/summon_messenger/create";
        std::string response="{\"UserId\":123,\"RoomId\":456}";
        returnCode=S32(response.size());
        const auto before=invoked;
        assert(J4HttpFixture::ReadData(55,response.data(),response.size())==S32(response.size()));
        assert((arguments==std::array<U64,6>{55,Word(response.data()),response.size()}));
        assert(invoked==before+1 && GetLastError()==1789 && errno==95);
        assert(Lines("claim-delivered ")==1 && windows.size()==1 && activeWindows==1);
        assert(windows.front().calls[Activate]==0);
        testNow=200;Tick();assert(Lines("sample window=")==1);
        returnCode=0;JoinRoom(1,request.data(),nullptr,&id);
        assert(Contains("function=sceNpMatching2JoinRoom","roomId=57"));
        assert(Lines("joinroom-no-window ")==0 && windows.front().end==1200);
        testNow=1200;Tick();assert(windows.empty() && Contains("summary","trigger=claim"));
        Reset();returnCode=S32(response.size());
        J4HttpFixture::ReadData(55,response.data(),response.size());
        testNow=8100;Tick();assert(windows.empty() && Contains("summary","outcome=timeout"));
        assert(Contains("summary","sceNpSignalingActivateConnection:0"));
        Reset();
        std::string bare="{\"MessageId\":\"SummonDataCreateResponse\"}";
        returnCode=S32(bare.size());J4HttpFixture::ReadData(55,bare.data(),bare.size());
        J4HttpFixture::request_urls[55]="http://fixture.invalid/summon_messenger/get";
        returnCode=S32(response.size());J4HttpFixture::ReadData(55,response.data(),response.size());
        assert(windows.empty() && Lines("claim-delivered ")==0);
        J4HttpFixture::request_urls[55]="http://fixture.invalid/summon_messenger/create";
        returnCode=-17;assert(J4HttpFixture::ReadData(55,nullptr,0)==-17);
        enabled=false;returnCode=S32(response.size());J4HttpFixture::ReadData(55,response.data(),response.size());
        assert(windows.empty() && Lines("claim-delivered ")==0);enabled=true;
        Pass("actual HTTP ReadData opens a claim-only observation window; unchanged bytes/ABI/result; normal JoinRoom closes it");
        Pass("claim timeout reports zero Activate; bare/get/error/disabled HTTP replies create no diagnostic window");
    }
    if(!selected.empty()) {
        assert(selected=="active" || selected=="join" || selected=="probe" || selected=="claim");
        std::cout<<"PASS focused outside-window case: "<<selected<<'\n';
        return 0;
    }
    Reset();status=7;
    for(S32 result:{S32(0),S32(123456),S32(-123456789)}) for(bool nulls:{false,true}) {
        Reset(); returnCode=result;
        auto* p=nulls?nullptr:request.data();auto* u=nulls?nullptr:&id;
        auto* s=nulls?nullptr:&signedStatus;auto* us=nulls?nullptr:&status;
        auto* a=nulls?nullptr:&address;auto* pt=nulls?nullptr:&port;
        const auto* np=nulls?nullptr:peer.data();
        const auto verify=[&](S32 actual,std::array<U64,6> expected) {
            assert(actual==result && arguments==expected && GetLastError()==1789 && errno==95);
        };
        unsigned before=invoked;
        verify(ActivateConnection(8,np,nulls?nullptr:&conn),{8,Word(np),Word(nulls?nullptr:&conn)});
        verify(DeactivateConnection(8,17),{8,17});
        for(auto fn:{&JoinRoom,&CreateJoinRoom,&LeaveRoom,&SearchRoom})
            verify(fn(0xfedc,p,p,u),{0xfedc,Word(p),Word(p),Word(u)});

        const auto signalResult=J4ChannelFixture::ConnectionStatus(8,17,us,a,pt);
        if(nulls) {
            assert(signalResult==result && arguments[0]==8 && arguments[1]==17 && arguments[2]==0 &&
                   arguments[3]!=0 && arguments[4]!=0 && GetLastError()==1789 && errno==95);
        } else verify(signalResult,{8,17,Word(us),Word(a),Word(pt)});
        verify(MatchingConnectionStatus(0xfedc,0xfedcba9876543210ULL,0x8234,s,p,pt),
               {0xfedc,0xfedcba9876543210ULL,0x8234,Word(s),Word(p),Word(pt)});
        verify(GetPingInfo(0xfedc,p,p,s),{0xfedc,Word(p),Word(p),Word(s)});
        verify(SignalCreateContext(np,p,p,s),{Word(np),Word(p),Word(p),Word(s)});
        verify(J4ChannelFixture::Recvfrom(17,p,0x123456789ULL,-32,p,nulls?nullptr:&len),
               {17,Word(p),0x123456789ULL,U64(S32(-32)),Word(p),Word(nulls?nullptr:&len)});
        verify(J4ChannelFixture::Sendto(17,p,0x123456789ULL,-32,p,16),
               {17,Word(p),0x123456789ULL,U64(S32(-32)),Word(p),16});
        assert(invoked-before==FunctionCount);
    }
    Pass("nine diagnostic plus three actual channel wrappers reach each original exactly once; success/positive/negative unchanged");
    Pass("all nullable pointer parameters pass through unchanged without diagnostic crashes; errno/last-error retained");


    Reset();
    std::atomic<bool> outsideDone{false};
    mutex.lock();
    const auto outsideReads=testReads.load();
    std::thread outside([&]{
        auto* net=BeginNet(true,nullptr,nullptr);
        auto* signal=BeginSignal(nullptr,nullptr);
        assert(!net && !signal);
        ObserveNet(net,-1,17,64,nullptr,0,nullptr);
        ObserveSignalStatus(signal,-1,8,17,nullptr,nullptr);
        outsideDone=true;
    });
    for(int i=0;i<100 && !outsideDone;++i) Sleep(1);
    const bool completedWhileLocked=outsideDone.load();
    mutex.unlock();outside.join();
    assert(completedWhileLocked && testReads==outsideReads && Lines("signal-status-no-window ")==1);
    Pass("idle network stays silent; failed no-window status emits one probe without window lock or diagnostic reads");

    Reset(); ActivateConnection(8,peer.data(),&conn);
    assert(windows.size()==1 && windows.front().connection==17);
    testNow=200;DeactivateConnection(8,18);assert(windows.front().end==8100);
    testNow=300;DeactivateConnection(8,17);assert(windows.front().end==1300);
    testNow=600;DeactivateConnection(8,17);assert(windows.front().end==1300);
    testNow=1299;Tick();assert(windows.size()==1);
    testNow=1300;Tick();assert(windows.empty());
    assert(Contains("summary","outcome=Deactivate") && Contains("summary","duration_ms=1200"));
    Pass("matching context/connId ends with one-second tail; unrelated conn and repeated events never extend it");
    Reset();ActivateConnection(8,peer.data(),&conn);testNow=500;JoinRoom(1,request.data(),nullptr,&id);
    testNow=1500;Tick();assert(windows.empty()&&Contains("summary","outcome=JoinRoom"));
    assert(Contains("sceNpMatching2JoinRoom","roomId=57") &&
           Contains("sceNpMatching2JoinRoom","request40=3900"));
    Pass("JoinRoom records original roomId/request head then ends after one second");
    Reset();ActivateConnection(8,peer.data(),&conn);testNow=8099;JoinRoom(1,nullptr,nullptr,nullptr);
    assert(windows.front().end==8100);testNow=8100;Tick();assert(windows.empty());
    Reset();ActivateConnection(8,peer.data(),&conn);testNow=8100;Tick();
    assert(windows.empty()&&Contains("summary","outcome=timeout")&&Contains("summary","duration_ms=8000"));
    Pass("eight-second absolute cap holds with or without a late terminal call");
    Reset();ActivateConnection(8,peer.data(),&conn);
    for(int i=0;i<100;++i) J4ChannelFixture::Sendto(17,request.data(),64,0,request.data(),16);
    const auto cappedReads=testReads.load();
    for(int i=0;i<7;++i) J4ChannelFixture::Sendto(17,request.data(),64,0,request.data(),16);
    for(int i=0;i<103;++i) J4ChannelFixture::Recvfrom(17,request.data(),64,0,request.data(),&len);
    assert(testReads==cappedReads); // Over-budget packets count without any diagnostic memory reads.
    assert(windows.front().calls[Send]==107 && windows.front().calls[Recv]==103);
    assert(windows.front().packetLines==100 && windows.front().packets==210);
    assert(Lines(" function=sceNet")==100 && Contains("function=sceNet","vport=2000"));
    testNow=8100;Tick();
    assert(Contains("summary","net_suppressed=110"));
    Pass("send+recv share a 100-line window budget; all later calls remain counted");
    Reset();
    for(int i=0;i<4;++i) *reinterpret_cast<S32*>(manager+0x118+i*4)=0;
    Tick();assert(BbscMod::logs.empty());
    *reinterpret_cast<S32*>(manager+0x124)=3;Tick();Tick();
    assert(everHost && Lines("host-first")==1 && Lines("sample")==0 &&
           Contains("host-first","state=3")&&Contains("host-first","utc_ms="));
    ObserveHttpUrl("http://fixture.invalid/summon_messenger/get?user_id=1");
    ObserveHttpUrl("/summon_messenger/get");
    ObserveHttpUrl("http://fixture.invalid/x?path=/summon_messenger/get");
    ObserveHttpUrl("/summon_messenger/request");
    CreateJoinRoom(1,nullptr,nullptr,nullptr);
    assert(getTotal==2 && createJoinTotal==1 && Lines(" function=sceNpMatching2CreateJoinRoom")==0);
    ActivateConnection(8,peer.data(),&conn);Tick();Tick();assert(Lines("sample")==1);
    *reinterpret_cast<S32*>(manager+0x11c)=41;Tick();assert(Lines("sample")==2);
    testNow=8100;Tick();
    assert(Contains("summary","ever_host=1")&&Contains("summary","summon_get_total=2")&&
           Contains("summary","create_join_total=1"));
    Pass("outside-window host history logs once; four-field samples log only changes; event totals survive windows");
    Reset();
    ActivateConnection(8,peer.data(),&conn);
    DWORD protection;VirtualProtect(manager,4096,PAGE_NOACCESS,&protection);Tick();
    assert(Contains("sample","+118=-1 +11c=-1 +120=-1 +124=-1"));
    VirtualProtect(manager,4096,protection,&protection);
    *reinterpret_cast<U64*>(BbscMod::base+Singleton)=0;Tick();testNow=8100;Tick();
    Pass("VirtualQuery plus guarded reads reject unreadable/null manager without touching it");
    Reset();
    const auto firstSerial=serial;
    Call outer(Activate,reinterpret_cast<void*>(BbscMod::base+0x1234),nullptr,1);
    U64 candidates[64]{BbscMod::base+0x2000,BbscMod::base+0x2100,
                      BbscMod::base+0x2200,BbscMod::base+0x2300,BbscMod::base+0x2400};
    Call query(SignalStatus,reinterpret_cast<void*>(BbscMod::base+0x3456),candidates);
    query.Complete(-15,"");
    assert(query.location.find("caller=eboot+0x3456")!=std::string::npos &&
           query.location.find("eboot+0x2300")!=std::string::npos &&
           query.location.find("eboot+0x2400")==std::string::npos);
    assert(Location(reinterpret_cast<void*>(123),nullptr).find("caller=0x7b")!=std::string::npos);
    testNow=8100;Tick();testNow=8200;
    ActivateConnection(1,peer.data(),&conn);assert(serial==firstSerial+2);
    Pass("caller offsets, four stack candidates, outside-image address and per-process window sequence");

    Reset();ActivateConnection(8,peer.data(),&conn);
    DWORD gameProtection;
    assert(VirtualProtect(reinterpret_cast<void*>(BbscMod::base+0x2000),4096,PAGE_EXECUTE_READWRITE,&gameProtection));
    const auto gameSend=reinterpret_cast<decltype(&MockSend)>(Thunk(0,Word(&J4ChannelFixture::Sendto),true));
    const auto beforeGame=invoked;
    assert(gameSend(17,nullptr,0,0,nullptr,0)==0 && invoked==beforeGame+1);
    assert(Contains("function=sceNetSendto","caller=eboot+0x2010"));
    const auto beforeWorker=invoked;
    assert(J4ChannelFixture::original_recvfrom(17,nullptr,0,0,nullptr,nullptr)==0 && invoked==beforeWorker+1);
    Pass("game-facing channel records the actual guest return offset; channel worker original reaches native directly");

    enabled=false;const auto total=getTotal.load();ObserveHttpUrl("/summon_messenger/get");Tick();
    assert(getTotal==total);
    VirtualFree(hostCode,0,MEM_RELEASE);
    VirtualFree(manager,0,MEM_RELEASE);VirtualFree(reinterpret_cast<void*>(BbscMod::base),0,MEM_RELEASE);
    std::cout<<"PASS: J4 production wrappers, read-only sampling, bounds and disabled startup\n";
}
