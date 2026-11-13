"""Harden the fixed preview.22 game source AFTER prepare_kit_initialization.

No edits to upstream checkouts or installed games. Every replaced function must
match its pinned digest and every injection anchor must occur exactly once.
Default PvP retains trusted-network compatibility with strict native-position
selection. SHADPS4_BBSC_PVP_REQUIRE_ROOM_BINDING=1 explicitly rejects unbound guest
state; the legacy sender/session fields are not identity authentication.
"""
import argparse
import hashlib
from pathlib import Path
import shutil

from prepare_npc_regression import block

ROOT = Path(__file__).resolve().parents[1]
PINNED = {
    'void TickBbscSpectate() {': '5c4cd1d8d38cbc482841cf9219b498b87646ff48891b7fda50e6bd780a4dfe72',
    'void WriteLuaSessionEvent(': 'd546ab4fceca935b5c5d7252d8345e6d4c889f182e30019420b458d064e2fb77',
    'void ApplyBbscPvpTeams() {': 'ccc45e62843d32c68ab849e47c735a447cf8319d4f4b7d17abb529aa4dbb58be',
    'void TickBbscPvp() {': 'ebf397ea202a20f2c1f2b6742b3a457c38ad68e94c2e98e4817cdf256e4c8efa',
    'void OnBbscPvpPacket(': '7a74d7d2cf35563e7df3d63237b59465fdb22d7b0d48eb9b6f714bc2c4f5a699',
    'void TickBbscDuelBellGive() {': '8ad607d46a83ccee251dea528dc00746760df37214bce3c609bbf35195fbe5d2',
    'u32 BbscRelayHostForPackets() {': '5bb700e5f85ca150b14d2edb2747011e000c582b0647c594cd4bb4c0c26de60f',
    'void TickBbscRelayHostLock() {': '3fd59302d5aeae44991b1e16c379f2824b547450067fe66a6dfe40d559152d31',
}


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unexpected game hardening anchor: ' + before[:100])
    return text.replace(before, after, 1)


def pinned(text, marker):
    original = block(text, marker)
    if hashlib.sha256(original.encode('utf-8')).hexdigest() != PINNED[marker]:
        raise ValueError('Unexpected pinned game function: ' + marker)
    return original


