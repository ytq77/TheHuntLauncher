// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
namespace Core::Net::P2P {
// Diagnostic metadata only. It never changes whether a frame is accepted.
enum class DecodeFailure { None, Malformed, NoKey, BadSignature, CommunicationId, Cipher, Count };
}
