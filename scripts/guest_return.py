"""Restore the personal return placement and start native home travel on session loss."""

def apply(text):
    changes = [
        ('void CaptureBbscOwnWorldBeforeJoin() {',
         '#include "bbsc_guest_return.inc"\n\nvoid CaptureBbscOwnWorldBeforeJoin() {'),
        ('    size_t kept = 0;\n    size_t items = 0;\n    for (const u32 key : keys) {',
         '    CaptureBbscGuestReturnOrigin();\n    size_t kept = 0;\n    size_t items = 0;\n    for (const u32 key : keys) {'),
        ('        SuspendBbscPersistence("guest sent home: session ended in the host\'s world");',
         '        SuspendBbscPersistence("guest sent home: session ended in the host\'s world");\n'
         '        ReturnBbscGuestAfterSessionEnd();'),
        ('    const u32 forced_map = ReadValue<u32>(global, 0x14F0);\n    // Mod 24 only cleared',
         '    RestoreBbscGuestReturnPlacement(global);\n'
         '    const u32 forced_map = ReadValue<u32>(global, 0x14F0);\n    // Mod 24 only cleared'),
        ('    if (!BbscOwnWorldNonGuest(global)) {\n        LOG_INFO(Debug, "BBSC leave:',
         '    if (!BbscOwnWorldNonGuest(global)) {\n        RestoreBbscGuestReturnPlacement(global);\n'
         '        LOG_INFO(Debug, "BBSC leave:'),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError('Unexpected guest-return anchor: ' + before[:100])
        text = text.replace(before, after, 1)
    return text
