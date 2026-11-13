"""Run real native hooks and extracted join guards using disposable memory/files."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from prepare_game_hardening_regression import function

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([str(value) for value in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT/'.build/bbsc')
    parser.add_argument('--port', type=Path, default=Path(os.environ.get('BBSC_PORT_SOURCE', ROOT/'.build/bbport')))
    args = parser.parse_args()
    source = args.source.resolve()
    core = source/'emulator/src/core'
    text = (core/'bloodborne_re_zh_cn.cpp').read_text('utf-8')
    guard = '\n'.join(function(text, marker) for marker in (
        'bool BbscOwnWorldCaptureStillCurrent() {', 'bool BbscOwnWorldReadyForGuest() {'))
    trace = function(text, 'void PS4_SYSV_ABI TraceEntry(')
    start = trace.index('    if (site.kind == TraceKind::LuaSessionEvent) {')
    # The handler's outer brace follows the nested invocation guard.
    end = trace.index('\n    }', start)+len('\n    }')
    template = (ROOT/'tests/save_hardening_join.cpp.in').read_text('utf-8')
    template = template.replace('// ACTUAL_BASELINE_GUARDS', guard).replace(
        '// ACTUAL_JOIN_DISPATCH', trace[start:end])
    out = ROOT/'.build/save-hardening-tests'
    out.mkdir(parents=True, exist_ok=True)
    join = out/'join.cpp'
    join.write_text(template, encoding='utf-8', newline='\n')
    compiler = 'clang++'
    common = ['-std=c++23', '-O2', '-g', '-fms-extensions', '-DWIN32_LEAN_AND_MEAN', '-DNOMINMAX']
    for name, path in (('active-save', ROOT/'tests/test_active_save.cpp'), ('join', join)):
        exe = out/(name+'.exe')
        run(compiler, *common, path, '-o', exe)
        if name == 'active-save':
            with tempfile.TemporaryDirectory(dir=out) as folder:
                run(exe, folder)
        else:
            run(exe)
    hook = out/'hook-guard.exe'
    run(compiler, *common, '-I'+str(source/'emulator/mod/shim'),
        '-I'+str(source/'emulator/src'), '-I'+str(args.port/'gpu/third_party/xbyak'),
        ROOT/'tests/test_hook_guard.cpp', source/'emulator/mod/bbsc_hooks.cpp', '-o', hook)
    run(hook)


if __name__ == '__main__':
    main()
