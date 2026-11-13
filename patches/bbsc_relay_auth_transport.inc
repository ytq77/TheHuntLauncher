// Included by the real bbsc_relay.cpp after ReadAll, before legacy SessionKey/Dispatch.
void Dispatch(const std::vector<u8>& wrapped);
std::atomic<bool> private_auth{true}; // Missing/invalid configuration always fails closed.
std::atomic<u32> authenticated_owner{0};
std::atomic<u64> authenticated_room{0};
TheHuntRelayAuth::Client auth_client;
TheHuntRelayAuth::Bytes auth_token;
ULONGLONG auth_context_ms = 0; // send_mutex
ULONGLONG pending_placement_expires = 0; // dispatch_mutex (relay thread/local invalidation)
u64 pending_placement_generation = 0;
u64 first_spawn_epoch = 0; // dispatch_mutex; never reused across reconnects
bool first_spawn_available = false;
void ClearAuthPlacement(const char* why = "context") {
    if (pending_placement_generation)
        Log("placement cleared reason=" + std::string(why) +
            " generation=" + std::to_string(pending_placement_generation));
    pending_placement_expires = 0;
    pending_placement_generation = 0;
    first_spawn_available = false;
    BbscMod::RelayAuthClearPlacement();
}
void ExpireAuthPlacement() {
    if (pending_placement_expires && GetTickCount64() >= pending_placement_expires) ClearAuthPlacement("expired");
}
void ApplyAuthPlacement(const TheHuntRelayAuth::PendingPlacement& placement) {
    Log(std::string("placement ") + (placement.ignored ? "ignored" : placement.ttl_ms ? "received" : "revoked") +
        " generation=" + std::to_string(placement.generation) + " room=" +
        std::to_string(placement.host_room) + " ttl=" + std::to_string(placement.ttl_ms));
    if (placement.ignored) return;
    if (!placement.ttl_ms) {
        if (placement.generation == pending_placement_generation) ClearAuthPlacement("revoked");
        return;
    }
    if (pending_placement_generation != placement.generation) ClearAuthPlacement("superseded");
    pending_placement_generation = placement.generation;
    pending_placement_expires = GetTickCount64() + placement.ttl_ms;
    ++first_spawn_epoch;
    first_spawn_available = BbscMod::OnHostPlacement(placement.text, placement.host);
}

// Caller holds dispatch_mutex then send_mutex, the same order as RelayThread.
const TheHuntRelayAuth::PendingPlacement* FirstSpawnGrant(bool matched) {
    const auto now = GetTickCount64();
    if (!first_spawn_available || !first_spawn_epoch || !auth_context_ms ||
        now - auth_context_ms > 1500 || now >= pending_placement_expires) return nullptr;
    const auto* pending = auth_client.Pending();
    if (!pending || pending->generation != pending_placement_generation || pending->target != own_id)
        return nullptr;
    const auto context = auth_client.Current();
    if (!context.room) return matched ? nullptr : pending;
    return context.room == pending->host_room && context.owner == pending->host &&
           context.own == pending->target ? pending : nullptr;
}
void ApplyAuthContext(const TheHuntRelayAuth::Delivery& delivery) {
    const auto& context = delivery.context;
    if (pending_placement_generation && pending_placement_generation != delivery.placement.generation)
        ClearAuthPlacement();
    else if (pending_placement_generation && context.room && !authenticated_room.load())
        Log("matched summon placement retained for native reload");
    if (authenticated_room.load() != context.room || authenticated_owner.load() != context.owner) {
        authenticated_owner = 0;
        Core::Bloodborne::OnBbscRelayAuthContextChanged(context.owner);
        authenticated_room = context.room;
        authenticated_owner = context.owner;
    }
    peers = context.peers;
}

bool PublicAuthHost(std::string host) {
    std::transform(host.begin(), host.end(), host.begin(), [](unsigned char c) { return std::tolower(c); });
    if (!host.empty() && host.back() == '.') host.pop_back();
    for (const std::string domain : {"thehuntersdream.com", "nodreamforthehunter.com",
                                    "shadps4.net", "scej-network.jp", "supermedo.com"}) {
        if (host == domain || (host.size() > domain.size() &&
            host.ends_with("." + domain))) return true;
    }
    return false;
}

