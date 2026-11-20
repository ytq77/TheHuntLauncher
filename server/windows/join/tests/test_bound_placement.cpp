// SPDX-License-Identifier: GPL-2.0-or-later
// Real Matching2 commands, disposable accounts and an authenticated loopback relay.
#include "authenticated_relay.h"
#include "client_session.h"
#include "proto_utils.h"
#include "summon_claim_origin.hpp"
#include "webapi_routes_bloodborne.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QHttpServer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QSqlQuery>
#include <QTcpServer>
#include <QTemporaryDir>
#include <QThread>
#include <QtEndian>
#include <iostream>
#include <stdexcept>

using namespace Bloodborne;

static void require(bool ok, const char* reason) {
    if (!ok) throw std::runtime_error(reason);
}

template<class T>
static T number(const QByteArray& bytes, qsizetype offset) {
    require(offset >= 0 && offset + qsizetype(sizeof(T)) <= bytes.size(), "short frame");
    return qFromLittleEndian<T>(bytes.constData() + offset);
}

template<class Message>
static StreamExtractor arguments(const Message& message) {
    QByteArray bytes;
    appendProto(bytes,message);
    return StreamExtractor(bytes);
}

class Player {
public:
    QTcpSocket socket;
    std::unique_ptr<ClientSession> session;
    SummonIdentity identity;
    QByteArray token;
    const QByteArray hostRun = QUuid::createUuid().toRfc4122().toHex();

    Player(const QString& name, const QString& path, SharedState& state, Database& db) {
        require(!db.CreateAccount(name,"local-password","",name+"@local.invalid"), "create account");
        auto user = db.CheckUser(name,"local-password","",false);
        require(user.has_value(),"fixture account");
        token = user->token.toUtf8();
        identity = {quint64(user->userId),QCryptographicHash::hash(token,QCryptographicHash::Sha256)};
        QTcpServer acceptor;
        require(acceptor.listen(QHostAddress::LocalHost,0),"NP loopback");
        socket.connectToHost(QHostAddress::LocalHost,acceptor.serverPort());
        require(socket.waitForConnected(2000) && acceptor.waitForNewConnection(2000),"NP connection");
        session = std::make_unique<ClientSession>(acceptor.nextPendingConnection(),&state,path,false);
        shadnet::LoginRequest login;
        login.set_npid(name.toStdString());
        login.set_password("local-password");
        login.set_title_id("CUSA03173");
        auto input = arguments(login);
        QByteArray reply;
        require(session->CmdLogin(input,reply) == ErrorType::NoError,"native login");
        startContext();
    }

    void startContext() {
        shadnet::ContextStartRequest start;
        start.set_ctx_id(1);
        auto args = arguments(start);
        require(session->CmdContextStart(args) == ErrorType::NoError,"context start");
    }

    BoundClaimOrigin create(const QString& registration, quint64 attempt) {
        shadnet::CreateRoomRequest create;
        create.set_max_slots(5);
        auto args = arguments(create);
        QByteArray bytes;
        require(session->CmdCreateRoom(args,bytes) == ErrorType::NoError,"create room");
        StreamExtractor payload(bytes);
        shadnet::CreateRoomReply room;
        require(decodeProto(room,payload),"room reply");
        for (const auto& member : room.details().members()) {
            if (member.is_owner() && member.member_id() == room.member_id())
                return {registration,room.room_id(),quint16(member.member_id()),member.join_date(),attempt,hostRun};
        }
        throw std::runtime_error("native owner missing");
    }

    ErrorType join(quint64 id) {
        shadnet::JoinRoomRequest request;
        request.set_room_id(id);
        auto args = arguments(request);
        QByteArray reply;
        return session->CmdJoinRoom(args,reply);
    }

    void leave(quint64 id) {
        shadnet::LeaveRoomRequest request;
        request.set_room_id(id);
        auto args = arguments(request);
        QByteArray reply;
        require(session->CmdLeaveRoom(args,reply) == ErrorType::NoError,"native leave");
    }
};

class PlacementReader {
    QTcpSocket socket;
    QByteArray input, key;
    quint64 sequence = 0;

