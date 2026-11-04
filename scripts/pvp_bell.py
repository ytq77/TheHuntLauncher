"""Keep the Duel Bell independent of recruitment and under host control."""


def apply(text):
    replacements = (
        ('        WriteValue<float>(effect, 8, 1.0f); // effectEndurance: 1 s\n',
         '        WriteValue<float>(effect, 8, 1.0f); // effectEndurance: 1 s\n'
         '        // Category 100 replaces the active recruitment bell. The PvP\n'
         '        // pulse must coexist with it (native category 0 is independent).\n'
         '        WriteValue<u16>(effect, 0x13E, 0); // spCategory: no bell replacement\n'),
        ('std::atomic<int> bbsc_pvp_request{-1}; // host side: a guest asked for this state (0 / 1)\n', ''),
        ('''            SendBbscPvpPacket(1, want);
            LOG_INFO(Debug, "BBSC PvP: the guest rang the Duel Bell - asking the host for {}",
                     want ? "on" : "off");''',
         '''            LOG_INFO(Debug, "BBSC PvP: guest Duel Bell ignored; only the host controls PvP");
            BbscAnnounce(u"\\u7eb7\\u4e89\\u7531\\u623f\\u4e3b\\u638c\\u63a7\\u3002\\n\\u8bf7\\u7531\\u623f\\u4e3b\\u6447\\u54cd\\u7eb7\\u4e89\\u4e4b\\u94c3\\u3002", "pvp host only");'''),
        ('''        if (const int asked = bbsc_pvp_request.exchange(-1); asked >= 0) {
            SetBbscPvp(asked != 0, "a guest rang the Duel Bell");
            SendBbscPvpPacket(0, asked != 0);
        }
''', ''),
        ('''        bbsc_pvp_request = on != 0 ? 1 : 0;
        LOG_INFO(Debug, "BBSC PvP: a guest rang the Duel Bell and asks for {}", on ? "on" : "off");''',
         '''        // Old clients can still send requests. Never change the host's
        // state in response; only its local Duel Bell is authoritative.
        LOG_INFO(Debug, "BBSC PvP: guest request for {} ignored; host-only control", on ? "on" : "off");'''),
        ('''//   * Agreement: the host decides. Relay "SHAD" 0x25 + u8 kind + u8 on + u32 sequence; kind 0 = the
//     host's state (sent every second while hosting), kind 1 = a guest rang the bell and asks for
//     that state. A guest follows the host's state.''',
         '''//   * Agreement: only the host's local bell changes PvP. Relay "SHAD" 0x25 + u8 kind + u8 on +
//     u32 sequence; kind 0 broadcasts its state every second. Legacy kind 1 guest requests are
//     ignored. A guest follows the host's state.'''),
        ('''//     a copy of the Beckoning Bell's 9000 without its special state (stateInfo +0x156 = 0: no sign)
//     that lasts 1 s.''',
         '''//     a copy of the Beckoning Bell's 9000 without its special state (stateInfo +0x156 = 0: no sign)
//     or exclusive bell category (spCategory +0x13E = 0), lasting 1 s.'''),
    )
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('Unexpected pinned PvP implementation: ' + before[:100])
        text = text.replace(before, after, 1)
    return text
