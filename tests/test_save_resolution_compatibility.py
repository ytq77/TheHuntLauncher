"""C1-C5 regression contracts on synthetic bytes; never a game-shape proof."""
import contextlib
import ctypes
import errno
import hashlib
import io
import sys
import json
import os
from pathlib import Path
import shutil
import tempfile
import traceback
import unittest
from unittest.mock import patch
import save_resolution_fixture as f
import client
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import test_save_resolution_interruptions as oracle

s,w,t=f.resolution,f.world,f.resolution.transfer
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


class ChoiceCorrections(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory(prefix='hunt-c1-c5-')
        self.addCleanup(self.area.cleanup)
        self.root=Path(self.area.name)/'root'
        self.saves=f.create(self.root,marker=False)

    def content(self):
        return s._content(s._capture(self.root))

    def marker(self):
        p=self.root/'data/user/world-recovery/review-required.json'
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'opaque marker')

    def pause(self, action='accept-all', point='after:lock'):
        class Pause(Exception):pass
        def observe(name):
            if name==point:raise Pause(name)
        with patch.object(s,'_checkpoint',observe),self.assertRaises(Pause):
            if action=='abandon':
                p=s.pending(self.root);self.assertTrue(p['can_abandon'])
                s.dispatch(self.root,{'action':'abandon','digest':p['abandon_digest'],'confirmed':True})
            else:f.choose(self.root,action)
        return (self.root/'.save-import.lock').read_bytes()

    def abandon(self):
        p=s.plan(self.root)['pending'];self.assertTrue(p['can_abandon'],p)
        return s.dispatch(self.root,{'action':'abandon','digest':p['abandon_digest'],'confirmed':True})

    def test_startability_separate_from_strict_restoration(self):
        for kind in ('zero-primary','bad-backup','conflict','all-zero','bad-profile'):
            with self.subTest(kind=kind):
                root=Path(self.area.name)/kind;saves=f.create(root,marker=False)
                if kind=='zero-primary':(saves/'userdata0000').write_bytes(bytes(0x140000))
                elif kind=='bad-backup':(saves/'backup0000').write_bytes(b'unknown')
                elif kind=='conflict':(saves/'backup0000').write_bytes((saves/'backup0001').read_bytes())
                elif kind=='all-zero':
                    for slot in (0,1):(saves/f'userdata{slot:04}').write_bytes(bytes(0x140000))
                else:(saves/'userdata0010').write_bytes(b'bad')
                s.backups.launch_backup(root)
                self.assertFalse(s._readability(root/s.TREES['savedata'])['valid'])
                before=s._content(s._capture(root))['trees']['savedata']
                plan=s.plan(root)
                self.assertFalse(plan['needed']);self.assertFalse(plan['show_choices']);self.assertEqual(plan['options'],[])
                w.begin(root)
                self.assertEqual(s._content(s._capture(root))['trees']['savedata'],before)
                (root/s.GATE).write_bytes(b'opaque gate')
                self.assertEqual(s.plan(root)['options'][0]['id'],'use-current')
                f.choose(root,'use-current')
                self.assertFalse((root/s.GATE).exists())
                w.begin(root)
                self.assertEqual(s._content(s._capture(root))['trees']['savedata'],before)

    def test_bad_nonzero_primary_blocks_use_current(self):
        (self.saves/'userdata0000').write_bytes(b'unsupported format')
        (self.root/s.GATE).write_bytes(b'gate')
        p=s.plan(self.root)
        self.assertTrue(p['needed']);self.assertFalse(p['startable'])
        self.assertNotIn('use-current',[o['id'] for o in p['options']])
        with self.assertRaises(w.ReviewRequired):w.primary_identities(self.root)

    def test_healthy_explicit_history_has_visible_choice_not_anomaly(self):
        self.marker();f.choose(self.root,'accept-all')
        row,=s.list_cases(self.root)['backups']
        p=s.plan(self.root,**{'selection_'+k:row[k] for k in ('kind','id','fingerprint')})
        self.assertFalse(p['needed']);self.assertTrue(p['show_choices'])
        self.assertEqual([o['id'] for o in p['options']],['restore-case'])
        self.assertFalse(p['options'][0]['recommended'])

    def test_gate_keeps_real_waiting_and_bound(self):
        f.request(self.root,mode=w.RESTORE)
        (self.saves/'userdata0000').write_bytes(f.GOOD)
        self.assertEqual({x['state'] for x in w.review_items(self.root)},{'waiting-backup'})
        before=self.content()['trees'];(self.root/s.GATE).write_bytes(b'gate')
        self.assertEqual(s.plan(self.root)['options'][0]['id'],'use-current')
        f.choose(self.root,'use-current')
        self.assertEqual(self.content()['trees'],before)
        w.begin(self.root)
        other=Path(self.area.name)/'bound';f.create(other,marker=False);f.request(other,job=True)
        (other/s.GATE).write_bytes(b'opaque gate')
        self.assertEqual(s.plan(other)['options'][0]['id'],'accept-bound')
        f.choose(other,'accept-bound');self.assertFalse((other/s.GATE).exists());w.begin(other)

    def test_changed_and_missing_original_abandon_is_forward_only(self):
        self.marker();old=self.pause();value=json.loads(old)
        original=s._case_path(self.root,value['transaction'])/'original'
        original.rename(original.with_name('kept-old-original'))
        (self.saves/'later-progress').write_bytes(b'new progress not merged')
        before=self.content();p=s.pending(self.root)
        self.assertFalse(p['can_resume']);self.assertTrue(p['can_abandon'])
        result=self.abandon();case=s._case_path(self.root,result['case_id'])
        self.assertEqual(s._original_content(case),before)
        self.assertEqual((case/'original/control/import-lock').read_bytes(),old)
        self.assertEqual((case/'retired/completed-lock').read_bytes(),(case/'intent.json').read_bytes())
        self.assertEqual(self.content()['trees'],before['trees']);self.assertTrue((self.root/s.GATE).exists())
        self.assertFalse(result['can_launch']);self.assertTrue(s.plan(self.root)['needed'])

    def test_abandon_again_after_receipt_does_not_complete_superseded_case(self):
        self.marker();self.pause();old=self.pause('abandon','after:abandon-receipt')
        oldcase=s._case_path(self.root,json.loads(old)['transaction'])
        self.assertTrue((oldcase/'receipt.json').is_file())
        self.assertFalse((oldcase/'retired/completed-lock').exists())
        (self.saves/'later').write_bytes(b'newer after receipt')
        before=self.content();result=self.abandon()
        self.assertEqual(self.content()['trees'],before['trees'])
        self.assertNotIn(oldcase.name,[r['id'] for r in s.list_cases(self.root)['backups']])
        with self.assertRaises(ValueError):s.resume(self.root,hashlib.sha256(old).hexdigest(),confirmed=True)
        case=s._case_path(self.root,result['case_id']);fingerprint=sha(case/'intent.json')
        (self.saves/'even-later').write_bytes(b'never replay')
        (self.root/s.GATE).write_bytes(b'new opaque gate')
        later=self.content();again=s.resume(self.root,fingerprint,confirmed=True)
        self.assertEqual(self.content(),later);self.assertFalse(again['can_launch'])

    def test_abandon_rejects_stale_or_unconfirmed_and_foreign_locks(self):
        self.marker();self.pause();p=s.pending(self.root)
        with self.assertRaises(ValueError):s.abandon(self.root,p['abandon_digest'])
        (self.saves/'new').write_bytes(b'new')
        with self.assertRaises(ValueError):s.abandon(self.root,p['abandon_digest'],confirmed=True)
        for raw in (b'1234',b'{}',b'{bad',b'{"schema":1,"operation":"restore"}'):
            (self.root/'.save-import.lock').write_bytes(raw)
            self.assertFalse(s.pending(self.root)['can_abandon'])
            self.assertEqual((self.root/'.save-import.lock').read_bytes(),raw)

    def test_future_a_envelope_can_be_preserved_but_not_executed(self):
        self.marker();raw=self.pause();value=json.loads(raw)
        value['text_version']=999;value['action']='future-action';value['root']='Z:/NEVER-FOLLOW'
        old=json.dumps(value).encode();(self.root/'.save-import.lock').write_bytes(old)
        self.assertFalse(s.pending(self.root)['can_resume'])
        result=self.abandon();case=s._case_path(self.root,result['case_id'])
        self.assertEqual((case/'original/control/import-lock').read_bytes(),old)

    def test_abandon_preservation_error_never_replaces_old_lock(self):
        self.marker();old=self.pause();p=s.pending(self.root);before=self.content()
        with patch.object(s,'_durable_copy',side_effect=PermissionError('synthetic copy denial')):
            with self.assertRaises(PermissionError):s.abandon(self.root,p['abandon_digest'],confirmed=True)
        self.assertEqual((self.root/'.save-import.lock').read_bytes(),old)
        self.assertEqual(self.content(),before)

    def test_abandon_change_at_atomic_boundary_rejects_confirmation(self):
        self.marker();old=self.pause();p=s.pending(self.root)
        def change(point):
            if point=='before:replace-record:.save-import.lock':(self.saves/'new').write_bytes(b'new during temp write')
        with patch.object(s,'_checkpoint',change),self.assertRaises(t.OperationUnavailable):
            s.abandon(self.root,p['abandon_digest'],confirmed=True)
        self.assertEqual((self.root/'.save-import.lock').read_bytes(),old)

    def test_common_guard_precedes_ordinary_lock_and_source_read(self):
        (self.root/s.GATE).write_bytes(b'gate')
        ordinary=Path(self.area.name)/'ordinary';f.create(ordinary,marker=False)
        (ordinary/'Bloodborne.exe').write_bytes(b'synthetic never execute')
        # Valid synthetic source supplied. The entry must not even inspect it.
        with patch.object(t,'ROOT',self.root),patch.object(t,'_import_lock',wraps=t._import_lock) as lock,patch.object(t,'source_path',return_value=ordinary/'data/user/savedata') as source:
            with self.assertRaises(s.ResolutionRequired):t.apply(ordinary)
            self.assertEqual(lock.call_count,0);self.assertEqual(source.call_count,0)
        with patch.object(t,'_import_lock',wraps=t._import_lock) as lock:
            with self.assertRaises(s.ResolutionRequired):w.export_evidence(self.root,w.evidence_plan(w.evidence_inventory(self.root)))
            self.assertEqual(lock.call_count,0)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_classification_permission_denial_is_not_review_after_capture(self):
        request=f.request(self.root,job=True);target=request['path'];real=w.stable;reached=[]
        def read(path):
            if Path(path)==target and any(fr.name=='review_items' for fr in traceback.extract_stack()):
                reached.append(True);raise PermissionError('read failure inside actual review')
            return real(path)
        with patch.object(w,'stable',read),self.assertRaises(PermissionError):s.plan(self.root)
        self.assertTrue(reached);self.assertEqual(s.plan(self.root)['options'][0]['id'],'accept-bound')

    def test_second_request_io_cannot_downgrade_to_accept_all(self):
        first=f.request(self.root,job=True)
        second=f.request(self.root,job=True,slot=1,token=first['token'])
        self.assertEqual(s.plan(self.root)['options'][0]['id'],'accept-all')
        real=w.stable;hits=[]
        def denied(path):
            if Path(path)==second['path'] and any(fr.name=='review_items' for fr in traceback.extract_stack()):
                hits.append(True);raise PermissionError('second request only')
            return real(path)
        with patch.object(w,'stable',denied),self.assertRaises(PermissionError):s.plan(self.root)
        self.assertTrue(hits)
        self.assertEqual(s.plan(self.root)['options'][0]['id'],'accept-all')

    def test_stable_missing_run_has_full_choice_not_permanent_io_retry(self):
        request=f.request(self.root)
        run=request['folder']/'runs'/f"{request['token']}.json"
        run.rename(run.with_suffix('.preserved'))
        p=s.plan(self.root)
        self.assertTrue(p['needed']);self.assertEqual(p['options'][0]['id'],'accept-all')

    def test_bound_permission_reaches_binding_after_readability(self):
        f.request(self.root,job=True);real=w.stable;reached=[]
        def read(path):
            if Path(path)==self.saves/'backup0000' and any(fr.name=='_bound_scope' for fr in traceback.extract_stack()):
                reached.append(True);raise PermissionError('binding-only denial')
            return real(path)
        with patch.object(w,'stable',read),self.assertRaises(PermissionError):s.plan(self.root)
        self.assertTrue(reached)

    def test_marker_and_unmatched_job_io_not_corruption(self):
        self.marker();target=self.root/'data/user/world-recovery/review-required.json';real=w.stable
        with patch.object(w,'stable',side_effect=lambda p: (_ for _ in ()).throw(PermissionError('marker IO')) if Path(p)==target else real(p)),self.assertRaises(PermissionError):s.plan(self.root)
        target.unlink();job=target.parent/'jobs'/('a'*32+'-0-1');job.mkdir(parents=True)
        manifest=job/'job.json';manifest.write_text('{"state":"complete"}',encoding='utf-8')
        with patch.object(w,'stable',side_effect=lambda p: (_ for _ in ()).throw(PermissionError('job IO')) if Path(p)==manifest else real(p)),self.assertRaises(PermissionError):s.plan(self.root)
        self.assertFalse(s.plan(self.root)['needed'])

    def test_later_runs_io_propagates_instead_of_false(self):
        request=f.request(self.root);real=w.stable
        with patch.object(w,'stable',side_effect=lambda p: (_ for _ in ()).throw(PermissionError('runs IO')) if Path(p).parent.name=='runs' else real(p)),self.assertRaises(PermissionError):
            w.later_run_exited_cleanly(request,None,None)
        self.assertFalse(w.later_run_exited_cleanly(request,None,None))

    def test_transient_missing_and_typed_value_errors_propagate(self):
        p=self.saves/'userdata0000'
        with self.assertRaises(w.EvidenceChangedError):w.require_stable_missing(FileNotFoundError(2,'transient',str(p)))
        missing=self.root/'missing';w.require_stable_missing(FileNotFoundError(2,'stable',str(missing)))
        for error in (w.EvidenceChangedError('changed'),t.OperationUnavailable('process guard')):
            with self.assertRaises(t.OperationUnavailable):w.require_content_error(error)
        request=f.request(self.root)
        with patch.object(w,'stable',side_effect=w.EvidenceChangedError('changed')),self.assertRaises(w.EvidenceChangedError):
            w.check_stopped_without_replay(request)

    def test_real_prepare_backup_begin_after_use_current_and_io_route(self):
        ordinary=Path(self.area.name)/'ordinary';ordinary.mkdir()
        for n in ('Bloodborne.exe','bbport.ini','eboot.bin'):(ordinary/n).write_bytes(b'synthetic never execute')
        profile={'install':str(ordinary),'game':str(ordinary),'graphics_source':str(ordinary/'bbport.ini')}
        (self.saves/'backup0000').write_bytes(b'unsupported backup')
        (self.root/s.GATE).write_bytes(b'gate');f.choose(self.root,'use-current')
        class GameBoundary(Exception):pass
        with patch.object(client,'ROOT',self.root),patch.object(client,'prepare',wraps=client.prepare) as prepare,patch.object(s.backups,'launch_backup',wraps=s.backups.launch_backup) as backup,patch.object(w,'begin',wraps=w.begin) as begin,patch.object(client,'run_game',side_effect=GameBoundary('no game process')):
            with self.assertRaises(GameBoundary):client.run_session(profile,'','',None)
            self.assertEqual((prepare.call_count,backup.call_count,begin.call_count),(1,1,1))
        self.assertEqual((self.saves/'backup0000').read_bytes(),b'unsupported backup')
        real=w.stable
        def denied(path):
            if Path(path)==self.saves/'userdata0000' and any(fr.name=='primary_identities' for fr in traceback.extract_stack()):raise PermissionError('begin read only')
            return real(path)
        with patch.object(client,'ROOT',self.root),patch.object(w,'stable',denied),self.assertRaises(PermissionError):
            client.run_session(profile,'','',None)
        phase=json.loads((self.root/'launcher-state.json').read_text(encoding='utf-8'))['phase']
        self.assertNotEqual(phase,'save-resolution-needed')


    def inventory(self):
        return {p.relative_to(self.root).as_posix():(sha(p),p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_journal_transient_missing_before_both_fallback_branches(self):
        for prepared in (False,True):
            with self.subTest(prepared=prepared):
                root=Path(self.area.name)/('prepared' if prepared else 'no-job')
                saves=f.create(root,marker=False)
                first=f.request(root,job=prepared)
                second=f.request(root,job=prepared,slot=1,token=first['token'])
                for request in (first,second):
                    for stem in ('userdata','backup'):
                        path=saves/f'{stem}{request["slot"]:04}'
                        path.write_bytes(w.image(path.read_bytes(),request))
                before=s._content(s._capture(root));control=s.plan(root)
                self.assertFalse(control['needed']);self.assertEqual(control['options'],[])
                targets={first['journal'],second['journal']};original=Path.read_bytes;hits=[]
                def missing(path):
                    if path in targets and any(fr.name=='read_request' for fr in traceback.extract_stack()):
                        hits.append(path);self.assertTrue(path.is_file())
                        raise FileNotFoundError(errno.ENOENT,'transient read',str(path))
                    return original(path)
                with patch.object(Path,'read_bytes',missing),self.assertRaises(w.EvidenceChangedError):
                    s.plan(root)
                self.assertTrue(hits);self.assertEqual(s._content(s._capture(root)),before)
                self.assertFalse(s.plan(root)['needed'])

    def test_content_error_checks_implicit_explicit_and_suppressed_context(self):
        for mode in ('implicit','cause','suppressed'):
            with self.subTest(mode=mode):
                try:
                    try:raise FileNotFoundError(errno.ENOENT,'transient',str(self.saves/'userdata0000'))
                    except FileNotFoundError as error:
                        if mode=='cause':raise ValueError('fallback') from error
                        if mode=='suppressed':raise ValueError('fallback') from None
                        raise ValueError('fallback')
                except ValueError as error:
                    with self.assertRaises(w.EvidenceChangedError):w.require_content_error(error)
        error=ValueError('cycle');error.__context__=error
        self.assertEqual(list(w.exception_chain(error)),[error])

    def test_stable_missing_journal_keeps_real_archived_fallback(self):
        request=f.request(self.root,job=True)
        for stem in ('userdata','backup'):
            path=self.saves/f'{stem}0000';path.write_bytes(w.image(path.read_bytes(),request))
        value=w.read_job(request)
        self.assertTrue(w.finish(request,value,proof='synthetic both generations match'))
        self.assertFalse(request['journal'].exists())
        before=self.inventory();actual=w.read_request(self.root,request['path'],enroll=False)
        self.assertEqual(actual['journal_bytes'],request['journal_bytes'])
        self.assertFalse(s.plan(self.root)['needed']);self.assertEqual(self.inventory(),before)

    def test_completed_v2_query_ignores_later_supported_locks_read_only(self):
        self.marker();self.pause();done=self.abandon()
        raw=(s._case_path(self.root,done['case_id'])/'intent.json').read_bytes();fingerprint=hashlib.sha256(raw).hexdigest()
        later=self.pause();self.assertNotEqual(hashlib.sha256(later).hexdigest(),fingerprint)
        lock=self.root/'.save-import.lock'
        for current in (later,b'12345',b'{"operation":"restore","pid":12345}'):
            with self.subTest(current=current[:40]):
                lock.write_bytes(current);(self.root/s.GATE).write_bytes(b'later opaque gate')
                before=self.inventory()
                result=s.dispatch(self.root,{'action':'resume','fingerprint':fingerprint,'confirmed':True})
                self.assertTrue(result['ok']);self.assertFalse(result['can_launch'])
                self.assertEqual(self.inventory(),before)
        # Even an exact current active intent plus a copied witness is ambiguous.
        lock.write_bytes(raw);before=self.inventory()
        with self.assertRaises(ValueError):s.resume(self.root,fingerprint,confirmed=True)
        self.assertEqual(self.inventory(),before)

    def test_control_bytes_bounded_stable_and_same_identity(self):
        lock=self.root/'.save-import.lock';lock.write_bytes(b'x'*64)
        original_open=Path.open;original_read=Path.read_bytes;reads=[]
        class Observe:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):self.stream.__enter__();return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def fileno(self):return self.stream.fileno()
            def read(self,amount=-1):
                reads.append(amount);self_amount=amount
                if self_amount<0:raise AssertionError('unbounded control read')
                return self.stream.read(amount)
        def opened(path,*args,**kwargs):
            stream=original_open(path,*args,**kwargs)
            return Observe(stream) if path==lock and args and args[0]=='rb' else stream
        with patch.object(s,'MAX_RECORD_BYTES',64),patch.object(Path,'open',opened),patch.object(Path,'read_bytes',side_effect=AssertionError('no read_bytes for controls')):
            info,raw=s._control(lock)
            self.assertEqual(raw,b'x'*64);self.assertEqual(info['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(info['bytes'],64);self.assertEqual(reads,[65])
        lock.write_bytes(b'x'*65);before=self.inventory();reads.clear()
        def no_unbounded(path):
            if path==lock:raise AssertionError('oversize control passed to read_bytes')
            return original_read(path)
        with patch.object(s,'MAX_RECORD_BYTES',64),patch.object(Path,'open',opened),patch.object(Path,'read_bytes',no_unbounded):
            pending=s.pending(self.root)
            self.assertFalse(pending['can_abandon']);self.assertFalse(pending['can_resume'])
            self.assertIn('安全大小上限',pending['abandon_message'])
            with self.assertRaises(ValueError):s.abandon(self.root,'stale',confirmed=True)
        self.assertEqual(reads,[]);self.assertEqual(self.inventory(),before)

    def test_control_growth_and_replacement_after_read_are_operational(self):
        lock=self.root/'.save-import.lock';original=Path.open
        for mode in ('grow','replace'):
            with self.subTest(mode=mode):
                lock.write_bytes(b'x'*32)
                class Changing:
                    def __init__(self,stream):self.stream=stream
                    def __enter__(self):self.stream.__enter__();return self
                    def __exit__(self,*args):return self.stream.__exit__(*args)
                    def fileno(self):return self.stream.fileno()
                    def read(self,amount):
                        raw=self.stream.read(amount)
                        if mode=='grow':
                            with original(lock,'ab') as writer:writer.write(b'y'*40)
                        return raw
                def opened(path,*args,**kwargs):
                    target=path==lock and args and args[0]=='rb'
                    if target and mode=='replace':
                        # Replace after the first stat but before opening: no
                        # unsupported assumption about deleting an open NT handle.
                        new=lock.with_name('replacement-record');new.write_bytes(b'z'*32)
                        os.replace(new,lock)
                    stream=original(path,*args,**kwargs)
                    return Changing(stream) if target and mode=='grow' else stream
                with patch.object(s,'MAX_RECORD_BYTES',64),patch.object(Path,'open',opened),self.assertRaises(t.OperationUnavailable):
                    s._control(lock)

    def json_plan(self):
        output=io.StringIO()
        with patch.object(s,'ROOT',self.root),patch.object(sys,'stdin',io.StringIO('{"action":"plan"}')),contextlib.redirect_stdout(output):
            code=s.main()
        return code,json.loads(output.getvalue())

    def test_operation_json_steps_and_unknown_lock_are_distinct(self):
        original=w.stable;target=self.saves/'userdata0000'
        for error,expected in ((PermissionError(errno.EACCES,'denied',str(target)),'读取和写入权限'),
                               (OSError(errno.ENOSPC,'full',str(target)),'非存档文件'),
                               (TimeoutError(errno.ETIMEDOUT,'timeout',str(target)),'等待超时'),
                               (w.EvidenceChangedError('changed'),'旧确认不能继续使用')):
            def failed(path):
                if Path(path)==target and any(fr.name=='primary_identities' for fr in traceback.extract_stack()):raise error
                return original(path)
            before=self.inventory()
            with patch.object(w,'stable',failed):code,value=self.json_plan()
            self.assertEqual(code,1);self.assertFalse(value['ok']);self.assertIn(expected,value['error'])
            self.assertNotIn('options',value);self.assertEqual(self.inventory(),before)
        lock=self.root/'.save-import.lock';lock.write_bytes(b'{unknown')
        code,value=self.json_plan();self.assertEqual(code,0)
        pending=value['pending'];self.assertFalse(pending['can_abandon'])
        self.assertIn('当前流程不能安全继续',pending['abandon_message'])
        self.assertNotIn('权限',pending['abandon_message'])
        self.assertNotIn('后重试',pending['abandon_message'])

    @unittest.skipUnless(os.name=='nt','Windows real sharing contract')
    def test_real_windows_sharing_denial_json_then_release(self):
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
        before=self.inventory();handle=kernel.CreateFileW(str(self.saves/'userdata0000'),0x80000000,0,None,3,0x80,None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        try:
            code,value=self.json_plan()
            self.assertEqual(code,1);self.assertFalse(value['ok']);self.assertIn('正常退出',value['error'])
            self.assertIn('权限',value['error']);self.assertNotIn('options',value)
        finally:
            if not kernel.CloseHandle(handle):raise ctypes.WinError(ctypes.get_last_error())
        self.assertEqual(self.inventory(),before);self.assertEqual(self.json_plan()[0],0)


class FrozenV1Reader(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('THEHUNT_A_V1_FIXTURES'),'Provide frozen old-writer manifest for compatibility execution.')
    def test_all_old_writer_completed_and_interrupted_prefixes(self):
        source=Path(os.environ['THEHUNT_A_V1_FIXTURES']);bundle=json.loads(source.read_text(encoding='utf-8'))
        self.assertEqual(bundle['writer_head'],'bf008958e435dda4197258c5374f1610ab05fa84')
        self.assertEqual(bundle['status'],'v1-fixtures-generated-not-new-reader-validated')
        for sample in bundle['samples']:
            with self.subTest(action=sample['action'],point=sample['point']),tempfile.TemporaryDirectory(prefix='hunt-v1-upgrade-') as area:
                old=Path(sample['root']);root=Path(area)/'relocated'
                for n,row in sample['inventory']['files'].items():self.assertEqual(sha(old/n),row['sha256'])
                shutil.copytree(old,root)
                if sample['point']=='completed':
                    case=root/'save-backups/recovery-choice'/sample['result']['case_id'];raw=(case/'intent.json').read_bytes()
                else:raw=(root/'.save-import.lock').read_bytes();case=root/'save-backups/recovery-choice'/json.loads(raw)['transaction']
                self.assertEqual(json.loads(raw)['text_version'],1)
                immutable={n:(case/n).read_bytes() for n in ('manifest.json','intent.json')}
                existing_receipt=(case/'receipt.json').read_bytes() if (case/'receipt.json').exists() else None
                existing_gate=(root/s.GATE).read_bytes() if (root/s.GATE).exists() else None
                with patch.object(s,'TEXT_VERSION',99):
                    result=s.resume(root,hashlib.sha256(raw).hexdigest(),confirmed=True)
                    self.assertTrue(result['ok']);self.assertFalse((root/'.save-import.lock').exists())
                    s._case(root,case.name)
                for n,data in immutable.items():self.assertEqual((case/n).read_bytes(),data)
                intent=json.loads(raw)
                expected={'schema':1,'root':intent['root'],'transaction':intent['transaction'],'action':intent['action'],
                          'intent_sha256':hashlib.sha256(raw).hexdigest(),'manifest_sha256':intent['manifest_sha256'],
                          'result':{'accept-bound':'accepted-current-unverified','accept-all':'accepted-current-unverified','restore-all':'selected-backup','restore-case':'restored-original-needs-review','use-current':'selected-current','preserve':'preserved-only'}[intent['action']]}
                self.assertEqual((case/'receipt.json').read_bytes(),oracle.canonical(expected))
                if existing_receipt is not None:self.assertEqual((case/'receipt.json').read_bytes(),existing_receipt)
                self.assertEqual(oracle.actual(root,case),oracle.independent_states(intent)[-1])
                self.assertEqual(oracle.case_content(case),intent['before'])
                if sample['point']=='completed':
                    self.assertEqual((root/s.GATE).read_bytes() if (root/s.GATE).exists() else None,existing_gate)
                elif intent['new_gate'] is not None:
                    self.assertEqual((root/s.GATE).read_bytes(),oracle.canonical(intent['new_gate']))
                for n,row in sample['inventory']['files'].items():self.assertEqual(sha(old/n),row['sha256'])


    @unittest.skipUnless(os.environ.get('THEHUNT_A_V1_FIXTURES'),'Old writer fixtures required')
    def test_v1_receipt_with_active_lock_does_not_gain_v2_query_semantics(self):
        bundle=json.loads(Path(os.environ['THEHUNT_A_V1_FIXTURES']).read_bytes())
        sample=next(row for row in bundle['samples'] if row['action']=='accept-all' and row['point']=='after:receipt')
        for mode in ('same-active','replaced'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(prefix='hunt-v1-receipt-') as area:
                root=Path(area)/'root';shutil.copytree(sample['root'],root)
                raw=(root/'.save-import.lock').read_bytes();fingerprint=hashlib.sha256(raw).hexdigest()
                case=s._case_path(root,json.loads(raw)['transaction']);receipt=(case/'receipt.json').read_bytes()
                if mode=='same-active':
                    result=s.resume(root,fingerprint,confirmed=True)
                    self.assertTrue(result['ok']);self.assertFalse((root/'.save-import.lock').exists())
                else:
                    pending=s.pending(root);self.assertTrue(pending['can_abandon'])
                    s.abandon(root,pending['abandon_digest'],confirmed=True)
                    (root/'.save-import.lock').write_bytes(b'12345')
                    before={p.relative_to(root).as_posix():(sha(p),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
                    with self.assertRaises(ValueError):s.resume(root,fingerprint,confirmed=True)
                    self.assertEqual({p.relative_to(root).as_posix():(sha(p),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()},before)
                self.assertEqual((case/'receipt.json').read_bytes(),receipt)


if __name__=='__main__':unittest.main()
