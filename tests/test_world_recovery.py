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
        self.journal = self.root/'mod/own-world/own-save-0.bin'
        self.journal.parent.mkdir(parents=True)
        self.path = self.folder/'requests/world-save-0.bin'
        self.bank = wr.offline_aux.flag_bank(RAW)
        self.row = (1, 5, RAW[self.bank+625:self.bank+750], GOOD[self.bank+625:self.bank+750])

    def request(self, mode=wr.QUIT, rows=None, seq=1):
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

    def test_confirmed_quit_restores_primary_and_waits_for_native_backup(self):
        self.request()
        self.exited()
        wr.finalize(self.root, self.token)
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

    def test_poll_never_writes_running_game_saves(self):
        self.request()
        wr.poll(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertFalse((self.folder/'jobs').exists())

    def test_capture_without_confirmed_exit_never_replays_old_state(self):
        self.request(wr.CAPTURE)
        self.exited()
        with self.assertRaises(ValueError):
            wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)
        self.assertTrue(self.journal.exists())

    def test_nonzero_exit_does_not_automatically_apply_quit_receipt(self):
        self.request()
        self.exited(3)
        with self.assertRaises(ValueError):
            wr.finalize(self.root, self.token)
        self.assertTrue(self.journal.exists())

    def test_unapplied_restore_request_does_not_write_after_timeout(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.exited()
        with self.assertRaises(ValueError):
            wr.finalize(self.root, self.token)
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
            wr.finalize(self.root, self.token)
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
        wr.finalize(self.root, self.token)
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
                wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        newer = bytearray(GOOD)
        newer[self.bank+20000] ^= 4
        (self.saves/'userdata0000').write_bytes(newer)
        wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(newer))
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)
        self.assertFalse(self.journal.exists())

    def test_interruption_between_primary_ready_marker_and_journal_move(self):
        self.request()
        self.exited()
        original = wr.mark_waiting
        with patch.object(wr, 'mark_waiting', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                wr.finalize(self.root, self.token)
        self.assertTrue(self.journal.exists())
        wr.finalize(self.root, self.token)
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
            wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), bytes(different))

    def test_title_reload_is_not_treated_as_a_clean_quit(self):
        self.request(wr.BLOCKED)
        self.exited()
        with self.assertRaises(ValueError):
            wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)

    def test_unknown_old_journal_blocks_startup(self):
        self.request()
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, '缺少完整恢复凭据'):
            wr.begin(self.root)

    def test_pending_backup_blocks_conversion_without_replaying_flags(self):
        self.request()
        self.exited()
        wr.finalize(self.root, self.token)
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
        wr.finalize(self.root, self.token)
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
        wr.finalize(self.root, self.token)
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
            wr.finalize(self.root, self.token)
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
            wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)

    def test_prepared_job_uses_immutable_baseline_copy(self):
        request = self.request()
        wr.prepare(request)
        (self.folder/'baselines/world-save-0.bin').write_bytes(b'new generation')
        self.exited()
        wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), GOOD)

    def test_review_marker_blocks_startup_without_consuming_records(self):
        self.request()
        wr.json_file(self.folder/'review-required.json', {'reason': 'inspection'})
        with self.assertRaisesRegex(ValueError, '正在核查'):
            wr.begin(self.root)
        self.assertTrue(self.journal.exists())
        self.assertEqual((self.saves/'userdata0000').read_bytes(), RAW)


if __name__ == '__main__':
    unittest.main(verbosity=2)
