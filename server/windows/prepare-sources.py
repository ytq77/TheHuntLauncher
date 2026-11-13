"""Fetch verified, pinned sources into this disposable native-build directory."""
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
RECIPE = ROOT.parent / "recipe"


def extract_source(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if not members:
            raise ValueError("Empty pinned source archive.")
        prefix = members[0].name.split("/")[0] + "/"
        for member in members:
            if member.name.rstrip("/") == prefix.rstrip("/") and member.isdir():
                continue
            if not member.name.startswith(prefix):
                raise ValueError("Pinned source archive has multiple roots.")
            member.name = member.name[len(prefix):]
            bundle.extract(member, destination, filter="data")


def prepare(root=ROOT, recipe_directory=RECIPE):
    root, recipe_directory = Path(root).resolve(), Path(recipe_directory)
    spec = importlib.util.spec_from_file_location("recipe", recipe_directory / "prepare.py")
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    manifest = json.loads((recipe_directory / "sources.json").read_text(encoding="utf-8"))
    archives = {"shadnet.tar.gz": "src", "protobuf.tar.gz": "src/externals/protobuf",
                "abseil.tar.gz": "src/externals/abseil-cpp"}
    if {item["name"] for item in manifest["archives"]} != set(archives):
        raise ValueError("Unexpected pinned source archive manifest.")
    # These are disposable generated inputs. Never trust their revision stamps,
    # and never recursively replace a junction or anything outside this build root.
    for name in ("src", "bbsc-game"):
        destination = root / name
        if (destination.is_symlink() or destination.is_junction() or
                not destination.resolve().is_relative_to(root)):
            raise ValueError("Unsafe generated source directory: " + str(destination))
    with tempfile.TemporaryDirectory(prefix=".prepare-sources-", dir=root) as temporary:
        stage = Path(temporary)
        for item in manifest["archives"]:
            archive = root / "downloads" / item["name"]
            recipe.fetch(f"https://codeload.github.com/{item['repo']}/tar.gz/{item['revision']}",
                         archive, item["sha256"], False)
            destination = stage / archives[item["name"]]
            extract_source(archive, destination)
            (destination / ".source-revision").write_text(item["revision"], encoding="ascii")
        patch_sources(stage, root, recipe_directory)
        recipe.security.patch_shadnet(stage / "src")
        recipe.relay_auth.patch_shadnet(stage / "src")
        bbsc = manifest["bbsc"]
        for relative, digest in bbsc["files"].items():
            original = root / "downloads" / "bbsc" / relative
            data = recipe.fetch(f"https://raw.githubusercontent.com/{bbsc['repo']}/{bbsc['revision']}/{relative}",
                                original, digest, False)
            if relative.endswith("/bbsc_gameserver.py"):
                data = recipe.patch_local_login(recipe.patch_gameserver(data))
            output = stage / "bbsc-game" / Path(relative).name
            output.parent.mkdir(exist_ok=True)
            output.write_bytes(data)
        # Only publish after all downloads, extraction and patch anchors passed.
        for name in ("src", "bbsc-game"):
            destination = root / name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(stage / name), destination)
    (root / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Pinned sources freshly extracted and verified; STUN, summon WebAPI and same-PC login patches applied.")


def patch_sources(stage, root, recipe_directory):
    for name in ("stun_server.cpp", "stun_server.h"):
        shutil.copy2(recipe_directory / "fixes" / name, stage / "src" / "src" / name)
    cmake = stage / "src" / "CMakeLists.txt"
    text = cmake.read_text(encoding="utf-8")
    if "add_subdirectory(clientsample)" not in text:
        cmake.write_text(text + "\nadd_subdirectory(clientsample)\n", encoding="utf-8")

    # A small upstream-client wrapper accepts UTF-8 credentials on stdin for the GUI.
    shutil.copy2(root / "stdin_account.cpp", stage / "src" / "clientsample" / "stdin_account.cpp")
    sample_cmake = stage / "src" / "clientsample" / "CMakeLists.txt"
    text = sample_cmake.read_text(encoding="utf-8")
    if "add_executable(shadnet-admin" not in text:
        sample_cmake.write_text(text + '''
add_executable(shadnet-admin stdin_account.cpp client.cpp connection.cpp "${PROTO_OUT}/shadnet.pb.cc")
target_include_directories(shadnet-admin PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${PROTO_OUT}"
    "${CMAKE_CURRENT_SOURCE_DIR}/../externals/protobuf/src")
target_link_libraries(shadnet-admin PRIVATE libprotobuf Qt6::Core ws2_32)
target_compile_definitions(shadnet-admin PRIVATE _WIN32_WINNT=0x0601 WIN32_LEAN_AND_MEAN
    _CRT_SECURE_NO_WARNINGS NOMINMAX)
''', encoding="utf-8")


if __name__ == "__main__":
    prepare()
