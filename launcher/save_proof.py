"""Accept only game-authored descendants of a verified recovered load.

This module never writes game saves or replays world flags. Native save entry
alone is insufficient: both actual generations must match later native banks.
"""
import json
import re


def prepare(root, token):
    import world_recovery as wr
    folder, saves = wr.layout(root)
    eligible = []
    for path in sorted((folder/'requests').glob('*.bin')):
        data = wr.stable(path)
        if len(data) < wr.HEADER.size:
            continue
        seq, old_token, slot, _, _, _ = wr.request_header(data)
        job = folder/'jobs'/f'{old_token}-{slot}-{seq}'/'job.json'
        if not job.exists() or json.loads(wr.stable(job)).get('state') != 'primary-ready':
            continue
        request = wr.read_request(root, path, enroll=False)
        value = wr.prepare(request)
        name = f'userdata{slot:04}'
        raw = wr.stable(saves/name)
        if wr.file_matches(request, value, name, raw):
            eligible.append((request, raw))
    # Ambiguous/multiple pending roles remain blocked; do not select by mtime.
    if len(eligible) != 1:
        return {}
    request, raw = eligible[0]
    start = wr.offline_aux.flag_bank(raw)
    bank = raw[start:start+150000]
    target = folder/'save-proof'/token
    if target.exists():
        raise ValueError('本轮保存凭据目录已存在，未复用旧记录。')
    wr.atomic(target/'expected-bank.bin', bank)
    wr.atomic(target/'input-save.bin', raw)
    context = {'schema':1, 'token':token, 'job_id':request['id'],
               'plan_sha256':request['plan_sha256'], 'identity':request['identity'],
               'input_sha256':wr.sha(raw), 'bank_sha256':wr.sha(bank),
               'slot':request['slot']}
    wr.json_file(target/'context.json', context)
    return {'THEHUNT_SAVE_PROOF_DIR':str(target)}


def eligible_run(run, token, root, slot, identity, running_token):
    if not isinstance(run, dict):
        return False
    exit_code = run.get('exit_code')
    return (run.get('token') == token and run.get('root') == root and
            isinstance(run.get('identities'), dict) and run['identities'].get(str(slot)) == identity and
            type(run.get('started_ns')) is int and run['started_ns'] > 0 and
            ((type(exit_code) is int and exit_code == 0) or
             ('exit_code' not in run and token == running_token)))


def native_records(raw, run, stage):
    # Require a complete bounded append before treating it as a native receipt.
    if len(raw) > 65536 or not raw.endswith(b'\n'):
        return None
    lines = raw.splitlines()
    if not 2 <= len(lines) <= 64:
        return None
    try:
        records = [json.loads(line) for line in lines]
    except (ValueError, RecursionError):
        return None
    previous = -1
    for sequence, row in enumerate(records, 1):
        if (not isinstance(row, dict) or
                any(type(row.get(key)) is not int for key in ('sequence', 'pid', 'monotonic_ms', 'epoch_ms')) or
                row['sequence'] != sequence or row['pid'] <= 0 or row['pid'] != records[0]['pid'] or
                row['monotonic_ms'] < max(0, previous) or row['epoch_ms']*1000000 < run['started_ns'] or
                row['epoch_ms'] < records[0]['epoch_ms'] or
                row.get('stage') != (stage if sequence == 1 else 'save-entry') or
                not isinstance(row.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            return None
        previous = row['monotonic_ms']
    return records


def verify(request, value, current, *, running_token=None):
    import world_recovery as wr
    for folder in sorted((request['folder']/'save-proof').glob('*')):
        context_path, log = folder/'context.json', folder/'native.jsonl'
        if not context_path.is_file() or not log.is_file():
            continue
        context = json.loads(wr.stable(context_path))
        if (context.get('schema') != 1 or context.get('token') != folder.name or
                context.get('job_id') != request['id'] or
                context.get('plan_sha256') != request['plan_sha256'] or
                context.get('identity') != value['identity'] or
                context.get('slot') != request['slot']):
            continue
        run = json.loads(wr.stable(request['folder']/'runs'/f'{folder.name}.json'))
        if not eligible_run(run, folder.name, request['context']['root'], request['slot'],
                            value['identity'], running_token):
            continue
        bank = wr.stable(folder/'expected-bank.bin')
        if len(bank) != 150000 or wr.sha(bank) != context['bank_sha256']:
            raise ValueError('加载核对的事件区凭据发生变化，保留恢复任务。')
        source = wr.stable(folder/'input-save.bin')
        start = wr.offline_aux.flag_bank(source)
        if (wr.sha(source) != context['input_sha256'] or
                source[start:start+150000] != bank or
                not wr.file_matches(request, value, f'userdata{request["slot"]:04}', source)):
            raise ValueError('加载核对的原始输入或恢复目标发生变化，保留恢复任务。')
        raw_log = wr.stable(log)
        records = native_records(raw_log, run, 'loaded')
        if not records or records[0]['sha256'] != context['bank_sha256']:
            continue
        files = {}
        for name, raw in current.items():
            path = request['saves']/name
            if wr.identity(raw) != value['identity'] or wr.stable(path) != raw:
                break
            start = wr.offline_aux.flag_bank(raw)
            digest = wr.sha(raw[start:start+150000])
            matches = [r for r in records[1:] if r.get('sha256') == digest and
                       path.stat().st_mtime_ns >= r['epoch_ms']*1000000]
            if not matches:
                break
            files[name] = {'sha256':wr.sha(raw), 'bank_sha256':digest,
                           'save_sequence':matches[-1]['sequence'],
                           'mtime_ns':path.stat().st_mtime_ns}
        if set(files) == set(value['files']):
            request['native_save_receipt'] = raw_log
            return {'schema':1, 'run_token':folder.name, 'pid':records[0]['pid'],
                    'loaded_bank_sha256':context['bank_sha256'],
                    'native_receipt_sha256':wr.sha(raw_log), 'files':files}
    return None
