// The Hunt private relay v2. This is the protocol used by the real mod transport.
#pragma once
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <string>
#include <vector>
#include "bbsc_visibility_wire.h"

namespace TheHuntRelayAuth {
using Bytes = std::vector<std::uint8_t>;
using Digest = std::array<std::uint8_t, 32>;
constexpr std::size_t MaxFrame = 65536;
inline void Wipe(std::span<std::uint8_t> bytes) {
    if (!bytes.empty()) SecureZeroMemory(bytes.data(), bytes.size());
}
inline bool Hmac(std::span<const std::uint8_t> key, std::span<const std::uint8_t> data,
                 Digest& out) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr,
                                    BCRYPT_ALG_HANDLE_HMAC_FLAG) < 0) return false;
    const bool ok = BCryptHash(algorithm, const_cast<PUCHAR>(key.data()),
        static_cast<ULONG>(key.size()), const_cast<PUCHAR>(data.data()),
        static_cast<ULONG>(data.size()), out.data(), static_cast<ULONG>(out.size())) >= 0;
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}
inline bool Equal(std::span<const std::uint8_t> a, std::span<const std::uint8_t> b) {
    if (a.size() != b.size()) return false;
    volatile std::uint8_t difference = 0;
    for (std::size_t i = 0; i < a.size(); ++i) difference = difference | (a[i] ^ b[i]);
    return difference == 0;
}
inline std::uint64_t Read(std::span<const std::uint8_t> data, std::size_t at, int count) {
    std::uint64_t result = 0;
    for (int i = 0; i < count; ++i) result |= std::uint64_t(data[at + i]) << (8 * i);
    return result;
}
inline void Put(Bytes& data, std::uint64_t value, int count) {
    for (int i = 0; i < count; ++i) data.push_back(static_cast<std::uint8_t>(value >> (8 * i)));
}
inline bool Tag(std::span<const std::uint8_t> data, const char* tag) {
    return data.size() >= 4 && std::memcmp(data.data(), tag, 4) == 0;
}
inline bool Inner(std::span<const std::uint8_t> p) {
    if (p.size() < 5 || !Tag(p, "SHAD")) return false;
    switch (p[4]) {
    case 0x20: return p.size() == 30 || p.size() == 34;
    case 0x22: return p.size() == 1018;
    case 0x23: return p.size() == 14 && p[9] <= 1;
    case 0x24: return p.size() == 15;
    case 0x25: return p.size() == 11 && p[5] <= 1 && p[6] <= 1;
    case 0x26: return p.size() >= 6 && p[5] != 0 && p.size() == 6u + 4u * p[5];
    case 0x27: return p.size() == 9;
    case 0x28: { TheHuntVisibility::Frame f; return TheHuntVisibility::Decode(p,f); }
    case 0x30: return p.size() > 5 && p.size() <= MaxFrame - 60;
    default: return false;
    }
}
struct Context {
    std::uint64_t room = 0;
    std::uint32_t own = 0, owner = 0;
    std::uint8_t peers = 0;
    bool operator==(const Context&) const = default;
};
struct PendingPlacement {
    std::uint64_t generation = 0, host_room = 0;
    std::uint32_t host = 0, target = 0, ttl_ms = 0;
    std::string text;
    bool ignored = false;
};
enum class Received { Invalid, Context, Data, Placement };
struct Delivery { Context context; std::uint32_t sender = 0; Bytes inner; PendingPlacement placement; };

