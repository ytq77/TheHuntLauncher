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
from urllib.parse import urlsplit
from settings_bridge import sync_settings

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
    settings_file = Path(os.environ["APPDATA"]) / "bbport-launcher/settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8-sig")) if settings_file.exists() else {}
    save_root = Path(settings.get("user_dir") or install / "user") / "savedata"
    if save_root.is_dir():
        shutil.copytree(save_root, stage / "user/savedata")
    # FSR4 is optional for machines using FSR3; copy available resources once.
    origin = install / "fsr4_shaders"
    if origin.is_dir() and not (ROOT / "fsr4_shaders").exists():
        state("preparing", "正在复制本机 FSR 4 资源…")
        shutil.copytree(origin, ROOT / "fsr4_shaders")
    stage.rename(ROOT / "data")


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
    env.update(SHADPS4_BBSC_SHARED_PROGRESS="0", SHADPS4_BBSC_DISCORD="0")
    return env, network


def main():
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
    prepare(profile)
    state("preparing", "正在同步这台电脑的画质与操作设置…")
    settings_env, snapshot = sync_settings(ROOT, profile)
    env, network = build_environment(profile, name, password, settings_env, snapshot)
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
        result = subprocess.run([sys.executable, str(ROOT / "engine/run.py")], env=env,
                                cwd=ROOT / "engine", stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    (user / "last-exit.json").write_text(json.dumps({
        "started": started, "ended": datetime.now().isoformat(),
        "exit_code": result.returncode, "log": log.name,
    }, indent=2), encoding="utf-8")
    state("exited" if result.returncode == 0 else "error",
          "本次狩猎已结束。" if result.returncode == 0 else
          f"游戏退出，返回码 {result.returncode}。请打开日志查看本次记录。")
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        state("error", str(error))
        sys.exit(1)
