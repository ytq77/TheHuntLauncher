// SPDX-License-Identifier: GPL-2.0-or-later
// Optional read-only J4 observation. Only the import slots are replaced; game data is never written.
#include <windows.h>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <vector>

namespace BbscMod {
std::uint64_t EbootAddress();
void LogPlain(const char*);
void LogJ4If(const char*,bool (*)(void*),void*);
void GuardSlot(std::uint64_t, std::uint64_t, void**, const char*);
}
namespace BbscJ4Diag {
using U64 = std::uint64_t;
using U32 = std::uint32_t;
using S32 = std::int32_t;
using U16 = std::uint16_t;
constexpr U64 EbootSize = 0x056D4000, CodeSize = 0x050D96DC;
constexpr U64 Singleton = 0x05540290, MaximumMs = 8000, TailMs = 1000;
enum Fn : unsigned { Activate, Deactivate, Join, CreateJoin, Leave, SignalStatus,
    MatchingStatus, Ping, Search, CreateContext, Recv, Send, FunctionCount };
constexpr std::array<const char*, FunctionCount> Names{
    "sceNpSignalingActivateConnection", "sceNpSignalingDeactivateConnection",
    "sceNpMatching2JoinRoom", "sceNpMatching2CreateJoinRoom", "sceNpMatching2LeaveRoom",
    "sceNpSignalingGetConnectionStatus", "sceNpMatching2SignalingGetConnectionStatus",
    "sceNpMatching2SignalingGetPingInfo", "sceNpMatching2SearchRoom",
    "sceNpSignalingCreateContext", "sceNetRecvfrom", "sceNetSendto"};
// Channel owns send/receive/signaling-status. They have observations, never another swap.
struct ImportSlot { Fn function; U64 offset; };
constexpr std::array<ImportSlot,9> Slots{{
    {Activate,0x053E52B0},{Deactivate,0x053E5148},{Join,0x053E5198},
    {CreateJoin,0x053E5190},{Leave,0x053E51A0},{MatchingStatus,0x053E51D0},
    {Ping,0x053E5188},{Search,0x053E5170},{CreateContext,0x053E5120}}};
constexpr U64 Slot(Fn fn) {
    for (const auto& slot : Slots) if (slot.function==fn) return slot.offset;
    return 0;
}

inline std::atomic<bool> enabled{false}, started{false};
inline std::mutex mutex;
inline U64 serial = 0;
inline std::atomic<U64> getTotal{0}, createJoinTotal{0};
inline std::atomic<unsigned> activeWindows{0};
inline std::atomic<void (*)(U64)> joinCauseClose{nullptr};
inline bool everHost = false;
// Only accessed by SignalChanged while the existing mod log lock is held.
static std::unordered_map<U32,U32> signalStates;
struct Window {
    U64 id = 0, begin = 0, end = 0;
    S32 context = 0, connection = -1;
    const char* outcome = "timeout";
    const char* trigger = "activate";
    std::array<U64,FunctionCount> calls{};
    std::array<S32,4> previous{};
    bool sampled = false;
    U64 packets = 0, packetLines = 0;
};
inline std::vector<Window> windows;

inline U64 Now() {
#ifdef BBSC_J4_DIAG_TEST
    extern U64 testNow;
    return testNow;
#else
    return GetTickCount64();
#endif
}
inline U64 UtcMs() {
    FILETIME ft; GetSystemTimeAsFileTime(&ft);
    return ((U64(ft.dwHighDateTime)<<32)|ft.dwLowDateTime)/10000-11644473600000ULL;
}
inline void Log(const std::string& text) {
    BbscMod::LogPlain(("BBSC J4: "+text).c_str());
}
inline bool Readable(const void* pointer, size_t size) {
    auto address = reinterpret_cast<std::uintptr_t>(pointer);
    if (address < 0x10000 || size > UINTPTR_MAX-address) return false;
    const auto end = address+size;
    while (address < end) {
        MEMORY_BASIC_INFORMATION info{};
        constexpr DWORD flags = PAGE_READONLY|PAGE_READWRITE|PAGE_WRITECOPY|
            PAGE_EXECUTE_READ|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY;
        if (!VirtualQuery(reinterpret_cast<void*>(address),&info,sizeof(info)) ||
            info.State != MEM_COMMIT || !(info.Protect&flags) ||
            (info.Protect&(PAGE_GUARD|PAGE_NOACCESS))) return false;
        const auto next = reinterpret_cast<std::uintptr_t>(info.BaseAddress)+info.RegionSize;
        if (next <= address) return false;
        address = next < end ? next : end;
    }
    return true;
}
inline bool Read(const void* from, void* to, size_t size) {
#ifdef BBSC_J4_DIAG_TEST
    extern std::atomic<U64> testReads;
    ++testReads;
#endif
    SIZE_T received = 0;
    return Readable(from,size) && ReadProcessMemory(GetCurrentProcess(),from,to,size,&received) &&
           received == size;
}
template<class T> T Value(const void* pointer, T missing) {
    T result = missing;
    return Read(pointer,&result,sizeof(result)) ? result : missing;
}
inline U64 Manager() {
    const auto base = BbscMod::EbootAddress();
    return base ? Value(reinterpret_cast<void*>(base+Singleton),U64(0)) : 0;
}
inline S32 State(U64 manager) {
    return manager ? Value(reinterpret_cast<void*>(manager+0x124),S32(-1)) : -1;
}
inline std::array<S32,4> Fields(U64 manager) {
    std::array<S32,4> values;
    for (unsigned i=0;i<4;++i)
        values[i] = manager ? Value(reinterpret_cast<void*>(manager+0x118+4*i),S32(-1)) : -1;
    return values;
}
inline std::string Address(U64 address) {
    const auto base = BbscMod::EbootAddress();
    std::ostringstream out;
    if (base && address >= base && address-base < EbootSize)
        out << "eboot+0x" << std::hex << (address-base);
    else out << "0x" << std::hex << address;
    return out.str();
}
inline std::string Location(void* caller, void* stack) {
    std::ostringstream out;
    out << " caller=" << Address(reinterpret_cast<U64>(caller)) << " stack_candidates=[";
    unsigned found = 0;
    const auto base = BbscMod::EbootAddress();
    for (unsigned at=0;at<512 && found<4;at+=sizeof(U64)) {
        U64 value;
        if (!Read(reinterpret_cast<const char*>(stack)+at,&value,sizeof(value))) break;
        if (!base || value < base || value-base >= CodeSize) continue;
        if (found++) out << ',';
        out << Address(value);
    }
    out << ']';
    return out.str();
}
inline std::string Peer(const void* pointer) {
    std::array<unsigned char,16> bytes{};
    if (!Read(pointer,bytes.data(),bytes.size())) return "<unreadable>";
    std::string text;
    for (auto ch : bytes) {
        if (!ch) break;
        text += (ch>=32 && ch<127 && ch!='\\' && ch!='"') ? char(ch) : '?';
    }
    return text;
}
inline std::string Hex(const void* pointer, size_t size) {
    std::array<unsigned char,64> bytes{};
    if (size > bytes.size() || !Read(pointer,bytes.data(),size)) return "<unreadable>";
    std::ostringstream out;
    for (unsigned i=0;i<size;++i) out << std::hex << std::setfill('0') << std::setw(2) << unsigned(bytes[i]);
    return out.str();
}
inline void Summary(const Window& window, U64 now) {
    std::ostringstream out;
    out << "summary window=" << window.id << " outcome=" << window.outcome
        << " duration_ms=" << (now-window.begin) << " ctx=" << window.context
        << " connId=" << window.connection << " trigger=" << window.trigger << " ever_host=" << everHost
        << " summon_get_total=" << getTotal << " create_join_total=" << createJoinTotal
        << " net_lines=" << window.packetLines << " net_suppressed=" << (window.packets-window.packetLines)
        << " calls={";
    for (unsigned i=0;i<FunctionCount;++i) {
        if (i) out << ',';
        out << Names[i] << ':' << window.calls[i];
    }
    out << '}';
    Log(out.str());
}
// Caller holds mutex. End times are absolute and never extended by later events.
inline void Expire(U64 now) {
    for (auto it=windows.begin();it!=windows.end();) {
        if (now < it->end) { ++it; continue; }
        Summary(*it,it->end);
        if (const auto close=joinCauseClose.load(std::memory_order_acquire)) close(it->id);
        it=windows.erase(it);
    }
    activeWindows.store(unsigned(windows.size()),std::memory_order_release);
}
inline void ObserveHost(S32 value) {
    if (!everHost && (value==1 || value==3)) {
        everHost = true;
        Log("host-first t_ms="+std::to_string(Now())+" utc_ms="+std::to_string(UtcMs())+
            " state="+std::to_string(value));
    }
}
inline void Tick() noexcept {
    try {
        std::scoped_lock lock(mutex);
        if (!enabled.load()) return;
        const auto manager = Manager();
        ObserveHost(State(manager)); // Outside a window this is the only manager field read.
        const auto now = Now();
        Expire(now);
        if (windows.empty()) return;
        const auto values = Fields(manager);
        for (auto& w : windows) if (!w.sampled || w.previous != values) {
            std::ostringstream out;
            out << "sample window=" << w.id << " elapsed_ms=" << (now-w.begin)
                << " +118=" << values[0] << " +11c=" << values[1]
                << " +120=" << values[2] << " +124=" << values[3];
            Log(out.str());
            w.previous = values; w.sampled = true;
        }
    } catch (...) {} // Diagnostics must never change an imported function's result.
}
inline void ObserveHttpUrl(std::string_view url) noexcept {
    if (!enabled.load()) return;
    const auto path = url.substr(0,url.find_first_of("?#"));
    if (!path.ends_with("/summon_messenger/get")) return;
    ++getTotal;
}
struct Call {
    Fn function;
    std::vector<U64> ids;
    std::string location;
    bool detailed = false;
    Call(Fn fn, void* caller, void* stack, S32 ctx=0) noexcept : function(fn) {
        try {
            if (!enabled.load()) return;
            if (fn==CreateJoin) ++createJoinTotal;
            if (fn!=Activate && activeWindows.load(std::memory_order_acquire)==0) return;
            std::scoped_lock lock(mutex);
            const auto now = Now(); Expire(now);
            if (fn==Activate) {
                Window w; w.id=++serial; w.begin=now; w.end=now+MaximumMs; w.context=ctx;
                windows.push_back(w);
                activeWindows.store(unsigned(windows.size()),std::memory_order_release);
                Log("begin window="+std::to_string(w.id)+" t_ms="+std::to_string(now)+
                    " utc_ms="+std::to_string(UtcMs())+" state="+std::to_string(State(Manager())));
                ids.push_back(w.id);
            } else for (const auto& w : windows) ids.push_back(w.id);
            for (auto& w : windows)
                for (const auto id : ids) if (w.id==id) {
                    ++w.calls[fn];
                    detailed |= fn!=CreateJoin && ((fn!=Recv && fn!=Send) || w.packetLines<100);
                }
            if (detailed) location=Location(caller,stack);
        } catch (...) {}
    }
    void Complete(S32 result, const std::string& details, S32 conn=-1, S32 ctx=0) noexcept {
        try {
            if (function==CreateJoin) return; // Requested as a count-only observation.
            std::scoped_lock lock(mutex);
            const auto now = Now();
            for (auto& w : windows) {
                bool selected = false;
                for (const auto id : ids) selected |= w.id==id;
                if (!selected) continue;
                if (function==Activate) w.connection=conn;
                if (function==Join || (function==Deactivate && w.context==ctx && w.connection==conn)) {
                    if (function==Join || std::string_view(w.outcome)=="timeout")
                        w.outcome=function==Join ? "JoinRoom" : "Deactivate";
                    const auto tail=now+TailMs;
                    if (tail<w.end) w.end=tail;
                }
                if (function==Recv || function==Send) {
                    ++w.packets;
                    if (w.packetLines>=100) continue;
                    ++w.packetLines;
                }
                Log("call window="+std::to_string(w.id)+" function="+Names[function]+location+
                    " elapsed_ms="+std::to_string(now-w.begin)+" state="+std::to_string(State(Manager()))+
                    details+" result="+std::to_string(result));
            }
        } catch (...) {}
    }
};
// No diagnostic work may alter the host thread's last-error / errno seen by the game or NP.
struct Errors {
    DWORD win = GetLastError();
    int number = errno;
    void Restore() const { SetLastError(win); errno=number; }
};
inline void ObserveClaimReply(S32 request,U64 bytes,bool claimed,void* caller,void* stack) noexcept {
    if (!enabled.load(std::memory_order_relaxed) || !claimed) return;
    Errors preserved;
    try {
        std::scoped_lock lock(mutex);
        const auto now=Now();Expire(now);
        Window window;window.id=++serial;window.begin=now;window.end=now+MaximumMs;
        window.context=-1;window.trigger="claim";
        windows.push_back(window);
        activeWindows.store(unsigned(windows.size()),std::memory_order_release);
        Log("claim-delivered window="+std::to_string(window.id)+" request="+std::to_string(request)+
            " bytes="+std::to_string(bytes)+" state="+std::to_string(State(Manager()))+
            " t_ms="+std::to_string(now)+" utc_ms="+std::to_string(UtcMs())+Location(caller,stack));
    } catch (...) {}
    preserved.Restore();
}

using ActivateFn = S32 (__attribute__((sysv_abi)) *)(S32,const void*,S32*);
using DeactivateFn = S32 (__attribute__((sysv_abi)) *)(S32,S32);
using MatchFn = S32 (__attribute__((sysv_abi)) *)(U16,void*,void*,U32*);
using PingFn = S32 (__attribute__((sysv_abi)) *)(U16,const void*,const void*,S32*);
using MatchingFn = S32 (__attribute__((sysv_abi)) *)(U16,U64,U16,S32*,void*,U16*);
using ContextFn = S32 (__attribute__((sysv_abi)) *)(const void*,void*,void*,S32*);
inline ActivateFn originalActivate;
inline DeactivateFn originalDeactivate;
inline MatchFn originalJoin, originalCreateJoin, originalLeave, originalSearch;
inline PingFn originalPing;
inline MatchingFn originalMatching;
inline ContextFn originalContext;
#define J4_CALL(kind,...) Errors entry; Call call(kind,__builtin_return_address(0),__builtin_frame_address(0) __VA_OPT__(,) __VA_ARGS__); entry.Restore()
#define J4_RETURN(expr,details,...) const S32 result=(expr); Errors returned; try { if (call.detailed) call.Complete(result,details __VA_OPT__(,) __VA_ARGS__); else if (!call.ids.empty()) call.Complete(result,""); } catch (...) {} returned.Restore(); return result
#define J4_ABI __attribute__((sysv_abi,noinline))
S32 J4_ABI ActivateConnection(S32 ctx,const void* peer,S32* conn) {
    J4_CALL(Activate,ctx);
    J4_RETURN(originalActivate(ctx,peer,conn)," peer="+Peer(peer)+" ctx="+std::to_string(ctx)+
              " connId="+std::to_string(Value(conn,S32(-1))), Value(conn,S32(-1)),ctx);
}
S32 J4_ABI DeactivateConnection(S32 ctx,S32 conn) {
    J4_CALL(Deactivate);
    J4_RETURN(originalDeactivate(ctx,conn)," ctx="+std::to_string(ctx)+" connId="+std::to_string(conn),conn,ctx);
}
S32 J4_ABI JoinRoom(U16 ctx,void* request,void* options,U32* id) {
    J4_CALL(Join);
    const bool noWindow=enabled.load(std::memory_order_relaxed) && call.ids.empty();
    std::string details;
    try { if (call.detailed || noWindow)
        details=" ctx="+std::to_string(ctx)+" roomId="+
            std::to_string(Value(request,U64(0)))+" request40="+Hex(request,0x40);
    } catch (...) {}
    entry.Restore();
    const S32 result=originalJoin(ctx,request,options,id);
    Errors returned;
    try {
        if (noWindow) Log("joinroom-no-window"+details+" result="+std::to_string(result)+
                          " t_ms="+std::to_string(Now())+" utc_ms="+std::to_string(UtcMs()));
        if (call.detailed) call.Complete(result,details);
        else if (!call.ids.empty()) call.Complete(result,"");
    } catch (...) {}
    returned.Restore();
    return result;
}
S32 J4_ABI CreateJoinRoom(U16 ctx,void* request,void* options,U32* id) {
    J4_CALL(CreateJoin);
    J4_RETURN(originalCreateJoin(ctx,request,options,id)," ctx="+std::to_string(ctx));
}
S32 J4_ABI LeaveRoom(U16 ctx,void* request,void* options,U32* id) {
    J4_CALL(Leave);
    J4_RETURN(originalLeave(ctx,request,options,id)," ctx="+std::to_string(ctx)+
              " roomId="+std::to_string(Value(request,U64(0))));
}
S32 J4_ABI SearchRoom(U16 ctx,void* request,void* options,U32* id) {
    J4_CALL(Search);
    J4_RETURN(originalSearch(ctx,request,options,id)," ctx="+std::to_string(ctx));
}
S32 J4_ABI MatchingConnectionStatus(U16 ctx,U64 room,U16 member,S32* status,void* address,U16* port) {
    J4_CALL(MatchingStatus);
    J4_RETURN(originalMatching(ctx,room,member,status,address,port)," ctx="+std::to_string(ctx)+
        " roomId="+std::to_string(room)+" member="+std::to_string(member)+
        " status="+std::to_string(Value(status,S32(-1))));
}
S32 J4_ABI GetPingInfo(U16 ctx,const void* request,const void* options,S32* id) {
    J4_CALL(Ping);
    J4_RETURN(originalPing(ctx,request,options,id)," ctx="+std::to_string(ctx)+
              " roomId="+std::to_string(Value(request,U64(0))));
}
S32 J4_ABI SignalCreateContext(const void* peer,void* callback,void* arg,S32* ctx) {
    J4_CALL(CreateContext);
    const S32 result=originalContext(peer,callback,arg,ctx);
    Errors returned;
    try {
        if (enabled.load(std::memory_order_relaxed))
            Log("signaling-context-created result="+std::to_string(result)+
                " ctx="+std::to_string(result==0 ? Value(ctx,S32(-1)) : -1)+
                " t_ms="+std::to_string(Now())+" utc_ms="+std::to_string(UtcMs()));
        if (call.detailed) call.Complete(result," peer="+Peer(peer)+" ctx="+std::to_string(Value(ctx,S32(-1))));
    } catch (...) {}
    returned.Restore();
    return result;
}
inline U16 Vport(const void* address, U32 size) {
    if (size<10) return 0;
    const U16 raw=Value(reinterpret_cast<const char*>(address)+8,U16(0));
    return U16((raw>>8)|(raw<<8));
}
#undef J4_CALL
#undef J4_RETURN
#undef J4_ABI


// Cross-TU observations used only by the channel's existing game-facing wrappers.
// The channel's worker still uses its original functions and is not observed.
inline void* BeginChannel(Fn fn,void* caller,void* stack) noexcept {
    if (!enabled.load(std::memory_order_relaxed) ||
        activeWindows.load(std::memory_order_acquire)==0) return nullptr;
    Errors preserved;
    Call* call=nullptr;
    try { call=new Call(fn,caller,stack); } catch (...) {}
    preserved.Restore();
    return call;
}
void* BeginNet(bool sending,void* caller,void* stack) noexcept {
    return BeginChannel(sending ? Send : Recv,caller,stack);
}
void* BeginSignal(void* caller,void* stack) noexcept {
    return BeginChannel(SignalStatus,caller,stack);
}
void ObserveNet(void* ticket,S32 result,S32 socket,U64 length,const void* address,
                U32 addressLength,const U32* receivedLength) noexcept {
    auto* call=static_cast<Call*>(ticket);
    if (!call) return;
    Errors preserved;
    try {
        if (call->detailed) {
            const auto size=call->function==Recv ? Value(receivedLength,U32(0)) : addressLength;
            call->Complete(result," socket="+std::to_string(socket)+
                " vport="+std::to_string(Vport(address,size))+" length="+std::to_string(length));
        } else call->Complete(result,"");
    } catch (...) {}
    delete call;
    preserved.Restore();
}
struct SignalObservation { U32 connection, status; };
inline bool SignalChanged(void* opaque) {
    const auto& observed=*static_cast<const SignalObservation*>(opaque);
    const auto [it,inserted]=signalStates.try_emplace(observed.connection,observed.status);
    const bool becameActive=observed.status==2 && (inserted || it->second!=2);
    it->second=observed.status;
    return becameActive;
}
void ObserveSignalStatus(void* ticket,S32 result,S32 context,S32 connection,
                         const U32* status,const U16* port) noexcept {
    auto* call=static_cast<Call*>(ticket);
    if (!enabled.load(std::memory_order_relaxed) && !call) return;
    Errors preserved;
    try {
        if (!call && enabled.load(std::memory_order_relaxed))
            Log("signal-status-no-window ctx="+std::to_string(context)+" connId="+std::to_string(connection)+
                " result="+std::to_string(result)+" t_ms="+std::to_string(Now())+
                " utc_ms="+std::to_string(UtcMs()));
        if (enabled.load(std::memory_order_relaxed) && result==0) {
            const U32 value=Value(status,U32(-1));
            if (value!=U32(-1)) {
                SignalObservation observed{U32(connection),value};
                std::string text;
                if (value==2) text="BBSC J4: signaling-active ctx="+std::to_string(context)+
                    " connId="+std::to_string(connection)+" port="+std::to_string(Value(port,U16(0)))+
                    " t_ms="+std::to_string(Now())+" utc_ms="+std::to_string(UtcMs());
                BbscMod::LogJ4If(text.c_str(),&SignalChanged,&observed);
            }
        }
        if (call) call->Complete(result," ctx="+std::to_string(context)+" connId="+std::to_string(connection)+
            " status="+std::to_string(Value(status,U32(-1)))+
            " port="+std::to_string(Value(port,U16(0))));
    } catch (...) {}
    delete call;
    preserved.Restore();
}
inline bool WrappedByMod(U64 address) {
    HMODULE module{};
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&WrappedByMod),&module)) return true;
    MEMORY_BASIC_INFORMATION info{};
    return VirtualQuery(reinterpret_cast<void*>(address),&info,sizeof(info))!=0 &&
           info.AllocationBase==module;
}

