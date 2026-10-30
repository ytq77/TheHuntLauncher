"""Real loopback protocol/HTTP boundaries; never uses a real account or game save."""
import base64
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from public_online import Account, Bridge, SUMMON, PUBLIC, fields, hybrid_info, text_field, varint


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
        self.assertEqual(fixture.received[1][1][1], b'bbport/1.9.6')

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


if __name__ == '__main__': unittest.main(verbosity=2)
