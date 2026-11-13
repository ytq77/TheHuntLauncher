"""Bind recovery to successful native role loads and complete personal baselines."""
import argparse
from pathlib import Path
import shutil
from prepare_personal_rewards import once
from prepare_hook_guard import apply_hooks, apply_header

ROOT = Path(__file__).resolve().parents[1]


def guard_title_dream_restore(text):
    return once(text, '''void RestoreBbscOwnDreamState(std::string_view why) {
    if (bbsc_dream_own.held == 0) {''', '''void RestoreBbscOwnDreamState(std::string_view why) {
    // An old Dream overlay must never write into a newly loaded character.
    if (BbscWorldRecoveryJournalFrozen() || bbsc_dream_own.held == 0) {''')


def apply(text):
    text = guard_title_dream_restore(text)
    text = once(text, '#include <iterator>', '''extern "C" __declspec(dllimport) void* __stdcall OpenFileMappingA(unsigned long, int, const char*);
extern "C" __declspec(dllimport) void* __stdcall MapViewOfFile(void*, unsigned long, unsigned long, unsigned long, size_t);
extern "C" __declspec(dllimport) int __stdcall CloseHandle(void*);
#include <iterator>''')
    start = text.index('std::optional<BbscSaveSlot> BbscActiveSaveSlotUnchecked() {')
    end = text.index('// ---- Own world journal', start)
    text = text[:start] + '''#include "bbsc_active_save.inc"

std::optional<BbscSaveSlot> BbscActiveSaveSlotUnchecked() {
    return BbscNativeActiveSaveSlot();
}

''' + text[end:]
    text = once(text, '    InstallBbscSaveProofHook();',
                '    InstallBbscActiveSaveHooks();\n    InstallBbscSaveProofHook();')
    text = once(text, 'bool BbscWorldRecoveryCanCapture();',
                'bool BbscWorldRecoveryCanCapture();\nbool BbscOwnWorldReadyForGuest();\n'
                'bool BbscOwnWorldCaptureStillCurrent();')
    text = once(text, 'std::optional<BbscSaveSlot> bbsc_own_world_slot;',
                'std::optional<BbscSaveSlot> bbsc_own_world_slot;\nu64 bbsc_own_world_generation = 0;')
    text = once(text,
        '        HandleBbscLuaSessionEvent(ReadGuestAscii(registers->rsi, 64));',
        '''        const auto event = ReadGuestAscii(registers->rsi, 64);
        HandleBbscLuaSessionEvent(event);
        if (event == "JoinSession_White") WriteBbscOwnWorldFile();
        if (event == "JoinSession_White" && GetSessionState() != 3 &&
            (!BbscOwnWorldReadyForGuest() || !BbscOwnWorldCaptureStillCurrent())) {
            auto* invocation = const_cast<GuestRegisterSnapshot*>(registers);
            invocation->rax = 0;
            invocation->skip_original = 1;
            LOG_WARNING(Debug, "BBSC join blocked: personal save baseline is not confirmed; preserve records and reload the character");
            return;
        }''')
    text = once(text, '    CaptureBbscGuestReturnOrigin();\n    size_t kept = 0;',
        '''    const auto active = BbscNativeActiveSaveToken();
    if (BbscWorldRecoveryEnabled() && (!active || keys.empty() ||
        visited >= 50'000 || !stack.empty())) return;
    for (const auto key : keys) {
        const auto storage = BbscFlagGroupStorage(key);
        if (!ReadablePointer(storage, BbscWorldGroupBytes)) return;
    }
    if (active) {
        bbsc_own_world_slot = BbscSaveSlot{"save", active->slot};
        bbsc_own_world_generation = active->lineage;
    }
    CaptureBbscGuestReturnOrigin();
    size_t kept = 0;''')
    text = once(text, 'bool ApplyBbscHostWorldFlags(u32 packed_map, std::string_view why) {\n'
                '    if (!BbscWorldSyncEnabled() || image_base == 0) {',
                'bool ApplyBbscHostWorldFlags(u32 packed_map, std::string_view why) {\n'
                '    if (!BbscWorldSyncEnabled() || image_base == 0 || !BbscOwnWorldReadyForGuest()) {')
    text = once(text, '    if (!enabled || image_base == 0 || !BbscGuestLootActive()) {',
                '    if (!enabled || image_base == 0 || !BbscGuestLootActive() ||\n'
                '        !BbscOwnWorldReadyForGuest()) {')
    text = once(text, '''    if (!ec) {
        bbsc_own_world_dirty = false;
        if (BbscWorldRecoveryEnabled()) BbscRecordWorldRecovery(1);
    }''', '''    if (!ec) {
        if (BbscWorldRecoveryEnabled() && !BbscRecordWorldRecovery(1)) return;
        bbsc_own_world_dirty = false; // Baseline and request both published.
    }''')
    text = once(text, 'void WriteBbscOwnWorldFile() {',
                'void WriteBbscOwnWorldFile() {\n'
                '    if (BbscWorldRecoveryEnabled() && !BbscOwnWorldCaptureStillCurrent()) return;')
    text = once(text, '    BbscWorldRecoveryQuit(false);\n    SuspendBbscPersistence("quit to title");',
                '    BbscWorldRecoveryQuit(false);\n    InvalidateBbscActiveSave();\n'
                '    SuspendBbscPersistence("quit to title");')
    text = once(text, '#include "bbsc_save_proof.inc"', '''bool BbscOwnWorldCaptureStillCurrent() {
    if (!BbscWorldRecoveryEnabled()) return true;
    const auto active = BbscNativeActiveSaveToken();
    return active && bbsc_own_world_slot && active->slot == bbsc_own_world_slot->slot &&
           bbsc_own_world_generation && active->lineage == bbsc_own_world_generation;
}

bool BbscOwnWorldReadyForGuest() {
    return !BbscWorldRecoveryEnabled() ||
           (BbscOwnWorldCaptureStillCurrent() && !bbsc_own_world.empty() && bbsc_own_world_slot.has_value() &&
            !bbsc_own_world_dirty && !bbsc_recovery_title_exit &&
            bbsc_recovery_initial_indices.size() == bbsc_own_world.size());
}

#include "bbsc_save_proof.inc"''')
    return text


