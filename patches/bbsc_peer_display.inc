// Host-side rendering gate. Native insertion, model updates and life reports
// continue. Release stops filtering; it never restores a cached actor pointer.
namespace V = TheHuntVisibility;
struct BbscDisplayPeer {
    V::Peer peer;
    u64 actor{}, member_object{}, model{}, challenge{}, accepted_ticket{}, accepted_boot{};
    u64 floor_ticket{}, floor_boot{}, reload{}, guest_reload{};
    s64 started{}, query_ms{}, ack_ms{};
    bool allowed{}, exposed{}, dead{}, ack_pending{}, expired{};
};
struct BbscDisplayGuest {
    u64 ticket{}, reload{}, role{}, actor{}, host{}, challenge{};
    u32 map{};
    bool ready{}, acknowledged{}, unavailable{};
    s64 sent_ms{};
};
struct BbscDisplayMessage { V::Context context; u32 sender{}; V::Frame frame; };
std::mutex bbsc_display_mutex;
V::Context bbsc_display_context;
std::map<u32,BbscDisplayPeer> bbsc_display_peers;
std::vector<BbscDisplayMessage> bbsc_display_messages;
BbscDisplayGuest bbsc_display_guest;
std::atomic<bool> bbsc_display_hooks{false};
u64 bbsc_display_nonce{}, bbsc_display_counter{};
bool bbsc_display_host{}, bbsc_display_reverse_active{};
struct BbscHostDisplayObserver {
    V::Peer peer;
    u64 actor{}, member{}, challenge{}, observer{}, local_reload{}, local_role{}, local_actor{};
    u64 host_boot{}, host_ticket{}, host_reload{}, host_role{};
    s64 started{}, sent{};
    bool allowed{}, dead{}, expired{};
};
BbscHostDisplayObserver bbsc_display_reverse;
std::map<u32,V::Frame> bbsc_display_host_queries;

void ResetBbscHostObserver() {
    auto& r=bbsc_display_reverse;
    r.allowed=r.expired=false;r.sent=0;r.started=SteadyNowMs();
    r.challenge=++bbsc_display_counter;r.observer=r.challenge;
}

u64 bbsc_display_local{}, bbsc_display_reload{};
u32 bbsc_display_map{};
s64 bbsc_display_unavailable_ms{};

thread_local std::vector<u32> bbsc_display_constructing;

u64 BbscDisplayNonce() {
    return BbscMod::RelayAuthNonce();
}

void ClearBbscPeerDisplay() {
    ClearBbscVisualDiagnostics();
    std::scoped_lock lock{bbsc_display_mutex};
    bbsc_display_context={};bbsc_display_peers.clear();bbsc_display_messages.clear();
    bbsc_display_guest={};bbsc_display_host=false;bbsc_display_reverse_active=false;
    bbsc_display_reverse={};bbsc_display_host_queries.clear();
}

void CacheBbscVisibilityPacket(const V::Context& context,u32 sender,std::span<const u8> bytes) {
    V::Frame frame;
    if(!V::Decode(bytes,frame) || !V::Allowed(context,frame,sender,context.own)) return;
    std::scoped_lock lock{bbsc_display_mutex};
    if(bbsc_display_messages.size()<32) bbsc_display_messages.push_back({context,sender,frame});
}

// 1.09 NP member implementation: outer wrapper locks protect its underlying
// object, whose pinned getter copies the 36-byte SceNpId under its own lock.
bool ReadBbscDisplayNpid(u64 wrapper,std::array<u8,16>& npid,u64* object=nullptr) {
    if(!ReadablePointer(wrapper,0x20)) return false;
    const u64 vtable=ReadValue<u64>(wrapper,8);
    const u64 enter=ReadValue<u64>(vtable,0x18),leave=ReadValue<u64>(vtable,0x28);
    if(!IsEbootCodeAddress(enter) || !IsEbootCodeAddress(leave)) return false;
    using Lock = void PS4_SYSV_ABI (*)(u64,u32);
    using Unlock = void PS4_SYSV_ABI (*)(u64);
    reinterpret_cast<Lock>(enter)(wrapper+8,0xffffffff);
    const u64 member=ReadValue<u64>(wrapper,0);
    bool ok=ReadablePointer(member,0x5a) && ReadValue<u64>(member,0)==image_base+0x052C5C00;
    if(ok) {
        std::array<u8,36> id{};
        using Get = void PS4_SYSV_ABI (*)(u64,void*);
        reinterpret_cast<Get>(image_base+0x00CA25E0)(member,id.data());
        std::copy_n(id.begin(),16,npid.begin());
        V::Peer check{1,1,1,npid,true};
        ok=V::ValidPeer(check);
        if(ok && object) *object=member;
    }
    reinterpret_cast<Unlock>(leave)(wrapper+8);
    return ok;
}

