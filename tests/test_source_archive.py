"""Build real source ZIPs from disposable Git repositories and synthetic inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source_archive as sources


def write(root, name, data=b'// synthetic source\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'core.longpaths=true', '-c',
        'user.name=Synthetic Test', '-c', 'user.email=synthetic@example.invalid',
        '-c', 'protocol.file.allow=always', '-C', str(root), *args], stderr=subprocess.STDOUT).decode().strip()


@unittest.skipUnless(shutil.which('git'), 'Git is required for source manifest regressions')
class SourceArchive(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def repo(self, name, files):
        root = self.root / name
        root.mkdir(parents=True)
        git(root, 'init', '-q')
        for relative, raw in files.items():
            write(root, relative, raw)
        git(root, 'add', '.')
        git(root, 'commit', '-qm', 'Synthetic pinned inputs')
        return root, git(root, 'rev-parse', 'HEAD')

    def bbsc(self):
        root, revision = self.repo('bbsc', {'LICENSE': b'synthetic license',
            'emulator/mod/bbsc_http.cpp': b'// upstream\n',
            'emulator/src/core/legacy.h': b'// upstream comment with legacy dash: \x97\n',
            '.env': b'tracked-private-marker'})
        for name in sources.BBSC_GENERATED:
            write(root, name)
        write(root, 'emulator/mod/bbsc_http.cpp', b'// compiled patched source\n')
        for name in ('local-secret.cpp', 'server/gameserver/online-data.json.tmp',
                     'server/bbsc/db/accounts.sqlite', 'emulator/mod/.env'):
            write(root, name, b'untracked-private-marker')
        return root, revision

    def test_exact_patched_sources_and_all_generated_inputs_reach_verified_zip(self):
        root, revision = self.bbsc()
        entries = [(path, 'bbsc/' + name) for path, name in sources.bbsc_sources(root, revision)]
        out = self.root / 'sources.zip'
        sources.write_archive(entries, out)
        with zipfile.ZipFile(out) as archive:
            manifest = json.loads(archive.read('SOURCE-MANIFEST.json'))
            self.assertEqual(set(archive.namelist()), set(manifest) | {'SOURCE-MANIFEST.json'})
            self.assertTrue({'bbsc/' + name for name in sources.BBSC_GENERATED} <= set(manifest))
            self.assertEqual(archive.read('bbsc/emulator/mod/bbsc_http.cpp'), b'// compiled patched source\n')
            self.assertEqual(archive.read('bbsc/emulator/src/core/legacy.h'),
                             b'// upstream comment with legacy dash: \x97\n')
            for name, digest in manifest.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
                self.assertNotIn(b'private-marker', archive.read(name))

    def test_missing_generated_input_preserves_previous_archive(self):
        root, revision = self.bbsc()
        (root / sources.BBSC_GENERATED[-1]).unlink()
        out = write(self.root, 'sources.zip', b'previous-archive')
        with self.assertRaises(ValueError):
            sources.write_archive(sources.bbsc_sources(root, revision), out)
        self.assertEqual(out.read_bytes(), b'previous-archive')
        self.assertFalse(out.with_suffix('.zip.tmp').exists())

    def test_new_local_include_must_be_registered(self):
        root, revision = self.bbsc()
        write(root, 'emulator/src/core/bloodborne_re_zh_cn.cpp', b'#include "unregistered.inc"\n')
        write(root, 'emulator/src/core/unregistered.inc')
        with self.assertRaisesRegex(ValueError, 'Unlisted BBSC include'):
            list(sources.bbsc_sources(root, revision))

    def test_unused_bbsc_submodules_do_not_require_unfetched_sources(self):
        root, _ = self.bbsc()
        git(root, 'update-index', '--add', '--cacheinfo',
            '160000,' + '1' * 40 + ',emulator/externals/unused')
        git(root, 'commit', '-qm', 'Record unused synthetic emulator dependency')
        names = {name for _, name in sources.bbsc_sources(root, git(root, 'rev-parse', 'HEAD'))}
        self.assertIn('emulator/src/core/bloodborne_re_zh_cn.cpp', names)
        self.assertNotIn('emulator/externals/unused', names)

    def test_port_submodules_use_pinned_manifest_and_exclude_local_extras(self):
        child, _ = self.repo('child', {'include/library.h': b'// public dependency\n',
                                    'tests/data/schema.json': b'{"synthetic":"public test data"}'})
        port, _ = self.repo('port', {'engine.cpp': b'// public engine\n'})
        git(port, 'submodule', 'add', '-q', str(child), 'third_party/library')
        git(port, 'commit', '-qam', 'Pin synthetic dependency')
        revision = git(port, 'rev-parse', 'HEAD')
        write(port / 'third_party/library', 'local.txt', b'private-marker')
        git(port / 'third_party/library', 'add', 'local.txt')
        names = {name for _, name in sources.pinned_sources(port, revision)}
        self.assertIn('third_party/library/include/library.h', names)
        self.assertIn('third_party/library/tests/data/schema.json', names)
        self.assertNotIn('third_party/library/local.txt', names)
        out = self.root / 'port-sources.zip'
        sources.write_archive(((path, 'bbport/' + name) for path, name in sources.pinned_sources(port, revision)), out)
        with zipfile.ZipFile(out) as archive:
            self.assertIn('bbport/third_party/library/tests/data/schema.json', archive.namelist())

    def test_application_uses_registered_sources_and_explicit_downloads_and_bridge(self):
        bbsc, bbsc_revision = self.bbsc()
        port, port_revision = self.repo('port', {'engine.cpp': b'// engine\n'})
        for name in sources.added_join_sources('bbport'):
            write(port, name)
        raw = b'synthetic locked archive'
        manifest = {'archives': [{'name': 'shadnet.tar.gz', 'sha256': hashlib.sha256(raw).hexdigest()}],
                    'bbsc': {'files': {}}}
        lock = {'bbsc': {'commit': bbsc_revision}, 'bbport': {'commit': port_revision}}
        project, _ = self.repo('launcher', {'README.md': b'readme',
            'scripts/build.py': b'# public source', 'server/windows/package.py': b'# package',
            'scripts/prepare_save_hardening.py': b'# synthetic new preparation source',
            'server/recipe/sources.json': json.dumps(manifest).encode(),
            'upstream.lock.json': json.dumps(lock).encode(),
            'tests/installer-tests/case/Server.exe': b'fixture-private-marker',
            'docs/server/recipe/.env': b'tracked-private-marker'})
        (project / '.build').mkdir()
        shutil.move(str(bbsc), project / '.build/bbsc')
        write(project, 'scripts/unregistered.txt', b'untracked-private-marker')
        write(project, 'server/windows/downloads/shadnet.tar.gz', raw)
        for name in sources.BRIDGE_GENERATED:
            write(project / '.build/save-bridge', name)
        write(project / '.build/save-bridge', 'local-secret.cpp', b'private-marker')
        archive = self.root / 'application.zip'
        sources.write_archive(sources.source_entries(project, port), archive)
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn('bbsc-launcher/server/windows/package.py', bundle.namelist())
            self.assertIn('bbsc-launcher/scripts/prepare_save_hardening.py', bundle.namelist())
            self.assertIn('bbsc/emulator/src/core/bbsc_active_save.inc', bundle.namelist())
            for name in sources.added_join_sources('bbport'):
                self.assertIn('bbport/' + name, bundle.namelist())
            self.assertEqual(bundle.read('bbsc-launcher/server/windows/downloads/shadnet.tar.gz'), raw)
            for name in sources.BRIDGE_GENERATED:
                self.assertIn('save-bridge/' + name, bundle.namelist())
            for name in bundle.namelist():
                self.assertNotIn(b'private-marker', bundle.read(name))

    def test_duplicate_archive_names_are_refused(self):
        path = write(self.root, 'public.cpp')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            sources.write_archive([(path, 'code/a.cpp'), (path, 'code/A.cpp')], self.root / 'source.zip')


if __name__ == '__main__':
    unittest.main()
