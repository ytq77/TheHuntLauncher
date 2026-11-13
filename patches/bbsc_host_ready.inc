bool BbscReverseCoopSession() {
    const auto global=GetGlobalState();
    return GetSessionState()==6 && GetLocalPlayerType()==1 && ReadablePointer(global,0x1593) &&
        ReadValue<u8>(global,0x1592)==0;
}

bool BbscHostReadyHooksAvailable() { return bbsc_load_protection_hooks.load(); }

// Read-only host readiness. Never arms guest damage protection or waits for a peer.
// Included after load protection so native screen/model predicates are shared.
struct BbscHostReadyState {
    u64 actor{}, reload{}, role{}, room{}, owner{}, transition{}, global{}, world{};
    s32 slot{-1};
    u32 map{};
    s64 armed{}, pad_ms{}, map_ms{};
    bool dead{}, loading{}, placed{}, placement_ticket{}, native_epoch{};
};
std::mutex bbsc_host_ready_mutex;
BbscHostReadyState bbsc_host_ready;
u64 bbsc_host_ready_serial{};

void ArmBbscHostReadyPlacement(u64 actor,u32 map,u64 reload,u64 role) {
    std::scoped_lock lock{bbsc_host_ready_mutex};
    auto& s=bbsc_host_ready;s={};
    s.actor=actor;s.reload=reload;s.role=role;s.room=BbscMod::RelayAuthRoom();
    s.owner=BbscRelayHostForPackets();s.transition=++bbsc_host_ready_serial;s.map=map;
    s.armed=SteadyNowMs();s.global=GetGlobalState();
    s.world=ReadValue<u64>(image_base+CandidateLocalStatePointerOffset,0);
    s.slot=ReadablePointer(s.global,0x1593)?ReadValue<s32>(s.global,0x1490):-1;
    s.placement_ticket=true;
    // Tick must independently confirm this exact native root/map before Pad can count.

}

bool BbscHostInserted(u64 actor,u32 map) {
    const auto world=ReadValue<u64>(image_base+CandidateLocalStatePointerOffset,0);
    return actor && ReadablePointer(world,0x70) && ReadValue<u64>(world,0x60)==actor &&
        IsUsablePackedMap(map) && GetCurrentPackedMap()==map && BbscMapResident(map) &&
        ReadCharacterTransform(actor).has_value();
}

void TickBbscHostReady() {
    const auto actor=GetLocalPlayerCharacter(), reload=bbsc_character_reload_generation.load();
    const auto role=BbscLoadProtectionRoleEpoch(), room=BbscMod::RelayAuthRoom();
    const auto owner=BbscRelayHostForPackets();
    const auto map=GetCurrentPackedMap();
    const auto global=GetGlobalState(), control=ReadValue<u64>(actor,0x58);
    const auto world=ReadValue<u64>(image_base+CandidateLocalStatePointerOffset,0);
    const s32 slot=ReadablePointer(global,0x1593)?ReadValue<s32>(global,0x1490):-1;
    const bool host=GetSessionState()==3 && GetLocalPlayerType()==0 && slot>=0 && slot<=9 &&
        ReadablePointer(global,0x1593) && ReadValue<u8>(global,0x1592)==1;
    const bool loading=!ReadablePointer(global,0x1593) || ReadValue<u8>(global,0x08)!=0;
    const bool dead=!ReadablePointer(control,0x116) || GetCharacterHp(actor)<=0 ||
        (ReadValue<u16>(control,0x114)&1);
    const bool inserted=host && !loading && !dead && BbscHostInserted(actor,map);
    std::scoped_lock lock{bbsc_host_ready_mutex};
    auto& s=bbsc_host_ready;
    if(!host) {s={};return;}
    if(!s.transition || s.actor!=actor || s.reload!=reload || s.role!=role || s.room!=room ||
       s.owner!=owner || s.map!=map || s.global!=global || s.world!=world || s.slot!=slot || (dead&&!s.dead) || (loading&&!s.loading)) {
        const bool native_epoch=!s.transition || s.reload!=reload || s.actor!=actor ||
            s.global!=global || s.world!=world || s.role!=role || s.room!=room || s.owner!=owner;
        s={};s.actor=actor;s.reload=reload;s.role=role;s.room=room;s.owner=owner;
        s.transition=++bbsc_host_ready_serial;s.map=map;s.armed=SteadyNowMs();
        s.global=global;s.world=world;s.slot=slot;s.native_epoch=native_epoch;
    }
    s.dead=dead;s.loading=loading;
    s.placed=inserted && (s.placement_ticket || s.native_epoch);
    s.map_ms=s.placed?SteadyNowMs():0;
    if(!s.placed) s.pad_ms=0;
}

