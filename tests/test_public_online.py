"""Real loopback protocol/HTTP boundaries; never uses a real account or game save."""
import base64
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from public_online import Account, Bridge, SUMMON, PUBLIC, fields, hybrid_info, text_field, varint
import public_online


def info(base, blocks=1):
    apis = sorted(SUMMON | {'api_Login', 'api_BloodMessGetList', 'api_TombMessGetList'})
    xml = '<ss>0</ss>' + ''.join('<gameurl' + str(i) + '>' + ''.join(
        '<' + key + '>' + base + '</' + key + '>' for key in apis) + '</gameurl' + str(i) + '>'
        for i in range(blocks))
    xml += '<SummonDataGetListInterval2>15</SummonDataGetListInterval2><BloodMessGetListInterval2>120</BloodMessGetListInterval2>'
    return base64.b64encode(xml.encode())


@contextmanager
def endpoint(response):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(response())
        def do_POST(self):
            received.append((self.path, dict(self.headers), self.rfile.read(int(self.headers.get('Content-Length', '0')))))
            raw = b'{"ResKind":0,"MessageId":"SummonDataGetListResponse","SummonDataList":[]}'
            self.send_response(200); self.send_header('Content-Length', str(len(raw)))
            self.send_header('X-ShadPS4-Bloodborne-Host-Placement', 'fixture-placement')
            self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield 'http://127.0.0.1:' + str(server.server_port), received
    finally: server.shutdown(); server.server_close(); thread.join()


class NPFixture:
    def __init__(self, error=0, version=1, version_error=0):
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen()
        self.address = self.listener.getsockname(); self.received = []; self.error = None
        def receive(sock, count):
            raw = b''
            while len(raw) < count:
                part = sock.recv(count - len(raw))
                if not part: raise EOFError()
                raw += part
            return raw
        def send(sock, kind, command, request_id, payload):
            sock.sendall(struct.pack('<BHIQ', kind, command, 15 + len(payload), request_id) + payload)
        def packet(sock):
            kind, command, size, request_id = struct.unpack('<BHIQ', receive(sock, 15))
            raw = receive(sock, size - 15)
            self.received.append((command, fields(raw[4:])))
            return command, request_id
        def serve():
            try:
                with self.listener.accept()[0] as sock:
                    sock.settimeout(8); send(sock, 3, 0, 0, struct.pack('<I', version))
                    if version != 1: return
                    command, request_id = packet(sock)
                    pb = b'\x10' + varint(2**63 + 17)
                    send(sock, 1, command, request_id, bytes([error]) + struct.pack('<I', len(pb)) + pb)
                    if error: return
                    command, request_id = packet(sock); send(sock, 1, command, request_id, bytes([version_error]))
                    receive(sock, 1)  # Remains alive until the owner closes, without GetToken or writes.
            except EOFError: pass
            except BaseException as failure: self.error = failure
        self.thread = threading.Thread(target=serve, daemon=True); self.thread.start()
    def close(self):
        self.thread.join(timeout=10); self.listener.close()
        if self.thread.is_alive(): raise AssertionError('NP fixture did not close')
        if self.error: raise self.error


FIXTURE_ID = 2**63 + 17
SECRET = 'Pw-fixture-Secret-7731'
FAST = (0.05, 0.2)  # retry_delays for every reconnect test


def ok(*after, user_id=FIXTURE_ID):
    """Script for a session that logs in normally, then runs ``after`` (default: hold)."""
    return [('hello', 1), ('login', 0, user_id), ('version', 0)] + list(after or [('hold',)])


def rejected(code):
    return [('hello', 1), ('login', code, FIXTURE_ID)]


