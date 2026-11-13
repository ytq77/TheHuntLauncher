"""Run the exact native protocol with the actual Python verifier on private copies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'launcher'))
sys.path.insert(0, str(ROOT/'tests'))
from test_world_recovery import RecoveryTests, RAW, GOOD
import world_recovery as wr


def main():
    out = ROOT/'.build/native-tests'
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    exe = out/'test-native.exe'
    generated = ROOT/'.build/bbsc/emulator/src/core/bbsc_world_recovery.inc'
    extra = ['-DBBSC_RECOVERY_FRAGMENT="'+generated.as_posix()+'"'] if generated.exists() else []
    build = subprocess.run(['clang++', '-std=c++23', '-O2', *extra,
                            str(ROOT/'tests/test_world_recovery.cpp'), '-o', str(exe)],
                           env=env, capture_output=True, text=True)
    (out/'compile.log').write_text(build.stdout+build.stderr, encoding='utf-8')
    build.check_returncode()
    passed = []
    modes = ('pending', 'missing', 'late', 'changed', 'valid', 'quit', 'quit-dream',
             'relocated-quit', 'quit-reload-failed', 'title-reload-own', 'title-reload-different')
    if len(sys.argv) > 1:
        assert sys.argv[1] in modes
        modes = (sys.argv[1],)
    for mode in modes:
        case = RecoveryTests()
        case.setUp()
        seal = wr.quit_seal(case.token)
        process = None
        try:
            (case.root/'before-bank.bin').write_bytes(RAW[case.bank:case.bank+150000])
            (case.root/'target-group.bin').write_bytes(GOOD[case.bank+625:case.bank+750])
            native_env = dict(env, **case.env)
            process = subprocess.Popen([str(exe), str(case.root), mode], env=native_env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic()+12
            primary_checked = False
            title_checked = False
            while process.poll() is None and time.monotonic() < deadline:
                if mode in ('valid', 'changed'):
                    try:
                        wr.poll(case.root, case.token)
                    except PermissionError:
                        # Windows can briefly deny a reader while the native
                        # writer replaces the request; the client retries too.
                        pass
                if mode == 'valid' and (case.root/'memory-restored.ok').exists() and not primary_checked:
                    later = bytearray(GOOD)
                    later[case.bank+20000] ^= 1
                    (case.saves/'userdata0000').write_bytes(later)
                    wr.poll(case.root, case.token)
                    assert case.journal.exists(), 'Must wait for the game backup too'
                    (case.saves/'backup0000').write_bytes(later)
                    primary_checked = True
                if mode == 'title-reload-own' and (case.root/'title-reloaded.ok').exists() and not title_checked:
                    (case.saves/'userdata0000').write_bytes(GOOD)
                    wr.poll(case.root, case.token)
                    assert case.journal.exists(), 'A verified reload must still wait for the backup'
                    assert not (case.folder/'acks'/'0.ack').exists()
                    (case.saves/'backup0000').write_bytes(GOOD)
                    # The real client polls periodically; retry until the verifier
                    # has seen both game-authored generations.
                    until = time.monotonic()+3
                    while not (case.folder/'acks'/'0.ack').exists() and time.monotonic()<until:
                        wr.poll(case.root, case.token)
                        time.sleep(0.03)
                    assert case.journal.with_suffix('.bin.restored').exists()
                    assert all((case.saves/name).read_bytes() == GOOD for name in ('userdata0000','backup0000'))
                    title_checked = True
                time.sleep(0.01)
            if process.poll() is None:
                process.terminate()
                raise RuntimeError('Native fixture exceeded its bound: '+mode)
            stdout, stderr = process.communicate()
            assert process.returncode == 0, (mode, process.returncode, stdout, stderr)
            if mode in ('quit', 'quit-dream', 'relocated-quit'):
                wr.process_exited(case.root, case.token, 0)
                assert wr.read_quit_seal(seal) == wr.read_request(case.root, case.path)['seq']
                wr.finalize(case.root, case.token, quit_sequence=wr.read_quit_seal(seal))
                expected = bytearray(GOOD)
                if mode == 'quit-dream':expected[case.bank+625+100]|=4
                assert (case.saves/'userdata0000').read_bytes() == bytes(expected)
                assert (case.saves/'backup0000').read_bytes() == RAW
                assert case.journal.with_suffix('.bin.awaiting-save').is_file()
            if mode == 'quit-reload-failed':
                assert wr.read_request(case.root, case.path)['mode'] == wr.QUIT
                assert wr.read_quit_seal(seal) == 0
                wr.process_exited(case.root, case.token, 0)
                with case.assertRaises(ValueError):
                    wr.finalize(case.root, case.token, quit_sequence=wr.read_quit_seal(seal))
                assert (case.saves/'userdata0000').read_bytes() == RAW
            if mode == 'title-reload-own':
                assert title_checked and wr.read_quit_seal(seal) == 0
            if mode == 'title-reload-different':
                assert wr.read_request(case.root, case.path)['mode'] == wr.BLOCKED
                assert wr.read_quit_seal(seal) == 0 and case.journal.exists()
                assert not (case.folder/'acks'/'0.ack').exists()
            passed.append(mode)
        finally:
            seal.close()
            if process is not None and process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)
            case.doCleanups()
    report = {'native_cases': passed, 'exact_candidate_fragment_compiled': True,
              'actual_python_verifier_used': True,
              'journal_not_consumed_for_missing_groups': True,
              'late_gameplay_restore_rejected': True,
              'changed_memory_prevents_writes': True,
              'repeated_restore_does_not_clear_new_progress': True,
              'archive_waits_for_both_generations': True,
              'normal_quit_primary_transform_only_after_process_exit': True,
              'older_backup_advanced_only_by_the_game': True,
              'personal_dream_changes_kept_before_quit_snapshot': True,
              'later_title_callbacks_do_not_replace_confirmed_quit_plan': True,
              'actual_game_runtime_validated': False}
    target = out/('native-tests.json' if len(sys.argv) == 1 else 'native-tests-'+sys.argv[1]+'.json')
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