RELAY_LOCK = r'''// Native room/member IDs are not mapped to the relay's process-random sender.
// This selector is a trusted-network compatibility heuristic, NOT authentication.
extern std::atomic<s64> bbsc_leave_event_ms;
void SelectBbscRelayHost(u32 sender) {
    if (bbsc_relay_locked_host.exchange(sender) != sender)
        bbsc_relay_selection_generation.fetch_add(1);
}

u64 BbscRelayNativeHost() {
    const u64 world = ReadValue<u64>(image_base + CandidateLocalStatePointerOffset, 0);
    if (!ReadablePointer(world, 0x68)) return 0;
    const s32 count = ReadValue<s32>(world, 0x30);
    const u64 entries = ReadValue<u64>(world, 0x38);
    const u64 local = ReadValue<u64>(world, 0x60);
    if (count <= 0 || count > 64 || !ReadablePointer(entries, static_cast<size_t>(count) * 0x38))
        return 0;
    u64 host = 0;
    for (s32 i = 0; i < count; ++i) {
        const u64 chr = ReadValue<u64>(entries, static_cast<size_t>(i) * 0x38);
        if (!chr || chr == local || GetCharacterType(chr) != 0) continue;
        if (!ReadablePointer(chr, 0x400) || !IsValidMemberId(chr + 0x3D8)) return 0;
        if (host) return 0; // Multiple native hosts: do not choose an arbitrary one.
        host = chr;
    }
    return host;
}

void TickBbscRelayHostLock() {
    static s64 last_ms = 0, not_guest_since_ms = 0, previous_leave_ms = 0;
    static u64 previous_matching = 0;
    const s64 now = SteadyNowMs();
    const s32 state = GetSessionState();
    bbsc_relay_session_state = state;
    const u64 matching = GetMatchingState();
    const s64 leave_ms = bbsc_leave_event_ms.load();
    if (image_base == 0 || leave_ms != previous_leave_ms ||
        (matching && previous_matching && matching != previous_matching)) {
        SelectBbscRelayHost(0);
        ClearBbscHostWorldSnapshots();
    }
    previous_leave_ms = leave_ms;
    if (matching) previous_matching = matching;
    if (image_base == 0) return;
    if (state != 6 && state != 4) {
        if (!not_guest_since_ms) not_guest_since_ms = now;
        // Keep the established lock across short native reload gaps, as before.
        if (state == 3 || now - not_guest_since_ms >= BbscRelayUnlockMs) {
            if (bbsc_relay_locked_host.load()) {
                SelectBbscRelayHost(0);
                ClearBbscHostWorldSnapshots();
            }
        }
        return;
    }
    not_guest_since_ms = 0;
    if (now - last_ms < 250) return;
    last_ms = now;
    // A selected host may travel to another map. Requiring same-map positions
    // continuously would break follow loads. Apply strict filtering at acquisition.
    if (bbsc_relay_locked_host.load() || state != 6) return;
    const u64 global = GetGlobalState();
    if (!ReadablePointer(global, 0x1598) || ReadValue<u8>(global, 0x08) != 0 ||
        ReadValue<u8>(global, 0x1592) != 0) return;
    const auto host = ReadCharacterTransform(BbscRelayNativeHost());
    const u32 map = GetCurrentPackedMap();
    if (!host.has_value() || !map) return;
    for (size_t i = 0; i < 3; ++i)
        if (!std::isfinite(host->position[i]) || std::abs(host->position[i]) > 20000.f) return;
    u32 selected = 0;
    {
        std::scoped_lock lock{bbsc_relay_mutex};
        for (const auto& [sender, seen] : bbsc_relay_hosts) {
            const s64 age = now - seen.ms;
            if (!sender || age < 0 || age > BbscRelayHostFreshMs || seen.map != map ||
                !std::isfinite(seen.x) || !std::isfinite(seen.y) || !std::isfinite(seen.z) ||
                std::abs(seen.x) > 20000.f || std::abs(seen.y) > 20000.f ||
                std::abs(seen.z) > 20000.f) continue;
            const float dx = seen.x - host->position[0];
            const float dy = seen.y - host->position[1];
            const float dz = seen.z - host->position[2];
            const float d2 = dx * dx + dy * dy + dz * dz;
            if (d2 >= BbscRelayLockMetres * BbscRelayLockMetres) continue;
            if (selected) return; // Two plausible broadcasters: wait, never guess nearest.
            selected = sender;
        }
    }
    if (selected) {
        SelectBbscRelayHost(selected);
        LOG_INFO(Debug, "BBSC relay: host {:08x} selected by native map/position (trusted-network heuristic)", selected);
    }
}'''


