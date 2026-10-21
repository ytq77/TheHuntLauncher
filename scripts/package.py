"""Build a clean release from source/build outputs; never package an installed profile."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
from isolate_patches import patched_script

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {".private", "data", "user", "logs", "captures", "save-backups", "save-conversion-backups", "conversion-jobs", "__pycache__", ".git",
                 ".build", "shadnet-state", "game-state", "backups"}
PRIVATE_FILES = {"profile.json", "account.clixml", "settings-snapshot.json", "launcher-state.json",
                 "last-exit.json", "save-transfer-result.json", ".save-import.lock",
                 "bbport.ini", "input.ini", "mods.json", "patches.json", ".env", "features.json", "server-admin.json",
                 "conversion-current.json", ".save-conversion.lock"}
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
        if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
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
    args = parser.parse_args()
    lock = json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
    out = ROOT / "dist"
    dest = out / ("BBSC-Launcher-" + lock["version"] + "-windows-x64")
    if dest.exists():
        raise SystemExit("Staging folder exists. Refusing to overwrite a possible initialized install.")
    for folder in ("bin", "mod", "engine", "runtime/bin", "licenses", "source-info"):
        (dest / folder).mkdir(parents=True, exist_ok=True)
    binary_dir = args.toolchain / "bin"
    for source, target in (
        (args.port / "out/bb-probe.exe", "bin/bb-probe.exe"),
        (args.port / "out/bb-gpu-capabilities.exe", "bin/bb-gpu-capabilities.exe"),
        (ROOT / ".build/mod/bbsc.dll", "mod/bbsc.dll"),
        (ROOT / ".build/mod/bbsc-launch.exe", "mod/bbsc-launch.exe"),
        (ROOT / ".build/save-bridge/save-bridge.dll", "mod/save-bridge.dll"),
    ):
        subprocess.run([str(binary_dir / "llvm-strip.exe"), "--strip-debug", "-o", str(dest / target), str(source)], check=True)
    shutil.copy2(ROOT / ".build/BBSC-Launcher.exe", dest)
    shutil.copy2(ROOT / ".build/TheHunt.ico", dest)
    for path in (ROOT / "launcher").iterdir():
        if path.suffix in (".ps1", ".xaml", ".py"):
            shutil.copy2(path, dest / path.name)
    shutil.copy2(ROOT / "launcher/feature-catalog.json", dest / "feature-catalog.json")
    # Windows PowerShell 5.1 requires a BOM for Chinese source text.
    for path in dest.glob("*.ps1"):
        text = path.read_text(encoding="utf-8-sig")
        path.write_text(text, encoding="utf-8-sig")
    (dest / "START-BBSC.cmd").write_bytes(
        b'@echo off\r\nstart "" "%~dp0BBSC-Launcher.exe"\r\n')
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
    shutil.copytree(args.port / "scripts", dest / "engine/scripts", ignore=shutil.ignore_patterns("__pycache__"))
    script = dest / "engine/scripts/patches.py"
    script.write_text(patched_script(script.read_text(encoding="utf-8")), encoding="utf-8")
    shutil.copytree(args.port / "patches", dest / "engine/patches")
    (dest / "engine/tools").mkdir()
    shutil.copy2(args.port / "tools/legacy-cpu-plan.json", dest / "engine/tools")
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
    queue = [*dest.glob("bin/*.exe"), *dest.glob("mod/*.dll"), *dest.glob("mod/*.exe"),
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
            "gameplay_validation": "launcher and kit categories/icons/Chinese verified on one RTX 5070 PC; early kit ready in 12540 ms; full co-op regression pending",
            "core_sha256": digest(dest / "bin/bb-probe.exe"), "bbsc_sha256": digest(dest / "mod/bbsc.dll"),
            "save_bridge_sha256": digest(dest / "mod/save-bridge.dll"),
            "offline_save_sha256": digest(dest / "offline_save.py"),
            "offline_aux_sha256": digest(dest / "offline_aux.py"),
            "original_item_policy": "Restore baseline-confirmed original items individually (any nonempty subset); never grant an absent item or equip it.",
            "save_conversion_validation": "98 Python tests and WPF checks passed. All 15 nonempty original item subsets covered. One RTX 5070 role passed vanilla reload/save and post-save comparison. Supplied guest sample retains 13 pickup bits and unchanged world flags while restoring only three owned originals; user reported export and ordinary reload success after retry. Guest post-reload bytes were not collected. Pending world/shared-progress journals, empty baseline ownership and storage cases remain unsupported."}
    (dest / "BUILD-INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    archive = out / (dest.name + ".zip")
    files = archive_tree(dest, archive)
    (out / "SHA256SUMS.txt").write_text(digest(archive) + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "files": len(files),
                      "sha256": digest(archive)}, indent=2))


if __name__ == "__main__":
    main()
