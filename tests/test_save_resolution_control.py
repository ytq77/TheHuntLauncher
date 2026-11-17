"""Control/guard regressions. Only caller-owned synthetic temporary saves."""
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import save_resolution_fixture as f
import client

s, w, t = f.resolution, f.world, f.resolution.transfer


class ControlAndGuardTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='hunt-control-guard-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.saves = f.create(self.root, marker=False)
        self.lock = self.root/'.save-import.lock'

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()
                and p.name not in ('.save-transfer.guard', 'launcher-state.json')}

    def test_control_different_stat_and_fstat_ctime_is_not_change(self):
        self.lock.write_bytes(b'{"operation":"recovery-choice"}')
        original = os.fstat
        calls = []
        def fstat(fd):
            value = original(fd)
            calls.append(fd)
            # Model CreationTime vs ChangeTime while retaining real file ID,
            # size and mtime. Each individual API remains stable.
            return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino,
                st_size=value.st_size, st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns + 1000000)
        with patch.object(s.os, 'fstat', fstat):
            info, raw = s._control(self.lock)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(info['identity']), 4)
        self.assertEqual(info['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(info['bytes'], len(raw))

    def test_rewritten_same_size_restored_mtime_invalidates_content_identity(self):
        self.lock.write_bytes(b'{"operation":"restore","pid":1}')
        first, raw = s._control(self.lock)
        times = self.lock.stat()
        self.lock.write_bytes(raw.replace(b':1', b':2'))
        os.utime(self.lock, ns=(times.st_atime_ns, times.st_mtime_ns))
        second, _ = s._control(self.lock)
        self.assertEqual(first['identity'], second['identity'])
        self.assertNotEqual(first['sha256'], second['sha256'])

    def test_control_actual_growth_truncation_and_preopen_replacement_reached(self):
        original_open = Path.open
        for mode in ('grow', 'truncate', 'replace'):
            with self.subTest(mode=mode):
                self.lock.write_bytes(b'x'*32)
                hits = []
                before_id = self.lock.stat().st_ino
                class Changing:
                    def __init__(self, stream): self.stream = stream
                    def __enter__(self): self.stream.__enter__(); return self
                    def __exit__(self, *args): return self.stream.__exit__(*args)
                    def fileno(self): return self.stream.fileno()
                    def read(inner, amount):
                        raw = inner.stream.read(amount)
                        with original_open(self.lock, 'ab' if mode == 'grow' else 'wb') as out:
                            out.write(b'y'*40 if mode == 'grow' else b'y')
                        hits.append(('after-read', self.lock.stat().st_size))
                        return raw
                def opened(path, *args, **kwargs):
                    target = path == self.lock and args and args[0] == 'rb'
                    if target and mode == 'replace':
                        new = self.lock.with_name('replacement')
                        new.write_bytes(b'z'*32)
                        os.replace(new, self.lock)
                        hits.append(('before-open', self.lock.stat().st_ino))
                    stream = original_open(path, *args, **kwargs)
                    return Changing(stream) if target and mode != 'replace' else stream
                with patch.object(Path, 'open', opened), self.assertRaises(w.EvidenceChangedError):
                    s._control(self.lock)
                self.assertEqual(len(hits), 1)
                if mode == 'replace': self.assertNotEqual(hits[0][1], before_id)
                else: self.assertEqual(hits[0][1], 72 if mode == 'grow' else 1)

    def test_guard_propagates_read_error_change_and_observed_disappearance(self):
        self.lock.write_bytes(b'{"operation":"recovery-choice"}')
        for error in (PermissionError(errno.EACCES, 'control denied', str(self.lock)),
                      w.EvidenceChangedError('changed control'),
                      FileNotFoundError(errno.ENOENT, 'vanished after exists', str(self.lock))):
            with self.subTest(error=type(error).__name__):
                before = self.inventory()
                with patch.object(s, '_control', side_effect=error) as read:
                    with self.assertRaises(type(error) if not isinstance(error, FileNotFoundError)
                                           else w.EvidenceChangedError):
                        s.require_launch_clear(self.root)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(self.inventory(), before)

    def test_known_ordinary_delegates_but_unknown_content_blocks(self):
        s.require_launch_clear(self.root)
        for raw in (b'1234', b'{"operation":"restore"}', b'{"operation":"import"}'):
            with self.subTest(raw=raw):
                self.lock.write_bytes(raw)
                s.require_launch_clear(self.root)
                with self.assertRaises(ValueError): s.require_launch_clear(self.root, strict=True)
        for raw in (b'{broken', b'{}', b'null', b'-1', b'{"operation":"future"}'):
            with self.subTest(raw=raw):
                self.lock.write_bytes(raw)
                with self.assertRaises(ValueError): s.require_launch_clear(self.root)

    def test_strict_restore_message_reuses_stable_value_without_second_read(self):
        value = {'operation': 'restore', 'destination': 'synthetic-current',
                 'backup': 'synthetic-pre-restore', 'selected': 'synthetic-selected'}
        self.lock.write_text(json.dumps(value), encoding='utf-8')
        with patch.object(s, '_control', wraps=s._control) as read, \
                patch.object(s.backups, '_json', side_effect=AssertionError('lock reread')):
            with self.assertRaises(ValueError) as caught:
                s.require_launch_clear(self.root, strict=True)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(str(caught.exception), s.backups.restore_lock_message(value))
        for field in ('destination', 'backup', 'selected'):
            self.assertIn(value[field], str(caught.exception))

    def test_strict_import_and_pid_reuse_original_import_message(self):
        for raw in (b'1234', b'{"operation":"import"}'):
            with self.subTest(raw=raw):
                self.lock.write_bytes(raw)
                with self.assertRaises(ValueError) as caught:
                    s.require_launch_clear(self.root, strict=True)
                self.assertEqual(str(caught.exception), s.backups.IMPORT_LOCK_MESSAGE)

    def test_direct_writers_and_client_block_before_side_effects_on_read_error(self):
        self.lock.write_bytes(b'{"operation":"recovery-choice"}')
        before = self.inventory()
        failure = PermissionError(errno.EACCES, 'control denied', str(self.lock))
        for name, action in (
            ('begin', lambda: w.begin(self.root)),
            ('finalize', lambda: w.finalize(self.root, None)),
            ('client-session', lambda: client.run_session({}, '', '', None)),
            ('client-main', client.main)):
            with self.subTest(entry=name), patch.object(client, 'ROOT', self.root), \
                    patch.object(s, '_control', side_effect=failure), \
                    patch.object(client, 'prepare', side_effect=AssertionError('prepare reached')) as prepare, \
                    patch.object(w, '_begin', side_effect=AssertionError('begin reached')) as begin, \
                    patch.object(w, '_finalize', side_effect=AssertionError('finalize reached')) as finalize:
                with self.assertRaises(PermissionError): action()
                self.assertEqual((prepare.call_count, begin.call_count, finalize.call_count), (0, 0, 0))
            self.assertEqual(self.inventory(), before)

    def test_ordinary_lock_blocks_direct_world_and_client_preparation(self):
        self.lock.write_bytes(b'1234')
        before = self.inventory()
        for action in (lambda: w.begin(self.root), lambda: w.finalize(self.root, None),
                       lambda: client.run_session({}, '', '', None), client.main):
            with patch.object(client, 'ROOT', self.root), \
                    patch.object(client, 'prepare', side_effect=AssertionError('prepare reached')) as prepare, \
                    patch.object(w, '_begin', side_effect=AssertionError('begin reached')) as begin, \
                    patch.object(w, '_finalize', side_effect=AssertionError('finalize reached')) as finalize:
                with self.assertRaises(ValueError): action()
                self.assertEqual((prepare.call_count, begin.call_count, finalize.call_count), (0, 0, 0))
            self.assertEqual(self.inventory(), before)

    def test_restored_case_use_current_runs_real_world_entry(self):
        gate = self.root/s.GATE
        gate.parent.mkdir(parents=True, exist_ok=True)
        gate.write_bytes(b'opaque restored-version gate')
        original = s._content(s._capture(self.root))['trees']['savedata']
        f.choose(self.root, 'use-current')
        row, = s.list_cases(self.root)['backups']
        f.choose(self.root, 'restore-case', row)
        self.assertTrue((self.root/s.GATE).exists())
        self.assertEqual(s.plan(self.root)['options'][0]['id'], 'use-current')
        f.choose(self.root, 'use-current')
        self.assertFalse((self.root/s.GATE).exists())
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN', w.begin(self.root))
        self.assertEqual(s._content(s._capture(self.root))['trees']['savedata'], original)

    def test_launchable_bad_backup_has_no_anomaly_or_recommended_rollback(self):
        (self.saves/'backup0000').write_bytes(b'unreadable backup, primary intact')
        self.assertFalse(s._readability(self.root/s.TREES['savedata'])['valid'])
        before = s._content(s._capture(self.root))['trees']['savedata']
        plan = s.plan(self.root)
        self.assertTrue(plan['startable'])
        self.assertFalse(plan['needed'])
        self.assertFalse(plan['show_choices'])
        self.assertEqual(plan['options'], [])
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN', w.begin(self.root))
        self.assertEqual(s._content(s._capture(self.root))['trees']['savedata'], before)

    def test_changed_scene_after_lock_abandon_then_use_current_has_exit(self):
        gate = self.root/s.GATE
        gate.parent.mkdir(parents=True, exist_ok=True)
        gate.write_bytes(b'opaque gate')
        f.make_pending(self.root, action='use-current')
        (self.saves/'later-progress.note').write_bytes(b'new local progress must survive')
        before = s._content(s._capture(self.root))['trees']['savedata']
        pending = s.pending(self.root)
        self.assertFalse(pending['can_resume'])
        self.assertTrue(pending['can_abandon'])
        s.abandon(self.root, pending['abandon_digest'], confirmed=True)
        self.assertFalse(self.lock.exists())
        self.assertTrue(gate.exists())
        self.assertEqual(s.plan(self.root)['options'][0]['id'], 'use-current')
        f.choose(self.root, 'use-current')
        self.assertFalse(gate.exists())
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN', w.begin(self.root))
        self.assertEqual(s._content(s._capture(self.root))['trees']['savedata'], before)


if __name__ == '__main__':
    unittest.main()