PVP_TEAMS = r'''// Snapshot only objects actually changed by us. A readable address alone is not identity.
struct BbscPvpTeamSnapshot {
    u32 team;
    u32 actor;
    s32 type;
    u64 modules;
    u64 effects;
    std::array<u64, 2> member;
};

bool BbscPvpTeamIdentity(u64 chr, BbscPvpTeamSnapshot& out) {
    if (!ReadablePointer(chr, 0x400) || !IsValidMemberId(chr + 0x3D8)) return false;
    // ChrIns+8 is the runtime actor handle (also used by the personal drop ledger).
    out.actor = ReadValue<u32>(chr, 8);
    out.type = ReadValue<s32>(chr, 0x78);
    out.modules = ReadValue<u64>(chr, 0x3B0);
    out.effects = ReadValue<u64>(chr, 0x1C8);
    for (size_t i = 0; i < out.member.size(); ++i)
        out.member[i] = ReadValue<u64>(chr, 0x3D8 + i * sizeof(u64));
    return out.actor != 0 && out.actor != 0xFFFFFFFFu && out.modules != 0 && out.effects != 0;
}

void ApplyBbscPvpTeams() {
    static std::map<u64, BbscPvpTeamSnapshot> original;
    static u64 last_world = 0, last_local = 0, last_matching = 0, last_generation = 0;
    const u64 generation = bbsc_character_reload_generation.load();
    const u64 world_chr = ReadValue<u64>(image_base + CandidateLocalStatePointerOffset, 0);
    const u64 matching = GetMatchingState();
    const u64 global = GetGlobalState();
    // We cannot safely restore across teardown. Forget, never write through stale pointers.
    if (!ReadablePointer(world_chr, 0x68)) {
        original.clear();
        return;
    }
    if (!ReadablePointer(global, 0x10) || ReadValue<u8>(global, 0x08) != 0) return;
    const u64 local = ReadValue<u64>(world_chr, 0x60);
    if (world_chr != last_world || local != last_local || matching != last_matching ||
        generation != last_generation) original.clear();
    last_world = world_chr;
    last_local = local;
    last_matching = matching;
    last_generation = generation;
    const s32 count = ReadValue<s32>(world_chr, 0x30);
    const u64 entries = ReadValue<u64>(world_chr, 0x38);
    if (count < 0 || count > 64 ||
        (count != 0 && !ReadablePointer(entries, static_cast<size_t>(count) * 0x38))) {
        original.clear();
        return;
    }
    std::map<u64, BbscPvpTeamSnapshot> current;
    for (s32 i = 0; i < count; ++i) {
        const u64 chr = ReadValue<u64>(entries, static_cast<size_t>(i) * 0x38);
        BbscPvpTeamSnapshot identity{};
        if (chr != 0 && chr != local && BbscPvpTeamIdentity(chr, identity))
            current.emplace(chr, identity);
    }
    for (auto it = original.begin(); it != original.end();) {
        const auto found = current.find(it->first);
        const auto& old = it->second;
        if (found == current.end() || found->second.actor != old.actor || found->second.type != old.type ||
            found->second.modules != old.modules || found->second.effects != old.effects ||
            found->second.member != old.member) it = original.erase(it);
        else ++it;
    }
    for (const auto& [chr, identity] : current) {
        if (!HasMemoryAccess(chr + 0x88, sizeof(u32), MemoryProt::CpuWrite)) continue;
        const u32 team = ReadValue<u32>(chr, 0x88);
        if (bbsc_pvp_on) {
            // Invaders already have their native team; never capture it as a co-op baseline.
            if (team == BbscPvpTeam || (identity.type != 0 && identity.type != 1)) continue;
            auto saved = identity;
            saved.team = team;
            // A fresh native team supersedes the old snapshot even at the same allocation.
            original.insert_or_assign(chr, saved);
            WriteValue<u32>(chr, 0x88, BbscPvpTeam);
        } else if (const auto it = original.find(chr); it != original.end()) {
            if (team == BbscPvpTeam) WriteValue<u32>(chr, 0x88, it->second.team);
        }
    }
    if (!bbsc_pvp_on) original.clear();
}'''

PVP_HELPERS = r'''bool BbscPvpRequireRoomBinding() {
    static const bool required = EnvFlagEnabled("SHADPS4_BBSC_PVP_REQUIRE_ROOM_BINDING");
    return required;
}

void ClearBbscPvpHostState() {
    std::scoped_lock lock{bbsc_pvp_packet_mutex};
    bbsc_pvp_host_state = -1;
    bbsc_pvp_host_state_ms = 0;
    bbsc_pvp_message_sender = 0;
    bbsc_pvp_message_generation = 0;
}'''

