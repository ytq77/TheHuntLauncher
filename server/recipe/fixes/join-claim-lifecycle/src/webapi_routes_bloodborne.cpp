// SPDX-FileCopyrightText: Copyright 2026 shadNet Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "webapi_routes_bloodborne.h"

#include <cstdlib>
#include <memory>
#include <optional>

#include <QDateTime>
#include <QDebug>
#include <QHttpHeaders>
#include <QHttpServer>
#include <QHttpServerRequest>
#include <QHttpServerResponse>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QList>
#include <QString>

#include "bloodborne_summon_broker.h"
#include "claim_diagnostic.h"
#include "authenticated_relay.h"
#include "client_session.h"
#include "webapi_auth.h"
#include <QCryptographicHash>

namespace WebApiRoutes {
namespace {

constexpr auto HostPlacementHeader = "X-ShadPS4-Bloodborne-Host-Placement";

QHttpServerResponse SummonResponse(const QString& messageId, const QByteArray& hostPlacement = {}) {
    QJsonObject body;
    body.insert(QStringLiteral("ResKind"), 0);
    body.insert(QStringLiteral("MessageId"), messageId);
    QHttpServerResponse response{"application/json",
                                 QJsonDocument(body).toJson(QJsonDocument::Compact),
                                 QHttpServerResponse::StatusCode::Ok};
    if (!hostPlacement.isEmpty()) {
        QHttpHeaders headers = response.headers();
        if (headers.append(HostPlacementHeader, hostPlacement)) {
            response.setHeaders(std::move(headers));
        }
    }
    return response;
}

QHttpServerResponse SummonListResponse(const QList<QByteArray>& signs) {
    QByteArray body = "{\"SummonDataList\":[";
    for (qsizetype index = 0; index < signs.size(); ++index) {
        if (index != 0) {
            body.append(',');
        }
        body.append(signs[index]);
    }
    body.append("],\"ResKind\":0,\"MessageId\":\"SummonDataGetListResponse\"}");
    return QHttpServerResponse{"application/json", body, QHttpServerResponse::StatusCode::Ok};
}

QHttpServerResponse RawJsonResponse(const QByteArray& body, const QByteArray& hostPlacement = {}) {
    QHttpServerResponse response{"application/json", body, QHttpServerResponse::StatusCode::Ok};
    if (!hostPlacement.isEmpty()) {
        QHttpHeaders headers = response.headers();
        if (headers.append(HostPlacementHeader, hostPlacement)) {
            response.setHeaders(std::move(headers));
        }
    }
    return response;
}

QHttpServerResponse InvalidRequest(const QString& reason) {
    QJsonObject body;
    body.insert(QStringLiteral("ResKind"), 1);
    body.insert(QStringLiteral("MessageId"), QString());
    body.insert(QStringLiteral("Error"), reason);
    return QHttpServerResponse{"application/json",
                               QJsonDocument(body).toJson(QJsonDocument::Compact),
                               QHttpServerResponse::StatusCode::BadRequest};
}

std::optional<QJsonObject> ParseRequest(const QHttpServerRequest& request) {
    QJsonParseError error{};
    const QJsonDocument document = QJsonDocument::fromJson(request.body(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        return std::nullopt;
    }
    return document.object();
}

qint64 Integer(const QJsonObject& object, const QString& key, qint64 fallback = 0) {
    const QJsonValue value = object.value(key);
    return value.isDouble() ? value.toVariant().toLongLong() : fallback;
}

bool EnvEnabled(const char* name) {
    const char* value = std::getenv(name);
    return value != nullptr && value[0] != '\0' && QString::fromUtf8(value).trimmed() != "0";
}

void TraceSummonRequest(const char* route, const QHttpServerRequest& request) {
    if (!EnvEnabled("SHADNET_BLOODBORNE_RE_TRACE")) {
        return;
    }
    qInfo().noquote() << "Bloodborne summon RE" << route << request.body();
}

} // namespace

void RegisterBloodborneRoutes(QHttpServer& http, bool seamlessCoop, Database& db, SharedState& shared) {
    seamlessCoop = seamlessCoop || EnvEnabled("SHADNET_BLOODBORNE_SEAMLESS_COOP");
    Bloodborne::SummonBroker::Options options;
    options.seamlessCoop = seamlessCoop;
    options.relayPlacement = [&shared](const Bloodborne::SummonIdentity& host,
                                      const Bloodborne::SummonIdentity& guest, const QByteArray& text) {
        if (shared.relayPlacement) shared.relayPlacement(host.user,host.tokenHash,guest.user,guest.tokenHash,text);
    };
    options.relayClaimPlacement = [&shared](const Bloodborne::SummonIdentity& host,
        const Bloodborne::SummonIdentity& guest, const QByteArray& text, const QByteArray& scope) {
        if (shared.relayClaimPlacement)
            return shared.relayClaimPlacement(host.user,host.tokenHash,guest.user,guest.tokenHash,text,scope);
        if (scope.isEmpty() && shared.relayPlacement)
            shared.relayPlacement(host.user,host.tokenHash,guest.user,guest.tokenHash,text);
        return QByteArray{}; // Preserve first-claim queuing; no retry without its binding.
    };
    options.relayCancelPlacement = [&shared](quint64 user) {
        if (shared.relayCancelPlacement) shared.relayCancelPlacement(user);
    };
    auto broker = std::make_shared<Bloodborne::SummonBroker>(options);
    // Explicit opt-in, bounded from the first denial; never dump request bodies.
    auto claimDiagnostic = std::make_shared<ClaimDiagnosticWindow>(
        qEnvironmentVariable("THEHUNT_CLAIM_DIAGNOSTICS") == QStringLiteral("1"));

    qInfo() << "Bloodborne summon routes registered; seamless co-op"
            << (broker->IsSeamlessCoopEnabled() ? "enabled" : "disabled") << "anywhere summons"
            << (broker->IsSeamlessAnywhereSummonsEnabled() ? "enabled" : "disabled");

    http.route("/summon_messenger/create", QHttpServerRequest::Method::Post,
               [broker, seamlessCoop, &db, &shared](const QHttpServerRequest& request) -> QHttpServerResponse {
                   Bloodborne::SummonIdentity caller;
                   if (seamlessCoop) {
                       auto auth = WebApiAuth::Authenticate(request,db);
                       if (!auth.userId) return std::move(auth.errorResponse);
                       if (*auth.userId <= 0 || static_cast<quint64>(*auth.userId) > 0xffffffffULL)
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::Unauthorized);
                       caller.user = static_cast<quint64>(*auth.userId);
                       const QByteArray token = QString::fromUtf8(request.value("Authorization")).trimmed().mid(7).trimmed().toUtf8();
                       caller.tokenHash = QCryptographicHash::hash(token,QCryptographicHash::Sha256);
                   }
                   if (!seamlessCoop) TraceSummonRequest("create", request);
                   const auto body = ParseRequest(request);
                   if (!body || !Bloodborne::HasRequiredAdvertisementFields(*body)) {
                       return InvalidRequest(QStringLiteral("Invalid summon advertisement"));
                   }
                   const auto result = broker->Advertise(*body, request.body(),
                                                         QDateTime::currentMSecsSinceEpoch(),caller);
                   if (!result.authorized) return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);
                   if (!result.pendingClaim.isEmpty()) {
                       const QByteArray response =
                           Bloodborne::BuildClaimDeliveryResponse(result.pendingClaim);
                       if (response.isEmpty()) {
                           return InvalidRequest(QStringLiteral("Invalid pending summon claim"));
                       }
                       qInfo() << "Bloodborne summon: delivered claim to user"
                               << Integer(*body, QStringLiteral("UserId")) << "session"
                               << body->value(QStringLiteral("SessionId")).toString()
                               << "host-placement-bytes" << result.pendingHostPlacement.size();
                       return RawJsonResponse(response, result.pendingHostPlacement);
                   }
                   if (!result.pendingHostPlacement.isEmpty()) {
                       qInfo() << "Bloodborne summon: preparing cross-map user"
                               << Integer(*body, QStringLiteral("UserId")) << "session"
                               << body->value(QStringLiteral("SessionId")).toString()
                               << "destination placement bytes"
                               << result.pendingHostPlacement.size();
                       return SummonResponse(QStringLiteral("SummonDataCreateResponse"),
                                             result.pendingHostPlacement);
                   }
                   qInfo() << "Bloodborne summon: advertised user"
                           << Integer(*body, QStringLiteral("UserId")) << "session"
                           << body->value(QStringLiteral("SessionId")).toString();
                   return SummonResponse(QStringLiteral("SummonDataCreateResponse"));
               });

