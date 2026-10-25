"""Capture trusted Dream globals before vanilla co-op overlays the live table."""

def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unexpected source marker: ' + before[:100])
    return text.replace(before, after, 1)


def apply(text):
    text = once(text, 'void KeepBbscOwnWorldGroup(u32 key, const u8* live); // see "Own world journal"',
                'void KeepBbscOwnWorldGroup(u32 key, const u8* live); // see "Own world journal"\n'
                'const u8* BbscOwnDreamBaseline(u32 key, const u8* live);')
    text = once(text, '''        KeepBbscOwnWorldGroup(BbscDreamStateKeys[i], live);
        if ((bbsc_dream_own.held & (u64{1} << i)) == 0) {
            std::memcpy(bbsc_dream_own.own[i].data(), live, BbscWorldGroupBytes);''',
                '''        // Vanilla co-op has already overlaid common flags by this point.
        // Only the pre-join journal can identify our own global quest/shop state.
        const u8* own = BbscOwnDreamBaseline(BbscDreamStateKeys[i], live);
        if (own == nullptr) {
            LOG_WARNING(Debug, "BBSC Dream state ({}): group {} has no pre-join baseline; left alone",
                        why, BbscDreamStateKeys[i]);
            continue;
        }
        if ((bbsc_dream_own.held & (u64{1} << i)) == 0) {
            std::memcpy(bbsc_dream_own.own[i].data(), own, BbscWorldGroupBytes);''')
    text = once(text, '        if (BbscIsMapFlagGroup(key)) {\n            keys.push_back(key);',
                '''        if (BbscIsMapFlagGroup(key) ||
            std::find(BbscDreamStateKeys.begin(), BbscDreamStateKeys.end(), key) !=
                BbscDreamStateKeys.end()) {
            keys.push_back(key);''')
    text = once(text, '// 2026-10-12 (a tester, guest, 21:11:20):', '''// Global Dream flags must have been captured before vanilla co-op replaced them.
// Retain the existing lazy fallback only for map-local groups.
const u8* BbscOwnDreamBaseline(u32 key, const u8* live) {
    if (BbscIsMapFlagGroup(key)) {
        KeepBbscOwnWorldGroup(key, live);
    }
    const auto it = bbsc_own_world.find(key);
    return it != bbsc_own_world.end() ? it->second.data() : nullptr;
}

// 2026-10-12 (a tester, guest, 21:11:20):''')
    text = once(text, '// game thread, before the load): every map group of kinds 1/5/6/7 in the flag table.',
                '// game thread, before the load): every map group of kinds 1/5/6/7 and the\n'
                '// Dream globals, before vanilla co-op can replace their live values.')
    text = once(text, 'kept before the join load - {} map flag group(s) of {} in the table, ',
                'kept before the join load - {} map/Dream flag group(s) of {} in the table, ')
    return text
