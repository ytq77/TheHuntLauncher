"""Initialize a new repeat-death spectator episode at first HP-zero observation.

Pure fail-closed/idempotent text transform. Does not perform filesystem writes.
First native-death entry, watchdog timing, real-wipe routing and guards remain
unchanged. Sampling at the first death observation keeps real peer revivals that
arrive during the 3.5-second repeat wait; sampling at admission would erase them.
"""

import re

BEFORE = '        if (!bbsc_spectate_active) {\n            bbsc_echoes_at_death = GetBloodEchoes();\n        }\n        return;'
AFTER = '        if (!bbsc_spectate_active) {\n            bbsc_echoes_at_death = GetBloodEchoes();\n            // A repeat Boss death has no native final step to initialize its\n            // spectator episode. Snapshot on the first dead observation, before\n            // the 3.5 s wait, so a real partner revival in that wait is retained.\n            bbsc_spectate_chr = player;\n            bbsc_spectate_logged_wait = false;\n            bbsc_spectate_saw_wipe = false;\n            bbsc_spectate_wipe_warped = false;\n            bbsc_spectate_host_back_ms = 0;\n            bbsc_spectate_revivals_at_death = bbsc_peer_revivals.load();\n        }\n        return;'


def _watchdog_bounds(text):
    marker = 'void TickBbscDeadWatchdog() {'
    if text.count(marker) != 1:
        raise ValueError('Unexpected watchdog definition count')
    start = text.index(marker)
    first = text.index('{', start)
    depth = 0
    tokens = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|(?<![\w])\'(?:\\.|[^\'\\\r\n])*\'|[{}]')
    for token in tokens.finditer(text, first):
        value = token.group()
        depth += (value == '{') - (value == '}')
        if depth == 0:
            return start, token.end()
    raise ValueError('Unclosed watchdog body')


def apply(text):
    start, end = _watchdog_bounds(text)
    body = text[start:end]
    # Never match an identically shaped block in a later, unrelated function.
    if text.count(BEFORE) != body.count(BEFORE) or text.count(AFTER) != body.count(AFTER):
        raise ValueError('Repeat-death episode block outside the watchdog')
    if body.count(AFTER) == 1:
        if BEFORE in body:
            raise ValueError('Ambiguous old/new repeat-death episode blocks')
        return text
    if body.count(BEFORE) != 1:
        raise ValueError('Unexpected repeat-death observation block')
    return text[:start] + body.replace(BEFORE, AFTER, 1) + text[end:]
