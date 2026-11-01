"""Repair the recorded pending-host override at the existing native reload hook."""


def apply(text):
    replacements = (
        ('std::vector<u64> GetMultiPlayerInsTasks() {',
         '#include "bbsc_summon_type.inc"\n\nstd::vector<u64> GetMultiPlayerInsTasks() {'),
        ('        BbscConnectFirstReloadFinished();',
         '        PrepareBbscSummonedType();\n        BbscConnectFirstReloadFinished();'),
        ('void TickBbscHostBlank() {',
         '#include "bbsc_blank_menu.inc"\n\nvoid TickBbscHostBlank() {'),
        ('    InstallBbscEmptyHostCancelHook();',
         '    InstallBbscEmptyHostCancelHook();\n    InstallBbscBlankMenuHook();'),
        # Existing detailed goods traces silently excluded the custom kit IDs.
        # Preserve their existing caps; capture the unresolved native Blank path.
        ('constexpr std::array TrackedGoods{100, 111, 200, 201, 205, 206, 225, 226};',
         'constexpr std::array TrackedGoods{100, 111, 200, 201, 205, 206, 225, 226,\n'
         '                                      4016, 4121, 11201, 4313, 11251};'),
    )
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('Unexpected summoned-type implementation: ' + before)
        text = text.replace(before, after, 1)
    return text
