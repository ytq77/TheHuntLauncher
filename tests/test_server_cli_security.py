"""Synthetic account commands: no service, installed runtime, or real credentials."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'server/windows/package-src/server.py'
spec = importlib.util.spec_from_file_location('synthetic_windows_server', SOURCE)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class CliSecurity(unittest.TestCase):
    def account(self, run):
        with patch.object(server, 'profile', return_value=('127.0.0.1', {
                'UnsecuredPort': '31413', 'RegistrationSecretKey': 'private-key'})), \
             patch('builtins.input', side_effect=['test01', 'test@example.test']), \
             patch.object(server.getpass, 'getpass', return_value='雪 Δ 😀 private-password'), \
             patch.object(server.subprocess, 'run', side_effect=run), \
             patch.object(server.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            server.account('bbsc')

    def test_register_and_login_credentials_only_use_ascii_stdin(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            self.assertNotIn('private', ' '.join(args))
            self.assertTrue(kwargs['input'].isascii())
            self.assertEqual(json.loads(kwargs['input'])['password'], '雪 Δ 😀 private-password')
            return SimpleNamespace(returncode=0, stdout=b'{"ok":true,"error":"NoError"}\n')
        self.account(run)
        self.assertEqual([args[-1] for args, _ in calls], ['register', 'login'])
        self.assertEqual(json.loads(calls[0][1]['input'])['key'], 'private-key')
        self.assertNotIn('key', json.loads(calls[1][1]['input']))

    def test_timeout_and_untrusted_output_are_redacted(self):
        for failure in (subprocess.TimeoutExpired(['private-password'], 25),
                        subprocess.CalledProcessError(1, ['private-key'])):
            def run(*args, **kwargs):
                raise failure
            with self.assertRaises(RuntimeError) as caught:
                self.account(run)
            self.assertNotIn('private', str(caught.exception))
        with self.assertRaises(RuntimeError) as caught:
            self.account(lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b'{"ok":false,"error":"private-password"}'))
        self.assertNotIn('private', str(caught.exception))

    def test_parent_and_local_transactions_block_cli_entry(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area) / 'server'; root.mkdir()
            for location in (root, root.parent):
                marker = location / '.update-transaction.json'
                marker.write_text('{}')
                with patch.object(server, 'ROOT', root):
                    with self.assertRaisesRegex(RuntimeError, 'update-backups'):
                        server.ensure_not_updating()
                marker.unlink()
            with patch.object(server, 'ROOT', root):
                server.ensure_not_updating()


if __name__ == '__main__':
    unittest.main()
