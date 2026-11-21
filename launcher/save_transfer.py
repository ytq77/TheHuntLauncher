"""Explicit, backed-up transfer from an ordinary install to independent BBSC saves."""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import uuid
import offline_aux
import offline_save
import trophy_sync

ROOT = Path(__file__).resolve().parent
REPARSE_POINT = 0x400
AUXILIARY = ('own-world', 'guest-items', 'shared-progress')
_guard_state = threading.local()


class OperationUnavailable(ValueError):
    """An operational/path/process guard, never evidence of corrupt saves."""


class ImportRecoveryError(ValueError):
    """An incomplete rollback must keep the launch-blocking import lock."""


def plain_path(path):
    """Check the spelling of every existing ancestor before resolving anything."""
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
            raise OperationUnavailable("存档路径包含符号链接或目录联接，已停止导入。")
    return path


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def validate_savedata(folder, *, allow_empty=False):
    """Validate the exact path loaded by this launcher, before copying a branch."""
    folder = plain_path(folder)
    if allow_empty and not any(folder.rglob('userdata*')):
        return
    game = folder/'1/CUSA00207/SPRJ0005'
    profile = game/'userdata0010'
    if not profile.is_file() or profile.stat().st_size < 84 or not any(profile.read_bytes()):
        raise ValueError('存档缺少有效的 userdata0010 角色列表，未替换。')
    used = 0
    for slot in range(10):
        primary, backup = (game/f'{stem}{slot:04}' for stem in ('userdata', 'backup'))
        if not primary.exists():
            if backup.exists() and any(backup.read_bytes()):
                raise ValueError('存档有备档但缺少对应主角色文件，未替换。')
            continue
        raw = primary.read_bytes()
        if len(raw) != 0x140000:
            raise ValueError('角色文件长度异常，未替换。')
        if not any(raw):
            continue  # The game's unused slots are all zero.
        role = offline_save.parse(raw)
        offline_aux.flag_bank(raw)
        used += 1
        if backup.exists():
            old = backup.read_bytes()
            other = offline_save.parse(old)
            offline_aux.flag_bank(old)
            if (raw[role.username:role.username+32] != old[other.username:other.username+32] or
                    raw[role.face+4:role.face+239] != old[other.face+4:other.face+239]):
                raise ValueError('角色主备两代身份不一致，未替换。')
    if not used:
        raise ValueError('存档没有可读取的已使用角色，未替换。')


def game_running():
    # tasklist is part of Windows. Failure to inspect processes must fail closed.
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/tasklist.exe"
    result = subprocess.run(
        [str(tasklist), "/FI", "IMAGENAME eq bb-probe.exe", "/FO", "CSV", "/NH"],
        capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    rows = csv.reader(io.StringIO(result.stdout.decode("utf-8", errors="replace")))
    return any(row and row[0].casefold() == "bb-probe.exe" for row in rows)


def require_stopped(action='导入'):
    try:
        running = game_running()
    except (OSError, subprocess.SubprocessError):
        raise OperationUnavailable(f"无法确认游戏是否已退出，已停止{action}。") from None
    if running:
        raise OperationUnavailable(f"检测到游戏仍在运行，请正常退出普通版和无缝版后再{action}。")


@contextmanager
def installation_guard(root=None, *, read_only=False):
    """Serialize this installation's sessions/transfers, not other installations."""
    root = plain_path(ROOT if root is None else Path(root)).resolve()
    key = os.path.normcase(str(root))
    held = getattr(_guard_state, 'held', None)
    if held is None:
        held = _guard_state.held = set()
    if key in held:
        yield
        return
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = 'Local\\TheHuntSaveTransfer-' + hashlib.sha256(key.encode('utf-8')).hexdigest()
        handle = kernel.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x80):
            kernel.CloseHandle(handle)
            raise OperationUnavailable('已有另一个游戏会话或存档操作正在使用本安装，请等待结束。')
        def close():
            try:
                kernel.ReleaseMutex(handle)
            finally:
                kernel.CloseHandle(handle)
    else:
        import fcntl
        path = plain_path(root/'.save-transfer.guard')
        try:
            descriptor = os.open(path, os.O_RDONLY if read_only else os.O_CREAT | os.O_RDWR, 0o600)
        except FileNotFoundError:
            if not read_only:
                raise
            descriptor = None
        if descriptor is None:
            # Writers leave this file in place. A fresh read needs no file of its own.
            yield
            try:
                plain_path(path).stat()
            except FileNotFoundError:
                return
            raise OperationUnavailable('查询期间出现了新的存档操作，请重新检查。')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise OperationUnavailable('已有另一个游戏会话或存档操作正在使用本安装，请等待结束。') from None
        def close():
            os.close(descriptor)
    held.add(key)
    try:
        yield
    finally:
        held.remove(key)
        close()


