"""Compile actual join observer/adapter with synthetic native memory and bounded hooks."""
import argparse, hashlib, importlib.util, json, os, pathlib, subprocess

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[1]);ap.add_argument('--output',type=pathlib.Path,required=True);ap.add_argument('--compiler',default='clang++');a=ap.parse_args()
    root=a.root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    files=[root/'patches/bbsc_join_cause.inc',root/'patches/bbsc_join_cause_adapter.inc',root/'scripts/join_cause_diagnostics.py',root/'tests/join_cause.cpp.in']
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    env=os.environ.copy();compiler=pathlib.Path(a.compiler);env['PATH']=str(compiler.resolve().parent)+os.pathsep+env.get('PATH','')
    records=[]
    def run(cmd):
        r=subprocess.run([str(x) for x in cmd],capture_output=True,text=True,env=env);records.append({'command':[str(x) for x in cmd],'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr});print(r.stdout,r.stderr,end='');assert r.returncode==0,records[-1]
    for disabled in (False,True):
        exe=out/('disabled.exe' if disabled else 'observer.exe')
        run([a.compiler,'-x','c++','-std=c++20','-O2','-pthread',*(['-DBBSC_J4_DIAG_DISABLED'] if disabled else []),'-I',root/'patches',root/'tests/join_cause.cpp.in','-o',exe])
        for mode in (['disabled'] if disabled else ['disabled','pin','unreadable','partial','normal']):run([exe,mode])
    spec=importlib.util.spec_from_file_location('join_generator',files[2]);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    fixtures=[(mod.apply_core,'bool RelayAuthPrivate();\nbool BbscConnectFirstEnabled() {\n}\n    InstallBbscBannerHook();\n}'),(mod.apply_http,'#include "bbsc_j4_diag.inc"\n#endif'),(mod.apply_j4,'inline std::atomic<unsigned> activeWindows{0};\n        Summary(*it,it->end);')]
    for fn,original in fixtures:
        changed=fn(original);assert changed!=original and fn(changed)==changed
        try:fn('unexpected')
        except ValueError:pass
        else:raise AssertionError('missing anchor accepted')
    for fn,original in fixtures:
        changed=fn(original)
        anchors=(["    if (installed) InstallBbscJoinCauseHooks();","bool J4JoinCauseEnabled(bool) noexcept;"] if fn==mod.apply_core else (["#include \"bbsc_j4_diag.inc\""] if fn==mod.apply_http else ["        if (const auto close=joinCauseClose.load(std::memory_order_acquire)) close(it->id);"]))
        for anchor in anchors:
            try:fn(changed.replace(anchor,''))
            except ValueError:pass
            else:raise AssertionError('partial application accepted')
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==hashes[str(p.relative_to(root))] for p in files)
    report={'pass':True,'hashes':hashes,'runs':records,'generator_idempotent_and_bad_anchor_rejection':True,'limits':['Synthetic native data; no real game/session executed','Actual observer/adapter, OS full-copy reads; allocator/log sink boundary stubs','Real trampoline execution is a separate independent review','Microbenchmark is observer-only, not whole-game or hook-trampoline cost']}
    (out/'results.json').write_text(json.dumps(report,indent=2),'utf-8');print('canonical suite PASS')
if __name__=='__main__':main()
