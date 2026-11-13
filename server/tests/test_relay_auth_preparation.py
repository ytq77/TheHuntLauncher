"""Offline strict-anchor preparation checks for the native authenticated relay."""
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("SERVER_SECURITY_CACHE", ROOT / "windows/downloads"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load("relay_auth_preparation", ROOT / "recipe/relay_auth.py")
security = load("relay_auth_security", ROOT / "recipe/security.py")
manifest = json.loads((ROOT / "recipe/sources.json").read_text(encoding="utf-8"))


def verified(path, digest):
    if not path.is_file():
        raise unittest.SkipTest("Pinned offline cache missing: " + str(path))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise AssertionError("Pinned SHA256 mismatch")
    return data


class RelayAuthPreparation(unittest.TestCase):
    def test_python_relay_cannot_bind_on_any_address(self):
        name = "server/gameserver/bbsc_gameserver.py"
        original = verified(CACHE / "bbsc" / name, manifest["bbsc"]["files"][name])
        secured = security.patch_gameserver(original)
        generated = relay.patch_gameserver(secured)
        self.assertEqual(relay.patch_gameserver(generated), generated)
        tree = ast.parse(generated)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start_relay")
        module = types.ModuleType("relay_disabled_function")
        module._trusted_address = lambda address: address not in ("8.8.8.8", "0.0.0.0")
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "<disabled-relay>", "exec"), module.__dict__)
        with patch("socket.socket") as socket:
            for address in ("127.0.0.1", "26.1.2.3", "192.168.1.1", "8.8.8.8", "0.0.0.0"):
                with self.assertRaisesRegex(ValueError, "Legacy relay disabled|Relay requires"):
                    module.start_relay(address, 20444)
            socket.assert_not_called()
        with self.assertRaisesRegex(ValueError, "digest changed"):
            relay.patch_gameserver(secured.replace(b"listener.listen(16)", b"listener.listen(17)"))

    def test_native_patch_idempotent_and_prevalidates_all_anchors(self):
        item = manifest["archives"][0]
        data = verified(CACHE / item["name"], item["sha256"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                prefix = archive.getnames()[0].split("/")[0] + "/"
                for member in archive.getmembers():
                    if member.isfile() and member.name.startswith(prefix):
                        path = root / member.name[len(prefix):]
                        self.assertTrue(path.resolve().is_relative_to(root.resolve()))
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(archive.extractfile(member).read())
            security.patch_shadnet(root)
            main = root / "src/main.cpp"
            original = main.read_bytes()
            main.write_bytes(original.replace(b"// Start the HTTP/JSON WebAPI", b"// Changed WebAPI"))
            snapshot = {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                relay.patch_shadnet(root)
            self.assertEqual(snapshot, {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()})
            main.write_bytes(original)
            relay.patch_shadnet(root)
            once = {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
            relay.patch_shadnet(root)
            self.assertEqual(once, {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()})
            status = (root / "src/webapi_server.cpp").read_text(encoding="utf-8")
            self.assertIn('if (m_shared && m_shared->relayAuthReady.load())', status)
            self.assertIn('body.insert("relay_auth", 2)', status)
            self.assertIn('AuthenticatedRelay relay(&server.Shared())', main.read_text(encoding="utf-8"))
            self.assertIn('[socket = m_socket]', (root / 'src/client_session.cpp').read_text('utf-8'))
            self.assertNotIn('"disconnectFromHost", Qt::QueuedConnection',
                             (root / 'src/client_session.cpp').read_text('utf-8'))
            self.assertEqual((root / "clientsample/types.h").read_text(encoding="utf-8").count("CreateRoom = 13"),1)
            self.assertIn("CreateRoom = 101", (root / "tests/relay_client/types.h").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
