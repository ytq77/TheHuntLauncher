// One accepted White Bell invitation. Neither the network thread nor later
// follow/rejoin loads can arm this record.
//
// v4 (2026-11-07). The received placement block (global+0x1620) is not where
// the host's position can live: the stage update at 0x0194132D copies the local
// player's own transform into it whenever the player's area equals +0x1640, and
// StartNotifyWait copies it into the summoned bank again and again (64 times in
// the 01:51 same-map run). The summon load builds its spawn descriptor from that
// bank: type/subtype 2/1 at 0x01944D00 copies +0x14D0/+0x14E0 to descriptor
// +0x60/+0x70 and joins 0x01944F23. So the host position is taken out of the
// relay grant at the first native copy, inside the grant's TTL, and written only
// into that descriptor. No global placement, area, map, ready or warp field is
// touched.
struct BbscFirstSpawn {
    u64 epoch{}, reload{}, room{};
    u32 map{}, owner{};
    // Taken from the relay grant at the first copy; applied at the spawn descriptor.
    bool captured{};
    u64 grant_room{};
    u32 grant_owner{};
    std::array<float, 8> pose{};
    u32 writes{}, refusals{};
};
std::mutex bbsc_first_spawn_mutex;
BbscFirstSpawn bbsc_first_spawn;

void ArmBbscFirstSpawn() {
    if (!BbscMod::RelayAuthPrivate()) return;
    std::scoped_lock lock{bbsc_first_spawn_mutex};
    bbsc_first_spawn = {};
    const auto session = GetSessionState();
    const auto map = GetCurrentPackedMap();
    std::string_view refused;
    if (session != 0 && session != 4 && session != 5) refused = "arm-session";
    else if (!BbscWorldRecoveryEnabled()) refused = "arm-recovery-off";
    else if (!BbscOwnWorldReadyForGuest()) refused = "arm-baseline";
    else if (!IsUsablePackedMap(map)) refused = "arm-map";
    if (!refused.empty()) {
        LOG_INFO(Debug, "BBSC first spawn: kept native position ({}) - state {}, map {:#x}", refused,
                 session, map);
        return;
    }
    bbsc_first_spawn.epoch = BbscMod::RelayAuthArmFirstSpawn();
    bbsc_first_spawn.reload = bbsc_character_reload_generation.load();
    bbsc_first_spawn.map = map;
    if (!bbsc_first_spawn.epoch)
        LOG_INFO(Debug, "BBSC first spawn: kept native position (no-invitation)");
    else
        LOG_INFO(Debug, "BBSC first spawn: armed in map {:#x}", map);
}

void CloseBbscFirstSpawnOnNativeReload() {
    if (!BbscMod::RelayAuthPrivate()) return;
    std::scoped_lock lock{bbsc_first_spawn_mutex};
    if (bbsc_first_spawn.epoch) {
        bbsc_first_spawn.epoch = 0;
        LOG_INFO(Debug, "BBSC first spawn: kept native position (no-copy-before-reload)");
    }
}

struct BbscFirstSpawnGrant {
    u64 global;
    u32 map;
    u64 room{};
    u32 owner{};
    std::array<float, 8> pose{};
    s32 native_area{-1}, host_area{-1};
    std::string_view skipped = "no-matching-grant";
};