// Serialized by the mod's send_mutex. No game memory or handlers are called here.
class Client {
public:
    ~Client() { Reset(); }
    Client() = default;
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;
    void Reset() {
        Wipe(key); key = {}; authenticated = false; pending = false;
        tx = rx = 0; context = {}; blocked_room = 0; pending_generation = 0; active_pending = {};
        visibility = false; visibility_context = {};
    }
    // Old connection contexts cannot undo Leave. A new nonce/ack/room context can
    // admit the same room again; loading or legitimately rejoining is not a ban.
    void Leave() { if (context.room) blocked_room = context.room; context = {}; visibility_context = {}; }
    bool Begin(std::uint32_t user, std::span<const std::uint8_t> token,
               std::span<const std::uint8_t> challenge, Bytes& proof, bool display = false) {
        Reset(); proof.clear();
        if (!user || token.empty() || token.size() > 4096 || challenge.size() != 36 ||
            !Tag(challenge, "THC2")) return false;
        own = user;
        visibility = display;
        constexpr char auth_domain[] = "TheHunt relay auth v2";
        constexpr char display_domain[] = "TheHunt relay auth v4 bidirectional visibility";
        constexpr char key_domain[] = "TheHunt relay key v2";
        auto derive = [&](const char* domain, std::size_t length, Digest& out) {
            Bytes data(domain, domain + length); // Includes the terminating NUL.
            data.insert(data.end(), challenge.begin() + 4, challenge.end()); Put(data, own, 8);
            return Hmac(token, data, out);
        };
        Digest mac{};
        if (!(display ? derive(display_domain,sizeof(display_domain),mac) : derive(auth_domain,sizeof(auth_domain),mac)) ||
            !derive(key_domain, sizeof(key_domain), key)) { Reset(); return false; }
        proof = {'T','H','A',static_cast<std::uint8_t>(display?'4':'2')}; Put(proof, own, 8);
        proof.insert(proof.end(), mac.begin(), mac.end()); Wipe(mac);
        pending = true; return true;
    }
    bool Ack(std::span<const std::uint8_t> frame) {
        if (!pending || authenticated || frame.size() != 44 || !Tag(frame, visibility?"THO4":"THO2") ||
            Read(frame, 4, 8) != own || !Verify(frame)) { Reset(); return false; }
        pending = false; authenticated = true; return true;
    }
    bool Send(std::span<const std::uint8_t> inner, Bytes& frame) {
        frame.clear();
        if (!authenticated || !context.room || !Allowed(inner, own) ||
            tx == std::numeric_limits<std::uint64_t>::max()) return false;
        frame = {'T','H','D','2'}; Put(frame, ++tx, 8);
        frame.insert(frame.end(), inner.begin(), inner.end());
        if (!Sign(frame)) { Reset(); frame.clear(); return false; }
        return true;
    }
    Received Receive(std::span<const std::uint8_t> frame, Delivery& out) {
        out = {};
        if (!authenticated || frame.size() > MaxFrame || frame.size() < 44 ||
            !Verify(frame) || rx == std::numeric_limits<std::uint64_t>::max() ||
            Read(frame, 4, 8) != rx + 1) return Received::Invalid;
        if (Tag(frame, "THP2") && frame.size() >= 72 && frame.size() <= 72 + 2048) {
            PendingPlacement placement{Read(frame,12,8), Read(frame,20,8),
                static_cast<std::uint32_t>(Read(frame,28,4)), static_cast<std::uint32_t>(Read(frame,32,4)),
                static_cast<std::uint32_t>(Read(frame,36,4)), {}};
            const auto text = frame.subspan(40, frame.size()-72);
            if (!placement.generation || !placement.host_room ||
                !placement.host || placement.host == own || placement.target != own || placement.ttl_ms > 20000 ||
                (placement.ttl_ms && (context.room || text.empty())) ||
                (!placement.ttl_ms && !text.empty()) ||
                (!text.empty() && (std::memchr(text.data(), 0, text.size()) ||
                  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, reinterpret_cast<const char*>(text.data()),
                                      static_cast<int>(text.size()), nullptr, 0) <= 0)))
                return Received::Invalid;
            placement.text.assign(reinterpret_cast<const char*>(text.data()), text.size());
            if (!placement.ttl_ms) {
                placement.ignored = placement.generation != active_pending.generation;
                if (!placement.ignored) {
                    if (placement.host_room != active_pending.host_room || placement.host != active_pending.host)
                        return Received::Invalid;
                    active_pending = {};
                }
            } else if (placement.generation <= pending_generation) {
                placement.ignored = true; // Delayed older hints cannot replace/extend a newer hint.
            } else {
                pending_generation = placement.generation; active_pending = placement;
            }
            ++rx;
            out.context = context; out.placement = std::move(placement); return Received::Placement;
        }
        const bool control = visibility ? Tag(frame,"THX4") : Tag(frame,"THX2");
        if (control && (visibility ? frame.size()>=62 : frame.size()==61)) {
            Context next{Read(frame,12,8), static_cast<std::uint32_t>(Read(frame,20,4)),
                static_cast<std::uint32_t>(Read(frame,24,4)), frame[28]};
            if (next.own != own || (!next.room && (next.owner || next.peers)) ||
                (next.room && !next.owner)) return Received::Invalid;
            TheHuntVisibility::Context roster{next.room,next.own,next.owner,{}};
            if(visibility) {
                const std::size_t count=frame[29];
                if(count>16 || frame.size()!=62+31*count || (!next.room && count)) return Received::Invalid;
                for(std::size_t i=0;i<count;++i) {
                    const auto at=30+31*i;
                    TheHuntVisibility::Peer p{static_cast<std::uint32_t>(Read(frame,at,4)),
                        static_cast<std::uint16_t>(Read(frame,at+4,2)),Read(frame,at+6,8),{},frame[at+14]!=0};
                    std::memcpy(p.npid.data(),frame.data()+at+15,16);
                    if(frame[at+14]>1 || !TheHuntVisibility::ValidPeer(p)) return Received::Invalid;
                    for(const auto& other:roster.peers)
                        if(other.user==p.user || other.member==p.member || other.npid==p.npid) return Received::Invalid;
                    roster.peers.push_back(p);
                }
                if(next.room && (!TheHuntVisibility::Find(roster,own) || count!=std::size_t(next.peers)+1)) return Received::Invalid;
            }
            ++rx;
            if (!next.room || next.room != blocked_room) blocked_room = 0;
            const auto previous = context;
            context = next.room == blocked_room && blocked_room ? Context{} : next;
            visibility_context = context.room?std::move(roster):TheHuntVisibility::Context{};
            // Native membership can arrive before the game's summoned reload.
            // It only hands off the existing hint to its exact room and host.
            if ((previous.room && (previous.room != context.room || previous.owner != context.owner)) ||
                (context.room && (context.room != active_pending.host_room ||
                                  context.owner != active_pending.host))) active_pending = {};
            out.context = context;
            out.placement.generation = active_pending.generation;
            return Received::Context;
        }
        if (!Tag(frame, "THS2") || frame.size() < 65 || !context.room ||
            Read(frame,12,8) != context.room || Read(frame,24,4) != context.owner)
            return Received::Invalid;
        const auto sender = static_cast<std::uint32_t>(Read(frame,20,4));
        const auto inner = frame.subspan(28, frame.size() - 60);
        if (!sender || sender == own || !Allowed(inner, sender)) return Received::Invalid;
        ++rx; out.context = context; out.sender = sender;
        out.inner.assign(inner.begin(), inner.end()); return Received::Data;
    }
    Context Current() const { return context; }
    bool Ready() const { return authenticated; }
    bool Visibility() const { return authenticated && visibility; }
    TheHuntVisibility::Context VisibilityContext() const { return visibility_context; }
    // Like Current(), access only while the transport holds its send mutex.
    const PendingPlacement* Pending() const {
        return authenticated && active_pending.generation ? &active_pending : nullptr;
    }
