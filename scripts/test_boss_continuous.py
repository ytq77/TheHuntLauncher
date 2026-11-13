"""Production-extracted continuous multi-life Boss regressions.

Only independent chains reset state. Lives within a chain retain state. Native
HP/curtain/load completion is an explicit mock boundary, not game execution.
"""
import argparse, hashlib, json, os, re, subprocess
from pathlib import Path
from test_death_entry_paths import generate as generate_death
from prepare_game_hardening_regression import function

def digest(data): return hashlib.sha256(data).hexdigest()

def generate(root):
    root=Path(root)
    core_path=root/'.build/bbsc/emulator/src/core/bloodborne_re_zh_cn.cpp'
    cases_path=root/'tests/boss_continuous.cpp.in'
    core=core_path.read_text('utf-8-sig')
    base,provenance=generate_death(root)
    marker='GuestRegisterSnapshot registers{};'
    if base.count(marker)!=1: raise ValueError('Unexpected original fixture boundary')
    head=base[:base.index(marker)]
    constants=[]
    for name in ('BbscLifeFreshMs','BbscBossUnlockMs'):
        matches=re.findall(r'^constexpr s64 '+name+r' = [^;\n]+;',core,re.M)
        if len(matches)!=1: raise ValueError('Missing/duplicate constant '+name)
        constants.append(matches[0])
    extras_head=EXTRA_HEAD.replace('@CONSTANTS@','\n'.join(constants)).replace('@LIFE_STRUCT@',function(core,'struct BbscPeerLife {')+';')
    markers=('void OnBbscLifePacket(', 'bool BbscSessionAlive() {','void PS4_SYSV_ABI BbscBossClearHook(')
    bodies=[function(core,m) for m in markers]
    provenance['additional_production_functions']={m:digest(b.encode()) for m,b in zip(markers,bodies)}
    provenance['input_sha256'][str(cases_path)]=digest(cases_path.read_bytes())
    provenance['scope']='Continuous same-state routing with real production life packet and boss-clear callbacks; explicit synthetic HP/curtain/loading/replacement boundaries. Not native rendering, save, protection, damage or two-game execution.'
    return head+extras_head+'\n'.join(bodies)+'\n'+cases_path.read_text('utf-8-sig'),provenance

EXTRA_HEAD = r'''
#include <map>
#include <mutex>
@CONSTANTS@
@LIFE_STRUCT@
std::mutex bbsc_life_mutex;
std::map<u32,BbscPeerLife> bbsc_peer_life;
std::atomic<u32> bbsc_life_own_id{0};
int invalidations=0;
void InvalidateBbscHostDisplayLife(u32){++invalidations;}
void ArmBbscDeathLockClear(){}
s32 LocalPlayerType(){return session_state==3?0:1;}
u8 bbsc_boss_leave_skip=0,bbsc_boss_home_skip=0;
s64 bbsc_boss_unlock_until_ms=0;
'''

def run(root,output,compiler):
    cpp,manifest=generate(root)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    source=output/'boss_continuous.cpp';source.write_text(cpp,'utf-8',newline='\n')
    exe=output/('boss_continuous.exe' if os.name=='nt' else 'boss_continuous')
    env=os.environ.copy()
    if Path(compiler).is_absolute(): env['PATH']=str(Path(compiler).parent)+os.pathsep+env.get('PATH','')
    build=subprocess.run([compiler,'-std=c++23','-O1',str(source),'-o',str(exe)],capture_output=True,text=True,env=env)
    (output/'compile.log').write_text(build.stdout+build.stderr,'utf-8');build.check_returncode()
    result=subprocess.run([str(exe.resolve())],capture_output=True,text=True,env=env,timeout=45)
    (output/'run.log').write_text(result.stdout+result.stderr,'utf-8')
    print(result.stdout+result.stderr,end='',flush=True)
    for path,expected in manifest['input_sha256'].items():
        if digest(Path(path).read_bytes())!=expected: raise RuntimeError('Input changed: '+path)
    manifest.update(exit_code=result.returncode,generated_sha256=digest(source.read_bytes()),executable_sha256=digest(exe.read_bytes()))
    (output/'results.json').write_text(json.dumps(manifest,indent=2),'utf-8')
    result.check_returncode()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--compiler',default='clang++')
    args=parser.parse_args();run(args.root.resolve(),args.output.resolve(),args.compiler)
