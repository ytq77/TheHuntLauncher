// SPDX-License-Identifier: GPL-2.0-or-later
#include "authenticated_relay.h"
#include "client_session.h"
#include "config.h"
#include "database.h"
#include "trusted_network.h"
#include <QCryptographicHash>
#include <QDebug>
#include <QElapsedTimer>
#include <QMessageAuthenticationCode>
#include <QRandomGenerator>
#include <QSqlQuery>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTimer>
#include <QUuid>
#include <QtEndian>
#include <algorithm>
#include <limits>
#include <vector>
#include <cmath>
#include <cstring>
#include <deque>

namespace {
template<class T> void Append(QByteArray& out, T value) {
    char bytes[sizeof(T)];
    qToLittleEndian<T>(value, bytes);
    out.append(bytes, sizeof(bytes));
}
template<class T> T Read(const QByteArray& data, qsizetype at) {
    return qFromLittleEndian<T>(data.constData() + at);
}
QByteArray Domain(const char* text, size_t length, const QByteArray& nonce, quint64 user) {
    QByteArray out(text, static_cast<qsizetype>(length)); // includes exactly ONE NUL
    out += nonce;
    Append(out, user);
    return out;
}
constexpr int MaxConnections = 64;
constexpr qint64 MaxBacklog = 256 * 1024;
constexpr qint64 AdmissionMs = 5000;
constexpr qint64 StallMs = 5000;
constexpr qint64 MatchingRetryMs = 1500;
constexpr int PlacementShortRetryMs = 10;
constexpr qint64 PlacementShortWindowMs = 2000;
constexpr qsizetype MaxDeferredBytes = 256 * 1024;
constexpr size_t MaxDeferredFrames = 32;
constexpr quint32 FramesPerSecond = 256;
constexpr quint32 BytesPerSecond = 1024 * 1024;
}

