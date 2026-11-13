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
from port_compat import (patched_run_script, patched_mods_script, patched_coop_saves, patched_pc_menu_script,
                         patched_relay_credentials, patched_exit_backup_credentials)

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC_TOOLS = ("launcher/support/Collect-TheHunt-Diagnostics.cmd",
                    "launcher/support/Collect-TheHunt-Diagnostics.ps1")
PRIVATE_PARTS = {".private", "data", "user", "logs", "captures", "save-backups", "save-conversion-backups", "conversion-jobs", "update-backups", "world-recovery", "save-proof", "home-save-proof", "diagnostics", "guest-items", "own-world", "shared-progress", "__pycache__", ".git",
                 ".build", "shadnet-state", "game-state", "backups"}
PRIVATE_FILES = {"shadnet.cfg", ".server.lock","profile.json", "account.clixml", "settings-snapshot.json", "launcher-state.json",
                 "last-exit.json", "save-transfer-result.json", ".save-import.lock",
                 "bbport.ini", "input.ini", "mods.json", "patches.json", ".env", "features.json", "server-admin.json",
                 "conversion-current.json", ".save-conversion.lock", ".save-conversion-pending.json",
                 "update-settings.json", "desktop-settings.json", ".update.lock", ".update-transaction.json",
                 "insight-fix.json", "online-data.json"}
PUBLIC_TEMPLATES = {".env.example", "shadnet.cfg.example"}


def private_path(name, *, source_tree=False):
    parts = [part.casefold() for part in Path(name).parts]
    # Registered dependency sources may legitimately contain data/, user/ or
    # diagnostics/. Runtime trees must never accept those directories.
    excluded = (PRIVATE_PARTS - {"data", "user", "logs", "captures", "world-recovery", "save-proof",
                                "home-save-proof", "diagnostics", "guest-items", "own-world", "shared-progress"}
                if source_tree else PRIVATE_PARTS)
    if any(part.startswith("summon-record-") for part in parts):
        return True
    if set(parts) & excluded or any(
            part.startswith((".savedata-import-", ".savedata-conversion-",
                             "thehunt-diagnostics-", "thehunt-evidence-")) for part in parts):
        return True
    if re.search(r"(?:^|/)(?:normal|bbsc)/db(?:/|$)", name, re.IGNORECASE):
        return True
    for part in parts:
        if part in PUBLIC_TEMPLATES:
            continue
        for private in PRIVATE_FILES:
            # Atomic writers either append .<id>.tmp or replace the extension.
            if part == private or part.startswith(private + "."):
                return True
            if ("." in private.lstrip(".") and part.startswith(private.rsplit(".", 1)[0] + ".") and
                    re.search(r"\.(?:tmp|bak|old|new)(?:[.-].*)?$", part)):
                return True
    return False


def public_docs(folder):
    """Keep deployment templates, excluding any locally initialized server state."""
    return sorted(path for path in folder.rglob("*") if path.is_file()
                  and not private_path(path.relative_to(folder).as_posix()))


