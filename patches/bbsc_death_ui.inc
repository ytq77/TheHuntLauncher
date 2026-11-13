// Native text-effect queue, separate from network/Lua event 0xFFA.
// 1.09: SetTextEffect 0x013360B0 takes effect in ESI, ignores RDI.
// Death entry 0x01381587 selects 2; 0x013815EE queues it. Menu update
// 0x017765BC consumes the ring into MenuMan+0x108 unless +0x1724 blocks.
// A queued/current effect is not proof that its pixels reached the screen.
struct BbscDeathUiState {
    u64 actor{}, global{}, reload{}, role{};
    s64 since{};
    bool seen{}, requested{}, diagnosed{};
};
BbscDeathUiState bbsc_death_ui;
std::mutex bbsc_death_ui_mutex;

bool BbscDeathUiNativeProducerMatches(bool first_entry = true) {
    static constexpr std::array<u8, 79> Pin{0x8D,0x46,0xFF,0x83,0xF8,0x10,0x77,0x46,0x48,0x8B,0x05,0xB9,0xC7,0x22,0x04,0x83,0x78,0x78,0x00,0x75,0x0D,0x48,0x8D,0x0D,0xEE,0x50,0x20,0x04,0x8A,0x09,0x84,0xC9,0x74,0x07,0xC7,0x40,0x78,0x00,0x00,0x00,0x00,0x48,0x63,0x88,0x10,0x16,0x00,0x00,0x89,0xB4,0x88,0xC8,0x15,0x00,0x00,0x8B,0x88,0x10,0x16,0x00,0x00,0x8D,0x51,0x01,0x31,0xF6,0x83,0xF9,0x0F,0x0F,0x4E,0xF2,0x89,0xB0,0x10,0x16,0x00,0x00,0xC3};
    constexpr u64 Offset = 0x013360B0;
    if (!HasMemoryAccess(image_base + Offset, Pin.size(), MemoryProt::CpuRead) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + Offset), Pin.data(), Pin.size()) != 0)
        return false;
    if (!first_entry) return true;
    static constexpr std::array<u8, 8> NativeDeathSelect{0x41,0xC7,0x47,0x18,0x02,0x00,0x00,0x00};
    if (!ReadablePointer(image_base + 0x01381587, NativeDeathSelect.size()) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x01381587), NativeDeathSelect.data(), NativeDeathSelect.size()) != 0) return false;
    static constexpr std::array<u8, 40> NativeDeathQueue{0x48,0x63,0x91,0x10,0x16,0x00,0x00,0x89,0x84,0x91,0xC8,0x15,0x00,0x00,0x8B,0x81,0x10,0x16,0x00,0x00,0x8D,0x50,0x01,0x31,0xF6,0x83,0xF8,0x0F,0x0F,0x4E,0xF2,0x89,0xB1,0x10,0x16,0x00,0x00,0x41,0xB5,0x01};
    if (!ReadablePointer(image_base + 0x013815E7, NativeDeathQueue.size()) ||
        std::memcmp(reinterpret_cast<const void*>(image_base + 0x013815E7), NativeDeathQueue.data(), NativeDeathQueue.size()) != 0) return false;
    return true;
}


