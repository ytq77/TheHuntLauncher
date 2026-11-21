"""Read-only recovery information and confirmed choice effects, using temporary installations."""
import json
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import save_resolution_fixture as f
import backup_characters
import client
import recovery_details as details
import save_effects

world, resolution, transfer = f.world, f.resolution, f.resolution.transfer


class RecoveryDetailsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.running = self.enterContext(patch.object(transfer, 'game_running', return_value=False))

    def inspect(self):
        before = transfer.snapshot(self.root)
        result = details.recovery_details(self.root)
        self.assertEqual(transfer.snapshot(self.root), before)
        return result

    def test_empty_installation_no_backup_scan_or_transaction(self):
        with patch.object(resolution, '_plan', side_effect=AssertionError('backup scan')):
            result = self.inspect()
        self.assertEqual((result['status'], result['reasons'], result['current_action']), ('clear', [], None))
        self.assertEqual(len(result['slots']), 10)
        self.assertTrue(all(s['primary']['state']=='missing' and s['bytes_equal'] is None for s in result['slots']))

    def test_zero_unknown_empty_missing_unreadable_and_identity(self):
        folder = f.create(self.root, marker=False)
        (folder/'userdata0002').write_bytes(bytes(0x140000))
        (folder/'backup0002').write_bytes(f.RAW)
        (folder/'userdata0003').write_bytes(b'invalid')
        (folder/'backup0004').write_bytes(f.RAW)
        (folder/'backup0001').write_bytes(f.RAW)
        reader = SimpleNamespace(read_with_name=lambda raw,name: SimpleNamespace(level=10,echoes=0))
        with patch.dict(sys.modules, {'engine.launcher':SimpleNamespace(bbport_character=reader)}):
            rows = self.inspect()['slots']
            self.assertEqual(rows[0]['primary']['echoes'], 0)
            self.assertTrue(rows[0]['bytes_equal'] and rows[0]['summary_equal'])
            self.assertEqual(rows[1]['identity_relation'], 'different')
            self.assertIsNone(rows[1]['summary_equal'])
            self.assertEqual(rows[2]['primary']['state'], 'empty')
            self.assertEqual(rows[3]['primary']['state'], 'unreadable')
            self.assertEqual(rows[4]['primary']['state'], 'missing')
            (folder/'backup0000').write_bytes(f.GOOD)
            row = self.inspect()['slots'][0]
            self.assertTrue(row['summary_equal'])
            self.assertFalse(row['bytes_equal'])
        with patch.dict(sys.modules, {'engine.launcher':SimpleNamespace(bbport_character=SimpleNamespace(read=lambda raw:None))}):
            row = self.inspect()['slots'][0]
            self.assertIsNone(row['primary']['echoes'])
            self.assertEqual(row['primary']['issue'], 'stats_unavailable')
            self.assertIsNone(row['summary_equal'])

    def test_all_reasons_and_known_pending_transactions(self):
        folder = f.create(self.root)
        (folder/'userdata0003').write_bytes(b'invalid')
        (self.root/resolution.GATE).write_bytes(b'gate')
        (self.root/'.save-conversion-pending.json').write_bytes(b'pending')
        (self.root/'.save-conversion.lock').write_bytes(b'lock')
        lock = self.root/'.save-import.lock'
        for kind,payload in (
            ('choice-pending',{'operation':resolution.OPERATION}), ('restore-pending',{'operation':'restore'}),
            ('import-pending',{'operation':'import'}),
            ('import-pending',dict.fromkeys(('destination','stage','backup','original','expected'),
                                              'ignored') | {'schema':1,'state':'prepared'}),
            ('save-operation-pending',1234)):
            lock.write_text(json.dumps(payload))
            with patch.object(resolution, '_plan', side_effect=AssertionError('must not recover')):
                result = self.inspect()
            self.assertEqual({r['code'] for r in result['reasons']},
                             {kind,'conversion-pending','choice-review','primary-check-failed','world-recovery'})
            self.assertEqual([r['slot'] for r in result['reasons'] if r['code']=='primary-check-failed'], [3])
            self.assertEqual(result['status'], 'blocked')
            self.assertIsNone(result['current_action'])

    def test_interrupted_choice_is_observed_without_progressing(self):
        f.create(self.root)
        raw = f.make_pending(self.root)
        with patch.object(resolution, '_run', side_effect=AssertionError('advanced transaction')):
            result = self.inspect()
        self.assertIn('choice-pending', [r['code'] for r in result['reasons']])
        self.assertIsNone(result['current_action'])
        self.assertEqual((self.root/'.save-import.lock').read_bytes(),raw)
        before = transfer.snapshot(self.root)
        pending = resolution.plan(self.root)['pending']
        self.assertEqual(len(pending['resume_effects']['roles']), 10)
        self.assertEqual(pending['resume_effects']['world_after'], 'all-retired-unverified')
        self.assertEqual(pending['abandon_effects']['world_after'], 'needs-review')
        self.assertEqual(transfer.snapshot(self.root), before)

    def test_interrupted_whole_restore_projects_from_staged_target(self):
        folder = f.create(self.root, marker=False)
        resolution.backups.launch_backup(self.root)
        marker = world.layout(self.root)[0]/'review-required.json'
        marker.parent.mkdir(exist_ok=True)
        marker.write_bytes(b'{}')
        (folder/'userdata0000').write_bytes(f.GOOD)
        f.make_pending(self.root, 'restore-all', 'after:0:retire:savedata:')
        before = transfer.snapshot(self.root)
        pending = resolution.plan(self.root)['pending']
        row = pending['resume_effects']['roles'][0]
        self.assertTrue(pending['can_resume'])
        self.assertEqual(row['before']['primary']['state'], 'missing')
        self.assertEqual(row['after']['primary']['state'], 'readable')
        self.assertEqual(row['primary']['state'], 'absent')
        self.assertEqual(transfer.snapshot(self.root),before)

    def test_existing_plan_is_the_only_source_of_current_action(self):
        f.create(self.root, marker=False)
        gate = self.root/resolution.GATE
        gate.write_bytes(b'gate')
        result = self.inspect()
        action = result['current_action']
        plan = resolution.plan(self.root)
        self.assertEqual((action['id'], action['digest'], action['selection']),
                         ('use-current',plan['digest'],plan['selection']))
        self.assertIsNone(action['slot'])
        with self.assertRaises(ValueError):
            resolution.apply(self.root, action['digest'],action['id'], confirmed=False)
        gate.unlink()
        marker = world.layout(self.root)[0]/'review-required.json'
        marker.parent.mkdir(exist_ok=True)
        marker.write_bytes(b'{}')
        self.assertIsNone(self.inspect()['current_action'])  # accept-all is a different product choice.

    def test_world_verification_separate_and_multiple_slots(self):
        folder = f.create(self.root,marker=False)
        token = world.begin(self.root)['THEHUNT_WORLD_RECOVERY_TOKEN']
        f.request(self.root,slot=0,token=token)
        f.request(self.root,slot=1,token=token)
        (folder/'userdata0000').write_bytes(f.GOOD)
        result = self.inspect()
        self.assertEqual({(r['slot'],r['state']) for r in result['reasons']},
                         {(0,'waiting-backup'),(1,'review-required')})
        self.assertEqual(result['current_action']['slot'],1)
        self.assertTrue(result['current_action']['requires_confirmation'])
        other = bytearray((folder/'userdata0001').read_bytes())
        other[world.offline_save.parse(other).face+4] ^= 4
        (folder/'userdata0001').write_bytes(other)
        message = details.exit_disk_progress(self.root,token)
        self.assertIn('槽位 1',message)
        self.assertNotIn('槽位 2',message)
        self.assertEqual(details.exit_disk_progress(self.root,'f'*32),'')

    def test_read_failure_and_simultaneous_generation_change(self):
        folder = f.create(self.root, marker=False)
        old = world.stable
        def fail(path):
            if Path(path).name=='backup0000':
                raise PermissionError('read denied')
            return old(path)
        with patch.object(world,'stable',fail), self.assertRaises(PermissionError):
            self.inspect()
        base = details._slots
        count = 0
        def capture(root,cache):
            nonlocal count
            result = base(root,cache)
            if count == 0:
                (folder/'backup0000').write_bytes(f.GOOD)
            count += 1
            return result
        with patch.object(details,'_slots',capture), self.assertRaises(world.EvidenceChangedError):
            details.recovery_details(self.root)
        self.running.return_value = True
        with self.assertRaises(transfer.OperationUnavailable):
            details.recovery_details(self.root)

    def test_effects_compare_only_same_identity_measured_fields(self):
        def generation(identity,amount):
            return {'state':'readable','name':'猎人','level':10,'echoes':amount,'issue':None,
                    '_identity':identity,'_sha':None}
        old = generation('a',1989)
        self.assertEqual(save_effects.change(old,generation('a',1358))['echoes_delta'],-631)
        self.assertEqual(save_effects.change(old,generation('a',0))['echoes_delta'],-1989)
        self.assertEqual(save_effects.change(old,generation('b',0))['state'],'different-character')
        unknown = save_effects.change(old,generation('a',None))
        self.assertEqual((unknown['state'],unknown['echoes_delta']),('unknown',None))
        self.assertEqual(save_effects.change(old,backup_characters.generation(None,{}))['state'],'absent')
        self.assertEqual(save_effects.change(old,{**old,'state':'unreadable'})['state'],'unreadable')

    def test_replaced_generation_and_whole_branch_impacts(self):
        folder = f.create(self.root,marker=False)
        # Unsupported flag length with identity intact: eligible for its own backup only.
        bad = bytearray(f.RAW)
        struct.pack_into('<I',bad,56,1)
        (folder/'userdata0000').write_bytes(bad)
        plan = resolution.plan(self.root)
        options = {o['id']:o['effects'] for o in plan['options']}
        self.assertTrue(all(len(e['roles'])==10 for e in options.values()))
        target = options['restore-generation-0']
        self.assertEqual(target['world_after'],'needs-review')
        self.assertEqual(target['roles'][0]['after']['primary'],target['roles'][0]['before']['backup'])
        self.assertEqual(target['roles'][0]['primary']['state'],'unreadable')
        self.assertEqual(target['roles'][1]['before'],target['roles'][1]['after'])
        self.assertEqual(options['restart']['roles'][1]['after']['primary']['state'],'missing')
        self.assertTrue(options['restart']['reversible']['available'])
        self.assertFalse(options['choose-ordinary']['reversible']['available'])
        self.assertEqual(options['preserve']['world_after'],'unchanged')
        (folder/'backup0000').write_bytes(f.GOOD)
        with self.assertRaisesRegex(ValueError,'变化'):
            resolution.apply(self.root,plan['digest'],'restore-generation-0',confirmed=True)

    def test_finish_exit_orders_bound_observation_without_changing_automatic_routes(self):
        user = self.root/'data/user'
        user.mkdir(parents=True)
        with (patch.object(client,'ROOT',self.root), patch.object(client,'state') as state,
              patch.object(world,'process_exited'),patch.object(world,'read_quit_seal',return_value=0),
              patch.object(details,'exit_disk_progress',return_value='槽位 1 当前主档读到猎人，0 回响。')):
            for outcomes,code,expected,result in ((['waiting-backup-save'],0,'等待游戏',0),
                    (['verified'],0,'已完成核对',0),([],23,'打开日志',23)):
                with patch.object(world,'finalize',return_value=outcomes):
                    self.assertEqual(client.finish_game(user,'token',None,code,'s','l'),result)
                msg = state.call_args.args[1]
                self.assertLess(msg.index('退出'),msg.index('主档读到'))
                self.assertLess(msg.index('主档读到'),msg.index(expected))
            with patch.object(world,'finalize',side_effect=ValueError('fixture')),patch.object(client,'report_failure'):
                self.assertEqual(client.finish_game(user,'token',None,0,'s','l'),1)
            self.assertIn('个人世界仍待核验',state.call_args.args[1])
            self.assertNotIn('保存成功',state.call_args.args[1])
