"""Real filesystem/entry-point tests on synthetic saves; never touch a game install."""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

LAUNCHER = Path(__file__).resolve().parents[1] / 'launcher'
sys.path.insert(0, str(LAUNCHER))
import client
import save_backups as backups
import save_transfer as transfer
from test_offline_aux import record
REAL_REQUIRE_STOPPED = transfer.require_stopped


# Exit, do not unwind Python contexts: this exercises durable intent and OS lock release.
CRASH_RESTORE = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import save_backups as b
import save_transfer as t
root, name, fingerprint, point = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
t.require_stopped = lambda *args: None  # Synthetic root, no engine is started.
rename = Path.rename
def crash_rename(self, target):
    kind = 'old' if self == root/'data/user/savedata' else 'new' if self.name.startswith('.savedata-restore-') else ''
    if point == kind + '-before': os._exit(87)
    result = rename(self, target)
    if point == kind + '-after': os._exit(87)
    return result
Path.rename = crash_rename
if point == 'copy-partial':
    def partial(source, destination, expected=None):
        destination.mkdir()
        (destination/'partial').write_bytes(b'incomplete')
        os._exit(87)
    t.copy_verified = partial
b.restore(root, name, fingerprint, confirmed=True)
raise RuntimeError('Crash boundary was not reached')
'''


CRASH_LAUNCH_BACKUP = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import save_backups as b
import save_transfer as t
t.require_stopped = lambda *args: None  # Synthetic root, no game process.
copy = t.shutil.copy2
def killed(*args, **kwargs):
    copy(*args, **kwargs)
    os._exit(88)
t.shutil.copy2 = killed
b.launch_backup(Path(sys.argv[2]))
raise RuntimeError('Backup copy crash was not reached')
'''

FRESH_START = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import client
client.ROOT = Path(sys.argv[2])
try:
    client.main()
except ValueError as error:
    print(json.dumps({'blocked': True, 'error': str(error)}, ensure_ascii=False))
else:
    raise RuntimeError('Interrupted restore did not block the real startup entry')