def write_lock_intent(descriptor, payload):
    os.lseek(descriptor, 0, os.SEEK_SET)
    remaining = memoryview(payload)
    while remaining:
        count = os.write(descriptor, remaining)
        if count <= 0:
            raise OSError('无法写入存档事务记录。')
        remaining = remaining[count:]
    os.ftruncate(descriptor, len(payload))
    os.fsync(descriptor)


def publish_intent(descriptor, folder, intent):
    """Persist the exact recovery locations before the first live-save rename."""
    payload = json.dumps(intent, ensure_ascii=False, indent=2).encode('utf-8')
    with plain_path(folder/'install.json').open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    write_lock_intent(descriptor, payload)


def character_name(raw, role=None, *, unknown='姓名未识别'):
    """Decode Bloodborne's UTF-16BE display name; never change identity bytes."""
    role = offline_save.parse(raw) if role is None else role
    try:
        return raw[role.username:role.username+32].decode('utf-16-be').split(chr(0), 1)[0]
    except UnicodeError:
        return unknown


def character_identity(raw, role):
    """The name and appearance bytes used to compare restore generations."""
    return hashlib.sha256(raw[role.username:role.username+32] + raw[role.face+4:role.face+239]).hexdigest()


def _source_io(error, kind, label, path):
    # Keep the actual OSError instance/type for A's temporary-error classifier.
    error.source_kind = kind
    error.source_label = label
    error.source_path = str(path)
    return error


def source_error_message(error):
    kind = getattr(error, 'source_kind', None)
    label = getattr(error, 'source_label', '读取普通版安装位置')
    path = getattr(error, 'source_path', getattr(error, 'filename', None) or '路径未知')
    if kind == 'manual':
        prefix = '无法读取手动选择的存档位置'
    elif kind == 'default':
        prefix = '无法读取所选 Bloodborne.exe 旁边的默认存档位置'
    elif kind == 'bbport':
        prefix = '无法读取普通版存档目录设置（bbport）'
    else:
        prefix = '无法读取普通版存档位置'
    return f'{prefix}：{path}。{label}。可以在存档页重新选择；请检查权限或稍后重试。原因：{error}'


def _savedata_path_spelling(folder):
    """Compute the exact target before IO, so an ancestor error names it too."""
    folder = Path(os.path.abspath(folder))
    if folder.name.casefold() == 'savedata':
        return folder
    if folder.name.casefold() == 'sprj0005':
        if (folder.parent.name.casefold() != 'cusa00207' or
                folder.parent.parent.name != '1' or
                folder.parent.parent.parent.name.casefold() != 'savedata'):
            raise ValueError(f'所选 SPRJ0005 不在 savedata/1/CUSA00207 下：{folder}。请重新选择 user、savedata 或正确的 SPRJ0005 文件夹。')
        return folder.parent.parent.parent
    return folder / 'savedata'


def normalize_savedata_path(folder):
    """Accept three visible levels and enforce the existing path guard."""
    return plain_path(_savedata_path_spelling(folder))


def manual_source_setting(root):
    profile = plain_path(Path(root) / 'profile.json')
    try:
        config = json.loads(profile.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return ''
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f'本启动器设置文件读不出：{profile}。请在设置页重新填写并保存；未改动存档。') from error
    if not isinstance(config, dict):
        raise ValueError(f'本启动器设置文件格式不正确：{profile}。')
    value = config.get('ordinary_savedata', '')
    if not isinstance(value, str):
        raise ValueError(f'本启动器手动存档路径格式不正确：{profile}。请在存档页清除后重新选择。')
    return value.strip()


