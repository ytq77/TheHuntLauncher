"""Bounded BBSC recovery protocol. Disk writes occur only with the game stopped."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time
import uuid

import offline_aux
import offline_save
import save_proof
import home_save_proof
from save_transfer import plain_path, require_stopped, import_lock

HEADER = struct.Struct('<4sIQ32sIIIQ')
ROW = struct.Struct('<II125s125s')
LIMIT = 10000
CAPTURE, RESTORE, APPLIED, QUIT, BLOCKED = range(1, 6)
_next_poll = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path = plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for attempt in range(10):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.002)
    finally:
        temp.unlink(missing_ok=True)


def json_file(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


def stable(path):
    path = plain_path(path)
    first = path.read_bytes()
    if path.read_bytes() != first:
        raise ValueError('存档正在写入，稍后重新核对。')
    return first


def identity(raw):
    role = offline_save.parse(raw)
    offline_aux.flag_bank(raw)
    return sha(raw[role.username+1:role.username+33]+raw[role.face+4:role.face+239])


def fnv(raw):
    value = 14695981039346656037
    for byte in raw:
        value = ((value ^ byte)*1099511628211) & 0xffffffffffffffff
    return value


def layout(root):
    root = plain_path(root)
    return root/'data/user/world-recovery', root/'data/user/savedata/1/CUSA00207/SPRJ0005'


def read_request(root, path):
    data = stable(path)
    if len(data) < HEADER.size:
        raise ValueError('世界恢复请求不完整。')
    magic, version, seq, token, slot, mode, count, journal_hash = HEADER.unpack_from(data)
    token = token.decode('ascii')
    if (magic != b'THWR' or version != 2 or not re.fullmatch('[0-9a-f]{32}', token)
            or not seq or not 0 <= slot < 10 or mode not in range(1, 6)
            or not 0 < count <= LIMIT or len(data) != HEADER.size+count*ROW.size):
        raise ValueError('世界恢复请求格式不受支持。')
    folder, saves = layout(root)
    context = json.loads(stable(folder/'runs'/f'{token}.json'))
    if context['token'] != token or context['root'] != str(plain_path(root)):
        raise ValueError('世界恢复请求属于另一运行环境。')
    journal = plain_path(Path(root)/'mod/own-world'/f'own-save-{slot}.bin')
    try:
        raw_journal = stable(journal)
    except FileNotFoundError:
        job = folder/'jobs'/f'{token}-{slot}-{seq}'
        state = json.loads(stable(job/'job.json'))
        if state.get('state') not in ('primary-ready', 'verified', 'complete'):
            raise ValueError('世界记录缺失且没有已验证的归档凭据。') from None
        waiting = journal.with_suffix('.bin.awaiting-save')
        raw_journal = stable(waiting) if waiting.exists() else stable(job/'journal.bin')
    if fnv(raw_journal) != journal_hash:
        raise ValueError('世界恢复记录与请求版本不一致，保留后稍后核对。')
    groups = offline_aux.journal(raw_journal, 0x574F4242)
    rows = [ROW.unpack_from(data, HEADER.size+i*ROW.size) for i in range(count)]
    keys, indexes = set(), {}
    for key, index, before, after in rows:
        if key in keys or key not in groups:
            raise ValueError('世界恢复请求包含重复或未知状态组。')
        keys.add(key)
        expected = (bytes(a | b for a, b in zip(before, groups[key]))
                    if key//10000 == 5 else groups[key])
        if after != expected or (index != 0xffffffff and index >= 1200):
            raise ValueError('世界恢复目标或存档位置不匹配。')
        if index != 0xffffffff:
            # Bloodborne has distinct flag keys which alias one persistent
            # group. They are safe only with the same before/after bytes and
            # recovery operation; conflicting aliases must never be replayed.
            definition = (before, after, key//10000 == 5)
            if index in indexes and indexes[index] != definition:
                raise ValueError('同一存档位置的世界恢复记录存在冲突，已停止。')
            indexes[index] = definition
    if keys != set(groups) or not indexes:
        raise ValueError('世界恢复请求缺少状态组或角色身份。')
    baseline, baseline_journal = read_baseline(folder, token, slot, seq, rows)
    if str(slot) not in context['identities']:
        # Only a complete pre-join, still-own-world snapshot may enroll a role
        # created during this run. An already known identity is never replaced.
        if mode != CAPTURE or any(before != after for _, _, before, after in rows):
            raise ValueError('新角色尚无可信入房前身份记录，已停止。')
        primary = saves/f'userdata{slot:04}'
        backup = saves/f'backup{slot:04}'
        name = identity(stable(primary))
        if (identity(stable(backup)) != name or
                primary.stat().st_mtime_ns < context['started_ns']):
            raise ValueError('新角色身份或保存时间尚未确认，稍后重试。')
        context['identities'][str(slot)] = name
        json_file(folder/'runs'/f'{token}.json', context)
    return {'seq': seq, 'token': token, 'slot': slot, 'mode': mode, 'rows': rows,
            'identity': context['identities'][str(slot)], 'request_sha256': sha(data),
            'journal': journal, 'journal_bytes': raw_journal, 'folder': folder, 'saves': saves,
            'path': path, 'id': f'{token}-{slot}-{seq}', 'context': context,
            'baseline': baseline, 'baseline_journal': baseline_journal,
            'plan_sha256': sha(data[:52]+data[56:])}


def read_baseline(folder, token, slot, seq, rows):
    job = folder/'jobs'/f'{token}-{slot}-{seq}'
    packet_path = job/'baseline.bin'
    journal_path = job/'baseline.bbow'
    if not (job/'job.json').exists():
        packet_path = folder/'baselines'/f'world-save-{slot}.bin'
        journal_path = folder/'baselines'/f'world-save-{slot}.bbow'
    packet, journal = stable(packet_path), stable(journal_path)
    if len(packet) < HEADER.size:
        raise ValueError('入房前的固定映射记录不完整，已停止。')
    magic, version, initial_seq, initial_token, initial_slot, mode, count, fingerprint = HEADER.unpack_from(packet)
    if (magic != b'THWR' or version != 2 or initial_token.decode('ascii') != token or
            initial_slot != slot or mode != CAPTURE or not 0 < initial_seq <= seq or
            not 0 < count <= LIMIT or len(packet) != HEADER.size+count*ROW.size or fnv(journal) != fingerprint):
        raise ValueError('入房前映射的运行身份或版本不匹配，已停止。')
    own = offline_aux.journal(journal, 0x574F4242)
    mapping = {}
    for n in range(count):
        key, index, before, after = ROW.unpack_from(packet, HEADER.size+n*ROW.size)
        if key in mapping or key not in own or before != after or after != own[key]:
            raise ValueError('固定映射不是完整的入房前个人世界记录，已停止。')
        mapping[key] = index
    if set(mapping) != set(own) or set(mapping) != {row[0] for row in rows}:
        raise ValueError('入房前后的状态组范围发生变化，已停止。')
    if any(mapping[key] != index for key, index, _, _ in rows):
        raise ValueError('退出记录与入房前固定的存档位置不一致，已停止。')
    return packet, journal


def image(raw, request):
    if identity(raw) != request['identity']:
        raise ValueError('世界恢复角色与本轮开始时不一致，已停止。')
    start = offline_aux.flag_bank(raw)
    result = bytearray(raw)
    for key, index, _, after in request['rows']:
        if index == 0xffffffff:
            continue  # A memory-only group has no persistent bank position.
        offset = start+index*125
        previous = result[offset:offset+125]
        result[offset:offset+125] = (bytes(a | b for a, b in zip(previous, after))
                                      if key//10000 == 5 else after)
    output = bytes(result)
    assert output[:start] == raw[:start] and output[start+150000:] == raw[start+150000:]
    assert offline_save.parse(output).entries == offline_save.parse(raw).entries
    return output


def job_for(request):
    return request['folder']/'jobs'/request['id']


def prepare(request):
    job = job_for(request)
    manifest = job/'job.json'
    if manifest.exists():
        value = json.loads(stable(manifest))
        if value['request_identity'] != [request['token'], request['slot'], request['seq']]:
            raise ValueError('恢复任务编号冲突。')
        if value['plan_sha256'] != request['plan_sha256']:
            raise ValueError('恢复任务中的状态内容发生变化，已停止。')
        if value['baseline_sha256'] != sha(request['baseline']):
            raise ValueError('入房前固定映射的凭据发生变化，已停止。')
        return value
    files = {}
    for stem in ('userdata', 'backup'):
        name = f'{stem}{request["slot"]:04}'
        before = stable(request['saves']/name)
        after = image(before, request)
        atomic(job/'before'/name, before)
        atomic(job/'expected'/name, after)
        files[name] = {'before': sha(before), 'after': sha(after)}
    atomic(job/'journal.bin', request['journal_bytes'])
    atomic(job/'baseline.bin', request['baseline'])
    atomic(job/'baseline.bbow', request['baseline_journal'])
    value = {'schema': 1, 'state': 'prepared', 'request_identity':
             [request['token'], request['slot'], request['seq']],
             'identity': request['identity'], 'files': files,
             'request_sha256': request['request_sha256'], 'plan_sha256': request['plan_sha256'],
             'baseline_sha256': sha(request['baseline']),
             'mode': request['mode']}
    json_file(manifest, value)
    return value


def file_matches(request, value, name, current):
    job = job_for(request)
    if identity(current) != value['identity']:
        return False
    before = (job/'before'/name).read_bytes()
    after = (job/'expected'/name).read_bytes()
    if sha(before) != value['files'][name]['before'] or sha(after) != value['files'][name]['after']:
        raise ValueError('恢复任务的备份或目标副本发生变化。')
    a, b, c = (offline_aux.flag_bank(raw) for raw in (before, after, current))
    # Verify only the recovery's changed bits, allowing later personal progress.
    return not any((current[c+i] ^ after[b+i]) & (before[a+i] ^ after[b+i]) for i in range(150000))


def saved_matches(request, value):
    current = {name:stable(request['saves']/name) for name in value['files']}
    # Preserve strict baseline/identity validation even if a native descendant
    # can later explain legitimate changes to load-time event completion flags.
    strict = [file_matches(request, value, name, raw) for name,raw in current.items()]
    if all(strict):
        return True
    proof = (save_proof.verify(request, value, current) or
             home_save_proof.verify(request, value, current))
    if proof:
        request['native_save_proof'] = proof
    return proof is not None


def finish(request, value, *, proof):
    job = job_for(request)
    if not saved_matches(request, value):
        return False
    original_journal = (job/'journal.bin').read_bytes()
    waiting = request['journal'].with_suffix('.bin.awaiting-save')
    active = request['journal'] if request['journal'].exists() else waiting
    if active.exists() and stable(active) != original_journal:
        raise ValueError('核对后恢复记录又有更新，未归档。')
    value.update(state='verified', proof=proof)
    if request.get('native_save_proof'):
        value['native_save_proof'] = request['native_save_proof']
        receipt = request.get('native_save_receipt')
        if receipt is None or sha(receipt) != value['native_save_proof']['native_receipt_sha256']:
            raise ValueError('游戏保存凭据发生变化，保留恢复任务。')
        atomic(job/'native-save-proof.jsonl', receipt)
        if value['native_save_proof'].get('origin') == 'same-run-home-restore':
            bank = request.get('native_restored_bank')
            if bank is None or sha(bank) != value['native_save_proof']['restored_bank_sha256']:
                raise ValueError('内存恢复的事件区凭据不完整，保留恢复任务。')
            atomic(job/'native-restored-bank.bin', bank)
    json_file(job/'job.json', value)
    # The full previous journal is already retained in this job's immutable backup.
    archived = request['journal'].with_suffix('.bin.restored')
    if active.exists():
        active.replace(archived)
    elif not archived.exists() or stable(archived) != original_journal:
        raise ValueError('待归档记录或已归档副本不匹配，保留任务。')
    value.update(state='complete')
    json_file(job/'job.json', value)
    confirm(request['folder'], request['slot'], request['seq'])
    return True


def confirm(folder, slot, seq):
    path = folder/'acks'/f'{slot}.ack'
    expected = struct.pack('<Q', seq)
    if not path.exists() or stable(path) != expected:
        atomic(path, expected)


def complete_stopped(request, *, can_write):
    root = Path(request['context']['root'])
    with import_lock(root):
        if (root/'.save-conversion.lock').exists():
            raise ValueError('存档转换尚未完成，已停止世界恢复写入。')
        return _complete_stopped(request, can_write=can_write)


def _complete_stopped(request, *, can_write):
    require_stopped()
    value = prepare(request)
    if finish(request, value, proof='both save generations already contain the recovery'):
        return 'verified'
    job = job_for(request)
    if value['state'] == 'primary-ready':
        mark_waiting(request, job)
        return 'waiting-backup-save'  # Never replay a completed primary recovery.
    staged = {}
    for name in value['files']:
        if name.startswith('backup'):
            # A backup's inventory can be a generation older. Only the game may
            # advance it together with the corresponding character data.
            continue
        current = stable(request['saves']/name)
        before = (job/'before'/name).read_bytes()
        after = (job/'expected'/name).read_bytes()
        if identity(current) != value['identity']:
            raise ValueError('恢复期间角色身份发生变化，已停止。')
        if file_matches(request, value, name, current):
            continue  # Resume safely if an earlier attempt already wrote this generation.
        if not can_write:
            raise ValueError('上次会话的世界恢复尚未确认；已保留记录，不会自动覆盖后来的进度。')
        if request['mode'] == QUIT:
            # A quit receipt is finalized only after the process has ended. Never
            # use an earlier running-process save snapshot for this write.
            if sha(current) != value['files'][name]['before']:
                raise ValueError('退出后的存档又发生变化，未写入。')
        else:
            bank_now, bank_before = offline_aux.flag_bank(current), offline_aux.flag_bank(before)
            if current[bank_now:bank_now+150001] != before[bank_before:bank_before+150001]:
                raise ValueError('恢复后已有新的事件进度，未重复套用旧状态。')
            # Keep newer non-event data even when retrying an interrupted write.
            start = offline_aux.flag_bank(current)
            old_start = offline_aux.flag_bank(before)
            target_start = offline_aux.flag_bank(after)
            candidate = bytearray(current)
            for i in range(150000):
                mask = before[old_start+i] ^ after[target_start+i]
                candidate[start+i] = (candidate[start+i] & ~mask) | (after[target_start+i] & mask)
            after = bytes(candidate)
        staged[name] = (current, after)
    attempt = job/'attempts'/uuid.uuid4().hex
    for name, (current, after) in staged.items():
        atomic(attempt/'before'/name, current)
        atomic(attempt/'expected'/name, after)
    json_file(attempt/'install.json', {'state': 'prepared', 'files':
              {name: {'before': sha(a), 'after': sha(b)} for name, (a, b) in staged.items()}})
    for name, (current, after) in staged.items():
        require_stopped()
        if stable(request['saves']/name) != current:
            raise ValueError('恢复写入前检测到存档变化，已保留任务供核对。')
        atomic(request['saves']/name, after)
    primary = f'userdata{request["slot"]:04}'
    if not file_matches(request, value, primary, stable(request['saves']/primary)):
        raise ValueError('主存档恢复写入后核对失败，保留全部备份。')
    value.update(state='primary-ready', proof='stopped process; current primary backed up, transformed and reread; old game backup untouched')
    json_file(job/'job.json', value)
    mark_waiting(request, job)
    return 'primary-restored-waiting-backup-save'


def mark_waiting(request, job):
    waiting = request['journal'].with_suffix('.bin.awaiting-save')
    if request['journal'].exists():
        if stable(request['journal']) != (job/'journal.bin').read_bytes():
            raise ValueError('主存档核对后世界记录又有变化，未改名。')
        request['journal'].replace(waiting)
    elif not waiting.exists() or stable(waiting) != (job/'journal.bin').read_bytes():
        raise ValueError('主存档已恢复，但待确认记录缺失，请保留恢复任务。')


def poll(root, token):
    folder, _ = layout(root)
    for path in sorted((folder/'requests').glob('*.bin')):
        with path.open('rb') as stream:
            data = stream.read(HEADER.size)
        if len(data) < HEADER.size:
            continue
        _, _, seq, run_token, slot, mode, _, _ = HEADER.unpack_from(data)
        request_token = run_token.decode('ascii')
        job = folder/'jobs'/f'{request_token}-{slot}-{seq}'/'job.json'
        job_state = json.loads(stable(job))['state'] if job.exists() else None
        if job_state == 'primary-ready':
            key = (str(folder), request_token, slot, seq, 'backup')
            now = time.monotonic()
            if now < _next_poll.get(key, 0):
                continue
            _next_poll[key] = now+1
            request = read_request(root, path)
            finish(request, prepare(request), proof='native game save advanced both complete generations')
            continue
        if request_token != token:
            continue
        if job_state == 'complete':
            confirm(folder, slot, seq)
            continue
        if mode == CAPTURE:
            context = json.loads(stable(folder/'runs'/f'{token}.json'))
            if str(slot) not in context['identities']:
                read_request(root, path)
            continue
        if mode not in (RESTORE, APPLIED):
            continue
        ready = folder/'ready'/f'{slot}.ready'
        if mode == RESTORE and ready.exists() and stable(ready) == struct.pack('<Q', seq):
            value = json.loads(stable(job))
            packet = stable(path)
            if sha(packet[:52]+packet[56:]) != value['plan_sha256']:
                raise ValueError('已准备的世界恢复请求内容发生变化。')
            continue
        key = (str(folder), token, slot, seq)
        if mode == APPLIED:
            now = time.monotonic()
            if now < _next_poll.get(key, 0):
                continue
            _next_poll[key] = now+1
        request = read_request(root, path)
        value = prepare(request)
        if mode == RESTORE:
            ready = folder/'ready'/f'{slot}.ready'
            expected = struct.pack('<Q', seq)
            if not ready.exists() or stable(ready) != expected:
                atomic(ready, expected)
        else:
            finish(request, value, proof='read-only comparison of both actual save generations')


def finalize(root, token):
    folder, _ = layout(root)
    results = []
    for path in sorted((folder/'requests').glob('*.bin')):
        data = stable(path)
        if len(data) < HEADER.size:
            raise ValueError('存在不完整世界恢复请求，已保留。')
        _, _, seq, run_token, slot, mode, _, _ = HEADER.unpack_from(data)
        job = folder/'jobs'/f'{run_token.decode("ascii")}-{slot}-{seq}'/'job.json'
        job_state = json.loads(stable(job))['state'] if job.exists() else None
        if token is not None and run_token.decode('ascii') != token and job_state != 'primary-ready':
            continue
        if job_state == 'complete':
            confirm(folder, slot, seq)
            continue
        request = read_request(root, path)
        if mode == BLOCKED:
            raise ValueError('退出后又重新读档，世界记录暂不自动套用；请保留存档与恢复任务。')
        results.append(complete_stopped(request, can_write=(mode == APPLIED or
                        (mode == QUIT and request['context'].get('exit_code') == 0))))
    pending = list((Path(root)/'mod/own-world').glob('own-save-*.bin'))
    if pending:
        raise ValueError('仍有缺少完整恢复凭据的世界记录，已保留；不要删除或手动改名。')
    return results


def begin(root):
    require_stopped()
    if (layout(root)[0]/'review-required.json').exists():
        raise ValueError('本轮世界恢复记录正在核查，隔离副本暂不启动；请保留现有记录。')
    finalize(root, None)
    folder, saves = layout(root)
    token = uuid.uuid4().hex
    identities = {}
    for slot in range(10):
        path = saves/f'userdata{slot:04}'
        if path.is_file():
            raw = stable(path)
            if any(raw):
                identities[str(slot)] = identity(raw)
    json_file(folder/'runs'/f'{token}.json', {'schema': 1, 'token': token,
              'root': str(plain_path(root)), 'identities': identities, 'started_ns': time.time_ns()})
    env = {'THEHUNT_WORLD_RECOVERY_V2': '1', 'THEHUNT_WORLD_RECOVERY_DIR': str(folder),
           'THEHUNT_WORLD_RECOVERY_TOKEN': token}
    env.update(save_proof.prepare(root, token))
    return env


def process_exited(root, token, exit_code):
    folder, _ = layout(root)
    path = folder/'runs'/f'{token}.json'
    value = json.loads(stable(path))
    value['exit_code'] = exit_code
    json_file(path, value)
