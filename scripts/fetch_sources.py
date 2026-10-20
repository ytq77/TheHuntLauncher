"""Fetch immutable source revisions into a build-only directory."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
    for name in ("bbport", "bbsc"):
        dest = ROOT / ".build" / name
        if dest.exists():
            raise SystemExit(f"{dest} already exists; use a fresh build directory.")
        subprocess.run(["git", "init", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "remote", "add", "origin", lock[name]["url"]], check=True)
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth=1", "origin", lock[name]["commit"]], check=True)
        subprocess.run(["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"], check=True)
        actual = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
        if actual != lock[name]["commit"]:
            raise SystemExit("Source revision does not match lock file.")
    subprocess.run(["git", "-C", str(ROOT / ".build/bbport"), "submodule", "update",
                    "--init", "--recursive", "--depth=1"], check=True)


if __name__ == "__main__":
    main()
