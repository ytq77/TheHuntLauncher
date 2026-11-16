"""Real entry-point and OS-lock tests on temporary synthetic installations only."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

LAUNCHER = Path(__file__).resolve().parents[1] / 'launcher'
sys.path.insert(0, str(LAUNCHER))
import client
import conversion_jobs as conversion
import save_backups as backups
import save_resolution as resolution
import save_transfer as transfer
import world_recovery as world
from synthetic_world_fixture import fixture_pair


CLI = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import conversion_jobs as conversion
conversion.ROOT = Path(sys.argv[2])
raise SystemExit(conversion.main())
'''

HOLD_LOCK = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import conversion_jobs as conversion
import save_transfer as transfer
root = Path(sys.argv[2])
conversion.ROOT = root
lock = conversion.operation_lock() if sys.argv[3] == 'conversion' else transfer.installation_guard(root)
with lock:
    print('LOCKED', flush=True)
    sys.stdin.read(1)
'''


class SaveResolutionGateTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='thehunt-resolution-gates-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.gate = self.root / 'data/user/save-resolution-review.json'
        # Opaque bytes are deliberately not valid JSON and must never be executed.
        self.gate.parent.mkdir(parents=True)
        self.enterContext(patch.object(client, 'ROOT', self.root))
        self.enterContext(patch.object(conversion, 'ROOT', self.root))

    def inventory(self):
        return {path.relative_to(self.root).as_posix():
                ('directory' if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest())
                for path in self.root.rglob('*')
                if path.name not in ('.save-transfer.guard', 'launcher-state.json')}

    def blocking_states(self):
        self.gate.write_bytes(b'{opaque review gate; path=Z:/never-follow-this}')
        yield 'opaque-gate'
        self.gate.unlink()
        lock = self.root / '.save-import.lock'
        lock.write_text(json.dumps({'operation': 'recovery-choice'}), encoding='utf-8')
        yield 'a-transaction'
        lock.unlink()

    def expected_block(self):
        with self.assertRaises(ValueError) as caught:
            resolution.require_launch_clear(self.root)
        return str(caught.exception)

    def assert_blocked(self, action, message):
        before = self.inventory()
        with self.assertRaises(ValueError) as caught:
            action()
        self.assertIn(message, str(caught.exception))
        self.assertEqual(self.inventory(), before)

    def test_real_startup_and_session_reject_before_any_preparation(self):
        for state in self.blocking_states():
            with self.subTest(state=state):
                message = self.expected_block()
                # No prepare/build-environment/game mock: the incomplete profile
                # would fail if real preparation were reached at all.
                self.assert_blocked(lambda: client.run_session({}, '', '', None), message)
                self.assertEqual(json.loads((self.root / 'launcher-state.json').read_text(encoding='utf-8'))['phase'],
                                 'save-resolution-needed')
                self.assert_blocked(client.main, message)
                self.assertFalse((self.root / 'save-backups').exists())
                self.assertFalse((self.root / 'data/user/world-recovery').exists())

    def test_real_client_process_keeps_the_structured_resolution_phase(self):
        self.gate.write_bytes(b'opaque gate for a fresh launcher process')
        script = self.root / 'client.py'
        shutil.copy2(LAUNCHER / 'client.py', script)
        result = subprocess.run([sys.executable, '-B', str(script)], capture_output=True, text=True,
            encoding='utf-8', timeout=30, env=dict(os.environ, PYTHONUTF8='1',
                PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(LAUNCHER)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        status = json.loads((self.root / 'launcher-state.json').read_text(encoding='utf-8'))
        self.assertEqual(status['phase'], 'save-resolution-needed')
        self.assertIn(self.expected_block(), status['message'])
        self.assertFalse((self.root / 'data/user/world-recovery').exists())

    @unittest.skipUnless(os.name == 'nt', 'Requires the real Windows stopped-game check.')
    def test_old_world_failure_routes_to_resolution_after_real_prepare_and_backup(self):
        with tempfile.TemporaryDirectory(prefix='thehunt-synthetic-ordinary-') as area:
            ordinary = Path(area)
            (ordinary / 'Bloodborne.exe').write_bytes(b'not an executable; never launched')
            (ordinary / 'bbport.ini').write_text('synthetic ini', encoding='utf-8')
            (ordinary / 'eboot.bin').write_bytes(b'not game data; presence check only')
            profile = {'install': str(ordinary), 'game': str(ordinary),
                       'graphics_source': str(ordinary / 'bbport.ini')}
            marker = self.root / 'data/user/world-recovery/review-required.json'
            marker.parent.mkdir()
            marker.write_bytes(b'opaque legacy review marker')
            before = self.inventory()
            # Real initialized prepare and real launch_backup skip, then the
            # real legacy marker refusal. No preparation/game stub is used.
            with self.assertRaises(client.SaveResolutionRequired):
                client.run_session(profile, '', '', None)
            self.assertEqual(self.inventory(), before)
            status = json.loads((self.root / 'launcher-state.json').read_text(encoding='utf-8'))
            self.assertEqual(status['phase'], 'save-resolution-needed')
            self.assertFalse((marker.parent / 'runs').exists())

    def test_direct_world_entry_points_never_replay_or_create_a_run(self):
        for state in self.blocking_states():
            with self.subTest(state=state):
                message = self.expected_block()
                self.assert_blocked(lambda: world.begin(self.root), message)
                self.assert_blocked(lambda: world.finalize(self.root, None), message)
                self.assert_blocked(lambda: world.require_review_clear_for_transfer(self.root), message)
                self.assertFalse((self.root / 'data/user/world-recovery').exists())

    @unittest.skipUnless(os.name == 'nt', 'Requires the real Windows stopped-game check.')
    def test_gate_blocks_real_quit_replay_and_normal_waiting_behavior_is_unchanged(self):
        raw, restored = fixture_pair()
        folder, saves = world.layout(self.root)
        saves.mkdir(parents=True)
        for stem in ('userdata', 'backup'):
            (saves / (stem + '0000')).write_bytes(raw)
        token = world.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        bank = world.offline_aux.flag_bank(raw)
        row = (1, 5, raw[bank + 625:bank + 750], restored[bank + 625:bank + 750])
        journal = struct.pack('<III', 0x574F4242, 125, 1) + struct.pack('<I', 1) + row[3]
        own = self.root / 'mod/own-world/own-save-0.bin'
        own.parent.mkdir(parents=True)
        own.write_bytes(journal)
        baseline = world.HEADER.pack(b'THWR', 2, 1, token.encode(), 0, world.CAPTURE, 1, world.fnv(journal))
        baseline += world.ROW.pack(1, 5, row[3], row[3])
        world.atomic(folder / 'baselines/world-save-0.bin', baseline)
        world.atomic(folder / 'baselines/world-save-0.bbow', journal)
        request = world.HEADER.pack(b'THWR', 2, 1, token.encode(), 0, world.QUIT, 1, world.fnv(journal))
        world.atomic(folder / 'requests/world-save-0.bin', request + world.ROW.pack(*row))
        world.process_exited(self.root, token, 0)
        self.gate.write_bytes(b'opaque gate over a fully replayable old request')
        self.assert_blocked(lambda: world.finalize(self.root, token, quit_sequence=1), self.expected_block())
        self.assert_blocked(lambda: world.begin(self.root), self.expected_block())
        self.assertEqual((saves / 'userdata0000').read_bytes(), raw)
        self.gate.unlink()  # Test setup only, not a production way to resolve a gate.
        result = world.finalize(self.root, token, quit_sequence=1)
        self.assertEqual(result, ['primary-restored-waiting-backup-save'])
        self.assertEqual((saves / 'userdata0000').read_bytes(), restored)
        self.assertEqual((saves / 'backup0000').read_bytes(), raw)
        # The pre-existing verified/waiting exit is not routed to A or relaxed.
        world.begin(self.root)
        self.assertEqual((saves / 'userdata0000').read_bytes(), restored)
        self.assertEqual((saves / 'backup0000').read_bytes(), raw)

    def test_launch_backup_blocks_even_the_missing_savedata_skip_path(self):
        for state in self.blocking_states():
            with self.subTest(state=state):
                self.assert_blocked(lambda: backups.launch_backup(self.root), self.expected_block())

    def test_ordinary_restore_and_resume_cannot_bypass_the_gate(self):
        for state in self.blocking_states():
            with self.subTest(state=state):
                message = self.expected_block()
                self.assert_blocked(lambda: backups.restore(self.root, 'unused', 'unused', confirmed=True), message)
                self.assert_blocked(lambda: backups.resume(self.root, 'unused', confirmed=True), message)

    def test_all_public_conversion_mutators_block_before_their_business_logic(self):
        calls = {
            'create': lambda: conversion.create_job({}, False),
            'step': lambda: conversion.run_step({}, 'baseline'),
            'export': lambda: conversion.export_job({}, False),
            'offline': lambda: conversion.begin_offline({}, False, 'export'),
            'install': lambda: conversion.install_converted_job({}, False),
            'pending': conversion.recover_pending_install,
            'dispatch': lambda: conversion.dispatch({'action': 'create'}),
        }
        for state in self.blocking_states():
            message = self.expected_block()
            for name, action in calls.items():
                with self.subTest(state=state, entry=name):
                    self.assert_blocked(action, message)
                    self.assertFalse((self.root / '.save-conversion.lock').exists())

    def conversion_cli(self):
        return subprocess.run([sys.executable, '-B', '-c', CLI, str(LAUNCHER), str(self.root)],
            input=json.dumps({'action': 'create'}), capture_output=True, text=True,
            encoding='utf-8', timeout=30, env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))

    def test_real_json_conversion_cli_obeys_both_new_blockers(self):
        for state in self.blocking_states():
            with self.subTest(state=state):
                before = self.inventory()
                result = self.conversion_cli()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                value = json.loads(result.stdout)
                self.assertFalse(value['ok'])
                self.assertIn(self.expected_block(), value['error'])
                self.assertEqual(self.inventory(), before)

    def test_gate_read_failure_is_not_treated_as_absence(self):
        self.gate.mkdir()
        before = self.inventory()
        with self.assertRaises((OSError, ValueError)):
            resolution.require_launch_clear(self.root)
        with self.assertRaises((OSError, ValueError)):
            world.begin(self.root)
        self.assertEqual(self.inventory(), before)

    @unittest.skipUnless(os.name == 'nt', 'Requires the real Windows named mutex and tasklist.')
    def test_unrelated_ordinary_import_lock_still_uses_existing_guards(self):
        lock = self.root / '.save-import.lock'
        lock.write_text(json.dumps({'operation': 'restore'}), encoding='utf-8')
        resolution.require_launch_clear(self.root)
        with transfer.installation_guard(self.root):
            # The ordinary restore/resume path owns this lock legitimately.
            backups._restore_ready(self.root, own_import_lock=True)
        with self.assertRaisesRegex(ValueError, '导入/恢复锁'):
            backups._restore_ready(self.root)
        with self.assertRaisesRegex(ValueError, '导入或恢复尚未完成'):
            backups.launch_backup(self.root)
        with self.assertRaisesRegex(ValueError, '存档导入尚未结束'):
            conversion.create_job({}, False)
        self.assertTrue(lock.exists())

    @contextmanager
    def held_by_other_process(self, kind='installation'):
        process = subprocess.Popen([sys.executable, '-B', '-c', HOLD_LOCK, str(LAUNCHER), str(self.root), kind],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
        try:
            # A child that exits early closes stdout; no sleep-based assumption
            # that it acquired the production mutex.
            self.assertEqual(process.stdout.readline().strip(), 'LOCKED', process.stderr.read() if process.poll() is not None else '')
            yield process
        finally:
            if process.poll() is None:
                process.communicate('x', timeout=10)
            else:
                process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0)

    @unittest.skipUnless(os.name == 'nt', 'Requires the real Windows named mutex and tasklist.')
    def test_real_cross_process_mutex_blocks_all_direct_entries_and_cli(self):
        before = self.inventory()
        with self.held_by_other_process():
            calls = [lambda: client.run_session({}, '', '', None),
                     lambda: world.begin(self.root), lambda: world.finalize(self.root, None),
                     lambda: backups.launch_backup(self.root),
                     lambda: backups.restore(self.root, 'unused', 'unused', confirmed=True),
                     lambda: conversion.create_job({}, False),
                     lambda: conversion.install_converted_job({}, False),
                     conversion.recover_pending_install]
            for action in calls:
                with self.assertRaisesRegex(ValueError, '另一个游戏会话或存档操作'):
                    action()
            result = self.conversion_cli()
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('另一个游戏会话或存档操作', json.loads(result.stdout)['error'])
        self.assertEqual(self.inventory(), before)

    @unittest.skipUnless(os.name == 'nt', 'Requires the real Windows named mutex and tasklist.')
    def test_conversion_holds_the_same_mutex_and_nested_calls_keep_the_original_lock(self):
        with self.held_by_other_process('conversion'):
            with self.assertRaisesRegex(ValueError, '另一个游戏会话或存档操作'):
                with transfer.import_lock(self.root):
                    self.fail('Import acquired the installation while conversion owned it.')
        self.assertFalse((self.root / '.save-conversion.lock').exists())
        with conversion.operation_lock():
            lock = self.root / '.save-conversion.lock'
            original = lock.read_bytes()
            with conversion.operation_lock():
                with self.assertRaisesRegex(ValueError, '同一个角色'):
                    conversion.create_job({}, False)
                self.assertEqual(lock.read_bytes(), original)
            self.assertEqual(lock.read_bytes(), original)
        self.assertFalse(lock.exists())

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows process enumeration.')
    def test_real_tasklist_rejects_a_controlled_non_game_process(self):
        # A renamed Microsoft ping binary only sends loopback traffic. This is
        # not the game executable and never reads any installation/save data.
        probe = self.root / 'bb-probe.exe'
        source = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/ping.exe'
        shutil.copy2(source, probe)
        process = subprocess.Popen([str(probe), '-t', '127.0.0.1'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not transfer.game_running() and time.monotonic() < deadline:
                self.assertIsNone(process.poll(), 'Controlled process exited before tasklist saw it.')
                time.sleep(.05)
            self.assertTrue(transfer.game_running())
            with self.assertRaisesRegex(ValueError, '游戏仍在运行'):
                world.begin(self.root)
            with self.assertRaisesRegex(ValueError, '游戏仍在运行'):
                conversion.create_job({}, False)
            self.assertFalse((self.root / '.save-conversion.lock').exists())
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
