"""Bounded security changes for the sources pinned in sources.json (no downloads)."""
import argparse
from pathlib import Path


def replace(text, before, after):
    if text.count(after) == 1 and before not in text.replace(after, "", 1):
        return text  # Reapplying this exact patch is harmless.
    if after not in text and text.count(before) == 1:
        return text.replace(before, after, 1)
    raise ValueError("Pinned source no longer matches security patch: " + before[:100])


def patch_gameserver(data):
    text = data.decode("utf-8")
    text = replace(text, '''        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    self.data.update(json.load(handle))
            except Exception as error:
                print("  WARNING: could not read {} ({}), starting empty".format(path, error))''', '''        if path:
            try:
                if os.path.islink(path):
                    raise ValueError("online data must not be a symlink")
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if not isinstance(loaded, dict):
                    raise ValueError("online data must be an object")
                if not {"next_id", "messages", "tombs", "deleted", "shells"}.issubset(loaded):
                    raise ValueError("incomplete online data")
                for key in ("messages", "tombs", "shells", "channels"):
                    if key in loaded and (not isinstance(loaded[key], dict) or
                                          any(not isinstance(v, dict) for v in loaded[key].values())):
                        raise ValueError("invalid online collection: " + key)
                if ("next_id" in loaded and (type(loaded["next_id"]) is not int or loaded["next_id"] < 0)):
                    raise ValueError("invalid next_id")
                if "deleted" in loaded and (not isinstance(loaded["deleted"], list) or
                                           any(type(v) is not int for v in loaded["deleted"])):
                    raise ValueError("invalid deleted ids")
                self.data.update(loaded)
            except FileNotFoundError:
                pass  # Only a genuinely absent file permits a fresh store.
            except (OSError, ValueError, TypeError) as error:
                raise RuntimeError("Cannot read online data; original preserved; startup refused: {}".format(path)) from error''')
    # These fields are consistency checks, NOT authentication. Deployment must
    # exclude untrusted peers; the legacy client supplies no verifiable identity.
    text = replace(text, 'def blood_delete(req):', '''def _delete_user(body):
    value = body.get("UserId")
    return value if type(value) is int and value > 0 else None


def blood_delete(req):''')
    for kind, record in (("blood", "msg"), ("tomb", "tomb")):
        function = text.index("def " + kind + "_delete(req):")
        end = text.index("\n\ndef ", function + 1)
        segment = text[function:end]
        segment = replace(segment, '    user = _as_int(body.get("UserId"))',
                          '    user = _delete_user(body)\n    if user is None:\n        return {"ResKind": 1}')
        segment = replace(segment, f'if {record} and (not user or {record}["UserId"] == user):',
                          f'if {record} and {record}["UserId"] == user:')
        text = text[:function] + segment + text[end:]
    text = replace(text, 'RELAY_CLIENTS = {}', '''# Network admission is not player/room authentication. Never port-forward these
# plaintext legacy services; restrict VPN ACLs/firewall to the actual player devices.
import ipaddress

TRUSTED_NETWORKS = tuple(ipaddress.IPv4Network(cidr) for cidr in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "26.0.0.0/8"))  # 26/8 is supported for Radmin VPN only.


def _trusted_address(value):
    try:
        address = ipaddress.IPv4Address(value)
        return any(address in network for network in TRUSTED_NETWORKS)
    except ipaddress.AddressValueError:
        return False


RELAY_CLIENTS = {}''')
    text = replace(text, '''def _relay_client(conn, address):
    name =''', '''def _relay_client(conn, address):
    if not _trusted_address(address[0]):
        conn.close()
        return
    name =''')
    text = replace(text, '''def start_relay(host, port):
    listener =''', '''def start_relay(host, port):
    if not _trusted_address(host):
        raise ValueError("Relay requires an explicit LAN/VPN/loopback IPv4")
    listener =''')
    text = replace(text, '''        caller = self.client_address[0]
        is_local =''', '''        caller = self.client_address[0]
        if not _trusted_address(caller):
            self.close_connection = True
            self._send(b'{"ResKind":1}', "application/json", 403)
            return
        is_local =''')
    text = replace(text, '''    ARGS = parser.parse_args()
''', '''    ARGS = parser.parse_args()
    if not _trusted_address(ARGS.host):
        parser.error("Use an explicit LAN/VPN/loopback IPv4; wildcard/public listeners are refused")
    if ARGS.bbhost:
        parser.error("This trusted-network build supports --no-bbhost only")
''')
    return text.encode("utf-8")


