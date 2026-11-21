"""Bounded BBSC recovery protocol. Disk writes occur only with the game stopped."""
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
import uuid

import offline_aux
import offline_save
import save_proof
import home_save_proof
from save_transfer import (plain_path, require_stopped, import_lock, snapshot, content_inventory,
                           copy_verified, installation_guard, OperationUnavailable)

HEADER = struct.Struct('<4sIQ32sIIIQ')
ROW = struct.Struct('<II125s125s')
LIMIT = 10000
CAPTURE, RESTORE, APPLIED, QUIT, BLOCKED, RELOADED, TITLE_RECORDS = range(1, 8)
MODE_NAMES = ('CAPTURE', 'RESTORE', 'APPLIED', 'QUIT', 'BLOCKED', 'RELOADED', 'TITLE_RECORDS')
_next_poll = {}


def quit_seal(token):
    # The worker owns this mapping until it observes the game exit. The game
    # can revoke a quit without rewriting a file, and a restarted worker
    # cannot inherit the previous worker's authorization.
    return mmap.mmap(-1, 8, tagname='Local\\TheHuntRecovery-'+token)


def read_quit_seal(seal):
    return struct.unpack('<Q', seal[:])[0]


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ExitRecordWriteError(PermissionError):
    def __init__(self, token, exit_code, cause):
        super().__init__(str(cause))
        self.token = token
        self.exit_code = exit_code


def atomic(path, data):
    path = plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    critical = (path.name == 'job.json' or
                path.parent.name == 'runs' and re.fullmatch(r'[0-9a-f]{32}\.json', path.name) or
                path.parent.name == 'acks' and re.fullmatch(r'[0-9]\.ack', path.name))
    attempts = 40 if critical else 10
    try:
        for attempt in range(attempts):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt == attempts-1:
                    raise
                time.sleep(0.0025*(attempt+1) if critical else 0.002)
    finally:
        temp.unlink(missing_ok=True)


def json_file(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))


class EvidenceChangedError(OperationUnavailable):
    """The read was incomplete or changed, not a save-content verdict."""


class ReviewRequired(ValueError):
    """A completed check found a save/world condition needing a choice."""


def require_stable_missing(error):
    """Keep FileNotFound fallbacks, but never accept a transient read miss."""
    if not isinstance(error, FileNotFoundError) or not error.filename:
        raise error
    path = plain_path(error.filename)
    for _ in range(2):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise EvidenceChangedError('读取时文件出现或消失，请重新检查。') from error


def exception_chain(error):
    """Keep implicit fallback context, including an explicit ``from None``."""
    seen, pending = set(), [error]
    while pending:
        item = pending.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        pending.extend((item.__context__, item.__cause__))


def require_content_error(error):
    """Re-raise operational failures before broad content-ValueError handlers."""
    for cause in exception_chain(error):
        if isinstance(cause, OperationUnavailable):
            raise cause
        if isinstance(cause, FileNotFoundError):
            require_stable_missing(cause)
        elif isinstance(cause, OSError):
            raise cause


def entries(folder, pattern='*'):
    """Path.glob can silently skip inaccessible evidence directories."""
    folder = plain_path(folder)
    try:
        return sorted(folder / path.name for path in folder.iterdir() if path.match(pattern))
    except FileNotFoundError as error:
        require_stable_missing(error)
        return []


def primary_identities(root, *, fingerprints=None):
    """Exactly the read-only nonzero-primary check used by normal begin()."""
    _, saves = layout(root)
    result = {}
    for slot in range(10):
        path = plain_path(saves/f'userdata{slot:04}')
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise OperationUnavailable('角色路径不是普通文件，请检查目录设置。')
            raw = stable(path)
        except FileNotFoundError as error:
            require_stable_missing(error)
            continue
        if fingerprints is not None:
            fingerprints[path] = sha(raw)
        if any(raw):
            try:
                result[str(slot)] = identity(raw)
            except (ValueError, IndexError) as error:
                require_content_error(error)
                raise ReviewRequired(f'角色槽位 {slot+1} 的主档未通过当前启动器检查：{error}；不代表进度永久丢失。') from error
    return result


def stable(path):
    path = plain_path(path)
    first = path.read_bytes()
    if path.read_bytes() != first:
        raise EvidenceChangedError('存档正在写入，稍后重新核对。')
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


def request_header(data):
    if len(data) < HEADER.size:
        raise ValueError('世界恢复请求不完整。')
    magic, version, seq, token, slot, mode, count, journal_hash = HEADER.unpack_from(data)
    token = token.decode('ascii')
    if (magic != b'THWR' or version != 2 or not re.fullmatch('[0-9a-f]{32}', token)
            or not seq or not 0 <= slot < 10 or not CAPTURE <= mode <= TITLE_RECORDS
            or not 0 < count <= LIMIT):
        raise ValueError('世界恢复请求格式不受支持。')
    return seq, token, slot, mode, count, journal_hash


