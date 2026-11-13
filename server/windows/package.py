"""Assemble native binaries and their runtime dependency closure, not a dev tree."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
PREFIX = Path(os.environ.get("WIN_SERVER_TOOLCHAIN", r"C:\bbsc-dev\msys64\clang64"))
from build_config import OUT, VERSION
PRIVATE_PARTS = {".private", "data", "user", "logs", "captures", "save-backups", "save-conversion-backups",
                 "conversion-jobs", "update-backups", "world-recovery", "save-proof", "home-save-proof",
                 "diagnostics", "guest-items", "own-world", "shared-progress", "__pycache__", ".git", ".build",
                 "shadnet-state", "game-state", "backups"}
PRIVATE_FILES = {"shadnet.cfg", ".server.lock", "online-data.json", "profile.json", "account.clixml",
                 "settings-snapshot.json", "launcher-state.json", "last-exit.json", "save-transfer-result.json",
                 ".save-import.lock", "bbport.ini", "input.ini", "mods.json", "patches.json", ".env",
                 "features.json", "server-admin.json", "conversion-current.json", ".save-conversion.lock",
                 ".save-conversion-pending.json", "update-settings.json", "desktop-settings.json", ".update.lock",
                 ".update-transaction.json", "insight-fix.json"}


def private_path(name):
    parts = [part.casefold() for part in Path(name).parts]
    if set(parts) & PRIVATE_PARTS or any(
            part.startswith((".savedata-import-", ".savedata-conversion-")) for part in parts):
        return True
    if re.search(r"(?:^|/)(?:normal|bbsc)/db(?:/|$)", name, re.IGNORECASE):
        return True
    for part in parts:
        if part in {".env.example", "shadnet.cfg.example"}:
            continue
        for private in PRIVATE_FILES:
            if part == private or part.startswith(private + "."):
                return True
            if ("." in private.lstrip(".") and part.startswith(private.rsplit(".", 1)[0] + ".") and
                    re.search(r"\.(?:tmp|bak|old|new)(?:[.-].*)?$", part)):
                return True
    return False


def validate_staging(folder, verify_manifest=False, allow_reports=False):
    if folder.is_symlink() or folder.is_junction():
        raise ValueError("Unsafe server staging directory.")
    files = {}
    for path in folder.rglob("*"):
        name = path.relative_to(folder).as_posix()
        if (private_path(name) or name.split("/")[0].casefold() == "normal" or
                path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(folder.resolve())):
            raise ValueError("Server staging contains private or unsafe state: " + name)
        if path.is_file():
            files[name] = path
    if verify_manifest and files:
        if "BUILD-INFO.json" not in files:
            raise ValueError("Existing server staging has no manifest; use a fresh output directory.")
        info = json.loads((folder / "BUILD-INFO.json").read_text("utf-8"))
        expected = set(info["files"]) | {"BUILD-INFO.json"}
        reports = {"LOCAL-VALIDATION.json", "LOCAL-MANAGER-VALIDATION.json"} if allow_reports else set()
        if info["label"] != VERSION or not expected <= set(files) or not set(files) <= expected | reports:
            raise ValueError("Existing server staging does not match its manifest; use a fresh output directory.")
        for name, expected in info["files"].items():
            if hashlib.sha256(files[name].read_bytes()).hexdigest() != expected:
                raise ValueError("Existing server staging was modified: " + name)
        if json.loads((folder / "server.json").read_text("utf-8")) != {"address": "127.0.0.1"}:
            raise ValueError("Server staging has initialized listener settings.")


# The native build adds these two public reports between its two packaging runs.
# All other pre-existing members must still match the first package's manifest.
validate_staging(OUT, verify_manifest=True, allow_reports=True)
BIN = OUT / "runtime" / "bin"
BIN.mkdir(parents=True, exist_ok=True)
dependencies = {}


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def closure(source):
    key = source.name.lower()
    if key in dependencies:
        return
    dependencies[key] = str(source)
    output = subprocess.check_output([str(PREFIX / "bin" / "llvm-objdump.exe"), "-p", str(source)])
    for name in re.findall(rb"DLL Name: ([^\r\n]+)", output):
        filename = name.decode("ascii").strip()
        candidate = PREFIX / "bin" / filename
        if candidate.exists():
            copy(candidate, BIN / filename)
            closure(candidate)
        elif not filename.lower().startswith(("api-ms-", "ext-ms-")) and not (
            Path(os.environ["SystemRoot"]) / "System32" / filename).exists():
            raise ValueError(f"Unresolved runtime dependency: {source.name} -> {filename}")


for mode in ("bbsc",):
    folder = OUT / mode
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("shadnet.exe", "worlds.cfg", "scoreboards.cfg"):
        copy(ROOT / "build" / name, folder / name)
    (folder / "qt.conf").write_text("[Paths]\nPlugins=../runtime/plugins\n", encoding="ascii")
closure(ROOT / "build" / "shadnet.exe")
copy(ROOT / "build" / "clientsample" / "shadnet-sample.exe", BIN / "shadnet-sample.exe")
closure(BIN / "shadnet-sample.exe")
copy(ROOT / "build" / "clientsample" / "shadnet-admin.exe", BIN / "shadnet-admin.exe")
closure(BIN / "shadnet-admin.exe")
copy(PREFIX / "share" / "qt6" / "plugins" / "sqldrivers" / "qsqlite.dll",
     OUT / "runtime" / "plugins" / "sqldrivers" / "qsqlite.dll")
closure(OUT / "runtime" / "plugins" / "sqldrivers" / "qsqlite.dll")
copy(PREFIX / "bin" / "python.exe", BIN / "python.exe")
closure(BIN / "python.exe")

# Keep the standard library and native extensions, excluding dev-only/test/UI packages.
python_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
standard = PREFIX / "lib" / python_dir
skip = {"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib-dynload"}
shutil.copytree(standard, OUT / "runtime" / "lib" / python_dir, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*skip, "*.pyc", "*.a", "*.exe"))
for source in (standard / "lib-dynload").glob("*.pyd"):
    if "test" in source.name or source.name.startswith(("_tkinter", "_curses", "readline")):
        continue
    copy(source, OUT / "runtime" / "lib" / python_dir / "lib-dynload" / source.name)
    closure(source)

shutil.copytree(ROOT / "bbsc-game", OUT / "bbsc-game", dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(ROOT / "package-src", OUT, dirs_exist_ok=True)
copy(ROOT / "build-ui" / "Server.exe", OUT / "Server.exe")
copy(ROOT / "build-ui" / "TheHunt.ico", OUT / "TheHunt.ico")
shutil.copytree(ROOT / 'build-ui/assets', OUT / 'assets', dirs_exist_ok=True)
copy(ROOT.parent / "README.md", OUT / "README.md")
for directory in ("docs", "recipe"):
    source = ROOT.parent / directory
    for path in sorted(source.rglob("*")):
        if path.is_file() and not private_path(path.relative_to(source).as_posix()):
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError("Unsafe server documentation: " + str(path))
            copy(path, OUT / directory / path.relative_to(source))
(OUT / "server.json").write_text('{"address": "127.0.0.1"}\n', encoding="ascii")

licenses = OUT / "licenses"
for source, target in ((ROOT / "src" / "LICENSES", "shadnet"),
                       (ROOT / "src" / "externals" / "abseil-cpp" / "LICENSE", "abseil-LICENSE"),
                       (ROOT / "src" / "externals" / "protobuf" / "LICENSE", "protobuf-LICENSE")):
    if source.is_dir():
        shutil.copytree(source, licenses / target, dirs_exist_ok=True)
    else:
        copy(source, licenses / target)

packages = subprocess.check_output([str(PREFIX.parent / "usr" / "bin" / "pacman.exe"), "-Q"], text=True)
for package in (PREFIX / "share" / "licenses").iterdir():
    # License texts are tiny; preserve all installed runtime notices rather than risk omissions.
    if package.is_dir():
        shutil.copytree(package, licenses / "runtime" / package.name, dirs_exist_ok=True)
(licenses / "runtime-packages.txt").write_text(packages, encoding="utf-8")
copy(ROOT / "source-manifest.json", OUT / "source-manifest.json")
copy(ROOT.parent / "LICENSE", OUT / "LICENSE")
copy(ROOT.parent / "NOTICE.md", OUT / "NOTICE.md")
validate_staging(OUT)
manifest = {str(p.relative_to(OUT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(OUT.rglob("*")) if p.is_file() and p.name != "BUILD-INFO.json"}
info = {"label": VERSION, "platform": "Windows x64", "compiler": subprocess.check_output(
    [str(PREFIX / "bin" / "clang++.exe"), "--version"], text=True).splitlines()[0],
        "qt": subprocess.check_output([str(PREFIX / "bin" / "qmake6.exe"), "-query", "QT_VERSION"], text=True).strip(), "python": sys.version, "files": manifest,
        "features": ["seamless-only", "trusted-network-listeners", "strict-owner-fields",
                     "revoked-reset-and-banned-tokens", "login-bound-stun-source", "stdin-account-helper",
                     "durable-update-intent", "authenticated-relay-v2", "native-room-role-routing",
                     "bearer-bound-summon-ownership", "targeted-revocable-prejoin-placement"],
        "security_validation": "Offline Python and native Broker/security/stdin-helper regressions passed. "
                               "Relay v2 verifies native account tokens, MACs and sequences; room and owner roles "
                               "come from native Matching. Private summon records bind to authenticated Bearers. "
                               "NP/HTTP remain unencrypted and STUN retains source-IP limitations; use trusted "
                               "LAN/VPN. Protocol/container checks do not prove two-player gameplay acceptance.",
        "patches": ["STUN 4/6 byte framing", "enable shadnet-sample target", "BBSC explicit summon WebAPI",
                    "same-PC login endpoints use the bound listener address",
                    "seamless game service uses 2-second summon search/create intervals"]}
(OUT / "BUILD-INFO.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Runtime staged: {len(manifest)} files, {sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()) / 1048576:.1f} MiB")


def archive(folder, output):
    validate_staging(folder, verify_manifest=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                bundle.write(file, str(file.relative_to(folder.parent)))
    with zipfile.ZipFile(output) as bundle:
        info = json.loads((folder / "BUILD-INFO.json").read_text("utf-8"))
        expected = {folder.name + "/" + name for name in info["files"]} | {folder.name + "/BUILD-INFO.json"}
        if bundle.testzip() is not None or set(bundle.namelist()) != expected:
            raise ValueError("Server archive manifest or CRC verification failed.")
        for name, digest in info["files"].items():
            if hashlib.sha256(bundle.read(folder.name + "/" + name)).hexdigest() != digest:
                raise ValueError("Server archive hash mismatch: " + name)
    print(f"ZIP: {output.name} SHA256={hashlib.sha256(output.read_bytes()).hexdigest()}")


if "--zip" in sys.argv:
    archive(OUT, OUT.parent / (OUT.name + ".zip"))
