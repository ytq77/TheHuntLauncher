"""Verify and stage pinned shadNet in a NEW disposable directory; never download."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependencies", action="store_true", help="also verify/extract pinned Protobuf and Abseil")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "recipe/sources.json").read_text())
    items = manifest["archives"] if args.dependencies else [manifest["archives"][0]]
    verified = []
    for item in items:
        data = (args.cache / item["name"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Pinned archive SHA256 mismatch: " + item["name"])
        verified.append((item, data))
    args.output.mkdir(parents=True, exist_ok=False)
    for item, data in verified:
        destination = args.output if item["name"] == "shadnet.tar.gz" else (
            args.output / "externals" / ("protobuf" if item["name"] == "protobuf.tar.gz" else "abseil-cpp"))
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            prefix = archive.getnames()[0].split("/")[0] + "/"
            for member in archive.getmembers():
                if member.isfile() and member.name.startswith(prefix):
                    target = destination / member.name[len(prefix):]
                    if not target.resolve().is_relative_to(args.output.resolve()):
                        raise ValueError("Unexpected archive path")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
    spec = importlib.util.spec_from_file_location("stage_security", ROOT / "recipe/security.py")
    security = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(security)
    security.patch_shadnet(args.output)
    print("Pinned shadNet verified and security patch applied: " + str(args.output))


if __name__ == "__main__":
    main()
