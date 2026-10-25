"""Run the real prepared BBSC functions against synthetic quest states in native checks."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def block(text, marker, suffix=''):
    if text.count(marker)!=1:
        raise ValueError('Unexpected NPC recovery function: '+marker)
    start=text.index(marker)
    first=text.index('{',start)
    depth=0
    for end in range(first,len(text)):
        depth+=(text[end]=='{')-(text[end]=='}')
        if depth==0:return text[start:end+1]+suffix
    raise ValueError('Unclosed NPC recovery function')


def generate(source, destination):
    text=Path(source).read_text(encoding='utf-8')
    declarations=[block(text,'constexpr std::array<u32, 57> BbscDreamStateKeys',';'),
                  block(text,'struct BbscDreamOwnState',';'),
                  block(text,'constexpr std::array<u16, 130> BbscDreamShopUnlockFlags',';')]
    functions=[block(text,marker) for marker in (
        'bool BbscIsMapFlagGroup(u32 key) {',
        'void KeepBbscOwnWorldGroup(u32 key, const u8* live) {',
        'const u8* BbscOwnDreamBaseline(u32 key, const u8* live) {',
        'void CaptureBbscOwnWorldBeforeJoin() {',
        'size_t KeepBbscOwnDreamShopFlags(size_t index, u8* live) {',
        'void ApplyBbscHostDreamState(std::string_view why) {',
        'void NoteBbscOwnDreamRestored(u32 key, const u8* live) {',
        'void RestoreBbscOwnDreamState(std::string_view why) {',
        'bool BbscHomeForOwnWorld() {',
        'size_t RestoreBbscOwnWorld(std::string_view why) {')]
    template=(ROOT/'tests/npc_prejoin_runtime.cpp.in').read_text(encoding='utf-8')
    for marker,value in [('// NPC_DECLARATIONS','\n'.join(declarations)),('// NPC_FUNCTIONS','\n'.join(functions))]:
        if template.count(marker)!=1:raise ValueError('Unexpected native regression template')
        template=template.replace(marker,value,1)
    Path(destination).write_text(template,encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    generate(args.source,args.destination)
    print('Generated NPC regression from actual prepared BBSC function bodies; fixtures are synthetic.')
