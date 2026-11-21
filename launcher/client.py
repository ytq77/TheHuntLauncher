"""Isolated BBSC session runner. Credentials arrive only through the worker environment."""
from collections import deque
from datetime import datetime
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid
import time
import world_recovery
import save_backups
import trophy_sync
import relay_auth
from trophy_notices import TrophyNotices
from public_online import Account, Bridge
from urllib.parse import urlsplit
from settings_bridge import sync_settings
from feature_settings import environment as feature_environment
from save_transfer import (copy_verified, import_lock, import_auxiliary_snapshot,
                           require_stopped, snapshot, content_inventory, source_path, source_details, validate_savedata,
                           installation_guard)

ROOT = Path(__file__).resolve().parent
EBOOT_SHA256 = "d65f0b4f01d59166aed16f8604196d8b7dd805abbf0758b356e8f1354c9429f9"
# Public NP reconnect notices. The game gives up after the 4th failed wandering_ghost/get
# (about 6 minutes of 502s); BBport hides drops shorter than 20 s (kSignedOutGrace).
PUBLIC_GRACE_S = 20
PUBLIC_DEGRADED_S = 120
PUBLIC_ALREADY_IN_S = 180


class SaveResolutionRequired(ValueError):
    """The real startup requires a local, explicit save choice before play."""


def require_save_resolution_clear():
    from save_resolution import require_launch_clear, ResolutionRequired
    try:
        require_launch_clear(ROOT, strict=True)
    except ResolutionRequired as error:
        state('save-resolution-needed', str(error))
        raise SaveResolutionRequired(str(error)) from error


def state(phase, message):
    path = ROOT / "launcher-state.json"
    payload = {"phase": phase, "message": message}

    def persist_status():
        try:
            world_recovery.json_file(path, payload)
        except (OSError, ValueError):
            # This is only the UI status file. Replacing its directory entry
            # works in redirected install directories without following a link at the leaf.
            temp = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
            try:
                with temp.open('x', encoding='utf-8') as stream:
                    json.dump(payload, stream, ensure_ascii=False)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)

    best_effort('launcher status', persist_status)
    # Console encoding varies by Windows locale; status JSON always remains UTF-8.
    def display():
        encoding = sys.stdout.encoding or "utf-8"
        print(message.encode(encoding, errors="backslashreplace").decode(encoding), flush=True)
    best_effort('launcher console', display)


def report_failure(stage, error):
    try:
        report_line(f'{stage}: {type(error).__name__}: {error}')
    except Exception:
        pass  # The diagnostic stream is also optional.


def report_line(message):
    try:
        encoding = sys.stderr.encoding or 'utf-8'
        print(message.encode(encoding, errors='backslashreplace').decode(encoding),
              file=sys.stderr, flush=True)
    except Exception:
        pass  # The diagnostic stream is also optional.


def format_public_event(event):
    """One log line: '<local ISO time> <kind> key=value ...'. Events never carry credentials."""
    values = dict(event)
    at = values.pop('at', None) or datetime.now().astimezone().isoformat(timespec='seconds')
    parts = [str(at), str(values.pop('kind', 'event'))]
    for key, value in values.items():
        if isinstance(value, str) and value and not any(c.isspace() or c in '"=' for c in value):
            parts.append(f'{key}={value}')
        else:
            parts.append(f'{key}={json.dumps(value, ensure_ascii=False, separators=(",", ":"))}')
    return ' '.join(parts)


