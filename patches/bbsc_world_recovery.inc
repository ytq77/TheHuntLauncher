// The Hunt protocol: export complete, run-bound recovery plans. The launcher
// verifies actual save bytes; only it archives journals after verification.
constexpr u32 BbscRecoveryCapture = 1, BbscRecoveryRestore = 2,
              BbscRecoveryApplied = 3, BbscRecoveryQuit = 4, BbscRecoveryBlocked = 5;
struct BbscRecoveryRow {
    u32 key = 0, index = 0xFFFFFFFF;
    std::array<u8, BbscWorldGroupBytes> before{}, after{};
};
std::vector<BbscRecoveryRow> bbsc_recovery_rows;
std::vector<u8> bbsc_recovery_packet;
std::map<u32, u32> bbsc_recovery_initial_indices;
u64 bbsc_recovery_sequence = 0;
u32 bbsc_recovery_mode = 0;
int bbsc_recovery_slot = -1;
bool bbsc_recovery_waiting = false, bbsc_recovery_at_load = false,
     bbsc_recovery_title_exit = false;

bool BbscWorldRecoveryEnabled() {
    return EnvFlagEnabled("THEHUNT_WORLD_RECOVERY_V2");
}

bool BbscWorldRecoveryJournalFrozen() {
    return BbscWorldRecoveryEnabled() && bbsc_recovery_title_exit;
}

bool BbscRecoveryHasPendingSave() {
    static u64 checked = 0;
    static bool pending = false;
    const u64 now = SteadyNowMs();
    if (now-checked < 500) return pending;
    checked = now; pending = false;
    const auto folder = Common::FS::GetUserPath(Common::FS::PathType::CapturesDir).parent_path()/"own-world";
    for (int slot=0; slot<10; ++slot) {
        std::error_code ec;
        if (std::filesystem::exists(folder/fmt::format("own-save-{}.bin.awaiting-save", slot), ec) || ec) {
            pending = true; break;
        }
    }
    return pending;
}

std::filesystem::path BbscRecoveryFolder() {
    const char* folder = std::getenv("THEHUNT_WORLD_RECOVERY_DIR");
    return folder && folder[0] ? std::filesystem::path(folder) : std::filesystem::path{};
}