void RecordBbscHostReadyPad(u64 tag,const GuestRegisterSnapshot* r) {
    if(!r || (tag!=3 && tag!=6) || GetSessionState()!=3 || GetLocalPlayerType()!=0) return;
    const auto actor=GetLocalPlayerCharacter(), pad=r->r12;
    if(!actor || !ReadablePointer(pad,0x195) || ReadValue<u64>(pad,0x88)!=actor) return;
    const auto control=ReadValue<u64>(actor,0x58);
    if(!ReadablePointer(control,0x246)) return;
    const auto flag=ReadValue<u8>(control,0x245), alternate=ReadValue<u8>(pad,0x194);
    if(tag==3 ? !(flag==0 || alternate>0) : !(flag>0 && alternate==0)) return;
    const auto reload=bbsc_character_reload_generation.load(), role=BbscLoadProtectionRoleEpoch();
    const auto room=BbscMod::RelayAuthRoom();const auto owner=BbscRelayHostForPackets();
    const auto global=GetGlobalState();
    const auto world=ReadValue<u64>(image_base+CandidateLocalStatePointerOffset,0);
    const auto now=SteadyNowMs();
    std::scoped_lock lock{bbsc_host_ready_mutex};
    auto& s=bbsc_host_ready;
    // Both callbacks are actual native dispatch witnesses, not static flag guesses.
    if(s.transition && s.placed && !s.dead && !s.loading && s.actor==actor && s.reload==reload &&
       s.role==role && s.room==room && s.owner==owner && s.global==global && s.world==world &&
       ReadablePointer(global,0x1593) && ReadValue<s32>(global,0x1490)==s.slot &&
       ReadValue<u8>(global,0x1592)==1 && ReadValue<u8>(global,0x08)==0 &&
       now>=s.armed && BbscLoadPadMapFresh(s.map_ms,now)) s.pad_ms=now;
}

bool BbscHostNativeReady(u64 actor,u32 map,u64 reload,u64 role) {
    BbscHostReadyState s;
    {std::scoped_lock lock{bbsc_host_ready_mutex};s=bbsc_host_ready;}
    const auto now=SteadyNowMs();const auto global=GetGlobalState();
    const auto control=ReadValue<u64>(actor,0x58);
    return BbscHostReadyHooksAvailable() && s.transition && s.placed && !s.dead && !s.loading && s.actor==actor && s.map==map &&
        s.reload==reload && s.role==role && s.room==BbscMod::RelayAuthRoom() &&
        s.owner==BbscRelayHostForPackets() && s.global==global &&
        s.world==ReadValue<u64>(image_base+CandidateLocalStatePointerOffset,0) &&
        ReadablePointer(global,0x1593) && ReadValue<s32>(global,0x1490)==s.slot &&
        ReadValue<u8>(global,0x1592)==1 && BbscLoadPadMapFresh(s.map_ms,now) && s.pad_ms>=s.armed && s.pad_ms>0 &&
        now>=s.pad_ms && now-s.pad_ms<=BbscLoadPadFreshMs &&
        GetSessionState()==3 && GetLocalPlayerType()==0 && actor==GetLocalPlayerCharacter() &&
        reload==bbsc_character_reload_generation.load() && role==BbscLoadProtectionRoleEpoch() &&
        ReadablePointer(global,0x1593) && ReadValue<u8>(global,0x08)==0 &&
        ReadablePointer(control,0x116) && !(ReadValue<u16>(control,0x114)&1) &&
        GetCharacterHp(actor)>0 && BbscHostInserted(actor,map) && ReadBbscLoadVisual(actor).Ready();
}

bool BbscHostDisplayReady(u64& transition,u64& reload,u64& role) {
    BbscHostReadyState s;
    {std::scoped_lock lock{bbsc_host_ready_mutex};s=bbsc_host_ready;}
    transition=s.transition;reload=s.reload+1;role=s.role+1;
    return transition && reload && role && BbscHostNativeReady(s.actor,s.map,s.reload,s.role) &&
        ReadBbscLoadScreen().Clear();
}

