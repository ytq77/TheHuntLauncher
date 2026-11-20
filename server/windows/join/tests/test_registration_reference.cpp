// SPDX-License-Identifier: GPL-2.0-or-later
#include "bloodborne_summon_broker.h"

#include <cstdio>
#include <cstdlib>

#include <QJsonDocument>
#include <QSet>

namespace {
using Broker = Bloodborne::SummonBroker;
using Identity = Bloodborne::SummonIdentity;

void check(bool ok, const char* expression, int line) {
    if (!ok) {
        std::fprintf(stderr, "FAIL line %d: %s\n", line, expression);
        std::exit(1);
    }
}
#define CHECK(condition) check(bool(condition), #condition, __LINE__)

QJsonObject json(const QByteArray& bytes) {
    const auto doc = QJsonDocument::fromJson(bytes);
    CHECK(doc.isObject());
    return doc.object();
}

const QByteArray ad = R"({"SessionId":"s1","UserId":11,"CharaId":42,"AreaId":385875968,"AreaRegionId":230010,"MatchingLevel":46,"SummonType":0,"SummonDataVersion":3,"SummonData":"game-data"})";
const QByteArray search = R"({"SessionId":"host","UserId":21,"SummonTypeList":[{"SummonType":0}]})";
const QByteArray placement = "1,18010000,c34207d6,c1e52aac,4288347a,4016c0a4,241010";
const Identity guest{101, QByteArray(32, 'a')}, host{201, QByteArray(32, 'h')};
const Identity otherGuest{102, QByteArray(32, 'b')}, otherHost{202, QByteArray(32, 'i')};

struct Calls {
    int prepare = 0, claim = 0, cancel = 0;
    quint64 lastCancelledUser = 0;
    Broker::Options options() {
        Broker::Options opts;
        opts.seamlessCoop = true;
        opts.ttlMs = 20;
        opts.relayPlacement = [this](const auto&, const auto&, const auto&) { ++prepare; };
        opts.relayClaimPlacement = [this](const auto&, const auto&, const auto&, const auto&) {
            ++claim;
            return QByteArray("scope");
        };
        opts.relayCancelPlacement = [this](quint64 user) { ++cancel; lastCancelledUser = user; };
        return opts;
    }
};

void advertise(Broker& broker, const QByteArray& raw, qint64 now, const Identity& publisher = guest) {
    const auto body = json(raw);
    CHECK(Bloodborne::HasRequiredAdvertisementFields(body));
    CHECK(broker.Advertise(body, raw, now, publisher).authorized);
}

// This group also compiles against the unmodified deployment source.
void legacyControl() {
    Calls calls;
    Broker broker(calls.options());
    const auto raw = ad.chopped(1) + R"(,"TheHuntRegistrationId":"client-data","CharaText":"unchanged"})";
    advertise(broker, raw, 100);
    CHECK(broker.Search(json(search), 101, {}, host) == QList<QByteArray>{raw});
    CHECK(broker.Search(json(search), 102, placement, host).isEmpty());
    CHECK(broker.StateFor("s1", 11, 102) == Broker::State::Preparing);
    CHECK(calls.prepare == 1 && calls.claim == 0);
    CHECK(broker.Search(json(search), 103, placement, host).isEmpty());
    CHECK(calls.prepare == 2);
    std::puts("PASS deployed legacy: exact candidate bytes, cross-map preparation and renewal");
}

#ifndef THEHUNT_LEGACY_CONTROL
constexpr char field[] = "TheHuntRegistrationId";

QList<QByteArray> discover(Broker& broker, qint64 now, const Identity& caller = host) {
    auto query = json(search);
    if (caller.user == otherHost.user) {
        query["SessionId"] = "other-host";
        query["UserId"] = 22;
    }
    return broker.Search(query, now, placement, caller, Broker::SearchMode::RegistrationDiscovery);
}

Bloodborne::TheHuntRegistrationId idOf(const QByteArray& row) {
    const auto value = json(row).value(QLatin1String(field));
    CHECK(value.isString());
    const auto id = value.toString();
    CHECK(id.size() == 48);
    CHECK(id.toLatin1().toLower() == id.toLatin1());
    CHECK(QByteArray::fromHex(id.toLatin1()).toHex() == id.toLatin1());
    return id;
}

QString onlyId(Broker& broker, qint64 now) {
    const auto rows = discover(broker, now);
    CHECK(rows.size() == 1);
    return idOf(rows.front());
}

void candidateDiscovery() {
    Calls calls;
    Broker broker(calls.options());
    const auto second = QByteArray(ad).replace("\"s1\"", "\"s2\"").replace("\"UserId\":11", "\"UserId\":12");
    advertise(broker, ad, 100);
    advertise(broker, second, 101, otherGuest);
    const auto cancels = calls.cancel;
    const auto found = discover(broker, 102);
    CHECK(found.size() == 2);
    CHECK(json(found[0])["SessionId"] == "s2" && json(found[1])["SessionId"] == "s1");
    const auto id2 = idOf(found[0]), id1 = idOf(found[1]);
    CHECK(id1 != id2);
    CHECK(discover(broker, 103, otherHost) == found);
    CHECK(broker.StateFor("s1", 11, 103) == Broker::State::Advertised);
    CHECK(broker.StateFor("s2", 12, 103) == Broker::State::Advertised);
    CHECK(calls.prepare == 0 && calls.claim == 0 && calls.cancel == cancels);
    const QJsonObject referenceOnly{{QLatin1String(field), id1}};
    CHECK(broker.Claim(referenceOnly, QJsonDocument(referenceOnly).toJson(), 104, placement, host).status ==
          Broker::ClaimStatus::NotFound);
    CHECK(broker.Consume(referenceOnly, 104, guest).consumed == 0);
    CHECK(discover(broker, 104) == found && calls.prepare == 0 && calls.claim == 0);

    advertise(broker, QByteArray(ad).replace("game-data", "updated-game-data"), 105);
    auto query = json(search);
    query["GetMaxCount"] = 1;
    query["AreaId"] = 402718720;
    const auto limited = broker.Search(query, 106, placement, host, Broker::SearchMode::RegistrationDiscovery);
    CHECK(limited.size() == 1 && idOf(limited[0]) == id1);
    CHECK(json(limited[0])["AreaId"].toInt() == 402718720);
    CHECK(json(limited[0])["SummonData"] == "updated-game-data");
    CHECK(idOf(discover(broker, 106).back()) == id2);

    // A different host can still make the ordinary explicit claim; discovery
    // with a valid placement has neither reserved the sign nor called either relay.
    const auto claim = QByteArray(R"({"SessionId":"other-host","UserId":22,"TargetUserId":11,"TargetCharaId":42})");
    CHECK(broker.Claim(json(claim), claim, 107, placement, otherHost).status == Broker::ClaimStatus::Claimed);
    CHECK(calls.prepare == 0 && calls.claim == 1);
    const auto remove = json(R"({"SessionId":"s1","UserId":11})");
    CHECK(broker.Consume(remove, 108, guest).retained == 1);
    CHECK(idOf(discover(broker, 109, otherHost).front()) == id1);
    const auto forOther = discover(broker, 109);
    CHECK(forOther.size() == 1 && idOf(forOther.front()) == id2);
    CHECK(calls.claim == 1 && calls.prepare == 0);
    std::puts("PASS two candidates, stable renewal, order/limit, discovery leaves both available");
}

void registrationLifetime() {
    Calls calls;
    Broker broker(calls.options());
    QSet<QString> ids;
    advertise(broker, ad, 100);
    const auto first = onlyId(broker, 101);
    ids.insert(first);
    CHECK(!broker.Advertise(json(ad), ad, 102, otherGuest).authorized);
    CHECK(onlyId(broker, 102) == first);

    const auto newRole = QByteArray(ad).replace("\"CharaId\":42", "\"CharaId\":43");
    advertise(broker, newRole, 103);
    ids.insert(onlyId(broker, 104));
    const Identity renewedLogin{guest.user, QByteArray(32, 'c')};
    advertise(broker, newRole, 105, renewedLogin);
    ids.insert(onlyId(broker, 106));
    const auto remove = json(R"({"SessionId":"s1","UserId":11,"Force":true})");
    CHECK(broker.Consume(remove, 107, renewedLogin).consumed == 1);
    CHECK(discover(broker, 108).isEmpty());
    advertise(broker, newRole, 109, renewedLogin);
    ids.insert(onlyId(broker, 129)); // Last live millisecond; discovery must not refresh the TTL.
    CHECK(discover(broker, 130).isEmpty());
    CHECK(!broker.StateFor("s1", 11, 130));
    advertise(broker, newRole, 130, renewedLogin);
    ids.insert(onlyId(broker, 130));

    Broker restarted(calls.options());
    advertise(restarted, ad, 100);
    ids.insert(onlyId(restarted, 101));
    CHECK(ids.size() == 6);
    std::puts("PASS character/login replacement, consumed/expired alias reuse, fresh broker namespace");
}

void reservedFieldAndWireBytes() {
    Calls calls;
    Broker broker(calls.options());
    const QByteArray clean = QByteArray(ad).replace("\"CharaId\":42", "\"CharaId\":18446744073709551613").chopped(1) +
        R"(,"Nested":{"TheHuntRegistrationId":"nested"})";
    const QByteArray raw = clean +
        R"(,"TheHuntRegistrationId":"client1","TheHuntRegistration\u0049d":"client2","TheHuntRegistrationId":"client3"})";
    advertise(broker, raw, 100);
    const auto row = discover(broker, 101).front();
    const auto id = idOf(row).toLatin1();
    const QByteArray expected = clean + ",\"TheHuntRegistrationId\":\"" + id + "\"}";
    CHECK(row == expected); // Exactly one top-level reference; nested data and large integer are exact.
    CHECK(broker.Search(json(search), 102, {}, host) == QList<QByteArray>{raw});
    CHECK(broker.Search(json(search), 102, {}, host, Broker::SearchMode::Legacy) == QList<QByteArray>{raw});
    auto query = json(search);
    query[field] = "query-supplied";
    CHECK(broker.Search(query, 102, placement, host, Broker::SearchMode::RegistrationDiscovery).front() == expected);
    const auto bom = QByteArray("\xef\xbb\xbf") + raw;
    advertise(broker, bom, 103);
    CHECK(discover(broker, 104).front() == expected);
    CHECK(broker.Search(json(search), 104, {}, host).front() == bom);
    const auto escapedRole = QByteArray(raw).replace("\"CharaId\":", "\"Chara\\u0049d\" : ");
    advertise(broker, escapedRole, 105);
    CHECK(onlyId(broker, 106).toLatin1() == id);
    const auto sameRole = QByteArray(escapedRole).replace("18446744073709551613", "0.1844674407370955161300E+20");
    advertise(broker, sameRole, 106);
    CHECK(onlyId(broker, 106).toLatin1() == id);
    const auto nextRole = QByteArray(escapedRole).replace("18446744073709551613", "18446744073709551614");
    advertise(broker, nextRole, 107);
    CHECK(onlyId(broker, 108).toLatin1() != id);
    CHECK(broker.Search(json(search), 108, {}, host).front() == nextRole);
    std::puts("PASS reserved names, escaped/duplicate keys, nested data, exact uint64 role replacement and legacy bytes");
}

void alreadyPreparing() {
    Calls calls;
    Broker broker(calls.options());
    advertise(broker, ad, 100);
    const auto id = onlyId(broker, 101);
    CHECK(broker.Search(json(search), 102, placement, host).isEmpty());
    CHECK(calls.prepare == 1);
    CHECK(onlyId(broker, 103) == id);
    CHECK(discover(broker, 103, otherHost).isEmpty());
    CHECK(broker.StateFor("s1", 11, 104) == Broker::State::Preparing);
    CHECK(calls.prepare == 1 && calls.claim == 0);
    std::puts("PASS discovery of own legacy preparation does not renew it or invite");
}

const QByteArray roleA = QByteArray(ad).replace("\"CharaId\":42", "\"CharaId\":9223372036854775808");
const QByteArray roleB = QByteArray(roleA).replace("9223372036854775808", "9223372036854775809");
const QByteArray claimA = R"({"SessionId":"host","UserId":21,"TargetUserId":11,"TargetCharaId":9223372036854775808,"HostData":"A"})";
const QByteArray claimB = R"({"SessionId":"other-host","UserId":22,"TargetUserId":11,"TargetCharaId":9223372036854775809,"HostData":"B"})";

void authenticatedCharacterReplacement(Broker::State previous) {
    Calls calls;
    Broker broker(calls.options());
    const auto oldMap = previous == Broker::State::Delivered ? "402718720" : "385875968";
    const auto first = QByteArray(roleA).replace("385875968", oldMap);
    const auto second = QByteArray(roleB).replace("385875968", oldMap);
    CHECK(first != second && json(first) == json(second)); // Only the exact CharaId differs.
    advertise(broker, first, 100);
    const auto idA = onlyId(broker, 101);
    if (previous == Broker::State::Preparing) {
        CHECK(broker.Search(json(search), 102, placement, host).isEmpty());
        CHECK(calls.prepare == 1);
    } else {
        CHECK(broker.Claim(json(claimA), claimA, 102, placement, host).status == Broker::ClaimStatus::Claimed);
        CHECK(calls.claim == 1);
        if (previous == Broker::State::Delivered)
            CHECK(broker.Advertise(json(first), first, 103, guest).pendingClaim == claimA);
    }
    CHECK(broker.StateFor("s1", 11, 103) == previous);
    const auto remove = json(R"({"SessionId":"s1","UserId":11})");
    CHECK(broker.Consume(remove, 103, guest).pendingHostPlacement == placement);
    CHECK(discover(broker, 103, otherHost).isEmpty());
    const int cancels = calls.cancel, claims = calls.claim, preparations = calls.prepare;

    const auto switched = broker.Advertise(json(second), second, 104, guest);
    CHECK(switched.authorized);
    if (switched.state != Broker::State::Advertised)
        std::fprintf(stderr, "role switch: previous=%d returned=%d old placement returned=%d\n",
                     int(previous), int(switched.state), int(switched.pendingHostPlacement == placement));
    CHECK(switched.state == Broker::State::Advertised);
    CHECK(switched.pendingClaim.isEmpty() && switched.pendingHostPlacement.isEmpty());
    CHECK(broker.StateFor("s1", 11, 104) == Broker::State::Advertised);
    CHECK(calls.cancel == cancels + 1 && calls.lastCancelledUser == guest.user);
    const auto rows = discover(broker, 105, otherHost);
    CHECK(rows.size() == 1 && rows.front().contains("\"CharaId\":9223372036854775809"));
    const auto idB = idOf(rows.front());
    CHECK(idB != idA);

    const auto renewed = QByteArray(second).replace("game-data", "renewed-data");
    CHECK(broker.Advertise(json(renewed), renewed, 106, guest).state == Broker::State::Advertised);
    CHECK(onlyId(broker, 106) == idB);
    CHECK(calls.cancel == cancels + 1 && calls.prepare == preparations && calls.claim == claims);
    const QByteArray newPlacement = "1,17010000,43110000,c2e80000,42800000,c016cbe4,-241109";
    CHECK(broker.Claim(json(claimB), claimB, 107, newPlacement, otherHost).status == Broker::ClaimStatus::Claimed);
    CHECK(calls.claim == claims + 1);
    const auto arrived = QByteArray(renewed).replace(oldMap, "385941504"); // 0x17010000
    const auto delivered = broker.Advertise(json(arrived), arrived, 108, guest);
    CHECK(delivered.state == Broker::State::Delivered);
    CHECK(delivered.pendingClaim == claimB && delivered.pendingHostPlacement == newPlacement);
    const auto retained = discover(broker, 108, otherHost);
    CHECK(retained.size() == 1 && idOf(retained.front()) == idB);
}

void anonymousCharacterReplacement() {
    Calls calls;
    Broker broker(calls.options());
    advertise(broker, roleA, 100, {});
    const auto before = discover(broker, 100, {});
    CHECK(before.size() == 1);
    CHECK(broker.Claim(json(claimA), claimA, 101, placement, {}).status == Broker::ClaimStatus::Claimed);
    const auto switched = broker.Advertise(json(roleB), roleB, 102, {});
    CHECK(switched.state == Broker::State::Claimed && switched.pendingHostPlacement == placement);
    const auto after = discover(broker, 103, {});
    CHECK(after.size() == 1 && idOf(after.front()) != idOf(before.front()));
    CHECK(calls.cancel == 0 && calls.prepare == 0 && calls.claim == 0);
    std::puts("PASS anonymous character replacement retains the legacy claim and rotates the discovery ID");
}

void exactCharacterSpelling() {
    Calls calls;
    Broker broker(calls.options());
    const auto integerA = QByteArray(roleA).replace("9223372036854775808", "9007199254740992");
    const auto exponentB = QByteArray(integerA).replace("9007199254740992", "9.007199254740993e15");
    const auto integerB = QByteArray(exponentB).replace("9.007199254740993e15", "9007199254740993");
    CHECK(json(integerA) == json(exponentB) && json(exponentB) != json(integerB));
    advertise(broker, integerA, 100);
    const auto idA = onlyId(broker, 100);
    const auto firstClaim = QByteArray(claimA).replace("9223372036854775808", "9007199254740992");
    CHECK(broker.Claim(json(firstClaim), firstClaim, 101, placement, host).status == Broker::ClaimStatus::Claimed);
    CHECK(broker.Advertise(json(exponentB), exponentB, 102, guest).state == Broker::State::Advertised);
    const auto idB = onlyId(broker, 102);
    CHECK(idA != idB);
    advertise(broker, integerB, 103);
    CHECK(onlyId(broker, 103) == idB);
    std::puts("PASS exact role switch and equivalent integer/exponent renewal at the double precision boundary");
}
#endif
} // namespace

int main() {
    legacyControl();
#ifndef THEHUNT_LEGACY_CONTROL
    candidateDiscovery();
    registrationLifetime();
    reservedFieldAndWireBytes();
    alreadyPreparing();
    for (const auto state : {Broker::State::Claimed, Broker::State::Preparing, Broker::State::Delivered})
        authenticatedCharacterReplacement(state);
    std::puts("PASS authenticated uint64 character replacement from Claimed, Preparing and Delivered");
    anonymousCharacterReplacement();
    exactCharacterSpelling();
#endif
}
