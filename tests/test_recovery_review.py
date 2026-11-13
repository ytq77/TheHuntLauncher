import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_world_recovery as fixture

wr = fixture.wr
RAW, GOOD = fixture.RAW, fixture.GOOD


class ReviewTests(fixture.RecoveryTests):
    def test_review_crash_modes_preserves_originals_and_refuses_start(self):
        for mode in (wr.CAPTURE, wr.RESTORE, wr.QUIT):
            with self.subTest(mode=mode):
                key, index, before, after = self.row
                self.request(mode, rows=[(key, index, after if mode == wr.CAPTURE else before, after)])
                self.exited(0xc0000005)
                original = wr.snapshot(self.root)
                report = wr.review(self.root)
                self.assertEqual(len(report['items']), 1)
                item = report['items'][0]
                self.assertEqual((item['state'], item['slot'], item['mode'], item['recorded_exit']),
                                 ('review-required', 0, wr.MODE_NAMES[mode-1], 0xc0000005))
                self.assertEqual({name: row['state'] for name, row in item['generations'].items()},
                                 {'userdata0000': 'unverified', 'backup0000': 'unverified'})
                with self.assertRaisesRegex(ValueError, '世界恢复复核'):
                    wr.begin(self.root)
                self.assertEqual(wr.snapshot(self.root), original)

    def test_review_verified_old_primary_continues_until_real_backup(self):
        self.request(wr.RESTORE)
        primary = self.saves/'userdata0000'
        backup = self.saves/'backup0000'
        primary.write_bytes(GOOD)
        self.assertEqual(wr.review(self.root)['items'][0]['state'], 'waiting-backup')
        new_token = wr.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        self.assertTrue(self.journal.with_suffix('.bin.awaiting-save').exists())
        ack = self.folder/'acks/0.ack'
        self.assertFalse(ack.exists())
        self.assertEqual((primary.read_bytes(), backup.read_bytes()), (GOOD, RAW))
        backup.write_bytes(GOOD)
        wr.poll(self.root, new_token)
        self.assertTrue(ack.exists())
        self.assertEqual(wr.review(self.root)['items'], [])

    def test_review_same_name_with_different_face_does_not_resume(self):
        self.request(wr.RESTORE)
        foreign = bytearray(GOOD)
        foreign[wr.offline_save.parse(GOOD).face+4] ^= 1
        (self.saves/'userdata0000').write_bytes(foreign)
        item = wr.review(self.root)['items'][0]
        self.assertEqual(item['generations']['userdata0000']['state'], 'different-character')
        with self.assertRaisesRegex(ValueError, '角色不一致'):
            wr.begin(self.root)
        self.assertEqual((self.saves/'userdata0000').read_bytes(), foreign)

    def test_review_verified_old_quit_archives_both_without_replay(self):
        self.request(wr.QUIT)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        original = wr.snapshot(self.saves)
        self.assertEqual(wr.review(self.root)['items'][0]['state'], 'verified')
        self.assertFalse((self.folder/'acks/0.ack').exists())
        wr.begin(self.root)
        self.assertTrue((self.folder/'acks/0.ack').exists())
        self.assertEqual(wr.snapshot(self.saves), original)

    def test_review_acknowledged_archive_does_not_block_after_import_backed_it_up(self):
        self.request(wr.RESTORE)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        wr.begin(self.root)
        self.journal.with_suffix('.bin.restored').rename(self.root/'imported-archive.bin')
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(RAW)
        original = wr.snapshot(self.saves)
        self.assertEqual(wr.review(self.root)['items'], [])
        wr.begin(self.root)
        self.assertEqual(wr.snapshot(self.saves), original)

    def test_review_missing_ack_uses_verified_archive_and_blocks_unknown_proof(self):
        request = self.request(wr.RESTORE)
        for name in ('userdata0000', 'backup0000'):
            (self.saves/name).write_bytes(GOOD)
        wr.begin(self.root)
        ack = self.folder/'acks/0.ack'
        ack.unlink()
        (self.saves/'backup0000').write_bytes(RAW)
        originals = wr.snapshot(self.saves)
        self.assertEqual(wr.review(self.root)['items'], [])
        self.assertFalse(ack.exists())
        wr.begin(self.root)
        self.assertEqual(int.from_bytes(ack.read_bytes(), 'little'), request['seq'])
        self.assertEqual(wr.snapshot(self.saves), originals)
        ack.unlink()
        manifest = wr.job_for(request)/'job.json'
        value = json.loads(wr.stable(manifest))
        value['proof'] = 'unknown historical proof'
        wr.json_file(manifest, value)
        self.assertIn('无法重新核验', wr.review(self.root)['items'][0]['reason'])
        with self.assertRaisesRegex(ValueError, '无法重新核验'):
            wr.begin(self.root)
        self.assertFalse(ack.exists())
        self.assertEqual(wr.snapshot(self.saves), originals)

    def test_review_unfinished_job_without_request_or_journal_is_blocked(self):
        request = self.request(wr.RESTORE)
        wr.prepare(request)
        self.path.unlink()
        self.journal.unlink()
        items = wr.review(self.root)['items']
        self.assertEqual([row['kind'] for row in items], ['orphan-job'])
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            wr.begin(self.root)
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            wr.require_review_clear_for_transfer(self.root)

    def test_review_moved_root_and_marker_are_distinct_and_transfer_stays_blocked(self):
        self.request(wr.RESTORE)
        run = self.folder/'runs'/f'{self.token}.json'
        context = json.loads(wr.stable(run))
        context['root'] += '-previous'
        wr.json_file(run, context)
        before = wr.snapshot(self.root)
        items = wr.review(self.root)['items']
        request = next(row for row in items if row['kind'] == 'request')
        self.assertIn('另一运行环境', request['reason'])
        self.assertEqual(request['generations']['userdata0000']['state'], 'unverified')
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            wr.begin(self.root)
        self.assertEqual(wr.snapshot(self.root), before)
        self.journal.unlink()  # A lost journal cannot clear an unresolved request for transfer.
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            wr.require_review_clear_for_transfer(self.root)
        marker = self.folder/'review-required.json'
        wr.json_file(marker, {'origin': 'isolated-review', 'reason': 'operator retained evidence'})
        self.path.unlink()
        self.assertEqual(wr.review(self.root)['items'][0]['origin']['origin'], 'isolated-review')
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            wr.require_review_clear_for_transfer(self.root)
        self.assertTrue(marker.exists())

    def test_review_orphan_copy_validates_exact_sources_and_keeps_original_blocked(self):
        self.journal.write_bytes(b'BBOW')
        (self.root/'data/user/trophies.log').write_bytes(b'private fixture data')
        report = wr.review(self.root)
        self.assertEqual(report['items'][0]['kind'], 'orphan')
        primary = self.saves/'userdata0000'
        primary.write_bytes(GOOD)
        with self.assertRaisesRegex(ValueError, '来源文件发生变化'):
            wr.export_evidence(self.root, report['digest'])
        self.assertFalse((self.root/'save-backups').exists())
        report = wr.review(self.root)
        with wr.import_lock(self.root), self.assertRaisesRegex(ValueError, '遗留导入锁'):
            wr.export_evidence(self.root, report['digest'])
        before = wr.evidence_inventory(self.root)
        target = Path(wr.export_evidence(self.root, report['digest'])['path'])
        manifest = json.loads(wr.stable(target/'manifest.json'))
        self.assertEqual(manifest['kind'], 'evidence-only')
        self.assertEqual(manifest['plan_sha256'], report['digest'])
        self.assertEqual({p.name for p in target.iterdir()}, {'manifest.json', *before})
        for label, (_, files) in before.items():
            self.assertEqual(wr.content_inventory(wr.snapshot(target/label)), wr.content_inventory(files))
        self.assertEqual(wr.evidence_inventory(self.root), before)
        with self.assertRaisesRegex(ValueError, '世界恢复复核'):
            wr.begin(self.root)

    def test_review_interrupted_copy_has_no_published_manifest_and_can_retry(self):
        self.request(wr.QUIT)
        report = wr.review(self.root)
        source = wr.evidence_inventory(self.root)
        copy = wr.copy_verified

        def interrupt(origin, destination, expected):
            if destination.name == 'own-world':
                raise OSError('simulated interruption')
            return copy(origin, destination, expected)

        with patch.object(wr, 'copy_verified', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'simulated interruption'):
                wr.export_evidence(self.root, report['digest'])
        base = self.root/'save-backups/world-recovery-evidence'
        partial, = base.iterdir()
        self.assertEqual(partial.suffix, '.partial')
        self.assertFalse((partial/'manifest.json').exists())
        target = Path(wr.export_evidence(self.root, report['digest'])['path'])
        self.assertTrue((target/'manifest.json').exists())
        self.assertTrue(partial.exists())
        self.assertEqual(wr.evidence_inventory(self.root), source)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ReviewTests(name) for name in ReviewTests.__dict__ if name.startswith('test_review_'))
