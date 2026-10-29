#pragma once
#include <array>
#include <cstdint>
#include <map>

namespace BbscPersonalRewards {
// NpcParam itemLotId_1/_2 whose ItemLotParam getItemFlagId is a global
// one-time NPC/quest flag (50000000..50009999), pinned CUSA03173 1.09.
// These belong to personal pickup/quest handling, never generic enemy fallback.
constexpr std::array<std::int32_t, 47> QuestLots{
    20000, 22010, 23010, 24070, 24075, 25000, 26040, 27070, 28010, 28040,
    29010, 30020, 32030, 33010, 34020, 34030, 34035, 34040, 35020, 39020,
    43030, 43040, 43300, 43400, 43500, 43700, 43710, 43720, 43730, 43740,
    43850, 43860, 43870, 47000, 48000, 49000, 50000, 51000, 52000, 53000,
    55000, 56000, 57000, 58000, 59000, 77000, 77010,
};
constexpr bool IsQuestLot(std::int32_t lot) {
    for (auto candidate : QuestLots) if (candidate == lot) return true;
    return false;
}

// One ledger per loaded map and session. Actor is the runtime ChrIns handle,
// not an NPC param or lot id: different enemies can share the same drop table.
class DropLedger {
public:
    enum class Path { Native, Pending, Supplemented };
    static constexpr std::size_t Limit = 8192;

    void Clear() { entries.clear(); }
    void NativeRoll(std::uint32_t actor) {
        if (actor == 0 || actor == UINT32_MAX) return;
        auto it = entries.find(actor);
        if (it != entries.end()) {
            if (it->second != Path::Supplemented) it->second = Path::Native;
        } else if (entries.size() < Limit) entries.emplace(actor, Path::Native);
    }
    bool Queue(std::uint32_t actor) {
        if (actor == 0 || actor == UINT32_MAX || entries.size() >= Limit) return false;
        return entries.emplace(actor, Path::Pending).second;
    }
    bool Pending(std::uint32_t actor) const {
        auto it = entries.find(actor);
        return it != entries.end() && it->second == Path::Pending;
    }
    void Supplemented(std::uint32_t actor) {
        auto it = entries.find(actor);
        if (it != entries.end() && it->second == Path::Pending)
            it->second = Path::Supplemented;
    }
    bool ClaimsCorpse(std::uint32_t actor) const {
        auto it = entries.find(actor);
        return it != entries.end() && it->second != Path::Native;
    }
private:
    std::map<std::uint32_t, Path> entries;
};
} // namespace BbscPersonalRewards
