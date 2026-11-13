// SPDX-License-Identifier: GPL-2.0-or-later
// Real Qt TCP relay + real Database, synthetic native membership fixtures.
// relay_auth_integration separately drives the full server via actual NP commands.
#include "authenticated_relay.h"
#include "client_session.h"
#include "config.h"
#include "database.h"
#include "bloodborne_summon_broker.h"
#include "webapi_routes_bloodborne.h"
#include <QJsonArray>
#include <QJsonDocument>
#include <QHttpServer>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTcpServer>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QFile>
#include <QSqlQuery>
#include <QTemporaryDir>
#include <QThread>
#include <QTcpSocket>
#include <QtEndian>
#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <limits>

#define CHECK(x) do { if (!(x)) { std::cerr << "line " << __LINE__ << ": " << #x << '\n'; std::abort(); } } while (false)
template<class T> static void Put(QByteArray& p, T n) { char b[sizeof(T)]; qToLittleEndian(n, b); p.append(b, sizeof(b)); }
template<class T> static T Get(const QByteArray& p, int at) { return qFromLittleEndian<T>(p.constData() + at); }
static void Pump(int ms = 30) {
    QElapsedTimer t; t.start();
    do { QCoreApplication::processEvents(); QThread::msleep(1); } while (t.elapsed() < ms);
}
static QByteArray Follow() { QByteArray p(34, 0); p.replace(0, 4, "SHAD"); p[4] = 0x20; return p; }
static QByteArray Pvp(int kind) { QByteArray p(11, 0); p.replace(0, 4, "SHAD"); p[4] = 0x25; p[5] = kind; return p; }
static QByteArray Life() { QByteArray p(14, 0); p.replace(0, 4, "SHAD"); p[4] = 0x23; p[5] = 99; return p; }
static QByteArray World(int version, int chunk = 0) {
    QByteArray p(1018,0);p.replace(0,4,"SHAD");p[4]=0x22;p[5]=version;
    const int groups=version==1?40:57;
    p[14]=chunk;p[15]=(groups+7)/8;p[17]=std::min(8,groups-8*chunk);return p;
}

