"""Read-only proof of game-authored saves after an in-memory return home.

Only a complete native recovered bank followed by matching native save-entry
digests for both actual disk generations can retire a recovery transaction.
Missing evidence retains the existing refusal to replay old world progress.
"""
import json


def verify(request, value, current):
    import world_recovery as wr
    if request['mode'] != wr.APPLIED:
        return None
    folder = request['folder']/'home-save-proof'/request['id']
    log, bank_path = folder/'native.jsonl', folder/'restored-bank.bin'
    if not log.is_file() or not bank_path.is_file():
        return None
    run = json.loads(wr.stable(request['folder']/'runs'/f'{request["token"]}.json'))
    if (run['token'] != request['token'] or run['root'] != request['context']['root'] or
            run['identities'].get(str(request['slot'])) != value['identity'] or
            run.get('exit_code', 0) != 0):
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
    if len(raw_log) > 65536 or not raw_log.endswith(b'\n'):
        return None
    records = [json.loads(line) for line in raw_log.splitlines()]
    if not 2 <= len(records) <= 64:
        return None
    restored = records[0]
    if (restored.get('stage') != 'restored' or restored.get('sha256') != wr.sha(bank) or
            restored.get('epoch_ms', 0)*1000000 < run['started_ns']):
        return None
    pid = restored.get('pid')
    if not isinstance(pid, int) or pid <= 0:
        return None
    previous = restored.get('monotonic_ms', -1)
    for sequence, row in enumerate(records, 1):
        if (row.get('sequence') != sequence or row.get('pid') != pid or
                row.get('slot') != request['slot'] or
                row.get('recovery_sequence') != request['seq'] or
                row.get('monotonic_ms', -1) < previous or
                row.get('epoch_ms', 0) < restored['epoch_ms'] or
                (sequence > 1 and row.get('stage') != 'save-entry')):
            return None
        previous = row['monotonic_ms']
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
            'run_token': request['token'], 'recovery_sequence': request['seq'], 'pid': pid,
            'restored_bank_sha256': wr.sha(bank),
            'native_receipt_sha256': wr.sha(raw_log), 'files': files}
