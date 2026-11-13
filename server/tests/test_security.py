"""Offline regressions against generated, pinned server code and synthetic state.

SERVER_SECURITY_CACHE points at a verified downloads directory when the normal
windows/downloads cache is not present. No test fetches or starts a service.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("SERVER_SECURITY_CACHE", ROOT / "windows/downloads"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recipe = load("security_recipe", ROOT / "recipe/prepare.py")
manifest = json.loads((ROOT / "recipe/sources.json").read_text(encoding="utf-8"))


def verified(path, digest):
    if not path.is_file():
        raise unittest.SkipTest("Pinned offline cache missing: " + str(path))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise AssertionError("Pinned cache SHA256 mismatch: " + str(path))
    return data


class GameSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        name = "server/gameserver/bbsc_gameserver.py"
        original = verified(CACHE / "bbsc" / name, manifest["bbsc"]["files"][name])
        cls.generated = recipe.patch_local_login(recipe.patch_gameserver(original))
        cls.game = types.ModuleType("offline_generated_gameserver")
        cls.game.__file__ = str(CACHE / "bbsc" / name)
        with patch.dict(sys.modules, {"bbhost_service": types.ModuleType("bbhost_service")}):
            exec(compile(cls.generated, cls.game.__file__, "exec"), cls.game.__dict__)

    def test_security_generation_is_idempotent(self):
        self.assertEqual(recipe.security.patch_gameserver(self.generated), self.generated)

    def test_unreadable_or_invalid_online_store_cannot_be_uploaded_over(self):
        values = [b'{"messages":', b'[]', b'null', b'{}', b'{"messages":{}}', b'{"messages":[]}', b'{"tombs":{"1":1}}',
                  b'{"next_id":"bad"}', b'{"next_id":true}', b'{"deleted":{}}', b'\xff']
        valid = {"next_id":1,"messages":{},"tombs":{},"deleted":[],"shells":{}}
        values += [json.dumps({**valid, key:value}).encode() for key, value in (
            ("messages", []), ("tombs", {"1":1}), ("next_id", True), ("next_id", -1),
            ("next_id", "bad"), ("deleted", {}), ("deleted", [True]))]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "online-data.json"
            for value in values:
                with self.subTest(value=value):
                    path.write_bytes(value)
                    with self.assertRaises(RuntimeError):
                        self.game.OnlineStore(str(path))
                    self.assertEqual(path.read_bytes(), value)
                    self.assertFalse(path.with_suffix(".json.tmp").exists())
            path.write_bytes(b'{"messages":{}}')
            with patch("builtins.open", side_effect=PermissionError("synthetic denied")):
                with self.assertRaises(RuntimeError):
                    self.game.OnlineStore(str(path))
            self.assertEqual(path.read_bytes(), b'{"messages":{}}')

    def test_missing_and_legacy_store_remain_writable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "online-data.json"
            store = self.game.OnlineStore(str(path))
            store.save()
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text())
            data.pop("channels")  # Legacy format predates chalice persistence.
            path.write_text(json.dumps(data), encoding="utf-8")
            store = self.game.OnlineStore(str(path))
            self.assertEqual(store.data["channels"], {})
            self.game.STORE = store
            with contextlib.redirect_stdout(io.StringIO()):
                self.game.blood_create(json.dumps({"UserId":42, "BloodMessList":[{"BloodData":"synthetic"}]}))
            reopened = self.game.OnlineStore(str(path))
            self.assertEqual(len(reopened.data["messages"]), 1)

    def test_missing_malformed_or_other_owner_cannot_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "online-data.json"
            for kind, collection, id_field, entry_id in (("blood", "messages", "BloodMessId", 123),
                                                       ("tomb", "tombs", "TombMessId", 456)):
                for identity in (None, 0, -1, True, "42", 42.5, 43):
                    with self.subTest(kind=kind, identity=identity):
                        store = self.game.OnlineStore(str(path))
                        store.data[collection][str(entry_id)] = {id_field:entry_id, "UserId":42}
                        store.save()
                        self.game.STORE = store
                        body = {id_field:entry_id}
                        if identity is not None:
                            body["UserId"] = identity
                        with contextlib.redirect_stdout(io.StringIO()):
                            getattr(self.game, kind + "_delete")(json.dumps(body))
                        self.assertIn(str(entry_id), self.game.OnlineStore(str(path)).data[collection])
                with contextlib.redirect_stdout(io.StringIO()):
                    getattr(self.game, kind + "_delete")(json.dumps({id_field:entry_id,"UserId":42}))
                self.assertNotIn(str(entry_id), self.game.OnlineStore(str(path)).data[collection])

    def test_public_relay_peer_never_joins_or_broadcasts(self):
        peer = Mock()
        with patch.object(self.game, "_relay_announce") as announce:
            self.game._relay_client(peer, ("8.8.8.8", 4321))
            announce.assert_not_called()
        peer.close.assert_called_once()
        peer.recv.assert_not_called()
        peer.sendall.assert_not_called()
        self.assertFalse(self.game.RELAY_CLIENTS)

    def test_legacy_relay_frame_from_trusted_peer_still_reaches_other_peer(self):
        peer, other = Mock(), Mock()
        payload = b"synthetic legacy relay frame"
        peer.recv.side_effect = [struct.pack("<I", len(payload)), payload, b""]
        with patch.object(self.game, "RELAY_CLIENTS", {other: "synthetic-other"}), \
             patch.object(self.game, "_relay_announce"), contextlib.redirect_stdout(io.StringIO()):
            self.game._relay_client(peer, ("100.64.0.2", 4321))
            self.assertEqual(self.game.RELAY_CLIENTS, {other: "synthetic-other"})
        other.sendall.assert_called_once_with(struct.pack("<I", len(payload)) + payload)
        peer.close.assert_called_once()

    def test_public_http_peer_refused_before_reading_or_dispatch(self):
        handler = Mock()
        handler.client_address = ("8.8.8.8", 1234)
        self.game.Handler._handle_inner(handler)
        self.assertTrue(handler.close_connection)
        handler._send.assert_called_once_with(b'{"ResKind":1}', "application/json", 403)
        handler.rfile.read.assert_not_called()

    def test_public_or_wildcard_bind_refused_before_socket_creation(self):
        for address in ("0.0.0.0", "8.8.8.8", "::", "localhost"):
            with patch.object(self.game.socket, "socket") as socket:
                with self.assertRaises(ValueError):
                    self.game.start_relay(address, 20444)
                socket.assert_not_called()

    def test_game_startup_refuses_unsafe_bind_before_logs_or_store(self):
        with patch.object(sys, "argv", ["bbsc_gameserver.py", "--host", "0.0.0.0", "--no-bbhost"]), \
             patch.object(self.game, "open_log") as log, \
             patch.object(self.game, "OnlineStore") as store, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.game.main()
            log.assert_not_called()
            store.assert_not_called()


class NativePatchTests(unittest.TestCase):
    def test_pinned_patch_is_repeatable_and_rejects_drift_before_writing(self):
        archive = next(x for x in manifest["archives"] if x["name"] == "shadnet.tar.gz")
        data = verified(CACHE / archive["name"], archive["sha256"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with tarfile.open(fileobj=io.BytesIO(data)) as bundle:
                prefix = bundle.getnames()[0].split("/")[0] + "/"
                for member in bundle.getmembers():
                    if member.isfile() and member.name.startswith(prefix):
                        target = root / member.name[len(prefix):]
                        self.assertTrue(target.resolve().is_relative_to(root.resolve()))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(bundle.extractfile(member).read())
            recipe.security.patch_shadnet(root)
            before = {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
            recipe.security.patch_shadnet(root)
            self.assertEqual(before, {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()})
            auth = root / "src/webapi_auth.cpp"
            auth.write_text(auth.read_text().replace("WHERE token = ? AND banned = 0", "WHERE unknown = ?"))
            before = {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                recipe.security.patch_shadnet(root)
            self.assertEqual(before, {p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()})


class AccountResetTests(unittest.TestCase):
    def test_reset_rotates_token_and_preserves_old_backup(self):
        package = ROOT / "windows/package-src"
        sys.path.insert(0, str(package))
        try:
            manager = load("offline_account_manager", package / "manager.py")
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "bbsc/db/shadnet.db"
            path.parent.mkdir(parents=True)
            with contextlib.closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE account(user_id INTEGER PRIMARY KEY,username TEXT,hash BLOB,salt BLOB,token TEXT,reset_token TEXT)")
                connection.execute("INSERT INTO account VALUES(42,'player',?,?,?,?)", (b'old-hash',b'old-salt','old-token','pending-reset'))
                connection.commit()
            controller = manager.Controller()
            with patch.object(manager, "ROOT", root), \
                 patch.object(controller, "offline", side_effect=lambda mode:contextlib.nullcontext()):
                response = controller.change("bbsc", "reset", 42, "player", "synthetic-new-password")
                with contextlib.closing(sqlite3.connect(path)) as connection:
                    hashed, salt, token, pending = connection.execute("SELECT hash,salt,token,reset_token FROM account").fetchone()
                self.assertEqual(hashed, manager.password_hash("synthetic-new-password", salt))
                self.assertNotEqual(token, 'old-token')
                self.assertEqual(len(token), 64)
                self.assertIsNone(pending)
                with contextlib.closing(sqlite3.connect(response['backup'])) as backup:
                    self.assertEqual(backup.execute("SELECT token FROM account").fetchone()[0], 'old-token')
                previous = path.read_bytes()
                with self.assertRaises(ValueError):
                    controller.change("bbsc", "reset", 42, "different-player", "ignored")
                self.assertEqual(path.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
