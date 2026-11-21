"""Explicit local save choices. Originals survive; interrupted choices only go forward.

The five data roots and the separate review gate are fixed by this module. Neither
old world records nor UI requests supply filesystem paths. This is not a world
repairer: it never modifies role bytes, creates THWR proof, or merges branches.
"""
from copy import deepcopy
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

from backup_characters import character_preview, inventory_slots, with_character_preview
import offline_aux
import offline_save
import save_backups as backups
from save_effects import effect, option_effects
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
TEXT_VERSION = 3
RECORD_VERSION = 3
SUPPORTED_VERSIONS = (1, 2, 3)
ACTIONS_V1 = frozenset(ACTIONS)
ACTIONS_V2 = ACTIONS_V1 | {'abandon'}
ACTIONS = ACTIONS_V2 | {'restore-generation', 'reimport-ordinary', 'restart'}
MAX_RECORD_BYTES = 16 * 1024 * 1024


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _control(path):
    """Bounded, stable control bytes and identity from the same read-only handle."""
    path = transfer.plain_path(path)
    # Windows path.stat and fstat can give ctime different meanings. Only
    # common fields are compared across APIs; ctime is compared within each.
    identity = lambda value: [value.st_dev, value.st_ino, value.st_size,
                              value.st_mtime_ns]
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('处理记录不是普通文件，当前流程不能安全结束它；不会删除或改名。')
    if before.st_size > MAX_RECORD_BYTES:
        raise ValueError('处理记录超过本版本的安全大小上限，当前流程不能安全结束它；原记录保留，不会删除或改名。')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if identity(opened) != identity(before):
            raise world.EvidenceChangedError('打开处理记录时文件变化，请重新检查。')
        raw = stream.read(MAX_RECORD_BYTES + 1)
        after = os.fstat(stream.fileno())
        current = transfer.plain_path(path).stat()
    if (identity(before) != identity(after) or identity(after) != identity(current) or
            before.st_ctime_ns != current.st_ctime_ns or
            opened.st_ctime_ns != after.st_ctime_ns or len(raw) != after.st_size):
        raise world.EvidenceChangedError('读取处理记录期间文件变化，请重新检查。')
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError('处理记录超过本版本的安全大小上限，当前流程不能安全结束它；原记录保留，不会删除或改名。')
    return {'identity': identity(after), 'bytes': len(raw), 'sha256': _sha(raw)}, raw


