"""Read-only proof of game-authored saves after an in-memory return home.

Only a complete native recovered bank followed by matching native save-entry
digests for both actual disk generations can retire a recovery transaction.
Missing evidence retains the existing refusal to replay old world progress.
"""
import json
from save_proof import eligible_run, native_records


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
