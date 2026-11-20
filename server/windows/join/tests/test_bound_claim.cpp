// SPDX-License-Identifier: GPL-2.0-or-later
#include "bloodborne_summon_broker.h"

#include <cstdio>
#include <cstdlib>
#include <thread>
#include <QJsonDocument>

namespace {
using namespace Bloodborne;
using Broker = SummonBroker;
using Status = Broker::ClaimStatus;
using Reason = Broker::ClaimFailureReason;
using State = Broker::State;

void check(bool ok, const char* expression, int line) {
    if (!ok) {
        std::fprintf(stderr, "FAIL line %d: %s\n", line, expression);
        std::exit(1);
    }
}
#define CHECK(expr) check(bool(expr), #expr, __LINE__)

const SummonIdentity host{1, QByteArray(32, 'h')}, guest{2, QByteArray(32, 'g')};
const QByteArray sign = R"({"SessionId":"S1","UserId":72,"CharaId":9223372036854775808,"AreaId":385875968,"AreaRegionId":230010,"SummonType":0,"SummonData":"guest","SummonDataVersion":3,"MatchingLevel":46})";
const QByteArray native = R"({"SessionId":"host","UserId":71,"TargetUserId":72,"TargetCharaId":9223372036854775808,"HostData":"native"})";
const QByteArray pose35 = "1,18010000,c34207d6,c1e52aac,4288347a,4016c0a4,241010";
const QByteArray moved = "1,18010000,c3421000,c1e52aac,4288347a,4016c0a4,241010";
const QByteArray pose36 = "1,17010000,43110000,c2e80000,42800000,c016cbe4,-241109";

QJsonObject json(const QByteArray& raw) {
    auto doc = QJsonDocument::fromJson(raw);
    CHECK(doc.isObject());
    return doc.object();
}

struct Grant {
    BoundClaimOrigin origin;
    QByteArray scope, pose;
    bool operator==(const Grant&) const = default;
};

// Only the native room/relay boundary is substituted. All broker operations
// below go through the candidate's normal public entry points and JSON parser.
struct Rooms {
    quint64 room = 35, joined = 135;
    quint16 member = 3;
    bool waiting = true, previousEnded = false, previousAccepted = false;
    BoundPlacementStatus decision = BoundPlacementStatus::Granted;
    int calls = 0, sends = 0, legacySends = 0, cancels = 0;
    std::optional<Grant> current;
    QList<BoundClaimStep> steps;

    Broker::Options options() {
        Broker::Options o;
        o.seamlessCoop = true;
        o.ttlMs = 500;
        o.seamlessTtlMs = 20'000;
        o.relayClaimPlacement = [this](const auto&, const auto&, const auto&, const auto&) {
            ++legacySends;
            return QByteArray("legacy");
        };
        o.boundPlacement = [this](const BoundPlacementRequest& p, const BoundCommit& commit) {
            ++calls;
            steps.append(p.step);
            if (decision != BoundPlacementStatus::Granted) return BoundPlacementResult{decision, {}};
            if (!waiting || p.host.user != host.user || p.host.tokenHash != host.tokenHash ||
                p.guest.user != guest.user || p.guest.tokenHash != guest.tokenHash ||
                p.origin.room != room || p.origin.ownerJoined != joined || p.origin.ownerMember != member ||
                (p.placement != pose35 && p.placement != moved && p.placement != pose36))
                return BoundPlacementResult{};
            if (p.step == BoundClaimStep::First) {
                if (current || p.previous || !p.previousScope.isEmpty()) return BoundPlacementResult{};
            } else {
                if (!current || p.previous != current->origin || p.previousScope != current->scope ||
                    previousAccepted || (p.step == BoundClaimStep::Rebind && !previousEnded))
                    return BoundPlacementResult{};
            }
            const auto scope = p.step == BoundClaimStep::Retry ? current->scope : "grant-" + QByteArray::number(sends+1);
            commit(scope);
            current = Grant{p.origin, scope, p.placement};
            ++sends;
            return BoundPlacementResult{BoundPlacementStatus::Granted, scope};
        };
        o.cancelBoundPlacement = [this](const TheHuntRegistrationId& id, const QByteArray& scope) {
            ++cancels;
            if (current && current->origin.registrationId == id && current->scope == scope) current.reset();
        };
        return o;
    }

