"""Verified launch backups and explicit savedata-only restore transactions."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from backup_characters import with_character_preview
import save_transfer as transfer
import world_recovery

ROOT = Path(__file__).resolve().parent
NAME = re.compile(r'(launch|pre-restore)-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}')
KEEP = 10
IMPORT_LOCK_MESSAGE = '存档导入正在进行或上次未完整结束，请检查 save-backups 中的备份后再启动。'


def _json(path):
    path = transfer.plain_path(path)
    with path.open('rb') as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('备份记录过大，已停止操作。')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('备份记录格式不正确，已保留原件。')
    return value, raw


def _write_json(path, value):
    path = transfer.plain_path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _inventory(value):
    directories, files = transfer.content_inventory(value)
    return {'directories': directories,
            'files': {name: {'bytes': row[0], 'sha256': row[1]} for name, row in files.items()}}


def _matches(path, expected):
    return path.is_dir() and _inventory(transfer.snapshot(path)) == expected


def _validate_inventory(value):
    if not isinstance(value, dict) or set(value) != {'directories', 'files'}:
        raise ValueError('备份文件清单格式不正确。')
    if not isinstance(value['directories'], list) or not isinstance(value['files'], dict):
        raise ValueError('备份文件清单格式不正确。')
    for name in [*value['directories'], *value['files']]:
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name or
                name.startswith('/') or any(x in ('', '.', '..') for x in name.split('/'))):
            raise ValueError('备份清单包含不安全的路径。')
    for row in value['files'].values():
        if (not isinstance(row, dict) or set(row) != {'bytes', 'sha256'} or
                type(row['bytes']) is not int or row['bytes'] < 0 or
                not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('备份文件校验信息不正确。')
    if value['directories'] != sorted(set(value['directories'])):
        raise ValueError('备份目录清单不正确。')


def _metadata(kind, created, inventory):
    return {'schema': 1, 'kind': kind, 'created_at': created,
            'file_count': len(inventory['files']),
            'total_bytes': sum(x['bytes'] for x in inventory['files'].values()),
            **inventory}


def _backup(root, name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('请选择列表中的启动备份或恢复前备份。')
    folder = transfer.plain_path(root/'save-backups'/name)
    value, raw = _json(folder/'backup.json')
    inventory = {key: value.get(key) for key in ('directories', 'files')}
    _validate_inventory(inventory)
    if (value.get('schema') != 1 or value.get('kind') != NAME.fullmatch(name).group(1) or
            type(value.get('file_count')) is not int or type(value.get('total_bytes')) is not int or
            value['file_count'] != len(inventory['files']) or
            value['total_bytes'] != sum(x['bytes'] for x in inventory['files'].values())):
        raise ValueError('备份记录和文件清单不一致。')
    created = datetime.fromisoformat(value['created_at'])
    if created.tzinfo is None:
        raise ValueError('备份时间缺少时区。')
    current = transfer.snapshot(folder/'savedata')
    if _inventory(current) != inventory:
        raise ValueError('备份内容校验不符，未恢复或删除任何存档。')
    return {'id': name, 'kind': value['kind'], 'time': value['created_at'],
            'file_count': value['file_count'], 'total_bytes': value['total_bytes'],
            'fingerprint': hashlib.sha256(raw).hexdigest(),
            '_sort': (created.timestamp(), name), '_inventory': inventory,
            '_snapshot': current, '_folder': folder}


def _list(root):
    folder = transfer.plain_path(root/'save-backups')
    rows, warnings = [], []
    if not folder.exists():
        return rows, warnings
    for child in folder.iterdir():
        if not NAME.fullmatch(child.name):
            continue  # Temporary, import-, evidence, and unrelated directories stay untouched.
        if not (child/'backup.json').exists():
            continue  # Not yet a published, verified backup.
        try:
            rows.append(_backup(root, child.name))
        except (OSError, ValueError, KeyError, TypeError) as error:
            warnings.append(f'{child.name}：无法验证，已保留。{error}')
    return sorted(rows, key=lambda row: row['_sort'], reverse=True), warnings


def _prune(root):
    rows, _ = _list(root)
    base = transfer.plain_path(root/'save-backups').resolve()
    for row in rows[KEEP:]:
        # Revalidate immediately before removing only our own completed backup kind.
        current = _backup(root, row['id'])
        folder = current['_folder']
        if folder.resolve().parent != base or current['fingerprint'] != row['fingerprint']:
            raise ValueError('旧备份在清理前发生变化，已停止清理。')
        hidden = transfer.plain_path(base/('.prune-' + uuid.uuid4().hex + '.tmp'))
        if hidden.exists():
            raise ValueError('旧备份清理临时目录冲突，已停止清理。')
        folder.rename(hidden)  # Never expose a partially deleted backup in the selectable list.
        if transfer.plain_path(hidden).resolve().parent != base:
            raise ValueError('旧备份清理路径变化，已停止清理。')
        shutil.rmtree(hidden)


def _prune_after_success(root, result):
    try:
        _prune(root)
    except (OSError, ValueError) as error:
        result['message'] += (' 旧备份清理未完成，不影响本次操作；未清理完的文件保留在 '
                              f'save-backups 的原目录或 .prune-* 临时目录中：{error}')
    return result


def _restore_auxiliary(root):
    try:
        return transfer.import_auxiliary_snapshot(root)
    except ValueError as error:
        raise ValueError(str(error).replace('导入', '恢复')) from error


def _no_conversion(root):
    if (root/'.save-conversion-pending.json').exists():
        raise ValueError('存在未完成的原版存档替换，请先在转换页核对恢复记录和备份。')
    if (root/'.save-conversion.lock').exists():
        raise ValueError('转换测试尚未结束，已停止存档恢复。')


def _restore_ready(root, *, own_import_lock=False):
    from save_resolution import require_launch_clear
    require_launch_clear(root)
    transfer.require_stopped('恢复')
    _no_conversion(root)
    if not own_import_lock and (root/'.save-import.lock').exists():
        raise ValueError('存在导入/恢复锁；普通恢复已停止，请先处理对应的未完成操作。')
    if world_recovery.review_items(root):
        raise ValueError('存在待复核的世界恢复记录，请先打开“世界恢复复核”；未替换存档。')


def launch_backup(root):
    root = transfer.plain_path(Path(root))
    saves = transfer.plain_path(root/'data/user/savedata')
    try:
        with transfer.installation_guard(root):
            from save_resolution import require_launch_clear
            require_launch_clear(root)
            if transfer.plain_path(root/'.save-import.lock').exists():
                raise ValueError('存档导入或恢复尚未完成，请先在存档页处理对应记录。')
            _no_conversion(root)
            # run_session checks require_stopped before calling us, including the skip path.
            if not saves.exists():
                return {'created': False, 'message': '启动备份已跳过：存档目录不存在。'}
            original = transfer.snapshot(saves)
            if not original[1]:
                return {'created': False, 'message': '启动备份已跳过：存档目录为空。'}
            transfer.require_stopped('启动备份')
            rows, warnings = _list(root)
            if rows and not warnings and rows[0]['_inventory'] == _inventory(original):
                return {'created': False, 'message': '启动备份已跳过：与最近一份备份内容完全相同。'}
            created = datetime.now().astimezone()
            name = 'launch-' + created.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
            base = transfer.plain_path(root/'save-backups')
            base.mkdir(exist_ok=True)
            stage = base/('.launch-' + uuid.uuid4().hex + '.tmp')
            stage.mkdir()
            transfer.copy_verified(saves, stage/'savedata', original)
            _write_json(stage/'backup.json', _metadata('launch', created.isoformat(), _inventory(original)))
            transfer.require_stopped('启动备份')
            if transfer.snapshot(saves) != original or not _matches(stage/'savedata', _inventory(original)):
                raise ValueError('复制期间存档或备份发生变化。')
            final = transfer.plain_path(base/name)
            stage.rename(final)
            _backup(root, name)
            return _prune_after_success(root, {'created': True, 'backup': str(final),
                'message': f'启动备份已校验：{name}（{len(original[1])} 个文件）。'})
    except (OSError, ValueError) as error:
        raise ValueError(f'启动前备份失败，游戏未启动：{error}') from error


def restore_lock_message(value):
    """Format an already-read restore intent without opening any paths."""
    return ('存档恢复正在进行或上次未完整结束，已阻止启动。请在存档页处理“未完成恢复”。'
            f'原存档位置：{value.get("destination", "记录不可读，请保留现场")}；'
            f'恢复前备份位置：{value.get("backup", "记录不可读，请保留现场")}；'
            f'所选备份：{value.get("selected", "记录不可读，请保留现场")}。')


def interrupted_message(root):
    """Startup only reads the lock; it never repairs or reads the live saves."""
    try:
        value, _ = _json(Path(root)/'.save-import.lock')
    except (OSError, ValueError):
        return None
    if value.get('operation') != 'restore':
        return None
    return restore_lock_message(value)


def _intent(root):
    value, raw = _json(root/'.save-import.lock')
    token = value.get('transaction')
    if (value.get('schema') != 1 or value.get('operation') != 'restore' or
            not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token) or
            value.get('root') != str(root)):
        raise ValueError('这不是可验证的本次恢复事务；请保留锁和备份，不自动处理。')
    _validate_inventory(value.get('original'))
    _validate_inventory(value.get('expected'))
    created = datetime.fromisoformat(value['created_at'])
    if created.tzinfo is None:
        raise ValueError('恢复记录时间不正确。')
    parent = root/'save-backups'/('pre-restore-' + created.strftime('%Y%m%d-%H%M%S') + '-' + token[:8])
    paths = {'destination': root/'data/user/savedata',
             'stage': root/'data/user'/('.savedata-restore-' + token),
             'backup': parent/'savedata'}
    for key, expected in paths.items():
        if value.get(key) != str(expected):
            raise ValueError('恢复意图路径不匹配，已保留现场。')
        transfer.plain_path(expected)
    selected = Path(value['selected'])
    if selected.parent != root/'save-backups' or not NAME.fullmatch(selected.name):
        raise ValueError('恢复来源不属于本安装备份，已停止处理。')
    transfer.plain_path(selected)
    manifest_error = None
    manifest = transfer.plain_path(parent/'install.json')
    if manifest.exists():
        try:
            published, _ = _json(manifest)
        except (OSError, ValueError) as error:
            manifest_error = error
        else:
            if any(published.get(key) != item for key, item in value.items() if key != 'state'):
                raise ValueError('恢复锁与意图文件不一致，已保留现场。')
    dest, stage, backup = (paths[key] for key in ('destination', 'stage', 'backup'))
    old_live = _matches(dest, value['original']) if dest.exists() else False
    if manifest_error is not None:
        # A torn duplicate manifest may only authorize cancelling an untouched original.
        if old_live and not backup.exists():
            return value, raw, 'cancel'
        raise ValueError('恢复意图文件不可读，且不能证明原存档未移动；已保留现场。') from manifest_error
    installed = _matches(dest, value['expected']) if dest.exists() else False
    old_saved = _matches(backup, value['original']) if backup.exists() else False
    staged = _matches(stage, value['expected']) if stage.exists() else False
    if old_live and not backup.exists():
        action = 'finish' if staged else 'cancel'
    elif old_saved and not dest.exists():
        action = 'finish' if staged else 'rollback'
    elif old_saved and installed and not stage.exists():
        action = 'complete'
    else:
        raise ValueError('当前存档与恢复记录不一致，可能有新的进度；不会覆盖，请保留现场。')
    return value, raw, action


def _finish(root, intent, action):
    dest, stage, backup = (Path(intent[key]) for key in ('destination', 'stage', 'backup'))
    parent = backup.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not (parent/'install.json').exists():
        _write_json(parent/'install.json', intent)
    if action == 'cancel':
        # The original is already intact. Partial scratch files remain hidden for diagnosis.
        if not _matches(dest, intent['original']) or backup.exists():
            raise ValueError('取消恢复前原存档发生变化，已停止。')
    elif action == 'rollback':
        if dest.exists() or not _matches(backup, intent['original']):
            raise ValueError('恢复原存档的目标或备份已变化，已停止。')
        backup.rename(dest)
        if not _matches(dest, intent['original']):
            raise ValueError('恢复原存档后校验失败，已保留事务记录。')
    else:
        if not backup.exists():
            if not _matches(dest, intent['original']) or not _matches(stage, intent['expected']):
                raise ValueError('改名前存档发生变化，已停止。')
            dest.rename(backup)
        if not _matches(backup, intent['original']):
            raise ValueError('恢复前备份校验失败，已停止。')
        _write_json(parent/'backup.json', _metadata('pre-restore', intent['created_at'], intent['original']))
        if not dest.exists():
            if not _matches(stage, intent['expected']):
                raise ValueError('待恢复副本校验失败，原存档仍在恢复前备份中。')
            stage.rename(dest)
        if not _matches(dest, intent['expected']) or stage.exists():
            raise ValueError('恢复后的存档不符合意图，已保留事务记录。')
    completed = dict(intent, state='cancelled' if action in ('cancel', 'rollback') else 'complete')
    _write_json(parent/'install.json', completed)
    return {'ok': True, 'message': ('未完成的恢复已取消，原存档已保持或恢复。'
                                  if completed['state'] == 'cancelled' else '存档恢复完成；当前存档已先保存为恢复前备份。'),
            'backup': str(backup), 'state': completed['state']}


def restore(root, name, fingerprint, *, confirmed=False):
    root = transfer.plain_path(Path(root)).resolve()
    if confirmed is not True:
        raise ValueError('请先确认恢复；当前存档会先自动备份。')
    with transfer.installation_guard(root):
        _restore_ready(root)
        auxiliary = _restore_auxiliary(root)
        chosen = _backup(root, name)
        if chosen['fingerprint'] != fingerprint:
            raise ValueError('所选备份记录已改变，请刷新列表后重新确认。')
        dest = transfer.plain_path(root/'data/user/savedata')
        if not dest.is_dir():
            raise ValueError('当前无缝版 savedata 存档目录不存在或不是目录，未恢复；请保留备份和现有文件。')
        original = transfer.snapshot(dest)
        token = uuid.uuid4().hex
        created = datetime.now().astimezone()
        parent = transfer.plain_path(root/'save-backups'/('pre-restore-' + created.strftime('%Y%m%d-%H%M%S') + '-' + token[:8]))
        stage = transfer.plain_path(dest.parent/('.savedata-restore-' + token))
        intent = {'schema': 1, 'operation': 'restore', 'state': 'prepared', 'pid': os.getpid(),
                  'transaction': token, 'root': str(root), 'created_at': created.isoformat(),
                  'destination': str(dest), 'stage': str(stage), 'backup': str(parent/'savedata'),
                  'selected': str(chosen['_folder']), 'original': _inventory(original),
                  'expected': chosen['_inventory']}
        with transfer.import_lock(root, intent=intent) as descriptor:
            transfer.copy_verified(chosen['_folder']/'savedata', stage, chosen['_snapshot'])
            _restore_ready(root, own_import_lock=True)
            if _restore_auxiliary(root) != auxiliary:
                raise ValueError('复制期间联机辅助记录发生变化，未替换存档。')
            if transfer.snapshot(dest) != original:
                raise ValueError('复制期间当前存档发生变化，未替换。')
            parent.mkdir(parents=True)
            transfer.publish_intent(descriptor, parent, intent)
            try:
                result = _finish(root, intent, 'finish')
            except BaseException as error:
                raise transfer.ImportRecoveryError(
                    f'存档恢复未完成，已阻止启动。恢复前存档：{parent/"savedata"}；'
                    f'原位置：{dest}。请在存档页处理未完成恢复。原因：{error}') from error
        return _prune_after_success(root, result)


def resume(root, fingerprint, *, confirmed=False):
    root = transfer.plain_path(Path(root)).resolve()
    if confirmed is not True:
        raise ValueError('请先确认处理本次未完成恢复。')
    with transfer.installation_guard(root):
        _restore_ready(root, own_import_lock=True)
        intent, raw, action = _intent(root)
        if hashlib.sha256(raw).hexdigest() != fingerprint:
            raise ValueError('恢复记录已改变，请刷新状态后重新确认。')
        if action in ('finish', 'complete'):
            _restore_auxiliary(root)
        result = _finish(root, intent, action)
        lock = transfer.plain_path(root/'.save-import.lock')
        if lock.read_bytes() != raw:
            raise ValueError('恢复锁在处理期间发生变化，已保留启动拦截。')
        lock.unlink()
        return _prune_after_success(root, result)


def overview(root):
    root = transfer.plain_path(Path(root)).resolve()
    rows, warnings = _list(root)
    pending = None
    if (root/'.save-import.lock').exists():
        pending = {'can_resume': False, 'message': interrupted_message(root) or '存在导入锁，不能作为备份恢复事务处理。'}
        try:
            with transfer.installation_guard(root):
                _restore_ready(root, own_import_lock=True)
                intent, raw, action = _intent(root)
                if action in ('finish', 'complete'):
                    _restore_auxiliary(root)
                pending.update(can_resume=True, fingerprint=hashlib.sha256(raw).hexdigest(), action=action,
                               message=(('继续完成上次恢复' if action in ('finish', 'complete') else '取消上次恢复，保持或找回原存档') +
                                        f'。原存档位置：{intent["destination"]}；恢复前备份位置：{intent["backup"]}。'))
        except (OSError, ValueError, KeyError, TypeError) as error:
            pending['message'] += ' ' + str(error)
    return {'ok': True, 'backups': [with_character_preview(row) for row in rows],
            'warnings': warnings, 'pending': pending}


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError('备份操作请求格式不正确。')
        action = request.get('action')
        if action == 'list':
            result = overview(ROOT)
        elif action == 'restore':
            result = restore(ROOT, request.get('id'), request.get('fingerprint'), confirmed=request.get('confirmed'))
        elif action == 'resume':
            result = resume(ROOT, request.get('fingerprint'), confirmed=request.get('confirmed'))
        else:
            raise ValueError('不支持的备份操作。')
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'ok': False, 'error': str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
