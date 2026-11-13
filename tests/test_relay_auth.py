"""Loopback NP/status tests; no real account, game, save, or public service."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import relay_auth as auth


def read(connection, count):
    result = b''
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk: raise EOFError
        result += chunk
    return result


class NP:
    def __init__(self, uid=17, token_uid=17, npid='Private_fixture', token=b'fixture-token',
                 error=0, version=1, second=False):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0)); self.listener.listen()
        self.listener.settimeout(5)
        self.address = self.listener.getsockname()
        self.commands = []; self.logins = []; self.failure = None; self.closed = threading.Event()
        def send(connection, kind, command, request, raw):
            packet = struct.pack('<BHIQ', kind, command, len(raw) + 15, request) + raw
            # Exercise bytewise partial reads of headers and payloads.
            for byte in packet: connection.sendall(bytes([byte]))
        def success(connection, command, request, protobuf):
            send(connection, 1, command, request, b'\0' + struct.pack('<I', len(protobuf)) + protobuf)
        def serve():
            try:
                for session in range(2 if second else 1):
                    with self.listener.accept()[0] as connection:
                        connection.settimeout(5)
                        send(connection, 3, 0, 0, struct.pack('<I', version))
                        if version != 1:
                            self.closed.set(); return
                        while True:
                            try: kind, command, size, request = struct.unpack('<BHIQ', read(connection, 15))
                            except EOFError: break
                            raw = read(connection, size - 15)
                            self.commands.append(command)
                            if command == 0:
                                self.logins.append(auth.fields(raw[4:]))
                                if error:
                                    send(connection, 1, command, request, bytes([error])); break
                                success(connection, command, request, b'\x10' + auth.varint(uid))
                            elif command == 39:
                                if raw: raise AssertionError('GetToken must have no payload')
                                pb = auth.varint(10) + auth.varint(len(token)) + token
                                pb += b'\x10' + auth.varint(token_uid) + auth.text_field(3, npid)
                                success(connection, command, request, pb)
                            elif command == 1:
                                send(connection, 1, command, request, b'\0')
                                break
                            else: raise AssertionError('unexpected NP command')
                    self.closed.set()
            except BaseException as failure: self.failure = failure
        self.thread = threading.Thread(target=serve, daemon=True); self.thread.start()
    def close(self):
        self.thread.join(6); self.listener.close()
        if self.thread.is_alive(): raise AssertionError('fixture did not stop')
        if self.failure: raise self.failure


@contextmanager
def status(payload, code=200):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            self.send_response(code); self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}', requests
    finally: server.shutdown(); server.server_close(); thread.join()


class RelayAuthTests(unittest.TestCase):
    def test_login_gettoken_terminate_then_same_account_login(self):
        fixture = NP(second=True)
        try:
            for _ in range(2):
                self.assertEqual(auth.retrieve('Private_fixture', 'private-password', fixture.address),
                                 (17, 'fixture-token'))
        finally: fixture.close()
        self.assertEqual(fixture.commands, [0,39,1,0,39,1])
        self.assertEqual(fixture.logins[0][1], b'Private_fixture')
        self.assertEqual(fixture.logins[0][2], b'private-password')
        self.assertNotIn(3, fixture.logins[0])

    def test_identity_mismatch_empty_bad_token_and_uint32_limit(self):
        for options in ({'token_uid':18}, {'npid':'Other'}, {'uid':2**32},
                        {'uid':0}, {'token':b''}, {'token':b'a\0b'}, {'token':b'\xff'}):
            with self.subTest(options=options):
                fixture = NP(**options)
                try:
                    with self.assertRaisesRegex(ValueError, '^' + auth.ERROR + '$'):
                        auth.retrieve('Private_fixture', 'secret', fixture.address)
                finally: fixture.close()

    def test_errors_and_protocol_mismatch_never_echo_secrets(self):
        for options in ({'error':6}, {'error':8}, {'version':2}):
            fixture = NP(**options)
            try:
                with self.assertRaises(ValueError) as failure:
                    auth.retrieve('Private_fixture', 'secret', fixture.address)
                self.assertEqual(str(failure.exception), auth.ERROR)
            finally: fixture.close()
            self.assertLessEqual(len(fixture.commands), 1)

    def test_status_is_version_gate_and_contains_no_credentials(self):
        with status({'relay_auth':2}) as (url, requests), status({'community':True}) as (community, community_requests):
            fixture = NP()
            network = {'shadnet':f'127.0.0.1:{fixture.address[1]}', 'relay':'127.0.0.1:20444',
                       'webapi':url, 'community':community}
            self.assertNotEqual(url, community)
            before = dict(os.environ)
            try: env = auth.environment(network, 'Private_fixture', 'secret')
            finally: fixture.close()
            self.assertEqual(env['BBSC_RELAY_TOKEN'], 'fixture-token')
            self.assertEqual(env['BBSC_RELAY_AUTH'], 'private-v2')
            self.assertEqual(env['BBSC_RELAY_USER_ID'], '17')
            self.assertEqual(before, dict(os.environ))
            self.assertEqual(requests[0][0], '/status')
            self.assertEqual(community_requests, [])
            self.assertNotIn('secret', str(requests)); self.assertNotIn('token', str(requests))
        for payload in ({}, {'relay_auth':'2'}, {'relay_auth':True}, {'relay_auth':1}):
            with status(payload) as (url, _), patch.object(auth, 'retrieve') as retrieve:
                with self.assertRaisesRegex(ValueError, auth.UPGRADE):
                    auth.environment({**network, 'webapi':url}, 'Private_fixture', 'secret')
                retrieve.assert_not_called()

    def test_explicit_public_mode_and_mixed_or_lookalike_host_fail_closed(self):
        with patch.object(auth, 'retrieve') as retrieve, patch.object(auth, 'preflight') as preflight:
            result = auth.environment({'shadnet':'np.supermedo.com:31313',
                                       'relay':'np.supermedo.com:20444'}, 'x', 'y')
            self.assertEqual(result, {'BBSC_RELAY_AUTH':'public'})
            with self.assertRaises(ValueError):
                auth.environment({'shadnet':'np.supermedo.com:31313',
                                  'relay':'127.0.0.1:20444'}, 'x', 'y')
            retrieve.assert_not_called(); preflight.assert_not_called()
        self.assertFalse(auth.is_public('evilshadps4.net'))
        self.assertFalse(auth.is_public('shadps4.net.evil.test'))

    def test_bad_protobuf_and_duplicate_identity(self):
        for raw in (b'\x10\x80', b'\x10\xff'*10, b'\x12\x02x', b'\0', b'\x10\x01\x10\x02'):
            with self.assertRaises(ValueError): auth.fields(raw)


if __name__ == '__main__': unittest.main()
