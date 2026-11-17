import errno
import hashlib
import json
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import save_resolution_fixture as fixture

s, w = fixture.resolution, fixture.world


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hunt-resolution-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.saves = fixture.create(self.root)
        self.sentinels = {p: (self.root/p).read_bytes() for p in ('profile.json', 'data/user/trophies.log')}

    def tearDown(self):
        for path, raw in self.sentinels.items():
            self.assertEqual((self.root/path).read_bytes(), raw)

    def content(self):
        return s._content(s._capture(self.root))

    def choose(self, action, selection=None):
        return fixture.choose(self.root, action, selection)

    def automatic(self):
        # Existing production backup creator, not a manufactured backup.json.
        s.backups.launch_backup(self.root)
        return s.backups._list(self.root)[0][0]

    def test_old_deadlock_and_accept_all_real_functions(self):
        before = self.content()
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            w.begin(self.root)
        with self.assertRaisesRegex(ValueError, '复核'):
            s.backups._restore_ready(self.root)
        plan = s.plan(self.root)
        self.assertEqual(plan['options'][0]['id'], 'accept-all')
        self.assertTrue(plan['options'][0]['recommended'])
        result = self.choose('accept-all')
        self.assertEqual(result['result'], 'accepted-current-unverified')
        self.assertEqual(self.content()['trees']['savedata'], before['trees']['savedata'])
        case = s._case_path(self.root, result['case_id'])
        self.assertEqual(s._original_content(case), before)
        self.assertFalse((self.root/'.save-import.lock').exists())
        self.assertFalse(s.plan(self.root)['needed'])
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN', w.begin(self.root))
        row, = s.list_cases(self.root)['backups']
        self.assertEqual(len(row['characters']), 2)
        self.assertIn('换回后需重新选择', row['label'])

    def test_accept_bound_preserves_other_role_and_shared_run(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        request = fixture.request(self.root, job=True)
        before = self.content()
        run = self.root/f"data/user/world-recovery/runs/{request['token']}.json"
        run_bytes = run.read_bytes()
        plan = s.plan(self.root)
        self.assertEqual(plan['options'][0]['id'], 'accept-bound')
        self.choose('accept-bound')
        self.assertEqual(run.read_bytes(), run_bytes)
        self.assertEqual(self.content()['trees']['savedata'], before['trees']['savedata'])
        self.assertFalse(request['path'].exists())
        self.assertFalse(w.job_for(request).exists())
        self.assertEqual(w.review_items(self.root), [])
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN', w.begin(self.root))

    def test_bound_does_not_guess_ack_or_different_character(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        request = fixture.request(self.root)
        w.atomic(request['folder']/'acks/0.ack', (2).to_bytes(8, 'little'))
        self.assertEqual(s.plan(self.root)['options'][0]['id'], 'accept-all')
        (request['folder']/'acks/0.ack').unlink()
        raw = bytearray((self.saves/'userdata0000').read_bytes())
        raw[w.offline_save.parse(raw).face+4] ^= 7
        (self.saves/'userdata0000').write_bytes(raw)
        self.assertNotIn('accept-bound', [o['id'] for o in s.plan(self.root)['options']])

    def test_bound_rename_failure_after_parent_creation_resumes(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        request = fixture.request(self.root, job=True)
        before = self.content()
        rename = Path.rename
        def fail(path, target):
            if path == request['path']:
                raise PermissionError('synthetic sharing violation after move parents exist')
            return rename(path, target)
        with patch.object(Path, 'rename', fail), self.assertRaises(PermissionError):
            self.choose('accept-bound')
        self.assertEqual(self.content(), before)
        pending = s.pending(self.root)
        self.assertTrue(pending['can_resume'])
        case, intent, raw = s._intent(self.root)
        self.assertEqual(s._position(self.root, case, intent), 0)
        self.assertEqual(s._actual(self.root, case)['retired'], s._retired_scaffold(intent))
        s.resume(self.root, pending['fingerprint'], confirmed=True)
        self.assertEqual(w.review_items(self.root), [])

    def test_malformed_backup_metadata_does_not_block_other_choices(self):
        backup = self.automatic()
        path = backup['_folder']/'backup.json'
        valid = json.loads(path.read_bytes())
        for value in ('missing', None, 7, []):
            bad = dict(valid)
            if value == 'missing':
                bad.pop('created_at')
            else:
                bad['created_at'] = value
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.subTest(value=value):
                plan = s.plan(self.root)
                self.assertEqual(plan['options'][0]['id'], 'accept-all')
                self.assertNotIn('restore-all', [o['id'] for o in plan['options']])
                self.assertTrue(plan['warnings'])

    def test_profile_readability_reason_is_not_mislabeled_as_bad_roles(self):
        (self.saves/'userdata0010').write_bytes(b'bad profile')
        self.choose('preserve')
        row, = s.list_cases(self.root)['backups']
        self.assertIn('角色列表或身份待核对', row['label'])
        self.assertIn('userdata0010', row['readability_note'])
        self.assertTrue(all(r['primary'] == r['backup'] == 'readable' for r in row['characters']))

    def test_same_bytes_backup_replacement_invalidates_exact_source_plan(self):
        backup = self.automatic()
        plan = s.plan(self.root)
        path = backup['_folder']/'savedata/1/CUSA00207/SPRJ0005/userdata0000'
        temporary = path.with_suffix('.new')
        temporary.write_bytes(path.read_bytes())
        temporary.replace(path)
        selection = {'selection_'+k: plan['selection'][k] for k in ('kind', 'id', 'fingerprint')}
        with self.assertRaisesRegex(ValueError, '变化'):
            s.apply(self.root, plan['digest'], 'restore-all', confirmed=True, **selection)

    def test_unused_primary_with_nonzero_backup_can_start_but_is_not_restore_source(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        (self.root/s.GATE).write_bytes(b'gate requires a real clean check')
        for backup_raw in (b'unsupported backup', fixture.RAW):
            with self.subTest(backup=backup_raw[:12]):
                (self.saves/'userdata0000').write_bytes(bytes(0x140000))
                (self.saves/'backup0000').write_bytes(backup_raw)
                # The unchanged old validator really does miss this combination.
                s.transfer.validate_savedata(self.root/'data/user/savedata')
                report = s._readability(self.root/'data/user/savedata')
                self.assertFalse(report['valid'])
                self.assertGreater(report['readable_generations'], 0)
                plan = s.plan(self.root)
                self.assertIn('use-current', [o['id'] for o in plan['options']])
                self.assertNotIn('accept-all', [o['id'] for o in plan['options']])
                self.choose('preserve')
                self.assertTrue(s.list_cases(self.root)['backups'])
        (self.root/s.GATE).unlink()
        candidate = self.automatic()
        rows, warnings = s._ordinary(self.root)
        self.assertNotIn(candidate['id'], [r['id'] for r in rows])
        self.assertTrue(warnings)

    def test_incomplete_matching_job_cancels_bound_without_new_orphan(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        request = fixture.request(self.root)
        job = w.job_for(request)
        job.mkdir(parents=True)
        (job/'partial-evidence').write_bytes(b'not a complete manifest')
        plan = s.plan(self.root)
        self.assertEqual([r['kind'] for r in w.review_items(self.root)], ['request'])
        self.assertEqual(plan['options'][0]['id'], 'accept-all')
        self.choose('accept-all')
        self.assertEqual(w.review_items(self.root), [])

    def test_missing_required_backup_uses_all_branch_but_denied_io_stays_error(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        fixture.request(self.root)
        missing = self.saves/'backup0000'
        missing.unlink()
        self.assertEqual(s.plan(self.root)['options'][0]['id'], 'accept-all')
        missing.write_bytes(fixture.RAW)
        real = Path.read_bytes
        def deny(path):
            if path == missing:
                raise PermissionError('synthetic backup sharing denial')
            return real(path)
        with patch.object(Path, 'read_bytes', deny), self.assertRaises(PermissionError):
            s.plan(self.root)

    def test_unreadable_all_restore_notice_promises_preservation_not_undo(self):
        self.automatic()
        for path in self.saves.iterdir():
            if path.name != 'userdata0010':
                path.write_bytes(b'unsupported synthetic role')
        plan = s.plan(self.root)
        option = next(o for o in plan['options'] if o['id'] == 'restore-all')
        self.assertIn('不会列为可换回项', option['description'])
        self.assertNotIn('可在列表换回', option['description'])
        self.choose('restore-all')
        self.assertEqual(s.list_cases(self.root)['backups'], [])

    def test_restore_case_notice_distinguishes_source_and_unreadable_current(self):
        self.choose('accept-all')
        row, = s.list_cases(self.root)['backups']
        for path in self.saves.iterdir():
            if path.name != 'userdata0010':
                path.write_bytes(b'unsupported current branch')
        plan = s.plan(self.root, **{'selection_'+k: row[k] for k in ('kind', 'id', 'fingerprint')})
        text = plan['options'][0]['description']
        self.assertIn('不会列为可换回项', text)
        self.assertIn('该版本各角色代次已通过', text)
        self.assertNotIn('包括读不出的角色', text)
        result = self.choose('restore-case', row)
        self.assertNotIn(result['case_id'], [r['id'] for r in s.list_cases(self.root)['backups']])

    def test_record_limits_and_real_self_read_fail_before_durable_lock(self):
        before = self.content()
        with patch.object(s, 'MAX_RECORD_BYTES', 512), self.assertRaisesRegex(ValueError, '超出当前支持'):
            self.choose('accept-all')
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'.save-import.lock').exists())
        reader = s._json
        def deny(path):
            if Path(path).name == 'intent.json':
                raise PermissionError('synthetic intent self-read denial')
            return reader(path)
        with patch.object(s, '_json', deny), self.assertRaises(PermissionError):
            self.choose('accept-all')
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_atomic_lock_temporary_is_private_and_ignored(self):
        before = self.content()
        def stop(name):
            if name == 'after:publish-temp:.save-import.lock':
                raise RuntimeError('fault after real temporary fsync before publication')
        with patch.object(s, '_checkpoint', stop), self.assertRaises(RuntimeError):
            self.choose('accept-all')
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'.save-import.lock').exists())
        temporary, = self.root.glob('.save-import.lock.*.tmp')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
        import package
        self.assertTrue(package.private_path(temporary.name))
        with self.assertRaises(ValueError):
            package.check_public([temporary.name])
        # Real Git rule evaluation, no tracked or user files created.
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(['git', '-C', str(repo), 'check-ignore', '--', temporary.name],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reparse_source_is_hard_block_and_no_case_created(self):
        # Windows directory junction is an actual filesystem reparse point.
        if sys.platform != 'win32':
            self.skipTest('Windows reparse-specific fixture')
        target = self.root/'safe-synthetic-target'
        target.mkdir()
        link = self.root/'mod/guest-items'
        command = ['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)]
        made = subprocess.run(command, capture_output=True, text=True, encoding='oem')
        self.assertEqual(made.returncode, 0, made.stdout+made.stderr)
        try:
            with self.assertRaisesRegex(ValueError, '联接'):
                s.plan(self.root)
            self.assertFalse((self.root/'save-backups').exists())
        finally:
            # rmdir of this exact verified junction does not traverse the target.
            self.assertEqual(link.resolve(), target.resolve())
            link.rmdir()

    def test_structural_conflicting_prefixes_and_foreign_gate_are_not_resumed(self):
        def stop(point):
            if point == 'after:lock':
                raise RuntimeError('fault after commitment before first move')
        with patch.object(s, '_checkpoint', stop), self.assertRaises(RuntimeError):
            self.choose('accept-all')
        case, intent, raw = s._intent(self.root)
        before = self.content()
        unexpected = case/'retired/world-recovery'
        unexpected.mkdir()
        self.assertFalse(s.pending(self.root)['can_resume'])
        unexpected.rmdir()
        self.assertTrue(s.pending(self.root)['can_resume'])
        (self.root/s.GATE).write_bytes(b'a different gate created after the committed choice')
        self.assertFalse(s.pending(self.root)['can_resume'])
        self.assertEqual(self.content()['trees'], before['trees'])

    def test_case_list_pending_does_not_hide_the_resume_page(self):
        def stop(point):
            if point == 'after:lock':
                raise RuntimeError('fault after commitment')
        with patch.object(s, '_checkpoint', stop), self.assertRaises(RuntimeError):
            self.choose('accept-all')
        self.assertEqual(s.list_cases(self.root)['backups'], [])
        self.assertTrue(s.plan(self.root)['pending']['can_resume'])

    def test_verified_waiting_not_reclassified_as_anomaly(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        fixture.request(self.root, mode=w.RESTORE)
        (self.saves/'userdata0000').write_bytes(fixture.GOOD)
        before = self.content()
        self.assertEqual(w.review_items(self.root)[0]['state'], 'waiting-backup')
        self.assertFalse(s.plan(self.root)['needed'])
        self.assertEqual(self.content(), before)
        (self.saves/'backup0000').write_bytes(fixture.GOOD)
        self.assertEqual(w.review_items(self.root)[0]['state'], 'verified')
        self.assertFalse(s.plan(self.root)['needed'])

    def test_partialbad_restore_and_five_tree_undo(self):
        backup = self.automatic()
        (self.saves/'userdata0001').write_bytes(b'unreadable fixture')
        for label in ('guest-items', 'shared-progress'):
            p = self.root/s.TREES[label]
            p.mkdir()
            (p/'opaque').write_bytes(label.encode())
        before = self.content()
        plan = s.plan(self.root)
        self.assertEqual(plan['options'][0]['id'], 'restore-all')
        self.assertTrue(plan['options'][0]['recommended'])
        self.assertIn('2', plan['options'][0]['description'])
        self.choose('restore-all')
        s.transfer.validate_savedata(self.root/'data/user/savedata')
        row, = s.list_cases(self.root)['backups']
        self.assertIn('含读不出的角色', row['label'])
        self.assertEqual(row['characters'][1]['primary'], 'unreadable')
        current = self.content()
        result = self.choose('restore-case', row)
        self.assertEqual(self.content()['trees'], before['trees'])
        self.assertIsNotNone(self.content()['gate'])
        self.assertEqual(s._original_content(s._case_path(self.root, result['case_id'])), current)
        with self.assertRaises(ValueError):
            w.begin(self.root)
        rows = s.list_cases(self.root)['backups']
        self.assertTrue(any('换回前的存档' in r['label'] for r in rows))
        self.assertEqual(s.plan(self.root)['options'][0]['id'], 'restore-all')
        self.assertEqual(s.backups._backup(self.root, backup['id'])['fingerprint'], backup['fingerprint'])

    def test_clean_branch_gate_has_use_current_exit(self):
        # Preserve a fully readable branch with no world pending records.
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        (self.root/s.GATE).write_bytes(b'opaque gate, no embedded paths used')
        first = self.choose('use-current')
        row, = s.list_cases(self.root)['backups']
        self.choose('restore-case', row)
        plan = s.plan(self.root)
        self.assertEqual(plan['options'][0]['id'], 'use-current')
        result = self.choose('use-current')
        self.assertTrue(result['can_launch'])
        self.assertFalse((self.root/s.GATE).exists())
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN', w.begin(self.root))

    def test_opaque_gate_is_preserved_not_executed(self):
        gate = b'{"root":"C:/FOREIGN","case":"../../NEVER","command":"do-not-run"}'
        (self.root/s.GATE).write_bytes(gate)
        result = self.choose('accept-all')
        case = s._case_path(self.root, result['case_id'])
        self.assertEqual((case/'original/gate').read_bytes(), gate)
        self.assertEqual((case/'retired/gate').read_bytes(), gate)
        self.assertFalse((self.root/s.GATE).exists())

    def test_preserve_does_not_clear_anomaly_or_gate(self):
        (self.root/s.GATE).write_bytes(b'unknown')
        before = self.content()
        self.assertFalse(next(o for o in s.plan(self.root)['options'] if o['id'] == 'preserve')['recommended'])
        self.choose('preserve')
        self.assertEqual(self.content(), before)
        self.assertTrue(s.plan(self.root)['needed'])

    def test_backup_only_visible_but_not_claimed_playable(self):
        (self.saves/'userdata0000').unlink()
        (self.saves/'userdata0001').write_bytes(b'bad')
        (self.saves/'backup0001').write_bytes(b'bad')
        self.choose('preserve')
        row, = s.list_cases(self.root)['backups']
        self.assertEqual(row['characters'][0]['primary'], 'missing')
        self.assertEqual(row['characters'][0]['backup'], 'readable')
        self.assertIn('含读不出的角色', row['label'])

    def test_all_unreadable_preserved_not_selectable(self):
        for path in self.saves.iterdir():
            if path.name != 'userdata0010':
                path.write_bytes(b'bad')
        result = self.choose('preserve')
        self.assertTrue((s._case_path(self.root, result['case_id'])/'original/savedata').is_dir())
        self.assertEqual(s.list_cases(self.root)['backups'], [])

    def test_source_or_current_change_invalidates_confirmation(self):
        plan = s.plan(self.root)
        (self.saves/'extra').write_bytes(b'new progress')
        before = self.content()
        with self.assertRaisesRegex(ValueError, '变化'):
            s.apply(self.root, plan['digest'], 'accept-all', confirmed=True)
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_tampered_case_rejected_before_live_changes(self):
        result = self.choose('accept-all')
        row, = s.list_cases(self.root)['backups']
        original = s._case_path(self.root, row['id'])/'original/savedata/1/CUSA00207/SPRJ0005/userdata0000'
        original.write_bytes(b'changed')
        before = self.content()
        with self.assertRaises(ValueError):
            self.choose('restore-case', row)
        self.assertEqual(self.content(), before)

    def test_partial_backup_not_usable_and_ordinary_restore_cannot_take_case(self):
        self.automatic()
        rows = s.backups._list(self.root)[0]
        (rows[0]['_folder']/'savedata/1/CUSA00207/SPRJ0005/userdata0001').write_bytes(b'bad')
        (self.saves/'userdata0001').write_bytes(b'bad')
        self.assertNotIn('restore-all', [o['id'] for o in s.plan(self.root)['options']])
        self.choose('preserve')
        row, = s.list_cases(self.root)['backups']
        with self.assertRaises(ValueError):
            s.backups._backup(self.root, row['id'])

    def test_copy_enospc_does_not_publish_or_change_live(self):
        before = self.content()
        real = shutil.copyfile
        count = 0
        def fail(source, destination, *args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError(errno.ENOSPC, 'synthetic no space at physical file copy')
            return real(source, destination, *args, **kwargs)
        with patch.object(s.shutil, 'copyfile', side_effect=fail):
            with self.assertRaises((OSError, shutil.Error)):
                self.choose('accept-all')
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'.save-import.lock').exists())
        self.assertEqual(s.list_cases(self.root)['backups'], [])

    def test_disk_full_preflight_changes_nothing(self):
        before = self.content()
        usage = shutil.disk_usage(self.root)
        with patch.object(s.shutil, 'disk_usage', return_value=usage._replace(free=0)):
            with self.assertRaisesRegex(ValueError, '空间不足'):
                self.choose('accept-all')
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'save-backups').exists())

    def test_io_error_never_classified_as_bad_role(self):
        real = Path.read_bytes
        def fail(path):
            if path == self.saves/'userdata0001':
                raise PermissionError('synthetic read denied')
            return real(path)
        with patch.object(Path, 'read_bytes', fail), self.assertRaises(PermissionError):
            s.plan(self.root)
        self.assertFalse((self.root/'save-backups').exists())

    def test_changed_during_preserve_leaves_live_new_progress(self):
        def checkpoint(point):
            if point == 'after:preserve':
                (self.saves/'new').write_bytes(b'new synthetic progress')
        with patch.object(s, '_checkpoint', checkpoint), self.assertRaisesRegex(ValueError, '变化'):
            self.choose('accept-all')
        self.assertEqual((self.saves/'new').read_bytes(), b'new synthetic progress')
        self.assertTrue((self.root/'data/user/world-recovery/review-required.json').exists())
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_interrupted_bound_and_repeat_resume_does_not_replay(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        fixture.request(self.root, job=True)
        def stop(point):
            if point.startswith('after:0:retire:'):
                raise RuntimeError('fault after actual first rename')
        with patch.object(s, '_checkpoint', stop), self.assertRaises(RuntimeError):
            self.choose('accept-bound')
        pending = s.pending(self.root)
        self.assertTrue(pending['can_resume'])
        result = s.resume(self.root, pending['fingerprint'], confirmed=True)
        self.assertEqual(result['result'], 'accepted-current-unverified')
        (self.saves/'new').write_bytes(b'after completion')
        now = self.content()
        repeated = s.resume(self.root, pending['fingerprint'], confirmed=True)
        self.assertFalse(repeated['can_launch'])
        self.assertEqual(self.content(), now)

    def test_resume_refuses_new_progress_and_keeps_lock(self):
        def stop(point):
            if point == 'after:lock':
                raise RuntimeError('fault after lock')
        with patch.object(s, '_checkpoint', stop), self.assertRaises(RuntimeError):
            self.choose('accept-all')
        fingerprint = hashlib.sha256((self.root/'.save-import.lock').read_bytes()).hexdigest()
        (self.saves/'new').write_bytes(b'uncontrolled later progress')
        before = self.content()
        self.assertFalse(s.pending(self.root)['can_resume'])
        with self.assertRaises(ValueError):
            s.resume(self.root, fingerprint, confirmed=True)
        self.assertEqual(self.content(), before)
        self.assertTrue((self.root/'.save-import.lock').exists())

    def test_unconfirmed_cancel_and_foreign_locks_do_not_write(self):
        plan = s.plan(self.root)
        before = self.content()
        with self.assertRaises(ValueError):
            s.apply(self.root, plan['digest'], 'accept-all')
        for name in ('.save-conversion.lock', '.save-conversion-pending.json', '.save-import.lock'):
            path = self.root/name
            path.write_bytes(b'opaque unrelated transaction')
            if name == '.save-import.lock':
                self.assertFalse(s.plan(self.root)['pending']['can_resume'])
            else:
                with self.assertRaises(ValueError):
                    s.plan(self.root)
            self.assertEqual(path.read_bytes(), b'opaque unrelated transaction')
            path.unlink()
        self.assertEqual(self.content(), before)
        self.assertFalse((self.root/'save-backups').exists())


if __name__ == '__main__':
    unittest.main()
