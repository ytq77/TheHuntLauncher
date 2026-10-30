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


def patched_pc_menu_script(text):
    # The runtime's UpscalerFsr412 is enum value 6 (119100 + 6), while the pinned
    # text generator ends at DLSS/value 5. Preserve all existing choice values.
    before="(119100, ['Off', 'FSR 3.1', 'FSR 4', 'FSR 4.1.1', 'TAA', 'DLSS']),"
    after="(119100, ['Off', 'FSR 3.1', 'FSR 4', 'FSR 4.1.1', 'TAA', 'DLSS', 'FSR 4.1.2']),"
    if text.count(before)!=1:
        raise ValueError('Unexpected PC upscaler text table; recheck runtime enum mapping.')
    return text.replace(before,after,1)


def patched_coop_saves(text):
    before = "    coop = user_dir / COOP_SAVES\n"
    after = '''    # The Hunt already owns an isolated save root and a run-bound recovery
    # transaction. Keep the engine and BBSC on that exact same character path.
    if env.get('THEHUNT_WORLD_RECOVERY_V2') == '1':
        expected = user_dir / 'savedata/1/CUSA00207/SPRJ0005'
        supplied = env.get('BBSC_SAVE_DIR')
        if not supplied or Path(supplied).resolve() != expected.resolve():
            raise ValueError('The Hunt: engine and world recovery save paths disagree')
        return user_dir
    coop = user_dir / COOP_SAVES
'''
    if text.count(before) != 1:
        raise ValueError('Unexpected 1.9.5 co-op save isolation; refusing an unverified change.')
    return text.replace(before, after, 1)