def copy_public_docs(source, destination):
    for path in public_docs(source):
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Unsafe documentation source: " + str(path))
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def copy_diagnostic_tools(source, destination):
    for relative in DIAGNOSTIC_TOOLS:
        path = source / relative
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Unsafe diagnostic tool source: " + str(path))
        encoding = "utf-8-sig" if path.suffix == ".ps1" else "ascii"
        (destination / path.name).write_text(path.read_text("utf-8-sig"), encoding, newline="\r\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_public(files, *, source_tree=False):
    for name in files:
        path = Path(name)
        if (not name or path.is_absolute() or ":" in name or "\\" in name or
                any(part in ("", ".", "..") or re.search(r'[. ]$|[\x00-\x1f<>"|?*]', part) or
                    re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
                    for part in name.split("/"))):
            raise ValueError("Unsafe archive path: " + name)
        if private_path(name, source_tree=source_tree):
            raise ValueError("Private state in release: " + name)


def archive_tree(folder, archive):
    paths = {p.relative_to(folder).as_posix(): p for p in folder.rglob("*") if p.is_file()}
    check_public(paths)
    for path in paths.values():
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("Unsafe package source: " + str(path))
    files = {name: digest(path) for name, path in paths.items() if name != "file-sha256.json"}
    (folder / "file-sha256.json").write_text(json.dumps(files, indent=2), encoding="utf-8")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for name in [*files, "file-sha256.json"]:
            out.write(folder / name, folder.name + "/" + name)
    with zipfile.ZipFile(archive) as check:
        expected_members = {folder.name + "/" + name for name in files} | {folder.name + "/file-sha256.json"}
        if check.testzip() is not None or set(check.namelist()) != expected_members:
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
    copy_diagnostic_tools(ROOT, dest)
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
    run_script.write_text(patched_relay_credentials(patched_coop_saves(patched_run_script(
        run_script.read_text(encoding="utf-8")))), encoding="utf-8")
    # The direct runner uses driver discovery and optional exit backups.
    # Include their small dependency closure, without the upstream GUI/accounts.
    (dest / "engine/launcher").mkdir()
    for name in ("bbport_driver_runtime", "bbport_exit_backup", "bbport_saves",
                 "bbport_character", "bbport_trophies", "bbport_trophy_icons", "bbport_tls"):
        shutil.copy2(args.port / "launcher" / (name + ".py"), dest / "engine/launcher" / (name + ".py"))
    shutil.copytree(args.port / "scripts", dest / "engine/scripts", ignore=shutil.ignore_patterns("__pycache__"))
    backup_runner = dest / 'engine/launcher/bbport_exit_backup.py'
    backup_runner.write_text(patched_exit_backup_credentials(backup_runner.read_text('utf-8')), 'utf-8')
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
    info["personal_rewards_validation"] = "Unchanged BBSC gameplay: user accepted personal shops, no historical NPC badge compensation, and one ordinary enemy drop on 1.9.4. Same-run return home verified 979 recovery rows / 939 persistent positions, both native save generations and clean exit. User subsequently confirmed 1.9.5 WPF candidate co-op, built-in local-server entry through Tailscale and 1.9.6 runtime. Reuse these results only for unchanged paths; they do not claim a 1.9.7 gameplay run."
    info["bbport_upgrade_validation"] = "1.9.7: pinned source reviewed against 1.9.6; runner save-root, explicit server, dependency closure, restart frame pacing and PC-menu regressions cover the integration. Release CI builds the pinned engine and runs Windows trust, Python, WPF and native BBSC checks. Targeted 1.9.7 game startup, load, kit initialization/first grant and normal exit passed on 2026-11-02. Existing co-op results are reused only for unchanged paths. In-game restart/reconnection and public-content visibility remain unverified; startup stalls are not claimed fixed."
    info["features"].append("active-parameter-kit-init")
    info["kit_initialization_validation"] = "BBport 1.9.7, 2026-11-02: real active Goods/SpEffect repositories used without scan fallback; complete readiness 4005 ms after hook installation and table validation/preparation about 1 ms. Pre-grant backup, one grant/notice, both native save generations holding one of each kit item, unchanged other character files and normal exit verified. Eleven actual-callback regression modes cover loading, invalid/read-only/truncated tables, scan fallback, backup failure and existing/storage ownership. These timings are not whole-startup or load-time guarantees; co-op and PvP item use were not newly tested."
    info["features"].extend(["guest-blank-role-guard", "voluntary-return-text", "personal-npc-gift-guard"])
    info["bell_session_validation"] = "BBport 1.9.7: preview.21 paired tests verified both players returning to their personal arrival positions after switching host and guest roles, and saving after Blank return. Normal host exits are corroborated by both traces; disconnect also passed by player report. In the preview.22 gameplay candidate, the player confirmed the guest Blank firing animation after travel, host Duel Bell sound when enabling and disabling friendly fire, and no repeated Hunt Bell prompt with the existing guest still present after travel to the Dream. New recruitment after travel remains unverified."
    info["features"].extend(["guest-blank-cross-map-effect", "duel-bell-audio", "launcher-quick-start-link"])
    info["features"].extend(["personal-return-origin", "native-disconnect-return", "host-only-duel-bell", "native-chest-gating", "bbport197-p2p-bind"])
    info["coop_fix_validation"] = "Closed chest gating and stale warp cleanup passed targeted regression checks; live chest pickup and the exact host death/guest departure/exit sequence are pending. Both clients reached auxiliary P2P bind ready; no claim that enemy latency or GPU exit code 23 is resolved."
    info["npc_gift_validation"] = "The real common-9040 guard executed during a two-player guest session. All 30 gift-request flags matched their own pre-join baselines in both save generations after a normal return. No badge or sedative was granted automatically. Native regressions cover own-world, cooperative/invasion guest and Dream-disabled boundaries; actual NPC dialogue rewards and invasion gameplay were not re-tested. Existing damaged saves are not automatically repaired."
    (dest / "BUILD-INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    server_info = install_package(args.server_package, dest / "server", lock["version"], check_public)
    info["features"].append("bundled-local-seamless-server")
    info["features"].extend(["native-csharp-wpf-desktop", "read-only-trophy-display"])
    info["local_server"] = {"version": server_info["label"], "location": "server/Server.exe"}
    info["features"].extend(["public-online-password-account", "split-public-content-private-seamless", "renamed-launcher-entry"])
    info["launcher_entry"] = "TheHuntLauncher.exe"
    info["public_online_validation"] = "Existing-account NP authentication, hybrid metadata, public game login and read-only notes query passed through the actual bridge (HTTP 200 / ResKind 0; empty notes list in the selected query). Loopback protocol/HTTP and credential isolation regressions passed. No real game, public content write or two-player hybrid-session acceptance is claimed. Unchanged gameplay results are reused only within their scope."
    apply_hardening_metadata(info)
    (dest / "BUILD-INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    archive = out / (dest.name + ".zip")
    files = archive_tree(dest, archive)
    (out / "SHA256SUMS.txt").write_text("".join(digest(path) + "  " + path.name + "\n"
                                               for path in (archive, graphics_source)), encoding="utf-8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "files": len(files),
                      "sha256": digest(archive)}, indent=2))


def apply_hardening_metadata(info):
    info["features"].extend([
        "native-active-save-slot", "current-session-quit-seal", "durable-save-install-intent",
        "durable-update-intent", "reload-generation-guards", "trusted-network-server-guards",
        "stdin-only-account-helper", "explicit-source-archive-manifest",
        "private-relay-v2-authentication", "native-room-role-routing", "authenticated-prejoin-placement",
    ])
    # Preserve the original evidence while distinguishing this changed candidate.
    for key in ("session_recovery_validation", "npc_recovery_validation", "save_conversion_validation",
                "kit_initialization_validation", "bell_session_validation", "coop_fix_validation",
                "personal_rewards_validation", "save_safety_validation"):
        if key in info:
            info[key] += (" Historical gameplay evidence applies to the named earlier implementation only. "
                          "The 2026-11-05 hardening changes have local regression/build coverage; "
                          "changed save, reload, kit and co-op paths require targeted gameplay acceptance.")
    info["hardening_validation"] = (
        "Unreleased 2026-11-05 candidate. Native BBSC, save bridge and Windows server built locally; "
        "synthetic interruption, native slot/load hooks, concurrent cancellation, update rollback, "
        "credential/UI and packaging regressions passed. No installed game, production save or "
        "running server was changed. Private relay v2 verifies the native account token, session MAC and "
        "sequence, derives membership and owner roles from native rooms, and binds prejoin placement to "
        "the authenticated broker-selected account. The legacy public-server path remains separate. "
        "Build and container results do not establish two-PC gameplay acceptance of the new protocol. "
        "Validation scope and remaining boundaries are recorded in docs/hardening-20261105.md.")


if __name__ == "__main__":
    main()
