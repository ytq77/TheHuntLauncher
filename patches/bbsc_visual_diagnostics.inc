// Bounded, Windows-only observation. No actor/node writes, no chain traversal.
// Include after memory/read helpers and reload generation, before peer_display.
namespace BbscVisualDiagnostics {
struct Key {
    u64 actor{}, member{}, reload{}, room{}, challenge{};
    bool operator==(const Key&) const = default;
};
struct Record {
    u32 elapsed_ms{}, vtable_rva{}, effect_parameter{};
    u16 node_index{}, attachment{};
    u8 flags{}, instance_present{}, hidden{};
};
struct State {
    Key key{};
    s64 started{}, next_sample{};
    u64 epoch{};
    std::array<Record,40> records{};
    std::array<s64,16> last_sample{};
    std::array<u64,16> nodes{}; // Private only: never written to logs.
    u32 count{}, node_count{}, attempts{}, failed{};
    bool hidden{}, valid{}, flushed{};
};
std::atomic_flag guard=ATOMIC_FLAG_INIT;
std::atomic<bool> installed{false}, armed{false};
std::atomic<u64> actor_hint{0}, epoch{1};
State state;
struct TryGuard {
    bool acquired{!guard.test_and_set(std::memory_order_acquire)};
    ~TryGuard(){if(acquired)guard.clear(std::memory_order_release);}
};
// No emulator memory-query lock or error logging in this path. One region
// query and one safe copy, never a page walk. Mapping != lifetime; callers
// additionally require native-update ownership plus identity revalidation.
bool Copy(u64 address,void* out,size_t bytes) {
#ifdef _WIN32
    if(!address || !bytes || address>~u64{0}-bytes) return false;
    MEMORY_BASIC_INFORMATION m{};
    if(!VirtualQuery(reinterpret_cast<const void*>(address),&m,sizeof(m)) ||
       m.State!=MEM_COMMIT || (m.Protect&(PAGE_GUARD|PAGE_NOACCESS))) return false;
    const DWORD protection=m.Protect&0xff;
    if(protection!=PAGE_READONLY && protection!=PAGE_READWRITE &&
       protection!=PAGE_WRITECOPY && protection!=PAGE_EXECUTE_READ &&
       protection!=PAGE_EXECUTE_READWRITE && protection!=PAGE_EXECUTE_WRITECOPY) return false;
    const u64 base=reinterpret_cast<u64>(m.BaseAddress);
    if(address<base || m.RegionSize<bytes || address-base>m.RegionSize-bytes) return false;
    SIZE_T copied{};
    return ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<const void*>(address),out,bytes,&copied) && copied==bytes;
#else
    return false;
#endif
}
template<class T,size_t N>T Field(const std::array<u8,N>& b,size_t offset) {
    T value{};std::memcpy(&value,b.data()+offset,sizeof(value));return value;
}
void Stop(){armed.store(false,std::memory_order_release);actor_hint.store(0,std::memory_order_release);}
}