// Runs under the relay locks: parse and check only, no game memory writes.
bool TakeBbscFirstSpawnGrant(std::string_view text, void* data) {
    auto& to = *static_cast<BbscFirstSpawnGrant*>(data);
    const auto host = ParseBbscSummonPlacement(text);
    const u64 global = to.global;
    if (!host) {
        to.skipped = "host-placement";
        return false;
    }
    to.host_area = host->area;
    if (host->packed_region != to.map) {
        to.skipped = "host-map";
        return false;
    }
    if (!ReadablePointer(global, 0x1649)) {
        to.skipped = "native-state";
        return false;
    }
    // Informational only. The area is the native refresh key, not part of the spawn.
    to.native_area = ReadValue<s32>(global, 0x1640);
    const bool bounded = std::isfinite(host->x) && std::isfinite(host->y) &&
                         std::isfinite(host->z) && std::isfinite(host->heading) &&
                         std::abs(host->x) <= 20000.0f && std::abs(host->y) <= 20000.0f &&
                         std::abs(host->z) <= 20000.0f && std::abs(host->heading) <= 20000.0f &&
                         !(host->x == 0 && host->y == 0 && host->z == 0);
    if (ReadValue<u8>(global, 0x08) != 0) to.skipped = "native-loading";
    else if (ReadValue<u8>(global, 0x1520) != 0) to.skipped = "native-forced-warp";
    else if (ReadValue<u8>(global, 0x1592) == 0) to.skipped = "native-own-world";
    else if (ReadValue<u8>(global, 0x1648) != 1) to.skipped = "native-unready";
    else if (ReadValue<u32>(global, 0x1644) != to.map) to.skipped = "native-map";
    else if (!bounded) to.skipped = "host-bounds";
    else {
        to.room = BbscMod::RelayAuthRoom();
        to.owner = BbscMod::RelayAuthOwner();
        if (!to.room || !to.owner) {
            to.skipped = "relay-context";
            return false;
        }
        to.pose = {host->x, host->y, host->z, 1.0f, 0.0f, host->heading, 0.0f, 0.0f};
        to.skipped = {};
        return true;
    }
    return false;
}

void TryBbscFirstSpawnBeforeCopy(u64 object, u64 global) {
    if (!BbscMod::RelayAuthPrivate() || global != GetGlobalState() || !ReadablePointer(object, 0xEE) ||
        ReadValue<u32>(object, 0xE8) != 0 || (ReadValue<u8>(object, 0xED) & 0x20)) return;
    std::scoped_lock lock{bbsc_first_spawn_mutex};
    auto& join = bbsc_first_spawn;
    if (!join.epoch) return;
    const auto epoch = join.epoch;
    join.epoch = 0;
    // Matching can complete before StartNotifyWait copies the summon position.
    // The White Bell marker is closed by the native reload entry, not by state 6.
    const auto session = GetSessionState();
    if (session != 4 && session != 6) {
        LOG_INFO(Debug, "BBSC first spawn: kept native position (session) - state {}", session);
        return;
    }
    // The player's local type can still be 0 before the native summon creates it.
    const auto type = GetLocalPlayerType();
    const auto here = GetCurrentPackedMap();
    std::string_view refused;
    if (type != 0 && type != 1) refused = "type";
    else if (GetCharacterHp(GetLocalPlayerCharacter()) <= 0) refused = "hp";
    else if (!BbscOwnWorldReadyForGuest()) refused = "baseline";
    else if (join.reload != bbsc_character_reload_generation.load()) refused = "reloaded";
    else if (here != join.map) refused = "map";
    if (!refused.empty()) {
        LOG_INFO(Debug, "BBSC first spawn: kept native position (local-state) - {}, type {}, map {:#x}",
                 refused, type, here);
        return;
    }
    BbscFirstSpawnGrant to{global, join.map};
    if (BbscMod::RelayAuthUseFirstSpawn(epoch, TakeBbscFirstSpawnGrant, &to)) {
        join.captured = true;
        join.grant_room = to.room;
        join.grant_owner = to.owner;
        join.pose = to.pose;
        LOG_INFO(Debug,
                 "BBSC first spawn: host position taken in map {:#x} (native area {}, host area {}) "
                 "at ({:.2f}, {:.2f}, {:.2f})",
                 join.map, to.native_area, to.host_area, to.pose[0], to.pose[1], to.pose[2]);
    } else {
        LOG_INFO(Debug, "BBSC first spawn: kept native position ({}) - native area {}, host area {}",
                 to.skipped, to.native_area, to.host_area);
    }
}