def read_request(root, path, *, enroll=True):
    data = stable(path)
    seq, token, slot, mode, count, journal_hash = request_header(data)
    if len(data) != HEADER.size+count*ROW.size or path.name != f'world-save-{slot}.bin':
        raise ValueError('世界恢复请求长度或槽位文件名不匹配。')
    folder, saves = layout(root)
    context = json.loads(stable(folder/'runs'/f'{token}.json'))
    if context['token'] != token or context['root'] != str(plain_path(root)):
        raise ValueError('世界恢复请求属于另一运行环境。')
    journal = plain_path(Path(root)/'mod/own-world'/f'own-save-{slot}.bin')
    try:
        raw_journal = stable(journal)
    except FileNotFoundError as error:
        require_stable_missing(error)
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
        if not enroll or mode != CAPTURE or any(before != after for _, _, before, after in rows):
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


def read_job(request):
    job = job_for(request)
    manifest = job/'job.json'
    if manifest.exists():
        value = json.loads(stable(manifest))
        if value['state'] not in ('prepared', 'primary-ready', 'verified', 'complete'):
            raise ValueError('恢复任务状态无效。')
        if value['request_identity'] != [request['token'], request['slot'], request['seq']]:
            raise ValueError('恢复任务编号冲突。')
        if value['plan_sha256'] != request['plan_sha256']:
            raise ValueError('恢复任务中的状态内容发生变化，已停止。')
        if value['baseline_sha256'] != sha(request['baseline']):
            raise ValueError('入房前固定映射的凭据发生变化，已停止。')
        if (value['identity'] != request['identity'] or
                set(value['files']) != {f'{stem}{request["slot"]:04}' for stem in ('userdata', 'backup')}):
            raise ValueError('恢复任务与当前角色或两代存档不一致，请保留恢复记录。')
        return value
    return None


def prepare(request, *, current=None):
    value = read_job(request)
    if value is not None:
        return value
    job = job_for(request)
    files = {}
    for stem in ('userdata', 'backup'):
        name = f'{stem}{request["slot"]:04}'
        before = stable(request['saves']/name) if current is None else current[name]
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
    json_file(job/'job.json', value)
    return value


def file_matches(request, value, name, current):
    job = job_for(request)
    if identity(current) != value['identity']:
        return False
    before = stable(job/'before'/name)
    after = stable(job/'expected'/name)
    if sha(before) != value['files'][name]['before'] or sha(after) != value['files'][name]['after']:
        raise ValueError('恢复任务的备份或目标副本发生变化。')
    b, c = (offline_aux.flag_bank(raw) for raw in (after, current))
    # Every requested group must match even when this generation needed no
    # changes at preparation. Native save proof can explain later event changes.
    for key, index, _, _ in request['rows']:
        if index == 0xffffffff:
            continue
        offset = index * 125
        target = after[b+offset:b+offset+125]
        actual = current[c+offset:c+offset+125]
        if key//10000 == 5:
            if any(a & t != t for a, t in zip(actual, target)):
                return False
        elif actual != target:
            return False
    return True


def saved_matches(request, value, *, current=None, running_token=None):
    if current is None:
        current = {name:stable(request['saves']/name) for name in value['files']}
    # Preserve strict baseline/identity validation even if a native descendant
    # can later explain legitimate changes to load-time event completion flags.
    strict = [file_matches(request, value, name, raw) for name,raw in current.items()]
    if request['mode'] == TITLE_RECORDS:
        proof = home_save_proof.verify_title(request, value, current)
        if proof:
            request['native_save_proof'] = proof
        return proof is not None
    if all(strict):
        return True
    proof = (save_proof.verify(request, value, current, running_token=running_token) or
             home_save_proof.verify(request, value, current, running_token=running_token))
    if proof:
        request['native_save_proof'] = proof
    return proof is not None


def finish(request, value, *, proof, current=None, running_token=None):
    job = job_for(request)
    if not saved_matches(request, value, current=current, running_token=running_token):
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
        if value['native_save_proof'].get('origin') == 'same-role-title-input':
            atomic(job/'native-title-input.bin', request['native_title_input'])
            atomic(job/'native-quit-bank.bin', request['native_quit_bank'])
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