PVP_PACKET = r'''void OnBbscPvpPacket(std::uint8_t kind, std::uint8_t on, std::uint32_t sender) {
    if (kind == 0) {
        if (BbscPvpRequireRoomBinding()) return; // Explicit opt-in, not the compatibility default.
        std::scoped_lock lock{bbsc_pvp_packet_mutex};
        // Sender is dispatch metadata, not an authenticated principal. Recheck it
        // here so a selection change after Dispatch's filter cannot mis-stamp an old packet.
        if (!sender || sender != BbscRelayHostForPackets() || bbsc_relay_session_state.load() != 6)
            return;
        bbsc_pvp_message_generation = bbsc_relay_selection_generation.load();
        bbsc_pvp_message_sender = sender;
        bbsc_pvp_host_state = on ? 1 : 0;
        bbsc_pvp_host_state_ms = SteadyNowMs();
    } else if (kind == 1) {
        LOG_INFO(Debug, "BBSC PvP: guest request ignored; host-only control");
    }
}'''

KIT_GIVE = r'''void TickBbscDuelBellGive() {
    static s64 last_ms = 0;
    static u64 checked_player = 0;
    static u64 checked_generation = 0;
    const s64 now = SteadyNowMs();
    if (!bbsc_duel_bell_ready || now - last_ms < 5'000) return;
    last_ms = now;
    const u64 player = GetLocalPlayerCharacter();
    const u64 global = GetGlobalState();
    const u64 generation = bbsc_character_reload_generation.load();
    if ((player == checked_player && generation == checked_generation) ||
        !ReadablePointer(player, 0x410) ||
        GetCharacterHp(player) <= 0 || !ReadablePointer(global, 0x1598) ||
        ReadValue<u8>(global, 0x08) != 0) return;
    const u64 carried = BbscCarriedInventory();
    const u64 storage = BbscStorageInventory();
    if (carried == 0 || storage == 0) return;
    const auto has = [&](u32 id) {
        return BbscInventoryHas(carried, 0x40000000u | id) ||
               BbscInventoryHas(storage, 0x40000000u | id);
    };
    std::vector<const BbscKitItem*> missing;
    for (const auto& item : BbscKit) if (!has(item.id)) missing.push_back(&item);
    static constexpr u32 Retired[] = {200, 201, 205, 206, 225, 226, 111};
    bool retire = false;
    for (u32 id : Retired) retire = retire || has(id);
    const bool own_world = ReadValue<u8>(global, 0x1592) != 0;
    const bool flag_ready = own_world &&
        ReadValue<u64>(image_base + 0x0553B0D8, 0) != 0 &&
        ReadValue<u64>(image_base + EventFlagManPointerOffset, 0) != 0;
    const bool mark_given = flag_ready && !BbscEventFlag(6622);
    if (missing.empty() && !retire && !mark_given) {
        // Retry later if our own world's flag manager is not ready yet.
        if (!own_world || flag_ready) {
            checked_player = player;
            checked_generation = generation;
        }
        return;
    }
    // This must precede EVERY mutation, including the messenger's event flag.
    if (!BackupBbscSaves()) {
        LOG_WARNING(Debug, "BBSC kit: backup failed; inventory and flag unchanged, will retry");
        return;
    }
    using Fits = s32 PS4_SYSV_ABI (*)(u64, const u32*, s32);
    using Add = s32 PS4_SYSV_ABI (*)(u64, const u32*, s32);
    // Preflight all items before granting any. Capacity can still change as slots fill;
    // verify each add and the whole kit before retiring ANY vanilla item.
    for (const auto* item : missing) {
        alignas(16) u32 code = 0x40000000u | item->id;
        if (reinterpret_cast<Fits>(image_base + InventoryFitsOffset)(carried, &code, 1) <= 0)
            return;
    }
    std::u16string notice = u"\u8054\u673a\u9053\u5177\u5df2\u8865\u53d1\uff1a";
    size_t given = 0;
    for (const auto* item : missing) {
        alignas(16) u32 code = 0x40000000u | item->id;
        alignas(16) u32 key = 0x80000000u | (3u << 28) | item->id;
        if (reinterpret_cast<Fits>(image_base + InventoryFitsOffset)(carried, &code, 1) <= 0)
            return;
        reinterpret_cast<Add>(image_base + InventoryAddOffset)(carried, &key, 1);
        if (!has(item->id)) return; // Partial new kit is safe and retryable; old kit/flag stay.
        notice += ++given == 1 ? u"\n" : u", ";
        notice += item->name;
    }
    for (const auto& item : BbscKit) if (!has(item.id)) return;
    bool retired = true;
    for (u32 id : Retired) {
        for (u64 inventory : {carried, storage}) {
            if (BbscInventoryHas(inventory, 0x40000000u | id)) {
                const bool gone = BbscInventoryRemove(inventory, 0x40000000u | id);
                retired = gone && !BbscInventoryHas(inventory, 0x40000000u | id) && retired;
            }
        }
    }
    if (!retired) return;
    if (mark_given) {
        using SetEventFlag = u64 PS4_SYSV_ABI (*)(u64, u32, u8);
        reinterpret_cast<SetEventFlag>(image_base + 0x0132E5A0)(0, 6622, 1);
        if (!BbscEventFlag(6622)) return;
    }
    if (!own_world || flag_ready) {
        checked_player = player;
        checked_generation = generation;
    }
    if (given) BbscAnnounce(notice.c_str(), "kit given");
}'''