u32 BbscDisplayFindPeer(const std::array<u8,16>& npid) {
    u32 found{};
    for(const auto& p:bbsc_display_context.peers) if(p.user!=bbsc_display_context.own && p.npid==npid) {
        if(found) return 0;
        found=p.user;
    }
    return found;
}

void ResetBbscDisplayGrant(BbscDisplayPeer& p,bool new_guest_load=false) {
    if(new_guest_load && p.accepted_ticket) {p.floor_ticket=p.accepted_ticket;p.floor_boot=p.accepted_boot;}
    p.allowed=p.exposed=p.ack_pending=p.expired=false;p.accepted_ticket=p.accepted_boot=0;
    p.challenge=++bbsc_display_counter;p.query_ms=p.ack_ms=0;
    p.started=SteadyNowMs();
}

// Lifecycle notifications can precede a native death draw or arrive after a
// new readiness grant. Rechallenge without flooring out that current ticket.
// Retain accepted history so a later native death observation can still floor it.
void InvalidateBbscGuestDisplayGrant(BbscDisplayPeer& p,const char* reason) {
    const auto ticket=p.accepted_ticket,boot=p.accepted_boot;
    const bool allowed=p.allowed;
    ResetBbscDisplayGrant(p);
    p.accepted_ticket=ticket;p.accepted_boot=boot;
    LOG_INFO(Debug,"BBSC display: guest visibility invalidated ({}, previous transition {}, permitted {})",
             reason,ticket,allowed);
}

bool BbscDisplayNativeAlive(u64 actor) {
    if(!ReadablePointer(actor,0x60)) return false;
    const auto control=ReadValue<u64>(actor,0x58);
    return ReadablePointer(control,0x116) && GetCharacterHp(actor)>0 &&
           !(ReadValue<u16>(control,0x114)&1);
}

bool BbscDisplayHideActor(u64 actor) {
    if(!actor || !ReadablePointer(actor,0x4d8)) return false;
    {
        std::scoped_lock lock{bbsc_display_mutex};
        if((!bbsc_display_host && !bbsc_display_reverse_active) || actor==bbsc_display_local ||
           GetCharacterType(actor)!=(bbsc_display_host?1:0)) return false;
    }
    std::array<u8,16> npid{};u64 member{};
    if(!ReadBbscDisplayNpid(actor+0x3d8,npid,&member)) return false;
    const auto model_owner=ReadValue<u64>(actor,0x48);
    const auto model=ReadValue<u64>(model_owner,0x18);
    const bool corpse=GetCharacterHp(actor)<=0;
    const bool dead=corpse || (ReadValue<u16>(ReadValue<u64>(actor,0x58),0x114)&1);
    std::scoped_lock lock{bbsc_display_mutex};
    if((!bbsc_display_host && !bbsc_display_reverse_active) || actor==bbsc_display_local ||
       GetCharacterType(actor)!=(bbsc_display_host?1:0)) return false;
    const auto user=BbscDisplayFindPeer(npid);
    if(bbsc_display_reverse_active) {
        const auto* owner=V::Find(bbsc_display_context,bbsc_display_context.owner);
        if(!owner || user!=owner->user || !owner->capable) return false;
        auto& r=bbsc_display_reverse;
        if(r.actor!=actor || r.member!=member || !(r.peer==*owner) || r.dead!=dead) {
            ResetBbscHostObserver();r.actor=actor;r.member=member;r.peer=*owner;
        }
        r.dead=dead;
        const bool hidden=!corpse && !r.expired && (!r.allowed || dead);
        BbscVisualDiagObserveHidden(actor,member,bbsc_display_reload,bbsc_display_context.room,r.challenge,hidden);
        return hidden;
    }
    const auto* peer=V::Find(bbsc_display_context,user);
    if(!peer || !peer->capable) return false;
    auto& p=bbsc_display_peers[user];
    if(p.actor!=actor || p.member_object!=member || p.reload!=bbsc_display_reload ||
       !(p.peer==*peer) || (dead && !p.dead)) {
        ResetBbscDisplayGrant(p,dead && !p.dead);
        p.peer=*peer;p.actor=actor;p.member_object=member;p.model=model;p.reload=bbsc_display_reload;
    }
    p.model=model;
    p.dead=dead;
    const bool hidden=!(p.allowed && !dead) && !p.expired && !corpse;
    BbscVisualDiagObserveHidden(actor,member,p.reload,bbsc_display_context.room,p.challenge,hidden);
    if(p.allowed && !dead) {p.exposed=true;return false;}
    return hidden;
}