def later_run_exited_cleanly(request, token, observed_exit):
    """Only the latest later session for this root can explain changed rows."""
    context = request['context']
    original_start = context.get('started_ns')
    if type(original_start) is not int or original_start <= 0:
        return False
    latest = None
    try:
        runs = plain_path(request['folder']/'runs')
        for path in entries(runs, '*.json'):
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            run = json.loads(stable(path))
            if not isinstance(run, dict) or run.get('token') != path.stem:
                return False
            if run.get('root') != context['root']:
                continue
            started = run.get('started_ns')
            if type(started) is not int or started <= 0:
                return False
            if started <= original_start:
                continue
            if latest is None or started > latest['started_ns']:
                latest = run
            elif started == latest['started_ns']:
                return False
    except (OSError, ValueError) as error:
        require_content_error(error)
        return False
    if latest is None or (token is not None and token != latest['token']):
        return False
    identities = latest.get('identities')
    if not isinstance(identities, dict) or identities.get(str(request['slot'])) != request['identity']:
        return False
    if observed_exit is not None and (type(observed_exit) is not int or observed_exit != 0):
        return False
    if 'exit_code' in latest:
        return type(latest['exit_code']) is int and latest['exit_code'] == 0
    return token is not None and type(observed_exit) is int and observed_exit == 0


def can_wait_after_progress(request, value, token=None, observed_exit=None):
    return (value is not None and value['state'] == 'primary-ready' and
            request['mode'] not in (BLOCKED, RELOADED, TITLE_RECORDS) and
            later_run_exited_cleanly(request, token, observed_exit))


def complete_stopped(request, *, can_write, compare_only=False, current_token=None, observed_exit=None):
    root = Path(request['context']['root'])
    with import_lock(root):
        require_no_conversion(root)
        if compare_only:
            require_stopped()
            actual = read_request(root, request['path'], enroll=False)
            if actual['request_sha256'] != request['request_sha256']:
                raise EvidenceChangedError('恢复请求在核对前发生变化，已保留原记录。')
            request = actual
            check_journal_location(request)
            if request['mode'] == TITLE_RECORDS:
                return check_stopped_without_replay(request)
            current, status = compare_generations(request)
            if 'different-character' in status.values():
                raise ValueError('主存档或游戏备份与本次恢复的角色不一致，请保留两代存档与恢复记录。')
            primary_status = status[f'userdata{request["slot"]:04}']
            later_progress = (primary_status == 'unverified' and
                              can_wait_after_progress(request, read_job(request),
                                                      current_token, observed_exit))
            if primary_status != 'verified' and not later_progress:
                raise ValueError('无法确认主存档已恢复。请保留两代存档与恢复记录，不要删除或改名。')
            return check_stopped_without_replay(request, current=current, later_progress=later_progress)
        return _complete_stopped(request, can_write=can_write)


def require_no_conversion(root):
    if ((root/'.save-conversion.lock').exists() or
            (root/'.save-conversion-pending.json').exists()):
        raise ValueError('存档转换尚未完成，已停止世界恢复操作。')


def _complete_stopped(request, *, can_write):
    require_stopped()
    if request['mode'] == TITLE_RECORDS:
        if not can_write:
            return check_stopped_without_replay(request)
        # Only the original worker can still have the exact live quit seal.
        # Any native load attempt or character creation irrevocably clears it.
        # No re-entry took place: hand the unchanged plan back to the QUIT path.
        packet = bytearray(stable(request['path']))
        if sha(packet) != request['request_sha256']:
            raise EvidenceChangedError('本次退出记录已变化，请重新核对。')
        struct.pack_into('<I', packet, 52, QUIT)
        atomic(request['path'], packet)
        request = read_request(Path(request['context']['root']), request['path'], enroll=False)
    if request['mode'] in (BLOCKED, RELOADED):
        return check_stopped_without_replay(request)
    value = prepare(request)
    if finish(request, value, proof='both save generations already contain the recovery'):
        return 'verified'
    job = job_for(request)
    if value['state'] == 'primary-ready':
        primary = f'userdata{request["slot"]:04}'
        if not file_matches(request, value, primary, stable(request['saves']/primary)):
            raise ValueError('无法确认主存档已恢复，已保留两代存档供复核。')
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
                raise EvidenceChangedError('退出后的存档又发生变化，未写入。')
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


def compare_generations(request):
    value = read_job(request)
    job = job_for(request)
    if value is not None and stable(job/'journal.bin') != request['journal_bytes']:
        raise ValueError('恢复任务的世界记录与当前请求不匹配。')
    slot = request['slot']
    current, status = {}, {}
    for name in (f'userdata{slot:04}', f'backup{slot:04}'):
        try:
            raw = stable(request['saves']/name)
        except FileNotFoundError as error:
            require_stable_missing(error)
            status[name] = 'missing'
            continue
        current[name] = raw
        try:
            same_role = identity(raw) == request['identity']
        except ValueError:
            status[name] = 'invalid'
            continue
        if not same_role:
            status[name] = 'different-character'
            continue
        if value is not None:
            if image(stable(job/'before'/name), request) != stable(job/'expected'/name):
                raise ValueError('恢复任务的目标与已核对的恢复计划不一致。')
        matches = request['mode'] != TITLE_RECORDS and (
            image(raw, request) == raw if value is None else file_matches(request, value, name, raw))
        status[name] = 'verified' if matches else 'unverified'
    if (value is not None and set(current) == set(status) and
            set(status.values()) <= {'verified', 'unverified'} and
            saved_matches(request, value, current=current)):
        status = dict.fromkeys(status, 'verified')
    return current, status


