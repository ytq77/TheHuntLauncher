// Personal rewards: coordinate native corpse rolls and the existing guest fallback.
#include "bbsc_personal_rewards_policy.h"
std::mutex bbsc_guest_drop_mutex;
BbscPersonalRewards::DropLedger bbsc_guest_drop_ledger;
struct BbscGuestDrop {
    u32 actor;
    u32 name;
    std::array<s32, 2> lots;
};
std::vector<BbscGuestDrop> bbsc_guest_drop_queue;
bool bbsc_guest_drop_roll_hook = false, bbsc_guest_drop_pickup_hook = false;

void ResetBbscGuestDrops() {
    std::scoped_lock lock{bbsc_guest_drop_mutex};
    bbsc_guest_drop_queue.clear();
    bbsc_guest_drop_ledger.Clear();
}

// CUSA03173 1.09: this site is reached immediately before the corpse's actual
// lottery, after all eligibility gates. Reaching it counts even when RNG picks
// nothing; absence of a visible glow must never cause a second roll.
void PS4_SYSV_ABI BbscNativeCorpseRollHook(u64, const GuestRegisterSnapshot* registers) {
    if (!registers || !BbscGuestInHostWorld() ||
        !HasMemoryAccess(registers->rbp - 0x264, 4, MemoryProt::CpuRead)) return;
    const u32 actor = ReadValue<u32>(registers->rbp - 0x264, 0);
    std::scoped_lock lock{bbsc_guest_drop_mutex};
    bbsc_guest_drop_ledger.NativeRoll(actor);
    static u32 logged = 0;
    if (logged++ < 80)
        LOG_INFO(Debug, "BBSC personal drops: native lottery reached for actor {:#x}; empty result also counts", actor);
}

void InstallBbscPersonalDropHook() {
    if (!EnvFlagEnabled("SHADPS4_BBSC_GUEST_ITEMS")) return;
    if (ReadValue<u8>(image_base + 0x017DD764, 0) != 0x90 ||
        ReadValue<u8>(image_base + 0x017DD764, 1) != 0xE9) {
        LOG_INFO(Debug, "BBSC personal drops: corpse eligibility patch not active; fallback disabled");
        return;
    }
    constexpr std::array<u8, 7> site{0x48, 0x8D, 0xBD, 0x40, 0xFF, 0xFF, 0xFF};
    bbsc_guest_drop_roll_hook = InstallGuestCodeHook(
        reinterpret_cast<void*>(image_base + 0x017DD5B7), std::span<const u8>{site},
        0, BbscNativeCorpseRollHook);
    LOG_INFO(Debug, "BBSC personal drops: native lottery observer {}; fallback requires pickup guard too",
             bbsc_guest_drop_roll_hook);
}

bool BbscGuestCorpseClaimed(u32 actor) {
    std::scoped_lock lock{bbsc_guest_drop_mutex};
    return bbsc_guest_drop_ledger.ClaimsCorpse(actor);
}

void NoteBbscGuestEnemyDeath(u64 dead) {
    static const bool enabled = EnvFlagEnabled("SHADPS4_BBSC_GUEST_ITEMS");
    if (!enabled || !bbsc_guest_drop_roll_hook || !bbsc_guest_drop_pickup_hook ||
        image_base == 0 || !BbscGuestInHostWorld() || GetLocalPlayerType() != 1 ||
        !ReadablePointer(dead, 0x1E8) || dead == GetLocalPlayerCharacter() ||
        (ReadValue<u32>(dead, 0x1E1) & 0x240000) != 0) return;
    const u64 vtable = ReadablePointer(dead, 0x08) ? ReadValue<u64>(dead, 0) : 0;
    const u64 info_fn = ReadablePointer(vtable, 0x170) ? ReadValue<u64>(vtable, 0x168) : 0;
    if (!IsEbootCodeAddress(info_fn)) return;
    const u64 info = reinterpret_cast<u64 PS4_SYSV_ABI (*)(u64)>(info_fn)(dead);
    const u64 row = ReadablePointer(info, 0x10) ? ReadValue<u64>(info, 8) : 0;
    if (!ReadablePointer(row, 0x150) || ((ReadValue<u8>(row, 329) >> 4) & 1) != 0)
        return; // player or boss; their rewards keep their existing paths
    BbscGuestDrop drop{ReadValue<u32>(dead, 8), ReadValue<u32>(row, 8),
        {ReadValue<s32>(row, 0x2C), ReadValue<s32>(row, 0x30)}};
    // A host's historical NPC death is not a guest quest reward. Without a
    // proven personal quest interaction, leave these lots to native pickup;
    // never use a missing item as evidence of a missed kill reward.
    if (BbscPersonalRewards::IsQuestLot(drop.lots[0]) ||
        BbscPersonalRewards::IsQuestLot(drop.lots[1])) {
        static std::atomic<u32> logged{0};
        if (logged.fetch_add(1, std::memory_order_relaxed) < 40)
            LOG_INFO(Debug, "BBSC personal drops: actor {:#x} NPC quest lots {} / {} left to personal pickup; historical host state is not a missed guest reward",
                     drop.actor, drop.lots[0], drop.lots[1]);
        return;
    }
    if (drop.lots[0] <= 0 && drop.lots[1] <= 0) return;
    std::scoped_lock lock{bbsc_guest_drop_mutex};
    if (bbsc_guest_drop_queue.size() < 64 && bbsc_guest_drop_ledger.Queue(drop.actor))
        bbsc_guest_drop_queue.push_back(drop);
}

void TickBbscGuestDrops() {
    const u64 global = GetGlobalState();
    if (!BbscGuestInHostWorld()) {
        ResetBbscGuestDrops();
        return;
    }
    std::vector<BbscGuestDrop> due;
    {
        std::scoped_lock lock{bbsc_guest_drop_mutex};
        due.swap(bbsc_guest_drop_queue);
    }
    if (due.empty()) return;
    const u64 item_man = ReadValue<u64>(image_base + ItemManPointerOffset, 0);
    if (!ReadablePointer(item_man, 0x10) || !ReadablePointer(global, 0x10) ||
        ReadValue<u8>(global, 8) != 0 || GetCharacterHp(GetLocalPlayerCharacter()) <= 0) {
        ResetBbscGuestDrops(); // no reward committed; original pickups remain available
        return;
    }
    using AwardItemLot = void PS4_SYSV_ABI (*)(u64, s32, u32);
    for (const auto& drop : due) {
        {
            std::scoped_lock lock{bbsc_guest_drop_mutex};
            if (!bbsc_guest_drop_ledger.Pending(drop.actor)) continue;
            bbsc_guest_drop_ledger.Supplemented(drop.actor);
        }
        for (s32 lot : drop.lots) if (lot > 0) {
            reinterpret_cast<AwardItemLot>(image_base + AwardItemLotOffset)(item_man, lot, 0);
            LOG_INFO(Debug, "BBSC guest drop: actor {:#x} (name id {}) - one personal fallback roll of lot {}",
                     drop.actor, drop.name, lot);
        }
    }
}