class ScriptedNP:
    """Loopback shadNet server: session N (in accept order) runs scripts[N], later ones ``default``.

    Steps: ('wait', Event), ('hello', version), ('login', code, user_id), ('version', error),
    ('hold',), ('close_after', s) (FIN), ('reset_after', s) (RST), ('garbage',) (kind 9 packet),
    ('notify_after', s) (valid kind 2 packet), ('drop',) (close right after accept).
    """
    def __init__(self, *scripts, default=(('drop',),)):
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen(16)
        self.listener.settimeout(0.05)
        self.address = self.listener.getsockname()
        self.scripts = [list(script) for script in scripts]; self.default = list(default)
        self.sessions = []; self.errors = []; self.lock = threading.Lock()
        self.stopping = threading.Event(); self.threads = []; self.sockets = []
        self.thread = threading.Thread(target=self._accept, daemon=True); self.thread.start()

    @property
    def accepted(self):
        with self.lock: return len(self.sessions)

    def _accept(self):
        while not self.stopping.is_set():
            try: sock, _ = self.listener.accept()
            except socket.timeout: continue
            except OSError: return
            with self.lock:
                index = len(self.sessions); record = {'login': None, 'version': None}
                self.sessions.append(record); self.sockets.append(sock)
                script = self.scripts[index] if index < len(self.scripts) else self.default
            thread = threading.Thread(target=self._serve, args=(sock, script, record), daemon=True)
            thread.start(); self.threads.append(thread)

    @staticmethod
    def _exact(sock, count):
        raw = b''
        while len(raw) < count:
            part = sock.recv(count - len(raw))
            if not part: raise EOFError()
            raw += part
        return raw

    def _read(self, sock):
        kind, command, size, request_id = struct.unpack('<BHIQ', self._exact(sock, 15))
        return command, request_id, fields(self._exact(sock, size - 15)[4:])

    @staticmethod
    def _send(sock, kind, command, request_id, payload):
        sock.sendall(struct.pack('<BHIQ', kind, command, 15 + len(payload), request_id) + payload)

    def _pause(self, seconds):
        self.stopping.wait(seconds)

    def _hold(self, sock):
        sock.settimeout(0.05)
        while not self.stopping.is_set():
            try:
                if not sock.recv(4096): return
            except socket.timeout: continue

    def _serve(self, sock, script, record):
        try:
            sock.settimeout(5)
            for step in script:
                action = step[0]
                if action == 'wait':
                    while not step[1].wait(0.02):
                        if self.stopping.is_set(): return
                elif action == 'hello': self._send(sock, 3, 0, 0, struct.pack('<I', step[1]))
                elif action == 'login':
                    command, request_id, record['login'] = self._read(sock)
                    pb = b'\x10' + varint(step[2])
                    self._send(sock, 1, command, request_id, bytes([step[1]]) + struct.pack('<I', len(pb)) + pb)
                    if step[1]: return
                elif action == 'version':
                    command, request_id, record['version'] = self._read(sock)
                    self._send(sock, 1, command, request_id, bytes([step[1]]))
                elif action == 'hold': self._hold(sock); return
                elif action == 'close_after': self._pause(step[1]); return
                elif action == 'reset_after':
                    self._pause(step[1])
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                    struct.pack('HH' if sys.platform == 'win32' else 'ii', 1, 0))
                    return
                elif action == 'garbage': self._send(sock, 9, 0, 0, b'')
                elif action == 'notify_after': self._pause(step[1]); self._send(sock, 2, 0, 0, b'')
                elif action == 'drop': return
                else: raise AssertionError('unknown step ' + repr(action))
        except (EOFError, ConnectionError, socket.timeout): pass
        except OSError as failure:
            if not self.stopping.is_set(): self.errors.append(failure)
        except BaseException as failure: self.errors.append(failure)
        finally:
            try: sock.close()
            except OSError: pass

    def close(self):
        self.stopping.set(); self.listener.close()
        for sock in list(self.sockets):
            try: sock.close()
            except OSError: pass
        self.thread.join(timeout=5)
        for thread in self.threads: thread.join(timeout=5)
        if self.thread.is_alive() or any(thread.is_alive() for thread in self.threads):
            raise AssertionError('scripted NP fixture did not stop')
        if self.errors: raise self.errors[0]


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return True
        time.sleep(0.005)
    return predicate()


@contextmanager
def live_bridge(account, transport):
    """Actual Bridge over loopback; ss.info comes from fixtures, other paths from ``transport``."""
    def wrapped(path, raw=None, headers=None):
        if path == '/bb-eu/ss.info': return 200, {}, info(PUBLIC + ':443')
        return transport(path, raw, headers)
    with endpoint(lambda: info(private)) as (private, _):
        with Bridge(account, private, wrapped) as bridge:
            yield bridge


