"""Embed a clean, version-matched server artifact without carrying local state."""
import hashlib
import json
from pathlib import Path
import zipfile


def install_package(archive, destination, version, check_public):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Server destination already exists; use a fresh package directory.')
    with zipfile.ZipFile(archive) as source:
        entries = [item for item in source.infolist() if not item.is_dir()]
        if not entries or source.testzip() is not None:
            raise ValueError('Invalid server archive.')
        prefix = entries[0].filename.split('/')[0] + '/'
        files = {}
        for item in entries:
            if not item.filename.startswith(prefix):
                raise ValueError('Server package has multiple roots.')
            name = item.filename[len(prefix):]
            check_public([name, 'server/' + name])
            if name.casefold() in {key.casefold() for key in files} or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Duplicate or symbolic-link server file.')
            if name.startswith('normal/'):
                raise ValueError('Obsolete normal mode in server package.')
            files[name] = item
        required = {'Server.exe', 'server.json', 'BUILD-INFO.json', 'bbsc/shadnet.exe',
                    'runtime/bin/python.exe', 'runtime/bin/shadnet-admin.exe',
                    'bbsc-game/bbsc_gameserver.py', 'Apply-Update.ps1'}
        if not required <= files.keys():
            raise ValueError('Incomplete server package.')
        info = json.loads(source.read(files['BUILD-INFO.json']))
        if info['label'] != version or 'seamless-only' not in info.get('features', []):
            raise ValueError('Server version or mode does not match the launcher.')
        manifest = info['files']
        if set(files) != set(manifest) | {'BUILD-INFO.json'}:
            raise ValueError('Server manifest does not cover the package.')
        for name, digest in manifest.items():
            if hashlib.sha256(source.read(files[name])).hexdigest() != digest:
                raise ValueError('Server file hash mismatch: ' + name)
        if json.loads(source.read(files['server.json'])) != {'address': '127.0.0.1'}:
            raise ValueError('Server package contains initialized listener settings.')
        for name, item in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(item))
    return info
