"""Fail closed when generated import-slot ownership overlaps J4's own swaps."""
from pathlib import Path
import json
import re
import sys

root=Path(sys.argv[1])
report=Path(sys.argv[2])
diag=root/'mod/bbsc_j4_diag.inc'
text=diag.read_text('utf-8')
enum=re.search(r'enum Fn.*?\{(.*?)FunctionCount',text,re.S).group(1)
functions=[s.strip() for s in enum.split(',') if s.strip()]
old=re.search(r'array<U64, FunctionCount> Slots\{(.*?)\};',text,re.S)
if old:
    values=[int(n,16) for n in re.findall(r'0x[0-9A-Fa-f]+',old.group(1))]
    mapping=dict(zip(functions,values))
    own=[mapping[name] for name in re.findall(r'\bSwap\(Slots\[(\w+)\]',text)]
else:
    mapping={name:int(value,16) for name,value in re.findall(r'\{(\w+),\s*(0x[0-9A-Fa-f]+)\}',text)}
    own=[mapping[name] for name in re.findall(r'\bSwap\(Slot\((\w+)\)',text)]
assert own and len(own)==len(set(own)), 'Missing or duplicate diagnostic slot registration'
existing=[]
unresolved=[]
for folder in (root/'mod',root/'src/core'):
    for path in sorted(folder.rglob('*')):
        if path.suffix not in ('.cpp','.h','.inc') or path==diag:
            continue
        source=path.read_text('utf-8',errors='replace')
        constants={n:int(v,16) for n,v in re.findall(r'constexpr\s+(?:u64|U64)\s+(\w+)\s*=\s*(0x[0-9a-fA-F]+)',source)}
        for call in re.finditer(r'\b(Swap(?:<[^;{}]+?>)?|GuardSlot)\s*\(\s*([^,\n]+),',source):
            kind,arg=call[1],call[2].strip()
            # Function declarations and generic forwarding sites are not concrete registrations.
            if arg in ('u64 slot_offset','std::uint64_t','slot_offset','offset'):
                continue
            if arg in constants: slot=constants[arg]
            elif re.fullmatch(r'0x[0-9a-fA-F]+(?:ULL)?',arg): slot=int(arg.removesuffix('ULL'),16)
            else:
                unresolved.append(dict(file=path.relative_to(root).as_posix(),call=call[0]))
                continue
            existing.append(dict(file=path.relative_to(root).as_posix(),kind=kind,arg=arg,slot=slot))
overlap=sorted(set(own)&{r['slot'] for r in existing})
result=dict(diagnostic_slots=[f'0x{x:08X}' for x in own],existing_registrations=existing,
            overlap=[f'0x{x:08X}' for x in overlap],unresolved=unresolved)
report.write_text(json.dumps(result,indent=2)+'\n','utf-8')
print(f'Enumerated {len(existing)} existing registrations and {len(own)} diagnostic registrations.')
print('Overlaps:',result['overlap'])
assert not unresolved, f'Unresolved slot registration: {unresolved}'
assert not overlap, 'Diagnostic slots overlap existing mod wrappers'


if len(sys.argv)>3:
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from prepare_game_hardening_regression import function
    channel=(root/'mod/bbsc_channel.cpp').read_text('utf-8')
    preamble = """
namespace J4ChannelFixture {
using s32=std::int32_t;using u32=std::uint32_t;using u16=std::uint16_t;using u64=std::uint64_t;
constexpr u64 EbootSize=0x056D4000;
constexpr u32 SignalingConnActive=2;
constexpr u64 SlotSendto=0x053E4F48,SlotRecvfrom=0x053E4F58,SlotConnectionStatus=0x053E52D0;
using SendtoFn=decltype(&MockSend);using RecvfromFn=decltype(&MockRecv);using ConnectionStatusFn=decltype(&MockSignal);
inline SendtoFn original_sendto=nullptr;
inline RecvfromFn original_recvfrom=nullptr;
inline ConnectionStatusFn original_connection_status=nullptr;
inline std::atomic<s32> channel_socket{-1};
inline void NoteGameAddress(const void*,u32,const char*){}
inline void NotePeer(u32,u16,const char*){}
"""
    bodies=[function(channel,m) for m in (
        's32 PS4_SYSV_ABI Sendto(', 's32 PS4_SYSV_ABI Recvfrom(',
        's32 PS4_SYSV_ABI ConnectionStatus(')]
    bodies.append('template <typename Fn>\n'+function(channel,'bool Swap(u64 slot_offset,'))
    setup="""
inline void Install() {
    assert(Swap<SendtoFn>(SlotSendto,&Sendto,original_sendto,"Sendto"));
    assert(Swap<RecvfromFn>(SlotRecvfrom,&Recvfrom,original_recvfrom,"Recvfrom"));
    assert(Swap<ConnectionStatusFn>(SlotConnectionStatus,&ConnectionStatus,original_connection_status,"ConnectionStatus"));
}
} // namespace J4ChannelFixture
"""
    Path(sys.argv[3]).write_text(preamble+'\n'.join(bodies)+setup,'utf-8')
    print('Extracted all three actual generated channel wrappers and their real Swap implementation.')

    http=(root/"mod/bbsc_http.cpp").read_text("utf-8")
    http_preamble='\nnamespace J4HttpFixture {\nusing s32=std::int32_t;using s64=std::int64_t;using u64=std::uint64_t;\nstatic std::mutex state_mutex;\nstatic std::unordered_map<int,std::string> request_urls;\nstatic std::atomic<s64> claim_delivered_ms{0};\nstatic s64 NowMs() {return BbscJ4Diag::Now();}\nstatic bool IsSummon(const std::string&) {return false;}\nstatic bool IsPublicServer(const std::string&) {return false;}\nstatic bool IsSummonCreate(const std::string& url) {return url.ends_with("/summon_messenger/create");}\nstatic int PS4_SYSV_ABI MockRead(int request,void* data,u64 size) {\n    arguments={U64(request),Word(data),size};return Result();\n}\nstatic auto original_read_data=&MockRead;\nstatic std::optional<int> CleanupRequestResult(int) {return {};}\n#define LOG_INFO(...) ((void)0)\n'
    Path(sys.argv[3]).with_name("j4_http_fixture.inc").write_text(http_preamble+function(http,"int PS4_SYSV_ABI ReadData(")+"\n#undef LOG_INFO\n}\n","utf-8")
    print("Extracted actual HTTP ReadData for claim-delivery regression.")
