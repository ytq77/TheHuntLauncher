// SPDX-License-Identifier: GPL-2.0-or-later
// Real generated wrappers and storage; only emulator HTTP callbacks are synthetic.
#ifndef HTTP_SOURCE
#define HTTP_SOURCE "../.build/bbsc/emulator/mod/bbsc_http.cpp"
#endif
#ifndef BBSC_J4_DIAG_DISABLED
#define BBSC_J4_DIAG_DISABLED
#endif
#include HTTP_SOURCE
#include <cassert>
#include <iostream>
#include <set>
static std::mutex fake_mutex,store_mutex;
static std::map<int,std::string> fake_responses;
static std::deque<CleanupEvent> fake_events;
static std::set<int> fake_notified;
static std::vector<int> fake_send_order;
static std::atomic<int> sent{0},removed{0},next_id{501},current_deleted{0},native_deleted{0};
static std::atomic<bool> allow_delete{false},response_ready{true};
static std::string test_origin="http://127.0.0.1:31415";
static bool test_private=true;
static std::vector<u8> test_image(0x056D4000),test_manager(0x200),test_global(0x2000),test_world(0x100),test_player(0x100);
namespace BbscMod {
void LogLine(char,const char*,const std::string&){}
bool RelayAuthPrivate(){return test_private;}
bool RelayAuthInRoom(){return false;}
std::uint32_t RelayAuthOwnId(){return 17;}
std::string RelayAuthHttpTarget(const std::string& url){return url;}
std::string RelayAuthBearer(const std::string& url){return TheHuntRelayAuth::BearerTarget(url,test_origin)?"Bearer fixture-token":"";}
void RelaySendPlacement(const std::string&){}
u64 EbootAddress(){return reinterpret_cast<u64>(test_image.data());}
void GuardSlot(u64,u64,void**,const char*){}
}
namespace Core::Bloodborne {
std::optional<std::string> GetSeamlessHostPlacementHeader(){return {};}
std::optional<u32> GetBbscConnectFirstAreaId(){return {};}
bool SetSeamlessHostPlacementHeader(std::string_view){return true;}
void ClearSeamlessHostPlacementHeader(){}
}
static void Wait(const std::function<bool()>& predicate) {
    for(int i=0;i<600;++i) {if(predicate()) return;Sleep(5);} assert(false && "HTTP queue timeout");
}
static int PS4_SYSV_ABI NativeCreate(int,s32,const char*,u64){return next_id++;}
static int PS4_SYSV_ABI NativeSend(int request,const void* data,u64 length){
    std::scoped_lock lock(fake_mutex);++sent;
    fake_send_order.push_back(request);
    fake_responses[request]=data?std::string(static_cast<const char*>(data),length):"";return 0;
}
static int PS4_SYSV_ABI NativeStatus(int request,int* status){
    std::scoped_lock lock(fake_mutex);
    if(!fake_responses.contains(request)) return CleanupInvalid;
    if(!response_ready && !fake_notified.contains(request)) return CleanupAgain;
    if(fake_notified.insert(request).second) fake_events.push_back({1,2,request,reinterpret_cast<void*>(std::uintptr_t(request))});
    *status=200;return 0;
}
static int PS4_SYSV_ABI NativeLength(int,int* kind,u64* size){*kind=1;*size=3;return 0;}
static int PS4_SYSV_ABI NativeRead(int,void* data,u64 size){if(size<3) return CleanupInvalid;std::memcpy(data,"ok!",3);return 3;}
static int PS4_SYSV_ABI NativeDelete(int request){std::scoped_lock lock(fake_mutex);++native_deleted;fake_responses.erase(request);return 0;}
static int PS4_SYSV_ABI NativeWait(void*,CleanupEvent* events,int count,int){
    std::scoped_lock lock(fake_mutex);int n=0;
    while(n<count && !fake_events.empty()){events[n++]=fake_events.front();fake_events.pop_front();}return n;
}
int main(int argc,char** argv) {
    assert(argc==2);
    const char* wire=std::getenv("CLEANUP_WIRE_ORIGIN");
    if(wire) test_origin=wire;
    const auto folder=std::filesystem::absolute(argv[1]);std::filesystem::create_directories(folder);
    CleanupStore store;store.path=folder/"record.state";store.origin=test_origin;store.account="17";
    const auto lock=folder/"record.lock";
    store.lease=CreateFileW(lock.c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    assert(store.lease!=INVALID_HANDLE_VALUE);
    assert(store.Persist({"previous-process","8617"}));
    TheHuntSummon::Sign loaded;assert(store.Load(loaded)==1 && loaded.session=="previous-process");
    store.origin="http://127.0.0.1:31416";assert(store.Load(loaded)==-1);store.origin=test_origin;
    {std::ofstream file(store.path,std::ios::binary|std::ios::trunc);file<<std::string(2049,'x');}
    assert(store.Load(loaded)==-1);assert(store.Persist({"previous-process","8617"}));
    HANDLE rival=CreateFileW(lock.c_str(),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    assert(rival==INVALID_HANDLE_VALUE);
    std::cout<<"PASS HTTP cleanup: actual atomic store, scope validation, corrupt record and same-install exclusion\n";

    original_create_request=&NativeCreate;original_send=&NativeSend;original_delete=&NativeDelete;
    original_status=&NativeStatus;original_length=&NativeLength;original_read_data=&NativeRead;original_wait=&NativeWait;
    TheHuntSummon::Backend backend;
    backend.load=[&](auto& s){std::scoped_lock l(store_mutex);return store.Load(s);};backend.persist=[&](const auto& s){std::scoped_lock l(store_mutex);return store.Persist(s);};
    backend.erase=[&]{std::scoped_lock l(store_mutex);return store.Erase();};
    backend.remove=[&](const auto& s){
        if(s.session=="new-process") {++current_deleted;return true;}
        assert(s.session=="previous-process");++removed;
        if(!allow_delete) return false;
        if(!wire) return true;
        const auto body="{\"MessageId\":\"SummonDataRemoveRequest\",\"SessionId\":\""+s.session+"\",\"UserId\":"+s.user+",\"Force\":true}";
        const auto status=Post(test_origin+"/summon_messenger/delete",body);
        return status>=200 && status<300;
    };
    backend.sample=[] {return TheHuntSummon::Sample{0,true,true,false,false};};
    backend.now=[] {return GetTickCount64();};backend.log=[](const auto&){};
    auto worker=std::make_unique<TheHuntSummon::SerialCleanup>(std::move(backend));summon_cleanup=worker.get();cleanup_started=true;
    const auto url=test_origin+"/summon_messenger/create?user_id=8617";
    const std::string body=R"({"SessionId":"new-process","UserId":8617,"AreaId":553648128})";
    const auto first=CreateRequestWithURL(1,1,url.c_str(),body.size());
    const auto before=GetTickCount64();assert(ForwardSend(first,body.data(),body.size())==0);
    assert(GetTickCount64()-before<50);
    int status=777,kind=555;u64 length=999;char data[4]={'x','x','x','x'};
    assert(CleanupGetStatus(first,&status)==CleanupAgain && status==777);
    assert(CleanupGetLength(first,&kind,&length)==CleanupAgain && kind==555 && length==999);
    assert(ReadData(first,data,4)==CleanupAgain && data[0]=='x');
    Wait([&]{return removed>=2;});assert(sent==0);
    const auto cancelled=CreateRequestWithURL(1,1,url.c_str(),body.size());
    assert(ForwardSend(cancelled,body.data(),body.size())==0);assert(DeleteRequest(cancelled)==0);
    next_id=cancelled;
    const auto reused=CreateRequestWithURL(1,1,url.c_str(),body.size());assert(reused==cancelled);
    assert(ForwardSend(reused,body.data(),body.size())==0);
    allow_delete=true;Wait([&]{return sent==2;});
    Wait([&]{return !CleanupRequestResult(reused).has_value();});
    assert(CleanupGetStatus(first,&status)==0 && status==200);
    assert(CleanupGetLength(first,&kind,&length)==0 && kind==1 && length==3);
    assert(ReadData(first,data,4)==3 && std::string(data,3)=="ok!");
    {std::scoped_lock guard(fake_mutex);assert(fake_responses[first]==body && fake_responses[reused]==body);}
    {std::scoped_lock l(store_mutex);assert(store.Load(loaded)==1 && loaded.session=="new-process");}
    CleanupEvent events[4]{};Wait([&]{int r=0;return CleanupGetStatus(reused,&r)==0;});
    assert(CleanupWait(nullptr,events,4,0)==2 && events[0].id==first && events[1].id==reused);
    assert(events[0].events==1 && events[0].detail==2 && events[0].user==reinterpret_cast<void*>(std::uintptr_t(first)));
    assert(DeleteRequest(first)==0 && DeleteRequest(reused)==0);
    std::cout<<"PASS HTTP cleanup: startup barrier, exact async polls, payload/result forwarding, cancellation and ID reuse\n";

    response_ready=false;
    const auto delayed=CreateRequestWithURL(1,1,url.c_str(),body.size());
    assert(ForwardSend(delayed,body.data(),body.size())==0);Wait([&]{return sent==3;});
    const auto deleted_before=native_deleted.load();
    assert(DeleteRequest(delayed)==0 && native_deleted==deleted_before);
    worker->ReclearOnNextSign();
    const auto following=CreateRequestWithURL(1,1,url.c_str(),body.size());
    assert(ForwardSend(following,body.data(),body.size())==0);
    Sleep(150);assert(sent==3 && current_deleted==0 && native_deleted==deleted_before);
    response_ready=true;Wait([&]{return sent==4;});
    assert(current_deleted==1 && native_deleted==deleted_before+1);
    Wait([&]{int result=0;return CleanupGetStatus(following,&result)==0;});
    assert(CleanupWait(nullptr,events,4,0)==1 && events[0].id==following);
    assert(DeleteRequest(following)==0);
    std::cout<<"PASS HTTP cleanup: cancellation waits completion before delete/new-create; cancelled epoll event suppressed\n";

    response_ready=false;
    const auto pending_create=CreateRequestWithURL(1,1,url.c_str(),body.size());
    assert(ForwardSend(pending_create,body.data(),body.size())==0);Wait([&]{return sent==5;});
    const auto remove_url=test_origin+"/summon_messenger/delete?user_id=8617";
    const auto game_delete=CreateRequestWithURL(1,1,remove_url.c_str(),body.size());
    assert(ForwardSend(game_delete,body.data(),body.size())==0);
    const auto next_create=CreateRequestWithURL(1,1,url.c_str(),body.size());
    assert(ForwardSend(next_create,body.data(),body.size())==0);
    Sleep(150);assert(sent==5 && "native ordinary delete must not overtake queued create");
    response_ready=true;Wait([&]{return sent==7;});
    {std::scoped_lock guard(fake_mutex);const auto n=fake_send_order.size();
     assert(fake_send_order[n-3]==pending_create && fake_send_order[n-2]==game_delete && fake_send_order[n-1]==next_create);}
    for(int request:{pending_create,game_delete,next_create}) {Wait([&]{int status=0;return CleanupGetStatus(request,&status)==0;});}
    assert(CleanupWait(nullptr,events,4,0)==3);
    for(int request:{pending_create,game_delete,next_create}) assert(DeleteRequest(request)==0);
    std::cout<<"PASS HTTP cleanup: native ordinary delete and next create share the same completion-ordered queue\n";

    auto put=[](auto& bytes,std::size_t offset,auto value){std::memcpy(bytes.data()+offset,&value,sizeof(value));};
    put(test_image,0x05540290,reinterpret_cast<u64>(test_manager.data()));put(test_manager,0x124,s32{0});
    put(test_image,0x05556678,reinterpret_cast<u64>(test_global.data()));
    put(test_image,0x0553E878,reinterpret_cast<u64>(test_world.data()));
    put(test_world,0x60,reinterpret_cast<u64>(test_player.data()));put(test_player,0x78,s32{0});
    test_global[0x1592]=1;
    auto sample=CleanupSample();assert(sample.readable && sample.state==0 && sample.home && !sample.loading && !sample.in_room);
    test_global[8]=1;assert(CleanupSample().loading);
    put(test_image,0x05540290,u64{1});assert(!CleanupSample().readable);
    std::cout<<"PASS HTTP cleanup: actual VirtualQuery/ReadProcessMemory sampling, own world and loading checks\n";
    summon_cleanup=nullptr;cleanup_started=true;
    const auto unavailable_create=CreateRequestWithURL(1,1,url.c_str(),body.size());
    const auto unavailable_delete=CreateRequestWithURL(1,1,remove_url.c_str(),body.size());
    assert(ForwardSend(unavailable_create,body.data(),body.size())==CleanupBusy);
    assert(ForwardSend(unavailable_delete,body.data(),body.size())==CleanupBusy && sent==7);
    cleanup_started=false;test_private=false;
    const auto public_request=CreateRequestWithURL(1,1,"https://thehuntersdream.com/summon_messenger/create",body.size());
    assert(ForwardSend(public_request,body.data(),body.size())==0);assert(sent==8);
    std::cout<<"PASS HTTP cleanup: public path retains native dispatch\n";
    worker.reset();CloseHandle(store.lease);
}
