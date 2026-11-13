// Real prepared HTTP wrappers and direct WinHTTP cleanup; dependency stubs only.
#ifndef HTTP_SOURCE
#define HTTP_SOURCE "../.build/relay-auth-client/emulator/mod/bbsc_http.cpp"
#endif
#include HTTP_SOURCE
#include <cassert>
#include <iostream>

static bool test_private=true, test_in_room=false;
static std::optional<u32> test_summon_map;
static std::string test_origin="http://127.0.0.1:31415";
static std::vector<u8> fake_eboot(EbootSize),fake_manager(0x200);
static int sent=0, next_request=1, placement_clear_count=0;
static std::string prepared_url, sent_body;
static std::map<int,std::map<std::string,std::string>> headers_added;
namespace BbscMod {
void LogLine(char,const char*,const std::string&){}
bool RelayAuthPrivate(){return test_private;}
bool RelayAuthInRoom(){return test_private && test_in_room;}
std::uint32_t RelayAuthOwnId(){return 17;}
std::string RelayAuthHttpTarget(const std::string& url){return url;}
std::string RelayAuthBearer(const std::string& url){
    return test_private && TheHuntRelayAuth::BearerTarget(url,test_origin)?"Bearer fixture-token":"";
}
void RelaySendPlacement(const std::string&){}
u64 EbootAddress(){return reinterpret_cast<u64>(fake_eboot.data());}
void GuardSlot(u64,u64,void**,const char*){}
}
namespace Core::Bloodborne {
std::optional<std::string> GetSeamlessHostPlacementHeader(){return {};}
std::optional<u32> GetBbscConnectFirstAreaId(){return test_summon_map;}
bool SetSeamlessHostPlacementHeader(std::string_view){return true;}
void ClearSeamlessHostPlacementHeader(){++placement_clear_count;}
}
static int PS4_SYSV_ABI add_header(int request,const char* key,const char* value,s32){headers_added[request][key]=value;return 0;}
static int PS4_SYSV_ABI set_length(int,u64){return 0;}
static int PS4_SYSV_ABI send_request(int,const void* data,u64 size){
    ++sent; sent_body=data ? std::string(static_cast<const char*>(data),size) : ""; return 0;
}
static int PS4_SYSV_ABI create_request(int,s32,const char* url,u64){prepared_url=url;return next_request++;}
static int PS4_SYSV_ABI delete_request(int){return 0;}
static void prepare_slots(){
    auto put=[](u64 offset,u64 value){std::memcpy(fake_eboot.data()+offset,&value,8);};
    put(0x05540290,reinterpret_cast<u64>(fake_manager.data()));
    put(SlotAddHeader,reinterpret_cast<u64>(&add_header));
    put(SlotSetContentLength,reinterpret_cast<u64>(&set_length));
    original_create_request=&create_request;original_send=&send_request;original_delete=&delete_request;
}
int main(int argc,char** argv){
    if(argc==3 && std::string(argv[1])=="--post"){
        if(const char* origin=std::getenv("FIXTURE_HTTP_ORIGIN"))test_origin=origin;
        std::cout<<"STATUS "<<Post(argv[2],"{}")<<"\n";return 0;
    }
    prepare_slots();
    // The running BBport makes POSTs with its game account alias in the URL.
    // Replay the real create/send wrapper boundary; bare fixture paths missed it.
    for(const auto* endpoint:{"create","get","delete","request"}){
      for(const auto* query:{"?user_id=4935", ""}) {
        const auto url=test_origin+"/summon_messenger/"+endpoint+query;
        const int request=CreateRequestWithURL(1,1,url.c_str(),0);
        assert(prepared_url==url);
        const auto before=sent;
        assert(SendRequest(request,nullptr,0)==0);
        assert(sent==before+1);
        assert(headers_added[request]["Authorization"]=="Bearer fixture-token");
        assert(headers_added[request]["X-ShadPS4-Bloodborne-Seamless"]=="1");
        DeleteRequest(request);
      }
    }
    const int before=sent;
    for(const auto* url:{"http://127.0.0.1:20443/summon_messenger/create",
        "http://127.0.0.1:31415/summon_messenger/create/evil","http://127.0.0.1:31415/summon_messenger/get?q=1",
        "http://127.0.0.1:31415/summon_messenger/get?user_id=",
        "http://127.0.0.1:31415/summon_messenger/get?user_id=4935&other=1",
        "http://127.0.0.1:31415/summon_messenger/get?user_id=4935#x",
        "http://127.0.0.1:31415/summon_messenger/get?user_id=%31",
        "http://user@127.0.0.1:31415/summon_messenger/get?user_id=4935",
        "http://127.0.0.1.evil.invalid:31415/summon_messenger/get?user_id=4935"}){
        const int request=CreateRequestWithURL(1,1,url,0);assert(SendRequest(request,nullptr,0)<0);
        assert(headers_added[request].empty());
    }
    assert(sent==before);
    // Retaining a matched Dream landing for the native reload must not cause
    // a new HTTP advertisement to claim we are still looking there.
    const auto summon_url=test_origin+"/summon_messenger/create?user_id=4935";
    const std::string advertisement=R"({"AreaId":402718720})";
    test_summon_map=0x15000000;
    const int sign=CreateRequestWithURL(1,1,summon_url.c_str(),0);
    assert(SendRequest(sign,advertisement.data(),advertisement.size())==0);
    assert(sent_body==R"({"AreaId":352321536})");
    test_in_room=true;
    assert(SendRequest(sign,advertisement.data(),advertisement.size())==0);
    assert(sent_body==advertisement);
    DeleteRequest(sign);
    test_private=false;
    const int public_supported=CreateRequestWithURL(1,1,"https://thehuntersdream.com/summon_messenger/create",0);
    assert(SendRequest(public_supported,nullptr,0)==0 && headers_added[public_supported].empty());
    placement_received_ms=123;placement_sender=18;BbscMod::RelayAuthClearPlacement();
    assert(placement_received_ms==0 && placement_sender==0 && placement_clear_count==1);
    std::cout<<"PASS actual HTTP private bearer/public isolation\n";
}