    QByteArray read() {
        QElapsedTimer deadline;
        deadline.start();
        do {
            QCoreApplication::processEvents();
            input += socket.readAll();
            if (input.size() >= 4) {
                const auto length = number<quint32>(input,0);
                require(length > 0 && length <= RelayAuth::MaxPayload,"framing");
                if (input.size() >= 4 + length) {
                    auto frame = input.mid(4,length);
                    input.remove(0,4 + length);
                    return frame;
                }
            }
            QThread::msleep(1);
        } while (deadline.elapsed() < 2000);
        throw std::runtime_error("relay frame timeout");
    }

public:
    quint64 nativeRoom = 0; // Last signed THX4 membership on this connection.

    PlacementReader(quint16 port, const Player& player) {
        socket.connectToHost(QHostAddress::LocalHost,port);
        require(socket.waitForConnected(2000),"relay connection");
        const auto challenge = read();
        require(challenge.startsWith("THC2") && challenge.size()==36,"relay challenge");
        const auto user = player.identity.user;
        const quint64 le = qToLittleEndian(user);
        const QByteArray id(reinterpret_cast<const char*>(&le),sizeof(le));
        const char domain[]="TheHunt relay auth v4 bidirectional visibility";
        const auto nonce = challenge.mid(4);
        const auto proof = RelayAuth::Mac(player.token,QByteArray(domain,sizeof(domain))+nonce+id);
        socket.write(RelayAuth::Frame("THA4"+id+proof));
        key = RelayAuth::SessionKey(player.token,nonce,user);
        const auto ack = read();
        require(ack.startsWith("THO4") && ack.mid(4,8)==id &&
                ack.right(32)==RelayAuth::Mac(key,ack.chopped(32)),"authenticated relay");
    }

    QList<QByteArray> grants(int timeoutMs = 35, qsizetype expected = 0) {
        QElapsedTimer clock;
        clock.start();
        QList<QByteArray> result;
        do {
            QCoreApplication::processEvents();
            while (!input.isEmpty() || socket.bytesAvailable()) {
                auto frame = read();
                require(number<quint64>(frame,4)==++sequence &&
                        frame.right(32)==RelayAuth::Mac(key,frame.chopped(32)),"signed sequence");
                if (frame.startsWith("THX4")) nativeRoom = number<quint64>(frame,12);
                if (frame.startsWith("THP2")) result.push_back(frame);
            }
            if (expected && result.size()>=expected) break;
            QThread::msleep(1);
        } while (clock.elapsed()<timeoutMs);
        return result;
    }
};

static void checkGrant(const QByteArray& frame, const BoundPlacementRequest& request) {
    require(number<quint64>(frame,20)==request.origin.room &&
            number<quint32>(frame,28)==request.host.user && number<quint32>(frame,32)==request.guest.user &&
            number<quint32>(frame,36)>0 && number<quint32>(frame,36)<=10000 &&
            frame.mid(40,frame.size()-72)==request.placement,"targeted COMP THP2 payload");
}