def check_journal_location(request):
    journal = request['journal']
    waiting = journal.with_suffix('.bin.awaiting-save')
    if journal.exists() and waiting.exists():
        raise ValueError('同一槽位存在两个待处理的世界记录。')
    if not journal.exists() and not waiting.exists():
        if stable(journal.with_suffix('.bin.restored')) != request['journal_bytes']:
            raise ValueError('世界记录与归档副本不匹配。')


def check_stopped_without_replay(request, *, current=None, later_progress=False):
    # These modes can establish what is already on disk, never authorize a save write.
    slot = request['slot']
    primary, backup = f'userdata{slot:04}', f'backup{slot:04}'
    try:
        if current is None:
            current = {name: stable(request['saves']/name) for name in (primary, backup)}
        if set(current) != {primary, backup}:
            raise ValueError('存档代次不完整。')
        identities = {identity(raw) for raw in current.values()}
    except (OSError, ValueError) as error:
        require_content_error(error)
        raise ValueError('无法核对主存档和游戏备份，请保留两代存档与恢复记录。') from error
    if identities != {request['identity']}:
        raise ValueError('主存档或游戏备份与本次恢复的角色不一致，请保留两代存档与恢复记录。')
    if any(stable(request['saves']/name) != raw for name, raw in current.items()):
        raise EvidenceChangedError('核对期间存档发生变化，已保留原记录。')
    value = prepare(request, current=current)
    if finish(request, value, proof='both save generations verified without replay', current=current):
        return 'verified'
    if request['mode'] == TITLE_RECORDS:
        raise ValueError('同一角色从标题读回的记录或后续两代存档尚未核实。当前进度和个人世界记录已保留。')
    # Only an earlier, completed primary recovery may wait through later gameplay.
    if not (later_progress and value['state'] == 'primary-ready') and not file_matches(
            request, value, primary, current[primary]):
        raise ValueError('无法确认主存档已恢复。请保留两代存档与恢复记录，不要删除或改名。')
    job = job_for(request)
    if value['state'] != 'primary-ready':
        value.update(state='primary-ready', proof='verified current primary without replay; awaiting native backup save')
        json_file(job/'job.json', value)
    mark_waiting(request, job)
    return 'waiting-backup-save'


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
    failure = None
    for path in entries(folder/'requests', '*.bin'):
        try:
            poll_request(root, folder, token, path)
        except Exception as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure


def poll_request(root, folder, token, path):
    with plain_path(path).open('rb') as stream:
        data = stream.read(HEADER.size)
    if len(data) < HEADER.size:
        return
    seq, request_token, slot, mode, _, _ = request_header(data)
    job = folder/'jobs'/f'{request_token}-{slot}-{seq}'/'job.json'
    job_state = json.loads(stable(job))['state'] if job.exists() else None
    if job_state == 'primary-ready':
        key = (str(folder), request_token, slot, seq, 'backup')
        now = time.monotonic()
        if now < _next_poll.get(key, 0):
            return
        _next_poll[key] = now+1
        request = read_request(root, path, enroll=False)
        finish(request, prepare(request), proof='native game save advanced both complete generations',
               running_token=token)
        return
    if request_token != token:
        return
    if job_state == 'complete':
        ack = folder/'acks'/f'{slot}.ack'
        if ack.exists() and stable(ack) == struct.pack('<Q', seq):
            return
        confirm_complete(root, path, running_token=token)
        return
    if mode == CAPTURE:
        context = json.loads(stable(folder/'runs'/f'{token}.json'))
        if str(slot) not in context['identities']:
            read_request(root, path)
        return
    if mode not in (RESTORE, APPLIED, RELOADED):
        return
    ready = folder/'ready'/f'{slot}.ready'
    if mode == RESTORE and ready.exists() and stable(ready) == struct.pack('<Q', seq):
        value = json.loads(stable(job))
        packet = stable(path)
        if sha(packet[:52]+packet[56:]) != value['plan_sha256']:
            raise ValueError('已准备的世界恢复请求内容发生变化。')
        return
    key = (str(folder), token, slot, seq)
    if mode in (APPLIED, RELOADED):
        now = time.monotonic()
        if now < _next_poll.get(key, 0):
            return
        _next_poll[key] = now+1
    request = read_request(root, path, enroll=False)
    value = prepare(request)
    if mode == RESTORE:
        ready = folder/'ready'/f'{slot}.ready'
        expected = struct.pack('<Q', seq)
        if not ready.exists() or stable(ready) != expected:
            atomic(ready, expected)
    else:
        finish(request, value, proof='read-only comparison of both actual save generations',
               running_token=token)


