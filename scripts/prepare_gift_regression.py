"""Exercise the production type callback against the native common-9040 gift flow."""
import argparse
from pathlib import Path
from prepare_npc_regression import block

ROOT = Path(__file__).resolve().parents[1]


def generate(source, destination):
    text = Path(source).read_text(encoding='utf-8')
    declarations = block(text, 'constexpr std::array<s64, 22> BbscDreamGuestEvents', ';')
    callback = block(text, 'void PS4_SYSV_ABI BbscDreamTypeCheckHook(')
    install = block(text, 'void InstallBbscDreamHooks() {')
    template = (ROOT/'tests/npc_gift_runtime.cpp.in').read_text(encoding='utf-8')
    for marker, value in [('// GIFT_DECLARATIONS',declarations),('// GIFT_CALLBACK',callback),
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
