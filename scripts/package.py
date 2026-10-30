"""Build a clean release from source/build outputs; never package an installed profile."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
from server_bundle import install_package
from isolate_patches import patched_script
from port_compat import patched_run_script, patched_mods_script, patched_coop_saves, patched_pc_menu_script

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {".private", "data", "user", "logs", "captures", "save-backups", "save-conversion-backups", "conversion-jobs", "update-backups", "world-recovery", "save-proof", "home-save-proof", "diagnostics", "guest-items", "own-world", "shared-progress", "__pycache__", ".git",
                 ".build", "shadnet-state", "game-state", "backups"}
PRIVATE_FILES = {"shadnet.cfg", ".server.lock","profile.json", "account.clixml", "settings-snapshot.json", "launcher-state.json",
                 "last-exit.json", "save-transfer-result.json", ".save-import.lock",
                 "bbport.ini", "input.ini", "mods.json", "patches.json", ".env", "features.json", "server-admin.json",
                 "conversion-current.json", ".save-conversion.lock", "update-settings.json", "desktop-settings.json", ".update.lock", "insight-fix.json"}
DOC_PRIVATE_NAMES = {".build", ".env", "shadnet-state", "game-state", "backups", "__pycache__"}


def public_docs(folder):
    """Keep deployment templates, excluding any locally initialized server state."""
    return sorted(path for path in folder.rglob("*") if path.is_file()
                  and not (set(path.relative_to(folder).parts) & DOC_PRIVATE_NAMES))


def copy_public_docs(source, destination):
    for path in public_docs(source):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_public(files):
    for name in files:
        path = Path(name)
        if re.match(r"^(?:server/)?(?:normal|bbsc)/(?:db/|online-data\.json$)", name):
            raise ValueError("Private server state in release: " + name)
        if name.startswith("/") or path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
            raise ValueError("Unsafe archive path: " + name)
        if ({part.lower() for part in path.parts} & PRIVATE_PARTS or path.name.lower() in PRIVATE_FILES or
                any(part.startswith((".savedata-import-", ".savedata-conversion-")) for part in path.parts)):
            raise ValueError("Private state in release: " + name)


def archive_tree(folder, archive):
    files = {p.relative_to(folder).as_posix(): digest(p)
             for p in folder.rglob("*") if p.is_file() and p.name != "file-sha256.json"}
    check_public(files)
    (folder / "file-sha256.json").write_text(json.dumps(files, indent=2), encoding="utf-8")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for name in [*files, "file-sha256.json"]:
            out.write(folder / name, folder.name + "/" + name)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise ValueError("Archive CRC check failed.")
        for name, expected in files.items():
            if hashlib.sha256(check.read(folder.name + "/" + name)).hexdigest() != expected:
                raise ValueError("Archive hash mismatch: " + name)
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=Path, default=ROOT / ".build/bbport")
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--fsr-assets", type=Path)
    parser.add_argument("--server-package", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
    out = ROOT / "dist"
    dest = out / ("TheHuntLauncher-" + lock["version"] + "-windows-x64")
    if dest.exists():
        raise SystemExit("Staging folder exists. Refusing to overwrite a possible initialized install.")
    for folder in ("bin", "mod", "engine", "runtime/bin", "licenses", "source-info"):
        (dest / folder).mkdir(parents=True, exist_ok=True)
    binary_dir = args.toolchain / "bin"
    for source, target in (
        (args.port / "out/bb-probe.exe", "bin/bb-probe.exe"),
        (args.port / "out/bb-gpu-capabilities.exe", "bin/bb-gpu-capabilities.exe"),
        (args.port / "out/bb-fg401-capabilities.exe", "bin/bb-fg401-capabilities.exe"),
        (ROOT / ".build/mod/bbsc.dll", "mod/bbsc.dll"),
        (ROOT / ".build/mod/bbsc-launch.exe", "mod/bbsc-launch.exe"),
        (ROOT / ".build/save-bridge/save-bridge.dll", "mod/save-bridge.dll"),
    ):
        subprocess.run([str(binary_dir / "llvm-strip.exe"), "--strip-debug", "-o", str(dest / target), str(source)], check=True)
    shutil.copy2(ROOT / ".build/TheHuntLauncher.exe", dest)
    shutil.copy2(ROOT / ".build/HuntUpdates.dll", dest)
    shutil.copy2(ROOT / ".build/TheHunt.ico", dest)
    for path in (ROOT / "launcher").iterdir():
        if path.suffix in (".ps1", ".xaml", ".py"):
            shutil.copy2(path, dest / path.name)
    shutil.copy2(ROOT / "launcher/feature-catalog.json", dest / "feature-catalog.json")
    shutil.copy2(ROOT / "launcher/trophy-catalog.json", dest / "trophy-catalog.json")
    shutil.copytree(ROOT / 'assets', dest / 'assets')
    vc = json.loads((ROOT / 'scripts/vc-runtime.lock.json').read_text('utf-8'))
    vc_source = ROOT / '.build/vc-runtime'
    vc_installer = vc_source / vc['file']
    if vc_installer.stat().st_size != vc['size'] or digest(vc_installer) != vc['sha256']:
        raise ValueError('Fetch and verify the pinned VC++ runtime installer before packaging.')
    provenance = json.loads((vc_source / 'source.json').read_text('utf-8'))
    if provenance.get('signature') != 'Valid' or provenance.get('sha256') != vc['sha256']:
        raise ValueError('Microsoft runtime signature provenance is missing.')
    (dest / 'prerequisites').mkdir()
    shutil.copy2(vc_installer, dest / 'prerequisites' / vc['file'])
    shutil.copy2(vc_source / 'source.json', dest / 'prerequisites/source.json')
    # Windows PowerShell 5.1 requires a BOM for Chinese source text.
    for path in dest.glob("*.ps1"):
        text = path.read_text(encoding="utf-8-sig")
        path.write_text(text, encoding="utf-8-sig")
    (dest / "START-TheHunt.cmd").write_bytes(
        b'@echo off\r\nstart "" "%~dp0TheHuntLauncher.exe"\r\n')
    for name in ("README.md", "LICENSE", "NOTICE.md", "CONTRIBUTING.md", "upstream.lock.json"):
        shutil.copy2(ROOT / name, dest / name)
    copy_public_docs(ROOT / "docs", dest / "docs")
    bbsc = ROOT / ".build/bbsc"
    shutil.copy2(bbsc / "emulator/mod/mod.json", dest / "mod/mod.json")
    for source, target in (("LICENSE", "LICENSE.txt"), ("NOTICE.md", "NOTICE.txt"),
                           ("SUPPORTERS.txt", "SUPPORTERS.txt"), ("CREDITS.txt", "CREDITS.txt")):
        shutil.copy2(bbsc / source, dest / "mod" / target)
    # BBSC checks these exact filenames and sentinel strings before installing any hooks.
    for name, marker in (("LICENSE.txt", "GNU GENERAL PUBLIC LICENSE"),
                         ("NOTICE.txt", "github.com/Classmg22/BBSC"),
                         ("SUPPORTERS.txt", "BBSC SUPPORTERS")):
        if marker not in (dest / "mod" / name).read_text(encoding="utf-8"):
            raise ValueError("BBSC startup notice check would fail: " + name)
    for name in ("LICENSE", "NOTICE.md", "CREDITS.txt", "SUPPORTERS.txt"):
        shutil.copy2(bbsc / name, dest / "licenses" / ("BBSC-" + name))
    shutil.copy2(args.port / "LICENSE", dest / "licenses/BBport-LICENSE")
    for path in (args.port / "packaging/windows/licenses").glob("*"):
        if path.is_file():
            shutil.copy2(path, dest / "licenses" / path.name)
    for name, relative in (("wepoll-LICENSE", "gpu/third_party/wepoll/LICENSE"),
                           ("httplib-LICENSE", "gpu/third_party/httplib/LICENSE"),
                           ("online-NOTICE.md", "gpu/bbnet/SHADNET-PORT.md")):
        shutil.copy2(args.port / relative, dest / "licenses" / name)
    shutil.copy2(args.port / "run.py", dest / "engine/run.py")
    run_script = dest / "engine/run.py"
    run_script.write_text(patched_coop_saves(patched_run_script(run_script.read_text(encoding="utf-8"))), encoding="utf-8")
    # 1.9.5's direct runner uses driver discovery and optional exit backups.
    # Include their small dependency closure, without the upstream GUI/accounts.
    (dest / "engine/launcher").mkdir()
    for name in ("bbport_driver_runtime", "bbport_exit_backup", "bbport_saves",
                 "bbport_character", "bbport_trophies", "bbport_trophy_icons", "bbport_tls"):
        shutil.copy2(args.port / "launcher" / (name + ".py"), dest / "engine/launcher" / (name + ".py"))
    shutil.copytree(args.port / "scripts", dest / "engine/scripts", ignore=shutil.ignore_patterns("__pycache__"))
    pc_menu = dest / 'engine/scripts/pc_menu.py'
    pc_menu.write_text(patched_pc_menu_script(pc_menu.read_text('utf-8')), encoding='utf-8')
    menu_mods = dest / "engine/scripts/mods.py"
    menu_mods.write_text(patched_mods_script(menu_mods.read_text(encoding="utf-8")), encoding="utf-8")
    shutil.copy2(ROOT / "scripts/quit_text_compat.py", dest / "engine/scripts/quit_text_compat.py")
    shutil.copy2(ROOT / "scripts/pc_menu_zh.py", dest / "engine/scripts/pc_menu_zh.py")
    script = dest / "engine/scripts/patches.py"
    script.write_text(patched_script(script.read_text(encoding="utf-8")), encoding="utf-8")
    shutil.copytree(args.port / "patches", dest / "engine/patches")
    (dest / "engine/tools").mkdir()
    shutil.copy2(args.port / "tools/legacy-cpu-plan.json", dest / "engine/tools")
    # Dynamic providers are not visible to the PE import scanner. Verify them
    # against the pinned upstream manifests before adding them to the package.
    from graphics_assets import stage_graphics_assets
    graphics_source = stage_graphics_assets(args.port, dest, out)
    shutil.copy2(binary_dir / "python.exe", dest / "runtime/bin")
    version = subprocess.check_output([str(binary_dir / "python.exe"), "-c",
                                      "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                                     text=True).strip()
    lib = "lib/python" + version
    shutil.copytree(args.toolchain / lib, dest / "runtime" / lib,
                    ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "site-packages",
                                                 "idlelib", "tkinter", "turtledemo", "ensurepip", "venv",
                                                 "Tools", "_test*", "_tkinter*"))
    shutil.copytree(args.toolchain / "share/licenses", dest / "runtime/licenses")
    available = {p.name.lower(): p for p in binary_dir.glob("*.dll")}
    queue = [*dest.glob("bin/*.exe"), *dest.glob("bin/*.dll"), *dest.glob("fsr4vk/*.dll"),
             *dest.glob("mod/*.dll"), *dest.glob("mod/*.exe"),
             dest / "runtime/bin/python.exe", *(dest / "runtime" / lib / "lib-dynload").glob("*.pyd")]
    seen = set()
    while queue:
        binary = queue.pop()
        imports = subprocess.check_output([str(binary_dir / "llvm-readobj.exe"), "--coff-imports", str(binary)], text=True)
        for name in re.findall(r"^\s+Name: (\S+\.dll)$", imports, re.M | re.I):
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            if name.lower() in available:
                target = dest / "runtime/bin" / available[name.lower()].name
                shutil.copy2(available[name.lower()], target)
                queue.append(target)
            elif name.lower().startswith(("api-ms-", "ext-ms-")) or (Path("C:/Windows/System32") / name).exists():
                continue
            else:
                raise ValueError(f"Unresolved dependency: {name} in {binary.name}")
    fsr = args.fsr_assets or args.port / "fsr4_shaders"
    if fsr.is_dir():
        for path in fsr.rglob("*"):
            if path.is_file():
                if path.suffix.lower() not in (".spv", ".bin", ".json", ".txt"):
                    raise ValueError("Unexpected FSR resource: " + str(path))
                target = dest / "fsr4_shaders" / path.relative_to(fsr)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        for manifest in (dest / "fsr4_shaders").glob("*_shader_manifest.json"):
            for name, metadata in json.loads(manifest.read_text(encoding="utf-8"))["artifacts"].items():
                binary = manifest.parent / name
                if not binary.exists() and "_4320_" in name:
                    continue
                if digest(binary) != metadata["sha256"] or binary.stat().st_size != metadata["size"]:
                    raise ValueError("FSR manifest mismatch: " + name)
    info = {**lock, "features": ["early-kit-init", "safe-memscan", "isolated-patches", "per-PC-settings", "zh-CN", "WPF-launcher", "save-import-with-backup", "gameplay-feature-page", "offline-save-conversion"],
            "gameplay_validation": "RTX 5070 isolated BBport 1.9.4 run: online load, five item categories/icons/Chinese, both touchpad menus and DLSS image confirmed by user; normal exit code 0 and real seamless saves unchanged. Two-PC NPC recovery round trip and standalone reload confirmed separately; death/respawn and other gameplay combinations not re-tested in this release.",
            "core_sha256": digest(dest / "bin/bb-probe.exe"), "bbsc_sha256": digest(dest / "mod/bbsc.dll"),
            "save_bridge_sha256": digest(dest / "mod/save-bridge.dll"),
            "offline_save_sha256": digest(dest / "offline_save.py"),
            "offline_aux_sha256": digest(dest / "offline_aux.py"),
            "original_item_policy": "Restore baseline-confirmed original items individually (any nonempty subset); never grant an absent item or equip it.",
            "save_conversion_validation": "169 Python tests and WPF checks passed. All 15 nonempty original item subsets covered. One RTX 5070 role passed vanilla reload/save and post-save comparison. Supplied guest sample retains 13 pickup bits and unchanged world flags while restoring only three owned originals; user reported export and ordinary reload success after retry. Guest post-reload bytes were not collected. Pending world/shared-progress journals, empty baseline ownership and storage cases remain unsupported."}
    info["features"].extend(["automatic-updates", "verified-save-import", "unchanged-event-region", "confirmed-empty-host-cancel", "prejoin-dream-baseline"])
    info["features"].extend(["active-kit-prepare", "confirmed-menu-quit", "fixed-world-save-mapping", "native-save-proof", "guarded-import-slots"])
    info["session_recovery_validation"] = "BBport 1.9.4: isolated two-PC guest join/movement, confirmed menu quit while window stays at title, process close, automatic primary-only event recovery, standalone online reload and both actual save generations verified. Fixed 979 logical groups/939 positions retained. User confirmed items, lamps, scene and movement normal. No normal-install saves were changed during testing. Other crash/death/invasion combinations remain unverified."
    info["features"].append("post-mod-quit-text-compatibility")
    info["quit_text_validation"] = "13 synthetic and actual-mod-loader checks passed; three supplied language-mod archives preserve all non-quit entries and source bytes. Correct game-visible exit labels and direct-desktop co-op cancellation/confirmation remain pending gameplay validation."
    info["npc_recovery_validation"] = "BBport 1.9.4: two-PC isolated guest passed join, Hunter's Dream round trip, selected dismissal, normal save and standalone reload. Native function traces and both complete save generations preserve the original NPC group; another character remains unchanged. Death, crash, invasion and reversed-host combinations not re-tested. Existing damaged saves are not automatically repaired."
    info["save_safety_validation"] = "Stable verified import copies, settled auxiliary-record archiving, pending-record rejection, rollback and unconditional unchanged event-region checks are covered by the bundled regression suite. No production save is modified during packaging."
    info["features"].extend(["personal-shop-rewards", "single-personal-enemy-roll", "same-run-home-save-proof", "bbport-195-existing-save-root"])
    info["personal_rewards_validation"] = "Unchanged BBSC gameplay: user accepted personal shops, no historical NPC badge compensation, and one ordinary enemy drop on 1.9.4. Same-run return home verified 979 recovery rows / 939 persistent positions, both native save generations and clean exit. User subsequently confirmed 1.9.5 WPF candidate co-op and built-in local-server entry through Tailscale. Reuse these results only for unchanged paths; they do not claim a 1.9.6 gameplay run."
    info["bbport_upgrade_validation"] = "1.9.6: pinned core built; 237 Python tests, native WPF and runner/menu compatibility checks passed. Upstream certificate tests passed offline and online, including whole-chain Windows acceptance and wrong-host rejection. Existing isolated save paths, explicit servers and disabled Rebirth remain. Existing 1.9.5 built-in-server/Tailscale gameplay acceptance is reused within unchanged scope; no new 1.9.6 gameplay is claimed."
    (dest / "BUILD-INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    server_info = install_package(args.server_package, dest / "server", lock["version"], check_public)
    info["features"].append("bundled-local-seamless-server")
    info["features"].extend(["native-csharp-wpf-desktop", "read-only-trophy-display"])
    info["local_server"] = {"version": server_info["label"], "location": "server/Server.exe"}
    info["features"].extend(["public-online-password-account", "split-public-content-private-seamless", "renamed-launcher-entry"])
    info["launcher_entry"] = "TheHuntLauncher.exe"
    info["public_online_validation"] = "Existing-account NP authentication, hybrid metadata, public game login and read-only notes query passed through the actual bridge (HTTP 200 / ResKind 0; empty notes list in the selected query). Loopback protocol/HTTP and credential isolation regressions passed. No real game, public content write or two-player hybrid-session acceptance is claimed. Unchanged gameplay results are reused only within their scope."
    (dest / "BUILD-INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    archive = out / (dest.name + ".zip")
    files = archive_tree(dest, archive)
    (out / "SHA256SUMS.txt").write_text("".join(digest(path) + "  " + path.name + "\n"
                                               for path in (archive, graphics_source)), encoding="utf-8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "files": len(files),
                      "sha256": digest(archive)}, indent=2))


if __name__ == "__main__":
    main()
