"""OS-terminate every observed A boundary, using synthetic temporary roots only.

Run with the intended Windows Python and --evidence <new empty evidence path>.
Production sources are byte-frozen for the run. Only the no-op checkpoint is
observed/paused; parsing, plans, copies, publication, mutexes and renames are real.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ACTIONS = ('accept-bound', 'accept-all', 'restore-all', 'restore-case', 'use-current', 'preserve', 'abandon')
TREES = {'savedata': 'data/user/savedata', 'world-recovery': 'data/user/world-recovery',
         'own-world': 'mod/own-world', 'guest-items': 'mod/guest-items',
         'shared-progress': 'mod/shared-progress'}
GATE = 'data/user/save-resolution-review.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    with temporary.open('wb') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def file_content(path):
    if not path.exists():
        return None
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': sha(raw)}


def inventory(path):
    if not path.exists():
        return None
    assert path.is_dir(), path
    return {'directories': sorted(p.relative_to(path).as_posix() for p in path.rglob('*') if p.is_dir()),
            'files': {p.relative_to(path).as_posix(): file_content(p)
                      for p in sorted(path.rglob('*')) if p.is_file()}}


def content(root):
    return {'trees': {name: inventory(root / relative) for name, relative in TREES.items()},
            'gate': file_content(root / GATE)}


def case_content(case, area='original'):
    return {'trees': {name: inventory(case / area / name) for name in TREES},
            'gate': file_content(case / area / 'gate')}


def actual(root, case):
    return {'live': content(root)['trees'], 'gate': file_content(root / GATE),
            'stage': {name: inventory(case / 'stage' / name) for name in TREES},
            'retired': {name: inventory(case / 'retired' / name) for name in TREES},
            'retired_gate': file_content(case / 'retired/gate')}


def independent_states(intent):
    """Derive legal prefixes from the explicit fixed-tree move contract."""
    action = intent['action']
    if action == 'abandon':
        state = {'live': deepcopy(intent['before']['trees']), 'stage': dict.fromkeys(TREES),
                 'retired': dict.fromkeys(TREES), 'retired_gate': None, 'gate': intent['before']['gate']}
        final = deepcopy(state)
        raw = canonical(intent['new_gate'])
        final['gate'] = {'bytes': len(raw), 'sha256': sha(raw)}
        return [state, final]
    if action == 'accept-bound':
        retire = intent['bound']['paths']
    else:
        labels = list(TREES) if action in ('restore-all', 'restore-case') else list(TREES)[1:] if action == 'accept-all' else []
        retire = [(name, '') for name in labels if intent['before']['trees'][name] is not None]
    scaffold = dict.fromkeys(TREES)
    for name, relative in retire:
        if relative:
            tree = scaffold[name] or {'directories': [], 'files': {}}
            parts = relative.split('/')
            tree['directories'] = sorted(set(tree['directories']) |
                                         {'/'.join(parts[:n]) for n in range(1, len(parts))})
            scaffold[name] = tree
    state = {'live': deepcopy(intent['before']['trees']), 'gate': deepcopy(intent['before']['gate']),
             'stage': deepcopy(intent['target']), 'retired': scaffold, 'retired_gate': None}
    states = [deepcopy(state)]
    for name, relative in retire:
        tree = state['live'][name]
        if not relative:
            state['live'][name], state['retired'][name] = None, deepcopy(tree)
        else:
            prefix = relative + '/'
            old = state['retired'][name] or {'directories': [], 'files': {}}
            directories = set(old['directories'])
            parts = relative.split('/')
            directories.update('/'.join(parts[:n]) for n in range(1, len(parts)))
            if relative in tree['files']:
                old['files'][relative] = tree['files'].pop(relative)
            else:
                assert relative in tree['directories'], relative
                moved_dirs = [p for p in tree['directories'] if p == relative or p.startswith(prefix)]
                directories.update(moved_dirs)
                for path in list(tree['files']):
                    if path.startswith(prefix):
                        old['files'][path] = tree['files'].pop(path)
                tree['directories'] = [p for p in tree['directories'] if p not in moved_dirs]
            old['directories'] = sorted(directories)
            state['retired'][name] = old
        states.append(deepcopy(state))
    if action != 'preserve' and intent['before']['gate'] is not None:
        state['retired_gate'], state['gate'] = state['gate'], None
        states.append(deepcopy(state))
    for name in TREES:
        if intent['target'][name] is not None:
            assert state['live'][name] is None, name
            state['live'][name], state['stage'][name] = state['stage'][name], None
            states.append(deepcopy(state))
    if intent['new_gate'] is not None:
        raw = canonical(intent['new_gate'])
        state['gate'] = {'bytes': len(raw), 'sha256': sha(raw)}
        states.append(deepcopy(state))
    return states


def load_modules(freeze, root=None):
    sys.path.insert(0, str(freeze))
    import save_resolution as s
    import save_resolution_fixture as fixture
    if root is not None:
        s.ROOT = root
    return s, fixture


def worker(args):
    root = args.root.resolve()
    s, fixture = load_modules(args.freeze, root)
    if args.probe:
        import client
        import conversion_jobs as conversion
        client.ROOT = conversion.ROOT = s.transfer.ROOT = root
        ordinary = root/'synthetic-probe-ordinary'
        fixture.create(ordinary, marker=False)
        (ordinary/'data/user').rename(ordinary/'user')
        for name in ('Bloodborne.exe','bbport.ini','eboot.bin'):
            (ordinary/name).write_bytes(b'synthetic never executed')
        backup = json.loads((root/'synthetic-probe-backup.json').read_bytes())
        profile = {'install': str(ordinary), 'game': str(ordinary), 'graphics_source': str(ordinary/'bbport.ini')}
        def forbidden_game(*args, **kwargs):
            raise AssertionError('A guarded entry reached the external game boundary')
        client.run_game = forbidden_game  # External boundary only; prepare remains real.
        os.environ['APPDATA'] = str(root/'synthetic-empty-appdata')
        probes = {'backup.restore': lambda: s.backups.restore(root, backup['id'], backup['fingerprint'], confirmed=True),
                  'import.apply': lambda: s.transfer.apply(ordinary),
                  'world.copy-evidence': lambda: s.world.export_evidence(root, s.world.evidence_plan(s.world.evidence_inventory(root))),
                  'conversion.dispatch': lambda: conversion.dispatch({'action': 'create'}),
                  'client.run_session': lambda: client.run_session(profile, '', '', None),
                  'world.begin': lambda: s.world.begin(root),
                  'world.finalize': lambda: s.world.finalize(root, None)}
        results = {}
        for name, call in probes.items():
            try:
                with redirect_stdout(io.StringIO()):
                    call()
            except (ValueError, OSError) as error:
                results[name] = {'blocked': isinstance(error, (s.ResolutionRequired, client.SaveResolutionRequired)),
                                 'type': type(error).__name__, 'error': str(error)}
            else:
                results[name] = {'blocked': False}
        print(json.dumps(results, ensure_ascii=False), flush=True)
        return 0
    count = 0
    def checkpoint(name):
        nonlocal count
        row = {'index': count, 'name': name}
        with args.trace.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        if count == args.pause:
            save(args.hit, row)
            while True:
                time.sleep(1)
        count += 1
    s._checkpoint = checkpoint
    return s.main()


class Runner:
    def __init__(self, evidence):
        self.repo = Path(__file__).resolve().parents[1]
        self.evidence = evidence.resolve()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.freeze = self.evidence / 'source-freeze'
        self.freeze.mkdir()
        self.fixtures = self.evidence / 'fixtures'
        self.fixtures.mkdir()
        files = sorted((self.repo / 'launcher').glob('*.py')) + [self.repo / 'tests/save_resolution_fixture.py',
                                                               self.repo / 'tests/synthetic_world_fixture.py']
        self.source_hashes = {str(p.relative_to(self.repo)).replace('\\', '/'): sha(p.read_bytes()) for p in files}
        for path in files:
            shutil.copyfile(path, self.freeze / path.name)
            assert sha((self.freeze / path.name).read_bytes()) == self.source_hashes[path.relative_to(self.repo).as_posix()]
        self.source_hashes['scripts/test_save_resolution_interruptions.py'] = sha(Path(__file__).read_bytes())
        save(self.evidence / 'source-hashes-before.json', self.source_hashes)
        self.s, self.fx = load_modules(self.freeze)
        self.completed = []
        self.current = {}
        self.invocations = []

    def command(self, root, trace, pause=-1, hit=None, probe=False):
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--freeze', str(self.freeze),
                   '--root', str(root), '--trace', str(trace), '--pause', str(pause)]
        if hit is not None:
            command.extend(['--hit', str(hit)])
        if probe:
            command.append('--probe')
        return command

    def process_start(self, process, command, root, directory, label, method):
        row = {'pid': process.pid, 'argv': command, 'fixture': str(root), 'label': label,
               'started_utc': datetime.now(timezone.utc).isoformat(), 'method': method,
               'source_manifest_sha256': sha(canonical(self.source_hashes))}
        save(directory/(label+'-process-start.json'), row)
        self.invocations.append(row)
        save(self.evidence/'process-ledger.json', self.invocations)
        return row

    def process_end(self, row, process, directory, label, stdout_path, stderr_path):
        assert type(process.returncode) is int, 'Missing integer subprocess exit status'
        row.update(ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode,
                   stdout=file_content(stdout_path), stderr=file_content(stderr_path))
        save(directory/(label+'-process-end.json'), row)
        save(self.evidence/'process-ledger.json', self.invocations)

    def invoke(self, root, command, directory, label, request=None):
        directory.mkdir(parents=True, exist_ok=True)
        out, err = directory/(label+'-stdout.json'), directory/(label+'-stderr.log')
        # Both streams go directly to owned files, never sequential pipe reads.
        failure, row = None, None
        with out.open('wb') as stdout, err.open('wb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, text=True, encoding='utf-8',
                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
            try:
                row = self.process_start(process, command, root, directory, label, 'bounded real JSON/CLI child')
                process.communicate(json.dumps(request) if request is not None else None, timeout=60)
            except BaseException as error:
                failure = error
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
        if row is not None:
            if failure is not None:
                row['failure'] = repr(failure)
            self.process_end(row, process, directory, label, out, err)
        if failure is not None:
            raise failure
        return subprocess.CompletedProcess(command, process.returncode,
                                           out.read_text(encoding='utf-8'), err.read_text(encoding='utf-8'))

    def cli(self, root, request, directory, label, expected=True):
        result = self.invoke(root, self.command(root, directory/(label+'-trace.jsonl')), directory, label, request)
        value = json.loads(result.stdout)
        assert value['ok'] is expected and result.returncode == (0 if expected else 1), (label, value, result.returncode)
        return value

    def selection_args(self, selection):
        return {} if selection is None else {'selection_' + k: selection[k] for k in ('kind', 'id', 'fingerprint')}

    def aux(self, root, name, payload=b'opaque synthetic auxiliary data'):
        folder = root / TREES[name]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'empty-directory').mkdir(exist_ok=True)
        (folder / 'opaque-synthetic').write_bytes(payload)

    def fixture(self, action):
        root = Path(tempfile.mkdtemp(prefix=action + '-', dir=self.fixtures)).resolve()
        saves = self.fx.create(root, marker=action != 'accept-bound' and action != 'use-current')
        self.s.backups.launch_backup(root)
        backup, = self.s.backups._list(root)[0]
        save(root/'synthetic-probe-backup.json', {key: backup[key] for key in ('id','fingerprint')})
        selection = None
        if action == 'accept-bound':
            self.fx.request(root, job=True)
        elif action == 'use-current':
            (root / GATE).write_bytes(b'opaque gate over a clean synthetic branch')
            self.fx.choose(root, 'use-current')
            selection, = self.s.list_cases(root)['backups']
            (saves / 'newer-branch').write_bytes(b'synthetic current branch not merged')
            self.fx.choose(root, 'restore-case', selection)
            selection = None
        else:
            if action in ('restore-all', 'restore-case'):
                self.s.backups.launch_backup(root)
                (saves / 'userdata0001').write_bytes(b'partially unreadable synthetic role')
            for name in ('own-world', 'shared-progress'):
                self.aux(root, name, action.encode())
            if action != 'restore-case':
                self.aux(root, 'guest-items', b'unknown guest journal retained byte-for-byte')
            (root / GATE).write_bytes(b'{"root":"Z:/NEVER","path":"../../NOT-EXECUTED"}')
            if action == 'restore-case':
                self.fx.choose(root, 'restore-all')
                selection, = self.s.list_cases(root)['backups']
                # New current branch has different presence/absence from the
                # selected original: guest present; own/shared absent.
                (saves / 'newer-branch').write_bytes(b'new synthetic progress after first A')
                self.aux(root, 'guest-items', b'new current guest-only branch')
                marker = root / TREES['world-recovery'] / 'review-required.json'
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b'new synthetic current world context')
        if action == 'abandon':
            self.fx.make_pending(root)
            (saves/'newer-during-pending').write_bytes(b'current progress after interrupted choice')
            pending = self.s.pending(root)
            assert pending['can_abandon'] and not pending['can_resume'], pending
            request = {'action': 'abandon', 'digest': pending['abandon_digest'], 'confirmed': True}
            before = content(root)
            sentinels = {p: (root/p).read_bytes() for p in ('profile.json', 'data/user/trophies.log')}
            cases = {p.name for p in (root/'save-backups/recovery-choice').iterdir() if p.is_dir()}
            return root, request, before, sentinels, cases, set()
        plan = self.s.plan(root, **self.selection_args(selection))
        assert action in [row['id'] for row in plan['options']], (action, plan)
        request = {'action': 'apply', 'digest': plan['digest'], 'option_id': action, 'confirmed': True,
                   **self.selection_args(plan['selection'])}
        before = content(root)
        sentinels = {p: (root / p).read_bytes() for p in ('profile.json', 'data/user/trophies.log')}
        cases = {p.name for p in (root / 'save-backups/recovery-choice').glob('*') if p.is_dir()}
        listed = {row['id'] for row in self.s.list_cases(root)['backups']}
        return root, request, before, sentinels, cases, listed

    def validate_complete(self, root, before, sentinels, result, directory):
        case = root / 'save-backups/recovery-choice' / result['case_id']
        intent_raw = (case / 'intent.json').read_bytes()
        intent = json.loads(intent_raw)
        assert intent['before'] == before
        assert case_content(case) == before, 'Original five-tree/gate capture changed'
        states = independent_states(intent)
        assert actual(root, case) == states[-1], 'Final fixed tree/gate state differs from the chosen contract'
        assert not (root / '.save-import.lock').exists(), 'Completed choice retained lock'
        if intent['action'] == 'abandon':
            assert (case/'retired/completed-lock').read_bytes() == intent_raw
            assert file_content(case/'original/control/import-lock') == intent['previous_lock']
            assert content(root)['trees'] == before['trees']
            assert result['can_launch'] is False and result['needs_review']

        for path, raw in sentinels.items():
            assert (root / path).read_bytes() == raw, path
        for name in ('manifest.json', 'intent.json', 'receipt.json'):
            shutil.copyfile(case / name, directory / name)
        assert result['case_id'] in {row['id'] for row in self.s.list_cases(root)['backups']}
        summary = {'result': result, 'before': before, 'after': content(root),
                   'original': case_content(case), 'retired': case_content(case, 'retired'),
                   'stage': case_content(case, 'stage'), 'intent_sha256': sha(intent_raw),
                   'sentinels': {name: sha(raw) for name, raw in sentinels.items()}}
        if intent['action'] == 'use-current':
            env = self.s.world.begin(root)
            assert env['THEHUNT_WORLD_RECOVERY_TOKEN']
            summary['real_world_begin_after_use_current'] = True
        # A completed fingerprint is never permission to overwrite later data.
        later = root / 'data/user/savedata/after-completion-synthetic-progress'
        later.write_bytes(b'uncontrolled later synthetic progress must not be overwritten')
        later_state = content(root)
        repeated = self.cli(root, {'action': 'resume', 'fingerprint': sha(intent_raw), 'confirmed': True},
                            directory, 'repeat-completed')
        assert repeated['can_launch'] is False and content(root) == later_state
        summary['repeat_completed_preserved_new_progress'] = True
        save(directory / 'summary.json', summary)
        return summary

    def cleanup_success(self, root):
        root = root.resolve()
        assert root.parent == self.fixtures.resolve() and root.is_dir()
        assert any(root.name.startswith(action + '-') for action in ACTIONS)
        shutil.rmtree(root)  # Only this run's newly created successful synthetic fixture.

    def dry_run(self, action):
        assert not self.source_after(), 'Worktree input changed; stop rather than mix source revisions'
        directory = self.evidence / action / 'dry-run'
        directory.mkdir(parents=True)
        root, request, before, sentinels, _, _ = self.fixture(action)
        self.current = {'action': action, 'point': 'dry-run', 'fixture': str(root)}
        result = self.cli(root, request, directory, 'apply')
        checkpoints = [json.loads(line) for line in (directory / 'apply-trace.jsonl').read_text(encoding='utf-8').splitlines()]
        assert checkpoints and len({row['name'] for row in checkpoints}) == len(checkpoints)
        self.validate_complete(root, before, sentinels, result, directory)
        save(directory / 'checkpoints.json', checkpoints)
        self.cleanup_success(root)
        return checkpoints

    def crash(self, action, checkpoint):
        assert not self.source_after(), 'Worktree input changed; stop rather than mix source revisions'
        index, name = checkpoint['index'], checkpoint['name']
        directory = self.evidence / action / ('point-%03d' % index)
        directory.mkdir(parents=True)
        root, request, before, sentinels, old_cases, old_listed = self.fixture(action)
        self.current = {'action': action, 'point': name, 'index': index, 'fixture': str(root)}
        hit, trace = directory / 'hit.json', directory / 'apply-trace.jsonl'
        command = self.command(root, trace, index, hit)
        stdout_path, stderr_path = directory/'killed-stdout.log', directory/'killed-stderr.log'
        stdout_file, stderr_file = stdout_path.open('wb'), stderr_path.open('wb')
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                stdout=stdout_file, stderr=stderr_file, text=True, encoding='utf-8',
                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
        except BaseException:
            stdout_file.close(); stderr_file.close()
            raise
        row = None
        try:
            row = self.process_start(process, command, root, directory, 'kill-worker',
                                     'TerminateProcess at annotated boundary; not power loss')
            process.stdin.write(json.dumps(request))
            process.stdin.close()
            process.stdin = None
            deadline = time.monotonic() + 60
            while not hit.exists():
                assert process.poll() is None, ('Exited before selected real boundary', process.communicate())
                assert time.monotonic() < deadline, 'Boundary handshake timeout'
                time.sleep(.02)
            observed = json.loads(hit.read_text(encoding='utf-8'))
            normalize = lambda point: re.sub(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', '<transaction>', point)
            assert observed['index'] == index and normalize(observed['name']) == normalize(name), (observed, checkpoint)
            if name in ('before:preserve', 'after:lock', 'before:abandon-preserve', 'after:abandon-lock'):
                second = self.cli(root, {'action': 'plan'}, directory, 'concurrent-second-A', expected=False)
                assert '另一个游戏会话或存档操作' in second['error']
            save(directory/'process.json', {'pid': process.pid, 'argv': self.command(root, trace, index, hit),
                'observed': observed, 'method': 'TerminateProcess at annotated boundary; not power loss'})
            process.terminate()  # TerminateProcess, no Python exception/context unwinding.
            stdout, stderr = process.communicate(timeout=10)
            assert type(process.returncode) is int and process.returncode != 0, 'OS termination did not interrupt the worker'
            save(directory/'process-exit.json', {'pid': process.pid, 'exit_code': process.returncode})
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
            stdout_file.close(); stderr_file.close()
            if row is not None:
                self.process_end(row, process, directory, 'kill-worker', stdout_path, stderr_path)
        case_root = root / 'save-backups/recovery-choice'
        new_cases = [p for p in case_root.iterdir() if p.name not in old_cases]
        assert len(new_cases) == 1, new_cases
        interrupted_case = new_cases[0]
        save(directory / 'interrupted-state.json', {'live': content(root),
            'original': case_content(interrupted_case), 'stage': case_content(interrupted_case, 'stage'),
            'retired': case_content(interrupted_case, 'retired'), 'lock': file_content(root / '.save-import.lock')})
        lock = root / '.save-import.lock'
        published = lock.exists()
        new_active = published and json.loads(lock.read_bytes()).get('transaction') == interrupted_case.name
        if action == 'abandon' and published and not new_active:
            assert content(root) == before, 'Pre-handoff abandonment changed current five trees/gate'
            plan = self.cli(root, {'action':'plan'}, directory, 'fresh-abandon-plan')
            assert plan['pending']['can_abandon']
            result = self.cli(root, {'action':'abandon','digest':plan['pending']['abandon_digest'],'confirmed':True}, directory,'fresh-abandon')
            assert result['case_id'] != interrupted_case.name
        elif new_active:
            intent_raw = lock.read_bytes()
            intent = json.loads(intent_raw)
            assert (interrupted_case / 'intent.json').read_bytes() == intent_raw
            assert case_content(interrupted_case) == before
            positions = [i for i, state in enumerate(independent_states(intent)) if state == actual(root, interrupted_case)]
            assert len(positions) == 1, ('Not a unique legal filesystem prefix', positions)
            plan = self.cli(root, {'action': 'plan'}, directory, 'fresh-plan')
            assert plan['pending']['can_resume'], plan
            assert plan['pending']['fingerprint'] == sha(intent_raw)
            # Probe all other actual entry points after the dead A relinquishes
            # the OS mutex but its immutable on-disk intent must still block.
            if name in ('after:lock', 'after:abandon-lock'):
                probe = self.invoke(root, self.command(root, directory/'probe-trace.jsonl', probe=True),
                                    directory, 'entry-probes')
                (directory / 'dead-A-blockers.json').write_text(probe.stdout, encoding='utf-8')
                assert probe.returncode == 0, probe.stderr
                assert all(row['blocked'] for row in json.loads(probe.stdout).values())
                assert case_content(interrupted_case) == before
                assert len([v for v in independent_states(intent) if v == actual(root, interrupted_case)]) == 1
            result = self.cli(root, {'action': 'resume', 'fingerprint': sha(intent_raw), 'confirmed': True},
                              directory, 'resume')
            assert result['case_id'] == interrupted_case.name
        elif name in ('after:unlock', 'after:abandon-unlock'):
            intent_raw = (interrupted_case / 'intent.json').read_bytes()
            result = self.cli(root, {'action': 'resume', 'fingerprint': sha(intent_raw), 'confirmed': True},
                              directory, 'already-completed')
            assert result['case_id'] == interrupted_case.name and result['can_launch'] is False
        else:
            assert content(root) == before, 'Pre-publication interruption changed live trees/gate'
            for tree, value in case_content(interrupted_case)['trees'].items():
                if value is not None:
                    assert before['trees'][tree] is not None
                    assert set(value['directories']) <= set(before['trees'][tree]['directories'])
                    assert all(before['trees'][tree]['files'].get(n) == v for n,v in value['files'].items()), ('Partial preservation corruption',tree)
            assert {row['id'] for row in self.s.list_cases(root)['backups']} == old_listed
            plan = self.cli(root, {'action': 'plan', **{k: v for k, v in request.items() if k.startswith('selection_')}},
                            directory, 'fresh-plan')
            assert plan['pending'] is None and action in [row['id'] for row in plan['options']]
            result = self.cli(root, {'action': 'apply', 'digest': plan['digest'], 'option_id': action,
                'confirmed': True, **self.selection_args(plan['selection'])}, directory, 'fresh-apply')
            assert result['case_id'] != interrupted_case.name
            assert interrupted_case.name not in {row['id'] for row in self.s.list_cases(root)['backups']}
        summary = self.validate_complete(root, before, sentinels, result, directory)
        completed = {'action': action, 'checkpoint': checkpoint, 'os_terminated': True,
                     'lock_published_at_interruption': published, 'new_intent_active': new_active,
                     'worker_pid': process.pid, 'worker_exit_code': process.returncode, 'case_id': result['case_id'],
                     'intent_sha256': summary['intent_sha256']}
        save(directory / 'result.json', completed)
        self.completed.append(completed)
        save(self.evidence / 'progress.json', {'completed': self.completed, 'count': len(self.completed)})
        print(json.dumps(completed), flush=True)
        self.cleanup_success(root)

    def source_after(self):
        after = {name: sha((self.repo / name).read_bytes()) for name in self.source_hashes}
        save(self.evidence / 'source-hashes-after.json', after)
        changed = [name for name in after if after[name] != self.source_hashes[name]]
        return changed

    def run(self, dry_run_only=False):
        try:
            matrix = {action: self.dry_run(action) for action in ACTIONS}
            save(self.evidence / 'checkpoint-matrix.json', matrix)
            if dry_run_only:
                result = {'all_required_passed': False, 'harness_dry_run_completed': True,
                          'actions': len(matrix), 'required_boundaries': sum(map(len, matrix.values())),
                          'terminated_passed': 0, 'source_worktree_changed_during_run': self.source_after()}
                save(self.evidence / 'RESULT.json', result)
                print(json.dumps(result), flush=True)
                return 0
            for action, checkpoints in matrix.items():
                for checkpoint in checkpoints:
                    self.crash(action, checkpoint)
            changed = self.source_after()
            assert not changed, ('Worktree sources changed before final verification', changed)
            result = {'all_required_passed': True, 'actions': len(matrix),
                      'required_boundaries': sum(map(len, matrix.values())), 'terminated_passed': len(self.completed),
                      'source_worktree_changed_during_run': changed,
                      'source_frozen': True, 'failures': 0, 'game_started': False,
                      'real_tasklist_and_mutex': os.name == 'nt',
                      'recorded_child_processes': len(self.invocations)}
            save(self.evidence / 'RESULT.json', result)
            print(json.dumps(result), flush=True)
            return 0
        except BaseException as error:
            result = {'all_required_passed': False, 'current': self.current, 'terminated_passed': len(self.completed),
                      'error': str(error), 'traceback': traceback.format_exc(),
                      'source_worktree_changed_during_run': self.source_after(),
                      'failed_fixture_preserved': True}
            save(self.evidence / 'RESULT.json', result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--freeze', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--hit', type=Path)
    parser.add_argument('--pause', type=int, default=-1)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--dry-run-only', action='store_true')
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    if args.evidence is None:
        parser.error('--evidence is required and must not already exist')
    if os.name != 'nt':
        parser.error('This independent runner requires Windows real tasklist/named-mutex behavior.')
    return Runner(args.evidence).run(args.dry_run_only)


if __name__ == '__main__':
    raise SystemExit(main())
