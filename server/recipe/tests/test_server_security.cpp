// SPDX-License-Identifier: GPL-2.0-or-later
// Entirely offline: no socket is opened and every database/state is synthetic.
#include <QCoreApplication>
#include <QJsonDocument>
#include <QSqlQuery>
#include <QTemporaryDir>
#include <cstdlib>
#include <iostream>
#include "bloodborne_summon_broker.h"
#include "database.h"
#include "stun_server.h"
#include "trusted_network.h"
#include "webapi_auth.h"

#define CHECK(x) do { if (!(x)) { std::cerr << "line " << __LINE__ << ": " << #x << '\n'; std::abort(); } } while (false)

static QJsonObject Json(const char* value) {
    return QJsonDocument::fromJson(value).object();
}

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    for (const char* address : {"127.0.0.1", "127.0.0.2", "192.168.1.2", "172.16.0.1",
                                "172.31.255.254", "10.1.2.3", "100.64.0.1", "100.127.255.254", "26.1.2.3"})
        CHECK(TrustedNetwork::Allows(QHostAddress(address)));
    for (const char* address : {"0.0.0.0", "8.8.8.8", "172.32.0.1", "172.15.255.255",
                                "100.63.255.255", "100.128.0.0", "::", "::1", "2001:db8::1"})
        CHECK(!TrustedNetwork::Allows(QHostAddress(address)));

    SharedState state;
    SharedState::ClientEntry player;
    player.npid = "player";
    player.signalingAddress = QHostAddress("100.64.0.2");
    state.clients.insert(42, player);
    state.npidToUserId.insert("player", 42);
    CHECK(!StunSecurity::RecordEndpoint(&state, "offline", QHostAddress("100.64.0.2"), 1234));
    CHECK(!StunSecurity::RecordEndpoint(&state, "player", QHostAddress("100.64.0.3"), 1234));
    CHECK(!StunSecurity::RecordEndpoint(&state, "player", QHostAddress("100.64.0.2"), 0));
    CHECK(!StunSecurity::RecordEndpoint(&state, "player", QHostAddress("8.8.8.8"), 1234));
    CHECK(state.matching.udpExt.isEmpty());
    CHECK(StunSecurity::RecordEndpoint(&state, "player", QHostAddress("100.64.0.2"), 1234));
    CHECK(state.matching.udpExt["player"].second == 1234);
    CHECK(!StunSecurity::RecordEndpoint(&state, "player", QHostAddress("100.64.0.3"), 5678));
    CHECK(state.matching.udpExt["player"].second == 1234);
    CHECK(StunSecurity::RecordEndpoint(&state, "player", QHostAddress("::ffff:100.64.0.2"), 5678));
    CHECK(state.matching.udpExt["player"].first == "100.64.0.2");
    state.clients.remove(42);
    CHECK(!StunSecurity::RecordEndpoint(&state, "player", QHostAddress("100.64.0.2"), 9999));

    const auto sign = Json(R"({"SessionId":"guest","UserId":42,"SummonData":"opaque"})");
    const auto claim = Json(R"({"SessionId":"guest","UserId":43})");
    Bloodborne::SummonBroker::Options options;
    options.ttlMs = 1000;
    options.seamlessCoop = true;
    Bloodborne::SummonBroker broker(options);
    broker.Advertise(sign, QJsonDocument(sign).toJson(), 100);
    broker.Claim(claim, QJsonDocument(claim).toJson(), 101);
    for (const char* invalid : {R"({"SessionId":"guest","Force":true})",
                               R"({"UserId":42,"Force":true})",
                               R"({"SessionId":"guest","UserId":0,"Force":true})",
                               R"({"SessionId":"guest","UserId":true,"Force":true})",
                               R"({"SessionId":"guest","UserId":"42","Force":true})",
                               R"({"SessionId":"guest","UserId":42.5,"Force":true})",
                               R"({"SessionId":"guest","UserId":43,"Force":true})",
                               R"({"SessionId":"other","UserId":42,"SeamlessLeave":true})"}) {
        const auto result = broker.Consume(Json(invalid), 102);
        CHECK(result.consumed == 0 && result.retained == 0);
    }
    const auto retained = broker.Consume(Json(R"({"SessionId":"guest","UserId":42})"), 103);
    CHECK(retained.consumed == 0 && retained.retained == 1);
    const auto secondSign = Json(R"({"SessionId":"second-guest","UserId":42,"SummonData":"opaque"})");
    broker.Advertise(secondSign, QJsonDocument(secondSign).toJson(), 103);
    const auto consumed = broker.Consume(Json(R"({"SessionId":"guest","UserId":42,"Force":true})"), 104);
    CHECK(consumed.consumed == 1);
    CHECK(broker.StateFor("second-guest", 42, 104) == Bloodborne::SummonBroker::State::Advertised);
    CHECK(broker.Consume(Json(R"({"SessionId":"second-guest","UserId":42,"SeamlessLeave":true})"), 105).consumed == 1);
    // Correct self-reported selectors are intentionally still accepted for legacy
    // compatibility. This test does NOT establish authenticated ownership.

    QTemporaryDir temporary;
    CHECK(temporary.isValid());
    Database database("offline_security_test");
    CHECK(database.Open(temporary.filePath("synthetic.db")));
    CHECK(!database.CreateAccount("player", "synthetic-password", "", "player@local.invalid").has_value());
    const auto user = database.CheckUser("player", "synthetic-password", "", false);
    CHECK(user.has_value());
    const QByteArray oldBearer = "Bearer " + user->token.toUtf8();
    CHECK(WebApiAuth::AuthenticateBearer(oldBearer, database).userId == user->userId);
    for (const QByteArray& field : {QByteArray(), QByteArray("Bearer "), QByteArray("Basic secret"), QByteArray("Bearer wrong")})
        CHECK(!WebApiAuth::AuthenticateBearer(field, database).userId.has_value());
    CHECK(database.BanUser(user->userId, true));
    CHECK(!WebApiAuth::AuthenticateBearer(oldBearer, database).userId.has_value());
    const auto banned = database.CheckUser("player", "synthetic-password", "", false);
    CHECK(banned.has_value() && banned->banned && banned->token != user->token);
    const QByteArray newBearer = "Bearer " + banned->token.toUtf8();
    CHECK(!WebApiAuth::AuthenticateBearer(newBearer, database).userId.has_value());
    CHECK(database.BanUser(user->userId, false));
    CHECK(!WebApiAuth::AuthenticateBearer(oldBearer, database).userId.has_value());
    CHECK(WebApiAuth::AuthenticateBearer(newBearer, database).userId == user->userId);
    // Also reject bans applied directly to SQLite without rotating the token.
    QSqlQuery query(database.Conn());
    CHECK(query.exec("UPDATE account SET banned=1"));
    CHECK(!WebApiAuth::AuthenticateBearer(newBearer, database).userId.has_value());
    std::cout << "PASS: trusted networks, STUN endpoint admission, summon selectors, bearer revocation\n";
}
