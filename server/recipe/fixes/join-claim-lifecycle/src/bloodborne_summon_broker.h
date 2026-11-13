// SPDX-FileCopyrightText: Copyright 2026 shadNet Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <optional>
#include <functional>

#include <QByteArray>
#include <QHash>
#include <QJsonObject>
#include <QList>
#include <QMutex>
#include <QString>

namespace Bloodborne {

// Server-owned identity; never parsed from JSON UserId/TargetUserId/CharaId.
struct SummonIdentity { quint64 user = 0; QByteArray tokenHash; };

class SummonBroker {
public:
    enum class State { Advertised, Preparing, Claimed, Delivered, Consumed };
    enum class ClaimStatus { Claimed, AlreadyClaimed, NotFound, Conflict };

    struct Options {
        qint64 ttlMs = 130'000;
        qint64 seamlessTtlMs = 15 * 60 * 1000;
        bool seamlessCoop = false;
        bool seamlessAnywhereSummons = false;
        std::function<void(const SummonIdentity&, const SummonIdentity&, const QByteArray&)> relayPlacement;
        // Private first-claim incarnation; an empty result never authorizes a retry.
        std::function<QByteArray(const SummonIdentity&, const SummonIdentity&, const QByteArray&, const QByteArray&)> relayClaimPlacement;
        std::function<void(quint64)> relayCancelPlacement;
    };

    struct AdvertiseResult {
        State state = State::Advertised;
        QByteArray pendingClaim;
        QByteArray pendingHostPlacement;
        bool authorized = true;
    };

    enum class ClaimFailureReason : quint8 {
        None=0, NoPublisher=1, SelfPublisher=2, OtherClaimant=3,
        TargetUserMismatch=4, TargetCharaMismatch=5, InvalidTargetSelector=6,
        AmbiguousTarget=7, TargetNotFound=8, ClaimBodyConflict=9
    };
    struct ClaimResult {
        ClaimStatus status = ClaimStatus::NotFound;
        QString targetSessionId;
        qint64 targetUserId = -1;
        // Private diagnostics only; never added to an HTTP or relay response.
        ClaimFailureReason failureReason = ClaimFailureReason::None;
        quint16 diagnosticFlags = 0;
    };

    struct ConsumeResult {
        int consumed = 0;
        int retained = 0;
        QByteArray pendingHostPlacement;
        bool authorized = true;
    };

    SummonBroker();
    explicit SummonBroker(qint64 ttlMs);
    explicit SummonBroker(Options options);

    AdvertiseResult Advertise(const QJsonObject& body, const QByteArray& rawBody, qint64 nowMs, const SummonIdentity& caller = {});
    QList<QByteArray> Search(const QJsonObject& request, qint64 nowMs,
                             const QByteArray& hostPlacement = {}, const SummonIdentity& caller = {});
    ClaimResult Claim(const QJsonObject& request, const QByteArray& rawRequest, qint64 nowMs,
                      const QByteArray& hostPlacement = {}, const SummonIdentity& caller = {});
    ConsumeResult Consume(const QJsonObject& request, qint64 nowMs, const SummonIdentity& caller = {});

    std::optional<State> StateFor(const QString& sessionId, qint64 userId, qint64 nowMs);
    int Size(qint64 nowMs);
    bool IsSeamlessCoopEnabled() const;
    bool IsSeamlessAnywhereSummonsEnabled() const;

private:
    struct Record {
        State state = State::Advertised;
        SummonIdentity publisher, claimant;
        QJsonObject advertisement;
        QByteArray rawAdvertisement;
        QJsonObject claim;
        QByteArray rawClaim;
        QByteArray hostPlacement;
        QByteArray claimPlacementScope;
        qint64 preparationRequester = -1;
        qint64 updatedAtMs = 0;
    };

    void CancelPlacementLocked(quint64 user);
    void PurgeExpiredLocked(qint64 nowMs);

    qint64 m_ttlMs;
    qint64 m_seamlessTtlMs;
    bool m_seamlessCoop;
    bool m_seamlessAnywhereSummons;
    std::function<void(const SummonIdentity&, const SummonIdentity&, const QByteArray&)> m_relayPlacement;
    std::function<QByteArray(const SummonIdentity&, const SummonIdentity&, const QByteArray&, const QByteArray&)> m_relayClaimPlacement;
    std::function<void(quint64)> m_relayCancelPlacement;
    QMutex m_mutex;
    QHash<QString, Record> m_records;
};

bool HasRequiredAdvertisementFields(const QJsonObject& body);
QByteArray BuildClaimDeliveryResponse(const QByteArray& rawClaim);

} // namespace Bloodborne
