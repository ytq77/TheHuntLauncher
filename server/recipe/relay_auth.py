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
        src/authenticated_relay.cpp src/database.cpp src/config.cpp src/bloodborne_summon_broker.cpp "${PROTO_OUT}/shadnet.pb.h")
    target_include_directories(test_relay_auth PRIVATE src "${PROTO_OUT}"
        "${CMAKE_CURRENT_SOURCE_DIR}/externals/protobuf/src")
    target_link_libraries(test_relay_auth PRIVATE Qt6::Core Qt6::Network Qt6::Sql libprotobuf)
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
    edit("src/client_session.h", "    ConfigManager* config;", "    ConfigManager* config;\n"
         "    std::atomic<bool> relayAuthReady{false}; // True only after native relay listen succeeds.\n"
         "    // Main Qt thread only: authenticated broker -> relay admission, never client claims.\n"
         "    std::function<bool(quint64)> relayHost;\n"
         "    enum class SummonSearchAccess { Denied, NoRoom, Owner };\n"
         "    std::function<SummonSearchAccess(quint64)> relaySearchAccess;\n"
         "    std::function<void(quint64,const QByteArray&,quint64,const QByteArray&,const QByteArray&)> relayPlacement;\n"
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
             for name in ("authenticated_relay.h", "authenticated_relay.cpp")}
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
    edit(header, "#include <optional>", "#include <optional>\n#include <functional>")
    edit(header, "class SummonBroker {", '''// Server-owned identity; never parsed from JSON UserId/TargetUserId/CharaId.
struct SummonIdentity { quint64 user = 0; QByteArray tokenHash; };

class SummonBroker {''')
    edit(header, "        bool seamlessAnywhereSummons = false;", '''        bool seamlessAnywhereSummons = false;
        std::function<void(const SummonIdentity&, const SummonIdentity&, const QByteArray&)> relayPlacement;
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
                             const QByteArray& hostPlacement = {}, const SummonIdentity& caller = {});
    ClaimResult Claim(const QJsonObject& request, const QByteArray& rawRequest, qint64 nowMs,
                      const QByteArray& hostPlacement = {}, const SummonIdentity& caller = {});
    ConsumeResult Consume(const QJsonObject& request, qint64 nowMs, const SummonIdentity& caller = {});''')
    edit(header, "        State state = State::Advertised;\n        QJsonObject advertisement;",
         "        State state = State::Advertised;\n        SummonIdentity publisher, claimant;\n        QJsonObject advertisement;")
    edit(header, "    QMutex m_mutex;", '''    std::function<void(const SummonIdentity&, const SummonIdentity&, const QByteArray&)> m_relayPlacement;
    std::function<void(quint64)> m_relayCancelPlacement;
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
        if (it == m_records.end() && m_records.size() >= 1024) return {State::Advertised, {}, {}, false};
        if (it == m_records.end() && m_relayCancelPlacement) m_relayCancelPlacement(caller.user);
        if (it != m_records.end() && (it->publisher.tokenHash != caller.tokenHash ||
            it->advertisement.value(QStringLiteral("CharaId")) != body.value(QStringLiteral("CharaId")))) {
            if (m_relayCancelPlacement) m_relayCancelPlacement(caller.user);
            it->state = State::Consumed;
        }
    }
    if (it == m_records.end() || it->state == State::Consumed) {''')
    edit(cpp, "        record.state = State::Advertised;\n        record.advertisement = body;",
         "        record.state = State::Advertised;\n        record.publisher = caller;\n        record.advertisement = body;")
    edit(cpp, "    it->advertisement = body;\n    it->rawAdvertisement = rawBody;",
         "    if (caller.user) it->publisher = caller;\n    it->advertisement = body;\n    it->rawAdvertisement = rawBody;")
    edit(cpp, '''QList<QByteArray> SummonBroker::Search(const QJsonObject& request, qint64 nowMs,
                                       const QByteArray& hostPlacement) {''', '''QList<QByteArray> SummonBroker::Search(const QJsonObject& request, qint64 nowMs,
                                       const QByteArray& hostPlacement, const SummonIdentity& caller) {''')
    edit(cpp, '''    for (auto it = m_records.begin(); it != m_records.end(); ++it) {
        if (it->state == State::Advertised &&''', '''    for (auto it = m_records.begin(); it != m_records.end(); ++it) {
        if (caller.user && (!it->publisher.user || it->publisher.user == caller.user ||
            (it->claimant.user && it->claimant.user != caller.user))) continue;
        if (caller.user && it->state == State::Preparing && it->claimant.user == caller.user &&
            MatchesSearch(request, it->advertisement, anywhereSummons) && !hostPlacement.isEmpty()) {
            if (m_relayPlacement) m_relayPlacement(caller,it->publisher,hostPlacement);
        }
        if (it->state == State::Advertised &&''')
    edit(cpp, "                it->preparationRequester = requester;\n                it->updatedAtMs = nowMs;", '''                it->preparationRequester = requester;
                it->claimant = caller;
                if (caller.user && m_relayPlacement) m_relayPlacement(caller,it->publisher,hostPlacement);
                it->updatedAtMs = nowMs;''')
    edit(cpp, '''SummonBroker::ClaimResult SummonBroker::Claim(const QJsonObject& request,
                                              const QByteArray& rawRequest, qint64 nowMs,
                                              const QByteArray& hostPlacement) {''', '''SummonBroker::ClaimResult SummonBroker::Claim(const QJsonObject& request,
                                              const QByteArray& rawRequest, qint64 nowMs,
                                              const QByteArray& hostPlacement, const SummonIdentity& caller) {''')
    edit(cpp, "    ClaimResult result;\n    result.targetSessionId", '''    if (caller.user && (!target->publisher.user || target->publisher.user == caller.user ||
        (target->claimant.user && target->claimant.user != caller.user) ||
        (targetUser.has_value() && Integer(target->advertisement,QStringLiteral("UserId"),-1) != *targetUser) ||
        (!targetChara.isUndefined() && !targetChara.isNull() && target->advertisement.value(QStringLiteral("CharaId")) != targetChara)))
        return {ClaimStatus::Conflict, {}, -1};
    ClaimResult result;
    result.targetSessionId''')
    edit(cpp, "    target->state = State::Claimed;\n    target->claim = request;", "    target->state = State::Claimed;\n    target->claimant = caller;\n    target->claim = request;")
    edit(cpp, "    result.status = ClaimStatus::Claimed;\n    return result;", '''    result.status = ClaimStatus::Claimed;
    if (caller.user && m_relayPlacement && !target->hostPlacement.isEmpty())
        m_relayPlacement(caller,target->publisher,target->hostPlacement);
    return result;''')
    edit(cpp, "SummonBroker::ConsumeResult SummonBroker::Consume(const QJsonObject& request, qint64 nowMs) {",
         "SummonBroker::ConsumeResult SummonBroker::Consume(const QJsonObject& request, qint64 nowMs, const SummonIdentity& caller) {")
    edit(cpp, "        if (sessionMatches && userMatches && it->state != State::Consumed) {", '''        if (sessionMatches && userMatches && it->state != State::Consumed) {
            if (caller.user && it->publisher.user != caller.user) return {0,0,{},false};''')
    edit(cpp, "            it->state = State::Consumed;\n            ++result.consumed;", '''            it->state = State::Consumed;
            if (it->publisher.user && m_relayCancelPlacement) m_relayCancelPlacement(it->publisher.user);
            ++result.consumed;''')
    edit(cpp, "        if (nowMs - it->updatedAtMs > ttl) {\n            it = m_records.erase(it);", '''        if (nowMs - it->updatedAtMs > ttl) {
            if (it->publisher.user && m_relayCancelPlacement) m_relayCancelPlacement(it->publisher.user);
            it = m_records.erase(it);''')

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
    options.relayPlacement = [&shared](const Bloodborne::SummonIdentity& host,
                                      const Bloodborne::SummonIdentity& guest, const QByteArray& text) {
        if (shared.relayPlacement) shared.relayPlacement(host.user,host.tokenHash,guest.user,guest.tokenHash,text);
    };
    options.relayCancelPlacement = [&shared](quint64 user) {
        if (shared.relayCancelPlacement) shared.relayCancelPlacement(user);
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
                   if (caller.user) {
                       using Access = SharedState::SummonSearchAccess;
                       const auto access = shared.relaySearchAccess ? shared.relaySearchAccess(caller.user) : Access::Denied;
                       if (access == Access::Denied)
                           return SummonListResponse({});
                       if (!placement.isEmpty() && !RelayAuth::ValidPlacement(placement))
                           return InvalidRequest(QStringLiteral("Invalid host placement"));
                       // Native Bloodborne selects a candidate before it creates its room.
                       // Only an existing owner can reserve a sign or queue placement in Search.
                       if (access == Access::NoRoom)
                           placement.clear();
                   }
                   const QList<QByteArray> signs =
                       broker->Search(*body, QDateTime::currentMSecsSinceEpoch(), placement, caller);''')
    edit(routes, "const auto result = broker->Consume(*body, QDateTime::currentMSecsSinceEpoch());", '''const auto result = broker->Consume(*body, QDateTime::currentMSecsSinceEpoch(),caller);
                   if (!result.authorized) return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);''')
    edit(routes, "                   const auto result =\n                       broker->Claim", '''                   if (caller.user && (!shared.relayHost || !shared.relayHost(caller.user)))
                       return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);
                   if (caller.user && !request.value(HostPlacementHeader).isEmpty() &&
                       !RelayAuth::ValidPlacement(request.value(HostPlacementHeader)))
                       return InvalidRequest(QStringLiteral("Invalid host placement"));
                   const auto result =
                       broker->Claim''')
    edit(routes, "\n                                     request.value(HostPlacementHeader));", "\n                                     request.value(HostPlacementHeader),caller);")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadnet", type=Path, required=True)
    patch_shadnet(parser.parse_args().shadnet)