def _decode_record(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError('处理记录格式无法识别，当前流程不能安全继续；记录已保留，不会删除或改名。') from error
    if not isinstance(value, dict):
        raise ValueError('处理记录不是受支持的事务封套，当前流程不能结束它；普通导入或恢复请回原页面继续。')
    return value


def _json(path):
    _, raw = _control(path)
    return _decode_record(raw), raw


def error_message(error):
    """Local next steps only; never turn an operational failure into save damage."""
    message = str(error)
    chain = list(world.exception_chain(error))
    if any(isinstance(item, world.EvidenceChangedError) for item in chain):
        return message + ' 本次读取未完成，不据此判定存档损坏。请正常退出仍在写入此目录的游戏或程序，等待同步结束，再点重新检查；旧确认不能继续使用。'
    operating = next((item for item in chain if isinstance(item, OSError)), None)
    if operating is None:
        return message
    code, win = operating.errno, getattr(operating, 'winerror', None)
    if code == errno.ENOSPC or win in (39, 112):
        steps = '请在报错目录所在磁盘释放非存档文件占用的空间后重试；不要删除存档或原件封存目录。'
    elif win in (32, 33):
        steps = '文件正被占用。请正常退出游戏及正在访问该目录的程序，等待同步结束后重试；不要删除或改名存档。'
    elif isinstance(operating, PermissionError) or code in (errno.EACCES, errno.EPERM) or win == 5:
        steps = '权限或文件占用阻止了访问。请先正常退出游戏及正在访问该目录的程序后重试；若仍被拒，请检查当前账户对报错目录的读取和写入权限。不要删除或改名存档。'
    elif isinstance(operating, TimeoutError) or code == errno.ETIMEDOUT or win == 121:
        steps = '读取等待超时。请等待磁盘或同步程序完成当前访问，再点重新检查或重试启动；不要删除存档。'
    else:
        steps = '请确认报错目录和所在磁盘可以访问，正常退出占用它的程序并等待同步完成后重新检查；不要删除或改名存档。'
    return message + ' 本次读取或处理未完成，不据此判定存档损坏。' + steps


def _exists(path):
    try:
        transfer.plain_path(path).lstat()
        return True
    except FileNotFoundError:
        return False


class ResolutionRequired(ValueError):
    """A known A transaction/review gate, not an IO or path failure."""


def require_launch_clear(root, *, strict=False):
    """A-only delegation by default; direct writers use strict active-lock rejection."""
    root = transfer.plain_path(root)
    if _exists(root/GATE):
        raise ResolutionRequired('这份存档需要您重新选择。请在存档页处理；不会自动启动或回退。')
    lock = root/'.save-import.lock'
    if _exists(lock):
        try:
            _, raw = _control(lock)
        except FileNotFoundError as error:
            raise world.EvidenceChangedError('读取处理记录时文件消失，请重新检查。') from error
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise ValueError('存档导入锁或恢复记录格式不受支持，导入或恢复尚未完成；原记录保留，不能安全继续。') from error
        if not isinstance(value, dict):
            if type(value) is int and value > 0:
                if strict:
                    raise ValueError(backups.IMPORT_LOCK_MESSAGE)
                return  # A-only check: the ordinary PID lock owner remains responsible.
            raise ValueError('存档导入锁或恢复记录格式不受支持，导入或恢复尚未完成；原记录保留，不能安全继续。')
        if value.get('operation') == OPERATION:
            raise ResolutionRequired('上次存档选择尚未处理完。请在存档页继续，或完整保全当前进度后放弃上次选择；游戏尚未启动。')
        if value.get('operation') not in ('import', 'restore'):
            raise ValueError('存档导入锁或恢复记录格式不受支持，导入或恢复尚未完成；原记录保留，不能安全继续。')
        if strict:
            raise ValueError(backups.restore_lock_message(value) if value['operation'] == 'restore'
                             else backups.IMPORT_LOCK_MESSAGE)


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
    if _exists(lock) and (own_lock is None or _control(lock)[1] != own_lock):
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
        raise world.EvidenceChangedError('读取期间文件发生变化，请正常退出占用它的程序后重新检查。')
    return {'identity': identity(after), 'bytes': len(raw), 'sha256': _sha(raw)}


def _tree(path):
    if not _exists(path):
        return None
    try:
        return transfer.snapshot(path)
    except ValueError as error:
        # This is a tree read/guard, never a role parser verdict.
        raise transfer.OperationUnavailable(str(error)) from error


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
        row = {'slot': slot, 'name': '', 'primary': 'missing', 'backup': 'missing',
               'primary_reason_code': 'missing', 'backup_reason_code': 'missing',
               'primary_category': 'missing', 'backup_category': 'missing'}
        identities = []
        for stem, key in (('userdata', 'primary'), ('backup', 'backup')):
            path = transfer.plain_path(game/f'{stem}{slot:04}')
            if not _exists(path):
                identities.append(None)
                continue
            raw = world.stable(path)  # An IO failure is a hard failure, not a bad role.
            if not any(raw) and len(raw) == 0x140000:
                row[key] = 'unused'
                row[key+'_reason_code'] = 'zero_generation'
                row[key+'_category'] = 'unused'
                identities.append(None)
                continue
            phase = 'role'
            try:
                role = offline_save.parse(raw)
                phase = 'flags'
                offline_aux.flag_bank(raw)
            except (ValueError, IndexError) as error:
                row[key] = 'unreadable'
                row[key+'_reason_code'] = 'flag_layout_unsupported' if phase == 'flags' else getattr(error, 'reason_code', 'unknown_parser_rejection')
                row[key+'_category'] = 'unsupported' if phase == 'flags' else getattr(error, 'category', 'unknown')
                row[key+'_detail'] = '当前版本无法读取：' + str(error)
                identities.append(None)
                continue
            row[key] = 'readable'
            row[key+'_reason_code'] = 'qualified_generation'
            row[key+'_category'] = 'readable'
            readable += 1
            identities.append(transfer.character_identity(raw, role))
            if not row['name']:
                row['name'] = transfer.character_name(raw, role)
        if all(identities) and identities[0] != identities[1]:
            row['identity_conflict'] = True
        if (row['primary'], row['backup']) != ('missing', 'missing'):
            rows.append(row)
    valid, reason = True, ''
    try:
        transfer.validate_savedata(folder)
    except ValueError as error:
        world.require_content_error(error)
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
    if (value.get('schema') != 1 or not isinstance(value.get('root'), str) or not value['root'] or value.get('transaction') != token or
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
    _require_completion(case, intent, intent_raw)
    readable = _readability(case/'original/savedata')
    sealed_restart = intent['text_version'] == 3 and intent['action'] == 'restart'
    if not readable['readable_generations'] and not sealed_restart:
        raise ValueError('原件已封存，但没有当前版本能读取的角色，不放入换回列表。')
    bad_generation = any(row[key] == 'unreadable' for row in readable['characters'] for key in ('primary', 'backup'))
    suffix = ('（含读不出的角色）' if not readable['valid'] and bad_generation else
              '（角色列表或身份待核对）' if not readable['valid'] else '（换回后需重新选择）')
    prefix = ('重新开始前封存的原件（当前无法确认可用）' if sealed_restart else
              '换回前的存档' if manifest['action'] == 'restore-case' else
              '放弃未完成处理前的存档' if manifest['action'] == 'abandon' else '处理前的存档')
    files = [item for tree in manifest['original']['trees'].values() if tree for item in tree['files'].values()]
    return {'kind': 'resolution-before', 'entry_kind': 'sealed-restart-original' if sealed_restart else 'resolution-before',
            'id': token, 'time': manifest['created_at'],
            'label': prefix + suffix, 'characters': readable['characters'], 'readability_note': readable['reason'],
            'fingerprint': _sha(raw+intent_raw+receipt_raw), 'file_count': len(files),
            'total_bytes': sum(item['bytes'] for item in files), '_folder': case,
            '_original': manifest['original']}


def _list_cases(root):
    base = transfer.plain_path(root/'save-backups/recovery-choice')
    rows, warnings = [], []
    if not _exists(base):
        return rows, warnings
    for child in sorted(base / path.name for path in base.iterdir()):
        if not TOKEN.fullmatch(child.name) or not _exists(child/'receipt.json'):
            continue
        try:
            rows.append(_case(root, child.name))
        except (ValueError, OSError, KeyError, TypeError) as error:
            warnings.append(f'{child.name}：原件保留，未列为可换回项。{error_message(error)}')
    return sorted(rows, key=lambda row: (row['time'], row['id']), reverse=True), warnings


def list_cases(root):
    root = _root(root)
    with transfer.installation_guard(root):
        if any(_exists(root/name) for name in ('.save-import.lock', '.save-conversion.lock', '.save-conversion-pending.json')):
            return {'ok': True, 'backups': [], 'warnings': ['有尚未结束的存档操作。请先继续原操作，再读取处理前存档列表。']}
        _guards(root)
        rows, warnings = _list_cases(root)
        return {'ok': True, 'backups': [with_character_preview(row) for row in rows],
                'warnings': warnings}


def _ordinary(root, *, with_status=False):
    base = transfer.plain_path(root/'save-backups')
    rows, warnings, incomplete = [], [], False
    if _exists(base):
        for child in sorted(base / path.name for path in base.iterdir()):
            if not backups.NAME.fullmatch(child.name) or not _exists(child/'backup.json'):
                continue
            try:
                row = backups._backup(root, child.name)
            except (OSError, ValueError, KeyError, TypeError) as error:
                # The old reader has untyped snapshot errors. Do not guess that
                # a skipped newer candidate is definitely unusable.
                incomplete = True
                warnings.append(f'{child.name}：本次未能完成来源检查，不据此推荐更旧备份。{error_message(error)}')
                continue
            try:
                qualification = _readability(row['_folder']/'savedata')
                if not qualification['valid']:
                    warnings.append(f"{child.name}：当前无法完整核对，未作为恢复来源。{qualification['reason']}")
                    continue
            except (OSError, transfer.OperationUnavailable) as error:
                incomplete = True
                warnings.append(f'{child.name}：暂时读取未完成，不据此推荐更旧备份。{error_message(error)}')
                continue
            rows.append(row)
    result = (sorted(rows, key=lambda row: row['_sort'], reverse=True), warnings)
    return (*result, incomplete) if with_status else result


def _bound_scope(root, items, valid, gate):
    blocked = [item for item in items if item['state'] == 'review-required']
    if not valid or len(blocked) != 1 or blocked[0].get('kind') != 'request':
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
    except FileNotFoundError as error:
        world.require_stable_missing(error)
        return None  # Full before/after capture still checks this absence.
    except (ValueError, KeyError, TypeError) as error:
        world.require_content_error(error)
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


def _generation_sources(root, readable):
    # Eligibility is separate from launchability. Unknown native layout is not damage.
    result = {}
    for row in readable['characters']:
        slot = row['slot']
        primary = root/TREES['savedata']/f'1/CUSA00207/SPRJ0005/userdata{slot:04}'
        if (row['backup'] != 'readable' or row.get('identity_conflict') or
                row['primary'] in ('missing', 'unused')):
            continue
        raw = world.stable(primary)
        relation = 'primary_unknown'
        try:
            role = offline_save.parse(raw)
        except (ValueError, IndexError):
            pass
        else:
            # The identity can remain readable when the later flag-bank check
            # fails. Do not throw away a known mismatch merely because the
            # whole generation was not qualified for restoration.
            primary_identity = transfer.character_identity(raw, role)
            backup_raw = world.stable(primary.with_name(f'backup{slot:04}'))
            backup_role = offline_save.parse(backup_raw)
            backup_identity = transfer.character_identity(backup_raw, backup_role)
            if primary_identity != backup_identity:
                continue
            relation = 'same'
        try:
            world.identity(raw)
        except (ValueError, IndexError):
            result[slot] = {'slot': slot, 'relative': f'1/CUSA00207/SPRJ0005/userdata{slot:04}',
                            'backup_relative': f'1/CUSA00207/SPRJ0005/backup{slot:04}',
                            'identity_relation': relation}
    return result


def _ordinary_source(root, install):
    if install is None or install == '':
        return None, {'status': 'not_selected', 'reason': '尚未选择原版程序。'}
    if not isinstance(install, str):
        raise ValueError('原版程序目录必须是明确的路径。')
    install = transfer.plain_path(Path(install))
    if not _exists(install/'Bloodborne.exe'):
        return None, {'status': 'not_found', 'reason': '所选原版程序已找不到，请重新选择。'}
    settings = Path(os.environ['APPDATA'])/'bbport-launcher/settings.json'
    profile_path = root/'profile.json'
    profile_before = _file(profile_path)
    manual = transfer.manual_source_setting(root)
    settings_before = _file(settings) if not manual else None
    details = transfer.source_details(install, root=root, settings_path=settings)
    source = Path(details['source'])
    if transfer.overlaps(source, root):
        raise ValueError('原版来源与当前安装重叠，不能用作重新导入来源。')
    if not _exists(source):
        return None, {'status': 'missing_savedata', 'reason': details['missing_message'] + '未证明原有进度损坏。'}
    snapshot = transfer.snapshot(source)
    try:
        metadata, inventory = transfer.inspect_source(source)
    except ValueError as error:
        world.require_content_error(error)
        quality = _readability(source)
        return None, {'status': 'unqualified_unknown', 'reason': str(error),
                      'characters': quality['characters'], 'occupied_roles_known': False}
    quality = _readability(source)
    metadata['characters'] = quality['characters']
    if (transfer.snapshot(source) != snapshot or _file(profile_path) != profile_before or
            (not manual and _file(settings) != settings_before)):
        raise world.EvidenceChangedError('原版来源或目录设置发生变化，请重新检查。')
    return {'path': source, 'snapshot': snapshot, 'inventory': backups._inventory(inventory),
            'metadata': metadata, 'settings': settings_before, 'settings_path': str(settings), 'profile': profile_before, 'install': str(install)}, {'status': 'qualified', 'metadata': metadata}


def _plan(root, selection_kind=None, selection_id=None, selection_fingerprint=None, ordinary_install=None):
    _guards(root)
    before = _capture(root)
    contents = _content(before)
    readable = _readability(root/TREES['savedata'])
    startable, primary_reason = True, ''
    try:
        world.primary_identities(root)
    except world.ReviewRequired as error:
        startable, primary_reason = False, str(error)
    items = world.review_items(root)
    blocked = any(item['state'] == 'review-required' for item in items)
    source, selection, warnings, incomplete = None, None, [], False
    if selection_kind == 'resolution-before':
        source = _case(root, selection_id)
        if source['fingerprint'] != selection_fingerprint:
            raise ValueError('所选处理前存档已变化，请刷新列表重新确认。')
        selection = {'kind': selection_kind, 'id': selection_id, 'fingerprint': selection_fingerprint}
    else:
        if selection_kind not in (None, 'launch', 'pre-restore'):
            raise ValueError('存档来源类型不受支持。')
        candidates, warnings, incomplete = _ordinary(root, with_status=True)
        if selection_kind is not None:
            source = next((row for row in candidates if row['id'] == selection_id), None)
            if source is None or source['kind'] != selection_kind or source['fingerprint'] != selection_fingerprint:
                raise ValueError('所选备份未通过完整校验或已变化，请刷新列表重新确认。')
        else:
            source = None if incomplete else next((row for row in candidates if row['kind'] == 'launch'), None)
        if source is not None:
            selection = {key: source[key] for key in ('kind', 'id', 'fingerprint')}
    needed = blocked or contents['gate'] is not None or not startable
    generations = _generation_sources(root, readable) if not startable else {}
    ordinary, ordinary_status = None, {'status': 'not_needed'}
    if not startable:
        try:
            ordinary, ordinary_status = _ordinary_source(root, ordinary_install)
        except (OSError, world.EvidenceChangedError) as error:
            ordinary_status = {'status': 'temporary_error', 'reason':
                '原版来源暂时无法读取或稳定核对：' + str(error) + '。可以重试，或重新选择原版程序；尚未改动任何存档。'}
        except ValueError as error:
            ordinary_status = {'status': 'error', 'reason':
                '当前无法核对所选原版来源：' + str(error) + '。可以重试，或重新选择原版程序；不代表原版进度损坏。'}
    show_choices = needed or selection_kind == 'resolution-before'
    bound = _bound_scope(root, items, startable, contents['gate']) if needed else None
    options = []
    clean_gate = contents['gate'] is not None and startable and not blocked
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
                '当前主档满足启动器检查，没有必须放弃的旧联机异常。会先完整保全，再结束换回提示并继续正常启动检查；正常等待的联机进度保留，不修改角色文件。', True))
        elif bound is not None:
            options.append(_option('accept-bound', '保留这个角色现在的进度（推荐）',
                f"角色槽位 {bound['slot']+1}：不改现有存档，也不改其它角色；停止这一次旧世界回退。门、Boss、商店或任务可能仍有联机影响，本次未保存的世界恢复变化不会再补回。处理前版本会先完整保全。", True))
        elif startable:
            options.append(_option('accept-all', '保留所有角色现在的存档（推荐）',
                '存档文件不改，但所有旧联机待补记录不再补入，可能保留房主世界变化，也可能放弃尚未保存的拾取、购买或共享进度。处理前版本会先完整保全，可在列表换回后重新选择。', True))
        if source is not None:
            recommended = not options and source['kind'] == 'launch' and not incomplete
            title = ('恢复所选可用的自动备份' if source['kind'] == 'launch' else '恢复所选整套备份')
            options.append(_option('restore-all', title + ('（推荐）' if recommended else ''),
                f"备份时间：{source['time']}。全部角色采用这份备份的进度，待补联机进度作废。" +
                _impact(contents['trees']['savedata'], source['_inventory']) +
                reversible, recommended))
        for slot, generation in generations.items():
            recommended = not options and not incomplete
            options.append(_option('restore-generation-'+str(slot),
                f'使用角色槽位 {slot+1} 的备份代次' + ('（推荐）' if recommended else ''),
                '只替换这个槽的主档，其它角色、该槽备档和联机记录不改。'
                + ('无法核对当前主档的角色身份；使用此槽通过自洽检查的备份代次。' if generation['identity_relation'] == 'primary_unknown' else '主备身份一致。')
                + '可能回退该角色最近进度，处理后仍须重新检查联机记录，不会自动启动。' + reversible, recommended))
        if not startable and ordinary is not None:
            recommended = not options and not incomplete
            options.append(_option('reimport-ordinary', '从原版重新导入' + ('（推荐）' if recommended else ''),
                '会回到原版存档里的全部角色进度；在无缝里新获得的进度不会合并，待补联机进度作废。'
                + '原版来源时间：' + str(ordinary['metadata'].get('latest_write', '未知')) + '。角色：'
                + '、'.join((row['name'] or '未知姓名')+'（槽位 '+str(row['slot']+1)+'）' for row in ordinary['metadata']['characters']) + '。' + reversible, recommended))
        elif not startable:
            options.append(_option('choose-ordinary', '选择原版程序' + ('（推荐）' if not options else ''),
                ordinary_status.get('reason', '') + '选择后先只读检查原版存档，不会自动覆盖当前存档。', not options))
        options.append(_option('preserve', '先保全原件，暂不改变',
            '完整另存当前存档和联机记录，不更换当前进度。异常仍未处理，这次先不启动。'))
        if not startable:
            names = '、'.join((row['name'] or '未知姓名')+'（槽位 '+str(row['slot']+1)+'）' for row in readable['characters']) or '尚未识别到角色'
            options.append(_option('restart', '封存后从头开始（不推荐）',
                '启动器无法确认这份存档还能用。此项不推荐，默认不选，需要再次确认。'
                '从头开始的新存档不包含以下已识别旧角色（包括当前可读的角色）：'+names+'。'
                '角色占用范围未完全识别，仍可能有未识别角色；不能认定原件已经损坏。'
                '执行前会完整保全并校验原件，成功后才清空当前存档现场，原版存档不会改动。'
                '原件会列在备份列表的“重新开始前封存的原件”中，可原样换回；换回后可能仍需处理。'
                '如果以后有能读取它的版本，可再处理原件，但不能承诺能救回。'))
    summary = ('换回后需由您重新选择，不会自动回放旧联机记录。' if contents['gate'] is not None else
               '旧联机记录不能自动安全结案，请选择本机处理方式。' if blocked else
               '当前没有需要此流程处理的异常。')
    if selection_kind == 'resolution-before' and not needed:
        summary = '您主动选择了历史版本；当前未发现必须回退的异常。确认才会换回，不合并进度。'
    if primary_reason:
        summary += ' ' + primary_reason
    if not readable['valid']:
        note = '部分备代、角色列表或主备身份未完整核对：' + readable['reason']
        warnings.append(note + ' 满足启动器主档检查不等于已验证游戏内可玩性。')
        for option in options:
            if option['id'] in ('use-current', 'accept-all', 'accept-bound'):
                option['description'] += note
    if needed and not options:
        summary += '请先保全原件并暂不启动。'
    if options:
        option_effects(options, root, contents['trees']['savedata'], source, ordinary, readable['readable_generations'])
    _guards(root)
    if _capture(root) != before:
        raise ValueError('检查期间存档或联机记录发生变化，请重新检查；尚未更换存档。')
    public = {'ok': True, 'needed': needed, 'show_choices': show_choices, 'startable': startable, 'summary': summary, 'options': options,
              'characters': readable['characters'], 'warnings': warnings, 'pending': None, 'selection': selection}
    public['startup_reason_code'] = 'nonzero_primary_blocked' if not startable else 'startup_contract_allowed'
    public['ordinary_source'] = ordinary_status
    public['restart_eligibility'] = {'eligible': not startable, 'recommended': False, 'facts': {'startup_blocked': not startable, 'some_readable_generation': bool(readable['readable_generations']), 'no_qualified_backup_found': source is None and not incomplete and not generations, 'original_definitely_unusable': False}, 'reason': '仅作为用户明确授权的非推荐本机出口；角色范围或可用性未知时不认定损坏。', 'occupied_roles_known': False}
    data = {'ordinary': ordinary, 'generations': generations, 'root': str(root), 'before': before, 'content': contents, 'bound': bound,
            'selection': selection, 'source': source, 'public': public}
    binding = {k: v for k, v in data.items() if k not in ('source', 'ordinary')}
    # An optional original installation cannot invalidate unrelated local choices.
    # Only reimport binds that source; the UI sends its option-specific digest.
    binding['public'] = {k: v for k, v in public.items() if k != 'ordinary_source'}
    binding['public']['options'] = [row for row in options if row['id'] not in ('reimport-ordinary', 'choose-ordinary')]
    if source is not None:
        binding['source'] = source.get('_original', source.get('_inventory'))
        if source['kind'] != 'resolution-before':
            binding['source_snapshot'] = source['_snapshot']
    public['digest'] = _sha(_bytes(binding))
    if ordinary is not None:
        original_binding = {k: (str(v) if isinstance(v, Path) else v) for k, v in ordinary.items()}
        for row in options:
            if row['id'] == 'reimport-ordinary':
                row['digest'] = _sha(_bytes({'plan': public['digest'], 'ordinary': original_binding,
                                             'effects': row['effects']}))
    return public, data