struct Client {
    QTcpSocket socket;
    QByteArray input, nonce, key;
    quint64 user = 0, incoming = 0, outgoing = 0;
    QVector<QByteArray> frames;
    explicit Client(quint16 port) {
        socket.connectToHost(QHostAddress::LocalHost, port);
        Pump();
        auto challenge = Read();
        CHECK(challenge.size() == 36 && challenge.startsWith("THC2"));
        nonce = challenge.mid(4);
    }
    void Collect() {
        input += socket.readAll();
        while (input.size() >= 4) {
            quint32 n = Get<quint32>(input, 0);
            CHECK(n && n <= RelayAuth::MaxPayload);
            if (input.size() < 4 + n) break;
            frames.push_back(input.mid(4, n)); input.remove(0, 4 + n);
        }
    }
    QByteArray Read(int ms = 100) {
        QElapsedTimer t; t.start();
        while (t.elapsed() < ms) {
            Pump(1); Collect();
            if (!frames.isEmpty()) { QByteArray p = frames.front(); frames.pop_front(); return p; }
            if (socket.state() == QAbstractSocket::UnconnectedState) break;
        }
        return {};
    }
    void Raw(const QByteArray& p, bool partial = false) {
        auto f = RelayAuth::Frame(p);
        if (!partial) { socket.write(f); Pump(); return; }
        for (int i = 0; i < f.size(); ++i) { socket.write(f.mid(i, 1)); Pump(1); }
    }
    QByteArray AuthPayload(quint64 id, const QByteArray& token) {
        constexpr char domain[]="TheHunt relay auth v4 bidirectional visibility";
        QByteArray proof(domain,sizeof(domain));proof+=nonce;Put(proof,id);
        QByteArray p("THA4",4);Put(p,id);return p+RelayAuth::Mac(token,proof);
    }
    void Auth(quint64 id, const QByteArray& token, bool partial = false) {
        user = id; key = RelayAuth::SessionKey(token, nonce, user);
        Raw(AuthPayload(id, token), partial);
        auto ack = Read();
        CHECK(ack.size() == 44 && ack.startsWith("THO4") && Get<quint64>(ack, 4) == id);
        CHECK(RelayAuth::Equal(ack.right(32), RelayAuth::Mac(key, ack.left(12))));
    }
    void Auth3(quint64 id,const QByteArray& token) {
        user=id;key=RelayAuth::SessionKey(token,nonce,user);
        constexpr char domain[]="TheHunt relay auth v4 bidirectional visibility";
        QByteArray proof(domain,sizeof(domain));proof+=nonce;Put(proof,id);
        QByteArray frame("THA4",4);Put(frame,id);frame+=RelayAuth::Mac(token,proof);Raw(frame);
        const auto ack=Read();
        CHECK(ack.size()==44 && ack.startsWith("THO4") && Get<quint64>(ack,4)==id);
        CHECK(RelayAuth::Equal(ack.right(32),RelayAuth::Mac(key,ack.left(12))));
    }
    QByteArray Data(const QByteArray& inner, quint64 sequence = 0) {
        QByteArray p("THD2", 4); Put(p, sequence ? sequence : ++outgoing);
        p += inner; return p + RelayAuth::Mac(key, p);
    }
    void Send(const QByteArray& inner) { Raw(Data(inner)); }
    QVector<QByteArray> Drain(int ms = 40) {
        Pump(ms);
        return TakeFrames();
    }
    QVector<QByteArray> TakeFrames() {
        Collect();
        QVector<QByteArray> data;
        for (const auto& p : frames) {
            CHECK(p.startsWith("THX4") || p.startsWith("THX4") || p.startsWith("THS2") || p.startsWith("THP2"));
            CHECK(Get<quint64>(p, 4) == ++incoming);
            CHECK(RelayAuth::Equal(p.right(32), RelayAuth::Mac(key, p.chopped(32))));
            data.push_back(p);
        }
        frames.clear(); return data;
    }
    int DataCount(int ms = 40) {
        int n = 0; for (const auto& p : Drain(ms)) if (p.startsWith("THS2")) ++n; return n;
    }
    bool Closed(int ms = 50) { Pump(ms); return socket.state() == QAbstractSocket::UnconnectedState; }
};

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    quint16 envPort;
    qunsetenv("THEHUNT_RELAY_PORT"); CHECK(RelayAuth::PortFromEnvironment(envPort) && envPort == 20444);
    for (const char* bad : {"", "0", "-1", "65536", " 20444", "20444x", "+123", "1.5"}) {
        CHECK(!RelayAuth::ParsePort(bad,envPort));
        qputenv("THEHUNT_RELAY_PORT", bad);
        if (qEnvironmentVariableIsSet("THEHUNT_RELAY_PORT")) CHECK(!RelayAuth::PortFromEnvironment(envPort));
    }
    qputenv("THEHUNT_RELAY_PORT", "29555"); CHECK(RelayAuth::PortFromEnvironment(envPort) && envPort == 29555);
    qunsetenv("THEHUNT_RELAY_PORT");
    // Independently calculated Python hmac vectors: exact terminating NUL, UTF-8 token, LE64 ID.
    CHECK(RelayAuth::Proof(QByteArray::fromHex("746573742de4bba4e7898c"), QByteArray(32, 'n'), 42).toHex() ==
          "0db767e8f6469dfeb0922f591ba4257e5a1d4b346f6efde8460b98a638b9bd7b");
    CHECK(RelayAuth::SessionKey(QByteArray::fromHex("746573742de4bba4e7898c"), QByteArray(32, 'n'), 42).toHex() ==
          "f11d85e5ec9ddc04880ac27bf765ea5755a8dd42a92556a6521916d757134630");
    CHECK(RelayAuth::ValidInner(Follow())); CHECK(RelayAuth::ValidInner(Life()));
    CHECK(!RelayAuth::ValidInner(QByteArray("SHAD\x40", 5)));
    CHECK(!RelayAuth::ValidInner(QByteArray("SHAD\x7f", 5)));
    CHECK(!RelayAuth::ValidInner(Follow().chopped(1)));
    CHECK(!RelayAuth::Allowed(Follow(), false, true));
    CHECK(RelayAuth::Allowed(Pvp(1), false, true));
    CHECK(!RelayAuth::Allowed(Pvp(1), true, false));
    for(auto world : {World(1),World(1,4),World(2),World(2,7)}) CHECK(RelayAuth::ValidInner(world));
    for(int version : {0,3,255}) { auto world=World(1);world[5]=version;CHECK(!RelayAuth::ValidInner(world)); }
    auto invalidWorld=World(2);invalidWorld[15]=5;CHECK(!RelayAuth::ValidInner(invalidWorld));
    invalidWorld=World(1);invalidWorld[14]=5;CHECK(!RelayAuth::ValidInner(invalidWorld));
    invalidWorld=World(2,7);invalidWorld[17]=8;CHECK(!RelayAuth::ValidInner(invalidWorld));
    CHECK(!RelayAuth::ValidInner(World(2).chopped(1)));

    QTemporaryDir temporary; CHECK(temporary.isValid());
    Database db("relay_unit_fixture"); CHECK(db.Open(temporary.filePath("fixture.db")));
    auto sql = [&](const QString& query) { QSqlQuery q(db.Conn()); CHECK(q.exec(query)); };
    for (int i = 1; i <= 6; ++i)
        sql(QString("INSERT INTO account(user_id,username,hash,salt,avatar_url,email,email_check,token,admin,stat_agent,banned) "
                    "VALUES(%1,'player%1',X'01',X'01','','player%1@test.invalid','check%1','token%1',0,0,0)").arg(i));
    SharedState state;
    auto login = [&](int id) {
        SharedState::ClientEntry c; c.npid = QString("player%1").arg(id);
        c.bloodborneMatching = true; c.matchingContextId = 1;
        state.clients.insert(id, c);
    };
    auto room = [&](QString com, quint64 id, QVector<int> ids) {
        Room r; r.roomId = id; r.ownerMemberId = static_cast<quint16>(ids.front());
        for (int user : ids) { RoomMember x; x.memberId = user; x.userId = user; x.joinDate = 100;
            x.npid = QString("player%1").arg(user); r.members.insert(user, x); }
        state.matching.rooms.insert({com,id}, r);
    };
    for (int i = 1; i <= 6; ++i) login(i);
    room("bloodborne", 22, {4});
    AuthenticatedRelay relay(&state);
    CHECK(!relay.Listen(QHostAddress::AnyIPv4, 0, temporary.filePath("fixture.db")));
    CHECK(!state.relayAuthReady.load());
    CHECK(relay.Listen(QHostAddress::LocalHost, 0, temporary.filePath("fixture.db")));
    CHECK(state.relayAuthReady.load()); const auto port = relay.Port();
    Client host(port), guest(port), guest2(port), other(port), unaffiliated(port);
    host.Auth(1,"token1",true); guest.Auth(2,"token2"); guest2.Auth(3,"token3");
    other.Auth(4,"token4"); unaffiliated.Auth(5,"token5");
    host.Drain(); guest.Drain(); guest2.Drain(); other.Drain(); unaffiliated.Drain();
    // Real native broker selection derives the prejoin recipient. Legacy game
    // aliases intentionally differ from the authenticated private account IDs.
    const QByteArray placement="1,21000000,00000000,00000000,00000000,00000000,0";
    const QByteArray refreshed="1,21000000,3f800000,00000000,00000000,00000000,0";
    CHECK(RelayAuth::ValidPlacement(placement));
    CHECK(!RelayAuth::ValidPlacement("1,21000000,7fc00000,0,0,0,0"));
    Bloodborne::SummonBroker::Options options;options.seamlessCoop=true;
    Bloodborne::SummonBroker* brokerPtr = nullptr;
    qint64 now = 100;
    qint64 grantTtlOverride = 0;
    options.relayPlacement=[&](const RelayAuth::PlacementRequest& p) {
        // A synchronous read through the broker would hang if the callback still held its mutex.
        CHECK(brokerPtr->Size(now) == 2);
        auto grant = p;
        if (grantTtlOverride) grant.ttlMs = grantTtlOverride;
        state.relayPlacement(grant);
    };
    options.relayCancelPlacement=[&](quint64 user){state.relayCancelPlacement(user);};
    Bloodborne::SummonBroker broker(options);
    brokerPtr = &broker;
    auto identity=[&](int user){return Bloodborne::SummonIdentity{quint64(user),QCryptographicHash::hash(QByteArray("token")+QByteArray::number(user),QCryptographicHash::Sha256)};};
    // A new sign from the same publisher supersedes even a same-map claim
    // which never allocated a relay placement record. S1 holds until that sign.
    {
        Bloodborne::SummonBroker::Options replacementOptions;
        replacementOptions.seamlessCoop = true;
        Bloodborne::SummonBroker replacementBroker(replacementOptions);
        const auto oldSign = QJsonDocument::fromJson(R"({"SessionId":"zero-old","UserId":7005,"CharaId":55,"AreaId":553648128,"AreaRegionId":1,"SummonType":0,"MatchingLevel":30})").object();
        const auto rawOld = QJsonDocument(oldSign).toJson();
        const auto searchOne = QJsonDocument::fromJson(R"({"UserId":9001,"AreaId":553648128,"GetMaxCount":1})").object();
        const auto oldClaim = QJsonDocument::fromJson(R"({"SessionId":"zero-old","UserId":9001,"TargetUserId":7005})").object();
        const RelayAuth::SummonRoom firstRoom{11,100,1}, retryRoom{33,200,1};
        CHECK(replacementBroker.Advertise(oldSign,rawOld,100,identity(5)).authorized);
        CHECK(replacementBroker.Claim(oldClaim,QJsonDocument(oldClaim).toJson(),101,{},identity(1),firstRoom).status == Bloodborne::SummonBroker::ClaimStatus::Claimed);
        CHECK(replacementBroker.Advertise(oldSign,rawOld,102,identity(5)).state == Bloodborne::SummonBroker::State::Delivered);
        const auto retry = replacementBroker.Search(searchOne,103,{},identity(1),retryRoom);
        CHECK(retry.size()==1 && retry.front().contains("zero-old")); // S1, no new sign yet.
        CHECK(replacementBroker.Claim(oldClaim,QJsonDocument(oldClaim).toJson(),104,{},identity(1),retryRoom).status != Bloodborne::SummonBroker::ClaimStatus::Conflict);
        auto next = oldSign; next["SessionId"] = "zero-new";
        CHECK(replacementBroker.Advertise(next,QJsonDocument(next).toJson(),105,identity(5)).authorized);
        const auto fresh = replacementBroker.Search(searchOne,106,{},identity(1),retryRoom);
        std::cout << "No-placement replacement: count=" << fresh.size()
                  << " old-visible=" << (fresh.size() && fresh.front().contains("zero-old")) << std::endl;
        CHECK(fresh.size()==1 && fresh.front().contains("zero-new"));
        CHECK(replacementBroker.StateFor("zero-old",7005,106)==Bloodborne::SummonBroker::State::Consumed);
        CHECK(replacementBroker.Claim(oldClaim,QJsonDocument(oldClaim).toJson(),107,{},identity(1),retryRoom).status == Bloodborne::SummonBroker::ClaimStatus::NotFound);
    }
    const QByteArray rawSign=R"({"SessionId":"private-sign","UserId":7005,"CharaId":9223372036854775808,"AreaId":385875968,"AreaRegionId":1,"SummonData":"opaque","SummonDataVersion":3,"SummonType":0,"MatchingLevel":30})";
    auto sign=QJsonDocument::fromJson(rawSign).object();
    auto otherSign=sign; otherSign["SessionId"]="z-sign"; otherSign["UserId"]=7006;
    const auto otherRaw=QJsonDocument(otherSign).toJson();
    CHECK(broker.Advertise(sign,rawSign,100,identity(5)).authorized);
    CHECK(broker.Advertise(otherSign,otherRaw,100,identity(6)).authorized);
    CHECK(!broker.Advertise(sign,rawSign,101,identity(4)).authorized);
    auto stolen=sign;stolen["UserId"]=8004;
    CHECK(!broker.Advertise(stolen,QJsonDocument(stolen).toJson(),101,identity(4)).authorized);
    auto removal=QJsonDocument::fromJson(R"({"SessionId":"private-sign","UserId":7005,"Force":true})").object();
    CHECK(!broker.Consume(removal,102,identity(4)).authorized);
    const auto search=QJsonDocument::fromJson(R"({"UserId":9001,"AreaId":553648128,"GetMaxCount":20})").object();
    // The game's SessionId can belong to the requester; the target aliases
    // still select and verify the sign without replacing its private account ID.
    const auto rawClaim=QByteArray(R"({"SessionId":"requester-session","UserId":9001,"TargetUserId":7005,"HostData":"native-invite"})");
    const auto claim=QJsonDocument::fromJson(rawClaim).object();
    using Access = RelayAuth::SummonAccess;
    using Claim = Bloodborne::SummonBroker::ClaimStatus;
    using State = Bloodborne::SummonBroker::State;
    auto noPlacement = [](Client& peer, int ms = 40) {
        for (const auto& p : peer.Drain(ms)) CHECK(!p.startsWith("THP2") || Get<quint32>(p,36)==0);
    };
    auto positive = [&](const QByteArray& text, int ms = 320, quint64 expectedRoom = 11) {
        quint64 generation = 0;
        for (const auto& p : unaffiliated.Drain(ms)) if (p.startsWith("THP2") && Get<quint32>(p,36)) {
            CHECK(!generation && Get<quint64>(p,20)==expectedRoom && Get<quint32>(p,28)==1 && Get<quint32>(p,32)==5);
            CHECK(Get<quint32>(p,36)<=10000 && p.mid(40,p.size()-72)==text);
            generation = Get<quint64>(p,12);
        }
        CHECK(generation);
        return generation;
    };
    CHECK(state.relaySummonAdmission(1).access == Access::NoRoom);
    CHECK(broker.Search(search,++now,placement,identity(1)).size() == 2);
    CHECK(broker.Claim(claim,rawClaim,++now,placement,identity(1)).status == Claim::Conflict);
    CHECK(broker.StateFor("private-sign",7005,now) == State::Advertised);
    noPlacement(unaffiliated,320);

    room("bloodborne",11,{1,2,3});
    const auto admission = state.relaySummonAdmission(1);
    CHECK(admission.access == Access::Owner && admission.room.room == 11);
    CHECK(state.relaySummonAdmission(2).access == Access::Denied);
    const auto rivalRoom = state.relaySummonAdmission(4).room;
    auto makeClaim = [&](const QByteArray& text) {
        return broker.Claim(claim,rawClaim,++now,text,identity(1),admission.room).status;
    };
    CHECK(makeClaim(placement) == Claim::Claimed);
    // Even a full tick cannot send a grant while this writer is held. Receiving
    // it here proves the Claim callback enqueued it before returning.
    state.matching.roomsLock.lockForWrite();
    const auto first = positive(placement);
    CHECK(state.relaySummonAdmission(1).access == Access::Busy);
    state.matching.roomsLock.unlock();
    for (auto* peer : {&host,&guest,&guest2,&other}) noPlacement(*peer);
    CHECK(broker.Claim(claim,rawClaim,++now,refreshed,identity(4),rivalRoom).status == Claim::Conflict);
    auto wrongTarget = claim; wrongTarget["SessionId"]="private-sign"; wrongTarget["TargetUserId"]=7006;
    CHECK(broker.Claim(wrongTarget,rawClaim,++now,refreshed,identity(1),admission.room).status == Claim::Conflict);
    CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).pendingClaim.isEmpty());
    // Start just after a periodic heartbeat, so the next 250 ms Tick cannot
    // accidentally satisfy the 50 ms fast-retry check.
    unaffiliated.TakeFrames();
    QElapsedTimer heartbeat; heartbeat.start();
    bool sawTick = false;
    while (!sawTick && heartbeat.elapsed() < 450) {
        Pump(1);
        for (const auto& p : unaffiliated.TakeFrames()) if (p.startsWith("THX4")) sawTick = true;
    }
    CHECK(sawTick);
    state.clientsLock.lockForWrite();
    CHECK(makeClaim(refreshed) == Claim::AlreadyClaimed);
    for (const auto& p : unaffiliated.Drain(15)) CHECK(!p.startsWith("THP2") || Get<quint32>(p,36)==0);
    QElapsedTimer released; released.start();
    state.clientsLock.unlock();
    quint64 retried = 0;
    while (!retried && released.elapsed() < 50) {
        Pump(1);
        for (const auto& p : unaffiliated.TakeFrames()) if (p.startsWith("THP2") && Get<quint32>(p,36)) {
            CHECK(!retried && Get<quint64>(p,20)==11 && Get<quint32>(p,28)==1 && Get<quint32>(p,32)==5);
            CHECK(p.mid(40,p.size()-72)==refreshed);
            retried = Get<quint64>(p,12);
        }
    }
    const bool fastPlacement = retried > first && released.elapsed() <= 50;
    std::cout << "THP2 release-to-frame: " << released.elapsed() << " ms, received=" << bool(retried) << std::endl;
    if (!retried) retried = positive(refreshed);  // Also exercise the independent busy-queue scenario on red.
    CHECK(retried > first);
    const auto claimedSigns = broker.Search(search,++now,{},identity(1),admission.room);
    CHECK(claimedSigns.size() == 2 &&
          std::count_if(claimedSigns.begin(),claimedSigns.end(),[](const QByteArray& p) {
              return QJsonDocument::fromJson(p).object().value("SessionId") == QStringLiteral("private-sign");
          }) == 1);
    auto ready = sign; ready["AreaId"] = 0x21000000;
    const auto readyRaw = QJsonDocument(ready).toJson();
    CHECK(broker.Advertise(ready,readyRaw,++now,identity(5)).pendingClaim == rawClaim);
    CHECK(makeClaim(placement) == Claim::AlreadyClaimed);
    const auto delivered = positive(placement);
    // The original game can re-poll for a response it did not process.
    CHECK(broker.Advertise(ready,readyRaw,++now,identity(5)).pendingClaim == rawClaim);
    const auto nextSign = broker.Search(search,++now,{},identity(1),admission.room);
    auto sessions = [](const QList<QByteArray>& entries) {
        QStringList ids;
        for (const auto& p : entries) ids.append(QJsonDocument::fromJson(p).object().value("SessionId").toString());
        ids.sort();
        return ids;
    };
    const QStringList allSigns{"private-sign","z-sign"}, unclaimedSigns{"z-sign"};
    CHECK(sessions(nextSign) == allSigns);

    const auto oldRoom = state.matching.rooms.take({"bloodborne",11});
    room("bloodborne",33,{1});
    const auto newAdmission = state.relaySummonAdmission(1);
    CHECK(newAdmission.access == Access::Owner && newAdmission.room.room == 33);
    CHECK(sessions(broker.Search(search,++now,{},identity(1),newAdmission.room)) == allSigns);
    CHECK(sessions(broker.Search(search,++now,{},identity(1))) == unclaimedSigns);
    CHECK(sessions(broker.Search(search,++now,{},identity(4),rivalRoom)) == unclaimedSigns);
    auto rotated = identity(1); rotated.tokenHash = identity(4).tokenHash;
    CHECK(sessions(broker.Search(search,++now,{},rotated,newAdmission.room)) == unclaimedSigns);
    CHECK(broker.Claim(claim,rawClaim,++now,refreshed,rotated,newAdmission.room).status == Claim::Conflict);
    CHECK(broker.Claim(claim,rawClaim,++now,refreshed,identity(1)).status == Claim::Conflict);
    noPlacement(unaffiliated);
    auto reboundClaim = claim; reboundClaim["HostData"] = "new-room-invite";
    const auto reboundRaw = QJsonDocument(reboundClaim).toJson();
    CHECK(broker.Claim(reboundClaim,reboundRaw,++now,refreshed,identity(1),newAdmission.room).status != Claim::Conflict);
    const auto rebound = positive(refreshed,320,33);
    CHECK(rebound > delivered);
    for (auto* peer : {&host,&guest,&guest2,&other}) noPlacement(*peer);
    CHECK(broker.Advertise(ready,readyRaw,++now,identity(5)).pendingClaim == reboundRaw);
    CHECK(broker.Claim(claim,rawClaim,++now,placement,identity(1),newAdmission.room).status == Claim::Conflict);
    auto normalDelete = removal; normalDelete["Force"] = false;
    CHECK(broker.Consume(normalDelete,++now,identity(5)).retained == 1);
    for (const auto& p : unaffiliated.Drain()) CHECK(!p.startsWith("THP2"));
    CHECK(broker.Consume(removal,++now,identity(5)).consumed == 1);
    bool revoked = false;
    for (const auto& p : unaffiliated.Drain()) if (p.startsWith("THP2")) {
        CHECK(Get<quint32>(p,36)==0 && Get<quint64>(p,12)==rebound && p.size()==72);
        revoked = true;
    }
    CHECK(revoked);
    state.matching.rooms.remove({"bloodborne",33});
    state.matching.rooms.insert({"bloodborne",11},oldRoom);

    // Joining the exact room hands an already delivered v2 hint to native
    // loading. Cleanup must not send a revoke racing that handoff.
    CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).authorized);
    CHECK(makeClaim(placement) == Claim::Claimed);
    positive(placement);
    RoomMember handoffGuest;
    handoffGuest.memberId = 5; handoffGuest.userId = 5; handoffGuest.npid = "player5";
    handoffGuest.joinDate = 199;
    state.matching.rooms[{"bloodborne",11}].members.insert(5,handoffGuest);
    bool handedOff = false;
    for (const auto& p : unaffiliated.Drain(350)) {
        CHECK(!p.startsWith("THP2"));
        if (p.startsWith("THX4") && Get<quint64>(p,12)==11) handedOff = true;
    }
    CHECK(handedOff);
    state.matching.rooms[{"bloodborne",11}].members.remove(5);
    CHECK(broker.Consume(removal,++now,identity(5)).consumed == 1);
    unaffiliated.Drain(350);

    // Search picks one sign, keeps the reservation on re-advertisement, and
    // does not extend its deadline on either search or publisher refresh.
    CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).authorized);
    const auto reserveAt = ++now;
    CHECK(broker.Search(search,now,placement,identity(1),admission.room).isEmpty());
    const auto reservedGeneration = positive(placement);
    CHECK(broker.StateFor("private-sign",7005,now) == State::Preparing);
    const auto available = broker.Search(search,++now,{},identity(4),rivalRoom);
    CHECK(available.size() == 1 && available.front().contains("z-sign"));
    now = reserveAt + RelayAuth::PlacementTtlMs - 1;
    CHECK(broker.Advertise(ready,readyRaw,now,identity(5)).authorized);
    CHECK(broker.Consume(normalDelete,now,identity(5)).retained == 1);
    const auto reserved = broker.Search(search,now,placement,identity(1),admission.room);
    const auto declined = broker.Claim(claim,rawClaim,now,placement,identity(1),admission.room);
    std::cout << "R1 removed sign: search count=" << reserved.size()
              << ", claim conflict=" << (declined.status == Claim::Conflict) << std::endl;
    CHECK(reserved.isEmpty() && declined.status == Claim::Conflict);
    CHECK(broker.StateFor("private-sign",7005,now) == State::Advertised);
    CHECK(broker.Claim(claim,rawClaim,now,placement,identity(4),rivalRoom).status == Claim::Conflict);
    CHECK(sessions(broker.Search(search,now,{},identity(4),rivalRoom)) == unclaimedSigns);
    for (const auto& p : unaffiliated.Drain(40)) CHECK(!p.startsWith("THP2"));
    ++now;
    CHECK(sessions(broker.Search(search,now,{},identity(4),rivalRoom)) == unclaimedSigns);
    CHECK(broker.StateFor("private-sign",7005,now) == State::Consumed);
    int expiryRevokes = 0;
    for (const auto& p : unaffiliated.Drain()) if (p.startsWith("THP2")) {
        CHECK(Get<quint32>(p,36)==0 && Get<quint64>(p,12)==reservedGeneration);
        ++expiryRevokes;
    }
    CHECK(expiryRevokes == 1);
    // Match z-sign's map so this search would prepare only a revived private-sign.
    CHECK(sessions(broker.Search(search,now,"1,17000000,00000000,00000000,00000000,00000000,0",
                                identity(4),rivalRoom)) == unclaimedSigns);
    noPlacement(unaffiliated);

    // A fresh, authenticated advertisement clears the ordinary delete, but
    // must not refresh the old reservation's deadline.
    CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).authorized);
    const auto readvertisedAt = ++now;
    CHECK(broker.Search(search,now,placement,identity(1),admission.room).isEmpty());
    positive(placement);
    CHECK(broker.Consume(normalDelete,++now,identity(5)).retained == 1);
    CHECK(broker.Advertise(ready,readyRaw,++now,identity(5)).authorized);
    now = readvertisedAt + RelayAuth::PlacementTtlMs;
    CHECK(sessions(broker.Search(search,now,{},identity(4),rivalRoom)) == allSigns);
    CHECK(broker.StateFor("private-sign",7005,now) == State::Advertised);
    noPlacement(unaffiliated);

    CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).authorized);
    CHECK(broker.Search(search,++now,placement,identity(1),admission.room).isEmpty());
    positive(placement);
    const auto cancelledAt = ++now;
    CHECK(broker.Consume(removal,cancelledAt,identity(5)).consumed == 1);
    const auto otherCandidates = broker.Search(search,++now,{},identity(1),admission.room);
    CHECK(otherCandidates.size() == 1 && otherCandidates.front().contains("z-sign"));
    CHECK(broker.StateFor("private-sign",7005,cancelledAt + RelayAuth::PlacementTtlMs + 1) != State::Advertised);
    unaffiliated.Drain();

    // Claiming another valid sign replaces the owner's pending preparation,
    // but a rejected claim must leave both existing owners' grants alone.
    {
        Client targetY(port);
        targetY.Auth(6,"token6");
        targetY.Drain();
        auto claimY = claim; claimY["TargetUserId"] = 7006;
        const auto rawY = QJsonDocument(claimY).toJson();
        auto removeY = removal; removeY["UserId"] = 7006; removeY["SessionId"] = "z-sign";
        const auto receiveY = [&](quint64 expectedRoom, quint32 expectedHost) {
            quint64 generation = 0;
            for (const auto& p : targetY.Drain(320)) if (p.startsWith("THP2") && Get<quint32>(p,36)) {
                CHECK(!generation && Get<quint64>(p,20)==expectedRoom &&
                      Get<quint32>(p,28)==expectedHost && Get<quint32>(p,32)==6);
                generation = Get<quint64>(p,12);
            }
            CHECK(generation);
            return generation;
        };
        CHECK(broker.Claim(claimY,rawY,++now,placement,identity(4),rivalRoom).status == Claim::Claimed);
        const auto rivalY = receiveY(22,4);
        CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).authorized);
        CHECK(broker.Search(search,++now,placement,identity(1),admission.room).isEmpty());
        const auto reservationX = positive(placement);
        CHECK(broker.Claim(claimY,rawY,++now,placement,identity(1),admission.room).status == Claim::Conflict);
        CHECK(broker.StateFor("private-sign",7005,now) == State::Preparing);
        CHECK(broker.StateFor("z-sign",7006,now) == State::Claimed);
        for (auto* peer : {&unaffiliated,&targetY})
            for (const auto& p : peer->Drain(40)) CHECK(!p.startsWith("THP2"));

        CHECK(broker.Consume(removeY,++now,identity(6)).consumed == 1);
        targetY.Drain();
        CHECK(broker.Advertise(otherSign,otherRaw,++now,identity(6)).authorized);
        CHECK(broker.Claim(claimY,rawY,++now,refreshed,identity(1),admission.room).status == Claim::Claimed);
        CHECK(receiveY(11,1) > rivalY);
        int revokedX = 0;
        for (const auto& p : unaffiliated.Drain()) if (p.startsWith("THP2")) {
            CHECK(Get<quint32>(p,36)==0 && Get<quint64>(p,12)==reservationX);
            ++revokedX;
        }
        CHECK(revokedX == 1);
        CHECK(sessions(broker.Search(search,++now,{},identity(4),rivalRoom)) == QStringList{"private-sign"});
        CHECK(broker.Consume(removeY,++now,identity(6)).consumed == 1);
        CHECK(broker.Advertise(otherSign,otherRaw,++now,identity(6)).authorized);
    }

    // Exercise the real authenticated HTTP route while matching reports Busy.
    {
        SharedState webState;
        bool busy = false;
        webState.relaySummonAdmission = [&](quint64 user) -> RelayAuth::SummonAdmission {
            if (busy) return {Access::Busy,{}};
            if (user == 1) return {Access::Owner,admission.room};
            if (user == 4) return {Access::Owner,rivalRoom};
            return {Access::NoRoom,{}};
        };
        QList<RelayAuth::PlacementRequest> grants;
        QList<quint64> cancels;
        webState.relayPlacement = [&](const RelayAuth::PlacementRequest& p) { grants.append(p); };
        webState.relayCancelPlacement = [&](quint64 record) { cancels.append(record); };
        QHttpServer http;
        auto* tcp = new QTcpServer(&http);  // bind gives the listener the HTTP server's lifetime.
        WebApiRoutes::RegisterBloodborneRoutes(http,true,db,webState);
        CHECK(tcp->listen(QHostAddress::LocalHost,0) && http.bind(tcp));
        QNetworkAccessManager httpClient;
        struct Response { int code; QByteArray type; QJsonObject body; };
        auto post = [&](const char* endpoint, int user, const QByteArray& body, const QByteArray& hint = {}) {
            QNetworkRequest req(QUrl(QString("http://127.0.0.1:%1/summon_messenger/%2?user_id=%3")
                                    .arg(tcp->serverPort()).arg(QString::fromLatin1(endpoint)).arg(user)));
            req.setHeader(QNetworkRequest::ContentTypeHeader,"application/json");
            req.setRawHeader("Authorization","Bearer token"+QByteArray::number(user));
            if (!hint.isEmpty()) req.setRawHeader("X-ShadPS4-Bloodborne-Host-Placement",hint);
            QNetworkReply* reply = httpClient.post(req,body);
            QElapsedTimer deadline; deadline.start();
            while (!reply->isFinished() && deadline.elapsed() < 2000) Pump(1);
            CHECK(reply->isFinished());
            Response response{reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt(),
                              reply->rawHeader("Content-Type"),QJsonDocument::fromJson(reply->readAll()).object()};
            reply->deleteLater();
            return response;
        };
        const auto query = QJsonDocument(search).toJson();
        CHECK(post("create",5,rawSign).code == 200);
        CHECK(post("get",1,query,placement).code == 200);
        CHECK(grants.size() == 1 && grants.front().target == 5 && grants.front().room.room == 11 && cancels.isEmpty());
        CHECK(post("create",5,readyRaw).code == 200);
        busy = true;
        const auto whileBusy = post("get",1,query,placement);
        CHECK(whileBusy.code == 200 && whileBusy.type.startsWith("application/json"));
        CHECK(whileBusy.body.value("ResKind").toInt(-1) == 0 &&
              whileBusy.body.value("MessageId") == QStringLiteral("SummonDataGetListResponse") &&
              whileBusy.body.value("SummonDataList").isArray() &&
              whileBusy.body.value("SummonDataList").toArray().isEmpty());
        CHECK(post("request",1,rawClaim,placement).code == 503);
        busy = false;
        CHECK(post("get",4,query).body.value("SummonDataList").toArray().isEmpty());
        const auto kept = post("get",1,query,placement).body.value("SummonDataList").toArray();
        CHECK(kept.size() == 1 && kept.first().toObject().value("SessionId") == QStringLiteral("private-sign"));
        CHECK(grants.size() == 1 && cancels.isEmpty());

        CHECK(post("delete",5,QJsonDocument(normalDelete).toJson()).code == 200);
        const auto removed = post("get",1,query,placement);
        CHECK(removed.code == 200 && removed.body.value("SummonDataList").toArray().isEmpty());
        static QStringList conflictLogs;
        const auto previous = qInstallMessageHandler([](QtMsgType type, const QMessageLogContext&, const QString& line) {
            if (type == QtWarningMsg && line.startsWith("relay placement dropped reason=claim-conflict"))
                conflictLogs.append(line);
        });
        const auto deniedClaim = post("request",1,rawClaim,placement);
        qInstallMessageHandler(previous);
        CHECK(deniedClaim.code == 200);
        CHECK(conflictLogs.size() == 1);
        std::cout << "R2 log: " << conflictLogs.front().toStdString() << std::endl;
        CHECK(conflictLogs.front() ==
              QStringLiteral("relay placement dropped reason=claim-conflict targetSessionId=\"private-sign\" caller=1"));
        CHECK(grants.size() == 1 && cancels.isEmpty());
        CHECK(post("create",5,readyRaw).code == 200);
        const auto republished = post("get",1,query,placement);
        CHECK(republished.code == 200);
        const auto restoredSigns = republished.body.value("SummonDataList").toArray();
        CHECK(restoredSigns.size() == 1 &&
              restoredSigns.first().toObject().value("SessionId") == QStringLiteral("private-sign"));
        CHECK(post("request",1,rawClaim,placement).code == 200);
        CHECK(grants.size() == 2 && grants.back().record == grants.front().record &&
              grants.back().target == 5 && cancels.isEmpty());
    }

    // Exercise terminal conditions while the new generation has never been sent.
    for (int failure = 0; failure < 5; ++failure) {
        CHECK(broker.Advertise(sign,rawSign,++now,identity(5)).authorized);
        state.matching.roomsLock.lockForWrite();
        grantTtlOverride = failure == 3 ? 30 : 0;
        CHECK(makeClaim(placement) == Claim::Claimed);
        grantTtlOverride = 0;
        if (failure == 0) {
            CHECK(broker.Consume(removal,++now,identity(5)).consumed == 1);
        } else if (failure == 1) {
            state.matching.rooms.remove({"bloodborne",11});
            room("bloodborne",33,{1,2,3});
        } else if (failure == 2) {
            room("other-title",99,{5});
        } else if (failure == 3) {
            noPlacement(unaffiliated,320); // TTL expires before the writer releases.
        } else {
            auto replacement = sign;
            replacement["SessionId"] = "replacement-sign";
            CHECK(broker.Advertise(replacement,QJsonDocument(replacement).toJson(),++now,identity(5)).authorized);
        }
        state.matching.roomsLock.unlock();
        noPlacement(unaffiliated,320);
        if (failure && failure < 4) CHECK(broker.Consume(removal,++now,identity(5)).consumed == 1);
        if (failure == 1) {
            state.matching.rooms.remove({"bloodborne",33});
            room("bloodborne",11,{1,2,3});
        } else if (failure == 2) {
            state.matching.rooms.remove({"other-title",99});
        }
    }
    host.Drain();guest.Drain();guest2.Drain();other.Drain();

    // A busy tick must not synthesize a leave, and verified traffic must
    // resume in original per-sender order using the latest native membership.
    state.matching.roomsLock.lockForWrite();
    host.Send(World(2,0)); host.Send(World(2,1));
    for (const auto& p : guest.Drain(320))
        CHECK(!p.startsWith("THS2") && (!p.startsWith("THX4") || Get<quint64>(p,12)==11));
    state.matching.roomsLock.unlock();
    int chunks = 0;
    for (const auto& p : guest.Drain(320)) if (p.startsWith("THS2")) {
        CHECK(chunks < 2 && p.mid(28,p.size()-60)==World(2,chunks));
        ++chunks;
    }
    CHECK(chunks == 2 && guest2.DataCount() == 2);

    const std::array<QByteArray,3> ordered{World(2,2),World(2,3),World(2,4)};
    state.matching.roomsLock.lockForWrite();
    QElapsedTimer longBusy; longBusy.start();
    host.Send(ordered[0]); host.Send(ordered[1]);
    for (const auto& p : guest.Drain(std::max(1,1200-int(longBusy.elapsed()))))
        CHECK(!p.startsWith("THS2") && (!p.startsWith("THX4") || Get<quint64>(p,12)==11));
    state.matching.roomsLock.unlock();
    const auto heldMs = longBusy.elapsed();
    host.Send(ordered[2]);  // All queued frames must precede the next live frame.
    chunks = 0;
    for (const auto& p : guest.Drain(320)) if (p.startsWith("THS2")) {
        CHECK(chunks < int(ordered.size()) && Get<quint64>(p,12)==11 && p.mid(28,p.size()-60)==ordered[chunks]);
        ++chunks;
    }
    std::cout << "Matching continuously busy: " << heldMs << " ms, frames delivered=" << chunks << std::endl;
    CHECK(heldMs >= 1200 && chunks == int(ordered.size()) && !host.Closed(1));
    CHECK(guest2.DataCount() == int(ordered.size()) && other.DataCount() == 0 && fastPlacement);
    state.clientsLock.lockForWrite();
    guest.Send(Life());
    state.matching.rooms[{"bloodborne",11}].members[2].joinDate = 101;
    state.clientsLock.unlock();
    CHECK(host.DataCount(320) == 0 && guest2.DataCount() == 0);
    guest.Drain();
    host.Send(Follow());
    auto received = guest.Drain(); bool valid = false;
    for (auto p : received) if (p.startsWith("THS2")) {
        CHECK(Get<quint64>(p,12)==11 && Get<quint32>(p,20)==1 && Get<quint32>(p,24)==1);
        CHECK(p.mid(28,p.size()-60)==Follow()); valid=true;
    }
    CHECK(valid && guest2.DataCount()==1 && other.DataCount()==0 && unaffiliated.DataCount()==0);
    for(auto world : {World(1),World(2,7)}) {
        host.Send(world); bool delivered=false;
        for(auto p:guest.Drain()) if(p.startsWith("THS2")) { CHECK(p.mid(28,p.size()-60)==world);delivered=true; }
        CHECK(delivered && guest2.DataCount()==1 && other.DataCount()==0);
    }
    guest.Send(Follow()); CHECK(host.DataCount()==0 && guest2.DataCount()==0);
    guest.Send(Pvp(1)); CHECK(host.DataCount()==1 && guest2.DataCount()==0);
    host.Send(Pvp(0)); CHECK(guest.DataCount()==1 && guest2.DataCount()==1);
    guest.Send(Life());
    int lifeCount=0;
    for(auto p:host.Drain()) if(p.startsWith("THS2")) {
        CHECK(Get<quint32>(p,33)==2 && p[37]==Life()[9] && Get<quint32>(p,38)==Get<quint32>(Life(),10));
        ++lifeCount;
    }
    CHECK(lifeCount==1 && guest2.DataCount()==1 && other.DataCount()==0);
    unaffiliated.Send(Life()); CHECK(host.DataCount()==0 && guest.DataCount()==0);
    // The same numeric room ID in another ComId is still a different native room.
    state.matching.rooms.remove({"bloodborne",22}); room("another-title",11,{4});
    host.Send(Follow()); CHECK(other.DataCount()==0); guest.Drain(); guest2.Drain();
    // Missing and ambiguous membership fail closed for BOTH recipients and senders.
    state.matching.rooms[{"bloodborne",11}].members.remove(2);
    host.Send(Follow()); CHECK(guest.DataCount()==0); guest2.Drain();
    state.matching.rooms[{"bloodborne",11}].members.insert(2, RoomMember{});
    auto& member = state.matching.rooms[{"bloodborne",11}].members[2];
    member.memberId=2; member.userId=2; member.npid="player2"; member.joinDate=200;
    host.Send(Follow()); bool sawClear=false, sawRejoin=false;
    for (auto p : guest.Drain()) if(p.startsWith("THX4")) {
        if(Get<quint64>(p,12)==0) sawClear=true; else if(Get<quint64>(p,12)==11) sawRejoin=true;
    }
    CHECK(sawRejoin); guest2.Drain();
    // A quick rejoin without a tick also emits zero then the new incarnation.
    state.matching.rooms[{"bloodborne",11}].members[2].joinDate=300;
    host.Send(Follow()); sawClear=sawRejoin=false;
    for(auto p:guest.Drain()) if(p.startsWith("THX4")) {
        if(Get<quint64>(p,12)==0) sawClear=true; else if(Get<quint64>(p,12)==11) sawRejoin=true;
    }
    CHECK(sawClear && sawRejoin); guest2.Drain();
    room("ambiguous",33,{2}); guest.Send(Life()); CHECK(host.DataCount()==0 && guest2.DataCount()==0);
    host.Send(Follow()); CHECK(guest.DataCount()==0); guest2.Drain();
    state.matching.rooms.remove({"ambiguous",33});
    // Native owner migration changes authority immediately, independent of client claims.
    state.matching.rooms[{"bloodborne",11}].ownerMemberId=2;
    host.Send(Follow()); CHECK(guest.DataCount()==0 && guest2.DataCount()==0);
    guest.Send(Follow()); CHECK(host.DataCount()==1 && guest2.DataCount()==1);
    state.clients.remove(3); guest.Send(Follow()); CHECK(guest2.DataCount()==0); host.Drain();
    login(3);
    // Reset and ban revoke on dispatch or the next <=250ms control tick.
    sql("UPDATE account SET token='replacement' WHERE user_id=3"); guest.Send(Life());
    CHECK(guest2.Closed()); host.Drain();
    sql("UPDATE account SET banned=1 WHERE user_id=4"); CHECK(other.Closed(320));
    Client bad(port); bad.Raw(bad.AuthPayload(6,"wrong")); CHECK(bad.Closed());
    Client missing(port); missing.Raw(missing.AuthPayload(99,"wrong")); CHECK(missing.Closed());
    Client wide(port); wide.Raw(wide.AuthPayload(quint64(1)<<32,"token1")); CHECK(wide.Closed());
    Client banned(port); banned.Raw(banned.AuthPayload(4,"token4")); CHECK(banned.Closed());
    sql("UPDATE account SET token='' WHERE user_id=6");
    Client empty(port); empty.Raw(empty.AuthPayload(6,"")); CHECK(empty.Closed());
    sql("UPDATE account SET token='token6' WHERE user_id=6");
    Client replayA(port); auto oldProof=replayA.AuthPayload(6,"token6");
    replayA.socket.abort(); Pump(); Client replayB(port);
    CHECK(replayA.nonce!=replayB.nonce); replayB.Raw(oldProof); CHECK(replayB.Closed());
    Client duplicate(port); duplicate.Raw(duplicate.AuthPayload(1,"token1")); CHECK(duplicate.Closed());
    Client tamper(port); tamper.Auth(6,"token6"); tamper.Drain();
    auto modified=tamper.Data(Life()); modified[17]^=1; tamper.Raw(modified); CHECK(tamper.Closed());
    Pump(300);
    Client replay(port); replay.Auth(6,"token6"); replay.Drain();
    auto once=replay.Data(Life()); replay.Raw(once); replay.Raw(once); CHECK(replay.Closed()); Pump(300);
    Client order(port); order.Auth(6,"token6"); order.Drain(); order.Raw(order.Data(Life(),2)); CHECK(order.Closed()); Pump(300);
    for(auto spoof : {QByteArray("SHAD\x40",5), QByteArray("THX4",4), QByteArray("SHAD\x7f",5), Follow().chopped(1)}) {
        Client control(port); control.Auth(6,"token6"); control.Drain(); control.Raw(control.Data(spoof)); CHECK(control.Closed()); Pump(260);
    }
    Client size(port); QByteArray oversized; Put(oversized, quint32(65537)); size.socket.write(oversized); CHECK(size.Closed());
    Client slow(port); slow.socket.write(QByteArray(1,'x')); CHECK(slow.Closed(5300));
    Client rate(port); rate.Auth(6,"token6"); rate.Drain(); QByteArray flood;
    for(int i=0;i<257;++i) flood+=RelayAuth::Frame(rate.Data(Life()));
    // The server yields after each 32 frames; allow those event-loop turns
    // inside the one-second ingress window even on a coarse Windows timer.
    rate.socket.write(flood); CHECK(rate.Closed(500));
    // A valid token can authenticate before the game's NP login. It stays
    // connected without a room, then acquires context after the live login/join.
    Pump(300); state.clients.remove(6);
    Client early(port); early.Auth(6,"token6"); early.Drain(320); CHECK(!early.Closed());
    early.Send(Life()); CHECK(host.DataCount()==0 && guest.DataCount()==0);
    login(6);
    auto newMember=state.matching.rooms[{"bloodborne",11}].members[2];
    newMember.memberId=6;newMember.userId=6;newMember.npid="player6";newMember.joinDate=600;
    state.matching.rooms[{"bloodborne",11}].members.insert(6,newMember);
    guest.Send(Follow()); CHECK(early.DataCount()==1); host.Drain();
    // A recipient which stops reading must be dropped with bounded output.
    early.socket.setSocketOption(QAbstractSocket::ReceiveBufferSizeSocketOption,1024);
    early.socket.setReadBufferSize(1);
    QByteArray bulkPlacement(60000,'x');bulkPlacement.replace(0,4,"SHAD");bulkPlacement[4]=0x30;
    // A single short burst can fit in the OS TCP receive window even when the
    // application stops reading. Sustain bounded traffic below ingress limits
    // until that window fills; keep the healthy recipient drained throughout.
    for(int batch=0;batch<36 && !early.Closed(1);++batch) {
        QByteArray outputFlood;
        for(int i=0;i<3;++i) outputFlood+=RelayAuth::Frame(guest.Data(bulkPlacement));
        guest.socket.write(outputFlood); Pump(350);
        CHECK(host.DataCount(1)==3);
        CHECK(!guest.Closed(1) && !host.Closed(1));
    }
    // With a zero receive window, TCP can delay delivery of the remote close.
    // Prove the SERVER released the old identity: duplicate live identities are
    // rejected above, so this new authenticated connection can succeed only
    // after the blocked server-side peer has been dropped.
    {
        Client recovered(port);recovered.Auth(6,"token6");recovered.Drain();
        CHECK(!recovered.Closed());
        recovered.socket.abort();early.socket.abort();
    }
    // A newer queued frame cannot reset the age of the first frame.
    state.matching.roomsLock.lockForWrite();
    QElapsedTimer timeout; timeout.start();
    host.Send(Life());
    CHECK(!host.Closed(std::max(1,1000-int(timeout.elapsed()))));
    host.Send(Life());
    CHECK(!host.Closed(std::max(1,1200-int(timeout.elapsed()))));
    while (!host.Closed(1) && timeout.elapsed() < 1850) {}
    const auto disconnected = timeout.elapsed();
    const bool didClose = host.socket.state() == QAbstractSocket::UnconnectedState;
    state.matching.roomsLock.unlock();
    std::cout << "Continuously busy queue disconnected: " << disconnected << " ms" << std::endl;
    CHECK(didClose && disconnected >= 1500 && disconnected < 1850);

    // Isolate admission capacity from the earlier peer lifetimes. In
    // particular, a slow reader or a revoked peer need not still be present.
    relay.Stop();
    CHECK(relay.Listen(QHostAddress::LocalHost, 0, temporary.filePath("fixture.db")));
    const auto capacityPort = relay.Port();
    std::vector<std::unique_ptr<Client>> capacity;
    // Open to the fixed cap; the next connection receives no challenge and closes.
    for(int i=0;i<64;++i) {
        auto s=std::make_unique<Client>(capacityPort); capacity.push_back(std::move(s));
    }
    QTcpSocket overflow; overflow.connectToHost(QHostAddress::LocalHost,capacityPort); Pump(200);
    CHECK(overflow.state()==QAbstractSocket::UnconnectedState && overflow.bytesAvailable()==0);
    CHECK(capacity.front()->Closed(5300));
    capacity.clear();
    relay.Stop(); CHECK(!state.relayAuthReady.load());
    QFile cfg(temporary.filePath("disabled.cfg")); CHECK(cfg.open(QIODevice::WriteOnly));
    cfg.write("[General]\nHost=127.0.0.1\nBloodborneSeamlessCoop=false\n"); cfg.close();
    ConfigManager config; config.Load(cfg.fileName()); CHECK(relay.Start(&config,temporary.filePath("fixture.db")));
    CHECK(relay.Port()==0 && !state.relayAuthReady.load());
    CHECK(cfg.open(QIODevice::WriteOnly|QIODevice::Truncate));
    cfg.write("[General]\nHost=127.0.0.1\nBloodborneSeamlessCoop=true\n");cfg.close();config.Load(cfg.fileName());
    qputenv("THEHUNT_RELAY_PORT","invalid");CHECK(!relay.Start(&config,temporary.filePath("fixture.db")));
    CHECK(!state.relayAuthReady.load());qunsetenv("THEHUNT_RELAY_PORT");
    {
        for(int i=1;i<=3;++i) {login(i);sql(QString("UPDATE account SET token='token%1',banned=0 WHERE user_id=%1").arg(i));}
        state.matching.rooms.clear();room("bloodborne",999,{1,2,3});
        CHECK(relay.Listen(QHostAddress::LocalHost,0,temporary.filePath("fixture.db")));
        Client host3(relay.Port());host3.Auth3(1,"token1");
        Client guest3(relay.Port());guest3.Auth3(2,"token2");
        Client guest2(relay.Port());guest2.Auth(3,"token3");
        const auto controls=host3.Drain(1100);guest3.Drain();guest2.Drain();
        bool roster=false;
        for(const auto& p:controls) if(p.startsWith("THX4") && p.size()==62+3*31) {
            CHECK(static_cast<unsigned char>(p[29])==3);roster=true;
        }
        CHECK(roster);
        auto ready=[](int kind,quint32 target) {
            QByteArray p("SHAD",4);p.append(char(0x28));p.append(char(1));p.append(char(kind));p.append(char(kind==1 || kind==2));
            Put(p,target);Put(p,quint64(111));Put(p,quint64(222));
            Put(p,quint64(kind?333:0));Put(p,quint64(kind?444:0));Put(p,quint64(kind?555:0));Put(p,quint32(0x18000000));return p;
        };
        host3.Send(ready(0,2));CHECK(guest3.DataCount()==1 && guest2.DataCount()==0);
        guest3.Send(ready(1,1));CHECK(host3.DataCount()==1 && guest2.DataCount()==0);
        guest3.Send(ready(0,1));CHECK(host3.DataCount()==0 && !guest3.Closed());
        host3.Send(ready(0,3));CHECK(guest2.DataCount()==1 && !guest2.Closed());
        auto reverse=[&](int kind,quint32 target,quint64 senderJoin,quint64 targetJoin) {
            auto p=ready(kind==4?0:1,target);p[5]=2;p[6]=kind;
            Put(p,quint64(999));Put(p,senderJoin);Put(p,targetJoin);Put(p,quint64(1));Put(p,quint64(2));return p;
        };
        const auto& nativeRoom=state.matching.rooms.value({"bloodborne",999});
        const auto hj=nativeRoom.members.value(1).joinDate, gj=nativeRoom.members.value(2).joinDate;
        auto query=reverse(4,1,gj,hj), status=reverse(5,2,hj,gj);
        CHECK(RelayAuth::ValidInner(query) && RelayAuth::ValidInner(status));
        guest3.Send(query);CHECK(host3.DataCount()==1 && guest2.DataCount()==0);
        host3.Send(status);CHECK(guest3.DataCount()==1 && guest2.DataCount()==0);
        auto wrong=query;qToLittleEndian<quint64>(998,wrong.data()+56);guest3.Send(wrong);CHECK(host3.DataCount()==0);
        wrong=query;qToLittleEndian<quint64>(gj+1,wrong.data()+64);guest3.Send(wrong);CHECK(host3.DataCount()==0);
        wrong=query;qToLittleEndian<quint64>(hj+1,wrong.data()+72);guest3.Send(wrong);CHECK(host3.DataCount()==0);
        CHECK(!RelayAuth::Allowed(query,true,false) && !RelayAuth::Allowed(status,false,true));
        wrong=query;wrong[5]=1;CHECK(!RelayAuth::ValidInner(wrong));
        CHECK(!RelayAuth::ValidInner(query.chopped(1)));
        for(const char* legacy:{"THA2","THA3"}) {
            Client old(relay.Port());auto proof=old.AuthPayload(3,"token3");proof.replace(0,4,legacy);old.Raw(proof);CHECK(old.Closed());
        }
        relay.Stop();
    }

    // Independent queue boundary regressions. Each scene uses fresh peers and
    // synthetic membership, avoiding rate windows/output backpressure elsewhere.
    for (int scenario = 0; scenario < 5; ++scenario) {
        relay.Stop();
        for (int i = 1; i <= 2; ++i) {
            login(i); sql(QString("UPDATE account SET token='token%1',banned=0 WHERE user_id=%1").arg(i));
        }
        state.matching.rooms.clear();
        if (scenario <= 1 || scenario == 4) room("bloodborne",777,{1,2});
        else room("bloodborne",777,{1});
        CHECK(relay.Listen(QHostAddress::LocalHost,0,temporary.filePath("fixture.db")));
        Client sender(relay.Port()); sender.Auth(1,"token1");
        Client receiver(relay.Port()); receiver.Auth(2,"token2");
        sender.Drain(); receiver.Drain();
        state.matching.roomsLock.lockForWrite();
        if (scenario <= 1) {
            for (int i = 0; i < 32; ++i) {
                auto packet=Life();qToLittleEndian<quint32>(i,packet.data()+10);sender.Send(packet);
            }
            CHECK(!sender.Closed(1) && receiver.DataCount(1)==0);
            if (scenario == 1) { sender.Send(Life()); CHECK(sender.Closed(50)); }
            state.matching.roomsLock.unlock();
            if (scenario == 0) {
                int received=0;
                for (const auto& p:receiver.Drain(320)) if (p.startsWith("THS2")) {
                    CHECK(Get<quint32>(p,38)==quint32(received));++received;
                }
                CHECK(received==32 && !sender.Closed(1));
            } else CHECK(receiver.DataCount(320)==0);
            std::cout << "PASS matching queue frame boundary: " << (scenario?"33 closes":"32 accepted ordered") << std::endl;
        } else if (scenario <= 3) {
            QByteArray packet(65476,'x');packet.replace(0,4,"SHAD");packet[4]=0x30;
            CHECK(RelayAuth::ValidInner(packet));
            for (int i=0;i<4;++i) sender.Send(packet);
            packet.resize(scenario==2?240:241);sender.Send(packet);
            CHECK(scenario==2?!sender.Closed(1):sender.Closed(50));
            state.matching.roomsLock.unlock();
            if(scenario==2) CHECK(!sender.Closed(320));
            std::cout << "PASS matching queue byte boundary: " << (scenario==2?"262144 accepted":"262145 closes") << std::endl;
        } else {
            sender.Send(Life()); // First matching-busy frame starts the deadline.
            QThread::msleep(1650); // Deliberately pause the event loop, never refresh the clock.
            state.matching.roomsLock.unlock();
            // The first resumed event must close the expired sender even though
            // matching is available now, and must never deliver the stale queue.
            CHECK(sender.Closed(320));
            CHECK(receiver.DataCount(40)==0);
            std::cout << "PASS matching queue deadline survives event-loop pause and lock recovery" << std::endl;
        }
        relay.Stop();
    }
    std::cout << "PASS: native relay THA4 authentication, business regression, and visibility routing; legacy authentication rejected\n";
}
