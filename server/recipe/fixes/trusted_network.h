// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QHostAddress>

// Admission guard only. Private/VPN address space is NOT player authentication.
// Firewall/VPN ACLs must admit only one group of trusted player devices; do not
// port-forward or proxy these plaintext protocols to an untrusted network.
namespace TrustedNetwork {
inline bool Allows(const QHostAddress& address) {
    bool ok = false;
    const quint32 ip = address.toIPv4Address(&ok);
    return ok && ((ip >> 24) == 127 || (ip >> 24) == 10 ||
                  (ip >> 20) == 0xac1 || (ip >> 16) == 0xc0a8 ||
                  (ip >> 22) == 0x191 || (ip >> 24) == 26); // Radmin VPN 26/8 only.
}
}