def post(bridge, path, body=b'{}'):
    """(status, raw) for a request through the bridge, including error statuses."""
    try:
        with build_opener(ProxyHandler({})).open(Request(bridge.url + path, data=body), timeout=10) as reply:
            return reply.status, reply.read()
    except HTTPError as error:
        try: return error.code, error.read()
        finally: error.close()


class PublicOnline(unittest.TestCase):
    def test_game_environment_excludes_public_credentials_and_keeps_private_np(self):
        import client
        import os
        import tempfile
        import shutil
        from unittest.mock import patch
        root_source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as area:
            root = Path(area); (root/'mod').mkdir()
            (root/'mod/mod.json').write_text('{"settings":[]}',encoding='utf-8')
            shutil.copy2(root_source/'launcher/feature-catalog.json',root/'feature-catalog.json')
            network={'shadnet':'127.0.0.1:31413','webapi':'http://127.0.0.1:31415','community':'http://127.0.0.1:20443','relay':'127.0.0.1:20444'}
            with patch.object(client,'ROOT',root), patch.dict(os.environ,{'THEHUNT_PUBLIC_ACCOUNT':'public-fixture','THEHUNT_PUBLIC_PASSWORD':'public-secret'}):
                env, _ = client.build_environment({'game':str(root/'game'),'network':network},'private-fixture','private-secret',{}, {'patches_dir':str(root/'patches'),'patches_config':str(root/'patches.json')})
            self.assertFalse(any(key.startswith('THEHUNT_PUBLIC_') for key in env))
            self.assertEqual(env['BB_SHADNET_NPID'],'private-fixture')
            self.assertEqual(env['BB_SHADNET_PASSWORD'],'private-secret')
            self.assertEqual(env['BBSC_RELAY'],network['relay'])

    def test_real_login_wire_keeps_identity_and_session_independent(self):
        fixture = NPFixture()
        try:
            with Account('Public_fixture', 'public-fixture-password', fixture.address) as account:
                self.assertEqual(account.user_id, 2**63 + 17)
                self.assertTrue(account.connected)
            self.assertFalse(account.connected)
        finally: fixture.close()
        self.assertEqual([row[0] for row in fixture.received], [0, 13])
        login = fixture.received[0][1]
        self.assertEqual(login[1], b'Public_fixture'); self.assertEqual(login[2], b'public-fixture-password')
        self.assertNotIn(6, login)  # Keep BBport's normal online presence; do not force Appear-Offline.
        self.assertEqual(fixture.received[1][1][1], b'bbport/1.9.7')

    def test_rejected_account_is_not_retried_or_registered(self):
        fixture = NPFixture(error=6)
        try:
            with self.assertRaisesRegex(ValueError, '其他游戏'): Account('Public_fixture', 'fixture', fixture.address)
        finally: fixture.close()
        self.assertEqual(len(fixture.received), 1)

    def test_optional_version_metadata_is_not_used_as_a_login_failure(self):
        fixture = NPFixture(version_error=2)
        try:
            with Account('Public_fixture', 'fixture', fixture.address) as account:
                self.assertTrue(account.connected); self.assertFalse(account.version_reported)
        finally: fixture.close()

    def test_protocol_mismatch_does_not_send_credentials(self):
        fixture = NPFixture(version=2)
        try:
            with self.assertRaisesRegex(ValueError, '协议版本'): Account('Public_fixture', 'fixture', fixture.address)
        finally: fixture.close()
        self.assertEqual(fixture.received, [])

    def test_truncated_and_invalid_protobuf_is_refused(self):
        for value in (b'\x10\x80', b'\x12\x05a', b'\0', b'\x0f'):
            with self.assertRaises(ValueError): fields(value)

    def test_all_regions_keep_private_summons_and_polling(self):
        public = info(PUBLIC + ':443', 3).replace(b'\n', b'')
        # Public notes keep their own polling, private summon policy is imported.
        xml = base64.b64decode(public).replace(b'>15<', b'>35<').replace(b'>120<', b'>90<')
        xml += b'<Playlog_ChrDead2>1</Playlog_ChrDead2>'
        private = base64.b64decode(info('http://127.0.0.1:31415')) + b'<Playlog_ChrDead2>0</Playlog_ChrDead2>'
        mixed = hybrid_info(base64.b64encode(xml), base64.b64encode(private), 'http://127.0.0.1:12000/secret')
        root = ET.fromstring('<root>' + base64.b64decode(mixed).decode() + '</root>')
        for node in root.iter():
            if node.tag in SUMMON: self.assertEqual(node.text, 'http://127.0.0.1:31415')
            elif node.tag.startswith('api_'): self.assertEqual(node.text, 'http://127.0.0.1:12000/secret')
        self.assertEqual(root.find('SummonDataGetListInterval2').text, '15')
        self.assertEqual(root.find('BloodMessGetListInterval2').text, '90')
        self.assertEqual(root.find('Playlog_ChrDead2').text, '0')
        with self.assertRaisesRegex(ValueError, '地址已变化'):
            hybrid_info(info('https://other.invalid:443'), info('http://127.0.0.1:31415'), 'http://127.0.0.1:12000/secret')

    def test_actual_http_bridge_login_notes_and_private_summon_boundary(self):
        requests = []
        class Identity:
            user_id = 2**63 + 17
            connected = True
        account = Identity()
        def transport(path, raw=None, headers=None):
            if path == '/bb-eu/ss.info': return 200, {}, info(PUBLIC + ':443')
            requests.append((path, json.loads(raw), headers))
            return 200, {'Content-Type': 'application/json'}, b'{"ResKind":0,"UserId":888,"SessionId":"public-game-session","BloodMessList":[]}'
        with endpoint(lambda: info(private)) as (private, private_requests):
            with Bridge(account, private, transport) as bridge:
                opener = build_opener(ProxyHandler({}))
                with opener.open(bridge.origin + '/bb-eu/ss.info') as reply:
                    root = ET.fromstring('<root>' + base64.b64decode(reply.read()).decode() + '</root>')
                api = {node.tag: node.text for node in root.iter() if node.tag.startswith('api_')}
                login = {'PlatformAccountId': 'private-id-77', 'MessageId': 'LoginRequest', 'AuthCode': 'AUTHEN'}
                with opener.open(Request(api['api_Login'] + '/basic_utils/login', data=json.dumps(login).encode())) as reply:
                    self.assertEqual(json.load(reply)['UserId'], 888)
                self.assertEqual(requests[0][1]['PlatformAccountId'], str(account.user_id))
                self.assertEqual(requests[0][1]['AuthCode'], 'AUTHEN'); self.assertTrue(bridge.login_ok)
                notes = {'UserId': 888, 'SessionId': 'public-game-session', 'MessageId': 'BloodMessGetListRequest'}
                with opener.open(Request(api['api_BloodMessGetList'] + '/blood_messenger/message_area', data=json.dumps(notes).encode())) as reply:
                    self.assertEqual(json.load(reply)['BloodMessList'], [])
                self.assertEqual(requests[1][1], notes)
                sign = dict(notes, NpId='private-player', SessionId='private-room-session')
                with opener.open(Request(api['api_SummonDataGetList'] + '/summon_messenger/get', data=json.dumps(sign).encode())) as reply:
                    self.assertEqual(reply.headers['X-ShadPS4-Bloodborne-Host-Placement'], 'fixture-placement')
                self.assertEqual(json.loads(private_requests[0][2]), sign)
                self.assertEqual(len(requests), 2)
                for path in ('/basic_utils/login', bridge.prefix + '/summon_messenger/get', bridge.prefix + '//other.invalid/x'):
                    with self.assertRaises(HTTPError) as error: opener.open(Request(bridge.origin + path, data=b'{}'))
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
                account.connected = False
                with self.assertRaises(HTTPError) as error:
                    opener.open(Request(api['api_BloodMessGetList'] + '/blood_messenger/message_area', data=b'{}'))
                self.assertEqual(error.exception.code, 502)
                error.exception.close()
                self.assertEqual(len(requests), 2)
            with socket.socket() as probe: self.assertNotEqual(probe.connect_ex(('127.0.0.1', bridge.server.server_port)), 0)