namespace RelayAuth {
QByteArray Mac(const QByteArray& key, const QByteArray& message) {
    return QMessageAuthenticationCode::hash(message, key, QCryptographicHash::Sha256);
}
bool Equal(const QByteArray& a, const QByteArray& b) {
    if (a.size() != b.size()) return false;
    volatile unsigned difference = 0;
    for (qsizetype i = 0; i < a.size(); ++i)
        difference = difference | (static_cast<unsigned char>(a[i]) ^ static_cast<unsigned char>(b[i]));
    return difference == 0;
}
QByteArray Proof(const QByteArray& token, const QByteArray& nonce, quint64 user) {
    constexpr char domain[] = "TheHunt relay auth v2";
    return Mac(token, Domain(domain, sizeof(domain), nonce, user));
}
QByteArray SessionKey(const QByteArray& token, const QByteArray& nonce, quint64 user) {
    constexpr char domain[] = "TheHunt relay key v2";
    return Mac(token, Domain(domain, sizeof(domain), nonce, user));
}
QByteArray Frame(const QByteArray& payload) {
    if (payload.isEmpty() || payload.size() > MaxPayload) return {};
    QByteArray out;
    Append(out, static_cast<quint32>(payload.size()));
    return out + payload;
}
bool PortFromEnvironment(quint16& port) {
    port = 20444;
    if (!qEnvironmentVariableIsSet("THEHUNT_RELAY_PORT")) return true;
    return ParsePort(qgetenv("THEHUNT_RELAY_PORT"), port);
}
bool ParsePort(const QByteArray& value, quint16& port) {
    if (value.isEmpty() || value.size() > 5) return false;
    for (char c : value) if (c < '0' || c > '9') return false;
    bool ok = false;
    const uint number = value.toUInt(&ok);
    if (!ok || number == 0 || number > 65535) return false;
    port = static_cast<quint16>(number);
    return true;
}
bool ValidPlacement(const QByteArray& text) {
    if (text.isEmpty() || text.size()>128) return false;
    const auto fields=text.split(',');
    if(fields.size()!=7 || fields[0]!="1") return false;
    for(int i=1;i<=5;++i) {
        if(fields[i].isEmpty() || fields[i].size()>8) return false;
        for(char c:fields[i])
            if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F'))) return false;
        bool ok=false;const quint32 bits=fields[i].toUInt(&ok,16);
        if(!ok) return false;
        if(i==1) {
            if(bits==0 || bits==std::numeric_limits<quint32>::max()) return false;
        } else { float number;std::memcpy(&number,&bits,4);if(!std::isfinite(number))return false; }
    }
    if(fields[6].isEmpty() || fields[6].size()>11) return false;
    for(qsizetype i=0;i<fields[6].size();++i) {
        char c=fields[6][i];if(i==0&&c=='-')continue;if(c<'0'||c>'9')return false;
    }
    bool ok=false;fields[6].toInt(&ok,10);return ok;
}
bool ValidInner(const QByteArray& p) {
    if (p.size() < 5 || !p.startsWith("SHAD")) return false;
    const auto byte = [&](int n) { return static_cast<quint8>(p[n]); };
    switch (byte(4)) {
    case 0x20: return (p.size() == 30 || p.size() == 34) && byte(5) == 0;
    case 0x22: { // SignalingBbscWorldFlags: map(v1)=40 groups, Dream(v2)=57.
        if (p.size() != 1018 || (byte(5) != 1 && byte(5) != 2)) return false;
        const int groups = byte(5) == 1 ? 40 : 57;
        const int chunks = (groups + 7) / 8;
        return byte(15) == chunks && byte(14) < chunks && byte(17) > 0 &&
               byte(17) <= std::min(8, groups - 8 * byte(14)) &&
               (byte(16) >> byte(17)) == 0;
    }
    case 0x23: return p.size() == 14 && byte(9) <= 1;
    case 0x24: return p.size() == 15;
    case 0x25: return p.size() == 11 && byte(5) <= 1 && byte(6) <= 1;
    case 0x26: return p.size() >= 6 && byte(5) > 0 && p.size() == 6 + 4 * byte(5);
    case 0x27: return p.size() == 9;
    case 0x28: {
        if(p.size()<56 || byte(7)>1 || !Read<quint32>(p,8)) return false;
        const bool reverse=byte(6)==4 || byte(6)==5;
        if(reverse ? (p.size()!=96 || byte(5)!=2) : (p.size()!=56 || byte(5)!=1 || byte(6)>3)) return false;
        if(reverse) for(int at=56;at<96;at+=8) if(!Read<quint64>(p,at)) return false;
        const auto kind=byte(6);
        if(kind==3) return byte(7)==0 && p.mid(12)==QByteArray(44,0);
        if(!Read<quint64>(p,12) || !Read<quint64>(p,20) || !Read<quint32>(p,52) || Read<quint32>(p,52)==0xffffffff) return false;
        if(kind==0 || kind==4) return byte(7)==0 && p.mid(28,24)==QByteArray(24,0);
        return Read<quint64>(p,28) && Read<quint64>(p,36) && Read<quint64>(p,44) && (kind!=2 || byte(7)==1);
    }
    case 0x30: return p.size() > 5 && p.size() <= MaxPayload - 60;
    default: return false; // Includes 0x40 legacy envelope and 0x7f peer control.
    }
}
bool Allowed(const QByteArray& p, bool senderOwner, bool recipientOwner) {
    if (!ValidInner(p)) return false;
    if (p[4] == 0x23) return true;
    if (p[4] == 0x25)
        return p[5] == 0 ? senderOwner : (!senderOwner && recipientOwner);
    if (p[4] == 0x28) return (p[6]==1 || p[6]==4) ? (!senderOwner && recipientOwner) : (senderOwner && !recipientOwner);
    return senderOwner;
}
}

struct AuthenticatedRelay::Impl {
    struct Context {
        QString com;
        quint64 room = 0, joined = 0;
        quint16 member = 0;
        quint32 owner = 0;
        quint8 peers = 0;
        QByteArray npid;
        bool SameMembership(const Context& b) const {
            return com == b.com && room == b.room && joined == b.joined && member == b.member;
        }
        bool operator==(const Context& b) const {
            return SameMembership(b) && owner == b.owner && peers == b.peers && npid == b.npid;
        }
        RelayAuth::SummonRoom SummonRoom() const { return {room, joined, member}; }
    };
    struct Peer {
        QTcpSocket* socket = nullptr;
        QByteArray input, nonce, key, tokenHash;
        quint64 user = 0, incoming = 0, outgoing = 0, connection = 0;
        qint64 accepted = 0, partialSince = -1, blockedSince = -1, window = 0;
        quint32 frames = 0, bytes = 0;
        bool authenticated = false, dead = false, haveContext = false, visibility = false;
        QByteArray roster;
        Context context;
        std::deque<QByteArray> deferred;
        qsizetype deferredBytes = 0;
        qint64 deferredSince = 0;
    };
    struct Placement : RelayAuth::PlacementRequest {
        quint64 generation = 0;
        qint64 expires = 0, shortRetryUntil = 0;
        quint64 deliveredTo = 0;
        bool matchingBusy = false;
        const char* deferredReason = nullptr;
    };
    struct MatchingRead {
        SharedState& state;
        bool rooms, clients;
        explicit MatchingRead(SharedState& s)
            : state(s), rooms(s.matching.roomsLock.tryLockForRead()),
              clients(rooms && s.clientsLock.tryLockForRead()) {}
        ~MatchingRead() {
            if (clients) state.clientsLock.unlock();
            if (rooms) state.matching.roomsLock.unlock();
        }
        explicit operator bool() const { return clients; }
    };
    AuthenticatedRelay* self;
    SharedState* shared;
    QTcpServer listener;
    QTimer timer, placementRetryTimer;
    QElapsedTimer clock;
    std::unique_ptr<Database> db;
    std::vector<std::unique_ptr<Peer>> peers;
    QHash<quint64, Placement> placements;
    quint64 nextPlacement = 0;
    quint64 nextConnection = 0;
    qint64 globalWindow = 0;
    quint32 globalFrames = 0, globalBytes = 0;