void ClearBbscVisualDiagnostics() {
    using namespace BbscVisualDiagnostics;
    // Stop first even if a callback owns the bounded try-lock. A later Observe
    // may start a new key; old callbacks see armed=false before commit.
    Stop();epoch.fetch_add(1,std::memory_order_acq_rel);
    TryGuard lock;if(!lock.acquired)return;
    state={};
}
void BbscVisualDiagObserveHidden(u64 actor,u64 member,u64 reload,u64 room,u64 challenge,bool hidden) {
    using namespace BbscVisualDiagnostics;
    if(!BbscVisualDiagnostics::installed.load(std::memory_order_acquire))return;
    TryGuard lock;if(!lock.acquired)return;
    const Key key{actor,member,reload,room,challenge};
    // One selected identity per explicit lifecycle epoch. Other draw owners,
    // invalid observations and challenge churn cannot reset budget or deadline.
    const auto current_epoch=epoch.load(std::memory_order_acquire);
    if(state.epoch!=current_epoch){Stop();state={};}
    if(state.valid) {
        if(state.key==key)state.hidden=hidden;
        else if(actor==state.key.actor)Stop(); // Reused owner: never re-arm.
        if(SteadyNowMs()<state.started || SteadyNowMs()-state.started>=10'000)Stop();
        return;
    }
    if(!hidden || !actor || !member || !reload || !room || !challenge ||
       reload!=bbsc_character_reload_generation.load(std::memory_order_acquire))return;
    state.key=key;state.epoch=current_epoch;state.valid=true;state.hidden=true;
    state.started=SteadyNowMs();state.next_sample=state.started;
    actor_hint.store(actor,std::memory_order_release);armed.store(true,std::memory_order_release);
}
void PS4_SYSV_ABI BbscVisualDiagnosticHook(u64,const GuestRegisterSnapshot* saved) {
    using namespace BbscVisualDiagnostics;
    if(!saved || !armed.load(std::memory_order_acquire) ||
       saved->r15!=actor_hint.load(std::memory_order_acquire))return;
    TryGuard lock;if(!lock.acquired)return;
    if(!armed.load(std::memory_order_acquire) || !state.valid || saved->r15!=state.key.actor)return;
    const s64 now=SteadyNowMs();
    if(now<state.started || now-state.started>=10'000 ||
       state.key.reload!=bbsc_character_reload_generation.load(std::memory_order_acquire)) {Stop();return;}
    if(state.epoch!=epoch.load(std::memory_order_acquire)){Stop();return;}
    if(state.attempts>=20){Stop();return;}
    if(now<state.next_sample || state.count>=state.records.size())return;
    size_t index{};
    while(index<state.node_count && state.nodes[index]!=saved->r12)++index;
    if(index<state.node_count && now-state.last_sample[index]<2000)return;
    if(index>=state.nodes.size())return;
    // Failed reads consume the same budget. No retry loops inside native code.
    state.next_sample=now+500;++state.attempts;
    const auto key=state.key;
    u64 member{},member_after{};
    std::array<u8,0x70> node{};
    if(key.actor>~u64{0}-0x3d8 ||
       !Copy(key.actor+0x3d8,&member,sizeof(member))) {++state.failed;return;}
    if(member!=key.member){++state.failed;Stop();return;}
    if(!Copy(saved->r12,node.data(),node.size())) {++state.failed;return;}
    const auto vt=Field<u64>(node,0);
    if(vt!=image_base+0x0532F4D0) {++state.failed;return;}
    // The native update keeps node and owner live. Member/reload and the
    // published armed state are checked again after the safe snapshot.
    if(!Copy(key.actor+0x3d8,&member_after,sizeof(member_after)) || member_after!=key.member ||
       key.reload!=bbsc_character_reload_generation.load(std::memory_order_acquire) ||
       !armed.load(std::memory_order_acquire) || state.epoch!=epoch.load(std::memory_order_acquire) || !(state.key==key)) {++state.failed;Stop();return;}
    if(index==state.node_count) {
        if(index>=state.nodes.size())return;
        state.nodes[index]=saved->r12;++state.node_count;
    }
    state.last_sample[index]=now;
    state.records[state.count++]={static_cast<u32>(now-state.started),0x0532F4D0,
        Field<u32>(node,0x20),static_cast<u16>(index+1),Field<u16>(node,0x24),
        node[0x26],static_cast<u8>(Field<u64>(node,0x68)!=0),static_cast<u8>(state.hidden)};
    if(state.attempts>=20 || state.count>=state.records.size())Stop();
    // Deliberately no writes to *saved, skip_original, native objects or flags.
}
void TickBbscVisualDiagnostics() {
    using namespace BbscVisualDiagnostics;
    std::array<Record,40> records{};u32 count{},attempts{},failed{};bool flush=false;
    {
        TryGuard lock;if(!lock.acquired || !state.valid || state.flushed)return;
        const auto now=SteadyNowMs();
        if(state.epoch!=epoch.load(std::memory_order_acquire)) {Stop();state={};return;}
        // Reload invalidates records but preserves selection until explicit Clear.
        if(state.key.reload!=bbsc_character_reload_generation.load(std::memory_order_acquire)) {Stop();state.flushed=true;state.count=0;return;}
        if(now<state.started || now-state.started>=10'000)Stop();
        if(armed.load(std::memory_order_acquire))return;
        count=state.count;attempts=state.attempts;failed=state.failed;
        records=state.records;state.flushed=true;flush=true;
    }
    if(!flush)return;
    LOG_INFO(Debug,"BBSC visual diagnostic: bounded SFX window records={} attempts={} failed={}",count,attempts,failed);
    for(u32 i=0;i<count;++i) {
        const auto& r=records[i];
        LOG_INFO(Debug,"BBSC visual diagnostic: ms={} node={} vt={:#x} effect-param={} attach={} flags={} instance={} hidden={}",
            r.elapsed_ms,r.node_index,r.vtable_rva,r.effect_parameter,r.attachment,r.flags,r.instance_present,r.hidden);
    }
}
void InstallBbscVisualDiagnostics() {
    using namespace BbscVisualDiagnostics;
#ifdef _WIN32
    constexpr std::array<u8,7> pin{0x41,0x8b,0x8f,0xe1,0x01,0x00,0x00};
    // 18ABBD7: mov ecx,[r15+1e1], complete instruction, no RIP-relative operand.
    // R15 is owner loaded from [RSI] at 18AB859; R12 is entry RDI=node.
    if(BbscVisualDiagnostics::installed.load(std::memory_order_acquire))return;
    std::array<u8,7> actual{};
    if(!Copy(image_base+0x018ABBD7,actual.data(),actual.size()) || actual!=pin ||
       !InstallGuestCodeHook(reinterpret_cast<void*>(image_base+0x018ABBD7),pin,0,BbscVisualDiagnosticHook)) {
        LOG_WARNING(Debug,"BBSC visual diagnostic: probe unavailable; native display behavior unchanged");return;
    }
    BbscVisualDiagnostics::installed.store(true,std::memory_order_release);
    LOG_INFO(Debug,"BBSC visual diagnostic: bounded read-only SFX probe ready");
#endif
}
