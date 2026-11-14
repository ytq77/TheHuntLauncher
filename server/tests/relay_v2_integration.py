#!/usr/bin/env python3
"""Opt-in wire integration against an ALREADY RUNNING disposable native shadNet.

Never starts a service, reads a save, accepts existing account credentials, or
injects native rooms. All accounts are newly registered synthetic users.
Passwords/tokens/registration secrets travel in memory and sockets, never argv
or output. Explicit --state-dir additionally authorizes reset/ban of ONLY those
new rows in state-dir/db/shadnet.db; other accounts are never updated/deleted.

Example (the caller owns starting/stopping the isolated native fixture):
  python -B relay_v2_integration.py --server-ip 127.0.0.1 --tcp-port 32413 \
    --web-port 32415 --relay-port 22444 --state-dir /tmp/native-relay-fixture
Or use --registration-key-file /tmp/fixture-key.txt (reset/ban then SKIP).
  python -B relay_v2_integration.py --self-test

Protocol sources: PROTOCOL.md; pinned shadNet src/protocol.h, shadnet.proto,
cmd_account.cpp, cmd_matching.cpp, proto_utils.h. In particular room commands
are 100/101/102/103, NOT the stale clientsample/types.h IDs 12/13/14/15.
Linux uses the independent Python peer. On Windows, --native-client-dll selects
the actual C++ Client Begin/Ack/Receive/Send via its existing ctypes adapter for
all authenticated peers. Malformed/server-negative payloads remain independent.
"""

import argparse
import base64
import configparser
from contextlib import closing
import hashlib
import hmac
import importlib.util
import ipaddress
import io
import json
from pathlib import Path
import secrets
import select
import socket
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


AUTH_DOMAIN = b"TheHunt relay auth v4 bidirectional visibility\0"
KEY_DOMAIN = b"TheHunt relay key v2\0"
MAX_FRAME = 65536
NP_HEADER = struct.Struct("<BHIQ")
LOGIN, TERMINATE, CREATE, GET_TOKEN = 0, 1, 2, 39
CONTEXT_START, CREATE_ROOM, JOIN_ROOM, LEAVE_ROOM = 100, 101, 102, 103
TITLE = "CUSA03173"  # worlds.cfg maps all Bloodborne regions to bloodborne.
MAP = 0x17010000
PLACEMENT_HEADER = "X-ShadPS4-Bloodborne-Host-Placement"
PLACEMENT = "1,17010000,43110000,c2e80000,42800000,c016cbe4,-241109"
# Native relay grants are 10000ms/max128 bytes; native client accepts a bounded
# <=20000ms TTL (bbsc_relay_auth.h). Check both limits at their respective boundary.
GRANT_TTL_MS, MAX_CLIENT_TTL_MS, MAX_PLACEMENT_BYTES = 10000, 20000, 128


class Failure(Exception):
    """Messages must describe checks, never include wire contents or secrets."""


def require(condition, message):
    if not condition:
        raise Failure(message)


def mac(key, message):
    return hmac.new(key, message, hashlib.sha256).digest()


def varint(value):
    require(type(value) is int and 0 <= value < 2**64, "invalid protobuf integer")
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def pb_int(field, value):
    return varint(field << 3) + varint(value)


