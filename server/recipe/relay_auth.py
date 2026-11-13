"""Authenticated relay v2 patch for the fixed shadNet/BBSC sources, after security.py."""
import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace(text, before, after):
    if text.count(after) == 1 and before not in text.replace(after, "", 1):
        return text
    if after not in text and text.count(before) == 1:
        return text.replace(before, after, 1)
    raise ValueError("Pinned source no longer matches relay auth patch: " + before[:90])


def patch_gameserver(data):
    text = data.decode("utf-8")
    # Fixed, security-patched start_relay function: remove its entire binding and
    # broadcast accept loop. Other HTTP/game behavior is untouched.
    before = '''def start_relay(host, port):
    if not _trusted_address(host):
        raise ValueError("Relay requires an explicit LAN/VPN/loopback IPv4")
    listener ='''
    after = '''def start_relay(host, port):
    if not _trusted_address(host):
        raise ValueError("Relay requires an explicit LAN/VPN/loopback IPv4")
    listener = None  # Preserve security.py's exact idempotence anchor; no socket.
    raise ValueError("Legacy relay disabled; use native authenticated relay v2")'''
    if after in text:
        if text.count(after) != 1 or before in text.replace(after, "", 1):
            raise ValueError("Conflicting disabled relay implementation")
        return data
    if text.count(before) != 1:
        raise ValueError("Pinned BBSC start_relay anchor changed")
    tree = ast.parse(text)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start_relay"]
    if len(functions) != 1:
        raise ValueError("Unexpected BBSC relay function")
    function = functions[0]
    lines = text.splitlines(keepends=True)
    old = "".join(lines[function.lineno - 1:function.end_lineno])
    # Assert the complete pinned function, not merely a loose prefix.
    import hashlib
    expected = "01ef631ba6dd99420c343917248dd0a0a7c589b8c2b4d0d258e7c7c5e8aa9961"
    if hashlib.sha256(old.encode()).hexdigest() != expected:
        raise ValueError("Pinned BBSC relay function digest changed")
    return ("".join(lines[:function.lineno - 1]) + after + "\n" +
            "".join(lines[function.end_lineno:])).encode("utf-8")


NATIVE_TEST_CMAKE = '''
# The Hunt authenticated relay: actual native unit and opt-in integration CLI.
if(BUILD_TESTING)
    add_executable(test_relay_auth EXCLUDE_FROM_ALL tests/test_relay_auth.cpp
        src/authenticated_relay.cpp src/database.cpp src/config.cpp src/bloodborne_summon_broker.cpp
        src/webapi_auth.cpp src/webapi_routes_bloodborne.cpp "${PROTO_OUT}/shadnet.pb.h")
    target_include_directories(test_relay_auth PRIVATE src "${PROTO_OUT}"
        "${CMAKE_CURRENT_SOURCE_DIR}/externals/protobuf/src")
    target_link_libraries(test_relay_auth PRIVATE Qt6::Core Qt6::Network Qt6::Sql Qt6::HttpServer libprotobuf)
    add_test(NAME relay_auth COMMAND test_relay_auth)

    set(RELAY_PROTO "${CMAKE_CURRENT_BINARY_DIR}/relay_client_proto")
    file(MAKE_DIRECTORY "${RELAY_PROTO}")
    add_custom_command(OUTPUT "${RELAY_PROTO}/shadnet.pb.cc" "${RELAY_PROTO}/shadnet.pb.h"
        COMMAND $<TARGET_FILE:protoc> "--proto_path=${CMAKE_CURRENT_SOURCE_DIR}/tests/relay_client"
            "--cpp_out=${RELAY_PROTO}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/relay_client/shadnet.proto"
        DEPENDS tests/relay_client/shadnet.proto protoc VERBATIM)
    add_executable(relay_auth_integration EXCLUDE_FROM_ALL tests/relay_auth_integration.cpp
        tests/relay_client/client.cpp tests/relay_client/connection.cpp "${RELAY_PROTO}/shadnet.pb.cc")
    target_include_directories(relay_auth_integration PRIVATE tests/relay_client "${RELAY_PROTO}"
        "${CMAKE_CURRENT_SOURCE_DIR}/externals/protobuf/src")
    target_link_libraries(relay_auth_integration PRIVATE libprotobuf Qt6::Core Qt6::Network)
    if(WIN32)
        target_link_libraries(relay_auth_integration PRIVATE ws2_32)
        target_compile_definitions(relay_auth_integration PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    endif()
endif()
'''


