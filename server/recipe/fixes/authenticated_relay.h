// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <memory>
#include <QByteArray>
#include <QHostAddress>
#include <QObject>

class ConfigManager;
struct SharedState;

namespace RelayAuth {
constexpr quint32 MaxPayload = 65536;
QByteArray Mac(const QByteArray& key, const QByteArray& message);
bool Equal(const QByteArray& a, const QByteArray& b);
QByteArray Proof(const QByteArray& token, const QByteArray& nonce, quint64 user);
QByteArray SessionKey(const QByteArray& token, const QByteArray& nonce, quint64 user);
QByteArray Frame(const QByteArray& payload);
// Validates the existing gameplay payload, never a legacy sender/session envelope.
bool ValidInner(const QByteArray& inner);
bool Allowed(const QByteArray& inner, bool senderOwner, bool recipientOwner);
bool PortFromEnvironment(quint16& port);
bool ParsePort(const QByteArray& value, quint16& port);
bool ValidPlacement(const QByteArray& text);
}

// All sockets, timers and database access stay on the owning Qt event thread.
// No blocking socket calls; native state is consulted under its existing locks.
class AuthenticatedRelay : public QObject {
public:
    explicit AuthenticatedRelay(SharedState* shared, QObject* parent = nullptr);
    ~AuthenticatedRelay() override;
    bool Start(ConfigManager* config, const QString& dbPath);
    // Port zero is only for an explicit ephemeral test listener, never the env.
    bool Listen(const QHostAddress& address, quint16 port, const QString& dbPath);
    quint16 Port() const;
    void Stop();
private:
    struct Impl;
    std::unique_ptr<Impl> m;
};
