"""Authoritative title input and later full-save receipts on an isolated synthetic mount."""
import json
import os
import time
import unittest

from test_world_recovery import GOOD, RAW, RecoveryTests
import world_recovery as wr


class TitleTests(RecoveryTests):
    def setUp(self):
        super().setUp()
        self.req = self.request(wr.TITLE_RECORDS)
        self.value = wr.prepare(self.req)
        self.proof = self.folder/'title-save-proof'/self.req['id']
        self.proof.mkdir(parents=True)
        self.source = self.proof/'input-save.bin'
        self.source.write_bytes(GOOD)
        original = RAW[self.bank:self.bank+150000]
        (self.proof/'quit-bank.bin').write_bytes(original)
        now = time.time_ns()//1000000 + 1
        def record(index, raw):
            return dict(sequence=index, stage='title-input' if index == 1 else 'save-entry',
                        pid=2732, epoch_ms=now+index, monotonic_ms=index, slot=0, token=self.token,
                        generation=6, recovery_sequence=self.req['seq'], sha256=wr.sha(raw),
                        quit_bank_sha256=wr.sha(original))
        # Both generations progress after the verified title input. Their full bytes differ.
        self.files = {'userdata0000': RAW[:-1]+b'7', 'backup0000': GOOD[:-1]+b'8'}
        self.rows = [record(1, GOOD), record(2, self.files['backup0000']),
                     record(3, self.files['userdata0000'])]
        for name, raw in self.files.items():
            path = self.saves/name
            path.write_bytes(raw)
            os.utime(path, ns=((now+4)*1000000, (now+4)*1000000))
        self.write()
        self.exited()

    def write(self):
        raw = b''.join(json.dumps(r).encode()+b'\n' for r in self.rows)
        (self.proof/'native.jsonl').write_bytes(raw)
        self.req['live_title_receipt'] = wr.sha(raw)

    def finalize(self):
        return wr.finalize(self.root, self.token, observed_exit=0, title_receipt=self.req['live_title_receipt'])

    def test_title_unsealed_or_superseded_transcript_cannot_finish_after_restart(self):
        for token, digest in ((self.token, None), (self.token, '0'*64), (None, self.req['live_title_receipt'])):
            with self.subTest(token=token, digest=digest), self.assertRaisesRegex(ValueError, '尚未核实'):
                wr.finalize(self.root, token, observed_exit=0, title_receipt=digest)
        self.assertTrue(self.journal.exists())
        self.assertFalse((self.folder/'acks/0.ack').exists())

    def test_title_missing_final_ack_remains_pending_on_restart(self):
        from unittest.mock import patch
        with patch.object(wr, 'confirm', side_effect=PermissionError('final ack unavailable')):
            with self.assertRaises(PermissionError):
                self.finalize()
        with self.assertRaisesRegex(ValueError, '缺少最终完成回执'):
            wr.finalize(self.root, None)
        self.assertEqual(wr.review_items(self.root)[0]['state'], 'review-required')
        self.assertFalse((self.folder/'acks/0.ack').exists())

    def test_title_archives_both_full_outputs_and_first_input(self):
        self.assertEqual(self.finalize(), ['verified'])
        self.assertEqual(wr.review_items(self.root), [])
        job = wr.job_for(self.req)
        self.assertEqual((job/'native-title-input.bin').read_bytes(), GOOD)
        result = json.loads((job/'job.json').read_bytes())
        self.assertEqual(result['native_save_proof']['origin'], 'same-role-title-input')
        self.assertEqual(result['native_save_proof']['final_worker_sha256'], self.req['live_title_receipt'])
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.files, {k:(self.saves/k).read_bytes() for k in self.files})

    def test_title_requires_one_authorized_generation_slot_token_and_exit(self):
        for key, bad in [('token','0'*32), ('pid', 15), ('generation', 7), ('slot', 1),
                         ('recovery_sequence', 400), ('sequence', 4), ('stage', 'title-input')]:
            with self.subTest(key):
                self.rows[-1][key], previous = bad, self.rows[-1][key]
                self.write()
                self.assertFalse(wr.saved_matches(self.req, self.value))
                self.rows[-1][key] = previous
        self.write()
        self.exited(23)
        self.assertFalse(wr.saved_matches(self.req, self.value))

    def test_title_unknown_first_input_never_retires_even_if_later_files_match(self):
        self.source.write_bytes(RAW)
        self.rows[0]['sha256'] = wr.sha(RAW)
        self.write()
        for name in self.files:
            (self.saves/name).write_bytes(GOOD)
        with self.assertRaisesRegex(ValueError, '尚未核实'):
            self.finalize()
        self.assertEqual(wr.review_items(self.root)[0]['state'], 'review-required')
        self.assertTrue(self.journal.exists())

    def test_title_rejects_unlogged_backup_and_stale_primary(self):
        backup = self.saves/'backup0000'
        backup.write_bytes(self.files['backup0000'][:-1]+b'9')
        self.assertFalse(wr.saved_matches(self.req, self.value))
        self.assertTrue(self.journal.exists())
        os.utime(self.saves/'userdata0000', ns=(1,1))
        backup.write_bytes(self.files['backup0000'])
        self.assertFalse(wr.saved_matches(self.req, self.value))

    def test_title_incomplete_evidence_cannot_use_static_or_running_fallback(self):
        (self.proof/'native.jsonl').unlink()
        for name in self.files:
            (self.saves/name).write_bytes(GOOD)
        self.assertFalse(wr.saved_matches(self.req, self.value, running_token=self.token))
        self.assertEqual(set(wr.compare_generations(self.req)[1].values()), {'unverified'})
        with self.assertRaisesRegex(ValueError, '尚未核实'):
            self.finalize()
        self.assertFalse((self.folder/'acks/0.ack').exists())

    def test_title_last_generated_output_must_reach_the_primary(self):
        self.rows.append(dict(self.rows[-1], sequence=4, sha256=wr.sha(b'newer output')))
        self.write()
        self.assertFalse(wr.saved_matches(self.req, self.value))
        self.assertTrue(self.journal.exists())

    def test_title_first_input_pickup_cannot_disappear_from_a_later_record(self):
        # The synthetic transaction has a single row. Rebind it as a pickup group
        # with a newly acquired bit; the later native files must also retain it.
        key, index, before, after = self.req['rows'][0]
        self.req['rows'] = [(50001, index, before, bytes(a | b for a, b in zip(before, after)))]
        first = bytearray(wr.image(GOOD, self.req))
        offset = self.bank + index*125
        bit, mask = next((i, 1 << j) for i in range(125) for j in range(8)
                         if not first[offset+i] & (1 << j))
        first[offset+bit] |= mask
        self.source.write_bytes(first)
        original = first[self.bank:self.bank+150000]
        (self.proof/'quit-bank.bin').write_bytes(original)
        for row in self.rows:
            row['quit_bank_sha256'] = wr.sha(original)
        self.rows[0]['sha256'] = wr.sha(first)
        self.write()
        import home_save_proof
        self.assertIsNone(home_save_proof.verify_title(self.req, self.value, self.files))

    def test_title_refuses_change_outside_exit_delta_even_if_all_targets_match(self):
        raw = bytearray(GOOD)
        raw[self.bank+149999] ^= 1
        self.source.write_bytes(raw)
        self.rows[0]['sha256'] = wr.sha(raw)
        self.write()
        self.assertFalse(wr.saved_matches(self.req, self.value))

    def test_title_unconsumed_live_quit_uses_original_stopped_exit(self):
        for name in self.files:
            (self.saves/name).write_bytes(RAW)
        self.assertEqual(wr.finalize(self.root, self.token, quit_sequence=self.req['seq']),
                         ['primary-restored-waiting-backup-save'])
        self.assertEqual(wr.request_header(self.path.read_bytes())[3], wr.QUIT)
        self.assertEqual((self.saves/'backup0000').read_bytes(), RAW)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(TitleTests(n) for n in TitleTests.__dict__ if n.startswith('test_title_'))