    Impl(AuthenticatedRelay* owner, SharedState* state) : self(owner), shared(state) {
        clock.start();
        timer.setInterval(250);
        timer.setTimerType(Qt::PreciseTimer);
        QObject::connect(&timer, &QTimer::timeout, self, [this] { Tick(); });
        placementRetryTimer.setSingleShot(true);
        placementRetryTimer.setTimerType(Qt::PreciseTimer);
        placementRetryTimer.setInterval(PlacementShortRetryMs);
        QObject::connect(&placementRetryTimer, &QTimer::timeout, self, [this] { RetryPlacements(true); });
        QObject::connect(&listener, &QTcpServer::newConnection, self, [this] { Accept(); });
        listener.setMaxPendingConnections(16);
    }
    void Close(Peer& p) {
        if (p.dead) return;
        p.dead = true;
        p.deferred.clear();
        p.key.fill(0); p.nonce.fill(0); p.tokenHash.fill(0);
        p.socket->abort();
    }
    bool Send(Peer& p, const QByteArray& payload) {
        const QByteArray frame = RelayAuth::Frame(payload);
        if (p.dead || frame.isEmpty() || p.socket->bytesToWrite() + frame.size() > MaxBacklog) {
            Close(p); return false;
        }
        if (p.socket->write(frame) != frame.size()) { Close(p); return false; }
        return true;
    }
    bool Signed(Peer& p, QByteArray payload) {
        if (p.dead || p.outgoing == std::numeric_limits<quint64>::max()) { Close(p); return false; }
        QByteArray out = payload.left(4);
        Append(out, ++p.outgoing);
        out += payload.mid(4);
        out += RelayAuth::Mac(p.key, out);
        return Send(p, out);
    }
    QByteArray Token(quint64 user, QString* npid = nullptr) {
        if (user == 0 || user > std::numeric_limits<quint32>::max()) return {};
        QSqlQuery query(db->Conn());
        query.prepare("SELECT token, username FROM account WHERE user_id=? AND banned=0");
        query.addBindValue(static_cast<qlonglong>(user));
        if (!query.exec() || !query.next()) return {};
        const QByteArray token = query.value(0).toString().toUtf8();
        if (npid) *npid = query.value(1).toString();
        return token;
    }
    bool Current(Peer& p) {
        const QByteArray token = Token(p.user);
        if (token.isEmpty() || !RelayAuth::Equal(p.tokenHash,
                QCryptographicHash::hash(token, QCryptographicHash::Sha256))) {
            Close(p); return false;
        }
        return true;
    }
    RelayAuth::SummonAdmission SummonAdmission(quint64 user) {
        using Access = RelayAuth::SummonAccess;
        const MatchingRead lock(*shared);
        if (!lock) return {Access::Busy, {}};
        bool noRoom = false;
        const auto c = Resolve(user, &noRoom);
        if (c.room && c.owner == user) return {Access::Owner, c.SummonRoom()};
        return {noRoom ? Access::NoRoom : Access::Denied, {}};
    }
    bool HashCurrent(quint64 user, const QByteArray& expected) {
        const auto token = Token(user);
        return !token.isEmpty() && RelayAuth::Equal(expected,
            QCryptographicHash::hash(token, QCryptographicHash::Sha256));
    }
    bool PlacementFrame(Peer& guest, const Placement& grant, quint32 ttl) {
        QByteArray out("THP2",4);
        Append(out,grant.generation); Append(out,grant.room.room);
        Append(out,static_cast<quint32>(grant.host)); Append(out,static_cast<quint32>(grant.target));
        Append(out,ttl);
        if(ttl) out += grant.text;
        return Signed(guest,out);
    }
    void Revoke(const Placement& grant) {
        for(auto& p:peers)
            if(!p->dead && p->authenticated && p->user==grant.target && p->connection==grant.deliveredTo &&
               RelayAuth::Equal(p->tokenHash,grant.targetHash)) PlacementFrame(*p,grant,0);
    }
    void PlacementLog(const Placement& grant, const char* reason) {
        qInfo() << "Relay placement" << reason << "record" << grant.record << "generation" << grant.generation
                << "room" << grant.room.room << "host" << grant.host << "target" << grant.target;
    }
    void CancelPlacement(quint64 record) {
        for (auto it = placements.begin(); it != placements.end(); ++it) {
            if (it->record != record) continue;
            PlacementLog(*it, "cancelled-by-broker");
            Revoke(*it);
            placements.erase(it);
            return;
        }
    }
    bool DeferPlacement(Placement& grant, const char* reason) {
        if (!grant.deferredReason || std::strcmp(grant.deferredReason, reason) != 0)
            PlacementLog(grant, reason);
        grant.deferredReason = reason;
        return true;
    }
    bool DropPlacement(Placement& grant, const char* reason) {
        PlacementLog(grant, reason);
        Revoke(grant);
        return false;
    }
    bool TryPlacement(Placement& grant) {
        grant.matchingBusy = false;
        if (grant.expires <= clock.elapsed()) return DropPlacement(grant, "expired");
        // Credential queries precede matching locks. Both identities are checked on every attempt.
        if (!HashCurrent(grant.host, grant.hostHash) || !HashCurrent(grant.target, grant.targetHash))
            return DropPlacement(grant, "credential-invalid");
        const MatchingRead lock(*shared);
        if (!lock) {
            if (!grant.deliveredTo) {
                grant.matchingBusy = true;
                if (!grant.shortRetryUntil)
                    grant.shortRetryUntil = clock.elapsed() + PlacementShortWindowMs;
            }
            return DeferPlacement(grant, "deferred-matching-busy");
        }
        const Context host = Resolve(grant.host);
        if (host.owner != grant.host || !(host.SummonRoom() == grant.room))
            return DropPlacement(grant, "host-ownership-or-membership-changed");
        bool noRoom = false, inRoom = false;
        Resolve(grant.target, &noRoom, &inRoom);
        if (inRoom) {
            // The matching THX2 hands the already sent hint to the native
            // summon load. Do not revoke it before that load can consume it.
            // A different room's THX2 invalidates it on the client.
            PlacementLog(grant, "target-already-in-room");
            return false;
        }
        if (!noRoom) return DeferPlacement(grant, "deferred-target-np-not-ready");
        for (auto& item : peers) {
            Peer& guest = *item;
            if (guest.dead || !guest.authenticated || guest.user != grant.target ||
                !RelayAuth::Equal(guest.tokenHash, grant.targetHash)) continue;
            if (grant.deliveredTo == guest.connection) return true;
            // Keep the membership read locks through the bounded socket enqueue so JoinRoom
            // cannot commit between the prejoin check and the positive v2 THP2.
            Update(guest, {}, false);
            const qint64 remaining = grant.expires - clock.elapsed();
            if (remaining <= 0) return DropPlacement(grant, "expired");
            if (PlacementFrame(guest, grant, static_cast<quint32>(remaining))) {
                grant.deliveredTo = guest.connection;
                grant.deferredReason = nullptr;
                PlacementLog(grant, "socket-enqueued");
                return true;
            }
            return DeferPlacement(grant, "deferred-target-write-failed");
        }
        return DeferPlacement(grant, "deferred-target-relay-offline");
    }
    void QueuePlacement(const RelayAuth::PlacementRequest& request) {
        Placement grant;
        static_cast<RelayAuth::PlacementRequest&>(grant) = request;
        if (!grant.record || !grant.room.room || grant.host == grant.target || grant.ttlMs <= 0 ||
            grant.ttlMs > RelayAuth::PlacementTtlMs || !RelayAuth::ValidPlacement(grant.text)) {
            PlacementLog(grant, "invalid-request");
            return;
        }
        if (nextPlacement == std::numeric_limits<quint64>::max() ||
            (!placements.contains(grant.target) && placements.size() >= 256)) {
            PlacementLog(grant, "queue-limit");
            return;
        }
        const auto old = placements.find(grant.target);
        if (old != placements.end()) {
            DropPlacement(*old, "superseded");
            placements.erase(old);
        }
        grant.generation = ++nextPlacement;
        grant.expires = clock.elapsed() + grant.ttlMs;
        if (TryPlacement(grant)) placements.insert(grant.target, std::move(grant));
        ScheduleShortPlacementRetry();
    }
    bool ShortRetryDue(const Placement& grant, qint64 now) const {
        return grant.matchingBusy && now < grant.shortRetryUntil && now < grant.expires;
    }
    void ScheduleShortPlacementRetry() {
        if (placementRetryTimer.isActive()) return;
        const auto now = clock.elapsed();
        for (const auto& grant : placements) if (ShortRetryDue(grant, now)) {
            placementRetryTimer.start();
            break;
        }
    }
    void RetryPlacements(bool shortOnly = false) {
        if (shortOnly) {
            // Avoid retrying every credential query while the same lock is busy.
            // TryPlacement still revalidates credentials and matching under its own locks.
            const MatchingRead lock(*shared);
            if (!lock) { ScheduleShortPlacementRetry(); return; }
        }
        for (auto it = placements.begin(); it != placements.end();) {
            if (shortOnly && !ShortRetryDue(*it, clock.elapsed())) { ++it; continue; }
            if (TryPlacement(*it)) ++it;
            else it = placements.erase(it);
        }
        ScheduleShortPlacementRetry();
    }
    // Caller holds roomsLock THEN clientsLock, matching the existing native order.
    Context Resolve(quint64 user, bool* noRoom = nullptr, bool* inRoom = nullptr) const {
        if (noRoom) *noRoom = false;
        if (inRoom) *inRoom = false;
        const auto client = shared->clients.constFind(static_cast<qint64>(user));
        if (client == shared->clients.cend() || client->npid.isEmpty()) return {};
        Context result;
        int found = 0;
        for (auto r = shared->matching.rooms.cbegin(); r != shared->matching.rooms.cend(); ++r) {
            for (auto member = r->members.cbegin(); member != r->members.cend(); ++member) {
                if (member->userId != static_cast<qint64>(user)) continue;
                if (inRoom) *inRoom = true;
                if (++found != 1 || member->npid != client->npid || member.key() != member->memberId ||
                    r.key().first.isEmpty() || r.key().second == 0 || r->roomId != r.key().second)
                    return {};
                const auto owner = r->members.constFind(r->ownerMemberId);
                if (owner == r->members.cend() || owner.key() != owner->memberId || owner->userId <= 0 ||
                    static_cast<quint64>(owner->userId) > std::numeric_limits<quint32>::max()) return {};
                const auto onlineOwner = shared->clients.constFind(owner->userId);
                if (onlineOwner == shared->clients.cend() || onlineOwner->npid != owner->npid) return {};
                result.com = r.key().first;
                result.room = r->roomId;
                result.joined = member->joinDate;
                result.member = member->memberId;
                result.owner = static_cast<quint32>(owner->userId);
                result.npid = member->npid.toUtf8();
            }
        }
        // Only a successful scan of ALL rooms can establish zero membership.
        // An empty Context can also mean a guest in another title or inconsistent state.
        if (noRoom)
            *noRoom = found == 0 && client->bloodborneMatching && client->matchingContextId != 0;
        return found == 1 && result.com == QStringLiteral("bloodborne") ? result : Context{};
    }
    void ContextFrame(Peer& p, const Context& c, const QByteArray& roster = {}) {
        QByteArray out(p.visibility?"THX4":"THX2", 4);
        Append(out, c.room);
        Append(out, static_cast<quint32>(p.user));
        Append(out, c.owner);
        out.append(static_cast<char>(c.peers));
        if(p.visibility) {
            out.append(static_cast<char>(roster.size()/31));
            out+=roster;
        }
        Signed(p, out);
    }
    void Update(Peer& p, const Context& c, bool periodic, const QByteArray& roster = {}) {
        if (!p.haveContext || !(p.context == c) || periodic || p.roster!=roster) {
            // Membership incarnations include native joinDate/memberId. Even a
            // rapid leave/rejoin of the SAME room explicitly clears old state.
            if (p.haveContext && p.context.room && !p.context.SameMembership(c))
                ContextFrame(p, {});
            ContextFrame(p, c, roster);
            p.context = c;
            p.roster = roster;
            p.haveContext = true;
        }
    }
    void DeferData(Peer& sender, const QByteArray& inner) {
        if (sender.deferred.size() >= MaxDeferredFrames ||
            sender.deferredBytes + inner.size() > MaxDeferredBytes) {
            qInfo() << "Relay matching retry queue limit, closing account" << sender.user;
            Close(sender);
            return;
        }
        if (sender.deferred.empty()) sender.deferredSince = clock.elapsed();
        sender.deferred.push_back(inner);
        sender.deferredBytes += inner.size();
    }
    void Forward(Peer& sender, const QByteArray& inner, const Context& from,
                 const std::vector<Peer*>& active, const std::vector<Context>& contexts) {
        if (sender.dead || !from.room) return;
        for (size_t i = 0; i < active.size(); ++i) {
            Peer& to = *active[i];
            const Context& c = contexts[i];
            if (&to == &sender || to.dead || !c.room || c.room != from.room || c.com != from.com ||
                !RelayAuth::Allowed(inner, sender.user == from.owner, to.user == c.owner)) continue;
            if(inner[4]==0x28 && (!sender.visibility || !to.visibility || Read<quint32>(inner,8)!=to.user)) continue;
            if(inner[4]==0x28 && inner[5]==2 &&
               (Read<quint64>(inner,56)!=from.room || Read<quint64>(inner,64)!=from.joined ||
                Read<quint64>(inner,72)!=c.joined)) continue;
            QByteArray out("THS2", 4);
            Append(out, from.room);
            Append(out, static_cast<quint32>(sender.user));
            Append(out, from.owner);
            QByteArray packet = inner;
            // Private v2 life keys are authenticated account IDs.
            if (packet[4] == 0x23)
                qToLittleEndian(static_cast<quint32>(sender.user), packet.data() + 5);
            out += packet;
            Signed(to, out);
        }
    }
    bool Refresh(Peer* sender = nullptr, const QByteArray& inner = {}) {
        std::vector<Peer*> active;
        const qint64 now = clock.elapsed();
        for (auto& p : peers) {
            // The first deferred frame owns an absolute deadline. Enforce it
            // before a recovered matching lock can drain an expired queue,
            // including after an event-loop pause or on a newly arrived frame.
            if (!p->dead && !p->deferred.empty() && now - p->deferredSince >= MatchingRetryMs) {
                qInfo() << "Relay matching retry expired, closing account" << p->user;
                Close(*p);
            }
            if (!p->dead && p->authenticated && Current(*p)) active.push_back(p.get());
        }
        if (sender && sender->dead) return true;
        // Never wait for game threads while the relay event loop is processing.
        const MatchingRead lock(*shared);
        if (!lock) {
            if (sender) DeferData(*sender, inner);
            return false;
        }
        std::vector<Context> contexts;
        for (Peer* p : active) contexts.push_back(Resolve(p->user));
        for (size_t i = 0; i < active.size(); ++i) {
            auto& c = contexts[i];
            if (!c.room) continue;
            for (size_t j = 0; j < active.size(); ++j)
                if (i != j && contexts[j].room == c.room && contexts[j].com == c.com)
                    ++c.peers;
        }
        for (size_t i = 0; i < active.size(); ++i) {
            Peer& p = *active[i];
            const auto& fresh = contexts[i];
            if (!p.deferred.empty() &&
                (!p.haveContext || !p.context.SameMembership(fresh) || p.context.owner != fresh.owner)) {
                qInfo() << "Relay matching retry discarded after membership change, account" << p.user;
                p.deferred.clear();
                p.deferredBytes = 0;
            }
            QByteArray roster;
            if(p.visibility && fresh.room) {
                for(size_t j=0;j<active.size();++j) {
                    const auto& c=contexts[j];
                    if(c.room!=fresh.room || c.com!=fresh.com) continue;
                    if(c.npid.isEmpty() || c.npid.size()>16 || !c.member || !c.joined ||
                       std::any_of(c.npid.cbegin(),c.npid.cend(),[](char ch){return static_cast<unsigned char>(ch)<0x21 || static_cast<unsigned char>(ch)>0x7e;})) {
                        Close(p);break;
                    }
                    Append(roster,static_cast<quint32>(active[j]->user));
                    Append(roster,c.member);Append(roster,c.joined);
                    roster.append(active[j]->visibility?char(1):char(0));
                    roster+=c.npid;roster+=QByteArray(16-c.npid.size(),0);
                }
            }
            if (!p.dead) Update(p, fresh, !sender,roster);
        }
        for (size_t i = 0; i < active.size(); ++i) {
            Peer& p = *active[i];
            while (!p.deferred.empty() && !p.dead) {
                const auto frame = std::move(p.deferred.front());
                p.deferred.pop_front();
                Forward(p, frame, contexts[i], active, contexts);
            }
            p.deferredBytes = 0;
            if (&p == sender) Forward(p, inner, contexts[i], active, contexts);
        }
        return true;
    }
    void Payload(Peer& p, const QByteArray& payload) {
        if (!p.authenticated) {
            if (clock.elapsed() - p.accepted >= AdmissionMs || payload.size() != 44 ||
                !payload.startsWith("THA4")) { Close(p); return; }
            const quint64 user = Read<quint64>(payload, 4);
            const QByteArray token = Token(user);
            const bool visibility=payload.startsWith("THA4");
            constexpr char visibilityDomain[]="TheHunt relay auth v4 bidirectional visibility";
            const auto proof=visibility?RelayAuth::Mac(token,Domain(visibilityDomain,sizeof(visibilityDomain),p.nonce,user)):
                RelayAuth::Proof(token,p.nonce,user);
            if (token.isEmpty() || !RelayAuth::Equal(payload.mid(12), proof)) {
                Close(p); return;
            }
            // Only one relay connection per account; no duplicate peer counts.
            for (auto& other : peers)
                if (other.get() != &p && !other->dead && other->authenticated && other->user == user) {
                    Close(p); return;
                }
            p.user = user;
            p.key = RelayAuth::SessionKey(token, p.nonce, user);
            p.tokenHash = QCryptographicHash::hash(token, QCryptographicHash::Sha256);
            p.nonce.fill(0);
            p.authenticated = true;
            p.visibility=visibility;
            QByteArray ack(visibility?"THO4":"THO2", 4);
            Append(ack, user);
            Send(p, ack + RelayAuth::Mac(p.key, ack));
            Refresh(); // Ack always precedes the first context, sequence starts at 1.
            const auto pending = placements.find(p.user);
            if (pending != placements.end() && !TryPlacement(*pending))
                placements.erase(pending);
            ScheduleShortPlacementRetry();
            return;
        }
        if (payload.size() < 49 || !payload.startsWith("THD2") ||
            p.incoming == std::numeric_limits<quint64>::max() ||
            Read<quint64>(payload, 4) != p.incoming + 1 ||
            !RelayAuth::Equal(payload.right(32), RelayAuth::Mac(p.key, payload.chopped(32)))) {
            Close(p); return;
        }
        const QByteArray inner = payload.mid(12, payload.size() - 44);
        if (!RelayAuth::ValidInner(inner) || (inner[4]==0x28 && !p.visibility)) { Close(p); return; }
        ++p.incoming;
        Refresh(&p, inner);
    }
    void ReadReady(Peer& p) {
        if (p.dead) return;
        const qint64 now = clock.elapsed();
        if (now - p.window >= 1000) { p.window = now; p.bytes = p.frames = 0; }
        if (now - globalWindow >= 1000) { globalWindow = now; globalBytes = globalFrames = 0; }
        const QByteArray read = p.socket->read(RelayAuth::MaxPayload + 4);
        p.bytes += static_cast<quint32>(read.size());
        globalBytes += static_cast<quint32>(read.size());
        if (p.bytes > BytesPerSecond || globalBytes > 8 * BytesPerSecond ||
            p.input.size() + read.size() > 2 * (RelayAuth::MaxPayload + 4)) { Close(p); return; }
        p.input += read;
        int processed = 0;
        while (!p.dead && p.input.size() >= 4 && processed++ < 32) {
            const quint32 size = Read<quint32>(p.input, 0);
            if (size == 0 || size > RelayAuth::MaxPayload) { Close(p); return; }
            if (p.input.size() < 4 + size) break;
            if (++p.frames > FramesPerSecond || ++globalFrames > 2048) { Close(p); return; }
            const QByteArray payload = p.input.mid(4, size);
            p.input.remove(0, 4 + size);
            p.partialSince = -1;
            Payload(p, payload);
        }
        if (!p.input.isEmpty() && p.partialSince < 0) p.partialSince = now;
        // Yield between bounded batches. The socket's Qt buffer is bounded too.
        if (!p.dead && ((p.input.size() >= 4 && p.input.size() >= 4 + Read<quint32>(p.input, 0)) ||
                        p.socket->bytesAvailable() > 0))
            QTimer::singleShot(0, p.socket, [this, &p] { ReadReady(p); });
    }
    void Accept() {
        while (auto* socket = listener.nextPendingConnection()) {
            if (peers.size() >= MaxConnections || nextConnection == std::numeric_limits<quint64>::max() ||
                !TrustedNetwork::Allows(socket->peerAddress())) {
                socket->abort(); socket->deleteLater(); continue;
            }
            auto item = std::make_unique<Peer>();
            Peer& p = *item;
            p.socket = socket;
            p.connection = ++nextConnection;
            p.accepted = clock.elapsed();
            socket->setReadBufferSize(RelayAuth::MaxPayload + 4);
            socket->setSocketOption(QAbstractSocket::LowDelayOption, 1);
            socket->setSocketOption(QAbstractSocket::SendBufferSizeSocketOption, 64 * 1024);
            quint32 random[8];
            QRandomGenerator::system()->fillRange(random);
            p.nonce = QByteArray(reinterpret_cast<const char*>(random), sizeof(random));
            peers.push_back(std::move(item));
            QObject::connect(socket, &QTcpSocket::readyRead, self, [this, &p] { ReadReady(p); });
            QObject::connect(socket, &QTcpSocket::disconnected, self, [this, &p] { Close(p); });
            Send(p, QByteArray("THC2", 4) + p.nonce);
        }
    }
    void Tick() {
        const qint64 now = clock.elapsed();
        for (auto& item : peers) {
            Peer& p = *item;
            if (p.dead) continue;
            if ((!p.authenticated && now - p.accepted >= AdmissionMs) ||
                (p.partialSince >= 0 && now - p.partialSince >= StallMs)) { Close(p); continue; }
            if (p.socket->bytesToWrite() == 0) p.blockedSince = -1;
            else if (p.blockedSince < 0) p.blockedSince = now;
            else if (now - p.blockedSince >= StallMs) Close(p);
        }
        Refresh();
        RetryPlacements();
        // Destroy signal/continuation contexts before freeing their captured Peer.
        for (auto& p : peers) if (p->dead) {
            QObject::disconnect(p->socket, nullptr, self, nullptr);
            delete p->socket;
        }
        peers.erase(std::remove_if(peers.begin(), peers.end(), [](const auto& p) { return p->dead; }), peers.end());
    }
};