template<class T> void Swap(U64 offset,T wrapper,T& original,const char* name) {
    const auto base=BbscMod::EbootAddress();
    auto* slot=reinterpret_cast<U64*>(base+offset);
    for (unsigned attempt=0;attempt<60000;++attempt) {
        const auto value=Value(slot,U64(0));
        if (value && (value<base || value-base>=EbootSize)) {
            if (WrappedByMod(value)) {
                Log(std::string("skipped: already wrapped by mod function=")+name);
                return;
            }
            DWORD before=0;
            if (!VirtualProtect(slot,sizeof(U64),PAGE_READWRITE,&before)) {
                Log(std::string("install failed function=")+name); return;
            }
            original=reinterpret_cast<T>(value);
            *slot=reinterpret_cast<U64>(wrapper);
            DWORD ignored; VirtualProtect(slot,sizeof(U64),before,&ignored);
            BbscMod::GuardSlot(offset,reinterpret_cast<U64>(wrapper),
                              reinterpret_cast<void**>(&original),name);
            Log(std::string("installed function=")+name+" slot="+Address(base+offset));
            return;
        }
        Sleep(1);
    }
    Log(std::string("slot unavailable function=")+name);
}
inline void InstallSlots() {
    Swap(Slot(Activate),&ActivateConnection,originalActivate,Names[Activate]);
    Swap(Slot(Deactivate),&DeactivateConnection,originalDeactivate,Names[Deactivate]);
    Swap(Slot(Join),&JoinRoom,originalJoin,Names[Join]);
    Swap(Slot(CreateJoin),&CreateJoinRoom,originalCreateJoin,Names[CreateJoin]);
    Swap(Slot(Leave),&LeaveRoom,originalLeave,Names[Leave]);
    Swap(Slot(MatchingStatus),&MatchingConnectionStatus,originalMatching,Names[MatchingStatus]);
    Swap(Slot(Ping),&GetPingInfo,originalPing,Names[Ping]);
    Swap(Slot(Search),&SearchRoom,originalSearch,Names[Search]);
    Swap(Slot(CreateContext),&SignalCreateContext,originalContext,Names[CreateContext]);
}
inline bool EnableFile(const std::filesystem::path& file) {
    const DWORD attrs=GetFileAttributesW(file.c_str());
    return attrs!=INVALID_FILE_ATTRIBUTES && !(attrs&FILE_ATTRIBUTE_DIRECTORY);
}
inline void Start(const std::filesystem::path& file) noexcept {
    try {
        if (!EnableFile(file) || started.exchange(true)) return;
        enabled.store(true);
        std::thread([] {
            try { InstallSlots(); } catch (...) { Log("slot installation stopped by diagnostic error"); }
            for (;;) { Tick(); Sleep(100); }
        }).detach();
    } catch (...) {}
}
inline void Start() noexcept {
    try {
        HMODULE module{};
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                              reinterpret_cast<LPCWSTR>(&InstallSlots),&module)) return;
        std::array<wchar_t,32768> path{};
        const auto count=GetModuleFileNameW(module,path.data(),DWORD(path.size()));
        if (!count || count>=path.size()) return;
        Start(std::filesystem::path(path.data()).parent_path()/L"j4-diag.enable");
    } catch (...) {}
}
} // namespace BbscJ4Diag
