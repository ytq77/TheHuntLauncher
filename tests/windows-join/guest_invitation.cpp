// All events run in memory on the upstream client's test connection.
#include <winsock2.h>
#include <windows.h>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <optional>

static ULONGLONG tick = 100000;
#define GetTickCount64() (tick)
#define main upstream_test_main
#include "fixture.inc"
#undef main
#undef GetTickCount64
#undef LOG_INFO
#define LOG_INFO(...) ((void)0)

namespace Core::Bloodborne {
#include "parse.inc"
std::mutex seamless_placement_mutex;
std::optional<SummonPlacementDescriptor> bbsc_connect_first_placement, seamless_received_host_placement;
bool seamless_received_host_placement_consumed = false;
std::atomic<u32> bbsc_tick_map{0x17000000}, bbsc_connect_first_map{0};
std::atomic<s64> bbsc_connect_first_since_ms{0};
bool BbscConnectFirstEnabled() { return true; }
s64 SteadyNowMs() { return tick; }
#include "cache.inc"
// Cross-map first spawn exits at host-map, before any native-memory access.
bool ReadablePointer(u64, size_t) { std::abort(); }
template<class T> T ReadValue(u64, size_t) { std::abort(); }
#include "spawn.inc"
}
namespace BbscMod {
std::atomic<s64> placement_received_ms{0};
std::atomic<u32> placement_sender{0};
s64 NowMs() { return tick; }
s32 SessionState() { return native_state; }
#include "http.inc"
}

static u64 sequence;
static void membership(u64 room) {
    Bytes f{'T','H','X','4'};
    Put(f,++sequence,8); Put(f,room,8); Put(f,17,4); Put(f,room ? 18 : 0,4);
    f.push_back(room ? 1 : 0); f.push_back(room ? 2 : 0);
    if (room) for (u32 id : {17u,18u}) {
        Put(f,id,4); Put(f,id,2); Put(f,123,8); f.push_back(1);
        f.push_back(static_cast<u8>('a'+id-17)); f.resize(f.size()+15,0);
    }
    Delivery d;
    assert(auth_client.Receive(signed_frame(f),d) == Received::Context);
    auth_context_ms = tick;
    ApplyAuthContext(d);
}
static void invite(u64 generation, u64 room, u32 ttl, const std::string& text, bool ignored = false) {
    Bytes f{'T','H','P','2'};
    Put(f,++sequence,8); Put(f,generation,8); Put(f,room,8);
    Put(f,18,4); Put(f,17,4); Put(f,ttl,4); f.insert(f.end(),text.begin(),text.end());
    Delivery d;
    assert(auth_client.Receive(signed_frame(f),d) == Received::Placement && d.placement.ignored == ignored);
    ApplyAuthPlacement(d.placement);
}
static void start() {
    ClearAuthPlacement(); auth_client.Reset(); authenticated_room=0; authenticated_owner=0;
    native_state=0; own_id=17; private_auth=true; sequence=0;
    Client fixture; establish(fixture); // Existing fixture key for its fixed test nonce and token.
    Bytes challenge{'T','H','C','2'}, proof, ack{'T','H','O','4'};
    challenge.resize(36,0x6e); Put(ack,17,8);
    assert(auth_client.Begin(17,token,challenge,proof,true) && auth_client.Ack(signed_frame(ack)));
    membership(0);
}

enum class Timing { BeforeArm, AfterArm, Joining };
enum class Retry { NoRoom36, ReplaceLive, KeepLive, ReissueExpired };

