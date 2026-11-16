"""Only synthetic bytes in caller-owned temporary installations."""
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'launcher'))
import save_resolution as resolution
import world_recovery as world
from synthetic_world_fixture import fixture_pair

RAW, GOOD = fixture_pair()


def create(root, *, marker=True):
    root = Path(root)
    folder = root/'data/user/savedata/1/CUSA00207/SPRJ0005'
    folder.mkdir(parents=True)
    (folder/'userdata0010').write_bytes(b'\x01'*84)
    for slot in (0, 1):
        raw = bytearray(RAW)
        role = world.offline_save.parse(raw)
        raw[role.face+4] = slot
        for stem in ('userdata', 'backup'):
            (folder/f'{stem}{slot:04}').write_bytes(raw)
    (root/'mod').mkdir()
    (root/'data/user/trophies.log').write_bytes(b'synthetic trophy sentinel')
    (root/'profile.json').write_bytes(b'synthetic configuration sentinel')
    if marker:
        path = root/'data/user/world-recovery/review-required.json'
        path.parent.mkdir()
        path.write_bytes(b'{unknown opaque marker with C:/NEVER-FOLLOW-THIS}')
    return folder


def request(root, *, mode=world.QUIT, job=False):
    root = Path(root)
    token = world.begin(root)['THEHUNT_WORLD_RECOVERY_TOKEN']
    bank = world.offline_aux.flag_bank(RAW)
    before, after = RAW[bank+625:bank+750], GOOD[bank+625:bank+750]
    row = (1, 5, after if mode == world.CAPTURE else before, after)
    journal = struct.pack('<III', 0x574F4242, 125, 1) + struct.pack('<I', 1)+after
    own = root/'mod/own-world/own-save-0.bin'
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_bytes(journal)
    folder = root/'data/user/world-recovery'
    baseline = world.HEADER.pack(b'THWR', 2, 1, token.encode(), 0, world.CAPTURE, 1, world.fnv(journal))
    baseline += world.ROW.pack(1, 5, after, after)
    world.atomic(folder/'baselines/world-save-0.bin', baseline)
    world.atomic(folder/'baselines/world-save-0.bbow', journal)
    packet = world.HEADER.pack(b'THWR', 2, 2, token.encode(), 0, mode, 1, world.fnv(journal))+world.ROW.pack(*row)
    path = folder/'requests/world-save-0.bin'
    world.atomic(path, packet)
    result = world.read_request(root, path, enroll=False)
    if job:
        world.prepare(result)
    world.process_exited(root, token, 0xc0000005)
    return result


def choose(root, action, selection=None):
    kwargs = {} if selection is None else {'selection_'+k: selection[k] for k in ('kind', 'id', 'fingerprint')}
    plan = resolution.plan(root, **kwargs)
    # Send exactly the effective source carried by the production UI DTO.
    kwargs = {} if plan['selection'] is None else {'selection_'+k: plan['selection'][k] for k in ('kind', 'id', 'fingerprint')}
    return resolution.apply(root, plan['digest'], action, confirmed=True, **kwargs)
