"""Observation, unknown-state and collection tests; no UI, game or live save access."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts/lamp_trace'),str(ROOT/'launcher'),str(ROOT/'tests')]
from lamp_analysis import native_flags,removed,summarize_native,disk_flags
from lamp_runner import collect,case_config,observe,run
from prepare_clone import safe_entries
from synthetic_world_fixture import fixture_pair
import offline_aux

MAPPING={'flags':[12417830,13407810],'groups':{'12417':3,'13407':4}}

def group(enabled=True):
    raw=bytearray(125)
    if enabled:raw[830//8]|=0x80>>(830%8)
    return raw.hex()

class LampObservation(unittest.TestCase):
    def test_missing_group_is_unknown_not_closed(self):
        before=native_flags({'groups':{'12417':group()}},MAPPING)
        after=native_flags({'groups':{}},MAPPING)
        self.assertIsNone(after['12417830']);self.assertEqual(removed(before,after),[])

    def test_real_client_preserves_trace_and_uses_explicit_private_endpoints(self):
        import client
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'mod').mkdir()
            (root/'mod/mod.json').write_text('{"settings":[]}',encoding='utf-8')
            shutil.copy2(ROOT/'launcher/feature-catalog.json',root/'feature-catalog.json')
            network={'shadnet':'127.0.0.1:31413','webapi':'http://127.0.0.1:31415','community':'http://127.0.0.1:20443','relay':'127.0.0.1:20444'}
            with patch.object(client,'ROOT',root),patch.dict(os.environ,{'BB_NET_BACKEND':'thd','BB_THD_TOKEN':'fixture-token','BB_COMMUNITY_SERVER':'https://thehuntersdream.com','THEHUNT_LAMP_TRACE':'1','THEHUNT_LAMP_TRACE_PATH':str(root/'native.jsonl')}):
                env,resolved=client.build_environment({'game':str(root/'game'),'network':network},'fixture-account','fixture-password',{}, {'patches_dir':str(root/'patches'),'patches_config':str(root/'patches.json')})
            self.assertEqual(env['BB_NET_BACKEND'],'shadnet')
            self.assertEqual(env['BB_SHADNET_SERVER'],network['shadnet'])
            self.assertEqual(env['BB_COMMUNITY_SERVER'],network['community'])
            self.assertEqual(env['BBSC_RELAY'],network['relay'])
            self.assertNotIn('BB_THD_TOKEN',env)
            self.assertEqual(env['THEHUNT_LAMP_TRACE'],'1')
            self.assertEqual(resolved,network)

    def test_pair_reports_actual_drop_and_cached_save_is_not_a_new_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'native.jsonl'
            def row(reason,value,seq,cached=False):return {'reason':reason,'operation':1,'sequence':seq,'sample_epoch_ms':seq,'cached':cached,'context':{'groups':{'12417':group(value)},'session':6,'own_world':0,'active_slot_hint':1}}
            rows=[row('dream-apply.enter',True,1),row('native-save-entry-cached',False,2,True),row('dream-apply.exit',False,3)]
            path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            report=summarize_native(path,MAPPING)
            self.assertEqual(len(report['changes']),1)
            self.assertEqual(report['changes'][0]['removed'],[12417830])
            self.assertEqual(report['changes'][0]['view'],'host/guest temporary view')
            self.assertEqual(report['cached_save_entries'],1)

    def test_malformed_or_truncated_native_data_is_rejected(self):
        with self.assertRaises(ValueError):native_flags({'groups':{'12417':'ab'}},MAPPING)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'native.jsonl';path.write_text('{"context":',encoding='utf-8')
            with self.assertRaises(ValueError):summarize_native(path,MAPPING)

    def test_archive_paths_cannot_escape_or_alias(self):
        for name in ('../escape','C:/escape','p/../../escape','p\\escape'):
            memory=io.BytesIO()
            with zipfile.ZipFile(memory,'w') as z:z.writestr(name,b'x')
            raw=memory.getvalue()
            # Windows' ZIP writer normalizes backslashes. Put the raw name in
            # both headers so this tests an external archive, not a sanitized one.
            if '\\' in name:raw=raw.replace(name.replace('\\','/').encode(),name.encode())
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                with self.assertRaises(ValueError):safe_entries(z)

    def test_changed_module_is_refused_and_collection_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'module').write_bytes(b'observer')
            cfg={'schema':1,'role':'Host02','slot':0,'program_sha256':{'module':hashlib.sha256(b'observer').hexdigest()}}
            (root/'LAMP-CASE.json').write_text(json.dumps(cfg),encoding='utf-8')
            case=root/'dream-evidence/case';(case/'before').mkdir(parents=True)
            (case/'before/userdata0000').write_bytes(b'save')
            (case/'case.json').write_text('{}',encoding='utf-8')
            (case/'profile.json').write_text('DO-NOT-COLLECT',encoding='utf-8')
            (case/'.private').mkdir();(case/'.private/account.clixml').write_text('SECRET',encoding='utf-8')
            archive=collect(root)
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()),{'dream-evidence/case/before/userdata0000','dream-evidence/case/case.json'})
            (root/'module').write_bytes(b'changed')
            with self.assertRaises(ValueError):case_config(root)

    def test_disk_watcher_preserves_both_observed_versions_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);saves=root/'data/user/savedata/1/CUSA00207/SPRJ0005';saves.mkdir(parents=True)
            evidence=root/'observed';evidence.mkdir()
            raw=bytearray(fixture_pair()[0]);bank=offline_aux.flag_bank(raw)
            raw[bank+3*125+830//8]|=0x80>>(830%8)
            first=bytes(raw)
            for stem in ('userdata','backup'):(saves/(stem+'0000')).write_bytes(first)
            stop=threading.Event();watcher=threading.Thread(target=observe,args=(root,evidence,0,MAPPING,stop));watcher.start()
            try:
                time.sleep(.65);raw[bank+3*125+830//8]&=~(0x80>>(830%8));second=bytes(raw)
                for stem in ('userdata','backup'):(saves/(stem+'0000')).write_bytes(second)
                time.sleep(.65)
            finally:stop.set();watcher.join(timeout=5)
            self.assertFalse(watcher.is_alive())
            rows=[json.loads(line) for line in (evidence/'disk-observations.jsonl').read_text('utf-8').splitlines()]
            self.assertFalse(any('read_error' in row for row in rows))
            self.assertEqual(len(rows),4)
            self.assertEqual({row['flags']['12417830'] for row in rows},{False,True})
            self.assertEqual((saves/'userdata0000').read_bytes(),second)
            self.assertEqual({(evidence/row['snapshot']).read_bytes() for row in rows},{first,second})

    def test_wpf_worker_adapter_uses_seed_and_preserves_failed_client_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);saves=root/'data/user/savedata/1/CUSA00207/SPRJ0005';saves.mkdir(parents=True)
            raw=bytearray(fixture_pair()[0]);bank=offline_aux.flag_bank(raw);raw[bank+3*125+830//8]|=0x80>>(830%8)
            for stem in ('userdata','backup'):(saves/(stem+'0000')).write_bytes(raw)
            baseline={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in saves.iterdir()}
            (root/'ordinary').mkdir();(root/'ordinary/input.ini').write_bytes(b'fixture settings')
            (root/'game').mkdir();(root/'game/eboot.bin').write_bytes(b'fixture game')
            (root/'profile.json').write_text(json.dumps({'install':str(root/'ordinary'),'game':str(root/'game')}),encoding='utf-8')
            (root/'.private').mkdir();(root/'.private/account.clixml').write_bytes(b'NEVER-COLLECT')
            (root/'lamp-mapping.json').write_text(json.dumps(MAPPING),encoding='utf-8')
            (root/'client-original.py').write_text('''from pathlib import Path
import json,os,time
from lamp_runner import collect
ROOT=Path(__file__).resolve().parent
def require_stopped(): pass
def main():
    assert os.environ['THEHUNT_LAMP_TRACE']=='1'
    try: collect(ROOT)
    except ValueError: pass
    else: raise AssertionError('Collection overlapped a live worker')
    time.sleep(.65)
    (ROOT/'launcher-state.json').write_text(json.dumps({'phase':'error','message':'original recovery error'}))
    return 9
''',encoding='utf-8')
            cfg={'schema':1,'role':'Host02','slot':0,'baseline_sha256':baseline,'eboot_sha256':hashlib.sha256(b'fixture game').hexdigest(),'program_sha256':{'client-original.py':hashlib.sha256((root/'client-original.py').read_bytes()).hexdigest()}}
            (root/'LAMP-CASE.json').write_text(json.dumps(cfg),encoding='utf-8')
            self.assertEqual(run(root),9)
            self.assertEqual(json.loads((root/'launcher-state.json').read_text('utf-8'))['message'],'original recovery error')
            case=next((root/'dream-evidence').iterdir())
            summary=json.loads((case/'lamp-summary.json').read_text('utf-8'))
            self.assertEqual(summary['disk']['userdata']['removed_after_run'],[])
            self.assertFalse(summary['native']['available'])
            self.assertEqual((root/'data/input.ini').read_bytes(),b'fixture settings')
            self.assertTrue(all(hashlib.sha256((saves/name).read_bytes()).hexdigest()==expected for name,expected in baseline.items()))
            archive=collect(root)
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertFalse(any('profile.json' in name or '.private' in name for name in z.namelist()))
            with self.assertRaises(ValueError):run(root)

@unittest.skipUnless(os.name=='nt' and (shutil.which('clang++') or Path('C:/bbsc-dev/msys64/clang64/bin/clang++.exe').exists()),'Windows clang required')
class NativeLampObserver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area=tempfile.TemporaryDirectory();cls.root=Path(cls.area.name)
        (cls.root/'bbsc_lamp_trace_groups.h').write_text('constexpr std::array<u32,2> LampTraceGroups{12417,13407};\n',encoding='utf-8')
        compiler=Path(shutil.which('clang++') or 'C:/bbsc-dev/msys64/clang64/bin/clang++.exe')
        binary=compiler.parent;cls.env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ.get('PATH',''))
        cls.exe=cls.root/'observe.exe'
        result=subprocess.run([str(compiler),'-std=c++23','-O1','-I'+str(cls.root),'-I'+str(ROOT/'patches'),str(ROOT/'tests/test_lamp_trace_native.cpp'),'-o',str(cls.exe)],capture_output=True,env=cls.env)
        if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace'))
    @classmethod
    def tearDownClass(cls):cls.area.cleanup()
    def test_disabled_observer_never_creates_trace(self):
        path=self.root/'disabled.jsonl';env=dict(self.env,THEHUNT_LAMP_TRACE='0',THEHUNT_LAMP_TRACE_PATH=str(path))
        self.assertEqual(subprocess.run([str(self.exe)],env=env).returncode,0)
        self.assertFalse(path.exists())
    def test_actual_cpp_observer_handles_early_return_and_preserves_sentinels(self):
        path=self.root/'enabled.jsonl';env=dict(self.env,THEHUNT_LAMP_TRACE='1',THEHUNT_LAMP_TRACE_PATH=str(path))
        self.assertEqual(subprocess.run([str(self.exe)],env=env).returncode,0)
        rows=[json.loads(line) for line in path.read_text('utf-8').splitlines()]
        reasons={row['reason'] for row in rows}
        self.assertIn('early-return.enter',reasons);self.assertIn('early-return.exit',reasons)
        report=summarize_native(path,MAPPING)
        self.assertEqual(report['changes'][0]['stage'],'test-operation')
        self.assertEqual(report['changes'][0]['removed'],[12417830])
        self.assertEqual(report['cached_save_entries'],1)
        self.assertTrue(report['unknown_groups_observed'])

if __name__=='__main__':unittest.main(verbosity=2)
