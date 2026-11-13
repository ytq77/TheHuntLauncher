"""Carry the native co-op effects across character rebuilds in a kept session."""


def apply(text):
    changes = [
        ('        WriteValue<u8>(row, 62, 14);       // goodsUseAnim: ring a bell',
         '        // This custom row has no SFX variation of its own. Use the native\n'
         '        // Sinister Bell variation without copying its multiplayer effect.\n'
         '        if (const u64 bell = BbscParamRow(table, 225, GoodsRowSize); bell != 0) {\n'
         '            WriteValue<s32>(row, 4, ReadValue<s32>(bell, 4)); // sfxVariationId\n'
         '        }\n'
         '        WriteValue<u8>(row, 62, 14);       // goodsUseAnim: ring a bell'),
        ('void TickBbscHostBlank() {',
         '#include "bbsc_coop_effect_restore.inc"\n\nvoid TickBbscHostBlank() {'),
        ('        }\n    }\n    if (site.offset == 0x0131E5C0) { // SummonedMapReload.Native:',
         '        }\n        TickBbscCoopEffectRestore();\n    }\n'
         '    if (site.offset == 0x0131E5C0) { // SummonedMapReload.Native:'),
    ]
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError('Unexpected co-op effect restore anchor: ' + before)
        text = text.replace(before, after, 1)
    return text