def confirm_complete(root, path, *, write=True, running_token=None):
    request = read_request(root, path, enroll=False)
    journal = request['journal']
    value = read_job(request)
    if (journal.exists() or journal.with_suffix('.bin.awaiting-save').exists() or
            value is None or value['state'] != 'complete'):
        raise ValueError('旧归档状态与实际世界记录不符，已保留。')
    ack = request['folder']/'acks'/f'{request["slot"]}.ack'
    if ack.exists() and stable(ack) == struct.pack('<Q', request['seq']):
        # Import may already have backed up the retired .restored file.
        return
    if stable(journal.with_suffix('.bin.restored')) != request['journal_bytes']:
        raise ValueError('旧归档状态与实际世界记录不符，已保留。')
    # Only finish() publishes these proofs after saved_matches verifies both saves.
    # Reissuing the same ack is a metadata operation, using the archived evidence.
    committed = value.get('proof') in (
        'both save generations already contain the recovery',
        'both save generations verified without replay',
        'native game save advanced both complete generations',
        'read-only comparison of both actual save generations',
    )
    if committed:
        job = job_for(request)
        for name, expected in value['files'].items():
            for generation in ('before', 'expected'):
                digest = expected['after' if generation == 'expected' else 'before']
                if sha(stable(job/generation/name)) != digest:
                    raise ValueError('恢复任务的备份或目标副本发生变化。')
    elif not saved_matches(request, value, running_token=running_token):
        raise ValueError('旧归档缺少确认，当前两代存档无法重新核验。')
    if write:
        confirm(request['folder'], request['slot'], request['seq'])


def pending_journals(root):
    folder = plain_path(Path(root)/'mod/own-world')
    return set(entries(folder, 'own-save-*.bin')) | set(entries(folder, 'own-save-*.bin.awaiting-save'))


def unmatched_jobs(folder, requested, *, supersede=False):
    pattern = re.compile(r'([0-9a-f]{32})-([0-9])-([1-9][0-9]*)')
    jobs = entries(folder/'jobs')
    newest = {}
    if supersede:
        evidence = set(requested)
        for job in jobs:
            try:
                if stat.S_ISDIR(plain_path(job).stat().st_mode):
                    evidence.add(job.name)
            except (OSError, ValueError) as error:
                require_content_error(error)
                continue
        for job_id in evidence:
            if match := pattern.fullmatch(job_id):
                key = (match[1], int(match[2]))
                newest[key] = max(newest.get(key, 0), int(match[3]))
    for job in jobs:
        match = pattern.fullmatch(job.name)
        if not match or job.name in requested:
            continue
        token, slot, seq = match[1], int(match[2]), int(match[3])
        manifest = job/'job.json'
        try:
            value = json.loads(stable(manifest))
            state = value.get('state')
            if state in ('complete', 'superseded'):
                continue
            later = newest.get((token, slot), 0)
            if (state == 'prepared' and later > seq and
                    value.get('request_identity') == [token, slot, seq]):
                try:
                    attempted = next(plain_path(job/'attempts').iterdir(), None) is not None
                except FileNotFoundError as error:
                    require_stable_missing(error)
                    attempted = False
                if not attempted:
                    json_file(manifest, dict(value, state='superseded', superseded_by=later))
                    continue
        except (OSError, ValueError, AttributeError) as error:
            require_content_error(error)  # Only stable content/absence is a blocker.
        yield job.name, slot


def finalize(root, token, *, quit_sequence=0, observed_exit=None, exit_record_failure=None):
    from save_resolution import require_launch_clear
    with installation_guard(root):
        require_launch_clear(root, strict=True)
        return _finalize(root, token, quit_sequence=quit_sequence, observed_exit=observed_exit,
                         exit_record_failure=exit_record_failure)


