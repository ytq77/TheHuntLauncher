// Host-only presentation guard. Read-only; no guest protection state is armed here.
struct BbscHostRespawnPartner {
    TheHuntVisibility::Context roster;
    TheHuntVisibility::Peer peer;
    u64 global{}, actor{}, reload{}, role{};
    u32 map{};
    s64 captured{}, curtain_started{};
} bbsc_host_respawn_partner;

u64 bbsc_host_curtain_fade_plate{};
u64 BbscHostRespawnFadePlate() { return bbsc_host_curtain_fade_plate; }
bool BbscHostRespawnOwnPlate(u64 plate) { return plate && BbscFadePlate() == plate; }

void CaptureBbscHostRespawnPartner() {
    bbsc_host_curtain_fade_plate = GetSessionState() == 3 && GetLocalPlayerType() == 0 ? BbscFadePlate() : 0;
    bbsc_host_respawn_partner = {};
    if (GetSessionState() != 3 || GetLocalPlayerType() != 0) return;
    const auto reject = [](std::string_view reason) {
        LOG_INFO(Debug, "BBSC host respawn: partner capture unavailable ({})", reason);
    };
    if (!BbscMod::RelayAuthPrivate()) { reject("private authentication"); return; }
    TheHuntVisibility::Context roster;
    if (!BbscMod::RelayAuthVisibilityContext(roster) || !roster.room ||
        roster.own != roster.owner) { reject("fresh owner roster"); return; }
    const u64 world = ReadValue<u64>(image_base + CandidateLocalStatePointerOffset, 0);
    if (!ReadablePointer(world, 0x70)) { reject("native world"); return; }
    const s32 count = ReadValue<s32>(world, 0x30);
    const u64 entries = ReadValue<u64>(world, 0x38);
    if (count <= 1 || count > 64 || !ReadablePointer(entries, count * 0x38)) {
        reject("native slot table"); return;
    }
    const u64 local = GetLocalPlayerCharacter();
    TheHuntVisibility::Peer selected{};
    s32 empty_slots = 0;
    for (s32 i = 0; i < count; ++i) {
        const u64 actor = ReadValue<u64>(entries, i * 0x38);
        // WorldChrMan exposes slot capacity, including intentionally empty entries.
        if (!actor) { ++empty_slots; continue; }
        if (actor == local) continue;
        if (!ReadablePointer(actor, 0x3F8) || GetCharacterType(actor) != 1) {
            reject("non-cooperator or unreadable actor"); return;
        }
        const s32 hp = GetCharacterHp(actor);
        if (hp < 0) { reject("unknown partner health"); return; }
        if (hp == 0) continue;
        std::array<u8, 16> npid{};
        if (!ReadBbscDisplayNpid(actor + 0x3D8, npid)) continue;
        for (const auto& peer : roster.peers) {
            if (peer.user != roster.own && peer.npid == npid) selected = peer;
        }
    }
    if (!selected.user) { reject("no living authenticated partner"); return; }
    bbsc_host_respawn_partner = {roster, selected, GetGlobalState(), local,
        bbsc_character_reload_generation.load(), BbscLoadProtectionRoleEpoch(), GetCurrentPackedMap(),
        SteadyNowMs(), bbsc_curtain.started_ms};
    LOG_INFO(Debug, "BBSC host respawn: partner captured (slots={} empty={})", count, empty_slots);
}

