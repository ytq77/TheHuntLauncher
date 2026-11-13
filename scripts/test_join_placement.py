"""Run the actual join-position tick with a synthetic game and native warp callbacks."""
import argparse
from pathlib import Path
import subprocess

from prepare_game_hardening_regression import function

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r"""
#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <cassert>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <mutex>
#include <optional>
#include <string_view>
#include <type_traits>
using u8 = std::uint8_t; using u32 = std::uint32_t; using u64 = std::uint64_t;
using s32 = std::int32_t; using s64 = std::int64_t;
using namespace std;
constexpr s64 BbscJoinTeleportSettleMs = 2000, BbscFollowStaleMs = 15000;
constexpr s64 BbscFollowAfterLoadMs = 5000, BbscFollowMismatchMs = 2500, BbscFollowArriveTimeoutMs = 90000;
constexpr u32 yharnam = 0x18010000, dream = 0x15000000, ward = 0x18000000;
constexpr u32 BbscDreamStateMap = dream;
constexpr u64 player = 100, host = 200, global = 300, table = 400, insert_object = 500, descriptor = 600;
u32 local_map = yharnam;
bool private_session = true, host_visible = true, bbsc_join_teleport_done = false;
bool game_loading = false, own_world = false, recovery_enabled = true, own_baseline = true, forced = false;
bool world_ready = true, dream_ready = true, native_reject = false;
int moves = 0, reloads = 0, writes = 0, covers = 0, notices = 0, preparation = 0;
s32 session_state = 6, role = 1, hp = 100;
s64 now = 20000;
u32 owner = 4, own = 5;
u64 room = 7;
struct BbscFollowMessage {
    u32 packed_map{};
    s64 received_ms{}, arrival_ms{};
    u32 arrival_map{};
    bool arrival_by_load{};
    float x{12}, y{40}, z{-5}, heading{3};
} bbsc_follow_latest;
std::mutex bbsc_follow_mutex;
std::atomic<bool> bbsc_cross_map_join{false};
std::atomic<u64> bbsc_character_reload_generation{10};
std::array<u8, 0x1649> native_memory{};
std::array<u8, 0xA0> descriptor_memory{};
bool spawn_fixture = false, spawn_writable = true, matched_grant = true;
int global_writes = 0, descriptor_writes = 0;
u32 insert_mode = 0;
u8 insert_flags = 0;
unsigned spawn_uses = 0;
string_view signed_placement = "1,18010000,41400000,42200000,c0a00000,40400000,230010";
namespace BbscMod {
bool RelayAuthPrivate() { return private_session; }
u32 RelayAuthOwner() { return owner; }
u32 RelayAuthOwnId() { return own; }
u64 RelayAuthRoom() { return room; }
u64 RelayAuthArmFirstSpawn() { return 11; }
bool RelayAuthUseFirstSpawn(u64 epoch, bool (*write)(string_view, void*), void* data) {
    assert(epoch == 11);
    ++spawn_uses;
    return matched_grant && write(signed_placement, data);
}
}
struct SummonPlacementDescriptor { u32 packed_region{}; float x{}, y{}, z{}, heading{}; s32 area{}; };
struct BbscTransform { std::array<float, 4> position{12, 40, -5, 1}, rotation{0, 3, 0, 0}; } own_transform;
struct BbscFollowSpot { float x, y, z, heading; };
BbscFollowSpot BbscFollowSpotOf(const BbscFollowMessage& m) { return {m.x, m.y, m.z, m.heading}; }
#define PS4_SYSV_ABI
#define LOG_INFO(...)
#define LOG_WARNING(...)
s64 SteadyNowMs() { return now; }
u32 GetCurrentPackedMap() { return local_map; }
bool IsUsablePackedMap(u32 map) { return map && map != UINT32_MAX; }
s32 GetSessionState() { return session_state; }
u64 GetGlobalState() { return global; }
bool ReadablePointer(u64 ptr, size_t) { return ptr != 0; }
u64 GetLocalPlayerCharacter() { return player; }
int GetLocalPlayerType() { return role; }
u64 FindRemoteHostCharacter() { return host_visible ? host : 0; }
u64 FindLivingPartnerCharacter() { return host; }
int GetCharacterHp(u64 c) { return c == player ? hp : 100; }
std::optional<BbscTransform> ReadCharacterTransform(u64 chr) {
    return chr == player ? own_transform : BbscTransform{};
}
bool BbscWarpJoinEnabled() { return true; }
u64 BbscLoadProtectionTicket() { return 0; }
void MarkBbscLoadProtectionPlaced(u64, u32) {}
bool RequestBbscSessionWarp(u64, const SummonPlacementDescriptor& to, string_view why,
                            bool (*prepare)(u32, std::string_view) = nullptr) {
    assert(to.x == 13);
    ++reloads;
    if (!prepare) { assert(to.packed_region == yharnam && why == "join"); return true; }
    assert(to.packed_region == dream && why == "join-recover" && moves == 0);
    return !native_reject && prepare(to.packed_region, why);
}
u32 BbscWorldGroupKey(u32 map, size_t) { return map == dream || map == yharnam ? 8 : 0; }
u64 BbscFlagGroupStorage(u32 key) { return key; }
bool BbscWorldSyncEnabled() { return true; }
bool BbscWorldRecoveryEnabled() { return recovery_enabled; }
bool BbscOwnWorldReadyForGuest() { return own_baseline; }
bool BbscOwnWorldCaptureStillCurrent() { return own_baseline; }
bool BbscWorldRecoveryJournalFrozen() { return false; }
bool BbscWorldRecoveryWaiting() { return false; }
void WriteBbscOwnWorldFile() {}
bool BbscHostWorldFlagsReady(u32) { return world_ready; }
bool BbscDreamEnabled() { return true; }
bool BbscHostDreamStateReady() { return dream_ready; }
void ApplyBbscHostWorldFlags(u32 map, string_view) { assert(map == dream); ++preparation; }
void ApplyBbscGuestLootView(u32 map, string_view) { assert(map == dream && preparation == 1); ++preparation; }
void ApplyBbscHostDreamState(string_view) { assert(preparation == 2); ++preparation; }
void RestoreBbscOwnDreamState(string_view) { assert(false); }
void LogBbscHostWorldFlagsDiff(u32, string_view) {}
void EndBbscLoadCover(string_view) { ++covers; }
void BbscAnnounce(const char16_t*, string_view) { ++notices; }
void NativeWarp(u64 chr, float* pos, float*, u32 flag) {
    assert(chr == player && pos[0] == 13 && flag == 1); ++moves;
}
template<class T> T ReadValue(u64 ptr, size_t off) {
    if constexpr (std::is_arithmetic_v<T>) {
        if (ptr == global && off == 0x08) return static_cast<T>(game_loading);
        if (ptr == global && off == 0x1520) return static_cast<T>(forced);
        if (ptr == global && off == 0x1592) return static_cast<T>(own_world);
    }
    if (spawn_fixture && ptr == global && off + sizeof(T) <= native_memory.size()) {
        T out{};
        std::memcpy(&out, native_memory.data() + off, sizeof(T));
        return out;
    }
    if (spawn_fixture && ptr == descriptor && off + sizeof(T) <= descriptor_memory.size()) {
        T out{};
        std::memcpy(&out, descriptor_memory.data() + off, sizeof(T));
        return out;
    }
    if constexpr (std::is_arithmetic_v<T>) {
        if (ptr == insert_object && off == 0xE8) return static_cast<T>(insert_mode);
        if (ptr == insert_object && off == 0xED) return static_cast<T>(insert_flags);
        if (ptr == player && !off) return static_cast<T>(table);
        if (ptr == table && off == 0x5F8) return static_cast<T>(reinterpret_cast<u64>(&NativeWarp));
    }
    return T{};
}
template<class T> bool WriteValue(u64 ptr, size_t off, T value) {
    if (spawn_fixture && ptr == global) ++global_writes;
    if constexpr (std::is_same_v<T, std::array<float, 8>>) {
        assert(spawn_fixture && ptr == descriptor && off == 0x60);
        if (!spawn_writable) return false;
        std::memcpy(descriptor_memory.data() + off, &value, sizeof(value));
        ++descriptor_writes;
    }
    ++writes;
    return true;
}
bool IsEbootCodeAddress(u64 addr) { return addr == reinterpret_cast<u64>(&NativeWarp); }
"""