def _finalize(root, token, *, quit_sequence=0, observed_exit=None, exit_record_failure=None):
    folder, _ = layout(root)
    results = []
    waiting = set()
    requested = set()
    failure = None
    for path in entries(folder/'requests', '*.bin'):
        try:
            packet = stable(path)
            seq, run_token, slot, mode, count, _ = request_header(packet)
            if len(packet) != HEADER.size+count*ROW.size or path.name != f'world-save-{slot}.bin':
                raise ValueError('世界恢复请求长度或槽位文件名不匹配。')
            requested.add(f'{run_token}-{slot}-{seq}')
            job = folder/'jobs'/f'{run_token}-{slot}-{seq}'/'job.json'
            job_state = json.loads(stable(job))['state'] if job.exists() else None
            if token is not None and run_token != token and job_state != 'primary-ready':
                continue
            if job_state == 'complete':
                with import_lock(root):
                    require_no_conversion(Path(root))
                    require_stopped()
                    confirm_complete(root, path)
                continue
            request = read_request(root, path, enroll=run_token == token)
            recorded_exit = request['context'].get('exit_code')
            clean_exit = (run_token == token and type(recorded_exit) is int and recorded_exit == 0 and
                          (observed_exit is None or observed_exit == 0))
            # A live worker may substitute only the exit-code write that failed,
            # for this exact sealed QUIT. Never substitute a recorded exit code.
            live_quit = (run_token == token and mode in (QUIT, TITLE_RECORDS) and
                         type(quit_sequence) is int and seq == quit_sequence and
                         type(observed_exit) is int and observed_exit == 0 and
                         'exit_code' not in request['context'] and
                         isinstance(exit_record_failure, ExitRecordWriteError) and
                         exit_record_failure.token == token and exit_record_failure.exit_code == 0)
            if mode == TITLE_RECORDS and run_token == token and observed_exit not in (None, 0):
                raise ValueError('游戏进程未正常结束，保留本轮标题读档凭据供复核。')
            result = complete_stopped(request, compare_only=run_token != token,
                                      current_token=token, observed_exit=observed_exit,
                                      can_write=live_quit or (clean_exit and (mode == APPLIED or
                                                                 (mode in (QUIT, TITLE_RECORDS) and seq == quit_sequence))))
            results.append(result)
            if 'waiting-backup-save' in result:
                waiting.add(request['journal'].with_suffix('.bin.awaiting-save'))
        except Exception as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise failure
    missing = '仍有缺少完整恢复凭据的世界记录，已保留；不要删除或手动改名。'
    if pending_journals(root) - waiting:
        raise ValueError(missing)
    if (folder/'jobs').exists():
        with import_lock(root):
            require_no_conversion(Path(root))
            require_stopped()
            if next(unmatched_jobs(folder, requested, supersede=True), None):
                raise ValueError(missing)
    return results


def begin(root):
    from save_resolution import require_launch_clear
    with installation_guard(root):
        require_launch_clear(root, strict=True)
        return _begin(root)


