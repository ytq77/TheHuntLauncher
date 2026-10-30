"""Publish this run's new recorded trophies to the desktop; never write trophies.log."""
import json
from pathlib import Path
import re
import time
import uuid

from trophy_sync import read


class TrophyNotices:
    def __init__(self, root, session):
        self.path = Path(root) / 'data/user/trophies.log'
        self.output = self.path.with_name('ui-trophy-events.json')
        self.session = session if re.fullmatch(r'[0-9a-f]{32}', session or '') else uuid.uuid4().hex
        self.events = []
        self.seen = None
        self.signature = None
        self.last_poll = 0.0
        # Called after first-time import, so imported history is already the baseline.
        try:
            state, _ = self._read()
            self.seen = set(state['records'])
        except (OSError, ValueError):
            pass

    def _read(self):
        state, raw = read(self.path)
        if raw and not raw.endswith((b'\n', b'\r')):
            raise ValueError('Trophy append incomplete')
        return state, raw

    def poll(self):
        now = time.monotonic()
        if now - self.last_poll < 1.0:
            return False
        self.last_poll = now
        try:
            stat = self.path.stat() if self.path.exists() else None
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size) if stat else None
            if signature == self.signature:
                return False
            state, _ = self._read()
            if self.seen is None:
                self.seen = set(state['records'])
            added = sorted(set(state['records']) - self.seen, key=lambda n: (state['records'][n], int(n)))
            if added:
                events = self.events + [dict(id=int(n), when=state['records'][n], seq=len(self.events) + i + 1)
                                        for i, n in enumerate(added)]
                temp = self.output.with_name(self.output.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    temp.write_text(json.dumps(dict(session=self.session, events=events)), 'utf-8')
                    temp.replace(self.output)
                finally:
                    temp.unlink(missing_ok=True)
                self.events = events
            self.seen.update(state['records'])
            self.signature = signature
            return bool(added)
        except (OSError, ValueError):
            # Notification failure cannot abort gameplay, saves or world recovery.
            return False
