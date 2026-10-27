"""Apply the pinned, gameplay-validated session/save patches after localization."""
import argparse
import hashlib
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
CORE_FRAGMENTS=('bbsc_kit_fast_prepare.inc','bbsc_menu_quit.inc',
                'bbsc_world_recovery.inc','bbsc_save_proof.inc')
SOURCES={
 'emulator/src/core/bloodborne_re_zh_cn.cpp':(
    '6a9b391546cbbe3148d05fcaf5ab0665bae8fff45c848c59e234987782a3d2a7',
    '0c89a5b54154cb7b3decb4fca69175a66055b4799ad292ecbd87f0f9dc2147eb',CORE_FRAGMENTS),
 'emulator/mod/bbsc_mod_zh_cn.cpp':(
    '91d17adcf3042ecf58d4f7b37f74738db5a23b1a8cc473f15a4f0816899542b9',
    '80a2123331106916a283f9bc805565545b7df20c9fd164397cd75ed02fc5d1ce',('bbsc_slot_guard.inc',)),
}

def sha(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    tree=args.source.resolve()
    for relative,(before,_,_) in SOURCES.items():
        path=tree/relative
        text=path.read_text('utf-8')
        if sha(text)!=before:raise ValueError('Unexpected pinned/localized source: '+relative)
    # Normalize only line endings before git apply; Windows Python otherwise
    # emits CRLF while CI/patch tools can produce LF. The source hashes above
    # and expanded hashes below check the exact normalized implementation.
    for relative in SOURCES:
        path=tree/relative
        path.write_text(path.read_text('utf-8'),encoding='utf-8',newline='\n')
    patch=ROOT/'patches/session-recovery.patch'
    git=['git','-c','safe.directory='+tree.as_posix()]
    subprocess.run([*git,'apply','--check',str(patch)],cwd=tree,check=True)
    subprocess.run([*git,'apply',str(patch)],cwd=tree,check=True)
    for relative,(_,expected,fragments) in SOURCES.items():
        path=tree/relative
        expanded=path.read_text('utf-8')
        for name in fragments:
            content=(ROOT/'patches'/name).read_text('utf-8')
            needle=f'#include "{name}"\n'
            if expanded.count(needle)!=1:raise ValueError('Unexpected fragment placement: '+name)
            expanded=expanded.replace(needle,content,1)
            (path.parent/name).write_text(content,encoding='utf-8',newline='\n')
        if sha(expanded)!=expected:raise ValueError('Expanded implementation differs from gameplay-tested source: '+relative)
    print('Applied gameplay-validated kit initialization, confirmed menu quit, world recovery, native save proof, and guarded slot access.')

if __name__=='__main__':main()
