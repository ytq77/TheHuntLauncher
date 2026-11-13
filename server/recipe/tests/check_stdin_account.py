"""Run actual stdin helper and pinned client with fake transport; no network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HELPER = str(Path(sys.argv.pop(1)).resolve())


class StdinAccountTests(unittest.TestCase):
    def invoke(self, action, result="success", ascii_json=True, raw=None):
        values = dict(name="synthetic_hunter", password='synthetic-password-密碼-🎮-"-\\',
                      email="synthetic@example.invalid", key='synthetic-registration-key-密鑰')
        request = values if action == "register" else {k: values[k] for k in ("name", "password")}
        environment = dict(os.environ, ADMIN_TEST_ACTION=action, ADMIN_TEST_RESULT=result)
        for key, value in values.items():
            environment["ADMIN_TEST_" + key.upper()] = value.encode("utf-8").hex()
        # Credentials enter only the inherited pipe (synthetic expected values
        # in the fixture environment are not part of the production helper).
        command = [HELPER, "100.64.0.10", "31413", action]
        process = subprocess.run(command, input=(raw if raw is not None else
            json.dumps(request, ensure_ascii=ascii_json) + "\n").encode("utf-8"),
            capture_output=True, env=environment, timeout=15)
        self.assertEqual(process.args, command)
        for secret in (values["password"], values["key"]):
            self.assertNotIn(secret, " ".join(process.args))
            self.assertNotIn(secret.encode(), process.stdout + process.stderr)
        self.assertEqual(process.stderr, b"")
        return process

    def test_register_ascii_escaped_json_matches_pinned_registration_proto(self):
        process = self.invoke("register")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout.splitlines()[-1]), {"ok": True, "error": "NoError"})

    def test_login_utf8_json_matches_pinned_login_proto(self):
        process = self.invoke("login", ascii_json=False)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(json.loads(process.stdout.splitlines()[-1])["ok"])

    def test_rejected_register_and_login_produce_safe_status(self):
        for action, error in (("register", "Unauthorized"), ("login", "LoginInvalidPassword")):
            with self.subTest(action=action):
                process = self.invoke(action, "rejected")
                self.assertEqual(process.returncode, 1)
                self.assertEqual(json.loads(process.stdout.splitlines()[-1]), {"ok": False, "error": error})

    def test_connection_failure_has_no_secret_diagnostics(self):
        process = self.invoke("login", "connect-failed")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout), {"ok": False, "error": "ConnectionFailed"})

    def test_malformed_nonobject_and_oversized_input_do_not_reach_transport(self):
        for raw in ('{"password":"synthetic-secret"', '[]', 'x' * 16385):
            with self.subTest(length=len(raw)):
                process = self.invoke("login", raw=raw)
                self.assertEqual(process.returncode, 2)
                self.assertEqual(process.stdout, b"")


if __name__ == "__main__":
    unittest.main()