def apply(text):
    # Check the required preceding stage, then validate all owned functions before editing.
    if text.count('#include "bbsc_kit_repository.inc"') != 1:
        raise ValueError('Run prepare_kit_initialization before game hardening')
    originals = {marker: pinned(text, marker) for marker in PINNED}
    text = once(text, 'std::atomic<u32> bbsc_tick_map{0};',
                'std::atomic<u32> bbsc_tick_map{0};\n'
                '// Only the native ReloadFinish trace advances the character rebuild generation.\n'
                'std::atomic<u64> bbsc_character_reload_generation{0};')
    lua = originals['void WriteLuaSessionEvent(']
    start = lua.index('    // The guest accepted a summon:')
    behavior = lua[start:-1].rstrip()
    handler = 'void HandleBbscLuaSessionEvent(std::string_view name) {\n' + behavior + '\n}\n\n'
    text = once(text, lua, handler + lua[:start] + '}')
    text = once(text, '    const auto& site = Sites[tag];\n',
                '    const auto& site = Sites[tag];\n'
                '    if (site.kind == TraceKind::LuaSessionEvent) {\n'
                '        HandleBbscLuaSessionEvent(ReadGuestAscii(registers->rsi, 64));\n'
                '    }\n')
    text = once(text, '    if (site.offset == 0x0178D9C0) {\n        PrepareBbscSummonedType();',
                '    if (site.offset == 0x0178D9C0) {\n'
                '        bbsc_character_reload_generation.fetch_add(1);\n'
                '        PrepareBbscSummonedType();')
    text = once(text, 'std::map<u64, u32> bbsc_pvp_original_team; // character -> its team before PvP\n', '')
    text = once(text, 'std::atomic<u32> bbsc_relay_locked_host{0};',
                'std::atomic<u32> bbsc_relay_locked_host{0};\n'
                'std::atomic<u64> bbsc_relay_selection_generation{0};')
    text = once(text, 'std::atomic<s64> bbsc_pvp_host_state_ms{0};',
                'std::atomic<s64> bbsc_pvp_host_state_ms{0};\n'
                'std::mutex bbsc_pvp_packet_mutex;\n'
                'u32 bbsc_pvp_message_sender = 0;\n'
                'u64 bbsc_pvp_message_generation = 0;')
    text = once(text, originals['u32 BbscRelayHostForPackets() {'],
                'u32 BbscRelayHostForPackets() {\n'
                '    return bbsc_relay_locked_host.load(); // No unfiltered single-broadcaster fallback.\n}')
    text = once(text, originals['void TickBbscRelayHostLock() {'], RELAY_LOCK)
    text = once(text, originals['void ApplyBbscPvpTeams() {'], PVP_HELPERS + '\n\n' + PVP_TEAMS)
    text = once(text, originals['void OnBbscPvpPacket('], PVP_PACKET)
    tick = originals['void TickBbscPvp() {']
    tick = once(tick, '        } else if (state == 6) {', r'''        } else if (state == 6 && BbscPvpRequireRoomBinding()) {
            BbscAnnounce(u"\u623f\u95f4\u8eab\u4efd\u5c1a\u672a\u9a8c\u8bc1\uff0c\u4e92\u4f24\u4fdd\u6301\u5173\u95ed\u3002", "pvp authority unavailable");
        } else if (state == 6) {''')
    tick = once(tick, '    // The bell: one toggle per ring (the effect lasts 1 s).', '''    static u64 previous_matching = 0, previous_selection = 0;
    static s64 previous_leave_ms = 0;
    static s32 previous_state = -1;
    const u64 matching = GetMatchingState();
    const u64 selection = bbsc_relay_selection_generation.load();
    const s64 leave_ms = bbsc_leave_event_ms.load();
    if (matching != previous_matching || selection != previous_selection ||
        leave_ms != previous_leave_ms || state != previous_state) {
        ClearBbscPvpHostState();
        SetBbscPvp(false, "the session or selected host changed");
    }
    previous_matching = matching;
    previous_selection = selection;
    previous_leave_ms = leave_ms;
    previous_state = state;
    // The bell: one toggle per ring (the effect lasts 1 s).''')
    tick = once(tick, '''    } else if (state == 6) {
        const int host = bbsc_pvp_host_state;
        if (host >= 0 && now - bbsc_pvp_host_state_ms.load() < 5'000) {
            SetBbscPvp(host != 0, "the host's state");
        }
    } else if (bbsc_pvp_on) {
        SetBbscPvp(false, "the co-op ended");
    }''', '''    } else if (state == 6) {
        int host_state = -1;
        {
            std::scoped_lock lock{bbsc_pvp_packet_mutex};
            const s64 age = now - bbsc_pvp_host_state_ms.load();
            if (!BbscPvpRequireRoomBinding() && bbsc_pvp_message_sender != 0 &&
                bbsc_pvp_message_sender == BbscRelayHostForPackets() &&
                bbsc_pvp_message_generation == bbsc_relay_selection_generation.load() &&
                age >= 0 && age < 5'000) host_state = bbsc_pvp_host_state.load();
            if (host_state < 0) {
                bbsc_pvp_host_state = -1;
                bbsc_pvp_host_state_ms = 0;
                bbsc_pvp_message_sender = 0;
                bbsc_pvp_message_generation = 0;
            }
        }
        SetBbscPvp(host_state == 1, host_state >= 0 ? "the selected host's state" : "host state expired or unavailable");
    } else {
        ClearBbscPvpHostState();
        SetBbscPvp(false, "the co-op ended");
    }''')
    text = once(text, originals['void TickBbscPvp() {'], tick)
    text = once(text, originals['void TickBbscDuelBellGive() {'], KIT_GIVE)
    text = once(text, '''bool BossFightActive() {
    const u64 gauge = ReadValue<u64>(image_base + BossGaugePointerOffset, 0);
    if (!ReadablePointer(gauge, 0x1690)) {
        return false;
    }
    for (int slot = 0; slot < 3; ++slot) {
        if (ReadValue<s32>(gauge, 0x165C + static_cast<size_t>(slot) * 0x14) != -1) {
            return true;
        }
    }
    return false;
}''', '''bool BossFightActive() {
    const u64 gauge = ReadValue<u64>(image_base + BossGaugePointerOffset, 0);
    if (!ReadablePointer(gauge, 0x1690)) {
        return false;
    }
    for (int slot = 0; slot < 3; ++slot) {
        if (ReadValue<s32>(gauge, 0x165C + static_cast<size_t>(slot) * 0x14) != -1) {
            const u64 chr = ReadValue<u64>(gauge, 0x165C + static_cast<size_t>(slot) * 0x14);
            if (GetCharacterHp(chr) > 0) {
                return true;
            }
        }
    }
    return false;
}''')
    text = once(text, '''bool BbscBossFightAtDeath() {
    if (BossFightActive()) {
        return true;
    }
    return bbsc_boss_bar_seen_ms != 0''', '''bool BbscBossFightAtDeath() {
    if (BossFightActive()) {
        return true;
    }
    const u64 gauge = ReadValue<u64>(image_base + BossGaugePointerOffset, 0);
    if (ReadablePointer(gauge, 0x1690)) {
        for (int slot = 0; slot < 3; ++slot) {
            const size_t at = 0x165C + static_cast<size_t>(slot) * 0x14;
            if (ReadValue<s32>(gauge, at) != -1 && GetCharacterHp(ReadValue<u64>(gauge, at)) == 0) {
                LOG_INFO(Debug, "BBSC boss fight: boss bar seen but HP=0, skipped (slot {})", slot);
                break;
            }
        }
    }
    return bbsc_boss_bar_seen_ms != 0''')
    text = once(text, "constexpr s64 BbscBossBarMemoryMs = 10'000;",
                "constexpr s64 BbscBossBarMemoryMs = 10'000;\n"
                "constexpr s64 BbscBossSpectateTimeoutMs = 90'000; // boss fight spectate max wait")
    spectate = once(originals['void TickBbscSpectate() {'], '''                respawn_why = "boss killed";
            }
        }
        if (!respawn_why.empty()) {''', '''                respawn_why = "boss killed";
            }
        } else if (bbsc_death_mode == BbscDeathMode::Spectate &&
                   now - bbsc_spectate_since_ms > BbscBossSpectateTimeoutMs) {
            bbsc_death_mode = BbscDeathMode::RespawnNow;
            LOG_WARNING(Debug, "BBSC spectate: boss spectate timeout after {} s, respawning in place",
                        BbscBossSpectateTimeoutMs / 1'000);
            respawn_why = "boss spectate timeout";
        }
        if (!respawn_why.empty()) {''')
    return once(text, originals['void TickBbscSpectate() {'], spectate)


