// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QDebug>
#include <QElapsedTimer>
#include "bloodborne_summon_broker.h"

// Owner Qt thread only. No identities, strings, JSON or credential data retained.
struct ClaimDiagnosticWindow {
    static constexpr qint64 DurationMs = 5 * 60 * 1000;
    static constexpr qint64 MinIntervalMs = 1000;
    static constexpr unsigned MaxLines = 32;
    bool enabled = false;
    QElapsedTimer clock;
    qint64 first = -1, last = -1;
    unsigned emitted = 0;
    explicit ClaimDiagnosticWindow(bool on) : enabled(on) { if (on) clock.start(); }
    bool Allow(quint8 reason, qint64 now) {
        if (!enabled || reason < 1 || reason > 9 || now < 0) return false;
        if (first < 0) first = now;
        if (now < first || now - first >= DurationMs || emitted >= MaxLines ||
            (last >= 0 && (now < last || now - last < MinIntervalMs))) return false;
        last = now; ++emitted; return true;
    }
};
inline void EmitClaimDiagnostic(ClaimDiagnosticWindow& window,
                                const Bloodborne::SummonBroker::ClaimResult& result) {
    const auto reason = static_cast<quint8>(result.failureReason);
    if (!window.enabled || !window.Allow(reason,window.clock.elapsed())) return;
    const auto f=result.diagnosticFlags;
    qWarning().noquote() << "Bloodborne claim admission diagnostic"
        << "reason=" << int(reason) << "selected=" << bool(f&1)
        << "session_present=" << bool(f&2) << "session_equal=" << bool(f&4)
        << "target_user_numeric=" << bool(f&8) << "target_user_equal=" << bool(f&16)
        << "target_chara_present=" << bool(f&32) << "target_chara_equal=" << bool(f&64)
        << "publisher_is_caller=" << bool(f&128) << "claimant_is_caller=" << bool(f&256);
}