    void changeRoom() { room = 36; joined = 136; }
};

QString discover(Broker& broker, const QString& session, qint64 now) {
    const QJsonObject search{{"SessionId","host"},{"UserId",71}};
    for (const auto& row : broker.Search(search, now, {}, host, Broker::SearchMode::RegistrationDiscovery)) {
        const auto body = json(row);
        if (body["SessionId"] == session) {
            const auto id = body["TheHuntRegistrationId"].toString();
            CHECK(id.size() == 48);
            return id;
        }
    }
    return {};
}

BoundClaimOrigin publish(Broker& broker, const QByteArray& raw = sign, qint64 now = 100) {
    const auto body = json(raw);
    CHECK(HasRequiredAdvertisementFields(body));
    CHECK(broker.Advertise(body, raw, now, guest).state == State::Advertised);
    const auto id = discover(broker, body["SessionId"].toString(), now);
    CHECK(!id.isEmpty());
    return {id, 35, 3, 135, 1, "01abcdef2345678901abcdef23456789"};
}

BoundClaimOrigin newRoom(BoundClaimOrigin old) {
    old.room = 36;
    old.ownerJoined = 136;
    old.attempt = 74; // New actions need not arrive in numeric order.
    return old;
}

void roomSwitch() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto old = publish(broker), next = newRoom(old);
    CHECK(broker.ClaimBound(old, native, 101, pose35, host).status == Status::Claimed);
    CHECK(broker.ClaimBound(old, native, 102, moved, host).status == Status::AlreadyClaimed);
    const auto firstGrant = rooms.current;
    CHECK(firstGrant && firstGrant->pose == moved && rooms.sends == 2);
    auto sameRoom = old;
    ++sameRoom.attempt;
    CHECK(broker.ClaimBound(sameRoom, native, 103, pose35, host).status == Status::Conflict);
    CHECK(broker.ClaimBound(old, native + " ", 103, moved, host).status == Status::Conflict);
    CHECK(broker.ClaimBound(old, native, 103, pose36, host).status == Status::Conflict);
    CHECK(rooms.calls == 2);

    rooms.changeRoom();
    auto foreignRun = next;
    foreignRun.hostRun = "fedcba9876543210fedcba9876543210";
    CHECK(broker.ClaimBound(foreignRun, native, 104, pose36, host).failureReason == Reason::BoundOriginInvalid);
    CHECK(rooms.calls == 2 && rooms.current == firstGrant);
    CHECK(broker.ClaimBound(old, native, 104, pose35, host).status == Status::Conflict);
    CHECK(broker.Claim(json(native), native, 104, pose35, host).status == Status::Conflict);
    CHECK(broker.ClaimBound(next, native, 104, pose36, host).status == Status::Conflict); // Former room still live.
    rooms.previousEnded = true;
    rooms.previousAccepted = true;
    CHECK(broker.ClaimBound(next, native, 104, pose36, host).status == Status::Conflict);
    rooms.previousAccepted = false;
    rooms.waiting = false;
    CHECK(broker.ClaimBound(next, native, 104, pose36, host).status == Status::Conflict);
    rooms.waiting = true;
    for (auto decision : {BoundPlacementStatus::Busy, BoundPlacementStatus::Expired, BoundPlacementStatus::Denied}) {
        rooms.decision = decision;
        CHECK(broker.ClaimBound(next, native, 105, pose36, host).status == Status::Conflict);
        CHECK(rooms.current == firstGrant && rooms.sends == 2 && rooms.cancels == 0);
        CHECK(broker.Advertise(json(sign), sign, 105, guest).pendingHostPlacement == moved);
    }
    rooms.decision = BoundPlacementStatus::Granted;
    CHECK(broker.ClaimBound(next, native, 106, pose36, host).status == Status::Claimed);
    CHECK(rooms.current->origin == next && rooms.current->scope != firstGrant->scope && rooms.current->pose == pose36);
    CHECK(rooms.steps.back() == BoundClaimStep::Rebind);
    const int callbacks = rooms.calls;
    CHECK(broker.ClaimBound(old, native, 107, moved, host).status == Status::Conflict);
    CHECK(broker.Claim(json(native), native, 107, moved, host).status == Status::Conflict);
    CHECK(broker.Claim(json(native), native, 107, moved, {}).status == Status::Conflict);
    CHECK(rooms.calls == callbacks && rooms.sends == 3 && rooms.legacySends == 0 && rooms.cancels == 0);
    CHECK(broker.ClaimBound(next, native, 108, pose36, host).status == Status::AlreadyClaimed);
    const auto arrival = QByteArray(sign).replace("385875968", "385941504");
    const auto delivery = broker.Advertise(json(arrival), arrival, 109, guest);
    CHECK(delivery.state == State::Delivered && delivery.pendingClaim == native && delivery.pendingHostPlacement == pose36);
    CHECK(broker.ClaimBound(next, native, 110, pose36, host).status == Status::AlreadyClaimed);
    CHECK(rooms.sends == 4);
    std::puts("PASS rooms 35/36, exact retry, positive room closure, pending guest, refusals, late bound and legacy copies");
}

