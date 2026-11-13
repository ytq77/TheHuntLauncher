"""Re-extract real production prompt paths for isolated regression tests.
No source/install edits. The optional overrides are only for an unmerged draft.
"""
from pathlib import Path
import argparse,hashlib,json,sys
p=argparse.ArgumentParser()
p.add_argument('--source',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--core',type=Path)
p.add_argument('--patches',type=Path)
p.add_argument('--mode',choices=('retained','death','all'),default='all')
p.add_argument('--elf',type=Path,default=Path(r'C:\bbsc-dev\experiment\baseline\out\eboot.elf'))
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(a.source/'scripts'))
from prepare_game_hardening_regression import function
corepath=a.core or a.source/'.build/bbsc/emulator/src/core/bloodborne_re_zh_cn.cpp'
patches=a.patches or a.source/'patches'
core=corepath.read_text('utf-8');elf=a.elf.read_bytes()
if hashlib.sha256(elf).hexdigest()!='cec1b276e7f9e4db978e57f524f41fbaac594530a3437b002e23f3fab14b4f86':
 raise ValueError('Native fixture requires verified CUSA03173 1.09 ELF')
meta={'core':str(corepath),'core_sha256':hashlib.sha256(corepath.read_bytes()).hexdigest(),'files':{}}
def copy(source,dest):
 data=source.read_bytes();(a.output/dest).write_bytes(data);meta['files'][str(source)]=hashlib.sha256(data).hexdigest()
def native(name,spans):
 parts=[]
 for ident,lo,hi in spans:
  b=elf[lo+0x4000:hi+0x4000]
  parts.append('constexpr unsigned char '+ident+'[]{'+','.join(f'0x{x:02x}' for x in b)+'};')
 (a.output/name).write_text('\n'.join(parts),encoding='utf-8')
for header in ('bbsc_visibility_wire.h','bbsc_relay_auth_guard.h'):
 copy(a.source/'patches'/header,header.replace('.h','.snapshot.h'))
root=Path(__file__).resolve().parents[1]
if a.mode in ('retained','all'):
 assert '#include "bbsc_retained_join.inc"' in core and 'QueueBbscRetainedJoinLine(task);' in core
 for name in ('bbsc_retained_join.inc','bbsc_visibility_wire.h','bbsc_relay_auth_guard.h'):
  copy(patches/name,name)
 (a.output/'accessors.inc').write_text('\n\n'.join(function(core,marker) for marker in (
  's32 GetLocalPlayerType()', 'std::vector<u64> GetMultiPlayerInsTasks()',
  'bool IsValidMemberId(', 'bool RemotePlayerExists(')),encoding='utf-8')
 (a.output/'reinsert.inc').write_text(function(core,'void ArmBbscReinsertV8() {')+'\n\n'+function(core,'void TickBbscReinsertV8() {'),encoding='utf-8')
 (a.output/'role.inc').write_text(function((patches/'bbsc_load_protection.inc').read_text('utf-8'),
  'u64 BbscLoadProtectionRoleEpoch()'),encoding='utf-8')
 relaypath=a.source/'.build/bbsc/emulator/mod/bbsc_relay.cpp'
 relay=relaypath.read_text('utf-8')
 meta['files'][str(relaypath)]=hashlib.sha256(relaypath.read_bytes()).hexdigest()
 (a.output/'lease.inc').write_text(function(relay,'bool RelayAuthVisibilityContext(')+'\n\n'+
  function(relay,'bool RelayAuthUseVisibilityContext('),encoding='utf-8')
 (a.output/'trace_hook.inc').write_text('void TraceHostLoad(const GuestRegisterSnapshot* registers) {\n'
  '    const struct { u64 offset; } site{0x01938F65};\n'+
  function(core,'if (site.offset == 0x01938F65) {')+'\n}\n',encoding='utf-8')
 (a.output/'event_text.inc').write_text(function((patches/'bbsc_banner_diagnostics.inc').read_text('utf-8'),
  'void PS4_SYSV_ABI BbscEventTextHook('),encoding='utf-8')
 copy(root/'tests/retained_join_runtime.cpp.in','test_retained_join.cpp')
 native('native_bytes.inc',[
  ('NativeHostLoad',0x1938de0,0x193929c), ('NativeJoin',0x196ba20,0x196be35),
  ('NativeMemberEquals',0xca1c70,0xca1d14), ('NativeOwnRole',0x1942216,0x1942272),
  ('NativeSetState',0x1e52560,0x1e5264c)])
if a.mode in ('death','all'):
 assert 'redundant guest Lua death event omitted' in core
 copy(patches/'bbsc_death_ui.inc','bbsc_death_ui.snapshot.inc')
 (a.output/'death_raise.snapshot.inc').write_text(function(core,'bool RaiseBbscDeathText(')+'\n'+function(core,'void ShowBbscGuestDeathText('),encoding='utf-8')
 copy(root/'tests/death_local_prompt_runtime.cpp.in','test_death_local_prompt.cpp')
 native('native_death_ui.inc',[('NativeFirstDeath',0x1381587,0x138160f),('NativeSetEffect',0x13360b0,0x13360ff)])
(a.output/'inputs.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
print('Extracted canonical production paths:',a.mode)
