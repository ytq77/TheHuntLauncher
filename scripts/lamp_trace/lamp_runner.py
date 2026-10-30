"""Private WPF worker adapter. All evidence is written under its isolated clone."""
from datetime import datetime
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import threading
import time
import zipfile
from lamp_analysis import disk_flags,summarize_case

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def stable(path):
    first=Path(path).read_bytes()
    if first!=Path(path).read_bytes():raise ValueError('Save is being written; retry after normal exit')
    return first

@contextmanager
def exclusive(root):
    with (Path(root)/'.lamp-operation.lock').open('a+b') as stream:
        if stream.tell()==0:stream.write(b'1');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ValueError('Diagnostic worker or collection is still running; wait for normal completion') from None
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

def case_config(root):
    root=Path(root).resolve()
    if (root/'LAMP-INCOMPLETE').exists():raise ValueError('Diagnostic clone preparation is incomplete')
    cfg=json.loads((root/'LAMP-CASE.json').read_text('utf-8'))
    if cfg.get('schema')!=1 or cfg.get('role') not in ('Host02','Guest01') or type(cfg.get('slot')) is not int or not 0<=cfg['slot']<10:raise ValueError('Invalid diagnostic role')
    for name,expected in cfg['program_sha256'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or sha(path)!=expected:raise ValueError('Diagnostic program changed: '+name)
    return cfg

def snapshot(root,folder,slot):
    folder.mkdir(parents=True,exist_ok=False)
    saves=Path(root)/'data/user/savedata/1/CUSA00207/SPRJ0005'
    for stem in ('userdata','backup'):(folder/f'{stem}{slot:04}').write_bytes(stable(saves/f'{stem}{slot:04}'))

def observe(root,folder,slot,mapping,stop):
    previous={};count=0
    saves=Path(root)/'data/user/savedata/1/CUSA00207/SPRJ0005'
    with (folder/'disk-observations.jsonl').open('w',encoding='utf-8') as output:
        while not stop.is_set():
            for stem in ('userdata','backup'):
                path=saves/f'{stem}{slot:04}'
                try:
                    raw=stable(path);digest=hashlib.sha256(raw).hexdigest()
                    if previous.get(stem)==digest:continue
                    values=disk_flags(raw,mapping);previous[stem]=digest;count+=1
                    row={'epoch_ms':time.time_ns()//1000000,'file':path.name,'sha256':digest,'flags':values,'enabled':sum(v is True for v in values.values())}
                    if count<=256:
                        row['snapshot']=f'disk-{count:03}-{path.name}.bin';(folder/row['snapshot']).write_bytes(raw)
                    else:row['snapshot_limit_reached']=True
                    output.write(json.dumps(row)+'\n');output.flush()
                except (OSError,ValueError) as error:
                    output.write(json.dumps({'epoch_ms':time.time_ns()//1000000,'file':path.name,'read_error':str(error)})+'\n');output.flush()
            stop.wait(.5)

def run(root):
    case_config(root)
    with exclusive(root):return _run(root)

def _run(root):
    root=Path(root).resolve();cfg=case_config(root)
    spec=importlib.util.spec_from_file_location('_lamp_original_client',root/'client-original.py')
    client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)
    client.require_stopped()
    if (root/'dream-evidence').exists():raise ValueError('This is a one-run diagnostic clone; collect its evidence and create a fresh clone')
    profile=json.loads((root/'profile.json').read_text('utf-8-sig'))
    if sha(Path(profile['game'])/'eboot.bin')!=cfg['eboot_sha256']:raise ValueError('Game executable is not the verified CUSA03173 1.09 input')
    # The seeded save branch is already present. Copy ordinary mods/settings only;
    # never replace it with the ordinary install's current savedata.
    for name in ('mods','mods.json','input.ini'):
        source=Path(profile['install'])/name;target=root/'data'/name
        if source.is_dir():shutil.copytree(source,target)
        elif source.is_file():shutil.copy2(source,target)
    mapping=json.loads((root/'lamp-mapping.json').read_text('utf-8'))
    folder=root/'dream-evidence'/datetime.now().strftime('%Y%m%d-%H%M%S');folder.mkdir(parents=True)
    snapshot(root,folder/'before',cfg['slot'])
    for stem in ('userdata','backup'):
        name=f'{stem}{cfg["slot"]:04}'
        if sha(folder/'before'/name)!=cfg['baseline_sha256'][name]:raise ValueError('Diagnostic baseline changed before first launch')
    (folder/'case.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
    os.environ['THEHUNT_LAMP_TRACE']='1';os.environ['THEHUNT_LAMP_TRACE_PATH']=str(folder/'native-lamps.jsonl')
    stop=threading.Event();watcher=threading.Thread(target=observe,args=(root,folder,cfg['slot'],mapping,stop),daemon=True);watcher.start()
    result=None
    try:
        result=client.main();return result
    finally:
        stop.set();watcher.join(timeout=5)
        if watcher.is_alive():raise ValueError('Disk observer did not stop; keep this incomplete evidence folder')
        snapshot(root,folder/'after',cfg['slot'])
        for name in ('startup.log','launcher-state.json','data/user/last-exit.json'):
            path=root/name
            if path.is_file():shutil.copy2(path,folder/path.name)
        (folder/'runner-result.json').write_text(json.dumps({'exit_code':result,'runtime_accepted':False}),encoding='utf-8')
        (folder/'lamp-summary.json').write_text(json.dumps(summarize_case(folder,mapping),ensure_ascii=False,indent=2),encoding='utf-8')

def collect(root):
    case_config(root)
    with exclusive(root):return _collect(root)

def _collect(root):
    root=Path(root).resolve();cfg=case_config(root)
    # Collect only named diagnostic outputs, never profile, credentials or the server database.
    files=[]
    for folder in (root/'dream-evidence').glob('*'):
        if not folder.is_dir():continue
        for path in folder.rglob('*'):
            relative=path.relative_to(folder)
            permitted=(len(relative.parts)==1 and (path.name in {'case.json','native-lamps.jsonl','disk-observations.jsonl','runner-result.json','lamp-summary.json','startup.log','launcher-state.json','last-exit.json'} or path.name.startswith('disk-') and path.suffix=='.bin') or len(relative.parts)==2 and relative.parts[0] in {'before','after'} and path.name in {f'{stem}{cfg["slot"]:04}' for stem in ('userdata','backup')})
            if permitted and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):files.append(path)
    if not files:raise ValueError('No diagnostic session to collect')
    destination=root/('Lamp-Evidence-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):archive.write(path,path.relative_to(root).as_posix())
    return destination

def main(root=None):
    root=Path(root or Path(__file__).resolve().parent)
    try:return run(root)
    except Exception as error:
        message=str(error)
        state=root/'launcher-state.json'
        try:
            previous=json.loads(state.read_text('utf-8'))
            if previous.get('phase')=='error' and previous.get('message'):message=previous['message']+'\n诊断：'+message
        except (OSError,ValueError):pass
        (root/'launcher-state.json').write_text(json.dumps({'phase':'error','message':message},ensure_ascii=False),encoding='utf-8')
        print(message,flush=True);return 1

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['collect','summarize']);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args()
    if args.action=='collect':print(collect(args.root))
    else:
        cfg=case_config(args.root);mapping=json.loads((args.root/'lamp-mapping.json').read_text('utf-8'))
        for folder in sorted((args.root/'dream-evidence').glob('*')):
            if folder.is_dir():print(json.dumps(summarize_case(folder,mapping),ensure_ascii=False,indent=2))
