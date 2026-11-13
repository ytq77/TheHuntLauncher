// SPDX-License-Identifier: GPL-2.0-or-later
// Test-only transport. Never linked into shadnet-admin; contains no socket calls.
#include <cstdlib>
#include <cstring>
#include <string>
#include <QByteArray>
#include "connection.h"
#include "shadnet.pb.h"

using namespace shadnetclient;

namespace {
std::string Expected(const char* key) {
    const char* value = std::getenv(key);
    return value ? value : "";
}
std::string ExpectedField(const char* key) {
    return QByteArray::fromHex(QByteArray::fromStdString(Expected(key))).toStdString();
}
void Check(bool valid) {
    if (!valid)
        std::exit(90); // No credential-bearing assertion or payload output.
}
}

ShadNetConnection::ShadNetConnection() = default;
ShadNetConnection::~ShadNetConnection() = default;

bool ShadNetConnection::connect(const char* host, uint16_t port) {
    Check(std::string(host) == "100.64.0.10" && port == 31413);
    return Expected("ADMIN_TEST_RESULT") != "connect-failed";
}
void ShadNetConnection::disconnect() {}
bool ShadNetConnection::isConnected() const { return true; }

bool ShadNetConnection::send(const std::vector<uint8_t>& data) {
    Check(data.size() >= HEADER_SIZE + 4 && data[0] == static_cast<uint8_t>(PacketType::Request));
    const auto command = fromLE16(data.data() + 1);
    const auto length = fromLE32(data.data() + HEADER_SIZE);
    Check(length == data.size() - HEADER_SIZE - 4);
    const char* body = reinterpret_cast<const char*>(data.data() + HEADER_SIZE + 4);
    if (command == static_cast<uint16_t>(CommandType::Create)) {
        Check(Expected("ADMIN_TEST_ACTION") == "register");
        shadnet::RegistrationRequest request;
        Check(request.ParseFromArray(body, static_cast<int>(length)));
        Check(request.npid() == ExpectedField("ADMIN_TEST_NAME"));
        Check(request.password() == ExpectedField("ADMIN_TEST_PASSWORD"));
        Check(request.email() == ExpectedField("ADMIN_TEST_EMAIL"));
        Check(request.secret_key() == ExpectedField("ADMIN_TEST_KEY"));
        Check(request.avatar_url().empty());
    } else {
        Check(command == static_cast<uint16_t>(CommandType::Login) && Expected("ADMIN_TEST_ACTION") == "login");
        shadnet::LoginRequest request;
        Check(request.ParseFromArray(body, static_cast<int>(length)));
        Check(request.npid() == ExpectedField("ADMIN_TEST_NAME"));
        Check(request.password() == ExpectedField("ADMIN_TEST_PASSWORD"));
        Check(request.token().empty());
    }
    m_readBuf = data;
    return true;
}

void ShadNetConnection::update() {
    if (m_readBuf.empty())
        return;
    Packet packet{};
    packet.type = PacketType::Reply;
    packet.command = fromLE16(m_readBuf.data() + 1);
    const bool login = packet.command == static_cast<uint16_t>(CommandType::Login);
    const bool success = Expected("ADMIN_TEST_RESULT") != "rejected";
    packet.payload.push_back(static_cast<uint8_t>(success ? ErrorType::NoError :
        (login ? ErrorType::LoginInvalidPassword : ErrorType::Unauthorized)));
    if (success && login) {
        shadnet::LoginReply reply;
        reply.set_user_id(42);
        const std::string encoded = reply.SerializeAsString();
        const uint32_t size = static_cast<uint32_t>(encoded.size());
        const auto begin = reinterpret_cast<const uint8_t*>(&size);
        packet.payload.insert(packet.payload.end(), begin, begin + 4);
        packet.payload.insert(packet.payload.end(), encoded.begin(), encoded.end());
    }
    m_readBuf.clear();
    Check(static_cast<bool>(onPacket));
    onPacket(packet);
}