def source_details(install, *, root=None, settings_path=None, manual_path=None):
    """Single readonly import-source policy, with all state passed explicitly."""
    install = plain_path(install)
    if not (install / "Bloodborne.exe").is_file():
        raise ValueError(f"找不到普通版 Bloodborne.exe：{install / 'Bloodborne.exe'}，请先选择正确的普通版程序。")
    if manual_path is None:
        try:
            manual_path = manual_source_setting(root) if root is not None else ''
        except OSError as error:
            raise _source_io(error, 'manual', '读取本启动器的手动来源设置',
                             Path(root) / 'profile.json')
    if not isinstance(manual_path, str):
        raise ValueError('手动存档路径必须是明确的文件夹路径。')
    if manual_path.strip():
        selected, kind = manual_path.strip(), 'manual'
        label = '按手动选择的存档位置查找'
    else:
        settings = plain_path(Path(settings_path) if settings_path is not None else
                              Path(os.environ["APPDATA"]) / "bbport-launcher/settings.json")
        try:
            config = json.loads(settings.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            config = {}
        except OSError as error:
            raise _source_io(error, 'bbport', '读取 bbport 设置里的存档位置', settings)
        if not isinstance(config, dict):
            raise ValueError(f"普通版设置文件格式不正确：{settings}。可以在存档页手动选择存档文件夹。")
        selected = config.get('user_dir')
        if selected:
            if not isinstance(selected, str):
                raise ValueError(f"普通版存档目录设置格式不正确：{settings}。可以在存档页手动选择存档文件夹。")
            kind, label = 'bbport', '按 bbport 设置里的存档位置查找'
        else:
            selected = str(install / 'user')
            kind, label = 'default', '按所选 Bloodborne.exe 旁边的默认位置查找'
    if not Path(selected).is_absolute():
        raise ValueError(f"普通版存档目录必须是绝对路径（{label}）：{selected}。请在存档页重新选择。")
    source = _savedata_path_spelling(selected)
    try:
        source = normalize_savedata_path(selected)
        info = source.stat()
    except FileNotFoundError:
        exists = False
    except OSError as error:
        raise _source_io(error, kind, label, source)
    else:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'{label}：{source}，不是文件夹。请在存档页手动选择存档文件夹。')
        exists = True
    missing = (f'没有找到普通版存档（查找位置：{source}）。{label}：{source}，没有找到。'
               '可以在存档页手动选择存档文件夹；现在进游戏会是空存档，以后可以在存档页导入。')
    return {'source': str(source), 'source_kind': kind, 'source_label': label,
            'exists': exists, 'missing_message': missing}


def resolve_source_path(install, settings_path, manual_path=None):
    """Explicit source resolver, independent of ROOT and launcher profile."""
    return Path(source_details(install, settings_path=settings_path, manual_path=manual_path)['source'])


def source_path(install, *, root=None, manual_path=None):
    # Callers supplying their installation root opt into its manual import source.
    # Conversion exports keep their existing automatic ordinary destination.
    try:
        return Path(source_details(install, root=root, manual_path=manual_path)['source'])
    except OSError as error:
        raise ValueError(source_error_message(error)) from error
    except (TypeError, json.JSONDecodeError) as error:
        settings = Path(os.environ["APPDATA"]) / "bbport-launcher/settings.json"
        raise ValueError(f"无法读取普通版存档目录设置（bbport）：{settings}。请在存档页手动选择存档文件夹。原因：{error}") from error


def snapshot(folder):
    """Record directory names and stable file identities, metadata and digests."""
    folder = plain_path(folder)
    if not folder.is_dir():
        raise ValueError("普通版存档文件夹不存在。")
    directories, files = [], {}
    def walk_error(error):
        raise error
    for base, names, filenames in os.walk(folder, followlinks=False, onerror=walk_error):
        base = Path(base)
        for name in sorted(names):
            child = plain_path(base / name)
            directories.append(child.relative_to(folder).as_posix())
        for name in sorted(filenames):
            child = plain_path(base / name)
            before = child.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("存档目录包含非常规文件，已停止导入。")
            with child.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            after = child.stat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                      value.st_mtime_ns, value.st_ctime_ns)
            if identity(before) != identity(after):
                raise ValueError("读取期间存档发生变化，请退出游戏后重试。")
            files[child.relative_to(folder).as_posix()] = (*identity(after), digest)
    return sorted(directories), files


def content_inventory(value):
    return value[0], {name: (row[2], row[-1]) for name, row in value[1].items()}


