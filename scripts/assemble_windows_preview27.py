"""Assemble preview.27 from the published Windows runtime, compiled desktops and verified co-op mod."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from package import ROOT, check_public, digest, private_path
from verify_reviewed_windows import release_documents, verify

VERSION = '0.1.0-preview.27'
PREVIOUS = '0.1.0-preview.26'
MOD_SHA256 = '237e5236abcad59725afec0085c0ede5044e86b27aeb9b557e54a4e278c710dd'


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def members(archive, prefix):
    names = archive.namelist()
    if any(not name.startswith(prefix) or name.endswith('/') for name in names):
        raise ValueError('Unexpected product archive layout')
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Duplicate archive member')
    return {name[len(prefix):]: (archive, name) for name in names}


def content(source):
    if isinstance(source, Path):
        return source.read_bytes()
    if isinstance(source, bytes):
        return source
    archive, name = source
    return archive.read(name)


def tracked_tree(folder):
    listing = subprocess.check_output(['git', '-C', ROOT, 'ls-files', '-z', '--', folder])
    paths = [ROOT / name.decode('utf-8') for name in listing.split(b'\0') if name]
    return {path.relative_to(ROOT / folder).as_posix(): path for path in paths
            if not private_path(path.relative_to(ROOT / folder).as_posix())}


def replace_tree(sources, name, files):
    prefix = name + '/'
    for old in [key for key in sources if key.startswith(prefix)]:
        del sources[old]
    sources.update({prefix + key: value for key, value in files.items()})


def archive_product(path, sources, catalog, metadata=None):
    sources.pop(catalog)
    prefix = path.stem + '/'
    check_public([*sources, catalog])
    hashes = {}
    def put(bundle, name, data):
        item = ZipInfo(prefix + name, (2026, 11, 21, 0, 0, 0))
        item.create_system = 0
        bundle.writestr(item, data, compress_type=ZIP_DEFLATED, compresslevel=6)
    temporary = path.with_suffix('.zip.partial')
    with ZipFile(temporary, 'w') as bundle:
        for name, source in sorted(sources.items()):
            data = content(source)
            hashes[name] = hashlib.sha256(data).hexdigest()
            put(bundle, name, data)
        manifest = encode(hashes if metadata is None else dict(metadata, files=hashes))
        put(bundle, catalog, manifest)
    with ZipFile(temporary) as archive:
        if set(members(archive, prefix)) != set(hashes) | {catalog} or archive.read(prefix + catalog) != manifest:
            raise ValueError('Incomplete product manifest')
        for name, expected in hashes.items():
            if hashlib.sha256(archive.read(prefix + name)).hexdigest() != expected:
                raise ValueError('Archive member changed: ' + name)
    temporary.replace(path)
    return hashes


def assemble(previous, output, mod):
    old_pins = json.loads((ROOT / f'docs/releases/v{PREVIOUS}.windows.json').read_bytes())
    previous_client = previous / f'TheHuntLauncher-{PREVIOUS}-windows-x64.zip'
    previous_server = previous / f'TheHuntServer-Windows-v{PREVIOUS}.zip'
    if (digest(previous_client), digest(previous_server), digest(mod)) != (
            old_pins['client'], old_pins['server'], MOD_SHA256):
        raise ValueError('Unexpected published runtime or co-op mod')
    documents = release_documents(ROOT, VERSION)
    if (json.loads(documents['upstream.lock.json'].read_bytes())['version'] != VERSION or
            (ROOT / 'server/VERSION').read_text().strip() != VERSION):
        raise ValueError('The client and server must have matching versions')
    client = output / previous_client.name.replace(PREVIOUS, VERSION)
    server = output / previous_server.name.replace(PREVIOUS, VERSION)
    output.mkdir(parents=True, exist_ok=True)
    assets = tracked_tree('assets')
    notes = documents['PREVIEW-NOTES.md']
    with ZipFile(previous_server) as old:
        sources = members(old, previous_server.stem + '/')
        metadata = json.loads(content(sources['BUILD-INFO.json']))
        del metadata['files']
        metadata['label'] = VERSION
        replace_tree(sources, 'assets', assets)
        replace_tree(sources, 'docs', tracked_tree('server/docs'))
        sources.update({name: ROOT / 'server/windows/build-ui' / name for name in ('Server.exe', 'TheHunt.ico')})
        sources['PREVIEW-NOTES.md'] = notes
        archive_product(server, sources, 'BUILD-INFO.json', metadata)
    with ZipFile(previous_client) as old, ZipFile(server) as standalone:
        sources = members(old, previous_client.stem + '/')
        replace_tree(sources, 'server', members(standalone, server.stem + '/'))
        replace_tree(sources, 'assets', assets)
        docs = tracked_tree('docs')
        # The ZIP digest cannot be embedded in its own contents. The source archive carries this pin.
        docs.pop(f'releases/v{VERSION}.windows.json', None)
        replace_tree(sources, 'docs', docs)
        sources.update(documents)
        sources['NOTICE.md'] = ROOT / 'NOTICE.md'
        sources['mod/bbsc.dll'] = mod
        for name in ('TheHuntLauncher.exe', 'HuntUpdates.dll', 'TheHunt.ico'):
            sources[name] = ROOT / '.build' / name
        launcher_files = {name: path for name, path in tracked_tree('launcher').items()
                          if '/' not in name and (name.endswith('.py') or name in ('Launcher.xaml', 'DesktopTheme.xaml'))}
        sources.update(launcher_files)
        metadata = json.loads(content(sources['BUILD-INFO.json']))
        metadata.update(version=VERSION, bbsc_sha256=MOD_SHA256,
                        launcher_sha256=digest(sources['TheHuntLauncher.exe']))
        metadata['local_server']['version'] = VERSION
        metadata.pop('restore_character_view_sha256', None)
        sources['BUILD-INFO.json'] = encode(metadata)
        hashes = archive_product(client, sources, 'file-sha256.json')
    tracked = set(old_pins['client_files']) - {f'docs/releases/v{PREVIOUS}.md'}
    tracked.update(documents)
    tracked.update(launcher_files)
    tracked.update(['Launcher.xaml', 'DesktopTheme.xaml', 'world_recovery.py', 'save_transfer.py',
                    'save_backups.py', 'server/Server.exe', 'TheHunt.ico'])
    pins = dict(version=VERSION, client=digest(client), server=digest(server),
                matching_service=old_pins['matching_service'],
                client_files={name: hashes[name] for name in sorted(tracked)})
    verify(pins, output)
    (ROOT / f'docs/releases/v{VERSION}.windows.json').write_bytes(encode(pins))
    print(json.dumps({key: pins[key] for key in ('version', 'client', 'server')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous', type=Path, help='directory of the two public preview.26 Windows archives')
    parser.add_argument('output', type=Path)
    parser.add_argument('mod', type=Path, help='reviewed build of bbsc.dll')
    args = parser.parse_args()
    assemble(args.previous, args.output, args.mod)