MAIN = r"""
void step(s64 elapsed = 0, bool refresh = true) {
    now += elapsed;
    if (refresh && bbsc_follow_latest.received_ms) bbsc_follow_latest.received_ms = now;
    TickBbscJoinTeleport();
}

// Native model of a same-map summon, from eboot 1.09 and the 2026-11-07 01:51 COMP run:
//  - 0x01E4FEE2: our hook, then StartNotifyWait copies +0x1620/+0x1630/+0x1644 into the
//    summoned bank +0x14D0/+0x14E0/+0x14C4 (64 times in that run);
//  - 0x0194132D: while ready and +0x1640 equals the player's area, the stage update writes
//    the local player's own pose into +0x1620/+0x1630;
//  - 0x01944D00: the 2/1 spawn descriptor copies the summoned bank and joins 0x01944F23.
const std::array<float, 8> bell_pose{-13, 40, -5, 1, 0, 1, 0, 0};   // the guest's own bell spot
const std::array<float, 8> host_pose{12, 40, -5, 1, 0, 3, 0, 0};    // the signed placement
s32 player_area = 230010;
void native_copy() {
    TryBbscFirstSpawnBeforeCopy(insert_object, global);
    std::memcpy(native_memory.data() + 0x14D0, native_memory.data() + 0x1620, 0x20);
    std::memcpy(native_memory.data() + 0x14C4, native_memory.data() + 0x1644, 4);
}
void native_refresh() {
    s32 record_area{};
    std::memcpy(&record_area, native_memory.data() + 0x1640, 4);
    if (native_memory[0x1648] && record_area == player_area)
        std::memcpy(native_memory.data() + 0x1620, bell_pose.data(), 0x20);
}
void native_finalize(s32 type = 2, s32 subtype = 1, bool from_bank = true) {
    std::memcpy(descriptor_memory.data() + 0x08, native_memory.data() + 0x14C4, 4);
    std::memcpy(descriptor_memory.data() + 0x54, &type, 4);
    std::memcpy(descriptor_memory.data() + 0x58, &subtype, 4);
    if (from_bank) std::memcpy(descriptor_memory.data() + 0x60, native_memory.data() + 0x14D0, 0x20);
    ApplyBbscFirstSpawnAtDescriptor(descriptor, global);
}
std::array<float, 8> spawn_pose() {
    std::array<float, 8> out{};
    std::memcpy(out.data(), descriptor_memory.data() + 0x60, 0x20);
    return out;
}

void first_spawn_case(string_view mode) {
    spawn_fixture = true;
    own_world = true;
    session_state = 0;
    role = 0; // The native player slot is still local at the first copy.
    native_memory.fill(0xA5);
    descriptor_memory.fill(0x5A);
    const s32 area = mode == "first-different-area" ? 230080 : 230010;
    player_area = area; // the guest stands in the record's area, so the refresh is live
    const u32 map = mode == "first-native-map" ? dream : yharnam;
    std::memcpy(native_memory.data() + 0x1620, bell_pose.data(), 0x20);
    std::memcpy(native_memory.data() + 0x1640, &area, sizeof(area));
    std::memcpy(native_memory.data() + 0x1644, &map, sizeof(map));
    native_memory[0x1648] = mode == "first-unready" ? 0 : 1;
    if (mode == "first-baseline") own_baseline = false;
    if (mode == "first-public") private_session = false;
    ArmBbscFirstSpawn();
    session_state = mode == "first-established" || mode == "first-after-native" ? 6 : 4;
    if (mode == "first-failed") session_state = 5;
    if (mode == "first-after-native") CloseBbscFirstSpawnOnNativeReload();
    if (mode == "first-reload") ++bbsc_character_reload_generation;
    if (mode == "first-loading") game_loading = true;
    if (mode == "first-forced") forced = true;
    if (mode == "first-remote") insert_mode = 1;
    if (mode == "first-skip") insert_flags = 0x20;
    if (mode == "first-invader") role = 2;
    if (mode == "first-context") matched_grant = false;
    if (mode == "first-readonly") spawn_writable = false;
    if (mode == "first-host-map") signed_placement = "1,15000000,41400000,42200000,c0a00000,40400000,230010";
    if (mode == "first-nan") signed_placement = "1,18010000,7fc00000,42200000,c0a00000,40400000,230010";
    const auto untouched = native_memory;

    // StartNotifyWait and the stage update interleave across the native reload entry.
    for (int copy = 0; copy < 64; ++copy) {
        native_copy();
        native_refresh();
        if (copy == 1) CloseBbscFirstSpawnOnNativeReload();
    }
    game_loading = true; // the summon load itself; the descriptor path must not depend on it
    session_state = mode == "first-desc-session" ? 5 : mode == "first-failed" ? 5 : 6;
    if (mode == "first-desc-relay") ++room;
    if (mode == "first-desc-reloaded") {
        ++bbsc_character_reload_generation; // an unrelated load finished first
        FinishBbscFirstSpawnAtReload();
    }
    if (mode == "first-desc-map") std::memcpy(native_memory.data() + 0x14C4, &dream, 4);
    if (mode == "first-desc-type") native_finalize(4, 1);           // a respawn, not this summon
    else if (mode == "first-desc-source") {                         // not copied from the bank
        std::memcpy(descriptor_memory.data() + 0x60, untouched.data() + 0x14D0, 0x20);
        native_finalize(2, 1, false);
    } else {
        native_finalize(); // 0x01944F23 is reached twice for the same descriptor
        native_finalize();
    }
    // The mod never writes the global placement block; only the native model does.
    assert(global_writes == 0);
    const bool applied = mode == "first-valid" || mode == "first-far" ||
                         mode == "first-established" || mode == "first-different-area";
    if (applied) assert(descriptor_writes == 2 && spawn_pose() == host_pose);
    else assert(descriptor_writes == 0);
    // Modes refused before the relay grant is touched; every other mode takes it exactly once.
    const bool grant_untouched = mode == "first-public" || mode == "first-baseline" ||
        mode == "first-after-native" || mode == "first-remote" || mode == "first-skip" ||
        mode == "first-failed" || mode == "first-reload" || mode == "first-invader";
    assert(spawn_uses == (grant_untouched ? 0u : 1u));
    if (mode == "first-desc-source" || mode == "first-desc-relay") return; // checked at the descriptor
    assert(spawn_pose() == (applied ? host_pose : bell_pose));

    // The load spawns the guest at the descriptor; the reload finishes.
    const auto spawned = spawn_pose();
    ++bbsc_character_reload_generation;
    FinishBbscFirstSpawnAtReload();
    native_finalize(); // a later 2/1 load must never be changed
    assert(descriptor_writes == (applied ? 2 : 0) && spawn_pose() == bell_pose);
    own_transform.position = {spawned[0], spawned[1], spawned[2], 1};
    if (mode == "first-far") own_transform.position[0] -= 25; // the game put the guest elsewhere
    game_loading = false;
    own_world = false;
    session_state = 6;
    role = 1;
    step(); step(2000);
    assert(bbsc_join_teleport_done && covers == 1 && !reloads);
    assert(moves == (applied && mode != "first-far" ? 0 : 1));
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const string_view mode = argv[1];
    bbsc_follow_latest = {yharnam, now};
    if (mode.starts_with("first-")) { first_spawn_case(mode); return 0; }
    if (mode.starts_with("dream-")) {
        local_map = bbsc_follow_latest.packed_map = dream;
        own_transform.position[2] += mode == "dream-far" ? 4.0f : 2.35f;
        own_transform.position[1] += mode == "dream-height" ? 1.0f : 0.59f;
        step(); step(2000);
        assert(bbsc_join_teleport_done && covers == 1 && !reloads);
        assert(moves == (mode != "dream-near"));
        return 0;
    }
    const bool wrong = mode == "recover" || mode == "recover-no-actor" || mode == "selector-rejected" ||
        mode == "timed-out" || mode == "owner-moved" || mode == "room-changed" ||
        mode == "missing-world" || mode == "missing-dream" || mode == "no-baseline" ||
        mode == "recovery-disabled" || mode == "not-finite" || mode == "invader" ||
        mode == "wrong-without-map" || mode == "dead" || mode == "loading" || mode == "returned" ||
        mode == "forced-pending";
    if (wrong) bbsc_follow_latest.packed_map = dream;
    if (mode == "recover" || mode == "same-map-reload") bbsc_cross_map_join = true;
    if (mode == "missing") bbsc_follow_latest = {};
    if (mode == "expired") bbsc_follow_latest.received_ms = now - BbscFollowStaleMs;
    if (mode == "wrong-without-map") local_map = 0;
    if (mode == "no-owner") owner = 0;
    if (mode == "not-guest") owner = own;
    if (mode == "other-guest" || mode == "recover-no-actor") host_visible = false;
    if (mode == "public") { private_session = false; bbsc_follow_latest = {}; }
    if (mode == "selector-rejected") native_reject = true;
    if (mode == "missing-world") world_ready = false;
    if (mode == "missing-dream") dream_ready = false;
    if (mode == "no-baseline") own_baseline = false;
    if (mode == "recovery-disabled") recovery_enabled = false;
    if (mode == "not-finite") bbsc_follow_latest.x = std::numeric_limits<float>::quiet_NaN();
    if (mode == "invader") role = 2;
    if (mode == "dead") hp = 0;
    if (mode == "loading") game_loading = true;
    if (mode == "forced-pending") forced = true;
    const bool refresh = mode != "expired";
    step(0, refresh);
    step(2000, refresh);
    step(2250, refresh);
    step(2000, refresh);
    if (mode == "same-map" || mode == "public" || mode == "same-map-reload") {
        assert(moves == 1 && writes == 1 && bbsc_join_teleport_done);
        assert(reloads == (mode == "same-map-reload") && covers == 1);
        return 0;
    }
    assert(!moves && !writes);
    if (mode == "recover" || mode == "recover-no-actor" || mode == "owner-moved" ||
        mode == "room-changed" || mode == "timed-out" || mode == "returned") {
        assert(reloads == 1 && preparation == 3 && !bbsc_join_teleport_done);
        if (mode == "returned") {
            session_state = 0; own_world = true; room = 0;
            step(1000);
            assert(!bbsc_private_join.room && !bbsc_private_join.requested_ms && !moves);
            return 0;
        }
        if (mode == "timed-out") {
            step(BbscFollowArriveTimeoutMs + 250);
            assert(reloads == 1 && notices == 1 && covers == 1 && !moves);
            assert(!BbscPrivateJoinAllowsLaterFollow(bbsc_follow_latest));
            auto newer = bbsc_follow_latest;
            newer.arrival_by_load = true;
            newer.arrival_map = newer.packed_map = ward;
            newer.arrival_ms = now + 500;
            assert(BbscPrivateJoinAllowsLaterFollow(newer));
            return 0;
        }
        // Native loading and the temporary state 4 must not rearm the request.
        game_loading = true;
        step(1500);
        session_state = 4;
        step(250);
        game_loading = false;
        session_state = 6;
        local_map = dream;
        host_visible = true;
        if (mode == "owner-moved") bbsc_follow_latest.packed_map = ward;
        if (mode == "room-changed") ++room;
        step(500);
        assert(!moves && reloads == 1);
        step(2250);
        step(2250);
        if (mode == "owner-moved" || mode == "room-changed") assert(!moves && notices == 1);
        else assert(moves == 1 && writes == 1 && notices == 0);
        assert(reloads == 1 && bbsc_join_teleport_done && covers == 1);
        return 0;
    }
    if (mode == "selector-rejected") {
        assert(reloads == 1 && preparation == 0 && notices == 1 && bbsc_join_teleport_done);
        return 0;
    }
    assert(!reloads && !preparation);
    if (mode == "invader") assert(bbsc_join_teleport_done && !notices);
    else {
        assert(!bbsc_join_teleport_done);
        step(BbscPrivateJoinWaitMs, refresh);
        assert(!moves && !reloads);
        if (mode == "dead" || mode == "loading" || mode == "other-guest") assert(!notices);
        else assert(notices == 1 && covers == 1);
    }
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("core", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--compiler", default="clang++")
    options = parser.parse_args()
    source = options.core.read_text("utf-8")
    guard = (ROOT / "patches/bbsc_relay_auth_guard.h").read_text("utf-8").removeprefix("#pragma once\n")
    tick = function(source, "void TickBbscJoinTeleport() {")
    assert source.count('#include "bbsc_private_join_recovery.inc"') == 1
    decision = "enum class BbscPrivateJoinDecision { Wait, Position, Stop };"
    assert source.count(decision) == 1
    helper = (options.core.parent / "bbsc_private_join_recovery.inc").read_text("utf-8")
    first_spawn = (options.core.parent / "bbsc_first_spawn.inc").read_text("utf-8")
    parse = function(source, "std::optional<SummonPlacementDescriptor> ParseBbscSummonPlacement(")
    code = options.output.with_suffix(".cpp")
    code.write_text("\n".join((guard, PREFIX, decision, helper, parse, first_spawn, tick, MAIN)), "utf-8")
    subprocess.run([options.compiler, "-std=c++23", str(code), "-o", str(options.output)], check=True)
    for scenario in ("recover", "recover-no-actor", "selector-rejected", "timed-out", "owner-moved",
                     "room-changed", "returned", "missing-world", "missing-dream", "no-baseline",
                     "recovery-disabled", "not-finite", "invader", "wrong-without-map", "dead", "loading", "forced-pending",
                     "missing", "expired", "no-owner", "not-guest", "other-guest", "same-map",
                     "same-map-reload", "public", "first-valid", "first-established", "first-far",
                     "first-different-area", "first-after-native", "first-failed",
                     "first-desc-type", "first-desc-map", "first-desc-source", "first-desc-relay",
                     "first-desc-reloaded", "first-desc-session",
                     "first-native-map", "first-unready", "first-baseline", "first-public",
                     "first-reload", "first-loading", "first-forced", "first-remote", "first-skip",
                     "first-invader", "first-context", "first-readonly", "first-host-map", "first-nan",
                     "dream-near", "dream-far", "dream-height"):
        subprocess.run([str(options.output.resolve()), scenario], check=True)
    print("PASS: 54 actual join scenarios; first spawn runs through the native copy/refresh/descriptor model "
          "and never writes the global placement block")


if __name__ == "__main__":
    main()
