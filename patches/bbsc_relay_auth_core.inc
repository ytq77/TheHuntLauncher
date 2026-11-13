// This callback touches only synchronized packet caches; no game memory writes.
// It is called before adopting a new authenticated room/owner and on disconnect.
void OnBbscRelayAuthContextChanged(std::uint32_t owner) {
    ClearBbscPeerDisplay();
    TheHuntRelayAuth::EffectGuard auth_guard{true};
    ClearBbscHostWorldSnapshots();
    { std::scoped_lock lock{bbsc_follow_mutex}; bbsc_follow_latest = {}; }
    { std::scoped_lock lock{bbsc_progress_mutex}; bbsc_progress_host_flags.clear(); }
    { std::scoped_lock lock{bbsc_scale_host_mutex}; bbsc_scale_host_ms = 0; }
    { std::scoped_lock lock{bbsc_life_mutex}; bbsc_peer_life.clear(); }
    { std::scoped_lock lock{bbsc_relay_mutex}; bbsc_relay_hosts.clear(); }
    bbsc_host_boss_id = -1; bbsc_host_boss_ms = 0;
    ClearBbscPvpHostState();
    bbsc_pvp_on = false; // Network thread clears authority; native team writes remain on the game tick.
    // A room change with the same owner must still invalidate cached generation metadata.
    bbsc_relay_selection_generation.fetch_add(1);
    SelectBbscRelayHost(owner);
}