// 0x01944F23, R9 = the stage load's spawn descriptor. Every load passes here;
// only the captured White Bell join may change it, and only +0x60..+0x80.
void ApplyBbscFirstSpawnAtDescriptor(u64 descriptor, u64 global) {
    if (!BbscMod::RelayAuthPrivate()) return;
    std::scoped_lock lock{bbsc_first_spawn_mutex};
    auto& join = bbsc_first_spawn;
    if (!join.captured) return;
    const auto refuse = [&join](std::string_view why, s32 type, s32 subtype, u32 map) {
        if (++join.refusals <= 4)
            LOG_INFO(Debug, "BBSC first spawn: kept native position ({}) - type {}/{}, map {:#x}", why,
                     type, subtype, map);
    };
    if (!ReadablePointer(descriptor, 0x80) || !ReadablePointer(global, 0x14F0)) {
        refuse("descriptor-access", -1, -1, 0);
        return;
    }
    const auto type = ReadValue<s32>(descriptor, 0x54);
    const auto subtype = ReadValue<s32>(descriptor, 0x58);
    const auto map = ReadValue<u32>(descriptor, 0x08);
    const auto session = GetSessionState();
    if (type != 2 || subtype != 1) return refuse("descriptor-type", type, subtype, map);
    if (map != join.map || ReadValue<u32>(global, 0x14C4) != join.map)
        return refuse("descriptor-map", type, subtype, map);
    if (session != 4 && session != 6) return refuse("descriptor-session", type, subtype, map);
    if (join.reload != bbsc_character_reload_generation.load())
        return refuse("descriptor-reloaded", type, subtype, map);
    if (!join.grant_room || BbscMod::RelayAuthRoom() != join.grant_room ||
        BbscMod::RelayAuthOwner() != join.grant_owner)
        return refuse("descriptor-relay", type, subtype, map);
    const auto placed = ReadValue<std::array<float, 8>>(descriptor, 0x60);
    // A second finalize of an already written descriptor: nothing to do.
    if (std::memcmp(placed.data(), join.pose.data(), sizeof(placed)) == 0) return;
    // The 2/1 branch has just copied the summoned bank here; anything else is not this spawn.
    const auto bank = ReadValue<std::array<float, 8>>(global, 0x14D0);
    if (std::memcmp(placed.data(), bank.data(), sizeof(placed)) != 0)
        return refuse("descriptor-source", type, subtype, map);
    if (!WriteValue(descriptor, 0x60, join.pose)) return refuse("descriptor-write", type, subtype, map);
    join.room = join.grant_room;
    join.owner = join.grant_owner;
    ++join.writes;
    const float dx = join.pose[0] - placed[0], dy = join.pose[1] - placed[1], dz = join.pose[2] - placed[2];
    LOG_INFO(Debug, "BBSC first spawn: spawn descriptor set in map {:#x}, {:.2f} m from the native spawn (hit {})",
             join.map, std::sqrt(dx * dx + dy * dy + dz * dz), join.writes);
}

// The summon reload finished (0x0178D9C0, after the generation advanced).
void FinishBbscFirstSpawnAtReload() {
    if (!BbscMod::RelayAuthPrivate()) return;
    std::scoped_lock lock{bbsc_first_spawn_mutex};
    auto& join = bbsc_first_spawn;
    if (!join.captured) return;
    join.captured = false;
    if (!join.writes) LOG_INFO(Debug, "BBSC first spawn: kept native position (no-spawn-descriptor)");
}

bool BbscKeepFirstSpawn(u32 map, const BbscTransform& own, const BbscTransform& host) {
    if (map != BbscDreamStateMap) {
        std::scoped_lock lock{bbsc_first_spawn_mutex};
        const auto& join = bbsc_first_spawn;
        if (!join.room || join.room != BbscMod::RelayAuthRoom() ||
            join.owner != BbscMod::RelayAuthOwner() || join.map != map ||
            bbsc_character_reload_generation.load() != join.reload + 1) return false;
        // Test evidence: where the load actually put the guest, against what was written.
        const float tx = own.position[0] - join.pose[0], tz = own.position[2] - join.pose[2];
        LOG_INFO(Debug, "BBSC first spawn: guest arrived {:.2f} m from the spawn target (height {:+.2f})",
                 std::sqrt(tx * tx + tz * tz), own.position[1] - join.pose[1]);
    }
    const float x = own.position[0] - host.position[0];
    const float y = own.position[1] - host.position[1];
    const float z = own.position[2] - host.position[2];
    return x * x + z * z <= 9.0f && std::abs(y) <= 0.75f;
}
