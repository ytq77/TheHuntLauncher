#pragma once
#include <mutex>
namespace TheHuntRelayAuth {
// Cache invalidation and native consumers linearize before a new signed context
// is published. Recursive because PvP tick calls its existing team-write helper.
inline std::recursive_mutex effect_mutex;
struct EffectGuard {
    explicit EffectGuard(bool enabled): lock(effect_mutex,std::defer_lock) { if(enabled)lock.lock(); }
    std::unique_lock<std::recursive_mutex> lock;
};
}
