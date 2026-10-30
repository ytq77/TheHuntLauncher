import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from trophy_notices import TrophyNotices


class TrophyNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'data/user/trophies.log'
        self.log.parent.mkdir(parents=True)

    def poll(self, tracker):
        tracker.last_poll = -10
        return tracker.poll()

    def test_history_is_baseline_and_duplicates_do_not_notify(self):
        self.log.write_bytes(b'14 100\n')
        tracker = TrophyNotices(self.root, 'a' * 32)
        self.assertFalse(self.poll(tracker))
        self.log.write_bytes(b'14 100\n14 200\n36 300\n')
        self.assertTrue(self.poll(tracker))
        notice = json.loads(tracker.output.read_text())
        self.assertEqual(notice, dict(session='a' * 32, events=[dict(id=36, when=300, seq=1)]))
        self.assertFalse(self.poll(tracker))
        self.log.write_bytes(b'14 100\n14 200\n36 300\n36 400\n')
        self.assertFalse(self.poll(tracker))
        self.assertEqual(len(tracker.events), 1)

    def test_first_real_unlock_without_prior_log_is_not_lost(self):
        tracker = TrophyNotices(self.root, 'b' * 32)
        self.log.write_bytes(b'7 500\n')
        self.assertTrue(self.poll(tracker))
        self.assertEqual(tracker.events[0]['id'], 7)
        self.assertEqual(self.log.read_bytes(), b'7 500\n')

    def test_partial_write_failure_and_later_retry_are_isolated(self):
        self.log.write_bytes(b'14 100\n')
        tracker = TrophyNotices(self.root, 'c' * 32)
        self.log.write_bytes(b'14 100\n36 30')
        self.assertFalse(self.poll(tracker))
        self.log.write_bytes(b'14 100\n36 300\n')
        with patch.object(Path, 'replace', side_effect=OSError('fixture write failure')):
            self.assertFalse(self.poll(tracker))
        self.assertTrue(self.poll(tracker))
        self.assertEqual(len(tracker.events), 1)
        self.assertFalse(list(self.log.parent.glob('*.tmp')))

    def test_older_branch_does_not_replay_history_and_new_run_is_scoped(self):
        self.log.write_bytes(b'14 100\n')
        tracker = TrophyNotices(self.root, 'd' * 32)
        self.log.write_bytes(b'14 100\n36 300\n')
        self.assertTrue(self.poll(tracker))
        self.log.write_bytes(b'14 100\n')
        self.assertFalse(self.poll(tracker))
        self.log.write_bytes(b'14 100\n36 300\n')
        self.assertFalse(self.poll(tracker))
        new = TrophyNotices(self.root, 'e' * 32)
        self.assertFalse(self.poll(new))
        self.assertEqual(new.events, [])


if __name__ == '__main__':
    unittest.main()
