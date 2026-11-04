// Keep the summoning origin apart from the forced placement used by follow warps.
// Vanilla OnLeave_Limit reads that placement; following a host must not move home.
struct BbscGuestReturnOrigin {
    bool valid = false;
    u32 map = 0;
    std::array<float, 4> position{}, rotation{};
};
BbscGuestReturnOrigin bbsc_guest_return_origin;

void CaptureBbscGuestReturnOrigin() {
    bbsc_guest_return_origin = {};
    const u64 global = GetGlobalState();
    const u32 map = GetCurrentPackedMap();
    const u64 player = GetLocalPlayerCharacter();
    if (!ReadablePointer(global, 0x1593) || ReadValue<u8>(global, 0x1592) == 0 ||
        ReadValue<u8>(global, 0x08) != 0 || !IsUsablePackedMap(map) ||
        player == 0 || GetCharacterHp(player) <= 0) return;
    const auto here = ReadCharacterTransform(player);
    if (!here) return;
    bbsc_guest_return_origin = {true, map, here->position, here->rotation};
    LOG_INFO(Debug, "BBSC guest return: own origin captured in map {:#x} at ({:.2f}, {:.2f}, {:.2f})",
             map, here->position[0], here->position[1], here->position[2]);
}

bool RestoreBbscGuestReturnPlacement(u64 global) {
    if (!bbsc_guest_return_origin.valid || GetSessionState() != 0 || GetLocalPlayerType() != 1 ||
        !ReadablePointer(global, 0x1593) || ReadValue<u8>(global, 0x1592) != 0 ||
        !HasMemoryAccess(global + 0x14F0, 0x30, MemoryProt::CpuWrite)) return false;
    // Check the complete destination span before any write. Let the native step
    // set forced-warp, session type, loading and save bookkeeping itself.
    std::memcpy(reinterpret_cast<void*>(global + 0x14F0), &bbsc_guest_return_origin.map, 4);
    std::memcpy(reinterpret_cast<void*>(global + 0x1500), bbsc_guest_return_origin.position.data(), 16);
    std::memcpy(reinterpret_cast<void*>(global + 0x1510), bbsc_guest_return_origin.rotation.data(), 16);
    return true;
}

void ReturnBbscGuestAfterSessionEnd() {
    const u64 global = GetGlobalState();
    if (!ReadablePointer(global, 0x1593) || ReadValue<u8>(global, 0x08) != 0 ||
        GetCharacterHp(GetLocalPlayerCharacter()) <= 0 ||
        !RestoreBbscGuestReturnPlacement(global)) return;
    // CUSA03173 1.09 OnLeave_Limit has no input arguments. Its native body sets
    // event state 2, forced warp and StageTransition, then queues the own type.
    // Do this once at the unexpected session end, while persistence is lifted,
    // instead of leaving an interactive guest behind until a later Lua step.
    using SendHome = void PS4_SYSV_ABI (*)();
    reinterpret_cast<SendHome>(image_base + 0x0138A4A0)();
    LOG_INFO(Debug, "BBSC guest return: lost session; native return started toward own origin {:#x}",
             bbsc_guest_return_origin.map);
}
