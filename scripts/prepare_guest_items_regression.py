"""Exercise the actual periodic guest pickup repair against container states."""
import argparse
from pathlib import Path
from prepare_npc_regression import block

ROOT = Path(__file__).resolve().parents[1]


def generate(source, destination):
    actual = block(Path(source).read_text('utf-8'), 'void TickBbscGuestItemsPickable() {')
    template = (ROOT / 'tests/guest_items_runtime.cpp.in').read_text('utf-8')
    assert template.count('// ACTUAL_FUNCTION') == 1
    Path(destination).write_text(template.replace('// ACTUAL_FUNCTION', actual), 'utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    generate(args.source, args.destination)
