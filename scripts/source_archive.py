"""Attach the exact patched application sources beside every binary release."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
from package import ROOT, digest, public_docs, PRIVATE_FILES, PRIVATE_PARTS


def launcher_sources(folder):
    return sorted(path for path in folder.rglob("*") if path.is_file()
                  and path.name.lower() not in PRIVATE_FILES
                  and not ({part.lower() for part in path.relative_to(folder).parts}
                           & (PRIVATE_PARTS | {"installer-tests"})))


def tracked(path):
    return subprocess.check_output(
        ["git", "-c", "core.longpaths=true", "-c", "safe.directory=" + path.as_posix(), "-C", str(path),
         "ls-files", "--recurse-submodules", "-z"]).decode("utf-8").split("\0")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=Path, default=ROOT / ".build/bbport")
    args = parser.parse_args()
    out = ROOT / "dist/TheHuntLauncher-sources.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for folder in ("launcher", "scripts", "patches", "locales", "tests", "docs", "assets", ".github"):
            paths = (public_docs(ROOT / folder) if folder == "docs" else
                     launcher_sources(ROOT / folder))
            for path in paths:
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, "bbsc-launcher/" + path.relative_to(ROOT).as_posix())
        for name in ("README.md", "LICENSE", "NOTICE.md", "CONTRIBUTING.md", "upstream.lock.json", ".gitignore", ".gitattributes"):
            archive.write(ROOT / name, "bbsc-launcher/" + name)
        # Include only registered server sources, never its build or test state.
        server = ROOT / "server"
        for name in tracked(server):
            if name and (server / name).is_file():
                archive.write(server / name, "bbsc-launcher/server/" + name)
        manifest = json.loads((server / "recipe/sources.json").read_text("utf-8"))
        fixed = [(item["name"], item["sha256"]) for item in manifest["archives"]]
        fixed += [("bbsc/" + name, value) for name, value in manifest["bbsc"]["files"].items()]
        for name, expected in fixed:
            path = server / "windows/downloads" / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Server source hash mismatch: " + name)
            archive.write(path, "server-upstream/" + name)
        # BBSC has no used submodules for this DLL; include the exact patched
        # sources (including generated Chinese source and GPL notices).
        bbsc = ROOT / ".build/bbsc"
        for path in bbsc.rglob("*"):
            if path.is_file() and not ({".git", "__pycache__"} & set(path.relative_to(bbsc).parts)):
                archive.write(path, "bbsc/" + path.relative_to(bbsc).as_posix())
        for name in tracked(args.port.resolve()):
            path = args.port / name
            if name and path.is_file():
                archive.write(path, "bbport/" + name)
        for path in (ROOT / '.build/save-bridge').glob('*.cpp'):
            archive.write(path, 'save-bridge/' + path.name)
    with zipfile.ZipFile(out) as archive:
        if archive.testzip() is not None:
            raise ValueError("Source archive CRC failed.")
    with (ROOT / "dist/SHA256SUMS.txt").open("a", encoding="utf-8") as sums:
        sums.write(digest(out) + "  " + out.name + "\n")
    print(f"Source archive: {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
