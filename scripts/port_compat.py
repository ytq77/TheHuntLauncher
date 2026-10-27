"""Keep the pinned engine's GPU helper lookup valid behind a mod loader."""


def patched_run_script(text):
    before = "probe.parent / 'bb-gpu-capabilities.exe'"
    if text.count(before) != 2:
        raise ValueError("Unexpected run.py capability lookups; refusing an unverified change.")
    return text.replace(before, "Path(env.get('BBSC_EMULATOR', str(probe))).parent / 'bb-gpu-capabilities.exe'")


def patched_mods_script(text):
    if 'from quit_text_compat import prepare_layer' in text:
        raise ValueError('Quit text compatibility is already installed in mods.py.')
    before="    try:\n        print(build_overlay(args.game, args.out, layers))"
    after="""    from quit_text_compat import prepare_layer
    compatibility = prepare_layer(layers, args.out, mod_files)
    if compatibility is not None:
        layers.append(compatibility)
    try:
        print(build_overlay(args.game, args.out, layers))"""
    if text.count(before)!=1:
        raise ValueError('Unexpected mods.py layer construction; recheck quit text compatibility.')
    return text.replace(before,after,1)
