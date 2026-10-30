import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'launcher'))
import trophy_view


class TrophyViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'data/user/trophies.log'
        (self.root / 'trophy-catalog.json').write_bytes((ROOT / 'launcher/trophy-catalog.json').read_bytes())

    def write(self, value):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_bytes(value)

    def test_missing_is_read_only(self):
        view = trophy_view.snapshot(self.root)
        self.assertTrue(view['missing'])
        self.assertFalse(self.log.parent.exists())
        self.assertEqual(len(view['trophies']), 40)
        self.assertEqual(sum(t['group'] == 'base' for t in view['trophies']), 34)
        self.assertEqual([t['id'] for t in view['trophies']], list(range(40)))

    def test_dedup_invalid_and_no_writes(self):
        data = b'14 200\n14 100\n36 300\n41 400\n14 nope\n'
        self.write(data)
        view = trophy_view.snapshot(self.root)
        self.assertEqual(view['trophies'][14]['unlocked'], 100)
        self.assertEqual(view['trophies'][36]['unlocked'], 300)
        self.assertEqual(view['ignored'], 2)
        self.assertEqual(view['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.log.read_bytes(), data)

    def test_platinum_is_not_invented_or_written(self):
        data = ''.join(f'{i} 100\n' for i in range(1, 34)).encode()
        self.write(data)
        view = trophy_view.snapshot(self.root)
        self.assertTrue(view['platinum_due'])
        self.assertIsNone(view['trophies'][0]['unlocked'])
        self.assertEqual(self.log.read_bytes(), data)
        self.write(data + b'0 200\n')
        self.assertFalse(trophy_view.snapshot(self.root)['platinum_due'])

    def test_partial_append_retries_then_fails(self):
        self.write(b'14 17')
        with patch.object(trophy_view.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, '末行'):
                trophy_view.snapshot(self.root)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(self.log.read_bytes(), b'14 17')

    def test_environment_and_older_branch_do_not_merge(self):
        self.write(b'14 100\n36 200\n')
        trophy_view.snapshot(self.root)
        self.write(b'14 100\n')
        self.assertIsNone(trophy_view.snapshot(self.root)['trophies'][36]['unlocked'])
        other = self.root / 'other'
        other.mkdir()
        (other / 'trophy-catalog.json').write_bytes((self.root / 'trophy-catalog.json').read_bytes())
        self.assertTrue(trophy_view.snapshot(other)['missing'])


if __name__ == '__main__':
    unittest.main()