bool BbscHostRespawnContext(bool require_alive) {
    const auto& proof = bbsc_host_respawn_partner;
    const u64 global = GetGlobalState();
    if (!proof.peer.user || proof.curtain_started != bbsc_curtain.started_ms ||
        proof.global != global || proof.actor != GetLocalPlayerCharacter() ||
        proof.reload != bbsc_character_reload_generation.load() ||
        proof.role != BbscLoadProtectionRoleEpoch() || proof.map != GetCurrentPackedMap() ||
        GetSessionState() != 3 || GetLocalPlayerType() != 0 ||
        !ReadablePointer(global, 0x1593) || ReadValue<u8>(global, 0x1592) != 1 ||
        ReadValue<u8>(global, 0x08) != 0 || !IsUsablePackedMap(GetCurrentPackedMap()) ||
        bbsc_load_cover.active || !BbscRespawnNativeScreenIdle() || bbsc_spectate_active ||
        bbsc_curtain.why.find("wipe") != std::string::npos || BbscAllPartnersReportDead()) return false;
    TheHuntVisibility::Context roster;
    if (!BbscMod::RelayAuthPrivate() || !BbscMod::RelayAuthVisibilityContext(roster) ||
        !(roster == proof.roster)) return false;
    const u64 world = ReadValue<u64>(image_base + CandidateLocalStatePointerOffset, 0);
    if (!ReadablePointer(world, 0x70)) return false;
    const s32 count = ReadValue<s32>(world, 0x30);
    const u64 entries = ReadValue<u64>(world, 0x38);
    if (count < 0 || count > 64 || (count && !ReadablePointer(entries, count * 0x38))) return false;
    bool native_alive = false;
    for (s32 i = 0; i < count; ++i) {
        const u64 actor = ReadValue<u64>(entries, i * 0x38);
        if (!actor || actor == proof.actor) continue;
        if (!ReadablePointer(actor, 0x3F8) || GetCharacterType(actor) != 1) return false;
        std::array<u8, 16> npid{};
        if (GetCharacterHp(actor) > 0 && ReadBbscDisplayNpid(actor + 0x3D8, npid) &&
            npid == proof.peer.npid) native_alive = true;
    }
    // Only the exact cooperator proven before revival may bridge a native list
    // gap. Generic life counts have no actor-role semantics and are not enough.
    std::scoped_lock lock{bbsc_life_mutex};
    const auto found = bbsc_peer_life.find(proof.peer.user);
    if (found == bbsc_peer_life.end()) return !require_alive || native_alive;
    const auto& life = found->second;
    const s64 age = SteadyNowMs() - life.received_ms;
    if (age < 0) return false;
    if (life.dead && age <= BbscLifeDeadHoldMs) return false;
    return !require_alive || native_alive || (!life.dead && life.received_ms >= proof.captured && age <= BbscLifeFreshMs);
}

// These read-only host witnesses are independent of guest protection tickets.
void ArmBbscHostReadyPlacement(u64 actor, u32 map, u64 reload, u64 role);
bool BbscHostNativeReady(u64 actor, u32 map, u64 reload, u64 role);
struct BbscHostRespawnWait {
    bool active{};
    s64 since{}, curtain_started{};
    u64 global{}, actor{}, reload{}, role{}, room{}, fade_plate{};
    u32 map{}, owner{};
    std::array<float, 3> target{};
} bbsc_host_respawn_wait;
constexpr s64 BbscHostRespawnWaitLimitMs = 1500;

void BeginBbscHostRespawnWait(s64 now, u64 global, u64 actor, u32 map, u64 reload, u64 role) {
    BbscHostRespawnWait wait{};
    wait.active = true; wait.since = now; wait.curtain_started = bbsc_curtain.started_ms;
    wait.global = global; wait.actor = actor; wait.map = map; wait.reload = reload; wait.role = role;
    wait.fade_plate = BbscHostRespawnFadePlate();
    wait.room = BbscMod::RelayAuthRoom(); wait.owner = BbscMod::RelayAuthOwner();
    for (size_t i = 0; i < 3; ++i) wait.target[i] = ReadValue<float>(global, 0x1450 + i * 4);
    bbsc_host_respawn_wait = wait;
    LOG_INFO(Debug, "BBSC host respawn: bounded native-ready wait ({} ms)", BbscHostRespawnWaitLimitMs);
}

bool BbscHostRespawnLocalGates(u64 actor) {
    const u64 control = ReadValue<u64>(actor, 0x58);
    if (!ReadablePointer(control, 0x116) || (ReadValue<u16>(control, 0x114) & 1)) return false;
    const u64 flags = ReadValue<u64>(control, 0x38);
    const u64 world = ReadValue<u64>(image_base + CandidateLocalStatePointerOffset, 0);
    const u64 menu = ReadValue<u64>(image_base + MenuManPointerOffset, 0);
    return ReadablePointer(flags, 0x49) && !(ReadValue<u8>(flags, 0x48) & 3) &&
        ReadablePointer(world, 0x70) && ReadValue<u8>(world, 0x68) == 0 &&
        ReadablePointer(menu, 0x2E0) && ReadValue<s32>(menu, 0x2DC) == 0;
}

