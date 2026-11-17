"""A2 synthetic OS-kill matrix; run only against an explicitly integrated candidate.

Reuses the original Runner process/TerminateProcess/CLI harness, not product
state derivation. Does not claim power-loss or restart coverage.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4
import traceback
import test_save_resolution_interruptions as base

ACTIONS = ('restore-generation-0', 'reimport-ordinary', 'restore-case', 'restart')
LEGACY_ORACLE = base.independent_states


def independent_states(intent):
    action = intent['action']
    if action == 'restore-case':
        return LEGACY_ORACLE(intent)
    assert action in ('restore-generation', 'reimport-ordinary', 'restart'), action
    before = deepcopy(intent['before'])
    retired = dict.fromkeys(base.TREES)
    relative = intent['generation']['relative'] if action == 'restore-generation' else None
    if relative is not None:
        assert relative == '1/CUSA00207/SPRJ0005/userdata0000', relative
        parents = ['/'.join(relative.split('/')[:n]) for n in range(1, len(relative.split('/')))]
        retired['savedata'] = {'directories': sorted(parents), 'files': {}}
    state = {'live': before['trees'], 'gate': before['gate'],
             'stage': deepcopy(intent['target']), 'retired': retired, 'retired_gate': None}
    states = [deepcopy(state)]
    if relative is not None:
        state['retired']['savedata']['files'][relative] = state['live']['savedata']['files'].pop(relative)
        states.append(deepcopy(state))
    else:
        for name in base.TREES:
            if before['trees'][name] is not None:
                state['retired'][name] = state['live'][name]
                state['live'][name] = None
                states.append(deepcopy(state))
    if intent['before']['gate'] is not None:
        state['retired_gate'], state['gate'] = state['gate'], None
        states.append(deepcopy(state))
    if relative is not None:
        # Sparse file installation retains empty stage ancestor directories.
        state['live']['savedata']['files'][relative] = state['stage']['savedata']['files'].pop(relative)
    else:
        assert state['stage']['savedata'] is not None
        state['live']['savedata'], state['stage']['savedata'] = state['stage']['savedata'], None
    states.append(deepcopy(state))
    if intent['new_gate'] is not None:
        raw = base.canonical(intent['new_gate'])
        state['gate'] = {'bytes': len(raw), 'sha256': base.sha(raw)}
        states.append(deepcopy(state))
    return states


def identities(folder):
    return {p.relative_to(folder).as_posix(): (p.stat().st_dev, p.stat().st_ino, p.stat().st_size,
                                            p.stat().st_mtime_ns, base.sha(p.read_bytes()))
            for p in sorted(folder.rglob('*')) if p.is_file()}


class Slice2Runner(base.Runner):
    def __init__(self, evidence, *, restart_all_unknown=False):
        super().__init__(evidence)
        self.restart_all_unknown = restart_all_unknown
        self.context = {}
        self.killed = set()
        self.appdata = self.fixtures/'synthetic-appdata'
        self.appdata.mkdir()
        os.environ['APPDATA'] = str(self.appdata)
        name = 'scripts/test_save_resolution_slice2_interruptions.py'
        self.source_hashes[name] = base.sha((self.repo/name).read_bytes())
        base.save(self.evidence/'source-hashes-before.json', self.source_hashes)

    def command(self, root, trace, pause=-1, hit=None, probe=False):
        command = super().command(root, trace, pause, hit, probe)
        command[2] = str(Path(__file__).resolve())
        return command

    def fixture(self, action):
        root = self.fixtures/(action+'-'+uuid4().hex)
        root.mkdir()  # Normal inherited ACL; no private TemporaryDirectory ACL.
        saves = self.fx.create(root, marker=False)
        ordinary = self.fixtures/(root.name+'-ordinary')
        ordinary.mkdir()
        (ordinary/'Bloodborne.exe').write_bytes(b'synthetic never executed')
        shutil.copytree(root/'data/user/savedata', ordinary/'user/savedata')
        original_source = base.inventory(ordinary/'user/savedata')
        bad = bytearray(self.fx.RAW)
        bad[:64] = b'unsupported-test-save-format'.ljust(64, b'!')
        (saves/'userdata0000').write_bytes(bad)
        if action in ('reimport-ordinary', 'restart'):
            (saves/'backup0000').write_bytes(bad)
        if action == 'restart' and self.restart_all_unknown:
            for stem in ('userdata','backup'):
                (saves/(stem+'0001')).write_bytes(bad)
        if action != 'restore-case':
            marker = root/'data/user/world-recovery/review-required.json'
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(b'opaque synthetic review after normal backup')
            for name in ('own-world','guest-items','shared-progress'):
                self.aux(root, name, action.encode('ascii'))
        # launch_backup copies verified bytes, not a playable-source verdict.
        # Its actual guard checks existing A gate/locks, not these malformed
        # character bytes. Create the unqualified probe backup BEFORE the gate.
        self.s.backups.launch_backup(root)
        backup, = self.s.backups._list(root)[0]
        base.save(root/'synthetic-probe-backup.json', {k: backup[k] for k in ('id','fingerprint')})
        (root/base.GATE).write_bytes(b'opaque pre-existing synthetic gate')
        kwargs = {'ordinary_install': str(ordinary)} if action == 'reimport-ordinary' else {}
        if action == 'restore-case':
            # Produce a real v3 single-slot recovery case, then choose its partial
            # original through the same DTO as the UI. This setup isn't counted
            # as an OS-kill test; the restore-case action below is.
            initial = self.s.plan(root)
            assert 'restore-generation-0' in [row['id'] for row in initial['options']], initial
            done = self.s.apply(root, initial['digest'], 'restore-generation-0', confirmed=True,
                                **self.selection_args(initial['selection']))
            old_case = next(row for row in self.s.list_cases(root)['backups'] if row['id'] == done['case_id'])
            kwargs = self.selection_args(old_case)
        plan = self.s.plan(root, **kwargs)
        assert action in [row['id'] for row in plan['options']], plan
        if action == 'restart':
            assert plan['options'][-1]['id'] == 'restart', plan
            assert not plan['restart_eligibility']['recommended'], plan
        request = {'action': 'apply', 'digest': plan['digest'], 'option_id': action,
                   'confirmed': True, **self.selection_args(plan['selection']), **kwargs}
        if action == 'restart':
            request['restart_confirmed'] = True
        before = base.content(root)
        sentinels = {p:(root/p).read_bytes() for p in ('profile.json','data/user/trophies.log')}
        cases = {p.name for p in (root/'save-backups/recovery-choice').glob('*') if p.is_dir()}
        listed = {row['id'] for row in self.s.list_cases(root)['backups']}
        self.context[str(root)] = {'action': action, 'ordinary': ordinary, 'source_before': original_source,
            'savedata_identity': identities(root/'data/user/savedata'), 'moved': False}
        base.save(root/'synthetic-before-identities.json', self.context[str(root)]['savedata_identity'])
        return root, request, before, sentinels, cases, listed

    def process_end(self, row, process, directory, label, stdout_path, stderr_path):
        super().process_end(row, process, directory, label, stdout_path, stderr_path)
        if label == 'kill-worker':
            self.killed.add(row['fixture'])

    def cli(self, root, request, directory, label, expected=True):
        ctx = self.context.get(str(root))
        request = dict(request)
        if request.get('action') == 'apply' and request.get('option_id') == 'restart':
            request['restart_confirmed'] = True
        if ctx and ctx['action'] == 'reimport-ordinary':
            lock = root/'.save-import.lock'
            if str(root) in self.killed and lock.exists() and not ctx['moved']:
                # Only after publication/OS termination. No reliance on this
                # original path is allowed while resuming the durable stage.
                destination = ctx['ordinary'].with_name(ctx['ordinary'].name+'-offline')
                ctx['ordinary'].rename(destination)
                ctx['ordinary'] = destination
                ctx['moved'] = True
                base.save(directory/'source-made-unavailable.json', {'moved_to':str(destination),
                    'content_unchanged': base.inventory(destination/'user/savedata') == ctx['source_before']})
            if label in ('apply','fresh-plan','fresh-apply') and not ctx['moved']:
                request['ordinary_install'] = str(ctx['ordinary'])
        return super().cli(root, request, directory, label, expected)

    def validate_complete(self, root, before, sentinels, result, directory):
        ctx = self.context[str(root)]
        case = root/'save-backups/recovery-choice'/result['case_id']
        intent_raw = (case/'intent.json').read_bytes()
        intent = json.loads(intent_raw)
        assert intent['before'] == before
        assert base.case_content(case) == before, 'Original five-tree/gate bytes lost'
        assert base.actual(root,case) == independent_states(intent)[-1]
        assert not (root/'.save-import.lock').exists()
        for path,raw in sentinels.items():
            assert (root/path).read_bytes() == raw, path
        assert base.inventory(ctx['ordinary']/'user/savedata') == ctx['source_before'], 'Original installation changed'
        if intent['action'] == 'restore-generation':
            now = identities(root/'data/user/savedata')
            base.save(directory/'single-slot-identities.json', {'before':ctx['savedata_identity'],'after':now})
            changed = '1/CUSA00207/SPRJ0005/userdata0000'
            assert set(now) == set(ctx['savedata_identity'])
            for name,value in ctx['savedata_identity'].items():
                if name != changed:
                    assert now[name] == value, ('Untouched slot metadata/content changed',name)
            assert base.content(root)['trees']['savedata']['files'][changed] == before['trees']['savedata']['files']['1/CUSA00207/SPRJ0005/backup0000']
        elif intent['action'] == 'reimport-ordinary':
            assert base.inventory(root/'data/user/savedata') == ctx['source_before']
        elif intent['action'] == 'restart':
            empty = base.inventory(root/'data/user/savedata')
            assert empty is not None and not empty['files'], empty
            assert all(base.content(root)['trees'][name] is None for name in list(base.TREES)[1:])
            assert not (root/base.GATE).exists()
            normal = self.cli(root, {'action':'plan'}, directory, 'empty-install-plan')
            assert not normal['needed'] and normal['pending'] is None, normal
            # Do not invent a readable empty backup. The empty install remains
            # empty until a game process creates data; no game runs in this matrix.
            base.save(directory/'empty-install-state.json', {'empty_savedata':empty,
                'normal_plan':normal,'game_started':False})
        choices = self.s.list_cases(root)['backups']
        selected = next(row for row in choices if row['id'] == result['case_id'])
        # Completed replay must be a no-op, then the user's public restore-case
        # route must return the entire partial original and show the gate again.
        completed = base.content(root)
        replay = self.cli(root, {'action':'resume','fingerprint':base.sha(intent_raw),'confirmed':True}, directory,'repeat-completed')
        assert base.content(root) == completed and not replay['can_launch']
        plan = self.cli(root, {'action':'plan',**self.selection_args(selected)}, directory,'roundtrip-plan')
        assert 'restore-case' in [row['id'] for row in plan['options']], plan
        restored = self.cli(root, {'action':'apply','digest':plan['digest'],'option_id':'restore-case',
            'confirmed':True,**self.selection_args(plan['selection'])}, directory,'roundtrip-restore')
        assert base.content(root)['trees'] == before['trees']
        assert (root/base.GATE).exists() and restored['needs_review']
        ready = self.cli(root, {'action':'plan'}, directory,'roundtrip-processing-page')
        assert ready['needed'] and ready['pending'] is None and ready['options'], ready
        if intent['action'] == 'restore-case':
            # Restoring the preserved repaired branch now offers a real usable
            # gate exit; no backend success or plan decision is mocked.
            assert 'use-current' in [row['id'] for row in ready['options']], ready
            saved = base.inventory(root/'data/user/savedata')
            used = self.cli(root, {'action':'apply','digest':ready['digest'], 'option_id':'use-current',
                'confirmed':True,**self.selection_args(ready['selection'])}, directory,'roundtrip-use-current')
            assert used['can_launch'] and not (root/base.GATE).exists()
            normal = self.cli(root, {'action':'plan'}, directory,'roundtrip-normal-page')
            assert not normal['needed'] and normal['pending'] is None
            assert base.inventory(root/'data/user/savedata') == saved
        assert base.case_content(case) == before
        assert base.inventory(ctx['ordinary']/'user/savedata') == ctx['source_before']
        summary = {'result':result,'before':before,'intent_sha256':base.sha(intent_raw),
            'independent_prefix_oracle':True,'original_unchanged':True,
            'ordinary_source_unchanged':True,'resume_with_source_unavailable':ctx['moved'],
            'roundtrip_gate_and_processing_page':True,'restart_mode': ('all-unknown' if self.restart_all_unknown else 'partial-readable') if intent['action'] == 'restart' else None}
        for name in ('manifest.json','intent.json','receipt.json'):
            shutil.copyfile(case/name,directory/name)
        base.save(directory/'summary.json',summary)
        return summary

    def cleanup_success(self, root):
        # Evidence includes complete synthetic branches; retain them, do not
        # delete anything, making the single-slot inode oracle independently auditable.
        assert root.resolve().parent == self.fixtures.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence','freeze','root','trace','hit'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--pause',type=int,default=-1)
    parser.add_argument('--dry-run-only',action='store_true')
    parser.add_argument('--restart-only',action='store_true')
    parser.add_argument('--restart-all-unknown',action='store_true')
    args=parser.parse_args()
    if args.worker:
        os.environ['APPDATA']=str(args.root.parent/'synthetic-appdata')
        return base.worker(args)
    if args.evidence is None or os.name != 'nt':
        parser.error('New --evidence directory and Windows required')
    base.independent_states=independent_states
    base.ACTIONS=('restart',) if args.restart_only else ACTIONS
    return Slice2Runner(args.evidence, restart_all_unknown=args.restart_all_unknown).run(args.dry_run_only)


if __name__=='__main__':
    raise SystemExit(main())