bool BbscRecoveryWriteFile(const std::filesystem::path& path, std::span<const u8> bytes) {
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    if (ec) return false;
    const auto temp = std::filesystem::path(path).concat(".tmp");
    { std::ofstream out(temp, std::ios::binary | std::ios::trunc);
      out.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
      out.flush(); if (!out) return false; }
    for (int attempt=0; attempt<10; ++attempt) {
        std::filesystem::rename(temp, path, ec);
        if (!ec) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    return !ec;
}

bool BbscRecoveryTokenFile(const char* category, const char* suffix) {
    if (bbsc_recovery_slot < 0 || !bbsc_recovery_sequence) return false;
    std::ifstream input(BbscRecoveryFolder()/category/
                          fmt::format("{}.{}", bbsc_recovery_slot, suffix), std::ios::binary);
    u64 value = 0;
    return input.read(reinterpret_cast<char*>(&value), 8) && input.peek() == EOF &&
           value == bbsc_recovery_sequence;
}

void BbscRecoveryPollAck() {
    if (bbsc_recovery_waiting && BbscRecoveryTokenFile("acks", "ack")) {
        bbsc_recovery_waiting = false;
        bbsc_recovery_rows.clear();
        bbsc_recovery_packet.clear();
        bbsc_recovery_initial_indices.clear();
        bbsc_recovery_mode = 0;
        LOG_INFO(Debug, "BBSC own world: launcher verified both save generations; recovery complete");
    }
}

bool BbscWorldRecoveryWaiting() {
    if (!BbscWorldRecoveryEnabled()) return false;
    BbscRecoveryPollAck();
    return bbsc_recovery_waiting;
}

bool BbscRecoverySetMode(u32 mode) {
    if (bbsc_recovery_packet.size() < 68) return false;
    std::memcpy(bbsc_recovery_packet.data()+52, &mode, 4);
    const bool ok = BbscRecoveryWriteFile(BbscRecoveryFolder()/"requests"/
                         fmt::format("world-save-{}.bin", bbsc_recovery_slot), bbsc_recovery_packet);
    if (ok) bbsc_recovery_mode = mode;
    return ok;
}

bool BbscRecordWorldRecovery(u32 mode) {
    if (!BbscWorldRecoveryEnabled() || bbsc_own_world.empty() ||
        !bbsc_own_world_slot || bbsc_own_world_slot->user != "save") return false;
    const char* token = std::getenv("THEHUNT_WORLD_RECOVERY_TOKEN");
    if (!token || std::strlen(token) != 32 || BbscRecoveryFolder().empty() ||
        !std::all_of(token, token+32, [](char c) {return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');}))
        return false;
    const u64 man = ReadValue<u64>(image_base+WorldFlagManPointerOffset, 0);
    if (!ReadablePointer(man, 0x40) || ReadValue<u32>(man, 0x24) != 1200) return false;
    const u64 base = ReadValue<u64>(man, 0x28);
    if (!ReadablePointer(base, 150000)) return false;
    const bool initial = bbsc_recovery_initial_indices.empty();
    if (initial && mode != BbscRecoveryCapture) return false;
    std::vector<BbscRecoveryRow> rows;
    for (const auto& [key, own] : bbsc_own_world) {
        const u64 storage = BbscFlagGroupStorage(key);
        if (!storage) return false; // Never publish a partial plan.
        BbscRecoveryRow row; row.key = key;
        if (storage >= base && storage-base < 150000 && (storage-base)%125 == 0)
            row.index = static_cast<u32>((storage-base)/125);
        else row.index = 0xFFFFFFFF; // A separate, memory-only group.
        if (!initial) {
            const auto saved = bbsc_recovery_initial_indices.find(key);
            if (saved == bbsc_recovery_initial_indices.end() ||
                (row.index != 0xFFFFFFFF && row.index != saved->second)) return false;
            // Vanilla co-op redirects many flag groups into temporary buffers.
            // Their save-bank positions remain those captured at home.
            row.index = saved->second;
        }
        std::memcpy(row.before.data(), reinterpret_cast<const void*>(storage), 125);
        row.after = own;
        if (key/10000 == 5) for (size_t b=0; b<125; ++b) row.after[b] |= row.before[b];
        rows.push_back(row);
    }
    if (initial && std::any_of(rows.begin(), rows.end(),
                              [](const auto& row) {return row.before != row.after;})) return false;
    std::ifstream journal(BbscOwnWorldPath(*bbsc_own_world_slot), std::ios::binary);
    std::vector<u8> journal_bytes((std::istreambuf_iterator<char>(journal)), {});
    if (journal_bytes.size() != 12+bbsc_own_world.size()*129) return false;
    u64 hash = 14695981039346656037ULL;
    for (u8 byte : journal_bytes) hash = (hash^byte)*1099511628211ULL;
    static u64 counter = 0;
    const u64 sequence = static_cast<u64>(SteadyNowMs())*1000+(++counter%1000);
    std::vector<u8> packet;
    const auto put = [&packet](auto value) {
        const auto size = packet.size(); packet.resize(size+sizeof(value));
        std::memcpy(packet.data()+size, &value, sizeof(value));
    };
    packet.insert(packet.end(), {'T','H','W','R'}); put(u32{2}); put(sequence);
    packet.insert(packet.end(), token, token+32);
    put(static_cast<u32>(bbsc_own_world_slot->slot)); put(mode);
    put(static_cast<u32>(rows.size())); put(hash);
    for (const auto& row : rows) {
        put(row.key); put(row.index);
        packet.insert(packet.end(), row.before.begin(), row.before.end());
        packet.insert(packet.end(), row.after.begin(), row.after.end());
    }
    const int slot = bbsc_own_world_slot->slot;
    if (initial) {
        if (!BbscRecoveryWriteFile(BbscRecoveryFolder()/"baselines"/
                                  fmt::format("world-save-{}.bbow", slot), journal_bytes) ||
            !BbscRecoveryWriteFile(BbscRecoveryFolder()/"baselines"/
                                  fmt::format("world-save-{}.bin", slot), packet)) return false;
    }
    if (!BbscRecoveryWriteFile(BbscRecoveryFolder()/"requests"/
                              fmt::format("world-save-{}.bin", slot), packet)) return false;
    if (initial) for (const auto& row : rows) bbsc_recovery_initial_indices[row.key] = row.index;
    bbsc_recovery_sequence = sequence; bbsc_recovery_slot = slot;
    bbsc_recovery_rows = std::move(rows); bbsc_recovery_packet = std::move(packet);
    bbsc_recovery_mode = mode;
    LOG_INFO(Debug, "BBSC own world: complete recovery plan mode {} slot {} groups {} sequence {}",
             mode, slot, bbsc_recovery_rows.size(), sequence);
    return true;
}

bool BbscRestoreWorldTransaction(std::string_view why) {
    if (BbscWorldRecoveryWaiting() || bbsc_recovery_title_exit || !bbsc_recovery_at_load ||
        bbsc_own_world.empty() || !BbscHomeForOwnWorld()) return false;
    // Resolve every group before even the Dream restore can mutate live flags.
    for (const auto& [key, own] : bbsc_own_world) if (!BbscFlagGroupStorage(key)) {
        LOG_WARNING(Debug, "BBSC own world: group {} missing; journal retained for a later load", key);
        return false;
    }
    if (bbsc_dream_own.held) RestoreBbscOwnDreamState(why);
    WriteBbscOwnWorldFile();
    if (!BbscRecordWorldRecovery(BbscRecoveryRestore)) return false;
    // The game thread remains at the pre-event load hook while the launcher
    // creates and verifies its before-images. No interactive gameplay here.
    for (int attempt=0; attempt<50 && !BbscRecoveryTokenFile("ready", "ready"); ++attempt)
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    if (!BbscRecoveryTokenFile("ready", "ready")) {
        LOG_WARNING(Debug, "BBSC own world: launcher not ready; no map flag writes; journal retained");
        return false;
    }
    for (const auto& row : bbsc_recovery_rows) {
        const auto storage = BbscFlagGroupStorage(row.key);
        if (!storage || (row.before != row.after &&
            std::memcmp(reinterpret_cast<const void*>(storage), row.before.data(), 125) != 0)) {
            BbscRecoverySetMode(BbscRecoveryBlocked);
            LOG_WARNING(Debug, "BBSC own world: live flags changed before restore; no map flag writes");
            return false;
        }
    }
    // Persist APPLIED first. If interruption occurs before the writes, the
    // launcher's exact before-image checks make retry safe or stop explicitly.
    if (!BbscRecoverySetMode(BbscRecoveryApplied)) return false;
    for (const auto& row : bbsc_recovery_rows) if (row.before != row.after)
        std::memcpy(reinterpret_cast<void*>(BbscFlagGroupStorage(row.key)), row.after.data(), 125);
    bbsc_own_world.clear(); bbsc_own_world_dirty = false; bbsc_own_world_slot.reset();
    bbsc_recovery_waiting = true;
    LOG_INFO(Debug, "BBSC own world ({}): memory restored once; journal awaits actual save verification", why);
    return true;
}

void BbscWorldRecoveryQuit(bool confirmed) {
    if (!BbscWorldRecoveryEnabled() || bbsc_own_world.empty()) return;
    if (bbsc_recovery_title_exit) return; // Keep the accepted quit's exact generation.
    const auto global = GetGlobalState();
    const bool guest = GetSessionState() == 6 && GetLocalPlayerType() == 1 &&
                       ReadablePointer(global, 0x1593) && ReadValue<u8>(global, 0x1592) == 0;
    if (confirmed && guest && bbsc_dream_own.held)
        RestoreBbscOwnDreamState("confirmed quit: retain personal Dream changes");
    WriteBbscOwnWorldFile();
    if (confirmed && guest && BbscRecordWorldRecovery(BbscRecoveryQuit))
        LOG_INFO(Debug, "BBSC own world: confirmed guest quit recorded; launcher finalizes after process exit");
    else if (bbsc_recovery_mode != BbscRecoveryQuit)
        BbscRecordWorldRecovery(BbscRecoveryBlocked);
    bbsc_recovery_title_exit = true;
}

void BbscWorldRecoveryLoadedAfterTitle() {
    if (BbscWorldRecoveryEnabled() && bbsc_recovery_title_exit && !bbsc_own_world.empty()) {
        BbscRecoverySetMode(BbscRecoveryBlocked);
        LOG_WARNING(Debug, "BBSC own world: title was reloaded without closing; old state not replayed");
    }
}
