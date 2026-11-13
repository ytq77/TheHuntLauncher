"""Exercise real ZIP/installer paths with disposable program and private-state markers."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from package import archive_tree, check_public, copy_public_docs
from server_bundle import install_package

PRIVATE_NAMES = ('bbsc/online-data.json.tmp', 'BBSC/ONLINE-DATA.JSON.abc.TMP', 'bbsc/online-data.tmp',
                 '.save-conversion-pending.json', '.save-conversion-pending.json.abc.tmp',
                 '.save-conversion-pending.tmp', 'launcher-state.tmp',
                 'update-settings.json.abc.tmp', 'bbsc/.server.lock.tmp',
                 '.update-transaction.json', '.update-transaction.json.tmp-abc', '.update-transaction.tmp-abc',
                 '.savedata-conversion-rejected-abc/userdata0001')


def put(root, name, raw=b'synthetic fixture'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


class PackagingPrivacy(unittest.TestCase):
    def test_client_summon_record_and_temporary_files_never_enter_public_archive(self):
        for name in ('mod/summon-record-17-123.state', 'mod/summon-record-17-123.state.tmp',
                     'mod/SUMMON-RECORD-17-123.STATE.LOCK'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / 'Application'
                put(package, name, b'synthetic private summon alias')
                archive = root / 'client.zip'
                with self.assertRaises(ValueError):
                    archive_tree(package, archive)
                self.assertFalse(archive.exists())
                copy_public_docs(package, root / 'public-docs')
                self.assertFalse(any(p.is_file() for p in (root / 'public-docs').rglob('*')))

    def test_client_archive_rejects_private_temporary_variants_before_writing_zip(self):
        for name in PRIVATE_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / 'Application'
                put(package, name, b'private marker')
                archive = root / 'client.zip'
                with self.assertRaises(ValueError):
                    archive_tree(package, archive)
                self.assertFalse(archive.exists())
                self.assertFalse((package / 'file-sha256.json').exists())

    def test_documentation_filters_private_variants_and_retains_public_templates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in PRIVATE_NAMES:
                put(root / 'docs', 'server/' + name, b'private marker')
            for name in ('README.md', 'server/.env.example', 'server/shadnet.cfg.example'):
                put(root / 'docs', name, b'public template')
            copy_public_docs(root / 'docs', root / 'copy')
            self.assertEqual({p.relative_to(root / 'copy').as_posix()
                              for p in (root / 'copy').rglob('*') if p.is_file()},
                             {'README.md', 'server/.env.example', 'server/shadnet.cfg.example'})


class WindowsServerPackage(unittest.TestCase):
    def fixture(self, root):
        windows, prefix = root / 'server/windows', root / 'toolchain'
        windows.mkdir(parents=True)
        shutil.copy2(ROOT / 'server/windows/package.py', windows / 'package.py')
        for name in ('build/shadnet.exe', 'build/worlds.cfg', 'build/scoreboards.cfg',
                     'build/clientsample/shadnet-sample.exe', 'build/clientsample/shadnet-admin.exe',
                     'build-ui/Server.exe', 'build-ui/TheHunt.ico', 'build-ui/assets/art.txt',
                     'src/LICENSES/GPL.txt', 'src/externals/abseil-cpp/LICENSE',
                     'src/externals/protobuf/LICENSE', 'source-manifest.json'):
            put(windows, name)
        put(windows, 'bbsc-game/bbsc_gameserver.py', b'# synthetic game service')
        put(windows, 'package-src/Apply-Update.ps1', b'# synthetic update helper')
        python_dir = f'lib/python{sys.version_info.major}.{sys.version_info.minor}'
        for name in ('bin/python.exe', 'share/qt6/plugins/sqldrivers/qsqlite.dll',
                     python_dir + '/lib-dynload/fixture.pyd', python_dir + '/json/__init__.py',
                     'share/licenses/python/LICENSE'):
            put(prefix, name)
        server = windows.parent
        for name in ('README.md', 'LICENSE', 'NOTICE.md', 'docs/deployment.md', 'docs/windows.md'):
            put(server, name, b'synthetic public documentation')
        put(server, 'recipe/sources.json', b'{}')
        put(server, 'recipe/.env.example', b'SERVER_IP=example')
        put(server, 'recipe/shadnet.cfg.example', b'# example')
        put(server, 'recipe/.env', b'private marker')
        put(server, 'docs/local/bbsc/online-data.json.tmp', b'private marker')
        return windows, prefix

    def run_package(self, windows, prefix, zip_output=True):
        config = types.ModuleType('build_config')
        config.VERSION = '0.1.0-preview.22'
        config.OUT = windows / 'dist/TheHuntServer-Windows-v0.1.0-preview.22'

        def output(command, **options):
            name = Path(command[0]).name
            if name == 'llvm-objdump.exe':
                return b''  # Synthetic binaries have no native dependency imports.
            if name == 'clang++.exe':
                return 'synthetic clang\n'
            if name == 'qmake6.exe':
                return '6.0\n'
            if name == 'pacman.exe':
                return 'synthetic-runtime 1.0\n'
            raise AssertionError('Unexpected tool call: ' + repr(command))

        with patch.dict(sys.modules, {'build_config': config}), \
                patch.dict(os.environ, {'WIN_SERVER_TOOLCHAIN': str(prefix)}), \
                patch.object(sys, 'argv', ['package.py'] + (['--zip'] if zip_output else [])), \
                patch('subprocess.check_output', side_effect=output), contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(windows / 'package.py'), run_name='__main__')
        return config.OUT, config.OUT.parent / (config.OUT.name + '.zip')

    def test_server_zip_manifest_docs_and_client_embedding_use_real_packaging_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            windows, prefix = self.fixture(root)
            # Use the actual public links while keeping native/runtime inputs synthetic.
            shutil.copy2(ROOT / 'server/README.md', windows.parent / 'README.md')
            copy_public_docs(ROOT / 'server/docs', windows.parent / 'docs')
            folder, archive = self.run_package(windows, prefix)
            with zipfile.ZipFile(archive) as bundle:
                info = json.loads(bundle.read(folder.name + '/BUILD-INFO.json'))
                self.assertEqual(set(bundle.namelist()),
                                 {folder.name + '/' + name for name in info['files']} |
                                 {folder.name + '/BUILD-INFO.json'})
                self.assertTrue({'README.md', 'docs/deployment.md', 'recipe/sources.json',
                                 'recipe/.env.example', 'recipe/shadnet.cfg.example'} <= set(info['files']))
                for name in bundle.namelist():
                    self.assertNotIn(b'private marker', bundle.read(name))
            client = root / 'Client'
            client.mkdir()
            copy_public_docs(ROOT / 'docs', client / 'docs')
            for name in ('README.md', 'LICENSE', 'NOTICE.md', 'CONTRIBUTING.md', 'upstream.lock.json'):
                shutil.copy2(ROOT / name, client / name)
            install_package(archive, client / 'server', '0.1.0-preview.22', check_public)
            for name in ('server/README.md', 'server/docs/deployment.md', 'server/docs/components.md',
                         'server/docs/windows.md', 'server/recipe/sources.json'):
                self.assertTrue((client / name).is_file(), name)
            for name in ('README.md', 'server/README.md', 'server/docs/deployment.md'):
                document = client / name
                for link in re.findall(r'\]\(([^)\n]+)\)', document.read_text('utf-8')):
                    relative = link.split('#', 1)[0]
                    if not relative or '://' in relative:
                        continue
                    self.assertTrue((document.parent / relative).is_file(), (name, relative))
            archive_tree(client, root / 'client.zip')

    def test_native_build_can_add_public_reports_between_its_two_packaging_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            windows, prefix = self.fixture(Path(temporary))
            folder, _ = self.run_package(windows, prefix, zip_output=False)
            put(folder, 'LOCAL-VALIDATION.json', b'{"synthetic":true}')
            put(folder, 'LOCAL-MANAGER-VALIDATION.json', b'{"synthetic":true}')
            _, archive = self.run_package(windows, prefix)
            self.assertTrue(archive.is_file())

    def test_staging_private_variants_are_rejected_before_binary_copy_or_zip(self):
        for name in PRIVATE_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                windows, prefix = self.fixture(Path(temporary))
                folder = windows / 'dist/TheHuntServer-Windows-v0.1.0-preview.22'
                put(folder, name, b'private marker')
                with self.assertRaises(ValueError):
                    self.run_package(windows, prefix)
                self.assertEqual((folder / name).read_bytes(), b'private marker')
                self.assertFalse((folder / 'Server.exe').exists())
                self.assertFalse(list(folder.parent.glob('*.zip')))

    def test_changed_or_unregistered_staging_files_are_not_silently_repackaged(self):
        for name in ('server.json', 'local-note.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                windows, prefix = self.fixture(Path(temporary))
                folder, _ = self.run_package(windows, prefix, zip_output=False)
                put(folder, name, b'local initialized state')
                with self.assertRaises(ValueError):
                    self.run_package(windows, prefix)
                self.assertEqual((folder / name).read_bytes(), b'local initialized state')
                self.assertFalse(list(folder.parent.glob('*.zip')))


POWERSHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


@unittest.skipUnless(os.name == 'nt' and POWERSHELL.is_file(), 'Windows PowerShell update helper tests')
class UpdatePackagePrivacy(unittest.TestCase):
    def install(self, area, client, private_name=None):
        helper = ROOT / ('launcher/Apply-Update.ps1' if client else 'server/windows/package-src/Apply-Update.ps1')
        root = area / 'installed'
        entry = 'TheHuntLauncher.exe' if client else 'Server.exe'
        put(root, entry, b'old synthetic program')
        files = {entry: b'new synthetic program', 'Apply-Update.ps1': helper.read_bytes(),
                 'docs/recipe/.env.example': b'public template',
                 'docs/recipe/shadnet.cfg.example': b'public template'}
        if private_name:
            files[private_name] = b'private payload marker'
        if client:
            files['BUILD-INFO.json'] = b'{"version":"0.2.0-preview.1"}'
            files['file-sha256.json'] = json.dumps({name: hashlib.sha256(raw).hexdigest()
                                                   for name, raw in files.items()}).encode()
        else:
            files['BUILD-INFO.json'] = json.dumps({'label': '0.2.0-preview.1', 'files': {
                name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}).encode()
        archive = area / 'update.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name, raw in files.items():
                bundle.writestr('Application/' + name, raw)
        plan = put(area, 'plan.json', json.dumps({'root': str(root), 'repository':
            'TheHuntLauncher' if client else 'TheHuntServer', 'version': '0.2.0-preview.1',
            'archive': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'parent_pid': 0}).encode())
        result = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(helper), '-Plan', str(plan), '-Headless', '-NoRestart'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return root, entry, result

    def test_clean_synthetic_update_with_public_templates_is_accepted_by_both_helpers(self):
        for client in (False, True):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as temporary:
                root, entry, result = self.install(Path(temporary), client)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                self.assertEqual((root / entry).read_bytes(), b'new synthetic program')

    def test_private_variants_are_rejected_by_actual_helpers_before_any_program_replacement(self):
        for client in (False, True):
            for name in PRIVATE_NAMES:
                # The bundled server is nested in a client update package.
                name = 'server/' + name if client and name.casefold().startswith('bbsc/') else name
                with self.subTest(client=client, name=name), tempfile.TemporaryDirectory() as temporary:
                    root, entry, result = self.install(Path(temporary), client, name)
                    self.assertNotEqual(result.returncode, 0, name)
                    self.assertEqual((root / entry).read_bytes(), b'old synthetic program')
                    self.assertFalse((root / 'update-backups').exists())
                    self.assertFalse((root / name).exists())


if __name__ == '__main__':
    unittest.main()
