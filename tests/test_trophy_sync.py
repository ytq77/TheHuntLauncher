"""Real recorded unlocks, stable snapshots, backups and failure paths; no game needed."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import trophy_sync as trophies


class TrophySyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.target = self.root / 'source.log', self.root / 'target.log'

    def plan(self):
        return trophies.prepare([self.source], self.target)

    def test_union_earliest_duplicates_and_idempotence_without_awarding_platinum(self):
        self.source.write_bytes(b'14 200\r\n14 180\r\n35 300\r\n')
        self.target.write_bytes(b'14 190\n15 250\n')
        plan = self.plan()
        trophies.backup(plan, self.root / 'backup')
        trophies.install(plan)
        self.assertEqual(self.target.read_bytes(), b'14 180\n15 250\n35 300\n')
        self.assertEqual((self.root / 'backup/target.log').read_bytes(), b'14 190\n15 250\n')
        self.assertEqual(self.source.read_bytes(), b'14 200\r\n14 180\r\n35 300\r\n')
        stamp = self.target.stat().st_mtime_ns
        with patch.object(trophies, 'atomic_write') as write:
            trophies.install(self.plan())
            write.assert_not_called()
        self.assertEqual(stamp, self.target.stat().st_mtime_ns)
        self.assertNotIn('0', trophies.read(self.target)[0]['records'])

    def test_missing_source_does_not_reset_existing_trophies(self):
        self.target.write_bytes(b'21 100\n')
        plan = self.plan()
        trophies.install(plan)
        self.assertEqual(self.target.read_bytes(), b'21 100\n')
        self.assertEqual(plan['summary']['missing_source_logs'], 1)
        self.assertIn('无法补齐', trophies.notice(plan['summary']))

    def test_invalid_input_is_backed_up_and_reported_not_invented(self):
        raw = b'\xef\xbb\xbf21 100\r\n39 400\n40 200\n-1 200\n15 0\n15 bad\nwrong\n\xff\n'
        self.source.write_bytes(raw)
        plan = self.plan()
        trophies.backup(plan, self.root / 'backup')
        trophies.install(plan)
        self.assertEqual(self.target.read_bytes(), b'21 100\n39 400\n')
        self.assertEqual(plan['summary']['ignored_lines'], 6)
        self.assertEqual((self.root / 'backup/source-0.log').read_bytes(), raw)

    def test_no_evidence_keeps_invalid_target_unchanged(self):
        self.target.write_bytes(b'not a trophy file\n')
        plan = self.plan()
        trophies.backup(plan, self.root / 'backup')
        trophies.install(plan)
        self.assertEqual(self.target.read_bytes(), b'not a trophy file\n')
        self.assertIsNone(plan['content'])

    def test_source_or_target_change_is_refused_before_write(self):
        self.source.write_bytes(b'21 100\n')
        for path in (self.source, self.target):
            plan = self.plan()
            path.write_bytes(b'22 200\n')
            before = self.target.read_bytes() if self.target.exists() else None
            with self.assertRaisesRegex(ValueError, '变化'):
                trophies.install(plan)
            self.assertEqual(self.target.read_bytes() if self.target.exists() else None, before)
            self.source.write_bytes(b'21 100\n')

    def test_atomic_replace_failure_keeps_original_and_no_temporary(self):
        self.source.write_bytes(b'22 200\n')
        self.target.write_bytes(b'21 100\n')
        plan = self.plan()
        trophies.backup(plan, self.root / 'backup')
        with patch.object(Path, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                trophies.install(plan)
        self.assertEqual(self.target.read_bytes(), b'21 100\n')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_rollback_restores_exact_original_or_absence(self):
        self.source.write_bytes(b'22 200\n')
        for existing in (b'\xef\xbb\xbf21 100\r\n', None):
            if existing is None:
                self.target.unlink()
            else:
                self.target.write_bytes(existing)
            plan = self.plan()
            folder = self.root / ('backup-' + str(existing is None))
            trophies.backup(plan, folder)
            trophies.install(plan)
            trophies.rollback(plan, folder)
            self.assertEqual(self.target.read_bytes() if self.target.exists() else None, existing)

    def test_rollback_never_overwrites_an_external_edit(self):
        self.source.write_bytes(b'22 200\n')
        plan = self.plan()
        trophies.backup(plan, self.root / 'backup')
        trophies.install(plan)
        self.target.write_bytes(b'23 300\n')
        with self.assertRaisesRegex(OSError, 'changed externally'):
            trophies.rollback(plan, self.root / 'backup')
        self.assertEqual(self.target.read_bytes(), b'23 300\n')


if __name__ == '__main__':
    unittest.main()
