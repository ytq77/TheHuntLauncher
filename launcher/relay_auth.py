"""Private NP credentials for one game child; no account files or public companion."""
import http.client
import json
import re
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit

ERROR = '私服认证失败，请核对账号、服务器状态及客户端/服务器版本。'
UPGRADE = '私服尚未启用 Relay v2 认证，请同时更新客户端和服务器。'
KEYS = ('BBSC_RELAY_AUTH', 'BBSC_RELAY_USER_ID', 'BBSC_RELAY_TOKEN')
PUBLIC_HOSTS = ('thehuntersdream.com', 'nodreamforthehunter.com', 'shadps4.net',
                'scej-network.jp', 'supermedo.com')
LIMIT = 8 * 1024 * 1024


def is_public(host):
    host = host.lower().rstrip('.')
    return any(host == domain or host.endswith('.' + domain) for domain in PUBLIC_HOSTS)


def varint(number):
    data = bytearray()
    while number >= 128:
        data.append((number & 127) | 128)
        number >>= 7
    data.append(number)
    return bytes(data)


def text_field(field, text):
    raw = text.encode('utf-8')
    return varint(field * 8 + 2) + varint(len(raw)) + raw


def fields(raw, singular=(1, 2)):
    """Strict bounded protobuf subset from the pinned NP schema."""
    position = 0
    def number():
        nonlocal position
        value = 0
        for shift in range(0, 70, 7):
            if position == len(raw): raise ValueError(ERROR)
            byte = raw[position]; position += 1
            if shift == 63 and byte > 1: raise ValueError(ERROR)
            value |= (byte & 127) << shift
            if byte < 128: return value
        raise ValueError(ERROR)
    result = {}
    while position < len(raw):
        key = number(); field, wire = key >> 3, key & 7
        if not field: raise ValueError(ERROR)
        if wire == 0:
            value = number()
        elif wire in (1, 2, 5):
            size = number() if wire == 2 else (8 if wire == 1 else 4)
            if size > len(raw) - position: raise ValueError(ERROR)
            value = raw[position:position + size]; position += size
        else: raise ValueError(ERROR)
        # LoginReply has repeated friend fields, but identity/token fields must be unique.
        if field in result and field in singular: raise ValueError(ERROR)
        result[field] = value
    return result


def retrieve(name, password, address):
    """Login(0), GetToken(39), Terminate(1), EOF; return only after close."""
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,16}', name) or not password:
            raise ValueError(ERROR)
        deadline = time.monotonic() + 15
        with socket.create_connection(address, timeout=10) as connection:
            def receive(count):
                raw = bytearray()
                while len(raw) < count:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise ValueError(ERROR)
                    connection.settimeout(remaining)
                    chunk = connection.recv(count - len(raw))
                    if not chunk: raise ValueError(ERROR)
                    raw.extend(chunk)
                return bytes(raw)
            def packet():
                kind, command, size, request = struct.unpack('<BHIQ', receive(15))
                if kind not in (1, 2, 3) or not 15 <= size <= LIMIT: raise ValueError(ERROR)
                return kind, command, request, receive(size - 15)
            def send(command, request, payload=b''):
                connection.sendall(struct.pack('<BHIQ', 0, command, 15 + len(payload), request) + payload)
            def reply(command, request, protobuf=True):
                for _ in range(64):
                    kind, got_command, got_request, raw = packet()
                    if kind == 2: continue
                    if (kind, got_command, got_request) != (1, command, request) or not raw or raw[0]:
                        raise ValueError(ERROR)
                    if not protobuf: return raw
                    if len(raw) < 5 or struct.unpack_from('<I', raw, 1)[0] != len(raw)-5:
                        raise ValueError(ERROR)
                    return fields(raw[5:], (1, 2, 3) if command == 39 else (1, 2))
                raise ValueError(ERROR)
            kind, _, _, raw = packet()
            if kind != 3 or len(raw) != 4 or struct.unpack('<I', raw)[0] != 1:
                raise ValueError(ERROR)
            login = (text_field(1, name) + text_field(2, password) +
                     text_field(4, 'CUSA03173') + text_field(5, 'Bloodborne'))
            send(0, 1, struct.pack('<I', len(login)) + login)
            identity = reply(0, 1).get(2)
            if type(identity) is not int or not 0 < identity <= 0xffffffff:
                raise ValueError(ERROR)
            send(39, 2)
            token_reply = reply(39, 2)
            token = token_reply.get(1)
            npid = token_reply.get(3)
            if (token_reply.get(2) != identity or not isinstance(token, bytes) or
                    not 0 < len(token) <= 4096 or b'\0' in token or
                    not isinstance(npid, bytes) or npid.decode('utf-8') != name):
                raise ValueError(ERROR)
            token = token.decode('utf-8')
            send(1, 3)
            reply(1, 3, protobuf=False)
            # Server acknowledges Terminate before disconnect; wait for the actual FIN.
            for _ in range(64):
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                trailing = connection.recv(1)
                if not trailing: return identity, token
            raise ValueError(ERROR)
    except (OSError, ValueError, TypeError, UnicodeError, struct.error):
        raise ValueError(ERROR) from None


