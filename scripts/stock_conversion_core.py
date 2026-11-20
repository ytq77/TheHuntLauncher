"""Stage the verified original BBport runtime used by legacy save diagnostics."""
import hashlib
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

NAME = "TheHuntLauncher-0.1.0-preview.25-windows-x64"
ARCHIVE_SHA256 = "0bf311214aa428e8ede5962285481efa36e08640d90e5ea074dff23a75652957"
CORE_SHA256 = "641d3d53af2817f4adce4aef58ac8dac20186a72647436a2770ce49d74a7adcc"
URL = "https://github.com/ytq77/TheHuntLauncher/releases/download/v0.1.0-preview.25/" + NAME + ".zip"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage_stock_core(destination, archive):
    destination, archive = Path(destination), Path(archive)
    if not archive.is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
            download = Path(temporary) / "stock.zip"
            with urlopen(URL, timeout=90) as response, download.open("wb") as output:
                shutil.copyfileobj(response, output)
            if digest(download) != ARCHIVE_SHA256:
                raise ValueError("Unexpected preview.25 archive")
            download.replace(archive)
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError("Unexpected preview.25 archive")
    with ZipFile(archive) as bundle:
        core = bundle.read(NAME + "/bin/bb-probe.exe")
    if hashlib.sha256(core).hexdigest() != CORE_SHA256:
        raise ValueError("Unexpected stock conversion runtime")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(core)
