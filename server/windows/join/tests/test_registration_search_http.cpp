// SPDX-License-Identifier: GPL-2.0-or-later
#include "client_session.h"
#include "database.h"
#include "summon_claim_origin.hpp"
#include "webapi_routes_bloodborne.h"

#include <QCoreApplication>
#include <QEventLoop>
#include <QHttpServer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QTcpServer>
#include <QTemporaryDir>
#include <QTimer>
#include <cstdlib>
#include <iostream>

namespace {
void require(bool ok, const char* detail) {
    if (!ok) {
        std::cerr << detail << '\n';
        std::exit(1);
    }
}
}

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    QTemporaryDir directory;
    Database db("registration_http_test");
    require(directory.isValid() && db.Open(directory.filePath("game-test.db")), "temporary database");
    auto account = [&](const QString& name) {
        const QString password = "fixture-password";
        require(!db.CreateAccount(name, password, QStringLiteral(""), name + "@local.invalid").has_value(),
                "create fixture account");
        auto user = db.CheckUser(name, password, QStringLiteral(""), false);
        require(user.has_value(), "open fixture account");
        return *user;
    };
    const auto host = account("fixture_host");
    const auto guest = account("fixture_guest");
    const auto otherHost = account("fixture_other");
    SharedState state;
    state.config = nullptr;
    state.relayHost = [&](quint64 user) { return user == host.userId || user == otherHost.userId; };
    state.relaySearchAccess = [&](quint64 user) {
        return state.relayHost(user) ? SharedState::SummonSearchAccess::Owner : SharedState::SummonSearchAccess::Denied;
    };
    int placements = 0;
    state.relayPlacement = [&](quint64, const QByteArray&, quint64, const QByteArray&, const QByteArray&) {
        ++placements;
    };
    QHttpServer http;
    WebApiRoutes::RegisterBloodborneRoutes(http, true, db, state);
    auto* listener = new QTcpServer(&http);
    require(listener->listen(QHostAddress::LocalHost, 0) && http.bind(listener), "local listener");
    QNetworkAccessManager client;
    client.setProxy(QNetworkProxy::NoProxy);

    auto postRaw = [&](const QString& operation, const UserRecord& user, const QByteArray& body,
                       const QByteArray& capability = {}, bool placement = false,
                       const QByteArray& claimOrigin = {}, int expectedStatus = 200) {
        const auto path = QString("http://127.0.0.1:%1/summon_messenger/%2").arg(listener->serverPort()).arg(operation);
        QNetworkRequest request{QUrl(path)};
        request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
        request.setRawHeader("Authorization", "Bearer " + user.token.toUtf8());
        if (!capability.isEmpty()) request.setRawHeader("X-TheHunt-Registration-Discovery", capability);
        if (!claimOrigin.isNull()) request.setRawHeader(TheHunt::Summon::claim_origin_header, claimOrigin);
        if (placement) request.setRawHeader("X-ShadPS4-Bloodborne-Host-Placement",
                                            "1,18010000,3f800000,40000000,40400000,00000000,2412951");
        auto* reply = client.post(request, body);
        QEventLoop loop;
        QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
        QTimer::singleShot(5000, &loop, &QEventLoop::quit);
        if (!reply->isFinished()) loop.exec();
        require(reply->isFinished() && reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt() == expectedStatus,
                "unexpected local HTTP status");
        auto response = reply->readAll();
        delete reply;
        return response;
    };
    auto post = [&](const QString& operation, const UserRecord& user, const QJsonObject& body,
                    const QByteArray& capability = {}, bool placement = false) {
        return postRaw(operation, user, QJsonDocument(body).toJson(QJsonDocument::Compact), capability, placement);
    };
    QJsonObject sign{{"SessionId", "guest-sign"}, {"UserId", 72}, {"CharaId", 12}, {"AreaId", 0x17000000},
                     {"AreaRegionId", 230010}, {"SummonData", "fixture"}, {"SummonDataVersion", 3},
                     {"SummonType", 0}, {"MatchingLevel", 46}};
    QJsonObject search{{"SessionId", "host"}, {"UserId", 71}, {"AreaId", 0x18010000},
                       {"AreaRegionId", 241010}, {"SummonDataVersion", 3}, {"MatchingLevel", 46}};
    QJsonObject close{{"SessionId", "guest-sign"}, {"UserId", 72}, {"Force", true}};
    auto list = [](const QByteArray& body) {
        auto document = QJsonDocument::fromJson(body).object();
        require(document.value("SummonDataList").isArray(), "search response shape");
        return document.value("SummonDataList").toArray();
    };
    auto reference = [&] {
        const auto rows = list(post("get", host, search, "1", true));
        require(rows.size() == 1, "discovery must return the advertised guest");
        auto id = rows[0].toObject().value("TheHuntRegistrationId").toString();
        require(!id.isEmpty(), "selected search row needs a server reference");
        return id;
    };

    post("create", guest, sign);
    const auto legacy = post("get", host, search);
    const auto legacyRows = list(legacy);
    require(legacyRows.size() == 1 && !legacyRows[0].toObject().contains("TheHuntRegistrationId"), "legacy search");
    require(post("get", host, search, "2") == legacy, "unsupported discovery version");
    const auto first = reference();
    post("create", guest, sign);
    require(reference() == first && post("get", host, search) == legacy, "refresh keeps both representations");
    require(list(post("get", guest, search, "1")).isEmpty(), "denied search");
    post("delete", guest, close);
    require(list(post("get", host, search, "1")).isEmpty(), "withdrawn registration");
    post("create", guest, sign);
    require(reference() != first, "same alias after withdrawal must get a fresh reference");
    require(placements == 0, "discovery alone must not send an invitation");
    std::cout << "PASS registration discovery over loopback HTTP\n";

    // The real HTTP route must forward the publisher's exact native integer to the broker.
    sign["AreaId"] = 0x18010000;
    sign["AreaRegionId"] = 241010;
    auto adA = QJsonDocument(sign).toJson(QJsonDocument::Compact);
    adA.replace("\"CharaId\":12", "\"CharaId\":9223372036854775808");
    auto adB = QByteArray(adA).replace("9223372036854775808", "9223372036854775809");
    require(adA != adB && QJsonDocument::fromJson(adA) == QJsonDocument::fromJson(adB), "Qt numeric collision fixture");
    postRaw("create", guest, adA);
    const auto idA = reference();
    const QByteArray claimA = R"({"SessionId":"host","UserId":71,"TargetUserId":72,"TargetCharaId":9223372036854775808,"HostData":"A"})";
    postRaw("request", host, claimA, {}, true);
    require(placements == 1, "original host must have claimed A");
    require(!QJsonDocument::fromJson(postRaw("create", guest, adB)).object().contains("HostData"), "B inherited A's claim");
    auto otherSearch = search;
    otherSearch["SessionId"] = "other";
    otherSearch["UserId"] = 73;
    auto rowsB = list(post("get", otherHost, otherSearch, "1"));
    require(rowsB.size() == 1, "other host must see B");
    const auto idB = rowsB[0].toObject().value("TheHuntRegistrationId").toString();
    require(!idB.isEmpty() && idB != idA, "new character must receive its own reference");
    postRaw("create", guest, adB);
    require(reference() == idB, "B's renewal must keep its reference");
    const QByteArray claimB = R"({"SessionId":"other","UserId":73,"TargetUserId":72,"TargetCharaId":9223372036854775809,"HostData":"B"})";
    postRaw("request", otherHost, claimB, {}, true);
    require(QJsonDocument::fromJson(postRaw("create", guest, adB)).object().value("HostData") == "B",
            "B must receive the new host's claim");
    std::cout << "PASS exact character switch after claim over loopback HTTP\n";

    // Exercise the actual HTTP parser, broker and guest poll. Native room admission
    // is a controlled fixture here, so it can also inspect each forwarded argument.
    post("delete", guest, close);
    postRaw("create", guest, adA);
    const auto signId = reference();
    const auto signText = signId.toLatin1();
    constexpr quint64 joined = 18446744073709551614ULL;
    constexpr quint64 attempt = 18446744073709551615ULL;
    constexpr std::string_view run = "f123456789abcdef0123456789abce01";
    const auto encoded = TheHunt::Summon::EncodeClaimOrigin({std::string_view(signText), 36, 16, joined, attempt, run});
    require(encoded.has_value(), "encode selected origin");
    const auto origin = QByteArray::fromStdString(*encoded);
    int qualified = 0;
    state.relayBoundClaimPlacement = [&](const Bloodborne::BoundPlacementRequest& request,
                                         const Bloodborne::BoundCommit& commit) {
        ++qualified;
        require(request.origin.registrationId == signId && request.origin.room == 36 &&
                request.origin.ownerMember == 16 && request.origin.ownerJoined == joined &&
                request.origin.attempt == attempt && std::string_view(request.origin.hostRun) == run,
                "wire origin changed in transit");
        require(request.host.user == quint64(host.userId) && request.guest.user == quint64(guest.userId) &&
                request.host.tokenHash.size() == 32 && request.guest.tokenHash.size() == 32,
                "bound claim needs both authenticated HTTP identities");
        require(request.step == (qualified == 1 ? Bloodborne::BoundClaimStep::First : Bloodborne::BoundClaimStep::Retry) &&
                !request.placement.isEmpty(), "wrong logical claim or pose");
        const QByteArray scope("fixture-room36");
        commit(scope);
        return Bloodborne::BoundPlacementResult{Bloodborne::BoundPlacementStatus::Granted, scope};
    };
    state.relayCancelBoundPlacement = [](const auto&, const auto&) {};
    const int legacyBefore = placements;
    for (const auto& bad : {QByteArray("1"), origin + ",1", QByteArray(origin).replace(",16,", ",65536,"),
                            QByteArray(origin).replace(",36,", ",036,"), QByteArray(origin).replace(",16,", ",+16,"),
                            QByteArray(origin).replace("f123", "F123"), QByteArray(origin).replace("f123", "f12")}) {
        postRaw("request", host, claimA, {}, true, bad, 400);
    }
    postRaw("request", host, claimA, {}, false, origin, 400);
    postRaw("request", guest, claimA, {}, true, origin, 403);
    require(qualified == 0 && placements == legacyBefore, "malformed/denied origin fell back to a claim");
    postRaw("request", host, claimA, {}, true, origin);
    postRaw("request", host, claimA, {}, true, origin);
    require(qualified == 2 && placements == legacyBefore, "qualified claim and its retry must use their own route");
    require(QJsonDocument::fromJson(postRaw("create", guest, adA)).object().value("HostData") == "A",
            "guest must receive its qualified native claim");
    std::cout << "PASS qualified claim and retry through HTTP and guest poll\n";
}
