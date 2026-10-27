import copy
import json
import os
from pathlib import Path
import struct
import time
import unittest

from test_world_recovery import RecoveryTests
import world_recovery as wr
import save_proof


class SaveProofTests(RecoveryTests):
    # Only new tests are enumerated by this module's explicit suite below.
    def setup_proof(self):
        self.request()
        self.exited()
        wr.finalize(self.root, self.token)
        self.token2 = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        self.request2 = wr.read_request(self.root,self.path)
        self.value = wr.prepare(self.request2)
        self.proof = self.folder/'save-proof'/self.token2
        ctx = json.loads((self.proof/'context.json').read_text())
        now = time.time_ns()//1000000+100
        self.loaded = {'stage':'loaded','sequence':1,'pid':1234,'monotonic_ms':100,
                       'epoch_ms':now,'sha256':ctx['bank_sha256']}
        # Simulate the game's own event completion reset after the correct load.
        raw = bytearray((self.saves/'userdata0000').read_bytes())
        raw[self.bank+625] ^= 1
        # Ensure a restored bit differs, rather than unrelated ordinary progress.
        old = (wr.job_for(self.request2)/'before/userdata0000').read_bytes()
        expected = (wr.job_for(self.request2)/'expected/userdata0000').read_bytes()
        changed = next(i for i in range(150000) if old[self.bank+i] != expected[self.bank+i])
        raw[self.bank+changed] = old[self.bank+changed]
        self.raw = bytes(raw)
        self.entry = {'stage':'save-entry','sequence':2,'pid':1234,'monotonic_ms':101,
                      'epoch_ms':now+1,'sha256':wr.sha(self.raw[self.bank:self.bank+150000])}
        self.write_receipt([self.loaded,self.entry])
        for name in self.value['files']:
            path = self.saves/name
            path.write_bytes(self.raw)
            os.utime(path,ns=((now+2)*1000000,(now+2)*1000000))
        self.current = {name:(self.saves/name).read_bytes() for name in self.value['files']}

    def write_receipt(self, records):
        (self.proof/'native.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))

    def verify(self):
        return save_proof.verify(self.request2,self.value,self.current)

    def test_proof_accepts_game_authored_changed_bits_and_archives(self):
        self.setup_proof()
        self.assertFalse(wr.file_matches(self.request2,self.value,'userdata0000',self.raw))
        before = dict(self.current)
        self.assertTrue(wr.finish(self.request2,self.value,proof='test native disk descendant'))
        for name,raw in before.items(): self.assertEqual((self.saves/name).read_bytes(),raw)
        state = json.loads((wr.job_for(self.request2)/'job.json').read_text())
        self.assertEqual(state['state'],'complete')
        self.assertIn('native_save_proof',state)
        self.assertTrue(self.journal.with_suffix('.bin.restored').exists())
        receipt = (wr.job_for(self.request2)/'native-save-proof.jsonl').read_bytes()
        self.assertEqual(wr.sha(receipt),state['native_save_proof']['native_receipt_sha256'])
        # Later log appends cannot change the immutable consumed receipt.
        with (self.proof/'native.jsonl').open('a') as stream:stream.write('{}\n')
        self.assertEqual((wr.job_for(self.request2)/'native-save-proof.jsonl').read_bytes(),receipt)
        new_env = wr.begin(self.root)
        self.assertNotIn('THEHUNT_SAVE_PROOF_DIR',new_env)
        for name,raw in before.items(): self.assertEqual((self.saves/name).read_bytes(),raw)

    def test_proof_save_entry_without_disk_writes_is_insufficient(self):
        self.setup_proof()
        path = self.saves/'backup0000'
        os.utime(path,ns=(1,1))
        self.assertIsNone(self.verify())

    def test_proof_no_loaded_receipt_rejected(self):
        self.setup_proof(); self.write_receipt([self.entry]); self.assertIsNone(self.verify())

    def test_proof_wrong_loaded_bank_rejected(self):
        self.setup_proof(); self.loaded['sha256']='0'*64
        self.write_receipt([self.loaded,self.entry]); self.assertIsNone(self.verify())

    def test_proof_wrong_saved_bank_rejected(self):
        self.setup_proof(); self.entry['sha256']='0'*64
        self.write_receipt([self.loaded,self.entry]); self.assertIsNone(self.verify())

    def test_proof_changed_identity_rejected(self):
        self.setup_proof()
        raw = bytearray(self.raw); role=wr.offline_save.parse(raw); raw[role.username+1] ^= 1
        self.current['userdata0000']=bytes(raw)
        (self.saves/'userdata0000').write_bytes(raw)
        self.assertIsNone(self.verify())

    def test_proof_mixed_processes_rejected(self):
        self.setup_proof(); self.entry['pid']=1235
        self.write_receipt([self.loaded,self.entry]); self.assertIsNone(self.verify())

    def test_proof_bad_sequence_rejected(self):
        self.setup_proof(); self.entry['sequence']=3
        self.write_receipt([self.loaded,self.entry]); self.assertIsNone(self.verify())

    def test_proof_before_load_save_rejected(self):
        self.setup_proof(); self.entry['monotonic_ms']=99
        self.write_receipt([self.loaded,self.entry]); self.assertIsNone(self.verify())

    def test_proof_partial_append_rejected(self):
        self.setup_proof()
        path=self.proof/'native.jsonl';path.write_bytes(path.read_bytes().rstrip(b'\n'))
        self.assertIsNone(self.verify())

    def test_proof_prior_run_receipt_rejected(self):
        self.setup_proof(); self.loaded['epoch_ms']=1
        self.write_receipt([self.loaded,self.entry]); self.assertIsNone(self.verify())

    def test_proof_failed_run_rejected(self):
        self.setup_proof();wr.process_exited(self.root,self.token2,3);self.assertIsNone(self.verify())

    def test_proof_tampered_input_rejected(self):
        self.setup_proof(); (self.proof/'input-save.bin').write_bytes(self.raw)
        with self.assertRaises(ValueError): self.verify()

    def test_proof_tampered_bank_rejected(self):
        self.setup_proof(); (self.proof/'expected-bank.bin').write_bytes(bytes(150000))
        with self.assertRaises(ValueError): self.verify()

    def test_proof_does_not_prepare_from_unverified_primary(self):
        self.setup_proof()
        token=wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        self.assertFalse((self.folder/'save-proof'/token).exists())


def load_tests(loader, tests, pattern):
    names=[name for name in SaveProofTests.__dict__ if name.startswith('test_proof_')]
    return unittest.TestSuite(SaveProofTests(name) for name in names)


if __name__ == '__main__':
    unittest.main()
