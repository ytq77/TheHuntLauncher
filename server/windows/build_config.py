"""One version and output name shared by build, package and runtime tests."""
from pathlib import Path
import re
import json

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT.parent / "VERSION").read_text(encoding="ascii").strip()
if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", VERSION):
    raise ValueError("Invalid VERSION")
monorepo = ROOT.parents[1] / "upstream.lock.json"
if monorepo.is_file() and json.loads(monorepo.read_text("utf-8"))["version"] != VERSION:
    raise ValueError("Client and server versions disagree.")
OUT = ROOT / "dist" / f"TheHuntServer-Windows-v{VERSION}"
