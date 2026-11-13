// SPDX-FileCopyrightText: Copyright 2026 shadNet Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <QDebug>
#include <QtEndian>
#include "stream_extractor.h"
#include "stun_server.h"
#include "trusted_network.h"

bool StunSecurity::RecordEndpoint(SharedState* shared, const QString& npid,
                                  const QHostAddress& sender, uint16_t senderPort) {
    bool ipv4 = false;
    const quint32 ip = sender.toIPv4Address(&ipv4);
    if (!shared || !ipv4 || !senderPort || !TrustedNetwork::Allows(sender))
        return false;
    QReadLocker loginLock(&shared->clientsLock);
    const auto user = shared->npidToUserId.constFind(npid);
    if (user == shared->npidToUserId.cend())
        return false;
    const auto client = shared->clients.constFind(*user);
    if (client == shared->clients.cend() || client->npid != npid ||
        client->signalingAddress.isNull() || client->signalingAddress.toIPv4Address() != ip)
        return false;
    QWriteLocker endpointLock(&shared->matching.udpLock);
    shared->matching.udpExt[npid] = {QHostAddress(ip).toString(), senderPort};
    return true;
}

// Match the framing used by both shadp2p and shadPS4 0.20 / bbport 1.9.1.
static constexpr uint16_t SIGNALING_VPORT_NBO = 0xFFFF;
static constexpr int VPORT_HEADER_SIZE = 4;
static const QByteArray PS4_SIGNALING_HEADER = QByteArray::fromHex("ff83ffffffff");

static QByteArray FrameSignaling(const QByteArray& payload, bool ps4Framing) {
    if (ps4Framing)
        return PS4_SIGNALING_HEADER + payload;
    QByteArray framed;
    framed.reserve(VPORT_HEADER_SIZE + payload.size());
    framed.append(reinterpret_cast<const char*>(&SIGNALING_VPORT_NBO), 2);
    framed.append(reinterpret_cast<const char*>(&SIGNALING_VPORT_NBO), 2);
    framed.append(payload);
    return framed;
}

static QByteArray StripVportHeader(const QByteArray& data) {
    if (data.startsWith(PS4_SIGNALING_HEADER))
        return data.mid(PS4_SIGNALING_HEADER.size());
    if (data.size() < VPORT_HEADER_SIZE)
        return {};
    uint16_t src_vp, dst_vp;
    memcpy(&src_vp, data.constData(), 2);
    memcpy(&dst_vp, data.constData() + 2, 2);
    if (src_vp != SIGNALING_VPORT_NBO || dst_vp != SIGNALING_VPORT_NBO)
        return {}; // not a signaling packet
    return data.mid(VPORT_HEADER_SIZE);
}

StunServer::StunServer(SharedState* shared, QObject* parent)
    : QObject(parent), m_socket(new QUdpSocket(this)), m_shared(shared) {
    connect(m_socket, &QUdpSocket::readyRead, this, &StunServer::OnReadyRead);
}

bool StunServer::Start(const QHostAddress& addr, uint16_t port) {
    if (!TrustedNetwork::Allows(addr)) {
        qCritical() << "STUN requires an explicit LAN/VPN/loopback IPv4";
        return false;
    }
    if (!m_socket->bind(addr, port)) {
        qCritical() << "STUN UDP bind failed on" << addr.toString() << ":" << port
                    << m_socket->errorString();
        return false;
    }
    qInfo().nospace().noquote() << "STUN UDP listener on: " << addr.toString() << ":" << port;
    return true;
}

void StunServer::OnReadyRead() {
    while (m_socket->hasPendingDatagrams()) {
        QByteArray raw;
        raw.resize(static_cast<int>(m_socket->pendingDatagramSize()));
        QHostAddress sender;
        uint16_t senderPort = 0;
        m_socket->readDatagram(raw.data(), raw.size(), &sender, &senderPort);

        if (raw.isEmpty() || !TrustedNetwork::Allows(sender))
            continue;

        // Strip signaling vport header and preserve the caller's reply framing.
        QByteArray data = StripVportHeader(raw);
        if (data.isEmpty())
            continue;

        uint8_t cmd = static_cast<uint8_t>(data[0]);
        switch (cmd) {
        case 0x01:
            HandleStunPing(data, sender, senderPort, raw.startsWith(PS4_SIGNALING_HEADER));
            break;
        default:
            break;
        }
    }
}

void StunServer::HandleStunPing(const QByteArray& data, const QHostAddress& sender,
                                uint16_t senderPort, bool ps4Framing) {
    // cmd(1) + online_id(16) + local_ip(4) = 21 bytes minimum
    if (data.size() < 21)
        return;

    // Extract null-padded online_id from bytes 1-16
    QByteArray rawId = data.mid(1, 16);
    int nullPos = rawId.indexOf('\0');
    QString npid = QString::fromUtf8(rawId.left(nullPos >= 0 ? nullPos : 16)).trimmed();
    if (npid.isEmpty())
        return;

    QString extIpStr = sender.toString();
    // Strip IPv6-mapped prefix if present (e.g. "::ffff:192.168.1.1" -> "192.168.1.1")
    if (extIpStr.startsWith("::ffff:"))
        extIpStr = extIpStr.mid(7);

    qInfo() << "STUN ping: npid=" << npid << "ext=" << extIpStr << ":" << senderPort;

    // The legacy packet has no credential. An unauthenticated discovery ping
    // may still receive its own observed endpoint, but cannot change a player.
    StunSecurity::RecordEndpoint(m_shared, npid, sender, senderPort);

    // Reply: ext_ip(4 bytes network order) + ext_port(2 bytes network order)
    QHostAddress extAddr(extIpStr);
    uint32_t ipNet = qToBigEndian(static_cast<uint32_t>(extAddr.toIPv4Address()));
    uint16_t portNet = qToBigEndian(senderPort);

    QByteArray response;
    response.append(reinterpret_cast<const char*>(&ipNet), 4);
    response.append(reinterpret_cast<const char*>(&portNet), 2);
    m_socket->writeDatagram(FrameSignaling(response, ps4Framing), sender, senderPort);
}
