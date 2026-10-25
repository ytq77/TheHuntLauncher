"""Close only the empty host room left after a confirmed Stop Hunt Bell action."""
def apply(text):
    replacements = (
        ('#include "core/bbsc_memscan.h"',
         '#include "core/bbsc_memscan.h"\n#include "bbsc_empty_host_cancel_policy.h"'),
        ('void TickBbscHostBlank() {',
         '#include "bbsc_empty_host_cancel.inc"\n\nvoid TickBbscHostBlank() {'),
        ('    TickBbscHostBlank();',
         '    TickBbscHostBlank();\n    TickBbscEmptyHostCancel();'),
        ('    InstallBbscQuitHook();',
         '    InstallBbscQuitHook();\n    InstallBbscEmptyHostCancelHook();'),
    )
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('Unexpected BBSC cancel implementation; recheck pinned source: ' + before)
        text = text.replace(before, after)
    return text