void TickBbscGuestDeathUi(s64 now, s32 session, u64 global, u64 actor, s32 hp) {
    std::scoped_lock lock{bbsc_death_ui_mutex};
    auto& state = bbsc_death_ui;
    if (!BbscDeathPenaltyEnabled() || (session >= 0 && session != 6) || hp > 0) {
        state = {};
        return;
    }
    // Unknown reads are not evidence of revival or a new death. Do not erase
    // the once-only latch or start a deadline until context is readable.
    const s32 type = GetLocalPlayerType();
    if (session < 0 || type < 0 || hp < 0 || !actor ||
        !ReadablePointer(global, 0x1593)) return;
    if (type != 1 || ReadValue<u8>(global, 0x1592) != 0) {
        state = {};
        return;
    }
    const u64 reload = bbsc_character_reload_generation.load();
    const u64 role = BbscLoadProtectionRoleEpoch();
    if (state.actor != actor || state.global != global || state.reload != reload || state.role != role)
        state = {actor, global, reload, role, now};
    // Temporary presentation/load gates are not a new death. Preserve the
    // original deadline and the once-only request/observation across them.
    if (ReadValue<u8>(global, 0x08) != 0 || BbscCurtainActive() || bbsc_load_cover.active) return;
    const u64 menu = ReadValue<u64>(image_base + MenuManPointerOffset, 0);
    if (!ReadablePointer(menu, 0x1725)) return;
    const s32 current = ReadValue<s32>(menu, 0x108);
    const s32 read = ReadValue<s32>(menu, 0x160C), write = ReadValue<s32>(menu, 0x1610);
    const bool blocked = ReadValue<u8>(menu, 0x1724) != 0;
    bool queued = false, pending = false;
    for (u32 i = 0; i < 17; ++i) {
        const s32 effect = ReadValue<s32>(menu, 0x15C8 + 4 * i);
        queued |= effect == 2;
        pending |= effect != 0;
    }
    state.seen |= current == 2 || queued;
    if (!state.diagnosed && now - state.since >= 500) {
        state.diagnosed = true;
        LOG_INFO(Debug, "BBSC death UI: current={} dead_queued={} previously_seen={} blocked={} read={} write={} (menu state, not pixel confirmation)",
                 current, queued, state.seen, blocked, read, write);
    }
    // Only repair a repeat death whose native death script did not run.
    // First deaths already have a native producer before PartyGhostDeath;
    // tick polling alone cannot prove that producer never ran or was never seen.
    if (!bbsc_kept_death_this_load || bbsc_death_script_ms >= state.since - 2'000) return;
    if (state.seen || state.requested || now - state.since < 500 || now - state.since > 3'000 ||
        current != 0 || pending || blocked || read < 0 || read > 16 || write < 0 || write > 16 ||
        !BbscRespawnNativeScreenIdle() ||
        !HasMemoryAccess(menu + 0x78, 4, MemoryProt::CpuWrite) ||
        !HasMemoryAccess(menu + 0x15C8, 0x4C, MemoryProt::CpuWrite)) return;
    // Validate the entire leaf before calling it. It writes only the menu state,
    // selected empty ring slot and producer index; no actor, effect or save data.
    if (!BbscDeathUiNativeProducerMatches(false)) return;
    constexpr u64 Offset = 0x013360B0;
    using SetEffect = void PS4_SYSV_ABI (*)(u64, s32);
    reinterpret_cast<SetEffect>(image_base + Offset)(0, 2);
    state.requested = true; // never retry this death, even if later consumed/cleared
    LOG_INFO(Debug, "BBSC death UI: requested native text effect 2 once (pixel display unverified)");
}

// Proof of the independent LOCAL menu path, not a promise that pixels were
// visible. First deaths have a native producer; repeat deaths use the guarded
// once-only repair above. Unknown/unobserved state must retain the old fallback.
bool BbscGuestDeathLocalUiOwnsPrompt(u64 global, u64 actor) {
    TheHuntVisibility::Context context;
    if (!BbscMod::RelayAuthVisibilityContext(context) || !context.room || !context.own ||
        !context.owner || context.own == context.owner) return false;
    const auto* guest = TheHuntVisibility::Find(context, context.own);
    const auto* host = TheHuntVisibility::Find(context, context.owner);
    if (!guest || !host || !guest->capable || !host->capable ||
        !TheHuntVisibility::ValidPeer(*guest) || !TheHuntVisibility::ValidPeer(*host)) return false;
    const s64 now = SteadyNowMs();
    const u64 reload = bbsc_character_reload_generation.load();
    const u64 role = BbscLoadProtectionRoleEpoch();
    if (!BbscDeathUiNativeProducerMatches()) return false;
    std::scoped_lock lock{bbsc_death_ui_mutex};
    auto& state = bbsc_death_ui;
    const u64 menu = ReadValue<u64>(image_base + MenuManPointerOffset, 0);
    const bool menu_readable = ReadablePointer(menu, 0x1725);
    const s32 current = menu_readable ? ReadValue<s32>(menu, 0x108) : -1;
    bool queued = false;
    if (menu_readable) for (u32 i = 0; i < 17; ++i)
        queued |= ReadValue<s32>(menu, 0x15C8 + 4 * i) == 2;
    const bool same = state.actor == actor && state.global == global &&
                      state.reload == reload && state.role == role;
    if (same) state.seen |= current == 2 || queued;
    LOG_INFO(Debug, "BBSC death UI: Lua decision same={} current={} queued={} seen={} requested={}",
             same, current, queued, state.seen, state.requested);
    return state.actor == actor && state.global == global && state.reload == reload &&
           state.role == role && state.since && now >= state.since &&
           now - state.since <= 15'000 && (state.seen || state.requested);
}