def patch_shadnet(root):
    root = Path(root)
    changes = {}

    def edit(name, before, after):
        path = root / "src" / name
        changes[path] = replace(changes.get(path, path.read_text(encoding="utf-8")), before, after)

    edit("webapi_auth.cpp", 'WHERE token = ?"', 'WHERE token = ? AND banned = 0"')
    edit("webapi_auth.cpp", '''    AuthResult out;

    const QByteArray rawAuth = req.value("Authorization");''', '''    return AuthenticateBearer(req.value("Authorization"), db);
}

AuthResult AuthenticateBearer(const QByteArray& rawAuth, Database& db) {
    AuthResult out;''')
    edit("webapi_auth.h", '''AuthResult Authenticate(const QHttpServerRequest& req, Database& db);''', '''AuthResult Authenticate(const QHttpServerRequest& req, Database& db);
// Same validation for an already-extracted Authorization field (also used offline).
AuthResult AuthenticateBearer(const QByteArray& rawAuth, Database& db);''')
    edit("database.cpp", '''    q.prepare("UPDATE account SET banned=? WHERE user_id=?");
    q.addBindValue(ban ? 1 : 0);''', '''    // Rotate on ban so lifting the ban cannot revive a stolen bearer token.
    const QString replacement = ban ? GenerateToken() : QString();
    if (ban && replacement.isEmpty())
        return false;
    q.prepare("UPDATE account SET banned=?, token=CASE WHEN ? THEN ? ELSE token END WHERE user_id=?");
    q.addBindValue(ban ? 1 : 0);
    q.addBindValue(ban ? 1 : 0);
    q.addBindValue(replacement);''')
    edit("bloodborne_summon_broker.cpp", '''    if (sessionId.isEmpty() && !userId.has_value()) {
        return {};
    }''', '''    // Both legacy selectors must identify the same advertisement, even for
    // Force/SeamlessLeave. They are client claims, NOT authenticated identity.
    if (sessionId.isEmpty() || !userId.has_value() || *userId <= 0 ||
        request.value(QStringLiteral("UserId")).toDouble() != static_cast<double>(*userId)) {
        return {};
    }''')
    edit("bloodborne_summon_broker.cpp", '''            sessionId.isEmpty() ||
            it->advertisement.value(QStringLiteral("SessionId")).toString() == sessionId;''', '''            it->advertisement.value(QStringLiteral("SessionId")).toString() == sessionId;''')
    edit("bloodborne_summon_broker.cpp", '''            !userId.has_value() ||
            Integer(it->advertisement, QStringLiteral("UserId"), -1) == *userId;''', '''            Integer(it->advertisement, QStringLiteral("UserId"), -1) == *userId;''')
    edit("client_session.h", '''        QString npid;
        std::function<void(QByteArray)> send;''', '''        QString npid;
        QHostAddress signalingAddress; // Observed login peer; not a UDP credential.
        std::function<void(QByteArray)> send;''')
    edit("cmd_account.cpp", '''        entry.npid = npid;
        entry.appearOffline''', '''        entry.npid = npid;
        entry.signalingAddress = m_socket->peerAddress();
        entry.appearOffline''')
    # Clear stale endpoint state at login/logout. STUN holds clientsLock before
    # udpLock too, so an update cannot race logout into the next login generation.
    edit("cmd_account.cpp", '''        m_shared->npidToUserId[npid] = user.userId;
''', '''        m_shared->npidToUserId[npid] = user.userId;
        QWriteLocker udpLock(&m_shared->matching.udpLock);
        m_shared->matching.udpExt.remove(npid);
''')
    edit("client_session.cpp", '''            m_shared->npidToUserId.remove(m_info.npid);
''', '''            m_shared->npidToUserId.remove(m_info.npid);
            QWriteLocker udpLock(&m_shared->matching.udpLock);
            m_shared->matching.udpExt.remove(m_info.npid);
''')
    edit("cmd_account.cpp", '''        m_shared->clients.remove(m_info.userId);
        m_authenticated = false;''', '''        m_shared->clients.remove(m_info.userId);
        m_shared->npidToUserId.remove(m_info.npid);
        QWriteLocker udpLock(&m_shared->matching.udpLock);
        m_shared->matching.udpExt.remove(m_info.npid);
        m_authenticated = false;''')
    for name in ("server.cpp", "webapi_server.cpp"):
        edit(name, '#include <QDebug>' if name == "webapi_server.cpp" else '#include <QDir>',
             '#include "trusted_network.h"\n' + ('#include <QDebug>' if name == "webapi_server.cpp" else '#include <QDir>'))
    edit("server.cpp", '''    m_config = config;
    m_shared.config = config;''', '''    if (!TrustedNetwork::Allows(QHostAddress(config->GetHost()))) {
        qCritical() << "Use an explicit LAN/VPN/loopback IPv4; public/wildcard listeners refused";
        return false;
    }
    m_config = config;
    m_shared.config = config;''')
    edit("server.cpp", '''        // Disconnect from the server's parent so spawnSession can reparent it''', '''        if (!TrustedNetwork::Allows(sock->peerAddress())) {
            sock->abort();
            sock->deleteLater();
            continue;
        }
        // Disconnect from the server's parent so spawnSession can reparent it''')
    edit("webapi_server.cpp", '''    m_config = config;
    m_shared = shared;''', '''    if (!TrustedNetwork::Allows(QHostAddress(config->GetHost()))) {
        qCritical() << "WebAPI requires an explicit LAN/VPN/loopback IPv4";
        return false;
    }
    m_config = config;
    m_shared = shared;''')
    fixes = Path(__file__).resolve().parent / "fixes"
    test = root / "tests" / "test_server_security.cpp"
    cmake = root / "CMakeLists.txt"
    addition = '''
# The Hunt offline server security regressions (synthetic SQLite/state, no sockets).
if(BUILD_TESTING)
    add_executable(test_server_security EXCLUDE_FROM_ALL
        tests/test_server_security.cpp src/bloodborne_summon_broker.cpp
        src/database.cpp src/webapi_auth.cpp src/stun_server.cpp src/stun_server.h
        "${PROTO_OUT}/shadnet.pb.h")
    target_include_directories(test_server_security PRIVATE src "${PROTO_OUT}"
        "${CMAKE_CURRENT_SOURCE_DIR}/externals/protobuf/src")
    target_link_libraries(test_server_security PRIVATE Qt6::Core Qt6::Network Qt6::Sql Qt6::HttpServer libprotobuf)
    add_test(NAME server_security COMMAND test_server_security)
endif()
'''
    current = cmake.read_text(encoding="utf-8")
    if addition not in current:
        if "test_server_security" in current:
            raise ValueError("Conflicting security regression target")
        changes[cmake] = current + addition
    fixed = {root / "src" / name: (fixes / name).read_bytes()
             for name in ("trusted_network.h", "stun_server.cpp", "stun_server.h")}
    fixed[test] = (Path(__file__).resolve().parent / "tests" / test.name).read_bytes()
    # Validate all anchors and read patch inputs before changing any file.
    for path, text in changes.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    test.parent.mkdir(exist_ok=True)
    for path, data in fixed.items():
        path.write_bytes(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadnet", type=Path, required=True)
    patch_shadnet(parser.parse_args().shadnet)
