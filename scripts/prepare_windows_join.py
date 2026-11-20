"""Apply the verified Windows join sources on top of the prepared preview.25 input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

PATCHES = Path(__file__).resolve().parents[1]/'patches/windows-join'


def digest(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def apply(kind, tree, patches=PATCHES):
    tree = tree.resolve()
    files = json.loads((patches/'inputs.json').read_bytes())[kind]
    actual = {name:digest(tree/name) for name in files}
    if all(actual[name] == info['after'] for name,info in files.items()):
        return
    for name, info in files.items():
        if actual[name] != info['before']:
            raise ValueError('Unexpected input for '+name)
    # git apply sees the same newline representation on Windows and in MSYS2.
    for name in files:
        source = tree/name
        if source.is_file():
            data = source.read_bytes()
            if b'\r\n' in data:
                source.write_bytes(data.replace(b'\r\n',b'\n'))
    command = ['git','-c','core.autocrlf=false','apply','--whitespace=nowarn',str(patches/(kind+'.patch'))]
    env = {**os.environ,'GIT_CEILING_DIRECTORIES':str(tree.parent)}
    subprocess.run(command,cwd=tree,env=env,check=True)
    for name,info in files.items():
        if digest(tree/name) != info['after']:
            raise ValueError('Output checksum mismatch for '+name)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=('bbsc','bbport','server'))
    parser.add_argument('tree',type=Path)
    parser.add_argument('--patches',type=Path,default=PATCHES)
    args=parser.parse_args()
    apply(args.kind,args.tree,args.patches.resolve())
