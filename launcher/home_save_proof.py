"""Read-only proof of game-authored saves after an in-memory return home.

Only a complete native recovered bank followed by matching native save-entry
digests for both actual disk generations can retire a recovery transaction.
Missing evidence retains the existing refusal to replay old world progress.
"""
import json
from save_proof import eligible_run, native_records


def verify_title(request, value, current):
    """Link the first successful title input to both subsequent full role records."""
    import world_recovery as wr
    slot, token = request['slot'], request['token']
    run = json.loads(wr.stable(request['folder']/'runs'/f'{token}.json'))
    if (request['mode'] != wr.TITLE_RECORDS or set(current) != set(value['files']) or
            not eligible_run(run, token, request['context']['root'], slot, value['identity'], None)):
        return None
    folder = request['folder']/'title-save-proof'/request['id']
    try:
        log = wr.stable(folder/'native.jsonl')
        raw = wr.stable(folder/'input-save.bin')
        original = wr.stable(folder/'quit-bank.bin')
    except FileNotFoundError as error:
        wr.require_stable_missing(error)
        return None
    records = native_records(log, run, 'title-input')
    if (not records or len(records) == 64 or len(raw) != 0x140000 or wr.sha(raw) != records[0]['sha256'] or
            len(original) != 150000 or wr.sha(original) != records[0].get('quit_bank_sha256')):
        return None
    primary = f'userdata{slot:04}'
    # Two old, individually known saves cannot account for a later generated record.
    if (wr.sha(current[primary]) != records[-1]['sha256'] or
            (request['saves']/primary).samefile(request['saves']/f'backup{slot:04}')):
        return None
    generation = records[0].get('generation')
    if type(generation) is not int or generation <= 0 or any(
            row.get('token') != token or row.get('quit_bank_sha256') != records[0]['quit_bank_sha256'] or
            type(row.get('generation')) is not int or
            row['generation'] != generation or type(row.get('slot')) is not int or row['slot'] != slot or
            type(row.get('recovery_sequence')) is not int or row['recovery_sequence'] != request['seq']
            for row in records):
        return None
    # image() independently validates the role, every mapped non-pickup group
    # and the pickup union against the immutable pre-join mapping. It works on bytes.
    bank = wr.offline_aux.flag_bank(raw)
    expected = bytearray(original)
    for key, index, before, target in request['rows']:
        if index == 0xffffffff:
            if before != target:
                return None
            continue
        start = index*125
        source = original[start:start+125]
        if key//10000 == 5:
            changed = bytes(a | b for a, b in zip(source, target))
        else:
            mask = bytes(old ^ own for old, own in zip(before, target))
            changed = bytes((saved & ~bits) | (own & bits) for saved, own, bits in zip(source, target, mask))
        expected[start:start+125] = changed
    if raw[bank:bank+150000] != expected or wr.image(raw, request) != raw:
        return None
    pickups = {index*125: raw[bank+index*125:bank+(index+1)*125]
               for key, index, _, _ in request['rows'] if key//10000 == 5 and index != 0xffffffff}
    files = {}
    for name, contents in current.items():
        path = request['saves']/name
        modified = path.stat().st_mtime_ns
        if (wr.identity(contents) != value['identity'] or wr.stable(path) != contents or
                path.stat().st_mtime_ns != modified):
            return None
        start = wr.offline_aux.flag_bank(contents)
        for offset, loaded in pickups.items():
            actual = contents[start+offset:start+offset+125]
            if any(a & b != b for a, b in zip(actual, loaded)):
                return None
        digest = wr.sha(contents)
        match = next((row for row in reversed(records[1:]) if row['sha256'] == digest and
                      row['epoch_ms']*1000000 <= modified), None)
        if match is None:
            return None
        files[name] = {'sha256': digest, 'save_sequence': match['sequence'], 'mtime_ns': modified}
    request['native_save_receipt'] = log
    request['native_title_input'] = raw
    request['native_quit_bank'] = original
    return {'schema': 3, 'origin': 'same-role-title-input', 'run_token': token, 'slot': slot,
            'recovery_sequence': request['seq'], 'generation': generation, 'pid': records[0]['pid'],
            'input_sha256': records[0]['sha256'], 'quit_bank_sha256': wr.sha(original),
            'native_receipt_sha256': wr.sha(log), 'files': files}


def verify(request, value, current, *, running_token=None):
    import world_recovery as wr
    if request['mode'] not in (wr.APPLIED, wr.RELOADED):
        return None
    folder = request['folder']/'home-save-proof'/request['id']
    log, bank_path = folder/'native.jsonl', folder/'restored-bank.bin'
    if not log.is_file() or not bank_path.is_file():
        return None
    run = json.loads(wr.stable(request['folder']/'runs'/f'{request["token"]}.json'))
    if not eligible_run(run, request['token'], request['context']['root'], request['slot'],
                        value['identity'], running_token):
        return None
    bank = wr.stable(bank_path)
    if len(bank) != 150000:
        return None
    # Independently check every target, including unchanged groups. A native
    # "restore happened" message or a partial bank is never sufficient.
    persistent = False
    for _, index, _, after in request['rows']:
        if index == 0xffffffff:
            continue
        if not 0 <= index < 1200 or bank[index*125:(index+1)*125] != after:
            return None
        persistent = True
    if not persistent:
        return None
    raw_log = wr.stable(log)
    first_stage = 'title-reload' if request['mode'] == wr.RELOADED else 'restored'
    records = native_records(raw_log, run, first_stage)
    if not records or records[0]['sha256'] != wr.sha(bank):
        return None
    if any(type(row.get('slot')) is not int or row['slot'] != request['slot'] or
           type(row.get('recovery_sequence')) is not int or row['recovery_sequence'] != request['seq']
           for row in records):
        return None
    files = {}
    for name, raw in current.items():
        path = request['saves']/name
        if wr.identity(raw) != value['identity'] or wr.stable(path) != raw:
            return None
        start = wr.offline_aux.flag_bank(raw)
        digest = wr.sha(raw[start:start+150000])
        matches = [r for r in records[1:] if r.get('sha256') == digest and
                   path.stat().st_mtime_ns >= r['epoch_ms']*1000000]
        if not matches:
            return None
        files[name] = {'sha256': wr.sha(raw), 'bank_sha256': digest,
                       'save_sequence': matches[-1]['sequence'],
                       'mtime_ns': path.stat().st_mtime_ns}
    if set(files) != set(value['files']):
        return None
    request['native_save_receipt'] = raw_log
    request['native_restored_bank'] = bank
    return {'schema': 2, 'origin': 'same-run-home-restore',
            'run_token': request['token'], 'recovery_sequence': request['seq'], 'pid': records[0]['pid'],
            'restored_bank_sha256': wr.sha(bank),
            'native_receipt_sha256': wr.sha(raw_log), 'files': files}