def plan(root, selection_kind=None, selection_id=None, selection_fingerprint=None, ordinary_install=None):
    root = _root(root)
    with transfer.installation_guard(root, read_only=True):
        transfer.require_stopped('预览存档处理选择')
        if _exists(root/'.save-import.lock'):
            interrupted = pending(root)
            transfer.require_stopped('预览存档处理选择')
            return {'ok': True, 'needed': True, 'show_choices': True, 'summary': '上次选择未完成，可继续或先完整保全当前现场后放弃。',
                    'digest': None, 'options': [], 'characters': [], 'warnings': [],
                    'selection': None, 'pending': interrupted}
        public, data = _plan(root, selection_kind, selection_id, selection_fingerprint, ordinary_install)
        # Display only the bytes bound to this completed plan. Apply recomputes its original digest.
        if public['show_choices']:
            public['character_preview'] = character_preview(root/TREES['savedata'], data['content']['trees']['savedata'])
            if data['source'] is not None:
                public['backup_view'] = with_character_preview(data['source'])
            if any(option['id'] == 'reimport-ordinary' for option in public['options']):
                ordinary = data['ordinary']
                public['import_view'] = {'character_preview': character_preview(ordinary['path'], ordinary['inventory']),
                                         'characters': ordinary['metadata']['characters']}
        transfer.require_stopped('预览存档处理选择')
        return public


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
        _checkpoint('before:copy-file:'+destination.parent.name+'/'+destination.name+'/'+Path(target).relative_to(destination).as_posix())
        shutil.copyfile(origin, target)
        _checkpoint('after:copy-file:'+destination.parent.name+'/'+destination.name+'/'+Path(target).relative_to(destination).as_posix())
        with Path(target).open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        _checkpoint('after:copy-fsync:'+destination.parent.name+'/'+destination.name+'/'+Path(target).relative_to(destination).as_posix())
        shutil.copystat(origin, target)
        return target
    transfer.copy_verified(source, destination, expected=expected, copy_function=copy_file)
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
    if action == 'restore-generation':
        return [['savedata', intent['generation']['relative']]]
    if action == 'accept-bound':
        return intent['bound']['paths']
    labels = list(TREES) if action in ('restore-all', 'restore-case', 'reimport-ordinary', 'restart') else list(TREES)[1:] if action == 'accept-all' else []
    return [[name, ''] for name in labels if before[name] is not None]


