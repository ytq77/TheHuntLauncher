// Compiles the real prepared relay, including its protocol, socket I/O and adapter.
// Only the game's side-effect handlers and unrelated public transport are stubbed.
#ifndef RELAY_SOURCE
#define RELAY_SOURCE "../.build/relay-auth-client/emulator/mod/bbsc_relay.cpp"
#endif
#include RELAY_SOURCE
#include <cassert>
#include <iostream>

static int handler_calls = 0, p2p_calls = 0, clear_calls = 0, placement_clears = 0;
static int native_state = 6;
static bool accept_placement = true;
static u32 last_player = 0, last_sender = 0;
namespace BbscMod {
void LogPlain(const char*) {}
bool OnHostPlacement(const std::string&, u32) { ++handler_calls; return accept_placement; }
void RelayAuthClearPlacement() { ++placement_clears; }
bool IsPublicServerAddress(const std::string&) { return false; }
u32 P2PChannelSend(const void*, u32) { ++p2p_calls; return 1; }
u32 P2PChannelPeerCount() { return 1; }
}
namespace Core::Bloodborne {
bool BbscVisibilitySupported() {return true;}
void OnBbscVisibilityPacket(const TheHuntVisibility::Context&,u32,const u8*,std::size_t) {}
void OnBbscRelayAuthContextChanged(std::uint32_t) { ++clear_calls; }
std::int32_t GetBbscRelaySessionState() { return native_state; }
std::uint32_t GetBbscRelayHost() { return authenticated_owner.load(); }
void NoteBbscRelayHost(u32, u32, float, float, float) {}
void OnBbscHostFollowPacket(u32, float, float, float, float, u32, s32) { ++handler_calls; }
void OnBbscLifePacket(u32 player, u8, u32) { ++handler_calls; last_player = player; }
void OnBbscScalePacket(const std::uint16_t (&)[5]) { ++handler_calls; }
void OnBbscPvpPacket(u8, u8, u32 sender) { ++handler_calls; last_sender = sender; }
void OnBbscProgressPacket(const u32*, std::size_t) { ++handler_calls; }
void OnBbscHostBossPacket(s32) { ++handler_calls; }
void OnBbscWorldFlagsPacket(const void*, u32) { ++handler_calls; }
}

