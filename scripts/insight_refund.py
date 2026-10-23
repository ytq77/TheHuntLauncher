"""Prevent the fixed game's refund flag from arming for BBSC's zero-cost bells."""


def apply(text):
    replacements = (
        ('// ---- The BBSC hunter\'s kit (2026-10-05)',
         '#include "bbsc_insight_refund.inc"\n\n// ---- The BBSC hunter\'s kit (2026-10-05)'),
        ('        WriteValue<u8>(row, 56, 0);              // consumeHeroPoint: no Insight',
         '        WriteValue<u8>(row, 56, 0);              // consumeHeroPoint: no Insight\n'
         '        if (item.id == 4016 && ReadValue<u8>(row, 56) == 0) {\n'
         '            bbsc_zero_cost_bell_ready.store(true, std::memory_order_release);\n'
         '        }'),
        ('    done = true;\n    LOG_INFO(Debug, "BBSC free bell: Beckoning Bell Insight cost {} -> {}", cost,',
         '    if (ReadValue<u8>(result.row, 0x38) == 0) {\n'
         '        bbsc_zero_cost_bell_ready.store(true, std::memory_order_release);\n'
         '    }\n'
         '    done = true;\n    LOG_INFO(Debug, "BBSC free bell: Beckoning Bell Insight cost {} -> {}", cost,'),
        ('    StartBbscDuelBell(); // Prepare saved kit rows before entering a character.',
         '    InstallBbscInsightRefundGuard();\n'
         '    StartBbscDuelBell(); // Prepare saved kit rows before entering a character.'),
    )
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('Unexpected BBSC free-bell implementation; recheck the pinned source.')
        text = text.replace(before, after)
    return text