void StopBbscHostRespawnCurtain(s64 now, u64 global, u64 actor, u32 map, u64 reload, u64 plate) {
    bbsc_host_respawn_wait = {};
    bbsc_host_respawn_partner = {};
    // Authentication can change without any native transition. Release only our
    // original local fade, never a newly installed world's fade or loading menu.
    const bool own_fade = GetSessionState() == 3 && GetLocalPlayerType() == 0 && plate && BbscFadePlate() == plate && GetGlobalState() == global &&
        GetLocalPlayerCharacter() == actor && GetCurrentPackedMap() == map &&
        bbsc_character_reload_generation.load() == reload && ReadablePointer(global, 0x1593) &&
        ReadValue<u8>(global, 0x08) == 0 && ReadValue<u8>(global, 0x1592) == 1 &&
        !bbsc_load_cover.active && BbscRespawnNativeScreenIdle();
    if (own_fade) {
        bbsc_curtain.notice = nullptr;
        EndBbscDeathCurtain(now, "host readiness cancelled: restore owned fade");
    } else {
        bbsc_curtain = {};
        LOG_INFO(Debug, "BBSC host respawn: relinquished curtain to native lifecycle");
    }
}

bool TickBbscHostRespawnWait() {
    const auto wait = bbsc_host_respawn_wait;
    if (!wait.active) return false;
    if (bbsc_curtain.step != BbscCurtainStep::FadingOut ||
        bbsc_curtain.started_ms != wait.curtain_started) {
        bbsc_host_respawn_wait = {};
        return false;
    }
    const s64 now = SteadyNowMs();
    // A real transition or unknown native ownership is not our synthetic menu.
    // Relinquish this feature without invoking any native menu/fade operation.
    const bool native_owned = GetSessionState() == 3 && GetLocalPlayerType() == 0 &&
        ReadablePointer(wait.global, 0x1593) && ReadValue<u8>(wait.global, 0x1592) == 1 &&
        ReadValue<u8>(wait.global, 0x08) == 0 && !bbsc_load_cover.active &&
        BbscRespawnNativeScreenIdle();
    const bool lifecycle = native_owned && BbscHostRespawnOwnPlate(wait.fade_plate) && GetGlobalState() == wait.global &&
        GetLocalPlayerCharacter() == wait.actor && GetCurrentPackedMap() == wait.map &&
        bbsc_character_reload_generation.load() == wait.reload &&
        BbscLoadProtectionRoleEpoch() == wait.role &&
        BbscMod::RelayAuthRoom() == wait.room && BbscMod::RelayAuthOwner() == wait.owner &&
        GetCharacterHp(wait.actor) >= 0;
    if (!lifecycle) {
        StopBbscHostRespawnCurtain(now, wait.global, wait.actor, wait.map, wait.reload, wait.fade_plate);
        return true;
    }
    const bool same = BbscHostRespawnContext(false) && GetCharacterHp(wait.actor) > 0;
    bool placed = false;
    if (same) {
        if (const auto actual = ReadCharacterTransform(wait.actor)) {
            placed = true;
            for (size_t i = 0; i < 3; ++i) {
                const float target = wait.target[i];
                placed = placed && std::isfinite(target) && std::abs(target) <= 20000.0f &&
                    std::abs(actual->position[i] - target) <= 0.25f;
            }
        }
    }
    const bool within_budget = now >= wait.since && now - wait.since < BbscHostRespawnWaitLimitMs;
    if (same && within_budget && BbscHostRespawnContext(true) && placed && BbscHostRespawnLocalGates(wait.actor) && BbscHostNativeReady(wait.actor, wait.map, wait.reload, wait.role)) {
        if (!BbscHostRespawnOwnPlate(wait.fade_plate)) {
            StopBbscHostRespawnCurtain(now, wait.global, wait.actor, wait.map, wait.reload, wait.fade_plate);
            return true;
        }
        bbsc_host_respawn_wait = {};
        EndBbscDeathCurtain(now, "same-map host: native ready without synthetic loading wait");
        return true;
    }
    if (same && within_budget) return true;
    bbsc_host_respawn_wait = {};
    LOG_INFO(Debug, "BBSC host respawn: old loading path (context={} elapsed={} placed={})",
             same, now - wait.since, placed);
    if (!BbscHostRespawnOwnPlate(wait.fade_plate)) {
        StopBbscHostRespawnCurtain(now, wait.global, wait.actor, wait.map, wait.reload, wait.fade_plate);
        return true;
    }
    bbsc_curtain.shown = BbscLoadingScreen(true);
    BbscCurtainStepTo(BbscCurtainStep::Loading, now);
    return true;
}