void replacement(bool reuseAlias, bool lateBefore, bool restarted) {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto old = publish(broker);
    CHECK(broker.ClaimBound(old, native, 101, pose35, host).status == Status::Claimed);
    const QJsonObject end{{"SessionId","S1"},{"UserId",72},{"Force",true}};
    CHECK(broker.Consume(end, 102, guest).consumed == 1 && rooms.cancels == 1 && !rooms.current);
    const auto raw = reuseAlias ? sign : QByteArray(sign).replace("S1", "S2");
    auto next = newRoom(publish(broker, raw, 103));
    if (restarted) {
        next.hostRun = "fedcba9876543210fedcba9876543210";
        next.attempt = old.attempt;
    }
    CHECK(next.registrationId != old.registrationId);
    rooms.changeRoom();
    auto late = [&] {
        const auto saved = rooms.current;
        const int calls = rooms.calls;
        CHECK(broker.ClaimBound(old, native, 104, pose35, host).status == Status::NotFound);
        auto recycled = next;
        recycled.hostRun = old.hostRun;
        recycled.attempt = old.attempt;
        CHECK(broker.ClaimBound(recycled, native, 104, pose36, host).status == Status::Conflict);
        CHECK(broker.Claim(json(native), native, 104, pose35, host).status == Status::Conflict);
        CHECK(rooms.current == saved && rooms.calls == calls && rooms.cancels == 1 && rooms.legacySends == 0);
    };
    if (lateBefore) late();
    CHECK(broker.ClaimBound(next, native, 104, pose36, host).status == Status::Claimed);
    if (!lateBefore) late();
    CHECK(rooms.current->origin == next && rooms.current->pose == pose36 && rooms.sends == 2);
    const auto grant = rooms.current;
    CHECK(broker.ClaimBound(next, native, 105, pose36, host).status == Status::AlreadyClaimed);
    CHECK(rooms.current == grant && rooms.sends == 3);
}

void firstArrivalAfterRoomChange() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto old = publish(broker), next = newRoom(old);
    rooms.changeRoom();
    CHECK(broker.ClaimBound(old, native, 101, pose35, host).status == Status::Conflict);
    CHECK(broker.StateFor("S1", 72, 101) == State::Advertised && !rooms.current);
    CHECK(broker.ClaimBound(next, native, 101, pose36, host).status == Status::Claimed);
    const auto saved = rooms.current;
    CHECK(broker.ClaimBound(old, native, 101, pose35, host).status == Status::Conflict);
    CHECK(rooms.current == saved && rooms.sends == 1 && rooms.cancels == 0);
    std::puts("PASS never-seen room 35 copy before and after room 36, at one timestamp");
}

