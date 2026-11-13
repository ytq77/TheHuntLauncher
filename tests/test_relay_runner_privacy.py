"""Actual patched runner boundaries: utilities never inherit the private relay proof."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from port_compat import patched_relay_credentials,patched_exit_backup_credentials


@unittest.skipUnless((ROOT/'.build/bbport/run.py').is_file(), 'Fetch pinned runner before native packaging checks')
class RelayRunnerPrivacy(unittest.TestCase):
    def test_actual_pinned_runner_pops_before_any_utility_and_passes_only_to_game(self):
        text=patched_relay_credentials((ROOT/'.build/bbport/run.py').read_text('utf-8'))
        main=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='main')
        # Execute the actual first statement. It must precede wait_for/probes.
        scope={'os':os}
        sentinel={'BBSC_RELAY_AUTH':'2','BBSC_RELAY_USER_ID':'123','BBSC_RELAY_TOKEN':'synthetic-private-token'}
        with patch.dict(os.environ,sentinel):
            exec(compile(ast.Module(body=main.body[:1],type_ignores=[]),'pinned-main','exec'),scope)
            child=[sys.executable,'-B','-c',
                   'import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith("BBSC_RELAY_")}))']
            self.assertEqual(json.loads(subprocess.check_output(child)),{})
            self.assertEqual(json.loads(subprocess.check_output(child,env=dict(os.environ,**scope['relay_credentials']))),sentinel)
            self.assertTrue(all(k not in os.environ for k in sentinel))
        calls=[n for n in ast.walk(main) if isinstance(n,ast.Call) and
               ((isinstance(n.func,ast.Name) and n.func.id=='run_with_backup') or
                (isinstance(n.func,ast.Attribute) and n.func.attr=='run' and n.args and
                 isinstance(n.args[0],ast.Name) and n.args[0].id=='command'))]
        self.assertEqual(len(calls),2)
        self.assertTrue(all(any(k.arg=='env' for k in n.keywords) for n in calls))

    def test_real_exit_backup_helper_uses_explicit_game_environment(self):
        text=patched_exit_backup_credentials((ROOT/'.build/bbport/launcher/bbport_exit_backup.py').read_text('utf-8'))
        function=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='run_with_backup')
        scope={'subprocess':subprocess,'wait_and_backup':lambda p,*a,**k:p.wait()}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'pinned-backup','exec'),scope)
        with tempfile.TemporaryDirectory() as area:
            out=Path(area)/'environment.json'
            code='import os,json,sys;open(sys.argv[1],"w").write(json.dumps({"token":os.environ.get("BBSC_RELAY_TOKEN")}))'
            env=dict(os.environ,BBSC_RELAY_TOKEN='synthetic-private-token')
            self.assertEqual(scope['run_with_backup']([sys.executable,'-B','-c',code,str(out)],area,env=env),0)
            self.assertEqual(json.loads(out.read_text()),{'token':'synthetic-private-token'})

    def test_changed_upstream_or_duplicate_patch_refused(self):
        original=(ROOT/'.build/bbport/run.py').read_text('utf-8')
        with self.assertRaises(ValueError):patched_relay_credentials(patched_relay_credentials(original))
        with self.assertRaises(ValueError):patched_exit_backup_credentials('unknown source')


if __name__=='__main__':unittest.main()
