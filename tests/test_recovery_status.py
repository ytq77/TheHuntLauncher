"""Home recovery status on fresh and interrupted synthetic installations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import save_resolution_fixture as fixture

world = fixture.world
resolution = fixture.resolution
transfer = resolution.transfer
LAUNCHER = Path(world.__file__).parent


class RecoveryStatusTests(unittest.TestCase):
    def setUp(self):
        area = self.enterContext(tempfile.TemporaryDirectory(prefix='hunt-home-status-'))
        self.root = Path(area).resolve()
        self.running = self.enterContext(patch.object(transfer, 'game_running', return_value=False))

    def assert_status(self, expected):
        before = transfer.snapshot(self.root)
        self.assertEqual(world.recovery_status(self.root), {'ok': True, 'status': expected})
        self.assertEqual(transfer.snapshot(self.root), before)

    def test_fresh_and_initialized_clear_without_full_evidence_inventory(self):
        with patch.object(world, 'evidence_inventory', side_effect=AssertionError('full inventory')):
            self.assert_status('clear')
            saves = fixture.create(self.root, marker=False)
            (saves/'userdata0009').write_bytes(bytes(len(fixture.RAW)))
            (saves/'extra-unrelated.bin').write_bytes(b'not part of a role')
            job = world.layout(self.root)[0]/'jobs'/('a'*32 + '-1-10')/'job.json'
            world.json_file(job, {'state': 'complete'})
            self.assert_status('clear')

    def test_safe_verified_and_waiting_mixed_with_review_required(self):
        saves = fixture.create(self.root, marker=False)
        token = world.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        fixture.request(self.root, slot=0, token=token)
        (saves/'userdata0000').write_bytes(fixture.GOOD)
        self.assertEqual([row['state'] for row in world.review_items(self.root)], ['waiting-backup'])
        self.assert_status('safe')
        (saves/'backup0000').write_bytes(fixture.GOOD)
        self.assertEqual([row['state'] for row in world.review_items(self.root)], ['verified'])
        self.assert_status('safe')
        fixture.request(self.root, slot=1, token=token)
        self.assertEqual({row['state'] for row in world.review_items(self.root)}, {'verified', 'review-required'})
        self.assert_status('blocked')

    def test_safe_record_with_bad_primary_or_separate_gate_is_blocked(self):
        saves = fixture.create(self.root, marker=False)
        fixture.request(self.root)
        (saves/'userdata0000').write_bytes(fixture.GOOD)
        self.assert_status('safe')
        (self.root/resolution.GATE).write_bytes(b'opaque selection gate')
        self.assert_status('blocked')
        (self.root/resolution.GATE).unlink()
        (saves/'userdata0001').write_bytes(b'nonzero unsupported primary')
        self.assert_status('blocked')

    def test_all_start_blocking_markers_include_legacy_and_unfinished_transactions(self):
        for relative, content in (
                (resolution.GATE, b'opaque choice marker'),
                ('data/user/world-recovery/review-required.json', b'opaque world marker'),
                ('.save-import.lock', b'12345'),
                ('.save-import.lock', b'{"operation":"import"}'),
                ('.save-import.lock', b'{"operation":"restore"}'),
                ('.save-import.lock', b'{"operation":"recovery-choice"}'),
                ('.save-import.lock', b'{partial intent'),
                ('.save-conversion.lock', b'{"pid":12345}'),
                ('.save-conversion-pending.json', b'{}')):
            with self.subTest(marker=relative, content=content):
                path = self.root/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                self.assert_status('blocked')
                path.unlink()

    def test_real_interrupted_choice_is_never_resumed(self):
        fixture.create(self.root)
        raw = fixture.make_pending(self.root)
        self.assertEqual(json.loads(raw)['operation'], 'recovery-choice')
        self.assert_status('blocked')

    def test_real_installed_restore_is_left_for_the_saves_page(self):
        saves = fixture.create(self.root, marker=False)
        backups = resolution.backups
        backups.launch_backup(self.root)
        row, = backups.overview(self.root)['backups']
        (saves/'userdata0000').write_bytes(fixture.GOOD)
        finish = backups._finish

        def stop_after_install(*args):
            finish(*args)
            raise OSError('simulated interruption after install')

        with patch.object(backups, '_finish', stop_after_install), self.assertRaises(transfer.ImportRecoveryError):
            backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(backups._intent(self.root)[2], 'complete')
        self.assert_status('blocked')

    def test_primary_read_error_and_game_use_are_not_content_verdicts(self):
        saves = fixture.create(self.root, marker=False)
        stable = world.stable

        def denied(path):
            if path == saves/'userdata0000':
                raise PermissionError('synthetic sharing violation')
            return stable(path)

        before = transfer.snapshot(self.root)
        with patch.object(world, 'stable', denied), self.assertRaises(PermissionError):
            world.recovery_status(self.root)
        self.running.return_value = True
        with self.assertRaises(transfer.OperationUnavailable):
            world.recovery_status(self.root)
        self.assertEqual(transfer.snapshot(self.root), before)

    def test_changes_to_gate_primary_or_world_evidence_require_retry(self):
        saves = fixture.create(self.root, marker=False)
        gate = self.root/resolution.GATE
        journal = self.root/'mod/own-world/own-save-2.bin'
        journal.parent.mkdir()
        for path, data in ((gate, b'new marker'), (saves/'userdata0000', fixture.GOOD),
                           (journal, b'orphan record')):
            with self.subTest(path=path):
                original = world.primary_identities
                def change(root, **kwargs):
                    result = original(root, **kwargs)
                    path.write_bytes(data)
                    return result
                with patch.object(world, 'primary_identities', change), self.assertRaises(world.EvidenceChangedError):
                    world.recovery_status(self.root)
                if path != saves/'userdata0000':
                    path.unlink()

    def test_same_size_primary_rewrite_with_preserved_timestamps_requires_retry(self):
        saves = fixture.create(self.root, marker=False)
        path = saves/'userdata0000'
        original = world.primary_identities
        times = path.stat()

        def replace_after_read(root, **kwargs):
            result = original(root, **kwargs)
            path.write_bytes(fixture.GOOD)
            os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
            return result

        with patch.object(world, 'primary_identities', replace_after_read), self.assertRaises(world.EvidenceChangedError):
            world.recovery_status(self.root)

    @unittest.skipUnless(os.name == 'nt', 'Requires Windows tasklist and byte-range locks.')
    def test_packaged_cli_shape_exit_codes_and_busy_or_unreadable_state(self):
        import msvcrt
        for script in LAUNCHER.glob('*.py'):
            shutil.copyfile(script, self.root/script.name)

        def query(code, status=None):
            before = transfer.snapshot(self.root) if status is not None else None
            result = subprocess.run([sys.executable, '-B', str(self.root/'world_recovery.py'), 'recovery-status'],
                                    cwd=self.root, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            payload = json.loads(result.stdout)
            if status is None:
                self.assertEqual(set(payload), {'ok', 'retryable', 'error'})
                self.assertIs(payload['ok'], False)
                self.assertIs(payload['retryable'], True)
                self.assertIn('暂时无法判断', payload['error'])
            else:
                self.assertEqual(payload, {'ok': True, 'status': status})
                self.assertEqual(transfer.snapshot(self.root), before)

        query(0, 'clear')
        saves = fixture.create(self.root, marker=False)
        fixture.request(self.root)
        (saves/'userdata0000').write_bytes(fixture.GOOD)
        query(0, 'safe')
        lock = self.root/'.save-import.lock'
        lock.write_bytes(b'12345')
        query(0, 'blocked')
        before = transfer.snapshot(self.root)
        with transfer.installation_guard(self.root):
            query(1)
        with lock.open('rb+') as stream:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                query(1)
            finally:
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertEqual(transfer.snapshot(self.root), before)