void exactTargetAndSelection() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto old = publish(broker);
    const auto newer = publish(broker, QByteArray(sign).replace("S1", "S2"), 105);
    CHECK(newer.registrationId != old.registrationId);
    for (const auto& wrong : {QByteArray(native).replace("9223372036854775808", "9223372036854775809"),
                              QByteArray(native).replace("9223372036854775808", "18446744073709551616"),
                              QByteArray(native).replace("9223372036854775808", "-1"),
                              QByteArray(native).replace("9223372036854775808", "0.5"),
                              QByteArray(native).replace("72,", "72.00000000000000000001,"),
                              QByteArray(native).replace("72,", "73,"),
                              QByteArray(native).replace("72,", R"(72,"Target\u0055serId":72,)"),
                              QByteArray(native).replace("\"TargetCharaId\"", "\"unused\"")})
        CHECK(broker.ClaimBound(old, wrong, 106, pose35, host).status == Status::Conflict);
    auto bad = old; bad.attempt = 0;
    CHECK(broker.ClaimBound(bad, native, 106, pose35, host).status == Status::Conflict);
    for (const auto& run : {QByteArray{}, QByteArray(31,'a'), QByteArray(33,'a'), QByteArray(32,'F'),
                            QByteArray(32,'g'), QByteArray(32,'\0')}) {
        bad = old; bad.hostRun = run;
        CHECK(broker.ClaimBound(bad, native, 106, pose35, host).failureReason == Reason::BoundOriginInvalid);
    }
    bad = old; bad.registrationId = QString(48, 'A');
    CHECK(broker.ClaimBound(bad, native, 106, pose35, host).status == Status::Conflict);
    bad = old; bad.registrationId.chop(1);
    CHECK(broker.ClaimBound(bad, native, 106, pose35, host).status == Status::Conflict);
    CHECK(broker.ClaimBound(old, native, 106, pose35, {}).status == Status::Conflict);
    CHECK(broker.ClaimBound(old, native, 106, pose35, guest).status == Status::Conflict);
    CHECK(rooms.calls == 0);
    bad = old; ++bad.ownerMember;
    CHECK(broker.ClaimBound(bad, native, 106, pose35, host).status == Status::Conflict);
    bad = old; ++bad.ownerJoined;
    CHECK(broker.ClaimBound(bad, native, 106, pose35, host).status == Status::Conflict);
    const auto invalidPose = QByteArray(pose35).replace("c34207d6", "7fc00000");
    CHECK(broker.ClaimBound(old, native, 106, invalidPose, host).status == Status::Conflict);
    CHECK(broker.ClaimBound(old, native, 106, pose35, {host.user,QByteArray(32,'x')}).status == Status::Conflict);
    CHECK(broker.StateFor("S1", 72, 106) == State::Advertised && rooms.sends == 0);
    const auto right = QByteArray(native).replace("9223372036854775808", "9.223372036854775808e18");
    CHECK(broker.ClaimBound(old, right, 106, pose35, host).targetSessionId == "S1");
    CHECK(broker.StateFor("S2", 72, 106) == State::Advertised);
    CHECK(broker.ClaimBound(old, right, 107, pose35, {host.user,QByteArray(32,'x')}).status == Status::Conflict);
    CHECK(!broker.Advertise(json(sign), sign, 107, {}).authorized);
    const QJsonObject end{{"SessionId","S1"},{"UserId",72},{"Force",true}};
    CHECK(!broker.Consume(end, 108, {guest.user,QByteArray(32,'x')}).authorized);
    CHECK(broker.Consume(end, 108, guest).consumed == 1 && rooms.cancels == 1 && !rooms.current);
    const auto max = QByteArray(sign).replace("9223372036854775808", "18446744073709551615");
    auto exactMax = publish(broker, max, 109);
    ++exactMax.attempt;
    CHECK(broker.ClaimBound(exactMax, QByteArray(native).replace("9223372036854775808", "18446744073709551615"),
                            109, pose35, host).status == Status::Claimed);
    std::puts("PASS exact native target, uint64 maximum, actual publisher/claimant, selected older live registration");
}

