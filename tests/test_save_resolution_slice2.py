"""Synthetic installations only; production stop/mutex and transaction paths."""
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import save_resolution_fixture as f
s = f.resolution


class Slice2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a-slice2-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'install'
        self.saves = f.create(self.root)
        self.env = patch.dict(os.environ, {'APPDATA': str(self.base/'appdata')})
        self.env.start(); self.addCleanup(self.env.stop)

    def bad(self):
        (self.saves/'userdata0000').write_bytes(b'nonzero unsupported role')

    def choose(self, action, **kwargs):
        plan = s.plan(self.root, **kwargs)
        choice=next(x for x in plan['options'] if x['id']==action)
        return s.apply(self.root, choice.get('digest',plan['digest']), action, confirmed=True, **kwargs)

    def original(self):
        # Import source selection now reads the installation's real JSON settings.
        (self.root/'profile.json').write_text('{}', encoding='utf-8')
        ordinary = self.base/'ordinary'
        ordinary.mkdir(); (ordinary/'Bloodborne.exe').write_bytes(b'not executed')
        shutil.copytree(self.root/'data/user/savedata', ordinary/'user/savedata')
        return ordinary

    def test_single_slot_only_and_reversible(self):
        self.bad()
        before = s._content(s._capture(self.root))
        other = self.saves/'userdata0001'; other_stat = other.stat()
        backup = (self.saves/'backup0000').read_bytes()
        plan = s.plan(self.root)
        self.assertEqual(plan['options'][0]['id'], 'restore-generation-0')
        self.assertIn('无法核对', plan['options'][0]['description'])
        result = self.choose('restore-generation-0')
        self.assertEqual((self.saves/'userdata0000').read_bytes(), backup)
        self.assertEqual((self.saves/'backup0000').read_bytes(), backup)
        self.assertEqual((other.stat().st_ino, other.stat().st_mtime_ns), (other_stat.st_ino, other_stat.st_mtime_ns))
        self.assertEqual(s._original_content(s._case_path(self.root,result['case_id'])), before)
        self.assertTrue(result['needs_review'])
        row = next(x for x in s.list_cases(self.root)['backups'] if x['id'] == result['case_id'])
        selection = {'selection_'+k: row[k] for k in ('kind','id','fingerprint')}
        self.choose('restore-case', **selection)
        self.assertEqual(s._content(s._capture(self.root))['trees'], before['trees'])
        self.assertTrue(s.plan(self.root)['needed'])

    def test_generation_resume_after_retirement(self):
        self.bad()
        before = s._content(s._capture(self.root))
        class Interrupted(Exception): pass
        def stop(name):
            if name.startswith('after:0:retire:savedata:'): raise Interrupted()
        with patch.object(s, '_checkpoint', stop), self.assertRaises(Interrupted):
            self.choose('restore-generation-0')
        pending = s.pending(self.root)
        self.assertTrue(pending['can_resume'])
        result = s.resume(self.root,pending['fingerprint'],confirmed=True)
        self.assertEqual(s._original_content(s._case_path(self.root,result['case_id'])),before)

    def test_reimport_original_unchanged(self):
        ordinary = self.original()
        original_snapshot = s.transfer.snapshot(ordinary/'user/savedata')
        self.bad()
        before = s._content(s._capture(self.root))
        result = self.choose('reimport-ordinary', ordinary_install=str(ordinary))
        self.assertEqual(s.transfer.snapshot(ordinary/'user/savedata'),original_snapshot)
        self.assertEqual(s.backups._inventory(s.transfer.snapshot(self.root/'data/user/savedata')),s.backups._inventory(original_snapshot))
        self.assertEqual(s._original_content(s._case_path(self.root,result['case_id'])),before)
        self.assertFalse(result['can_launch'])
        self.assertFalse(s.plan(self.root)['needed'])

    def test_reimport_source_changed_after_confirmation(self):
        ordinary = self.original(); self.bad()
        kwargs={'ordinary_install':str(ordinary)}
        plan=s.plan(self.root,**kwargs)
        (ordinary/'user/savedata/extra').write_bytes(b'new')
        with self.assertRaises(ValueError): s.apply(self.root,next(x['digest'] for x in plan['options'] if x['id']=='reimport-ordinary'),'reimport-ordinary',confirmed=True,**kwargs)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_unknown_restart_is_never_recommended_or_called_damage(self):
        self.bad(); (self.saves/'backup0000').write_bytes(b'unsupported backup')
        plan=s.plan(self.root)
        self.assertEqual(plan['options'][-1]['id'],'restart')
        self.assertFalse(plan['options'][-1]['recommended'])
        self.assertTrue(plan['restart_eligibility']['eligible'])
        self.assertEqual(plan['options'][0]['id'],'choose-ordinary')

    def test_strict_backup_failure_does_not_trigger(self):
        (self.root/'data/user/world-recovery/review-required.json').unlink()
        (self.saves/'backup0000').write_bytes(b'unsupported backup')
        plan=s.plan(self.root)
        self.assertFalse(plan['needed']); self.assertEqual(plan['options'],[])

    def test_choice_selector_is_not_transaction(self):
        self.bad()
        before=s._capture(self.root)
        with self.assertRaises(ValueError): self.choose('choose-ordinary')
        self.assertEqual(s._capture(self.root),before)

    def test_generation_source_changed_rejected(self):
        self.bad();plan=s.plan(self.root)
        (self.saves/'backup0000').write_bytes(b'changed')
        with self.assertRaises(ValueError): s.apply(self.root,plan['digest'],'restore-generation-0',confirmed=True)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_io_error_not_restart(self):
        self.bad(); real=s.world.stable
        def failed(path):
            if Path(path).name=='backup0000': raise PermissionError('synthetic sharing violation')
            return real(path)
        with patch.object(s.world,'stable',failed),self.assertRaises(PermissionError): s.plan(self.root)

    def test_reimport_resume_does_not_need_original(self):
        ordinary=self.original();self.bad()
        class Interrupted(Exception): pass
        def stop(name):
            if name=='after:lock': raise Interrupted()
        with patch.object(s,'_checkpoint',stop),self.assertRaises(Interrupted):
            self.choose('reimport-ordinary',ordinary_install=str(ordinary))
        ordinary.rename(self.base/'original-now-offline')
        pending=s.pending(self.root)
        self.assertTrue(pending['can_resume'])
        self.assertEqual(s.resume(self.root,pending['fingerprint'],confirmed=True)['result'],'reimported-ordinary')

    def test_settings_change_rejects_confirmed_source(self):
        ordinary=self.original();self.bad()
        kwargs={'ordinary_install':str(ordinary)}
        plan=s.plan(self.root,**kwargs)
        settings=self.base/'appdata/bbport-launcher/settings.json'
        settings.parent.mkdir(parents=True);settings.write_text('{}')
        with self.assertRaises(ValueError): s.apply(self.root,next(x['digest'] for x in plan['options'] if x['id']=='reimport-ordinary'),'reimport-ordinary',confirmed=True,**kwargs)

    def test_primary_identity_different_blocks_generation(self):
        # primary remains parseable but backup belongs to another synthetic role.
        (self.saves/'backup0000').write_bytes((self.saves/'backup0001').read_bytes())
        plan=s.plan(self.root)
        self.assertNotIn('restore-generation-0',[x['id'] for x in plan['options']])

    def test_readable_identity_survives_unsupported_flag_layout(self):
        raw=bytearray((self.saves/'userdata0000').read_bytes())
        struct.pack_into('<I',raw,56,1)
        s.offline_save.parse(raw)
        (self.saves/'userdata0000').write_bytes(raw)
        plan=s.plan(self.root)
        choice=next(x for x in plan['options'] if x['id']=='restore-generation-0')
        self.assertIn('主备身份一致',choice['description'])
        (self.saves/'backup0000').write_bytes((self.saves/'backup0001').read_bytes())
        self.assertNotIn('restore-generation-0',[x['id'] for x in s.plan(self.root)['options']])

    def test_structured_rejection_not_permanent_damage(self):
        raw=bytearray((self.saves/'userdata0000').read_bytes())
        for kind,offset,value,code,category in (
                ('header',0,99,'role_header_unsupported','unsupported'),
                ('structure',36,1,'section_relations_invalid','structure_rejected'),
                ('flags',56,1,'flag_layout_unsupported','unsupported')):
            with self.subTest(kind=kind):
                damaged=bytearray(raw);struct.pack_into('<I',damaged,offset,value)
                (self.saves/'userdata0000').write_bytes(damaged)
                plan=s.plan(self.root)
                row=next(x for x in plan['characters'] if x['slot']==0)
                self.assertEqual(row['primary_reason_code'],code)
                self.assertEqual(row['primary_category'],category)
                self.assertEqual(plan['startup_reason_code'],'nonzero_primary_blocked')
                self.assertFalse(plan['restart_eligibility']['recommended'])

    def restart(self):
        plan=s.plan(self.root)
        return s.dispatch(self.root,{'action':'apply','digest':plan['digest'],'option_id':'restart',
                                    'confirmed':True,'restart_confirmed':True})

    def test_restart_requires_second_confirmation_without_side_effects(self):
        self.bad(); before=s._capture(self.root)
        plan=s.plan(self.root)
        for value in (None,False,1,'true'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                s.apply(self.root,plan['digest'],'restart',confirmed=True,restart_confirmed=value)
        self.assertEqual(s._capture(self.root),before)
        self.assertFalse((self.root/'save-backups/recovery-choice').exists())

    def test_restart_partial_and_unknown_have_real_reversible_exit(self):
        for all_unknown in (False,True):
            with self.subTest(all_unknown=all_unknown):
                root=self.base/('unknown' if all_unknown else 'partial')
                saves=f.create(root)
                (saves/'userdata0000').write_bytes(b'unknown primary')
                if all_unknown:
                    for path in saves.glob('*'):
                        path.write_bytes(b'unknown role or profile')
                before=s._content(s._capture(root))
                plan=s.plan(root)
                choice=plan['options'][-1]
                self.assertEqual(choice['id'],'restart');self.assertFalse(choice['recommended'])
                self.assertIn('角色占用范围未完全识别',choice['description'])
                self.assertIn('启动器无法确认',choice['description'])
                result=s.apply(root,plan['digest'],'restart',confirmed=True,restart_confirmed=True)
                self.assertEqual(list((root/'data/user/savedata').iterdir()),[])
                self.assertEqual((root/'data/user/trophies.log').read_bytes(),b'synthetic trophy sentinel')
                self.assertEqual((root/'profile.json').read_bytes(),b'synthetic configuration sentinel')
                self.assertEqual(s._original_content(s._case_path(root,result['case_id'])),before)
                row=next(x for x in s.list_cases(root)['backups'] if x['id']==result['case_id'])
                self.assertEqual(row['entry_kind'],'sealed-restart-original')
                self.assertIn('无法确认',row['label'])
                selection={'selection_'+k:row[k] for k in ('kind','id','fingerprint')}
                restore=s.plan(root,**selection)
                s.apply(root,restore['digest'],'restore-case',confirmed=True,**selection)
                self.assertEqual(s._content(s._capture(root))['trees'],before['trees'])
                self.assertTrue(s.plan(root)['needed'])
                self.assertEqual(s.plan(root)['options'][-1]['id'],'restart')

    def test_restart_prepare_begin_no_implicit_original_import(self):
        import client
        ordinary=self.original()
        ini=ordinary/'bbport.ini';ini.write_text('synthetic existing configuration')
        game=self.base/'game';game.mkdir();(game/'eboot.bin').write_bytes(b'not executed')
        profile={'game':str(game),'graphics_source':str(ini),'install':str(ordinary)}
        original=s.transfer.snapshot(ordinary/'user/savedata')
        self.bad(); self.restart()
        reached=[]
        def external_game_boundary(profile,name,password,bridge,recovery_env,seal):
            reached.append(recovery_env['THEHUNT_WORLD_RECOVERY_TOKEN'])
            self.assertEqual(list((self.root/'data/user/savedata').iterdir()),[])
            return 0
        with patch.object(client,'ROOT',self.root),patch.object(client,'run_game',external_game_boundary),\
                patch.object(client,'source_path',side_effect=AssertionError('implicit ordinary import')):
            self.assertEqual(client.run_session(profile,'synthetic','synthetic',None),0)
        self.assertEqual(len(reached),1)
        self.assertEqual(s.transfer.snapshot(ordinary/'user/savedata'),original)
        self.assertTrue((self.root/'data/user/world-recovery/runs'/str(reached[0]+'.json')).exists())

    def test_restart_failed_preservation_does_not_touch_live(self):
        self.bad();before=s._capture(self.root)
        original_copy=s._durable_copy
        def fail(source,destination,expected):
            if Path(destination).name=='savedata': raise PermissionError('synthetic preserve copy blocked')
            return original_copy(source,destination,expected)
        with patch.object(s,'_durable_copy',fail),self.assertRaises(PermissionError): self.restart()
        self.assertEqual(s._capture(self.root),before)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_reimport_uses_source_bound_digest_not_generic_digest(self):
        ordinary=self.original();self.bad();kwargs={'ordinary_install':str(ordinary)}
        plan=s.plan(self.root,**kwargs)
        bound=next(x['digest'] for x in plan['options'] if x['id']=='reimport-ordinary')
        self.assertNotEqual(bound,plan['digest'])
        before=s._capture(self.root)
        with self.assertRaises(ValueError):
            s.apply(self.root,plan['digest'],'reimport-ordinary',confirmed=True,**kwargs)
        self.assertEqual(s._capture(self.root),before)
        self.assertFalse((self.root/'save-backups/recovery-choice').exists())
        self.assertEqual(s.apply(self.root,bound,'reimport-ordinary',confirmed=True,**kwargs)['result'],'reimported-ordinary')

    def test_optional_source_change_or_io_only_rejects_reimport(self):
        for mode in ('changed','unreadable'):
            for action in ('preserve','restart'):
                with self.subTest(mode=mode,action=action):
                    root=self.base/(mode+'-'+action);saves=f.create(root)
                    (root/'profile.json').write_text('{}', encoding='utf-8')
                    ordinary=self.base/(mode+'-'+action+'-ordinary');ordinary.mkdir()
                    (ordinary/'Bloodborne.exe').write_bytes(b'not executed')
                    source=ordinary/'user/savedata';shutil.copytree(root/'data/user/savedata',source)
                    (saves/'userdata0000').write_bytes(b'unsupported current primary')
                    kwargs={'ordinary_install':str(ordinary)}
                    plan=s.plan(root,**kwargs)
                    bound=next(x['digest'] for x in plan['options'] if x['id']=='reimport-ordinary')
                    before=s._capture(root);real=s.transfer.snapshot
                    if mode=='changed': (source/'new-file').write_bytes(b'changed source')
                    def read(path,*args,**options):
                        if mode=='unreadable' and Path(path)==source:
                            raise PermissionError('synthetic original no longer readable')
                        return real(path,*args,**options)
                    with patch.object(s.transfer,'snapshot',read):
                        fresh=s.plan(root,**kwargs)
                        self.assertEqual(fresh['digest'],plan['digest'])
                        with self.assertRaises(ValueError):
                            s.apply(root,bound,'reimport-ordinary',confirmed=True,**kwargs)
                        self.assertEqual(s._capture(root),before)
                        self.assertFalse((root/'save-backups/recovery-choice').exists())
                        result=s.apply(root,plan['digest'],action,confirmed=True,
                                       restart_confirmed=action=='restart',**kwargs)
                    self.assertEqual(s._original_content(s._case_path(root,result['case_id'])),s._content(before))
                    if action=='preserve': self.assertEqual(s._capture(root),before)
                    else: self.assertEqual(list((root/'data/user/savedata').iterdir()),[])

    def test_optional_source_error_after_preservation_does_not_block_preserve(self):
        ordinary=self.original();self.bad();kwargs={'ordinary_install':str(ordinary)}
        plan=s.plan(self.root,**kwargs);before=s._capture(self.root)
        settings=self.base/'appdata/bbport-launcher/settings.json'
        fired=[]
        def observe(point):
            if point=='after:preserve':
                settings.parent.mkdir(parents=True,exist_ok=True)
                settings.write_text('{invalid json',encoding='utf-8');fired.append(point)
        with patch.object(s,'_checkpoint',observe):
            result=s.apply(self.root,plan['digest'],'preserve',confirmed=True,**kwargs)
        self.assertEqual(fired,['after:preserve'])
        self.assertEqual(s._capture(self.root),before)
        self.assertEqual(s._original_content(s._case_path(self.root,result['case_id'])),s._content(before))

    def test_startable_gate_and_review_keep_only_slice1_choices(self):
        marker=self.root/'data/user/world-recovery/review-required.json'
        gate=self.root/s.GATE
        for kind in ('review','gate','both'):
            with self.subTest(kind=kind):
                marker.unlink(missing_ok=True);gate.unlink(missing_ok=True)
                if kind in ('review','both'): marker.write_bytes(b'opaque review')
                if kind in ('gate','both'): gate.write_bytes(b'opaque gate')
                with patch.object(s,'_generation_sources',side_effect=AssertionError('unneeded generation read')),\
                        patch.object(s,'_ordinary_source',side_effect=AssertionError('unneeded original read')):
                    plan=s.plan(self.root,ordinary_install=object())
                self.assertTrue(plan['startable']);self.assertTrue(plan['needed'])
                self.assertFalse(plan['restart_eligibility']['eligible'])
                self.assertEqual(plan['ordinary_source']['status'],'not_needed')
                ids={x['id'] for x in plan['options']}
                self.assertFalse(ids & {'restart','reimport-ordinary','choose-ordinary'})
                self.assertIn('preserve',ids)
                self.assertIn('use-current' if kind=='gate' else 'accept-all',ids)
        marker.unlink(missing_ok=True);gate.unlink(missing_ok=True)
        plan=s.plan(self.root)
        self.assertFalse(plan['needed']);self.assertEqual(plan['options'],[])
        self.assertFalse(plan['restart_eligibility']['eligible'])

    def test_malformed_optional_original_settings_keep_local_choices(self):
        ordinary=self.original();self.bad()
        settings=self.base/'appdata/bbport-launcher/settings.json'
        settings.parent.mkdir(parents=True);settings.write_text('{invalid json',encoding='utf-8')
        plan=s.plan(self.root,ordinary_install=str(ordinary))
        self.assertFalse(plan['startable'])
        self.assertEqual(plan['ordinary_source']['status'],'error')
        ids={x['id'] for x in plan['options']}
        self.assertTrue({'preserve','restart','choose-ordinary'}<=ids)
        self.assertNotIn('reimport-ordinary',ids)
        choose=next(x for x in plan['options'] if x['id']=='choose-ordinary')
        self.assertIn('重新选择',choose['description'])
        before=s._content(s._capture(self.root))
        result=s.apply(self.root,plan['digest'],'preserve',confirmed=True,ordinary_install=str(ordinary))
        self.assertEqual(s._original_content(s._case_path(self.root,result['case_id'])),before)
        self.assertEqual(s._content(s._capture(self.root)),before)

    def test_optional_original_permission_error_keeps_local_choices(self):
        ordinary=self.original();self.bad();source=ordinary/'user/savedata'
        real=s.transfer.snapshot
        def read(path,*args,**kwargs):
            if Path(path)==source: raise PermissionError('synthetic original directory access denied')
            return real(path,*args,**kwargs)
        before=s._content(s._capture(self.root))
        with patch.object(s.transfer,'snapshot',read):
            plan=s.plan(self.root,ordinary_install=str(ordinary))
            self.assertEqual(plan['ordinary_source']['status'],'temporary_error')
            self.assertIn('access denied',plan['ordinary_source']['reason'])
            ids={x['id'] for x in plan['options']}
            self.assertTrue({'preserve','restart','choose-ordinary'}<=ids)
            self.assertNotIn('reimport-ordinary',ids)
            result=s.apply(self.root,plan['digest'],'preserve',confirmed=True,ordinary_install=str(ordinary))
        self.assertEqual(s._original_content(s._case_path(self.root,result['case_id'])),before)
        self.assertEqual(s._content(s._capture(self.root)),before)

    def test_optional_original_changed_during_read_is_temporary(self):
        self.bad()
        with patch.object(s,'_ordinary_source',side_effect=s.world.EvidenceChangedError('synthetic unstable source')):
            plan=s.plan(self.root,ordinary_install='synthetic original')
        self.assertEqual(plan['ordinary_source']['status'],'temporary_error')
        self.assertIn('unstable source',plan['ordinary_source']['reason'])
        self.assertTrue({'preserve','restart','choose-ordinary'}<={x['id'] for x in plan['options']})

    def test_synthetic_new_progress_survives_switching_back_and_forth(self):
        self.bad(); original=s._content(s._capture(self.root))
        restart=self.restart()
        # Explicit synthetic bytes, not a claim that a real game created them.
        folder=self.root/'data/user/savedata/1/CUSA00207/SPRJ0005'
        folder.mkdir(parents=True)
        (folder/'userdata0010').write_bytes(b'\x01'*84)
        (folder/'userdata0000').write_bytes(f.GOOD)
        (folder/'backup0000').write_bytes(f.GOOD)
        fresh=s._content(s._capture(self.root))
        def switch(case_id):
            row=next(x for x in s.list_cases(self.root)['backups'] if x['id']==case_id)
            selection={'selection_'+k:row[k] for k in ('kind','id','fingerprint')}
            return self.choose('restore-case',**selection)
        swapped=switch(restart['case_id'])
        self.assertEqual(s._content(s._capture(self.root))['trees'],original['trees'])
        self.assertTrue(s.plan(self.root)['needed'])
        switch(swapped['case_id'])
        self.assertEqual(s._content(s._capture(self.root))['trees'],fresh['trees'])
        result=self.choose('use-current')
        self.assertTrue(result['can_launch'])
        self.assertIn('THEHUNT_WORLD_RECOVERY_TOKEN',s.world.begin(self.root))
        self.assertEqual(s._original_content(s._case_path(self.root,restart['case_id'])),original)
        self.assertEqual(s._original_content(s._case_path(self.root,swapped['case_id'])),fresh)
        self.assertEqual((self.root/'profile.json').read_bytes(),b'synthetic configuration sentinel')
        self.assertEqual((self.root/'data/user/trophies.log').read_bytes(),b'synthetic trophy sentinel')


if __name__=='__main__': unittest.main()
