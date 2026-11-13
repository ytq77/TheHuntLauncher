// Optional diagnostic only: no game data, registers, packets or session state are written.
struct BbscJoinCauseRecord {
    u32 kind{}, state{}, status{}, event{}, error{}, prerequisites{};
    u64 reason{}, caller{};
    bool operator==(const BbscJoinCauseRecord&) const = default;
};
struct BbscJoinCauseWindow {
    u64 id{}, begin{}, end{};
    std::array<BbscJoinCauseRecord,32> records{};
    u32 count{}, suppressed{}, join_seen{};
    bool closed{};
};
std::atomic<bool> bbsc_join_cause_ready{false};
std::mutex bbsc_join_cause_mutex;
std::array<BbscJoinCauseWindow,8> bbsc_join_cause_windows{};
size_t bbsc_join_cause_used{};
std::atomic<unsigned> bbsc_join_cause_closed{0};
struct BbscJoinCauseErrors {
    DWORD win=GetLastError(); int number=errno;
    ~BbscJoinCauseErrors(){ errno=number; SetLastError(win); }
};
struct BbscJoinCausePin { u64 rva; std::array<u8,32> bytes; };
constexpr std::array<BbscJoinCausePin,5> BbscJoinCausePins{{
    {0xcd8bb0, {0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x81,0xE4,0xE0,0xFF,0xFF,0xFF,0x48,0x81,0xEC,0x20,0x03,0x00,0x00,0x48,0x89,0xFB,0x4C,0x8B}},
    {0xcd89c0, {0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x53,0x50,0x49,0x89,0xF7,0x48,0x89,0xFB,0x4C,0x8D,0x73,0x70,0x48,0x8B,0x43,0x70,0xBE,0xFF,0xFF,0xFF,0xFF,0x4C,0x89,0xF7}},
    {0xcda510, {0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x53,0x50,0x49,0x89,0xF7,0x48,0x89,0xFB,0x4C,0x8D,0x73,0x70,0x48,0x8B,0x43,0x70,0xBE,0xFF,0xFF,0xFF,0xFF,0x4C,0x89,0xF7}},
    {0xcda250, {0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x83,0xEC,0x38,0x49,0x89,0xF7,0x49,0x89,0xFC,0x48,0x8B,0x05,0x72,0x99,0x70,0x04,0x48,0x8B}},
    {0x1ed0670, {0x55,0x48,0x89,0xE5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x48,0x83,0xEC,0x28,0x49,0x89,0xFD,0x48,0x8B,0x1D,0x55,0x35,0x51,0x03,0x48,0x8B,0x03,0x48,0x89}}
}};

template<class T> bool CopyBbscJoinCause(u64 address,T& output) {
    if(!address||address+sizeof(T)<address)return false;
    size_t copied{};T value{};
    if(!ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<const void*>(address),&value,sizeof(value),&copied)||copied!=sizeof(value))return false;
    output=value;return true;
}
template<class T,size_t N>T BbscJoinCauseField(const std::array<u8,N>& data,size_t offset) {
    T value{};if(offset<=N&&sizeof(T)<=N-offset)std::memcpy(&value,data.data()+offset,sizeof(value));return value;
}
struct BbscJoinCausePolicy { u32 valid{},index{},create_ms{}; };
BbscJoinCausePolicy ReadBbscJoinCausePolicy() {
    BbscJoinCausePolicy out;u64 root{},timer{};u32 index{};float seconds{};
    if(!CopyBbscJoinCause(image_base+0x0553B120,root)||!root||root+0xC58<root)return out;
    if(CopyBbscJoinCause(root+0x9E8,index)){out.index=index;out.valid|=1;}
    if(CopyBbscJoinCause(root+0xC50,timer)&&timer&&timer+0x194>=timer&&
       CopyBbscJoinCause(timer+0x190,seconds)&&std::isfinite(seconds)&&seconds>=0&&seconds<=3600) {
        out.create_ms=static_cast<u32>(seconds*1000.0f);out.valid|=2;
    }
    return out;
}
struct BbscJoinCausePolicyPin {u64 rva;std::array<u8,15> bytes;size_t length;};
constexpr std::array<BbscJoinCausePolicyPin,3> BbscJoinCausePolicyPins{{
 {0x1E899E6,{0x8B,0x80,0xE8,0x09,0x00,0x00},6},
 {0x1E89A57,{0x8B,0x88,0x9C,0x08,0x00,0x00},6},
 {0x1E89AA6,{0x48,0x8B,0x89,0x50,0x0C,0x00,0x00,0xC5,0xFA,0x11,0x81,0x90,0x01,0x00,0x00},15}
}};

