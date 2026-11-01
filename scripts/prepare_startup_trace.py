"""Generate a separate observation build from an explicitly verified core."""
import argparse
import hashlib
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    '0f612e75c80eedbcba48e6f79da2b00f4f744c874d228e0d42baf6d4fb020474',  # bell candidate 1
    '603e102594a1d1321834a3273a020a53ae51e0d50fea43d66050cc16dd49c2d0',  # personal NPC gifts
}


def prepare(source, output):
    text = source.read_text(encoding='utf-8')
    if hashlib.sha256(text.encode('utf-8')).hexdigest() not in EXPECTED:
        raise ValueError('Startup diagnostic requires an explicitly verified core.')
    replacements = (
        ('void PS4_SYSV_ABI TraceEntry(u64 tag, const GuestRegisterSnapshot* registers) {',
         '#include "bbsc_startup_timing.inc"\n\n'
         'void PS4_SYSV_ABI TraceEntry(u64 tag, const GuestRegisterSnapshot* registers) {'),
        ('    const auto& site = Sites[tag];',
         '    const auto& site = Sites[tag];\n    BbscStartupScope startup_scope(site.offset);'),
        ('    InstallBbscBlankMenuHook();',
         '    InstallBbscBlankMenuHook();\n    InstallBbscStartupTimingHooks();'),
    )
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('Unexpected startup trace marker: ' + before)
        text = text.replace(before, after, 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding='utf-8', newline='\n')
    shutil.copy2(ROOT/'scripts/startup_trace/bbsc_startup_timing.inc', output.parent)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    prepare(args.source, args.output)
    print('Generated diagnostic core; normal release inputs are unchanged.')
