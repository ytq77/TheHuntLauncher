import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from port_compat import patched_run_script
from graphics_assets import verified_copy
import fetch_dlss


class Port194Compatibility(unittest.TestCase):
    def test_capability_lookup_uses_real_core_with_and_without_mod_loader(self):
        source = "caps = probe.parent / 'bb-gpu-capabilities.exe'\nother = probe.parent / 'bb-gpu-capabilities.exe'"
        code = compile(patched_run_script(source), "run.py", "exec")
        for env, expected in (({}, Path("package/mod/bb-gpu-capabilities.exe")),
                              ({"BBSC_EMULATOR": "package/bin/bb-probe.exe"}, Path("package/bin/bb-gpu-capabilities.exe"))):
            scope = {"Path": Path, "env": env, "probe": Path("package/mod/bbsc-launch.exe")}
            exec(code, scope)
            self.assertEqual(scope["caps"], expected)
            self.assertEqual(scope["other"], expected)
        with self.assertRaises(ValueError):
            patched_run_script(source.replace("probe.parent", "changed.parent", 1))

    def test_graphics_runtime_rejects_modified_binary_before_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / "provider.dll", Path(temporary) / "staged/provider.dll"
            source.write_bytes(b"verified-runtime")
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            verified_copy(source, target, source.stat().st_size, expected)
            source.write_bytes(b"modified-runtime")
            with self.assertRaises(ValueError):
                verified_copy(source, target, source.stat().st_size, expected)
            self.assertEqual(target.read_bytes(), b"verified-runtime")

    def test_official_dlss_cache_keeps_licenses_and_rejects_component_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            cache = root / "cache"
            cache.mkdir()
            archive = cache / "Bloodborne-Windows-1.9.4.zip"
            files = {"bin/bbport_dlss.dll": b"bridge", "licenses/NVIDIA-DLSS-LICENSE.txt": b"license"}
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, data in files.items():
                    bundle.writestr("bbport-windows/" + name, data)
            manifest = {"archive_url": "https://example.invalid/should-not-download",
                        "archive_size": archive.stat().st_size,
                        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                  for name, data in files.items()}}
            lock = root / "scripts/dlss-runtime.lock.json"
            lock.write_text(json.dumps(manifest))
            with patch.object(fetch_dlss, "ROOT", root), patch.object(fetch_dlss.urllib.request, "urlopen", side_effect=AssertionError("No network expected")):
                fetch_dlss.prepare(cache)
                self.assertEqual((cache / "licenses/NVIDIA-DLSS-LICENSE.txt").read_bytes(), b"license")
                manifest["files"]["bin/bbport_dlss.dll"]["sha256"] = "0" * 64
                lock.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "component mismatch"):
                    fetch_dlss.prepare(cache)
