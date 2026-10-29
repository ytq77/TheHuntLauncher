#include "../patches/bbsc_personal_rewards_policy.h"
#include <cassert>
#include <iostream>

using BbscPersonalRewards::DropLedger;
int main() {
    DropLedger state;
    // Native RNG ran, including the no-item outcome: never roll again.
    state.NativeRoll(10);
    assert(!state.Queue(10));
    assert(!state.ClaimsCorpse(10));
    // Missing native path: one fallback, with its duplicate corpse refused.
    assert(state.Queue(20));
    assert(state.ClaimsCorpse(20));
    assert(!state.Queue(20));
    state.Supplemented(20);
    assert(!state.Pending(20));
    assert(state.ClaimsCorpse(20));
    state.NativeRoll(20); // late remote corpse cannot undo a committed award
    assert(state.ClaimsCorpse(20));
    // Native path becomes available before fallback commits: cancel fallback.
    assert(state.Queue(30));
    state.NativeRoll(30);
    assert(!state.Pending(30));
    assert(!state.ClaimsCorpse(30));
    state.Supplemented(30);
    assert(!state.ClaimsCorpse(30));
    // Different enemies may legitimately use the same lot, even simultaneously.
    assert(state.Queue(40));
    assert(state.Queue(41));
    // A new load may reuse the same runtime handles.
    state.Clear();
    assert(state.Queue(20));
    assert(!state.Queue(0));
    assert(!state.Queue(UINT32_MAX));
    state.Clear();
    for (std::uint32_t n=1;n<=DropLedger::Limit;++n) assert(state.Queue(n));
    assert(!state.Queue(DropLedger::Limit+1));
    state.NativeRoll(1); // cancellation must still work at capacity
    assert(!state.Pending(1));
    // NPC historical quest/key lots cannot enter generic enemy compensation.
    assert(BbscPersonalRewards::IsQuestLot(33010));
    assert(BbscPersonalRewards::IsQuestLot(34030));
    assert(!BbscPersonalRewards::IsQuestLot(10302200));
    assert(!BbscPersonalRewards::IsQuestLot(26102200));
    assert(!BbscPersonalRewards::IsQuestLot(-1));
    for (std::size_t n=1;n<BbscPersonalRewards::QuestLots.size();++n)
        assert(BbscPersonalRewards::QuestLots[n-1]<BbscPersonalRewards::QuestLots[n]);
    std::cout << "Personal drop ordering, empty native roll, repetition, actor isolation, load reset, bounds and NPC exclusion passed.\n";
}
