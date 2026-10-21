import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
import server_admin
import server_remote


class ServerAdminTests(unittest.TestCase):
    def setUp(self):
        self.connection = {"target": "root@example.test", "port": 2222,
                           "directory": "/opt/test-bbsc", "identity": "", "sudo": False}

    def test_ssh_is_strict_noninteractive_and_credentials_use_stdin(self):
        secret = "private-password-test"
        with patch.object(server_admin.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout='{"ok":true}', stderr="")) as run:
            response = server_admin.invoke(self.connection, "verify", {"name": "test01", "password": secret})
        self.assertTrue(response["ok"])
        args, kwargs = run.call_args
        command = args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertNotIn(secret, " ".join(command))
        self.assertEqual(json.loads(kwargs["input"])["password"], secret)
        self.assertEqual(kwargs["timeout"], 100)

    def test_shell_injection_ports_and_unknown_actions_are_rejected(self):
        for field, value in (("target", "-oProxyCommand=calc"), ("target", "root@host;id"),
                             ("target", "host $(id)"), ("directory", "/opt/../etc"),
                             ("directory", "/opt/x;id"), ("port", 65536), ("port", True),
                             ("sudo", "yes")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    server_admin.ssh_command({**self.connection, field: value})
        with self.assertRaises(ValueError):
            server_admin.invoke(self.connection, "delete")

    def test_identity_and_trust_paths_are_explicit_and_never_disable_checking(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            trust = Path(directory) / "known_hosts"
            key.write_text("not-a-real-key")
            trust.write_text("test-record")
            command = server_admin.ssh_command({**self.connection, "identity": str(key), "known_hosts": str(trust)})
            self.assertIn(str(key), command)
            self.assertIn('UserKnownHostsFile="' + trust.as_posix() + '"', command)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertNotIn("not-a-real-key", " ".join(command))

    def test_remote_output_cannot_leak_raw_stderr_or_invalid_response(self):
        for result in (SimpleNamespace(returncode=1, stdout="private", stderr="private"),
                       SimpleNamespace(returncode=0, stdout="not-json-private", stderr="")):
            with patch.object(server_admin.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError) as caught:
                    server_admin.invoke(self.connection, "status")
            self.assertNotIn("private", str(caught.exception))

    def test_registration_requires_confirmation_before_reading_config(self):
        with patch.object(server_remote, "root_path", return_value=Path("/missing")):
            with self.assertRaises(ValueError):
                server_remote.handle({"action": "create", "name": "ab", "password": "secret"})

    def test_remote_status_only_lists_public_account_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_database(root)
            before = (root / "shadnet-state/db/shadnet.db").read_bytes()
            with patch.object(server_remote, "command", return_value='{"Service":"shadnet","State":"running","Health":"healthy"}'), patch.object(server_remote.os, "uname", create=True, return_value=SimpleNamespace(nodename="test-server")):
                result = server_remote.status(root)
            self.assertEqual(result["accounts"], [{"id": 1, "name": "test01", "admin": False, "banned": False}])
            self.assertNotIn("private", json.dumps(result))
            self.assertEqual(before, (root / "shadnet-state/db/shadnet.db").read_bytes())
            self.assertFalse((root / "backups").exists())

    @staticmethod
    def make_database(root):
        folder = root / "shadnet-state/db"
        folder.mkdir(parents=True)
        with closing(sqlite3.connect(folder / "shadnet.db")) as connection:
            connection.execute("CREATE TABLE account(user_id INTEGER,username TEXT,admin BOOL,banned BOOL,hash BLOB,salt BLOB,token TEXT)")
            connection.execute("INSERT INTO account VALUES(1,'test01',0,0,'private-hash','private-salt','private-token')")
            connection.commit()
        (root / "shadnet-state/shadnet.cfg").write_text("[General]\nHost=example.test\nUnsecuredPort=31413\nRegistrationSecretKey=private-registration\n")

    def test_creation_backs_up_before_service_registration_and_never_writes_database_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_database(root)
            original = (root / "shadnet-state/db/shadnet.db").read_bytes()
            called = []
            def sample(folder, config, action, *values):
                self.assertEqual(len(list((root / "backups").glob("*.sqlite"))), 1)
                called.append(action)
            request = {"action": "create", "name": "test02", "password": "secret",
                       "email": "test02@example.test", "confirmed": True}
            with patch.object(server_remote, "root_path", return_value=root), patch.object(server_remote, "sample", side_effect=sample):
                result = server_remote.handle(request)
            self.assertEqual(called, ["register", "login"])
            self.assertTrue(result["backup"].startswith("before-account-"))
            self.assertEqual(original, (root / "shadnet-state/db/shadnet.db").read_bytes())
            with closing(sqlite3.connect(root / "backups" / result["backup"])) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_missing_confirmation_or_existing_account_has_no_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_database(root)
            with patch.object(server_remote, "root_path", return_value=root), patch.object(server_remote, "sample") as sample:
                for name, confirmed in (("test02", False), ("test01", True)):
                    with self.assertRaises(ValueError):
                        server_remote.handle({"action": "create", "name": name, "password": "secret", "confirmed": confirmed})
                sample.assert_not_called()
            self.assertFalse((root / "backups").exists())

    def test_created_but_login_failed_reports_partial_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_database(root)
            with patch.object(server_remote, "root_path", return_value=root), patch.object(server_remote, "sample", side_effect=[None, ValueError("private-password")]):
                with self.assertRaisesRegex(ValueError, "已创建") as caught:
                    server_remote.handle({"action": "create", "name": "test02", "password": "secret", "email": "test02@example.test", "confirmed": True})
            self.assertNotIn("private", str(caught.exception))

    def test_bad_request_json_is_redacted(self):
        output = io.StringIO()
        with patch.object(server_remote.sys, "stdin", io.StringIO('{"password":"private')), patch.object(server_remote.sys, "stdout", output):
            server_remote.main()
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