def _steps(intent):
    result = [('retire', name, relative) for name, relative in _retire_paths(intent)]
    if intent['action'] != 'preserve' and intent['before']['gate'] is not None:
        result.append(('retire-gate', '', ''))
    if intent['action'] == 'restore-generation':
        result.append(('install-file', 'savedata', intent['generation']['relative']))
    else:
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
        elif kind == 'install-file':
            piece = _extract(state['stage'][name], relative)
            if relative in state['live'][name]['files']:
                raise ValueError('目标主档尚未退休。')
            state['live'][name] = _insert(state['live'][name], relative, piece)
            state['stage'][name] = _remove(state['stage'][name], relative)
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
    if (value.get('schema') != 1 or value.get('operation') != OPERATION or value.get('root') != manifest['root'] or
            token != manifest['transaction'] or action != manifest['action'] or
            value.get('manifest_sha256') != _sha(manifest_raw) or value.get('confirmed') is not True or
            value.get('text_version') not in SUPPORTED_VERSIONS or value.get('before') != manifest['original'] or
            action not in {1: ACTIONS_V1, 2: ACTIONS_V2, 3: ACTIONS}.get(value.get('text_version'), ())):
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
                value.get('text_version') == 1 and value['before']['gate'] is not None):
            raise ValueError('单角色处理路径不属于已绑定异常。')
    elif bound is not None:
        raise ValueError('整套处理不能夹带单角色路径。')
    generation = value.get('generation')
    if action == 'restore-generation':
        if not isinstance(generation, dict) or type(generation.get('slot')) is not int or not 0 <= generation['slot'] < 10:
            raise ValueError('备份代次槽位无效。')
        relative = f"1/CUSA00207/SPRJ0005/userdata{generation['slot']:04}"
        backup_relative = f"1/CUSA00207/SPRJ0005/backup{generation['slot']:04}"
        source_tree = value['before']['trees']['savedata']
        expected = {'directories': ['1', '1/CUSA00207', '1/CUSA00207/SPRJ0005'], 'files': {relative: source_tree['files'].get(backup_relative)}}
        if (generation.get('relative') != relative or generation.get('backup_relative') != backup_relative or
                generation.get('identity_relation') not in ('same', 'primary_unknown') or
                relative not in source_tree['files'] or expected['files'][relative] is None or
                value['target']['savedata'] != expected or any(value['target'][k] is not None for k in list(TREES)[1:])):
            raise ValueError('备份代次来源或固定路径无效。')
    elif generation is not None:
        raise ValueError('此选择不能夹带单槽替换。')
    if action not in ('restore-all', 'restore-case', 'restore-generation', 'reimport-ordinary', 'restart') and any(v is not None for v in value['target'].values()):
        raise ValueError('此选择不允许替换角色文件。')
    if action in ('restore-all', 'reimport-ordinary', 'restart') and (value['target']['savedata'] is None or any(value['target'][k] is not None for k in list(TREES)[1:])):
        raise ValueError('普通备份只能作为整套 savedata 来源。')
    if action == 'restart' and value['target']['savedata'] != {'directories': [], 'files': {}}:
        raise ValueError('重新开始只允许空存档。')
    gate = value.get('new_gate')
    expected_gate = {'schema': 1, 'root': value['root'], 'transaction': token,
                     'target_digest': _sha(_bytes(value['target'])), 'reason': 'restored-original-needs-review'}
    if action == 'abandon':
        _validate_abandon(root, value, manifest)
        return
    if gate != (expected_gate if action in ('restore-case', 'restore-generation') else None):
        raise ValueError('换回门禁不属于此处理。')
    _states(value)  # Validate all fixed-path transitions before any live operation.


