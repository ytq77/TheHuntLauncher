"""Create a separate pinned current BBSC tree with opt-in read-only lamp observation."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def once(text,before,after):
    if text.count(before)!=1:raise ValueError('Unexpected current source marker: '+before[:80])
    return text.replace(before,after,1)

def scope(text,signature,label):
    return once(text,signature,signature+'\n    LampTraceScope lamp_trace{"'+label+'"};')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--upstream',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();tree=args.output.resolve();upstream=args.upstream.resolve()
    if tree.exists() or tree==upstream or upstream in tree.parents:raise ValueError('Output must be a new independent directory')
    lock=json.loads((ROOT/'upstream.lock.json').read_text('utf-8'))
    payload=subprocess.check_output(['git','-c','safe.directory='+upstream.as_posix(),'-C',str(upstream),'archive',lock['bbsc']['commit']])
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(payload)) as bundle:bundle.extractall(tree,filter='data')
    for name in ('prepare_sources.py','localize.py','prepare_session_recovery.py','prepare_personal_rewards.py'):
        subprocess.run([sys.executable,'-B',str(ROOT/'scripts'/name),str(tree)],check=True)
    core=tree/'emulator/src/core';path=core/'bloodborne_re_zh_cn.cpp';source=path.read_text('utf-8')
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in core.iterdir() if p.is_file() and (p.name.startswith('bbsc_') or p.name=='bloodborne_re_zh_cn.cpp')}
    source=once(source,'void CaptureBbscOwnWorldBeforeJoin();','''void LampTraceTick();
void LampTraceCachedSave();
struct LampTraceScope {
    const char* label;std::string before;s64 sampled=0;u64 operation=0;
    explicit LampTraceScope(const char* name) noexcept;
    ~LampTraceScope() noexcept;
};
void CaptureBbscOwnWorldBeforeJoin();''')
    source=once(source,'bool BbscIsMapFlagGroup(u32 key) {','#include "bbsc_lamp_trace.inc"\n\nbool BbscIsMapFlagGroup(u32 key) {')
    for signature,label in (
        ('bool ApplyBbscHostWorldFlags(u32 packed_map, std::string_view why) {','world-apply'),
        ('void ApplyBbscHostDreamState(std::string_view why) {','dream-apply'),
        ('void RestoreBbscOwnDreamState(std::string_view why) {','dream-restore'),
        ('void ApplyBbscHostWorldFlagsAtLoad() {','load-apply'),
        ('void CaptureBbscOwnWorldBeforeJoin() {','prejoin'),
        ('bool LoadBbscOwnWorldFile(const BbscSaveSlot& slot) {','journal-load'),
    ):source=scope(source,signature,label)
    source=once(source,'        TickBbscPvp();','        TickBbscPvp();\n        LampTraceTick();')
    path.write_text(source,encoding='utf-8',newline='\n')
    recovery=core/'bbsc_world_recovery.inc'
    recovery.write_text(scope(recovery.read_text('utf-8'),'bool BbscRestoreWorldTransaction(std::string_view why) {','world-restore'),encoding='utf-8',newline='\n')
    proof=core/'bbsc_save_proof.inc'
    proof.write_text(once(proof.read_text('utf-8'),'void PS4_SYSV_ABI BbscSaveProofEntry(u64, const GuestRegisterSnapshot*) {','void PS4_SYSV_ABI BbscSaveProofEntry(u64, const GuestRegisterSnapshot*) {\n    LampTraceCachedSave();'),encoding='utf-8',newline='\n')
    (core/'bbsc_lamp_trace.inc').write_bytes((ROOT/'patches/bbsc_lamp_trace.inc').read_bytes())
    flags=json.loads((ROOT/'scripts/lamp_trace/flags.json').read_text('utf-8'))['flags']
    groups=sorted({flag//1000 for flag in flags})
    (core/'bbsc_lamp_trace_groups.h').write_text('constexpr std::array<u32,'+str(len(groups))+'> LampTraceGroups{'+','.join(map(str,groups))+'};\n',encoding='utf-8')
    report={'bbsc_commit':lock['bbsc']['commit'],'base_version':lock['version'],'current_prepared_sha256':before,'diagnostic_core_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'lamp_flags':flags,'stages':['world-apply','dream-apply','dream-restore','load-apply','prejoin','journal-load','world-restore'],'writes_game_flags_added':False,'runtime_tested':False}
    (tree/'lamp-trace-source.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='current_prepared_sha256'},indent=2))

if __name__=='__main__':main()