def copy_verified(source, destination, expected=None, *, copy_function=None):
    source, destination = plain_path(source), plain_path(destination)
    if overlaps(source, destination) or destination.exists():
        raise ValueError('存档副本路径冲突，已停止导入。')
    original = snapshot(source) if expected is None else expected
    if copy_function is None:
        shutil.copytree(source, destination, copy_function=shutil.copy2)
    else:
        # copytree aggregates OSError into shutil.Error. Recovery callers need
        # the actual IO type (permission/full disk is not corrupt savedata).
        failures = []
        def copy_one(src, dst):
            try:
                return copy_function(src, dst)
            except OSError as error:
                failures.append(error)
                raise
        try:
            shutil.copytree(source, destination, copy_function=copy_one)
        except shutil.Error as error:
            if failures:
                raise failures[0] from error
            raise
    if content_inventory(snapshot(destination)) != content_inventory(original):
        raise ValueError('存档副本校验失败，原无缝版存档保持不变。')
    if snapshot(source) != original:
        raise ValueError('复制期间普通版存档发生变化，原无缝版存档保持不变。')
    return original


def import_auxiliary_snapshot(root=None):
    root = ROOT if root is None else Path(root)
    from world_recovery import require_review_clear_for_transfer
    require_review_clear_for_transfer(root)
    result = {}
    for category in AUXILIARY:
        folder = plain_path(root / 'mod' / category)
        value = snapshot(folder) if folder.exists() else None
        result[category] = value
        if value is None:
            continue
        for name in value[1]:
            if category == 'shared-progress':
                raise ValueError('存在尚待处理的共享进度记录，已停止导入；请先在旧无缝存档中完成恢复，不要删除记录。')
            if category == 'own-world' and re.fullmatch(r'own-save-[0-9]\.bin', name):
                raise ValueError('存在尚待恢复的自己世界记录，已停止导入；请先加载旧无缝存档完成恢复，不要删除记录。')
            pattern = r'own-save-[0-9]\.bin\.restored' if category == 'own-world' else r'taken-save-[0-9]\.bin'
            if not re.fullmatch(pattern, name):
                raise ValueError('辅助记录格式不受支持，已停止导入；请保留记录。')
            path = plain_path(folder / name)
            if path.stat().st_size > 12 + 10000 * 129:
                raise ValueError('辅助记录过大，已停止导入。')
            offline_aux.journal(path.read_bytes(), 0x574F4242 if category == 'own-world' else 0x544C4242)
    return result


def inspect_source(source):
    """Strict readonly source inventory; no installation locks or auxiliary policy.

    Return (metadata, stable snapshot). IO errors remain IO errors. A caller
    decides whether this source is appropriate and owns its transaction.
    """
    source = plain_path(source)
    inventory = snapshot(source)
    validate_savedata(source)
    files = inventory[1]
    valid = any(re.fullmatch(r"userdata000[0-9]", Path(name).name, re.IGNORECASE)
                and "SPRJ0005" in (part.upper() for part in Path(name).parts)
                and details[2] > 0 for name, details in files.items())
    if not valid:
        raise ValueError("未找到有效的非空 Bloodborne userdata 存档，已停止导入。")
    latest = max(details[3] for details in files.values())
    characters = []
    for slot in range(10):
        path = source / '1/CUSA00207/SPRJ0005' / f'userdata{slot:04}'
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if not any(raw):
            continue
        role = offline_save.parse(raw)
        name = character_name(raw, role)
        characters.append({'slot': slot + 1, 'name': name})
    return {
        "source": str(source), "characters": characters, "file_count": len(files),
        "total_bytes": sum(item[2] for item in files.values()),
        "latest_write": datetime.fromtimestamp(latest / 1e9).strftime("%Y-%m-%d %H:%M:%S"),
    }, inventory


def install_prepared_savedata(stage, destination, expected):
    """Validate then perform one rename. No post-rename work can hide its success.

    Caller owns locks, preservation, intent, rollback and post-install checks.
    This deliberately does not validate a whole save: a verified prepared tree
    may be the preserved partial tree offered by the recovery UI.
    """
    stage, destination = plain_path(stage), plain_path(destination)
    if overlaps(stage, destination) or destination.exists():
        raise ValueError('存档副本路径冲突，已停止导入。')
    if content_inventory(snapshot(stage)) != content_inventory(expected):
        raise ValueError('存档副本校验失败，原无缝版存档保持不变。')
    stage.rename(destination)