def _receipt(intent, raw):
    results = {'accept-bound': 'accepted-current-unverified', 'accept-all': 'accepted-current-unverified',
               'restore-all': 'selected-backup', 'restore-case': 'restored-original-needs-review',
               'restore-generation': 'restored-generation-needs-review',
               'reimport-ordinary': 'reimported-ordinary',
               'restart': 'restarted-empty',
               'use-current': 'selected-current', 'preserve': 'preserved-only', 'abandon': 'abandoned-needs-review'}
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
        raise ValueError('现场与原选择不一致，可能出现了新的进度或文件变化；未覆盖。请重新检查；可完整另存当前进度后放弃上次选择，不能假定旧保全件仍完整。')
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
    if value['action'] == 'abandon':
        _abandon_position(root, case, value, raw)
    else:
        _position(root, case, value)
    return case, value, raw


def _resume_effects(root, case, intent, raw):
    action = intent['action']
    live_folder = root/TREES['savedata']
    if action == 'abandon':
        current = inventory_slots(live_folder, intent['before']['trees']['savedata'])
        after = current
    else:
        prefix = _actual(root, case)
        current = inventory_slots(live_folder, prefix['live']['savedata'])
        after = current
        staged = prefix['stage']['savedata']
        if action == 'restore-case' and intent['target']['savedata'] is None:
            after = inventory_slots(live_folder, None)
        if staged is not None and (action != 'restore-generation' or
                                   intent['generation']['relative'] in staged['files']):
            incoming = inventory_slots(case/'stage/savedata', staged)
            if action == 'restore-generation':
                slot = intent['generation']['slot']
                after = list(current)
                after[slot] = {**current[slot], 'primary': incoming[slot]['primary']}
            else:
                after = incoming
    original = _readability(case/'original/savedata')
    result = effect(action, current, after, original['readable_generations'])
    if _intent(root)[2] != raw:
        raise world.EvidenceChangedError('预览时上次处理记录发生变化，请重新检查。')
    if action != 'abandon' and _actual(root, case) != prefix:
        raise world.EvidenceChangedError('预览时上次处理进度发生变化，请重新检查。')
    return result


def pending(root):
    root = _root(root)
    with transfer.installation_guard(root):
        result = {'can_resume': False, 'fingerprint': None, 'can_abandon': False,
                  'abandon_digest': None, 'message': '', 'abandon_message': ''}
        try:
            case, value, raw = _intent(root)
            _guards(root, own_lock=raw)
            effects = _resume_effects(root, case, value, raw)
            result.update(can_resume=True, fingerprint=_sha(raw), resume_effects=effects, message=
                '上次处理未完成，将继续原来的选择。已保全的版本不会重新挑选或合并。')
        except (ValueError, OSError, KeyError, TypeError) as error:
            result['message'] = error_message(error)
        try:
            choice, _ = _abandon_plan(root)
            result.update(can_abandon=True, abandon_digest=choice['digest'], abandon_effects=choice['effects'],
                          abandon_message=choice['description'], characters=choice['characters'])
        except (ValueError, OSError, KeyError, TypeError) as error:
            result['abandon_message'] = '当前不能安全放弃：' + error_message(error)
        return result


