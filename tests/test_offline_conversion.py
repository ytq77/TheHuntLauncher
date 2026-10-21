"""Offline entry integration, including real backup/swap on temporary trees."""
import unittest
from unittest.mock import patch
import json
import zipfile
import struct

import test_conversion_jobs as setup_jobs
from test_offline_save import fixture
from test_offline_aux import record, with_flags

jobs = setup_jobs.jobs


class OfflineConversion(unittest.TestCase):
    def setUp(self):
        setup_jobs.ConversionJobs.setUp(self)
        for folder, kit in ((self.source,False),(self.seamless,True)):
            root = folder/'1/CUSA00207/SPRJ0005'
            for slot in range(10):
                (root/f'userdata{slot:04}').write_bytes(fixture(kit) if slot==1 else bytes(0x140000))
            (root/'backup0001').write_bytes(fixture(kit))
        self.before = jobs.hashes(self.source),jobs.hashes(self.seamless)

    def begin(self, output='export'):
        return jobs.dispatch({'action':'begin','flow':'offline-v1','profile':self.profile,
                              'same_role':True,'output':output})

    def test_export_does_not_launch_game_or_change_sources(self):
        with patch.object(jobs,'play',side_effect=AssertionError('Must not launch game')):
            result = self.begin()
        self.assertEqual(result['state'],'exported')
        self.assertIsNone(result['next'])
        self.assertEqual(self.before,(jobs.hashes(self.source),jobs.hashes(self.seamless)))
        with zipfile.ZipFile(result['export']) as archive:
            self.assertIsNone(archive.testzip())
            self.assertNotEqual(archive.read('savedata/1/CUSA00207/SPRJ0005/userdata0001'),fixture(True))
        fields = json.loads((jobs.job_path(result['id'])/'offline-fields.json').read_text())
        self.assertIn('inventory-only',fields['equipment_policy'])

    def test_explicit_replace_backs_up_whole_ordinary_and_keeps_seamless(self):
        result = self.begin('replace')
        self.assertEqual(result['state'],'applied')
        self.assertEqual(jobs.hashes(__import__('pathlib').Path(result['ordinary_backup'])),self.before[0])
        self.assertEqual(jobs.hashes(self.seamless),self.before[1])
        self.assertNotEqual(jobs.hashes(self.source),self.before[0])

    def test_multiple_kit_roles_refused_without_source_writes(self):
        (self.seamless/'1/CUSA00207/SPRJ0005/userdata0000').write_bytes(fixture(True))
        before = jobs.hashes(self.source),jobs.hashes(self.seamless)
        with self.assertRaises(ValueError): self.begin()
        self.assertEqual(before,(jobs.hashes(self.source),jobs.hashes(self.seamless)))
        self.assertEqual(jobs.load_job()['state'],'failed')

    def test_no_kit_role_refused(self):
        (self.seamless/'1/CUSA00207/SPRJ0005/userdata0001').write_bytes(fixture(False))
        with self.assertRaises(ValueError): self.begin()

    def test_bad_backup_refused_before_export(self):
        (self.seamless/'1/CUSA00207/SPRJ0005/backup0001').write_bytes(bytes(0x140000))
        with self.assertRaises(ValueError): self.begin()
        self.assertFalse((jobs.job_path(jobs.load_job()['id'])/'converted-save.zip').exists())

    def test_partial_baseline_ownership_is_recorded_and_not_expanded(self):
        path = self.source/'1/CUSA00207/SPRJ0005/userdata0001'
        raw = bytearray(path.read_bytes())
        role = jobs.offline_save.parse(raw)
        struct.pack_into('<IIII',raw,role.goods(225)[0].offset,0,0xFFFFFFFF,0,0)
        struct.pack_into('<I',raw,role.username+453,jobs.offline_save.u32(raw,role.username+453)-1)
        path.write_bytes(raw)
        result = self.begin()
        fields = json.loads((jobs.job_path(result['id'])/'offline-fields.json').read_text())
        self.assertEqual(fields['restored_original_goods'],[200,205,111])
        with zipfile.ZipFile(result['export']) as archive:
            for stem in ('userdata','backup'):
                raw = archive.read(f'savedata/1/CUSA00207/SPRJ0005/{stem}0001')
                jobs.offline_save.expect_goods(jobs.offline_save.parse(raw),(200,205,111))

    def test_auxiliary_recovery_record_refused(self):
        folder=self.root/'mod/guest-items'
        folder.mkdir()
        (folder/'pending.json').write_text('{}')
        with self.assertRaises(ValueError): self.begin()

    def settled_auxiliary(self):
        rows = [(52800,b'\xff\x0f'+bytes(123)),(52420,b'\x08'+bytes(124))]
        role = self.seamless/'1/CUSA00207/SPRJ0005'
        for stem in ('userdata','backup'):
            (role/f'{stem}0001').write_bytes(with_flags(fixture(True),rows))
        files = [('guest-items/taken-save-1.bin',record(0x544C4242,rows)),
                 ('own-world/own-save-1.bin.restored',record(0x574F4242,[(12400,bytes(125))]))]
        for name,data in files:
            path = self.root/'mod'/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data)
        self.before = jobs.hashes(self.source),jobs.hashes(self.seamless)
        return files

    def test_settled_auxiliary_is_backed_up_but_not_exported_or_replayed(self):
        files = self.settled_auxiliary()
        result = self.begin('replace')
        root = jobs.job_path(result['id'])
        proof = json.loads((root/'auxiliary-check.json').read_text())
        self.assertFalse(proof['world_flag_writes'])
        self.assertEqual(len(proof['records']),2)
        for name,data in files:
            self.assertEqual((root/'backup/auxiliary'/name).read_bytes(),data)
            self.assertEqual((self.root/'mod'/name).read_bytes(),data)
        for stem in ('userdata','backup'):
            relative = f'1/CUSA00207/SPRJ0005/{stem}0001'
            before = (self.seamless/relative).read_bytes()
            after = (self.source/relative).read_bytes()
            start,length = struct.unpack_from('<II',before,52)
            self.assertEqual(before[start:start+length],after[start:start+length])
        with zipfile.ZipFile(result['export']) as archive:
            self.assertFalse(any('guest-items' in name or 'own-world' in name for name in archive.namelist()))
        self.assertEqual(jobs.hashes(self.seamless),self.before[1])

    def test_pending_world_recovery_is_backed_up_and_stops_before_replacement(self):
        self.settled_auxiliary()
        pending = self.root/'mod/own-world/own-save-1.bin'
        data = record(0x574F4242,[(12400,bytes(125))])
        pending.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'尚待恢复'):self.begin('replace')
        root = jobs.job_path(jobs.load_job()['id'])
        self.assertEqual((root/'backup/auxiliary/own-world/own-save-1.bin').read_bytes(),data)
        self.assertEqual(self.before,(jobs.hashes(self.source),jobs.hashes(self.seamless)))
        self.assertFalse((root/'converted-save.zip').exists())

    def test_changed_auxiliary_during_transform_prevents_export(self):
        self.settled_auxiliary()
        original = jobs.offline_save.convert
        def change_record(*args):
            result = original(*args)
            (self.root/'mod/guest-items/taken-save-1.bin').write_bytes(b'new-session')
            return result
        with patch.object(jobs.offline_save,'convert',side_effect=change_record):
            with self.assertRaisesRegex(ValueError,'辅助记录发生变化'):self.begin('replace')
        self.assertEqual(self.before,(jobs.hashes(self.source),jobs.hashes(self.seamless)))
        self.assertFalse((jobs.job_path(jobs.load_job()['id'])/'converted-save.zip').exists())

    def test_changed_source_during_transform_prevents_export(self):
        original=jobs.offline_save.convert
        def change_source(*args):
            result=original(*args)
            (self.source/'1/CUSA00207/SPRJ0005/userdata0010').write_bytes(b'changed')
            return result
        with patch.object(jobs.offline_save,'convert',side_effect=change_source):
            with self.assertRaises(ValueError): self.begin()
        self.assertFalse((jobs.job_path(jobs.load_job()['id'])/'converted-save.zip').exists())


if __name__=='__main__': unittest.main()
