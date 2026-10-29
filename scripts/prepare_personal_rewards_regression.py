"""Generate an executable fixture around the actual prepared hooks and adapter."""
import argparse
from pathlib import Path
from prepare_npc_regression import block

ROOT=Path(__file__).resolve().parents[1]

def generate(source,destination):
    text=Path(source).read_text(encoding='utf-8')
    declarations='\n'.join(block(text,marker,';') for marker in (
        'constexpr std::array<s64, 22> BbscDreamGuestEvents',
        'std::array<u64, 64> bbsc_dream_type_conditions',
        'std::array<s64, 32> bbsc_dream_type_logged'))
    # The array aggregate declarations above have empty {} initializers.
    functions='\n'.join(block(text,marker) for marker in (
        'void PS4_SYSV_ABI BbscDreamTypeCheckHook(',
        'u32 BbscCorpseOwner(u64 manager, u32 id) {',
        'void PS4_SYSV_ABI BbscPickupHook('))
    start=text.index('    const auto keep_own_dream_messengers = []')
    end=text.index('    const auto write_block =',start)
    badge_lambda=text[start:end]
    template=(ROOT/'tests/personal_rewards_runtime.cpp.in').read_text(encoding='utf-8')
    for marker,value in [('// ACTUAL_DECLARATIONS',declarations),('// ACTUAL_FUNCTIONS',functions),('// ACTUAL_BADGE_GUARD',badge_lambda)]:
        if template.count(marker)!=1:raise ValueError('Unexpected fixture marker: '+marker)
        template=template.replace(marker,value)
    Path(destination).write_text(template,encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('destination')
    args=parser.parse_args();generate(args.source,args.destination)