def patch_shadnet(root):
    root = Path(root)
    changes = {}

    def edit(name, before, after):
        path = root / name
        changes[path] = replace(changes.get(path, path.read_text(encoding="utf-8")), before, after)

    # disconnectFromHost is not an invokable Qt slot. Queue the actual method
    # so the Terminate reply is sent before the authenticated session closes.
    edit("src/client_session.cpp",
         '        QMetaObject::invokeMethod(m_socket, "disconnectFromHost", Qt::QueuedConnection);',
         '        QMetaObject::invokeMethod(m_socket, [socket = m_socket] {\n'
         '            socket->disconnectFromHost();\n'
         '        }, Qt::QueuedConnection);')
    edit("src/client_session.h", "#pragma once", '#pragma once\n#include "summon_placement.h"')
    edit("src/client_session.h", "    ConfigManager* config;", "    ConfigManager* config;\n"
         "    std::atomic<bool> relayAuthReady{false}; // True only after native relay listen succeeds.\n"
         "    // Main Qt thread only: authenticated broker -> relay admission, never client claims.\n"
         "    std::function<RelayAuth::SummonAdmission(quint64)> relaySummonAdmission;\n"
         "    std::function<void(const RelayAuth::PlacementRequest&)> relayPlacement;\n"
         "    std::function<void(quint64)> relayCancelPlacement;")
    edit("src/client_session.h", "        uint32_t matchingContextId = 0;",
         "        uint32_t matchingContextId = 0;\n"
         "        bool bloodborneMatching = false; // NP login title group, not WebAPI presence.")
    edit("src/cmd_account.cpp", '''    m_matching.titleId = QString::fromStdString(req.title_id());
    {
        QReadLocker lk(&m_shared->matching.roomsLock);
        m_matching.matchingKey =
            m_shared->matching.titleGroups.value(m_matching.titleId, m_matching.titleId);
    }''', '''    m_matching.titleId = QString::fromStdString(req.title_id());
    bool bloodborneMatching;
    {
        QReadLocker lk(&m_shared->matching.roomsLock);
        const auto group = m_shared->matching.titleGroups.constFind(m_matching.titleId);
        m_matching.matchingKey = group == m_shared->matching.titleGroups.cend() ? m_matching.titleId : *group;
        bloodborneMatching = group != m_shared->matching.titleGroups.cend() && *group == QStringLiteral("bloodborne");
    }''')
    edit("src/cmd_account.cpp", "        entry.npTitleId = QString::fromStdString(req.title_id());",
         "        entry.npTitleId = QString::fromStdString(req.title_id());\n"
         "        entry.bloodborneMatching = bloodborneMatching;")
    edit("src/main.cpp", '#include "config.h"', '#include "authenticated_relay.h"\n#include "config.h"')
    edit("src/main.cpp", "    // Start the HTTP/JSON WebAPI listener alongside the binary protocol.",
         '''    AuthenticatedRelay relay(&server.Shared());
    if (!relay.Start(&config, "db/shadnet.db")) {
        qCritical() << "Authenticated relay startup failed (listener or configuration)";
        return 1;
    }

    // Start the HTTP/JSON WebAPI listener alongside the binary protocol.''')
    edit("src/webapi_server.cpp", '    m_http->route("/status", [](const QHttpServerRequest&) {',
         '    m_http->route("/status", [this](const QHttpServerRequest&) {')
    edit("src/webapi_server.cpp", '#include "webapi_server.h"',
         '#include "webapi_server.h"\n#include "client_session.h"')
    edit("src/webapi_server.cpp", '        body.insert("service", "shadnet-webapi");',
         '        body.insert("service", "shadnet-webapi");\n'
         '        if (m_shared && m_shared->relayAuthReady.load())\n'
         '            body.insert("relay_auth", 2);')
    edit("CMakeLists.txt", "    src/main.cpp\n", "    src/main.cpp\n    src/authenticated_relay.cpp\n    src/authenticated_relay.h\n")
    patch_summon(edit)
    cmake = root / "CMakeLists.txt"
    text = changes[cmake]
    if NATIVE_TEST_CMAKE not in text:
        if "relay_auth_integration" in text or "test_relay_auth" in text:
            raise ValueError("Conflicting relay auth target")
        changes[cmake] = text + NATIVE_TEST_CMAKE
    fixed = {root / "src" / name: (ROOT / "fixes" / name).read_bytes()
             for name in ("authenticated_relay.h", "authenticated_relay.cpp", "summon_placement.h")}
    fixed.update({root / "tests" / name: (ROOT / "tests" / name).read_bytes()
                  for name in ("test_relay_auth.cpp", "relay_auth_integration.cpp")})
    # The pinned clientsample lags the pinned server's Matching protocol. Use an
    # isolated TEST copy with exact anchors; never alter the production client.
    for name in ("client.cpp", "client.h", "connection.cpp", "connection.h", "packet.h", "types.h", "shadnet.proto"):
        value = (root / "clientsample" / name).read_text(encoding="utf-8")
        if name == "types.h":
            for command, old, new in (("CreateRoom",13,101),("JoinRoom",14,102),("LeaveRoom",15,103)):
                value = replace(value, f"    {command} = {old},", f"    {command} = {new},")
        elif name == "client.cpp":
            value = replace(value, "    req.set_npid(npid);\n    req.set_password(password);\n    if (!token.empty())",
                            '    req.set_npid(npid);\n    req.set_password(password);\n'
                            '    req.set_title_id("CUSA03173");\n    if (!token.empty())')
        elif name == "shadnet.proto":
            value = replace(value, "    string token    = 3;  \n", "    string token    = 3;  \n    string title_id = 4;\n")
            value += "\nmessage GetTokenReply { string token = 1; uint64 user_id = 2; string npid = 3; }\n"
        fixed[root / "tests" / "relay_client" / name] = value.encode("utf-8")
    # All anchors and input files must validate before the first write.
    for path, value in changes.items():
        path.write_text(value, encoding="utf-8", newline="\n")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "relay_client").mkdir(exist_ok=True)
    for path, data in fixed.items():
        path.write_bytes(data)


