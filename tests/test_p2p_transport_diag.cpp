// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "common/logging/log.h"
#include "core/net/p2p_codec.h"
#if !defined(P2P_TEST_BASELINE) && __has_include("core/net/p2p_diagnostics.h")
#include "core/net/p2p_diagnostics.h"
#define HAS_P2P_DIAG 1
#else
#define HAS_P2P_DIAG 0
#endif
using namespace Core::Net;
using namespace Core::Net::P2P;
static std::mutex output_mutex;
static std::vector<std::string> output;
namespace Common::Log {
bool ShouldLog(Level) {return true;}
void Write(std::string_view,Level,const char*,int,const char*,std::string&& message) {
    std::scoped_lock lock(output_mutex);output.push_back(std::move(message));
}
}
static bool Has(const std::string& text) {
    std::scoped_lock lock(output_mutex);
    for(const auto& line:output) if(line.find(text)!=std::string::npos)return true;
    return false;
}
static void Sink(const std::string& text) {
    std::scoped_lock lock(output_mutex);output.push_back(text);
}
static void Pass(const char* text) {std::cout<<"PASS P2P: "<<text<<'\n';}
static std::shared_ptr<Transport> Open(size_t queue=256) {
    Host::Error error;
    TransportConfig config;config.signaling_queue_packets=queue;
    auto t=Transport::Create(AF_INET,Endpoint::IPv4("127.0.0.1",0),
                            std::make_unique<FramingCodec>(std::make_shared<Keyring>()),config,&error);
    assert(t && error==Host::Error::Ok);return t;
}
static void LiveProbe() {
    _putenv_s("THEHUNT_P2P_DIAG","1");
    auto receiver=Open();
    auto sender=Open();
    const auto to=Endpoint::IPv4("127.0.0.1",receiver->BoundPort());
    const std::vector<u8> payload{'S','H','A','D',1,2,3};
    assert(sender->SendSignaling(payload,to)==Host::Error::Ok);
    std::vector<u8> received;Endpoint from;
    bool got=false;
    for(int i=0;i<100 && !(got=receiver->RecvSignaling(0,&received,&from));++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    assert(got && received==payload && from.Port()==sender->BoundPort());
    // A silent UDP socket still emits the scheduled five-second summary.
    std::this_thread::sleep_for(std::chrono::milliseconds(5200));
    assert(Has("P2P DIAG: enabled"));
    assert(Has("tx_signal=1") && Has("rx_signal=1"));
    sender.reset();receiver.reset();
    Pass("actual UDP signaling arrives unchanged; idle sockets emit five-second per-peer send/receive summaries");
}

static void Corpus() {
    auto peer=Endpoint::IPv4("127.0.0.1",3658);
    P2PKey key{};for(unsigned i=0;i<16;++i)key.value[i]=u8(i+1);
    std::vector<std::vector<u8>> packets;
    for(unsigned flags=0;flags<256;++flags) for(unsigned len:{0u,1u,2u,3u,5u,6u,10u,24u}) {
        std::vector<u8> p(len,0);
        if(len) p[0]=0xff;if(len>1)p[1]=u8(flags);
        if(len>=6){p[2]=0;p[3]=40;p[4]=0;p[5]=40;}
        packets.push_back(p);
    }
    const std::vector<u8> payload{1,2,3,4,5};
    auto txkeys=std::make_shared<Keyring>();txkeys->SetPeerKey(peer,key);FramingCodec tx(txkeys);
    packets.push_back(tx.EncodeDatagram(40,40,payload,peer,{false,true}));
    packets.push_back(tx.EncodeDatagram(40,40,payload,peer,{}));
    packets.push_back(tx.EncodeSignaling(payload,peer));
    auto bad=packets[packets.size()-3];bad.back()^=1;packets.push_back(bad);
    for(unsigned profile=0;profile<4;++profile) {
        auto keys=std::make_shared<Keyring>();
        if(profile&1)keys->SetPeerKey(peer,key);
        if(profile&2)keys->SetCommunicationId(CommunicationId{1,2,3,4});
        FramingCodec rx(keys);
        for(size_t i=0;i<packets.size();++i) {
            const auto decoded=rx.Decode(packets[i],peer);
            std::cout<<"CORPUS "<<profile<<' '<<i<<' '<<int(decoded.kind)<<' '
                     <<decoded.src_vport<<' '<<decoded.dst_vport<<' ';
            for(auto b:decoded.payload)std::cout<<std::hex<<unsigned(b)<<',';
            std::cout<<std::dec<<'\n';
        }
    }
}
static void Relay() {
    _putenv_s("THEHUNT_P2P_DIAG","1");
    auto client=Open(1);
    Host::Error error;auto relay=Host::CreateSocket(AF_INET,SOCK_DGRAM,0,&error);
    assert(relay!=Host::InvalidSocket);
    auto bind=Endpoint::IPv4("127.0.0.1",0);
    assert(Host::Bind(relay,bind.Sockaddr(),bind.Length())==Host::Error::Ok);
    sockaddr_in actual{};socklen_t length=sizeof(actual);
    assert(getsockname(relay,reinterpret_cast<sockaddr*>(&actual),&length)==0);
    auto server=Endpoint::FromSockaddr(reinterpret_cast<sockaddr*>(&actual),length);
    auto logical=Endpoint::IPv4("127.0.0.2",3658);
    client->SetRelayServer(server);
    client->UseRelayFor(reinterpret_cast<const sockaddr_in*>(&logical.addr)->sin_addr.s_addr);
    std::vector<u8> payload{'S','H','A','D',1,2,3};
    assert(client->SendSignaling(payload,logical)==Host::Error::Ok);
    std::array<u8,256> packet{};sockaddr_storage from{};socklen_t from_len=sizeof(from);
    Host::IoResult got{};
    for(int i=0;i<100;++i) {
        got=Host::RecvFrom(relay,packet.data(),packet.size(),0,reinterpret_cast<sockaddr*>(&from),&from_len);
        if(got.error==Host::Error::Ok)break;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    assert(got.error==Host::Error::Ok && got.value==24);
    assert(std::memcmp(packet.data(),"SMRL",4)==0 && packet[4]==2);
    packet[4]=3; // Same bytes, now delivered by the configured relay.
    for(int i=0;i<3;++i)
        assert(Host::SendTo(relay,packet.data(),size_t(got.value),0,
                           reinterpret_cast<sockaddr*>(&from),from_len).error==Host::Error::Ok);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    std::vector<u8> received;Endpoint peer;
    assert(client->RecvSignaling(0,&received,&peer) && received==payload && peer==logical);
    assert(!client->RecvSignaling(0,&received,&peer)); // Original queue limit remains one.
    client.reset();Host::CloseSocket(relay);
#if HAS_P2P_DIAG
    assert(Has("tx_direct=0 tx_relay=1") && Has("rx_relay=3"));
    assert(Has("queue_drop=2"));
#endif
    Pass("actual SMRL relay wraps/unpacks unchanged, attributes logical peer and retains queue overflow behavior");
}
static void SendError() {
    _putenv_s("THEHUNT_P2P_DIAG","1");auto client=Open();
    const std::vector<u8> payload{1,2,3};
    sockaddr_in6 v6{};v6.sin6_family=AF_INET6;v6.sin6_port=htons(3658);
    assert(inet_pton(AF_INET6,"::1",&v6.sin6_addr)==1);
    const auto wrongFamily=Endpoint::FromSockaddr(reinterpret_cast<sockaddr*>(&v6),sizeof(v6));
    assert(client->SendSignaling(payload,wrongFamily)!=Host::Error::Ok);
    client.reset();
#if HAS_P2P_DIAG
    assert(Has("tx_error=1") && !Has("last_native_error=0"));
#endif
    Pass("real sendto error remains an error and diagnostic records the native socket error");
}

int main(int argc,char** argv) {
    assert(argc==2);const std::string mode=argv[1];
    assert(Host::Initialize());
    if(mode=="probe") {LiveProbe();return 0;}
    if(mode=="corpus") {Corpus();return 0;}
    if(mode=="relay") {Relay();return 0;}
    if(mode=="send-error") {SendError();return 0;}
#if HAS_P2P_DIAG
    if(mode=="unit") {
        const auto zero=Clock::time_point{};
        Diagnostics table(3658,&Sink,zero);
        auto peer=Endpoint::IPv4("127.0.0.1",10001);
        const std::vector<u8> wire{0xff,0x83,0xff,0xff,0xff,0xff,1,2,3};
        Codec::Decoded accepted;accepted.kind=Codec::Kind::Signaling;
        Codec::Decoded rejected;rejected.failure=DecodeFailure::NoKey;
        SetLastError(4321);errno=71;
        table.Submit(peer);table.Sent(peer,wire,false,Host::Error::Ok,0);
        table.Sent(peer,wire,true,Host::Error::HostUnreach,10065);
        table.Received(peer,wire,false,accepted);table.Received(peer,wire,true,rejected);
        assert(GetLastError()==4321 && errno==71);
        table.Flush(zero+std::chrono::milliseconds(4999));assert(output.empty());
        table.Submit(peer); // Does not postpone the deadline.
        assert(table.Deadline()==zero+std::chrono::seconds(5));
        table.Flush(zero+std::chrono::seconds(5));
        assert(Has("tx=2 tx_signal=2 tx_ok=1") && Has("tx_direct=1 tx_relay=1"));
        assert(Has("rx=2 rx_signal=2 rx_direct=1 rx_relay=1") && Has("drop_no_key=1"));
        assert(Has("last_error=65 last_native_error=10065"));
        size_t rows;{std::scoped_lock lock(output_mutex);rows=output.size();}
        table.Flush(zero+std::chrono::milliseconds(9999));
        {std::scoped_lock lock(output_mutex);assert(output.size()==rows);}
        for(unsigned i=0;i<200;++i)table.Submit(Endpoint::IPv4("127.0.0.1",u16(11000+i)));
        assert(table.Peers().size()==Diagnostics::PeerLimit && table.Overflow()>0);
        Pass("five-second rate bound, fixed deadline, direct/relay split, native errors, bounded peers and error state preservation");
        auto keys=std::make_shared<Keyring>();FramingCodec codec(keys);
        P2PKey key{};for(unsigned i=0;i<key.value.size();++i)key.value[i]=u8(i+1);
        auto txkeys=std::make_shared<Keyring>();txkeys->SetPeerKey(peer,key);
        FramingCodec tx(txkeys);
        const std::vector<u8> payload{1,2,3,4,5};
        auto signed_wire=tx.EncodeDatagram(40,40,payload,peer,{false,true});
        assert(codec.Decode(signed_wire,peer).failure==DecodeFailure::NoKey);
        keys->SetPeerKey(peer,key);
        assert(codec.Decode(signed_wire,peer).payload==payload);
        signed_wire.back()^=1;
        auto bad=codec.Decode(signed_wire,peer);
        assert(bad.kind==Codec::Kind::Invalid && bad.failure==DecodeFailure::BadSignature);
        keys->SetCommunicationId(CommunicationId{1,2,3,4});
        auto wrong=tx.EncodeDatagram(40,40,payload,peer,{});
        assert(codec.Decode(wrong,peer).failure==DecodeFailure::CommunicationId);
        auto signaling=tx.EncodeSignaling(payload,peer);
        auto ok=codec.Decode(signaling,peer);
        assert(ok.kind==Codec::Kind::Signaling && ok.payload==payload && ok.failure==DecodeFailure::None);
        assert(codec.Decode(std::vector<u8>{0xff},peer).failure==DecodeFailure::Malformed);
        Pass("real codec distinguishes missing-key/bad-signature/communication-id/malformed while plain signaling retains its original bypass");
        _putenv_s("THEHUNT_P2P_DIAG","");
        assert(!DiagnosticEnabled());
        std::array<wchar_t,32768> exe_path{};
        assert(GetModuleFileNameW(nullptr,exe_path.data(),DWORD(exe_path.size())));
        const auto flag=std::filesystem::path(exe_path.data()).parent_path().parent_path()/L"mod/p2p-diag.enable";
        assert(!std::filesystem::exists(flag));
        std::filesystem::create_directories(flag.parent_path());
        FILE* file=_wfopen(flag.c_str(),L"wb");assert(file);fclose(file);
        assert(Diagnostics::Create(3658,&Sink));
        assert(std::filesystem::remove(flag));
        assert(!DiagnosticEnabled());
        _putenv_s("THEHUNT_P2P_DIAG","0");
        assert(!Diagnostics::Create(3658,&Sink));
        _putenv_s("THEHUNT_P2P_DIAG","1");
        assert(Diagnostics::Create(3658,&Sink));
        Pass("absent file disables, empty file enables, removal disables again, test overrides remain explicit");
        return 0;
    }
#endif
    assert(false && "unknown/unavailable test mode");
}
