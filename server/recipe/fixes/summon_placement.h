// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <QByteArray>

namespace RelayAuth {
constexpr qint64 PlacementTtlMs = 10'000;

// Owner membership in the native Bloodborne room, captured before broker admission.
struct SummonRoom {
    quint64 room = 0, joined = 0;
    quint16 member = 0;
    bool operator==(const SummonRoom& other) const {
        return room == other.room && joined == other.joined && member == other.member;
    }
};

enum class SummonAccess { Denied, Busy, NoRoom, Owner };
struct SummonAdmission {
    SummonAccess access = SummonAccess::Denied;
    SummonRoom room;
};

struct PlacementRequest {
    quint64 record = 0, host = 0, target = 0;
    SummonRoom room;
    QByteArray hostHash, targetHash, text;
    qint64 ttlMs = 0;
};
}