def private_target(network):
    """Pin the configured NP/relay/status services to one trusted IPv4."""
    np = urlsplit('//' + network['shadnet'])
    relay = urlsplit('//' + network['relay'])
    webapi = urlsplit(network['webapi'])
    addresses = []
    for endpoint in (np, relay, webapi):
        found = {entry[4][0] for entry in socket.getaddrinfo(endpoint.hostname, None,
                                                          socket.AF_INET, socket.SOCK_STREAM)}
        if len(found) != 1: raise ValueError(ERROR)
        addresses.append(found.pop())
    if len(set(addresses)) != 1: raise ValueError(ERROR)
    return addresses[0], np.port, relay.port, webapi


def preflight(ip, webapi):
    """Native /status is a compatibility gate, not a credential or MAC proof."""
    port = webapi.port or (443 if webapi.scheme == 'https' else 80)
    if webapi.scheme == 'https':
        connection = http.client.HTTPSConnection(webapi.hostname, port, timeout=5,
                                                context=ssl.create_default_context())
        # Keep certificate/hostname verification while connecting to the pinned IPv4.
        connection._create_connection = lambda _address, timeout, source_address: socket.create_connection(
            (ip, port), timeout=timeout, source_address=source_address)
    else:
        connection = http.client.HTTPConnection(ip, port, timeout=5)
    try:
        connection.request('GET', '/status', headers={'Host': webapi.netloc})
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536: raise ValueError(UPGRADE)
        result = json.loads(raw)
        if not isinstance(result, dict) or type(result.get('relay_auth')) is not int or result['relay_auth'] != 2:
            raise ValueError(UPGRADE)
    except (OSError, ValueError, http.client.HTTPException):
        raise ValueError(UPGRADE) from None
    finally:
        connection.close()


def environment(network, name, password):
    """Called only by the backend immediately before creating the game child."""
    try:
        np_host = urlsplit('//' + network['shadnet']).hostname
        relay_host = urlsplit('//' + network['relay']).hostname
        if is_public(np_host) and is_public(relay_host):
            return {'BBSC_RELAY_AUTH': 'public'}
        if is_public(np_host) or is_public(relay_host): raise ValueError(ERROR)
        ip, np_port, relay_port, webapi = private_target(network)
        preflight(ip, webapi)
        identity, token = retrieve(name, password, (ip, np_port))
        return {'BBSC_RELAY_AUTH': 'private-v2', 'BBSC_RELAY_USER_ID': str(identity),
                'BBSC_RELAY_TOKEN': token, 'BBSC_RELAY': f'{ip}:{relay_port}',
                'BB_SHADNET_SERVER': f'{ip}:{np_port}',
                'BBSC_RELAY_HTTP_SOURCE': network['webapi'].rstrip('/'),
                'BBSC_RELAY_HTTP_ORIGIN': f'{webapi.scheme}://{ip}:' + str(
                    webapi.port or (443 if webapi.scheme == 'https' else 80))}
    except (OSError, TypeError, KeyError, ValueError) as error:
        raise ValueError(UPGRADE if str(error) == UPGRADE else ERROR) from None
