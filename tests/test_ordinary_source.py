"""Synthetic ordinary-source selection; never consult the real user's APPDATA."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_save_transfer as legacy

T = legacy.transfer


class OrdinarySourceTests(unittest.TestCase):
    setUp = legacy.SaveTransferTests.setUp
    contents = legacy.SaveTransferTests.contents
    write_save = legacy.SaveTransferTests.write_save

    def manual(self):
        target = self.base/'manual/user/savedata'
        shutil.copytree(self.source,target)
        return target

    def profile(self,path):
        (self.root/'profile.json').write_text(json.dumps({'ordinary_savedata':str(path)},ensure_ascii=False),encoding='utf-8')

    def details(self,**kw):
        return T.source_details(self.install,root=self.root,settings_path=self.settings,**kw)

    def test_default_source_metadata_and_legacy_path(self):
        d=self.details()
        self.assertEqual(Path(d['source']),self.source)
        self.assertEqual(d['source_kind'],'default')
        self.assertTrue(d['exists'])
        self.assertTrue(d['source_label'])
        self.assertEqual(T.source_path(self.install),self.source)

    def test_bbport_user_dir_overrides_install_default(self):
        custom=self.base/'bbport-user'
        shutil.copytree(self.source,custom/'savedata')
        self.settings.write_text(json.dumps({'user_dir':str(custom)}),encoding='utf-8')
        d=self.details()
        self.assertEqual(Path(d['source']),custom/'savedata')
        self.assertEqual(d['source_kind'],'bbport')
        self.assertEqual(T.source_path(self.install),custom/'savedata')

    def test_profile_manual_wins_over_bbport_and_default(self):
        manual=self.manual();self.profile(manual)
        self.settings.write_text(json.dumps({'user_dir':str(self.base/'automatic')}),encoding='utf-8')
        d=self.details()
        self.assertEqual(Path(d['source']),manual)
        self.assertEqual(d['source_kind'],'manual')
        self.assertEqual(T.source_path(self.install,root=self.root),manual)

    def test_explicit_manual_wins_over_profile(self):
        manual=self.manual();self.profile(self.base/'other/savedata')
        self.assertEqual(Path(self.details(manual_path=str(manual))['source']),manual)

    def test_bad_settings_does_not_poison_manual_selection(self):
        manual=self.manual();self.profile(manual)
        self.settings.write_text('{bad-json',encoding='utf-8')
        self.assertEqual(Path(self.details()['source']),manual)
        self.assertEqual(T.resolve_source_path(self.install,self.settings,str(manual)),manual)

    def test_manual_empty_bypasses_profile_then_clear_profile_falls_back(self):
        manual=self.manual();self.profile(manual)
        self.assertEqual(Path(self.details(manual_path='')['source']),self.source)
        self.assertEqual(T.source_path(self.install,root=self.root,manual_path=''),self.source)
        (self.root/'profile.json').write_text('{}',encoding='utf-8')
        self.assertEqual(Path(self.details()['source']),self.source)

    def test_no_root_conversion_compatibility_ignores_profile(self):
        self.profile(self.manual())
        self.assertEqual(T.source_path(self.install),self.source)
        with patch.object(T,'ROOT',self.base/'unrelated'):
            self.assertEqual(T.resolve_source_path(self.install,self.settings),self.source)

    def test_manual_user_savedata_and_game_leaf_normalize_to_same_root(self):
        manual=self.manual()
        for selected in (manual.parent,manual,manual/'1/CUSA00207/SPRJ0005'):
            with self.subTest(selected=str(selected)):
                self.assertEqual(T.normalize_savedata_path(selected),manual)
                self.assertEqual(Path(self.details(manual_path=str(selected))['source']),manual)

    def test_arbitrary_sprj_leaf_is_not_guessed(self):
        for selected in (self.base/'random/SPRJ0005',self.base/'savedata/2/CUSA00207/SPRJ0005',
                         self.base/'savedata/1/CUSA99999/SPRJ0005'):
            selected.mkdir(parents=True)
            with self.subTest(selected=str(selected)),self.assertRaises(ValueError):
                T.normalize_savedata_path(selected)

    def test_missing_each_source_reports_full_path_and_origin(self):
        shutil.rmtree(self.source)
        for kind,manual in (('default',None),('bbport',None),('manual',str(self.base/'manual-missing/savedata'))):
            if kind=='bbport':
                self.settings.write_text(json.dumps({'user_dir':str(self.base/'bbport-missing')}))
            d=self.details(manual_path=manual)
            with self.subTest(kind=kind):
                self.assertFalse(d['exists'])
                self.assertEqual(d['source_kind'],kind)
                self.assertIn(str(d['source']),d['missing_message'])
                self.assertIn(d['source_label'],d['missing_message'])

    def test_manual_missing_inspect_error_names_selected_path_and_source(self):
        path=self.base/'manual-missing/savedata';self.profile(path)
        d=self.details()
        with self.assertRaises(ValueError) as caught:
            T.inspect(self.install)
        self.assertIn(str(path),str(caught.exception))
        self.assertIn(d['source_label'],str(caught.exception))

    def test_manual_inspect_character_slots_and_source_are_readonly(self):
        manual=self.manual();self.profile(manual)
        expected_names={1:'猎人甲',2:'Hunter B'}
        for slot,name in expected_names.items():
            path=manual/'1/CUSA00207/SPRJ0005'/('userdata%04d'%(slot-1))
            raw=bytearray(path.read_bytes());role=T.offline_save.parse(raw)
            raw[role.username:role.username+32]=name.encode('utf-16-be').ljust(32,b'\0')
            path.write_bytes(raw)
        before=T.snapshot(manual);old=self.contents(self.destination)
        info,_=T.inspect(self.install)
        self.assertEqual(Path(info['source']),manual)
        self.assertEqual([row['slot'] for row in info['characters']],[1,2])
        for row in info['characters']:
            raw=(manual/'1/CUSA00207/SPRJ0005'/('userdata%04d'%(row['slot']-1))).read_bytes()
            role=T.offline_save.parse(raw)
            expected=raw[role.username:role.username+32].decode('utf-16-be').split('\0',1)[0]
            self.assertEqual(row['name'],expected)
            self.assertEqual(row['name'],expected_names[row['slot']])
        self.assertEqual(T.snapshot(manual),before)
        self.assertEqual(self.contents(self.destination),old)

    def test_manual_apply_keeps_source_and_full_previous_destination(self):
        manual=self.manual();self.profile(manual)
        before=T.snapshot(manual);old=self.contents(self.destination)
        result=T.apply(self.install,expected_source=str(manual))
        self.assertEqual(Path(result['source']),manual)
        self.assertEqual(self.contents(self.destination),self.contents(manual))
        self.assertEqual(self.contents(Path(result['backup'])),old)
        self.assertEqual(T.snapshot(manual),before)
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_manual_apply_failure_retains_ordinary_rollback(self):
        manual=self.manual();self.profile(manual)
        old=self.contents(self.destination);before=T.snapshot(manual)
        rename=Path.rename
        def fail(path,target):
            if path.name.startswith('.savedata-import-'):
                raise PermissionError('synthetic blocked rename')
            return rename(path,target)
        with patch.object(Path,'rename',fail),self.assertRaisesRegex(ValueError,'已保持或恢复'):
            T.apply(self.install)
        self.assertEqual(self.contents(self.destination),old)
        self.assertEqual(T.snapshot(manual),before)

    def test_a_reimport_and_ordinary_inspect_resolve_same_manual_source(self):
        import save_resolution as S
        import save_resolution_fixture as fixture
        aroot=self.base/'resolution';fixture.create(aroot,marker=True)
        (aroot/'data/user/save-resolution-review.json').write_bytes(b'synthetic explicit gate')
        (aroot/'data/user/savedata/1/CUSA00207/SPRJ0005/userdata0000').write_bytes(b'unsupported nonzero role')
        manual=self.manual()
        (aroot/'profile.json').write_text(json.dumps({'ordinary_savedata':str(manual)}),encoding='utf-8')
        before=T.snapshot(manual)
        with patch.object(T,'ROOT',aroot):
            # Ordinary inspect retains its review guard, so compare the shared
            # readonly resolver DTO, not a deliberately blocked transaction.
            normal=T.source_details(self.install,root=aroot)
            plan=S.plan(aroot,ordinary_install=str(self.install))
        self.assertEqual(plan['ordinary_source']['status'],'qualified')
        self.assertEqual(Path(plan['ordinary_source']['metadata']['source']),Path(normal['source']))
        self.assertEqual(Path(normal['source']),manual)
        self.assertEqual(T.snapshot(manual),before)

    def test_source_only_cli_manual_is_readonly(self):
        manual=self.manual();before=T.snapshot(manual);old=self.contents(self.root)
        result=self.base/'source-result.json'
        code=T.main(['--install',str(self.install),'--result',str(result),'--source-only','--manual-source',str(manual)])
        self.assertEqual(code,0)
        value=json.loads(result.read_text(encoding='utf-8'))
        self.assertTrue(value['ok'])
        self.assertEqual(Path(value['source']),manual)
        self.assertEqual(self.contents(self.root),old)
        self.assertEqual(T.snapshot(manual),before)

    def test_first_prepare_missing_source_emits_explicit_empty_notice(self):
        import client
        root=self.base/'first-launch';root.mkdir()
        shutil.rmtree(self.source)
        game=b'only synthetic eboot fixture'
        (self.install/'eboot.bin').write_bytes(game)
        (self.install/'bbport.ini').write_text('[GPU]')
        profile={'install':str(self.install),'game':str(self.install),'graphics_source':str(self.install/'bbport.ini')}
        details=T.source_details(self.install,root=root)
        observed=[]
        with patch.object(client,'ROOT',root),patch.object(client,'EBOOT_SHA256',hashlib.sha256(game).hexdigest()), \
             patch.object(client,'state',side_effect=lambda *args,**kwargs:observed.append(args)):
            client.prepare(profile)
        messages='\n'.join(str(part) for row in observed for part in row)
        self.assertIn(str(details['source']),messages)
        self.assertIn(details['source_label'],messages)
        self.assertIn(details['missing_message'],messages)
        self.assertTrue((root/'data').is_dir())
        self.assertFalse((root/'data/user/savedata').exists())

    def test_uninitialized_manual_preview_lists_characters_without_importing(self):
        manual=self.manual(); self.profile(manual)
        shutil.rmtree(self.root/'data')
        original=self.contents(manual)
        result=self.base/'preview.json'
        code=T.main(['--install',str(self.install),'--result',str(result),
                     '--source-only','--preview-source'])
        value=json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(code,0)
        self.assertTrue(value['ok'])
        self.assertEqual(len(value['characters']),2)
        self.assertEqual(value['source'],str(manual))
        self.assertIn('只读',value['notice'])
        self.assertEqual(self.contents(manual),original)
        self.assertFalse((self.root/'data').exists())
        self.assertFalse((self.root/'save-backups').exists())
        self.assertFalse((self.root/'.save-import.lock').exists())

    def test_preview_invalid_source_is_refused_without_initialization(self):
        manual=self.manual(); self.profile(manual)
        shutil.rmtree(self.root/'data')
        (manual/'1/CUSA00207/SPRJ0005/userdata0000').write_bytes(b'broken-synthetic')
        original=self.contents(manual)
        result=self.base/'preview-invalid.json'
        self.assertEqual(T.main(['--install',str(self.install),'--result',str(result),
                                 '--source-only','--preview-source']),1)
        self.assertFalse(json.loads(result.read_text(encoding='utf-8'))['ok'])
        self.assertFalse((self.root/'data').exists())
        self.assertEqual(self.contents(manual),original)

    def test_missing_manual_preview_reports_path_without_initialization(self):
        self.profile(self.base/'absent/user')
        shutil.rmtree(self.root/'data')
        result=self.base/'preview-missing.json'
        self.assertEqual(T.main(['--install',str(self.install),'--result',str(result),
                                 '--source-only','--preview-source']),0)
        value=json.loads(result.read_text(encoding='utf-8'))
        self.assertFalse(value['exists'])
        self.assertIn(str(self.base/'absent/user/savedata'),value['missing_message'])
        self.assertIn('手动',value['missing_message'])
        self.assertFalse((self.root/'data').exists())

    def test_invalid_local_profile_keeps_a_local_exits_without_silent_fallback(self):
        import save_resolution_fixture as f
        aroot=self.base/'a-invalid-profile'
        saves=f.create(aroot)
        (saves/'userdata0000').write_bytes(b'unsupported synthetic role')
        before=self.contents(aroot)
        source_before=self.contents(self.source)
        plan=f.resolution.plan(aroot,ordinary_install=str(self.install))
        self.assertEqual(plan['ordinary_source']['status'],'error')
        self.assertIn(str(aroot/'profile.json'),plan['ordinary_source']['reason'])
        self.assertIn('设置页',plan['ordinary_source']['reason'])
        ids={x['id'] for x in plan['options']}
        self.assertTrue({'preserve','restart','choose-ordinary'}<=ids)
        self.assertNotIn('reimport-ordinary',ids)
        self.assertEqual(self.contents(aroot),before)
        self.assertEqual(self.contents(self.source),source_before)
        result=f.resolution.apply(aroot,plan['digest'],'preserve',confirmed=True,
                                  ordinary_install=str(self.install))
        self.assertEqual(result['result'],'preserved-only')
        self.assertEqual((aroot/'profile.json').read_bytes(),b'synthetic configuration sentinel')
        self.assertEqual(self.contents(self.source),source_before)

    def test_big_endian_names_match_import_and_a_processing_page(self):
        import save_resolution_fixture as f
        aroot=self.base/'a-big-endian-name'
        saves=f.create(aroot)
        for stem in ('userdata','backup'):
            path=saves/(stem+'0000')
            raw=bytearray(path.read_bytes())
            role=T.offline_save.parse(raw)
            raw[role.username:role.username+32]='猎人甲'.encode('utf-16-be').ljust(32,bytes([0]))
            path.write_bytes(raw)
        plan=f.resolution.plan(aroot)
        row=next(row for row in plan['characters'] if row['slot']==0)
        self.assertEqual(row['name'],'猎人甲')
        metadata,_=T.inspect_source(aroot/'data/user/savedata')
        self.assertEqual(metadata['characters'][0]['name'],row['name'])

    def test_name_decode_invalid_be_is_honestly_unknown(self):
        raw=bytearray(legacy.RAW);role=T.offline_save.parse(raw)
        raw[role.username:role.username+32]=bytes.fromhex('d800')+bytes(30)
        self.assertEqual(T.character_name(raw,role),'姓名未识别')

    def test_source_path_permission_errors_identify_each_actual_source(self):
        manual=self.manual()
        custom=self.base/'custom-user'
        shutil.copytree(self.source,custom/'savedata')
        real=Path.stat
        cases=[('manual',manual,'手动'),('bbport',custom/'savedata','bbport'),
               ('default',self.source,'默认')]
        for kind,target,label in cases:
            with self.subTest(kind=kind):
                if kind=='manual':
                    self.profile(manual)
                    self.settings.write_text('{bad-json',encoding='utf-8')
                else:
                    (self.root/'profile.json').write_text('{}',encoding='utf-8')
                    self.settings.write_text(json.dumps({'user_dir':str(custom)}) if kind=='bbport' else '{}',encoding='utf-8')
                def denied(path,*args,**kwargs):
                    if Path(path)==target:
                        raise PermissionError('synthetic source directory denied')
                    return real(path,*args,**kwargs)
                with patch.object(Path,'stat',denied):
                    with self.assertRaises(ValueError) as caught:
                        T.source_path(self.install,root=self.root)
                self.assertIn(str(target),str(caught.exception))
                self.assertIn(label,str(caught.exception))
                if kind=='manual':
                    self.assertNotIn('bbport',str(caught.exception))

    def test_explicit_source_io_keeps_original_oserror_type_and_context(self):
        real=Path.stat;error=PermissionError('synthetic denied')
        def denied(path,*args,**kwargs):
            if Path(path)==self.source:raise error
            return real(path,*args,**kwargs)
        with patch.object(Path,'stat',denied):
            with self.assertRaises(PermissionError) as caught:
                T.resolve_source_path(self.install,self.settings)
        self.assertIs(caught.exception,error)
        self.assertEqual(error.source_kind,'default')
        self.assertEqual(error.source_path,str(self.source))


if __name__=='__main__':
    unittest.main()
