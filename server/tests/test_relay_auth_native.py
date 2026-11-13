"""Build actual patched native relay/server with installed Qt/Protobuf in a NEW directory.

This opt-in CLI never modifies windows/src or any existing build directory.
The production CMake targets use the pinned bundled Protobuf; this focused build
uses the installed compiler/Qt/Protobuf to validate actual Windows translation units.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "windows/downloads")
    parser.add_argument("--toolchain", type=Path, default=Path("C:/bbsc-dev/msys64/clang64"))
    parser.add_argument("--unit-only", action="store_true", help="build/run the actual relay unit first; full service targets remain available")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, "-B", str(ROOT / "tests/stage_native.py"),
                    "--cache", str(args.cache.resolve()), "--output", str(output / "src")], check=True)
    subprocess.run([sys.executable, "-B", str(ROOT / "recipe/relay_auth.py"),
                    "--shadnet", str(output / "src")], check=True)
    env = os.environ.copy()
    env["PATH"] = str(args.toolchain / "bin") + os.pathsep + env.get("PATH", "")
    protoc = shutil.which("protoc", path=env["PATH"])
    if not protoc:
        raise RuntimeError("Installed protoc required for focused native build")
    original = (output / "src/CMakeLists.txt").read_text(encoding="utf-8")
    source_list = re.search(r"set\(SOURCES\n(.*?)\n\)", original, re.S)
    if not source_list:
        raise ValueError("Fixed native CMake SOURCES anchor changed")
    sources = re.sub(r"(?m)^(\s*)src/", r"\1src/src/", source_list.group(1))
    cmake = '''cmake_minimum_required(VERSION 3.21)
project(relay_auth_focused_native LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 REQUIRED COMPONENTS Core Network Sql HttpServer Concurrent)
find_package(Protobuf CONFIG REQUIRED)
set(PROTO_OUT "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${PROTO_OUT}")
execute_process(COMMAND "@PROTOC@" "--proto_path=${CMAKE_SOURCE_DIR}/src/src"
    "--cpp_out=${PROTO_OUT}" "${CMAKE_SOURCE_DIR}/src/src/shadnet.proto" COMMAND_ERROR_IS_FATAL ANY)
add_executable(test_relay_auth src/tests/test_relay_auth.cpp src/src/authenticated_relay.cpp src/src/database.cpp src/src/config.cpp src/src/bloodborne_summon_broker.cpp)
target_include_directories(test_relay_auth PRIVATE src/src "${PROTO_OUT}")
target_link_libraries(test_relay_auth PRIVATE Qt6::Core Qt6::Network Qt6::Sql)
set(RELAY_PROTO "${CMAKE_BINARY_DIR}/relay_client_proto")
file(MAKE_DIRECTORY "${RELAY_PROTO}")
execute_process(COMMAND "@PROTOC@" "--proto_path=${CMAKE_SOURCE_DIR}/src/tests/relay_client"
    "--cpp_out=${RELAY_PROTO}" "${CMAKE_SOURCE_DIR}/src/tests/relay_client/shadnet.proto" COMMAND_ERROR_IS_FATAL ANY)
add_executable(relay_auth_integration src/tests/relay_auth_integration.cpp src/tests/relay_client/client.cpp
    src/tests/relay_client/connection.cpp "${RELAY_PROTO}/shadnet.pb.cc")
target_include_directories(relay_auth_integration PRIVATE src/tests/relay_client "${RELAY_PROTO}")
target_link_libraries(relay_auth_integration PRIVATE protobuf::libprotobuf Qt6::Core Qt6::Network)
if(WIN32)
    target_link_libraries(relay_auth_integration PRIVATE ws2_32)
    target_compile_definitions(relay_auth_integration PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
endif()
set(SOURCES
@SOURCES@
)
add_executable(shadnet ${SOURCES})
target_include_directories(shadnet PRIVATE src/src "${PROTO_OUT}")
target_link_libraries(shadnet PRIVATE Qt6::Core Qt6::Network Qt6::Sql Qt6::Concurrent Qt6::HttpServer protobuf::libprotobuf)
foreach(config worlds.cfg scoreboards.cfg)
    configure_file("${CMAKE_SOURCE_DIR}/src/${config}" "${CMAKE_BINARY_DIR}/${config}" COPYONLY)
endforeach()
enable_testing()
add_test(NAME relay_auth COMMAND test_relay_auth)
'''.replace("@PROTOC@", Path(protoc).as_posix()).replace("@SOURCES@", sources)
    (output / "CMakeLists.txt").write_text(cmake, encoding="utf-8", newline="\n")
    subprocess.run(["cmake", "-S", str(output), "-B", str(output / "build"), "-G", "Ninja",
                    "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=clang++",
                    "-DCMAKE_PREFIX_PATH=" + args.toolchain.as_posix()], env=env, check=True)
    build = ["cmake", "--build", str(output / "build"), "--parallel", "4"]
    if args.unit_only:
        build += ["--target", "test_relay_auth"]
    subprocess.run(build, env=env, check=True)
    subprocess.run(["ctest", "--test-dir", str(output / "build"), "--output-on-failure"], env=env, check=True)


if __name__ == "__main__":
    main()
