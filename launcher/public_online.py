"""SuperMedo identity and game API bridge; seamless NP/P2P stays on the private server."""
import base64
from collections import deque
from datetime import datetime
import json
import random
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
# Reconnect policy mirrors BBport's own shadNet client (MarkForReconnect / TryReconnect).
RETRY_DELAYS = (2.0, 30.0)     # first retry, backoff ceiling (seconds)
RETRY_JITTER = 0.2             # +-20%, so machines do not reconnect in lockstep
KEEPALIVE_MS = (60000, 10000)  # SHAD_KEEPALIVE_IDLE_MS / SHAD_KEEPALIVE_INTERVAL_MS
KEEPALIVE_PROBES = 10          # Windows always sends 10 probes; match it elsewhere
CONNECT_TIMEOUT = 12
REPLY_TIMEOUT = 15
EVENT_HISTORY = 50
PERMANENT_REJECTIONS = frozenset({7, 8, 9})
INSPECT_LIMIT = 64 * 1024
ERROR_REPLY = b'{"ResKind":1,"MessageId":"PublicOnlineError"}'


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


class LoginRejected(ValueError):
    """The NP server answered the login with a non-zero result code."""
    def __init__(self, code, message):
        super().__init__(message); self.code = code


class ProtocolMismatch(ValueError):
    """The server speaks another shadNet protocol version; retrying cannot help."""


