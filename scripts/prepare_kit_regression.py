"""Exercise actual kit preparation and inventory callbacks in a native fixture."""
import argparse
from pathlib import Path
from prepare_npc_regression import block

ROOT=Path(__file__).resolve().parents[1]


def generate(source,destination):
    core=Path(source).parent;text=Path(source).read_text('utf-8')
    declarations='\n'.join(block(text,marker,';') for marker in (
        'struct BbscKitItem {','constexpr BbscKitItem BbscKit[] =',
        'constexpr u32 BbscHiddenVanilla[] ='))
    lookup=block((core/'bbsc_kit_fast_prepare.inc').read_text('utf-8'),'u64 BbscActiveGoodsTable() {')
    helpers=(core/'bbsc_kit_repository.inc' if '#include "bbsc_kit_repository.inc"' in text else
             ROOT/'patches/bbsc_kit_repository.inc').read_text('utf-8')
    # Lookup helpers must precede the actual initializer. All callbacks stay intact.
    callbacks=block(text,'void PrepareBbscKitRows(')+'\n'+lookup+'\n'+helpers+'\n'+\
        block(text,'void PrepareBbscDuelBell() {')+'\n'+block(text,'void TickBbscDuelBellGive() {')
    row_access=block(text,'u64 BbscCheckedParamRow(')+'\n'+block(text,'u64 BbscParamRow(')
    template=(ROOT/'tests/kit_initialization_runtime.cpp.in').read_text('utf-8')
    for marker,value in (('// KIT_DECLARATIONS',declarations),('// ROW_ACCESS',row_access),('// KIT_CALLBACKS',callbacks)):
        if template.count(marker)!=1:raise ValueError('Unexpected kit fixture marker: '+marker)
        template=template.replace(marker,value,1)
    Path(destination).write_text(template,encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('destination')
    args=parser.parse_args();generate(args.source,args.destination)
