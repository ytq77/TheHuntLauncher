"""Apply bounded read-only join-cause diagnostics after the reviewed wipe candidate."""
from pathlib import Path
import argparse
import shutil


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unexpected join-cause anchor: ' + before[:100])
    return text.replace(before, after, 1)


def validate(text, required):
    for anchor in required:
        if text.count(anchor) != 1:
            raise ValueError('Partial/duplicate join-cause application: ' + anchor[:100])
    return text


def apply_core(text):
    expected=['#include "bbsc_join_cause.inc"','bool J4JoinCauseEnabled(bool) noexcept;',
              'void J4JoinCauseRegisterClose(void (*close)(std::uint64_t)) noexcept;',
              'bool J4JoinCauseWindow(std::int32_t,std::int32_t,std::uint64_t&,std::uint64_t&,std::uint64_t&,std::uint64_t&) noexcept;',
              '    if (installed) InstallBbscJoinCauseHooks();']
    if any(anchor in text for anchor in expected):
        return validate(text,expected)
    declarations='''bool J4JoinCauseEnabled(bool) noexcept;
void J4JoinCauseRegisterClose(void (*close)(std::uint64_t)) noexcept;
bool J4JoinCauseWindow(std::int32_t,std::int32_t,std::uint64_t&,std::uint64_t&,std::uint64_t&,std::uint64_t&) noexcept;
'''
    text=once(text,'bool RelayAuthPrivate();',declarations+'bool RelayAuthPrivate();')
    text=once(text,'bool BbscConnectFirstEnabled() {','#include "bbsc_join_cause.inc"\n\nbool BbscConnectFirstEnabled() {')
    return once(text,'    InstallBbscBannerHook();\n}', '    InstallBbscBannerHook();\n    if (installed) InstallBbscJoinCauseHooks();\n}')


def apply_http(text):
    if '#include "bbsc_join_cause_adapter.inc"' in text:
        return validate(text,['#include "bbsc_j4_diag.inc"\n#endif\n#include "bbsc_join_cause_adapter.inc"'])
    return once(text,'#include "bbsc_j4_diag.inc"\n#endif', '#include "bbsc_j4_diag.inc"\n#endif\n#include "bbsc_join_cause_adapter.inc"')


def apply_j4(text):
    if 'joinCauseClose' in text:
        return validate(text,['inline std::atomic<unsigned> activeWindows{0};\ninline std::atomic<void (*)(U64)> joinCauseClose{nullptr};',
                              '        Summary(*it,it->end);\n        if (const auto close=joinCauseClose.load(std::memory_order_acquire)) close(it->id);'])
    text=once(text,'inline std::atomic<unsigned> activeWindows{0};', 'inline std::atomic<unsigned> activeWindows{0};\ninline std::atomic<void (*)(U64)> joinCauseClose{nullptr};')
    return once(text,'        Summary(*it,it->end);', '        Summary(*it,it->end);\n        if (const auto close=joinCauseClose.load(std::memory_order_acquire)) close(it->id);')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    args=parser.parse_args();root=args.root.resolve();patches=root/'patches';emu=root/'.build/bbsc/emulator'
    core=emu/'src/core/bloodborne_re_zh_cn.cpp';http=emu/'mod/bbsc_http.cpp';j4=patches/'bbsc_j4_diag.inc'
    # Compute and validate all unique anchors before changing any file.
    changes={core:apply_core(core.read_text('utf-8')),http:apply_http(http.read_text('utf-8')),j4:apply_j4(j4.read_text('utf-8'))}
    for path,text in changes.items():path.write_text(text,'utf-8',newline='\n')
    shutil.copy2(patches/'bbsc_join_cause.inc',emu/'src/core/bbsc_join_cause.inc')
    shutil.copy2(patches/'bbsc_join_cause_adapter.inc',emu/'mod/bbsc_join_cause_adapter.inc')
    shutil.copy2(j4,emu/'mod/bbsc_j4_diag.inc')


if __name__=='__main__':main()