class ConnectionClosed(OSError):
    """The peer closed the NP connection (recv returned no data)."""


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _enable_keepalive(sock, idle_ms, interval_ms):
    """TCP keepalive like BBport's EnableKeepalive; detects half-open NP connections."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
        sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, idle_ms, interval_ms))
        return
    for name, value in (('TCP_KEEPIDLE', max(1, idle_ms // 1000)),
                        ('TCP_KEEPINTVL', max(1, interval_ms // 1000)),
                        ('TCP_KEEPCNT', KEEPALIVE_PROBES)):
        option = getattr(socket, name, None)
        if option is not None: sock.setsockopt(socket.IPPROTO_TCP, option, value)


def _error_numbers(error):
    return {name: getattr(error, name) for name in ('errno', 'winerror')
            if type(getattr(error, name, None)) is int}


def _drop_reason(error):
    """Classify a lost or failed NP connection for the event log. Never includes secrets."""
    numbers = _error_numbers(error)
    if isinstance(error, ConnectionClosed): return 'eof', {}
    if isinstance(error, ConnectionResetError): return 'reset', numbers
    if isinstance(error, TimeoutError): return 'timeout', numbers
    if isinstance(error, OSError):
        return 'oserror:' + str(numbers.get('errno', numbers.get('winerror', 'none'))), numbers
    if isinstance(error, LoginRejected): return 'rejected:' + str(error.code), {}
    if isinstance(error, ValueError): return 'protocol', {}
    return 'internal:' + type(error).__name__, {}


def _peer_text(address):
    host, port = address[0], address[1]
    return ('[' + host + ']' if ':' in host else host) + ':' + str(port)


def _close_socket(sock):
    if sock is None: return
    try: sock.shutdown(socket.SHUT_RDWR)
    except OSError: pass
    try: sock.close()
    except OSError: pass


class Account:
    """One shadNet login to the public NP server.

    keep_alive=True (game sessions): after the constructor returns, the reader thread is
    the only owner of the socket. After a drop it logs in again with BBport's backoff while
    callers see ``connected`` turn False. keep_alive=False (credential check): one login,
    the password is not kept.
    """
    def __init__(self, name, password, address=NP_SERVER, *, keep_alive=False,
                 retry_delays=RETRY_DELAYS, keepalive_ms=KEEPALIVE_MS, on_event=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,16}', name) or not password:
            raise ValueError('请填写 SuperMedo 的 Online ID 和密码。')
        self._address = address; self._keep_alive = bool(keep_alive)
        self._retry_delays = (float(retry_delays[0]), float(retry_delays[1]))
        self._keepalive_ms = (int(keepalive_ms[0]), int(keepalive_ms[1]))
        self._on_event = on_event
        # One condition guards state, both socket references, counters and events.
        # Blocking I/O never runs while it is held.
        self._lock = threading.Condition(); self._stop = threading.Event()
        self._state = 'connecting'; self._sock = None; self._pending = None
        # Only game sessions keep the password, in memory, for reconnecting.
        self._credentials = (name, password) if self._keep_alive else None
        self._events = deque(maxlen=EVENT_HISTORY)
        self._reconnects = 0; self._attempts = 0; self._message = ''; self._remote = None
        self._connected_at = None; self._disconnected_at = None; self._already_in_since = None
        self._last_disconnect = None
        self.thread = None; self.user_id = None; self.version_reported = False
        try:
            sock, user_id, version_reported, remote = self._handshake(name, password)
            with self._lock:
                if self._pending is sock: self._pending = None
                self._sock = sock; self.user_id = user_id; self.version_reported = version_reported
                self._remote = remote; self._connected_at = time.monotonic(); self._state = 'connected'
                event = self._record('connected', remote=remote, version_reported=version_reported,
                                     keep_alive=self._keep_alive)
                self._lock.notify_all()
            self._emit(event)  # Before the reader starts, so a drop can never be logged first.
            self.thread = threading.Thread(target=self._run, args=(sock,), name='public-np-reader', daemon=True)
            self.thread.start()
        except BaseException:
            self.close(); raise

    @property
    def connected(self): return self._state == 'connected'

    @property
    def state(self): return self._state

    def wait_connected(self, timeout):
        """True once connected; False when failed, closed or still reconnecting at timeout."""
        with self._lock:
            self._lock.wait_for(lambda: self._state not in ('connecting', 'reconnecting'), timeout)
            return self._state == 'connected'

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            return {
                'state': self._state, 'reconnects': self._reconnects, 'attempts': self._attempts,
                'since_disconnect_s': None if self._disconnected_at is None else round(now - self._disconnected_at, 1),
                'last_disconnect': dict(self._last_disconnect) if self._last_disconnect else None,
                'remote': self._remote,
                'already_in_for_s': None if self._already_in_since is None else round(now - self._already_in_since, 1),
                'message': self._message,
            }

    def events(self):
        with self._lock: return [dict(event) for event in self._events]

    def __repr__(self):
        return f'<Account state={self._state} reconnects={self._reconnects} remote={self._remote}>'

    # Wire helpers: used by the constructor (before the reader starts) and by the reader.
    def _receive(self, sock, length):
        raw = bytearray()
        while len(raw) < length:
            chunk = sock.recv(length - len(raw))
            if not chunk: raise ConnectionClosed('公共账号连接已断开。')
            raw.extend(chunk)
        return bytes(raw)

    def packet(self, sock):
        kind, command, length, request_id = struct.unpack('<BHIQ', self._receive(sock, 15))
        if kind not in (1, 2, 3) or not 15 <= length <= LIMIT:
            raise ValueError('公共账号数据包无效。')
        return kind, command, request_id, self._receive(sock, length - 15)

    def _send(self, sock, command, request_id, protobuf):
        payload = struct.pack('<I', len(protobuf)) + protobuf
        sock.sendall(struct.pack('<BHIQ', 0, command, 15 + len(payload), request_id) + payload)

    def _reply(self, sock, command, request_id):
        deadline = time.monotonic() + REPLY_TIMEOUT
        for _ in range(64):
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('公共账号响应超时。')
            sock.settimeout(remaining)
            kind, received_command, received_id, payload = self.packet(sock)
            if kind == 1 and received_command == command and received_id == request_id: return payload
        raise ValueError('公共账号响应数量异常。')

    def _open(self):
        """Connect with the attempt registered as pending, so close() can interrupt it."""
        last = None
        for family, kind, proto, _, target in socket.getaddrinfo(
                self._address[0], self._address[1], type=socket.SOCK_STREAM):
            sock = socket.socket(family, kind, proto)
            with self._lock:
                if self._stop.is_set():
                    sock.close(); raise ConnectionClosed('公共账号连接已关闭。')
                self._pending = sock
            try:
                sock.settimeout(CONNECT_TIMEOUT); sock.connect(target)
                break
            except OSError as error:
                self._discard(sock); last = error
                if self._stop.is_set(): raise ConnectionClosed('公共账号连接已关闭。') from None
        else:
            raise last if last is not None else OSError('公共账号服务地址无法解析。')
        try: _enable_keepalive(sock, *self._keepalive_ms)
        except Exception as error:  # Optional hardening; never a login failure.
            with self._lock:
                event = self._record('keepalive_failed', error=type(error).__name__, **_error_numbers(error))
            self._emit(event)
        try: remote = _peer_text(sock.getpeername())
        except (OSError, IndexError, TypeError): remote = None
        return sock, remote

    def _discard(self, sock):
        with self._lock:
            if self._pending is sock: self._pending = None
        _close_socket(sock)

    def _handshake(self, name, password):
        """Connect, log in and report the client version. Shared by first login and reconnects."""
        sock, remote = self._open()
        try:
            kind, command, request_id, payload = self.packet(sock)
            if kind != 3 or len(payload) < 4 or struct.unpack_from('<I', payload)[0] != 1:
                raise ProtocolMismatch('公共账号协议版本与当前客户端不一致。')
            login = text_field(1, name) + text_field(2, password)
            login += text_field(4, 'CUSA03173') + text_field(5, 'Bloodborne')
            self._send(sock, 0, 1, login)
            del login
            reply = self._reply(sock, 0, 1)
            if not reply or reply[0] != 0:
                code = reply[0] if reply else -1
                raise LoginRejected(code, ERRORS.get(code, '公共账号登录被拒绝，请核对账号状态。'))
            if len(reply) < 5 or struct.unpack_from('<I', reply, 1)[0] != len(reply) - 5:
                raise ValueError('公共账号响应不完整。')
            user_id = fields(reply[5:]).get(2)
            if type(user_id) is not int or not 0 < user_id < 2**64:
                raise ValueError('公共服没有返回有效账号身份。')
            self._send(sock, 13, 2, text_field(1, 'bbport/1.9.7'))
            version_reply = self._reply(sock, 13, 2)
            # Like BBport, version reporting is optional metadata, not an authentication gate.
            # Older compatible shadNet servers return Invalid for command 13.
            version_reported = bool(version_reply and version_reply[0] == 0)
            sock.settimeout(None)
            return sock, user_id, version_reported, remote
        except BaseException:
            self._discard(sock); raise

    # Reader thread: the only owner of the socket after the constructor returns.
    def _run(self, sock):
        while sock is not None:
            try:
                while True: self.packet(sock)  # Drain notifications; no friends or presence is logged.
            except Exception as error:
                reason, numbers = _drop_reason(error)
            try: sock = self._dropped(sock, reason, numbers)
            except Exception:
                # Never die silently while the bridge still trusts this account.
                with self._lock:
                    if self._state not in ('closed', 'failed'):
                        self._state = 'failed'; self._message = '公共账号连接线程异常退出。'
                        self._credentials = None
                        self._lock.notify_all()
                return

    def _dropped(self, sock, reason, numbers):
        now = time.monotonic()
        with self._lock:
            if self._sock is sock: self._sock = None
            event = None; retry = False
            if not self._stop.is_set() and self._state != 'closed':
                connected_for = None if self._connected_at is None else round(now - self._connected_at, 1)
                self._last_disconnect = {'at': _now(), 'reason': reason, 'connected_for_s': connected_for}
                retry = self._keep_alive and self._credentials is not None
                self._state = 'reconnecting' if retry else 'closed'
                self._disconnected_at = now
                event = self._record('drop', reason=reason, connected_for_s=connected_for,
                                     remote=self._remote, retry=retry, **numbers)
                self._lock.notify_all()
        _close_socket(sock)
        self._emit(event)
        return self._reconnect() if retry else None

    def _reconnect(self):
        delay, ceiling = self._retry_delays
        while True:
            if self._stop.wait(delay * random.uniform(1 - RETRY_JITTER, 1 + RETRY_JITTER)): return None
            with self._lock:
                credentials = self._credentials
            if credentials is None or self._stop.is_set(): return None
            code = None
            try:
                sock, user_id, version_reported, remote = self._handshake(*credentials)
            except LoginRejected as error:
                if error.code in PERMANENT_REJECTIONS:
                    return self._fail('rejected:' + str(error.code), str(error))
                code = error.code; reason, numbers = _drop_reason(error)
            except ProtocolMismatch as error:
                return self._fail('protocol_version', str(error))
            except Exception as error:
                reason, numbers = _drop_reason(error)
            else:
                return self._install(sock, user_id, version_reported, remote)
            finally:
                credentials = None
            if self._stop.is_set(): return None
            delay = min(delay * 2, ceiling)
            with self._lock:
                self._attempts += 1
                if code == 6:
                    # The old session can linger on the server until its keepalive expires.
                    if self._already_in_since is None: self._already_in_since = time.monotonic()
                elif code is not None:
                    self._already_in_since = None
                event = self._record('retry_failed', attempt=self._attempts, reason=reason,
                                     next_delay_s=round(delay, 2), **numbers)
            self._emit(event)

    def _install(self, sock, user_id, version_reported, remote):
        now = time.monotonic()
        with self._lock:
            if self._pending is sock: self._pending = None
            stopped = self._stop.is_set()
            same = user_id == self.user_id
            event = None
            if not stopped and same:
                down_for = None if self._disconnected_at is None else round(now - self._disconnected_at, 1)
                self._sock = sock; self._state = 'connected'; self._reconnects += 1
                event = self._record('reconnected', reconnects=self._reconnects,
                                     failed_attempts=self._attempts, down_for_s=down_for, remote=remote)
                self._attempts = 0; self._connected_at = now; self._disconnected_at = None
                self._already_in_since = None; self._remote = remote; self.version_reported = version_reported
                self._lock.notify_all()
        if stopped or not same:
            _close_socket(sock)
            return None if stopped else self._fail(
                'identity_changed', '重连后公共账号身份与本次游戏不一致，已停止转发公共内容。')
        self._emit(event)
        return sock

    def _fail(self, reason, message):
        with self._lock:
            if self._stop.is_set(): return None
            self._attempts += 1
            self._state = 'failed'; self._message = message; self._credentials = None
            event = self._record('failed', reason=reason, attempts=self._attempts, message=message)
            self._lock.notify_all()
        self._emit(event)
        return None

    def _record(self, kind, **values):
        """Append an event; the caller holds the lock. Values never contain credentials."""
        event = {'at': _now(), 'kind': kind}
        event.update((key, value) for key, value in values.items() if value is not None)
        self._events.append(event)
        return dict(event)

    def _emit(self, event):
        if event is None or self._on_event is None: return
        try: self._on_event(dict(event))
        except Exception: pass  # Diagnostics must never take down the reader.

    def close(self):
        self._stop.set()
        with self._lock:
            sockets = (self._sock, self._pending)
            self._sock = self._pending = None
            self._credentials = None
            # Interrupts a blocking recv/connect in the reader or in a constructor attempt.
            for sock in sockets: _close_socket(sock)
            event = None
            if self._state != 'closed' and self.thread is not None:
                event = self._record('closed', reconnects=self._reconnects)
            self._state = 'closed'
            self._lock.notify_all()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        self._emit(event)

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


def _reskind_error(headers, raw):
    """MessageId of a small JSON 2xx reply whose ResKind reports an error; None otherwise."""
    content_type = next((value for name, value in headers.items() if name.lower() == 'content-type'), '')
    if 'json' not in str(content_type).lower() or len(raw) > INSPECT_LIMIT: return None
    try: body = json.loads(raw)
    except (ValueError, TypeError): return None
    if not isinstance(body, dict) or body.get('ResKind', 0) == 0: return None
    return str(body.get('MessageId', ''))[:80]


class Bridge:
    def __init__(self, account, private_base, transport=public_request):
        self.account = account; self.transport = transport; self.login_ok = False
        self._stats = threading.Lock()  # Handler threads update the counters concurrently.
        self.failures = 0              # Cumulative; older state readers still use it.
        self.offline_rejections = 0    # Refused locally while the NP account was offline.
        self.upstream_failures = 0     # Upstream or forwarding failures while online.
        self.consecutive_failures = 0  # Upstream failures since the last successful reply.
        self.reskind_errors = 0        # 2xx JSON replies with ResKind != 0 (still forwarded).
        self.last_failure = None; self.last_reskind_error = None
        self.prefix = '/' + secrets.token_hex(24)
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self): self.handle_api()
            def do_POST(self): self.handle_api()
            def handle_api(self):
                reply = self.route()
                if reply is None: return
                try: self.respond(*reply)
                except OSError: pass  # The game closed its side; nothing upstream failed.
            def route(self):
                endpoint = None
                try:
                    if self.path == '/bb-eu/ss.info' and self.command == 'GET':
                        return 200, {}, bridge.info
                    if not self.path.startswith(bridge.prefix + '/'):
                        self.send_error(404); return None
                    path = self.path[len(bridge.prefix):]
                    if path == '/bb-eu/ss.info' and self.command == 'GET':
                        return 200, {}, bridge.info
                    endpoint = urlsplit(path).path
                    if not re.fullmatch(r'/(?:basic_utils|blood_messenger|tomb_messenger|wandering_ghost|chair_messenger|channel|messenger_shell|penalty)/[a-z_]+', endpoint):
                        self.send_error(404); return None
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= length <= LIMIT or self.headers.get('Transfer-Encoding'):
                        self.send_error(413); return None
                    # Read the body even when refusing: closing a Windows socket with unread
                    # input sends RST, and the game may then lose the 502 reply itself.
                    raw = self.rfile.read(length) if self.command == 'POST' else None
                    if not account.connected:
                        # Fail fast while the NP identity is offline or reconnecting; public
                        # requests are never forwarded without a live public login.
                        bridge._offline(endpoint)
                        return 502, {}, ERROR_REPLY
                    if endpoint == '/basic_utils/login':
                        body = json.loads(raw)
                        if not isinstance(body, dict) or 'PlatformAccountId' not in body:
                            raise ValueError('公共游戏登录请求缺少账号身份。')
                        body['PlatformAccountId'] = str(account.user_id)
                        raw = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
                    headers = {name: self.headers[name] for name in ('Content-Type', 'User-Agent', 'Accept', 'Cookie') if name in self.headers}
                    headers['Accept-Encoding'] = 'identity'
                    upstream = transport(path, raw, headers)
                    status, reply_headers, data = upstream
                    ok = 200 <= status < 300
                    if endpoint == '/basic_utils/login':
                        result = json.loads(data)
                        bridge.login_ok = (status == 200 and isinstance(result, dict) and result.get('ResKind') == 0
                                           and bool(result.get('SessionId')) and bool(result.get('UserId')))
                        ok = bridge.login_ok
                    bridge._replied(endpoint, status, reply_headers, data, ok)
                    return upstream
                except HTTPError as error:
                    bridge._failed('http', error.code, endpoint)
                    return error.code, {}, ERROR_REPLY
                except OSError:
                    bridge._failed('network', 502, endpoint)
                except (ValueError, TypeError):
                    bridge._failed('invalid', 502, endpoint)
                except Exception:
                    bridge._failed('internal', 502, endpoint)
                return 502, {}, ERROR_REPLY
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

    # Counters record only the endpoint path, never request bodies or headers.
    def _offline(self, endpoint):
        with self._stats:
            self.failures += 1; self.offline_rejections += 1
            self.last_failure = {'at': _now(), 'kind': 'offline', 'status': 502, 'endpoint': endpoint}

    def _failed(self, kind, status, endpoint):
        with self._stats:
            self.failures += 1; self.upstream_failures += 1; self.consecutive_failures += 1
            self.last_failure = {'at': _now(), 'kind': kind, 'status': status, 'endpoint': endpoint}

    def _replied(self, endpoint, status, headers, raw, ok):
        message = _reskind_error(headers, raw) if 200 <= status < 300 else None
        if message is not None:
            with self._stats:
                self.reskind_errors += 1
                self.last_reskind_error = {'at': _now(), 'endpoint': endpoint, 'MessageId': message}
        if not ok:
            self._failed('login' if endpoint == '/basic_utils/login' else 'status', status, endpoint)
            return
        with self._stats: self.consecutive_failures = 0

    def stats(self):
        with self._stats:
            return {'failures': self.failures, 'offline_rejections': self.offline_rejections,
                    'upstream_failures': self.upstream_failures,
                    'consecutive_failures': self.consecutive_failures,
                    'reskind_errors': self.reskind_errors,
                    'last_failure': dict(self.last_failure) if self.last_failure else None,
                    'last_reskind_error': dict(self.last_reskind_error) if self.last_reskind_error else None}

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