def apply_relay(text):
    return once(text, 'Core::Bloodborne::OnBbscPvpPacket(frame[5], frame[6]);',
                'Core::Bloodborne::OnBbscPvpPacket(frame[5], frame[6], sender);')


def apply_header(text):
    original = 'void OnBbscPvpPacket(std::uint8_t kind, std::uint8_t on);'
    metadata = 'void OnBbscPvpPacket(std::uint8_t kind, std::uint8_t on, std::uint32_t sender);'
    if metadata in text:
        raise ValueError('PvP sender overload already declared')
    # Keep the original emulator declaration; only the mod relay uses the new overload.
    return once(text, original, original + '\n' + metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='prepared BBSC tree (contains emulator/)')
    args = parser.parse_args()
    core = args.source / 'emulator/src/core'
    path = core / 'bloodborne_re_zh_cn.cpp'
    updated = apply(path.read_text('utf-8'))
    relay = args.source / 'emulator/mod/bbsc_relay.cpp'
    header = core / 'bloodborne_re.h'
    updated_relay = apply_relay(relay.read_text('utf-8'))
    updated_header = apply_header(header.read_text('utf-8'))
    shutil.copy2(ROOT / 'patches/bbsc_coop_effect_restore.inc', core)
    path.write_text(updated, encoding='utf-8', newline='\n')
    relay.write_text(updated_relay, encoding='utf-8', newline='\n')
    header.write_text(updated_header, encoding='utf-8', newline='\n')
    print('Prepared game hardening; trusted-network PvP retained, strict room-binding mode is opt-in.')


if __name__ == '__main__':
    main()