void InitAuth() {
    const char* mode = std::getenv("BBSC_RELAY_AUTH");
    std::string host; u16 port = 0;
    private_auth = !(mode && std::strcmp(mode, "public") == 0 &&
                     FindServer(host, port) && PublicAuthHost(host));
    if (private_auth) {
        const char* id = std::getenv("BBSC_RELAY_USER_ID");
        std::uint64_t parsed = 0;
        if (id && *id) {
            for (const char* p = id; *p; ++p) {
                if (*p < '0' || *p > '9' || parsed > (UINT32_MAX - (*p - '0')) / 10u) {
                    parsed = 0; break;
                }
                parsed = parsed * 10 + (*p - '0');
            }
        }
        own_id = static_cast<u32>(parsed);
        const DWORD size = GetEnvironmentVariableW(L"BBSC_RELAY_TOKEN", nullptr, 0);
        if (size > 1 && size <= 4097) {
            std::wstring wide(size, L'\0');
            if (GetEnvironmentVariableW(L"BBSC_RELAY_TOKEN", wide.data(), size) == size - 1) {
                const int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(),
                    static_cast<int>(size - 1), nullptr, 0, nullptr, nullptr);
                if (bytes > 0 && bytes <= 4096) {
                    auth_token.resize(bytes);
                    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide.data(),
                        static_cast<int>(size - 1), reinterpret_cast<char*>(auth_token.data()), bytes,
                        nullptr, nullptr);
                }
            }
            SecureZeroMemory(wide.data(), wide.size() * sizeof(wchar_t));
        }
    } else {
        if (BCryptGenRandom(nullptr, reinterpret_cast<PUCHAR>(&own_id), sizeof(own_id),
                            BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) own_id = 0;
        if (!own_id) own_id = 1; // Public legacy sender is not an authenticated identity.
    }
    _wputenv_s(L"BBSC_RELAY_TOKEN", L"");
    _wputenv_s(L"BBSC_RELAY_USER_ID", L"");
    SetEnvironmentVariableW(L"BBSC_RELAY_TOKEN", nullptr);
    SetEnvironmentVariableW(L"BBSC_RELAY_USER_ID", nullptr);
}

bool AuthRead(SOCKET s, void* data, int size, ULONGLONG deadline) {
    auto* at = static_cast<char*>(data);
    while (size > 0) {
        const auto now = GetTickCount64();
        if (now >= deadline) return false;
        const auto left = deadline - now;
        timeval timeout{static_cast<long>(left / 1000), static_cast<long>((left % 1000) * 1000)};
        fd_set ready; FD_ZERO(&ready); FD_SET(s, &ready);
        if (select(0, &ready, nullptr, nullptr, &timeout) <= 0) return false;
        const int got = recv(s, at, size, 0);
        if (got <= 0) return false;
        at += got; size -= got;
    }
    return true;
}
bool AuthFrame(SOCKET s, TheHuntRelayAuth::Bytes& frame, ULONGLONG deadline) {
    u32 size = 0;
    if (!AuthRead(s, &size, 4, deadline) || !size || size > TheHuntRelayAuth::MaxFrame) return false;
    frame.resize(size);
    return AuthRead(s, frame.data(), static_cast<int>(size), deadline);
}
bool AuthWrite(SOCKET s, const TheHuntRelayAuth::Bytes& payload) {
    TheHuntRelayAuth::Bytes frame;
    TheHuntRelayAuth::Put(frame, payload.size(), 4);
    frame.insert(frame.end(), payload.begin(), payload.end());
    const auto deadline = GetTickCount64() + 300;
    std::size_t at = 0;
    while (at < frame.size()) {
        if (GetTickCount64() >= deadline) return false;
        const int sent = send(s, reinterpret_cast<const char*>(frame.data() + at),
                              static_cast<int>(frame.size() - at), 0);
        if (sent <= 0) return false;
        at += sent;
    }
    return true;
}

// Caller holds send_mutex. RelayThread alone closes its socket (no descriptor reuse race).
void AuthStopLocked(bool local_leave) {
    if (local_leave) auth_client.Leave();
    auth_client.Reset(); auth_context_ms = 0;
    authenticated_owner = 0; authenticated_room = 0; peers = 0;
    if (relay != INVALID_SOCKET) shutdown(relay, SD_BOTH);
    relay = INVALID_SOCKET;
}

u32 AuthSend(const void* data, u32 size, const TheHuntVisibility::Context* visibility_context=nullptr) {
    std::scoped_lock lock{send_mutex};
    if (relay == INVALID_SOCKET || !data || !size || !auth_context_ms ||
        GetTickCount64() - auth_context_ms > 1500) return 0;
    if(visibility_context && (!auth_client.Visibility() || !(auth_client.VisibilityContext()==*visibility_context))) return 0;
    TheHuntRelayAuth::Bytes frame;
    if (!auth_client.Send({static_cast<const u8*>(data), size}, frame)) return 0;
    if (!AuthWrite(relay, frame)) { AuthStopLocked(false); return 0; }
    return static_cast<u32>(peers.load());
}