def _begin(root):
    require_stopped()
    if (layout(root)[0]/'review-required.json').exists():
        raise ReviewRequired('世界恢复记录正在核查，请打开「联机存档检查」查看标记来源。')
    try:
        finalize(root, None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        require_content_error(error)
        raise ReviewRequired(f'{error} 联机存档检查尚未结案，请在存档页选择本机处理方式。') from error
    folder, saves = layout(root)
    token = uuid.uuid4().hex
    identities = primary_identities(root)
    json_file(folder/'runs'/f'{token}.json', {'schema': 1, 'token': token,
              'root': str(plain_path(root)), 'identities': identities, 'started_ns': time.time_ns()})
    env = {'THEHUNT_WORLD_RECOVERY_V2': '1', 'THEHUNT_WORLD_RECOVERY_DIR': str(folder),
           'THEHUNT_WORLD_RECOVERY_TOKEN': token}
    env.update(save_proof.prepare(root, token))
    return env


def process_exited(root, token, exit_code):
    if type(exit_code) is not int:
        raise ValueError('游戏进程的退出码未知。')
    folder, _ = layout(root)
    path = folder/'runs'/f'{token}.json'
    value = json.loads(stable(path))
    if value['token'] != token or value['root'] != str(plain_path(root)):
        raise ValueError('退出码与本次游戏会话不匹配。')
    value['exit_code'] = exit_code
    try:
        json_file(path, value)
    except PermissionError as error:
        raise ExitRecordWriteError(token, exit_code, error) from error


def review_items(root):
    """Inspect evidence without preparing jobs, enrolling identities or issuing ACKs."""
    folder, saves = layout(root)
    items = []
    marker = plain_path(folder/'review-required.json')
    if marker.exists():
        item = {'state': 'review-required', 'kind': 'marker', 'reason': '现有复核标记仍阻止启动。'}
        try:
            value = json.loads(stable(marker))
            item['origin'] = {key: value[key][:300] for key in ('origin', 'reason', 'source')
                              if isinstance(value.get(key), str)}
        except (OSError, ValueError, AttributeError) as error:
            require_content_error(error)
            item['origin'] = {'reason': '标记不可读或格式未知。'}
        items.append(item)
    journals = pending_journals(root)
    requested = set()
    for path in entries(folder/'requests', '*.bin'):
        item = {'state': 'review-required', 'kind': 'request', 'file': path.name}
        try:
            data = stable(path)
            item['request_sha256'] = sha(data)
            seq, token, slot, mode, _, _ = request_header(data)
            requested.add(f'{token}-{slot}-{seq}')
            item.update(slot=slot, mode=MODE_NAMES[mode-1], run=token, sequence=seq)
            request = read_request(root, path, enroll=False)
            value = read_job(request)
            item.update(identity=request['identity'], plan_sha256=request['plan_sha256'],
                        recorded_exit=request['context'].get('exit_code'))
            journal = request['journal']
            associated = {journal, journal.with_suffix('.bin.awaiting-save')} & journals
            if value is not None and value['state'] == 'complete':
                confirm_complete(root, path, write=False)
                continue
            check_journal_location(request)
            current, status = compare_generations(request)
            item['generations'] = {name: {'state': state, 'sha256': sha(current[name]) if name in current else None}
                                   for name, state in status.items()}
            journals.difference_update(associated)
            if set(status.values()) == {'verified'}:
                item.update(state='verified', reason='两代均有恢复证据。可重试正常启动以归档。')
            elif (status[f'userdata{slot:04}'] == 'verified' and
                  status[f'backup{slot:04}'] == 'unverified' and associated):
                item.update(state='waiting-backup', reason='主档已核验。可重试正常启动，并用同一角色正常保存以更新游戏备档。')
            elif (associated and status[f'userdata{slot:04}'] == 'unverified' and
                  status[f'backup{slot:04}'] in ('verified', 'unverified') and
                  can_wait_after_progress(request, value)):
                item.update(state='waiting-backup', reason='主档曾完成核验，此后同一角色正常退出。可继续游玩，等待游戏更新备档。')
            else:
                item['reason'] = '现有证据不足以安全启动原档；请保留原档并核对证据副本。'
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            require_content_error(error)
            item['reason'] = str(error)
            if 'slot' in item and 'generations' not in item:
                item['generations'] = unverified_generations(saves, item['slot'])
        items.append(item)
    for path in sorted(journals):
        item = {'state': 'review-required', 'kind': 'orphan', 'file': path.name,
                'reason': '存在未关联到可核验请求的世界记录。'}
        match = re.fullmatch(r'own-save-([0-9])\.bin(?:\.awaiting-save)?', path.name)
        if match:
            item['slot'] = int(match[1])
            item['generations'] = unverified_generations(saves, item['slot'])
        items.append(item)
    for job_id, slot in unmatched_jobs(folder, requested):
        items.append({'state': 'review-required', 'kind': 'orphan-job', 'file': job_id, 'slot': slot,
                      'reason': '存在缺少请求的未结案恢复任务。',
                      'generations': unverified_generations(saves, slot)})
    return items


def unverified_generations(saves, slot):
    result = {}
    for name in (f'userdata{slot:04}', f'backup{slot:04}'):
        try:
            result[name] = {'state': 'unverified', 'sha256': sha(stable(saves/name))}
        except FileNotFoundError as error:
            require_stable_missing(error)
            result[name] = {'state': 'missing', 'sha256': None}
        except (OSError, ValueError) as error:
            require_content_error(error)
            result[name] = {'state': 'unreadable', 'sha256': None}
    return result


def require_review_clear_for_transfer(root):
    from save_resolution import require_launch_clear
    require_launch_clear(root)
    # The transfer paths already reject pending own-world journals. Also guard
    # moved/incomplete evidence whose journal is missing, and explicit markers.
    folder, _ = layout(root)
    if (plain_path(folder/'review-required.json').exists() or
            (not pending_journals(root) and review_items(root))):
        raise ValueError('有未结案的世界恢复记录，请先打开「联机存档检查」；已停止存档导入、转换和导出。')


def evidence_inventory(root):
    folder, saves = layout(root)
    sources = {'world-recovery': folder, 'own-world': Path(root)/'mod/own-world', 'save-generations': saves}
    return {label: (source, snapshot(source)) for label, path in sources.items()
            if (source := plain_path(path)).exists()}


def evidence_plan(inventory):
    # Include file identities and timestamps as well as hashes: a replace or
    # an interrupted append after review requires a fresh confirmation.
    exact = {label: (str(source), files) for label, (source, files) in inventory.items()}
    return sha(json.dumps(exact, sort_keys=True).encode('utf-8'))


def review(root):
    require_stopped()
    before = evidence_inventory(root)
    items = review_items(root)
    require_stopped()
    if evidence_inventory(root) != before:
        raise EvidenceChangedError('复核期间证据发生变化，请重新读取。')
    return {'ok': True, 'items': items, 'digest': evidence_plan(before) if items else None}


def _status_stamp(path):
    try:
        value = plain_path(path).stat()
    except FileNotFoundError as error:
        require_stable_missing(error)
        return None
    if not stat.S_ISREG(value.st_mode):
        raise OperationUnavailable('处理记录或角色路径不是普通文件，请检查目录设置。')
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _status_gates(root):
    from save_resolution import GATE, _control
    folder, _ = layout(root)
    paths = (root/GATE, folder/'review-required.json', root/'.save-import.lock',
             root/'.save-conversion.lock', root/'.save-conversion-pending.json')
    # Presence blocks launch even for legacy or opaque intents. Read stably so
    # an inaccessible marker or one that disappeared is reported as a read error.
    return {path: _control(path)[0] for path in paths if _status_stamp(path) is not None}


def _status_primaries(saves):
    return {path: _status_stamp(path) for path in entries(saves, 'userdata000[0-9]')}


def recovery_status(root):
    """Read-only home indicator. Normal startup independently rechecks recovery."""
    root = plain_path(root)
    if not stat.S_ISDIR(root.stat().st_mode):
        raise OperationUnavailable('当前安装目录无法读取。')
    with installation_guard(root, read_only=True):
        require_stopped('查询恢复状态')
        changed = '查询期间恢复记录或角色存档发生变化，请重新检查。'
        gates = _status_gates(root)
        status = 'blocked'
        if not gates:
            _, saves = layout(root)
            primaries = _status_primaries(saves)
            items = review_items(root)
            fingerprints = {}
            try:
                if primaries:
                    primary_identities(root, fingerprints=fingerprints)
            except ReviewRequired:
                pass  # A stable nonzero primary failed the normal character check.
            else:
                if not items:
                    status = 'clear'
                elif all(row['state'] in ('verified', 'waiting-backup') for row in items):
                    status = 'safe'
            # Empty reviews need no full inventory of saves, runs or archived jobs.
            checked = review(root)['items'] if items else review_items(root)
            if (checked != items or _status_primaries(saves) != primaries or
                    any(sha(plain_path(path).read_bytes()) != digest for path, digest in fingerprints.items())):
                raise EvidenceChangedError(changed)
        if _status_gates(root) != gates:
            raise EvidenceChangedError(changed)
        require_stopped('查询恢复状态')
        return {'ok': True, 'status': status}


def export_evidence(root, expected):
    root = plain_path(root)
    with import_lock(root):
        require_no_conversion(root)
        require_stopped()
        inventory = evidence_inventory(root)
        if not isinstance(expected, str) or expected != evidence_plan(inventory):
            raise EvidenceChangedError('复核后来源文件发生变化，请重新读取并确认。')
        items = review_items(root)
        if not items:
            raise ValueError('当前没有需要保留的复核项目。')
        base = plain_path(root/'save-backups'/'world-recovery-evidence')
        target = base/uuid.uuid4().hex
        stage = target.with_suffix('.partial')
        stage.mkdir(parents=True)
        for label, (source, files) in inventory.items():
            copy_verified(source, stage/label, files)
        json_file(stage/'manifest.json', {
            'schema': 1, 'kind': 'evidence-only', 'plan_sha256': expected, 'items': items,
            'sources': {label: {'path': str(source), 'inventory': content_inventory(files)}
                        for label, (source, files) in inventory.items()},
        })
        require_stopped()
        if evidence_inventory(root) != inventory:
            raise EvidenceChangedError('复制期间原始证据发生变化，未发布复核副本。')
        stage.rename(target)
        return {'ok': True, 'path': str(target)}


if __name__ == '__main__':
    try:
        root = Path(__file__).resolve().parent
        if sys.argv[1:] == ['recovery-status']:
            result = recovery_status(root)
        elif sys.argv[1:] == ['recovery-details']:
            from recovery_details import recovery_details
            result = recovery_details(root)
        elif sys.argv[1:] == ['review']:
            result = review(root)
        elif sys.argv[1:] == ['copy-evidence']:
            result = export_evidence(root, json.load(sys.stdin)['digest'])
        else:
            raise ValueError('请选择复核或复制证据。')
        print(json.dumps(result, ensure_ascii=True))
    except (OSError, ValueError, KeyError, TypeError) as error:
        if sys.argv[1:] == ['recovery-details']:
            print(json.dumps({'ok': False, 'schema': 1, 'status': 'unknown', 'retryable': True,
                              'error': f'暂时无法读取当前存档和恢复详情，请稍后重试。{error}'}, ensure_ascii=True))
        elif sys.argv[1:] == ['recovery-status']:
            print(json.dumps({'ok': False, 'retryable': True,
                              'error': f'暂时无法判断恢复状态，请稍后重试。{error}'}, ensure_ascii=True))
        else:
            print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=True))
        sys.exit(1)
