"""SuperMedo identity and game API bridge; seamless NP/P2P stays on the private server."""
import base64
import json
import re
import secrets
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET

PUBLIC = 'https://game.supermedo.com'
NP_SERVER = ('np.supermedo.com', 31313)
SUMMON = {'api_SummonDataCreate', 'api_SummonDataGetList',
          'api_SummonDataRemove', 'api_SummonDataSummon'}
LIMIT = 8 * 1024 * 1024
ERRORS = {6: '公共账号正在其他游戏中使用，请先正常退出那边游戏。',
          7: '公共账号不存在或账号名无效。', 8: '公共账号密码不正确。', 9: '公共账号凭据被拒绝。'}


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128); value >>= 7
    result.append(value)
    return bytes(result)


def text_field(field, value):
    raw = value.encode('utf-8')
    return varint(field * 8 + 2) + varint(len(raw)) + raw


def fields(raw):
    """Decode the limited protobuf wire types used by the pinned shadNet login reply."""
    position = 0
    def number():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(raw): raise ValueError('公共账号响应不完整。')
            byte = raw[position]; position += 1; value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError('公共账号响应格式无效。')
    result = {}
    while position < len(raw):
        key = number(); wire = key & 7; field = key >> 3
        if field == 0: raise ValueError('公共账号响应格式无效。')
        if wire == 0: value = number()
        elif wire in (1, 2, 5):
            size = number() if wire == 2 else (8 if wire == 1 else 4)
            if size > len(raw) - position: raise ValueError('公共账号响应不完整。')
            value = raw[position:position + size]; position += size
        else: raise ValueError('公共账号响应格式无效。')
        result[field] = value
    return result


class Account:
    def __init__(self, name, password, address=NP_SERVER):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,16}', name) or not password:
            raise ValueError('请填写 SuperMedo 的 Online ID 和密码。')
        self.socket = None; self.connected = False; self.thread = None; self.user_id = None
        try:
            self.socket = socket.create_connection(address, timeout=12)
            kind, command, request_id, payload = self.packet()
            if kind != 3 or len(payload) < 4 or struct.unpack_from('<I', payload)[0] != 1:
                raise ValueError('公共账号协议版本与当前客户端不一致。')
            login = text_field(1, name) + text_field(2, password)
            login += text_field(4, 'CUSA03173') + text_field(5, 'Bloodborne') + b'\x30\x01'
            self.send(0, 1, login)
            reply = self.reply(0, 1)
            if not reply or reply[0] != 0:
                raise ValueError(ERRORS.get(reply[0] if reply else -1, '公共账号登录被拒绝，请核对账号状态。'))
            if len(reply) < 5 or struct.unpack_from('<I', reply, 1)[0] != len(reply) - 5:
                raise ValueError('公共账号响应不完整。')
            self.user_id = fields(reply[5:]).get(2)
            if type(self.user_id) is not int or not 0 < self.user_id < 2**64:
                raise ValueError('公共服没有返回有效账号身份。')
            self.send(13, 2, text_field(1, 'bbport/1.9.6'))
            version_reply = self.reply(13, 2)
            # Like BBport, version reporting is optional metadata, not an authentication gate.
            # Older compatible shadNet servers return Invalid for command 13.
            self.version_reported = bool(version_reply and version_reply[0] == 0)
            self.connected = True; self.socket.settimeout(None)
            self.thread = threading.Thread(target=self.watch, daemon=True); self.thread.start()
        except BaseException:
            self.close(); raise

    def receive(self, length):
        raw = bytearray()
        while len(raw) < length:
            chunk = self.socket.recv(length - len(raw))
            if not chunk: raise OSError('公共账号连接已断开。')
            raw.extend(chunk)
        return bytes(raw)

    def packet(self):
        kind, command, length, request_id = struct.unpack('<BHIQ', self.receive(15))
        if kind not in (1, 2, 3) or not 15 <= length <= LIMIT:
            raise ValueError('公共账号数据包无效。')
        return kind, command, request_id, self.receive(length - 15)

    def send(self, command, request_id, protobuf):
        payload = struct.pack('<I', len(protobuf)) + protobuf
        self.socket.sendall(struct.pack('<BHIQ', 0, command, 15 + len(payload), request_id) + payload)

    def reply(self, command, request_id):
        deadline = time.monotonic() + 15
        for _ in range(64):
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('公共账号响应超时。')
            self.socket.settimeout(remaining)
            kind, received_command, received_id, payload = self.packet()
            if kind == 1 and received_command == command and received_id == request_id: return payload
        raise ValueError('公共账号响应数量异常。')

    def watch(self):
        try:
            while self.connected: self.packet()  # Drain notifications; no friends or presence is logged.
        except (OSError, ValueError): self.connected = False

    def close(self):
        self.connected = False
        if self.socket:
            try: self.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.socket.close()
        if self.thread: self.thread.join(timeout=2)

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def public_request(path, data=None, headers=None):
    # Reuse BBport's verified Windows certificate-chain implementation, without loading its GUI.
    sys.path.append(str(Path(__file__).resolve().parent / 'engine/launcher'))
    import bbport_tls
    request = Request(PUBLIC + path, data=data, headers=headers or {})
    with bbport_tls.urlopen(request, timeout=20) as response:
        raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT: raise ValueError('公共在线响应超过限制。')
        return response.status, dict(response.headers.items()), raw