def pb_bytes(field, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return varint((field << 3) | 2) + varint(len(value)) + value


def pb_fields(raw):
    """Decode used wire types, retaining repetitions for nested room replies."""
    pos = 0

    def number():
        nonlocal pos
        result = 0
        for shift in range(0, 70, 7):
            require(pos < len(raw), "truncated protobuf varint")
            byte = raw[pos]
            pos += 1
            require(shift != 63 or byte <= 1, "oversized protobuf varint")
            result |= (byte & 127) << shift
            if byte < 128:
                return result
        raise Failure("unterminated protobuf varint")

    result = {}
    while pos < len(raw):
        tag = number()
        field, wire = tag >> 3, tag & 7
        require(field > 0, "invalid protobuf field")
        if wire == 0:
            value = number()
        else:
            require(wire in (1, 2, 5), "unsupported protobuf wire type")
            size = number() if wire == 2 else (8 if wire == 1 else 4)
            require(size <= len(raw) - pos, "truncated protobuf field")
            value = raw[pos:pos + size]
            pos += size
        result.setdefault(field, []).append(value)
    return result


def pb_one(fields, field, default=None):
    values = fields.get(field, [])
    require(len(values) <= 1, "unexpected repeated scalar field")
    return values[0] if values else default


def blob(raw):
    return struct.pack("<I", len(raw)) + raw


def unblob(raw):
    require(len(raw) >= 4, "truncated NP protobuf blob")
    require(struct.unpack_from("<I", raw)[0] == len(raw) - 4,
            "NP protobuf blob length mismatch")
    return pb_fields(raw[4:])


def recv_exact(sock, size, deadline):
    out = bytearray()
    while len(out) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        sock.settimeout(remaining)
        part = sock.recv(size - len(out))
        if not part:
            raise EOFError
        out.extend(part)
    return bytes(out)


class NP:
    def __init__(self, sock, timeout):
        self.sock, self.timeout, self.counter = sock, timeout, 0
        kind, command, request, payload = self.packet(time.monotonic() + timeout)
        require((kind, command, request, payload) == (3, 0, 0, struct.pack("<I", 1)),
                "native NP protocol version/ServerInfo mismatch")

    @classmethod
    def connect(cls, address, timeout):
        sock = socket.create_connection(address, timeout=timeout)
        try:
            return cls(sock, timeout)
        except BaseException:
            sock.close()
            raise

    def packet(self, deadline):
        kind, command, size, request = NP_HEADER.unpack(recv_exact(self.sock, 15, deadline))
        require(kind in (1, 2, 3) and 15 <= size <= 8 * 1024 * 1024,
                "invalid native NP packet header")
        return kind, command, request, recv_exact(self.sock, size - 15, deadline)

    def call(self, command, body=b"", protobuf=True, expected=0):
        self.counter += 1
        payload = blob(body) if protobuf else body
        wire = NP_HEADER.pack(0, command, 15 + len(payload), self.counter) + payload
        self.sock.settimeout(self.timeout)
        self.sock.sendall(wire)
        deadline = time.monotonic() + self.timeout
        while True:
            kind, received_command, request, reply = self.packet(deadline)
            if kind == 2:
                continue  # Real member/presence notifications can precede replies.
            require(kind == 1 and received_command == command and request == self.counter,
                    "native NP reply command/request mismatch")
            require(bool(reply), "empty native NP reply")
            require(reply[0] == expected, "NP command %d rejected (code %d)" % (command, reply[0]))
            return reply[1:]

    def login(self, account, expected=0, title=TITLE):
        body = pb_bytes(1, account.name) + pb_bytes(2, account.password)
        body += pb_bytes(4, title) + pb_bytes(5, "Bloodborne")
        reply = self.call(LOGIN, body, expected=expected)
        if expected:
            return None
        user = pb_one(unblob(reply), 2)
        require(type(user) is int and 0 < user <= 0xFFFFFFFF, "invalid native Login user ID")
        return user

    def token(self, user):
        # CmdGetToken consumes NO protobuf, unlike the login and room commands.
        reply = unblob(self.call(GET_TOKEN, protobuf=False))
        require(pb_one(reply, 2) == user, "GetToken user differs from Login")
        token = pb_one(reply, 1)
        require(isinstance(token, bytes) and bool(token), "GetToken returned an empty token")
        token.decode("utf-8", errors="strict")
        return token

    def close(self):
        self.sock.close()


class Frames:
    """Incremental decoder: retains a partial prefix/body across polls."""
    def __init__(self):
        self.pending = bytearray()

    def pop(self):
        if len(self.pending) < 4:
            return None
        size = struct.unpack_from("<I", self.pending)[0]
        require(0 < size <= MAX_FRAME, "invalid relay frame length")
        if len(self.pending) < 4 + size:
            return None
        payload = bytes(self.pending[4:4 + size])
        del self.pending[:4 + size]
        return payload


def framed(payload):
    require(0 < len(payload) <= MAX_FRAME, "invalid outgoing relay frame length")
    return blob(payload)


def auth_payload(user, token, nonce):
    identity = struct.pack("<Q", user)
    suffix = nonce + identity
    return (b"THA4" + identity + mac(token, AUTH_DOMAIN + suffix),
            mac(token, KEY_DOMAIN + suffix))


def signed(key, kind, sequence, body):
    raw = kind + struct.pack("<Q", sequence) + body
    return raw + mac(key, raw)


class NativeFactory:
    """Load the client's existing ctypes test adapter; never alter its C++ source."""
    def __init__(self, dll_path):
        require(sys.platform == "win32", "--native-client-dll is Windows only")
        self.path = Path(dll_path).resolve(strict=True)
        helper = Path(__file__).resolve().parents[2] / "tests/test_relay_auth_abi.py"
        require(helper.is_file(), "native client ctypes test adapter is missing")
        spec = importlib.util.spec_from_file_location("relay_v2_native_abi", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.client_class = module.NativeClient
        self.calls = dict(begin=0, ack=0, receive=0, send=0)
        self.deliveries = dict(context=0, data=0, placement=0, cancellation=0)
        self.sessions = 0

    def create(self):
        peer = NativePeer(self, self.client_class(self.path))
        self.sessions += 1
        return peer

    def executed(self):
        return all(self.calls.values()) and all(self.deliveries.values())


class NativePeer:
    def __init__(self, factory, client):
        self.factory, self.client = factory, client

    def invoke(self, name, *args):
        try:
            result = getattr(self.client, name)(*args)
        except (ValueError, RuntimeError):
            raise Failure("native C++ Client %s rejected protocol input" % name) from None
        self.factory.calls[name] += 1
        if name == "receive":
            kind, raw = result
            require(kind in (1, 2, 3, 4), "native C++ Client returned unknown delivery kind")
            label = {1: "context", 2: "data", 3: "placement", 4: "placement"}[kind]
            if kind in (3, 4) and len(raw) >= 28 and struct.unpack_from("<I", raw, 24)[0] == 0:
                label = "cancellation"
            self.factory.deliveries[label] += 1
        return result

    def close(self):
        self.client.close()


class Relay:
    def __init__(self, address, timeout, native_factory=None):
        self.native = native_factory.create() if native_factory else None
        try:
            self.sock = socket.create_connection(address, timeout=timeout)
        except BaseException:
            if self.native:
                self.native.close()
            raise
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.timeout, self.decoder, self.closed = timeout, Frames(), False
        self.key, self.user = b"", 0
        self.incoming = self.outgoing = 0
        self.room = self.owner = self.peers = self.contexts = 0
        self.data = []
        self.placements, self.pending_placement, self.pending_generation = [], None, 0

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            payload = self.decoder.pop()
            if payload is not None:
                return payload
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            part = self.sock.recv(MAX_FRAME + 4)
            if not part:
                self.closed = True
                require(not self.decoder.pending, "relay EOF inside a partial frame")
                raise EOFError
            self.decoder.pending.extend(part)

    def challenge(self):
        payload = self.read(self.timeout)
        require(len(payload) == 36 and payload[:4] == b"THC2",
                "server does not speak relay v2; upgrade required (no legacy fallback)")
        return payload[4:]

    def write(self, payload, split=False):
        self.sock.settimeout(self.timeout)
        wire = framed(payload)
        if split:
            # Actually fragment prefix AND body; TCP is free to coalesce writes.
            for byte in wire:
                self.sock.sendall(bytes([byte]))
                time.sleep(0.001)
        else:
            self.sock.sendall(wire)

    def authenticate(self, account, split=False):
        nonce = self.challenge()
        proof, key = auth_payload(account.user, account.token, nonce)
        if getattr(self, "native", None):
            native_proof = self.native.invoke("begin", account.user, account.token, b"THC2" + nonce)
            require(native_proof == proof, "native C++ Begin disagrees with independent proof vector")
            proof = native_proof
        self.write(proof, split=split)
        ack = self.read(self.timeout)
        require(len(ack) == 44 and ack[:12] == b"THO4" + struct.pack("<Q", account.user),
                "invalid relay acknowledgement identity")
        require(hmac.compare_digest(ack[12:], mac(key, ack[:12])), "relay acknowledgement MAC failed")
        if getattr(self, "native", None):
            self.native.invoke("ack", ack)
        self.key, self.user = key, account.user

    def accept(self, payload):
        decoded = self.native.invoke("receive", payload) if getattr(self, "native", None) else None
        require(len(payload) >= 44 and payload[:4] in (b"THX4", b"THS2", b"THP2"),
                "unexpected relay server control/data")
        require(hmac.compare_digest(payload[-32:], mac(self.key, payload[:-32])),
                "relay server MAC failed")
        sequence = struct.unpack_from("<Q", payload, 4)[0]
        require(sequence == self.incoming + 1 and sequence < 2**64,
                "relay server sequence replay/order failure")
        self.incoming = sequence
        if payload[:4] == b"THP2":
            require(72 <= len(payload) <= 72 + MAX_PLACEMENT_BYTES, "invalid THP2 text size")
            if decoded:
                require(decoded[0] in (3, 4) and decoded[1] == payload[12:-32],
                        "native C++ pending placement decode mismatch")
            body = decoded[1] if decoded else payload[12:-32]
            generation, room, host, target, ttl = struct.unpack_from("<QQIII", body)
            text = body[28:]
            require(generation > 0 and room > 0 and
                    host > 0 and host != self.user and target == self.user and ttl <= MAX_CLIENT_TTL_MS,
                    "invalid THP2 generation/room/host/target/TTL")
            require((ttl == 0 and not text) or (ttl > 0 and self.room == 0 and bool(text)),
                    "THP2 attempted room admission or invalid pending cancellation")
            require(b"\0" not in text, "THP2 text contains NUL")
            try:
                text = text.decode("utf-8", errors="strict")
            except UnicodeError:
                raise Failure("THP2 text is not valid UTF-8") from None
            now = time.monotonic()
            placement = dict(generation=generation, room=room, host=host, target=target,
                             ttl=ttl, text=text, received=now, expires=now + ttl / 1000, wire=payload)
            ignored = (ttl > 0 and generation <= self.pending_generation) or (
                ttl == 0 and (self.pending_placement is None or
                              generation != self.pending_placement["generation"]))
            if decoded:
                require((decoded[0] == 4) == ignored, "native C++ pending generation ignore mismatch")
            if ignored:
                return
            if ttl:
                self.pending_generation = generation
                self.pending_placement = placement
            elif self.pending_placement is not None and all(
                    placement[field] == self.pending_placement[field] for field in ("generation", "room", "host", "target")):
                self.pending_placement = None
            self.placements.append(placement)
        elif payload[:4] == b"THX4":
            require(len(payload) >= 62, "invalid signed v4 context size")
            count = payload[29]
            require(count <= 16 and len(payload) == 62 + 31 * count,
                    "invalid signed v4 roster size")
            if decoded:
                require(decoded == (1, payload[12:29]), "native C++ context decode mismatch")
            body = decoded[1] if decoded else payload[12:29]
            room, user, owner, peers = struct.unpack("<QIIB", body)
            require(user == self.user and ((room == 0 and owner == 0 and peers == 0)
                                          or (room > 0 and owner > 0)), "invalid signed context identity")
            users, members, names = set(), set(), set()
            for index in range(count):
                uid, member, joined, visible, raw_name = struct.unpack_from("<IHQB16s", payload, 30 + 31 * index)
                name = raw_name.split(b"\0", 1)[0]
                require(uid > 0 and member > 0 and joined > 0 and visible <= 1 and name and
                        all(0x21 <= byte <= 0x7e for byte in name) and
                        raw_name[len(name):] == bytes(16 - len(name)), "invalid signed v4 roster member")
                require(uid not in users and member not in members and raw_name not in names,
                        "duplicate signed v4 roster identity")
                users.add(uid); members.add(member); names.add(raw_name)
            require((room == 0 and count == 0) or
                    (room > 0 and count == peers + 1 and self.user in users),
                    "signed v4 roster disagrees with context")
            self.room, self.owner, self.peers = room, owner, peers
            self.contexts += 1
            if room:
                self.pending_placement = None  # Broker metadata never grants gameplay membership.
        else:
            require(len(payload) >= 65, "truncated signed relay data")
            room, sender, owner = struct.unpack_from("<QII", payload, 12)
            inner = payload[28:-32]
            if decoded:
                expected = struct.pack("<QIIBI", room, self.user, owner, self.peers, sender) + inner
                require(decoded == (2, expected), "native C++ room data decode mismatch")
                inner = decoded[1][21:]
            require(room > 0 and room == self.room and owner == self.owner and 0 < sender != self.user,
                    "relay data escaped authenticated current context")
            require(inner.startswith(b"SHAD"), "relay delivered a non-SHAD packet")
            self.data.append((room, sender, owner, inner))

    def active_placement(self, now=None):
        now = time.monotonic() if now is None else now
        if self.pending_placement is not None and self.pending_placement["expires"] <= now:
            self.pending_placement = None
        return self.pending_placement

    def packet(self, inner, use_native=False):
        self.outgoing += 1
        packet = signed(self.key, b"THD2", self.outgoing, inner)
        if use_native and getattr(self, "native", None):
            native_packet = self.native.invoke("send", inner)
            require(native_packet == packet, "native C++ Send disagrees with independent frame/sequence")
            return native_packet
        return packet

    def close(self):
        self.closed = True
        self.sock.close()
        if getattr(self, "native", None):
            self.native.close()


class Account:
    def __init__(self, name):
        self.name, self.password = name, secrets.token_urlsafe(24)
        self.user, self.token, self.np, self.relay = 0, b"", None, None

    @property
    def legacy_user(self):
        # Deliberately distinct from private NP u32 IDs, valid JSON integer.
        return 7000000000 + self.user


def follow(sequence=1):
    # Packed FollowPacket: magic/type/kind/map, x/y/z/heading, sequence/lamp.
    return struct.pack("<4sBBI4fIi", b"SHAD", 0x20, 0, MAP, 1.0, 2.0, 3.0, 0.0, sequence, -1)


def life(user, kind=1, count=1):
    return struct.pack("<4sBIBI", b"SHAD", 0x23, user, kind, count)


def pvp(kind):
    return b"SHAD\x25" + struct.pack("<BBI", kind, 1, 1)


def host_packets():
    # World-flag v1 has five chunks of eight groups; use one complete chunk.
    flags = struct.pack("<4sBBIIBBBB", b"SHAD", 0x22, 1, MAP, 1, 0, 5, 1, 8) + bytes(1000)
    return [follow(), flags, b"SHAD\x24" + struct.pack("<5H", 1, 2, 3, 4, 5),
            b"SHAD\x26\x01" + struct.pack("<I", 12345),
            b"SHAD\x27" + struct.pack("<i", 12345), b"SHAD\x30{}"]


class Suite:
    def __init__(self, args, key):
        self.args, self.registration_key = args, key
        self.np_address = (args.server_ip, args.tcp_port)
        self.relay_address = (args.server_ip, args.relay_port)
        self.relays, self.accounts, self.passed, self.skipped = [], [], [], []
        self.current = "fixture readiness"
        self.native_factory = NativeFactory(args.native_client_dll) if getattr(args, "native_client_dll", None) else None

    def mark(self, label):
        self.passed.append(label)
        print("PASS " + label, flush=True)

    def pump(self, duration=0.05):
        deadline = time.monotonic() + duration
        while True:
            live = [r for r in self.relays if not r.closed]
            for relay in live:
                while True:
                    payload = relay.decoder.pop()
                    if payload is None:
                        break
                    relay.accept(payload)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            sockets = [r.sock for r in live]
            readable, _, _ = select.select(sockets, [], [], min(remaining, 0.05)) if sockets else ([], [], [])
            if not sockets:
                time.sleep(min(remaining, 0.01))
            for relay in live:
                if relay.sock not in readable:
                    continue
                try:
                    part = relay.sock.recv(MAX_FRAME + 4)
                except ConnectionError:
                    part = b""
                if not part:
                    require(not relay.decoder.pending, "relay closed inside partial server frame")
                    relay.closed = True
                else:
                    relay.decoder.pending.extend(part)

    def until(self, predicate, message, timeout=None):
        deadline = time.monotonic() + (self.args.timeout if timeout is None else timeout)
        while not predicate() and time.monotonic() < deadline:
            self.pump()
        require(predicate(), message)

    def connect_relay(self, account, split=False):
        relay = Relay(self.relay_address, self.args.timeout, self.native_factory)
        self.relays.append(relay)
        relay.authenticate(account, split=split)
        account.relay = relay
        self.until(lambda: relay.contexts > 0 or relay.closed, "missing initial signed context")
        require(not relay.closed, "authenticated relay immediately closed")
        return relay

    def replace_relay(self, account):
        if account.relay:
            account.relay.close()
        self.pump(0.1)  # Let native disconnected callback remove duplicate identity.
        return self.connect_relay(account)

    def counts(self):
        return {relay: len(relay.data) for relay in self.relays}

    def unchanged(self, counts):
        require(all(len(relay.data) == count for relay, count in counts.items()),
                "rejected/out-of-room traffic was forwarded")

    def rejected_auth(self, make_payload):
        before = self.counts()
        relay = Relay(self.relay_address, self.args.timeout)
        try:
            nonce = relay.challenge()
            relay.write(make_payload(nonce))
            try:
                relay.read(self.args.timeout)
            except (EOFError, ConnectionError):
                pass
            else:
                raise Failure("invalid relay admission received a frame instead of disconnect")
        finally:
            relay.close()
        self.pump(self.args.quiet_window)
        self.unchanged(before)

    def rejected_packet(self, account, payload, must_disconnect=True):
        before = self.counts()
        placement_counts = self.placement_counts()
        account.relay.write(payload)
        if must_disconnect:
            self.until(lambda: account.relay.closed, "invalid signed traffic not disconnected")
        self.pump(self.args.quiet_window)
        self.unchanged(before)
        self.no_placements(placement_counts)
        # Role rejection may silently drop the valid packet or close the peer.
        # MAC/framing/sequence violations above must close the bad connection.
        if not account.relay.closed:
            contexts = account.relay.contexts
            self.until(lambda: account.relay.contexts > contexts or account.relay.closed,
                       "role rejection stalled the live relay")

    def context(self, account, room, owner, peers):
        self.until(lambda: (account.relay.room, account.relay.owner, account.relay.peers) == (room, owner, peers)
                   or account.relay.closed, "native room/owner/peers context did not converge")
        require(not account.relay.closed, "relay closed during native context change")

    def route(self, source, inner, targets, expected_inner=None, native_send=True):
        self.pump(0.05)
        before = self.counts()
        live = [relay for relay in self.relays if not relay.closed]
        source.relay.write(source.relay.packet(inner, use_native=native_send))
        self.until(lambda: all(len(a.relay.data) > before[a.relay] for a in targets),
                   "valid native-room packet not delivered")
        self.pump(self.args.quiet_window)
        expected = inner if expected_inner is None else expected_inner
        target_relays = {a.relay for a in targets}
        for relay, count in before.items():
            require(len(relay.data) == count + (1 if relay in target_relays else 0),
                    "relay delivered to wrong room/member or duplicated a packet")
            if relay in target_relays:
                require(relay.data[-1] == (source.relay.room, source.user, source.relay.owner, expected),
                        "relay sender/owner/room/payload mismatch")
        require(not source.relay.closed and all(not a.relay.closed for a in targets),
                "valid transfer disconnected a peer")
        require(all(not relay.closed for relay in live), "valid transfer disconnected an unrelated peer")

    def create_room(self, account):
        body = pb_int(1, 1) + pb_int(2, 4) + pb_int(4, 1)
        reply = unblob(account.np.call(CREATE_ROOM, body))
        room = pb_one(reply, 1)
        require(type(room) is int and room > 0 and pb_one(reply, 3) == 1,
                "CreateRoom returned invalid room/world")
        details = pb_fields(pb_one(reply, 9, b""))
        require(pb_one(details, 3) == pb_one(details, 4) != 0,
                "CreateRoom creator is not native owner")
        return room

    def join(self, account, room):
        reply = unblob(account.np.call(JOIN_ROOM, pb_int(1, room) + pb_int(2, 1)))
        require(pb_one(reply, 1) == room, "JoinRoom reply differs from requested native room")

    def leave(self, account, room):
        reply = unblob(account.np.call(LEAVE_ROOM, pb_int(1, room) + pb_int(2, 1)))
        require(pb_one(reply, 1) == room, "LeaveRoom reply differs from native room")

    def placement_counts(self):
        return {relay: len(relay.placements) for relay in self.relays}

    def no_placements(self, counts):
        require(all(len(relay.placements) == count for relay, count in counts.items()),
                "THP2 leaked to an unselected or cancelled/expired target")

    def summon(self, action, body, account=None, placement=None, authorization=None):
        require(action in ("create", "get", "delete", "request"), "invalid test summon endpoint")
        headers = {"Content-Type": "application/json", "Connection": "close"}
        if account is not None:
            headers["Authorization"] = "Bearer " + account.token.decode("utf-8")
        if authorization is not None:
            headers["Authorization"] = authorization
        if placement is not None:
            headers[PLACEMENT_HEADER] = placement
        url = "http://%s:%d/summon_messenger/%s" % (self.args.server_ip, self.args.web_port, action)
        if action in ("get", "request"):
            url += "?user_id=%d" % body.get("UserId", 0)
        request = Request(url, data=json.dumps(body, separators=(",", ":")).encode("utf-8"), headers=headers)
        opener = build_opener(ProxyHandler({}))
        try:
            response = opener.open(request, timeout=self.args.timeout)
        except HTTPError as error:
            response = error  # Inspect status/JSON only, never log a body/header.
        with response:
            raw = response.read(256 * 1024 + 1)
            require(len(raw) <= 256 * 1024, "oversized summon response")
            status = response.code
        try:
            parsed = json.loads(raw)
        except ValueError:
            require(status >= 400, "successful summon response is not JSON")
            parsed = {}  # QHttpServerResponse(Forbidden) has no JSON body.
        require(isinstance(parsed, dict), "summon response is not an object")
        return status, parsed

    def summon_ok(self, action, body, account, placement=None):
        status, reply = self.summon(action, body, account, placement)
        require(status == 200 and reply.get("ResKind") == 0,
                "authenticated native summon %s failed (HTTP %d)" % (action, status))
        return reply

    def summon_denied(self, action, body, account=None, placement=None, authorization=None, expected=None,
                      allow_noop=False):
        before, placements = self.counts(), self.placement_counts()
        status, reply = self.summon(action, body, account, placement, authorization)
        noop = allow_noop and status == 200 and reply.get("ResKind") == 0
        if noop and action == "get":
            require(reply.get("SummonDataList") == [], "non-owner search returned private signs")
        require((status == expected if expected is not None else (status in (400, 401, 403, 409) or noop)),
                "unauthorized summon %s accepted (HTTP %d)" % (action, status))
        require(noop or reply.get("ResKind") != 0, "denied summon returned successful result")
        self.pump(self.args.quiet_window)
        self.unchanged(before)
        self.no_placements(placements)

    def expect_placement(self, target, host, room, before, data_before):
        self.until(lambda: len(target.relay.placements) > before.get(target.relay, 0) and
                   target.relay.placements[-1]["ttl"] > 0,
                   "native broker did not deliver THP2 to authenticated prejoin sign owner")
        self.pump(self.args.quiet_window)
        for relay, count in before.items():
            if relay is not target.relay:
                require(len(relay.placements) == count, "THP2 broadcast to unrelated same-map member")
        received = target.relay.placements[-1]
        require((received["room"], received["host"], received["target"], received["text"]) ==
                (room, host.user, target.user, PLACEMENT) and 0 < received["ttl"] <= GRANT_TTL_MS,
                "THP2 does not bind actual native host-room and private sign-owner identities")
        require((target.relay.room, target.relay.owner, target.relay.peers) == (0, 0, 0),
                "prejoin THP2 changed real native room membership")
        require(target.relay.active_placement() is not None, "fresh THP2 already expired")
        self.unchanged(data_before)
        return received

    def expect_cancel(self, target, grant, before, data_before):
        fields = ("generation", "room", "host", "target")
        offset = before[target.relay]
        self.until(lambda: any(p["ttl"] == 0 and all(p[f] == grant[f] for f in fields)
                               for p in target.relay.placements[offset:]),
                   "already-delivered THP2 was not cancelled on its next shared signed control stream")
        self.pump(self.args.quiet_window)
        cancelled = target.relay.placements[offset:]
        require(len(cancelled) == 1 and cancelled[0]["ttl"] == 0 and cancelled[0]["text"] == "" and
                all(cancelled[0][f] == grant[f] for f in fields), "THP2 cancellation metadata mismatch/duplicate")
        require(target.relay.active_placement() is None, "matching cancelled THP2 remained active")
        for relay, count in before.items():
            if relay is not target.relay:
                require(len(relay.placements) == count, "THP2 cancellation leaked to another recipient")
        require((target.relay.room, target.relay.owner, target.relay.peers) == (0, 0, 0),
                "THP2 cancellation modified native room membership")
        self.unchanged(data_before)

    def prejoin_http(self, host, target, bystander, outsider, outsider_room):
        """Real native authenticated HTTP -> broker -> recipient's v2 socket."""
        self.current = "HTTP authenticated sign ownership and THP2 prejoin"
        remote_map = MAP + 0x10000

        def advert(account, tag, word):
            return dict(MessageId="SummonDataCreateRequest", SessionId=account.name + tag,
                        UserId=account.legacy_user, CharaId=1000000 + account.user,
                        AreaId=remote_map, AreaRegionId=230100, ChannelId=0, MatchingLevel=46,
                        SummonData="synthetic-placement-sign", SummonDataVersion=3,
                        SummonMethod=0, SummonType=0, SummonWord=word, PosX=143, PosY=-116, PosZ=-87)

        def search_for(sign):
            return dict(MessageId="SummonDataGetListRequest", SessionId=host.name + "-search",
                        UserId=host.legacy_user, AreaId=MAP, AreaRegionId=230100, ChannelId=0,
                        MatchingLevel=46, SummonDataVersion=3, SummonMethod=0,
                        SummonTypeList=[{"SummonType": 0}], SummonWord=sign["SummonWord"],
                        DistanceThreshold=100, GetMaxCount=20, PosX=143, PosY=-116, PosZ=-87)

        def claim_for(sign):
            return dict(MessageId="SummonDataSummonRequest", SessionId=sign["SessionId"],
                        UserId=host.legacy_user, TargetUserId=sign["UserId"], TargetCharaId=sign["CharaId"],
                        HostData="synthetic-host-data")

        def removal(sign, **flags):
            return dict(MessageId="SummonDataRemoveRequest", SessionId=sign["SessionId"],
                        UserId=sign["UserId"], **flags)

        # Native order: candidate search, then CreateRoom, then claim. A remote
        # sign exercises the location-spoof path without granting placement yet.
        self.current = "native no-room search before CreateRoom and owner claim"
        first_sign = advert(target, "-first-join", target.name + "-first-join")
        first_sign["SummonData"] = base64.b64encode(bytes(0xE0)).decode("ascii")
        first_search, first_claim = search_for(first_sign), claim_for(first_sign)
        self.summon_ok("create", first_sign, target)
        before, data_before = self.placement_counts(), self.counts()
        for searcher in (host, bystander):
            query = dict(first_search, UserId=searcher.legacy_user, SessionId=searcher.name + "-search")
            candidates = self.summon_ok("get", query, searcher, PLACEMENT)["SummonDataList"]
            require(len(candidates) == 1 and candidates[0]["SessionId"] == first_sign["SessionId"] and
                    candidates[0]["AreaId"] == MAP and
                    base64.b64decode(candidates[0]["SummonData"])[0x79] == 1,
                    "no-room /get did not return a usable, unreserved cross-map candidate")
        self.summon_denied("request", first_claim, host, placement=PLACEMENT, expected=403)
        self.no_placements(before)
        self.unchanged(data_before)
        for account in (host, target, bystander):
            self.context(account, 0, 0, 0)

        # A valid Bearer does not make an account online or start a BB context.
        probe = self.register(target.name + "p", start_game=False)
        self.summon_denied("get", first_search, probe, placement=PLACEMENT, allow_noop=True)
        self.login_game(probe, start_context=False)
        self.summon_denied("get", first_search, probe, placement=PLACEMENT, allow_noop=True)
        probe.np.call(TERMINATE, protobuf=False)
        require(probe.np.sock.recv(1) == b"", "probe NP session did not close")
        probe.np.close()
        # A literal title equal to the room key is not a mapped Bloodborne SKU.
        self.login_game(probe, title="bloodborne")
        self.summon_denied("get", first_search, probe, placement=PLACEMENT, allow_noop=True)

        # Try real guest membership and ambiguity while a matching sign exists.
        self.join(bystander, outsider_room)
        self.context(bystander, outsider_room, outsider.user, 1)
        self.summon_denied("get", first_search, bystander, placement=PLACEMENT, allow_noop=True)
        self.summon_denied("request", first_claim, bystander, placement=PLACEMENT, expected=403)
        second_room = self.create_room(bystander)
        self.context(bystander, 0, 0, 0)
        self.summon_denied("get", first_search, bystander, placement=PLACEMENT, allow_noop=True)
        self.summon_denied("request", first_claim, bystander, placement=PLACEMENT, expected=403)
        self.leave(bystander, second_room)
        self.leave(bystander, outsider_room)
        self.context(outsider, outsider_room, outsider.user, 0)

        room = self.create_room(host)
        self.context(host, room, host.user, 0)
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("request", first_claim, host, PLACEMENT)
        first_hint = self.expect_placement(target, host, room, before, data_before)
        old_map = self.summon_ok("create", first_sign, target)
        require("HostData" not in old_map, "cross-map claim delivered before the guest re-advertised at the host map")
        delivered = self.summon_ok("create", dict(first_sign, AreaId=MAP), target)
        require(delivered.get("MessageId") == "SummonDataCreateResponse" and
                all(delivered.get(field) == first_claim[field]
                    for field in ("UserId", "TargetUserId", "TargetCharaId", "HostData")),
                "owner claim was not delivered after re-advertising at the host map")
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("delete", removal(first_sign, Force=True), target)
        self.expect_cancel(target, first_hint, before, data_before)
        self.mark("real query /get with zero rooms is read-only; native CreateRoom then owner /request grants THP2 and delivers claim after AreaId refresh")
        self.mark("offline, unstarted/wrong NP context, native guest and ambiguous membership cannot search; only owner can claim")

        self.current = "HTTP authenticated sign ownership and THP2 prejoin"
        sign = advert(target, "-auth", target.name)
        search, claim = search_for(sign), claim_for(sign)
        # Test all FOUR native endpoints, including both forced cleanup paths.
        bodies = [("create", sign), ("get", search), ("request", claim),
                  ("delete", removal(sign)), ("delete", removal(sign, Force=True)),
                  ("delete", removal(sign, SeamlessLeave=True))]
        for action, body in bodies:
            for authorization in (None, "Bearer invalid-synthetic-bearer", "Basic invalid-scheme"):
                self.summon_denied(action, body, placement=PLACEMENT,
                                   authorization=authorization, expected=401)
        self.mark("all four private summon endpoints/forced cleanup require a valid Bearer")

        self.summon_ok("create", sign, target)
        # Another sign on the TARGET'S map is excluded by the broker word filter.
        other_sign = advert(bystander, "-bystander", bystander.name)
        self.summon_ok("create", other_sign, bystander)
        for attacker in (bystander, outsider):
            self.summon_denied("create", dict(sign, SummonData="attempted-overwrite"), attacker)
            for flags in ({}, {"Force": True}, {"SeamlessLeave": True}):
                self.summon_denied("delete", removal(sign, **flags), attacker)
        # The real owner can refresh its hybrid-ID sign with its private bearer.
        self.summon_ok("create", sign, target)
        # A header-free search inspects the surviving original record, without
        # creating a cross-map preparation. Public/game aliases stay intact.
        visible = self.summon_ok("get", search, host)
        listed = visible.get("SummonDataList")
        require(isinstance(listed, list) and len(listed) == 1 and
                all(listed[0].get(field) == sign[field]
                    for field in ("SessionId", "UserId", "CharaId", "SummonData")),
                "native broker overwrote/deleted hybrid sign or replaced legacy identity with private ID")
        # A zero-room account can search, but knowing the host's legacy UserId
        # does not give it native owner authority to claim.
        self.summon_denied("request", claim, bystander, placement=PLACEMENT)
        self.mark("hybrid body UserId preserved; foreign Bearer cannot overwrite/delete/force-clean or impersonate host")

        # Invalid placement cannot produce any grant, regardless of whether the
        # native HTTP route rejects the whole request or ignores the bad hint.
        for bad in ("0,invalid", "1,00000000,43110000,c2e80000,42800000,c016cbe4,-241109", "x" * 129):
            before, data_before = self.placement_counts(), self.counts()
            status, _ = self.summon("get", search, host, bad)
            require(status in (200, 400), "invalid placement failed with unexpected HTTP status")
            self.pump(self.args.quiet_window)
            self.no_placements(before)
            self.unchanged(data_before)

        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("get", search, host, PLACEMENT)
        first = self.expect_placement(target, host, room, before, data_before)
        for changes in ({"TargetUserId": target.user}, {"TargetUserId": bystander.legacy_user},
                        {"TargetCharaId": sign["CharaId"] + 1}):
            self.summon_denied("request", dict(claim, **changes), host,
                               placement=PLACEMENT, allow_noop=True)
        # The sign is now selected by THIS private host. Another actual room
        # owner cannot hijack it by copying the host's legacy body identity.
        self.summon_denied("request", claim, outsider, placement=PLACEMENT, allow_noop=True)
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("request", claim, host, PLACEMENT)
        claimed = self.expect_placement(target, host, room, before, data_before)
        require(claimed["generation"] > first["generation"], "new native claim reused pending generation")
        # No response header is read anywhere: the assertion uses only THP2.
        self.mark("native host search/claim delivers THP2 only to selected no-room sign owner; no same-map broadcast/header reliance")
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("delete", removal(sign, Force=True), target)
        self.expect_cancel(target, claimed, before, data_before)
        self.summon_ok("delete", removal(other_sign, SeamlessLeave=True), bystander)
        self.mark("owner sign delete immediately cancels already-delivered THP2 with matching signed generation")

        leave_sign = advert(target, "-delivered-leave", target.name + "-delivered-leave")
        self.summon_ok("create", leave_sign, target)
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("get", search_for(leave_sign), host, PLACEMENT)
        leave_hint = self.expect_placement(target, host, room, before, data_before)
        before, data_before = self.placement_counts(), self.counts()
        self.leave(host, room)
        self.context(host, 0, 0, 0)
        self.expect_cancel(target, leave_hint, before, data_before)
        self.summon_ok("delete", removal(leave_sign, Force=True), target)
        room = self.create_room(host)
        self.context(host, room, host.user, 0)
        self.mark("native host LeaveRoom immediately cancels already-delivered THP2 without admitting a room")

        # A received signed THP2 is not a client permission to assert controls.
        tampered = bytearray(claimed["wire"])
        tampered[32] ^= 1  # target identity, MAC deliberately unchanged
        self.rejected_packet(target, bytes(tampered))
        self.replace_relay(target)
        self.rejected_packet(target, claimed["wire"])
        self.replace_relay(target)
        self.mark("reflected/tampered server THP2 cannot be submitted as client admission/control")

        def queued_offline(tag):
            target.relay.close()
            self.pump(0.1)
            queued_sign = advert(target, tag, target.name + tag)
            self.summon_ok("create", queued_sign, target)
            counts, data_counts = self.placement_counts(), self.counts()
            self.summon_ok("get", search_for(queued_sign), host, PLACEMENT)
            self.pump(self.args.quiet_window)
            self.no_placements(counts)
            self.unchanged(data_counts)
            return queued_sign

        def reconnect_without_stale_grant():
            self.replace_relay(target)
            counts = self.placement_counts()
            # Include a THP2 coalesced with the first context during Connect.
            require(not target.relay.placements, "cancelled/expired THP2 arrived at reconnect")
            self.pump(self.args.quiet_window)
            self.no_placements(counts)
            self.context(target, 0, 0, 0)

        offline_positive = queued_offline("-offline-positive")
        before, data_before = self.placement_counts(), self.counts()
        self.replace_relay(target)
        offline_hint = self.expect_placement(target, host, room, before, data_before)
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("delete", removal(offline_positive, Force=True), target)
        self.expect_cancel(target, offline_hint, before, data_before)
        self.mark("broker grant queued without target relay is delivered on fresh authenticated prejoin connection")

        deleted = queued_offline("-delete")
        self.summon_ok("delete", removal(deleted, Force=True), target)
        reconnect_without_stale_grant()

        departed = queued_offline("-departure")
        self.leave(host, room)
        self.context(host, 0, 0, 0)
        reconnect_without_stale_grant()
        self.summon_ok("delete", removal(departed, Force=True), target)
        room = self.create_room(host)
        self.context(host, room, host.user, 0)

        expired = queued_offline("-expiry")
        self.pump(GRANT_TTL_MS / 1000 + 0.5)
        reconnect_without_stale_grant()
        self.summon_ok("delete", removal(expired, Force=True), target)
        self.mark("undelivered THP2 invalidated by owner delete, real host LeaveRoom, and native 10s TTL before reconnect")

        if self.args.state_dir is not None:
            reset_sign = queued_offline("-reset")
            old_token = target.token
            target.token = self.revoke(target)
            self.rejected_auth(lambda nonce: auth_payload(target.user, old_token, nonce)[0])
            self.revoked_http(target, old_token)
            reconnect_without_stale_grant()
            self.summon_ok("delete", removal(reset_sign, Force=True), target)
            self.mark("pending THP2 cannot survive selected sign owner's synthetic token reset")

        # Positive control after cancellations: no permanent ban of same-map or
        # same-account recruitment, and fresh generations are monotonic.
        fresh = advert(target, "-fresh", target.name + "-fresh")
        self.summon_ok("create", fresh, target)
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("get", search_for(fresh), host, PLACEMENT)
        last = self.expect_placement(target, host, room, before, data_before)
        require(last["generation"] > claimed["generation"], "fresh grant generation did not advance")
        before, data_before = self.placement_counts(), self.counts()
        self.summon_ok("delete", removal(fresh, Force=True), target)
        self.expect_cancel(target, last, before, data_before)
        return room

    def register(self, name, start_game=True):
        account = Account(name)
        connection = NP.connect(self.np_address, self.args.timeout)
        try:
            body = pb_bytes(1, account.name) + pb_bytes(2, account.password)
            body += pb_bytes(4, account.name + "@relay-v2.invalid") + pb_bytes(5, self.registration_key)
            require(connection.call(CREATE, body) == b"", "unexpected registration reply")
            self.accounts.append(account)
            account.user = connection.login(account)
            account.token = connection.token(account.user)
            connection.call(TERMINATE, protobuf=False)
            connection.sock.settimeout(self.args.timeout)
            require(connection.sock.recv(1)==b"", "native Terminate acknowledged without closing session")
        finally:
            connection.close()
        if start_game:
            self.login_game(account)
        return account

    def login_game(self, account, title=TITLE, start_context=True):
        # The companion connection has ended before the game-style NP login.
        deadline = time.monotonic() + self.args.timeout
        while True:
            game = NP.connect(self.np_address, self.args.timeout)
            try:
                user = game.login(account, title=title)
            except Failure as error:
                game.close()
                if str(error) != "NP command 0 rejected (code 6)" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)
                continue
            account.np = game
            require(user == account.user and game.token(user) == account.token,
                    "companion/game identity or token mismatch")
            if start_context:
                require(game.call(CONTEXT_START, pb_int(1, 1)) == b"", "unexpected ContextStart reply")
            return

    def revoke(self, account, ban=False):
        # No account INSERT and no native room mutation: only a guarded update
        # to a row just registered and authenticated by THIS process.
        require(self.args.state_dir is not None and account in self.accounts,
                "reset/ban requires explicit synthetic state directory")
        root = self.args.state_dir.resolve(strict=True)
        database = (root / "db/shadnet.db").resolve(strict=True)
        require(database.is_relative_to(root), "fixture database escapes state directory")
        replacement = secrets.token_hex(32).encode("ascii")
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=3)) as db, db:
            row = db.execute("SELECT username, token, banned FROM account WHERE user_id=?",
                             (account.user,)).fetchone()
            require(row == (account.name, account.token.decode("utf-8"), 0),
                    "state database does not match newly created synthetic account")
            result = db.execute("UPDATE account SET token=?, banned=? WHERE user_id=? AND username=? AND token=? AND banned=0",
                                (replacement.decode("ascii"), int(ban), account.user,
                                 account.name, account.token.decode("utf-8")))
            require(result.rowcount == 1, "synthetic token revoke did not update exactly one row")
        return replacement

    def revoked_http(self, account, token):
        create = dict(MessageId="SummonDataCreateRequest", SessionId=account.name + "-revoked",
                      UserId=account.legacy_user, AreaId=MAP, AreaRegionId=230100,
                      SummonData="synthetic-revoked", SummonDataVersion=3, SummonType=0, MatchingLevel=46)
        for action in ("create", "get", "delete", "request"):
            self.summon_denied(action, create, authorization="Bearer " + token.decode("utf-8"), expected=401)

    def run(self):
        # A status check is readiness only; the later authenticated operations
        # prove actual integration. Disable environment proxies for loopback.
        url = "http://%s:%d/status" % (self.args.server_ip, self.args.web_port)
        with build_opener(ProxyHandler({})).open(url, timeout=self.args.timeout) as response:
            raw = response.read(8193)
            require(len(raw) <= 8192, "oversized native WebAPI health reply")
            status = json.loads(raw)
            require(status.get("ok") is True and status.get("service") == "shadnet-webapi",
                    "native WebAPI not ready")
            require(status.get("relay_auth") == 2,
                    "native relay v2 readiness missing; upgrade required (no legacy fallback)")
        self.mark("native NP/WebAPI explicit fixture readiness")
        self.current = "synthetic registration and companion/game NP login"
        prefix = "rv2" + secrets.token_hex(4)
        host, guest, member, outsider = [self.register(prefix + str(i)) for i in range(4)]
        self.mark("actual Registration/Login/GetToken/Terminate/Login/ContextStart; identity matched")
        for i, account in enumerate(self.accounts):
            self.connect_relay(account, split=i == 0)
            self.context(account, 0, 0, 0)
        self.mark("v2 nonce/auth/ack/MAC and initial empty context; partial TCP reads")

        self.current = "admission and no-room rejection"
        invalid_login = NP.connect(self.np_address, self.args.timeout)
        wrong = Account(host.name)
        try:
            invalid_login.login(wrong, expected=8)
        finally:
            invalid_login.close()
        self.rejected_auth(lambda nonce: auth_payload(host.user, b"incorrect-synthetic-token", nonce)[0])
        self.rejected_auth(lambda nonce: auth_payload(host.user, host.token, nonce)[0][:-1] +
                           bytes([auth_payload(host.user, host.token, nonce)[0][-1] ^ 1]))
        self.rejected_auth(lambda nonce: auth_payload(0, host.token, nonce)[0])
        self.rejected_auth(lambda nonce: auth_payload(2**32, host.token, nonce)[0])
        self.rejected_auth(lambda nonce: auth_payload(0xFFFFFFFF, host.token, nonce)[0])
        self.rejected_auth(lambda nonce: auth_payload(host.user, host.token, nonce)[0])  # duplicate live identity
        old = Relay(self.relay_address, self.args.timeout)
        try:
            old_nonce = old.challenge()
            old_proof = auth_payload(host.user, host.token, old_nonce)[0]
        finally:
            old.close()

        def replay_proof(nonce):
            require(nonce != old_nonce, "server reused an admission nonce")
            return old_proof

        self.rejected_auth(replay_proof)
        self.rejected_auth(lambda _: follow())  # old unauthenticated legacy packet
        before = self.counts()
        host.relay.write(host.relay.packet(follow()))
        self.pump(self.args.quiet_window)
        self.unchanged(before)
        require(not host.relay.closed, "authenticated no-room peer unexpectedly closed")
        self.replace_relay(host)  # Raw negative sequence must not desync a C++ Client tx counter.
        self.mark("bad password/token/proof/account/range/duplicate/nonce replay and legacy admission rejected; no-room denied")

        self.current = "real native rooms and roles"
        room_b = self.create_room(outsider)
        self.context(outsider, room_b, outsider.user, 0)
        room_a = self.prejoin_http(host, guest, member, outsider, room_b)
        require(room_a != room_b, "native room IDs collided")
        self.current = "real native rooms and roles"
        self.join(guest, room_a)
        self.join(member, room_a)
        for account in (host, guest, member):
            self.context(account, room_a, host.user, 2)
        self.context(outsider, room_b, outsider.user, 0)
        for inner in host_packets():
            self.route(host, inner, [guest, member])
        self.route(outsider, follow(), [])  # same map/world; no affiliated recipient
        self.route(host, pvp(0), [guest, member])
        self.route(guest, pvp(1), [host])  # a third same-room guest must not receive it
        self.route(guest, life(host.user), [host, member], expected_inner=life(guest.user), native_send=False)
        self.mark("same-room host/life/PvP routing, server-owned sender/life ID, cross-room same-map isolation")

        # Every malformed/forged frame is sent on a fresh authenticated session.
        self.current = "host spoof/control/tamper/replay rejection"
        host_only = host_packets() + [pvp(0)]
        for inner in host_only:
            self.rejected_packet(guest, guest.relay.packet(inner), must_disconnect=False)
            self.replace_relay(guest)
            self.context(guest, room_a, host.user, 2)
        self.mark("guest cannot send any of the six host-only types or PvP host-state")
        attacks = [b"SHAD\x40" + struct.pack("<II", host.user, host.user) + follow(),
                   b"SHAD\x7f\x01", b"SHAD\x01", b"SHAD\x23", b"SHAD\x25\x02\0" + bytes(4)]
        for inner in attacks:
            self.rejected_packet(guest, guest.relay.packet(inner))
            self.replace_relay(guest)
        control = signed(guest.relay.key, b"THX4", 1,
                         struct.pack("<QIIB", room_a, guest.user, guest.user, 2))
        self.rejected_packet(guest, control)
        self.replace_relay(guest)
        packet = bytearray(guest.relay.packet(life(guest.user)))
        packet[12 + 5] ^= 1  # Change authenticated inner bytes without recalculating MAC.
        self.rejected_packet(guest, bytes(packet))
        self.replace_relay(guest)
        self.route(guest, life(guest.user), [host, member])
        replay = signed(guest.relay.key, b"THD2", guest.relay.outgoing, life(guest.user))
        self.rejected_packet(guest, replay)
        self.replace_relay(guest)
        zero = signed(guest.relay.key, b"THD2", 0, life(guest.user))
        self.rejected_packet(guest, zero)
        self.replace_relay(guest)
        self.mark("legacy identity envelope/control/unknown/truncated/invalid-kind spoof, tamper, sequence replay/zero rejected")

        self.current = "leave/rejoin and owner migration"
        self.leave(guest, room_a)
        self.context(guest, 0, 0, 0)
        before = self.counts()
        guest.relay.write(guest.relay.packet(life(guest.user)))
        self.pump(self.args.quiet_window)
        self.unchanged(before)
        self.replace_relay(guest)  # Fresh native tx state after the deliberately raw no-room probe.
        self.context(host, room_a, host.user, 1)
        self.route(host, follow(2), [member])
        self.join(guest, room_a)
        self.context(guest, room_a, host.user, 2)
        self.route(host, follow(3), [guest, member])
        self.leave(host, room_a)
        self.context(host, 0, 0, 0)
        self.until(lambda: member.relay.owner == guest.relay.owner and member.relay.owner in (member.user, guest.user),
                   "native room owner migration did not arrive")
        successor = member if member.relay.owner == member.user else guest
        remaining = guest if successor is member else member
        self.context(successor, room_a, successor.user, 1)
        self.join(host, room_a)
        self.context(host, room_a, successor.user, 2)
        self.route(successor, follow(4), [host, remaining])
        self.rejected_packet(host, host.relay.packet(follow(5)), must_disconnect=False)
        self.replace_relay(host)
        self.route(host, pvp(1), [successor])
        self.mark("LeaveRoom clears context/delivery; JoinRoom restores; native owner migration and role swap enforced")

        self.current = "ambiguous native membership"
        # Native handlers currently permit a second CreateRoom without leaving
        # the first. Use that REAL NP path to exercise ambiguity, never inject it.
        ambiguous = remaining
        ambiguous_room = self.create_room(ambiguous)
        self.context(ambiguous, 0, 0, 0)
        before = self.counts()
        ambiguous.relay.write(ambiguous.relay.packet(life(ambiguous.user)))
        self.pump(self.args.quiet_window)
        self.unchanged(before)
        targets = [a for a in (host, guest, member) if a not in (successor, ambiguous)]
        self.route(successor, follow(6), targets)
        self.leave(ambiguous, ambiguous_room)
        self.context(ambiguous, room_a, successor.user, 2)
        self.replace_relay(ambiguous)
        self.mark("multiple actual NP rooms fail closed and recover after native LeaveRoom")

        self.current = "token reset/ban revocation"
        if self.args.state_dir is None:
            self.skipped.append("reset/ban revocation: requires explicit --state-dir")
        else:
            revoked = remaining  # Preserve the native successor as live control.
            old_token = revoked.token
            replacement = self.revoke(revoked)
            self.until(lambda: revoked.relay.closed, "token reset did not revoke established relay")
            self.rejected_auth(lambda nonce: auth_payload(revoked.user, old_token, nonce)[0])
            self.revoked_http(revoked, old_token)
            revoked.token = replacement
            self.replace_relay(revoked)
            self.context(revoked, room_a, successor.user, 2)
            self.route(successor, follow(7), [host, revoked])
            self.revoke(revoked, ban=True)
            self.until(lambda: revoked.relay.closed, "ban did not revoke established relay")
            self.rejected_auth(lambda nonce: auth_payload(revoked.user, revoked.token, nonce)[0])
            self.revoked_http(revoked, revoked.token)
            self.route(successor, follow(8), [host])
            self.mark("synthetic SQLite reset/ban revoke relay sessions and all four summon endpoints; new reset token works")

        self.current = "unauthenticated limits and service liveness"
        probe = Relay(self.relay_address, self.args.timeout)
        try:
            probe.challenge()
            start = time.monotonic()
            try:
                probe.read(6.5)
            except (EOFError, ConnectionError):
                require(time.monotonic() - start <= 6.5, "unauthenticated lifetime exceeded bound")
            else:
                raise Failure("unauthenticated peer received unexpected traffic")
        finally:
            probe.close()
        for length in (0, MAX_FRAME + 1):
            probe = Relay(self.relay_address, self.args.timeout)
            try:
                probe.challenge()
                probe.sock.sendall(struct.pack("<I", length))
                try:
                    probe.read(self.args.timeout)
                except (EOFError, ConnectionError):
                    pass
                else:
                    raise Failure("invalid ingress length not disconnected")
            finally:
                probe.close()
        require(outsider.np.token(outsider.user) == outsider.token, "native NP no longer responds")
        self.route(outsider, follow(9), [])
        self.mark("unauthenticated lifetime and invalid ingress sizes bounded; native NP/relay still respond")
        self.skipped.append("slow-reader/output-backlog and connection/rate saturation: separate bounded stress fixture required")
        if self.native_factory:
            require(self.native_factory.executed(), "native client DLL did not execute complete handshake/send/control/data coverage")
            self.mark("actual C++ Client Begin/Ack/Receive/Send and context/data/placement/cancellation execute through ctypes")
        return {"ok": True, "passed": self.passed, "skipped": self.skipped,
                "native_rooms_from_np_commands": True,
                "native_client_code_executed": bool(self.native_factory and self.native_factory.executed()),
                "native_client_calls": self.native_factory.calls if self.native_factory else {},
                "native_client_deliveries": self.native_factory.deliveries if self.native_factory else {},
                "native_http_ownership_thp2": True}

    def close(self):
        for relay in self.relays:
            relay.close()
        for account in self.accounts:
            if account.np:
                account.np.close()


