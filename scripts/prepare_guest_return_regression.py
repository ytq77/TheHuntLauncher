"""Compile the changed session-end branch with the real guest-return helpers."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate(source, destination):
    core = Path(source).read_text('utf-8')
    start = core.index('void TickBbscSessionEnd() {')
    end = core.index('    // An invader who beat the host goes home', start)
    branch = core[start:end]+'}\n'
    fixture = (ROOT/'tests/test_guest_return.cpp').read_text('utf-8')
    fixture = fixture.replace('#include "../patches/bbsc_guest_return.inc"',
                              '#include "'+(ROOT/'patches/bbsc_guest_return.inc').as_posix()+'"')
    fixture = fixture.replace('int main(){', 'void test_helpers(){', 1)
    fixture += r'''
using s64=std::int64_t; using s32=std::int32_t;
s64 now=10000,bbsc_persistence_restore_at_ms=0;
int lifted=0;
s64 SteadyNowMs(){return now;}
namespace Core::Bbsc{void SetPersistentSessionPatches(bool){}}
void SuspendBbscPersistence(const char*){++lifted;bbsc_persistence_restore_at_ms=now+15000;}
'''+branch+r'''
int main(){
    test_helpers();
    reset();TickBbscSessionEnd();CaptureBbscGuestReturnOrigin();
    guest();session=6;TickBbscSessionEnd();assert(home_calls==0);
    // Following the host overwrites the native placement, not the captured origin.
    map=0x19000000;global[0x14f0]=0xff;session=0;TickBbscSessionEnd();
    assert(home_calls==1&&lifted==1&&global[8]==1);
    assert(ReadValue<u32>(GetGlobalState(),0x14f0)==0x18010000);
    TickBbscSessionEnd();assert(home_calls==1&&lifted==1);
    // A voluntary teardown already in progress does not start another transition.
    reset();TickBbscSessionEnd();CaptureBbscGuestReturnOrigin();
    guest();session=6;TickBbscSessionEnd();session=0;
    bbsc_persistence_restore_at_ms=now+5000;TickBbscSessionEnd();assert(home_calls==0);
    // Once home the guest history clears, so a later solo tick does not return.
    reset();bbsc_persistence_restore_at_ms=0;TickBbscSessionEnd();guest();
    TickBbscSessionEnd();assert(home_calls==0);
    std::cout<<"PASS actual session-end branch: lost session returns once; follow destination replaced; "
                "voluntary teardown preserved; own-world history cleared\n";
}
'''
    Path(destination).write_text(fixture, 'utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source');parser.add_argument('destination')
    args=parser.parse_args();generate(args.source,args.destination)