def hybrid_info(public_raw, private_raw, bridge_url):
    public_xml = base64.b64decode(public_raw, validate=True).decode('utf-8')
    private_xml = base64.b64decode(private_raw, validate=True).decode('utf-8')
    public = ET.fromstring('<root>' + public_xml + '</root>')
    private = ET.fromstring('<root>' + private_xml + '</root>')
    private_apis = {e.tag: e.text for e in private.iter() if e.tag in SUMMON}
    if set(private_apis) != SUMMON: raise ValueError('私服召唤地址表不完整。')
    for value in private_apis.values():
        parsed = urlsplit(value or '')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('私服召唤地址无效。')
    blocks = [e for e in public if re.fullmatch(r'gameurl\d+', e.tag)]
    if not blocks: raise ValueError('公共地址表不完整。')
    for block in blocks:
        if not SUMMON.issubset({e.tag for e in block}): raise ValueError('公共召唤地址表不完整。')
        for entry in block:
            if not entry.tag.startswith('api_'): raise ValueError('公共地址表格式已变化。')
            if entry.text != PUBLIC + ':443': raise ValueError('公共服务地址已变化，需要适配后使用。')
            entry.text = private_apis[entry.tag] if entry.tag in SUMMON else bridge_url
    # Reuse private seamless matching/poll policy. Public content options stay public.
    options = {e.tag: e.text for e in private if e.tag.startswith(('Summon', 'Playlog', 'PlayLog'))}
    for entry in public:
        if entry.tag in options: entry.text = options[entry.tag]
    return base64.b64encode(''.join(ET.tostring(e, encoding='unicode') for e in public).encode('utf-8'))


class Bridge:
    def __init__(self, account, private_base, transport=public_request):
        self.account = account; self.transport = transport; self.failures = 0; self.login_ok = False
        self.prefix = '/' + secrets.token_hex(24)
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self): self.handle_api()
            def do_POST(self): self.handle_api()
            def handle_api(self):
                try:
                    if self.path == '/bb-eu/ss.info' and self.command == 'GET':
                        self.respond(200, {}, bridge.info); return
                    if not self.path.startswith(bridge.prefix + '/'):
                        self.send_error(404); return
                    path = self.path[len(bridge.prefix):]
                    if path == '/bb-eu/ss.info' and self.command == 'GET':
                        self.respond(200, {}, bridge.info); return
                    endpoint = urlsplit(path).path
                    if not re.fullmatch(r'/(?:basic_utils|blood_messenger|tomb_messenger|wandering_ghost|chair_messenger|channel|messenger_shell|penalty)/[a-z_]+', endpoint):
                        self.send_error(404); return
                    if not account.connected: raise ValueError('公共账号连接已断开。')
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= length <= LIMIT or self.headers.get('Transfer-Encoding'):
                        self.send_error(413); return
                    raw = self.rfile.read(length) if self.command == 'POST' else None
                    if endpoint == '/basic_utils/login':
                        body = json.loads(raw)
                        if not isinstance(body, dict) or 'PlatformAccountId' not in body:
                            raise ValueError('公共游戏登录请求缺少账号身份。')
                        body['PlatformAccountId'] = str(account.user_id)
                        raw = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
                    headers = {name: self.headers[name] for name in ('Content-Type', 'User-Agent', 'Accept', 'Cookie') if name in self.headers}
                    headers['Accept-Encoding'] = 'identity'
                    status, response_headers, data = transport(path, raw, headers)
                    if endpoint == '/basic_utils/login':
                        result = json.loads(data)
                        bridge.login_ok = (status == 200 and result.get('ResKind') == 0
                                           and bool(result.get('SessionId')) and bool(result.get('UserId')))
                        if not bridge.login_ok: bridge.failures += 1
                    self.respond(status, response_headers, data)
                except HTTPError as error:
                    bridge.failures += 1
                    self.respond(error.code, {}, b'{"ResKind":1,"MessageId":"PublicOnlineError"}')
                except (OSError, ValueError, TypeError):
                    bridge.failures += 1
                    self.respond(502, {}, b'{"ResKind":1,"MessageId":"PublicOnlineError"}')
            def respond(self, status, headers, raw):
                self.send_response(status)
                for name, value in headers.items():
                    if name.lower() in ('content-type', 'set-cookie'): self.send_header(name, value)
                self.send_header('Content-Length', str(len(raw))); self.send_header('Connection', 'close')
                self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.url = self.origin + self.prefix
        try:
            status, _, public_raw = transport('/bb-eu/ss.info')
            if status != 200: raise ValueError('公共地址表读取失败。')
            # Private metadata only; never send public credentials to the private service.
            with build_opener(ProxyHandler({})).open(private_base + '/bb-eu/ss.info', timeout=12) as response:
                private_raw = response.read(LIMIT + 1)
            if len(private_raw) > LIMIT: raise ValueError('私服地址表超过限制。')
            self.info = hybrid_info(public_raw.strip(), private_raw.strip(), self.url)
        except BaseException:
            self.server.server_close(); raise
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def main():
    try:
        request = json.load(sys.stdin)
        with Account(request.get('name', ''), request.get('password', '')):
            print(json.dumps({'ok': True, 'message': '公共账号验证成功，启动时会自动连接。'}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) else '公共账号服务连接失败，请稍后重试。'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False)); return 1


if __name__ == '__main__': raise SystemExit(main())
