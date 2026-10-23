"""Keep the pinned engine's GPU helper lookup valid behind a mod loader."""


def patched_run_script(text):
    before = "probe.parent / 'bb-gpu-capabilities.exe'"
    if text.count(before) != 2:
        raise ValueError("Unexpected run.py capability lookups; refusing an unverified change.")
    return text.replace(before, "Path(env.get('BBSC_EMULATOR', str(probe))).parent / 'bb-gpu-capabilities.exe'")