void AuthDeliver(const TheHuntRelayAuth::Delivery& delivery) {
    if(delivery.inner.size()>=5 && delivery.inner[4]==0x28) {
        TheHuntVisibility::Context context;
        {
            std::scoped_lock lock{send_mutex};
            if(!auth_client.Visibility() || !auth_context_ms || GetTickCount64()-auth_context_ms>1500) return;
            context=auth_client.VisibilityContext();
        }
        if(context.room==delivery.context.room && context.owner==delivery.context.owner)
            Core::Bloodborne::OnBbscVisibilityPacket(context,delivery.sender,delivery.inner.data(),delivery.inner.size());
        return;
    }
    // Only the verified server metadata becomes an old-handler envelope. No network
    // legacy envelope or client's self-declared sender/session reaches this path.
    TheHuntRelayAuth::Bytes wrapped{'S','H','A','D',EnvelopeType};
    TheHuntRelayAuth::Put(wrapped, delivery.sender, 4);
    TheHuntRelayAuth::Put(wrapped, delivery.context.owner, 4);
    wrapped.insert(wrapped.end(), delivery.inner.begin(), delivery.inner.end());
    Dispatch(wrapped);
}

DWORD WINAPI RelayThread(void*) {
    if (!private_auth) return 0; // Public mode uses only its separately guarded P2P entry.
    if (!own_id || auth_token.empty()) { Log("private v2 credentials missing; transport disabled"); return 0; }
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 0;
    if (!Core::Bloodborne::BbscVisibilitySupported()) { Log("THA4 display hooks unavailable; transport disabled"); WSACleanup(); return 0; }
    for (;;) {
        std::string host; u16 port = 0;
        if (!FindServer(host, port) || PublicAuthHost(host)) { Sleep(5000); continue; }
        // Launcher pins this address to the same IPv4 as NP. No DNS/public fallback.
        sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(port);
        if (inet_pton(AF_INET, host.c_str(), &address.sin_addr) != 1) { Sleep(5000); continue; }
        SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (s == INVALID_SOCKET) { Sleep(5000); continue; }
        const DWORD send_timeout = 300;
        setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&send_timeout), sizeof(send_timeout));
        BOOL on = TRUE;
        setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&on), sizeof(on));
        bool ok = connect(s, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0;
        const auto deadline = GetTickCount64() + 5000;
        TheHuntRelayAuth::Bytes frame, proof;
        const bool try_visibility = true;
        if (ok) ok = AuthFrame(s, frame, deadline);
        if (ok) {
            std::scoped_lock lock{send_mutex};
            ok = auth_client.Begin(own_id, auth_token, frame, proof,try_visibility);
        }
        if (ok) ok = AuthWrite(s, proof) && AuthFrame(s, frame, deadline);
        {
            std::scoped_lock lock{send_mutex};
            ok = ok && auth_client.Ack(frame);
            if (ok) relay = s;
        }
        if (!ok) Log("THA4 authentication rejected; no legacy fallback");
        else Log("private THA4 authenticated; bidirectional display readiness enabled");
        while (ok) {
            { std::scoped_lock dispatch_lock{dispatch_mutex}; ExpireAuthPlacement(); }
            if (!AuthFrame(s, frame, GetTickCount64() + 1500)) break;
            std::scoped_lock dispatch_lock{dispatch_mutex};
            ExpireAuthPlacement();
            TheHuntRelayAuth::Delivery delivery;
            TheHuntRelayAuth::Received received;
            {
                std::scoped_lock lock{send_mutex};
                received = auth_client.Receive(frame, delivery);
                if (received == TheHuntRelayAuth::Received::Context) auth_context_ms = GetTickCount64();
                if (received == TheHuntRelayAuth::Received::Invalid ||
                    (auth_context_ms && GetTickCount64() - auth_context_ms > 1500)) { ok = false; break; }
            }
            if (received == TheHuntRelayAuth::Received::Context) {
                ApplyAuthContext(delivery);
            } else if (received == TheHuntRelayAuth::Received::Placement) {
                ApplyAuthPlacement(delivery.placement);
            } else {
                AuthDeliver(delivery);
            }
        }
        {
            std::scoped_lock dispatch_lock{dispatch_mutex};
            { std::scoped_lock lock{send_mutex}; AuthStopLocked(false); }
            Core::Bloodborne::OnBbscRelayAuthContextChanged(0);
            ClearAuthPlacement();
            closesocket(s);
        }
        Log("private v2 disconnected or rejected; authenticated transport only");
        Sleep(2000);
    }
}
