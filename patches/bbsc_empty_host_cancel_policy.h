#pragma once
#include <cstdint>

namespace BbscEmptyHostCancel {
enum class Decision { Discard, Wait, KeepRoom, EndRoom };

struct Snapshot {
    std::int64_t requested_ms;
    std::int64_t now_ms;
    int session_state;
    int members;
    int remote_players;
    bool same_manager;
    bool memory_ready;
    bool own_world;
    bool alive;
    bool loading;
    bool searching;
};

inline Decision Evaluate(const Snapshot& s) {
    if (s.requested_ms <= 0 || s.now_ms < s.requested_ms ||
        s.now_ms - s.requested_ms > 10'000 || s.session_state != 3 ||
        !s.same_manager || !s.memory_ready || !s.own_world || !s.alive || s.loading ||
        s.members < 1 || s.remote_players < 0) {
        return Decision::Discard;
    }
    // Native session membership protects a guest who is loading and has no character yet.
    if (s.members > 1 || s.remote_players > 0) {
        return Decision::KeepRoom;
    }
    // Let the original confirmed stop-search action finish before ending its empty room.
    if (s.searching || s.now_ms - s.requested_ms < 250) {
        return Decision::Wait;
    }
    return Decision::EndRoom;
}
} // namespace BbscEmptyHostCancel
