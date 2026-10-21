// Experimental single-role save workflow. Normal BBSC installers are never called.
namespace {
std::string save_job_token;
std::string save_job_step;
std::filesystem::path save_job_root;
int save_expected_slot = -1;
std::atomic<bool> save_display_ready{false};

// Vanilla placeholders can hide equipped kit entries from the equipment menus.
// Restore only the five definitions in memory so the player can inspect/unmount them.
// Do not initialize BBSC, give items, alter original rows or write equipment slots.
void SaveBridgePrepareDisplay() {
    for (int attempt = 0; attempt < 36; ++attempt) {
        const auto tables = FindBbscParamTables("EQUIP_PARAM_GOODS_ST", 100, 1);
        size_t prepared = 0;
        for (const u64 table : tables) {
            bool complete = true;
            for (const auto& item : BbscKit) {
                if (!BbscParamRow(table, item.id, GoodsRowSize) ||
                    !BbscParamRow(table, item.copy_of ? item.copy_of : 200, GoodsRowSize)) complete = false;
            }
            if (!complete) continue;
            for (const auto& item : BbscKit) {
                const u64 row = BbscParamRow(table, item.id, GoodsRowSize);
                const u64 source = BbscParamRow(table, item.copy_of ? item.copy_of : 200, GoodsRowSize);
                std::array<u8, GoodsRowSize> copy{};
                std::memcpy(copy.data(), reinterpret_cast<const void*>(source), copy.size());
                std::memcpy(reinterpret_cast<void*>(row), copy.data(), copy.size());
                WriteValue<u16>(row, 44, item.icon);
                WriteValue<s32>(row, 28, item.sort_id);
                WriteValue<u8>(row, 58, 0);
                WriteValue<s32>(row, 100, -1);
                WriteValue<s32>(row, 104, -1);
            }
            ++prepared;
        }
        if (prepared) {
            LOG_INFO(Debug, "SAVE DISPLAY ready: {} table(s); inventory/equipment untouched", prepared);
            save_display_ready.store(true, std::memory_order_release);
            return;
        }
        std::this_thread::sleep_for(std::chrono::seconds(5));
    }
    LOG_ERROR(Debug, "SAVE DISPLAY failed: no complete goods table");
}

bool SaveBridgeContext() {
    const char* token = std::getenv("BBSC_SAVE_JOB_TOKEN");
    const char* root = std::getenv("BBSC_SAVE_JOB_ROOT");
    const char* step = std::getenv("BBSC_SAVE_STEP");
    const char* saves = std::getenv("BBSC_SAVE_DIR");
    if (!token || !root || !step || !saves) return false;
    save_job_token = token;
    save_job_step = step;
    if (save_job_token.size() != 32 || !std::ranges::all_of(save_job_token, [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        }) || (save_job_step != "baseline" && save_job_step != "inspect" && save_job_step != "convert")) return false;
    save_job_root = std::filesystem::path(root).lexically_normal();
    const auto expected = save_job_root / save_job_step / "user/savedata/1/CUSA00207/SPRJ0005";
    if (!save_job_root.is_absolute() || save_job_root.filename() != save_job_token ||
        std::filesystem::path(saves).lexically_normal() != expected.lexically_normal()) return false;
    std::ifstream marker(save_job_root / "job.id");
    std::string marker_token;
    marker >> marker_token;
    if (marker_token != save_job_token) return false;
    if (save_job_step != "baseline") {
        std::ifstream plan(save_job_root / "slot.txt");
        if (!(plan >> save_expected_slot) || save_expected_slot < 0 || save_expected_slot > 9) return false;
    }
    return true;
}

bool SaveBridgeGoods(u64 carried, u64 storage, bool converted) {
    for (const u32 id : {200u, 205u, 225u, 111u}) {
        if (BbscInventoryCount(carried, 0x40000000u | id) != (converted ? 1 : 0) ||
            BbscInventoryCount(storage, 0x40000000u | id) != 0) return false;
    }
    for (const u32 id : {201u, 206u, 226u}) {
        if (BbscInventoryHas(carried, 0x40000000u | id) || BbscInventoryHas(storage, 0x40000000u | id)) return false;
    }
    for (const u32 id : {4016u, 4121u, 11201u, 4313u, 11251u}) {
        if (BbscInventoryCount(carried, 0x40000000u | id) != (converted ? 0 : 1) ||
            BbscInventoryCount(storage, 0x40000000u | id) != 0) return false;
    }
    return true;
}

std::optional<BbscSaveSlot> SaveBridgeSlot() {
    const std::filesystem::path folder(std::getenv("BBSC_SAVE_DIR"));
    auto latest = bbsc_run_started;
    std::optional<BbscSaveSlot> selected;
    for (int slot = 0; slot < 10; ++slot) {
        std::error_code error;
        const auto written = std::filesystem::last_write_time(folder / fmt::format("userdata{:04}", slot), error);
        if (!error && written > latest) {
            latest = written;
            selected = BbscSaveSlot{"save", slot};
        }
    }
    return selected; // never inspect unrelated emulator save folders
}

void SaveBridgeConvert(u64 carried, u64 storage, const BbscSaveSlot& slot) {
    if (save_job_step != "convert") return;
    static bool attempted = false;
    static s64 ready_since = 0;
    if (attempted || slot.slot != save_expected_slot) return;
    if (!ready_since) ready_since = SteadyNowMs();
    if (SteadyNowMs() - ready_since < 10'000) return;
    attempted = true;
    const auto result = save_job_root / "mod/conversion-outcome.json";
    if (std::filesystem::exists(result)) return;
    const auto report = [&result](const char* status) {
        std::ofstream file(result);
        file << "{\"schema\":1,\"job\":\"" << save_job_token << "\",\"status\":\"" << status << "\"}\n";
        LOG_INFO(Debug, "SAVE BRIDGE: conversion {}", status);
    };
    const char* cleared = std::getenv("BBSC_CONVERSION_QUICK_SLOTS_CLEARED");
    if (!cleared || std::string_view(cleared) != "1" || GetSessionState() != 0 ||
        ReadValue<u8>(GetGlobalState(), 0x1592) == 0 || !BbscEventFlag(6622) ||
        !SaveBridgeGoods(carried, storage, false)) {
        report("rejected");
        return;
    }
    using Fits = s32 PS4_SYSV_ABI (*)(u64, const u32*, s32);
    using Add = s32 PS4_SYSV_ABI (*)(u64, const u32*, s32);
    for (const u32 id : {200u, 205u, 225u, 111u}) {
        alignas(16) u32 code = 0x40000000u | id;
        if (reinterpret_cast<Fits>(image_base + InventoryFitsOffset)(carried, &code, 1) < 1) {
            report("rejected");
            return;
        }
    }
    report("started");
    for (const u32 id : {4016u, 4121u, 11201u, 4313u, 11251u}) {
        if (!BbscInventoryRemove(carried, 0x40000000u | id)) { report("failed"); return; }
    }
    for (const u32 id : {200u, 205u, 225u, 111u}) {
        alignas(16) u32 key = 0x80000000u | (3u << 28) | id;
        reinterpret_cast<Add>(image_base + InventoryAddOffset)(carried, &key, 1);
    }
    report(SaveBridgeGoods(carried, storage, true) && BbscEventFlag(6622) ? "memory-ready" : "failed");
}

void PS4_SYSV_ABI SaveBridgeTick(u64, const GuestRegisterSnapshot*) {
    static s64 last = 0;
    if (SteadyNowMs() - last < 5'000) return;
    last = SteadyNowMs();
    if (save_job_step == "inspect" && !save_display_ready.load(std::memory_order_acquire)) return;
    try {
        const u64 player = GetLocalPlayerCharacter();
        const u64 global = GetGlobalState();
        if (!ReadablePointer(player, 0x410) || GetCharacterHp(player) <= 0 ||
            !ReadablePointer(global, 0x1598) || ReadValue<u8>(global, 0x08) != 0 ||
            ReadValue<u64>(image_base + EventFlagManPointerOffset, 0) == 0) return;
        const u64 carried = BbscCarriedInventory();
        const u64 storage = BbscStorageInventory();
        const auto slot = SaveBridgeSlot();
        if (!carried || !storage || !slot) return;
        SaveBridgeConvert(carried, storage, *slot);
        std::ofstream file(save_job_root / "mod" /
            (save_job_step + "-" + std::to_string(last) + ".json"));
        file << "{\"schema\":1,\"job\":\"" << save_job_token << "\",\"step\":\"" << save_job_step
             << "\",\"slot\":" << slot->slot << ",\"own_world\":"
             << (ReadValue<u8>(global, 0x1592) != 0 ? "true" : "false")
             << ",\"session_state\":" << GetSessionState()
             << ",\"flag_6622\":" << (BbscEventFlag(6622) ? "true" : "false")
             << ",\"normal_bbsc_initialized\":false,\"kit_display_ready\":"
             << (save_display_ready.load(std::memory_order_acquire) ? "true" : "false")
             << ",\"hp\":" << GetCharacterHp(player)
             << ",\"map\":" << GetCurrentPackedMap() << ",\"goods\":[";
        bool first = true;
        for (const u32 id : {200u, 201u, 205u, 206u, 225u, 226u, 111u, 4016u, 4121u, 11201u, 4313u, 11251u}) {
            if (!first) file << ',';
            first = false;
            file << "{\"id\":" << id << ",\"carried\":" << BbscInventoryCount(carried, 0x40000000u | id)
                 << ",\"storage\":" << BbscInventoryCount(storage, 0x40000000u | id) << '}';
        }
        file << "]}\n";
    } catch (const std::exception& e) { LOG_ERROR(Debug, "SAVE BRIDGE: snapshot failed ({})", e.what()); }
}
} // namespace

void InstallSaveBridgeOnly() {
    if (MemoryPatcher::g_game_serial != "CUSA03173" || !SaveBridgeContext()) {
        LOG_ERROR(Debug, "SAVE BRIDGE: invalid isolated job; no hooks installed");
        return;
    }
    image_base = MemoryPatcher::g_eboot_address;
    const auto site = std::ranges::find_if(Sites, [](const TraceSite& s) { return s.offset == BbscGameTickOffset; });
    if (!image_base || site == Sites.end() || !InstallGuestCodeHook(
            reinterpret_cast<void*>(image_base + site->offset),
            std::span<const u8>{site->prologue.data(), site->prologue_size}, 0, SaveBridgeTick)) {
        LOG_ERROR(Debug, "SAVE BRIDGE: unexpected game tick signature; disabled");
        return;
    }
    if (save_job_step == "inspect") {
        std::thread{[] {
            try { SaveBridgePrepareDisplay(); }
            catch (const std::exception& e) { LOG_ERROR(Debug, "SAVE DISPLAY failed ({})", e.what()); }
        }}.detach();
    }
    LOG_INFO(Debug, "SAVE BRIDGE ONLY: one isolated observer; normal BBSC initialization and networking OFF");
}
