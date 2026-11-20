"""Exercise host claims, guest sign creation, invitation turnover and returns using one prepared source tree."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_game_hardening_regression import function

ROOT=Path(__file__).resolve().parents[1]
TESTS=ROOT/'tests'


def run_tests(bbsc,port,sdk):
    emu=bbsc.resolve()/'emulator'
    core=emu/'src/core'
    mod=emu/'mod'
    sdk=sdk.resolve()/'bin'
    cpp=(core/'bloodborne_re_zh_cn.cpp').read_text('utf8')
    http=(mod/'bbsc_http.cpp').read_text('utf8')
    flags=[sdk/'clang++.exe','-std=c++23','-O1','-fms-extensions','-DNOMINMAX','-DWIN32_LEAN_AND_MEAN',
           '-D_CRT_SECURE_NO_WARNINGS','-DBBSC_MOD','-DFMT_HEADER_ONLY']
    flags+=['-I'+p.as_posix() for p in (mod/'shim',mod,emu/'src',core,port.resolve()/'gpu/third_party/xbyak',TESTS/'windows-join')]
    env={**os.environ,'PATH':str(sdk)+os.pathsep+os.environ['PATH'],'THEHUNT_JOIN_CLAIM_V1':'1',
         'BBSC_RELAY_HTTP_ORIGIN':'http://127.0.0.1:31415'}
    with tempfile.TemporaryDirectory(prefix='hunt-role-') as directory:
        tmp=Path(directory)
        def run(args):
            subprocess.run(args,cwd=tmp,env=env,check=True,timeout=100)
        def exe(name,source,extra):
            target=tmp/(name+'.exe')
            run([*flags,'-I'+tmp.as_posix(),*extra,source,'-o',target,'-lws2_32','-lbcrypt','-lwinhttp'])
            return target
        template=(TESTS/'test_relay_auth_http.cpp').read_text('utf8')
        main='int main(int argc,char** argv){'
        assert template.count(main)==1
        guest_sign=r'''
int guest_sign() {
    prepare_slots();
    const std::string base=test_origin+"/summon_messenger/";
    auto send=[&](const char* path) {
        int handle=CreateRequestWithURL(1,1,(base+path+"?user_id=17").c_str(),0);
        assert(SendRequest(handle,nullptr,0)==0);
        const auto& headers=headers_added.at(handle);
        assert(!headers.contains(TheHunt::Summon::claim_origin_header));
        assert(!headers.contains("X-ShadPS4-Bloodborne-Host-Placement"));
        return handle;
    };
    send("create"); int poll=send("get"); send("delete");
    assert(headers_added.at(poll).at(TheHunt::Summon::discovery_header)=="1");
    assert(sent==3);
    std::puts("PASS guest sign, discovery poll and withdrawal with host claims enabled");
    return 0;
}
'''
        src=tmp/'http.cpp'
        src.write_text(template.replace(main,'#include "host_claim.inc"\n'+guest_sign+main+
            '\nif(argc==2 && std::string(argv[1])=="host") return test_bound_http();'+
            '\nif(argc==2 && std::string(argv[1])=="guest") return guest_sign();'),'utf8')
        test=exe('http',src,['-DBBSC_J4_DIAG_DISABLED','-DHTTP_SOURCE="'+(mod/'bbsc_http.cpp').as_posix()+'"'])
        for role in ('host','guest'):
            run([test,role])
        relay_fixture=(TESTS/'test_relay_auth_native.cpp').read_text('utf8')
        for stub in ('bool OnHostPlacement(', 'void RelayAuthClearPlacement() {'):
            relay_fixture=relay_fixture.replace(function(relay_fixture,stub),'')
        (tmp/'fixture.inc').write_text(relay_fixture,'utf8')
        def parts(text,*markers):
            return '\n'.join(function(text,m)+(';' if m.startswith('struct ') else '') for m in markers)
        (tmp/'parse.inc').write_text(parts(cpp,'struct SummonPlacementDescriptor {','bool IsUsablePackedMap(u32 packed_region) {',
            'bool IsSameSummonPlacement(','std::optional<SummonPlacementDescriptor> ParseBbscSummonPlacement(std::string_view value) {'),'utf8')
        (tmp/'cache.inc').write_text(re.search(r'constexpr s64 BbscConnectFirstTimeoutMs = [^;]+;',cpp)[0]+'\n'+
            parts(cpp,'bool BbscConnectFirstPlacementFresh() {','std::optional<SummonPlacementDescriptor> BbscConnectFirstHost() {',
                  'bool SetSeamlessHostPlacementHeader(std::string_view value) {','void ClearSeamlessHostPlacementHeader() {'),'utf8')
        (tmp/'http.inc').write_text(re.search(r'constexpr s64 PlacementStaleMs = [^;]+;',http)[0]+'\n'+
            parts(http,'void RelayAuthClearPlacement() {','bool OnHostPlacement(const std::string& placement, u32 sender) {'),'utf8')
        (tmp/'spawn.inc').write_text(parts((core/'bbsc_first_spawn.inc').read_text('utf8'),
            'struct BbscFirstSpawnGrant {','bool TakeBbscFirstSpawnGrant('),'utf8')
        invite=exe('invite',TESTS/'windows-join/guest_invitation.cpp',['-DRELAY_SOURCE="'+(mod/'bbsc_relay.cpp').as_posix()+'"'])
        old='1,18010000,3f800000,40000000,40400000,00000000,241010'
        new='1,18010000,40000000,40400000,40800000,3f800000,241010'
        run([invite,old,new,new])
        assert (core/'bbsc_guest_return.inc').read_bytes()==(ROOT/'patches/bbsc_guest_return.inc').read_bytes()
        run([exe('return',TESTS/'test_guest_return.cpp',[])])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('bbsc','bbport','sdk'):
        parser.add_argument(key,type=Path)
    args=parser.parse_args()
    run_tests(args.bbsc,args.bbport,args.sdk)
