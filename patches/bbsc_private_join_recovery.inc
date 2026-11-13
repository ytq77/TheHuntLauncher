// Game-thread state for the initial private co-op join only. Signed follow
// data comes from the current room's owner; room changes clear that cache.
constexpr s64 BbscPrivateJoinWaitMs = 15'000;
struct BbscPrivateJoinState {
    u64 room{};
    u32 owner{}, target{}, observed_map{};
    s64 waiting_since{}, ready_since{}, observed_since{}, requested_ms{}, arrived_since{}, failed_ms{};
};
BbscPrivateJoinState bbsc_private_join;

u32 BbscWorldGroupKey(u32 packed_map, size_t index);
u64 BbscFlagGroupStorage(u32 key);
bool BbscWorldSyncEnabled();
bool BbscWorldRecoveryEnabled();

void NoteBbscPrivateJoinUnready(bool home) {
    if (home) bbsc_private_join = {};
    else {
        bbsc_private_join.ready_since = 0;
        bbsc_private_join.observed_since = 0;
        bbsc_private_join.arrived_since = 0;
    }
}

bool BbscPrivateJoinRequestedLoad() {
    return bbsc_private_join.requested_ms != 0;
}

bool BbscPrivateJoinSameRoom() {
    const auto& state = bbsc_private_join;
    return state.room && state.room == BbscMod::RelayAuthRoom() &&
           state.owner && state.owner == BbscMod::RelayAuthOwner() &&
           BbscMod::RelayAuthOwnId() && state.owner != BbscMod::RelayAuthOwnId();
}

std::optional<BbscFollowMessage> BbscPrivateJoinHost(s64 now) {
    if (!BbscPrivateJoinSameRoom()) return std::nullopt;
    std::scoped_lock lock{bbsc_follow_mutex};
    const auto& host = bbsc_follow_latest;
    if (!host.received_ms || now < host.received_ms || now - host.received_ms > BbscFollowStaleMs ||
        !IsUsablePackedMap(host.packed_map)) return std::nullopt;
    return host;
}

bool BbscPrivateJoinMayPlace(u32 map, s64 now) {
    const auto host = BbscPrivateJoinHost(now);
    return host && host->packed_map == map && BbscPrivateJoinSameRoom();
}

bool PrepareBbscGuestTravelBaseline(u32 map, std::string_view leaving_dream) {
    const auto wait = [map](std::string_view reason) {
        static u32 logged_map = 0;
        static s64 logged_ms = 0;
        static std::string_view logged_reason;
        const s64 now = SteadyNowMs();
        if (logged_map != map || logged_reason != reason || now - logged_ms >= 5'000) {
            logged_map = map;
            logged_ms = now;
            logged_reason = reason;
            LOG_WARNING(Debug, "BBSC travel state: waiting ({}) for map {:#x}; personal baseline ready {}",
                        reason, map, BbscOwnWorldReadyForGuest());
        }
        return false;
    };
    if (BbscWorldRecoveryEnabled()) {
        if (!BbscOwnWorldCaptureStillCurrent()) return wait("active save changed");
        if (BbscWorldRecoveryJournalFrozen()) return wait("title exit protects the baseline");
        if (BbscWorldRecoveryWaiting()) return wait("recovery acknowledgement pending");
    }
    // Keep personal Dream changes before publishing the baseline used by the
    // destination's character-creation hook. Never clear dirty on a failed write.
    if (map != BbscDreamStateMap) RestoreBbscOwnDreamState(leaving_dream);
    if (BbscWorldRecoveryEnabled()) WriteBbscOwnWorldFile();
    if (!BbscOwnWorldReadyForGuest()) return wait("baseline publication pending");
    return true;
}

bool ApplyBbscGuestTravelState(u32 map, std::string_view why, std::string_view leaving_dream,
                              bool host_flags_ready) {
    if (!PrepareBbscGuestTravelBaseline(map, leaving_dream)) return false;
    if (host_flags_ready) ApplyBbscHostWorldFlags(map, why);
    ApplyBbscGuestLootView(map, why);
    if (map == BbscDreamStateMap) ApplyBbscHostDreamState(why);
    // Applying a newly encountered map/loot group may extend the baseline.
    return PrepareBbscGuestTravelBaseline(map, leaving_dream);
}

bool BbscPrivateJoinWorldReady(u32 map) {
    return BbscHostWorldFlagsReady(map) &&
           (map != BbscDreamStateMap || !BbscWorldSyncEnabled() || !BbscDreamEnabled() ||
            BbscHostDreamStateReady());
}

bool PrepareBbscPrivateJoinLoad(u32 map, std::string_view why) {
    if (!BbscPrivateJoinSameRoom() || !BbscWorldRecoveryEnabled() ||
        !BbscOwnWorldReadyForGuest() || !BbscPrivateJoinMayPlace(map, SteadyNowMs()) ||
        !BbscPrivateJoinWorldReady(map)) return false;
    return ApplyBbscGuestTravelState(map, why, "joining outside the Dream", true);
}

