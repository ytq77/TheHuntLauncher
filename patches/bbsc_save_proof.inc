// One-use run-bound proof, enabled only for an eligible primary-ready job.
// No flag writes. A save-entry digest must still be matched to both disk files
// by the parent; entry alone never retires a journal.
std::mutex bbsc_save_proof_mutex;
bool bbsc_save_proof_loaded = false, bbsc_save_proof_hook = false;
u32 bbsc_save_proof_sequence = 0;
std::string bbsc_save_proof_previous;

std::filesystem::path BbscSaveProofFolder() {
    const char* value = std::getenv("THEHUNT_SAVE_PROOF_DIR");
    return value && value[0] ? std::filesystem::path(value) : std::filesystem::path{};
}

bool BbscSaveProofRead(u64 address, void* result, size_t size) {
    size_t copied = 0;
    return address >= 0x10000 && ReadProcessMemory(GetCurrentProcess(),
        reinterpret_cast<const void*>(address), result, size, &copied) && copied == size;
}

bool BbscSaveProofBank(std::vector<u8>& bytes) {
    u64 manager=0, base=0, check=0; u32 count=0;
    if (!BbscSaveProofRead(image_base+WorldFlagManPointerOffset, &manager, 8) ||
        !BbscSaveProofRead(manager+0x24, &count, 4) || count != 1200 ||
        !BbscSaveProofRead(manager+0x28, &base, 8)) return false;
    bytes.resize(150000);
    std::vector<u8> second(150000);
    return BbscSaveProofRead(base, bytes.data(), bytes.size()) &&
        BbscSaveProofRead(base, second.data(), second.size()) && bytes == second &&
        BbscSaveProofRead(manager+0x28, &check, 8) && check == base;
}

std::string BbscSaveProofHash(std::span<const u8> bytes) {
    void* algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, L"SHA256", nullptr, 0) != 0) return {};
    std::array<u8,32> hash{};
    const auto status = BCryptHash(algorithm, nullptr, 0,
        const_cast<u8*>(bytes.data()), static_cast<unsigned long>(bytes.size()),
        hash.data(), static_cast<unsigned long>(hash.size()));
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (status != 0) return {};
    std::string output;
    for (u8 byte : hash) { output += "0123456789abcdef"[byte>>4]; output += "0123456789abcdef"[byte&15]; }
    return output;
}

bool BbscSaveProofAppend(const char* stage, const std::string& hash) {
    if (bbsc_save_proof_sequence >= 64 || hash.empty()) return false;
    const auto folder = BbscSaveProofFolder();
    if (folder.empty()) return false;
    const auto epoch = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::ofstream stream(folder/"native.jsonl", std::ios::app);
    if (!stream) return false;
    stream << "{\"sequence\":" << bbsc_save_proof_sequence+1
           << ",\"pid\":" << GetCurrentProcessId() << ",\"stage\":\"" << stage
           << "\",\"epoch_ms\":" << epoch << ",\"monotonic_ms\":" << SteadyNowMs()
           << ",\"sha256\":\"" << hash << "\"}\n";
    stream.flush();
    if (!stream) return false;
    ++bbsc_save_proof_sequence;
    return true;
}

void BbscSaveProofAtLoad() {
    if (!bbsc_save_proof_hook || BbscSaveProofFolder().empty()) return;
    try {
        std::scoped_lock lock{bbsc_save_proof_mutex};
        if (bbsc_save_proof_loaded || !BbscHomeForOwnWorld()) return;
        std::ifstream stream(BbscSaveProofFolder()/"expected-bank.bin", std::ios::binary);
        std::vector<u8> expected(150000), current;
        if (!stream.read(reinterpret_cast<char*>(expected.data()), expected.size()) ||
            stream.peek() != EOF || !BbscSaveProofBank(current) || expected != current) return;
        const auto hash = BbscSaveProofHash(current);
        if (BbscSaveProofAppend("loaded", hash)) {
            bbsc_save_proof_loaded = true;
            LOG_INFO(Debug, "BBSC save proof: full recovered bank loaded; awaiting native disk generations");
        }
    } catch (...) { /* No exception may escape a game hook. */ }
}

void PS4_SYSV_ABI BbscSaveProofEntry(u64, const GuestRegisterSnapshot*) {
    // Save-thread side: only kernel memory copies, private immutable context,
    // and SHA256. Never inspect mutable game-thread BBSC maps or call the game.
    try {
        std::scoped_lock lock{bbsc_save_proof_mutex};
        if (!bbsc_save_proof_loaded || bbsc_save_proof_sequence >= 64) return;
        std::vector<u8> current;
        if (!BbscSaveProofBank(current)) return;
        const auto hash = BbscSaveProofHash(current);
        if (hash != bbsc_save_proof_previous && BbscSaveProofAppend("save-entry", hash))
            bbsc_save_proof_previous = hash;
    } catch (...) { /* Missing evidence keeps the launcher fail-closed. */ }
}

void InstallBbscSaveProofHook() {
    if (BbscSaveProofFolder().empty()) return;
    static constexpr std::array<u8,16> prologue{
        0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x50,0x49,0x89,0xf6,0x49,0x89,0xff};
    bbsc_save_proof_hook = InstallGuestCodeHook(reinterpret_cast<void*>(image_base+0x01399DE0),
        std::span<const u8>{prologue}, 0, BbscSaveProofEntry);
    LOG_INFO(Debug, "BBSC save proof: native entry {}", bbsc_save_proof_hook ? "ready" : "unavailable");
}