void ArmBbscDisplayGuest(u64 ticket) {
    std::scoped_lock lock{bbsc_display_mutex};
    const auto host=bbsc_display_guest.host,challenge=bbsc_display_guest.challenge;
    bbsc_display_guest={};bbsc_display_guest.ticket=ticket;
    bbsc_display_guest.host=host;bbsc_display_guest.challenge=challenge;
}

bool UpdateBbscDisplayGuest(u64 ticket,u64 reload,u64 role,u64 actor,u32 map,bool ready) {
    std::scoped_lock lock{bbsc_display_mutex};
    auto& g=bbsc_display_guest;
    if(g.ticket>ticket) return false;
    if(g.ticket!=ticket) {g={};g.ticket=ticket;}
    if(g.reload!=reload || g.actor!=actor || g.role!=role || g.map!=map || !ready) {
        g.acknowledged=false;g.sent_ms=0;
    }
    g.reload=reload;g.role=role;g.actor=actor;g.map=map;g.ready=ready;
    const auto* owner=V::Find(bbsc_display_context,bbsc_display_context.owner);
    // No new-protocol host means the tested local protection remains unchanged.
    if(!owner || !owner->capable || g.unavailable) return true;
    return ready && g.acknowledged;
}

void TickBbscPeerDisplay() {
    V::Context context;
    const bool authenticated=BbscMod::RelayAuthVisibilityContext(context);
    const auto now=SteadyNowMs();
    const auto state=GetSessionState();
    const auto local=GetLocalPlayerCharacter();
    const auto map=GetCurrentPackedMap();
    const auto reload=bbsc_character_reload_generation.load();
    u64 host_transition{}, host_reload{}, host_role{};
    const bool host_ready=BbscHostDisplayReady(host_transition,host_reload,host_role);
    const auto local_role=BbscLoadProtectionRoleEpoch();
    std::vector<V::Frame> send;
    {
        std::scoped_lock lock{bbsc_display_mutex};
        if(!authenticated || (state!=3 && state!=4 && state!=6)) {
            ClearBbscVisualDiagnostics();
            bbsc_display_context={};bbsc_display_peers.clear();bbsc_display_messages.clear();
            bbsc_display_host=false;bbsc_display_reverse_active=false;
            bbsc_display_reverse={};bbsc_display_host_queries.clear();return;
        }
        if(!(bbsc_display_context==context)) {
            ClearBbscVisualDiagnostics();
            bbsc_display_reverse={};bbsc_display_host_queries.clear();
        }
        if(bbsc_display_context.room!=context.room || bbsc_display_context.owner!=context.owner || bbsc_display_context.own!=context.own) {
            bbsc_display_peers.clear();bbsc_display_messages.clear();
            bbsc_display_guest.host=bbsc_display_guest.challenge=0;
            bbsc_display_guest.acknowledged=bbsc_display_guest.unavailable=false;
        }
        if(bbsc_display_map && map!=bbsc_display_map) {
            for(auto& [_,p]:bbsc_display_peers) ResetBbscDisplayGrant(p,true);
            ResetBbscHostObserver();
        }
        bbsc_display_map=map;
        bbsc_display_context=context;
        bbsc_display_host=state==3 && context.owner==context.own && bbsc_display_hooks && BbscHostReadyHooksAvailable();
        bbsc_display_reverse_active=state==6 && context.owner!=context.own && BbscReverseCoopSession() && bbsc_display_hooks && BbscHostReadyHooksAvailable();
        auto& reverse=bbsc_display_reverse;
        if(!bbsc_display_reverse_active) reverse={};
        if(bbsc_display_reverse_active && (!reverse.challenge || reverse.local_reload!=reload ||
           reverse.local_role!=local_role || reverse.local_actor!=local || bbsc_display_map!=map)) {
            ResetBbscHostObserver();reverse.local_reload=reload;reverse.local_role=local_role;reverse.local_actor=local;
        }
        bbsc_display_local=local;bbsc_display_reload=reload;
        if(!bbsc_display_nonce) bbsc_display_nonce=BbscDisplayNonce();
        if(!bbsc_display_nonce) return;
        for(const auto& message:bbsc_display_messages) {
            if(!(message.context==context)) continue;
            const auto& f=message.frame;
            if(bbsc_display_host && f.kind==V::HostQuery) {
                if(f.map==map) bbsc_display_host_queries[message.sender]=f;
            } else if(bbsc_display_reverse_active && f.kind==V::HostStatus) {
                auto& r=bbsc_display_reverse;
                if(f.challenge!=r.challenge || f.observer!=r.observer || f.boot!=bbsc_display_nonce ||
                   f.map!=map || r.expired || !r.actor || r.dead) continue;
                if(r.host_boot && r.host_boot!=f.host) {
                    ResetBbscHostObserver();r.host_boot=f.host;r.host_ticket=0;continue;
                }
                if(r.host_boot==f.host && (f.ticket<r.host_ticket ||
                   (f.ticket==r.host_ticket && (f.reload!=r.host_reload || f.role!=r.host_role)))) continue;
                // Current challenge ties this reply to this native observer life.
                r.host_boot=f.host;r.host_ticket=f.ticket;r.host_reload=f.reload;r.host_role=f.role;
                if(f.ready && !r.allowed)
                    LOG_INFO(Debug,"BBSC host display: matching host is ready (transition {})",f.ticket);
                r.allowed=f.ready;
            } else if(bbsc_display_host && f.kind==V::Status) {
                auto p=bbsc_display_peers.find(message.sender);
                if(p==bbsc_display_peers.end() || f.host!=bbsc_display_nonce || f.challenge!=p->second.challenge || f.map!=map) continue;
                auto& hold=p->second;
                if(hold.expired) continue;
                if(f.ticket<=hold.floor_ticket && f.boot==hold.floor_boot) continue;
                if(hold.accepted_boot==f.boot && hold.accepted_ticket>f.ticket) continue;
                if(!f.ready) {
                    if(hold.allowed) ResetBbscDisplayGrant(hold,true);
                } else if(!hold.dead && BbscDisplayNativeAlive(hold.actor)) {
                    if(!hold.allowed) LOG_INFO(Debug,"BBSC display: matching guest is ready (transition {})",f.ticket);
                    hold.accepted_boot=f.boot;hold.accepted_ticket=f.ticket;hold.guest_reload=f.reload;hold.allowed=true;
                    hold.ack_pending=true;
                }
            } else if(context.own!=context.owner && message.sender==context.owner) {
                auto& g=bbsc_display_guest;
                if(f.kind==V::Unavailable) {g.unavailable=true;bbsc_display_reverse.allowed=false;bbsc_display_reverse.expired=true;continue;}
                if(f.kind==V::Query && f.map==g.map) {
                    if(g.host!=f.host || g.challenge!=f.challenge) {g.acknowledged=false;g.sent_ms=0;}
                    g.host=f.host;g.challenge=f.challenge;
                } else if(f.kind==V::Ack && f.host==g.host && f.challenge==g.challenge && f.boot==bbsc_display_nonce &&
                          f.ticket==g.ticket && f.reload==g.reload && f.map==g.map && g.ready) {
                    if(!g.acknowledged) LOG_INFO(Debug,"BBSC display: host draw acknowledged (transition {})",g.ticket);
                    g.acknowledged=true;
                }
            }
        }
        bbsc_display_messages.clear();
        if(bbsc_display_host) {
            const auto* own=V::Find(context,context.own);
            for(const auto& [user,q]:bbsc_display_host_queries) {
                const auto* peer=V::Find(context,user);
                if(!own || !peer || !host_transition || !host_reload || !host_role || q.map!=map) continue;
                send.push_back({V::HostStatus,host_ready,user,map,bbsc_display_nonce,q.challenge,q.host,
                    host_transition,host_reload,context.room,own->joined,peer->joined,host_role,q.observer});
            }
            bbsc_display_host_queries.clear(); // Guest retries queries; no frame flood per tick.
            std::erase_if(bbsc_display_peers,[&](const auto& pair){const auto* p=V::Find(context,pair.first);return !p || !(*p==pair.second.peer);});
            for(auto& [user,p]:bbsc_display_peers) {
                if(!p.allowed && !p.expired && now-p.started>=80'000) {
                    // Cosmetic gate only. Never ACK or alter the original 75s
                    // guest protection/leave path if this peer failed to settle.
                    p.expired=true;
                    LOG_WARNING(Debug,"BBSC display: readiness expired; restoring native visibility without acknowledgment");
                }
                if(p.expired) continue;
                if(!p.allowed && now-p.query_ms>=250) {
                    p.query_ms=now;send.push_back({V::Query,false,user,map,bbsc_display_nonce,p.challenge});
                } else if(p.allowed && p.exposed && p.ack_pending && now-p.ack_ms>=250 &&
                          ReadValue<u64>(p.actor,0x3d8)==p.member_object &&
                          ReadValue<u64>(ReadValue<u64>(p.actor,0x48),0x18)==p.model &&
                          (ReadValue<u8>(p.model,0x210)&1)) {
                    const auto opacity=ReadValue<float>(p.model,0x18c);
                    if(!std::isfinite(opacity) || opacity<=0) continue;
                    p.ack_ms=now;p.ack_pending=false;
                    send.push_back({V::Ack,true,user,map,bbsc_display_nonce,p.challenge,p.accepted_boot,p.accepted_ticket,p.guest_reload});
                }
            }
        } else if(context.own==context.owner && (!bbsc_display_hooks || !BbscHostReadyHooksAvailable()) && now-bbsc_display_unavailable_ms>=500) {
            bbsc_display_unavailable_ms=now;
            for(const auto& p:context.peers) if(p.capable && p.user!=context.own) send.push_back({V::Unavailable,false,p.user});
        } else if(context.own!=context.owner) {
            auto& r=bbsc_display_reverse;
            const auto* own=V::Find(context,context.own);
            const auto* owner=V::Find(context,context.owner);
            if(bbsc_display_reverse_active && !r.allowed && !r.expired && now-r.started>=80'000) {
                r.allowed=false;r.expired=true;
                LOG_WARNING(Debug,"BBSC host display: readiness expired; native visibility restored without ready or ACK");
            }
            if(bbsc_display_reverse_active && !r.expired && own && owner && owner->capable &&
               r.challenge && r.observer && map && map!=0xffffffff && local_role+1 && now-r.sent>=250) {
                r.sent=now;
                send.push_back({V::HostQuery,false,context.owner,map,bbsc_display_nonce,r.challenge,0,0,0,
                    context.room,own->joined,owner->joined,local_role+1,r.observer});
            }
            auto& g=bbsc_display_guest;
            // Protection clears its scope after success. Do not let its cached
            // display readiness survive native death until the next Arm.
            if(g.actor!=local || g.reload!=reload || g.role!=local_role || g.map!=map ||
               GetLocalPlayerType()!=1 || !BbscDisplayNativeAlive(local) || bbsc_spectate_active) {
                g.ready=false;g.acknowledged=false;
            }
            if(g.host && g.challenge && g.ticket && g.reload && !g.acknowledged && !g.unavailable && now-g.sent_ms>=250) {
                g.sent_ms=now;send.push_back({V::Status,g.ready,context.owner,g.map,g.host,g.challenge,bbsc_display_nonce,g.ticket,g.reload});
            }
        }
    }
    TickBbscVisualDiagnostics();
    for(const auto& frame:send) {
        const auto bytes=V::Encode(frame);
        BbscMod::RelayAuthSendVisibility(context,bytes);
    }
}

