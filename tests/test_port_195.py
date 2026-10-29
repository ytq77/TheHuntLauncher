"""Exercise the pinned runner's actual path selection and public package imports."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PORT=Path(os.environ.get('BBSC_PORT_SOURCE',ROOT/'.build/bbport'))
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'launcher'))
from port_compat import patched_coop_saves,patched_run_script
from settings_bridge import sync_settings

@unittest.skipUnless((PORT/'run.py').exists(),'fetch the pinned BBport source first')
class Port195Compatibility(unittest.TestCase):
    def function(self,name,patched=False):
        source=(PORT/'run.py').read_text(encoding='utf-8')
        if patched:source=patched_coop_saves(patched_run_script(source))
        tree=ast.parse(source);node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
        scope={'Path':Path,'re':re,'sys':sys,'PORT':PORT,'COOP_SAVES':'seamless-coop'}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'pinned-run.py','exec'),scope)
        return scope[name]

    def test_existing_thehunt_save_root_keeps_recovery_and_engine_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            user=Path(temporary)/'user';saves=user/'savedata/1/CUSA00207/SPRJ0005';saves.mkdir(parents=True)
            sentinel=saves/'userdata0001';sentinel.write_bytes(b'own-save-unchanged')
            env={'THEHUNT_WORLD_RECOVERY_V2':'1','BBSC_SAVE_DIR':str(saves)}
            actual=self.function('coop_user_dir',patched=True)(env,user,Path(temporary)/'game')
            self.assertEqual(actual,user);self.assertFalse((user/'seamless-coop').exists())
            self.assertEqual(sentinel.read_bytes(),b'own-save-unchanged')
            for bad in (None,str(Path(temporary)/'foreign/savedata')):
                with self.subTest(bad=bad),self.assertRaisesRegex(ValueError,'paths disagree'):
                    self.function('coop_user_dir',patched=True)(dict(env,BBSC_SAVE_DIR=bad),user,Path(temporary)/'game')

    def test_original_upstream_starts_keep_their_own_new_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            user=Path(temporary)/'user';user.mkdir();env={}
            actual=self.function('coop_user_dir',patched=True)(env,user,Path(temporary)/'game')
            self.assertEqual(actual,user/'seamless-coop')
            self.assertEqual(env['BBSC_SAVE_DIR'],str(actual/'savedata/1/CUSA00207/SPRJ0005'))

    def test_explicit_community_server_stays_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            user=Path(temporary);fn=self.function('online_overrides')
            fn.__globals__.update(json=json,COMMUNITY_SERVER='https://default.invalid',GAME_SERVER='https://ss4.scej-network.jp:20443',COMMUNITY_API_PORT=18671,
                                  seamless_coop=lambda env:True,bbsc_overrides=lambda path:False)
            env={'BB_COMMUNITY_SERVER':'https://private.example:20443'};fn(env,user)
            value=json.loads((user/'host_overrides.json').read_text())
            self.assertEqual(value['https://ss4.scej-network.jp:20443'],'https://private.example:20443')

    def test_runner_dependency_closure_imports_without_full_upstream_gui(self):
        import shutil,subprocess
        names=('bbport_driver_runtime','bbport_exit_backup','bbport_saves','bbport_character','bbport_trophies')
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)
            for n in names:shutil.copy2(PORT/'launcher'/(n+'.py'),target/(n+'.py'))
            code='import sys;sys.path.insert(0,sys.argv[1]);'+ ';'.join('import '+n for n in names)
            r=subprocess.run([sys.executable,'-I','-B','-c',code,str(target)],capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr.decode(errors='replace'))

    def test_new_graphics_choices_sync_while_rebirth_remains_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp=Path(temporary);normal=temp/'normal';normal.mkdir();root=temp/'thehunt';root.mkdir()
            ini=normal/'bbport.ini';ini.write_text('output_res=1920x1080\nupscaler=fsr412\npreset=2\nobject_motion_cache=1\nrebirth=1\n')
            settings=temp/'settings.json';settings.write_text(json.dumps({'low_latency':'on','vrr':'1','early_submit':'1','async_pipelines':'1',
                    'fullscreen':True,'exclusive_fullscreen':True,'vram_warning':False,'menu_key':'F10','menu_pad':'l3+r3'}))
            before=ini.read_bytes();env,_=sync_settings(root,{'graphics_source':str(ini)},settings)
            for name,value in {'BB_LOW_LATENCY':'on','BB_VRR':'1','BB_EARLY_SUBMIT':'1','BB_ASYNC_PIPELINES':'1','BB_WINDOW_MODE':'fullscreen',
                               'BB_VRAM_WARN_MB':'0','BB_MENU_KEY':'F10','BB_MENU_PAD':'l3+r3','BB_OBJECT_MOTION_CACHE':'1','BB_REBIRTH':'0'}.items():
                self.assertEqual(env[name],value)
            self.assertIn('rebirth=0',(root/'data/bbport.ini').read_text());self.assertEqual(ini.read_bytes(),before)

    def test_unknown_upstream_save_switch_is_rejected(self):
        with self.assertRaises(ValueError):patched_coop_saves('upstream changed')

if __name__=='__main__':unittest.main()
