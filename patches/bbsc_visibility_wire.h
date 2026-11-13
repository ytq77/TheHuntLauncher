// Visibility readiness is separate from life/PvP. All identities in a roster
// come from the authenticated server's native matching membership.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <span>
#include <vector>

namespace TheHuntVisibility {
using Bytes = std::vector<std::uint8_t>;
struct Peer {
    std::uint32_t user{};
    std::uint16_t member{};
    std::uint64_t joined{};
    std::array<std::uint8_t,16> npid{};
    bool capable{};
    bool operator==(const Peer&) const = default;
};
struct Context {
    std::uint64_t room{};
    std::uint32_t own{}, owner{};
    std::vector<Peer> peers;
    bool operator==(const Context&) const = default;
};
enum Kind : std::uint8_t { Query, Status, Ack, Unavailable, HostQuery, HostStatus };
struct Frame {
    Kind kind{};
    bool ready{};
    std::uint32_t target{}, map{};
    std::uint64_t host{}, challenge{}, boot{}, ticket{}, reload{};
    std::uint64_t room{}, sender_joined{}, target_joined{}, role{}, observer{};
};
inline std::uint64_t Read(std::span<const std::uint8_t> data, std::size_t at, int count) {
    std::uint64_t out=0;
    for(int i=0;i<count;++i) out|=std::uint64_t(data[at+i])<<(8*i);
    return out;
}
inline void Put(Bytes& out,std::uint64_t n,int count) {
    for(int i=0;i<count;++i) out.push_back(static_cast<std::uint8_t>(n>>(8*i)));
}
inline bool Decode(std::span<const std::uint8_t> raw,Frame& f) {
    if(raw.size()<56 || std::memcmp(raw.data(),"SHAD",4) || raw[4]!=0x28 || raw[7]>1) return false;
    const bool reverse=raw[6]==HostQuery || raw[6]==HostStatus;
    if(reverse ? (raw.size()!=96 || raw[5]!=2) :
       (raw.size()!=56 || raw[5]!=1 || raw[6]>Unavailable)) return false;
    f={static_cast<Kind>(raw[6]),raw[7]!=0,static_cast<std::uint32_t>(Read(raw,8,4)),
       static_cast<std::uint32_t>(Read(raw,52,4)),Read(raw,12,8),Read(raw,20,8),
       Read(raw,28,8),Read(raw,36,8),Read(raw,44,8)};
    if(reverse) {
        f.room=Read(raw,56,8);f.sender_joined=Read(raw,64,8);f.target_joined=Read(raw,72,8);
        f.role=Read(raw,80,8);f.observer=Read(raw,88,8);
        if(!f.room || !f.sender_joined || !f.target_joined || !f.role || !f.observer) return false;
    }
    if(!f.target) return false;
    if(f.kind==Unavailable) return !f.ready && !f.host && !f.challenge && !f.boot && !f.ticket && !f.reload && !f.map;
    if(!f.host || !f.challenge || !f.map || f.map==0xffffffff) return false;
    if(f.kind==Query || f.kind==HostQuery) return !f.ready && !f.boot && !f.ticket && !f.reload;
    return f.boot && f.ticket && f.reload && (f.kind!=Ack || f.ready);
}
inline Bytes Encode(const Frame& f) {
    Bytes out{'S','H','A','D',0x28,static_cast<std::uint8_t>(f.kind>=HostQuery?2:1),static_cast<std::uint8_t>(f.kind),static_cast<std::uint8_t>(f.ready)};
    Put(out,f.target,4);Put(out,f.host,8);Put(out,f.challenge,8);Put(out,f.boot,8);
    Put(out,f.ticket,8);Put(out,f.reload,8);Put(out,f.map,4);
    if(f.kind>=HostQuery) {
        Put(out,f.room,8);Put(out,f.sender_joined,8);Put(out,f.target_joined,8);
        Put(out,f.role,8);Put(out,f.observer,8);
    }
    Frame check;
    return Decode(out,check)?out:Bytes{};
}
inline bool ValidPeer(const Peer& p) {
    const auto end=std::find(p.npid.begin(),p.npid.end(),0);
    return p.user && p.member && p.joined && end!=p.npid.begin() &&
        std::all_of(p.npid.begin(),end,[](auto c){return c>=0x21 && c<=0x7e;}) &&
        std::all_of(end,p.npid.end(),[](auto c){return c==0;});
}
inline const Peer* Find(const Context& c,std::uint32_t user) {
    const auto it=std::find_if(c.peers.begin(),c.peers.end(),[&](const auto& p){return p.user==user;});
    return it==c.peers.end()?nullptr:&*it;
}
inline bool Allowed(const Context& c,const Frame& f,std::uint32_t sender,std::uint32_t recipient) {
    const auto* a=Find(c,sender); const auto* b=Find(c,recipient);
    if(!c.room || !a || !b || !a->capable || !b->capable || sender==recipient || f.target!=recipient) return false;
    if(f.kind>=HostQuery && (f.room!=c.room || f.sender_joined!=a->joined || f.target_joined!=b->joined)) return false;
    return (f.kind==Status || f.kind==HostQuery) ? sender!=c.owner && recipient==c.owner : sender==c.owner && recipient!=c.owner;
}
} // namespace TheHuntVisibility
