"""Run deterministic first/repeat death routing against actual prepared functions.

Peer-life observations and native action boundaries are synthetic. This does not
simulate two running games, transport timing, native penalty effects or loading.
The separate boss-spectate suite owns the boss-kill and 90-second timeout matrix.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from prepare_game_hardening_regression import function

CONSTANTS = (
    'BbscSpectateReleaseDebounceMs', 'BbscSpectateSafetyMs',
    'BbscGuestWaitHostMs', 'BbscWipeHostSettleMs', 'BbscBossClearedSettleMs',
    'BbscBossBarMemoryMs', 'BbscBossSpectateTimeoutMs', 'BbscDeadWatchdogMs',
    'BbscRepeatDeathMs', 'BbscRepeatDeathTextMs', 'BossGaugePointerOffset',
)
FUNCTIONS = (
    'bool BossFightActive() {', 'void NoteBbscBossBar() {',
    'bool BbscBossFightAtDeath() {',
    'void PS4_SYSV_ABI BbscSpectateDeathHook(',
    'void TickBbscSpectate() {', 'void TickBbscDeadWatchdog() {',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def generate(root):
    root = Path(root)
    source = root / '.build/bbsc/emulator/src/core/bloodborne_re_zh_cn.cpp'
    template_path = root / 'tests/death_entry_paths.cpp.in'
    text, template = (path.read_text('utf-8-sig') for path in (source, template_path))
    constants = []
    for name in CONSTANTS:
        matches = re.findall(r'^constexpr (?:s64|u64) ' + re.escape(name) +
                             r' = [^;\n]+;', text, re.M)
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one production constant: {name}')
        constants.append(matches[0])
    constants.append(function(text, 'enum class BbscDeathMode : u8 {') + ';')
    bodies = [function(text, marker) for marker in FUNCTIONS]
    for marker in ('@CONSTANTS@', '@PRODUCTION@'):
        if template.count(marker) != 1:
            raise ValueError(f'Expected exactly one fixture marker: {marker}')
    generated = template.replace('@CONSTANTS@', '\n'.join(constants)).replace(
        '@PRODUCTION@', '\n\n'.join(bodies))
    provenance = {
        'input_sha256': {str(path): digest(path.read_bytes())
                         for path in (source, template_path)},
        'production_function_sha256': {
            marker: digest(body.encode()) for marker, body in zip(FUNCTIONS, bodies)
        },
        'scope': ('Deterministic single-process route decisions with boundary mocks, '
                  'not two-game, native penalty, save, rendering or load simulation'),
    }
    return generated, provenance


def run(root, output, compiler='clang++'):
    generated, provenance = generate(root)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    compiler_path = Path(compiler)
    if compiler_path.is_absolute():
        env['PATH'] = str(compiler_path.parent) + os.pathsep + env.get('PATH', '')
    cpp = output / 'death_entry_paths.cpp'
    exe = output / ('death_entry_paths.exe' if os.name == 'nt' else 'death_entry_paths')
    cpp.write_text(generated, encoding='utf-8', newline='\n')
    compiled = subprocess.run([compiler, '-std=c++23', '-O1', str(cpp), '-o', str(exe)],
                              capture_output=True, text=True, env=env)
    (output / 'compile.log').write_text(compiled.stdout + compiled.stderr, 'utf-8')
    compiled.check_returncode()
    result = subprocess.run([str(exe)], capture_output=True, text=True, env=env, timeout=45)
    (output / 'run.log').write_text(result.stdout + result.stderr, 'utf-8')
    print(result.stdout + result.stderr, end='', flush=True)
    result.check_returncode()
    for path, expected in provenance['input_sha256'].items():
        if digest(Path(path).read_bytes()) != expected:
            raise RuntimeError('Input changed during test run: ' + path)
    provenance.update(exit_code=result.returncode, generated_sha256=digest(cpp.read_bytes()))
    (output / 'results.json').write_text(json.dumps(provenance, indent=2), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Prepared source repository root')
    parser.add_argument('output', type=Path, help='Isolated build/results directory')
    parser.add_argument('--compiler', default='clang++')
    args = parser.parse_args()
    run(args.root.resolve(), args.output, args.compiler)
