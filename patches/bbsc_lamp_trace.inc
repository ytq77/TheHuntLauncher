// Private diagnostic only. Included by prepare_lamp_trace.py, never by the release build.
// Reads game memory on the game thread; the native save hook uses cached context only.
#include "bbsc_lamp_trace_groups.h"
extern "C" __declspec(dllimport) unsigned long __stdcall GetCurrentThreadId();
std::mutex lamp_trace_mutex;
std::string lamp_trace_cached="{}", lamp_trace_last;
s64 lamp_trace_sample=0;
u32 lamp_trace_lines=0;
u64 lamp_trace_operations=0;
std::map<std::string,u32> lamp_trace_seen;

bool LampTraceEnabled() {
    static const bool enabled=EnvFlagEnabled("THEHUNT_LAMP_TRACE");
    return enabled;
}
s64 LampTraceNow() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}
std::string LampTraceContext() {
    if(image_base==0)return "{}";
    try {
        const u64 global=GetGlobalState();
        if(!ReadablePointer(global,0x1598))return "{}";
        std::ostringstream state;
        const auto slot=BbscActiveSaveSlot();
        state<<"{\"session\":"<<GetSessionState()<<",\"map\":"<<GetCurrentPackedMap()
             <<",\"own_world\":"<<int(ReadValue<u8>(global,0x1592))
             <<",\"loading\":"<<int(ReadValue<u8>(global,0x08))
             <<",\"active_slot_hint\":"<<(slot?slot->slot:-1)
             <<",\"journal_slot\":"<<(bbsc_own_world_slot?bbsc_own_world_slot->slot:-1)
             <<",\"groups\":{";
        constexpr char hex[]="0123456789abcdef";
        bool comma=false;
        for(u32 key:LampTraceGroups) {
            std::array<u8,125> first{},second{};size_t read=0;
            const auto address=BbscFlagGroupStorage(key);
            if(!address||!ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(address),first.data(),first.size(),&read)||read!=first.size()||
               !ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(address),second.data(),second.size(),&read)||read!=second.size()||first!=second)continue;
            if(comma)state<<',';comma=true;
            state<<'"'<<key<<"\":\"";
            for(u8 value:first)state<<hex[value>>4]<<hex[value&15];
            state<<'"';
        }
        state<<"}}";return state.str();
    }catch(...){return "{}";}
}
void LampTraceCache(const std::string& context,s64 sampled) {
    std::scoped_lock lock{lamp_trace_mutex};
    lamp_trace_cached=context;lamp_trace_sample=sampled;
}
void LampTraceEmit(const char* reason,const std::string& context,s64 sampled,u64 operation,bool cached=false) {
    std::scoped_lock lock{lamp_trace_mutex};
    if(lamp_trace_lines>=4096)return;
    const char* path=std::getenv("THEHUNT_LAMP_TRACE_PATH");if(!path||!path[0])return;
    std::ofstream out(std::filesystem::path(path),std::ios::app);if(!out)return;
    out<<"{\"sequence\":"<<++lamp_trace_lines<<",\"epoch_ms\":"<<LampTraceNow()
       <<",\"sample_epoch_ms\":"<<sampled<<",\"thread\":"<<GetCurrentThreadId()
       <<",\"reason\":\""<<reason<<"\",\"operation\":"<<operation
       <<",\"cached\":"<<(cached?"true":"false")<<",\"context\":"<<context<<"}\n";
}
LampTraceScope::LampTraceScope(const char* name) noexcept:label(name) {
    if(!LampTraceEnabled())return;
    try{sampled=LampTraceNow();before=LampTraceContext();operation=++lamp_trace_operations;LampTraceCache(before,sampled);}catch(...){}
}
LampTraceScope::~LampTraceScope() noexcept {
    if(!operation)return;
    try {
        const auto after=LampTraceContext();const auto now=LampTraceNow();
        // Keep the first few unchanged boundaries too; a per-frame no-op must not exhaust the trace.
        if(before!=after||++lamp_trace_seen[label]<=4) {
            const auto enter=std::string(label)+".enter",leave=std::string(label)+".exit";
            LampTraceEmit(enter.c_str(),before,sampled,operation);LampTraceEmit(leave.c_str(),after,now,operation);
        }
        LampTraceCache(after,now);
    }catch(...){}
}
void LampTraceTick() {
    if(!LampTraceEnabled())return;
    try {
        static s64 last=0;const auto now=LampTraceNow();if(now-last<500)return;last=now;
        const auto context=LampTraceContext();LampTraceCache(context,now);
        if(context!=lamp_trace_last){lamp_trace_last=context;LampTraceEmit("tick",context,now,0);}
    }catch(...){}
}
void LampTraceCachedSave() {
    if(!LampTraceEnabled())return;
    try {
        std::string context;s64 sampled;
        {std::scoped_lock lock{lamp_trace_mutex};context=lamp_trace_cached;sampled=lamp_trace_sample;}
        LampTraceEmit("native-save-entry-cached",context,sampled,0,true);
    }catch(...){}
}