void PS4_SYSV_ABI BbscPeerDisplayHook(u64 tag,const GuestRegisterSnapshot* saved) {
    if(!saved) return;
    auto& r=*const_cast<GuestRegisterSnapshot*>(saved);
    if(tag==0 && BbscDisplayHideActor(r.rdi)) {
        using Disable=void PS4_SYSV_ABI (*)(u64);
        reinterpret_cast<Disable>(image_base+0x018F48E6)(r.rdi);
        r.skip_original=1;
    } else if(tag==1 || tag==4) {
        if(!bbsc_display_hooks) return;
        const u64 wrapper=tag==1?r.rbx+0xc8:r.r12+0x3d8;
        std::array<u8,16> npid{};
        u32 user=tag==4 && !bbsc_display_constructing.empty()?bbsc_display_constructing.back():0;
        if(ReadBbscDisplayNpid(wrapper,npid)) {
            std::scoped_lock lock{bbsc_display_mutex};
            const auto id=BbscDisplayFindPeer(npid);
            if(bbsc_display_reverse_active && id==bbsc_display_context.owner &&
               (tag==4 || static_cast<u32>(r.r13)==0)) {
                const auto* owner=V::Find(bbsc_display_context,id);
                if(owner && owner->capable) {
                    auto& observer=bbsc_display_reverse;
                    const auto member=ReadValue<u64>(r.r12,0x3d8);
                    const bool same_actor=tag==4 && observer.actor==r.r12 &&
                        observer.member==member && observer.peer==*owner;
                    if(tag==1 || !same_actor) {
                        ResetBbscHostObserver();observer.actor=tag==4?r.r12:0;
                        observer.member=tag==4?member:0;observer.peer=*owner;
                    }
                    // Equipment/model refresh on a granted actor is not a new life.
                    if(!observer.allowed && !observer.expired) user=id;
                }
            } else if(bbsc_display_host && (tag==4 || static_cast<u32>(r.r13)==1)) {
                if(const auto* p=V::Find(bbsc_display_context,id);p && p->capable) {
                    auto it=bbsc_display_peers.find(id);
                    if(tag==1) {
                        auto& hold=bbsc_display_peers[id];ResetBbscDisplayGrant(hold);
                        hold.peer=*p;hold.actor=0;user=id;
                    } else if(it==bbsc_display_peers.end() || (!it->second.allowed && !it->second.expired)) user=id;
                }
            }
        }
        bbsc_display_constructing.push_back(user);
    } else if(tag==2 || tag==5) {
        if(!bbsc_display_constructing.empty()) bbsc_display_constructing.pop_back();
    } else if(tag==3 && !bbsc_display_constructing.empty() && bbsc_display_constructing.back()) {
        // Model construction registers with the scene only after this initial
        // flags store. Gate the initial bit before publication, including parts
        // built by the native remote creation call, without changing other bits.
        r.rcx&=~u64{1};
    }
}

