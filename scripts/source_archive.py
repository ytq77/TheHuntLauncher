"""Attach the exact patched application sources beside every binary release."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile
from package import ROOT, check_public, digest, private_path

BBSC_GENERATED = (
    "emulator/mod/bbsc_launch_zh_cn.cpp", "emulator/mod/bbsc_mod_zh_cn.cpp",
    "emulator/mod/bbsc_slot_guard.inc",
    "emulator/mod/bbsc_relay_auth.h", "emulator/mod/bbsc_relay_auth_transport.inc", "emulator/mod/bbsc_visibility_wire.h",
    "emulator/mod/bbsc_relay_auth_http.h", "emulator/mod/bbsc_j4_diag.inc",
    "emulator/mod/bbsc_summon_cleanup.h", "emulator/mod/bbsc_summon_cleanup.inc",
    *("emulator/src/core/" + name for name in (
        "bbsc_active_save.inc", "bbsc_relay_auth_core.inc", "bbsc_relay_auth_guard.h",
        "bbsc_banner_diagnostics.inc", "bbsc_blank_menu.inc", "bbsc_coop_effect_restore.inc",
        "bbsc_empty_host_cancel_policy.h", "bbsc_empty_host_cancel.inc", "bbsc_guest_return.inc",
        "bbsc_home_save_proof.inc", "bbsc_insight_refund.inc", "bbsc_kit_fast_prepare.inc",
        "bbsc_kit_repository.inc", "bbsc_memscan.cpp", "bbsc_menu_quit.inc", "bbsc_messages_zh_cn.inc",
        "bbsc_personal_rewards_policy.h", "bbsc_personal_rewards.inc", "bbsc_save_proof.inc",
        "bbsc_private_join_recovery.inc", "bbsc_first_spawn.inc", "bbsc_load_protection.inc", "bbsc_peer_display.inc", "bbsc_host_ready.inc", "bbsc_visual_diagnostics.inc", "bbsc_visibility_wire.h",
        "bbsc_summon_type.inc", "bbsc_world_recovery.inc", "bloodborne_re_zh_cn.cpp",
        "windows-zh-cn-validation.json", "zh-cn-validation.json")),
)
BRIDGE_GENERATED = ("bloodborne_re_save.cpp", "bbsc_mod_save.cpp")
SOURCE_EXCLUDES = {"installer-tests", "test-output", "dist", "build", "build-ui", "downloads", "bbsc-game"}


def safe_source(folder, name):
    check_public([name], source_tree=True)
    path = folder / name
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Missing or unsafe source: " + str(path))
    return path


def launcher_sources(folder):
    """Use the registered manifest, never discover extra local files recursively."""
    for name in tracked(folder):
        if private_path(name) or {part.casefold() for part in Path(name).parts} & SOURCE_EXCLUDES:
            continue
        if name.endswith((".pyc", ".log")):
            continue
        yield safe_source(folder, name)


def tracked(path):
    return sorted(name for name in subprocess.check_output(
        ["git", "-c", "core.longpaths=true", "-c", "safe.directory=" + path.as_posix(), "-C", str(path),
         "ls-files", "--recurse-submodules", "-z"]).decode("utf-8").split("\0") if name)


def pinned_sources(folder, revision, recurse_submodules=True):
    """Include gitlink contents at their recorded revisions, not extra local files."""
    git = ["git", "-c", "core.longpaths=true", "-c", "safe.directory=" + folder.as_posix(), "-C", str(folder)]
    entries = subprocess.check_output([*git, "ls-tree", "-r", "-z", revision]).decode("utf-8").split("\0")
    for entry in entries:
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        _, kind, object_id = metadata.split(" ")
        if private_path(name, source_tree=True):
            continue
        if kind == "blob":
            yield safe_source(folder, name), name
        elif kind == "commit" and recurse_submodules:
            check_public([name], source_tree=True)
            child = folder / name
            if not child.resolve().is_relative_to(folder.resolve()):
                raise ValueError("Unsafe submodule source: " + name)
            for path, relative in pinned_sources(child, object_id):
                yield path, name + "/" + relative


def bbsc_sources(folder, revision):
    # This DLL uses BBport/toolchain headers, not BBSC's uninitialized emulator
    # submodules. fetch_sources.py intentionally only initializes BBport's tree.
    sources = {name: path for path, name in pinned_sources(folder, revision, recurse_submodules=False)}
    sources.update((name, safe_source(folder, name)) for name in BBSC_GENERATED)
    # A new generated include must be registered explicitly, rather than silently
    # omitted or picked up with every other untracked file in the build directory.
    for name, path in sources.items():
        if path.suffix not in (".cpp", ".h", ".inc"):
            continue
        # Some upstream comments use a legacy encoding. Inspect include lines
        # without rewriting or rejecting the original source bytes.
        for include in re.findall(r'^\s*#\s*include\s*"([^"]+)"', path.read_text("utf-8", errors="replace"), re.M):
            for parent in (path.parent, folder / "emulator/src", folder / "emulator/mod"):
                dependency = parent / include
                if dependency.is_file():
                    if not dependency.resolve().is_relative_to(folder.resolve()):
                        raise ValueError("Unsafe BBSC include: " + include)
                    relative = dependency.resolve().relative_to(folder.resolve()).as_posix()
                    if relative not in sources:
                        raise ValueError("Unlisted BBSC include: " + relative)
                    break
    yield from ((path, name) for name, path in sorted(sources.items()))


def source_entries(root, port):
    for path in launcher_sources(root):
        yield path, "bbsc-launcher/" + path.relative_to(root).as_posix()
    server = root / "server"
    manifest = json.loads((server / "recipe/sources.json").read_text("utf-8"))
    fixed = [(item["name"], item["sha256"]) for item in manifest["archives"]]
    fixed += [("bbsc/" + name, value) for name, value in manifest["bbsc"]["files"].items()]
    for name, expected in fixed:
        path = safe_source(server / "windows/downloads", name)
        if digest(path) != expected:
            raise ValueError("Server source hash mismatch: " + name)
        # Preserve the path consumed by prepare-sources.py after extraction.
        yield path, "bbsc-launcher/server/windows/downloads/" + name
    lock = json.loads((root / "upstream.lock.json").read_text("utf-8"))
    for path, name in bbsc_sources(root / ".build/bbsc", lock["bbsc"]["commit"]):
        yield path, "bbsc/" + name
    for path, name in pinned_sources(port.resolve(), lock["bbport"]["commit"]):
        yield path, "bbport/" + name
    for name in BRIDGE_GENERATED:
        yield safe_source(root / ".build/save-bridge", name), "save-bridge/" + name


def write_archive(entries, out):
    entries = list(entries)  # Validate every source before replacing an old archive.
    names = [name for _, name in entries]
    check_public(names, source_tree=True)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Duplicate source archive member.")
    manifest = {name: digest(path) for path, name in entries}
    temporary = out.with_suffix(out.suffix + ".tmp")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, name in entries:
                archive.write(path, name)
            archive.writestr("SOURCE-MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
        with zipfile.ZipFile(temporary) as archive:
            if (archive.testzip() is not None or
                    set(archive.namelist()) != set(manifest) | {"SOURCE-MANIFEST.json"}):
                raise ValueError("Source archive members or CRC failed verification.")
            for name, expected in manifest.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError("Source archive hash mismatch: " + name)
        temporary.replace(out)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=Path, default=ROOT / ".build/bbport")
    args = parser.parse_args()
    out = ROOT / "dist/TheHuntLauncher-sources.zip"
    write_archive(source_entries(ROOT, args.port), out)
    with (ROOT / "dist/SHA256SUMS.txt").open("a", encoding="utf-8") as sums:
        sums.write(digest(out) + "  " + out.name + "\n")
    print(f"Source archive: {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