using namespace TheHuntRelayAuth;
static Bytes token{'f','i','x','t','u','r','e','-','t','o','k','e','n'};
static Digest key{};
static Bytes signed_frame(Bytes frame) {
    Digest mac{}; assert(Hmac(key, frame, mac));
    frame.insert(frame.end(), mac.begin(), mac.end()); return frame;
}
static Bytes context_frame(u64 sequence, u64 room, u32 owner, u8 peers=1) {
    Bytes frame{'T','H','X','2'}; Put(frame,sequence,8); Put(frame,room,8);
    Put(frame,17,4); Put(frame,owner,4); frame.push_back(peers); return signed_frame(frame);
}
static Bytes data_frame(u64 sequence, u64 room, u32 sender, u32 owner, Bytes inner) {
    Bytes frame{'T','H','S','2'}; Put(frame,sequence,8); Put(frame,room,8);
    Put(frame,sender,4); Put(frame,owner,4); frame.insert(frame.end(),inner.begin(),inner.end());
    return signed_frame(frame);
}
static Bytes placement_frame(u64 sequence,u64 generation,u32 target,u32 ttl,const std::string& text) {
    Bytes frame{'T','H','P','2'};Put(frame,sequence,8);Put(frame,generation,8);Put(frame,9,8);
    Put(frame,18,4);Put(frame,target,4);Put(frame,ttl,4);frame.insert(frame.end(),text.begin(),text.end());
    return signed_frame(frame);
}
static void establish(Client& client, u32 user=17) {
    Bytes challenge{'T','H','C','2'}; challenge.resize(36,0x6e);
    Bytes proof; assert(client.Begin(user, token, challenge, proof));
    constexpr char domain[]="TheHunt relay key v2";
    Bytes message(domain,domain+sizeof(domain)); message.insert(message.end(),challenge.begin()+4,challenge.end());
    Put(message,user,8); assert(Hmac(token,message,key));
    Bytes ack{'T','H','O','2'}; Put(ack,user,8); assert(client.Ack(signed_frame(ack)));
}
static Bytes life(u32 sender) {
    Bytes frame{'S','H','A','D',0x23}; Put(frame,sender,4); frame.push_back(0); Put(frame,1,4);return frame;
}
static void summoned_reload_after_native_membership() {
    const std::string dream = "1,15000000,c107543a,c0cf11f1,c1902d1b,c01b67c9,210000";
    for (const auto next : {Context{9,17,18,1}, Context{10,17,18,1}, Context{9,17,19,1}}) {
        ClearAuthPlacement();
        authenticated_room=0; authenticated_owner=0;
        Client client; establish(client);
        Delivery delivery;
        assert(client.Receive(placement_frame(1,1,17,10000,dream),delivery)==Received::Placement);
        ApplyAuthPlacement(delivery.placement);
        const auto expiry = pending_placement_expires;
        assert(expiry > GetTickCount64());
        assert(client.Receive(context_frame(2,0,0,0),delivery)==Received::Context);
        ApplyAuthContext(delivery);
        assert(pending_placement_expires==expiry); // Periodic zero-room context while advertising.
        assert(client.Receive(context_frame(3,next.room,next.owner),delivery)==Received::Context);
        ApplyAuthContext(delivery);
        if (next.room!=9 || next.owner!=18) {
            assert(!pending_placement_expires);
            continue;
        }
        // Native JoinRoom is acknowledged before the game's later spawn/reload
        // hooks. That specific load must still be able to read its Dream location.
        assert(pending_placement_expires==expiry && pending_placement_generation==1);
        assert(client.Receive(context_frame(4,9,18),delivery)==Received::Context);
        ApplyAuthContext(delivery);
        assert(pending_placement_expires==expiry);
        assert(client.Receive(placement_frame(5,1,17,0,""),delivery)==Received::Placement);
        assert(!delivery.placement.ignored);
        ApplyAuthPlacement(delivery.placement);
        assert(!pending_placement_expires); // A delayed valid cancellation still wins.
    }
    ClearAuthPlacement();
    authenticated_room=0; authenticated_owner=0; clear_calls=0;
}
static void first_spawn_grants() {
    const std::string placement = "1,17000000,41400000,42200000,c0a00000,40400000,230010";
    auto write = +[](std::string_view value, void* count) {
        assert(value.starts_with("1,17000000,"));
        ++*static_cast<int*>(count);
        return true;
    };
    u64 previous = 0;
    own_id = 17;
    private_auth = true;
    for (int scenario = 0; scenario < 6; ++scenario) {
        ClearAuthPlacement();
        authenticated_room = 0;
        authenticated_owner = 0;
        auth_client.Reset();
        establish(auth_client);
        Delivery delivery;
        accept_placement = scenario != 4;
        assert(auth_client.Receive(context_frame(1,0,0,0),delivery)==Received::Context);
        auth_context_ms = GetTickCount64();
        ApplyAuthContext(delivery);
        assert(auth_client.Receive(placement_frame(2,1,17,10000,placement),delivery)==Received::Placement);
        ApplyAuthPlacement(delivery.placement);
        const auto epoch = BbscMod::RelayAuthArmFirstSpawn();
        if (!accept_placement) { assert(!epoch); continue; }
        assert(epoch && epoch != previous);
        int writes = 0;
        assert(!BbscMod::RelayAuthUseFirstSpawn(epoch, write, &writes) && !writes);
        assert(auth_client.Receive(context_frame(3,scenario==1 ? 10 : 9,scenario==2 ? 19 : 18),delivery)==Received::Context);
        auth_context_ms = GetTickCount64();
        ApplyAuthContext(delivery);
        if (scenario == 3) pending_placement_expires = GetTickCount64();
        if (scenario == 5) {
            assert(auth_client.Receive(placement_frame(4,1,17,0,""),delivery)==Received::Placement);
            ApplyAuthPlacement(delivery.placement);
        }
        assert(!BbscMod::RelayAuthUseFirstSpawn(previous, write, &writes) && !writes);
        assert(BbscMod::RelayAuthUseFirstSpawn(epoch, write, &writes) == (scenario == 0));
        assert(!BbscMod::RelayAuthUseFirstSpawn(epoch, write, &writes));
        assert(writes == (scenario == 0));
        previous = epoch;
    }
    auth_client.Reset();
    ClearAuthPlacement();
    accept_placement = true;
    authenticated_room = 0; authenticated_owner = 0; auth_context_ms = 0; clear_calls = 0;
}
static void unit_tests() {
    {
        namespace V=TheHuntVisibility;
        Client upgraded;
        Bytes challenge{'T','H','C','2'};challenge.resize(36,0x6e);
        Bytes proof;
        assert(upgraded.Begin(17,token,challenge,proof,true) && Tag(proof,"THA4"));
        constexpr char domain[]="TheHunt relay key v2";
        Bytes material(domain,domain+sizeof(domain));material.insert(material.end(),challenge.begin()+4,challenge.end());
        Put(material,17,8);assert(Hmac(token,material,key));
        Bytes ack{'T','H','O','4'};Put(ack,17,8);assert(upgraded.Ack(signed_frame(ack)));
        Bytes context{'T','H','X','4'};Put(context,1,8);Put(context,9,8);Put(context,17,4);Put(context,18,4);
        context.push_back(1);context.push_back(2);
        for(u32 id:{17u,18u}) {
            Put(context,id,4);Put(context,id,2);Put(context,123,8);context.push_back(1);
            context.push_back(static_cast<u8>('a'+id-17));context.resize(context.size()+15,0);
        }
        Delivery delivery;
        assert(upgraded.Receive(signed_frame(context),delivery)==Received::Context);
        assert(upgraded.VisibilityContext().peers.size()==2);
        const auto query=V::Encode({V::Query,false,17,0x18000000,1,2});
        const auto status=V::Encode({V::Status,true,18,0x18000000,1,2,3,4,5});
        Bytes wire;
        assert(upgraded.Send(status,wire) && !upgraded.Send(query,wire));
        assert(upgraded.Receive(data_frame(2,10,18,18,query),delivery)==Received::Invalid);
        assert(upgraded.Receive(data_frame(2,9,18,18,query),delivery)==Received::Data);
        assert(upgraded.Receive(data_frame(2,9,18,18,query),delivery)==Received::Invalid);
        Client legacy;establish(legacy);
        assert(legacy.Receive(context_frame(1,9,18),delivery)==Received::Context);
        assert(!legacy.Send(status,wire));
        assert(legacy.Receive(data_frame(2,9,18,18,query),delivery)==Received::Invalid);
        std::cout<<"PASS signed visibility roster, guest status, host query, wrong room, replay and v2 exclusion\n";
    }
    // RFC 4231 test case 1, BCrypt primitive used by production.
    Bytes kat_key(20,0x0b), kat_message{'H','i',' ','T','h','e','r','e'};
    Digest digest{}; assert(Hmac(kat_key,kat_message,digest));
    const u8 expected[32]={0xb0,0x34,0x4c,0x61,0xd8,0xdb,0x38,0x53,0x5c,0xa8,0xaf,0xce,0xaf,0x0b,0xf1,0x2b,
        0x88,0x1d,0xc2,0x00,0xc9,0x83,0x3d,0xa7,0x26,0xe9,0x37,0x6c,0x2e,0x32,0xcf,0xf7};
    assert(Equal(digest,expected));
    summoned_reload_after_native_membership();
    first_spawn_grants();
    Client client; establish(client); Delivery delivery; Bytes output;
    Client prejoin; establish(prejoin);
    const std::string placement="1,18000000,100,200,300,0,1000";
    assert(prejoin.Receive(placement_frame(1,1,19,1000,placement),delivery)==Received::Invalid);
    assert(prejoin.Receive(placement_frame(1,1,17,20001,placement),delivery)==Received::Invalid);
    assert(prejoin.Receive(placement_frame(1,1,17,1000,std::string("\xff")),delivery)==Received::Invalid);
    assert(prejoin.Receive(placement_frame(1,1,17,1000,placement),delivery)==Received::Placement);
    assert(prejoin.Current().room==0 && delivery.placement.host==18 && delivery.placement.target==17);
    assert(!prejoin.Send(life(17),output)); // Pending placement never admits world/life data.
    assert(prejoin.Receive(placement_frame(1,1,17,1000,placement),delivery)==Received::Invalid);
    assert(prejoin.Receive(placement_frame(2,2,17,0,""),delivery)==Received::Placement);
    assert(prejoin.Receive(placement_frame(3,3,17,1000,placement),delivery)==Received::Placement);
    assert(!delivery.placement.ignored); ApplyAuthPlacement(delivery.placement);
    const auto expires = pending_placement_expires;
    assert(prejoin.Receive(placement_frame(4,1,17,0,""),delivery)==Received::Placement);
    assert(delivery.placement.ignored); ApplyAuthPlacement(delivery.placement);
    assert(pending_placement_generation==3 && pending_placement_expires==expires);
    assert(prejoin.Receive(placement_frame(5,1,17,1000,placement),delivery)==Received::Placement);
    assert(delivery.placement.ignored);
    assert(prejoin.Receive(placement_frame(6,3,17,0,""),delivery)==Received::Placement);
    assert(!delivery.placement.ignored);ApplyAuthPlacement(delivery.placement);assert(!pending_placement_expires);
    assert(prejoin.Receive(context_frame(7,9,18),delivery)==Received::Context);
    assert(prejoin.Receive(placement_frame(8,4,17,1000,placement),delivery)==Received::Invalid);
    assert(!client.Send(life(17),output)); // Ack alone is not room admission.
    auto context=context_frame(1,9,18);
    auto bad=context; bad[28]^=1;
    assert(client.Receive(bad,delivery)==Received::Invalid);
    assert(client.Receive(context,delivery)==Received::Context);
    assert(client.Receive(context,delivery)==Received::Invalid); // Replay.
    assert(client.Receive(data_frame(2,10,18,18,life(18)),delivery)==Received::Invalid); // Other room.
    assert(client.Receive(data_frame(2,9,18,19,life(18)),delivery)==Received::Invalid); // Owner spoof.
    assert(client.Receive(data_frame(2,9,18,18,life(19)),delivery)==Received::Invalid); // Life identity spoof.
    Bytes boss{'S','H','A','D',0x27}; Put(boss,100,4);
    assert(client.Receive(data_frame(2,9,19,18,boss),delivery)==Received::Invalid); // Guest cannot host.
    assert(client.Receive(data_frame(2,9,18,18,boss),delivery)==Received::Data);
    assert(client.Send(life(17),output)); assert(Tag(output,"THD2") && Read(output,4,8)==1);
    assert(!client.Send(boss,output)); // Local guest cannot assert hosting.
    Bytes pvp{'S','H','A','D',0x25,1,1,0,0,0,0};
    assert(client.Send(pvp,output));
    assert(client.Receive(context_frame(3,9,17),delivery)==Received::Context); // Owner migration.
    assert(client.Send(boss,output)); assert(!client.Send(pvp,output));
    assert(client.Receive(data_frame(4,9,19,17,pvp),delivery)==Received::Data);
    assert(client.Receive(context_frame(5,0,0,0),delivery)==Received::Context);
    assert(!client.Send(life(17),output));
    assert(client.Receive(context_frame(6,9,18),delivery)==Received::Context);
    client.Leave(); assert(client.Receive(context_frame(7,9,18),delivery)==Received::Context);
    assert(client.Current().room==0); // Stale same-room ticks cannot undo local leave.
    client.Reset(); establish(client);
    assert(client.Receive(context_frame(1,9,18),delivery)==Received::Context);
    assert(client.Current().room==9); // Fresh nonce + signed current context permits rejoin.
    assert(client.Receive(context_frame(2,0,0,0),delivery)==Received::Context);
    assert(client.Receive(context_frame(3,9,18),delivery)==Received::Context);
    assert(client.Current().room==9);
    auto malformed=data_frame(4,9,18,18,Bytes{'S','H','A','D',0x40});
    assert(client.Receive(malformed,delivery)==Received::Invalid);
    auto oversized=Bytes(MaxFrame+1); assert(client.Receive(oversized,delivery)==Received::Invalid);
    client.Reset(); assert(!client.Ready() && !client.Send(life(17),output));

    own_id=17; private_auth=true; authenticated_owner=18; authenticated_room=9;
    handler_calls=0; p2p_calls=0;
    Bytes legacy{'S','H','A','D',0x40}; Put(legacy,18,4);Put(legacy,18,4);
    auto inner=life(18);legacy.insert(legacy.end(),inner.begin(),inner.end());
    BbscMod::RelayDispatch(legacy); assert(handler_calls==0);
    assert(SendFrame(inner.data(),inner.size())==0 && p2p_calls==0);
    delivery={Context{9,17,18,1},18,inner}; AuthDeliver(delivery);
    assert(handler_calls==1 && last_player==18);
    delivery.inner=Bytes{'S','H','A','D',0x25,0,1,0,0,0,0}; AuthDeliver(delivery);
    assert(handler_calls==2 && last_sender==18);
    BbscMod::RelayAuthInvalidate();
    assert(!authenticated_owner && !authenticated_room && !auth_client.Ready() && clear_calls==1);
    assert(!PublicAuthHost("evilshadps4.net")); assert(PublicAuthHost("np.supermedo.com"));
    assert(BearerTarget("http://127.0.0.1:31415/summon_messenger/delete","http://127.0.0.1:31415"));
    for(const auto* url:{"http://127.0.0.1:20443/summon_messenger/create",
        "http://127.0.0.1:31415/summon_messenger/create/extra","http://user@127.0.0.1:31415/summon_messenger/get",
        "http://127.0.0.1:31415/summon_messenger/get?q=1","http://127.0.0.1.evil.test:31415/summon_messenger/get"})
        assert(!BearerTarget(url,"http://127.0.0.1:31415"));
    pending_placement_expires=GetTickCount64();pending_placement_generation=1;
    authenticated_room=9;authenticated_owner=18;
    ExpireAuthPlacement();assert(pending_placement_expires==0 && authenticated_room==9 && authenticated_owner==18);
    std::cout << "PASS native protocol, authenticated adapter, private no-fallback\n";
}