void refusalAndExpiry() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto first = publish(broker);
    for (auto decision : {BoundPlacementStatus::Denied, BoundPlacementStatus::Busy, BoundPlacementStatus::Expired}) {
        rooms.decision = decision;
        const auto result = broker.ClaimBound(first, native, 101, pose35, host);
        const auto expected = decision == BoundPlacementStatus::Busy ? Reason::BoundPlacementBusy :
                              decision == BoundPlacementStatus::Expired ? Reason::BoundPlacementExpired : Reason::BoundPlacementDenied;
        CHECK(result.status == Status::Conflict && result.failureReason == expected);
        CHECK(broker.StateFor("S1", 72, 101) == State::Advertised && !rooms.current && rooms.sends == 0);
    }
    rooms.decision = BoundPlacementStatus::Granted;
    CHECK(broker.ClaimBound(first, native, 101, pose35, host).status == Status::Claimed);
    const auto another = publish(broker, QByteArray(sign).replace("S1", "S2"), 102);
    auto otherBody = QByteArray(native).replace("\"native\"", "\"another action\"");
    CHECK(broker.Claim(json(otherBody), otherBody, 103, moved, host).status == Status::Conflict);
    broker.Search(QJsonObject{{"UserId",89}}, 103, pose36, {4,QByteArray(32,'o')});
    CHECK(broker.StateFor("S2", 72, 103) == State::Advertised && rooms.legacySends == 0);
    CHECK(broker.ClaimBound(another, native, 603, pose35, host).status == Status::NotFound);
    CHECK(broker.Size(603) == 1 && rooms.cancels == 0 && rooms.current->origin == first);
    CHECK(broker.ClaimBound(first, native, 10101, moved, host).status == Status::AlreadyClaimed);
    CHECK(broker.ClaimBound(first, native, 10102, pose35, host).failureReason == Reason::BoundPlacementExpired);
    CHECK(broker.ClaimBound(first, native, 20102, pose35, host).status == Status::NotFound);
    CHECK(rooms.sends == 2 && rooms.cancels == 0);
    CHECK(broker.Size(20102) == 0 && rooms.cancels == 1 && !rooms.current);
    CHECK(broker.Size(20103) == 0 && rooms.cancels == 1);
    std::puts("PASS first refusal/busy/expiry, other sign isolation, unchanged retry TTL and exact terminal cancellation");
}

void callbackContract() {
    Rooms rooms;
    auto options = rooms.options();
    const auto normal = options.boundPlacement;
    int mode = 0;
    options.boundPlacement = [&](const BoundPlacementRequest& p, const BoundCommit& commit) -> BoundPlacementResult {
        if (mode == 0) return {BoundPlacementStatus::Granted, "never-committed"};
        if (mode == 1) { commit({}); return {BoundPlacementStatus::Granted, {}}; }
        if (mode == 2) { commit("a"); commit("a"); return {BoundPlacementStatus::Granted, "a"}; }
        if (mode == 3) { commit("a"); return {BoundPlacementStatus::Granted, "b"}; }
        if (mode == 4) { commit("a"); return {BoundPlacementStatus::Busy, {}}; }
        if (mode == 6) { commit("wrong-retry-scope"); return {BoundPlacementStatus::Granted, "wrong-retry-scope"}; }
        return normal(p, commit);
    };
    Broker broker(options);
    const auto first = publish(broker);
    for (mode = 0; mode < 5; ++mode) {
        CHECK(broker.ClaimBound(first, native, 101, pose35, host).status == Status::Conflict);
        CHECK(broker.StateFor("S1", 72, 101) == State::Advertised);
        CHECK(rooms.sends == 0 && !rooms.current);
    }
    CHECK(broker.ClaimBound(first, native, 101, pose35, host).status == Status::Claimed);
    mode = 6;
    CHECK(broker.ClaimBound(first, native, 102, moved, host).status == Status::Conflict);
    CHECK(broker.Advertise(json(sign), sign, 102, guest).pendingHostPlacement == pose35 && rooms.sends == 1);
    mode = 5;
    CHECK(broker.ClaimBound(first, native, 103, moved, host).status == Status::AlreadyClaimed);
    CHECK(rooms.current->pose == moved);
    std::puts("PASS grant requires exactly one matching commit, broken grants leave broker state intact");
}

