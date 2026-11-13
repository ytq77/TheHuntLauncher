// CUSA03173 1.09: native selected slot + successful full role deserialization.
// Never infer a character from file timestamps, a name or an object address alone.
struct BbscActiveSaveLoad {
    u64 generation = 0, manager = 0, player_data = 0, stack = 0, lineage = 0;
    s32 slot = -1;
};
std::mutex bbsc_active_save_mutex;
u64 bbsc_active_save_generation = 0;
BbscActiveSaveLoad bbsc_active_save;
thread_local BbscActiveSaveLoad bbsc_active_save_loading;
std::atomic<bool> bbsc_active_save_hooks{false};

u64 BbscActivePlayerData() {
    const auto holder = ReadValue<u64>(image_base+0x0553B130, 0);
    return ReadablePointer(holder, 0x10) ? ReadValue<u64>(holder, 8) : 0;
}

void InvalidateBbscActiveSave() {
    std::scoped_lock lock{bbsc_active_save_mutex};
    ++bbsc_active_save_generation;
    bbsc_active_save = {};
}

void PS4_SYSV_ABI BbscActiveSaveNewCharacter(u64, const GuestRegisterSnapshot*) {
    InvalidateBbscActiveSave();
}

void PS4_SYSV_ABI BbscActiveSaveLoadBegin(u64, const GuestRegisterSnapshot* registers) {
    try {
        std::scoped_lock lock{bbsc_active_save_mutex};
        const auto previous = bbsc_active_save;
        ++bbsc_active_save_generation;
        bbsc_active_save = {};
        bbsc_active_save_loading = {};
        if (!bbsc_active_save_hooks || !registers) return;
        const auto manager = GetGlobalState();
        if (!ReadablePointer(manager, 0x1752) || registers->rdi != manager ||
            registers->rdx != 0x140000 || !ReadablePointer(registers->rsi, 84) ||
            !ReadablePointer(registers->rsp, 8) || ReadValue<u8>(manager, 0x1751) < 100)
            return; // Native debug-file loads are not this launcher's savedata mount.
        const auto caller = ReadValue<u64>(registers->rsp, 0);
        if (caller != image_base+0x0193F823 && caller != image_base+0x01C2B453) return;
        const auto slot = ReadValue<s32>(manager, 0x1490);
        if (slot < 0 || slot > 9 || ReadValue<u32>(registers->rsi, 0) != 0x41) return;
        bbsc_active_save_loading = {bbsc_active_save_generation, manager, 0,
                                   registers->rsp,
                                   previous.manager == manager && previous.slot == slot ?
                                       previous.lineage : bbsc_active_save_generation,
                                   slot};
    } catch (...) { /* Missing evidence leaves this load unbound. */ }
}

void PS4_SYSV_ABI BbscActiveSaveLoadEnd(u64, const GuestRegisterSnapshot* registers) {
    try {
        std::scoped_lock lock{bbsc_active_save_mutex};
        const auto load = bbsc_active_save_loading;
        bbsc_active_save_loading = {};
        const auto manager = GetGlobalState();
        if (!registers || !load.generation || !bbsc_active_save_hooks ||
            load.generation != bbsc_active_save_generation || (registers->r14 & 0xff) != 1 ||
            registers->rbp+8 != load.stack || manager != load.manager ||
            !ReadablePointer(manager, 0x1752) || ReadValue<s32>(manager, 0x1490) != load.slot ||
            ReadValue<u8>(manager, 0x1751) < 100) return;
        const auto data = BbscActivePlayerData();
        if (!ReadablePointer(data, 0x3B8)) return;
        bbsc_active_save = load;
        bbsc_active_save.player_data = data;
        LOG_INFO(Debug, "BBSC active save: native slot {} loaded successfully, generation {}",
                 load.slot, load.generation);
    } catch (...) { /* No exceptions may leave a native load hook. */ }
}