AuthenticatedRelay::AuthenticatedRelay(SharedState* shared, QObject* parent)
    : QObject(parent), m(std::make_unique<Impl>(this, shared)) {}
AuthenticatedRelay::~AuthenticatedRelay() { Stop(); }
bool AuthenticatedRelay::Start(ConfigManager* config, const QString& dbPath) {
    Stop();
    if (!config->IsBloodborneSeamlessCoopEnabled()) return true;
    quint16 port;
    if (!RelayAuth::PortFromEnvironment(port)) return false;
    return Listen(QHostAddress(config->GetHost()), port, dbPath);
}
bool AuthenticatedRelay::Listen(const QHostAddress& address, quint16 port, const QString& dbPath) {
    Stop();
    if (!m->shared || !TrustedNetwork::Allows(address)) return false;
    m->db = std::make_unique<Database>(QStringLiteral("relay_") + QUuid::createUuid().toString());
    if (!m->db->Open(dbPath)) { m->db.reset(); return false; }
    QSqlQuery noWait(m->db->Conn());
    if (!noWait.exec("PRAGMA busy_timeout=0") || !m->listener.listen(address, port)) {
        m->db.reset(); return false;
    }
    m->shared->relayAuthReady.store(true);
    // WebAPI routes and relay callbacks all execute on this Qt event thread.
    m->shared->relaySummonAdmission = [this](quint64 user) { return m->SummonAdmission(user); };
    m->shared->relayPlacement = [this](const RelayAuth::PlacementRequest& placement) {
        m->QueuePlacement(placement);
    };
    m->shared->relayCancelPlacement = [this](quint64 record) { m->CancelPlacement(record); };
    m->timer.start();
    return true;
}
quint16 AuthenticatedRelay::Port() const { return m->listener.serverPort(); }
void AuthenticatedRelay::Stop() {
    if (m->shared) {
        m->shared->relayAuthReady.store(false);
        m->shared->relaySummonAdmission = {};
        m->shared->relayPlacement = {};
        m->shared->relayCancelPlacement = {};
    }
    m->placements.clear();
    m->timer.stop();
    m->placementRetryTimer.stop();
    m->listener.close();
    for (auto& p : m->peers) {
        m->Close(*p);
        QObject::disconnect(p->socket, nullptr, this, nullptr);
        delete p->socket;
    }
    m->peers.clear();
    m->db.reset();
}
