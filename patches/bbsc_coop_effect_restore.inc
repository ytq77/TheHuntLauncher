// A kept session can outlive the PlayerIns and its non-persistent SpEffects.
// Carry only the guest's native co-op effect, used by the Blank animation.
struct BbscCoopEffectSnapshot {
    u64 matching = 0;
    u64 player = 0;
    u64 effects = 0;
    s64 seen_ms = 0;
    s64 leave_ms = 0;
    u64 reload_generation = 0;
    u64 ready_player = 0;
    u64 ready_effects = 0;
    u64 ready_generation = 0;
    s64 ready_ms = 0;
};

// Distinguish an empty list from one that cannot be read during a load.
int BbscCoopEffectActive(u64 effects, s32 id) {
    u64 node = ReadValue<u64>(effects, 0x08);
    for (int n = 0; node && n < 256; ++n) {
        if (!ReadablePointer(node, 0x60)) return -1;
        if (ReadValue<s32>(node, 0x40) == id &&
            (ReadValue<u32>(node, 0x1C) & 0x800C0003U) == 0) {
            return ReadablePointer(ReadValue<u64>(node, 0x48), 1) ? 1 : -1;
        }
        node = ReadValue<u64>(node, 0x58);
    }
    return node ? -1 : 0;
}

void TickBbscCoopEffectRestore() {
    static std::atomic_flag restoring = ATOMIC_FLAG_INIT;
    if (restoring.test_and_set(std::memory_order_acquire)) return;
    struct Release {
        std::atomic_flag& flag;
        ~Release() { flag.clear(std::memory_order_release); }
    } release{restoring}; // Serialize the snapshot/add, including reentrant native callbacks.
    static BbscCoopEffectSnapshot saved;
    if (image_base == 0) {
        saved = {};
        return;
    }
    const u64 matching = GetMatchingState();
    const s32 state = GetSessionState();
    constexpr s32 id = BbscHpPenaltyEffectId;
    const s64 now = SteadyNowMs();
    const s64 leave_ms = bbsc_leave_event_ms.load();
    const u64 reload_generation = bbsc_character_reload_generation.load();
    if (!matching || state != 6 || bbsc_persistence_restore_at_ms) {
        saved = {};
        return;
    }
    if (saved.matching != matching || saved.leave_ms != leave_ms ||
        now - saved.seen_ms > BbscReinsertTimeoutMs) {
        saved = {};
    }

    const u64 global = GetGlobalState();
    const u64 player = GetLocalPlayerCharacter();
    const u64 effects = ReadablePointer(player, 0x1D0) ? ReadValue<u64>(player, 0x1C8) : 0;
    if (bbsc_v8_pending || bbsc_spectate_active || !ReadablePointer(global, 0x1598) ||
        ReadValue<u8>(global, 0x08) != 0 || !ReadablePointer(effects, 0x10) ||
        GetCharacterHp(player) <= 0) {
        saved.ready_player = 0;
        return;
    }
    const bool own_world = ReadValue<u8>(global, 0x1592) != 0;
    const s32 role = GetLocalCharacterType();
    if (own_world || role != 1) {
        saved.ready_player = 0;
        return;
    }

    const int active = BbscCoopEffectActive(effects, id);
    if (active < 0) {
        saved.ready_player = 0;
        return;
    }
    if (active == 1) {
        saved = {matching, player, effects, now, leave_ms, reload_generation};
        return;
    }
    if (!saved.player || (saved.player == player && saved.effects == effects &&
                          saved.reload_generation == reload_generation)) {
        saved = {}; // Ordinary removal on the same character.
        return;
    }
    if (saved.ready_player != player || saved.ready_effects != effects ||
        saved.ready_generation != reload_generation) {
        saved.ready_player = player;
        saved.ready_effects = effects;
        saved.ready_generation = reload_generation;
        saved.ready_ms = now;
        return;
    }
    if (now - saved.ready_ms < BbscReinsertSettleMs) return;

    // One native add per observed loss. Re-arm only after seeing an active node.
    saved = {};
    const u64 vtable = ReadValue<u64>(player, 0);
    const u64 add = ReadablePointer(vtable, 0x3F8) ? ReadValue<u64>(vtable, 0x3F0) : 0;
    if (!IsEbootCodeAddress(add)) return;
    using Add = void PS4_SYSV_ABI (*)(u64, s32, u64, s32, s32, s32, float, float, float, float,
                                     float, s32);
    reinterpret_cast<Add>(add)(player, id, player, 0, 0, 0, 1.f, 1.f, 1.f, 1.f, 1.f, 0);
    const int restored = BbscCoopEffectActive(effects, id);
    LOG_INFO(Debug, "BBSC co-op reload: SpEffect {} native add, active={} (session {})",
             id, restored, state);
}
