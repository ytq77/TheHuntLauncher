"""Add bounded event-message diagnostics to the pinned BBSC source."""

OLD_READER = '''std::string BbscBannerText() {
    // Every banner in the 2026-10-06 logs read "" as a pointer: the producers copy the text INTO the
    // banner block (see "Co-op banner lines": "copied into the banner ... 0x055813D8 on"), so the
    // bytes at 0x055813E0 are the characters themselves when they are not a readable pointer.
    // HYPOTHESIS until a log shows real banner text.
    u64 text = ReadValue<u64>(image_base + BannerTextOffset, 0);
    if (!ReadablePointer(text, 2)) {
        text = image_base + BannerTextOffset;
    }
    std::string out;
    for (size_t i = 0; i < 120 && ReadablePointer(text + i * 2, 2); ++i) {
        const u16 c = ReadValue<u16>(text, i * 2);
        if (c == 0) {
            break;
        }
        out.push_back(c == '\\n' ? ' ' : (c < 0x80 ? static_cast<char>(c) : '?'));
    }
    return out;
}'''

OLD_INSTALLED = '    LOG_INFO(Debug, "BBSC banner: banners logged; the ones a joining guest gets are hidden");'
NEW_INSTALLED = '''    // Verified against the fixed game's 0x01AE78B0 prologue (six whole bytes).
    constexpr u64 EventTextOffset = 0x01AE78B0;
    if (!InstallGuestCodeHook(reinterpret_cast<void*>(image_base + EventTextOffset),
                              std::span<const u8>{PushR15}, 0, BbscEventTextHook)) {
        LOG_WARNING(Debug, "BBSC event text: diagnostic hook signature mismatch at {:#x}",
                    EventTextOffset);
    } else {
        LOG_INFO(Debug, "BBSC event text: message ID diagnostics installed");
    }
''' + OLD_INSTALLED


def apply(text):
    for before, after in ((OLD_READER, '#include "bbsc_banner_diagnostics.inc"'),
                          (OLD_INSTALLED, NEW_INSTALLED)):
        if text.count(before) != 1:
            raise ValueError("Unexpected BBSC banner implementation; recheck the pinned source.")
        text = text.replace(before, after)
    return text