class Reconnect(unittest.TestCase):
    """Game-session account (keep_alive=True) against a scripted loopback NP server."""
    def start(self, *scripts, default=(('drop',),), keep_alive=True, **options):
        fixture = ScriptedNP(*scripts, default=default)
        self.addCleanup(fixture.close)
        events = []
        options.setdefault('retry_delays', FAST)
        account = Account('Public_fixture', SECRET, fixture.address, keep_alive=keep_alive,
                          on_event=events.append, **options)
        self.addCleanup(account.close)
        return fixture, account, events

    @staticmethod
    def kinds(events): return [event['kind'] for event in events]

    def test_server_close_reconnects_with_same_identity(self):
        gate = threading.Event()
        fixture, account, events = self.start(ok(('close_after', 0.05)), [('wait', gate)] + ok())
        self.assertTrue(wait_until(lambda: account.state == 'reconnecting'))
        self.assertFalse(account.connected)
        self.assertFalse(account.wait_connected(0.1))
        gate.set()
        self.assertTrue(account.wait_connected(3))
        self.assertEqual(account.state, 'connected')
        snapshot = account.snapshot()
        self.assertEqual((snapshot['reconnects'], snapshot['attempts'], snapshot['since_disconnect_s']), (1, 0, None))
        self.assertEqual(snapshot['last_disconnect']['reason'], 'eof')
        self.assertEqual(account.user_id, FIXTURE_ID)
        first, second = fixture.sessions[0]['login'], fixture.sessions[1]['login']
        self.assertEqual(second, first)
        self.assertEqual((second[1], second[2]), (b'Public_fixture', SECRET.encode()))
        self.assertEqual(fixture.sessions[1]['version'][1], b'bbport/1.9.7')
        self.assertEqual(self.kinds(events), ['connected', 'drop', 'reconnected'])
        remote = '127.0.0.1:' + str(fixture.address[1])
        self.assertEqual((events[0]['remote'], events[1]['remote'], snapshot['remote']), (remote,) * 3)
        self.assertEqual((events[1]['reason'], events[1]['retry']), ('eof', True))
        self.assertGreaterEqual(events[1]['connected_for_s'], 0)
        self.assertEqual(events[2]['reconnects'], 1)

    def test_reset_is_classified_and_recovered(self):
        fixture, account, events = self.start(ok(('reset_after', 0.05)), ok())
        self.assertTrue(wait_until(lambda: account.snapshot()['reconnects'] == 1))
        self.assertTrue(account.connected)
        drop = next(event for event in events if event['kind'] == 'drop')
        self.assertEqual(drop['reason'], 'reset'); self.assertIn('errno', drop)
        self.assertEqual(account.snapshot()['last_disconnect']['reason'], 'reset')

    def test_already_logged_in_is_retried_during_reconnect(self):
        gate = threading.Event()
        fixture, account, events = self.start(ok(('close_after', 0.05)), rejected(6),
                                              [('wait', gate)] + rejected(6), ok())
        self.assertTrue(wait_until(lambda: account.snapshot()['attempts'] == 1))
        snapshot = account.snapshot()
        self.assertEqual(snapshot['state'], 'reconnecting')
        self.assertIsNotNone(snapshot['already_in_for_s'])
        gate.set()
        self.assertTrue(wait_until(lambda: account.snapshot()['reconnects'] == 1))
        retries = [(event['attempt'], event['reason']) for event in events if event['kind'] == 'retry_failed']
        self.assertEqual(retries, [(1, 'rejected:6'), (2, 'rejected:6')])
        self.assertEqual(next(event for event in events if event['kind'] == 'reconnected')['failed_attempts'], 2)
        snapshot = account.snapshot()
        self.assertEqual((snapshot['state'], snapshot['attempts'], snapshot['already_in_for_s']), ('connected', 0, None))
        self.assertEqual(fixture.accepted, 4)

    def test_wrong_password_on_reconnect_stops(self):
        fixture, account, events = self.start(ok(('close_after', 0.05)), rejected(8), default=ok())
        self.assertTrue(wait_until(lambda: account.state == 'failed'))
        self.assertFalse(account.wait_connected(0))
        sessions = fixture.accepted
        time.sleep(0.5)
        self.assertEqual((fixture.accepted, sessions), (2, 2))
        self.assertIn('密码', account.snapshot()['message'])
        self.assertEqual((events[-1]['kind'], events[-1]['reason']), ('failed', 'rejected:8'))

    def test_identity_change_on_reconnect_fails_closed(self):
        fixture, account, events = self.start(ok(('close_after', 0.05)), ok(user_id=FIXTURE_ID + 1), default=ok())
        self.assertTrue(wait_until(lambda: account.state == 'failed'))
        self.assertEqual((events[-1]['kind'], events[-1]['reason']), ('failed', 'identity_changed'))
        self.assertEqual(account.user_id, FIXTURE_ID)
        self.assertIn('身份', account.snapshot()['message'])
        time.sleep(0.3)
        self.assertEqual(fixture.accepted, 2)

    def test_protocol_garbage_triggers_reconnect(self):
        fixture, account, events = self.start(ok(('garbage',), ('hold',)), ok())
        self.assertTrue(wait_until(lambda: account.snapshot()['reconnects'] == 1))
        self.assertTrue(account.connected)
        self.assertEqual(account.snapshot()['last_disconnect']['reason'], 'protocol')

    def test_unexpected_exception_in_reader_is_a_drop(self):
        fixture, account, events = self.start(ok(('notify_after', 0.2), ('hold',)), ok())
        original = account.packet; raised = []
        def explode(sock):
            if not raised:
                raised.append(True); raise RuntimeError('fixture reader bug')
            return original(sock)
        account.packet = explode
        self.assertTrue(wait_until(lambda: account.snapshot()['reconnects'] == 1))
        self.assertTrue(account.thread.is_alive()); self.assertTrue(account.connected)
        self.assertEqual(self.kinds(events), ['connected', 'drop', 'reconnected'])
        self.assertEqual(events[1]['reason'], 'internal:RuntimeError')

    def test_close_interrupts_backoff_and_attempts(self):
        # Backoff: every later session is dropped right after accept; ceiling far above 1 s.
        fixture, account, events = self.start(ok(('close_after', 0.05)), retry_delays=(0.05, 5.0))
        self.assertTrue(wait_until(lambda: account.snapshot()['attempts'] >= 3))
        started = time.monotonic(); account.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(account.state, 'closed'); self.assertFalse(account.thread.is_alive())
        sessions = fixture.accepted; time.sleep(0.4)
        self.assertEqual(fixture.accepted, sessions)
        # In-flight handshake: the server accepts and never sends the hello packet.
        never = threading.Event()
        fixture, account, events = self.start(ok(('close_after', 0.05)), default=[('wait', never)])
        self.assertTrue(wait_until(lambda: fixture.accepted == 2))
        time.sleep(0.05)
        started = time.monotonic(); account.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(account.state, 'closed'); self.assertFalse(account.thread.is_alive())
        time.sleep(0.3)
        self.assertEqual(fixture.accepted, 2)
        self.assertEqual(self.kinds(events)[-1], 'closed')
        account.close()  # Repeated close is harmless.
        self.assertEqual(self.kinds(events).count('closed'), 1)

    def test_keepalive_is_enabled_on_every_connection(self):
        real = public_online._enable_keepalive; calls = []
        def spy(sock, idle_ms, interval_ms):
            calls.append((idle_ms, interval_ms)); return real(sock, idle_ms, interval_ms)
        with patch.object(public_online, '_enable_keepalive', spy):
            fixture, account, events = self.start(ok(('close_after', 0.2)), ok())
            first = account._sock  # Test-only peek; production code never touches the socket.
            self.assertNotEqual(first.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE), 0)
            self.assertTrue(wait_until(lambda: account.snapshot()['reconnects'] == 1))
            second = account._sock
            self.assertIsNot(second, first)
            self.assertNotEqual(second.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE), 0)
        self.assertEqual(calls, [(60000, 10000)] * 2)
        class Recorder:
            def __init__(self): self.calls = []
            def setsockopt(self, *args): self.calls.append(('setsockopt',) + args)
            def ioctl(self, *args): self.calls.append(('ioctl',) + args)
        recorder = Recorder(); public_online._enable_keepalive(recorder, 60000, 10000)
        self.assertIn(('setsockopt', socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1), recorder.calls)
        if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
            self.assertEqual(recorder.calls[-1], ('ioctl', socket.SIO_KEEPALIVE_VALS, (1, 60000, 10000)))
        elif hasattr(socket, 'TCP_KEEPIDLE'):
            self.assertIn(('setsockopt', socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60), recorder.calls)
        # Keepalive is hardening only: a refusal is recorded, the login still succeeds.
        with patch.object(public_online, '_enable_keepalive', side_effect=OSError(10022, 'fixture')):
            fixture, account, events = self.start(ok())
            self.assertTrue(account.connected)
            self.assertEqual(self.kinds(events), ['keepalive_failed', 'connected'])
            self.assertEqual(events[0]['errno'], 10022)

    def test_verification_path_does_not_keep_password_or_reconnect(self):
        fixture, account, events = self.start(ok(('close_after', 0.05)), default=ok(), keep_alive=False)
        self.assertTrue(wait_until(lambda: account.state == 'closed'))
        time.sleep(0.3)
        self.assertEqual(fixture.accepted, 1)
        self.assertEqual(self.kinds(events), ['connected', 'drop'])
        self.assertFalse(events[1]['retry'])
        self.assertFalse(account.wait_connected(0))
        self.assertIsNone(account._credentials)
        self.assertNotIn(SECRET, repr(vars(account)))

    def test_password_never_leaks(self):
        import client
        captured = []; errors = []
        with tempfile.TemporaryDirectory() as area:
            log = client.PublicOnlineEvents(Path(area) / 'user/public-online-events.log', anchor=Path(area))
            stderr = io.StringIO()
            with patch('sys.stderr', stderr):
                def record(event): captured.append(event); log.write(event)
                # drop (eof) -> already in (6) -> recovered -> drop (reset) -> wrong password (8).
                fixture = ScriptedNP(ok(('close_after', 0.05)), rejected(6), ok(('reset_after', 0.05)), rejected(8))
                self.addCleanup(fixture.close)
                account = Account('Public_fixture', SECRET, fixture.address, keep_alive=True,
                                  retry_delays=FAST, on_event=record)
                self.addCleanup(account.close)
                self.assertTrue(wait_until(lambda: account.state == 'failed', 5))
                snapshot = account.snapshot(); history = account.events(); text = repr(account)
                notice = client.public_notice(snapshot)
                account.close()
                for code in (8, 6, 99):
                    refusing = ScriptedNP(rejected(code))
                    try:
                        with self.assertRaises(ValueError) as caught:
                            Account('Public_fixture', SECRET, refusing.address, keep_alive=True)
                        errors.append(caught.exception)
                    finally: refusing.close()
            written = log.path.read_text(encoding='utf-8')
        self.assertEqual(self.kinds(captured), ['connected', 'drop', 'retry_failed', 'reconnected',
                                                'drop', 'failed', 'closed'])
        self.assertIn('reason=reset', written); self.assertIn('reason=rejected:8', written)
        haystack = (json.dumps([captured, history, snapshot], ensure_ascii=False) + notice + text + written
                    + stderr.getvalue() + repr(vars(account))
                    + ''.join(str(error) + repr(error) + repr(vars(error)) for error in errors))
        self.assertNotIn(SECRET, haystack)
        self.assertEqual([error.code for error in errors], [8, 6, 99])

    def test_bridge_rejects_while_reconnecting_and_resumes(self):
        gate = threading.Event()
        fixture, account, events = self.start(ok(('close_after', 0.05)), [('wait', gate)] + ok())
        forwarded = []
        def transport(path, raw, headers):
            forwarded.append((path, json.loads(raw)))
            return 200, {'Content-Type': 'application/json'}, b'{"ResKind":0,"UserId":888,"SessionId":"public-game-session"}'
        with live_bridge(account, transport) as bridge:
            self.assertTrue(wait_until(lambda: account.state == 'reconnecting'))
            status, raw = post(bridge, '/wandering_ghost/get?user_id=4935', b'{"UserId":888,"SessionId":"s"}')
            self.assertEqual((status, json.loads(raw)['MessageId']), (502, 'PublicOnlineError'))
            self.assertEqual(forwarded, [])
            stats = bridge.stats()
            self.assertEqual((stats['failures'], stats['offline_rejections'], stats['upstream_failures'],
                              stats['consecutive_failures']), (1, 1, 0, 0))
            self.assertEqual({key: stats['last_failure'][key] for key in ('kind', 'status', 'endpoint')},
                             {'kind': 'offline', 'status': 502, 'endpoint': '/wandering_ghost/get'})
            gate.set()
            self.assertTrue(account.wait_connected(3))
            login = {'PlatformAccountId': 'private-id-77', 'MessageId': 'LoginRequest'}
            self.assertEqual(post(bridge, '/basic_utils/login', json.dumps(login).encode())[0], 200)
            self.assertEqual(forwarded[0], ('/basic_utils/login', {'PlatformAccountId': str(FIXTURE_ID),
                                                                   'MessageId': 'LoginRequest'}))
            self.assertTrue(bridge.login_ok)
            self.assertEqual(post(bridge, '/wandering_ghost/get?user_id=4935', b'{"UserId":888}')[0], 200)
            self.assertEqual(forwarded[1], ('/wandering_ghost/get?user_id=4935', {'UserId': 888}))
            stats = bridge.stats()
            self.assertEqual((stats['failures'], stats['offline_rejections'], stats['consecutive_failures']), (1, 1, 0))

    def test_bridge_counts_upstream_failures_separately(self):
        class Identity:
            user_id = FIXTURE_ID
            connected = True
        account = Identity()
        replies = [HTTPError('https://fixture.invalid/x', 502, 'fixture', {}, None), None, OSError('fixture network')]
        def transport(path, raw, headers):
            reply = replies.pop(0) if replies else None
            if reply is not None: raise reply
            return 200, {'Content-Type': 'application/json'}, b'{"ResKind":0,"BloodMessList":[]}'
        def brief(failure): return {key: failure[key] for key in ('kind', 'status', 'endpoint')}
        with live_bridge(account, transport) as bridge:
            self.assertEqual(post(bridge, '/blood_messenger/message_area', b'{"Body":"private"}')[0], 502)
            stats = bridge.stats()
            self.assertEqual((stats['failures'], stats['upstream_failures'], stats['consecutive_failures'],
                              stats['offline_rejections']), (1, 1, 1, 0))
            self.assertEqual(brief(stats['last_failure']),
                             {'kind': 'http', 'status': 502, 'endpoint': '/blood_messenger/message_area'})
            self.assertNotIn('private', json.dumps(stats))
            self.assertEqual(post(bridge, '/blood_messenger/message_area')[0], 200)
            stats = bridge.stats()
            self.assertEqual((stats['failures'], stats['upstream_failures'], stats['consecutive_failures']), (1, 1, 0))
            self.assertEqual(post(bridge, '/blood_messenger/message_area')[0], 502)
            stats = bridge.stats()
            self.assertEqual((stats['upstream_failures'], stats['consecutive_failures']), (2, 1))
            self.assertEqual(stats['last_failure']['kind'], 'network')
            # Handler threads share the counters; concurrent rejections are all counted.
            account.connected = False
            def burst():
                for _ in range(6): post(bridge, '/wandering_ghost/get')
            workers = [threading.Thread(target=burst) for _ in range(4)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(timeout=10)
            stats = bridge.stats()
            self.assertEqual((stats['offline_rejections'], stats['failures'], stats['consecutive_failures']), (24, 26, 1))

    def test_bridge_records_reskind_errors_without_blocking(self):
        class Identity:
            user_id = FIXTURE_ID
            connected = True
        json_type = {'Content-Type': 'application/json'}
        replies = [(json_type, b'{"ResKind":1,"MessageId":"X"}'),
                   ({'Content-Type': 'text/plain'}, b'{"ResKind":1,"MessageId":"Y"}'),
                   (json_type, b'{"ResKind":1,"Pad":"' + b'a' * 70000 + b'"}'),
                   (json_type, b'{"ResKind":0}')]
        def transport(path, raw, headers):
            reply_headers, body = replies.pop(0)
            return 200, reply_headers, body
        with live_bridge(Identity(), transport) as bridge:
            self.assertEqual(post(bridge, '/tomb_messenger/get_list'), (200, b'{"ResKind":1,"MessageId":"X"}'))
            stats = bridge.stats()
            self.assertEqual((stats['reskind_errors'], stats['consecutive_failures'], stats['failures']), (1, 0, 0))
            self.assertEqual({key: stats['last_reskind_error'][key] for key in ('endpoint', 'MessageId')},
                             {'endpoint': '/tomb_messenger/get_list', 'MessageId': 'X'})
            for _ in range(3): self.assertEqual(post(bridge, '/tomb_messenger/get_list')[0], 200)
            self.assertEqual(bridge.stats()['reskind_errors'], 1)


if __name__ == '__main__': unittest.main(verbosity=2)
