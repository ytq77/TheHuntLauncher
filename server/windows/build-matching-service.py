"""Build the Windows matching service with the shared Qt/protobuf runtime used by this preview."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

NEUTRAL = r"X:\TheHunt\build\windows-x64\matching-service\preview-candidate\reproducible-build\source-inputs"


def build(tree, sdk, output, tests):
    tree, sdk, output = tree.resolve(), sdk.resolve(), output.resolve()
    bin_dir = sdk / "bin"
    qt = sdk / "share/qt6"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "QT_PLUGIN_PATH":str(qt / "plugins")}
    modules = ["Qt6Core", "Qt6Network", "Qt6HttpServer", "Qt6Concurrent", "Qt6Sql", "protobuf"]
    definition = (tree / "CMakeLists.txt").read_text("utf8").split("set(SOURCES", 1)[1].split(")", 1)[0]
    units = re.findall(r"^\s*(src/[\w_]+\.cpp)\s*$", definition, re.M)
    if set(units) != {"src/" + f.name for f in (tree / "src").glob("*.cpp")}:
        raise ValueError("Matching service CMake source list changed")
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        root = Path(temporary)
        inc = root / "source/src"
        gen = root / "build/shadnet"
        inc.mkdir(parents=True)
        gen.mkdir(parents=True)
        for path in (tree / "src").iterdir():
            if path.suffix in {".cpp", ".h", ".hpp", ".proto"}:
                shutil.copyfile(path, inc/path.name)

        def run(args):
            return subprocess.run(args, cwd=gen.parent, env=env, text=True, check=True,
                                  stdout=subprocess.PIPE, errors="replace", timeout=300).stdout.strip()

        run([bin_dir/"protoc.exe", "-I"+str(inc), "--cpp_out="+str(gen), inc/"shadnet.proto"])
        generated = [gen/"shadnet.pb.cc"]
        for header in sorted(inc.glob("*.h")):
            if re.search(r"^\s*Q_OBJECT\s*$", header.read_text("utf-8-sig"), re.M):
                moc = gen/("moc_"+header.stem+".cpp")
                run([qt/"bin/moc.exe",header,"-o",moc])
                generated.append(moc)
        cflags = shlex.split(run([bin_dir/"pkg-config.exe", "--cflags", *modules]))
        libs = shlex.split(run([bin_dir/"pkg-config.exe", "--libs", *modules]))
        compiler = [bin_dir/"clang++.exe", "-std=c++23", "-O1", "-pthread", "-Wno-ignored-attributes",
                    "-DWIN32_LEAN_AND_MEAN", "-DNOMINMAX", "-D_WIN32_WINNT=0x0A00",
                    "-ffile-prefix-map="+str(root)+"="+NEUTRAL]
        include = ["-I"+str(inc), "-I"+str(gen), *cflags]

        def compile_unit(path):
            obj = gen/(path.name+".o")
            run([*compiler,*include,"-c",path,"-o",obj])
            return obj

        with ThreadPoolExecutor(max_workers=4) as workers:
            objects = list(workers.map(compile_unit, [*(root/"source"/name for name in units), *generated]))
        exe = gen/"shadnet.exe"
        run([*compiler,*objects,*libs,"-lws2_32","-Wl,--no-insert-timestamp","-o",exe])
        if tests:
            # The HTTP tests provide their own session stubs; select only their real dependencies.
            route = ["bloodborne_summon_broker.cpp","webapi_routes_bloodborne.cpp","webapi_auth.cpp",
                     "database.cpp","authenticated_relay.cpp","config.cpp","shadnet.pb.cc"]
            native = route + ["client_session.cpp","cmd_matching.cpp","cmd_account.cpp","cmd_friend.cpp",
                              "cmd_score.cpp","score_cache.cpp","score_db.cpp","webapi_routes_session.cpp","moc_client_session.cpp"]
            cases = {"test_bound_claim":route[:1], "test_registration_reference":route[:1],
                     "test_registration_search_http":route,"test_bound_placement":native}
            for case, required in cases.items():
                test = gen/(case+".exe")
                src = Path(__file__).resolve().parent/"join/tests"/(case+".cpp")
                run([*compiler,*include,src,*(gen/(name+".o") for name in required),*libs,"-lws2_32","-o",test])
                print(run([test]),flush=True)
        exe.replace(output)


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("tree","sdk","output"):
        p.add_argument(name,type=Path)
    p.add_argument("--tests",action="store_true")
    args=p.parse_args()
    build(args.tree,args.sdk,args.output,args.tests)
