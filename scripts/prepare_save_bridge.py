"""Generate a separate, job-bound save helper; never change the normal BBSC DLL."""
import argparse
from pathlib import Path


def generate(source, output, fragment):
    source, output = Path(source), Path(output)
    core = (source / 'emulator/src/core/bloodborne_re_zh_cn.cpp').read_text(encoding='utf-8')
    marker = '} // namespace Core::Bloodborne'
    if core.count(marker) != 1:
        raise ValueError('Unexpected core namespace; recheck upstream.')
    core = core.replace(marker, Path(fragment).read_text(encoding='utf-8') + '\n' + marker)
    mod = (source / 'emulator/mod/bbsc_mod_zh_cn.cpp').read_text(encoding='utf-8')
    marker = 'using namespace Core::Bbsc::PatchTables;'
    if mod.count(marker) != 1:
        raise ValueError('Unexpected mod namespace; recheck upstream.')
    mod = mod.replace(marker, 'namespace Core::Bloodborne { void InstallSaveBridgeOnly(); }\n' + marker)
    start = mod.index("    // Wozzardman's seamless patches first:")
    stop = mod.index('\n}\n\n} // namespace', start)
    mod = mod[:start] + '    Core::Bloodborne::InstallSaveBridgeOnly();\n    return 0;\n' + mod[stop:]
    worker = mod[mod.index('DWORD WINAPI Worker(void*)'):mod.index('\n} // namespace', start)]
    for call in ('InstallSeamlessCoopPatches(', 'SyncSites(', 'InstallReverseEngineeringTrace(',
                 'StartRelay(', 'StartP2PChannel(', 'InstallHttpWrappers(', 'InstallPadWrapper('):
        if call in worker:
            raise ValueError('Normal initialization reachable in helper: ' + call)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'bloodborne_re_save.cpp').write_text(core, encoding='utf-8')
    (output / 'bbsc_mod_save.cpp').write_text(mod, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    generate(args.source, args.output, Path(__file__).resolve().parents[1] / 'patches/bbsc_save_bridge.inc')
    print('Generated isolated save helper; normal BBSC initialization excluded.')