std::optional<BbscActiveSaveLoad> BbscNativeActiveSaveToken() {
    std::scoped_lock lock{bbsc_active_save_mutex};
    const auto manager = GetGlobalState();
    const char* folder = std::getenv("BBSC_SAVE_DIR");
    if (!bbsc_active_save_hooks || !folder || !folder[0] ||
        !bbsc_active_save.generation || manager != bbsc_active_save.manager ||
        !ReadablePointer(manager, 0x1752) ||
        ReadValue<s32>(manager, 0x1490) != bbsc_active_save.slot ||
        ReadValue<u8>(manager, 0x1751) < 100 ||
        BbscActivePlayerData() != bbsc_active_save.player_data) {
        if (bbsc_active_save.generation) ++bbsc_active_save_generation;
        bbsc_active_save = {};
        return std::nullopt;
    }
    std::error_code error;
    const auto path = std::filesystem::path(folder) /
                      fmt::format("userdata{:04}", bbsc_active_save.slot);
    if (std::filesystem::file_size(path, error) != 0x140000 || error) return std::nullopt;
    return bbsc_active_save;
}

std::optional<BbscSaveSlot> BbscNativeActiveSaveSlot() {
    const auto active = BbscNativeActiveSaveToken();
    if (!active) return std::nullopt;
    return BbscSaveSlot{"save", active->slot};
}

void InstallBbscActiveSaveHooks() {
    static constexpr std::array<u8,14> getter{
        0x48,0x8b,0x05,0xc1,0xa0,0xfe,0x03,0x8b,0x80,0x90,0x14,0x00,0x00,0xc3};
    static constexpr std::array<u8,56> entry{
        0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,
        0x48,0x81,0xe4,0xe0,0xff,0xff,0xff,0x48,0x81,0xec,0xe0,0x00,0x00,0x00,
        0x49,0x89,0xfe,0x4c,0x8b,0x25,0x4b,0x82,0xe7,0x03,0x49,0x8b,0x04,0x24,
        0x48,0x89,0x84,0x24,0xc0,0x00,0x00,0x00,0x48,0x8d,0x05,0x38,0x72,0xe4,0x03};
    static constexpr std::array<u8,32> end{
        0x49,0x8b,0x04,0x24,0x48,0x3b,0x84,0x24,0xc0,0x00,0x00,0x00,0x75,0x12,
        0x44,0x88,0xf0,0x48,0x8d,0x65,0xd8,0x5b,0x41,0x5c,0x41,0x5d,0x41,0x5e,
        0x41,0x5f,0x5d,0xc3};
    static constexpr std::array<u8,22> create{
        0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x41,0x54,0x53,0x4c,0x8b,0x77,0x08,
        0x4c,0x8d,0x3d,0x3a,0xcb,0x9f,0x03};
    const auto matches = [](u64 offset, auto& bytes) {
        return ReadablePointer(image_base+offset, bytes.size()) &&
               std::memcmp(reinterpret_cast<const void*>(image_base+offset),
                           bytes.data(), bytes.size()) == 0;
    };
    if (!matches(0x0156C5B0, getter) || !matches(0x0156B970, entry) ||
        !matches(0x0156BC6C, end) || !matches(0x01B3E5E0, create)) {
        LOG_WARNING(Debug, "BBSC active save: native signatures unavailable; no timestamp fallback");
        return;
    }
    const bool begin = InstallGuestCodeHook(reinterpret_cast<void*>(image_base+0x0156B970),
        std::span<const u8>{entry.data(), 13}, 0, BbscActiveSaveLoadBegin);
    const bool finish = InstallGuestCodeHook(reinterpret_cast<void*>(image_base+0x0156BC7A),
        std::span<const u8>{end.data()+14, 7}, 0, BbscActiveSaveLoadEnd);
    const bool new_character = InstallGuestCodeHook(reinterpret_cast<void*>(image_base+0x01B3E5E0),
        std::span<const u8>{create.data(), 15}, 0, BbscActiveSaveNewCharacter);
    bbsc_active_save_hooks = begin && finish && new_character;
    LOG_INFO(Debug, "BBSC active save: success-bound native slot hooks {}",
             bbsc_active_save_hooks.load() ? "ready" : "unavailable");
}
