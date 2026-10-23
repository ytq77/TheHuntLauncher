"""Stage only verified dynamic graphics runtimes from the pinned upstream caches."""
import ast
import hashlib
import json
from pathlib import Path
import shutil


def constants(path, names):
    result = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    result[target.id] = ast.literal_eval(node.value)
    if set(result) != set(names):
        raise ValueError("Pinned graphics manifest changed: " + str(path))
    return result


def verified_copy(source, target, size, expected):
    with source.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if source.stat().st_size != size or actual != expected:
        raise ValueError("Graphics runtime verification failed: " + source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def stage_graphics_assets(port, dest, out):
    port, dest, out = Path(port), Path(dest), Path(out)
    scripts = port / "packaging/windows"
    sdk = constants(scripts / "fetch_fsr411_dx12.py", {"DLL_NAME", "DLL_SIZE", "DLL_SHA256"})
    files = {sdk["DLL_NAME"]: (sdk["DLL_SIZE"], sdk["DLL_SHA256"])}
    files.update(constants(scripts / "fetch_fg401_dx12.py", {"FILES"})["FILES"])
    for name, (size, expected) in files.items():
        verified_copy(port / "out/fsr411-dx12" / name, dest / "bin" / name, size, expected)
    provider = constants(port / "scripts/fetch_fsr4vk.py", {"FILES"})
    cache = port / "out/fsr4vk/provider"
    for _, name, size, expected in provider["FILES"]:
        verified_copy(cache / name, dest / "fsr4vk" / name, size, expected)
    shutil.copy2(cache / "SOURCE.txt", dest / "fsr4vk/SOURCE.txt")
    if (cache / "PATCH.md").is_file():
        shutil.copy2(cache / "PATCH.md", dest / "fsr4vk/PATCH.md")
    provenance = constants(scripts / "prepare_fsr4vk.py", {"SOURCE_NAME", "SOURCE_SIZE", "SOURCE_SHA256"})
    source = out / provenance["SOURCE_NAME"]
    verified_copy(port / "out/fsr4vk" / source.name, source,
                  provenance["SOURCE_SIZE"], provenance["SOURCE_SHA256"])
    dlss = json.loads((Path(__file__).with_name("dlss-runtime.lock.json")).read_text(encoding="utf-8"))
    for relative, entry in dlss["files"].items():
        verified_copy(port / "out/dlss" / relative, dest / relative, entry["size"], entry["sha256"])
    (dest / "source-info/DLSS-runtime-source.json").write_text(json.dumps(dlss, indent=2) + "\n", encoding="utf-8")
    return source
