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
import uuid
import offline_aux
import trophy_sync

ROOT = Path(__file__).resolve().parent
REPARSE_POINT = 0x400
AUXILIARY = ('own-world', 'guest-items', 'shared-progress')


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
            raise ValueError("存档路径包含符号链接或目录联接，已停止导入。")
    return path


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def game_running():
    # tasklist is part of Windows. Failure to inspect processes must fail closed.
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/tasklist.exe"
    result = subprocess.run(
        [str(tasklist), "/FI", "IMAGENAME eq bb-probe.exe", "/FO", "CSV", "/NH"],
        capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    rows = csv.reader(io.StringIO(result.stdout.decode("utf-8", errors="replace")))
    return any(row and row[0].casefold() == "bb-probe.exe" for row in rows)


def require_stopped():
    try:
        running = game_running()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("无法确认游戏是否已退出，已停止导入。") from None
    if running:
        raise ValueError("检测到游戏仍在运行，请正常退出普通版和无缝版后再导入。")


def source_path(install):
    install = plain_path(install)
    if not (install / "Bloodborne.exe").is_file():
        raise ValueError("找不到普通版 Bloodborne.exe，请先选择正确的普通版程序。")
    settings = Path(os.environ["APPDATA"]) / "bbport-launcher/settings.json"
    try:
        config = json.loads(settings.read_text(encoding="utf-8-sig")) if settings.exists() else {}
        if not isinstance(config, dict):
            raise ValueError("普通版设置文件格式不正确。")
        user_dir = config.get("user_dir") or install / "user"
        if not Path(user_dir).is_absolute():
            raise ValueError("普通版存档目录必须是绝对路径。")
    except (OSError, TypeError, json.JSONDecodeError):
        raise ValueError("无法读取普通版存档目录设置。") from None
    return plain_path(Path(user_dir) / "savedata")


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


def copy_verified(source, destination, expected=None):
    source, destination = plain_path(source), plain_path(destination)
    if overlaps(source, destination) or destination.exists():
        raise ValueError('存档副本路径冲突，已停止导入。')
    original = snapshot(source) if expected is None else expected
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    if content_inventory(snapshot(destination)) != content_inventory(original):
        raise ValueError('存档副本校验失败，原无缝版存档保持不变。')
    if snapshot(source) != original:
        raise ValueError('复制期间普通版存档发生变化，原无缝版存档保持不变。')
    return original


def import_auxiliary_snapshot(root=None):
    root = ROOT if root is None else Path(root)
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


def inspect(install):
    if (ROOT / ".save-conversion.lock").exists():
        raise ValueError("转换测试正在进行，请先等待转换操作完成。")
    source = source_path(install)
    destination = plain_path(ROOT / "data/user/savedata")
    if overlaps(source, destination):
        raise ValueError("普通版与无缝版的存档路径相同或互相包含，已停止导入。")
    if not destination.parent.is_dir():
        raise ValueError("无缝版尚未初始化，请先启动一次；首次启动会自动导入普通版存档。")
    inventory = snapshot(source)
    files = inventory[1]
    valid = any(re.fullmatch(r"userdata000[0-9]", Path(name).name, re.IGNORECASE)
                and "SPRJ0005" in (part.upper() for part in Path(name).parts)
                and details[2] > 0 for name, details in files.items())
    if not valid:
        raise ValueError("未找到有效的非空 Bloodborne userdata 存档，已停止导入。")
    if destination.exists():
        # Checking the entire old tree prevents renaming or backing up linked data.
        snapshot(destination)
    import_auxiliary_snapshot()
    latest = max(details[3] for details in files.values())
    return {
        "ok": True, "source": str(source), "destination": str(destination),
        "file_count": len(files), "total_bytes": sum(item[2] for item in files.values()),
        "latest_write": datetime.fromtimestamp(latest / 1e9).strftime("%Y-%m-%d %H:%M:%S"),
        "backup": None, "error": None,
        "notice": "将替换无缝版全部角色和进度；不会合并。旧存档及已完成的联机辅助记录会一起备份、隔离。",
    }, inventory


@contextmanager
def import_lock(root=None):
    path = plain_path((ROOT if root is None else Path(root)) / ".save-import.lock")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("已有存档导入操作或遗留导入锁，请确认没有导入任务后再处理。") from None
    recovery_required = False
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    except ImportRecoveryError:
        recovery_required = True
        raise
    finally:
        os.close(descriptor)
        if not recovery_required:
            path.unlink()


def apply(install, expected_source=None):
    with import_lock():
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
            stage.rename(destination)
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
        return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-source")
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
        source = source_path(args.install)
        info["source"] = str(source)
        if overlaps(result, source):
            return 1
        info = apply(args.install, args.expected_source) if args.apply else inspect(args.install)[0]
    except (OSError, ValueError, KeyError) as error:
        info["error"] = str(error) if isinstance(error, ValueError) else "无法读取或复制存档文件，请检查路径、权限和磁盘空间。"
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.with_name(result.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(result)
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