// Driver for an independent Python HMAC peer or a real native-server NP-room test.
// Credentials arrive only through this process's env, never argv/stdout.
static int wire_driver(const char* host, const char* port) {
    WSADATA wsa{}; if (WSAStartup(MAKEWORD(2,2),&wsa)) return 10;
    InitAuth();
    SOCKET s=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(std::atoi(port));
    if (inet_pton(AF_INET,host,&address.sin_addr)!=1 || connect(s,reinterpret_cast<sockaddr*>(&address),sizeof(address))) return 11;
    DWORD timeout=300;setsockopt(s,SOL_SOCKET,SO_SNDTIMEO,reinterpret_cast<const char*>(&timeout),sizeof(timeout));
    Bytes frame,proof;const auto deadline=GetTickCount64()+5000;
    if (!AuthFrame(s,frame,deadline) || !auth_client.Begin(own_id,auth_token,frame,proof,true) ||
        !AuthWrite(s,proof) || !AuthFrame(s,frame,deadline) || !auth_client.Ack(frame)) return 12;
    std::cout << "ACK\n" << std::flush;
    std::string command;
    while (std::getline(std::cin,command)) {
        if(command=="quit") break;
        if(command=="read") {
            if(!AuthFrame(s,frame,GetTickCount64()+5000)) { std::cout<<"CLOSED\n"<<std::flush; break; }
            Delivery delivery;const auto result=auth_client.Receive(frame,delivery);
            if(result==Received::Invalid) { std::cout<<"REJECT\n"<<std::flush;break; }
            if(result==Received::Context) {
                const auto c=delivery.context;
                ApplyAuthContext(delivery);
                std::cout<<"CTX "<<c.room<<" "<<c.own<<" "<<c.owner<<" "<<int(c.peers)<<"\n"<<std::flush;
            } else if(result==Received::Placement) {
                ApplyAuthPlacement(delivery.placement);
                std::cout<<"PLACEMENT "<<delivery.placement.host<<" "<<delivery.placement.target<<" "
                         <<delivery.placement.ttl_ms<<"\n"<<std::flush;
            } else {
                AuthDeliver(delivery);
                std::cout<<"DATA "<<delivery.sender<<" "<<int(delivery.inner[4])<<" "<<handler_calls<<"\n"<<std::flush;
            }
        } else if(command=="life" || command=="boss") {
            Bytes inner=life(own_id);
            if(command=="boss") { inner={'S','H','A','D',0x27};Put(inner,100,4); }
            if(!auth_client.Send(inner,frame)) std::cout<<"BLOCKED\n"<<std::flush;
            else std::cout<<(AuthWrite(s,frame)?"SENT\n":"CLOSED\n")<<std::flush;
        }
    }
    closesocket(s);return 0;
}
static void visibility_retry_tests() {
    Client client;establish(client);Bytes challenge{'T','H','C','2'};challenge.resize(36,0x6e);Bytes proof;
    assert(client.Begin(17,token,challenge,proof,true) && Tag(proof,"THA4"));
    Bytes old{'T','H','O','3'};Put(old,17,8);assert(!client.Ack(signed_frame(old)));
    assert(!client.Ready() && !client.Visibility());
    assert(client.Begin(17,token,challenge,proof,true) && Tag(proof,"THA4"));
    Bytes ack{'T','H','O','4'};Put(ack,17,8);assert(client.Ack(signed_frame(ack)) && client.Visibility());
    std::cout << "PASS THA4 mandatory capability; old acknowledgement rejected, reconnect remains THA4\n";
}
int main(int argc,char** argv) {
    if(argc==4 && std::string(argv[1])=="--wire") return wire_driver(argv[2],argv[3]);
    visibility_retry_tests();unit_tests();return 0;
}
