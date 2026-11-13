from pathlib import Path
"""Bounded same-map guest respawn presentation; no readiness or damage changes."""

def apply_guest(text):
    if 'same-map guest: no synthetic loading wait' in text:
        return text
    text=text.replace('void TickBbscDeathCurtain() {', 'u64 BbscLoadProtectionRoleEpoch();\n\n'
        'bool BbscRespawnNativeScreenIdle() {\n'
        '    const u64 menu = ReadValue<u64>(image_base + MenuManPointerOffset, 0);\n'
        '    if (!ReadablePointer(menu, 0x808) ||\n'
        '        !HasMemoryAccess(image_base + 0x0556286B, 1, MemoryProt::CpuRead)) return false;\n'
        '    const s32 state = ReadValue<s32>(menu, 0x804);\n'
        '    return (state == 0 || state == 3) && ReadValue<u8>(image_base + 0x0556286B, 0) == 0;\n'
        '}\n\nvoid TickBbscDeathCurtain() {')
    old = '    case BbscCurtainStep::FadingOut:\n'
    new = '''    case BbscCurtainStep::FadingOut: {
        const u64 respawn_global = GetGlobalState();
        const u64 respawn_actor = GetLocalPlayerCharacter();
        const u32 respawn_map = GetCurrentPackedMap();
        const u64 respawn_reload = bbsc_character_reload_generation.load();
        const u64 respawn_role = BbscLoadProtectionRoleEpoch();
        const bool same_map_guest = GetSessionState() == 6 && GetLocalPlayerType() == 1 &&
            ReadablePointer(respawn_global, 0x1593) &&
            ReadValue<u8>(respawn_global, 0x1592) == 0 &&
            ReadValue<u8>(respawn_global, 0x08) == 0 &&
            IsUsablePackedMap(respawn_map) && !bbsc_load_cover.active && BbscRespawnNativeScreenIdle();
'''
    assert text.count(old)==1
    text=text.replace(old,new)
    old='''        bbsc_curtain.shown = BbscLoadingScreen(true);
        BbscCurtainStepTo(BbscCurtainStep::Loading, now);
        return;
    case BbscCurtainStep::Loading:'''
    new='''        // Revive and placement have completed in the current world. Do not open
        // a synthetic loading menu and then wait for it. Readiness deliberately
        // still requires !curtain; protection and peer visibility release later.
        // A real load/cover, host path, or changed actor/world keeps the old path.
        if (same_map_guest && GetSessionState() == 6 && GetLocalPlayerType() == 1 &&
            GetGlobalState() == respawn_global && GetLocalPlayerCharacter() == respawn_actor &&
            GetCurrentPackedMap() == respawn_map &&
            bbsc_character_reload_generation.load() == respawn_reload &&
            BbscLoadProtectionRoleEpoch() == respawn_role &&
            ReadValue<u8>(respawn_global, 0x1592) == 0 && BbscRespawnNativeScreenIdle() &&
            ReadValue<u8>(respawn_global, 0x08) == 0 && !bbsc_load_cover.active &&
            GetCharacterHp(respawn_actor) > 0) {
            EndBbscDeathCurtain(now, "same-map guest: no synthetic loading wait");
            return;
        }
        bbsc_curtain.shown = BbscLoadingScreen(true);
        BbscCurtainStepTo(BbscCurtainStep::Loading, now);
        return;
    }
    case BbscCurtainStep::Loading:'''
    assert text.count(old)==1
    text=text.replace(old,new)
    return text


def apply_death_ui(text):
    if '#include "bbsc_death_ui.inc"' in text:
        return text
    marker = 'void TickBbscDeadWatchdog() {'
    text=text.replace(marker, '#include "bbsc_death_ui.inc"\n\n'+marker)
    old='    const s32 hp = ReadablePointer(player, 0x3B8) ? GetCharacterHp(player) : -1;'
    start=text.index(marker)
    assert text[start:].count(old)==1
    return text[:start]+text[start:].replace(old,old+'\n    TickBbscGuestDeathUi(now, session_state, global, player, hp);',1)


