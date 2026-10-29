import json
import os
import time
import unittest

from test_world_recovery import RecoveryTests, GOOD, RAW
import world_recovery as wr
import home_save_proof


class HomeProofTests(RecoveryTests):
    def setup_home(self):
        self.request(wr.RESTORE)
        wr.poll(self.root, self.token)
        self.req = self.request(wr.APPLIED)
        self.value = wr.prepare(self.req)
        self.proof = self.folder/'home-save-proof'/self.req['id']
        self.proof.mkdir(parents=True)
        bank = GOOD[self.bank:self.bank+150000]
        (self.proof/'restored-bank.bin').write_bytes(bank)
        now = time.time_ns()//1000000+100
        self.restored = {'stage': 'restored', 'sequence': 1, 'pid': 5678,
                         'recovery_sequence': self.req['seq'], 'slot': 0,
                         'monotonic_ms': 10, 'epoch_ms': now, 'sha256': wr.sha(bank)}
        # Native load events change a restored bit, as in the submitted round.
        later = bytearray(GOOD)
        changed = next(i for i in range(150000)
                       if RAW[self.bank+i] != GOOD[self.bank+i])
        later[self.bank+changed] = RAW[self.bank+changed]
        later[-1] ^= 1  # Preserve newer personal bytes too.
        self.later = bytes(later)
        self.entry = dict(self.restored, stage='save-entry', sequence=2,
                          monotonic_ms=11, epoch_ms=now+1,
                          sha256=wr.sha(self.later[self.bank:self.bank+150000]))
        self.write_home()
        for name in self.value['files']:
            p = self.saves/name
            p.write_bytes(self.later)
            os.utime(p, ns=((now+2)*1000000, (now+2)*1000000))
        self.current = {name: (self.saves/name).read_bytes() for name in self.value['files']}

    def write_home(self, records=None):
        (self.proof/'native.jsonl').write_text(''.join(
            json.dumps(r)+'\n' for r in (records or [self.restored, self.entry])))

    def verify_home(self):
        return home_save_proof.verify(self.req, self.value, self.current)

    def test_home_archives_native_descendants_without_rewriting_saves(self):
        self.setup_home()
        self.assertFalse(wr.file_matches(self.req, self.value, 'userdata0000', self.later))
        before = dict(self.current)
        self.assertTrue(wr.finish(self.req, self.value, proof='native home save descendant'))
        self.assertEqual(before, {n:(self.saves/n).read_bytes() for n in before})
        job = wr.job_for(self.req)
        status = json.loads((job/'job.json').read_text())
        self.assertEqual(status['state'], 'complete')
        self.assertEqual(status['native_save_proof']['origin'], 'same-run-home-restore')
        self.assertTrue(self.journal.with_suffix('.bin.restored').exists())
        self.assertEqual((job/'native-save-proof.jsonl').read_bytes(),
                         (self.proof/'native.jsonl').read_bytes())
        self.assertEqual((job/'native-restored-bank.bin').read_bytes(),
                         (self.proof/'restored-bank.bin').read_bytes())

    def test_home_no_receipt_retains_original_progress_guard(self):
        self.setup_home()
        (self.proof/'native.jsonl').unlink()
        self.exited()
        with self.assertRaisesRegex(ValueError, '新的事件进度'):
            wr.finalize(self.root, self.token)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), self.later)
        self.assertTrue(self.journal.exists())

    def test_home_wrong_restored_target_rejected(self):
        self.setup_home()
        bad = RAW[self.bank:self.bank+150000]
        (self.proof/'restored-bank.bin').write_bytes(bad)
        self.restored['sha256'] = wr.sha(bad)
        self.write_home()
        self.assertIsNone(self.verify_home())

    def test_home_primary_only_is_insufficient(self):
        self.setup_home()
        p = self.saves/'backup0000'; p.write_bytes(RAW)
        self.current['backup0000'] = RAW
        self.assertIsNone(self.verify_home())

    def test_home_save_entry_without_actual_disk_save_rejected(self):
        self.setup_home()
        os.utime(self.saves/'backup0000', ns=(1, 1))
        self.assertIsNone(self.verify_home())

    def test_home_wrong_receipt_identity_order_and_time_rejected(self):
        for field, bad in [('pid', 777), ('slot', 1), ('recovery_sequence', 2),
                           ('sequence', 3), ('monotonic_ms', 9), ('epoch_ms', 1),
                           ('sha256', '0'*64), ('stage', 'restored')]:
            with self.subTest(field=field):
                self.setup_home_for_subtest(field, bad)

    def setup_home_for_subtest(self, field, bad):
        # setUp owns one transaction; change only one receipt field at a time.
        if not hasattr(self, 'proof'):
            self.setup_home()
        old = self.entry[field]; self.entry[field] = bad
        self.write_home(); self.assertIsNone(self.verify_home())
        self.entry[field] = old; self.write_home()

    def test_home_failed_process_exit_rejected(self):
        self.setup_home(); self.exited(3)
        self.assertIsNone(self.verify_home())

    def test_home_non_applied_request_rejected(self):
        self.setup_home()
        for mode in (wr.CAPTURE, wr.RESTORE, wr.QUIT, wr.BLOCKED):
            self.req['mode'] = mode
            self.assertIsNone(self.verify_home())

    def test_home_missing_restored_partial_and_short_receipt_rejected(self):
        self.setup_home()
        self.write_home([self.entry]); self.assertIsNone(self.verify_home())
        self.write_home()
        p=self.proof/'native.jsonl'; p.write_bytes(p.read_bytes().rstrip(b'\n'))
        self.assertIsNone(self.verify_home())
        self.write_home([self.restored]); self.assertIsNone(self.verify_home())

    def test_home_changed_character_and_stale_disk_snapshot_rejected(self):
        self.setup_home()
        raw = bytearray(self.later); role=wr.offline_save.parse(raw)
        raw[role.username+1] ^= 1
        self.current['userdata0000'] = bytes(raw)
        (self.saves/'userdata0000').write_bytes(raw)
        self.assertIsNone(self.verify_home())


def load_tests(loader, tests, pattern):
    names = [name for name in HomeProofTests.__dict__ if name.startswith('test_home_')]
    return unittest.TestSuite(HomeProofTests(name) for name in names)
