"""Apply only the investigated personal shop/reward changes after session recovery."""
import argparse
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]

def once(text,before,after):
    if text.count(before)!=1:raise ValueError('Unexpected pinned source marker: '+before[:90])
    return text.replace(before,after,1)

def apply_header(text):
    text=once(text,'constexpr std::array<ByteEdit, 17> ByteEdits{{',
        'constexpr std::array<ByteEdit, 18> ByteEdits{{')
    return once(text,
        '    {"GuestItems.GiveToGuest", 0x017D88C2, {0x74, 0x0F}, {0x90, 0x90}, Group::GuestItems},',
        '''    {"GuestItems.GiveToGuest", 0x017D88C2, {0x74, 0x0F}, {0x90, 0x90}, Group::GuestItems},
    // The corpse lottery has already selected a personal outcome here. Keep
    // solo eligibility instead of filtering that outcome out just for a guest;
    // otherwise observing the roll alone would lose e.g. special enemy items.
    {"GuestItems.PersonalEnemyLots", 0x017DD764, {0x0F, 0x84}, {0x90, 0xE9}, Group::GuestItems},''')

def apply_home_proof(core):
    # The pinned session stage is verified first. Extend only its evidence
    # collection; preserve its restore transaction and all refusal guards.
    path=core/'bbsc_world_recovery.inc'
    text=path.read_text(encoding='utf-8')
    text=once(text,'bool BbscRestoreWorldTransaction(std::string_view why) {',
        'void BbscHomeProofRestored();\n\nbool BbscRestoreWorldTransaction(std::string_view why) {')
    text=once(text,'    bbsc_own_world.clear(); bbsc_own_world_dirty = false; bbsc_own_world_slot.reset();',
        '    BbscHomeProofRestored(); // before events can change the restored bank\n'
        '    bbsc_own_world.clear(); bbsc_own_world_dirty = false; bbsc_own_world_slot.reset();')
    path.write_text(text,encoding='utf-8',newline='\n')
    path=core/'bbsc_save_proof.inc';text=path.read_text(encoding='utf-8')
    text=once(text,'void PS4_SYSV_ABI BbscSaveProofEntry(u64, const GuestRegisterSnapshot*) {',
        '#include "bbsc_home_save_proof.inc"\n\n'
        'void PS4_SYSV_ABI BbscSaveProofEntry(u64, const GuestRegisterSnapshot*) {\n'
        '    BbscHomeProofSaveEntry();')
    text=once(text,'    if (BbscSaveProofFolder().empty()) return;',
        '    if (BbscSaveProofFolder().empty() && !BbscWorldRecoveryEnabled()) return;')
    path.write_text(text,encoding='utf-8',newline='\n')
    shutil.copy2(ROOT/'patches/bbsc_home_save_proof.inc',core/'bbsc_home_save_proof.inc')