BbscPrivateJoinDecision StopBbscPrivateJoin(s64 now, std::string_view reason) {
    if (!bbsc_private_join.failed_ms) {
        bbsc_private_join.failed_ms = now;
        LOG_WARNING(Debug, "BBSC join recovery: {}", reason);
        EndBbscLoadCover("join recovery ended");
        BbscAnnounce(u"未能在房主所在区域完成入房。可使用断鸣空弹返回。", "join recovery stopped");
    }
    return BbscPrivateJoinDecision::Stop;
}

BbscPrivateJoinDecision TickBbscPrivateJoinRecovery(u64 global, u32 local_map, s64 now) {
    auto& state = bbsc_private_join;
    if (state.failed_ms) return BbscPrivateJoinDecision::Stop;
    if (!state.waiting_since) state.waiting_since = now;
    if (!state.ready_since) state.ready_since = now;
    const u64 room = BbscMod::RelayAuthRoom();
    const u32 owner = BbscMod::RelayAuthOwner(), own = BbscMod::RelayAuthOwnId();
    if (room && owner && own && owner != own) {
        if (!state.room) {
            state.room = room;
            state.owner = owner;
        } else if (!BbscPrivateJoinSameRoom()) {
            return StopBbscPrivateJoin(now, "room changed during initial placement");
        }
    }
    const auto host = BbscPrivateJoinHost(now);
    if (state.requested_ms) {
        if (now - state.requested_ms > BbscFollowArriveTimeoutMs)
            return StopBbscPrivateJoin(now, "native load did not reach the confirmed destination");
        if (local_map != state.target) return BbscPrivateJoinDecision::Wait;
        if (!host) {
            if (now - state.ready_since >= BbscPrivateJoinWaitMs)
                return StopBbscPrivateJoin(now, "arrived without a fresh owner location");
            return BbscPrivateJoinDecision::Wait;
        }
        if (host->packed_map != state.target)
            return StopBbscPrivateJoin(now, "arrived, but the owner has moved on");
        if (!state.arrived_since) {
            state.arrived_since = now;
            LOG_INFO(Debug, "BBSC join recovery: arrived in map {:#x}", local_map);
        }
        return now - state.arrived_since >= BbscJoinTeleportSettleMs
            ? BbscPrivateJoinDecision::Position : BbscPrivateJoinDecision::Wait;
    }
    if (host && local_map == host->packed_map) return BbscPrivateJoinDecision::Position;
    if (!host) state.observed_since = 0;
    else if (host->packed_map != state.observed_map || !state.observed_since) {
        state.observed_map = host->packed_map;
        state.observed_since = now;
    }
    if (now - state.waiting_since >= BbscPrivateJoinWaitMs)
        return StopBbscPrivateJoin(now, "no verified destination for initial placement");
    if (!host || !IsUsablePackedMap(local_map) ||
        now - state.ready_since < BbscFollowAfterLoadMs ||
        now - state.observed_since < BbscFollowMismatchMs ||
        ReadValue<u8>(global, 0x1520) != 0 ||
        !BbscWorldRecoveryEnabled() ||
        !BbscPrivateJoinWorldReady(host->packed_map))
        return BbscPrivateJoinDecision::Wait;

    const u32 key = BbscWorldGroupKey(host->packed_map, 0);
    const auto spot = BbscFollowSpotOf(*host);
    const auto valid = [](float n) { return std::isfinite(n) && std::abs(n) <= 20000.0f; };
    if (!key || !BbscFlagGroupStorage(key) ||
        !valid(spot.x + 1.0f) || !valid(spot.y) || !valid(spot.z) || !valid(spot.heading) ||
        (spot.x == 0 && spot.y == 0 && spot.z == 0))
        return BbscPrivateJoinDecision::Wait;
    SummonPlacementDescriptor to{};
    to.packed_region = host->packed_map;
    to.x = spot.x + 1.0f;
    to.y = spot.y;
    to.z = spot.z;
    to.heading = spot.heading;
    // A publication failure is a wait, not a native-selector failure. Prepare
    // before consuming the one initial load request; the callback rechecks it.
    if (!PrepareBbscGuestTravelBaseline(to.packed_region, "joining outside the Dream"))
        return BbscPrivateJoinDecision::Wait;
    // One request per initial join. Once the native load starts, neither a
    // slow response nor a temporary session-state change can request it again.
    state.requested_ms = now;
    state.target = to.packed_region;
    state.ready_since = 0;
    if (!RequestBbscSessionWarp(global, to, "join-recover", PrepareBbscPrivateJoinLoad))
        return StopBbscPrivateJoin(now, "native selector rejected the destination");
    LOG_INFO(Debug, "BBSC join recovery: reload from {:#x} into {:#x}", local_map, state.target);
    return BbscPrivateJoinDecision::Wait;
}

bool BbscPrivateJoinAllowsLaterFollow(const BbscFollowMessage& host) {
    auto& state = bbsc_private_join;
    if (!state.failed_ms) return true;
    // Do not make ordinary follow retry the failed join. A subsequent
    // confirmed host load in the same authenticated room can take over.
    if (!BbscPrivateJoinSameRoom() || !host.arrival_by_load ||
        host.arrival_map != host.packed_map || host.arrival_ms <= state.failed_ms)
        return false;
    state.failed_ms = 0;
    return true;
}
