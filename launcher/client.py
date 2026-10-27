"""Isolated BBSC session runner. Credentials arrive only through the worker environment."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import time
import world_recovery
import trophy_sync
from urllib.parse import urlsplit
from settings_bridge import sync_settings
from feature_settings import environment as feature_environment
from save_transfer import (copy_verified, import_lock, import_auxiliary_snapshot,
                           require_stopped, snapshot, content_inventory, source_path)

ROOT = Path(__file__).resolve().parent
EBOOT_SHA256 = "d65f0b4f01d59166aed16f8604196d8b7dd805abbf0758b356e8f1354c9429f9"


def state(phase, message):
    path = ROOT / "launcher-state.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"phase": phase, "message": message}, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    # Console encoding varies by Windows locale; status JSON always remains UTF-8.
    encoding = sys.stdout.encoding or "utf-8"
    print(message.encode(encoding, errors="backslashreplace").decode(encoding), flush=True)


def validate_network(network):
    for key in ("shadnet", "relay"):
        value = network.get(key, "").strip()
        parsed = urlsplit("//" + value)
        if (not parsed.hostname or parsed.port is None or parsed.path or parsed.query or
                parsed.fragment or parsed.username or parsed.password or any(c.isspace() for c in value)):
            raise ValueError(f"{key} 请填写主机:端口。")
    for key in ("webapi", "community"):
        parsed = urlsplit(network.get(key, ""))
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ("", "/")):
            raise ValueError(f"{key} 请填写 http(s)://主机:端口。")
        _ = parsed.port  # Validate numeric range even if it is omitted.
    return {key: value.strip().rstrip("/") for key, value in network.items()
            if key in {"shadnet", "relay", "webapi", "community"}}


def prepare(profile):
    if (ROOT / 'data').exists():
        return _prepare(profile)
    with import_lock(ROOT):
        require_stopped()
        if (ROOT / '.save-conversion.lock').exists():
            raise ValueError('转换操作尚未完成，已停止首次初始化。')
        old_auxiliary = import_auxiliary_snapshot(ROOT)
        if any(value is not None and value[1] for value in old_auxiliary.values()):
            raise ValueError('发现旧无缝联机记录，但独立存档尚未初始化；请先保留并核对旧存档，不能自动套用记录。')
        _prepare(profile, old_auxiliary)


def _prepare(profile, old_auxiliary=None):
    game = Path(profile["game"])
    source = Path(profile["graphics_source"]).resolve()
    install = Path(profile["install"]).resolve()
    if ROOT == source.parent or ROOT in source.parents:
        raise ValueError("请选择普通版的 bbport.ini，不能选择本包里的配置。")
    # Always validate source before creating an independent save snapshot.
    if not source.is_file() or not (install / "Bloodborne.exe").is_file():
        raise ValueError("普通版路径或画质配置不存在，请在设置中重新选择。")
    if not (game / "eboot.bin").is_file():
        raise ValueError("找不到 eboot.bin，请在设置中选择游戏文件。")
    if (ROOT / "data").exists():
        return
    state("preparing", "正在核对游戏版本…")
    with (game / "eboot.bin").open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != EBOOT_SHA256:
            raise ValueError("游戏文件与已验证的 CUSA03173 1.09 不一致，已停止初始化。")
    stage = ROOT / ("data.setup-" + uuid.uuid4().hex[:10])
    stage.mkdir()
    (stage / "user").mkdir()
    state("preparing", "正在创建独立配置与存档副本…")
    # A failed copy remains in a distinct setup folder for diagnosis; never merge
    # half-copied saves or overwrite an already-initialized data directory.
    for name in ("input.ini", "mods.json", "mods"):
        origin = install / name
        if origin.is_dir():
            shutil.copytree(origin, stage / name)
        elif origin.is_file():
            shutil.copy2(origin, stage / name)
    save_root = source_path(install)
    trophies = trophy_sync.prepare([save_root.parent / trophy_sync.NAME],
                                   stage / 'user' / trophy_sync.NAME)
    trophy_sync.backup(trophies, stage / 'trophy-import-backup')
    save_state = None
    if save_root.is_dir():
        save_state = copy_verified(save_root, stage / 'user/savedata')
    # FSR4 is optional for machines using FSR3; copy available resources once.
    origin = install / "fsr4_shaders"
    if origin.is_dir() and not (ROOT / "fsr4_shaders").exists():
        state("preparing", "正在复制本机 FSR 4 资源…")
        shutil.copytree(origin, ROOT / "fsr4_shaders")
    require_stopped()
    if (ROOT / '.save-conversion.lock').exists():
        raise ValueError('转换操作已经开始，初始化副本已保留，尚未启用。')
    if import_auxiliary_snapshot(ROOT) != old_auxiliary:
        raise ValueError('初始化期间联机辅助记录发生变化，副本已保留，尚未启用。')
    # Copy verification ran before optional assets; check the source once more
    # immediately before publishing the independent branch.
    if save_state is not None:
        if snapshot(save_root) != save_state or content_inventory(snapshot(stage / 'user/savedata')) != content_inventory(save_state):
            raise ValueError('初始化期间存档发生变化或副本校验失败，尚未启用。')
    trophy_sync.install(trophies)
    stage.rename(ROOT / "data")
    state('preparing', trophy_sync.notice(trophies['summary']))


def build_environment(profile, name, password, settings_env, snapshot):
    data = ROOT / "data"
    user = data / "user"
    network = validate_network(profile["network"])
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BB_", "BBSC_", "SHADPS4_"))}
    env.update(settings_env)
    env["PATH"] = str(ROOT / "runtime/bin") + os.pathsep + env.get("PATH", "")
    env.update(
        BB_GAME_DIR=profile["game"], BB_DATA_DIR=str(data),
        BB_CONFIG=str(data / "bbport.ini"), BB_USER_DIR=str(user), BB_GPU_USER_DIR=str(user),
        BB_MODS_DIR=str(data / "mods"), BB_MODS_CONFIG=str(data / "mods.json"),
        BB_PATCHES_DIR=snapshot["patches_dir"], BB_PATCHES_CONFIG=snapshot["patches_config"],
        BB_FSR4_DIR=str(ROOT / "fsr4_shaders"), BB_GPU_LOG="info",
        BB_FSR4VK_DIR=str(ROOT / "fsr4vk"),
        BB_PROBE=str(ROOT / "mod/bbsc-launch.exe"), BBSC_EMULATOR=str(ROOT / "bin/bb-probe.exe"),
        BB_ONLINE="1", BB_NET_BACKEND="shadnet",
        BB_SHADNET_SERVER=network["shadnet"], BB_SHADNET_WEBAPI=network["webapi"],
        BB_COMMUNITY_SERVER=network["community"], BBSC_RELAY=network["relay"],
        BB_SHADNET_NPID=name, BB_SHADNET_PASSWORD=password, BB_USER_NAME=name,
        BBSC_URL_OVERRIDES="0",
        BBSC_SAVE_DIR=str(user / "savedata/1/CUSA00207/SPRJ0005"),
    )
    for setting in json.loads((ROOT / "mod/mod.json").read_text(encoding="utf-8"))["settings"]:
        value = setting["default"]
        env.setdefault(setting["env"], str(int(value)) if isinstance(value, bool) else str(value))
    feature_env, feature_values = feature_environment(ROOT)
    env.update(feature_env)
    snapshot["features"] = feature_values
    return env, network


def main():
    if (ROOT / ".save-conversion.lock").exists():
        raise ValueError("转换测试尚未结束，请先在存档页处理测试状态。")
    if (ROOT / ".save-import.lock").exists():
        raise ValueError("存档导入正在进行或上次未完整结束，请检查 save-backups 中的备份后再启动。")
    for file, marker in (("LICENSE.txt", "GNU GENERAL PUBLIC LICENSE"),
                         ("NOTICE.txt", "github.com/Classmg22/BBSC"),
                         ("SUPPORTERS.txt", "BBSC SUPPORTERS")):
        path = ROOT / "mod" / file
        if not path.is_file() or marker not in path.read_text(encoding="utf-8")[:65536]:
            raise ValueError("模组署名文件缺失或不完整，请重新解压完整安装包。")
    profile = json.loads((ROOT / "profile.json").read_text(encoding="utf-8-sig"))
    validate_network(profile["network"])
    name = os.environ["BBSC_ACCOUNT"]
    password = os.environ["BBSC_PASSWORD"]
    if not name or not password:
        raise ValueError("请先填写联机账号与密码。")
    feature_environment(ROOT)  # Reject invalid preferences before preparing saves.
    prepare(profile)
    recovery_env = world_recovery.begin(ROOT)
    state("preparing", "正在同步这台电脑的画质与操作设置…")
    settings_env, snapshot = sync_settings(ROOT, profile)
    env, network = build_environment(profile, name, password, settings_env, snapshot)
    env.update(recovery_env)
    (ROOT / "settings-snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    user = ROOT / "data/user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "host_overrides.custom.json").write_text(json.dumps({
        "https://ss4.scej-network.jp:20443": network["community"],
        "https://bb-playlog-prod.s3.amazonaws.com:443": network["community"],
        "https://bb-playlog-test.s3.amazonaws.com:443": network["community"],
    }, indent=2), encoding="utf-8")
    log = user / ("launch-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
    started = datetime.now().isoformat()
    state("running", "游戏已启动，正在加载。道具准备情况会显示在这里。")
    with log.open("w", encoding="utf-8") as output:
        output.write("BBSC settings snapshot: " + json.dumps(snapshot) + "\n")
        output.flush()
        result = subprocess.Popen([sys.executable, str(ROOT / "engine/run.py")], env=env,
                                  cwd=ROOT / "engine", stdin=subprocess.DEVNULL,
                                  stdout=output, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        while result.poll() is None:
            try:
                world_recovery.poll(ROOT, recovery_env['THEHUNT_WORLD_RECOVERY_TOKEN'])
            except (ValueError, OSError) as error:
                world_recovery.json_file(user / 'world-recovery/latest-observation.json',
                                         {'state': 'waiting', 'message': str(error)})
            time.sleep(0.1)
    (user / "last-exit.json").write_text(json.dumps({
        "started": started, "ended": datetime.now().isoformat(),
        "exit_code": result.returncode, "log": log.name,
    }, indent=2), encoding="utf-8")
    world_recovery.process_exited(ROOT, recovery_env['THEHUNT_WORLD_RECOVERY_TOKEN'], result.returncode)
    state("preparing", "游戏已退出，正在核对自己的世界与两代存档…")
    outcomes = world_recovery.finalize(ROOT, recovery_env['THEHUNT_WORLD_RECOVERY_TOKEN'])
    waiting_backup = any('waiting-backup' in item for item in outcomes)
    world_recovery.json_file(user / 'world-recovery/latest-result.json',
                             {'state': 'waiting-backup' if waiting_backup else 'complete',
                              'outcomes': outcomes, 'game_exit_code': result.returncode})
    state("exited" if result.returncode == 0 else "error",
          ("主存档已恢复。下次加载并正常保存后，将确认游戏备份。" if waiting_backup else
           "本次狩猎已结束，世界记录核对完成。") if result.returncode == 0 else
          f"游戏退出，返回码 {result.returncode}。请打开日志查看本次记录。")
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        state("error", str(error))
        sys.exit(1)
