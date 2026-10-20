"""Recognize the pinned shadNet sample output without exposing credentials."""
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock

source = Path(__file__).resolve().parents[1] / "docs/server/recipe/create-account.py"
account = types.ModuleType("server_account_recipe")
account.__file__ = str(source)
exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), account.__dict__)


class ServerAccountResponses(unittest.TestCase):
    def test_pinned_sample_registration_and_login_success(self):
        # Exact success messages from pinned clientsample/client.cpp.
        responses = [
            subprocess.CompletedProcess([], 0, "[create] Account created successfully.\n", ""),
            subprocess.CompletedProcess([], 0, "", "[login] OK userId=9\n"),
        ]
        with mock.patch.object(account.subprocess, "run", side_effect=responses) as run:
            account.invoke("100.64.0.1", "31413", "register",
                           "hunter01", "test-password", "hunter01@example.invalid", "test-key")
            account.invoke("100.64.0.1", "31413", "login", "hunter01", "test-password")
        self.assertEqual(run.call_count, 2)

    def test_rejection_and_unknown_output_do_not_expose_credentials(self):
        for output in ("[create] FAILED: ExistingUsername\n",
                       "unexpected test-password test-key\n"):
            with self.subTest(output=output):
                response = subprocess.CompletedProcess([], 0, output, "")
                with mock.patch.object(account.subprocess, "run", return_value=response):
                    with self.assertRaises(ValueError) as caught:
                        account.invoke("100.64.0.1", "31413", "register",
                                       "hunter01", "test-password", "hunter01@example.invalid",
                                       "test-key")
                self.assertNotIn("test-password", str(caught.exception))
                self.assertNotIn("test-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
