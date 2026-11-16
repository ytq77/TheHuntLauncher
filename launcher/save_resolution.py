"""Explicit local save choices. Originals survive; interrupted choices only go forward.

The five data roots and the separate review gate are fixed by this module. Neither
old world records nor UI requests supply filesystem paths. This is not a world
repairer: it never modifies role bytes, creates THWR proof, or merges branches.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

import offline_aux
import offline_save
import save_backups as backups
import save_transfer as transfer
import world_recovery as world

ROOT = Path(__file__).resolve().parent
TREES = {'savedata': 'data/user/savedata', 'world-recovery': 'data/user/world-recovery',
         'own-world': 'mod/own-world', 'guest-items': 'mod/guest-items',
         'shared-progress': 'mod/shared-progress'}
GATE = 'data/user/save-resolution-review.json'
OPERATION = 'recovery-choice'
TOKEN = re.compile('[0-9a-f]{32}')
ACTIONS = {'accept-bound', 'accept-all', 'restore-all', 'restore-case', 'use-current', 'preserve'}
TEXT_VERSION = 1
MAX_RECORD_BYTES = 16 * 1024 * 1024


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(path):
    path = transfer.plain_path(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError('处理记录过大，已保留原件。')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('处理记录格式不正确，已保留原件。')
    return value, raw


def _exists(path):
    try:
        transfer.plain_path(path).lstat()
        return True
    except FileNotFoundError:
        return False


def require_launch_clear(root):
    """Only the fixed gate/A lock, no live-save reads or old embedded paths."""
    root = transfer.plain_path(root)
    if _exists(root/GATE):
        raise ValueError('这份存档需要您重新选择。请在存档页点击“处理这个异常”；不会自动启动或回退。')
    lock = root/'.save-import.lock'
    if _exists(lock):
        try:
            value, _ = _json(lock)
        except (OSError, ValueError):
            return  # The ordinary, stricter unknown-import-lock guard remains responsible.
        if value.get('operation') == OPERATION:
            raise ValueError('上次存档选择尚未处理完。请在存档页继续原来的选择；游戏尚未启动。')


def _root(root):
    root = transfer.plain_path(Path(root)).resolve()
    if not root.is_dir() or not transfer.plain_path(root/'data/user').is_dir():
        raise ValueError('此安装尚未初始化，不能处理存档。请先完成安装初始化。')
    for relative in (*TREES.values(), GATE, 'save-backups/recovery-choice'):
        transfer.plain_path(root/relative)
    return root


def _guards(root, *, own_lock=None):
    transfer.require_stopped('处理存档')
    for name in ('.save-conversion.lock', '.save-conversion-pending.json'):
        if _exists(root/name):
            raise ValueError('存档转换尚未结束，请先在转换页继续原来的操作，再重新检查。不会更换存档。')
    lock = root/'.save-import.lock'
    if _exists(lock) and (own_lock is None or lock.read_bytes() != own_lock):
        raise ValueError('另一个导入、恢复或存档选择尚未结束，请先在对应页面继续；不会清除它的记录。')


def _file(path):
    path = transfer.plain_path(path)
    try:
        before = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('存档或处理记录不是普通文件，请检查文件占用和目录设置后重新检查。')
    raw = path.read_bytes()
    after = path.stat()
    identity = lambda s: [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    if identity(before) != identity(after):
        raise ValueError('读取期间文件发生变化，请正常退出占用它的程序后重新检查。')
    return {'identity': identity(after), 'bytes': len(raw), 'sha256': _sha(raw)}


def _tree(path):
    if not _exists(path):
        return None
    return transfer.snapshot(path)


def _capture(root):
    return {'trees': {name: _tree(root/relative) for name, relative in TREES.items()},
            'gate': _file(root/GATE)}


def _content_file(value):
    return None if value is None else {key: value[key] for key in ('bytes', 'sha256')}


def _content(capture):
    return {'trees': {name: None if value is None else backups._inventory(value)
                      for name, value in capture['trees'].items()},
            'gate': _content_file(capture['gate'])}


def _validate_content(value):
    if not isinstance(value, dict) or set(value) != {'trees', 'gate'} or set(value['trees']) != set(TREES):
        raise ValueError('处理记录的固定范围不匹配，已保留现场。')
    for tree in value['trees'].values():
        if tree is not None:
            backups._validate_inventory(tree)
    if value['gate'] is not None:
        backups._validate_inventory({'directories': [], 'files': {'gate': value['gate']}})


def _readability(folder):
    """Inspect every generation; parser rejection is not a claim of permanent loss."""
    game = folder/'1/CUSA00207/SPRJ0005'
    rows, readable = [], 0
    for slot in range(10):
        row = {'slot': slot, 'name': '', 'primary': 'missing', 'backup': 'missing'}
        identities = []
        for stem, key in (('userdata', 'primary'), ('backup', 'backup')):
            path = transfer.plain_path(game/f'{stem}{slot:04}')
            if not _exists(path):
                identities.append(None)
                continue
            raw = world.stable(path)  # An IO failure is a hard failure, not a bad role.
            if not any(raw) and len(raw) == 0x140000:
                row[key] = 'unused'
                identities.append(None)
                continue
            try:
                role = offline_save.parse(raw)
                offline_aux.flag_bank(raw)
            except (ValueError, IndexError) as error:
                row[key] = 'unreadable'
                row[key+'_detail'] = '当前版本无法读取：' + str(error)
                identities.append(None)
                continue
            row[key] = 'readable'
            readable += 1
            identities.append(_sha(raw[role.username:role.username+32]+raw[role.face+4:role.face+239]))
            if not row['name']:
                try:
                    row['name'] = raw[role.username:role.username+32].decode('utf-16-le').split('\0')[0]
                except UnicodeError:
                    row['name'] = '姓名未识别'
        if all(identities) and identities[0] != identities[1]:
            row['identity_conflict'] = True
        if (row['primary'], row['backup']) != ('missing', 'missing'):
            rows.append(row)
    valid, reason = True, ''
    try:
        transfer.validate_savedata(folder)
    except ValueError as error:
        valid, reason = False, str(error)
    # The original importer skips a backup when its primary is all zero. A's
    # stronger whole-branch choices must account for every observed generation.
    inconsistent = [row['slot']+1 for row in rows
                    if row.get('identity_conflict') or 'unreadable' in (row['primary'], row['backup']) or
                    row['primary'] in ('unused', 'missing') and row['backup'] == 'readable']
    if inconsistent:
        valid = False
        reason = (reason+' ' if reason else '') + '角色槽位 '+ '、'.join(map(str, inconsistent)) + ' 的主备代次不能完整核对；不代表这些进度永久丢失。'
    return {'characters': rows, 'readable_generations': readable, 'valid': valid, 'reason': reason}


def _case_path(root, token):
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('请选择列表中的处理前存档。')
    return transfer.plain_path(root/'save-backups/recovery-choice'/token)


def _original_content(case):
    return {'trees': {name: None if (v := _tree(case/'original'/name)) is None else backups._inventory(v)
                      for name in TREES}, 'gate': _content_file(_file(case/'original/gate'))}


def _load_manifest(root, token):
    case = _case_path(root, token)
    value, raw = _json(case/'manifest.json')
    if (value.get('schema') != 1 or value.get('root') != str(root) or value.get('transaction') != token or
            value.get('kind') != 'resolution-before' or value.get('action') not in ACTIONS):
        raise ValueError('处理前存档的安装或身份不匹配。')
    _validate_content(value.get('original'))
    created = datetime.fromisoformat(value['created_at'])
    if created.tzinfo is None or _original_content(case) != value['original']:
        raise ValueError('处理前原件未通过完整五树核验，已保留但不能换回。')
    return case, value, raw


def _case(root, token):
    case, manifest, raw = _load_manifest(root, token)
    intent, intent_raw = _json(case/'intent.json')
    _validate_intent(root, intent, manifest, raw)
    receipt, receipt_raw = _json(case/'receipt.json')
    if receipt != _receipt(intent, intent_raw):
        raise ValueError('这份处理前存档所属操作尚未完整结束，请先继续上次选择。')
    readable = _readability(case/'original/savedata')
    if not readable['readable_generations']:
        raise ValueError('原件已封存，但没有当前版本能读取的角色，不放入换回列表。')
    bad_generation = any(row[key] in ('unreadable', 'missing') for row in readable['characters'] for key in ('primary', 'backup'))
    suffix = ('（含读不出的角色）' if not readable['valid'] and bad_generation else
              '（角色列表或身份待核对）' if not readable['valid'] else '（换回后需重新选择）')
    prefix = '换回前的存档' if manifest['action'] == 'restore-case' else '处理前的存档'
    files = [item for tree in manifest['original']['trees'].values() if tree for item in tree['files'].values()]
    return {'kind': 'resolution-before', 'id': token, 'time': manifest['created_at'],
            'label': prefix + suffix, 'characters': readable['characters'], 'readability_note': readable['reason'],
            'fingerprint': _sha(raw+intent_raw+receipt_raw), 'file_count': len(files),
            'total_bytes': sum(item['bytes'] for item in files), '_folder': case,
            '_original': manifest['original']}


def _list_cases(root):
    base = transfer.plain_path(root/'save-backups/recovery-choice')
    rows, warnings = [], []
    if not _exists(base):
        return rows, warnings
    for child in sorted(base.iterdir()):
        if not TOKEN.fullmatch(child.name) or not _exists(child/'receipt.json'):
            continue
        try:
            rows.append(_case(root, child.name))
        except (ValueError, OSError, KeyError, TypeError) as error:
            warnings.append(f'{child.name}：原件保留，未列为可换回项。{error}')
    return sorted(rows, key=lambda row: (row['time'], row['id']), reverse=True), warnings


def list_cases(root):
    root = _root(root)
    with transfer.installation_guard(root):
        if any(_exists(root/name) for name in ('.save-import.lock', '.save-conversion.lock', '.save-conversion-pending.json')):
            return {'ok': True, 'backups': [], 'warnings': ['有尚未结束的存档操作。请先继续原操作，再读取处理前存档列表。']}
        _guards(root)
        rows, warnings = _list_cases(root)
        return {'ok': True, 'backups': [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows],
                'warnings': warnings}


def _ordinary(root):
    base = transfer.plain_path(root/'save-backups')
    rows, warnings = [], []
    if not _exists(base):
        return rows, warnings
    for child in sorted(base.iterdir()):
        if not backups.NAME.fullmatch(child.name) or not _exists(child/'backup.json'):
            continue
        try:
            row = backups._backup(root, child.name)
            qualification = _readability(row['_folder']/'savedata')
            if not qualification['valid']:
                raise ValueError(qualification['reason'])
            rows.append(row)
        except (ValueError, KeyError, TypeError) as error:
            warnings.append(f'{child.name}：当前无法完整核对，未作为恢复来源。{error}')
        # Permission/IO failure is NOT proof that a newer backup is unusable.
    return sorted(rows, key=lambda row: row['_sort'], reverse=True), warnings


def _bound_scope(root, items, valid, gate):
    blocked = [item for item in items if item['state'] == 'review-required']
    if not valid or gate is not None or len(blocked) != 1 or blocked[0].get('kind') != 'request':
        return None
    item = blocked[0]
    try:
        slot, token, seq = item['slot'], item['run'], item['sequence']
        folder, saves = world.layout(root)
        request = world.read_request(root, folder/'requests'/f'world-save-{slot}.bin', enroll=False)
        if request['context'].get('schema') != 1:
            return None
        job = world.read_job(request)
        if job is None and _exists(world.job_for(request)):
            return None  # Present-but-incomplete is not the absence of a job.
        world.check_journal_location(request)
        if not all(_exists(saves/f'{stem}{slot:04}') for stem in ('userdata', 'backup')):
            return None  # Stable absence cancels narrow binding, not all choices.
        if any(world.identity(world.stable(saves/f'{stem}{slot:04}')) != request['identity']
               for stem in ('userdata', 'backup')):
            return None
        # seq alone does not bind ACK/ready to this run. Never retire them by guessing.
        if any(_exists(folder/part/f'{slot}.{extension}') for part, extension in (('acks', 'ack'), ('ready', 'ready'))):
            return None
        paths = [('world-recovery', f'requests/world-save-{slot}.bin')]
        if job is not None:
            for name, values in job['files'].items():
                for side, key in (('before', 'before'), ('expected', 'after')):
                    raw = world.stable(world.job_for(request)/side/name)
                    if world.sha(raw) != values[key] or world.identity(raw) != request['identity']:
                        return None
            paths.append(('world-recovery', 'jobs/'+request['id']))
        journal_names = [f'own-save-{slot}.bin', f'own-save-{slot}.bin.awaiting-save']
        for name in journal_names:
            path = root/'mod/own-world'/name
            if _exists(path):
                if world.stable(path) != request['journal_bytes']:
                    return None
                paths.append(('own-world', name))
        for extension, expected in (('bin', request['baseline']), ('bbow', request['baseline_journal'])):
            relative = f'baselines/world-save-{slot}.{extension}'
            path = folder/relative
            if _exists(path):
                if world.stable(path) != expected:
                    return None
                paths.append(('world-recovery', relative))
        return {'token': token, 'slot': slot, 'sequence': seq, 'paths': [list(p) for p in paths]}
    except (ValueError, KeyError, TypeError):
        return None


def _clean_aux(root):
    for category in transfer.AUXILIARY:
        tree = _tree(root/'mod'/category)
        if tree is None:
            continue
        for name in tree[1]:
            pattern = r'own-save-[0-9]\.bin\.restored' if category == 'own-world' else r'taken-save-[0-9]\.bin'
            if category == 'shared-progress' or not re.fullmatch(pattern, name):
                return False
            raw = world.stable(root/'mod'/category/name)
            try:
                offline_aux.journal(raw, 0x574F4242 if category == 'own-world' else 0x544C4242)
            except ValueError:
                return False
    return True


def _impact(before, after):
    first, second = (value['files'] if value else {} for value in (before, after))
    changed = sorted(name for name in first.keys() | second.keys() if first.get(name) != second.get(name))
    slots, other = set(), False
    for path in changed:
        match = re.fullmatch(r'1/CUSA00207/SPRJ0005/(?:userdata|backup)000([0-9])', path)
        if match:
            slots.add(int(match[1])+1)
        else:
            other = True
    parts = ['变化的角色槽位：' + ('、'.join(map(str, sorted(slots))) if slots else '未发现角色文件变化')]
    if other or (before or {}).get('directories') != (after or {}).get('directories'):
        parts.append('角色列表或其它路径也有变化，可能影响全部角色')
    return '；'.join(parts) + '。这些是文件差异，不代表已核实每个角色的游戏进度时间。'


def _option(action, label, description, recommended=False):
    return {'id': action, 'label': label, 'description': description, 'recommended': recommended}


def _plan(root, selection_kind=None, selection_id=None, selection_fingerprint=None):
    _guards(root)
    before = _capture(root)
    contents = _content(before)
    readable = _readability(root/TREES['savedata'])
    items = world.review_items(root)
    blocked = any(item['state'] == 'review-required' for item in items)
    source, selection, warnings = None, None, []
    if selection_kind == 'resolution-before':
        source = _case(root, selection_id)
        if source['fingerprint'] != selection_fingerprint:
            raise ValueError('所选处理前存档已变化，请刷新列表重新确认。')
        selection = {'kind': selection_kind, 'id': selection_id, 'fingerprint': selection_fingerprint}
    else:
        if selection_kind not in (None, 'launch', 'pre-restore'):
            raise ValueError('存档来源类型不受支持。')
        candidates, warnings = _ordinary(root)
        if selection_kind is not None:
            source = next((row for row in candidates if row['id'] == selection_id), None)
            if source is None or source['kind'] != selection_kind or source['fingerprint'] != selection_fingerprint:
                raise ValueError('所选备份未通过完整校验或已变化，请刷新列表重新确认。')
        else:
            source = next((row for row in candidates if row['kind'] == 'launch'), candidates[0] if candidates else None)
        if source is not None:
            selection = {key: source[key] for key in ('kind', 'id', 'fingerprint')}
    has_save_files = bool((contents['trees']['savedata'] or {}).get('files'))
    needed = (blocked or contents['gate'] is not None or selection_kind == 'resolution-before' or
              has_save_files and not readable['valid'])
    bound = _bound_scope(root, items, readable['valid'], contents['gate']) if needed else None
    options = []
    clean_gate = contents['gate'] is not None and readable['valid'] and not items and _clean_aux(root)
    reversible = ("当前版本会先完整保全，可在列表换回，不合并两个版本。" if readable['readable_generations'] else
                  "当前版本会先完整封存；目前没有已证可读的角色代次，不会列为可换回项，不能保证日后恢复。")
    if selection_kind == 'resolution-before':
        source_quality = _readability(source['_folder']/'original/savedata')
        source_notice = ('其中有当前版本无法完整核对的角色或角色列表。' + source_quality['reason'] if not source_quality['valid'] else
                         '该版本各角色代次已通过当前结构检查，旧联机记录仍须重新选择。')
        options.append(_option('restore-case', '换回这份处理前的存档',
            f"来源时间：{source['time']}。会切回此时的全部角色和联机记录。" + source_notice +
            '现在的版本会先完整另存为“换回前的存档”。' + reversible + '换回后仍需重新选择，不会自动启动。'))
    elif needed:
        if clean_gate:
            options.append(_option('use-current', '使用这份存档（推荐）',
                '当前角色结构及联机记录检查未发现待处理项。会先完整保全，再结束这次换回提示，然后按正常启动流程继续；不修改角色进度。', True))
        elif bound is not None:
            options.append(_option('accept-bound', '保留这个角色现在的进度（推荐）',
                f"角色槽位 {bound['slot']+1}：不改现有存档，也不改其它角色；停止这一次旧世界回退。门、Boss、商店或任务可能仍有联机影响，本次未保存的世界恢复变化不会再补回。处理前版本会先完整保全。", True))
        elif readable['valid']:
            options.append(_option('accept-all', '保留所有角色现在的存档（推荐）',
                '存档文件不改，但所有旧联机待补记录不再补入，可能保留房主世界变化，也可能放弃尚未保存的拾取、购买或共享进度。处理前版本会先完整保全，可在列表换回后重新选择。', True))
        if source is not None:
            recommended = not options
            title = ('恢复所选可用的自动备份' if source['kind'] == 'launch' else '恢复所选整套备份')
            options.append(_option('restore-all', title + ('（推荐）' if recommended else ''),
                f"备份时间：{source['time']}。全部角色采用这份备份的进度，待补联机进度作废。" +
                _impact(contents['trees']['savedata'], source['_inventory']) +
                reversible, recommended))
        options.append(_option('preserve', '先保全原件，暂不改变',
            '完整另存当前存档和联机记录，不更换当前进度。异常仍未处理，这次先不启动。'))
    summary = ('换回后需由您重新选择，不会自动回放旧联机记录。' if contents['gate'] is not None else
               '旧联机记录不能自动安全结案，请选择本机处理方式。' if blocked else
               '当前没有需要此流程处理的异常。')
    if needed and not readable['valid']:
        summary += ' 当前有角色或角色列表无法完整核对；不代表所有进度永久丢失。' + readable['reason']
        if source is None:
            summary += ' 本第一切片尚不含原版重导和重新开始，不能单独作为完整恢复功能发布。'
    _guards(root)
    if _capture(root) != before:
        raise ValueError('检查期间存档或联机记录发生变化，请重新检查；尚未更换存档。')
    public = {'ok': True, 'needed': needed, 'summary': summary, 'options': options,
              'characters': readable['characters'], 'warnings': warnings, 'pending': None, 'selection': selection}
    data = {'root': str(root), 'before': before, 'content': contents, 'bound': bound,
            'selection': selection, 'source': source, 'public': public}
    binding = {k: v for k, v in data.items() if k != 'source'}
    if source is not None:
        binding['source'] = source.get('_original', source.get('_inventory'))
        if source['kind'] != 'resolution-before':
            binding['source_snapshot'] = source['_snapshot']
    public['digest'] = _sha(_bytes(binding))
    return public, data


def plan(root, selection_kind=None, selection_id=None, selection_fingerprint=None):
    root = _root(root)
    with transfer.installation_guard(root):
        if _exists(root/'.save-import.lock'):
            return {'ok': True, 'needed': True, 'summary': '请先继续尚未完成的存档操作。',
                    'digest': None, 'options': [], 'characters': [], 'warnings': [],
                    'selection': None, 'pending': pending(root)}
        return _plan(root, selection_kind, selection_id, selection_fingerprint)[0]


def _checkpoint(name):
    """No production switches. Tests may observe a boundary without skipping work."""


def _write_new(path, raw):
    path = transfer.plain_path(path)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _check_record(raw):
    """Every record needed after commitment must satisfy our own reader contract."""
    if len(raw) > MAX_RECORD_BYTES or not isinstance(json.loads(raw), dict):
        raise ValueError('处理记录超出当前支持的大小，尚未发布处理意图，当前存档未替换。已保全的副本仍保留。')


def _publish_new(path, raw):
    _check_record(raw)
    temporary = transfer.plain_path(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
    _write_new(temporary, raw)
    _checkpoint('after:publish-temp:'+path.name)
    _checkpoint('before:publish-record:'+path.name)
    # Windows rename refuses an existing destination. link has that same atomic
    # no-replace contract on POSIX (ordinary rename there would silently replace).
    if os.name == 'nt':
        temporary.rename(path)
    else:
        os.link(temporary, path)
        temporary.unlink()
    _checkpoint('after:publish-record:'+path.name)


def _durable_copy(source, destination, expected):
    source, destination = transfer.plain_path(source), transfer.plain_path(destination)
    if transfer.overlaps(source, destination) or _exists(destination):
        raise ValueError('保全副本路径冲突，当前存档未替换。')
    def copy_file(origin, target):
        # Flush a writable handle before copying a possible read-only attribute.
        # Windows FlushFileBuffers rejects the read-only handle used by fsync.
        shutil.copyfile(origin, target)
        with Path(target).open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(origin, target)
        return target
    shutil.copytree(source, destination, copy_function=copy_file)
    if (backups._inventory(transfer.snapshot(destination)) != backups._inventory(expected) or
            transfer.snapshot(source) != expected):
        raise ValueError('保全副本校验失败，当前存档未被替换。')


def _remove(tree, relative):
    if not relative:
        return None
    tree = deepcopy(tree)
    tree['directories'] = [name for name in tree['directories'] if name != relative and not name.startswith(relative+'/')]
    tree['files'] = {name: row for name, row in tree['files'].items() if name != relative and not name.startswith(relative+'/')}
    return tree


def _extract(tree, relative):
    if not relative:
        return {'tree': deepcopy(tree)}
    if relative in tree['files']:
        return {'file': tree['files'][relative]}
    if relative not in tree['directories']:
        raise ValueError('精确处理的记录已不存在，不能继续旧选择。')
    prefix = relative+'/'
    return {'tree': {'directories': [name[len(prefix):] for name in tree['directories'] if name.startswith(prefix)],
                     'files': {name[len(prefix):]: row for name, row in tree['files'].items() if name.startswith(prefix)}}}


def _insert(tree, relative, piece):
    if not relative:
        return deepcopy(piece['tree'])
    tree = deepcopy(tree) if tree is not None else {'directories': [], 'files': {}}
    dirs = set(tree['directories'])
    parts = relative.split('/')
    dirs.update('/'.join(parts[:n]) for n in range(1, len(parts)))
    if 'file' in piece:
        tree['files'][relative] = piece['file']
    else:
        dirs.add(relative)
        dirs.update(relative+'/'+name for name in piece['tree']['directories'])
        tree['files'].update({relative+'/'+name: row for name, row in piece['tree']['files'].items()})
    tree['directories'] = sorted(dirs)
    return tree


def _retire_paths(intent):
    action, before = intent['action'], intent['before']['trees']
    if action == 'accept-bound':
        return intent['bound']['paths']
    labels = list(TREES) if action in ('restore-all', 'restore-case') else list(TREES)[1:] if action == 'accept-all' else []
    return [[name, ''] for name in labels if before[name] is not None]


def _steps(intent):
    result = [('retire', name, relative) for name, relative in _retire_paths(intent)]
    if intent['action'] != 'preserve' and intent['before']['gate'] is not None:
        result.append(('retire-gate', '', ''))
    result.extend(('install', name, '') for name in TREES if intent['target'][name] is not None)
    if intent['new_gate'] is not None:
        result.append(('publish-gate', '', ''))
    return result


def _retired_scaffold(intent):
    """All nested move parents exist before the durable intent can be published."""
    result = dict.fromkeys(TREES)
    for name, relative in _retire_paths(intent):
        if not relative:
            continue  # Whole-tree rename must still see a nonexistent destination.
        tree = result[name] or {'directories': [], 'files': {}}
        parts = relative.split('/')
        tree['directories'] = sorted(set(tree['directories']) | {'/'.join(parts[:n]) for n in range(1, len(parts))})
        result[name] = tree
    return result


def _states(intent):
    state = {'live': deepcopy(intent['before']['trees']), 'stage': deepcopy(intent['target']),
             'retired': _retired_scaffold(intent), 'gate': intent['before']['gate'], 'retired_gate': None}
    result = [deepcopy(state)]
    for kind, name, relative in _steps(intent):
        if kind == 'retire':
            piece = _extract(state['live'][name], relative)
            state['live'][name] = _remove(state['live'][name], relative)
            state['retired'][name] = _insert(state['retired'][name], relative, piece)
        elif kind == 'retire-gate':
            state['retired_gate'], state['gate'] = state['gate'], None
        elif kind == 'install':
            if state['live'][name] is not None:
                raise ValueError('安装目标尚未退休。')
            state['live'][name], state['stage'][name] = state['stage'][name], None
        else:
            raw = _bytes(intent['new_gate'])
            state['gate'] = {'bytes': len(raw), 'sha256': _sha(raw)}
        result.append(deepcopy(state))
    return result


def _validate_intent(root, value, manifest, manifest_raw):
    token, action = value.get('transaction'), value.get('action')
    if (value.get('schema') != 1 or value.get('operation') != OPERATION or value.get('root') != str(root) or
            token != manifest['transaction'] or action != manifest['action'] or
            value.get('manifest_sha256') != _sha(manifest_raw) or value.get('confirmed') is not True or
            value.get('text_version') != TEXT_VERSION or value.get('before') != manifest['original']):
        raise ValueError('处理意图和完整保全记录不一致，已保留现场。')
    _validate_content(value['before'])
    _validate_content({'trees': value.get('target'), 'gate': None})
    bound = value.get('bound')
    if action == 'accept-bound':
        if (not isinstance(bound, dict) or not TOKEN.fullmatch(str(bound.get('token', ''))) or
                type(bound.get('slot')) is not int or not 0 <= bound['slot'] < 10 or
                type(bound.get('sequence')) is not int or bound['sequence'] <= 0):
            raise ValueError('单角色处理范围不完整。')
        slot = bound['slot']
        required = ['world-recovery', f'requests/world-save-{slot}.bin']
        allowed = [required, ['world-recovery', f"jobs/{bound['token']}-{slot}-{bound['sequence']}"],
                   ['world-recovery', f'baselines/world-save-{slot}.bin'],
                   ['world-recovery', f'baselines/world-save-{slot}.bbow'],
                   ['own-world', f'own-save-{slot}.bin'], ['own-world', f'own-save-{slot}.bin.awaiting-save']]
        paths = bound.get('paths')
        if (not isinstance(paths, list) or not paths or paths[0] != required or
                len({_bytes(p) for p in paths}) != len(paths) or any(p not in allowed for p in paths) or
                value['before']['gate'] is not None):
            raise ValueError('单角色处理路径不属于已绑定异常。')
    elif bound is not None:
        raise ValueError('整套处理不能夹带单角色路径。')
    if action not in ('restore-all', 'restore-case') and any(v is not None for v in value['target'].values()):
        raise ValueError('此选择不允许替换角色文件。')
    if action == 'restore-all' and (value['target']['savedata'] is None or any(value['target'][k] is not None for k in list(TREES)[1:])):
        raise ValueError('普通备份只能作为整套 savedata 来源。')
    gate = value.get('new_gate')
    expected_gate = {'schema': 1, 'root': str(root), 'transaction': token,
                     'target_digest': _sha(_bytes(value['target'])), 'reason': 'restored-original-needs-review'}
    if gate != (expected_gate if action == 'restore-case' else None):
        raise ValueError('换回门禁不属于此处理。')
    _states(value)  # Validate all fixed-path transitions before any live operation.


def _receipt(intent, raw):
    results = {'accept-bound': 'accepted-current-unverified', 'accept-all': 'accepted-current-unverified',
               'restore-all': 'selected-backup', 'restore-case': 'restored-original-needs-review',
               'use-current': 'selected-current', 'preserve': 'preserved-only'}
    return {'schema': 1, 'root': intent['root'], 'transaction': intent['transaction'],
            'action': intent['action'], 'intent_sha256': _sha(raw), 'manifest_sha256': intent['manifest_sha256'],
            'result': results[intent['action']]}


def _actual(root, case):
    result = {'live': {name: None if (v := _tree(root/relative)) is None else backups._inventory(v)
                       for name, relative in TREES.items()},
              'gate': _content_file(_file(root/GATE)), 'retired_gate': _content_file(_file(case/'retired/gate'))}
    for area in ('stage', 'retired'):
        result[area] = {name: None if (v := _tree(case/area/name)) is None else backups._inventory(v) for name in TREES}
    return result


def _position(root, case, intent):
    actual = _actual(root, case)
    states = _states(intent)
    matches = [i for i, value in enumerate(states) if value == actual]
    if len(matches) != 1:
        raise ValueError('现场与原选择不一致，可能出现了新的进度或文件变化；未覆盖。请退出占用程序后重新检查，原件仍完整保留。')
    return matches[0]


def _intent(root):
    value, raw = _json(root/'.save-import.lock')
    if value.get('operation') != OPERATION:
        raise ValueError('这不是存档选择事务，请在原导入或恢复页面继续。')
    case, manifest, manifest_raw = _load_manifest(root, value.get('transaction'))
    published, published_raw = _json(case/'intent.json')
    if raw != published_raw or value != published:
        raise ValueError('活动处理锁与原选择不一致，未覆盖任何存档。')
    _validate_intent(root, value, manifest, manifest_raw)
    _position(root, case, value)
    return case, value, raw


def pending(root):
    root = _root(root)
    with transfer.installation_guard(root):
        try:
            case, value, raw = _intent(root)
            _guards(root, own_lock=raw)
            return {'can_resume': True, 'fingerprint': _sha(raw), 'message':
                    '上次处理未完成，将继续原来的选择。已保全的原件和所选版本不会重新挑选或合并。'}
        except (ValueError, OSError, KeyError, TypeError) as error:
            return {'can_resume': False, 'fingerprint': None, 'message': str(error)}


def _result(intent, raw):
    receipt = _receipt(intent, raw)
    message = {'accept-bound': '已保留现有存档，停止所选旧世界回退；不代表世界状态已修复。',
               'accept-all': '现有存档文件未改，旧联机待补记录已归档，不再补入。',
               'restore-all': '已采用所选整套备份；旧联机待补记录已归档。',
               'restore-case': '已换回所选完整版本。换回前的版本已另存；请重新选择处理方式，不会自动启动。',
               'use-current': '原件已保全，已选用当前存档；将继续正常启动检查。',
               'preserve': '原件已完整保全，当前进度没有改变。异常仍未处理，这次先不启动。'}[intent['action']]
    return {'ok': True, 'result': receipt['result'], 'case_id': intent['transaction'],
            'message': message + ' 处理前原件不会自动删除。',
            'needs_review': intent['action'] in ('restore-case', 'preserve'),
            'can_launch': intent['action'] == 'use-current'}


def _run(root, case, intent, raw):
    states, steps = _states(intent), _steps(intent)
    position = _position(root, case, intent)
    for index in range(position, len(steps)):
        _guards(root, own_lock=raw)
        if _original_content(case) != intent['before'] or _actual(root, case) != states[index]:
            raise ValueError('继续前原件或现场发生变化，已保持启动拦截。')
        kind, name, relative = steps[index]
        point = f'{index}:{kind}:{name}:{relative}'
        _checkpoint('before:'+point)
        if kind == 'retire':
            source = transfer.plain_path(root/TREES[name]/relative)
            destination = transfer.plain_path(case/'retired'/name/relative)
        elif kind == 'retire-gate':
            source, destination = transfer.plain_path(root/GATE), transfer.plain_path(case/'retired/gate')
        elif kind == 'install':
            source, destination = transfer.plain_path(case/'stage'/name), transfer.plain_path(root/TREES[name])
        else:
            _publish_new(transfer.plain_path(root/GATE), _bytes(intent['new_gate']))
            source = destination = None
        if source is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if _exists(destination):
                raise ValueError('目标位置已有新内容，未覆盖。')
            source.rename(destination)
        _checkpoint('after:'+point)
        if _actual(root, case) != states[index+1]:
            raise ValueError('处理步骤完成后的校验不一致，已保持启动拦截。')
    _guards(root, own_lock=raw)
    if _actual(root, case) != states[-1] or _original_content(case) != intent['before']:
        raise ValueError('最终核验失败，已保留原件和启动拦截。')
    receipt_path = case/'receipt.json'
    expected = _receipt(intent, raw)
    _checkpoint('before:receipt')
    if _exists(receipt_path):
        if _json(receipt_path)[0] != expected:
            raise ValueError('处理结果记录与本事务不一致，已保持启动拦截。')
    else:
        _publish_new(receipt_path, _bytes(expected))
    _checkpoint('after:receipt')
    _checkpoint('before:unlock')
    _guards(root, own_lock=raw)
    if (root/'.save-import.lock').read_bytes() != raw or _actual(root, case) != states[-1]:
        raise ValueError('解除拦截前现场变化，已保留处理记录。')
    (root/'.save-import.lock').unlink()
    _checkpoint('after:unlock')
    return _result(intent, raw)


def apply(root, digest, option_id, *, confirmed=False, selection_kind=None, selection_id=None, selection_fingerprint=None):
    if confirmed is not True:
        raise ValueError('请先阅读影响并明确确认这个选择。')
    root = _root(root)
    with transfer.installation_guard(root):
        public, plan_data = _plan(root, selection_kind, selection_id, selection_fingerprint)
        if not isinstance(digest, str) or digest != public['digest']:
            raise ValueError('检查后来源或当前存档已变化，请重新检查并确认；尚未更换存档。')
        if option_id not in {row['id'] for row in public['options']}:
            raise ValueError('这个选择不适用于当前存档，请重新检查。')
        before, source = plan_data['before'], plan_data['source']
        target = dict.fromkeys(TREES)
        if option_id == 'restore-all':
            target['savedata'] = source['_inventory']
        elif option_id == 'restore-case':
            target = source['_original']['trees']
        total = sum(row[2] for tree in before['trees'].values() if tree for row in tree[1].values())
        total += sum(row['bytes'] for tree in target.values() if tree for row in tree['files'].values())
        required = total + 8 * 1024 * 1024
        if shutil.disk_usage(root).free < required:
            raise ValueError(f'空间不足，至少需要 {required/1048576:.1f} MB 空闲空间保全并准备版本。请腾出空间后重新检查；当前存档未替换。')
        token = uuid.uuid4().hex
        case = _case_path(root, token)
        case.mkdir(parents=True, exist_ok=False)
        for area in ('original', 'stage', 'retired'):
            (case/area).mkdir()
        _checkpoint('before:preserve')
        for name, snap in before['trees'].items():
            if snap is not None:
                _durable_copy(root/TREES[name], case/'original'/name, snap)
            _checkpoint('preserved:'+name)
        if before['gate'] is not None:
            _write_new(case/'original/gate', (root/GATE).read_bytes())
        if _original_content(case) != plan_data['content']:
            raise ValueError('保全副本核验失败，当前存档未替换。')
        _checkpoint('after:preserve')
        if option_id == 'restore-all':
            _checkpoint('before:stage:savedata')
            _durable_copy(source['_folder']/'savedata', case/'stage/savedata', source['_snapshot'])
            if not _readability(case/'stage/savedata')['valid']:
                raise ValueError('所选整套备份的代次资格未通过，当前存档未替换。')
            _checkpoint('after:stage:savedata')
        elif option_id == 'restore-case':
            for name, inventory in target.items():
                if inventory is not None:
                    _checkpoint('before:stage:'+name)
                    origin = source['_folder']/'original'/name
                    _durable_copy(origin, case/'stage'/name, transfer.snapshot(origin))
                    _checkpoint('after:stage:'+name)
        _checkpoint('after:stage')
        again, _ = _plan(root, selection_kind, selection_id, selection_fingerprint)
        if again['digest'] != digest or _capture(root) != before:
            raise ValueError('保全期间当前存档或所选来源变化，请重新检查；尚未更换存档。')
        created = datetime.now(timezone.utc).isoformat()
        manifest = {'schema': 1, 'kind': 'resolution-before', 'transaction': token, 'root': str(root),
                    'action': option_id, 'created_at': created, 'original': plan_data['content'],
                    'source_snapshot': before, 'plan_digest': digest}
        manifest_raw = _bytes(manifest)
        _check_record(manifest_raw)
        _write_new(case/'manifest.json', manifest_raw)
        intent = {'schema': 1, 'operation': OPERATION, 'root': str(root), 'transaction': token,
                  'action': option_id, 'created_at': created, 'manifest_sha256': _sha(manifest_raw),
                  'before': plan_data['content'], 'target': target,
                  'bound': plan_data['bound'] if option_id == 'accept-bound' else None,
                  'selection': plan_data['selection'], 'plan_digest': digest, 'confirmed': True,
                  'text_version': TEXT_VERSION, 'choice': next(row for row in public['options'] if row['id'] == option_id),
                  'new_gate': None}
        if option_id == 'restore-case':
            intent['new_gate'] = {'schema': 1, 'root': str(root), 'transaction': token,
                                  'target_digest': _sha(_bytes(target)), 'reason': 'restored-original-needs-review'}
        _validate_intent(root, intent, manifest, manifest_raw)
        raw = _bytes(intent)
        # Validate all future control records before publishing the durable lock,
        # including records that will only be physically written after installs.
        for record in (raw, _bytes(_receipt(intent, raw))):
            _check_record(record)
        if intent['new_gate'] is not None:
            _check_record(_bytes(intent['new_gate']))
        _write_new(case/'intent.json', raw)
        _checkpoint('before:retired-scaffold')
        for name, tree in _retired_scaffold(intent).items():
            if tree is not None:
                (case/'retired'/name).mkdir()
                for relative in tree['directories']:
                    (case/'retired'/name/relative).mkdir(parents=True, exist_ok=True)
        _checkpoint('after:retired-scaffold')
        if _actual(root, case) != _states(intent)[0]:
            raise ValueError('准备版本核验失败，当前存档未替换。')
        _guards(root)
        if _capture(root) != before or _original_content(case) != intent['before']:
            raise ValueError('提交前存档变化，当前存档未替换。')
        if _json(case/'manifest.json')[1] != manifest_raw or _json(case/'intent.json')[1] != raw:
            raise ValueError('提交前无法重新读取原处理记录，当前存档未替换。')
        _checkpoint('before:lock')
        _publish_new(root/'.save-import.lock', raw)
        _checkpoint('after:lock')
        # From this point all exceptions intentionally retain the immutable lock.
        return _run(root, case, intent, raw)


def resume(root, fingerprint, *, confirmed=False):
    if confirmed is not True:
        raise ValueError('请先确认继续上一次的选择。')
    root = _root(root)
    with transfer.installation_guard(root):
        if not _exists(root/'.save-import.lock'):
            base = root/'save-backups/recovery-choice'
            if _exists(base):
                for child in base.iterdir():
                    if TOKEN.fullmatch(child.name) and _exists(child/'receipt.json'):
                        _, raw = _json(child/'intent.json')
                        if _sha(raw) == fingerprint:
                            _case_root, manifest, manifest_raw = _load_manifest(root, child.name)
                            intent = json.loads(raw)
                            _validate_intent(root, intent, manifest, manifest_raw)
                            if _json(child/'receipt.json')[0] != _receipt(intent, raw):
                                raise ValueError('旧事务的结果记录不一致。')
                            result = _result(intent, raw)
                            result.update(can_launch=False, message='该选择此前已经完成，本次没有再次改动存档。请刷新查看当前状态。')
                            return result
            raise ValueError('没有匹配的未完成选择，请重新检查当前状态。')
        case, intent, raw = _intent(root)
        if _sha(raw) != fingerprint:
            raise ValueError('未完成选择已经变化，请重新读取并确认。')
        _guards(root, own_lock=raw)
        return _run(root, case, intent, raw)


def dispatch(root, request):
    if not isinstance(request, dict):
        raise ValueError('存档选择请求格式不正确。')
    action = request.get('action')
    selection = {key: request.get(key) for key in ('selection_kind', 'selection_id', 'selection_fingerprint')}
    if action == 'plan':
        return plan(root, **selection)
    if action == 'list':
        return list_cases(root)
    if action == 'apply':
        return apply(root, request.get('digest'), request.get('option_id'), confirmed=request.get('confirmed'), **selection)
    if action == 'resume':
        return resume(root, request.get('fingerprint'), confirmed=request.get('confirmed'))
    raise ValueError('不支持的存档选择操作。')


def main():
    try:
        result = dispatch(ROOT, json.load(sys.stdin))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'ok': False, 'error': str(error) + ' 请先正常退出游戏和占用文件的程序；检查权限与空闲空间后重新检查。不会自动回退存档。'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
