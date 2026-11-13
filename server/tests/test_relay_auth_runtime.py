"""Opt-in isolated Windows runtime check: native CLI plus independent NP integration.

Copies only newly built executables/config into a NEW fixture directory. No
installed service/account/database is used. Credentials never enter argv/logs.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def delivered_cancellation_checks(wire, output, key, np, web, relay):
    args=SimpleNamespace(server_ip="127.0.0.1",tcp_port=np,web_port=web,relay_port=relay,
                         state_dir=output,timeout=3.0,quiet_window=.65)
    suite=wire.Suite(args,key)
    passed=[]
    try:
        prefix="can"+secrets.token_hex(3)
        host,target,successor=[suite.register(prefix+str(i)) for i in range(3)]
        for account in (host,target,successor): suite.connect_relay(account)
        room=suite.create_room(host)
        suite.context(host,room,host.user,0)
        serial=0

        def grant():
            nonlocal serial
            serial+=1
            sign=dict(SessionId=target.name+str(serial),UserId=target.legacy_user,CharaId=serial,
                      AreaId=wire.MAP+0x10000,AreaRegionId=1,SummonData="synthetic",SummonDataVersion=3,
                      SummonType=0,SummonMethod=0,MatchingLevel=30,SummonWord=str(serial))
            suite.summon_ok("create",sign,target)
            search=dict(UserId=host.legacy_user,AreaId=wire.MAP,SummonWord=str(serial),GetMaxCount=20)
            before,data_before=suite.placement_counts(),suite.counts()
            suite.summon_ok("get",search,host,wire.PLACEMENT)
            receipt=suite.expect_placement(target,host,room,before,data_before)
            count=len(target.relay.placements)
            suite.pump(.65)
            wire.require(len(target.relay.placements)==count,"delivered placement repeated on periodic control tick")
            return sign,receipt

        def cancelled(receipt,label):
            suite.until(lambda: target.relay.placements[-1]["ttl"]==0,"delivered hint was not cancelled")
            cancel=target.relay.placements[-1]
            wire.require(all(cancel[field]==receipt[field] for field in ("generation","room","host","target"))
                         and cancel["text"]=="" and len(cancel["wire"])==72,"cancellation changed grant identity/layout")
            wire.require(target.relay.active_placement() is None,"cancelled hint remained active")
            passed.append(label)
            print("PASS delivered cancellation: "+label,flush=True)

        sign,receipt=grant()
        suite.summon_ok("delete",dict(SessionId=sign["SessionId"],UserId=sign["UserId"],Force=True),target)
        cancelled(receipt,"owner delete")
        sign,receipt=grant()
        suite.summon_ok("create",dict(sign,CharaId=9999),target)
        cancelled(receipt,"character/sign replacement")
        sign,receipt=grant()
        host.token=suite.revoke(host)
        cancelled(receipt,"host token reset")
        suite.replace_relay(host)
        sign,receipt=grant()
        suite.join(successor,room)
        suite.leave(host,room)
        cancelled(receipt,"actual NP host leave and owner migration")
        room=suite.create_room(host)
        sign,receipt=grant()
        suite.pump(10.5)
        cancelled(receipt,"delivered TTL expiry")
        sign,receipt=grant()
        old=target.relay
        target.token=suite.revoke(target)
        suite.until(lambda: old.closed,"revoked target connection stayed open")
        suite.replace_relay(target)
        suite.pump(.65)
        wire.require(not target.relay.placements,"target reset revived old placement")
        passed.append("target reset closes old authenticated connection")
        sign,receipt=grant()
        host.token=suite.revoke(host,ban=True)
        cancelled(receipt,"host ban")
        return passed
    finally:
        suite.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, default=Path("C:/bbsc-dev/msys64/clang64"))
    parser.add_argument("--cancel-only",action="store_true",help="exercise delivered-hint cancellation on actual native service")
    parser.add_argument("--native-client-dll",type=Path,help="also execute the production C++ client protocol via ctypes")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ("shadnet.exe", "relay_auth_integration.exe", "worlds.cfg", "scoreboards.cfg"):
        shutil.copy2(args.build_dir / name, output / name)
    reservations = []
    ports = []
    for _ in range(4):
        s = socket.socket(); s.bind(("127.0.0.1",0)); reservations.append(s); ports.append(s.getsockname()[1])
    np, web, relay, udp = ports
    key = secrets.token_hex(32)
    config = ("[General]\nHost=127.0.0.1\nUnsecuredPort=%d\nWebApiPort=%d\nMatchingUdpPort=%d\n"
              "StatsEnabled=false\nMatching2Enabled=true\nBloodborneSeamlessCoop=true\nEmailValidated=false\n"
              "RegistrationSecretKey=%s\n") % (np,web,udp,key)
    (output / "shadnet.cfg").write_text(config,encoding="utf-8",newline="\n")
    env = os.environ.copy()
    env["PATH"] = str(args.toolchain / "bin") + os.pathsep + env.get("PATH", "")
    env["THEHUNT_RELAY_PORT"] = str(relay)
    # Test credential/body logging is explicitly disabled even in a traced shell.
    env.pop("SHADNET_BLOODBORNE_RE_TRACE",None)
    for s in reservations: s.close()
    result = {"ok":False,"native_cli":False,"independent_wire":False}
    process = None
    try:
        with (output / "server.log").open("wb") as log:
            process = subprocess.Popen([str(output / "shadnet.exe")],cwd=output,env=env,stdout=log,stderr=log,
                                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            opener = build_opener(ProxyHandler({}))
            deadline=time.monotonic()+10
            while True:
                if process.poll() is not None: raise RuntimeError("native fixture exited during startup")
                try:
                    with opener.open("http://127.0.0.1:%d/status" % web,timeout=.5) as response:
                        status=json.load(response)
                    if status.get("relay_auth")==2: break
                except OSError:
                    pass
                if time.monotonic()>=deadline: raise RuntimeError("native fixture readiness timed out")
                time.sleep(.05)
            spec=importlib.util.spec_from_file_location("independent_relay_wire",ROOT/"tests/relay_v2_integration.py")
            wire=importlib.util.module_from_spec(spec);sys.modules[spec.name]=wire;spec.loader.exec_module(wire)
            if args.cancel_only:
                result["delivered_cancellations"]=delivered_cancellation_checks(wire,output,key,np,web,relay)
                result["ok"]=True
                return
            accounts=[]
            prefix="nat"+secrets.token_hex(3)
            for i in range(3):
                account=wire.Account(prefix+str(i)); connection=wire.NP.connect(("127.0.0.1",np),3)
                try:
                    body=wire.pb_bytes(1,account.name)+wire.pb_bytes(2,account.password)
                    body+=wire.pb_bytes(4,account.name+"@relay-fixture.invalid")+wire.pb_bytes(5,key)
                    connection.call(wire.CREATE,body)
                finally:
                    connection.close()
                accounts.append({"npid":account.name,"password":account.password})
            if args.native_client_dll is not None:
                backend=ROOT.parent/"launcher/relay_auth.py"
                backend_spec=importlib.util.spec_from_file_location("actual_launcher_relay_auth",backend)
                auth=importlib.util.module_from_spec(backend_spec);backend_spec.loader.exec_module(auth)
                network=dict(shadnet="127.0.0.1:%d"%np,relay="127.0.0.1:%d"%relay,
                             webapi="http://127.0.0.1:%d"%web)
                proof=auth.environment(network,accounts[0]["npid"],accounts[0]["password"])
                if (proof.get("BBSC_RELAY_AUTH")!="private-v2" or
                        not proof.get("BBSC_RELAY_TOKEN") or int(proof.get("BBSC_RELAY_USER_ID","0"))<=0):
                    raise RuntimeError("actual launcher preflight/native credentials failed")
                proof.clear()
                result["actual_launcher_status_login_token_terminate"]=True
            payload=json.dumps({"host":"127.0.0.1","np_port":np,"relay_port":relay,"accounts":accounts})
            cli=subprocess.run([str(output/"relay_auth_integration.exe")],input=payload,text=True,env=env,
                               capture_output=True,timeout=60)
            for a in accounts:
                if a["password"] in cli.stdout+cli.stderr: raise RuntimeError("native CLI leaked fixture credentials")
            (output/"native-cli.log").write_text(cli.stdout+cli.stderr,encoding="utf-8")
            if cli.returncode: raise RuntimeError("native CLI integration failed; inspect sanitized fixture log")
            result["native_cli"]=True
            command=[sys.executable,"-B",str(ROOT/"tests/relay_v2_integration.py"),
                "--server-ip","127.0.0.1","--tcp-port",str(np),"--web-port",str(web),"--relay-port",str(relay),
                "--state-dir",str(output)]
            if args.native_client_dll is not None:
                command.extend(["--native-client-dll",str(args.native_client_dll.resolve())])
            independent=subprocess.run(command,env=env,capture_output=True,text=True,timeout=180)
            (output/"independent-wire.log").write_text(independent.stdout+independent.stderr,encoding="utf-8")
            if independent.returncode: raise RuntimeError("independent native wire integration failed; inspect fixture log")
            result["independent_wire"]=True
            if args.native_client_dll is not None:
                report=json.loads(independent.stdout.splitlines()[-1])
                if not report.get("native_client_code_executed"):
                    raise RuntimeError("production C++ client was not fully exercised")
                result["native_client_code_executed"]=True
            result["ok"]=True
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()
        (output/"results.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,sort_keys=True))


if __name__ == "__main__":
    main()