class PublicOnlineEvents:
    """Append-only public NP diagnostics (data/user/public-online-events.log).

    Lines wait in memory until ``anchor`` exists: creating data/ here would make
    prepare() treat a first launch as already initialized. Never raises.
    """
    LIMIT = 256 * 1024

    def __init__(self, path, anchor=None, limit=LIMIT):
        self.path = Path(path); self.anchor = None if anchor is None else Path(anchor)
        self.limit = limit; self._lock = threading.Lock(); self._pending = deque(maxlen=200)

    def write(self, event):
        try:
            line = format_public_event(event)
            report_line('public online: ' + line)
            with self._lock:
                self._pending.append(line)
                self._flush()
        except Exception:
            pass  # Diagnostics only; the game session must not notice.

    def flush(self):
        try:
            with self._lock: self._flush()
        except Exception:
            pass

    def _flush(self):
        try:
            if not self._pending or (self.anchor is not None and not self.anchor.is_dir()):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.path.stat().st_size >= self.limit:
                    os.replace(self.path, self.path.with_name(self.path.name + '.1'))
            except FileNotFoundError:
                pass
            with self.path.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(''.join(line + '\n' for line in self._pending))
            self._pending.clear()
        except Exception:
            pass  # Keep the newest lines (bounded) for the next attempt.


def public_notice(account):
    """Launcher text for the public NP state; empty keeps the launcher's normal status."""
    state = account.get('state')
    if state == 'failed':
        return (account.get('message') or '公共账号连接失败。') + '请在启动器中重新验证公共账号后重启游戏。'
    if state != 'reconnecting':
        return ''
    down = account.get('since_disconnect_s') or 0
    if down < PUBLIC_GRACE_S:
        return ''
    if down < PUBLIC_DEGRADED_S:
        return f'公共账号连接中断，正在自动重连（已尝试 {account.get("attempts") or 0} 次）。私服召唤不受影响。'
    notice = '公共内容暂时不可用，正在后台重连。若持续约 6 分钟，游戏可能提示“无法连接至服务器”并回到标题。'
    if (account.get('already_in_for_s') or 0) >= PUBLIC_ALREADY_IN_S:
        notice += '公共账号可能在别处登录，或旧连接尚未释放。'
    return notice


def public_state(bridge, ui_session):
    account = bridge.account.snapshot()
    stats = bridge.stats()
    return {
        'ui_session': ui_session,
        'account_connected': account['state'] == 'connected',
        'game_authenticated': bridge.login_ok, 'failures': stats['failures'],
        'account_state': account['state'], 'reconnects': account['reconnects'],
        'reconnect_attempts': account['attempts'], 'last_disconnect': account['last_disconnect'],
        'consecutive_failures': stats['consecutive_failures'],
        'offline_rejections': stats['offline_rejections'],
        'upstream_failures': stats['upstream_failures'],
        'reskind_errors': stats['reskind_errors'], 'last_reskind_error': stats['last_reskind_error'],
        'notice': public_notice(account),
    }


def best_effort(stage, action):
    try:
        action()
    except Exception as error:
        report_failure(stage, error)


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
    if (ROOT/'.save-conversion-pending.json').exists():
        raise ValueError('存在未完成的原版存档替换，请先在转换页核对恢复记录。')
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
    source_info = source_details(install, root=ROOT, manual_path=profile.get('ordinary_savedata'))
    save_root = Path(source_info['source'])
    if not source_info['exists']:
        state('preparing', source_info['missing_message'])
        report_line(source_info['missing_message'])
    trophies = trophy_sync.prepare([save_root.parent / trophy_sync.NAME],
                                   stage / 'user' / trophy_sync.NAME)
    trophy_sync.backup(trophies, stage / 'trophy-import-backup')
    save_state = None
    if save_root.is_dir():
        validate_savedata(save_root, allow_empty=True)
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
           if not key.startswith(("BB_", "BBSC_", "SHADPS4_", "THEHUNT_PUBLIC_"))}
    env.update(settings_env)
    for key in relay_auth.KEYS:
        env.pop(key, None)
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
    env["THEHUNT_JOIN_CLAIM_V1"] = "1"
    env["TH_WIPE_OBSERVE_01"] = "0"
    env.pop("TH_WIPE_PREFLIGHT_ONLY", None)
    env.pop("TH_WIPE_TRACE_DIR", None)
    return env, network


