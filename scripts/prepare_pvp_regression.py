"""Extract the actual prepared PvP callbacks for native behavioral checks."""
import argparse
from pathlib import Path
from prepare_npc_regression import block

ROOT = Path(__file__).resolve().parents[1]


def generate(source, destination):
    text = Path(source).read_text('utf-8')
    callbacks = '\n'.join(block(text, marker) for marker in (
        'void SetBbscPvp(bool on, std::string_view why) {',
        'void TickBbscPvp() {',
        'void OnBbscPvpPacket(std::uint8_t kind, std::uint8_t on) {'))
    template = (ROOT / 'tests/pvp_runtime.cpp.in').read_text('utf-8')
    assert template.count('// PVP_CALLBACKS') == 1
    Path(destination).write_text(template.replace('// PVP_CALLBACKS', callbacks), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    generate(args.source, args.destination)