    http.route("/summon_messenger/get", QHttpServerRequest::Method::Post,
               [broker, seamlessCoop, &db, &shared](const QHttpServerRequest& request) -> QHttpServerResponse {
                   Bloodborne::SummonIdentity caller;
                   if (seamlessCoop) {
                       auto auth = WebApiAuth::Authenticate(request,db);
                       if (!auth.userId) return std::move(auth.errorResponse);
                       if (*auth.userId <= 0 || static_cast<quint64>(*auth.userId) > 0xffffffffULL)
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::Unauthorized);
                       caller.user = static_cast<quint64>(*auth.userId);
                       const QByteArray token = QString::fromUtf8(request.value("Authorization")).trimmed().mid(7).trimmed().toUtf8();
                       caller.tokenHash = QCryptographicHash::hash(token,QCryptographicHash::Sha256);
                   }
                   if (!seamlessCoop) TraceSummonRequest("get", request);
                   const auto body = ParseRequest(request);
                   if (!body) {
                       return InvalidRequest(QStringLiteral("Invalid summon search"));
                   }
                   QByteArray placement = request.value(HostPlacementHeader);
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
                       broker->Search(*body, QDateTime::currentMSecsSinceEpoch(), placement, caller);
                   qInfo() << "Bloodborne summon: search for user"
                           << Integer(*body, QStringLiteral("UserId")) << "returned"
                           << signs.size();
                   return SummonListResponse(signs);
               });

    http.route("/summon_messenger/delete", QHttpServerRequest::Method::Post,
               [broker, seamlessCoop, &db, &shared](const QHttpServerRequest& request) -> QHttpServerResponse {
                   Bloodborne::SummonIdentity caller;
                   if (seamlessCoop) {
                       auto auth = WebApiAuth::Authenticate(request,db);
                       if (!auth.userId) return std::move(auth.errorResponse);
                       if (*auth.userId <= 0 || static_cast<quint64>(*auth.userId) > 0xffffffffULL)
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::Unauthorized);
                       caller.user = static_cast<quint64>(*auth.userId);
                       const QByteArray token = QString::fromUtf8(request.value("Authorization")).trimmed().mid(7).trimmed().toUtf8();
                       caller.tokenHash = QCryptographicHash::hash(token,QCryptographicHash::Sha256);
                   }
                   if (!seamlessCoop) TraceSummonRequest("delete", request);
                   const auto body = ParseRequest(request);
                   if (!body) {
                       return InvalidRequest(QStringLiteral("Invalid summon removal"));
                   }
                   const auto result = broker->Consume(*body, QDateTime::currentMSecsSinceEpoch(),caller);
                   if (!result.authorized) return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);
                   qInfo() << "Bloodborne summon: consumed" << result.consumed << "retained"
                           << result.retained << "advertisement(s) host-placement-bytes"
                           << result.pendingHostPlacement.size();
                   QHttpServerResponse response =
                       SummonResponse(QStringLiteral("SummonDataRemoveResponse"));
                   if (result.pendingHostPlacement.isEmpty()) {
                       return response;
                   }
                   QHttpHeaders headers = response.headers();
                   if (headers.append(HostPlacementHeader, result.pendingHostPlacement)) {
                       response.setHeaders(std::move(headers));
                   }
                   return response;
               });

    http.route("/summon_messenger/request", QHttpServerRequest::Method::Post,
               [broker, claimDiagnostic, seamlessCoop, &db, &shared](const QHttpServerRequest& request) -> QHttpServerResponse {
                   Bloodborne::SummonIdentity caller;
                   if (seamlessCoop) {
                       auto auth = WebApiAuth::Authenticate(request,db);
                       if (!auth.userId) return std::move(auth.errorResponse);
                       if (*auth.userId <= 0 || static_cast<quint64>(*auth.userId) > 0xffffffffULL)
                           return QHttpServerResponse(QHttpServerResponse::StatusCode::Unauthorized);
                       caller.user = static_cast<quint64>(*auth.userId);
                       const QByteArray token = QString::fromUtf8(request.value("Authorization")).trimmed().mid(7).trimmed().toUtf8();
                       caller.tokenHash = QCryptographicHash::hash(token,QCryptographicHash::Sha256);
                   }
                   if (!seamlessCoop) TraceSummonRequest("request", request);
                   const auto body = ParseRequest(request);
                   if (!body) {
                       return InvalidRequest(QStringLiteral("Invalid summon request"));
                   }
                   if (caller.user && (!shared.relayHost || !shared.relayHost(caller.user)))
                       return QHttpServerResponse(QHttpServerResponse::StatusCode::Forbidden);
                   if (caller.user && !request.value(HostPlacementHeader).isEmpty() &&
                       !RelayAuth::ValidPlacement(request.value(HostPlacementHeader)))
                       return InvalidRequest(QStringLiteral("Invalid host placement"));
                   const auto result =
                       broker->Claim(*body, request.body(), QDateTime::currentMSecsSinceEpoch(),
                                     request.value(HostPlacementHeader),caller);
                   if (caller.user) EmitClaimDiagnostic(*claimDiagnostic,result);
                   switch (result.status) {
                   case Bloodborne::SummonBroker::ClaimStatus::Claimed:
                       qInfo() << "Bloodborne summon: claimed session" << result.targetSessionId
                               << "user" << result.targetUserId;
                       break;
                   case Bloodborne::SummonBroker::ClaimStatus::AlreadyClaimed:
                       qInfo() << "Bloodborne summon: repeated claim for session"
                               << result.targetSessionId;
                       break;
                   case Bloodborne::SummonBroker::ClaimStatus::NotFound:
                       qWarning() << "Bloodborne summon: claim target not found";
                       break;
                   case Bloodborne::SummonBroker::ClaimStatus::Conflict:
                       qWarning() << "Bloodborne summon: conflicting claim for session"
                                  << result.targetSessionId;
                       break;
                   }
                   return SummonResponse(QStringLiteral("SummonDataSummonResponse"));
               });
}

} // namespace WebApiRoutes