void EmitBbscJoinCause(u64 id, u64 elapsed, const BbscJoinCauseRecord& r) {
    LOG_INFO(Debug, "BBSC join cause: window={} elapsed_ms={} kind={} state={} status={} reason={:#x} caller_rva={:#x} event={} error={:#x} prerequisites={:#x}",
             id, elapsed, r.kind, r.state, r.status, r.reason, r.caller, r.event, r.error, r.prerequisites);
}
void CloseBbscJoinCauseWindow(u64 id) noexcept {
    BbscJoinCauseErrors preserved;
    try {
        if (!id || !bbsc_join_cause_ready.load(std::memory_order_acquire)) return;
        BbscJoinCauseWindow summary{}; bool found=false;
        {
            std::scoped_lock lock{bbsc_join_cause_mutex};
            for (auto& w: bbsc_join_cause_windows) if(w.id==id) {
                if(w.closed)return;
                w.closed=true;summary=w;found=true;break;
            }
            if(!found && id && bbsc_join_cause_used<bbsc_join_cause_windows.size()) {
                auto& w=bbsc_join_cause_windows[bbsc_join_cause_used++];w.id=id;w.closed=true;summary=w;found=true;
            }
        }
        if(found)bbsc_join_cause_closed.fetch_add(1,std::memory_order_release);
        // A zero count means no entry observed under the single-window rule,
        // not a claim that an entry could not occur outside/under contention.
        if(found) LOG_INFO(Debug,"BBSC join cause summary: window={} records={} suppressed={} join_gate_seen_in_window={} coverage=bounded-single-window",
                           id,summary.count,summary.suppressed,summary.join_seen);
    } catch (...) {}
}

void PS4_SYSV_ABI BbscJoinCauseHook(u64 tag,const GuestRegisterSnapshot* registers) noexcept {
    BbscJoinCauseErrors preserved;
    try {
        if (!bbsc_join_cause_ready.load(std::memory_order_acquire) || !registers || tag>=5 ||
            !BbscMod::J4JoinCauseEnabled(true) || bbsc_join_cause_closed.load(std::memory_order_acquire)>=8) return;
        u64 id{},begin{},end{},now{};
        s32 context{},connection{};
        const u64 object=registers->rdi;
        std::array<u8,0xB8> native{};
        if(tag!=4) {
            if(!CopyBbscJoinCause(object,native))return;
            context=BbscJoinCauseField<s32>(native,0xA8);connection=BbscJoinCauseField<s32>(native,0x10);
            if(context<=0||connection<=0)return;
        }
        if(!BbscMod::J4JoinCauseWindow(context,connection,id,begin,end,now) || !id ||
           now<begin || now>=end || now-begin>8000) return;
        BbscJoinCauseRecord record{}; record.kind=static_cast<u32>(tag);
        if(tag==4) {
            std::array<u8,0x128> manager{};if(!CopyBbscJoinCause(object,manager))return;
            record.state=BbscJoinCauseField<u32>(manager,0x124);
            record.prerequisites=(BbscJoinCauseField<u64>(manager,0x18)!=0?1u:0u) |
                                 (BbscJoinCauseField<u64>(manager,0xA0)!=0?2u:0u) |
                                 (BbscJoinCauseField<u64>(manager,0xA8)!=0?4u:0u);
        } else {
            record.state=BbscJoinCauseField<u32>(native,0xB4);record.status=BbscJoinCauseField<u32>(native,0x0C);
            record.reason=tag==1||tag==2?registers->rsi:BbscJoinCauseField<u64>(native,0x68);
            if(tag==3) {
                std::array<u8,0x28> event{};
                if(!CopyBbscJoinCause(registers->rsi,event)||BbscJoinCauseField<u64>(event,0x20)!=static_cast<u64>(connection))return;
                record.event=BbscJoinCauseField<u32>(event,0x04);record.error=BbscJoinCauseField<u32>(event,0x10);
            }
        }
        u64 caller{};
        if(tag!=0&&CopyBbscJoinCause(registers->rsp,caller)&&caller>=image_base&&caller-image_base<0x050D96DC)record.caller=caller-image_base;
        bool emit=false,policy_sample=false;
        {
            std::unique_lock lock{bbsc_join_cause_mutex,std::try_to_lock};
            if(!lock)return;
            BbscJoinCauseWindow* scope=nullptr;
            for(size_t i=0;i<bbsc_join_cause_used;++i)if(bbsc_join_cause_windows[i].id==id)scope=&bbsc_join_cause_windows[i];
            if(!scope) {
                if(bbsc_join_cause_used==bbsc_join_cause_windows.size())return;
                scope=&bbsc_join_cause_windows[bbsc_join_cause_used++];scope->id=id;scope->begin=begin;scope->end=end;policy_sample=true;
            }
            if(scope->closed||scope->begin!=begin)return;
            if(tag==4&&scope->join_seen!=UINT32_MAX)++scope->join_seen;
            bool duplicate=false;
            for(u32 i=scope->count;i>0;--i)if(scope->records[i-1].kind==record.kind){duplicate=scope->records[i-1]==record;break;}
            if(duplicate)return;
            if(scope->count==scope->records.size()){if(scope->suppressed!=UINT32_MAX)++scope->suppressed;return;}
            scope->records[scope->count++]=record;emit=true;
        }
        if(policy_sample) {
            const auto policy=ReadBbscJoinCausePolicy();
            LOG_INFO(Debug,"BBSC join policy: window={} valid_mask={} native_suffix_index={} effective_create_ms={}",id,policy.valid,policy.index,policy.create_ms);
        }
        if(emit)EmitBbscJoinCause(id,now-begin,record);
    } catch (...) {} // Observers never unwind into guest code.
}

