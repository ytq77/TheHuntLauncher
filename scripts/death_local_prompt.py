"""Do not broadcast the legacy Lua death banner when local death UI is proved."""
def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Unexpected local-death source marker: " + before[:90])
    return text.replace(before, after, 1)
def apply(text):
    if 'redundant guest Lua death event omitted' in text:
        return text
    text = once(text, 'bool RaiseBbscDeathText(u64 event_object, std::string_view who) {',
        'bool BbscGuestDeathLocalUiOwnsPrompt(u64 global, u64 actor);\n\n'
        'bool RaiseBbscDeathText(u64 event_object, std::string_view who) {')
    begin = text.index('bool RaiseBbscDeathText(u64 event_object, std::string_view who) {')
    end = text.index('\nvoid ShowBbscGuestDeathText', begin)
    body = text[begin:end]
    body = once(body, '    const u64 vtable = ReadValue<u64>(player, 0);',
        '    // Lua 0xFFA can create a small banner and broadcast to peers. It is\n'
        '    // not the native local effect-2 producer. Avoid only BBSC\'s redundant\n'
        '    // extra event for a proved private co-op guest death; preserve all\n'
        '    // other roles and unknown/unobserved local-UI contexts.\n'
        '    const u64 global = GetGlobalState();\n'
        '    if (GetSessionState() == 6 && GetLocalPlayerType() == 1 &&\n'
        '        ReadablePointer(global, 0x1593) && ReadValue<u8>(global, 0x1592) == 0 &&\n'
        '        GetCharacterHp(player) == 0 && BbscGuestDeathLocalUiOwnsPrompt(global, player)) {\n'
        '        LOG_INFO(Debug, "BBSC death text: redundant guest Lua death event omitted ({}, "\n'
        '                        "local effect observed/requested; pixels unverified)", who);\n'
        '        return true;\n'
        '    }\n'
        '    const u64 vtable = ReadValue<u64>(player, 0);')
    text = text[:begin] + body + text[end:]
    text = once(text, '// which is what puts "YOU DIED" on the screen and tells the other session members. The guest\'s',
        '// which queues Lua event 0xFFA locally and to other members, not the native local effect-2 UI.\n'
        '// The independent native death entry queues effect 2 before the death script. The guest\'s')
    text = once(text, '// the same event with the same arguments from the guest\'s own death state.',
        '// that legacy event only when the independent local death-UI path is not proved.\n'
        '// First/repeat local UI is observed/repaired by bbsc_death_ui.inc, without broadcasting 0xFFA.')
    text = once(text, '    LOG_INFO(Debug, "BBSC death text: \\\"YOU DIED\\\" raised for the {} (args {}, {}, accepted {})",',
        '    LOG_INFO(Debug, "BBSC death text: Lua 0xFFA sent for the {} (args {}, {}, accepted {})",')
    text = once(text, '// no death step at all (log: "repeat death in the same load"). BBSC raises the text itself there,',
        '// no death step at all (log: "repeat death in the same load"). The local UI helper repairs effect 2;\n'
        '// this legacy Lua fallback is retained only without proof of that independent local path,')
    text = once(text, 'bool bbsc_death_text_raised = false; // "YOU DIED" raised by BBSC for the current repeat death',
        'bool bbsc_death_text_raised = false; // legacy Lua death notice handled once for this repeat death')
    text = once(text, '// A repeat death gets its "YOU DIED" this long after the HP hit zero, unless the game\'s own death\n// script has started by then (it raises the text itself).',
        '// Consider the legacy Lua death notice after this delay, unless the native death script ran.\n'
        '// Independent local effect-2 UI is observed/repaired by bbsc_death_ui.inc.')
    text = once(text, 'BBSC death text: no Lua event object, repeat death stays silent',
        'BBSC death text: legacy Lua notice not dispatched; local death UI is independent')
    return text
