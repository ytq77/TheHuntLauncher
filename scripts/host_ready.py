"""Version-pinned integration for read-only host readiness and reverse display."""
def once(text, before, after):
    if after in text:
        return text
    if text.count(before) != 1:
        raise ValueError('Host readiness integration anchor changed: ' + before[:90])
    return text.replace(before, after, 1)


def apply(text):
    text=once(text,'std::atomic<u64> bbsc_character_reload_generation{0};',
        '''std::atomic<u64> bbsc_character_reload_generation{0};
void ArmBbscHostReadyPlacement(u64,u32,u64,u64);
bool BbscHostNativeReady(u64,u32,u64,u64);
void TickBbscHostReady();
bool BbscHostReadyHooksAvailable();
bool BbscReverseCoopSession();
void RecordBbscHostReadyPad(u64,const GuestRegisterSnapshot*);
bool BbscHostDisplayReady(u64&,u64&,u64&);
void InvalidateBbscHostDisplayActor(u64);
void InvalidateBbscHostDisplayLife(u32);
u64 BbscLoadProtectionRoleEpoch();''')
    text=once(text,'#include "bbsc_load_protection.inc"',
        '#include "bbsc_load_protection.inc"\n#include "bbsc_host_ready.inc"')
    text=once(text,'        TickBbscDeathCurtain();',
        '        TickBbscHostReady();\n        TickBbscDeathCurtain();')
    # This is after accepted life sequence validation and AFTER releasing life_mutex.
    text=once(text,'    if (changed) {\n        LOG_INFO(Debug, "BBSC life: player',
        '    if (changed) InvalidateBbscHostDisplayLife(player);\n    if (changed) {\n        LOG_INFO(Debug, "BBSC life: player')
    text=once(text,'        using PhysicsReset = void PS4_SYSV_ABI (*)(u64 chr, u8 flag);',
        '        InvalidateBbscHostDisplayActor(chr);\n        using PhysicsReset = void PS4_SYSV_ABI (*)(u64 chr, u8 flag);')
    text=once(text,'    bbsc_inplace_respawn_ms = SteadyNowMs();',
        '    bbsc_inplace_respawn_ms = SteadyNowMs();\n'
        '    if (GetSessionState()==3 && GetLocalPlayerType()==0)\n'
        '        ArmBbscHostReadyPlacement(GetLocalPlayerCharacter(),GetCurrentPackedMap(),\n'
        '            bbsc_character_reload_generation.load(),BbscLoadProtectionRoleEpoch());')
    return text