def _abandon_plan(root):
    """Recognize only the fixed A envelope; never execute its paths or steps."""
    lock = root/'.save-import.lock'
    lock_info, raw = _control(lock)
    value = _decode_record(raw)
    if (type(value.get('schema')) is not int or value['schema'] != 1 or
            value.get('operation') != OPERATION or not isinstance(value.get('transaction'), str) or
            not TOKEN.fullmatch(value['transaction']) or not isinstance(value.get('root'), str) or not value['root']):
        raise ValueError('此记录不是可识别的存档选择封套，当前不支持由此流程结束；不会清除普通导入、恢复或未知锁。')
    _guards(root, own_lock=raw)
    before = _capture(root)
    readable = _readability(root/TREES['savedata'])
    current = inventory_slots(root/TREES['savedata'], _content(before)['trees']['savedata'])
    impact = effect('abandon', current, current, readable['readable_generations'])
    _guards(root, own_lock=raw)
    if _capture(root) != before or _control(lock) != (lock_info, raw):
        raise transfer.OperationUnavailable('检查期间当前进度或处理记录变化，请重新检查。')
    data = {'root': str(root), 'before': before, 'content': _content(before),
            'lock_info': lock_info, 'lock_raw': raw}
    digest = _sha(_bytes({key: value for key, value in data.items() if key != 'lock_raw'} | {'effects': impact}))
    return {'digest': digest, 'effects': impact, 'characters': readable['characters'], 'description':
            '先完整另存当前全部角色、联机记录和上次处理记录，再放弃上次未完成选择。不会恢复旧目标，也不改写当前角色。'
            '当前可能是中断时的混合版本，仍须重新检查；旧保全件不自动删除，但不能保证已经缺失的旧原件完整。'}, data


def _validate_abandon(root, value, manifest):
    if value['text_version'] not in (2, 3):
        raise ValueError('此版本不支持放弃事务。')
    previous = value.get('previous_lock')
    if previous != manifest.get('previous_lock') or previous is None:
        raise ValueError('放弃事务缺少原活动锁的保全记录。')
    backups._validate_inventory({'directories': [], 'files': {'import-lock': previous}})
    case = _case_path(root, value['transaction'])
    if _content_file(_control(case/'original/control/import-lock')[0]) != previous:
        raise ValueError('旧处理记录的原字节未通过保全核验。')
    expected_gate = {'schema': 1, 'root': value['root'], 'transaction': value['transaction'],
                     'target_digest': _sha(_bytes(value['before']['trees'])), 'reason': 'abandoned-choice-needs-review'}
    if value.get('new_gate') != expected_gate:
        raise ValueError('放弃事务的重新检查门禁不匹配。')


def _require_completion(case, intent, raw):
    if intent['action'] == 'abandon':
        witness = case/'retired/completed-lock'
        if not _exists(witness):
            raise ValueError('放弃选择尚无活动锁退休证明，不能当成完成或换回来源。')
        if _content_file(_control(witness)[0]) != {'bytes': len(raw), 'sha256': _sha(raw)}:
            raise ValueError('放弃选择尚无活动锁退休证明，不能当成完成或换回来源。')


def _abandon_position(root, case, intent, raw):
    if _original_content(case) != intent['before']:
        raise ValueError('本次原件已变化，不能继续原放弃；可重新完整保全当前现场后放弃。')
    if _exists(case/'retired/completed-lock'):
        raise ValueError('活动锁与退休证明同时存在，不能猜测完成；请重新检查当前现场。')
    actual = _actual(root, case)
    expected = {'live': intent['before']['trees'], 'stage': dict.fromkeys(TREES),
                'retired': dict.fromkeys(TREES), 'retired_gate': None}
    if {k: v for k, v in actual.items() if k != 'gate'} != expected:
        raise ValueError('当前现场出现新变化，未覆盖；可重新完整保全当前进度后放弃。')
    gate_raw = _bytes(intent['new_gate'])
    gate = {'bytes': len(gate_raw), 'sha256': _sha(gate_raw)}
    if actual['gate'] == gate:
        position = 1
    elif actual['gate'] == intent['before']['gate']:
        position = 0
    else:
        raise ValueError('当前重新检查标记已变化，未覆盖；请重新保全并确认。')
    if _exists(case/'receipt.json') and (position != 1 or _json(case/'receipt.json')[0] != _receipt(intent, raw)):
        raise ValueError('放弃结果记录不匹配，未猜测或重写它。')
    return position


