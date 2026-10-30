"""Create a new private observation clone from a verified preview.17 release ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

HERE=Path(__file__).resolve().parent

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def safe_entries(archive,prefix=''):
    result={}
    for item in archive.infolist():
        if item.is_dir():continue
        name=item.orig_filename
        if not name.startswith(prefix):raise ValueError('Archive root differs')
        relative=name[len(prefix):];parts=relative.split('/')
        if not relative or relative.startswith('/') or '\\' in relative or ':' in relative or '\x00' in relative or any(part in ('','.','..') or part.endswith(('.', ' ')) for part in parts) or (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe archive path')
        if relative.casefold() in {key.casefold() for key in result}:raise ValueError('Duplicate archive entry')
        result[relative]=item
    return result

def prepare(release,destination,role,kit=HERE):
    release=Path(release).resolve();destination=Path(destination).resolve();kit=Path(kit).resolve()
    settings=json.loads((kit/'kit.json').read_text('utf-8'))
    if role not in ('Host02','Guest01'):raise ValueError('Unknown diagnostic role')
    if destination.exists() or not destination.name.startswith('TheHunt-Lamp-') or destination.is_relative_to(kit):raise ValueError('Use a new independent TheHunt-Lamp-* directory')
    if sha(release)!=settings['release_sha256']:raise ValueError('Release ZIP is not the verified preview.17 full package')
    for name,expected in settings['files'].items():
        path=(kit/name).resolve()
        if not path.is_relative_to(kit) or sha(path)!=expected:raise ValueError('Diagnostic kit changed: '+name)
    seed=settings['roles'][role]
    with zipfile.ZipFile(kit/seed['archive']) as seed_zip:
        seed_files=safe_entries(seed_zip)
        if set(seed_files)!=set(seed['inventory']):raise ValueError('Seed inventory mismatch')
        for name,expected in seed['inventory'].items():
            if hashlib.sha256(seed_zip.read(seed_files[name])).hexdigest()!=expected:raise ValueError('Seed bytes changed')
    destination.mkdir(parents=True)
    marker=destination/'LAMP-INCOMPLETE';marker.write_text('Do not launch until preparation completes.',encoding='utf-8')
    with zipfile.ZipFile(release) as archive:
        entries=safe_entries(archive,'TheHuntLauncher-0.1.0-preview.17-windows-x64/')
        manifest=json.loads(archive.read(entries['file-sha256.json']))
        if set(entries)!=set(manifest)|{'file-sha256.json'}:raise ValueError('Release manifest does not cover package')
        # This observation clone uses the already-running service. Omit its own
        # bundled server and hide that navigation entry to avoid an accidental second server.
        for name,item in entries.items():
            if name.startswith('server/'):continue
            raw=archive.read(item)
            if name in manifest and hashlib.sha256(raw).hexdigest()!=manifest[name]:raise ValueError('Release file hash mismatch')
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    with zipfile.ZipFile(kit/seed['archive']) as archive:
        for name,item in safe_entries(archive).items():
            target=destination/'data/user/savedata'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(item))
    for name in ('lamp_runner.py','lamp_analysis.py','lamp-mapping.json'):
        shutil.copy2(kit/name,destination/name)
    shutil.copy2(kit/'bbsc.dll',destination/'mod/bbsc.dll')
    (destination/'client.py').rename(destination/'client-original.py')
    (destination/'client.py').write_text('from pathlib import Path\nfrom lamp_runner import main\nif __name__ == "__main__":\n    raise SystemExit(main(Path(__file__).resolve().parent))\n',encoding='utf-8')
    xaml=(destination/'Launcher.xaml').read_text('utf-8-sig')
    if xaml.count('Title="The Hunt"')!=1:raise ValueError('Unexpected WPF title')
    xaml=xaml.replace('Title="The Hunt"','Title="The Hunt · LAMP TRACE · '+role+'"',1)
    xaml=xaml.replace('Text="0.1.0-preview.17"','Text="preview.17 · LAMP TRACE"',1)
    for name in ('FeatureNav','SaveNav','ServerNav','CheckUpdates'):
        xaml,count=re.subn(r'(x:Name="'+name+r'")',r'\1 Visibility="Collapsed"',xaml)
        if count!=1:raise ValueError('Unexpected WPF navigation: '+name)
    (destination/'Launcher.xaml').write_text(xaml,encoding='utf-8')
    catalog=json.loads((destination/'feature-catalog.json').read_text('utf-8-sig'))
    values={item['id']:item['default'] for item in catalog['settings'] if item['type'] in ('bool','int')}
    values.update(world_sync=True,dream=True,shared_progress=False)
    (destination/'features.json').write_text(json.dumps({'schema':1,'values':values},indent=2),encoding='utf-8')
    info=json.loads((destination/'BUILD-INFO.json').read_text('utf-8'))
    info.update(version='0.1.0-preview.17-lamp-trace',diagnostic=True,runtime_tested=False)
    (destination/'BUILD-INFO.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    programs=['BBSC-Launcher.exe','HuntUpdates.dll','client.py','client-original.py','lamp_runner.py','lamp_analysis.py','lamp-mapping.json','Launcher.xaml','mod/bbsc.dll','features.json']
    cfg={'schema':1,'role':role,'slot':seed['slot'],'baseline_sha256':seed['baseline_sha256'],
         'character_name':seed['character_name'],
         'seed_lamps':seed['enabled'],'seed_description':seed['description'],
         'identity_note':'Expected slot is fixed by this test seed; native active_slot_hint is a BBSC inference, not a unique character UUID.',
         'eboot_sha256':settings['eboot_sha256'],'program_sha256':{name:sha(destination/name) for name in programs},
         'release_sha256':settings['release_sha256'],'diagnostic_only':True,'runtime_tested':False}
    (destination/'LAMP-CASE.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    cmd='@echo off\r\nsetlocal\r\nset "PYTHONHOME=%~dp0runtime"\r\nset "PATH=%~dp0runtime\\bin;%PATH%"\r\n"%~dp0runtime\\bin\\python.exe" -B "%~dp0lamp_runner.py" collect\r\npause\r\n'
    (destination/'Collect-Lamp-Evidence.cmd').write_bytes(cmd.encode('ascii'))
    files={p.relative_to(destination).as_posix():sha(p) for p in destination.rglob('*') if p.is_file() and p.name not in {'file-sha256.json','LAMP-INCOMPLETE'}}
    (destination/'file-sha256.json').write_text(json.dumps(files,indent=2),encoding='utf-8')
    marker.unlink()
    return cfg

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--release-zip',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--role',choices=['Host02','Guest01'],required=True)
    args=parser.parse_args();result=prepare(args.release_zip,args.output,args.role)
    print(json.dumps({'directory':str(args.output.resolve()),'role':result['role'],'slot':result['slot'],'seed_lamps':len(result['seed_lamps']),'game_started':False,'runtime_tested':False},indent=2))
