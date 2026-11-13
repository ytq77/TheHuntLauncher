// SPDX-License-Identifier: GPL-2.0-or-later
// Real Qt TCP relay + real Database, synthetic native membership fixtures.
// relay_auth_integration separately drives the full server via actual NP commands.
#include "authenticated_relay.h"
#include "client_session.h"
#include "config.h"
#include "database.h"
#include "bloodborne_summon_broker.h"
#include <QJsonDocument>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QFile>
#include <QSqlQuery>
#include <QTemporaryDir>
#include <QThread>
#include <QTcpSocket>
#include <QtEndian>
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
    QByteArray AuthPayload(quint64 id, const QByteArray& token, bool v3 = true) {
QByteArray p(v3 ? "THA4" : "THA2", 4); Put(p, id);
        constexpr char authDomain[]="TheHunt relay auth v4 bidirectional visibility";
        QByteArray domain(authDomain,sizeof(authDomain)); domain+=nonce; Put(domain,id);
        return p + (v3 ? RelayAuth::Mac(token,domain) : RelayAuth::Proof(token, nonce, id));
    }
    void Auth(quint64 id, const QByteArray& token, bool partial = false, bool v3 = true) {
        user = id; key = RelayAuth::SessionKey(token, nonce, user);
        Raw(AuthPayload(id, token, v3), partial);
        auto ack = Read();
        CHECK(ack.size() == 44 && ack.startsWith(v3 ? "THO4" : "THO2") && Get<quint64>(ack, 4) == id);
        CHECK(RelayAuth::Equal(ack.right(32), RelayAuth::Mac(key, ack.left(12))));
    }
    QByteArray Data(const QByteArray& inner, quint64 sequence = 0) {
        QByteArray p("THD2", 4); Put(p, sequence ? sequence : ++outgoing);
        p += inner; return p + RelayAuth::Mac(key, p);
    }
    void Send(const QByteArray& inner) { Raw(Data(inner)); }
    QVector<QByteArray> Drain(int ms = 40) {
        Pump(ms); Collect();
        QVector<QByteArray> data;
        for (const auto& p : frames) {
            CHECK(p.startsWith("THX2") || p.startsWith("THX4") || p.startsWith("THS2") || p.startsWith("THP2"));
            if(p.startsWith("THX4")) CHECK(p.size()==62+31*quint8(p[29]));
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
    auto login = [&](int id) { SharedState::ClientEntry c; c.npid = QString("player%1").arg(id); state.clients.insert(id, c); };
    auto room = [&](QString com, quint64 id, QVector<int> ids) {
        Room r; r.roomId = id; r.ownerMemberId = static_cast<quint16>(ids.front());
        for (int user : ids) { RoomMember x; x.memberId = user; x.userId = user; x.joinDate = 100;
            x.npid = QString("player%1").arg(user); r.members.insert(user, x); }
        state.matching.rooms.insert({com,id}, r);
    };
    for (int i = 1; i <= 6; ++i) login(i);
    room("bloodborne", 11, {1,2,3}); room("bloodborne", 22, {4});
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
    CHECK(RelayAuth::ValidPlacement(placement));
    CHECK(!RelayAuth::ValidPlacement("1,21000000,7fc00000,0,0,0,0"));
    Bloodborne::SummonBroker::Options options;options.seamlessCoop=true;
    options.relayPlacement=[&](const Bloodborne::SummonIdentity& h,const Bloodborne::SummonIdentity& g,const QByteArray& p) {
        state.relayPlacement(h.user,h.tokenHash,g.user,g.tokenHash,p);
    };
    options.relayCancelPlacement=[&](quint64 user){state.relayCancelPlacement(user);};
    Bloodborne::SummonBroker broker(options);
    auto identity=[&](int user){return Bloodborne::SummonIdentity{quint64(user),QCryptographicHash::hash(QByteArray("token")+QByteArray::number(user),QCryptographicHash::Sha256)};};
    const QByteArray rawSign=R"({"SessionId":"private-sign","UserId":7005,"CharaId":9223372036854775808,"AreaId":385875968,"AreaRegionId":1,"SummonData":"opaque","SummonDataVersion":3,"SummonType":0,"MatchingLevel":30})";
    auto sign=QJsonDocument::fromJson(rawSign).object();
    CHECK(broker.Advertise(sign,rawSign,100,identity(5)).authorized);
    CHECK(!broker.Advertise(sign,rawSign,101,identity(4)).authorized);
    auto stolen=sign;stolen["UserId"]=8004;
    CHECK(!broker.Advertise(stolen,QJsonDocument(stolen).toJson(),101,identity(4)).authorized);
    auto removal=QJsonDocument::fromJson(R"({"SessionId":"private-sign","UserId":7005,"Force":true})").object();
    CHECK(!broker.Consume(removal,102,identity(4)).authorized);
    const auto search=QJsonDocument::fromJson(R"({"UserId":9001,"AreaId":553648128,"GetMaxCount":20})").object();
    broker.Search(search,103,placement,identity(1));
    bool prejoin=false;
    for(auto p:unaffiliated.Drain(320)) if(p.startsWith("THP2")) {
        CHECK(Get<quint64>(p,12)>0 && Get<quint64>(p,20)==11);
        CHECK(Get<quint32>(p,28)==1 && Get<quint32>(p,32)==5 && Get<quint32>(p,36)>0 && Get<quint32>(p,36)<=10000);
        CHECK(p.mid(40,p.size()-72)==placement);prejoin=true;
    }
    CHECK(prejoin && guest.DataCount()==0 && other.DataCount()==0);
    broker.Search(search,104,placement,identity(2)); // A non-owner cannot mint a placement grant.
    for(auto p:unaffiliated.Drain(320)) CHECK(!p.startsWith("THP2") || Get<quint32>(p,36)==0);
    broker.Search(search,105,placement,identity(1));
    CHECK(broker.Consume(removal,106,identity(5)).consumed==1);
    bool revoked=false;
    for(auto p:unaffiliated.Drain(320)) if(p.startsWith("THP2")) {CHECK(Get<quint32>(p,36)==0 && p.size()==72);revoked=true;}
    CHECK(revoked);
    CHECK(broker.Advertise(sign,rawSign,107,identity(5)).authorized);
    broker.Search(search,108,placement,identity(1));
    for(auto p:unaffiliated.Drain(320)) if(p.startsWith("THP2")) CHECK(Get<quint32>(p,36)>0);
    state.matching.rooms[{"bloodborne",11}].ownerMemberId=2;
    revoked=false;
    for(auto p:unaffiliated.Drain(320)) if(p.startsWith("THP2")){CHECK(Get<quint32>(p,36)==0);revoked=true;}
    CHECK(revoked);
    state.matching.rooms[{"bloodborne",11}].ownerMemberId=1;
    broker.Consume(removal,109,identity(5));
    host.Drain();guest.Drain();guest2.Drain();other.Drain();
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
    for(auto spoof : {QByteArray("SHAD\x40",5), QByteArray("THX2",4), QByteArray("SHAD\x7f",5), Follow().chopped(1)}) {
        Client control(port); control.Auth(6,"token6"); control.Drain(); control.Raw(control.Data(spoof)); CHECK(control.Closed()); Pump(260);
    }
    Client size(port); QByteArray oversized; Put(oversized, quint32(65537)); size.socket.write(oversized); CHECK(size.Closed());
    Client slow(port); slow.socket.write(QByteArray(1,'x')); CHECK(slow.Closed(5300));
    Client rate(port); rate.Auth(6,"token6"); rate.Drain(); QByteArray flood;
    for(int i=0;i<257;++i) flood+=RelayAuth::Frame(rate.Data(Life()));
    rate.socket.write(flood); CHECK(rate.Closed(100));
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
    host.Drain();
    std::vector<std::unique_ptr<Client>> capacity;
    Pump(300);
    // Open to the fixed cap; the next connection receives no challenge and closes.
    for(int i=0;i<61;++i) {
        auto s=std::make_unique<Client>(port); capacity.push_back(std::move(s));
    }
    QTcpSocket overflow; overflow.connectToHost(QHostAddress::LocalHost,port); Pump();
    CHECK(overflow.state()==QAbstractSocket::UnconnectedState);
    CHECK(capacity.front()->Closed(5300));
    capacity.clear();
    relay.Stop(); CHECK(!state.relayAuthReady.load());
    // Unified THA4 visibility suite; original business/security suite above uses new authentication.
    state.matching.rooms.clear(); state.clients.clear();
    for(int i=1;i<=6;++i) { login(i); sql(QString("UPDATE account SET token='token%1',banned=0 WHERE user_id=%1").arg(i)); }
    room("bloodborne",101,{1,2,3}); room("bloodborne",202,{4});
    CHECK(relay.Listen(QHostAddress::LocalHost,0,temporary.filePath("fixture.db")));
    {
        Client h(relay.Port()), g(relay.Port()), old(relay.Port()), cross(relay.Port());
        h.Auth(1,"token1",true,true);g.Auth(2,"token2",false,true);old.Auth(3,"token3");cross.Auth(4,"token4",false,true);
        auto rosterCheck=[&](Client& client,int count) {
            bool seen=false;
            for(auto p:client.Drain(320)) if(p.startsWith("THX4")) {
                CHECK(p.size()==62+31*quint8(p[29]));
                if(Get<quint64>(p,12)!=101) continue;
                const int advertised=quint8(p[29]);
                CHECK(quint8(p[28])==advertised-1);
                for(int i=0;i<advertised;++i) {
                    int at=30+31*i; auto id=Get<quint32>(p,at);
                    CHECK(id>=1 && id<=3 && Get<quint16>(p,at+4)==id && Get<quint64>(p,at+6)>0);
                    CHECK(quint8(p[at+14])==1);
                    CHECK(p.mid(at+15,16)==(QByteArray("player")+QByteArray::number(id)).leftJustified(16,0));
                }
                if(advertised==count) seen=true;
            }
            CHECK(seen);
        };
        rosterCheck(h,3);rosterCheck(g,3);old.Drain();cross.Drain();
        auto vis=[](int kind,quint32 target) {
            QByteArray p(56,0);p.replace(0,4,"SHAD");p[4]=0x28;p[5]=1;p[6]=kind;
            qToLittleEndian(target,p.data()+8);
            if(kind!=3) {
                qToLittleEndian(quint64(10),p.data()+12);qToLittleEndian(quint64(20),p.data()+20);
                qToLittleEndian(quint32(21),p.data()+52);
                if(kind!=0) { p[7]=1;for(int at:{28,36,44})qToLittleEndian(quint64(30),p.data()+at); }
            }
            return p;
        };
        for(int kind=0;kind<4;++kind) {
            auto v=vis(kind,kind==1?1:2);CHECK(RelayAuth::ValidInner(v));
            CHECK(RelayAuth::Allowed(v,kind!=1,kind==1));
            CHECK(!RelayAuth::Allowed(v,kind==1,kind!=1));
            CHECK(!RelayAuth::ValidInner(v.chopped(1)));
            auto invalid=v;invalid[5]=2;CHECK(!RelayAuth::ValidInner(invalid));
            invalid=v;invalid[6]=4;CHECK(!RelayAuth::ValidInner(invalid));
            invalid=v;qToLittleEndian(quint32(0),invalid.data()+8);CHECK(!RelayAuth::ValidInner(invalid));
        }
        auto invalid=vis(0,2);invalid[28]=1;CHECK(!RelayAuth::ValidInner(invalid));
        invalid=vis(2,2);invalid[7]=0;CHECK(!RelayAuth::ValidInner(invalid));
        invalid=vis(3,2);invalid[12]=1;CHECK(!RelayAuth::ValidInner(invalid));
        h.Send(vis(0,2));CHECK(g.DataCount()==1 && old.DataCount()==0 && cross.DataCount()==0);
        g.Send(vis(1,1));CHECK(h.DataCount()==1 && old.DataCount()==0);
        h.Send(vis(2,2));h.Send(vis(3,2));CHECK(g.DataCount()==2);
        h.Send(vis(0,3));h.Send(vis(0,4));CHECK(old.DataCount()==1 && cross.DataCount()==0 && g.DataCount()==0);
        g.Send(vis(0,1));h.Send(vis(1,2));CHECK(h.DataCount()==0 && g.DataCount()==0);
        h.Send(Follow());CHECK(g.DataCount()==1 && old.DataCount()==1);
        auto reverse=[&](int kind,quint32 target) {
            auto p=vis(kind==4?0:1,target);p[5]=2;p[6]=kind;
            Put(p,quint64(101));Put(p,quint64(100));Put(p,quint64(100));Put(p,quint64(1));Put(p,quint64(2));return p;
        };
        auto query=reverse(4,1), status=reverse(5,2);
        CHECK(RelayAuth::ValidInner(query) && RelayAuth::ValidInner(status));
        g.Send(query);CHECK(h.DataCount()==1 && old.DataCount()==0 && cross.DataCount()==0);
        h.Send(status);CHECK(g.DataCount()==1 && old.DataCount()==0 && cross.DataCount()==0);
        for(int at:{56,64,72}) {auto bad=query;qToLittleEndian<quint64>(987,bad.data()+at);g.Send(bad);CHECK(h.DataCount()==0);}
        CHECK(!RelayAuth::Allowed(query,true,false) && !RelayAuth::Allowed(status,false,true));
        auto bad=query;bad[5]=1;CHECK(!RelayAuth::ValidInner(bad));CHECK(!RelayAuth::ValidInner(query.chopped(1)));
        for(const char* legacy:{"THA2","THA3"}) {
            Client rejected(relay.Port());auto proof=rejected.AuthPayload(5,"token5");proof.replace(0,4,legacy);rejected.Raw(proof);CHECK(rejected.Closed());
        }
        state.matching.rooms[{"bloodborne",101}].members[2].joinDate=999;
        h.Send(Follow());bool clear=false,fresh=false;
        for(auto p:g.Drain()) if(p.startsWith("THX4")) {
            if(!Get<quint64>(p,12)) {CHECK(p.size()==62 && !p[29]);clear=true;} else fresh=true;
        }
        CHECK(clear && fresh);old.Drain();h.Drain();
        state.matching.rooms[{"bloodborne",101}].members.remove(2);
        h.Send(vis(0,2));CHECK(g.DataCount()==0);rosterCheck(h,2);
        old.socket.abort();Pump(320);rosterCheck(h,1);
        // Proof domains cannot be relabelled in either direction; challenge replay fails.
        Client wrong3(relay.Port());auto proof=wrong3.AuthPayload(5,"token5",false);proof.replace(0,4,"THA4");wrong3.Raw(proof);CHECK(wrong3.Closed());
        Client wrong2(relay.Port());proof=wrong2.AuthPayload(5,"token5",true);proof.replace(0,4,"THA2");wrong2.Raw(proof);CHECK(wrong2.Closed());
        Client prior(relay.Port());proof=prior.AuthPayload(5,"token5",true);prior.socket.abort();Pump();
        Client again(relay.Port());again.Raw(proof);CHECK(again.Closed());
        Client replay3(relay.Port());replay3.Auth(5,"token5",false,true);replay3.Drain();auto packet=replay3.Data(Life());replay3.Raw(packet);replay3.Raw(packet);CHECK(replay3.Closed());
        // Invalid native identity must never produce a malformed signed roster.
        state.clients[1].npid="invalid name";state.matching.rooms[{"bloodborne",101}].members[1].npid="invalid name";
        CHECK(h.Closed(320));
    }
    relay.Stop();CHECK(!state.relayAuthReady.load());
    QFile cfg(temporary.filePath("disabled.cfg")); CHECK(cfg.open(QIODevice::WriteOnly));
    cfg.write("[General]\nHost=127.0.0.1\nBloodborneSeamlessCoop=false\n"); cfg.close();
    ConfigManager config; config.Load(cfg.fileName()); CHECK(relay.Start(&config,temporary.filePath("fixture.db")));
    CHECK(relay.Port()==0 && !state.relayAuthReady.load());
    CHECK(cfg.open(QIODevice::WriteOnly|QIODevice::Truncate));
    cfg.write("[General]\nHost=127.0.0.1\nBloodborneSeamlessCoop=true\n");cfg.close();config.Load(cfg.fileName());
    qputenv("THEHUNT_RELAY_PORT","invalid");CHECK(!relay.Start(&config,temporary.filePath("fixture.db")));
    CHECK(!state.relayAuthReady.load());qunsetenv("THEHUNT_RELAY_PORT");
    std::cout << "PASS: native THA4 relay authentication, routing, revocation, bounds, partial TCP and bidirectional visibility frames\n";
}
