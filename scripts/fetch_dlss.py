"""Fetch the unmodified DLSS components from the fixed official BBport release."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verified(path, size, expected):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == expected


def prepare(folder):
    manifest = json.loads((ROOT / "scripts/dlss-runtime.lock.json").read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "Bloodborne-Windows-1.9.4.zip"
    if not verified(archive, manifest["archive_size"], manifest["archive_sha256"]):
        temporary = archive.with_suffix(".zip.part")
        request = urllib.request.Request(manifest["archive_url"], headers={"User-Agent": "The-Hunt-DLSS-assets"})
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as stream:
            while block := response.read(1 << 20):
                stream.write(block)
        if not verified(temporary, manifest["archive_size"], manifest["archive_sha256"]):
            raise ValueError("Official BBport archive does not match the pinned SHA256.")
        temporary.replace(archive)
    with zipfile.ZipFile(archive) as source:
        for relative, entry in manifest["files"].items():
            candidates = [name for name in source.namelist() if name == relative or name.endswith("/" + relative)]
            if len(candidates) != 1:
                raise ValueError("Ambiguous or missing official DLSS file: " + relative)
            data = source.read(candidates[0])
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("Official DLSS component mismatch: " + relative)
            output = folder / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
    print("Official release SHA256 verified; DLSS bridge, SR/FG runtimes and both licenses extracted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / ".build/bbport/out/dlss")
    args = parser.parse_args()
    prepare(args.dir)
