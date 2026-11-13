"""Recovery transactions on copies of a real save layout; no installation writes."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent/'launcher'))
import world_recovery as wr
from synthetic_world_fixture import fixture_pair

RAW, GOOD = fixture_pair()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        wr._next_poll.clear()
        self.stop = patch.object(wr, 'require_stopped')
        self.stop.start()
        self.addCleanup(self.stop.stop)
        self.folder, self.saves = wr.layout(self.root)
        self.saves.mkdir(parents=True)
        for stem in ('userdata', 'backup'):
            (self.saves/f'{stem}0000').write_bytes(RAW)
        self.env = wr.begin(self.root)
        self.token = self.env['THEHUNT_WORLD_RECOVERY_TOKEN']
        self.quit_sequence = 0
        self.journal = self.root/'mod/own-world/own-save-0.bin'
        self.journal.parent.mkdir(parents=True)
        self.path = self.folder/'requests/world-save-0.bin'
        self.bank = wr.offline_aux.flag_bank(RAW)
        self.row = (1, 5, RAW[self.bank+625:self.bank+750], GOOD[self.bank+625:self.bank+750])

    def request(self, mode=wr.QUIT, rows=None, seq=1):
        self.quit_sequence = seq if mode == wr.QUIT else 0
        rows = rows or [self.row]
        journal = struct.pack('<III', 0x574F4242, 125, len(rows))+b''.join(
            struct.pack('<I', key)+after for key, _, _, after in rows)
        self.journal.write_bytes(journal)
        data = wr.HEADER.pack(b'THWR', 2, seq, self.token.encode(), 0, mode,
                              len(rows), wr.fnv(journal))
        data += b''.join(wr.ROW.pack(*row) for row in rows)
        baseline = self.folder/'baselines/world-save-0.bin'
        if not baseline.exists():
            own_rows = [(key, index, after, after) for key, index, before, after in rows]
            initial = wr.HEADER.pack(b'THWR', 2, seq, self.token.encode(), 0, wr.CAPTURE,
                                     len(rows), wr.fnv(journal))
            initial += b''.join(wr.ROW.pack(*row) for row in own_rows)
            wr.atomic(baseline, initial)
            wr.atomic(baseline.with_suffix('.bbow'), journal)
        wr.atomic(self.path, data)
        return wr.read_request(self.root, self.path)

    def exited(self, code=0):
        wr.process_exited(self.root, self.token, code)

    def finalize(self):
        return wr.finalize(self.root, self.token, quit_sequence=self.quit_sequence)

    def settle_second_restore(self):
        self.request(wr.RESTORE, seq=2)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED, seq=2)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        wr._next_poll.clear()
        wr.poll(self.root, self.token)
        self.exited()

    def test_superseded_restore_retains_job_directory(self):
        self.request(wr.RESTORE, seq=1)
        wr.poll(self.root, self.token)
        job = self.folder/'jobs'/f'{self.token}-0-1'
        manifest = job/'job.json'
        old = json.loads(manifest.read_text('utf-8'))
        self.assertEqual(old['state'], 'prepared')
        self.settle_second_restore()
        self.assertEqual(self.finalize(), [])
        wr.begin(self.root)
        self.assertTrue(job.is_dir())
        self.assertEqual(json.loads(manifest.read_text('utf-8')),
                         dict(old, state='superseded', superseded_by=2))

    def test_orphan_prepared_after_primary_write_still_blocks(self):
        self.request()
        self.exited()
        write_json = wr.json_file

        def interrupt(path, value):
            if path.name == 'job.json' and value.get('state') == 'primary-ready':
                raise OSError('synthetic interruption after primary write')
            write_json(path, value)

        with patch.object(wr, 'json_file', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'after primary write'):
                self.finalize()
        job = self.folder/'jobs'/f'{self.token}-0-1'
        self.assertTrue(list((job/'attempts').iterdir()))
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.settle_second_restore()
        with self.assertRaisesRegex(ValueError, '缺少完整恢复凭据'):
            self.finalize()
        self.assertEqual(json.loads((job/'job.json').read_text('utf-8'))['state'], 'prepared')

    def test_orphan_prepared_without_same_run_successor_still_blocks(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        job = self.folder/'jobs'/f'{self.token}-0-1'
        # An unrelated token with a higher sequence is not a successor.
        foreign = self.folder/'jobs'/f'{"f"*32}-0-99'/'job.json'
        wr.json_file(foreign, {'state': 'complete'})
        self.path.unlink()
        self.journal.unlink()
        self.assertFalse((job/'attempts').exists())
        with self.assertRaisesRegex(ValueError, '缺少完整恢复凭据'):
            wr.begin(self.root)
        self.assertEqual(json.loads((job/'job.json').read_text('utf-8'))['state'], 'prepared')

    def test_orphan_prepared_with_partial_attempt_still_blocks(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        job = self.folder/'jobs'/f'{self.token}-0-1'
        (job/'attempts'/'partial'/'before').mkdir(parents=True)
        self.settle_second_restore()
        with self.assertRaisesRegex(ValueError, '缺少完整恢复凭据'):
            self.finalize()
        self.assertEqual(json.loads((job/'job.json').read_text('utf-8'))['state'], 'prepared')

    def test_unparseable_orphan_manifest_still_blocks(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        manifest = self.folder/'jobs'/f'{self.token}-0-1'/'job.json'
        manifest.write_bytes(b'{incomplete')
        self.settle_second_restore()
        with self.assertRaisesRegex(ValueError, '缺少完整恢复凭据'):
            self.finalize()
        self.assertEqual(manifest.read_bytes(), b'{incomplete')

    def test_old_quit_without_live_seal_preserves_newer_progress(self):
        self.request()
        primary = self.saves/'userdata0000'
        later = bytearray(RAW)
        later[self.bank+625] ^= 1
        primary.write_bytes(later)
        self.exited()
        # Even though the disk still says QUIT, a revoked/missing seal is zero.
        with self.assertRaises(ValueError):
            wr.finalize(self.root, self.token)
        self.assertEqual(primary.read_bytes(), later)
        self.assertTrue(self.journal.exists())

    def test_confirmed_quit_restores_primary_and_waits_for_native_backup(self):
        self.request()
        self.exited()
        self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        self.assertFalse(self.journal.exists())
        self.assertTrue(self.journal.with_suffix('.bin.awaiting-save').is_file())
        self.assertFalse(self.journal.with_suffix('.bin.restored').exists())
        # Next run is allowed without replaying the primary; its own save
        # advances the entire backup generation, inventory included.
        new_token = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        (self.saves/'backup0000').write_bytes(GOOD)
        wr.poll(self.root, new_token)
        self.assertTrue(self.journal.with_suffix('.bin.restored').is_file())

    def test_waiting_backup_allows_next_run_progress_without_replay(self):
        self.request()
        self.exited()
        self.assertEqual(self.finalize(), ['primary-restored-waiting-backup-save'])
        later_token = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        primary = self.saves/'userdata0000'
        progress = bytearray(primary.read_bytes())
        progress[self.bank+625] ^= 1
        primary.write_bytes(progress)
        wr.poll(self.root, later_token)
        wr.process_exited(self.root, later_token, 0)
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            self.assertEqual(wr.finalize(self.root, later_token, observed_exit=0),
                             ['waiting-backup-save'])
            report = wr.review(self.root)['items'][0]
            self.assertEqual(report['state'], 'waiting-backup')
            self.assertEqual(report['generations']['userdata0000']['state'], 'unverified')
            wr.begin(self.root)
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), progress)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_waiting_backup_rejects_changed_rows_after_crash_or_missing_exit(self):
        self.request()
        self.exited()
        self.finalize()
        later_token = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        primary = self.saves/'userdata0000'
        progress = bytearray(primary.read_bytes())
        progress[self.bank+625] ^= 1
        primary.write_bytes(progress)
        runs = self.folder/'runs'
        earlier = set(runs.glob('*.json'))
        for exit_code in (None, 0xc0000005):
            with self.subTest(exit_code=exit_code), patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
                if exit_code is not None:
                    wr.process_exited(self.root, later_token, exit_code)
                with self.assertRaisesRegex(ValueError, '无法确认主存档已恢复'):
                    wr.finalize(self.root, later_token, observed_exit=exit_code)
                with self.assertRaisesRegex(ValueError, '无法确认主存档已恢复'):
                    wr.begin(self.root)
                self.assertEqual(wr.review(self.root)['items'][0]['state'], 'review-required')
                self.assert_no_save_writes(writes)
                self.assertEqual(set(runs.glob('*.json')), earlier)
                self.assertEqual(primary.read_bytes(), progress)
                self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_waiting_backup_accepts_observed_zero_only_for_current_run(self):
        self.request()
        self.exited()
        self.finalize()
        later_token = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        primary = self.saves/'userdata0000'
        progress = bytearray(primary.read_bytes())
        progress[self.bank+625] ^= 1
        primary.write_bytes(progress)
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            self.assertEqual(wr.finalize(self.root, later_token, observed_exit=0), ['waiting-backup-save'])
            with self.assertRaisesRegex(ValueError, '无法确认主存档已恢复'):
                wr.begin(self.root)  # A live observation does not manufacture a persisted exit code.
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), progress)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_waiting_backup_rejects_next_run_replaced_primary(self):
        self.request()
        self.exited()
        self.finalize()
        later_token = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        primary = self.saves/'userdata0000'
        foreign = bytearray(GOOD)
        foreign[wr.offline_save.parse(GOOD).face+4] ^= 1
        self.assertNotEqual(wr.identity(foreign), wr.identity(GOOD))
        primary.write_bytes(foreign)
        wr.process_exited(self.root, later_token, 0)
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            with self.assertRaisesRegex(ValueError, '角色不一致'):
                wr.finalize(self.root, later_token, observed_exit=0)
            with self.assertRaisesRegex(ValueError, '角色不一致'):
                wr.begin(self.root)
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), foreign)

    def test_poll_never_writes_running_game_saves(self):
        self.request()
        wr.poll(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertFalse((self.folder/'jobs').exists())

    def finish_with_exit_record_denied(self, *, seal=1, exit_code=0, fail_count=None):
        import client
        replace = Path.replace
        record = self.folder/'runs'/f'{self.token}.json'
        writes = 0

        def deny(source, target):
            nonlocal writes
            if target == record:
                writes += 1
                if fail_count is None or writes <= fail_count:
                    raise PermissionError('synthetic run record busy')
            return replace(source, target)

        with (patch.object(client, 'ROOT', self.root),
              patch.object(client, 'report_failure'), patch.object(client, 'state'),
              patch.object(Path, 'replace', deny), patch.object(wr.time, 'sleep')):
            rc = client.finish_game(self.root/'data/user', self.token,
                                    struct.pack('<Q', seal), exit_code, 'start', 'log')
        return rc, writes, json.loads(record.read_text('utf-8'))

    def test_exit_record_retries_transient_critical_replace(self):
        self.request()
        rc, attempts, run = self.finish_with_exit_record_denied(fail_count=12)
        self.assertEqual((rc, attempts, run['exit_code']), (0, 13, 0))
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_exit_record_permission_failure_with_live_quit_seal(self):
        self.request()
        rc, attempts, run = self.finish_with_exit_record_denied()
        self.assertEqual(rc, 0)
        self.assertGreater(attempts, 12)
        self.assertNotIn('exit_code', run)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_exit_record_permission_failure_mismatched_quit_seal_blocks(self):
        self.request()
        rc, _, _ = self.finish_with_exit_record_denied(seal=2)
        self.assertEqual(rc, 1)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_exit_record_permission_failure_never_authorizes_applied(self):
        self.request(wr.APPLIED)
        rc, _, _ = self.finish_with_exit_record_denied()
        self.assertEqual(rc, 1)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_exit_record_permission_failure_never_overrides_recorded_nonzero(self):
        self.request()
        self.exited(7)
        rc, _, run = self.finish_with_exit_record_denied()
        self.assertEqual(rc, 1)
        self.assertEqual(run['exit_code'], 7)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)

    def test_capture_without_confirmed_exit_never_replays_old_state(self):
        self.request(wr.CAPTURE)
        self.exited()
        with self.assertRaises(ValueError):
            self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertTrue(self.journal.exists())

    def test_nonzero_exit_does_not_automatically_apply_quit_receipt(self):
        self.request()
        self.exited(3)
        with self.assertRaises(ValueError):
            self.finalize()
        self.assertTrue(self.journal.exists())

    def test_unapplied_restore_request_does_not_write_after_timeout(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.exited()
        with self.assertRaises(ValueError):
            self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)

    def test_memory_recovery_is_not_archived_until_both_saves_match(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.assertTrue((self.folder/'ready/0.ready').exists())
        self.request(wr.APPLIED)
        (self.saves/'userdata0000').write_bytes(GOOD)
        wr.poll(self.root, self.token)
        self.assertTrue(self.journal.exists())
        (self.saves/'backup0000').write_bytes(GOOD)
        wr._next_poll.clear()
        wr.poll(self.root, self.token)
        self.assertFalse(self.journal.exists())

    def test_later_personal_progress_is_verified_without_being_overwritten(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        later = bytearray(GOOD)
        later[self.bank+20000] ^= 4
        later[-1] ^= 1
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(later)
        wr.poll(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(later))
        self.assertFalse(self.journal.exists())

    def test_ambiguous_new_event_progress_blocks_retry(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        newer = bytearray(RAW)
        newer[self.bank+20000] ^= 4
        (self.saves/'userdata0000').write_bytes(newer)
        self.exited()
        with self.assertRaisesRegex(ValueError, '新的事件进度'):
            self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(newer))
        self.assertTrue(self.journal.exists())

    def test_retry_preserves_newer_non_event_bytes(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        newer = bytearray(RAW)
        newer[-1] ^= 1
        (self.saves/'userdata0000').write_bytes(newer)
        self.exited()
        self.finalize()
        expected = bytearray(GOOD)
        expected[-1] ^= 1
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(expected))

    def test_interruption_after_primary_write_resumes_without_replaying(self):
        self.request()
        self.exited()
        original = wr.json_file
        def fail(path, value):
            if path.name == 'job.json' and value.get('state') == 'primary-ready':
                raise OSError('synthetic interruption after primary')
            original(path, value)
        with patch.object(wr, 'json_file', side_effect=fail):
            with self.assertRaises(OSError):
                self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        newer = bytearray(GOOD)
        newer[self.bank+20000] ^= 4
        (self.saves/'userdata0000').write_bytes(newer)
        self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(newer))
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        self.assertFalse(self.journal.exists())

    def test_interruption_between_primary_ready_marker_and_journal_move(self):
        self.request()
        self.exited()
        original = wr.mark_waiting
        with patch.object(wr, 'mark_waiting', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                self.finalize()
        self.assertTrue(self.journal.exists())
        self.finalize()
        self.assertFalse(self.journal.exists())
        self.assertTrue(self.journal.with_suffix('.bin.awaiting-save').is_file())

    def test_changed_character_rejected_before_writes(self):
        self.request()
        role = wr.offline_save.parse(RAW)
        different = bytearray(RAW)
        different[role.username+1] ^= 1
        (self.saves/'userdata0000').write_bytes(different)
        self.exited()
        with self.assertRaises(ValueError):
            self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(different))

    def test_title_reload_is_not_treated_as_a_clean_quit(self):
        self.request(wr.BLOCKED)
        packet, journal = self.path.read_bytes(), self.journal.read_bytes()
        self.exited()
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            with self.assertRaisesRegex(ValueError, '无法确认主存档已恢复'):
                self.finalize()
        self.assert_no_save_writes(writes)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        self.assertEqual(self.path.read_bytes(), packet)
        self.assertEqual(self.journal.read_bytes(), journal)
        self.assertFalse((self.folder/'acks/0.ack').exists())

    def assert_no_save_writes(self, writes):
        self.assertEqual([call.args[0] for call in writes.call_args_list
                          if self.saves in call.args[0].parents], [])

    def test_reloaded_poll_waits_for_both_actual_generations(self):
        request = self.request(6)  # Native wire mode for a verified title reload.
        self.assertEqual(request['mode'], wr.RELOADED)
        packet, journal = self.path.read_bytes(), self.journal.read_bytes()
        primary, backup = (self.saves/name for name in ('userdata0000', 'backup0000'))
        ack = self.folder/'acks/0.ack'
        primary.write_bytes(GOOD)
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            wr.poll(self.root, self.token)
            self.assertEqual(self.journal.read_bytes(), journal)
            self.assertEqual(backup.read_bytes(), RAW)
            self.assertFalse(ack.exists())
            self.assertFalse((self.folder/'ready/0.ready').exists())
            backup.write_bytes(GOOD)  # The game writes the second generation.
            wr._next_poll.clear()
            wr.poll(self.root, self.token)
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), GOOD)
        self.assertEqual(backup.read_bytes(), GOOD)
        self.assertEqual(self.path.read_bytes(), packet)
        self.assertEqual(self.journal.with_suffix('.bin.restored').read_bytes(), journal)
        self.assertEqual(wr.prepare(request)['state'], 'complete')
        self.assertEqual(ack.read_bytes(), struct.pack('<Q', request['seq']))

    def test_reloaded_finalize_checks_primary_without_replaying(self):
        request = self.request(wr.RELOADED)
        packet, journal = self.path.read_bytes(), self.journal.read_bytes()
        primary, backup = (self.saves/name for name in ('userdata0000', 'backup0000'))
        ack = self.folder/'acks/0.ack'
        self.exited()
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            with self.assertRaisesRegex(ValueError, '无法确认主存档已恢复'):
                wr.finalize(self.root, self.token, quit_sequence=request['seq'])
            self.assertEqual(primary.read_bytes(), RAW)
            self.assertEqual(self.journal.read_bytes(), journal)
            self.assertFalse(ack.exists())
            primary.write_bytes(GOOD)
            self.assertEqual(self.finalize(), ['waiting-backup-save'])
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), GOOD)
        self.assertEqual(backup.read_bytes(), RAW)
        self.assertEqual(self.path.read_bytes(), packet)
        self.assertEqual(self.journal.with_suffix('.bin.awaiting-save').read_bytes(), journal)
        self.assertEqual(wr.prepare(request)['state'], 'primary-ready')
        self.assertFalse(ack.exists())

    def test_blocked_with_both_generations_verified_retires_on_next_start(self):
        request = self.request(wr.BLOCKED)
        packet, journal = self.path.read_bytes(), self.journal.read_bytes()
        primary, backup = (self.saves/name for name in ('userdata0000', 'backup0000'))
        later = bytearray(GOOD)
        later[self.bank+20000] ^= 1  # New progress outside the requested groups.
        primary.write_bytes(later)
        backup.write_bytes(GOOD)
        self.exited()
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            self.assertNotEqual(wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN'], self.token)
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), later)
        self.assertEqual(backup.read_bytes(), GOOD)
        self.assertEqual(self.path.read_bytes(), packet)
        self.assertEqual(self.journal.with_suffix('.bin.restored').read_bytes(), journal)
        self.assertFalse(self.journal.exists())
        self.assertEqual(wr.prepare(request)['state'], 'complete')
        self.assertEqual((self.folder/'acks/0.ack').read_bytes(), struct.pack('<Q', 1))

    def test_blocked_with_verified_primary_waits_for_same_role_backup(self):
        request = self.request(wr.BLOCKED)
        packet, journal = self.path.read_bytes(), self.journal.read_bytes()
        primary, backup = (self.saves/name for name in ('userdata0000', 'backup0000'))
        primary.write_bytes(GOOD)
        self.exited()
        ack = self.folder/'acks/0.ack'
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            self.assertEqual(self.finalize(), ['waiting-backup-save'])
            env = wr.begin(self.root)
            self.assertIn('THEHUNT_SAVE_PROOF_DIR', env)
            wr.poll(self.root, env['THEHUNT_WORLD_RECOVERY_TOKEN'])
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), GOOD)
        self.assertEqual(backup.read_bytes(), RAW)
        self.assertEqual(wr.prepare(request)['state'], 'primary-ready')
        self.assertEqual(self.journal.with_suffix('.bin.awaiting-save').read_bytes(), journal)
        self.assertFalse(self.journal.exists())
        self.assertFalse(ack.exists())
        backup.write_bytes(GOOD)  # Simulate the game's own next backup save.
        wr._next_poll.clear()
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            wr.poll(self.root, env['THEHUNT_WORLD_RECOVERY_TOKEN'])
        self.assert_no_save_writes(writes)
        self.assertEqual(self.path.read_bytes(), packet)
        self.assertEqual(self.journal.with_suffix('.bin.restored').read_bytes(), journal)
        self.assertFalse(self.journal.with_suffix('.bin.awaiting-save').exists())
        self.assertEqual(wr.prepare(request)['state'], 'complete')
        self.assertEqual(ack.read_bytes(), struct.pack('<Q', 1))

    def test_blocked_primary_ready_is_rechecked_before_restart(self):
        request = self.request(wr.BLOCKED)
        primary = self.saves/'userdata0000'
        primary.write_bytes(GOOD)
        value = wr.prepare(request)
        value['state'] = 'primary-ready'
        job = wr.job_for(request)
        wr.json_file(job/'job.json', value)
        wr.mark_waiting(request, job)
        waiting = self.journal.with_suffix('.bin.awaiting-save')
        saved = {p: p.read_bytes() for p in (self.path, waiting, job/'job.json')}
        primary.write_bytes(RAW)
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            with self.assertRaisesRegex(ValueError, '无法确认主存档已恢复'):
                wr.begin(self.root)
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), RAW)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        self.assertEqual({p: p.read_bytes() for p in saved}, saved)
        self.assertFalse((self.folder/'acks/0.ack').exists())

    def test_blocked_prepared_job_rejects_either_replaced_generation(self):
        request = self.request(wr.BLOCKED)
        primary, backup = (self.saves/name for name in ('userdata0000', 'backup0000'))
        primary.write_bytes(GOOD)
        wr.prepare(request)
        job = wr.job_for(request)
        saved = {p: p.read_bytes() for p in (self.path, self.journal, job/'job.json')}
        foreign = bytearray(GOOD)
        foreign[wr.offline_save.parse(GOOD).username+1] ^= 1
        for path in (primary, backup):
            with self.subTest(file=path.name):
                primary.write_bytes(GOOD)
                backup.write_bytes(RAW)
                path.write_bytes(foreign)
                with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
                    with self.assertRaisesRegex(ValueError, '角色不一致'):
                        wr.begin(self.root)
                self.assert_no_save_writes(writes)
                self.assertEqual(path.read_bytes(), foreign)
                other, other_original = (backup, RAW) if path == primary else (primary, GOOD)
                self.assertEqual(other.read_bytes(), other_original)
                self.assertEqual({p: p.read_bytes() for p in saved}, saved)
                self.assertFalse((self.folder/'acks/0.ack').exists())

    def test_unknown_old_journal_blocks_startup(self):
        self.request()
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, '缺少完整恢复凭据'):
            wr.begin(self.root)

    def test_pending_backup_blocks_conversion_without_replaying_flags(self):
        self.request()
        self.exited()
        self.finalize()
        before = (self.saves/'userdata0000').read_bytes()
        with self.assertRaisesRegex(ValueError, '备份尚待确认'):
            wr.offline_aux.validate(self.root/'mod', self.root/'data/user/savedata', 0)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), before)

    def test_new_role_enrolled_only_from_own_prejoin_capture(self):
        context_path = self.folder/'runs'/f'{self.token}.json'
        value = json.loads(context_path.read_text())
        value['identities'] = {}
        value['started_ns'] = 0
        wr.json_file(context_path, value)
        source = self.row[2]
        self.request(wr.CAPTURE, rows=[(1, 5, source, source)])
        self.assertIn('0', json.loads(context_path.read_text())['identities'])

    def test_foreign_world_capture_cannot_enroll_new_role(self):
        context_path = self.folder/'runs'/f'{self.token}.json'
        value = json.loads(context_path.read_text())
        value['identities'] = {}
        wr.json_file(context_path, value)
        with self.assertRaises(ValueError):
            self.request(wr.CAPTURE)

    def test_changed_row_contents_with_same_transaction_number_rejected(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        altered = bytearray(self.path.read_bytes())
        struct.pack_into('<I', altered, wr.HEADER.size+4, 6)
        self.path.write_bytes(altered)
        with self.assertRaises(ValueError):
            wr.poll(self.root, self.token)

    def test_missing_ack_after_archive_is_reissued(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        with patch.object(wr, 'confirm', side_effect=OSError('synthetic ack loss')):
            with self.assertRaises(OSError):
                wr.poll(self.root, self.token)
        self.assertFalse(self.journal.exists())
        wr._next_poll.clear()
        wr.poll(self.root, self.token)
        self.assertEqual((self.folder/'acks/0.ack').read_bytes(), struct.pack('<Q', 1))

    def test_missing_ack_after_archive_ignores_later_progress(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        with patch.object(wr, 'confirm', side_effect=PermissionError('synthetic ack loss')):
            with self.assertRaises(PermissionError):
                wr.poll(self.root, self.token)
        job = self.folder/'jobs'/f'{self.token}-0-1'
        self.assertEqual(json.loads((job/'job.json').read_text('utf-8'))['state'], 'complete')
        primary = self.saves/'userdata0000'
        later = bytearray(GOOD)
        later[self.bank+625] ^= 1
        primary.write_bytes(later)
        ack = self.folder/'acks/0.ack'
        wr._next_poll.clear()
        with patch.object(wr, 'atomic', wraps=wr.atomic) as writes:
            wr.poll(self.root, self.token)
            self.assertEqual(ack.read_bytes(), struct.pack('<Q', 1))
            self.assertEqual([call.args[0] for call in writes.call_args_list], [ack])
            ack.unlink()
            wr.begin(self.root)
        self.assertEqual(ack.read_bytes(), struct.pack('<Q', 1))
        self.assert_no_save_writes(writes)
        self.assertEqual(primary.read_bytes(), later)
        self.assertEqual((self.saves/'backup0000').read_bytes(), GOOD)

    def test_completed_job_cannot_reissue_ack_with_damaged_evidence(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        with patch.object(wr, 'confirm', side_effect=PermissionError('synthetic ack loss')):
            with self.assertRaises(PermissionError):
                wr.poll(self.root, self.token)
        job = self.folder/'jobs'/f'{self.token}-0-1'
        archived = self.journal.with_suffix('.bin.restored')
        archived_bytes = archived.read_bytes()
        for path in (archived, job/'expected/userdata0000'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b'broken')
                with self.assertRaises(ValueError):
                    wr.begin(self.root)
                self.assertFalse((self.folder/'acks/0.ack').exists())
                path.write_bytes(original)
        self.assertEqual(archived.read_bytes(), archived_bytes)

    def test_interruption_between_archive_and_complete_marker_is_resumable(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.request(wr.APPLIED)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        original = wr.json_file
        def fail(path, value):
            if path.name == 'job.json' and value.get('state') == 'complete':
                raise OSError('synthetic interruption after archive')
            original(path, value)
        with patch.object(wr, 'json_file', side_effect=fail):
            with self.assertRaises(OSError):
                wr.poll(self.root, self.token)
        self.assertFalse(self.journal.exists())
        self.finalize()
        self.assertTrue((self.folder/'acks/0.ack').is_file())

    def test_bad_journal_version_rejected(self):
        self.request()
        self.journal.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            wr.read_request(self.root, self.path)

    def test_duplicate_persistent_index_rejected(self):
        row2 = (2, 5, b'\0'*125, b'\0'*125)
        with self.assertRaises(ValueError):
            self.request(rows=[self.row, row2])

    def test_identical_persistent_aliases_restore_and_verify_once(self):
        _, index, before, after = self.row
        request = self.request(rows=[self.row, (2, index, before, after)])
        self.assertEqual(len(request['rows']), 2)
        self.exited()
        self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        (self.saves/'backup0000').write_bytes(GOOD)
        wr.poll(self.root, self.token)
        self.assertTrue(self.journal.with_suffix('.bin.restored').is_file())

    def test_alias_with_different_before_bytes_rejected(self):
        _, index, before, after = self.row
        changed = bytearray(before)
        changed[100] ^= 1
        with self.assertRaises(ValueError):
            self.request(rows=[self.row, (2, index, bytes(changed), after)])

    def test_alias_with_different_recovery_operation_rejected(self):
        before, after = b'\0'*125, b'\xff'*125
        with self.assertRaises(ValueError):
            self.request(rows=[(1, 5, before, after), (50001, 5, before, after)])

    def test_persistent_mapping_cannot_change_after_prejoin_baseline(self):
        self.request()
        raw = bytearray(self.path.read_bytes())
        struct.pack_into('<I', raw, wr.HEADER.size+4, 6)
        self.path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, '入房前固定'):
            wr.read_request(self.root, self.path)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)

    def test_missing_prejoin_baseline_blocks_writes(self):
        self.request()
        (self.folder/'baselines/world-save-0.bin').unlink()
        self.exited()
        with self.assertRaises(OSError):
            self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)

    def test_baseline_must_be_captured_in_own_world(self):
        self.request()
        path = self.folder/'baselines/world-save-0.bin'
        raw = bytearray(path.read_bytes())
        raw[wr.HEADER.size+8] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, '入房前个人世界'):
            wr.read_request(self.root, self.path)

    def test_old_protocol_without_fixed_mapping_is_rejected(self):
        self.request()
        raw = bytearray(self.path.read_bytes())
        struct.pack_into('<I', raw, 4, 1)
        self.path.write_bytes(raw)
        self.exited()
        with self.assertRaisesRegex(ValueError, '格式不受支持'):
            self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)

    def test_prepared_job_uses_immutable_baseline_copy(self):
        request = self.request()
        wr.prepare(request)
        (self.folder/'baselines/world-save-0.bin').write_bytes(b'new generation')
        self.exited()
        self.finalize()
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)

    def test_review_marker_blocks_startup_without_consuming_records(self):
        self.request()
        wr.json_file(self.folder/'review-required.json', {'reason': 'inspection'})
        with self.assertRaisesRegex(ValueError, '正在核查'):
            wr.begin(self.root)
        self.assertTrue(self.journal.exists())
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)


    def test_previously_matching_backup_must_still_match_to_confirm(self):
        backup = self.saves/'backup0000'
        backup.write_bytes(GOOD)
        wr.prepare(self.request())
        backup.write_bytes(RAW)
        self.exited()
        self.finalize()
        wr.poll(self.root, self.token)
        self.assertTrue(self.journal.with_suffix('.bin.awaiting-save').is_file())
        self.assertFalse((self.folder/'acks/0.ack').exists())
        self.assertEqual(backup.read_bytes(), RAW)


if __name__ == '__main__':
    unittest.main(verbosity=2)