void InvalidateBbscHostDisplayActor(u64 actor) {
    std::array<u8,16> npid{};u64 member{};
    if(!actor || !ReadBbscDisplayNpid(actor+0x3d8,npid,&member)) return;
    V::Context current;
    const bool authenticated=BbscMod::RelayAuthVisibilityContext(current);
    std::scoped_lock lock{bbsc_display_mutex};
    const auto user=BbscDisplayFindPeer(npid);
    if(bbsc_display_host && authenticated && current==bbsc_display_context) {
        const auto it=bbsc_display_peers.find(user);
        const auto* peer=V::Find(current,user);
        if(it!=bbsc_display_peers.end() && peer && peer->capable && it->second.peer==*peer &&
           it->second.actor==actor && it->second.member_object==member)
            InvalidateBbscGuestDisplayGrant(it->second,"remote revive");
    }
    if(bbsc_display_reverse_active && user==bbsc_display_context.owner)
        ResetBbscHostObserver();
}

void InvalidateBbscHostDisplayLife(u32 user) {
    V::Context current;
    if(!BbscMod::RelayAuthVisibilityContext(current)) return;
    std::scoped_lock lock{bbsc_display_mutex};
    if(bbsc_display_host && current==bbsc_display_context) {
        const auto it=bbsc_display_peers.find(user);
        const auto* peer=V::Find(current,user);
        if(it!=bbsc_display_peers.end() && peer && peer->capable && it->second.peer==*peer)
            InvalidateBbscGuestDisplayGrant(it->second,"life report");
    }
    if(bbsc_display_reverse_active && current==bbsc_display_context && user==current.owner)
        ResetBbscHostObserver();
}