static void httpReopen(Database& db, SharedState& state, Player& host, Player& guest, PlacementReader& reader) {
    QHttpServer http;
    WebApiRoutes::RegisterBloodborneRoutes(http,true,db,state);
    auto* listener = new QTcpServer(&http);
    require(listener->listen(QHostAddress::LocalHost,0) && http.bind(listener),"HTTP listener");
    QNetworkAccessManager client;
    client.setProxy(QNetworkProxy::NoProxy);
    auto post = [&](const QString& operation, const Player& player, const QByteArray& body,
                    const BoundPlacementRequest* claim = nullptr, QByteArray* receivedPlacement = nullptr) {
        QNetworkRequest request{QUrl(QString("http://127.0.0.1:%1/summon_messenger/%2")
                                     .arg(listener->serverPort()).arg(operation))};
        request.setTransferTimeout(4000);
        request.setHeader(QNetworkRequest::ContentTypeHeader,"application/json");
        request.setRawHeader("Authorization","Bearer "+player.token);
        if (operation=="get") request.setRawHeader(TheHunt::Summon::discovery_header,"1");
        if (claim) {
            const auto& origin = claim->origin;
            const auto id = origin.registrationId.toLatin1();
            const auto wire = TheHunt::Summon::EncodeClaimOrigin({std::string_view(id),origin.room,origin.ownerMember,
                                      origin.ownerJoined,origin.attempt,std::string_view(origin.hostRun)});
            require(wire.has_value(),"encode native room origin");
            request.setRawHeader(TheHunt::Summon::claim_origin_header,QByteArray::fromStdString(*wire));
            request.setRawHeader("X-ShadPS4-Bloodborne-Host-Placement",claim->placement);
        }
        auto* reply = client.post(request,body);
        QEventLoop loop;
        QObject::connect(reply,&QNetworkReply::finished,&loop,&QEventLoop::quit);
        if (!reply->isFinished()) loop.exec();
        require(reply->error()==QNetworkReply::NoError &&
                reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt()==200,"local HTTP response");
        if (receivedPlacement) *receivedPlacement = reply->rawHeader("X-ShadPS4-Bloodborne-Host-Placement");
        const auto json = QJsonDocument::fromJson(reply->readAll());
        reply->deleteLater();
        require(json.isObject(),"summon HTTP JSON");
        return json.object();
    };
    const QByteArray sign = R"({"SessionId":"S1","UserId":72,"CharaId":23,"AreaId":385875968,"AreaRegionId":230010,"SummonData":"guest","SummonType":0,"MatchingLevel":46,"SummonDataVersion":3})";
    const QByteArray search = R"({"SessionId":"host","UserId":71,"AreaId":402718720,"AreaRegionId":241010,"MatchingLevel":46,"SummonDataVersion":3})";
    const QByteArray claim = R"({"SessionId":"host","UserId":71,"TargetUserId":72,"TargetCharaId":23,"HostData":"native"})";
    post("create",guest,sign);
    const auto rows = post("get",host,search).value("SummonDataList").toArray();
    require(rows.size()==1,"one waiting guest before room creation");
    const auto id = rows.first().toObject().value("TheHuntRegistrationId").toString();
    require(!id.isEmpty(),"discovered registration");

    const BoundPlacementRequest first{BoundClaimStep::First,host.create(id,1),{}, {},host.identity,guest.identity,
                                     "1,18010000,00000000,00000000,00000000,00000000,241010"};
    require(first.origin.room==35,"native room 35");
    post("request",host,claim,&first);
    auto frames = reader.grants(2000,1);
    require(frames.size()==1,"one HTTP room 35 grant");
    checkGrant(frames.first(),first);
    const auto firstGeneration = number<quint64>(frames.first(),12);

    host.leave(first.origin.room);
    auto reopened = first;
    reopened.origin = host.create(id,2);
    reopened.placement = "1,18010000,4000000a,00000000,00000000,00000000,241020";
    require(reopened.origin.room==36,"native room 36");
    post("request",host,claim,&reopened);
    frames = reader.grants(2000,2);
    require(frames.size()==2 && number<quint32>(frames.first(),36)==0 &&
            number<quint64>(frames.first(),12)==firstGeneration && number<quint64>(frames.first(),20)==35,
            "revoke only the preceding room 35 generation");
    checkGrant(frames.last(),reopened);
    require(number<quint64>(frames.last(),12)>firstGeneration,"fresh signed room 36 generation");
    post("request",host,claim,&first);
    require(reader.grants(550).isEmpty(),"late HTTP room 35 touched room 36"); // spans two 250 ms relay ticks
    post("request",host,claim,&reopened);
    frames = reader.grants(550);
    for (const auto& frame : frames) {
        std::cerr<<"identical retry THP2 room="<<number<quint64>(frame,20)
                 <<" generation="<<number<quint64>(frame,12)<<" ttl_ms="<<number<quint32>(frame,36)<<'\n';
    }
    require(frames.isEmpty(),"identical HTTP retry revoked or reissued the live room 36 invitation");
    auto equivalent = reopened;
    equivalent.placement = "1,18010000,4000000A,0,0000,00,000241020";
    post("request",host,claim,&equivalent);
    require(reader.grants(550).isEmpty(),"equivalent HTTP pose replaced the room 36 invitation");
    post("request",host,claim,&reopened);
    QByteArray hint;
    require(!post("create",guest,sign,nullptr,&hint).contains("HostData") && hint==reopened.placement,
            "cross-map guest waits with room 36 placement");
    require(guest.join(36)==ErrorType::NoError,"native admission to room 36");
    // The broker is still Claimed. Retry before the destination-map poll that changes it to Delivered.
    post("request",host,claim,&reopened);
    require(reader.grants(550).isEmpty() && reader.nativeRoom==36,
            "native room 36 membership must preserve the previously sent invitation");
    post("request",host,claim,&reopened);
    require(reader.grants(550).isEmpty(),"retry after the matching THX4 changed the guest cache");
    auto arrived = QJsonDocument::fromJson(sign).object();
    arrived["AreaId"] = 0x18010000;
    arrived["AreaRegionId"] = 241020;
    const auto delivered = post("create",guest,QJsonDocument(arrived).toJson(QJsonDocument::Compact),nullptr,&hint);
    require(delivered.value("HostData")=="native" && hint==reopened.placement,
            "joined guest reporting the destination map still receives the original room 36 placement and claim");
    post("delete",guest,R"({"SessionId":"S1","UserId":72,"Force":true})");
    require(reader.grants().isEmpty(),"joined room 36 was offered again or revoked after consumption");
    guest.leave(reopened.origin.room);
    host.leave(reopened.origin.room);
    std::cout<<"PASS HTTP -> Matching2 -> authenticated THP2: 35 replaced by 36; late 35, exact/equivalent 36 retries and joined 36 emitted no placement frames\n";
}

