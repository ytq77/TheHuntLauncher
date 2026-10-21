"""Exercise the complete copy-only workflow and refusal paths without a game."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import conversion_jobs as jobs


class ConversionJobs(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'hunt'
        self.root.mkdir()
        self.ordinary = self.base / 'ordinary'
        self.source = self.ordinary / 'user/savedata'
        self.seamless = self.root / 'data/user/savedata'
        for folder in (self.source, self.seamless):
            p = folder / '1/CUSA00207/SPRJ0005'
            p.mkdir(parents=True)
            for name in ('userdata0000', 'userdata0001', 'userdata0010', 'backup0001', 'backup0010'):
                (p / name).write_bytes((str(folder) + name).encode())
        game = self.base / 'game'
        game.mkdir()
        (game / 'eboot.bin').write_bytes(b'validated-game')
        (self.ordinary / 'Bloodborne.exe').write_bytes(b'launcher')
        (self.ordinary / 'bbport.ini').write_text('output_res=1280x720\nupscaler=fsr3\npreset=1\n')
        mod = self.root / 'mod'
        mod.mkdir()
        for name in ('save-bridge.dll', 'bbsc-launch.exe', 'LICENSE.txt', 'NOTICE.txt', 'SUPPORTERS.txt'):
            (mod / name).write_bytes(name.encode())
        self.profile = {'install': str(self.ordinary), 'game': str(game), 'graphics_source': str(self.ordinary / 'bbport.ini')}
        for name, value in [('ROOT', self.root), ('EBOOT_SHA256', hashlib.sha256(b'validated-game').hexdigest())]:
            context = patch.object(jobs, name, value)
            context.start()
            self.addCleanup(context.stop)
        for name, value in [('require_stopped', None), ('source_path', self.source)]:
            context = patch.object(jobs, name, return_value=value)
            context.start()
            self.addCleanup(context.stop)
        self.before = (jobs.hashes(self.source), jobs.hashes(self.seamless))

    def create(self):
        return jobs.dispatch({'action': 'create', 'profile': self.profile, 'same_role': True})

    def assert_sources_untouched(self):
        self.assertEqual(self.before, (jobs.hashes(self.source), jobs.hashes(self.seamless)))

    def record(self, job, step, converted, slot=1):
        return {'schema': 1, 'job': job['id'], 'step': step, 'slot': slot, 'own_world': True,
                'session_state': 0, 'flag_6622': True, 'normal_bbsc_initialized': False,
                'kit_display_ready': step == 'inspect',
                'goods': [{'id': number, 'carried': int(number in (jobs.ORIGINAL if converted else jobs.KIT)),
                           'storage': 0} for number in sorted(jobs.ALL_GOODS)]}

    def play(self, job, step):
        root = jobs.job_path(job['id'])
        if step != 'verify':
            value = self.record(job, step, step in ('baseline', 'convert'))
            for i in (1, 2):
                jobs.atomic_json(root / 'mod' / f'{step}-{i}.json', value)
        p = root / step / 'user/savedata/1/CUSA00207/SPRJ0005/userdata0001'
        p.write_bytes(p.read_bytes() + step.encode())
        if step == 'convert':
            jobs.atomic_json(root / 'mod/conversion-outcome.json', {'job': job['id'], 'status': 'memory-ready'})
        return 0

    def advance(self, target):
        self.create()
        with patch.object(jobs, 'play', side_effect=self.play):
            for step in ('baseline', 'inspect', 'convert', 'verify'):
                jobs.dispatch({'action': step, 'cleared': True})
                if step == target:
                    return jobs.load_job()

    def test_complete_workflow_exports_only_after_reload_and_confirmation(self):
        job = self.advance('verify')
        with self.assertRaises(ValueError):
            jobs.dispatch({'action': 'export', 'confirmed': False})
        result = jobs.dispatch({'action': 'export', 'confirmed': True})
        self.assertEqual(result['state'], 'exported')
        with zipfile.ZipFile(result['export']) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('savedata/1/CUSA00207/SPRJ0005/userdata0001', archive.namelist())
            self.assertFalse(any('profile' in name or 'job.json' in name for name in archive.namelist()))
        self.assert_sources_untouched()
        self.assertFalse((self.root / '.save-conversion.lock').exists())

    def test_creation_requires_role_confirmation_and_whitelists_profile(self):
        with self.assertRaises(ValueError):
            jobs.dispatch({'action': 'create', 'profile': self.profile})
        profile = dict(self.profile, network={'password': 'never-copy'})
        jobs.dispatch({'action': 'create', 'profile': profile, 'same_role': True})
        job = jobs.load_job()
        self.assertEqual(set(job['profile']), {'install', 'game', 'graphics_source'})
        text = (jobs.job_path(job['id']) / 'job.json').read_text(encoding='utf-8')
        self.assertNotIn('never-copy', text)
        self.assert_sources_untouched()

    def test_source_changes_stop_before_starting_game(self):
        self.create()
        (self.source / '1/CUSA00207/SPRJ0005/userdata0001').write_bytes(b'new-progress')
        with patch.object(jobs, 'play') as play:
            with self.assertRaisesRegex(ValueError, '原始存档'):
                jobs.dispatch({'action': 'baseline'})
            play.assert_not_called()

    def test_inspection_requires_restored_item_definitions_before_equipment_confirmation(self):
        self.create()
        job = jobs.load_job()
        job['slot'] = 1
        record = self.record(job, 'inspect', False)
        for value in (False, None, 1):
            with self.subTest(value=value):
                record['kit_display_ready'] = value
                with self.assertRaisesRegex(ValueError, '显示定义'):
                    jobs.validate_snapshot(record, job, 'inspect', False)
        record['kit_display_ready'] = True
        jobs.validate_snapshot(record, job, 'inspect', False)

    def test_exit_after_memory_ready_does_not_require_an_extra_snapshot(self):
        self.create()
        self.advance_existing_to_inspected()
        def convert(job, step):
            self.play(job, step)
            jobs.atomic_json(jobs.job_path(job['id']) / 'mod/convert-1.json', self.record(job, step, False))
            jobs.atomic_json(jobs.job_path(job['id']) / 'mod/convert-2.json', self.record(job, step, True))
            return 0
        with patch.object(jobs, 'play', side_effect=convert):
            result = jobs.dispatch({'action': 'convert', 'cleared': True})
        self.assertEqual(result['state'], 'converted')
        self.assert_sources_untouched()

    def test_auxiliary_records_are_preserved_and_refuse_creation(self):
        sidecar = self.root / 'mod/own-world/own-save-1.bin'
        sidecar.parent.mkdir()
        sidecar.write_bytes(b'pending-world')
        with self.assertRaisesRegex(ValueError, '辅助记录'):
            self.create()
        self.assertEqual(sidecar.read_bytes(), b'pending-world')
        self.assert_sources_untouched()

    def test_bad_step_or_unconfirmed_shortcuts_never_starts_game(self):
        self.create()
        with patch.object(jobs, 'play') as play:
            with self.assertRaises(ValueError):
                jobs.dispatch({'action': 'convert', 'cleared': True})
            play.assert_not_called()
        self.advance_existing_to_inspected()
        with patch.object(jobs, 'play') as play:
            with self.assertRaises(ValueError):
                jobs.dispatch({'action': 'convert'})
            play.assert_not_called()

    def advance_existing_to_inspected(self):
        with patch.object(jobs, 'play', side_effect=self.play):
            jobs.dispatch({'action': 'baseline'})
            jobs.dispatch({'action': 'inspect'})

    def test_crash_retains_copies_and_cannot_export(self):
        self.create()
        with patch.object(jobs, 'play', return_value=17):
            with self.assertRaises(ValueError):
                jobs.dispatch({'action': 'baseline'})
        job = jobs.load_job()
        self.assertEqual(job['state'], 'failed')
        self.assertTrue((jobs.job_path(job['id']) / 'baseline/user/savedata').is_dir())
        with self.assertRaises(ValueError):
            jobs.dispatch({'action': 'export', 'confirmed': True})
        self.assert_sources_untouched()

    def test_wrong_role_or_switching_roles_refuses_further_steps(self):
        self.create()
        def switched(job, step):
            self.play(job, step)
            value = self.record(job, step, True, slot=2)
            jobs.atomic_json(jobs.job_path(job['id']) / 'mod/baseline-2.json', value)
            return 0
        with patch.object(jobs, 'play', side_effect=switched):
            with self.assertRaisesRegex(ValueError, '切换了角色'):
                jobs.dispatch({'action': 'baseline'})

    def test_invalid_snapshot_shapes_flags_and_inventory_are_rejected(self):
        self.create()
        job = jobs.load_job()
        good = self.record(job, 'baseline', True)
        bad = []
        for key, value in [('slot', True), ('slot', 10), ('job', 'different'), ('own_world', False),
                           ('normal_bbsc_initialized', True), ('flag_6622', False), ('session_state', 6)]:
            bad.append(dict(good, **{key: value}))
        for mutate in ('duplicate', 'bool-count', 'storage'):
            item = json.loads(json.dumps(good))
            if mutate == 'duplicate': item['goods'][0] = item['goods'][1]
            elif mutate == 'bool-count': item['goods'][0]['carried'] = True
            else: item['goods'][0]['storage'] = 1
            bad.append(item)
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): jobs.validate_snapshot(value, job, 'baseline', True)

    def test_conversion_missing_result_or_unrelated_slot_write_is_rejected(self):
        for mode in ('missing-result', 'other-role'):
            with self.subTest(mode=mode):
                self.create()
                self.advance_existing_to_inspected()
                def unexpected(job, step):
                    self.play(job, step)
                    root = jobs.job_path(job['id'])
                    if mode == 'missing-result': (root / 'mod/conversion-outcome.json').unlink()
                    else: (root / step / 'user/savedata/1/CUSA00207/SPRJ0005/userdata0000').write_bytes(b'other-role')
                    return 0
                with patch.object(jobs, 'play', side_effect=unexpected):
                    with self.assertRaises((ValueError, OSError)):
                        jobs.dispatch({'action': 'convert', 'cleared': True})
                self.assertEqual(jobs.load_job()['state'], 'failed')
        self.assert_sources_untouched()

    def test_copy_failure_does_not_publish_job_or_touch_sources(self):
        with patch.object(jobs.shutil, 'copytree', side_effect=OSError('disk-full')):
            with self.assertRaises(OSError): self.create()
        self.assertFalse((self.root / 'conversion-current.json').exists())
        self.assert_sources_untouched()

    def test_existing_output_and_unsafe_job_ids_are_rejected(self):
        for value in ('../outside', 'A' * 32, '1234', None):
            with self.assertRaises(ValueError): jobs.job_path(value)
        source = self.source
        with self.assertRaises(ValueError): jobs.copy_verified(source, source)
        with self.assertRaises(ValueError): jobs.copy_verified(source, self.seamless)

    def test_stale_lock_recovers_but_active_worker_blocks(self):
        lock = self.root / '.save-conversion.lock'
        lock.write_text('{"pid": 1111}')
        with patch.object(jobs, 'pid_alive', return_value=True):
            with self.assertRaises(ValueError): self.create()
        with patch.object(jobs, 'pid_alive', return_value=False): self.create()
        self.assertFalse(lock.exists())

    def test_private_job_and_pointer_cannot_be_packaged(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        from package import check_public
        for name in ('conversion-current.json', '.save-conversion.lock', 'conversion-jobs/abc/backup/userdata0001'):
            with self.assertRaises(ValueError): check_public([name])


if __name__ == '__main__':
    unittest.main()
