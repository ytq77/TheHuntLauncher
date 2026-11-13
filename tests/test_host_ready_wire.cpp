// Native wire regression; no game memory, sockets, or credentials.
#include "../patches/bbsc_visibility_wire.h"
#include <cassert>
#include <iostream>
using namespace TheHuntVisibility;
int main() {
    Peer h{1,17,101,{},true},g{2,34,202,{},true};h.npid[0]='h';g.npid[0]='g';
    Context c{999,2,1,{h,g}};
    Frame q{HostQuery,false,1,0x18000000,11,22,0,0,0,999,202,101,1,2};
    Frame s{HostStatus,true,2,0x18000000,33,22,11,44,55,999,101,202,3,2};
    for(auto f:{q,s}) {
        const auto raw=Encode(f);assert(raw.size()==96 && raw[5]==2);Frame decoded;
        assert(Decode(raw,decoded) && decoded.room==999 && decoded.observer==2);
        const auto sender=f.kind==HostQuery?2:1;
        assert(Allowed(c,decoded,sender,f.target));
        assert(!Allowed(c,decoded,f.target,sender));
        auto old=c;old.room++;assert(!Allowed(old,decoded,sender,f.target));
        old=c;old.peers[0].joined++;assert(!Allowed(old,decoded,sender,f.target));
        old=c;old.peers[1].joined++;assert(!Allowed(old,decoded,sender,f.target));
        old=c;old.peers[0].capable=false;assert(!Allowed(old,decoded,sender,f.target));
        for(std::size_t n=0;n<raw.size();++n) assert(!Decode({raw.data(),n},decoded));
        auto bad=raw;bad.push_back(0);assert(!Decode(bad,decoded));
        for(int at:{5,6,7,56,64,72,80,88}) {
            bad=raw;
            if(at>=56) for(int i=0;i<8;++i)bad[at+i]=0;
            else bad[at]=at==7?2:0;
            assert(!Decode(bad,decoded));
        }
    }
    auto bad=q;bad.ready=true;assert(Encode(bad).empty());
    bad=q;bad.boot=1;assert(Encode(bad).empty());
    bad=s;bad.ready=false;assert(Encode(bad).size()==96); // unready is an explicit valid status, never an ACK
    bad=s;bad.ticket=0;assert(Encode(bad).empty());
    Frame old{Query,false,2,0x18000000,1,2};assert(Encode(old).size()==56);
    auto raw=Encode(old);raw[5]=2;Frame out;assert(!Decode(raw,out));
    std::cout<<"PASS reverse visibility wire: exact layout, direction, version, truncation, room, joined generations, capability, ready/unready semantics\n";
}