void legacyAndAuthoredSign() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto origin = publish(broker);
    CHECK(broker.Advertise(json(sign), sign, 101, {}).authorized);
    CHECK(broker.ClaimBound(origin, native, 102, pose35, host).failureReason == Reason::NoPublisher);
    CHECK(broker.Advertise(json(sign), sign, 103, guest).authorized);
    CHECK(broker.Claim(json(native), native, 104, pose35, host).status == Status::Claimed);
    CHECK(broker.Claim(json(native), native, 105, moved, host).status == Status::AlreadyClaimed);
    CHECK(broker.ClaimBound(origin, native, 106, moved, host).status == Status::Conflict);
    CHECK(rooms.calls == 0 && rooms.legacySends == 2);

    auto disconnected = rooms.options();
    disconnected.boundPlacement = {};
    Broker plain(disconnected);
    const auto unused = publish(plain);
    CHECK(plain.ClaimBound(unused, native, 101, moved, host).status == Status::Conflict);
    CHECK(plain.StateFor("S1", 72, 101) == State::Advertised);
    std::puts("PASS ordinary legacy first/retry, no promotion of legacy claim, current authenticated advertisement required");
}

void endedAndReplacedGrants() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto old = publish(broker);
    CHECK(broker.ClaimBound(old, native, 101, pose35, host).status == Status::Claimed);
    const auto newCharacter = QByteArray(sign).replace("9223372036854775808", "9223372036854775809");
    const auto newRole = publish(broker, newCharacter, 102);
    CHECK(newRole.registrationId != old.registrationId && rooms.cancels == 1 && !rooms.current);
    CHECK(broker.ClaimBound(old, native, 103, pose35, host).status == Status::NotFound);
    const auto otherAction = QByteArray(native).replace("9223372036854775808", "9223372036854775809")
                                               .replace("\"native\"", "\"other\"");
    CHECK(broker.Claim(json(otherAction), otherAction, 103, pose35, host).status == Status::Claimed);
    CHECK(rooms.legacySends == 1 && rooms.sends == 1);
    std::puts("PASS authenticated character replacement revokes its exact grant, unrelated legacy action remains available");
}

void simultaneousRequests() {
    Rooms rooms;
    Broker broker(rooms.options());
    const auto first = publish(broker);
    Broker::ClaimResult a, b;
    std::thread one([&] { a = broker.ClaimBound(first, native, 101, pose35, host); });
    std::thread two([&] { b = broker.ClaimBound(first, native, 101, pose35, host); });
    one.join();
    two.join();
    CHECK((a.status == Status::Claimed && b.status == Status::AlreadyClaimed) ||
          (b.status == Status::Claimed && a.status == Status::AlreadyClaimed));
    CHECK(rooms.steps.size() == 2 && rooms.steps.front() == BoundClaimStep::First && rooms.steps.back() == BoundClaimStep::Retry);
    CHECK(rooms.current->scope == "grant-1" && rooms.sends == 2);
    std::puts("PASS simultaneous requests commit one first binding and one same-scope retry");
}
} // namespace

int main() {
    roomSwitch();
    firstArrivalAfterRoomChange();
    for (bool restarted : {true, false})
        for (bool same : {false, true})
            for (bool before : {false, true}) replacement(same, before, restarted);
    std::puts("PASS S1/S2 across host restart and continuous run, both SessionId and late arrival orders (8/8)");
    exactTargetAndSelection();
    refusalAndExpiry();
    callbackContract();
    legacyAndAuthoredSign();
    endedAndReplacedGrants();
    simultaneousRequests();
}
