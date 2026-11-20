"""Verify that the tagged preview promotes the same Windows binaries approved for gameplay."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def archive_catalog(path):
    prefix = path.stem + "/"
    with ZipFile(path) as z:
        members = z.namelist()
        if len(set(n.casefold() for n in members)) != len(members) or any(not n.startswith(prefix) for n in members):
            raise ValueError("Wrong ZIP layout: "+path.name)
        return {n[len(prefix):]:hashlib.sha256(z.read(n)).hexdigest() for n in members}


def verify(pins, directory):
    version=pins["version"]
    client=directory/("TheHuntLauncher-"+version+"-windows-x64.zip")
    server=directory/("TheHuntServer-Windows-v"+version+".zip")
    for key,path in (("client",client),("server",server)):
        if digest(path)!=pins[key]:
            raise ValueError("Reviewed ZIP digest differs: "+path.name)
    c,s=archive_catalog(client),archive_catalog(server)
    if {k[7:]:v for k,v in c.items() if k.startswith("server/")}!=s:
        raise ValueError("Embedded Windows server differs")
    if any(c.get(k)!=v for k,v in pins["client_files"].items()) or s["bbsc/shadnet.exe"]!=pins["matching_service"]:
        raise ValueError("Reviewed engine, launcher or server differs")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("pins",type=Path)
    p.add_argument("directory",type=Path)
    p.add_argument("--remote-assets",type=Path)
    args=p.parse_args()
    pins=json.loads(args.pins.read_bytes())
    if args.remote_assets:
        assets=json.loads(args.remote_assets.read_bytes())
        local={f.name:(f.stat().st_size,"sha256:"+digest(f)) for f in args.directory.iterdir() if f.is_file()}
        remote={a["name"]:(a["size"],a["digest"]) for a in assets["assets"]}
        if not assets["isDraft"] or local!=remote:
            raise SystemExit("Draft release asset list, size or digest differs")
    else:
        verify(pins,args.directory)
    print("Verified reviewed Windows release assets.")
