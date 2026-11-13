"""Compile actual wipe helpers against synthetic memory/callbacks (no game bytes)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def function(text, marker):
    start = text.index(marker)
    at = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Prepared source repository root')
    parser.add_argument('output', type=Path, help='Isolated build/results directory')
    parser.add_argument('--compiler', default='clang++')
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    core = root / '.build/bbsc/emulator/src/core'
    relay = root / '.build/bbsc/emulator/mod/bbsc_relay.cpp'
    game = core / 'bloodborne_re_zh_cn.cpp'
    helper_path = core / 'bbsc_wipe_respawn.inc'
    templates = {name: root / f'tests/wipe_{name}.cpp.in' for name in ('lease', 'request', 'descriptor')}
    paths = [relay, game, helper_path, core / 'bbsc_visibility_wire.h', core / 'bbsc_relay_auth_guard.h', *templates.values()]
    inputs = {str(p): digest(p.read_bytes()) for p in paths}
    relay_text, game_text, helper = (p.read_text('utf-8-sig') for p in (relay, game, helper_path))
    extractions = []

    def actual(text, marker, path):
        body = function(text, marker)
        extractions.append({'source': str(path), 'marker': marker, 'sha256_lf': digest(body.encode())})
        return body

    lease = actual(relay_text, 'bool RelayAuthUseVisibilityContext(', relay)
    context = actual(relay_text, 'bool RelayAuthVisibilityContext(', relay)
    request = actual(game_text, 'bool RequestBbscSessionWarp(', game)
    pins = []
    total_pin_bytes = 0
    pattern = r'static constexpr std::array<u8, (\d+)> (Pin\d+)\{([^}]+)\};\s*if \(!HasMemoryAccess\(image_base \+ (0x[0-9A-Fa-f]+),'
    for match in re.finditer(pattern, helper):
        size, name, contents, offset = match.groups()
        if len(contents.split(',')) != int(size):
            raise ValueError('Unexpected native-layout pin length')
        total_pin_bytes += int(size)
        pins += [f'    const std::array<u8, {size}> {name}{{{contents}}};',
                 f'    Install(image_base + {offset}, {name}.data(), {name}.size());']
    if len(pins) != 6 or total_pin_bytes != 179:
        raise ValueError('Unexpected helper pin set: expected exactly 179 non-executed layout bytes')
    replacements = {
        'lease': {'// ACTUAL_FUNCTION': lease},
        'request': {'// ACTUAL_FUNCTION': request},
        'descriptor': {'// ACTUAL_CONTEXT_FUNCTIONS': context + '\n' + lease,
                       '// ACTUAL_HELPER': helper, '// ACTUAL_PINS': '\n'.join(pins)},
    }
    env = os.environ.copy()
    compiler = Path(args.compiler)
    if compiler.is_absolute():
        env['PATH'] = str(compiler.parent) + os.pathsep + env.get('PATH', '')
    results = []
    for name in ('lease', 'request', 'descriptor'):
        text = templates[name].read_text('utf-8-sig')
        for marker, replacement in replacements[name].items():
            if text.count(marker) != 1:
                raise ValueError('Unexpected template marker: ' + marker)
            text = text.replace(marker, replacement, 1)
        cpp = output / f'wipe_{name}.cpp'
        cpp.write_text(text, 'utf-8', newline='\n')
        exe = cpp.with_suffix('.exe' if os.name == 'nt' else '')
        compiled = subprocess.run([args.compiler, '-std=c++23', '-O1', '-pthread', '-I', str(core),
                                   str(cpp), '-o', str(exe)], capture_output=True, text=True, env=env)
        (output / f'{name}-compile.log').write_text(compiled.stdout + compiled.stderr, 'utf-8')
        compiled.check_returncode()
        run = subprocess.run([str(exe)], capture_output=True, text=True, env=env, timeout=45)
        (output / f'{name}-run.log').write_text(run.stdout + run.stderr, 'utf-8')
        print(run.stdout + run.stderr, end='', flush=True)
        results.append({'fixture': name, 'exit_code': run.returncode, 'generated_sha256': digest(cpp.read_bytes())})
        run.check_returncode()
    for path in paths:
        if digest(path.read_bytes()) != inputs[str(path)]:
            raise RuntimeError('Input changed during test run: ' + str(path))
    (output / 'results.json').write_text(json.dumps({
        'input_sha256': inputs, 'extractions': extractions, 'results': results,
        'scope': 'Actual production functions and helper, synthetic native memory/callbacks/auth client. Descriptor template contains only 179 non-executed layout-pin bytes already in helper; no native executable bytes. No claim of real native constructor/resolver equivalence or gameplay acceptance.'
    }, indent=2), 'utf-8')
    print('PASS: wipe lease, request ordering/cancellation, and synthetic descriptor lifecycle guards')


if __name__ == '__main__':
    main()