'''


class SaveBackupTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='thehunt-save-backup-test-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name).resolve()
        self.saves = self.root/'data/user/savedata'
        (self.saves/'1/CUSA00207/SPRJ0005').mkdir(parents=True)
        (self.saves/'empty-directory').mkdir()
        self.file = self.saves/'1/CUSA00207/SPRJ0005/userdata0000'
        self.file.write_bytes(b'synthetic position A\x00\xff')
        self.extra = {
            'data/user/trophies.log': b'synthetic unrelated data',
            'mod/own-world/own-save-0.bin.restored': record(0x574F4242, [(1, bytes(125))]),
            'mod/guest-items/taken-save-0.bin': record(0x544C4242, [(52400, b'\x80' + bytes(124))]),
        }
        for name, data in self.extra.items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # Any shared-progress file is pending, not a settled sentinel.
        (self.root/'mod/shared-progress').mkdir()
        self.stopped = self.enterContext(patch.object(transfer, 'require_stopped'))

    def inventory(self, path=None):
        return transfer.content_inventory(transfer.snapshot(path or self.saves))

    def assert_unrelated(self):
        self.assertEqual({name: (self.root/name).read_bytes() for name in self.extra}, self.extra)

    def capture(self):
        result = backups.launch_backup(self.root)
        self.assertTrue(result['created'])
        rows = backups.overview(self.root)['backups']
        return next(row for row in rows if row['id'] == Path(result['backup']).name)

    def prepare_restore(self):
        old = self.inventory()
        row = self.capture()
        self.file.write_bytes(b'synthetic position B, newer progress')
        return row, old, self.inventory()

    def crash(self, row, point):
        result = subprocess.run([sys.executable, '-B', '-c', CRASH_RESTORE, str(LAUNCHER),
                                 str(self.root), row['id'], row['fingerprint'], point],
                                capture_output=True, text=True, encoding='utf-8', timeout=30,
                                env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 87, result.stdout + result.stderr)
        lock = self.root/'.save-import.lock'
        self.assertTrue(lock.is_file())
        intent = json.loads(lock.read_text(encoding='utf-8'))
        start = subprocess.run([sys.executable, '-B', '-c', FRESH_START, str(LAUNCHER), str(self.root)],
                               capture_output=True, text=True, encoding='utf-8', timeout=30,
                               env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
        message = json.loads(start.stdout)
        self.assertTrue(message['blocked'])
        self.assertIn('存档恢复', message['error'])
        self.assertIn(intent['backup'], message['error'])
        self.assertIn(str(self.saves), message['error'])
        return intent

    def finish_pending(self):
        pending = backups.overview(self.root)['pending']
        self.assertTrue(pending['can_resume'], pending)
        result = backups.resume(self.root, pending['fingerprint'], confirmed=True)
        self.assertFalse((self.root/'.save-import.lock').exists())
        self.assertIsNone(backups.overview(self.root)['pending'])
        return result

    def test_backup_hashes_metadata_and_savedata_only(self):
        original = transfer.snapshot(self.saves)
        row = self.capture()
        folder = self.root/'save-backups'/row['id']
        metadata = json.loads((folder/'backup.json').read_text(encoding='utf-8'))
        self.assertEqual(row['file_count'], 1)
        self.assertEqual(row['total_bytes'], self.file.stat().st_size)
        self.assertIsNotNone(datetime.fromisoformat(row['time']).tzinfo)
        item = metadata['files']['1/CUSA00207/SPRJ0005/userdata0000']
        self.assertEqual(item['sha256'], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(set(p.name for p in folder.iterdir()), {'savedata', 'backup.json'})
        self.assertEqual(self.inventory(folder/'savedata'), self.inventory())
        self.assertEqual(transfer.snapshot(self.saves), original)
        self.assert_unrelated()

    def test_missing_and_empty_skip_without_creating_backups(self):
        empty = self.root/'empty-install'
        empty.mkdir()
        self.assertIn('不存在', backups.launch_backup(empty)['message'])
        (empty/'data/user/savedata/empty').mkdir(parents=True)
        self.assertIn('为空', backups.launch_backup(empty)['message'])
        self.assertFalse((empty/'save-backups').exists())

    def test_identical_content_skips_even_if_mtime_changed(self):
        row = self.capture()
        self.file.write_bytes(self.file.read_bytes())
        with patch.object(transfer, 'copy_verified', side_effect=AssertionError('duplicate copied')):
            result = backups.launch_backup(self.root)
        self.assertFalse(result['created'])
        self.assertIn('完全相同', result['message'])
        self.assertEqual([r['id'] for r in backups.overview(self.root)['backups']], [row['id']])

    def test_retention_mixed_types_only_oldest_verified_managed_directories(self):
        base = self.root/'save-backups'
        base.mkdir()
        names = []
        now = datetime.now(timezone.utc) - timedelta(days=3)
        inventory = backups._inventory(transfer.snapshot(self.saves))
        for index in range(11):
            kind = 'launch' if index % 2 else 'pre-restore'
            created = now + timedelta(seconds=index)
            name = f'{kind}-{created:%Y%m%d-%H%M%S}-{index:08x}'
            folder = base/name
            folder.mkdir()
            transfer.copy_verified(self.saves, folder/'savedata')
            backups._write_json(folder/'backup.json', backups._metadata(kind, created.isoformat(), inventory))
            names.append(name)
        protected = ['import-20260101-000000-deadbeef', 'world-recovery-evidence', '.launch-partial.tmp',
                     'launch-not-a-managed-name', 'launch-20260101-000000-12345678']
        for name in protected:
            folder = base/name
            folder.mkdir()
            (folder/'sentinel').write_bytes(b'keep')
        self.file.write_bytes(b'new distinct synthetic progress')
        self.capture()
        self.assertEqual(len(backups.overview(self.root)['backups']), 10)
        self.assertFalse((base/names[0]).exists())
        self.assertFalse((base/names[1]).exists())
        self.assertTrue(all((base/name).is_dir() for name in names[2:]))
        self.assertTrue(all((base/name/'sentinel').read_bytes() == b'keep' for name in protected))

    def test_copy_failure_does_not_prune_or_publish_partial(self):
        row = self.capture()
        self.file.write_bytes(b'newer')
        before = self.inventory()
        def partial(source, destination, expected=None):
            destination.mkdir()
            (destination/'partial').write_bytes(b'x')
            raise OSError('synthetic disk full')
        with patch.object(transfer, 'copy_verified', side_effect=partial):
            with self.assertRaisesRegex(ValueError, '游戏未启动.*synthetic disk full'):
                backups.launch_backup(self.root)
        self.assertEqual(self.inventory(), before)
        self.assertEqual([r['id'] for r in backups.overview(self.root)['backups']], [row['id']])
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_backup_copy_permissions_and_digest_mismatch_preserve_original(self):
        original = self.inventory()
        real_copy = transfer.shutil.copy2
        def corrupt(source, destination, **kwargs):
            result = real_copy(source, destination, **kwargs)
            Path(result).write_bytes(b'corrupt synthetic copy')
            return result
        for copy_action in (PermissionError('synthetic access denied'), corrupt):
            with self.subTest(copy_action=str(copy_action)):
                with patch.object(transfer.shutil, 'copy2', side_effect=copy_action):
                    with self.assertRaisesRegex(ValueError, '启动前备份失败'):
                        backups.launch_backup(self.root)
                self.assertEqual(self.inventory(), original)
                self.assertEqual(backups.overview(self.root)['backups'], [])

    def test_run_session_backup_failure_never_reaches_world_recovery_or_game(self):
        # Only setup/account/engine boundaries are mocked; run_session and copy verification are real.
        original = self.inventory()
        with (patch.object(client, 'ROOT', self.root), patch.object(client, 'prepare') as prepare,
              patch.object(client, 'require_stopped'), patch.object(client, 'state'),
              patch.object(client.world_recovery, 'begin') as begin,
              patch.object(client, 'run_game') as game,
              patch.object(transfer.shutil, 'copy2', side_effect=OSError('synthetic disk full'))):
            with self.assertRaisesRegex(ValueError, '游戏未启动'):
                client.run_session({}, 'fixture', 'fixture', None)
            prepare.assert_called_once()
            begin.assert_not_called()
            game.assert_not_called()
        self.assertEqual(self.inventory(), original)

    def test_run_session_order_and_skip_are_logged_before_world_begin(self):
        sequence = []
        seal = Mock()
        with (patch.object(client, 'ROOT', self.root),
              patch.object(client, 'prepare', side_effect=lambda _: sequence.append('prepare')),
              patch.object(client, 'require_stopped', side_effect=lambda *_: sequence.append('stopped')),
              patch.object(client, 'state', side_effect=lambda *args: sequence.append(args[1])),
              patch.object(client.world_recovery, 'begin', side_effect=lambda _: (sequence.append('begin') or {'THEHUNT_WORLD_RECOVERY_TOKEN': 'fixture'})),
              patch.object(client.world_recovery, 'quit_seal', return_value=seal),
              patch.object(client, 'run_game', side_effect=lambda *args: sequence.append('game') or 0)):
            self.assertEqual(client.run_session({}, 'fixture', 'fixture', None), 0)
            self.assertEqual(sequence[:2], ['prepare', 'stopped'])
            self.assertIn('启动备份已校验', sequence[2])
            self.assertEqual(sequence[3:], ['begin', 'game'])
            sequence.clear()
            self.assertEqual(client.run_session({}, 'fixture', 'fixture', None), 0)
            self.assertIn('完全相同', sequence[2])
        self.assertEqual(seal.close.call_count, 2)

    def test_restore_and_restore_back_preserve_trophies_and_auxiliary(self):
        row, old, current = self.prepare_restore()
        result = backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(self.inventory(), old)
        prior = next(r for r in backups.overview(self.root)['backups'] if r['kind'] == 'pre-restore')
        self.assertEqual(Path(result['backup']), self.root/'save-backups'/prior['id']/'savedata')
        self.assertEqual(self.inventory(Path(result['backup'])), current)
        backups.restore(self.root, prior['id'], prior['fingerprint'], confirmed=True)
        self.assertEqual(self.inventory(), current)
        self.assert_unrelated()

    def test_confirmation_and_stale_selection_are_rejected(self):
        row, _, current = self.prepare_restore()
        for confirmed, fingerprint in ((False, row['fingerprint']), (True, '0'*64)):
            with self.assertRaises(ValueError):
                backups.restore(self.root, row['id'], fingerprint, confirmed=confirmed)
        self.assertEqual(self.inventory(), current)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_corrupt_backup_is_not_listed_restored_or_pruned(self):
        row, _, current = self.prepare_restore()
        folder = self.root/'save-backups'/row['id']
        (folder/'savedata/1/CUSA00207/SPRJ0005/userdata0000').write_bytes(b'corrupt')
        view = backups.overview(self.root)
        self.assertEqual(view['backups'], [])
        self.assertEqual(len(view['warnings']), 1)
        with self.assertRaisesRegex(ValueError, '校验不符'):
            backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        backups._prune(self.root)
        self.assertTrue(folder.exists())
        self.assertEqual(self.inventory(), current)

    def test_all_import_conversion_markers_refuse_without_changes(self):
        row, _, current = self.prepare_restore()
        for name in ('.save-import.lock', '.save-conversion.lock', '.save-conversion-pending.json'):
            with self.subTest(marker=name):
                marker = self.root/name
                marker.write_bytes(b'synthetic pending operation')
                try:
                    with self.assertRaises(ValueError):
                        backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
                    self.assertEqual(marker.read_bytes(), b'synthetic pending operation')
                    self.assertEqual(self.inventory(), current)
                finally:
                    marker.unlink()

    def test_real_world_review_marker_refuses_restore(self):
        row, _, current = self.prepare_restore()
        marker = self.root/'data/user/world-recovery/review-required.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{"reason":"synthetic unresolved world record"}', encoding='utf-8')
        self.assertTrue(backups.world_recovery.review_items(self.root))
        with self.assertRaisesRegex(ValueError, '世界恢复'):
            backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(self.inventory(), current)
        self.assertTrue(marker.exists())

    def test_running_game_and_failed_inspection_refuse_restore(self):
        row, _, current = self.prepare_restore()
        self.stopped.side_effect = REAL_REQUIRE_STOPPED
        with patch.object(transfer, 'game_running', return_value=True):
            with self.assertRaisesRegex(ValueError, '仍在运行'):
                backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        with patch.object(transfer, 'game_running', side_effect=OSError('synthetic process query failure')):
            with self.assertRaisesRegex(ValueError, '无法确认'):
                backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(self.inventory(), current)

    def test_selected_or_live_changes_during_copy_never_overwrite(self):
        row, _, current = self.prepare_restore()
        copy = transfer.copy_verified
        def changed(source, destination, expected=None):
            value = copy(source, destination, expected)
            self.file.write_bytes(b'new progress while copying')
            return value
        with patch.object(transfer, 'copy_verified', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '当前存档发生变化'):
                backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(self.file.read_bytes(), b'new progress while copying')
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_partial_copy_hard_exit_blocks_start_then_cancels_without_overwrite(self):
        row, _, current = self.prepare_restore()
        self.crash(row, 'copy-partial')
        self.assertEqual(backups.overview(self.root)['pending']['action'], 'cancel')
        self.assertEqual(self.finish_pending()['state'], 'cancelled')
        self.assertEqual(self.inventory(), current)
        self.assert_unrelated()

    def crash_at_rename(self, point):
        row, old, current = self.prepare_restore()
        intent = self.crash(row, point)
        self.assertEqual(self.finish_pending()['state'], 'complete')
        self.assertEqual(self.inventory(), old)
        self.assertEqual(self.inventory(Path(intent['backup'])), current)
        self.assert_unrelated()

    def test_crash_before_old_save_rename(self):
        self.crash_at_rename('old-before')

    def test_crash_after_old_save_rename(self):
        self.crash_at_rename('old-after')

    def test_crash_before_install_rename(self):
        self.crash_at_rename('new-before')

    def test_crash_after_install_rename(self):
        self.crash_at_rename('new-after')

    def test_missing_staged_copy_after_old_move_rolls_back_verified_original(self):
        row, _, current = self.prepare_restore()
        intent = self.crash(row, 'old-after')
        stage = Path(intent['stage'])
        self.assertEqual(stage.parent, self.saves.parent)
        self.assertTrue(stage.name.startswith('.savedata-restore-'))
        shutil.rmtree(stage)  # Only this synthetic transaction's staged copy.
        self.assertEqual(backups.overview(self.root)['pending']['action'], 'rollback')
        self.assertEqual(self.finish_pending()['state'], 'cancelled')
        self.assertEqual(self.inventory(), current)

    def test_new_progress_after_crash_is_never_overwritten(self):
        row, _, _ = self.prepare_restore()
        intent = self.crash(row, 'new-after')
        self.file.write_bytes(b'new unrecognized progress after crash')
        lock = (self.root/'.save-import.lock').read_bytes()
        view = backups.overview(self.root)['pending']
        self.assertFalse(view['can_resume'])
        with self.assertRaisesRegex(ValueError, '新的进度'):
            backups.resume(self.root, hashlib.sha256(lock).hexdigest(), confirmed=True)
        self.assertEqual(self.file.read_bytes(), b'new unrecognized progress after crash')
        self.assertEqual((self.root/'.save-import.lock').read_bytes(), lock)
        self.assertTrue(Path(intent['backup']).is_dir())

    def test_unknown_intent_and_changed_lock_are_not_cleared(self):
        row, _, current = self.prepare_restore()
        intent = self.crash(row, 'old-before')
        pending = backups.overview(self.root)['pending']
        with self.assertRaisesRegex(ValueError, '请先确认'):
            backups.resume(self.root, pending['fingerprint'])
        lock = self.root/'.save-import.lock'
        intent['destination'] = str(self.root/'other-save')
        lock.write_text(json.dumps(intent), encoding='utf-8')
        changed = lock.read_bytes()
        self.assertFalse(backups.overview(self.root)['pending']['can_resume'])
        with self.assertRaisesRegex(ValueError, '路径不匹配'):
            backups.resume(self.root, pending['fingerprint'], confirmed=True)
        self.assertEqual(lock.read_bytes(), changed)
        self.assertEqual(self.inventory(), current)

    def test_same_install_guard_is_cross_process_and_not_cross_install(self):
        code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import save_transfer as t
try:
    with t.installation_guard(Path(sys.argv[2])): print('acquired')
except ValueError: print('blocked')
"""
        other = self.root/'other-install'
        other.mkdir()
        with transfer.installation_guard(self.root):
            with transfer.installation_guard(self.root):
                for root, expected in ((self.root, 'blocked'), (other, 'acquired')):
                    result = subprocess.run([sys.executable, '-B', '-c', code, str(LAUNCHER), str(root)],
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), expected)


    def test_pending_auxiliary_refuses_restore_without_any_filesystem_change(self):
        row, _, _ = self.prepare_restore()
        for name, data in (
            ('mod/own-world/own-save-0.bin', record(0x574F4242, [(1, bytes(125))])),
            ('mod/shared-progress/pending-save-0.bin', b'synthetic pending progress'),
            ('mod/guest-items/unknown.bin', b'synthetic unknown ledger'),
        ):
            with self.subTest(name=name):
                pending = self.root/name
                pending.parent.mkdir(parents=True, exist_ok=True)
                pending.write_bytes(data)
                before = transfer.snapshot(self.root)
                try:
                    with self.assertRaises(ValueError) as failure:
                        backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
                    self.assertNotIn('导入', str(failure.exception))
                    self.assertEqual(transfer.snapshot(self.root), before)
                finally:
                    pending.unlink()

    def test_auxiliary_change_during_copy_refuses_before_live_rename(self):
        row, _, current = self.prepare_restore()
        selected = transfer.snapshot(self.root/'save-backups'/row['id'])
        copy = transfer.copy_verified
        def changed(source, destination, expected=None):
            result = copy(source, destination, expected)
            (self.root/'mod/guest-items/taken-save-0.bin').write_bytes(
                record(0x544C4242, [(52400, b'\x81' + bytes(124))]))
            return result
        with patch.object(transfer, 'copy_verified', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '辅助记录发生变化'):
                backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(self.inventory(), current)
        self.assertEqual(transfer.snapshot(self.root/'save-backups'/row['id']), selected)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_pending_auxiliary_after_copy_refuses_before_live_rename(self):
        row, _, current = self.prepare_restore()
        copy = transfer.copy_verified
        pending = self.root/'mod/shared-progress/pending.bin'
        def appeared(source, destination, expected=None):
            result = copy(source, destination, expected)
            pending.write_bytes(b'synthetic pending')
            return result
        with patch.object(transfer, 'copy_verified', side_effect=appeared):
            with self.assertRaisesRegex(ValueError, '共享进度'):
                backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertEqual(self.inventory(), current)
        self.assertEqual(pending.read_bytes(), b'synthetic pending')
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_resume_finish_checks_pending_auxiliary_without_changing_files(self):
        row, _, _ = self.prepare_restore()
        self.crash(row, 'old-before')
        (self.root/'mod/shared-progress/pending.bin').write_bytes(b'synthetic pending')
        before = transfer.snapshot(self.root)
        pending = backups.overview(self.root)['pending']
        self.assertFalse(pending['can_resume'])
        self.assertIn('共享进度', pending['message'])
        fingerprint = hashlib.sha256((self.root/'.save-import.lock').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, '共享进度'):
            backups.resume(self.root, fingerprint, confirmed=True)
        self.assertEqual(transfer.snapshot(self.root), before)

    def resume_original_with_pending_shared(self, point, *, remove_stage=False):
        row, _, current = self.prepare_restore()
        intent = self.crash(row, point)
        if remove_stage:
            stage = Path(intent['stage'])
            self.assertEqual(stage.parent, self.saves.parent)
            self.assertTrue(stage.name.startswith('.savedata-restore-'))
            shutil.rmtree(stage)
        pending = self.root/'mod/shared-progress/pending.bin'
        pending.write_bytes(b'synthetic pending')
        self.assertEqual(self.finish_pending()['state'], 'cancelled')
        self.assertEqual(self.inventory(), current)
        self.assertEqual(pending.read_bytes(), b'synthetic pending')

    def test_resume_cancel_does_not_apply_new_auxiliary_gate_to_untouched_original(self):
        self.resume_original_with_pending_shared('copy-partial')

    def test_resume_rollback_does_not_apply_new_auxiliary_gate_to_verified_original(self):
        self.resume_original_with_pending_shared('old-after', remove_stage=True)

    def test_launch_backup_hard_exit_leaves_no_import_lock_or_listed_partial(self):
        before = self.inventory()
        result = subprocess.run([sys.executable, '-B', '-c', CRASH_LAUNCH_BACKUP,
                                 str(LAUNCHER), str(self.root)], capture_output=True,
                                text=True, encoding='utf-8', timeout=30,
                                env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 88, result.stdout + result.stderr)
        self.assertFalse((self.root/'.save-import.lock').exists())
        view = backups.overview(self.root)
        self.assertIsNone(view['pending'])
        self.assertEqual(view['backups'], [])
        self.assertTrue(list((self.root/'save-backups').glob('.launch-*.tmp')))
        self.assertEqual(self.inventory(), before)
        # A new process enters the real startup and passes all transaction-lock gates.
        # It deliberately stops at missing synthetic fixture notices, before account/game work.
        start = subprocess.run([sys.executable, '-B', '-c', FRESH_START, str(LAUNCHER), str(self.root)],
                               capture_output=True, text=True, encoding='utf-8', timeout=30,
                               env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
        self.assertIn('署名文件', json.loads(start.stdout)['error'])
        self.capture()  # The abandoned OS guard was released and the next backup completes.
        self.assertEqual(len(backups.overview(self.root)['backups']), 1)

    def test_launch_backup_refuses_existing_import_lock_even_when_save_missing(self):
        lock = self.root/'.save-import.lock'
        lock.write_bytes(b'synthetic existing import')
        for root in (self.root, self.root/'empty-install'):
            with self.subTest(root=root.name):
                root.mkdir(exist_ok=True)
                (root/'.save-import.lock').write_bytes(b'synthetic existing import')
                before = transfer.snapshot(root)
                with self.assertRaisesRegex(ValueError, '导入或恢复尚未完成'):
                    backups.launch_backup(root)
                self.assertEqual(transfer.snapshot(root), before)

    def ten_old_backups(self):
        base = self.root/'save-backups'
        base.mkdir()
        inventory = backups._inventory(transfer.snapshot(self.saves))
        oldest = None
        for index in range(10):
            created = datetime.now(timezone.utc) - timedelta(days=2, seconds=10-index)
            name = f'launch-{created:%Y%m%d-%H%M%S}-{index:08x}'
            folder = base/name
            folder.mkdir()
            transfer.copy_verified(self.saves, folder/'savedata')
            backups._write_json(folder/'backup.json', backups._metadata('launch', created.isoformat(), inventory))
            if oldest is None: oldest = folder
        self.file.write_bytes(b'new synthetic launch progress')
        return oldest

    def test_prune_permission_error_warns_but_real_run_session_reaches_game(self):
        oldest = self.ten_old_backups()
        original = self.inventory()
        seal = Mock()
        with (patch.object(client, 'ROOT', self.root), patch.object(client, 'prepare'),
              patch.object(client, 'require_stopped'), patch.object(client, 'state') as state,
              patch.object(client.world_recovery, 'begin', return_value={'THEHUNT_WORLD_RECOVERY_TOKEN':'fixture'}),
              patch.object(client.world_recovery, 'quit_seal', return_value=seal),
              patch.object(client, 'run_game', return_value=0) as game,
              patch.object(backups.shutil, 'rmtree', side_effect=PermissionError('synthetic occupied old backup'))):
            self.assertEqual(client.run_session({}, 'fixture', 'fixture', None), 0)
            game.assert_called_once()
            self.assertIn('旧备份清理未完成', state.call_args.args[1])
        self.assertEqual(self.inventory(), original)
        self.assertFalse(oldest.exists())
        self.assertEqual(len(list((self.root/'save-backups').glob('.prune-*.tmp'))), 1)
        view = backups.overview(self.root)
        self.assertEqual(len(view['backups']), 10)
        self.assertEqual(view['warnings'], [])
        latest = view['backups'][0]
        self.assertEqual(self.inventory(self.root/'save-backups'/latest['id']/'savedata'), original)

    def test_partial_prune_stays_hidden_and_does_not_break_duplicate_skip(self):
        self.ten_old_backups()
        def partial(folder):
            self.assertEqual(folder.resolve().parent, (self.root/'save-backups').resolve())
            self.assertTrue(folder.name.startswith('.prune-'))
            (folder/'backup.json').unlink()
            raise PermissionError('synthetic partial deletion')
        with patch.object(backups.shutil, 'rmtree', side_effect=partial):
            result = backups.launch_backup(self.root)
        self.assertTrue(result['created'])
        self.assertIn('旧备份清理未完成', result['message'])
        self.assertEqual(len(backups.overview(self.root)['backups']), 10)
        self.assertEqual(backups.overview(self.root)['warnings'], [])
        self.assertFalse(backups.launch_backup(self.root)['created'])

    def test_prune_rename_failure_keeps_old_backup_complete_without_blocking_launch(self):
        oldest = self.ten_old_backups()
        old = transfer.snapshot(oldest)
        rename = Path.rename
        def locked(path, target):
            if path == oldest: raise PermissionError('synthetic rename denial')
            return rename(path, target)
        with patch.object(Path, 'rename', locked):
            result = backups.launch_backup(self.root)
        self.assertTrue(result['created'])
        self.assertIn('旧备份清理未完成', result['message'])
        self.assertEqual(transfer.snapshot(oldest), old)
        self.assertEqual(len(backups.overview(self.root)['backups']), 11)

    def test_torn_manifest_can_only_cancel_when_original_is_verified_unmoved(self):
        row, _, current = self.prepare_restore()
        intent = self.crash(row, 'old-before')
        manifest = Path(intent['backup']).parent/'install.json'
        manifest.write_bytes(b'{"schema":')
        self.assertEqual(backups.overview(self.root)['pending']['action'], 'cancel')
        self.assertEqual(self.finish_pending()['state'], 'cancelled')
        self.assertEqual(self.inventory(), current)
        self.assertTrue(Path(intent['stage']).is_dir())  # Retained scratch, not a selectable backup.
        self.assertEqual(len(backups.overview(self.root)['backups']), 1)

    def test_torn_manifest_after_original_move_stays_blocked_and_preserves_evidence(self):
        row, _, _ = self.prepare_restore()
        intent = self.crash(row, 'old-after')
        (Path(intent['backup']).parent/'install.json').write_bytes(b'{"schema":')
        before = transfer.snapshot(self.root)
        self.assertFalse(backups.overview(self.root)['pending']['can_resume'])
        fingerprint = hashlib.sha256((self.root/'.save-import.lock').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, '不能证明原存档未移动'):
            backups.resume(self.root, fingerprint, confirmed=True)
        self.assertEqual(transfer.snapshot(self.root), before)

    def test_missing_live_savedata_has_restore_specific_error_and_no_write(self):
        row, _, _ = self.prepare_restore()
        self.saves.rename(self.saves.with_name('synthetic-live-save-held'))
        before = transfer.snapshot(self.root)
        with self.assertRaisesRegex(ValueError, '当前无缝版 savedata') as failure:
            backups.restore(self.root, row['id'], row['fingerprint'], confirmed=True)
        self.assertNotIn('普通版', str(failure.exception))
        self.assertEqual(transfer.snapshot(self.root), before)


    def test_json_backend_lists_and_requires_confirmation_without_game(self):
        row, _, current = self.prepare_restore()
        with (patch.object(backups, 'ROOT', self.root),
              patch.object(sys, 'stdin', io.StringIO('{"action":"list"}')),
              patch.object(sys, 'stdout', io.StringIO()) as output):
            self.assertEqual(backups.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['backups'][0]['id'], row['id'])
        request = {'action': 'restore', 'id': row['id'], 'fingerprint': row['fingerprint']}
        with (patch.object(backups, 'ROOT', self.root),
              patch.object(sys, 'stdin', io.StringIO(json.dumps(request))),
              patch.object(sys, 'stdout', io.StringIO()) as output):
            self.assertEqual(backups.main(), 1)
            self.assertIn('请先确认', json.loads(output.getvalue())['error'])
        self.assertEqual(self.inventory(), current)


if __name__ == '__main__':
    unittest.main()
