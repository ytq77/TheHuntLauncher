"""Read this PC's normal settings; write only the isolated test configuration."""
import hashlib
import json
import os
from pathlib import Path
import shutil

# Match launcher/bbport_launcher_win.py game_environment, excluding online/save paths.
VALUES = {
    "fps_mode": ("BB_FPS", "uncap"), "frame_cap": ("BB_FPS_LIMIT", ""),
    "present_mode": ("BB_PRESENT_MODE", "Mailbox"), "language": ("BB_LANGUAGE", "1"),
    "voice_language": ("BB_VOICE_LANGUAGE", "auto"), "draw_pipe": ("BB_DRAW_PIPE", ""),
    "readbacks": ("BB_READBACKS", ""), "frames_ahead": ("BB_FRAMES_AHEAD", ""),
    "low_vram": ("BB_LOW_VRAM", ""),
}
FLAGS = {
    "fullscreen": ("BB_FULLSCREEN", False), "hdr": ("BB_HDR", False),
    "mods_enabled": ("BB_MODS_ENABLED", True), "frame_stats": ("BB_FRAME_STATS", False),
    "gpu_profile": ("BB_GPU_PROFILE", False), "vk_validation": ("BB_VK_VALIDATION", False),
}
EXTRA_KEYS = {name for name, _ in (*VALUES.values(), *FLAGS.values())} | {
    "BB_UPSCALER", "BB_UPSCALE_PRESET", "BB_RENDER_RES", "BB_OUTPUT_RES", "BB_LIVE_RES",
}


def sync_settings(root, profile, settings_path=None):
    root = Path(root).resolve()
    source = Path(profile["graphics_source"]).resolve()
    if source == root or root in source.parents:
        raise ValueError("请选择本机普通版的 bbport.ini，不能选择无缝版目录内的配置。")
    raw = source.read_bytes()
    ini = dict(line.split("=", 1) for line in raw.decode("utf-8-sig").splitlines()
               if "=" in line and not line.lstrip().startswith(("#", ";")))
    if not all(key in ini for key in ("output_res", "upscaler", "preset")):
        raise ValueError("所选 bbport.ini 缺少分辨率、超分辨率或预设字段（output_res、upscaler、preset），已停止启动。")
    if settings_path is None:
        settings_path = Path(os.environ["APPDATA"]) / "bbport-launcher/settings.json"
    settings_path = Path(settings_path)
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
    env = {}
    for key, (name, default) in VALUES.items():
        value = settings.get(key, default)
        if value is not None and str(value) != "":
            env[name] = str(value)
    for key, (name, default) in FLAGS.items():
        enabled = bool(settings.get(key, default))
        if enabled or key in ("fullscreen", "mods_enabled"):
            env[name] = "1" if enabled else "0"
    for item in str(settings.get("extra_env", "")).split():
        if "=" in item:
            key, value = item.split("=", 1)
            if key in EXTRA_KEYS:
                env[key] = value

    # Match 1.9.4's shared launcher/overlay settings, including in-game restarts.
    backend = "dx12" if ini.get("fsr411_backend") == "dx12" else "vk"
    env.update(BB_SHARED_GRAPHICS_CONFIG="1", BB_FSR411_BACKEND=backend,
               BB_FSR411_NATIVE="1" if backend == "dx12" else "0",
               BB_OBJECT_MOTION_CACHE="1" if (ini.get("object_motion_cache") == "1" and
                   ini.get("object_motion", "1") == "1" and
                   ini.get("upscaler") in {"fsr3", "fsr4", "fsr411"}) else "0",
               BB_REBIRTH="0")

    # Rebirth carries separate free-level state and changes the Doll's event flow.
    # Keep it off in seamless mode until save transfer and co-op are validated.
    lines = raw.decode("utf-8-sig").splitlines(keepends=True)
    runtime_raw = "".join(line for line in lines
                          if line.split("=", 1)[0].strip() != "rebirth")
    if runtime_raw and not runtime_raw.endswith(("\n", "\r")):
        runtime_raw += "\n"
    runtime_raw = (runtime_raw + "rebirth=0\n").encode("utf-8")

    target = root / "data/bbport.ini"
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name("bbport.before-settings-sync.ini")
    if target.exists() and not backup.exists():
        shutil.copy2(target, backup)
    temporary = target.with_suffix(".ini.tmp")
    temporary.write_bytes(runtime_raw)
    temporary.replace(target)
    input_source = source.with_name("input.ini")
    input_target = root / "data/input.ini"
    input_snapshot = {"source": str(input_source), "copied": False}
    if input_source.is_file():
        input_raw = input_source.read_bytes()
        input_backup = input_target.with_name("input.before-settings-sync.ini")
        if input_target.is_file() and not input_backup.exists():
            shutil.copy2(input_target, input_backup)
        input_temporary = input_target.with_suffix(".ini.tmp")
        input_temporary.write_bytes(input_raw)
        input_temporary.replace(input_target)
        input_snapshot.update(copied=True, source_sha256=hashlib.sha256(input_raw).hexdigest())
    snapshot = {
        "source": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "target": str(target), "settings_file": str(settings_path),
        "settings_file_exists": settings_path.exists(),
        "graphics": {key: ini[key] for key in ("output_res", "upscaler", "preset")},
        "launch_options": env,
        "runtime_overrides": {"rebirth": "0"},
        "input": input_snapshot,
        "patches_dir": str(Path(settings.get("patches_dir") or source.parent / "patches").expanduser().resolve()),
        "patches_config": str(source.parent / "patches.json"),
    }
    (root / "settings-snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return env, snapshot