int main(int argc, char** argv) {
    QCoreApplication app(argc,argv);
    try {
        QTemporaryDir directory;
        require(directory.isValid(),"temporary directory");
        const auto path = directory.filePath("bound.sqlite");
        Database db("bound-fixture");
        require(db.Open(path),"open fixture database");
        QSqlQuery writer(db.Conn());
        require(writer.exec("PRAGMA busy_timeout=0"),"no waiting");
        ConfigManager config;
        SharedState state;
        state.config = &config;
        state.matching.titleGroups.insert("CUSA03173","bloodborne");
        state.matching.nextRoomId.store(35);
        Player host("boundhost",path,state,db), guest("boundguest",path,state,db), other("boundother",path,state,db);
        AuthenticatedRelay relay(&state);
        require(relay.Listen(QHostAddress::LocalHost,0,path),"local relay");
        PlacementReader h(relay.Port(),host), g(relay.Port(),guest), observer(relay.Port(),other);
        httpReopen(db,state,host,guest,g);
        require(h.grants().isEmpty() && observer.grants().isEmpty(),"HTTP grant reached a different account");
        const QString registration(48,'a');
        auto original = host.create(registration,1);
        BoundPlacementRequest first{BoundClaimStep::First,original,{}, {},host.identity,guest.identity,
                                    "1,18010000,3f800000,00000000,00000000,00000000,241010"};
        int commits = 0;
        auto submit = [&](const BoundPlacementRequest& request, BoundPlacementStatus expected) {
            int calls = 0;
            QByteArray committed;
            const auto result = state.relayBoundClaimPlacement(request,[&](const QByteArray& scope) {
                ++calls;
                ++commits;
                committed = scope;
                require(!state.matching.roomsLock.tryLockForWrite(),"rooms lock missing at commit");
                require(!state.clientsLock.tryLockForWrite(),"clients lock missing at commit");
                require(!writer.exec("BEGIN IMMEDIATE"),"token generation unprotected at commit");
            });
            require(result.status==expected,"placement result");
            const bool granted = expected==BoundPlacementStatus::Granted;
            require(calls==(granted ? 1 : 0) && (granted ? !committed.isEmpty() : committed.isEmpty()) &&
                    result.scope==committed,"commit and returned receipt");
            return committed;
        };
        using Status = BoundPlacementStatus;
        auto invalid = first;
        invalid.origin.ownerJoined++;
        submit(invalid,Status::Denied);
        invalid = first;
        invalid.origin.ownerMember++;
        submit(invalid,Status::Denied);
        invalid = first;
        invalid.origin.hostRun = QByteArray(32,'A');
        submit(invalid,Status::Denied);
        invalid = first;
        invalid.host = other.identity;
        submit(invalid,Status::Denied);
        invalid = first;
        invalid.guest.tokenHash.fill('x');
        submit(invalid,Status::Denied);
        invalid = first;
        invalid.placement = "bad";
        submit(invalid,Status::Denied);
        require(writer.exec("BEGIN IMMEDIATE"),"competing credential writer");
        submit(first,Status::Busy);
        require(writer.exec("ROLLBACK"),"release credential writer");
        {
            QWriteLocker held(&state.matching.roomsLock);
            submit(first,Status::Busy);
        }
        {
            QWriteLocker held(&state.clientsLock);
            submit(first,Status::Busy);
        }
        const auto setBanned = [&](quint64 user, bool banned) {
            writer.prepare("UPDATE account SET banned=? WHERE user_id=?");
            writer.addBindValue(banned ? 1 : 0);
            writer.addBindValue(qint64(user));
            require(writer.exec(),"fixture account change");
        };
        for (const auto user : {host.identity.user,guest.identity.user}) {
            setBanned(user,true);
            submit(first,Status::Denied);
            setBanned(user,false);
        }
        auto& firstRoom=state.matching.rooms[{"bloodborne",original.room}];
        firstRoom.blockedAccountIds.append(guest.identity.user);
        submit(first,Status::Denied);
        firstRoom.blockedAccountIds.clear();
        require(g.grants().isEmpty(),"no denied invitation");
        QElapsedTimer firstLease;
        firstLease.start();
        const auto originalScope = submit(first,Status::Granted);
        auto retry = first;
        retry.step=BoundClaimStep::Retry;
        retry.previous=original;
        retry.previousScope=originalScope;
        // No event loop between these calls. The original THP2 is still in the output FIFO.
        require(submit(retry,Status::Granted)==originalScope,"retry before first socket write");
        auto frames = g.grants(550);
        require(frames.size()==1,"pending first grant must arrive exactly once");
        checkGrant(frames.first(),first);
        const auto generation = number<quint64>(frames.first(),12);
        const auto ttl = number<quint32>(frames.first(),36);
        require(g.grants(4000).isEmpty(),"original grant retired early");
        require(submit(retry,Status::Granted)==originalScope,"reuse unexpired grant");
        // Allow for the 250 ms expiry tick and scheduling, measured from the first grant.
        const auto untilExpiry = ttl + 1000 - firstLease.elapsed();
        require(untilExpiry>0,"fixture missed the original deadline");
        frames = g.grants(int(untilExpiry),1);
        require(frames.size()==1 && number<quint32>(frames.first(),36)==0 &&
                number<quint64>(frames.first(),12)==generation && firstLease.elapsed()+1>=ttl,
                "retry must preserve original generation and deadline");
        require(submit(retry,Status::Granted)==originalScope,"same-room renewal");
        frames = g.grants(2000,1);
        require(frames.size()==1 && number<quint64>(frames.first(),12)>generation,"new generation");
        checkGrant(frames.first(),retry);
        const auto renewed = number<quint64>(frames.first(),12);
        auto moved = retry;
        moved.placement="1,18010000,3f800001,00000000,00000000,00000000,241010";
        require(submit(moved,Status::Granted)==originalScope,"same-room move under the existing scope");
        frames = g.grants(2000,2);
        require(frames.size()==2 && number<quint32>(frames.first(),36)==0 &&
                number<quint64>(frames.first(),12)==renewed && number<quint64>(frames.last(),12)>renewed,
                "one position bit changed: replace the grant");
        checkGrant(frames.last(),moved);

        // Another native member keeps the OLD room alive after its owner leaves.
        require(other.join(original.room)==ErrorType::NoError,"old room keeper");
        host.leave(original.room);
        const auto reopened = host.create(registration,2);
        require(reopened.room==original.room+1,"native room sequence");
        auto rebind = retry;
        rebind.step=BoundClaimStep::Rebind;
        rebind.origin=reopened;
        rebind.placement="1,18010000,40000000,00000000,00000000,00000000,241020";
        submit(rebind,Status::Denied);
        // A missing directory row without a real destruction is insufficient.
        const auto oldKey=qMakePair(QStringLiteral("bloodborne"),uint64_t(original.room));
        const auto oldRoom=state.matching.rooms.take(oldKey);
        submit(rebind,Status::Denied);
        state.matching.rooms.insert(oldKey,oldRoom);
        other.leave(original.room);
        submit(retry,Status::Denied);
        invalid=rebind;
        invalid.previousScope="unrelated";
        submit(invalid,Status::Denied);
        invalid=rebind;
        invalid.previous->hostRun=other.hostRun;
        submit(invalid,Status::Denied);
        invalid=rebind;
        invalid.origin.hostRun=other.hostRun;
        submit(invalid,Status::Denied);
        const auto reopenedScope=submit(rebind,Status::Granted);
        require(reopenedScope!=originalScope,"fresh room receipt");
        frames=g.grants();
        require(!frames.isEmpty(),"reopened grant");
        checkGrant(frames.last(),rebind);

        state.relayCancelBoundPlacement(registration,originalScope);
        state.relayCancelPlacement(guest.identity.user);
        submit(retry,Status::Denied);
        require(g.grants().isEmpty(),"old cancel/retry touched new grant");

        auto retryReopened=rebind;
        retryReopened.step=BoundClaimStep::Retry;
        retryReopened.previous=reopened;
        retryReopened.previousScope=reopenedScope;
        require(guest.join(reopened.room)==ErrorType::NoError,"real guest admission");
        submit(retryReopened,Status::Denied);
        guest.leave(reopened.room); // no Qt event loop or relay tick since JoinRoom
        host.leave(reopened.room);
        const auto nextRoom=host.create(registration,3);
        auto afterJoin=retryReopened;
        afterJoin.step=BoundClaimStep::Rebind;
        afterJoin.origin=nextRoom;
        submit(afterJoin,Status::Denied);
        state.relayCancelBoundPlacement(registration,reopenedScope);
        submit(afterJoin,Status::Expired);
        for (const auto& frame : g.grants())
            require(number<quint32>(frame,36)==0,"accepted guest was summoned again");
        auto fresh=first;
        fresh.origin=nextRoom;
        fresh.origin.registrationId=QString(48,'b');
        fresh.origin.attempt=4;
        auto nextScope=submit(fresh,Status::Granted);
        frames=g.grants();
        require(frames.size()==1,"new registration");
        checkGrant(frames.first(),fresh);
        guest.startContext(); // same numeric context, new actual context lifetime
        auto contextRetry=fresh;
        contextRetry.step=BoundClaimStep::Retry;
        contextRetry.previous=fresh.origin;
        contextRetry.previousScope=nextScope;
        submit(contextRetry,Status::Denied);
        require(h.grants().isEmpty() && observer.grants().isEmpty(),"unexpected recipient");
        require(commits==7,"unexpected commits");
        state.relayCancelBoundPlacement(fresh.origin.registrationId,nextScope);
        g.grants();

        SummonBroker::Options options;
        options.seamlessCoop=true;
        options.boundPlacement=state.relayBoundClaimPlacement;
        options.cancelBoundPlacement=state.relayCancelBoundPlacement;
        SummonBroker broker(options);
        const QByteArray sign=R"({"SessionId":"waiting","UserId":72,"CharaId":23,"AreaId":385875968,"AreaRegionId":230010,"SummonData":"guest","SummonType":0,"MatchingLevel":46,"SummonDataVersion":3})";
        const QByteArray claim=R"({"SessionId":"host","UserId":71,"TargetUserId":72,"TargetCharaId":23,"HostData":"native"})";
        const auto signBody=QJsonDocument::fromJson(sign).object();
        require(broker.Advertise(signBody,sign,100,guest.identity).authorized,"broker signup");
        const auto rows=broker.Search({{"UserId",71},{"SessionId","host"}},101,{},host.identity,
                                      SummonBroker::SearchMode::RegistrationDiscovery);
        require(rows.size()==1,"broker discovery");
        auto real=fresh;
        real.origin.registrationId=QJsonDocument::fromJson(rows.first()).object()["TheHuntRegistrationId"].toString();
        real.origin.attempt=5;
        const auto admitted=broker.ClaimBound(real.origin,claim,102,real.placement,host.identity);
        require(admitted.status==SummonBroker::ClaimStatus::Claimed,"real broker and relay commit");
        frames=g.grants();
        require(frames.size()==1,"real claim frame");
        checkGrant(frames.first(),real);
        require(guest.join(nextRoom.room)==ErrorType::NoError,"real broker guest joined");
        guest.leave(nextRoom.room);
        host.leave(nextRoom.room);
        auto next=host.create(real.origin.registrationId,6);
        const auto refused=broker.ClaimBound(next,claim,103,real.placement,host.identity);
        require(refused.failureReason==SummonBroker::ClaimFailureReason::BoundPlacementDenied,
                "a retained registration cannot rebind after actual guest admission");
        for (const auto& frame : g.grants())
            require(number<quint32>(frame,36)==0,"no late grant");
        std::cout<<"PASS bound placement: native join history, atomic commit, signed THP2 and scope isolation\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr<<error.what()<<'\n';
        return 1;
    }
}