def inspect(install):
    if (ROOT / ".save-conversion.lock").exists():
        raise ValueError("转换测试正在进行，请先等待转换操作完成。")
    details = source_details(install, root=ROOT)
    source = Path(details['source'])
    destination = plain_path(ROOT / "data/user/savedata")
    if overlaps(source, destination):
        raise ValueError("普通版与无缝版的存档路径相同或互相包含，已停止导入。")
    if not details['exists']:
        raise ValueError(details['missing_message'])
    if not destination.parent.is_dir():
        raise ValueError("无缝版尚未初始化，请先启动一次；首次启动会自动导入普通版存档。")
    if (ROOT/'.save-conversion-pending.json').exists():
        raise ValueError('存在未完成的原版存档替换，请先在转换页核对恢复记录。')
    source_info, inventory = inspect_source(source)
    if destination.exists():
        # Checking the entire old tree prevents renaming or backing up linked data.
        snapshot(destination)
    import_auxiliary_snapshot()
    return {
        "ok": True, "source": str(source), "destination": str(destination),
        "file_count": source_info["file_count"], "total_bytes": source_info["total_bytes"],
        "latest_write": source_info["latest_write"],
        "characters": source_info["characters"],
        "source_kind": details["source_kind"], "source_label": details["source_label"],
        "backup": None, "error": None,
        "notice": "将替换无缝版全部角色和进度；不会合并。旧存档及已完成的联机辅助记录会一起备份、隔离。",
    }, inventory


@contextmanager
def _import_lock(root=None, *, intent=None):
    path = plain_path((ROOT if root is None else Path(root)) / ".save-import.lock")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        raise ValueError("已有存档导入操作或遗留导入锁，请确认没有导入任务后再处理。") from None
    recovery_required = False
    try:
        payload = (json.dumps(intent, ensure_ascii=False, indent=2).encode('utf-8')
                   if intent is not None else str(os.getpid()).encode('ascii'))
        write_lock_intent(descriptor, payload)
        yield descriptor
    except (ImportRecoveryError, KeyboardInterrupt, SystemExit):
        recovery_required = True
        raise
    finally:
        os.close(descriptor)
        if not recovery_required:
            path.unlink()


@contextmanager
def import_lock(root=None, *, intent=None):
    with installation_guard(root):
        # Runtime import avoids S -> W/B -> T module initialization cycles.
        # No ordinary persistent lock or ordinary-source read may precede this.
        from save_resolution import require_launch_clear
        require_launch_clear(ROOT if root is None else Path(root))
        with _import_lock(root, intent=intent) as descriptor:
            yield descriptor