def registration_key(args):
    if args.registration_key_file is not None:
        raw = args.registration_key_file.read_bytes()
        require(len(raw) <= 4096, "registration key file too large")
        key = raw.decode("utf-8-sig").strip()
    else:
        root = args.state_dir.resolve(strict=True)
        config = (root / "shadnet.cfg").resolve(strict=True)
        require(config.is_relative_to(root), "fixture config escapes state directory")
        settings = configparser.ConfigParser(interpolation=None)
        settings.read_string(config.read_text(encoding="utf-8-sig"))
        # Linux QSettings configs may contain [General]; Windows native files
        # produced by the runner use that same section.
        general = settings["General"]
        require(general.get("Host") == args.server_ip and
                general.getint("UnsecuredPort") == args.tcp_port and
                general.getint("WebApiPort") == args.web_port,
                "explicit endpoint does not match disposable state config")
        key = general.get("RegistrationSecretKey", "").strip()
    require(bool(key) and len(key.encode("utf-8")) <= 4096,
            "a nonempty disposable registration key is required")
    return key


class PacketTests(unittest.TestCase):
    """Offline codec/framing checks; these never claim native integration."""
    @staticmethod
    def context_frame(key, sequence, core):
        # Synthetic v4 roster for codec tests only, never injected into live rooms.
        room, user, owner, peers = struct.unpack("<QIIB", core)
        users = [] if not room else [user]
        if room and peers and owner != user:
            users.append(owner)
        candidate = 1
        while room and len(users) < peers + 1:
            if candidate not in users:
                users.append(candidate)
            candidate += 1
        roster = b"".join(struct.pack("<IHQB16s", uid, index + 1, index + 1, 1,
                                     str(uid).encode("ascii")) for index, uid in enumerate(users))
        return signed(key, b"THX4", sequence, core + bytes([len(users)]) + roster)

    @staticmethod
    def pending_peer():
        relay = Relay.__new__(Relay)
        relay.key, relay.user, relay.incoming = b"synthetic", 9, 0
        relay.room = relay.owner = relay.peers = relay.contexts = 0
        relay.data, relay.placements = [], []
        relay.pending_placement, relay.pending_generation = None, 0
        return relay

    def test_proto_known_wire_and_repetitions(self):
        raw = bytes.fromhex("0a036162631003100420ffffffff0f")
        self.assertEqual(pb_fields(raw), {1: [b"abc"], 2: [3, 4], 4: [0xFFFFFFFF]})
        self.assertEqual(pb_bytes(1, "abc") + pb_int(2, 3), raw[:7])
        for malformed in (b"\0", b"\x0a\x02x", b"\x08" + b"\xff" * 10, b"\x0b"):
            with self.assertRaises(Failure):
                pb_fields(malformed)

    def test_fragmented_and_coalesced_relay(self):
        decoder = Frames()
        wire = framed(b"THC2" + bytes(range(32))) + framed(b"next")
        packets = []
        for byte in wire:
            decoder.pending.append(byte)
            packet = decoder.pop()
            if packet is not None:
                packets.append(packet)
        self.assertEqual(packets, [b"THC2" + bytes(range(32)), b"next"])
        decoder.pending.extend(wire)
        self.assertEqual(decoder.pop(), packets[0])
        self.assertEqual(decoder.pop(), packets[1])
        self.assertIsNone(decoder.pop())
        for size in (0, 65537, 0xFFFFFFFF):
            decoder.pending[:] = struct.pack("<I", size)
            with self.assertRaises(Failure):
                decoder.pop()

    def test_hmac_domain_known_vector(self):
        # Independent RFC 4231 HMAC-SHA256 vector, plus exact NUL domain lengths.
        self.assertEqual(mac(b"\x0b" * 20, b"Hi There").hex(),
                         "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7")
        self.assertEqual((len(AUTH_DOMAIN), len(KEY_DOMAIN)), (47, 21))
        proof, key = auth_payload(0x12345678, b"synthetic", bytes(32))
        self.assertEqual(proof[:12], b"THA4\x78\x56\x34\x12\0\0\0\0")
        self.assertEqual(len(proof), 44)
        self.assertNotEqual(proof[-32:], key)

    def test_signed_context_data_tamper_replay(self):
        relay = Relay.__new__(Relay)
        relay.key, relay.user, relay.incoming = b"synthetic", 9, 0
        relay.contexts, relay.data = 0, []
        context = self.context_frame(relay.key, 1, struct.pack("<QIIB", 123, 9, 7, 1))
        relay.accept(context)
        self.assertEqual((relay.room, relay.owner, relay.peers), (123, 7, 1))
        data = signed(relay.key, b"THS2", 2, struct.pack("<QII", 123, 7, 7) + follow())
        relay.accept(data)
        self.assertEqual(relay.data, [(123, 7, 7, follow())])
        for bad in (data, data[:-1] + bytes([data[-1] ^ 1]), b"\x7f\x01"):
            with self.assertRaises(Failure):
                relay.accept(bad)
        wrong_room = signed(relay.key, b"THS2", 3, struct.pack("<QII", 124, 7, 7) + follow())
        with self.assertRaises(Failure):
            relay.accept(wrong_room)
        relay.accept(self.context_frame(relay.key, 4, struct.pack("<QIIB", 0, 9, 0, 0)))
        self.assertEqual((relay.room, relay.owner, relay.peers), (0, 0, 0))

    def test_native_np_framing_notifications_and_eof(self):
        # socketpair is local only. Predelivered reply/notification fixtures
        # exercise the actual NP call path without a listening service/thread.
        left, right = socket.socketpair()
        try:
            info = NP_HEADER.pack(3, 0, 19, 0) + struct.pack("<I", 1)
            for byte in info:
                right.sendall(bytes([byte]))
            client = NP(left, 1)
            right.sendall(NP_HEADER.pack(2, 10, 16, 0) + b"\0" +
                          NP_HEADER.pack(1, CONTEXT_START, 16, 1) + b"\0")
            self.assertEqual(client.call(CONTEXT_START, pb_int(1, 1)), b"")
            expected = bytes.fromhex("006400150000000100000000000000020000000801")
            self.assertEqual(recv_exact(right, len(expected), time.monotonic() + 1), expected)
            right.sendall(b"\x01\0")
            right.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError):
                client.packet(time.monotonic() + 1)
        finally:
            left.close()
            right.close()

    def test_old_server_and_bad_ack_fail_closed(self):
        relay = Relay.__new__(Relay)
        relay.timeout = 1
        relay.read = lambda _: b"\x7f\x01"  # The legacy relay's actual greeting.
        with self.assertRaisesRegex(Failure, "upgrade required"):
            relay.challenge()
        account = Account("offlinefixture")
        account.user, account.token = 9, b"synthetic"
        replies = iter([b"THC2" + bytes(32), b"THO4" + struct.pack("<Q", 9) + bytes(32)])
        relay.read = lambda _: next(replies)
        relay.write = lambda *_, **__: None
        with self.assertRaisesRegex(Failure, "acknowledgement MAC"):
            relay.authenticate(account)

    def test_partial_relay_eof(self):
        left, right = socket.socketpair()
        try:
            relay = Relay.__new__(Relay)
            relay.sock, relay.decoder = left, Frames()
            right.sendall(struct.pack("<I", 36) + b"THC2partial")
            right.shutdown(socket.SHUT_WR)
            with self.assertRaisesRegex(Failure, "partial frame"):
                relay.read(1)
        finally:
            left.close()
            right.close()

    def test_explicit_state_key_and_guarded_synthetic_revoke(self):
        with tempfile.TemporaryDirectory(prefix="relay-v2-codec-") as temporary:
            root = Path(temporary)
            (root / "db").mkdir()
            config = root / "shadnet.cfg"
            config.write_text("[General]\nHost=127.0.0.1\nUnsecuredPort=32413\n"
                              "WebApiPort=32415\nRegistrationSecretKey=offline-synthetic-key\n", encoding="utf-8")
            args = argparse.Namespace(state_dir=root, registration_key_file=None,
                                      server_ip="127.0.0.1", tcp_port=32413, web_port=32415, relay_port=22444)
            self.assertEqual(registration_key(args), "offline-synthetic-key")
            suite = Suite(args, "offline-synthetic-key")
            account = Account("rv2offline")
            account.user, account.token = 1, b"offline-synthetic-token"
            database = root / "db/shadnet.db"
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE account(user_id INTEGER PRIMARY KEY, username TEXT, token TEXT, banned INTEGER)")
                db.executemany("INSERT INTO account VALUES(?,?,?,?)",
                               [(1, account.name, account.token.decode(), 0), (2, "canary", "unchanged", 0)])
            with self.assertRaisesRegex(Failure, "synthetic state directory"):
                suite.revoke(account)  # Not registered by this Suite.
            suite.accounts.append(account)
            account.token = b"does-not-match-database"
            with self.assertRaisesRegex(Failure, "does not match"):
                suite.revoke(account)
            account.token = b"offline-synthetic-token"
            account.token = suite.revoke(account)
            suite.revoke(account, ban=True)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute("SELECT banned FROM account WHERE user_id=1").fetchone(), (1,))
                self.assertEqual(db.execute("SELECT username,token,banned FROM account WHERE user_id=2").fetchone(),
                                 ("canary", "unchanged", 0))
            args.tcp_port += 1
            with self.assertRaisesRegex(Failure, "does not match disposable state"):
                registration_key(args)

    def test_thp2_shared_sequence_and_local_pending_expiry(self):
        relay = self.pending_peer()
        context = self.context_frame(relay.key, 1, struct.pack("<QIIB", 0, relay.user, 0, 0))
        pending = signed(relay.key, b"THP2", 2,
                         struct.pack("<QQIII", 5, 123, 7, relay.user, GRANT_TTL_MS) + PLACEMENT.encode())
        # Authenticated context and placement share one stream, including when
        # the transport delivers a split prefix then both coalesced frames.
        decoder = Frames()
        wire = framed(context) + framed(pending)
        decoder.pending.extend(wire[:3])
        self.assertIsNone(decoder.pop())
        decoder.pending.extend(wire[3:])
        relay.accept(decoder.pop())
        relay.accept(decoder.pop())
        self.assertEqual((relay.room, relay.owner, relay.peers, relay.incoming), (0, 0, 0, 2))
        self.assertEqual(relay.active_placement()["text"], PLACEMENT)
        self.assertEqual((relay.placements[0]["host"], relay.placements[0]["target"],
                          relay.placements[0]["generation"]), (7, 9, 5))
        expires = relay.pending_placement["expires"]
        self.assertIsNotNone(relay.active_placement(expires - 0.001))
        self.assertIsNone(relay.active_placement(expires))
        relay.accept(self.context_frame(relay.key, 3, struct.pack("<QIIB", 0, relay.user, 0, 0)))
        with self.assertRaises(Failure):
            relay.accept(pending)  # Replay crosses context/control frame types.
        cancel = signed(relay.key, b"THP2", 4, struct.pack("<QQIII", 5, 123, 7, relay.user, 0))
        relay.accept(cancel)
        self.assertIsNone(relay.active_placement())
        relay.accept(signed(relay.key, b"THP2", 5,
                            struct.pack("<QQIII", 6, 123, 7, relay.user, GRANT_TTL_MS) + PLACEMENT.encode()))
        relay.accept(signed(relay.key, b"THP2", 6, struct.pack("<QQIII", 5, 123, 7, relay.user, 0)))
        self.assertEqual(relay.incoming, 6)
        self.assertEqual(relay.active_placement()["generation"], 6)
        self.assertEqual(relay.pending_generation, 6)
        relay.accept(signed(relay.key, b"THP2", 7, struct.pack("<QQIII", 6, 123, 7, relay.user, 0)))
        self.assertIsNone(relay.active_placement())

    def test_thp2_tamper_identity_generation_and_size_bounds(self):
        base = (5, 123, 7, 9, GRANT_TTL_MS)
        variants = [(0, 123, 7, 9, 1000), (5, 0, 7, 9, 1000),
                    (5, 123, 0, 9, 1000), (5, 123, 9, 9, 1000),
                    (5, 123, 7, 10, 1000), (5, 123, 7, 9, MAX_CLIENT_TTL_MS + 1)]
        for fields in variants:
            with self.subTest(fields=fields):
                relay = self.pending_peer()
                raw = signed(relay.key, b"THP2", 1, struct.pack("<QQIII", *fields) + PLACEMENT.encode())
                with self.assertRaises(Failure):
                    relay.accept(raw)
        for text in (b"", b"\xff", b"x\0y", b"x" * (MAX_PLACEMENT_BYTES + 1)):
            relay = self.pending_peer()
            with self.assertRaises(Failure):
                relay.accept(signed(relay.key, b"THP2", 1, struct.pack("<QQIII", *base) + text))
        relay = self.pending_peer()
        valid = signed(relay.key, b"THP2", 1, struct.pack("<QQIII", *base) + PLACEMENT.encode())
        bad = bytearray(valid)
        bad[32] ^= 1
        with self.assertRaisesRegex(Failure, "MAC"):
            relay.accept(bytes(bad))
        relay.pending_generation = 6
        relay.accept(valid)
        self.assertFalse(relay.placements)
        self.assertEqual(relay.pending_generation, 6)
        relay = self.pending_peer()
        relay.room, relay.owner = 123, 7
        with self.assertRaisesRegex(Failure, "room admission"):
            relay.accept(valid)

    def test_http_fixture_keeps_private_bearer_separate_from_game_alias(self):
        args = argparse.Namespace(server_ip="127.0.0.1", tcp_port=32413, web_port=32415,
                                  relay_port=22444, timeout=1)
        suite = Suite(args, "offline-synthetic-key")
        account = Account("offlinefixture")
        account.user, account.token = 9, b"offline-private-bearer"
        captured = []

        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                response = io.BytesIO(b'{"ResKind":0}')
                response.code = 200
                return response

        body = dict(SessionId="offline", UserId=account.legacy_user)
        with patch(__name__ + ".build_opener", return_value=Opener()):
            self.assertEqual(suite.summon("create", body, account, PLACEMENT), (200, {"ResKind": 0}))
        request = captured[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:32415/summon_messenger/create")
        self.assertEqual(request.get_header("Authorization"), "Bearer offline-private-bearer")
        self.assertEqual(json.loads(request.data)["UserId"], 7000000009)
        self.assertNotIn(account.token.decode(), request.full_url)

        class Forbidden:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

        with patch(__name__ + ".build_opener", return_value=Forbidden()):
            self.assertEqual(suite.summon("delete", body, account), (403, {}))

    def test_native_dll_actual_cpp_vectors(self):
        dll = getattr(self, "native_dll_path", None)
        if dll is None:
            self.skipTest("optional Windows --native-client-dll not supplied")
        factory = NativeFactory(dll)
        peer = factory.create()
        token, user, nonce = b"offline-synthetic-native-token", 9, bytes(range(32))
        proof, key = auth_payload(user, token, nonce)
        try:
            self.assertEqual(peer.invoke("begin", user, token, b"THC2" + nonce), proof)
            ack = b"THO4" + struct.pack("<Q", user)
            peer.invoke("ack", ack + mac(key, ack))
            context = struct.pack("<QIIB", 0, user, 0, 0)
            self.assertEqual(peer.invoke("receive", self.context_frame(key, 1, context)), (1, context))
            for sequence, generation, ttl, text in ((2, 5, 10000, PLACEMENT.encode()),
                                                    (3, 6, 10000, PLACEMENT.encode()),
                                                    (4, 5, 0, b""), (5, 6, 0, b"")):
                body = struct.pack("<QQIII", generation, 123, 7, user, ttl) + text
                expected_kind = 4 if (sequence, generation, ttl) == (4, 5, 0) else 3
                self.assertEqual(peer.invoke("receive", signed(key, b"THP2", sequence, body)), (expected_kind, body))
            context = struct.pack("<QIIB", 123, user, 7, 1)
            self.assertEqual(peer.invoke("receive", self.context_frame(key, 6, context)), (1, context))
            inner = life(user)
            self.assertEqual(peer.invoke("send", inner), signed(key, b"THD2", 1, inner))
            self.assertEqual(peer.invoke("send", pvp(1)), signed(key, b"THD2", 2, pvp(1)))
            with self.assertRaises(Failure):
                peer.invoke("send", follow())  # C++ role gate, not a server negative.
            data = signed(key, b"THS2", 7, struct.pack("<QII", 123, 7, 7) + follow())
            self.assertEqual(peer.invoke("receive", data), (2, context + struct.pack("<I", 7) + follow()))
            empty = struct.pack("<QIIB", 0, user, 0, 0)
            peer.invoke("receive", self.context_frame(key, 8, empty))
            with self.assertRaises(Failure):
                peer.invoke("send", inner)  # No native room, C++ denies Send.
            bad = bytearray(self.context_frame(key, 9, empty))
            bad[-1] ^= 1
            with self.assertRaises(Failure):
                peer.invoke("receive", bytes(bad))
            self.assertTrue(factory.executed())
        finally:
            peer.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true", help="offline codec/socketpair checks only")
    parser.add_argument("--server-ip", type=str, help="explicit disposable native server IPv4")
    parser.add_argument("--tcp-port", type=int)
    parser.add_argument("--web-port", type=int)
    parser.add_argument("--relay-port", type=int)
    parser.add_argument("--native-client-dll", type=Path,
                        help="Windows: actual C++ relay Client ABI DLL; all valid peers use native codec")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--registration-key-file", type=Path, help="UTF-8 plain disposable registration key")
    source.add_argument("--state-dir", type=Path, help="disposable native state root; enables guarded synthetic reset/ban")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--quiet-window", type=float, default=0.65, help="negative delivery observation window (>=0.5s)")
    args = parser.parse_args(argv)
    if args.native_client_dll is not None and sys.platform != "win32":
        parser.error("--native-client-dll is Windows only; omit it for independent Linux peers")
    if args.self_test:
        PacketTests.native_dll_path = args.native_client_dll
        return 0 if unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(PacketTests)).wasSuccessful() else 1
    if (args.server_ip is None or any(p is None or not 1 <= p <= 65535
                                     for p in (args.tcp_port, args.web_port, args.relay_port)) or
            (args.registration_key_file is None and args.state_dir is None)):
        parser.error("explicit --server-ip/--tcp-port/--web-port/--relay-port and one disposable key source required")
    if args.timeout < 1 or args.quiet_window < 0.5:
        parser.error("--timeout must be >=1 and --quiet-window >=0.5 seconds")
    suite = None
    try:
        address = ipaddress.IPv4Address(args.server_ip)
        require(not address.is_unspecified and not address.is_multicast and
                any(address in ipaddress.IPv4Network(cidr) for cidr in
                    ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "26.0.0.0/8")),
                "explicit trusted fixture IPv4 required")
        key = registration_key(args)
        suite = Suite(args, key)
        result = suite.run()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Failure, OSError, EOFError, ValueError, KeyError, configparser.Error, sqlite3.Error) as error:
        # Do not serialize arbitrary network/file/SQLite exception text; it
        # might contain request headers, config fragments, or secret values.
        result = {"ok": False, "phase": suite.current if suite else "arguments/fixture key",
                  "error": str(error) if isinstance(error, Failure) else type(error).__name__,
                  "passed": suite.passed if suite else []}
        print(json.dumps(result, sort_keys=True))
        return 1
    finally:
        if suite:
            suite.close()


if __name__ == "__main__":
    raise SystemExit(main())