def main():
    with installation_guard(ROOT):
        require_save_resolution_clear()
    if (ROOT / ".save-conversion-pending.json").exists():
        raise ValueError("存在未完成的原版存档替换，请先在转换页核对恢复记录和备份。")
    if (ROOT / ".save-conversion.lock").exists():
        raise ValueError("转换测试尚未结束，请先在存档页处理测试状态。")
    if (ROOT / ".save-import.lock").exists():
        raise ValueError(save_backups.interrupted_message(ROOT) or
                         save_backups.IMPORT_LOCK_MESSAGE)
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
    public_name = os.environ.pop('THEHUNT_PUBLIC_ACCOUNT', '')
    public_password = os.environ.pop('THEHUNT_PUBLIC_PASSWORD', '')
    with ExitStack() as services:
        bridge = None; events = None
        if profile.get('public_online', True):
            state('preparing', '正在连接 SuperMedo 公共账号…')
            events = PublicOnlineEvents(ROOT / 'data/user/public-online-events.log', anchor=ROOT / 'data')
            # The account keeps the password in memory only, to reconnect after drops.
            account = services.enter_context(Account(public_name, public_password, keep_alive=True,
                                                     on_event=events.write))
            public_password = None
            bridge = services.enter_context(Bridge(account, profile['network']['community'].rstrip('/')))
        return run_session(profile, name, password, bridge, events)


def run_session(profile, name, password, bridge, public_events=None):
    from save_resolution import ResolutionRequired
    with installation_guard(ROOT):
        require_save_resolution_clear()
        prepare(profile)
        if public_events is not None:
            public_events.flush()  # data/ exists now; write the startup connection record.
        if bridge and not bridge.account.wait_connected(30):
            raise ValueError('初始化期间公共账号连接已断开，请重新启动。')
        require_stopped('启动备份')
        backup = save_backups.launch_backup(ROOT)
        state('preparing', backup['message'])
        try:
            recovery_env = world_recovery.begin(ROOT)
        except (world_recovery.ReviewRequired, ResolutionRequired) as error:
            state('save-resolution-needed', str(error))
            raise SaveResolutionRequired(str(error)) from error
        token = recovery_env['THEHUNT_WORLD_RECOVERY_TOKEN']
        try:
            seal = world_recovery.quit_seal(token)
        except OSError:
            # The first mapping attempt may fail transiently. Never launch without it.
            seal = world_recovery.quit_seal(token)
        try:
            return run_game(profile, name, password, bridge, recovery_env, seal)
        finally:
            best_effort('quit mapping close', seal.close)