def apply(install, expected_source=None):
    with import_lock() as descriptor:
        require_stopped()
        info, original = inspect(install)
        auxiliary = import_auxiliary_snapshot()
        source, destination = Path(info["source"]), Path(info["destination"])
        trophies = trophy_sync.prepare([source.parent / trophy_sync.NAME],
                                       destination.parent / trophy_sync.NAME)
        if expected_source and source != plain_path(expected_source):
            raise ValueError("普通版存档路径已发生变化，请重新读取并确认导入来源。")
        stage = plain_path(destination.parent / (".savedata-import-" + uuid.uuid4().hex))
        # A failed stage is intentionally retained for diagnosis. It is never merged
        # into the live save directory, and the source is never modified.
        copy_verified(source, stage, original)
        require_stopped()
        # Recheck paths immediately before the only operation affecting live saves.
        plain_path(destination)
        if snapshot(source) != original or import_auxiliary_snapshot() != auxiliary:
            raise ValueError('存档或辅助记录在复制期间发生变化，已停止导入。')
        backup = None
        backup_parent = plain_path(ROOT / "save-backups" /
                                   ("import-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                                    + "-" + uuid.uuid4().hex[:8]))
        backup_parent.mkdir(parents=True)
        trophy_backup = backup_parent / 'trophies'
        trophy_sync.backup(trophies, trophy_backup)
        moved = []
        installed = False
        old_destination = snapshot(destination) if destination.exists() else None
        intent = {
            'schema': 1, 'pid': os.getpid(), 'state': 'prepared',
            'destination': str(destination), 'stage': str(stage),
            'backup': str(backup_parent/'savedata'), 'auxiliary_backup': str(backup_parent/'auxiliary'),
            'trophy_backup': str(trophy_backup),
            'original': {name: row[-1] for name, row in old_destination[1].items()} if old_destination else None,
            'expected': {name: row[-1] for name, row in original[1].items()},
        }
        # Publish the full intent before the first rename. If the worker stops
        # abruptly, the existing startup blocker now points to exact backups.
        publish_intent(descriptor, backup_parent, intent)
        try:
            if old_destination is not None:
                backup = backup_parent / 'savedata'
                destination.rename(backup)
            for category, value in auxiliary.items():
                if value is None:
                    continue
                old = plain_path(ROOT / 'mod' / category)
                saved = plain_path(backup_parent / 'auxiliary' / category)
                saved.parent.mkdir(parents=True, exist_ok=True)
                old.rename(saved)
                moved.append((old, saved))
                if snapshot(saved) != value:
                    raise ValueError('辅助记录备份校验失败。')
            install_prepared_savedata(stage, destination, original)
            installed = True
            if content_inventory(snapshot(destination)) != content_inventory(original):
                raise ValueError('安装后的存档校验失败。')
            if old_destination is not None and snapshot(backup) != old_destination:
                raise ValueError('旧存档备份校验失败。')
            trophy_sync.install(trophies)
        except (OSError, ValueError):
            trophy_error = False
            try:
                trophy_sync.rollback(trophies, trophy_backup)
            except (OSError, ValueError):
                trophy_error = True
            try:
                if installed:
                    rejected = plain_path(destination.parent / ('.savedata-import-rejected-' + uuid.uuid4().hex))
                    destination.rename(rejected)
                for old, saved in reversed(moved):
                    saved.rename(old)
                if backup is not None and backup.exists():
                    backup.rename(destination)
            except OSError:
                raise ImportRecoveryError(f'替换和回滚均失败。完整旧存档保留在：{backup or backup_parent}；辅助记录保留在：{backup_parent / "auxiliary"}。请勿启动游戏。') from None
            if trophy_error:
                raise ImportRecoveryError(f'原存档已恢复，奖杯记录回滚未完成；原始记录保留在：{trophy_backup}。请保留现场。') from None
            raise ValueError('替换存档失败，原无缝版存档及辅助记录已保持或恢复。') from None
        info['auxiliary_backup'] = str(backup_parent / 'auxiliary') if moved else None
        info['backup'] = str(backup) if backup else None
        info['trophies'] = trophies['summary']
        info['trophy_backup'] = str(trophy_backup)
        info['notice'] += '\n' + trophy_sync.notice(trophies['summary'])
        intent['state'] = 'complete'
        with (backup_parent/'install.json').open('wb') as stream:
            stream.write(json.dumps(intent, ensure_ascii=False, indent=2).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-source")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--preview-source", action="store_true")
    parser.add_argument("--manual-source")
    args = parser.parse_args(argv)
    info = {"ok": False, "source": None, "destination": str(ROOT / "data/user/savedata"),
            "file_count": 0, "total_bytes": 0, "latest_write": None, "backup": None, "error": None}
    # The caller owns this report path; reject it before any import can happen.
    result = plain_path(args.result)
    for protected in (plain_path(Path(args.install) / "user/savedata"),
                      plain_path(ROOT / "data/user/savedata"),
                      plain_path(ROOT / "save-backups")):
        if overlaps(result, protected):
            return 1
    try:
        details = source_details(args.install, root=ROOT, manual_path=args.manual_source)
        source = Path(details['source'])
        info["source"] = str(source)
        info.update({key: details[key] for key in ('source_kind', 'source_label', 'exists', 'missing_message')})
        if overlaps(result, source):
            return 1
        if args.source_only:
            if args.apply:
                raise ValueError('只读路径检查不能同时执行导入。')
            if args.preview_source and details['exists']:
                metadata, _ = inspect_source(source)
                info.update(metadata)
                info['notice'] = '只读检查完成；没有导入或改变任何存档。首次启动时才会复制到独立存档。'
            info['ok'] = True
        else:
            if args.preview_source:
                raise ValueError('来源预览只允许与 --source-only 一起使用。')
            if args.manual_source is not None:
                raise ValueError('手动路径须先在本启动器设置中保存，再读取并确认导入。')
            info = apply(args.install, args.expected_source) if args.apply else inspect(args.install)[0]
    except (OSError, ValueError, KeyError) as error:
        info["error"] = (str(error) if isinstance(error, ValueError) else source_error_message(error)
                         if hasattr(error, 'source_kind') else "无法读取或复制存档文件，请检查路径、权限和磁盘空间。")
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.with_name(result.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(result)
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