def apply(text):
    text=once(text,'constexpr std::array<s64, 21> BbscDreamGuestEvents{\n',
        '// Common event 5500 must reach its own weapon-possession check for a guest.\n'
        '// Its early client End otherwise completes every slot and unlocks all 22 shelves.\n'
        'constexpr std::array<s64, 22> BbscDreamGuestEvents{\n    5500,\n')
    text=once(text,'        } else if (key == 12101) {\n            keep_bits(20, 29);',
        '''        } else if (key == 12101) {
            keep_bits(20, 29);
            // Badge shelves are personal too. Use the pre-join baseline, not
            // the live table vanilla may already have overlaid with the host.
            if (const u8* own = BbscOwnDreamBaseline(key, before)) {
                for (u32 bit : {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 9u}) {
                    const u8 mask = static_cast<u8>(0x80u >> (bit % 8));
                    live[bit / 8] = static_cast<u8>((live[bit / 8] & ~mask) | (own[bit / 8] & mask));
                }
            }''')
    start=text.index('// ---- Enemy drops for the guest (2026-10-08)')
    end=text.index('// ---- Shared progress, merge mode',start)
    text=text[:start]+'// ---- Personal enemy rewards ---------------------------------------------------------------\n#include "bbsc_personal_rewards.inc"\n\n'+text[end:]
    text=once(text,'void ApplyBbscGuestLootView(u32 packed_map, std::string_view why);',
        'void ResetBbscGuestDrops();\nvoid ApplyBbscGuestLootView(u32 packed_map, std::string_view why);')
    text=once(text,'void ApplyBbscGuestLootViewAtLoad() {',
        'void ApplyBbscGuestLootViewAtLoad() {\n    ResetBbscGuestDrops(); // handles and drop ownership belong to this load only')
    text=once(text,'void InstallBbscEchoPathHooks() {\n    if (image_base == 0) {\n        return;\n    }',
        'void InstallBbscEchoPathHooks() {\n    if (image_base == 0) {\n        return;\n    }\n    InstallBbscPersonalDropHook();')
    text=once(text,'s32 BbscItemFlags(u32 id) {',
        '''// Corpse items in list +0x38 carry their ChrIns runtime handle at +0x2C
// (also used by 0x018BD8D1 when removing an actor's drops on respawn).
u32 BbscCorpseOwner(u64 manager, u32 id) {
    if (!ReadablePointer(manager, 0x40)) return 0;
    u64 item = ReadValue<u64>(manager, 0x38);
    for (int n = 0; item && n < 1024; ++n) {
        if (!ReadablePointer(item, 0x50)) break;
        if (ReadValue<u32>(item, 0x20) == id) return ReadValue<u32>(item, 0x2C);
        item = ReadValue<u64>(item, 0);
    }
    return 0;
}

s32 BbscItemFlags(u32 id) {''')
    text=once(text,'            const s32 flag = ReadValue<s32>(item, 0x28);\n            const bool flag_set',
        '''            if (list == 2 && BbscGuestCorpseClaimed(ReadValue<u32>(item, 0x2C))) {
                if ((ReadValue<u16>(item, 0x48) & 0x40) && stale.size() < 256)
                    stale.push_back(ReadValue<u32>(item, 0x20));
                item = ReadValue<u64>(item, 0);
                continue; // this enemy already has its one personal fallback roll
            }
            const s32 flag = ReadValue<s32>(item, 0x28);
            const bool flag_set''')
    text=once(text,'        const u32 id = ReadValue<u32>(registers->rsi, 0);\n        LOG_INFO(Debug, "BBSC pickup: take item',
        '''        const u32 id = ReadValue<u32>(registers->rsi, 0);
        const u64 manager = ReadValue<u64>(image_base + ItemManagerPointerOffset, 0);
        const u32 actor = BbscCorpseOwner(manager, id);
        if (BbscGuestInHostWorld() && actor) {
            std::scoped_lock lock{bbsc_guest_drop_mutex};
            if (bbsc_guest_drop_ledger.ClaimsCorpse(actor)) {
                // Run the original function with its normal "no item" id.
                // Its own validation returns normally; no global skip flag or
                // guessed return value is shared by simultaneous game threads.
                static thread_local u32 no_item = 0;
                no_item = 0;
                const_cast<GuestRegisterSnapshot*>(registers)->rsi = reinterpret_cast<u64>(&no_item);
                LOG_INFO(Debug, "BBSC personal drops: duplicate corpse item {:#x} for actor {:#x} ignored", id, actor);
                return;
            }
            bbsc_guest_drop_ledger.NativeRoll(actor); // physical pickup wins before a late reward callback
        }
        LOG_INFO(Debug, "BBSC pickup: take item''')
    text=once(text,'    LOG_INFO(Debug, "BBSC pickup trace: reach={} treasure={} accepted={} take={} give={}", reach,',
        '    bbsc_guest_drop_pickup_hook = take;\n    LOG_INFO(Debug, "BBSC pickup trace: reach={} treasure={} accepted={} take={} give={}", reach,')
    return text

def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);args=parser.parse_args()
    core=args.source/'emulator/src/core';path=core/'bloodborne_re_zh_cn.cpp'
    text=path.read_text(encoding='utf-8');updated=apply(text)
    header=core/'bbsc_patch_tables.h'
    updated_header=apply_header(header.read_text(encoding='utf-8'))
    for name in ['bbsc_personal_rewards.inc','bbsc_personal_rewards_policy.h']:
        shutil.copy2(ROOT/'patches'/name,core/name)
    path.write_text(updated,encoding='utf-8',newline='\n')
    header.write_text(updated_header,encoding='utf-8',newline='\n')
    apply_home_proof(core)
    print('Applied personal shop checks and single-path guest reward handling.')

if __name__=='__main__':main()
