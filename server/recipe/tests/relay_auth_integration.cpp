// SPDX-License-Identifier: GPL-2.0-or-later
// Opt-in CLI for a DISPOSABLE full native server. JSON credentials arrive only
// through stdin; no secret is printed. Accounts must already exist in the fixture.
// {"host":"127.0.0.1","np_port":31413,"relay_port":20444,
//  "accounts":[{"npid":"relayhost","password":"..."}, ...three accounts...]}
#include "client.h"
#include "shadnet.pb.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMessageAuthenticationCode>
#include <QThread>
#include <QTcpSocket>
#include <QtEndian>
#include <functional>
#include <iostream>
#include <memory>
#include <stdexcept>
using shadnetclient::ShadNetClient;
static void Need(bool good, const char* message) { if (!good) throw std::runtime_error(message); }
template<class T> static void Put(QByteArray& p,T n) { char b[sizeof(T)]; qToLittleEndian(n,b); p.append(b,sizeof(b)); }
template<class T> static T Get(const QByteArray& p,int n) { return qFromLittleEndian<T>(p.constData()+n); }
static void Pump(int ms=10) { QElapsedTimer t; t.start(); do { QCoreApplication::processEvents(); QThread::msleep(1); } while(t.elapsed()<ms); }
static void Until(const std::function<void()>& update,const std::function<bool()>& done) {
    QElapsedTimer t; t.start(); while(!done() && t.elapsed()<5000) { update(); Pump(); } Need(done(),"native command timeout");
}
static QByteArray Mac(const QByteArray& key,const QByteArray& message) {
    return QMessageAuthenticationCode::hash(message,key,QCryptographicHash::Sha256);
}
static QByteArray Framed(const QByteArray& payload) { QByteArray p; Put(p,quint32(payload.size())); return p+payload; }
static std::vector<uint8_t> Request(quint16 cmd,quint64 id,const QByteArray& body) {
    QByteArray p(1,0); Put(p,cmd); Put(p,quint32(15+body.size())); Put(p,id); p+=body;
    return std::vector<uint8_t>(p.begin(),p.end());
}
static QByteArray Blob(const google::protobuf::MessageLite& message) {
    auto text=message.SerializeAsString(); QByteArray p; Put(p,quint32(text.size())); return p+QByteArray::fromStdString(text);
}
struct Credential { std::string npid,password; quint64 user=0; QByteArray token; };
static void Companion(const std::string& host,quint16 port,Credential& c) {
    shadnetclient::ShadNetConnection connection;
    Need(connection.connect(host.c_str(),port),"companion connection failed");
    bool done=false;
    connection.onPacket=[&](const shadnetclient::Packet& reply) {
        if(reply.type!=shadnetclient::PacketType::Reply || reply.command!=0) return;
        Need(reply.payload.size()>=5 && reply.payload[0]==0,"companion login failed");
        QByteArray p(reinterpret_cast<const char*>(reply.payload.data()),reply.payload.size());
        Need(Get<quint32>(p,1)==quint32(p.size()-5),"login blob length");
        shadnet::LoginReply login; Need(login.ParseFromArray(p.constData()+5,p.size()-5),"login protobuf");
        c.user=login.user_id(); Need(c.user>0 && c.user<=0xffffffffULL,"unsupported account ID"); done=true;
    };
    shadnet::LoginRequest login; login.set_npid(c.npid); login.set_password(c.password);
    Need(connection.send(Request(0,1,Blob(login))),"companion login send");
    Until([&]{connection.update();},[&]{return done;});
    done=false;
    connection.onPacket=[&](const shadnetclient::Packet& reply) {
        if(reply.type!=shadnetclient::PacketType::Reply || reply.command!=39) return;
        Need(reply.payload.size()>=5 && reply.payload[0]==0,"GetToken rejected");
        QByteArray p(reinterpret_cast<const char*>(reply.payload.data()),reply.payload.size());
        Need(Get<quint32>(p,1)==quint32(p.size()-5),"token blob length");
        shadnet::GetTokenReply token; Need(token.ParseFromArray(p.constData()+5,p.size()-5),"token protobuf");
        Need(token.user_id()==c.user && !token.token().empty(),"GetToken account mismatch");
        c.token=QByteArray::fromStdString(token.token()); done=true;
    };
    Need(connection.send(Request(39,2,{})),"GetToken send");
    Until([&]{connection.update();},[&]{return done;}); connection.disconnect(); Pump(100);
}
struct Relay {
    QTcpSocket socket; QByteArray input,key; quint64 own=0,sendSeq=0,recvSeq=0,room=0; quint32 owner=0; int peers=0;
    int received=0; quint64 lastRoom=0; quint32 lastSender=0,lastOwner=0; QByteArray lastInner;
    QByteArray Read(int timeout=1500) {
        QElapsedTimer t; t.start();
        while(t.elapsed()<timeout) {
            input+=socket.readAll();
            if(input.size()>=4) {
                auto n=Get<quint32>(input,0); Need(n>0 && n<=65536,"invalid relay frame size");
                if(input.size()>=4+n) { auto p=input.mid(4,n); input.remove(0,4+n); return p; }
            }
            if(socket.state()==QAbstractSocket::UnconnectedState) return {};
            Pump(1);
        }
        return {};
    }
    void Connect(const std::string& host,quint16 port,const Credential& c) {
        own=c.user; socket.connectToHost(QString::fromStdString(host),port);
        Need(socket.waitForConnected(2000),"relay connection failed");
        auto challenge=Read(); Need(challenge.size()==36 && challenge.startsWith("THC2"),"server needs relay v2 upgrade");
        QByteArray suffix=challenge.mid(4); Put(suffix,c.user);
        QByteArray auth("THA4",4); Put(auth,c.user);
        auth+=Mac(c.token,QByteArray("TheHunt relay auth v4 bidirectional visibility\0",47)+suffix);
        key=Mac(c.token,QByteArray("TheHunt relay key v2\0",21)+suffix);
        // One-byte writes exercise partial TCP framing in the real native server.
        auto wire=Framed(auth); for(int i=0;i<wire.size();++i) { socket.write(wire.mid(i,1)); socket.flush(); Pump(1); }
        auto ack=Read(); Need(ack.size()==44 && ack.startsWith("THO4") && Get<quint64>(ack,4)==own &&
                            ack.right(32)==Mac(key,ack.left(12)),"relay ack proof failed");
    }
    void Drain(int ms=300) {
        QElapsedTimer t; t.start();
        do {
            auto p=Read(5); if(p.isEmpty()) continue;
            Need(p.size()>=61 && (p.startsWith("THX4") || p.startsWith("THS2")),"unexpected signed control");
            Need(Get<quint64>(p,4)==++recvSeq && p.right(32)==Mac(key,p.chopped(32)),"server MAC/sequence failure");
            if(p.startsWith("THX4")) {
                Need(p.size()==62+31*static_cast<unsigned char>(p[29]) && Get<quint32>(p,20)==own,"invalid context");
                room=Get<quint64>(p,12); owner=Get<quint32>(p,24); peers=quint8(p[28]);
                Need((room==0)==(owner==0),"invalid empty context");
            } else {
                lastRoom=Get<quint64>(p,12); lastSender=Get<quint32>(p,20); lastOwner=Get<quint32>(p,24);
                Need(room && room==lastRoom && owner==lastOwner && lastSender!=own,"data escaped native context");
                lastInner=p.mid(28,p.size()-60); ++received;
            }
        } while(t.elapsed()<ms);
    }
    void Send(const QByteArray& inner) { QByteArray p("THD2",4); Put(p,++sendSeq); p+=inner; p+=Mac(key,p); socket.write(Framed(p)); socket.flush(); }
};
int main(int argc,char** argv) {
    QCoreApplication app(argc,argv);
    try {
        QFile input; Need(input.open(stdin,QIODevice::ReadOnly),"stdin required");
        QJsonParseError error; auto json=QJsonDocument::fromJson(input.readAll(),&error);
        Need(error.error==QJsonParseError::NoError && json.isObject(),"invalid fixture JSON");
        auto options=json.object(); auto accounts=options["accounts"].toArray(); Need(accounts.size()==3,"three fixture accounts required");
        auto host=options["host"].toString("127.0.0.1").toStdString();
        int np=options["np_port"].toInt(), rp=options["relay_port"].toInt(); Need(np>0 && np<=65535 && rp>0 && rp<=65535,"explicit fixture ports required");
        Credential credentials[3]; ShadNetClient clients[3]; Relay relays[3];
        auto update=[&]{for(auto& c:clients)c.update();};
        for(int i=0;i<3;++i) {
            credentials[i].npid=accounts[i].toObject()["npid"].toString().toStdString();
            credentials[i].password=accounts[i].toObject()["password"].toString().toStdString();
            Companion(host,np,credentials[i]);
            Need(clients[i].connect(host.c_str(),np),"native game client connection failed"); bool done=false;
            clients[i].onLoginResult=[&,i](const shadnetclient::LoginResult& r) {
                Need(r.error==shadnetclient::ErrorType::NoError && r.userId==credentials[i].user,"native game login failed/mismatched");done=true;
            };
            clients[i].login(credentials[i].npid,credentials[i].password);
            Until(update,[&]{return done;}); clients[i].onLoginResult={};
            relays[i].Connect(host,rp,credentials[i]);
        }
        auto create=[&](int i) {
            bool done=false; quint64 id=0;
            clients[i].onCreateRoom=[&](const shadnetclient::CreateRoomResult& r) {Need(r.error==shadnetclient::ErrorType::NoError,"CreateRoom rejected");id=r.roomId;done=true;};
            shadnetclient::CreateRoomParams p; p.maxSlots=4; p.worldId=1; clients[i].createRoom(p);
            Until(update,[&]{return done;}); clients[i].onCreateRoom={}; Need(id!=0,"zero native room"); return id;
        };
        auto join=[&](int i,quint64 room) {
            bool done=false; clients[i].onJoinRoom=[&](const shadnetclient::JoinRoomResult& r) {Need(r.error==shadnetclient::ErrorType::NoError && r.roomId==room,"JoinRoom rejected");done=true;};
            shadnetclient::JoinRoomParams p;p.roomId=room;clients[i].joinRoom(p);Until(update,[&]{return done;});clients[i].onJoinRoom={};
        };
        auto leave=[&](int i,quint64 room) {
            bool done=false; clients[i].onLeaveRoom=[&](const shadnetclient::LeaveRoomResult& r){Need(r.error==shadnetclient::ErrorType::NoError,"LeaveRoom rejected");done=true;};
            clients[i].leaveRoom(room);Until(update,[&]{return done;});clients[i].onLeaveRoom={};
        };
        auto drain=[&]{for(auto& r:relays)r.Drain();update();};
        quint64 roomA=create(0),roomB=create(2);join(1,roomA);drain();
        Need(roomA!=roomB && relays[0].room==roomA && relays[1].room==roomA && relays[2].room==roomB,"native room context mismatch");
        QByteArray follow(34,0);follow.replace(0,4,"SHAD");follow[4]=0x20;
        relays[0].Send(follow);drain();Need(relays[1].received==1 && relays[2].received==0 && relays[1].lastSender==credentials[0].user && relays[1].lastInner==follow,"valid host route/cross-room isolation failed");
        relays[1].Send(follow);drain();Need(relays[0].received==0,"guest spoofed native owner");
        QByteArray request(11,0);request.replace(0,4,"SHAD");request[4]=0x25;request[5]=1;
        relays[1].Send(request);drain();Need(relays[0].received==1 && relays[2].received==0,"guest request route failed");
        leave(1,roomA);drain();Need(relays[1].room==0 && relays[1].owner==0 && relays[1].peers==0,"leave did not clear context");
        relays[0].Send(follow);drain();Need(relays[1].received==1,"left-room recipient received data");
        join(1,roomA);drain();relays[0].Send(follow);drain();Need(relays[1].received==2,"rejoin route failed");
        leave(0,roomA);drain();Need(relays[1].owner==credentials[1].user,"native owner migration failed");
        leave(2,roomB);join(2,roomA);drain();relays[1].Send(follow);drain();
        Need(relays[2].received==1 && relays[2].lastSender==credentials[1].user && relays[2].lastOwner==credentials[1].user,"migrated owner route failed");
        leave(1,roomA);leave(2,roomA);for(auto& c:clients)c.disconnect();
        std::cout<<"PASS: full native server, fixed ShadNetClient login/Create/Join/Leave, GetToken, signed relay, cross-room isolation, leave/rejoin and owner migration\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<"FAIL: "<<e.what()<<'\n'; return 1; }
}
