"""Compile production C++ then exercise it with an independent Python HMAC peer.

python tests/test_relay_auth_native.py --source .build/bbsc-candidate-02
The input tree must already have game/save hardening, before relay-auth prep.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_relay_auth as prepare
EXE = ROOT / '.build/relay-auth-client/test-relay-auth.exe'
HTTP_EXE = ROOT / '.build/relay-auth-client/test-relay-http.exe'


def receive(connection):
    def read(size):
        result = b''
        while len(result) < size:
            part = connection.recv(size-len(result))
            if not part: raise EOFError
            result += part
        return result
    size, = struct.unpack('<I', read(4))
    if not 0 < size <= 65536: raise AssertionError('bad frame size')
    return read(size)


def send(connection, payload):
    raw = struct.pack('<I', len(payload)) + payload
    for at in range(0, len(raw), 3): connection.sendall(raw[at:at+3])


class NativeWireTests(unittest.TestCase):
    def setUp(self):
        if not EXE.is_file() or not HTTP_EXE.is_file() or not (EXE.parent/'relay-auth-wire.dll').is_file():
            self.skipTest('native fixture artifacts absent; run test_relay_auth_native.py --source after preparation')
    def exchange(self, bad_ack=False):
        listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        listener.settimeout(5)
        observed = []; errors = []
        nonce = bytes(range(32)); token = b'fixture-token'; uid = struct.pack('<Q',17)
        key = hmac.digest(token,b'TheHunt relay key v2\0'+nonce+uid,'sha256')
        def sign(raw): return raw+hmac.digest(key,raw,'sha256')
        def server():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(5)
                    send(connection,b'THC2'+nonce)
                    proof = receive(connection)
                    expected = b'THA2'+uid+hmac.digest(token,b'TheHunt relay auth v2\0'+nonce+uid,'sha256')
                    if proof != expected: raise AssertionError('proof or domain mismatch')
                    ack = sign(b'THO2'+uid)
                    if bad_ack: ack = ack[:-1]+bytes([ack[-1]^1])
                    send(connection,ack)
                    if bad_ack: return
                    send(connection,sign(b'THX2'+struct.pack('<QQIIB',1,9,17,18,1)))
                    outgoing = receive(connection)
                    if not hmac.compare_digest(outgoing[-32:],hmac.digest(key,outgoing[:-32],'sha256')):
                        raise AssertionError('client data MAC mismatch')
                    expected = b'THD2'+struct.pack('<Q',1)+b'SHAD\x23'+struct.pack('<IBI',17,0,1)
                    if outgoing[:-32] != expected: raise AssertionError('business payload changed')
                    observed.append(outgoing[:12])
                    send(connection,sign(b'THS2'+struct.pack('<QQII',2,9,18,18)+b'SHAD\x27'+struct.pack('<i',100)))
                    send(connection,sign(b'THX2'+struct.pack('<QQIIB',3,0,17,0,0)))
                    tampered = bytearray(sign(b'THX2'+struct.pack('<QQIIB',4,99,17,18,1)))
                    tampered[12] ^= 1; send(connection,tampered)
            except BaseException as error: errors.append(error)
        thread=threading.Thread(target=server,daemon=True);thread.start()
        env = {k:v for k,v in os.environ.items() if not k.startswith('BBSC_')}
        env.update(BBSC_RELAY_AUTH='private-v2',BBSC_RELAY_USER_ID='17',BBSC_RELAY_TOKEN='fixture-token')
        command=[str(EXE),'--wire','127.0.0.1',str(listener.getsockname()[1])]
        try:
            result=subprocess.run(command,input='read\nboss\nlife\nread\nread\nread\nquit\n',
                                  env=env,text=True,capture_output=True,timeout=12)
        finally:
            thread.join(6);listener.close()
        if errors: raise errors[0]
        self.assertFalse(thread.is_alive())
        self.assertNotIn('fixture-token', result.stdout + result.stderr)
        return result, observed

    def test_production_cpp_with_independent_hmac_partial_reads_and_adapter(self):
        result, observed = self.exchange()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),['ACK','CTX 9 17 18 1','BLOCKED','SENT',
                                                    'DATA 18 39 1','CTX 0 17 0 0','REJECT'])
        self.assertEqual(len(observed),1)

    def test_bad_server_ack_never_sends_application_frame(self):
        result, observed = self.exchange(bad_ack=True)
        self.assertEqual(result.returncode,12)
        self.assertEqual(result.stdout,'')
        self.assertEqual(observed,[])

    def test_ctypes_actual_client_shared_sequence_pending_cancel_and_rejoin(self):
        from test_relay_auth_abi import NativeClient
        nonce=bytes(range(32));token=b'fixture-token';identity=struct.pack('<Q',17)
        key=hmac.digest(token,b'TheHunt relay key v2\0'+nonce+identity,'sha256')
        def sign(raw):return raw+hmac.digest(key,raw,'sha256')
        def pending(seq,gen,ttl,text=b''):
            return sign(b'THP2'+struct.pack('<QQQIII',seq,gen,9,18,17,ttl)+text)
        placement=b'1,18000000,43110000,c2e80000,42800000,c016cbe4,-241109'
        with NativeClient(EXE.parent/'relay-auth-wire.dll') as client:
            self.assertEqual(client.begin(17,token,b'THC2'+nonce),
                b'THA2'+identity+hmac.digest(token,b'TheHunt relay auth v2\0'+nonce+identity,'sha256'))
            client.ack(sign(b'THO2'+identity))
            self.assertEqual(client.receive(sign(b'THX2'+struct.pack('<QQIIB',1,0,17,0,0)))[0],1)
            self.assertEqual(client.receive(pending(2,5,10000,placement))[0],3)
            self.assertEqual(client.receive(pending(3,6,10000,placement))[0],3)
            self.assertEqual(client.receive(pending(4,5,0))[0],4) # Old cancel consumes seq, leaves newer hint.
            self.assertEqual(client.receive(pending(5,6,0))[0],3)
            self.assertEqual(client.receive(sign(b'THX2'+struct.pack('<QQIIB',6,9,17,18,1)))[0],1)
            inner=b'SHAD\x23'+struct.pack('<IBI',17,0,1)
            frame=client.send(inner)
            self.assertEqual(frame[:12],b'THD2'+struct.pack('<Q',1))
            self.assertTrue(hmac.compare_digest(frame[-32:],hmac.digest(key,frame[:-32],'sha256')))
            with self.assertRaises(ValueError):client.receive(pending(5,6,0)) # Cross-control replay.

    def test_actual_winhttp_delete_has_bearer_only_on_native_origin(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                requests.append((self.path,dict(self.headers)))
                self.rfile.read(int(self.headers.get('Content-Length','0')))
                self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
        native=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        community=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in (native,community)]
        for thread in threads:thread.start()
        origin=f'http://127.0.0.1:{native.server_port}'
        env=dict(os.environ,FIXTURE_HTTP_ORIGIN=origin)
        try:
            result=subprocess.run([str(HTTP_EXE),'--post',origin+'/summon_messenger/delete'],
                                  env=env,text=True,capture_output=True,timeout=8)
            self.assertEqual(result.stdout.strip(),'STATUS 200',result.stderr)
            self.assertEqual(requests[0][0],'/summon_messenger/delete')
            self.assertEqual(requests[0][1]['Authorization'],'Bearer fixture-token')
            result=subprocess.run([str(HTTP_EXE),'--post',f'http://127.0.0.1:{community.server_port}/summon_messenger/delete'],
                                  env=env,text=True,capture_output=True,timeout=8)
            self.assertEqual(result.stdout.strip(),'STATUS 0')
            self.assertEqual(len(requests),1)
        finally:
            for server in (native,community):server.shutdown();server.server_close()
            for thread in threads:thread.join()


def core_runtime(source, output, compiler):
    """Use the existing synthetic memory fixture with extracted production bodies."""
    from prepare_game_hardening_regression import function
    from prepare_npc_regression import block
    text=source.read_text('utf-8')
    template=(ROOT/'tests/game_hardening_pvp.cpp.in').read_text('utf-8')
    template=template.replace('void OnBbscRelayAuthContextChanged(u32){}','')
    template='#include "'+(ROOT/'patches/bbsc_relay_auth_guard.h').as_posix()+'"\n'+template
    start=template.index('namespace BbscMod {');end=template.index('\n}',start)+2
    template=template[:start]+'''void OnBbscRelayAuthContextChanged(std::uint32_t);
bool test_private=false; u32 test_owner=18,test_own=17;int invalidations=0,world_clears=0,kit_ticks=0;
namespace BbscMod {
bool RelayAuthPrivate(){return test_private;}
u32 RelayAuthOwner(){return test_owner;}
u32 RelayAuthOwnId(){return test_own;}
void RelayAuthInvalidate(){++invalidations;test_owner=0;OnBbscRelayAuthContextChanged(0);}
}'''+template[end:]
    template=template.replace('void TickBbscDuelBellGive(){}','void TickBbscDuelBellGive(){++kit_ticks;}')
    template=template.replace('void ClearBbscHostWorldSnapshots(){}','''void ClearBbscPeerDisplay(){}
void ClearBbscHostWorldSnapshots(){++world_clears;}
std::mutex bbsc_follow_mutex,bbsc_progress_mutex,bbsc_scale_host_mutex,bbsc_life_mutex;
int bbsc_follow_latest=7;
std::vector<u32> bbsc_progress_host_flags{1,2};
s64 bbsc_scale_host_ms=123;
std::map<u32,u32> bbsc_peer_life{{18,1}};
std::atomic<s32> bbsc_host_boss_id{100};
std::atomic<s64> bbsc_host_boss_ms{123};''')
    markers=('void SelectBbscRelayHost(','u64 BbscRelayNativeHost() {','u32 BbscRelayHostForPackets() {',
             'void TickBbscRelayHostLock() {','bool BbscPvpRequireRoomBinding() {','void ClearBbscPvpHostState() {',
             'bool BbscPvpTeamIdentity(','void SetBbscPvp(','void ApplyBbscPvpTeams() {','void TickBbscPvp() {',
             'void OnBbscPvpPacket(')
    bodies=block(text,'struct BbscPvpTeamSnapshot {',';')+'\n'+'\n'.join(function(text,m) for m in markers)
    bodies+='\n'+(ROOT/'patches/bbsc_relay_auth_core.inc').read_text('utf-8')
    template=prepare.once(template,'// RELAY_DECLARATIONS',block(text,'struct BbscRelayHostSeen {',';'))
    template=prepare.once(template,'// CALLBACKS',bodies)
    scenarios='''    if(mode.starts_with("private-")) {
        test_private=true;session=6;test_owner=18;
        if(mode=="private-alone") {
            session=0;test_owner=0;TickBbscPvp();assert(kit_ticks==1 && !bbsc_pvp_on && team()==20);
        } else if(mode=="private-caches") {
            bbsc_pvp_on=true;const int before=writes;
            OnBbscRelayAuthContextChanged(18);
            assert(writes==before && !bbsc_pvp_on && bbsc_follow_latest==0 && bbsc_progress_host_flags.empty());
            assert(bbsc_scale_host_ms==0 && bbsc_peer_life.empty() && bbsc_host_boss_ms==0 && world_clears==1);
        } else if(mode=="private-reload") {
            TickBbscRelayHostLock();++matching;TickBbscRelayHostLock();
            assert(BbscRelayHostForPackets()==18 && invalidations==0 && world_clears==1);
            session=-1;matching=0;TickBbscRelayHostLock();session=6;matching=3;TickBbscRelayHostLock();
            assert(BbscRelayHostForPackets()==18 && invalidations==0);
            bbsc_leave_event_ms=now;TickBbscRelayHostLock();assert(invalidations==1 && !BbscRelayHostForPackets());
        } else if(mode=="private-host") {
            session=3;test_owner=17;TickBbscPvp();bell=true;TickBbscPvp();assert(bbsc_pvp_on && team()==21);
            test_owner=0;OnBbscRelayAuthContextChanged(0);TickBbscPvp();assert(!bbsc_pvp_on && team()==20);
            test_owner=18;bell=false;TickBbscPvp();bell=true;TickBbscPvp();assert(!bbsc_pvp_on && team()==20);
        } else {
            TickBbscPvp();OnBbscPvpPacket(0,1,18);TickBbscPvp();assert(bbsc_pvp_on && team()==21);
            OnBbscRelayAuthContextChanged(18);TickBbscPvp();assert(!bbsc_pvp_on && team()==20);
            OnBbscPvpPacket(0,1,18);TickBbscPvp();assert(bbsc_pvp_on);
            test_owner=0;OnBbscRelayAuthContextChanged(0);OnBbscPvpPacket(0,1,18);TickBbscPvp();
            assert(!bbsc_pvp_on && team()==20);
        }
        std::printf("PASS actual private core: %s\\n",argv[1]);return 0;
    }
'''
    template=prepare.once(template,'    strict_mode=mode=="strict";',scenarios+'    strict_mode=mode=="strict";')
    fixture=output/'core-runtime.cpp';fixture.write_text(template,encoding='utf-8')
    exe=output/'core-runtime.exe'
    subprocess.run([compiler,'-std=c++23','-O2',str(fixture),'-o',str(exe)],check=True)
    for mode in ('private-alone','private-caches','private-reload','private-host','private-guest'):
        subprocess.run([str(exe),mode],check=True)


def compile_native(source, output, compiler):
    global EXE, HTTP_EXE
    source=source.resolve();output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    target=output/'emulator'
    for name in ('mod/bbsc_relay.cpp','mod/bbsc_http.cpp','src/core/bloodborne_re_zh_cn.cpp'):
        destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/'emulator'/name,destination)
    prepare.prepare(output)
    flags=['-std=c++23','-O2','-g','-fms-extensions','-DNOMINMAX','-DWIN32_LEAN_AND_MEAN',
           '-D_CRT_SECURE_NO_WARNINGS','-DBBSC_MOD','-DFMT_HEADER_ONLY',
           '-I'+str(source/'emulator/mod/shim'),'-I'+str(source/'emulator/src'),
           '-I'+str(source/'emulator/src/core'),'-I'+str(ROOT/'.build/bbport/gpu/third_party/xbyak')]
    for name, obj in (('mod/bbsc_relay.cpp','relay.o'),('mod/bbsc_http.cpp','http.o'),('src/core/bloodborne_re_zh_cn.cpp','core.o')):
        subprocess.run([compiler,*flags,'-c',str(target/name),'-o',str(output/obj)],check=True)
    EXE=output/'test-relay-auth.exe'
    definition='-DRELAY_SOURCE="'+(target/'mod/bbsc_relay.cpp').as_posix()+'"'
    subprocess.run([compiler,*flags,definition,str(ROOT/'tests/test_relay_auth_native.cpp'),
                    '-o',str(EXE),'-lws2_32','-lbcrypt','-lwinhttp'],check=True)
    subprocess.run([str(EXE)],check=True)
    HTTP_EXE=output/'test-relay-http.exe'
    definition='-DHTTP_SOURCE="'+(target/'mod/bbsc_http.cpp').as_posix()+'"'
    subprocess.run([compiler,*flags,definition,str(ROOT/'tests/test_relay_auth_http.cpp'),
                    '-o',str(HTTP_EXE),'-lwinhttp'],check=True)
    subprocess.run([str(HTTP_EXE)],check=True)
    core_runtime(target/'src/core/bloodborne_re_zh_cn.cpp',output,compiler)
    definition='-DRELAY_AUTH_HEADER="'+(target/'mod/bbsc_relay_auth.h').as_posix()+'"'
    subprocess.run([compiler,*flags,definition,'-shared',str(ROOT/'tests/relay_auth_wire_client.cpp'),
                    '-o',str(output/'relay-auth-wire.dll'),'-lbcrypt'],check=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'.build/relay-auth-client')
    parser.add_argument('--compiler',default='clang++')
    args=parser.parse_args()
    compile_native(args.source,args.output,args.compiler)
    unittest.main(argv=[sys.argv[0]],verbosity=2)
