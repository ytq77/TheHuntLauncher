from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Unexpected wipe source marker: " + before[:90])
    return text.replace(before, after, 1)

def native_own_world(text):
    if '"BBSC.Type.OwnWorldCompleted"' in text:
        return native_parent(text)
    entry = '    {"BBSC.Type.OwnWorldStep", 0x01941C20, TraceKind::MatchingLeaveDecision, StandardR15Prologue, 6},'
    text = once(text, entry, entry + '\n'
        '    {"BBSC.Type.OwnWorldCompleted", 0x01942332, TraceKind::MatchingLeaveDecision,\n'
        '     {0x41,0xC7,0x46,0x0C,0xFF,0xFF,0xFF,0xFF}, 8},')
    marker = '    if (site.offset == 0x0178D9C0) {\n        bbsc_character_reload_generation.fetch_add(1);'
    text = once(text, marker,
        '    // Bind only the native OwnWorldStep belonging to this authenticated wipe.\n'
        '    if (site.offset == 0x01941C20) ObserveBbscWipeOwnWorldStep(registers->rdi, false);\n'
        '    if (site.offset == 0x01942332) { ObserveBbscWipeOwnWorldStep(registers->r14, true); return; }\n'
        + marker)
    return native_parent(text)

def native_parent(text):
    if '"BBSC.Wipe.ParentTransition"' in text:
        return text
    entry = '    {"BBSC.Type.OwnWorldStep", 0x01941C20, TraceKind::MatchingLeaveDecision, StandardR15Prologue, 6},'
    text = once(text, entry, entry + '\n'
        '    {"BBSC.Wipe.ParentTransition", 0x019297E0, TraceKind::MatchingLeaveDecision, StandardR15Prologue, 6},\n'
        '    {"BBSC.Wipe.MapRetirement", 0x01548622, TraceKind::MatchingLeaveDecision,\n'
        '     {0x48,0xC7,0x00,0x00,0x00,0x00,0x00}, 7},\n'
        '    {"BBSC.Wipe.DescriptorConsume", 0x01928680, TraceKind::MatchingLeaveDecision, StandardR15Prologue, 6},')
    marker = '    if (site.offset == 0x0178D9C0) {\n        bbsc_character_reload_generation.fetch_add(1);'
    return once(text, marker,
        '    if (site.offset == 0x019297E0) ObserveBbscWipeParentTransition(registers->rdi, registers->rsp);\n'
        '    if (site.offset == 0x01548622) { ObserveBbscWipeMapRetirement(registers->rax, registers->r13); return; }\n'
        '    if (site.offset == 0x01928680) {\n'
        '        const u64 caller = ReadablePointer(registers->rsp, 8) ? ReadValue<u64>(registers->rsp, 0) : 0;\n'
        '        ApplyBbscWipeRespawnAtDescriptor(registers->rsi, GetGlobalState(), registers->rdi, caller, registers->rbp, registers->rsp);\n'
        '    }\n' + marker)
def apply(text):
    if '#include "bbsc_wipe_respawn.inc"' in text:
        return native_own_world(text)
    text = once(text, 'bool RelayAuthVisibilityContext(TheHuntVisibility::Context&);',
        'bool RelayAuthVisibilityContext(TheHuntVisibility::Context&);\n'
        'bool RelayAuthUseVisibilityContext(const TheHuntVisibility::Context&, bool (*write)(void*), void* target);')
    text = once(text, 'bool RequestBbscSessionWarp(u64 global, const SummonPlacementDescriptor& to,',
        'void ClearBbscWipeRespawnTarget();\n'
        'void ArmBbscWipeRespawnTarget(u64 global, const SummonPlacementDescriptor& to, u64 ticket);\n\n'
        'bool RequestBbscSessionWarp(u64 global, const SummonPlacementDescriptor& to,')
    text = once(text, '                            BbscFollowLoadProtection* protection = nullptr) {',
        '                            BbscFollowLoadProtection* protection = nullptr) {\n'
        '    if (why == \"wipe\") ClearBbscWipeRespawnTarget();')
    text = once(text, '    const u64 result = reinterpret_cast<StageTransition>(image_base + StageTransitionOffset)();',
        '    const u64 result = reinterpret_cast<StageTransition>(image_base + StageTransitionOffset)();\n'
        '    if (why == \"wipe\" && result == 0) ClearBbscWipeRespawnTarget();')
    text = once(text, '    if (protection) *protection = {ticket, bbsc_character_reload_generation.load()};',
        '    if (protection) *protection = {ticket, bbsc_character_reload_generation.load()};\n'
        '    if (why == "wipe") ArmBbscWipeRespawnTarget(global, to, ticket);')
    text = once(text, 'void TickBbscSpectate() {',
        '#include "bbsc_wipe_respawn.inc"\n\nvoid TickBbscSpectate() {')
    text = once(text, '        ApplySummonReloadStateAtDescriptor(*registers);',
        '        ApplyBbscWipeRespawnAtDescriptor(registers->r9, GetGlobalState());\n'
        '        ApplySummonReloadStateAtDescriptor(*registers);')
    # Bind provenance only after the existing sequence/freshness acceptance.
    text = once(text, '    bbsc_follow_latest = {packed_map, x, y, z, heading, sequence, lamp_point, now};',
        '    bbsc_wipe_follow_context = wipe_context;\n'
        '    bbsc_follow_latest = {packed_map, x, y, z, heading, sequence, lamp_point, now};')
    marker = 'void OnBbscHostFollowPacket(std::uint32_t packed_map, float x, float y, float z, float heading,'
    begin = text.index(marker)
    before = text[:begin]
    tail = text[begin:]
    tail = once(tail, '    const s64 now = SteadyNowMs();\n    std::scoped_lock lock{bbsc_follow_mutex};',
        '    TheHuntVisibility::Context wipe_context;\n'
        '    BbscMod::RelayAuthVisibilityContext(wipe_context);\n'
        '    const s64 now = SteadyNowMs();\n    std::scoped_lock lock{bbsc_follow_mutex};')
    return native_own_world(before + tail)
