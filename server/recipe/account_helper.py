"""Stage the shared stdin account helper against the pinned clientsample (offline)."""
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINNED_SAMPLE_CMAKE_SHA256 = "6750cb214cf90a02f7fcd39b5d552f677eab398b0a5c1fcaac913e4ab42e73a3"

ADDITION = '''
# The Hunt: same stdin JSON helper as windows/stdin_account.cpp; no credential argv.
find_package(Qt6 REQUIRED COMPONENTS Core)
find_package(Threads REQUIRED)

function(hunt_account_target target transport)
    add_executable(${target} stdin_account.cpp client.cpp ${transport} "${PROTO_OUT}/shadnet.pb.cc")
    target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${PROTO_OUT}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../externals/protobuf/src")
    target_link_libraries(${target} PRIVATE libprotobuf Qt6::Core Threads::Threads)
    if(WIN32)
        target_link_libraries(${target} PRIVATE ws2_32)
        target_compile_definitions(${target} PRIVATE _WIN32_WINNT=0x0601 WIN32_LEAN_AND_MEAN
            _CRT_SECURE_NO_WARNINGS NOMINMAX)
    endif()
endfunction()

hunt_account_target(shadnet-admin connection.cpp)
if(BUILD_TESTING)
    # Actual helper + pinned client/protobuf with an in-memory transport, no sockets.
    hunt_account_target(test_stdin_account "${CMAKE_CURRENT_SOURCE_DIR}/../tests/admin_fake_connection.cpp")
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME stdin_account COMMAND "${Python3_EXECUTABLE}" -B
        "${CMAKE_CURRENT_SOURCE_DIR}/../tests/check_stdin_account.py" "$<TARGET_FILE:test_stdin_account>")
endif()
'''


def stage(root):
    root = Path(root)
    cmake = root / "clientsample/CMakeLists.txt"
    text = cmake.read_text(encoding="utf-8")
    original = text.removesuffix(ADDITION)
    if hashlib.sha256(original.encode()).hexdigest() != PINNED_SAMPLE_CMAKE_SHA256:
        raise ValueError("Pinned clientsample CMake changed; account helper was not staged")
    parent = root / "CMakeLists.txt"
    parent_text = parent.read_text(encoding="utf-8")
    if parent_text.count("add_subdirectory(clientsample)") > 1:
        raise ValueError("Conflicting clientsample build entry")
    helper = (ROOT / "fixes/stdin_account.cpp").read_bytes()
    canonical = ROOT.parent / "windows/stdin_account.cpp"
    # The standalone recipe includes an exact copy because its Docker context
    # deliberately excludes windows/. In a complete source tree, refuse drift.
    if canonical.is_file() and canonical.read_bytes() != helper:
        raise ValueError("Recipe stdin helper differs from windows/stdin_account.cpp")
    fixtures = {name: (ROOT / "tests" / name).read_bytes() for name in (
        "admin_fake_connection.cpp", "check_stdin_account.py")}
    # All validation/read failures happen before publishing the generated files.
    if "add_subdirectory(clientsample)" not in parent_text:
        parent.write_text(parent_text + "\nadd_subdirectory(clientsample)\n", encoding="utf-8", newline="\n")
    cmake.write_text(original + ADDITION, encoding="utf-8", newline="\n")
    (root / "clientsample/stdin_account.cpp").write_bytes(helper)
    (root / "tests").mkdir(exist_ok=True)
    for name, data in fixtures.items():
        (root / "tests" / name).write_bytes(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadnet", type=Path, required=True)
    stage(parser.parse_args().shadnet)