void InstallBbscPeerDisplayHooks() {
    constexpr std::array<u8,6> gate{0x8b,0x87,0xe1,0x01,0,0};
    constexpr std::array<u8,7> disable{0x48,0x8b,0x47,0x48,0x48,0x85,0xc0};
    constexpr std::array<u8,14> getter{0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x50,0x49,0x89,0xf6,0x48};
    constexpr std::array<u8,10> create_begin{0x48,0x83,0xc7,0x30,0x44,0x89,0xee,0x4c,0x89,0xf1};
    constexpr std::array<u8,6> create_end{0x49,0x89,0xc7,0x49,0x8b,0x07};
    constexpr std::array<u8,6> initial{0x88,0x8b,0x10,0x02,0x00,0x00};
    constexpr std::array<u8,9> model_begin{0x48,0x8d,0x75,0xb0,0x31,0xc9,0x45,0x31,0xc0};
    for(const auto& [offset,pin]:std::array<std::pair<u64,std::span<const u8>>,3>{{
        {0x018F4830,gate},{0x018F48E6,disable},{0x00CA25E0,getter}}}) {
        if(!HasMemoryAccess(image_base+offset,pin.size(),MemoryProt::CpuRead) ||
           std::memcmp(reinterpret_cast<const void*>(image_base+offset),pin.data(),pin.size())) {
            LOG_WARNING(Debug,"BBSC display: native signature unavailable at {:#x}; damage protection retained",offset);return;
        }
    }
    // model_end includes the vector load's complete 5-byte instruction below.
    constexpr std::array<u8,8> model_return{0x48,0x8b,0x00,0xc5,0xf8,0x10,0x40,0x30};
    const std::array<std::pair<u64,std::span<const u8>>,6> sites{{
        {0x018F4830,gate},{0x01E50271,create_begin},{0x01E50280,create_end},
        {0x01D20EF5,initial},{0x018F3EA2,model_begin},{0x018F3EC7,model_return}}};
    for(const auto& [offset,pin]:sites)
        if(!HasMemoryAccess(image_base+offset,pin.size(),MemoryProt::CpuRead) ||
           std::memcmp(reinterpret_cast<const void*>(image_base+offset),pin.data(),pin.size())) {
            LOG_WARNING(Debug,"BBSC display: native signature unavailable at {:#x}; damage protection retained",offset);return;
        }
    for(size_t i=0;i<sites.size();++i) {
        const auto& [offset,pin]=sites[i];
        if(!InstallGuestCodeHook(reinterpret_cast<void*>(image_base+offset),pin,i,BbscPeerDisplayHook)) {
            LOG_WARNING(Debug,"BBSC display: hook unavailable at {:#x}; damage protection retained",offset);return;
        }
    }
    bbsc_display_hooks=true;
    InstallBbscVisualDiagnostics();
    LOG_INFO(Debug,"BBSC display: PlayerIns gate {}",bbsc_display_hooks.load()?"ready":"unavailable");
}

