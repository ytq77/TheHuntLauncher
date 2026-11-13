// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <sstream>
#include <string>
#include <string_view>
#include "core/net/p2p_transport.h"
#ifdef _WIN32
#include <windows.h>
#endif

namespace Core::Net::P2P {
struct DiagnosticErrors {
    int number=errno;
#ifdef _WIN32
    DWORD win=GetLastError();
    int socket=WSAGetLastError();
#endif
    ~DiagnosticErrors() {
#ifdef _WIN32
        WSASetLastError(socket);SetLastError(win);
#endif
        errno=number;
    }
};
inline int DiagnosticNativeError() {
#ifdef _WIN32
    return WSAGetLastError();
#else
    return errno;
#endif
}
inline bool DiagnosticEnabled() noexcept {
    DiagnosticErrors preserved;
    try {
        if(const char* env=std::getenv("THEHUNT_P2P_DIAG")) return std::string_view(env)=="1";
#ifdef _WIN32
        std::array<wchar_t,32768> path{};
        const auto n=GetModuleFileNameW(nullptr,path.data(),DWORD(path.size()));
        if(!n || n>=path.size()) return false;
        const auto flag=std::filesystem::path(path.data()).parent_path().parent_path()/L"mod/p2p-diag.enable";
        const auto attrs=GetFileAttributesW(flag.c_str());
        return attrs!=INVALID_FILE_ATTRIBUTES && !(attrs&FILE_ATTRIBUTE_DIRECTORY);
#endif
    } catch(...) {}
    return false;
}
inline bool DiagnosticSignaling(std::span<const u8> packet) {
    return packet.size()>=6 && packet[0]==0xff && (packet[1]&0x80) &&
        !(packet[1]&0x40) && (packet[1]&15)>=3 && (packet[1]&15)<=6 &&
        packet[4]==0xff && packet[5]==0xff;
}

// Every production call holds Transport::mutex_; no additional lock or worker.
class Diagnostics {
public:
    static constexpr size_t PeerLimit=128;
    static constexpr auto Interval=std::chrono::seconds(5);
    using Sink=void (*)(const std::string&);
    struct Counters {
        u64 submits=0,tx=0,tx_signal=0,tx_ok=0,tx_error=0,tx_direct=0,tx_relay=0;
        u64 rx=0,rx_signal=0,rx_direct=0,rx_relay=0,queue_drop=0,test_drop=0;
        std::array<u64,size_t(DecodeFailure::Count)> dropped{};
        int last_error=0,last_native_error=0;
    };
    Diagnostics(u16 port,Sink sink,Clock::time_point now) : port_(port),sink_(sink),due_(now+Interval) {}
    static std::unique_ptr<Diagnostics> Create(u16 port,Sink sink) noexcept {
        DiagnosticErrors preserved;
        try {
            if(!DiagnosticEnabled()) return {};
            auto result=std::make_unique<Diagnostics>(port,sink,Clock::now());
            sink("P2P DIAG: enabled interval_ms=5000 peer_limit=128 local_port="+std::to_string(port));
            return result;
        } catch(...) {return {};}
    }
    void Submit(const Endpoint& peer) noexcept {
        DiagnosticErrors preserved;try {if(auto* c=Peer(peer)) ++c->submits;}catch(...) {}
    }
    void Sent(const Endpoint& peer,std::span<const u8> wire,bool relay,Host::Error error,int native) noexcept {
        DiagnosticErrors preserved;
        try {if(auto* c=Peer(peer)) {
            ++c->tx;c->tx_signal+=DiagnosticSignaling(wire);
            relay ? ++c->tx_relay : ++c->tx_direct;
            if(error==Host::Error::Ok) ++c->tx_ok;
            else {++c->tx_error;c->last_error=int(error);c->last_native_error=native;}
        }}catch(...) {}
    }
    void Received(const Endpoint& peer,std::span<const u8> wire,bool relay,const Codec::Decoded& decoded) noexcept {
        DiagnosticErrors preserved;
        try {if(auto* c=Peer(peer)) {
            ++c->rx;c->rx_signal+=DiagnosticSignaling(wire);
            relay ? ++c->rx_relay : ++c->rx_direct;
            if(decoded.kind==Codec::Kind::Invalid) ++c->dropped[size_t(decoded.failure)];
        }}catch(...) {}
    }
    void QueueDrop(const Endpoint& peer) noexcept {
        DiagnosticErrors preserved;try {if(auto* c=Peer(peer)) ++c->queue_drop;}catch(...) {}
    }
    void TestDrop(const Endpoint& peer) noexcept {
        DiagnosticErrors preserved;try {if(auto* c=Peer(peer)) ++c->test_drop;}catch(...) {}
    }
    Clock::time_point Deadline() const noexcept {return due_;}
    const std::map<Endpoint,Counters>& Peers() const {return peers_;}
    u64 Overflow() const {return overflow_;}
    void Flush(Clock::time_point now,bool final=false) noexcept {
        DiagnosticErrors preserved;
        try {
            if(!final && now<due_) return;
            due_=now+Interval; // Later traffic never postpones the scheduled summary.
            const auto t=std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
            const auto utc=std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            if(peers_.empty()) sink_("P2P DIAG: t_ms="+std::to_string(t)+" utc_ms="+std::to_string(utc)+
                " local_port="+std::to_string(port_)+" peer=none submits=0 tx=0 rx=0 overflow_events="+std::to_string(overflow_));
            for(const auto& [peer,c]:peers_) {
                std::array<char,INET6_ADDRSTRLEN> address{};
                const void* bytes=peer.Family()==AF_INET ?
                    static_cast<const void*>(&reinterpret_cast<const sockaddr_in*>(&peer.addr)->sin_addr) :
                    static_cast<const void*>(&reinterpret_cast<const sockaddr_in6*>(&peer.addr)->sin6_addr);
                const char* ip=inet_ntop(peer.Family(),bytes,address.data(),address.size());
                std::ostringstream row;
                row<<"P2P DIAG: t_ms="<<t<<" utc_ms="<<utc<<" local_port="<<port_
                   <<" peer=["<<(ip?ip:"?")<<"]:"<<peer.Port()<<" final="<<final
                   <<" submits="<<c.submits<<" tx="<<c.tx<<" tx_signal="<<c.tx_signal<<" tx_ok="<<c.tx_ok
                   <<" tx_error="<<c.tx_error<<" last_error="<<c.last_error<<" last_native_error="<<c.last_native_error
                   <<" tx_direct="<<c.tx_direct<<" tx_relay="<<c.tx_relay
                   <<" rx="<<c.rx<<" rx_signal="<<c.rx_signal<<" rx_direct="<<c.rx_direct<<" rx_relay="<<c.rx_relay
                   <<" drop_no_key="<<c.dropped[size_t(DecodeFailure::NoKey)]
                   <<" drop_bad_signature="<<c.dropped[size_t(DecodeFailure::BadSignature)]
                   <<" drop_communication_id="<<c.dropped[size_t(DecodeFailure::CommunicationId)]
                   <<" drop_malformed="<<c.dropped[size_t(DecodeFailure::Malformed)]
                   <<" drop_cipher="<<c.dropped[size_t(DecodeFailure::Cipher)]
                   <<" queue_drop="<<c.queue_drop<<" test_drop="<<c.test_drop<<" overflow_events="<<overflow_;
                sink_(row.str());
            }
        }catch(...) {}
    }
private:
    Counters* Peer(const Endpoint& peer) {
        if(auto it=peers_.find(peer);it!=peers_.end()) return &it->second;
        if(peers_.size()>=PeerLimit) {++overflow_;return nullptr;}
        return &peers_.try_emplace(peer).first->second;
    }
    u16 port_;
    Sink sink_;
    Clock::time_point due_;
    std::map<Endpoint,Counters> peers_;
    u64 overflow_=0;
};
}