static void replay(Timing timing, Retry retry, const std::string& pose35,
                   const std::string& pose36, const std::string& retryPose, bool expired35 = false) {
    using namespace Core::Bloodborne;
    start();
    invite(44,35,9939,pose35);
    const auto oldEpoch = BbscMod::RelayAuthArmFirstSpawn();
    const auto oldHint = BbscConnectFirstHost();
    const auto oldPose = ParseBbscSummonPlacement(pose35);
    assert(oldEpoch && oldHint && oldPose && IsSameSummonPlacement(*oldHint,*oldPose));
    tick += expired35 ? 9938 : 500;
    membership(0); ExpireAuthPlacement();
    assert(BbscMod::RelayAuthArmFirstSpawn() == oldEpoch);
    if (expired35) {
        ++tick; ExpireAuthPlacement();
        assert(!BbscMod::RelayAuthArmFirstSpawn() && !BbscConnectFirstHost());
    }
    invite(44,35,0,{});
    assert(!BbscMod::RelayAuthArmFirstSpawn() && !BbscConnectFirstHost());
    invite(44,35,10000,pose35,true);
    assert(!BbscMod::RelayAuthArmFirstSpawn() && !BbscConnectFirstHost());

    struct Observation {
        std::vector<std::string> received;
        BbscFirstSpawnGrant native{0, bbsc_tick_map.load()};
    } observed;
    auto take = [&](u64 epoch) {
        return BbscMod::RelayAuthUseFirstSpawn(epoch, [](std::string_view value, void* p) {
            auto& out = *static_cast<Observation*>(p);
            out.received.emplace_back(value);
            return TakeBbscFirstSpawnGrant(value,&out.native);
        }, &observed);
    };
    if (retry == Retry::NoRoom36) {
        membership(36);
        assert(!take(oldEpoch) && !BbscMod::RelayAuthArmFirstSpawn() && !BbscConnectFirstHost());
        assert(observed.received.empty());
        std::puts("PASS no room 36 invitation: 0 first-spawn calls, no hint");
        return;
    }

    invite(45,36,10000,pose36);
    const auto initialHint = BbscConnectFirstHost();
    const auto wanted = ParseBbscSummonPlacement(pose36);
    assert(initialHint && wanted && IsSameSummonPlacement(*initialHint,*wanted));
    const auto originalEpoch = first_spawn_epoch;
    const auto originalDeadline = pending_placement_expires;
    u64 armed = 0;
    if (timing != Timing::BeforeArm) {
        armed = BbscMod::RelayAuthArmFirstSpawn();
        assert(armed == originalEpoch && armed != oldEpoch && !take(armed) && observed.received.empty());
    }
    if (timing == Timing::Joining) native_state = 4;

    if (retry == Retry::ReissueExpired) {
        tick = originalDeadline - 1;
        membership(0); ExpireAuthPlacement();
        assert(pending_placement_generation == 45 && BbscConnectFirstHost());
        ++tick; ExpireAuthPlacement();
        assert(!pending_placement_generation && !BbscConnectFirstHost());
        invite(45,36,0,{}); // Expiry notification, before the new request.
    } else {
        tick += 100;
    }
    const auto beforeRequest = sequence;
    if (retry == Retry::ReplaceLive) invite(45,36,0,{});
    if (retry != Retry::KeepLive) {
        assert(!BbscConnectFirstHost());
        invite(46,36,10000,retryPose);
        assert(first_spawn_epoch != originalEpoch && pending_placement_expires > originalDeadline);
    } else {
        // No THP2 is delivered for this HTTP retry. Preserve the original expiry and arm.
        assert(first_spawn_epoch == originalEpoch && pending_placement_expires == originalDeadline);
    }
    const auto requestFrames = sequence - beforeRequest;
    assert(requestFrames == (retry == Retry::KeepLive ? 0 : retry == Retry::ReplaceLive ? 2 : 1));
    assert(pending_placement_generation == (retry == Retry::KeepLive ? 45 : 46));
    const auto deadline = pending_placement_expires, latestEpoch = first_spawn_epoch;
    invite(44,35,10000,pose35,true); invite(44,35,0,{},true);
    assert(pending_placement_expires == deadline && first_spawn_epoch == latestEpoch);
    if (timing == Timing::BeforeArm) {
        armed = BbscMod::RelayAuthArmFirstSpawn();
        assert(armed == latestEpoch && armed);
    }
    membership(36); native_state=6;
    assert(BbscMod::RelayAuthRoom() == 36 && BbscMod::RelayAuthOwner() == 18);
    invite(44,35,0,{},true);
    assert(!take(oldEpoch) && observed.received.empty());
    const bool consumed = retry == Retry::KeepLive || timing == Timing::BeforeArm;
    assert(!take(armed)); // The actual cross-map callback returns host-map when reached.
    assert(observed.received.size() == static_cast<size_t>(consumed));
    invite(44,35,0,{},true);
    membership(36);
    assert(!take(armed));
    if (consumed) {
        assert(observed.received == std::vector<std::string>{pose36});
        assert(observed.native.skipped == "host-map" && !first_spawn_available);
    } else {
        assert(observed.received.empty() && observed.native.skipped == "no-matching-grant");
    }
    const auto hint = BbscConnectFirstHost();
    assert(bool(hint) == (retry == Retry::KeepLive || timing != Timing::Joining));
    if (hint) assert(IsSameSummonPlacement(*hint,*wanted));
    std::printf("PASS 35 %s -> 36 / %s / %s: retry THP2=%llu, first-spawn calls=%zu, callback=%s, hint=%s\n",
                expired35 ? "expired" : "revoked",
                retry == Retry::KeepLive ? "live silent retry" : retry == Retry::ReplaceLive ? "old live replacement" : "expired reissue",
                timing == Timing::BeforeArm ? "before arm" : timing == Timing::AfterArm ? "after arm" : "during join",
                static_cast<unsigned long long>(requestFrames), observed.received.size(),
                consumed ? "host-map" : "not entered", hint ? "36" : "none");
}

int main(int argc, char** argv) {
    assert(argc == 4 && std::string(argv[1]) != argv[2] && std::string(argv[2]) == argv[3]);
    replay(Timing::BeforeArm,Retry::NoRoom36,argv[1],argv[2],argv[3],true);
    replay(Timing::AfterArm,Retry::KeepLive,argv[1],argv[2],argv[3],true);
    for (auto retry : {Retry::ReplaceLive, Retry::KeepLive, Retry::ReissueExpired})
        for (auto timing : {Timing::BeforeArm, Timing::AfterArm, Timing::Joining})
            replay(timing,retry,argv[1],argv[2],argv[3]);
}
