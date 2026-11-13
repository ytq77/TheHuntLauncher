"""Exercise the actual detached installer against disposable installs, never game data."""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
HELPER = next((path for path in (HERE / 'Apply-Update.ps1', HERE.parent / 'launcher/Apply-Update.ps1',
                                HERE / 'package-src/Apply-Update.ps1') if path.is_file()), HERE / 'Apply-Update.ps1')
POWERSHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def sha(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(os.name == 'nt', 'Windows installer tests')
class AutomaticUpdate(unittest.TestCase):
    def fixture(self, client=False, extra=None):
        parent = HERE / 'installer-tests'
        parent.mkdir(exist_ok=True)
        area = Path(tempfile.mkdtemp(prefix='case-', dir=parent))
        root = area / 'installed'; root.mkdir()
        entry = 'TheHuntLauncher.exe' if client else 'Server.exe'
        private = ({'.private/account.clixml': b'private-credential-marker',
                    'data/user/savedata/userdata0000': b'private-save-marker',
                    'profile.json': b'private-profile-marker',
                    'features.json': b'private-feature-marker',
                    'mod/guest-items/a.json': b'private-guest-marker',
                    'mod/own-world/a.json': b'private-world-marker',
                    'server/server.json': b'{"address":"100.64.1.2"}',
                    'server/bbsc/db/shadnet.db': b'embedded-server-account-marker',
                    'server/bbsc/shadnet.cfg': b'embedded-server-private-config',
                    'server/bbsc/online-data.json': b'embedded-server-online-data',
                    'server/bbsc/worlds.cfg': b'embedded-server-worlds',
                    'server/bbsc/scoreboards.cfg': b'embedded-server-scoreboards'} if client else {
                    'normal/db/shadnet.db': b'normal-account-marker',
                    'bbsc/db/shadnet.db': b'bbsc-account-marker',
                    'bbsc/online-data.json': b'private-online-marker',
                    'normal/shadnet.cfg': b'private-network-marker',
                    'server.json': b'{"address":"100.64.1.2"}',
                    'normal/worlds.cfg': b'private-world-config-marker'})
        private['update-settings.json'] = b'private-update-options-marker'
        private['desktop-settings.json'] = b'1'
        old = {entry: b'old-entry', 'a.txt': b'old-a', 'z.dll': b'old-z', **private}
        for name, data in old.items():
            target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        files = {entry: b'new-entry', 'a.txt': b'new-a', 'z.dll': b'new-z',
                 'Apply-Update.ps1': HELPER.read_bytes()}
        if not client:
            files.update({'server.json': b'{"address":"127.0.0.1"}', 'normal/worlds.cfg': b'new-world-default'})
        files.update(extra or {})
        if client:
            files.update({'server/server.json': b'{"address":"127.0.0.1"}',
                          'server/bbsc/worlds.cfg': b'default-worlds',
                          'server/bbsc/scoreboards.cfg': b'default-scoreboards'})
        if client:
            files['BUILD-INFO.json'] = json.dumps({'version': '0.2.0-preview.1'}).encode()
            files['file-sha256.json'] = json.dumps({name: sha(data) for name, data in files.items()}).encode()
        else:
            files['BUILD-INFO.json'] = json.dumps({'label': '0.2.0-preview.1',
                'files': {name: sha(data) for name, data in files.items()}}).encode()
        archive = area / 'update.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name, data in files.items():
                bundle.writestr('Application/' + name, data)
        request = {'repository': 'TheHuntLauncher' if client else 'TheHuntServer',
                   'root': str(root), 'archive': str(archive), 'sha256': sha(archive.read_bytes()),
                   'version': '0.2.0-preview.1', 'parent_pid': 0}
        plan = area / 'plan.json'; plan.write_text(json.dumps(request), encoding='utf-8')
        return root, private, old, plan, request

    def run_installer(self, plan):
        return subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(HELPER), '-Plan', str(plan), '-Headless', '-NoRestart'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def test_both_projects_replace_only_programs_and_keep_private_bytes(self):
        for client in (False, True):
            with self.subTest(client=client):
                root, private, old, plan, _ = self.fixture(client)
                result = self.run_installer(plan)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertEqual((root / 'a.txt').read_bytes(), b'new-a')
                if client:
                    self.assertEqual((root / 'TheHuntLauncher.exe').read_bytes(), b'new-entry')
                    self.assertFalse((root / 'BBSC-Launcher.exe').exists())
                for name, data in private.items():
                    self.assertEqual((root / name).read_bytes(), data, name)
                backup = next((root / 'update-backups').iterdir())
                self.assertEqual((backup / 'program/a.txt').read_bytes(), b'old-a')
                self.assertTrue(json.loads((backup / 'result.json').read_text())['success'])
                self.assertFalse((backup / 'program/update-settings.json').exists())
                self.assertFalse((root / '.update-transaction.json').exists())
                self.assertFalse((plan.parent / 'update.zip').exists())
                self.assertFalse(list(plan.parent.glob('payload-*')))
                manifest = json.loads((backup / 'transaction.json').read_text())
                self.assertEqual(next(row for row in manifest['operations'] if row['path'] == 'a.txt')['old_sha256'], sha(b'old-a'))
                self.assertTrue((backup / 'RECOVERY.txt').is_file())

    def test_runtime_path_policy_matches_current_packager_including_atomic_variants(self):
        source = next((parent for parent in HERE.parents if (parent / 'scripts/package.py').is_file()), None)
        if source is None:
            self.skipTest('Source packaging policy is not included in a standalone installed test suite')
        sys.path.insert(0, str(source / 'scripts'))
        import package as packaging
        cases = {'server.json', 'server/server.json', 'docs/recipe/server.json',
                 '.env.example', 'shadnet.cfg.example', 'docs/recipe/.env.example',
                 'docs/recipe/shadnet.cfg.example', '.env.example.tmp', 'shadnet.cfg.example.old',
                 'Server.exe', 'runtime/bin/python.exe', 'docs/normal/db/source.txt',
                 'server/BBSC/DB/account.sqlite', '.savedata-import-abc/file.bin',
                 '.savedata-conversion-rejected-abc/userdata0001',
                 '', '../escape', 'a//b', '/escape', 'C:/escape', 'a\\b', 'NUL.txt',
                 'file.', 'file ', 'a<.txt', 'a>.txt', 'a".txt', 'a|.txt', 'a?.txt', 'a*.txt', 'a\x01.txt'}
        for name in packaging.PRIVATE_FILES:
            cases.add(name)
            for suffix in ('.tmp', '.abc.tmp', '.tmp-abc', '.bak', '.old-abc', '.new.abc'):
                cases.add(name + suffix)
                if '.' in name.lstrip('.'):
                    cases.add(name.rsplit('.', 1)[0] + suffix)
        for name in packaging.PRIVATE_PARTS:
            cases.add(name + '/file.bin')
        cases |= {name.upper() for name in cases}
        cases |= {'docs/recipe/' + name for name in cases if name and not name.startswith('/')}
        names = sorted(cases)
        expected = []
        for name in names:
            try:
                packaging.check_public([name])
                expected.append(True)
            except ValueError:
                expected.append(False)
        parent = HERE / 'installer-tests'; parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='policy-', dir=parent) as area:
            script = Path(area) / 'policy.ps1'
            # Evaluate the actual helper's path gate without executing an installer.
            script.write_text('''param([string]$Helper)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Helper,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Helper parse failed' }
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Safe-Relative'},$true)
if (-not $function) { throw 'Path gate not found' }
. ([scriptblock]::Create($function.Extent.Text))
$names=[Console]::In.ReadToEnd() | ConvertFrom-Json
$accepted=@(foreach ($name in $names) { try { $null=Safe-Relative $name; $true } catch { $false } })
[Console]::Out.Write((ConvertTo-Json -InputObject $accepted -Compress))
''', encoding='utf-8-sig')
            result = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(script), '-Helper', str(HELPER)], input=json.dumps(names, ensure_ascii=True),
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        self.assertEqual(len(actual), len(names))
        mismatches = [(name, want, got) for name, want, got in zip(names, expected, actual) if want != got]
        self.assertEqual(mismatches, [], 'Helper and packaging policy disagree')

    def test_public_templates_and_fresh_server_settings_are_installed(self):
        templates = {'docs/recipe/.env.example': b'SERVER_IP=example',
                     'docs/recipe/shadnet.cfg.example': b'# public template',
                     'docs/recipe/server.json': b'{"address":"127.0.0.1"}'}
        for client in (False, True):
            with self.subTest(client=client):
                root, private, _, plan, _ = self.fixture(client, extra=templates)
                settings = 'server/server.json' if client else 'server.json'
                (root / settings).unlink()
                result = self.run_installer(plan)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((root / settings).read_bytes()), {'address': '127.0.0.1'})
                for name, data in templates.items():
                    self.assertEqual((root / name).read_bytes(), data, name)
                for name, data in private.items():
                    if name != settings:
                        self.assertEqual((root / name).read_bytes(), data, name)

    def test_legacy_client_install_is_rejected_without_changes(self):
        root, _, old, plan, _ = self.fixture(client=True)
        (root / 'TheHuntLauncher.exe').rename(root / 'BBSC-Launcher.exe')
        result = self.run_installer(plan)
        self.assertNotEqual(result.returncode, 0)
        for name, data in old.items():
            target = root / ('BBSC-Launcher.exe' if name == 'TheHuntLauncher.exe' else name)
            self.assertEqual(target.read_bytes(), data, name)
        self.assertFalse((root / 'update-backups').exists())

    def test_client_restarts_the_renamed_entry(self):
        root, private, _, plan, request = self.fixture(client=True)
        area = root.parent
        source = area / 'Stub.cs'
        source.write_text('using System; using System.IO; class Stub { static void Main() {'
            'File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"restarted.txt"),"renamed entry"); }}')
        compiler = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        compiled = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:Stub.exe', 'Stub.cs'],
                                  cwd=area, capture_output=True)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        archive = Path(request['archive'])
        with zipfile.ZipFile(archive) as bundle:
            files = {name: bundle.read(name) for name in bundle.namelist()}
        files['Application/TheHuntLauncher.exe'] = (area / 'Stub.exe').read_bytes()
        manifest = json.loads(files['Application/file-sha256.json'])
        manifest['TheHuntLauncher.exe'] = sha(files['Application/TheHuntLauncher.exe'])
        files['Application/file-sha256.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name, data in files.items():
                bundle.writestr(name, data)
        request['sha256'] = sha(archive.read_bytes())
        plan.write_text(json.dumps(request), encoding='utf-8')
        result = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(HELPER), '-Plan', str(plan), '-Headless'], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        for _ in range(30):
            if (root / 'restarted.txt').exists():
                break
            time.sleep(0.1)
        self.assertEqual((root / 'restarted.txt').read_text(), 'renamed entry')
        self.assertFalse((root / 'BBSC-Launcher.exe').exists())
        for name, data in private.items():
            self.assertEqual((root / name).read_bytes(), data, name)

    def test_locked_target_rolls_back_previously_replaced_files(self):
        root, private, old, plan, _ = self.fixture(extra={'middle/new.txt': b'new-program-file'})
        with (root / 'z.dll').open('rb'):
            result = self.run_installer(plan)
        self.assertNotEqual(result.returncode, 0)
        for name, data in old.items():
            self.assertEqual((root / name).read_bytes(), data, name)
        backup = next((root / 'update-backups').iterdir())
        report = json.loads((backup / 'result.json').read_text())
        self.assertFalse(report['success'])
        self.assertIsNone(report['rollback_error'])
        self.assertIn('a.txt', report['applied'])
        self.assertFalse((root / '.update-transaction.json').exists())
        self.assertFalse((root / 'middle/new.txt').exists())

    def test_failed_rollback_keeps_transaction_and_diagnostic(self):
        root, private, _, plan, _ = self.fixture(extra={'m-%03d.bin' % i: b'x' * 262144 for i in range(40)})
        with (root / 'z.dll').open('rb'):
            helper = subprocess.Popen([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(HELPER), '-Plan', str(plan), '-Headless', '-NoRestart'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:
                    line = helper.stdout.readline()
                    self.assertTrue(line, 'Helper exited before a.txt replacement')
                    if b'a.txt' in line:
                        break
                transaction = json.loads((root / '.update-transaction.json').read_text())
                backup = Path(transaction['backup'])
                (backup / 'program/a.txt').write_bytes(b'synthetic-corrupt-backup')
                helper.communicate(timeout=20)
            finally:
                if helper.poll() is None:
                    helper.kill(); helper.communicate(timeout=10)
        self.assertNotEqual(helper.returncode, 0)
        result = json.loads((backup / 'result.json').read_text())
        self.assertIn('Recovery backup hash mismatch', result['rollback_error'])
        self.assertEqual(json.loads((root / '.update-transaction.json').read_text())['phase'], 'rolling-back')
        self.assertTrue((plan.parent / 'update-result.json').is_file())
        for name, data in private.items():
            self.assertEqual((root / name).read_bytes(), data)

    def test_parent_update_rejects_an_unfinished_embedded_transaction(self):
        root, _, old, plan, _ = self.fixture(client=True)
        marker = root / 'server/.update-transaction.json'
        marker.write_text('{"phase":"applying"}')
        self.assertNotEqual(self.run_installer(plan).returncode, 0)
        self.assertIn('Unfinished embedded server transaction', json.loads((plan.parent / 'update-result.json').read_text())['error'])
        for name, data in old.items():
            self.assertEqual((root / name).read_bytes(), data)
        self.assertEqual(marker.read_text(), '{"phase":"applying"}')

    def test_hard_termination_blocks_reentry_with_verified_backups(self):
        for client in (False, True):
            with self.subTest(client=client):
                root, private, old, plan, _ = self.fixture(client,
                    extra={'m-%03d.bin' % i: b'x' * 262144 for i in range(40)})
                helper = subprocess.Popen([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(HELPER), '-Plan', str(plan), '-Headless', '-NoRestart'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    # Synchronize with the actual replacement report, not a sleep.
                    while True:
                        line = helper.stdout.readline()
                        self.assertTrue(line, 'Helper exited before replacing a.txt')
                        if b'a.txt' in line:
                            helper.kill()
                            break
                    helper.communicate(timeout=10)
                    marker = root / '.update-transaction.json'
                    transaction = json.loads(marker.read_text())
                    self.assertEqual(transaction['phase'], 'applying')
                    backup = Path(transaction['backup'])
                    self.assertTrue((backup / 'RECOVERY.txt').is_file())
                    for row in transaction['operations']:
                        if row['old_sha256']:
                            self.assertEqual(sha((backup / 'program' / row['path']).read_bytes()), row['old_sha256'])
                    before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
                    rejected = self.run_installer(plan)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn('Unfinished update transaction', json.loads((plan.parent / 'update-result.json').read_text())['error'])
                    self.assertEqual(before, {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()})
                    for name, data in private.items():
                        self.assertEqual((root / name).read_bytes(), data)
                finally:
                    if helper.poll() is None:
                        helper.kill(); helper.communicate(timeout=10)

    def test_embedded_server_cannot_update_under_parent_client(self):
        root, private, old, plan, request = self.fixture()
        parent = root.parent / 'client'; parent.mkdir()
        (parent / 'TheHuntLauncher.exe').write_bytes(b'parent-client')
        root.rename(parent / 'server'); root = parent / 'server'
        request['root'] = str(root); plan.write_text(json.dumps(request), encoding='utf-8')
        result = self.run_installer(plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Embedded server updates are disabled', json.loads((plan.parent / 'update-result.json').read_text())['error'])
        for name, data in old.items():
            self.assertEqual((root / name).read_bytes(), data)
        self.assertFalse((root / 'update-backups').exists())

    def test_private_and_traversal_payloads_are_rejected_before_install(self):
        for name in ('../escape.txt', 'profile.json', 'bbport.ini', 'input.ini', 'mods.json', '.update-transaction.json', 'server/.update-transaction.json', 'mod/guest-items/pickup.json', 'bbsc/db/shadnet.db', 'bbsc/online-data.json',
                     'server/bbsc/db/shadnet.db', 'server/bbsc/shadnet.cfg', 'server/bbsc/online-data.json', 'server/bbsc/.server.lock'):
            with self.subTest(name=name):
                root, _, old, plan, _ = self.fixture(extra={name: b'not-allowed'})
                result = self.run_installer(plan)
                self.assertNotEqual(result.returncode, 0)
                for path, data in old.items():
                    self.assertEqual((root / path).read_bytes(), data, path)
                self.assertFalse((root / 'update-backups').exists())

    def test_archive_hash_mismatch_never_replaces_files(self):
        root, _, old, plan, request = self.fixture()
        request['sha256'] = '0' * 64; plan.write_text(json.dumps(request), encoding='utf-8')
        self.assertNotEqual(self.run_installer(plan).returncode, 0)
        for name, data in old.items():
            self.assertEqual((root / name).read_bytes(), data)

    def test_invalid_plan_keeps_machine_readable_failure_evidence(self):
        root, _, old, plan, _ = self.fixture()
        plan.write_text('{invalid-plan', encoding='utf-8')
        self.assertNotEqual(self.run_installer(plan).returncode, 0)
        report = json.loads((plan.parent / 'update-result.json').read_text())
        self.assertFalse(report['success'])
        self.assertEqual(report['phase'], 'read-plan')
        for name, data in old.items():
            self.assertEqual((root / name).read_bytes(), data)

    def test_cleanup_never_recursively_deletes_an_archive_directory_from_plan(self):
        root, _, old, plan, request = self.fixture()
        request['archive'] = str(root)
        plan.write_text(json.dumps(request), encoding='utf-8')
        self.assertNotEqual(self.run_installer(plan).returncode, 0)
        for name, data in old.items():
            self.assertEqual((root / name).read_bytes(), data)

    def test_successful_rollback_reopens_original_program(self):
        root, private, _, plan, _ = self.fixture()
        area = plan.parent
        (area / 'Stub.cs').write_text('using System; using System.IO; class Stub { static void Main() {'
            'var root=AppDomain.CurrentDomain.BaseDirectory;'
            'using(File.Open(Path.Combine(root,".update.lock"),FileMode.Open,FileAccess.Read,FileShare.None)) {}'
            'if(File.Exists(Path.Combine(root,".update-transaction.json"))) return;'
            'File.WriteAllText(Path.Combine(root,"rollback-restarted.txt"),"original program"); }}')
        compiler = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        compiled = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:Stub.exe', 'Stub.cs'], cwd=area, capture_output=True)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        (root / 'Server.exe').write_bytes((area / 'Stub.exe').read_bytes())
        with (root / 'z.dll').open('rb'):
            result = subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(HELPER), '-Plan', str(plan), '-Headless'], capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        for _ in range(30):
            if (root / 'rollback-restarted.txt').exists():
                break
            time.sleep(0.1)
        self.assertEqual((root / 'rollback-restarted.txt').read_text(), 'original program')
        for name, data in private.items():
            self.assertEqual((root / name).read_bytes(), data)

    def test_running_application_mutex_blocks_installation(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetFullPathNameW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_void_p)
        kernel.GetFullPathNameW.restype = wintypes.DWORD
        for client in (False, True):
            root, _, old, plan, _ = self.fixture(client)
            # Match .NET GetFullPath even when MSYS2 Python emits forward slashes.
            native_path = ctypes.create_unicode_buffer(32768)
            length = kernel.GetFullPathNameW(str(root), len(native_path), native_path, None)
            self.assertTrue(0 < length < len(native_path))
            native_root = native_path.value
            identity = native_root if client else native_root.lower() + '\\'
            digest = hashlib.sha256(identity.encode()).hexdigest().upper()
            name = ('Local\\BBSCLauncher-' + '-'.join(digest[i:i+2] for i in range(0, len(digest), 2))
                    if client else 'Local\\TheHuntServer-' + digest)
            handle = kernel.CreateMutexW(None, True, name)
            self.assertTrue(handle)
            try:
                installed = self.run_installer(plan)
                self.assertNotEqual(installed.returncode, 0,
                    'Application mutex was not respected: ' + repr((native_root, installed.stdout, installed.stderr)))
                for file, data in old.items():
                    self.assertEqual((root / file).read_bytes(), data, file)
            finally:
                kernel.ReleaseMutex(handle); kernel.CloseHandle(handle)

    def test_detached_helper_waits_for_parent_then_restarts_new_program(self):
        root, private, _, plan, request = self.fixture()
        area = plan.parent
        source = area / 'Stub.cs'
        source.write_text('using System; using System.IO; class Stub { static void Main() {'
            'var root=AppDomain.CurrentDomain.BaseDirectory;'
            'using(File.Open(Path.Combine(root,".update.lock"),FileMode.Open,FileAccess.Read,FileShare.None)) {}'
            'File.WriteAllText(Path.Combine(root,"restarted.txt"),"new program"); }}')
        executable = area / 'Stub.exe'
        compiler = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        # Legacy csc interprets absolute POSIX-style paths as compiler switches.
        compiled = subprocess.run([str(compiler), '/nologo', '/target:winexe',
            '/out:Stub.exe', 'Stub.cs'], cwd=area, capture_output=True)
        self.assertEqual(compiled.returncode, 0, (compiled.stdout + compiled.stderr).decode(errors='replace'))
        archive = Path(request['archive'])
        with zipfile.ZipFile(archive) as bundle:
            files = {name: bundle.read(name) for name in bundle.namelist()}
        files['Application/Server.exe'] = executable.read_bytes()
        info = json.loads(files['Application/BUILD-INFO.json'])
        info['files']['Server.exe'] = sha(executable.read_bytes())
        files['Application/BUILD-INFO.json'] = json.dumps(info).encode()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name, data in files.items():
                bundle.writestr(name, data)
        parent = subprocess.Popen([str(POWERSHELL), '-NoProfile', '-Command', 'Start-Sleep -Seconds 20'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        request.update(parent_pid=parent.pid, sha256=sha(archive.read_bytes()))
        plan.write_text(json.dumps(request), encoding='utf-8')
        helper = subprocess.Popen([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(HELPER), '-Plan', str(plan), '-Headless'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(1)
            self.assertIsNone(helper.poll(), 'Installer did not wait for the parent')
            self.assertEqual((root / 'a.txt').read_bytes(), b'old-a')
            parent.terminate(); parent.wait(timeout=5)
            _, error = helper.communicate(timeout=20)
            self.assertEqual(helper.returncode, 0, error[-2000:])
            for _ in range(30):
                if (root / 'restarted.txt').exists():
                    break
                time.sleep(0.1)
            self.assertEqual((root / 'restarted.txt').read_text(), 'new program')
            for name, data in private.items():
                self.assertEqual((root / name).read_bytes(), data)
        finally:
            if parent.poll() is None:
                parent.terminate(); parent.wait(timeout=5)
            if helper.poll() is None:
                helper.terminate(); helper.wait(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
