// The Blank menu caches PlayerIns::vfunc558 (role metadata +0x15) at
// 0x01BBC625. A kept-session cooperator can have character type 1 but role
// class 0. Native 0x01BC6CF0 then displays "No resonance" (302123), yet its
// confirmation still sets menu+0x470=1 and executes the item. This produces
// the reported error-then-use/return sequence. Native class 7 is the ordinary
// cooperator's "Return to Your Own World" confirmation (14011).
// Repair only this menu's missing class; do not write network roles, item
// effects or character types, and retain the native confirmation/cancel path.
void PS4_SYSV_ABI BbscBlankMenuRole(u64, const GuestRegisterSnapshot* r) {
    if (r == nullptr || GetSessionState() != 6 || bbsc_spectate_active) return;
    const u64 global = GetGlobalState();
    const u64 player = GetLocalPlayerCharacter();
    if (!ReadablePointer(global, 0x1593) || !ReadablePointer(player, 0x498) ||
        ReadValue<u8>(global, 0x08) != 0 || ReadValue<u8>(global, 0x1592) != 0 ||
        ReadValue<u32>(player, 0x1C0) != 11251 || GetCharacterHp(player) <= 0) return;
    const s32 slot_type = GetLocalPlayerType(), chr_type = GetLocalCharacterType();
    LOG_INFO(Debug, "BBSC Blank menu: native class {}, slot type {}, character type {}",
             static_cast<u32>(r->rax), slot_type, chr_type);
    if (static_cast<u32>(r->rax) != 0 || slot_type != 1 || chr_type != 1) return;
    const_cast<GuestRegisterSnapshot*>(r)->rax = 7;
    LOG_INFO(Debug, "BBSC Blank menu: missing role class 0 -> native cooperator class 7; confirmation/cancel unchanged");
}

void InstallBbscBlankMenuHook() {
    constexpr u64 offset = 0x01BBC625;
    // mov dword ptr [rbp-0x224C],eax: one complete, non-relative instruction.
    constexpr std::array<u8, 6> store{0x89,0x85,0xB4,0xDD,0xFF,0xFF};
    if (!InstallGuestCodeHook(reinterpret_cast<void*>(image_base + offset),
                              std::span<const u8>{store}, 0, BbscBlankMenuRole)) {
        LOG_ERROR(Debug, "BBSC Blank menu: role-cache signature mismatch; fix not installed");
    }
}