def _replace_record(path, raw, expected, verify, *, bounded_previous=False):
    """Replace one fixed control name atomically, never truncate an active lock."""
    _check_record(raw)
    path = transfer.plain_path(path)
    temporary = transfer.plain_path(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
    _write_new(temporary, raw)
    if _json(temporary)[1] != raw:
        raise ValueError('新控制记录未通过自读，未替换活动记录。')
    _checkpoint('after:replace-temp:'+path.name)
    _checkpoint('before:replace-record:'+path.name)
    actual = (_control(path)[1] if bounded_previous else path.read_bytes()) if _exists(path) else None
    if actual != expected:
        raise transfer.OperationUnavailable('活动控制记录已变化，未替换；请重新检查。')
    verify()  # Last full read-set/authority check after temporary-file IO.
    os.replace(temporary, path)
    _checkpoint('after:replace-record:'+path.name)
    if _json(path)[1] != raw:
        raise transfer.OperationUnavailable('控制交接后记录又变化，请重新检查；不会回放旧选择。')


def _run_abandon(root, case, intent, raw):
    _guards(root, own_lock=raw)
    position = _abandon_position(root, case, intent, raw)
    if position == 0:
        _checkpoint('before:abandon-gate')
        expected = (case/'original/gate').read_bytes() if intent['before']['gate'] is not None else None
        _replace_record(root/GATE, _bytes(intent['new_gate']), expected, lambda: (_guards(root, own_lock=raw), _intent(root)))
        _checkpoint('after:abandon-gate')
    _guards(root, own_lock=raw)
    if _abandon_position(root, case, intent, raw) != 1:
        raise ValueError('门禁尚未核验，不退休活动锁。')
    _checkpoint('before:abandon-receipt')
    if not _exists(case/'receipt.json'):
        _publish_new(case/'receipt.json', _bytes(_receipt(intent, raw)))
    _checkpoint('after:abandon-receipt')
    _checkpoint('before:abandon-unlock')
    _guards(root, own_lock=raw)
    # Re-read immutable authority as well as the live prefix before retirement.
    _, checked, checked_raw = _intent(root)
    if checked_raw != raw or _abandon_position(root, case, checked, raw) != 1:
        raise ValueError('退休前现场改变，已保持拦截。')
    if _json(case/'receipt.json')[0] != _receipt(intent, raw):
        raise ValueError('退休前结果回执不符，已保持拦截。')
    (root/'.save-import.lock').rename(transfer.plain_path(case/'retired/completed-lock'))
    _checkpoint('after:abandon-unlock')
    _require_completion(case, intent, raw)
    return _result(intent, raw)


def abandon(root, digest, *, confirmed=False):
    if confirmed is not True:
        raise ValueError('请先确认完整另存当前现场并放弃上次选择。')
    root = _root(root)
    with transfer.installation_guard(root):
        public, data = _abandon_plan(root)
        if not isinstance(digest, str) or digest != public['digest']:
            raise ValueError('上次确认后现场或处理记录变化，请重新检查并确认。')
        before, previous = data['before'], data['lock_raw']
        total = sum(row[2] for tree in before['trees'].values() if tree for row in tree[1].values())
        required = total + len(previous) + (before['gate']['bytes'] if before['gate'] else 0) + 8*1024*1024
        if shutil.disk_usage(root).free < required:
            raise OSError(errno.ENOSPC, f'保全空间不足，至少需要 {required/1048576:.1f} MB 空闲空间。', str(root))
        token = uuid.uuid4().hex
        case = _case_path(root, token)
        case.mkdir(parents=True, exist_ok=False)
        for area in ('original', 'stage', 'retired'):
            (case/area).mkdir()
        _checkpoint('before:abandon-preserve')
        for name, snap in before['trees'].items():
            if snap is not None:
                _durable_copy(root/TREES[name], case/'original'/name, snap)
            _checkpoint('abandon-preserved:'+name)
        if before['gate'] is not None:
            _write_new(case/'original/gate', (root/GATE).read_bytes())
        (case/'original/control').mkdir()
        _write_new(case/'original/control/import-lock', previous)
        if _original_content(case) != data['content']:
            raise ValueError('当前原件未完整保全，未交接活动锁。')
        created = datetime.now(timezone.utc).isoformat()
        old_control = {'bytes': len(previous), 'sha256': _sha(previous)}
        manifest = {'schema': 1, 'kind': 'resolution-before', 'transaction': token, 'root': str(root),
                    'action': 'abandon', 'created_at': created, 'original': data['content'],
                    'source_snapshot': before, 'plan_digest': digest, 'previous_lock': old_control}
        manifest_raw = _bytes(manifest)
        intent = {'schema': 1, 'operation': OPERATION, 'root': str(root), 'transaction': token,
                  'action': 'abandon', 'created_at': created, 'manifest_sha256': _sha(manifest_raw),
                  'before': data['content'], 'target': dict.fromkeys(TREES), 'bound': None,
                  'selection': None, 'plan_digest': digest, 'confirmed': True, 'text_version': RECORD_VERSION,
                  'choice': {'id': 'abandon', 'description': public['description']}, 'previous_lock': old_control,
                  'new_gate': {'schema': 1, 'root': str(root), 'transaction': token,
                               'target_digest': _sha(_bytes(data['content']['trees'])), 'reason': 'abandoned-choice-needs-review'}}
        _validate_intent(root, intent, manifest, manifest_raw)
        raw = _bytes(intent)
        for record in (manifest_raw, raw, _bytes(_receipt(intent, raw)), _bytes(intent['new_gate'])):
            _check_record(record)
        _write_new(case/'manifest.json', manifest_raw)
        _write_new(case/'intent.json', raw)
        _checkpoint('after:abandon-preserve')
        again, _ = _abandon_plan(root)
        if again['digest'] != digest or _capture(root) != before:
            raise transfer.OperationUnavailable('保全期间现场变化，未交接活动锁。')
        # Every future record is readable before the sole active control changes.
        _, verified, verified_raw = _load_manifest(root, token)
        _validate_intent(root, intent, verified, verified_raw)
        if _json(case/'intent.json')[1] != raw:
            raise ValueError('新意图未通过自读，未交接活动锁。')
        _abandon_position(root, case, intent, raw)
        _guards(root, own_lock=previous)
        _checkpoint('before:abandon-lock')
        def ready():
            current, _ = _abandon_plan(root)
            if current['digest'] != digest:
                raise transfer.OperationUnavailable('交接前现场变化，未替换旧选择。')
            _, current_manifest, current_raw = _load_manifest(root, token)
            _validate_intent(root, intent, current_manifest, current_raw)
            if _json(case/'intent.json')[1] != raw:
                raise ValueError('交接前新意图变化，未替换旧选择。')
            _abandon_position(root, case, intent, raw)
        _replace_record(root/'.save-import.lock', raw, previous, ready, bounded_previous=True)
        _checkpoint('after:abandon-lock')
        return _run_abandon(root, case, intent, raw)


def _result(intent, raw):
    receipt = _receipt(intent, raw)
    message = {'accept-bound': '已保留现有存档，停止所选旧世界回退；不代表世界状态已修复。',
               'accept-all': '现有存档文件未改，旧联机待补记录已归档，不再补入。',
               'restore-all': '已采用所选整套备份；旧联机待补记录已归档。',
               'restore-case': '已换回所选完整版本。换回前的版本已另存；请重新选择处理方式，不会自动启动。',
               'restore-generation': '已替换所选角色的主档，其它角色及备档未改。请重新检查旧联机记录。',
               'reimport-ordinary': '已从原版重新导入全部角色；原版未改，旧联机待补记录已归档。',
               'restart': '原件已完整封存并校验；当前为空存档，下次由游戏建立新进度。可在存档页备份列表选择“重新开始前封存的原件”原样换回；当前无法确认原件可用，换回后可能再次出现处理页。',
               'use-current': '原件已保全，已选用当前存档；将继续正常启动检查。',
               'preserve': '原件已完整保全，当前进度没有改变。异常仍未处理，这次先不启动。',
               'abandon': '当前五树和旧处理记录已完整另存，已放弃上次未完成选择。当前进度没有因此改写，请重新检查并选择；不会自动启动。'}[intent['action']]
    return {'ok': True, 'result': receipt['result'], 'case_id': intent['transaction'],
            'message': message + ' 处理前原件不会自动删除。',
            'needs_review': intent['action'] in ('restore-case', 'restore-generation', 'preserve', 'abandon'),
            'can_launch': intent['action'] == 'use-current'}


def _run(root, case, intent, raw):
    if intent['action'] == 'abandon':
        return _run_abandon(root, case, intent, raw)
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
        elif kind == 'install-file':
            source, destination = transfer.plain_path(case/'stage'/name/relative), transfer.plain_path(root/TREES[name]/relative)
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
    if _control(root/'.save-import.lock')[1] != raw or _actual(root, case) != states[-1]:
        raise ValueError('解除拦截前现场变化，已保留处理记录。')
    (root/'.save-import.lock').unlink()
    _checkpoint('after:unlock')
    return _result(intent, raw)


def apply(root, digest, option_id, *, confirmed=False, selection_kind=None, selection_id=None, selection_fingerprint=None, ordinary_install=None, restart_confirmed=False):
    if confirmed is not True:
        raise ValueError('请先阅读影响并明确确认这个选择。')
    if option_id == 'restart' and restart_confirmed is not True:
        raise ValueError('从头开始必须经过第二次明确确认；当前存档未改。')
    root = _root(root)
    with transfer.installation_guard(root):
        public, plan_data = _plan(root, selection_kind, selection_id, selection_fingerprint, ordinary_install)
        choice = next((row for row in public['options'] if row['id'] == option_id), None)
        expected_digest = choice.get('digest', public['digest']) if choice is not None else public['digest']
        if not isinstance(digest, str) or digest != expected_digest:
            raise ValueError('检查后来源或当前存档已变化，请重新检查并确认；尚未更换存档。')
        if option_id not in {row['id'] for row in public['options']}:
            raise ValueError('这个选择不适用于当前存档，请重新检查。')
        if option_id == 'choose-ordinary':
            raise ValueError('请先选择原版程序并重新检查；此选项不执行存档事务。')
        before, source = plan_data['before'], plan_data['source']
        generation = plan_data['generations'].get(int(option_id.rsplit('-', 1)[1])) if option_id.startswith('restore-generation-') else None
        action = 'restore-generation' if generation is not None else option_id
        target = dict.fromkeys(TREES)
        if option_id == 'restore-all':
            target['savedata'] = source['_inventory']
        elif option_id == 'restore-case':
            target = source['_original']['trees']
        elif action == 'reimport-ordinary':
            target['savedata'] = plan_data['ordinary']['inventory']
        elif action == 'restart':
            target['savedata'] = {'directories': [], 'files': {}}
        elif generation is not None:
            tree = plan_data['content']['trees']['savedata']
            target['savedata'] = {'directories': ['1', '1/CUSA00207', '1/CUSA00207/SPRJ0005'],
                                 'files': {generation['relative']: tree['files'][generation['backup_relative']]}}
        total = sum(row[2] for tree in before['trees'].values() if tree for row in tree[1].values())
        total += sum(row['bytes'] for tree in target.values() if tree for row in tree['files'].values())
        required = total + 8 * 1024 * 1024
        if shutil.disk_usage(root).free < required:
            raise ValueError(f'空间不足，至少需要 {required/1048576:.1f} MB 空闲空间保全并准备版本。请释放非存档文件占用的空间后重新检查，不要删除原件封存目录；当前存档未替换。')
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
        elif action == 'reimport-ordinary':
            origin = plan_data['ordinary']
            _durable_copy(origin['path'], case/'stage/savedata', origin['snapshot'])
            transfer.validate_savedata(case/'stage/savedata')
        elif action == 'restart':
            (case/'stage/savedata').mkdir()
        elif generation is not None:
            destination = case/'stage/savedata'/generation['relative']
            destination.parent.mkdir(parents=True)
            origin = root/TREES['savedata']/generation['backup_relative']
            _checkpoint('before:stage:generation')
            _write_new(destination, world.stable(origin))
            _checkpoint('after:stage:generation')
        _checkpoint('after:stage')
        again, _ = _plan(root, selection_kind, selection_id, selection_fingerprint, ordinary_install)
        again_choice = next((row for row in again['options'] if row['id'] == option_id), None)
        if (again_choice is None or again_choice.get('digest', again['digest']) != digest or
                _capture(root) != before):
            raise ValueError('保全期间当前存档或所选来源变化，请重新检查；尚未更换存档。')
        created = datetime.now(timezone.utc).isoformat()
        manifest = {'schema': 1, 'kind': 'resolution-before', 'transaction': token, 'root': str(root),
                    'action': action, 'created_at': created, 'original': plan_data['content'],
                    'source_snapshot': before, 'plan_digest': digest}
        manifest_raw = _bytes(manifest)
        _check_record(manifest_raw)
        _write_new(case/'manifest.json', manifest_raw)
        intent = {'schema': 1, 'operation': OPERATION, 'root': str(root), 'transaction': token,
                  'action': action, 'created_at': created, 'manifest_sha256': _sha(manifest_raw),
                  'before': plan_data['content'], 'target': target,
                  'bound': plan_data['bound'] if option_id == 'accept-bound' else None,
                  'selection': plan_data['selection'], 'plan_digest': digest, 'confirmed': True,
                  'text_version': RECORD_VERSION,
                  'choice': {key: value for key, value in choice.items() if key != 'effects'},
                  'new_gate': None}
        if generation is not None:
            intent['generation'] = generation
        if action in ('restore-case', 'restore-generation'):
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


def _completed_result(root, fingerprint, *, v2_abandon_only=False):
    base = root/'save-backups/recovery-choice'
    if not _exists(base):
        return None
    for path in base.iterdir():
        child = base / path.name
        if not TOKEN.fullmatch(child.name) or not _exists(child/'receipt.json'):
            continue
        intent, raw = _json(child/'intent.json')
        if _sha(raw) != fingerprint:
            continue
        if v2_abandon_only and (intent.get('text_version') not in (2, 3) or intent.get('action') != 'abandon'):
            return None  # A v1 receipt never gains a new completion meaning.
        _, manifest, manifest_raw = _load_manifest(root, child.name)
        _validate_intent(root, intent, manifest, manifest_raw)
        if _json(child/'receipt.json')[0] != _receipt(intent, raw):
            raise ValueError('旧事务的结果记录不一致。')
        _require_completion(child, intent, raw)
        result = _result(intent, raw)
        result.update(can_launch=False, message='该选择此前已经完成，本次没有再次改动存档。请刷新查看当前状态。')
        return result
    return None


def resume(root, fingerprint, *, confirmed=False):
    if confirmed is not True:
        raise ValueError('请先确认继续上一次的选择。')
    root = _root(root)
    with transfer.installation_guard(root):
        lock = root/'.save-import.lock'
        if not _exists(lock):
            result = _completed_result(root, fingerprint)
            if result is not None:
                return result
            raise ValueError('没有匹配的未完成选择，请重新检查当前状态。')
        _, active = _control(lock)
        if _sha(active) != fingerprint:
            result = _completed_result(root, fingerprint, v2_abandon_only=True)
            if result is not None:
                return result  # Read-only: later lock/gate/save state is untouched.
        case, intent, raw = _intent(root)
        if _sha(raw) != fingerprint:
            raise ValueError('未完成选择已经变化，请重新读取并确认。')
        _guards(root, own_lock=raw)
        return _run(root, case, intent, raw)


def dispatch(root, request):
    if not isinstance(request, dict):
        raise ValueError('存档选择请求格式不正确。')
    action = request.get('action')
    selection = {key: request.get(key) for key in ('selection_kind', 'selection_id', 'selection_fingerprint', 'ordinary_install')}
    if action == 'plan':
        return plan(root, **selection)
    if action == 'list':
        return list_cases(root)
    if action == 'apply':
        return apply(root, request.get('digest'), request.get('option_id'), confirmed=request.get('confirmed'), restart_confirmed=request.get('restart_confirmed'), **selection)
    if action == 'abandon':
        return abandon(root, request.get('digest'), confirmed=request.get('confirmed'))
    if action == 'resume':
        return resume(root, request.get('fingerprint'), confirmed=request.get('confirmed'))
    raise ValueError('不支持的存档选择操作。')


def main():
    try:
        result = dispatch(ROOT, json.load(sys.stdin))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'ok': False, 'error': error_message(error) + ' 本次未确认完成，不会自动回退存档。'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