private:
    bool Allowed(std::span<const std::uint8_t> inner, std::uint32_t sender) const {
        if (!Inner(inner)) return false;
        if(inner[4]==0x28) {
            TheHuntVisibility::Frame f;
            return visibility && TheHuntVisibility::Decode(inner,f) &&
                TheHuntVisibility::Allowed(visibility_context,f,sender,sender==own?f.target:own);
        }
        if (inner[4] == 0x23) return Read(inner, 5, 4) == sender;
        if (inner[4] == 0x25 && inner[5] == 1)
            return sender != context.owner && (sender == own || own == context.owner);
        return sender == context.owner;
    }
    bool Verify(std::span<const std::uint8_t> frame) const {
        if (frame.size() < 32) return false;
        Digest mac{};
        const bool ok = Hmac(key, frame.first(frame.size()-32), mac) &&
                        Equal(mac, frame.last(32));
        Wipe(mac); return ok;
    }
    bool Sign(Bytes& frame) const {
        Digest mac{}; if (!Hmac(key, frame, mac)) return false;
        frame.insert(frame.end(), mac.begin(), mac.end()); Wipe(mac); return true;
    }
    Digest key{};
    std::uint32_t own = 0;
    std::uint64_t tx = 0, rx = 0, blocked_room = 0, pending_generation = 0;
    bool pending = false, authenticated = false;
    bool visibility = false;
    Context context{};
    TheHuntVisibility::Context visibility_context{};
    PendingPlacement active_pending{};
};
} // namespace TheHuntRelayAuth
