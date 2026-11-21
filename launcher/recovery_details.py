"""Read-only current-slot information. World verification always uses the original verifier."""
import json
from pathlib import Path

import backup_characters as characters
import save_resolution as resolution
import world_recovery as world
from save_transfer import OperationUnavailable, installation_guard, plain_path, require_stopped


def _slots(root, cache):
    _, folder = world.layout(root)
    slots, bad, stamps = [], [], []
    for slot in range(10):
        pair = []
        for stem in ('userdata', 'backup'):
            path = plain_path(folder/f'{stem}{slot:04}')
            stamp = world._status_stamp(path)
            info, detail = None, {}
            if stamp is not None:
                raw = world.stable(path)
                digest = world.sha(raw)
                info = {'bytes': len(raw), 'sha256': digest}
                if info != characters.EMPTY_SLOT:
                    if digest not in cache:
                        cache[digest] = characters._character(raw)
                    detail = cache[digest]
                    if stem == 'userdata' and detail.get('issue') == 'unrecognized' and any(raw):
                        bad.append(slot)
                if stamp != world._status_stamp(path):
                    raise world.EvidenceChangedError('读取角色时文件已替换，请重新检查。')
            stamps.append((stamp, info))
            pair.append(characters.generation(info, detail))
        slots.append(characters.display_slot(slot, *pair))
    return slots, bad, stamps


def _reason(code, message, slot=None, state='blocked'):
    return {'code': code, 'state': state, 'slot': slot, 'message': message}


def _transaction_reason(path, expected):
    info, raw = resolution._control(path)
    if info != expected:
        raise world.EvidenceChangedError('读取期间存档操作发生变化，请重新检查。')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        value = None
    op = value.get('operation') if isinstance(value, dict) else None
    legacy_import = (isinstance(value, dict) and op is None and value.get('schema') == 1
                     and value.get('state') == 'prepared'
                     and {'destination', 'stage', 'backup', 'original', 'expected'} <= value.keys())
    if op == 'import' or legacy_import:
        return _reason('import-pending', '上次导入尚未完成，请在存档页处理。')
    if op == 'restore':
        return _reason('restore-pending', '上次恢复备份尚未完成，请在存档页处理。')
    if op == resolution.OPERATION:
        return _reason('choice-pending', '上次存档选择尚未完成，请在存档页继续或保全现场。')
    return _reason('save-operation-pending', '存在未结束的存档操作，具体类型暂时无法确认。')


def _current_action(root):
    # _plan has no transaction recovery. Its guards reject every active import/conversion lock.
    plan, data = resolution._plan(root)
    for option in plan['options']:
        if option['id'] in ('accept-bound', 'use-current'):
            return {'id': option['id'], 'slot': data['bound']['slot'] if option['id'] == 'accept-bound' else None,
                    'digest': plan['digest'], 'selection': plan['selection'], 'requires_confirmation': True,
                    'risk': option['description']}
    return None


def recovery_details(root):
    root = plain_path(Path(root)).resolve()
    if not root.is_dir():
        raise OperationUnavailable('暂时无法读取安装目录。')
    with installation_guard(root, read_only=True):
        require_stopped('读取当前存档详情')
        gates = world._status_gates(root)
        lock = root/'.save-import.lock'
        converting = any(root/name in gates for name in ('.save-conversion.lock', '.save-conversion-pending.json'))
        reasons = []
        if lock in gates:
            reasons.append(_transaction_reason(lock, gates[lock]))
        if converting:
            reasons.append(_reason('conversion-pending', '存档转换尚未完成，请在转换页继续原操作。'))
        if root/resolution.GATE in gates:
            reasons.append(_reason('choice-review', '换回或处理过的存档仍需在存档页重新确认。'))
        cache = {}
        slots, bad, stamps = _slots(root, cache)
        for slot in bad:
            reasons.append(_reason('primary-check-failed', f'槽位 {slot+1} 的当前主档未通过结构检查。', slot))
        items = world.review_items(root)
        for item in items:
            state = item['state']
            reasons.append(_reason('world-recovery', item['reason'], item.get('slot'), state))
        status = ('blocked' if any(r['state'] not in ('verified', 'waiting-backup') for r in reasons)
                  else 'safe' if reasons else 'clear')
        action = None
        if status == 'blocked' and lock not in gates and not converting and not bad:
            action = _current_action(root)
        # Fast on a fresh installation; no scan of old backups unless a real choice is needed.
        again = world.review(root)['items'] if items else world.review_items(root)
        if again != items or _slots(root, cache)[2] != stamps or world._status_gates(root) != gates:
            raise world.EvidenceChangedError('查询期间存档或待核记录发生了变化，请重新检查。')
        require_stopped('读取当前存档详情')
        return {'ok': True, 'schema': 1, 'status': status, 'reasons': reasons, 'slots': slots,
                'current_action': action}


def exit_disk_progress(root, token):
    """A run token plus its validated request and native identity bind a slot, even after a title reload."""
    folder, saves = world.layout(root)
    messages = []
    with installation_guard(root, read_only=True):
        require_stopped('读取退出后的磁盘进度')
        for path in world.entries(folder/'requests', '*.bin'):
            raw_request = world.stable(path)
            seq, run, slot, *_ = world.request_header(raw_request)
            if run != token:
                continue
            # Read after finalize(): a verified run may have moved the live journal to its
            # archived location. read_request uses the matching, checked job when necessary.
            req = world.read_request(root, path, enroll=False)
            if ((req['token'], req['slot'], req['seq']) != (run, slot, seq)
                    or req['request_sha256'] != world.sha(raw_request)):
                raise world.EvidenceChangedError('退出后的运行记录发生变化。')
            name = saves/f'userdata{slot:04}'
            raw = world.stable(name)
            if world.identity(raw) != req['identity']:
                continue
            detail = characters._character(raw)
            fields = []
            if detail.get('name'):
                fields.append(detail['name'])
            if detail.get('level') is not None:
                fields.append(f"{detail['level']} 级")
            if detail.get('echoes') is not None:
                fields.append(f"{detail['echoes']} 回响")
            if world.stable(name) != raw or world.stable(path) != raw_request:
                raise world.EvidenceChangedError('退出后读取期间角色或运行记录已变化。')
            if fields:
                messages.append(f"槽位 {slot+1} 当前主档读到 " + '，'.join(fields) + '。')
        require_stopped('读取退出后的磁盘进度')
    return ''.join(messages)