def run_game(profile, name, password, bridge, recovery_env, seal):
    trophy_notices = TrophyNotices(ROOT, os.environ.get('THEHUNT_UI_SESSION'))
    state("preparing", "正在同步这台电脑的画质与操作设置…")
    settings_env, snapshot = sync_settings(ROOT, profile)
    env, network = build_environment(profile, name, password, settings_env, snapshot)
    # Keep the private NP companion short-lived and pass its token only to this child.
    # It has already received Terminate/EOF before Popen can start the game's NP login.
    env.update(relay_auth.environment(network, name, password))
    env.update(recovery_env)
    (ROOT / "settings-snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    user = ROOT / "data/user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "host_overrides.custom.json").write_text(json.dumps({
        "https://ss4.scej-network.jp:20443": bridge.origin if bridge else network["community"],
        "https://bb-playlog-prod.s3.amazonaws.com:443": network["community"],
        "https://bb-playlog-test.s3.amazonaws.com:443": network["community"],
    }, indent=2), encoding="utf-8")
    log = user / ("launch-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
    started = datetime.now().isoformat()
    token = recovery_env['THEHUNT_WORLD_RECOVERY_TOKEN']
    output = log.open("w", encoding="utf-8")
    try:
        output.write("BBSC settings snapshot: " + json.dumps(snapshot) + "\n")
        output.flush()
        result = subprocess.Popen([sys.executable, str(ROOT / "engine/run.py")], env=env,
                                  cwd=ROOT / "engine", stdin=subprocess.DEVNULL,
                                  stdout=output, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        for key in relay_auth.KEYS:
            env.pop(key, None)
        state("running", "游戏已启动，正在加载。道具准备情况会显示在这里。")
        exit_code = monitor_game(result, trophy_notices, bridge, user, token)
    finally:
        best_effort('game log close', output.close)
    return finish_game(user, token, seal, exit_code, started, log.name)


def monitor_game(result, trophy_notices, bridge, user, token):
    retry_at = {}
    last_public_state = None

    def observe(stage, action):
        now = time.monotonic()
        if now < retry_at.get(stage, 0):
            return
        try:
            action()
        except Exception as error:
            retry_at[stage] = now + 1
            report_failure(stage, error)
            if stage == 'world recovery observation':
                best_effort('world recovery status', lambda: world_recovery.json_file(
                    user / 'world-recovery/latest-observation.json',
                    {'state': 'waiting', 'message': str(error)}))

    def publish_public():
        nonlocal last_public_state
        current = public_state(bridge, os.environ.get('THEHUNT_UI_SESSION'))
        if current != last_public_state:
            world_recovery.json_file(user / 'public-online-state.json', current)
            last_public_state = current

    while True:
        exit_code = result.poll()
        if exit_code is not None:
            return exit_code
        observe('trophy observation', trophy_notices.poll)
        if bridge is not None:
            observe('public online observation', publish_public)
        observe('world recovery observation', lambda: world_recovery.poll(ROOT, token))
        time.sleep(0.1)


def finish_game(user, token, seal, exit_code, started, log_name):
    quit_sequence = 0
    recovery_error = None
    exit_record_failure = None
    try:
        world_recovery.process_exited(ROOT, token, exit_code)
    except world_recovery.ExitRecordWriteError as error:
        exit_record_failure = error
        report_failure('game exit evidence', error)
    except Exception as error:
        recovery_error = error
        report_failure('game exit evidence', error)
    try:
        quit_sequence = world_recovery.read_quit_seal(seal)
    except Exception as error:
        recovery_error = recovery_error or error
        report_failure('game quit seal', error)
    live_evidence = {}
    if exit_record_failure is not None:
        if type(exit_code) is int and exit_code == 0 and type(quit_sequence) is int and quit_sequence > 0:
            live_evidence['exit_record_failure'] = exit_record_failure
        else:
            recovery_error = recovery_error or exit_record_failure
    outcomes = []
    try:
        outcomes = world_recovery.finalize(ROOT, token, quit_sequence=quit_sequence,
                                           observed_exit=exit_code, **live_evidence)
    except Exception as error:
        recovery_error = error
        report_failure('world recovery finalization', error)
    best_effort('game exit status', lambda: world_recovery.json_file(user / "last-exit.json", {
        "started": started, "ended": datetime.now().isoformat(),
        "exit_code": exit_code, "log": log_name,
    }))
    waiting_backup = any('waiting-backup' in item for item in outcomes)
    best_effort('world recovery result', lambda: world_recovery.json_file(
        user / 'world-recovery/latest-result.json',
        {'state': 'review-required' if recovery_error else 'waiting-backup' if waiting_backup else 'complete',
         'outcomes': outcomes, 'game_exit_code': exit_code}))
    if recovery_error:
        state("error", f"游戏已退出（返回码 {exit_code}）。世界恢复需复核：{recovery_error} 请打开「联机存档检查」。")
        return exit_code or 1
    state("exited" if exit_code == 0 else "error",
          ("主存档已恢复。下次加载并正常保存后，将确认游戏备份。" if waiting_backup else
           "本次狩猎已结束，世界记录核对完成。") if exit_code == 0 else
          f"游戏退出，返回码 {exit_code}。请打开日志查看本次记录。")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        report_failure('launcher startup', error)
        from save_resolution import error_message
        state("save-resolution-needed" if isinstance(error, SaveResolutionRequired) else "error", error_message(error))
        sys.exit(1)
