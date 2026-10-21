"""Offline entry integration, including real backup/swap on temporary trees."""
import unittest
from unittest.mock import patch
import json
import zipfile

import test_conversion_jobs as setup_jobs
from test_offline_save import fixture

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

    def test_auxiliary_recovery_record_refused(self):
        folder=self.root/'mod/guest-items'
        folder.mkdir()
        (folder/'pending.json').write_text('{}')
        with self.assertRaises(ValueError): self.begin()

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
