"""Exercise the release character reader and restore list APIs on disposable fixture saves."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch

import save_resolution_fixture as fixture


def character(name, level, echoes):
    from engine.launcher import bbport_character as reader
    raw = bytearray(fixture.RAW)
    role = fixture.resolution.offline_save.parse(raw)
    # Same input contract as test_ordinary_source.test_big_endian_names_match_import_and_a_processing_page.
    raw[role.username:role.username+32] = name.encode('utf-16-be').ljust(32, bytes(1))
    assert fixture.resolution.transfer.character_name(raw, role) == name
    at = role.face - reader.FACE_TO_NAME
    for (_, offset), value in zip(reader.ATTRIBUTES, [level]+[10]*5):
        struct.pack_into('<I', raw, at+offset, value)
    struct.pack_into('<II', raw, at+reader.LEVEL, level, echoes)
    return bytes(raw)


def check(release, candidate, output):
    sys.path.append(str(candidate))
    from engine.launcher import bbport_character as reader
    spec = importlib.util.spec_from_file_location('released_reader', release/'engine/launcher/bbport_character.py')
    original_reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original_reader)
    resolution = fixture.resolution
    backups = resolution.backups
    empty = bytes(len(fixture.RAW))

    with tempfile.TemporaryDirectory(prefix='hunt-character-preview-') as temp, patch.object(backups.transfer, 'require_stopped'):
        root = Path(temp)
        game = fixture.create(root, marker=False)

        def pair(slot, primary, backup):
            for stem, raw in (('userdata', primary), ('backup', backup)):
                if raw is not None:
                    (game/f'{stem}{slot:04}').write_bytes(raw)

        first = character('猎人甲', 33, 106)
        old = original_reader.read(first)
        assert (old.level, old.echoes) == (33, 106) and old.name != '猎人甲'
        assert (reader.read(first).name, reader.read(first).summary()) == (old.name, old.summary())
        import backup_characters
        with patch('engine.launcher.bbport_character', original_reader):
            stock_progress = backup_characters._character(first)
        assert [stock_progress[key] for key in ('name', 'level', 'echoes')] == ['猎人甲', 33, 106]
        pair(0, first, first)
        pair(1, empty, empty)
        first_id = Path(backups.launch_backup(root)['backup']).name
        selected = backups._backup(root, first_id)
        previous = character('旧月', 16, 0)
        pair(0, previous, previous)
        backups.restore(root, first_id, selected['fingerprint'], confirmed=True)
        pair(0, empty, empty)
        empty_id = Path(backups.launch_backup(root)['backup']).name

        sealed = character('封存', 41, 777)
        spare = character('晓', 18, 91)
        pair(0, sealed, sealed)
        pair(1, first, spare)
        pair(2, empty, None)
        pair(3, None, spare)
        pair(4, bytes(30), None)
        pair(5, empty, spare)
        pair(6, bytes(4)+first[4:], spare)
        same_numbers = bytearray(first)
        same_numbers[8] ^= 1
        pair(7, first, same_numbers)
        unknown_stats = bytearray(first)
        at = first.find(b'FACE', 0xF000)-reader.FACE_TO_NAME
        struct.pack_into('<I', unknown_stats, at+reader.LEVEL, 999)
        pair(8, unknown_stats, None)
        pair(9, b'', None)
        mixed_id = Path(backups.launch_backup(root)['backup']).name
        marker = root/'data/user/world-recovery/review-required.json'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('synthetic review marker', 'utf-8')
        case_id = fixture.choose(root, 'restore-all', selected)['case_id']
        current = character('当前', 24, 999)
        pair(0, current, current)
        another_face = bytearray(first)
        another_face[resolution.offline_save.parse(first).face+4] ^= 1
        pair(1, first, another_face)
        pair(2, first, character('猎人甲', 33, 0))
        pair(3, unknown_stats, unknown_stats)
        pair(4, first, character('猎人甲', 34, 106))
        rare = character('𠀀猎', 19, 28)
        assert original_reader.read(rare) is None and reader.read(rare) is None
        pair(5, rare, rare)
        bad_name = bytearray(first)
        at = resolution.offline_save.parse(first).username
        bad_name[at:at+32] = bytes.fromhex('d800') + bytes(30)
        pair(6, bad_name, bad_name)
        pair(7, first, same_numbers)
        pair(8, b'bad', spare)
        named_unknown = character('姓名未识别', 11, 9)
        pair(9, named_unknown, named_unknown)

        before = backups.transfer.snapshot(root)
        ordinary = backups.overview(root)
        special = resolution.dispatch(root, {'action': 'list'})
        case = next(r for r in special['backups'] if r['id'] == case_id)
        request = {'selection_'+k: case[k] for k in ('kind','id','fingerprint')}
        explicit_plan = resolution.dispatch(root, {'action': 'plan', **request})
        original_view = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import save_backups as b, save_resolution as s
root = Path(sys.argv[2])
print(json.dumps([b.overview(root), s.list_cases(root), s.plan(root), s.plan(root, **json.loads(sys.argv[3]))]))
"""
        original = subprocess.check_output([sys.executable, '-I', '-B', '-c', original_view, str(release), str(root), json.dumps(request)])
        before_ordinary, before_special, before_plan, before_explicit = json.loads(original)
        for actual, expected in ((ordinary, before_ordinary), (special, before_special)):
            without_display = [{k: v for k, v in row.items() if k != 'character_preview'} for row in actual['backups']]
            assert {**actual, 'backups': without_display} == expected
        for actual_plan, release_plan in ((resolution.plan(root), before_plan), (explicit_plan, before_explicit)):
            assert {k: v for k, v in actual_plan.items() if k not in ('character_preview','backup_view','import_view')} == release_plan
        assert explicit_plan['backup_view'] == case
        assert backups.transfer.snapshot(root) == before

        launch = next(r for r in ordinary['backups'] if r['id'] == first_id)
        prior = next(r for r in ordinary['backups'] if r['kind'] == 'pre-restore')
        mixed = next(r for r in ordinary['backups'] if r['id'] == mixed_id)
        vacant = next(r for r in ordinary['backups'] if r['id'] == empty_id)
        assert vacant['character_preview'] == []
        assert launch['character_preview'] == [dict(slot=0, source='both', name='猎人甲', level=33, echoes=106)]
        assert prior['character_preview'] == [dict(slot=0, source='both', name='旧月', level=16, echoes=0)]
        assert case['character_preview'] == mixed['character_preview']
        actual = {(r['slot'], r['source']): {k: v for k, v in r.items() if k not in ('slot', 'source')}
                  for r in case['character_preview']}
        hunter, dawn = dict(name='猎人甲', level=33, echoes=106), dict(name='晓', level=18, echoes=91)
        bad = {'issue': 'unrecognized'}
        assert actual == {(0, 'both'): dict(name='封存', level=41, echoes=777),
                          (1, 'primary'): hunter, (1, 'backup'): {**dawn, 'identity_conflict': True}, (3, 'backup'): dawn,
                          (4, 'primary'): bad, (5, 'backup'): dawn, (6, 'primary'): bad, (6, 'backup'): dawn,
                          (7, 'both'): hunter, (8, 'primary'): {'name':'猎人甲', 'issue':'stats_unavailable'},
                          (9, 'primary'): bad}, actual
        current_rows = explicit_plan['character_preview']
        def slot_rows(slot):
            return [{k:v for k,v in row.items() if k != 'slot'} for row in current_rows if row['slot']==slot]
        assert slot_rows(0) == [dict(source='both', name='当前', level=24, echoes=999)]
        assert slot_rows(1) == [dict(source='primary', **hunter), dict(source='backup', identity_conflict=True, **hunter)]
        assert slot_rows(2) == [dict(source='primary', **hunter), {**hunter,'source':'backup','echoes':0}]
        assert slot_rows(3) == [dict(source=s, name='猎人甲', issue='stats_unavailable') for s in ('primary','backup')]
        assert slot_rows(4) == [dict(source='primary', **hunter), {**hunter,'source':'backup','level':34}]
        assert slot_rows(5) == [dict(source='both', name='𠀀猎', level=19, echoes=28)]
        assert [r['source'] for r in slot_rows(6)] == ['primary','backup']
        assert all('name' not in r and r.get('issue') for r in slot_rows(6))
        assert slot_rows(7) == [dict(source='both', **hunter)]
        assert slot_rows(8) == [dict(source='primary', **bad), dict(source='backup', **dawn)]
        assert slot_rows(9) == [dict(source='both', name='姓名未识别', level=11, echoes=9)]
        if output:
            output.write_text(json.dumps({**ordinary,'backups':[launch, prior, case, mixed, vacant],
                                          'explicit_plan':explicit_plan}, ensure_ascii=False), 'utf-8')
    print('PASS: launch, pre-restore and sealed snapshots, zero echoes, equal and differing generations, backup only, '
          'empty, truncated, unsupported and partial slots. Release metadata, plan/digest and filesystem snapshot match.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path, help='clean preview.25 client directory with original scripts and reader')
    parser.add_argument('candidate', type=Path, help='isolated candidate directory with compatible numeric reader')
    parser.add_argument('--ui-fixture', type=Path)
    args = parser.parse_args()
    check(args.release.resolve(), args.candidate.resolve(), args.ui_fixture)