def patch_summon(edit):
    """Bind game-owned summon aliases to Bearer identities inside the native broker."""
    header = "src/bloodborne_summon_broker.h"
    cpp = "src/bloodborne_summon_broker.cpp"
    routes = "src/webapi_routes_bloodborne.cpp"
    edit(header, "#include <optional>", '#include <optional>\n#include <functional>\n#include "summon_placement.h"')
    edit(cpp, "#include <QMutexLocker>", "#include <QMutexLocker>\n#include <QScopeGuard>")
    edit(header, "class SummonBroker {", '''// Server-owned identity; never parsed from JSON UserId/TargetUserId/CharaId.
struct SummonIdentity { quint64 user = 0; QByteArray tokenHash; };

class SummonBroker {''')
    edit(header, "        bool seamlessAnywhereSummons = false;", '''        bool seamlessAnywhereSummons = false;
        std::function<void(const RelayAuth::PlacementRequest&)> relayPlacement;
        std::function<void(quint64)> relayCancelPlacement;''')
    edit(header, "        QByteArray pendingClaim;\n        QByteArray pendingHostPlacement;",
         "        QByteArray pendingClaim;\n        QByteArray pendingHostPlacement;\n        bool authorized = true;")
    edit(header, "        int retained = 0;\n        QByteArray pendingHostPlacement;",
         "        int retained = 0;\n        QByteArray pendingHostPlacement;\n        bool authorized = true;")
    edit(header, "AdvertiseResult Advertise(const QJsonObject& body, const QByteArray& rawBody, qint64 nowMs);",
         "AdvertiseResult Advertise(const QJsonObject& body, const QByteArray& rawBody, qint64 nowMs, const SummonIdentity& caller = {});")
    edit(header, '''    QList<QByteArray> Search(const QJsonObject& request, qint64 nowMs,
                             const QByteArray& hostPlacement = {});
    ClaimResult Claim(const QJsonObject& request, const QByteArray& rawRequest, qint64 nowMs,
                      const QByteArray& hostPlacement = {});
    ConsumeResult Consume(const QJsonObject& request, qint64 nowMs);''', '''    QList<QByteArray> Search(const QJsonObject& request, qint64 nowMs,
                             const QByteArray& hostPlacement = {}, const SummonIdentity& caller = {},
                             const RelayAuth::SummonRoom& room = {});
    ClaimResult Claim(const QJsonObject& request, const QByteArray& rawRequest, qint64 nowMs,
                      const QByteArray& hostPlacement = {}, const SummonIdentity& caller = {},
                      const RelayAuth::SummonRoom& room = {});
    ConsumeResult Consume(const QJsonObject& request, qint64 nowMs, const SummonIdentity& caller = {});''')
    edit(header, "        State state = State::Advertised;\n        QJsonObject advertisement;",
         '''        State state = State::Advertised;
        SummonIdentity publisher, claimant;
        RelayAuth::SummonRoom claimantRoom;
        quint64 placementRecord = 0;
        qint64 reservationExpires = 0;
        bool publisherRemoved = false;
        QJsonObject advertisement;''')
    edit(header, "    void PurgeExpiredLocked(qint64 nowMs);", '''    void PurgeExpiredLocked(qint64 nowMs, QList<quint64>& cancelled);
    bool Expired(const Record& record, qint64 nowMs) const;
    void ClearReservation(Record& record, QList<quint64>& cancelled);
    void FlushRelay(const QList<quint64>& cancelled,
                    const RelayAuth::PlacementRequest& placement = {});
    RelayAuth::PlacementRequest PlacementFor(Record& record, qint64 ttlMs);''')
    edit(header, "    QMutex m_mutex;", '''    std::function<void(const RelayAuth::PlacementRequest&)> m_relayPlacement;
    std::function<void(quint64)> m_relayCancelPlacement;
    quint64 m_nextPlacementRecord = 0;
    QMutex m_mutex;''')
    edit(cpp, "      m_seamlessAnywhereSummons(options.seamlessAnywhereSummons || options.seamlessCoop) {}",
         "      m_seamlessAnywhereSummons(options.seamlessAnywhereSummons || options.seamlessCoop),\n"
         "      m_relayPlacement(std::move(options.relayPlacement)),\n"
         "      m_relayCancelPlacement(std::move(options.relayCancelPlacement)) {}")
    edit(cpp, "const QByteArray& rawBody, qint64 nowMs) {", "const QByteArray& rawBody, qint64 nowMs, const SummonIdentity& caller) {")
    edit(cpp, "    auto it = m_records.find(key);\n    if (it == m_records.end() || it->state == State::Consumed) {", '''    auto it = m_records.find(key);
    if (caller.user) {
        if (caller.user > std::numeric_limits<quint32>::max() || caller.tokenHash.size() != 32 ||
            !body.contains(QStringLiteral("CharaId"))) return {State::Advertised, {}, {}, false};
        // Session/UserId/CharaId remain game aliases. Learning them from a
        // search never grants a different private account mutation rights.
        for (auto old = m_records.cbegin(); old != m_records.cend(); ++old)
            if (old->advertisement.value(QStringLiteral("SessionId")) == body.value(QStringLiteral("SessionId")) &&
                old->publisher.user && old->publisher.user != caller.user)
                return {State::Advertised, {}, {}, false};
        if (it == m_records.end()) {
            if (m_records.size() >= 1024) return {State::Advertised, {}, {}, false};
            for (auto old = m_records.begin(); old != m_records.end(); ++old) {
                if (old->publisher.user != caller.user ||
                    old->state == State::Consumed) continue;
                cancelled.append(old->placementRecord);
                old->state = State::Consumed;
                old->reservationExpires = 0;
            }
        }
        if (it != m_records.end() && (it->publisher.tokenHash != caller.tokenHash ||
            it->advertisement.value(QStringLiteral("CharaId")) != body.value(QStringLiteral("CharaId")))) {
            cancelled.append(it->placementRecord);
            it->state = State::Consumed;
        }
    }
    if (it == m_records.end() || it->state == State::Consumed) {''')
    edit(cpp, "        record.state = State::Advertised;\n        record.advertisement = body;",
         "        record.state = State::Advertised;\n        record.publisher = caller;\n        record.advertisement = body;")
    edit(cpp, "    it->advertisement = body;\n    it->rawAdvertisement = rawBody;",
         "    if (caller.user) it->publisher = caller;\n    it->publisherRemoved = false;\n    it->advertisement = body;\n    it->rawAdvertisement = rawBody;")
    edit(cpp, '''        it->state = State::Advertised;
        it->preparationRequester = -1;''', '''        it->state = State::Advertised;
        it->preparationRequester = -1; // Private reservation remains until Claim or its original deadline.''')
    edit(cpp, '''QList<QByteArray> SummonBroker::Search(const QJsonObject& request, qint64 nowMs,
                                       const QByteArray& hostPlacement) {''', '''QList<QByteArray> SummonBroker::Search(const QJsonObject& request, qint64 nowMs,
                                       const QByteArray& hostPlacement, const SummonIdentity& caller,
                                       const RelayAuth::SummonRoom& room) {
    if (caller.user) return SearchPrivate(request, nowMs, hostPlacement, caller, room);''')
    edit(header, "    void PurgeExpiredLocked", '''    QList<QByteArray> SearchPrivate(const QJsonObject& request, qint64 nowMs,
                                    const QByteArray& hostPlacement, const SummonIdentity& caller,
                                    const RelayAuth::SummonRoom& room);
    void PurgeExpiredLocked''')
    edit(cpp, '''SummonBroker::ClaimResult SummonBroker::Claim(const QJsonObject& request,
                                              const QByteArray& rawRequest, qint64 nowMs,
                                              const QByteArray& hostPlacement) {''', '''SummonBroker::ClaimResult SummonBroker::Claim(const QJsonObject& request,
                                              const QByteArray& rawRequest, qint64 nowMs,
                                              const QByteArray& hostPlacement, const SummonIdentity& caller,
                                              const RelayAuth::SummonRoom& room) {
    RelayAuth::PlacementRequest placement;''')
    edit(cpp, "    result.targetUserId = Integer(", '''    if (target->publisherRemoved)
        return {ClaimStatus::Conflict, result.targetSessionId, -1};
    if (caller.user && (!room.room || !target->publisher.user || target->publisher.user == caller.user ||
        (target->claimant.user && (target->claimant.user != caller.user ||
          target->claimant.tokenHash != caller.tokenHash ||
          (target->reservationExpires && !(target->claimantRoom == room)))) ||
        (targetUser.has_value() && Integer(target->advertisement,QStringLiteral("UserId"),-1) != *targetUser) ||
        (!targetChara.isUndefined() && !targetChara.isNull() && target->advertisement.value(QStringLiteral("CharaId")) != targetChara)))
        return {ClaimStatus::Conflict, result.targetSessionId, -1};
    result.targetUserId = Integer(''')
    edit(cpp, "    if (target->state == State::Claimed || target->state == State::Delivered) {", '''    const bool activeClaim = target->state == State::Claimed || target->state == State::Delivered;
    const bool rebind = caller.user && activeClaim && !(target->claimantRoom == room);
    if (activeClaim && !rebind && target->claim != request) {
        result.status = ClaimStatus::Conflict;
        return result;
    }
    // Claim is authoritative only after the target and its ownership are validated.
    if (caller.user) {
        for (auto it = m_records.begin(); it != m_records.end(); ++it) {
            if (it != target && it->reservationExpires && it->claimant.user == caller.user &&
                it->claimant.tokenHash == caller.tokenHash)
                ClearReservation(*it, cancelled);
        }
    }
    if (activeClaim && !rebind) {''')
    edit(cpp, '''            target->claim == request ? ClaimStatus::AlreadyClaimed : ClaimStatus::Conflict;
        return result;''', '''            ClaimStatus::AlreadyClaimed;
        if (caller.user && !hostPlacement.isEmpty()) {
            target->hostPlacement = hostPlacement;
            placement = PlacementFor(*target, std::min(RelayAuth::PlacementTtlMs,
                m_seamlessTtlMs - (nowMs - target->updatedAtMs) + 1));
        }
        return result;''')
    edit(cpp, "    target->state = State::Claimed;\n    target->claim = request;", '''    target->state = State::Claimed;
    if (rebind) {
        cancelled.append(target->placementRecord);
        target->placementRecord = 0;
    }
    target->claimant = caller;
    target->claimantRoom = room;
    target->reservationExpires = 0;
    target->claim = request;''')
    edit(cpp, "    result.status = ClaimStatus::Claimed;\n    return result;", '''    result.status = ClaimStatus::Claimed;
    if (caller.user && !target->hostPlacement.isEmpty())
        placement = PlacementFor(*target, std::min(RelayAuth::PlacementTtlMs, m_seamlessTtlMs));
    else if (caller.user)
        cancelled.append(target->placementRecord);
    return result;''')
    edit(cpp, "SummonBroker::ConsumeResult SummonBroker::Consume(const QJsonObject& request, qint64 nowMs) {",
         "SummonBroker::ConsumeResult SummonBroker::Consume(const QJsonObject& request, qint64 nowMs, const SummonIdentity& caller) {")
    edit(cpp, "        if (sessionMatches && userMatches && it->state != State::Consumed) {", '''        if (sessionMatches && userMatches && it->state != State::Consumed) {
            if (caller.user && it->publisher.user != caller.user) return {0,0,{},false};''')
    edit(cpp, "            if (m_seamlessCoop && !forceConsume && IsSeamlessActiveState(it->state)) {",
         """            if (m_seamlessCoop && !forceConsume && (IsSeamlessActiveState(it->state) || it->reservationExpires)) {
                if (it->reservationExpires) it->publisherRemoved = true;""")
    edit(cpp, "            it->state = State::Consumed;\n            ++result.consumed;", '''            // A cancelled prejoin reservation must neither occupy the owner's
            // slot nor return to Advertised when its old deadline expires.
            if (caller.user) ClearReservation(*it, cancelled);
            it->state = State::Consumed;
            ++result.consumed;''')
    # The scope guard runs after QMutexLocker destruction, including early returns.
    for anchor, outgoing in (
        ("const QByteArray& rawBody, qint64 nowMs, const SummonIdentity& caller) {", "{}"),
        ("    RelayAuth::PlacementRequest placement;", "placement"),
        ("SummonBroker::ConsumeResult SummonBroker::Consume(const QJsonObject& request, qint64 nowMs, const SummonIdentity& caller) {", "{}"),
        ("qint64 nowMs) {\n    QMutexLocker lock(&m_mutex);\n    PurgeExpiredLocked(nowMs);\n    const auto it = m_records.constFind", None),
        ("int SummonBroker::Size(qint64 nowMs) {", "{}"),
        ("    if (caller.user) return SearchPrivate(request, nowMs, hostPlacement, caller, room);", "{}"),
    ):
        guard = f"\n    QList<quint64> cancelled;\n    const auto flush = qScopeGuard([&] {{ FlushRelay(cancelled, {outgoing or '{}'}); }});"
        if outgoing is None:
            edit(cpp, anchor, anchor.replace(" {\n", " {" + guard + "\n", 1)
                 .replace("PurgeExpiredLocked(nowMs);", "PurgeExpiredLocked(nowMs, cancelled);"))
        else:
            before = anchor + "\n    QMutexLocker lock(&m_mutex);\n    PurgeExpiredLocked(nowMs);"
            edit(cpp, before, anchor + guard + "\n    QMutexLocker lock(&m_mutex);\n    PurgeExpiredLocked(nowMs, cancelled);")
    edit(cpp, '''void SummonBroker::PurgeExpiredLocked(qint64 nowMs) {
    for (auto it = m_records.begin(); it != m_records.end();) {
        const qint64 ttl =
            m_seamlessCoop && IsSeamlessActiveState(it->state) ? m_seamlessTtlMs : m_ttlMs;
        if (nowMs - it->updatedAtMs > ttl) {
            it = m_records.erase(it);''', '''bool SummonBroker::Expired(const Record& record, qint64 nowMs) const {
    const qint64 ttl = m_seamlessCoop && IsSeamlessActiveState(record.state) &&
        (!record.reservationExpires || nowMs < record.reservationExpires) ? m_seamlessTtlMs : m_ttlMs;
    return nowMs - record.updatedAtMs > ttl;
}

void SummonBroker::ClearReservation(Record& record, QList<quint64>& cancelled) {
    cancelled.append(record.placementRecord);
    record.placementRecord = 0;
    record.state = record.publisherRemoved ? State::Consumed : State::Advertised;
    record.claimant = {};
    record.claimantRoom = {};
    record.reservationExpires = 0;
    record.hostPlacement.clear();
    record.preparationRequester = -1;
}

void SummonBroker::FlushRelay(const QList<quint64>& cancelled, const RelayAuth::PlacementRequest& placement) {
    if (m_relayCancelPlacement)
        for (const quint64 record : cancelled)
            if (record) m_relayCancelPlacement(record);
    if (placement.record && m_relayPlacement) m_relayPlacement(placement);
}

RelayAuth::PlacementRequest SummonBroker::PlacementFor(Record& record, qint64 ttlMs) {
    if (!record.placementRecord) record.placementRecord = ++m_nextPlacementRecord;
    return {record.placementRecord, record.claimant.user, record.publisher.user, record.claimantRoom,
            record.claimant.tokenHash, record.publisher.tokenHash, record.hostPlacement, ttlMs};
}

void SummonBroker::PurgeExpiredLocked(qint64 nowMs, QList<quint64>& cancelled) {
    for (auto it = m_records.begin(); it != m_records.end();) {
        if (it->reservationExpires && nowMs >= it->reservationExpires)
            ClearReservation(*it, cancelled);
        if (Expired(*it, nowMs)) {
            cancelled.append(it->placementRecord);
            it = m_records.erase(it);''')
    edit(cpp, "SummonBroker::ClaimResult SummonBroker::Claim(", '''QList<QByteArray> SummonBroker::SearchPrivate(const QJsonObject& request, qint64 nowMs,
                                              const QByteArray& hostPlacement, const SummonIdentity& caller,
                                              const RelayAuth::SummonRoom& room) {
    RelayAuth::PlacementRequest placement;
    QList<quint64> cancelled;
    const auto flush = qScopeGuard([&] { FlushRelay(cancelled, placement); });
    QMutexLocker lock(&m_mutex);
    // The native first search happens before CreateRoom. Even cleanup is read-only on that path.
    if (room.room) PurgeExpiredLocked(nowMs, cancelled);
    const bool anywhere = m_seamlessCoop && m_seamlessAnywhereSummons;
    const auto hostMap = HostPlacementMap(hostPlacement);
    const auto maxCount = std::max<qint64>(0, Integer(request, QStringLiteral("GetMaxCount"), 20));
    if (!maxCount) return {};

    QList<QString> ownedClaims, candidates;
    QString reservation;
    for (auto it = m_records.begin(); it != m_records.end(); ++it) {
        if (room.room && it->reservationExpires && it->claimant.user == caller.user) {
            if (!(it->claimantRoom == room) || it->claimant.tokenHash != caller.tokenHash) {
                ClearReservation(*it, cancelled);
            } else {
                reservation = it.key();
            }
        }
        if (!it->publisher.user || it->publisher.user == caller.user || Expired(*it, nowMs) ||
            !MatchesSearch(request, it->advertisement, anywhere)) continue;
        if (room.room && (it->state == State::Claimed || it->state == State::Delivered) &&
            it->claimant.user == caller.user && it->claimant.tokenHash == caller.tokenHash) {
            ownedClaims.append(it.key());
        } else if (it->state == State::Advertised || (it->state == State::Preparing && it->reservationExpires)) {
            if (!it->reservationExpires || nowMs >= it->reservationExpires) candidates.append(it.key());
        }
    }
    const auto newer = [&](const QString& a, const QString& b) {
        const auto& left = *m_records.constFind(a);
        const auto& right = *m_records.constFind(b);
        return left.updatedAtMs == right.updatedAtMs ? a < b : left.updatedAtMs > right.updatedAtMs;
    };
    std::sort(ownedClaims.begin(), ownedClaims.end(), newer);
    std::sort(candidates.begin(), candidates.end(), newer);

    QList<QByteArray> results;
    for (const auto& key : ownedClaims) {
        const auto& sign = *m_records.constFind(key);
        results.append(PrepareAdvertisementForSearch(sign.rawAdvertisement, sign.advertisement, request, anywhere));
        if (results.size() >= maxCount) return results;
    }
    if (!reservation.isEmpty()) {
        const auto& sign = *m_records.constFind(reservation);
        if (!sign.publisherRemoved && sign.state == State::Advertised &&
            MatchesSearch(request, sign.advertisement, anywhere))
            results.append(PrepareAdvertisementForSearch(sign.rawAdvertisement, sign.advertisement, request, anywhere));
        return results;
    }
    for (const auto& key : candidates) {
        auto& sign = *m_records.find(key);
        if (room.room && anywhere && hostMap && results.isEmpty() && sign.state == State::Advertised &&
            Integer(sign.advertisement, QStringLiteral("AreaId"), -1) != *hostMap) {
            sign.state = State::Preparing;
            sign.claimant = caller;
            sign.claimantRoom = room;
            const auto ttl = std::min(RelayAuth::PlacementTtlMs, m_seamlessTtlMs);
            sign.reservationExpires = nowMs + ttl;
            sign.hostPlacement = hostPlacement;
            placement = PlacementFor(sign, ttl);
            break;
        }
        results.append(PrepareAdvertisementForSearch(sign.rawAdvertisement, sign.advertisement, request, anywhere));
        if (results.size() >= maxCount) break;
    }
    return results;
}

SummonBroker::ClaimResult SummonBroker::Claim(''')

    edit("src/webapi_routes_bloodborne.h", "class QHttpServer;", "class QHttpServer;\nclass Database;\nstruct SharedState;")
    edit("src/webapi_routes_bloodborne.h", "void RegisterBloodborneRoutes(QHttpServer& http, bool seamlessCoop);",
         "void RegisterBloodborneRoutes(QHttpServer& http, bool seamlessCoop, Database& db, SharedState& shared);")
    edit("src/webapi_server.cpp", "WebApiRoutes::RegisterBloodborneRoutes(*m_http, m_config->IsBloodborneSeamlessCoopEnabled());",
         "WebApiRoutes::RegisterBloodborneRoutes(*m_http, m_config->IsBloodborneSeamlessCoopEnabled(), *m_db, *m_shared);")
    edit(routes, '#include "bloodborne_summon_broker.h"', '''#include "bloodborne_summon_broker.h"
#include "authenticated_relay.h"
#include "client_session.h"
#include "webapi_auth.h"
#include <QCryptographicHash>''')
    edit(routes, "void RegisterBloodborneRoutes(QHttpServer& http, bool seamlessCoop) {",
         "void RegisterBloodborneRoutes(QHttpServer& http, bool seamlessCoop, Database& db, SharedState& shared) {")
    edit(routes, "    options.seamlessCoop = seamlessCoop;", '''    options.seamlessCoop = seamlessCoop;
    options.relayPlacement = [&shared](const RelayAuth::PlacementRequest& placement) {
        if (shared.relayPlacement) shared.relayPlacement(placement);
    };
    options.relayCancelPlacement = [&shared](quint64 record) {
        if (shared.relayCancelPlacement) shared.relayCancelPlacement(record);
    };''')
    # All four endpoint lambdas have the same capture. Use one exact whole-file
    # transformation via route-specific Trace anchors so duplicate anchors fail.
    for route in ("create", "get", "delete", "request"):
        edit(routes, f'''               [broker](const QHttpServerRequest& request) -> QHttpServerResponse {{
                   TraceSummonRequest("{route}", request);''', f'''               [broker, seamlessCoop, &db, &shared](const QHttpServerRequest& request) -> QHttpServerResponse {{
                   Bloodborne::SummonIdentity caller;
                   if (seamlessCoop) {{
                       auto auth = WebApiAuth::Authenticate(request,db);
                       if (!auth.userId) return std::move(auth.errorResponse);
                       if (*auth.userId <= 0 || static_cast<quint64>(*auth.userId) > 0xffffffffULL)
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::Unauthorized);
                       caller.user = static_cast<quint64>(*auth.userId);
                       const QByteArray token = QString::fromUtf8(request.value("Authorization")).trimmed().mid(7).trimmed().toUtf8();
                       caller.tokenHash = QCryptographicHash::hash(token,QCryptographicHash::Sha256);
                   }}
                   if (!seamlessCoop) TraceSummonRequest("{route}", request);''')
    edit(routes, "QDateTime::currentMSecsSinceEpoch());\n                   if (!result.pendingClaim.isEmpty())", '''QDateTime::currentMSecsSinceEpoch(),caller);
                   if (!result.authorized) return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);
                   if (!result.pendingClaim.isEmpty())''')
    edit(routes, '''                   const QList<QByteArray> signs =
                       broker->Search(*body, QDateTime::currentMSecsSinceEpoch(),
                                      request.value(HostPlacementHeader));''', '''                   QByteArray placement = request.value(HostPlacementHeader);
                   RelayAuth::SummonAdmission admission;
                   if (caller.user) {
                       using Access = RelayAuth::SummonAccess;
                       if (shared.relaySummonAdmission) admission = shared.relaySummonAdmission(caller.user);
                       if (admission.access == Access::Busy) {
                           qInfo() << "Bloodborne summon: search matching busy for account" << caller.user;
                           return SummonListResponse({});
                       }
                       if (admission.access == Access::Denied)
                           return SummonListResponse({});
                       if (!placement.isEmpty() && !RelayAuth::ValidPlacement(placement))
                           return InvalidRequest(QStringLiteral("Invalid host placement"));
                       // Native Bloodborne selects a candidate before it creates its room.
                       // Only an existing owner can reserve a sign or queue placement in Search.
                       if (admission.access == Access::NoRoom)
                           placement.clear();
                   }
                   const QList<QByteArray> signs =
                       broker->Search(*body, QDateTime::currentMSecsSinceEpoch(), placement, caller, admission.room);''')
    edit(routes, "const auto result = broker->Consume(*body, QDateTime::currentMSecsSinceEpoch());", '''const auto result = broker->Consume(*body, QDateTime::currentMSecsSinceEpoch(),caller);
                   if (!result.authorized) return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);''')
    edit(routes, "                   const auto result =\n                       broker->Claim", '''                   RelayAuth::SummonAdmission admission;
                   if (caller.user) {
                       if (shared.relaySummonAdmission) admission = shared.relaySummonAdmission(caller.user);
                       if (admission.access == RelayAuth::SummonAccess::Busy) {
                           qInfo() << "Bloodborne summon: claim matching busy for account" << caller.user;
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::ServiceUnavailable);
                       }
                       if (admission.access != RelayAuth::SummonAccess::Owner) {
                           qInfo() << "Bloodborne summon: claim requires current room owner, account" << caller.user;
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);
                       }
                   }
                   if (caller.user && !request.value(HostPlacementHeader).isEmpty() &&
                       !RelayAuth::ValidPlacement(request.value(HostPlacementHeader)))
                       return InvalidRequest(QStringLiteral("Invalid host placement"));
                   const auto result =
                       broker->Claim''')
    edit(routes, "\n                                     request.value(HostPlacementHeader));", "\n                                     request.value(HostPlacementHeader),caller,admission.room);")

    edit(routes, '''                       qWarning() << "Bloodborne summon: conflicting claim for session"
                                  << result.targetSessionId;''',
         '''                       qWarning().nospace() << "relay placement dropped reason=claim-conflict targetSessionId="
                                            << result.targetSessionId << " caller=" << caller.user;''')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadnet", type=Path, required=True)
    patch_shadnet(parser.parse_args().shadnet)