def apply(text):
    text = apply_guest(text)
    if 'host respawn: bounded native-ready wait' in text:
        return text
    text = text.replace('bool StartBbscDeathCurtain(std::string_view why) {', 'void CaptureBbscHostRespawnPartner();\n\nbool StartBbscDeathCurtain(std::string_view why) {', 1)
    marker_start = '    BbscCurtainStepTo(BbscCurtainStep::FadingOut, now);'
    assert text.count(marker_start) == 1
    text = text.replace(marker_start, marker_start + '\n    CaptureBbscHostRespawnPartner();', 1)
    helper = (Path(__file__).resolve().parents[1] / 'patches/bbsc_host_respawn.inc').read_text('utf-8')
    text = text.replace('u64 BbscLoadProtectionRoleEpoch();',
        'u64 BbscLoadProtectionRoleEpoch();\nbool BbscHostRespawnContext(bool require_alive);\n'
        'void BeginBbscHostRespawnWait(s64 now, u64 global, u64 actor, u32 map, u64 reload, u64 role);\n'
        'bool TickBbscHostRespawnWait();\n'
        'void StopBbscHostRespawnCurtain(s64,u64,u64,u32,u64,u64);\nu64 BbscHostRespawnFadePlate();\nbool BbscHostRespawnOwnPlate(u64);', 1)
    text = text.replace('void TickBbscDeathCurtain() {',
        'void TickBbscDeathCurtain() {\n    if (TickBbscHostRespawnWait()) return;', 1)
    old = '        if (in_step < static_cast<s64>(BbscCurtainFadeOutS * 1000.0f) + 100) {'
    assert text.count(old) == 1
    text = text.replace(old, '        const bool same_map_host = BbscHostRespawnContext(false);\n' +
        '        const bool respawn_host = GetSessionState() == 3 && GetLocalPlayerType() == 0 &&\n            ReadablePointer(respawn_global, 0x1593) && ReadValue<u8>(respawn_global, 0x1592) == 1;\n' +
        '        const u64 respawn_plate = BbscHostRespawnFadePlate();\n' +
        '        const u64 respawn_room = BbscMod::RelayAuthRoom();\n' +
        '        const u32 respawn_owner = BbscMod::RelayAuthOwner();\n' + old)
    old = '        bbsc_curtain.shown = BbscLoadingScreen(true);\n        BbscCurtainStepTo(BbscCurtainStep::Loading, now);'
    new = '''        // A native transition may start inside the successful revive call.
        // Never open the synthetic menu over its loading screen.
        if (respawn_host && (!BbscHostRespawnOwnPlate(respawn_plate) || GetGlobalState() != respawn_global ||
            GetLocalPlayerCharacter() != respawn_actor || GetCurrentPackedMap() != respawn_map ||
            bbsc_character_reload_generation.load() != respawn_reload ||
            BbscLoadProtectionRoleEpoch() != respawn_role ||
            GetSessionState() != 3 || GetLocalPlayerType() != 0 ||
            !ReadablePointer(respawn_global, 0x1593) || ReadValue<u8>(respawn_global, 0x08) != 0 ||
            ReadValue<u8>(respawn_global, 0x1592) != 1 || bbsc_load_cover.active ||
            !BbscRespawnNativeScreenIdle() || BbscMod::RelayAuthRoom() != respawn_room ||
            BbscMod::RelayAuthOwner() != respawn_owner)) {
            StopBbscHostRespawnCurtain(now, respawn_global, respawn_actor, respawn_map,
                                      respawn_reload, respawn_plate);
            return;
        }
        // Host is not covered by guest load protection. Keep the fade closed,
        // without opening NowLoading, while native local readiness catches up.
        if (same_map_host && BbscHostRespawnContext(false) &&
            GetGlobalState() == respawn_global && GetLocalPlayerCharacter() == respawn_actor &&
            GetCurrentPackedMap() == respawn_map &&
            bbsc_character_reload_generation.load() == respawn_reload &&
            BbscLoadProtectionRoleEpoch() == respawn_role &&
            BbscMod::RelayAuthRoom() == respawn_room && BbscMod::RelayAuthOwner() == respawn_owner &&
            GetCharacterHp(respawn_actor) > 0) {
            BeginBbscHostRespawnWait(now, respawn_global, respawn_actor, respawn_map,
                                      respawn_reload, respawn_role);
            return;
        }
        if (respawn_host) {
            LOG_INFO(Debug, "BBSC host respawn: admission rejected (before={} after={})",
                     same_map_host, BbscHostRespawnContext(false));
        }
''' + old
    assert text.count(old) == 1
    text = text.replace(old, new)
    marker = '#include "bbsc_host_ready.inc"'
    assert text.count(marker) == 1
    return text.replace(marker, marker + '\n\n' + helper)
