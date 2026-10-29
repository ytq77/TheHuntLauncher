// Evidence for a same-run return home. This never writes inventory, flags or saves.
// The game thread arms it only after every persistent recovery target is present.
// The save thread uses private immutable context and kernel memory copies only.
std::mutex bbsc_home_proof_mutex;
std::filesystem::path bbsc_home_proof_folder;
u64 bbsc_home_proof_recovery = 0;
int bbsc_home_proof_slot = -1;
u32 bbsc_home_proof_sequence = 0;
std::string bbsc_home_proof_previous;

bool BbscHomeProofAppend(const char* stage, const std::string& hash) {
    if (bbsc_home_proof_folder.empty() || hash.empty() || bbsc_home_proof_sequence >= 64)
        return false;
    const auto epoch = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::ofstream stream(bbsc_home_proof_folder/"native.jsonl", std::ios::app);
    if (!stream) return false;
    stream << "{\"sequence\":" << bbsc_home_proof_sequence+1
           << ",\"pid\":" << GetCurrentProcessId() << ",\"stage\":\"" << stage
           << "\",\"recovery_sequence\":" << bbsc_home_proof_recovery
           << ",\"slot\":" << bbsc_home_proof_slot
           << ",\"epoch_ms\":" << epoch << ",\"monotonic_ms\":" << SteadyNowMs()
           << ",\"sha256\":\"" << hash << "\"}\n";
    stream.flush();
    if (!stream) return false;
    ++bbsc_home_proof_sequence;
    return true;
}

void BbscHomeProofRestored() {
    try {
        if (!bbsc_save_proof_hook || !BbscWorldRecoveryEnabled() ||
            bbsc_recovery_mode != BbscRecoveryApplied || !BbscHomeForOwnWorld()) return;
        std::scoped_lock lock{bbsc_home_proof_mutex};
        if (!bbsc_home_proof_folder.empty()) {
            if (bbsc_home_proof_recovery == bbsc_recovery_sequence) return;
            std::ifstream ack(BbscRecoveryFolder()/"acks"/
                fmt::format("{}.ack", bbsc_home_proof_slot), std::ios::binary);
            u64 completed = 0;
            if (!ack.read(reinterpret_cast<char*>(&completed), 8) || ack.peek() != EOF ||
                completed != bbsc_home_proof_recovery) return;
            // A later session can arm only after the parent verified the old one.
            bbsc_home_proof_folder.clear();
            bbsc_home_proof_sequence = 0;
            bbsc_home_proof_previous.clear();
        }
        std::vector<u8> current;
        if (!BbscSaveProofBank(current) || bbsc_recovery_rows.empty()) return;
        bool persistent = false;
        for (const auto& row : bbsc_recovery_rows) {
            if (row.index == 0xFFFFFFFF) continue;
            if (row.index >= 1200 || std::memcmp(current.data()+row.index*125,
                                                row.after.data(), 125) != 0) {
                LOG_WARNING(Debug, "BBSC home save proof: persistent group {} differs; no receipt armed", row.key);
                return;
            }
            persistent = true;
        }
        if (!persistent || bbsc_recovery_slot < 0 || !bbsc_recovery_sequence) return;
        const char* token = std::getenv("THEHUNT_WORLD_RECOVERY_TOKEN");
        if (!token || std::strlen(token) != 32 || !std::all_of(token, token+32,
                [](char c) {return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');})) return;
        const auto folder = BbscRecoveryFolder()/"home-save-proof"/
            fmt::format("{}-{}-{}", token, bbsc_recovery_slot, bbsc_recovery_sequence);
        std::error_code ec;
        if (std::filesystem::exists(folder, ec) || ec) return;
        const auto hash = BbscSaveProofHash(current);
        if (hash.empty() || !BbscRecoveryWriteFile(folder/"restored-bank.bin", current)) return;
        bbsc_home_proof_folder = folder;
        bbsc_home_proof_recovery = bbsc_recovery_sequence;
        bbsc_home_proof_slot = bbsc_recovery_slot;
        if (!BbscHomeProofAppend("restored", hash)) {
            bbsc_home_proof_folder.clear();
            return;
        }
        LOG_INFO(Debug, "BBSC home save proof: complete recovered bank verified; awaiting both native disk generations");
    } catch (...) { /* Missing evidence must leave recovery pending. */ }
}

void BbscHomeProofSaveEntry() {
    try {
        std::scoped_lock lock{bbsc_home_proof_mutex};
        if (bbsc_home_proof_folder.empty() || !bbsc_home_proof_sequence ||
            bbsc_home_proof_sequence >= 64) return;
        std::vector<u8> current;
        if (!BbscSaveProofBank(current)) return;
        const auto hash = BbscSaveProofHash(current);
        if (hash != bbsc_home_proof_previous && BbscHomeProofAppend("save-entry", hash))
            bbsc_home_proof_previous = hash;
    } catch (...) { /* No exception may escape a save-thread hook. */ }
}
