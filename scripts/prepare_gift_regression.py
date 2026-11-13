"""Exercise the production type callback against the native common-9040 gift flow."""
import argparse
from pathlib import Path
from prepare_npc_regression import block

ROOT = Path(__file__).resolve().parents[1]


def generate(source, destination):
    text = Path(source).read_text(encoding='utf-8')
    declarations = [block(text, 'constexpr std::array<s64, 22> BbscDreamGuestEvents', ';')]
    for marker in ('constexpr u64 TalkClientCheckedOffset =',
                   'constexpr u64 CharacterTypeCheckOffset =',
                   'u8 bbsc_dream_type_skip =',
                   'std::array<u64, 64> bbsc_dream_type_conditions',
                   'size_t bbsc_dream_type_next =',
                   'std::array<s64, 32> bbsc_dream_type_logged'):
        if text.count(marker) != 1:
            raise ValueError('Unexpected gift declaration: '+marker)
        start = text.index(marker)
        declarations.append(text[start:text.index(';', start)+1])
    callbacks = [block(text, marker) for marker in (
        'bool BbscGuestInHostWorld() {',
        's64 BbscEventIdOf(u64 instance) {',
        'void PS4_SYSV_ABI BbscDreamTypeCheckHook(')]
    install = block(text, 'void InstallBbscDreamHooks() {')
    template = (ROOT/'tests/npc_gift_runtime.cpp.in').read_text(encoding='utf-8')
    for marker, value in [('// GIFT_DECLARATIONS','\n'.join(declarations)),
                          ('// GIFT_CALLBACK','\n'.join(callbacks)),
                          ('// GIFT_INSTALL',install)]:
        if template.count(marker) != 1:
            raise ValueError('Unexpected gift fixture marker: '+marker)
        template = template.replace(marker,value,1)
    Path(destination).write_text(template,encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    generate(args.source,args.destination)
    print('Generated native gift regression from the production type callback.')