def apply_recovery(text):
    text = once(text, 'BbscRecoveryQuit = 4, BbscRecoveryBlocked = 5;',
                'BbscRecoveryQuit = 4, BbscRecoveryBlocked = 5, BbscRecoveryReloaded = 6;')
    text = once(text, '        bbsc_recovery_waiting = false;\n        bbsc_recovery_rows.clear();',
                '        bbsc_recovery_waiting = false;\n        bbsc_recovery_title_exit = false;\n'
                '        bbsc_recovery_rows.clear();')
    return once(text, '''void BbscWorldRecoveryLoadedAfterTitle() {
    if (BbscWorldRecoveryEnabled() && bbsc_recovery_title_exit) {
        BbscRecoverySealQuit(0);
        if (!bbsc_own_world.empty() && !BbscRecoverySetMode(BbscRecoveryBlocked))
            LOG_WARNING(Debug, "BBSC own world: title reloaded; quit seal cleared, request update failed");
    }
}''', '''bool BbscRecordHomeSaveProof(u64 title_generation);

void BbscWorldRecoveryLoadedAfterTitle() {
    if (!BbscWorldRecoveryEnabled() || !bbsc_recovery_title_exit || bbsc_recovery_waiting)
        return;
    BbscRecoverySealQuit(0);
    if (bbsc_own_world.empty()) return;
    if (bbsc_recovery_mode != BbscRecoveryBlocked && !BbscRecoverySetMode(BbscRecoveryBlocked)) {
        LOG_WARNING(Debug, "BBSC own world: title reloaded; quit seal cleared, request update failed");
        return;
    }
    // Recognize an already-restored load without replaying old world flags.
    const auto active = BbscNativeActiveSaveToken();
    if (!active || active->slot != bbsc_recovery_slot || !BbscHomeForOwnWorld() ||
        !BbscRecordHomeSaveProof(active->generation) || !BbscRecoverySetMode(BbscRecoveryReloaded))
        return;
    bbsc_dream_own.held = 0; // Discard the old overlay without writing it back.
    bbsc_own_world.clear();
    bbsc_own_world_dirty = false;
    bbsc_own_world_slot.reset();
    bbsc_recovery_waiting = true;
    LOG_INFO(Debug, "BBSC own world: own character reloaded; awaiting save verification");
}''')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    core = args.source/'emulator/src/core'
    path = core/'bloodborne_re_zh_cn.cpp'
    updated = apply(path.read_text('utf-8'))
    shutil.copy2(ROOT/'patches/bbsc_active_save.inc', core)
    path.write_text(updated, encoding='utf-8', newline='\n')
    menu = core/'bbsc_menu_quit.inc'
    text = once(menu.read_text('utf-8'), '    BbscWorldRecoveryQuit(true);',
                '    BbscWorldRecoveryQuit(true);\n    InvalidateBbscActiveSave();')
    menu.write_text(text, encoding='utf-8', newline='\n')
    recovery = core/'bbsc_world_recovery.inc'
    text = once(recovery.read_text('utf-8'),
                '    if (!BbscWorldRecoveryEnabled() || bbsc_own_world.empty() ||',
                '    if (!BbscWorldRecoveryEnabled() || !BbscOwnWorldCaptureStillCurrent() ||\n'
                '        bbsc_own_world.empty() ||')
    recovery.write_text(apply_recovery(text), encoding='utf-8', newline='\n')
    hooks = args.source/'emulator/mod/bbsc_hooks.cpp'
    hooks.write_text(apply_hooks(hooks.read_text('utf-8')), encoding='utf-8', newline='\n')
    header = core/'cpu_patches.h'
    header.write_text(apply_header(header.read_text('utf-8')), encoding='utf-8', newline='\n')
    print('Prepared success-bound native save slots and complete-baseline write guards.')


if __name__ == '__main__':
    main()
