"""Build the single Windows co-op DLL from all eight source units with MSYS2 CLANG64."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import tempfile

from prepare_windows_join import apply


def build(tree,port,sdk,output):
    tree,port,sdk,output = (p.resolve() for p in (tree,port,sdk,output))
    apply('bbsc',tree)
    output.parent.mkdir(parents=True,exist_ok=True)
    bin_dir=sdk/'bin'
    env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH']}
    cxx=bin_dir/'clang++.exe'
    flags=['-std=c++23','-O2','-fms-extensions','-DNOMINMAX','-DWIN32_LEAN_AND_MEAN',
           '-D_CRT_SECURE_NO_WARNINGS','-DBBSC_MOD','-DFMT_HEADER_ONLY','-Iemulator/mod/shim',
           '-Iemulator/mod','-Iemulator/src','-Iemulator/src/core','-I'+str(port/'gpu/third_party/xbyak'),
           '-ffile-prefix-map='+str(tree)+'=bbsc','-ffile-prefix-map='+str(port)+'=bbport',
           '-ffile-prefix-map='+str(sdk)+'=clang64']
    units=['emulator/mod/'+n+'.cpp' for n in ('bbsc_mod_zh_cn','bbsc_hooks','bbsc_relay','bbsc_channel','bbsc_http','bbsc_discord')]
    units+=['emulator/src/core/bbsc_memscan.cpp','emulator/src/core/bloodborne_re_zh_cn.cpp']
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        area=Path(scratch)
        def compile_unit(unit):
            obj=area/(Path(unit).stem+'.o')
            subprocess.run([cxx,*flags,'-c',unit,'-o',obj],cwd=tree,env=env,check=True)
            return obj
        with ThreadPoolExecutor(max_workers=3) as workers:
            objects=list(workers.map(compile_unit,units))
        target=area/'bbsc.dll'
        subprocess.run([cxx,'-shared',*objects,'-lws2_32','-lwinhttp','-luser32','-lbcrypt',
                        '-Wl,--no-insert-timestamp','-Wl,--build-id=none','-o',target],cwd=tree,env=env,check=True)
        target.replace(output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for argument in ('bbsc','bbport','sdk','output'):
        parser.add_argument(argument,type=Path)
    args=parser.parse_args()
    build(args.bbsc,args.bbport,args.sdk,args.output)