void InstallBbscJoinCauseHooks() {
    BbscJoinCauseErrors preserved;
    static bool attempted=false;
    if(attempted||!BbscMod::J4JoinCauseEnabled(false))return;
    attempted=true;
    const auto size=MemoryPatcher::g_eboot_image_size;
    for(const auto& pin: BbscJoinCausePins) {
        std::array<u8,32> actual{};
        if(!image_base||pin.rva>size||pin.bytes.size()>size-pin.rva||
           !CopyBbscJoinCause(image_base+pin.rva,actual)||actual!=pin.bytes) {
            LOG_WARNING(Debug,"BBSC join cause: disabled, native pin mismatch at {:#x}",pin.rva);return;
        }
    }
    for(const auto& pin:BbscJoinCausePolicyPins) {
        std::array<u8,15> actual{};
        if(pin.rva>size||actual.size()>size-pin.rva||!CopyBbscJoinCause(image_base+pin.rva,actual)||
           std::memcmp(actual.data(),pin.bytes.data(),pin.length)) {
            LOG_WARNING(Debug,"BBSC join cause: disabled, policy layout mismatch at {:#x}",pin.rva);return;
        }
    }
    // All first-eight-byte spans end on instruction boundaries and contain only
    // push rbp; mov rbp,rsp; push r15; push r14. Existing hook code preserves registers.
    for(size_t i=0;i<BbscJoinCausePins.size();++i) {
        const auto& pin=BbscJoinCausePins[i];
        if(!InstallGuestCodeHook(reinterpret_cast<void*>(image_base+pin.rva),
            std::span<const u8>{pin.bytes.data(),8},i,BbscJoinCauseHook)) {
            // Previously installed hooks remain forwarding-only: ready is still false.
            LOG_WARNING(Debug,"BBSC join cause: disabled, hook installation incomplete");return;
        }
    }
    bbsc_join_cause_ready.store(true,std::memory_order_release);
    BbscMod::J4JoinCauseRegisterClose(&CloseBbscJoinCauseWindow);
    LOG_INFO(Debug,"BBSC join cause: five read-only entry hooks enabled; 8 windows x 32 records maximum");
}
