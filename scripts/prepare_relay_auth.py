"""Prepare authenticated private relay AFTER prepare_game_hardening/save_hardening.

No package/build integration here. Every mutation uses a unique fixed anchor;
relay and modified authority functions are also pinned to their input digests.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
from prepare_game_hardening_regression import function

ROOT = Path(__file__).resolve().parents[1]
RELAY_SHA256 = '4b165bed694fe8ba5b1884cebc3c6c5edd5c915726c30716231841d197316adf'
HTTP_SHA256 = 'a996e0c504ec282f1e9b92f49a5d1c31486c9212d63b926b1dfe1a2663088733'
CORE_PINS = {
    'void TickBbscRelayHostLock() {': '3f96692b74110995f299e1fe1439a11d108b59f09a305885266cc61eb54dd6d2',
    'u32 BbscRelayHostForPackets() {': 'f2edb071fdf706216a5b987daa06ef28af542a5148326b2ae7b3ec912dce3cfa',
    'bool BbscPvpRequireRoomBinding() {': 'ac860c7ef341d58481c46855c68f99d92f0f40a1799cfaa649897ff37d4e7836',
}


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unexpected relay-auth anchor: ' + before[:100])
    return text.replace(before, after, 1)


def apply_relay(text):
    if hashlib.sha256(text.encode()).hexdigest() != RELAY_SHA256:
        raise ValueError('Unexpected relay source; run game hardening first, exactly once')
    text = '#include "bbsc_visibility_wire.h"\n' + text
    text = once(text, '#include <atomic>', '#include <algorithm>\n#include <cctype>\n#include <atomic>\n#include "bbsc_relay_auth.h"\n#include "bbsc_relay_auth_http.h"')
    text = once(text, 'void OnHostPlacement(const std::string& placement, u32 sender);',
                'bool OnHostPlacement(const std::string& placement, u32 sender);')
    text = once(text, 'namespace BbscMod {\nvoid LogPlain', '''namespace Core::Bloodborne {
void OnBbscRelayAuthContextChanged(std::uint32_t owner);
void OnBbscVisibilityPacket(const TheHuntVisibility::Context&, std::uint32_t, const std::uint8_t*, std::size_t);
bool BbscVisibilitySupported();
}

namespace BbscMod {
void RelayAuthClearPlacement();
void LogPlain''')
    text = once(text, '// Our session as the relay sees it:',
                '#include "bbsc_relay_auth_transport.inc"\n\n// Our session as the relay sees it:')
    text = once(text, 'u32 SessionKey() {', '''u32 SessionKey() {
    if (private_auth) return authenticated_owner.load();''')
    text = once(text, 'u32 SendFrame(const void* data, u32 size) {', '''u32 SendFrame(const void* data, u32 size) {
    if (private_auth) return AuthSend(data, size);''')
    # A public server has no owned relay. Its old transport is explicitly P2P only.
    thread = function(text, 'DWORD WINAPI RelayThread(void*) {')
    text = once(text, thread, '// Private v2 RelayThread is defined in bbsc_relay_auth_transport.inc.')
    text = once(text, '    return BbscMod::IsPublicServerAddress(host);',
                '    return !private_auth && PublicAuthHost(host);')
    text = once(text, 'void RelayDispatch(const std::vector<u8>& wrapped) {', '''void RelayDispatch(const std::vector<u8>& wrapped) {
    if (private_auth) return; // Never admit an unauthenticated P2P envelope in private mode.''')
    original = '''        LARGE_INTEGER counter{};
        QueryPerformanceCounter(&counter);
        own_id = static_cast<u32>((static_cast<u64>(counter.QuadPart) ^ GetCurrentProcessId()) *
                                  2654435761ull) |
                 1u;'''
    text = once(text, original, '        InitAuth();')
    text = once(text, 'void StartRelay() {', '''bool RelayAuthPrivate() { return private_auth.load(); }
bool RelayAuthInRoom() { return private_auth && authenticated_room.load() != 0; }
std::uint32_t RelayAuthOwner() { return authenticated_owner.load(); }
std::uint32_t RelayAuthOwnId() { return own_id; }
std::uint64_t RelayAuthRoom() { return authenticated_room.load(); }
std::uint64_t RelayAuthNonce() {
    std::uint64_t value=0;
    return BCryptGenRandom(nullptr,reinterpret_cast<PUCHAR>(&value),sizeof(value),BCRYPT_USE_SYSTEM_PREFERRED_RNG)<0?0:value;
}
bool RelayAuthVisibilityContext(TheHuntVisibility::Context& out) {
    std::scoped_lock lock{send_mutex};
    if (!private_auth || !auth_client.Visibility() || !auth_context_ms || GetTickCount64()-auth_context_ms>1500) {
        out={}; return false;
    }
    out=auth_client.VisibilityContext(); return out.room!=0;
}
bool RelayAuthUseVisibilityContext(const TheHuntVisibility::Context& expected,
                                   bool (*write)(void*), void* target) {
    std::scoped_lock lock{send_mutex};
    if (!write || !private_auth || !auth_client.Visibility() || !auth_context_ms ||
        GetTickCount64()-auth_context_ms>1500 || !expected.room ||
        auth_client.VisibilityContext()!=expected) return false;
    // Bounded native memory write only. No relay calls, logging or other locks.
    return write(target);
}
void RelayAuthSendVisibility(const TheHuntVisibility::Context& context,const TheHuntVisibility::Bytes& bytes) {
    if (!bytes.empty()) AuthSend(bytes.data(),static_cast<u32>(bytes.size()),&context);
}
std::uint64_t RelayAuthArmFirstSpawn() {
    if (!private_auth) return 0;
    std::unique_lock dispatch{dispatch_mutex, std::try_to_lock};
    if (!dispatch) return 0;
    std::unique_lock send{send_mutex, std::try_to_lock};
    return send && FirstSpawnGrant(false) ? first_spawn_epoch : 0;
}
bool RelayAuthUseFirstSpawn(std::uint64_t epoch, bool (*write)(std::string_view, void*), void* target) {
    if (!private_auth || !epoch) return false;
    std::unique_lock dispatch{dispatch_mutex, std::try_to_lock};
    if (!dispatch || epoch != first_spawn_epoch) return false;
    std::unique_lock send{send_mutex, std::try_to_lock};
    if (!send) return false;
    const auto* pending = FirstSpawnGrant(true);
    if (!pending) return false;
    first_spawn_available = false;
    // Bounded parse and one native memory write only; no callbacks into the relay.
    return write(pending->text, target);
}
std::string RelayAuthHttpTarget(const std::string& url) {
    if (!private_auth) return url;
    const char* source = std::getenv("BBSC_RELAY_HTTP_SOURCE");
    const char* pin = std::getenv("BBSC_RELAY_HTTP_ORIGIN");
    TheHuntRelayAuth::HttpUrl target, origin;
    if (!source || !pin || !TheHuntRelayAuth::ParseHttp(url, target) ||
        !TheHuntRelayAuth::ParseHttp(source, origin) || !TheHuntRelayAuth::SameOrigin(target, origin) ||
        (!target.path.empty() && target.path != L"/" && !TheHuntRelayAuth::SummonPath(target.path))) return url;
    const auto path = url.find('/', url.find("://") + 3);
    return std::string(pin) + (path == std::string::npos ? "" : url.substr(path));
}
std::string RelayAuthBearer(const std::string& url) {
    const char* origin = std::getenv("BBSC_RELAY_HTTP_ORIGIN");
    if (!private_auth || !own_id || auth_token.empty() || !origin ||
        !TheHuntRelayAuth::BearerTarget(url, origin)) return {};
    for (const auto byte : auth_token) if (byte < 0x20 || byte == 0x7f) return {};
    return "Bearer " + std::string(auth_token.begin(), auth_token.end());
}
void RelayAuthInvalidate() {
    if (!private_auth) return;
    std::scoped_lock dispatch_lock{dispatch_mutex};
    { std::scoped_lock lock{send_mutex}; AuthStopLocked(true); }
    Core::Bloodborne::OnBbscRelayAuthContextChanged(0);
    ClearAuthPlacement();
}

void StartRelay() {''')
    return text



def apply_j4_logging(text):
    if 'void LogJ4If(' in text:
        raise ValueError('J4 logger already patched')
    # The predicate executes under the existing log lock; it must not log or
    # acquire the diagnostic window lock. Keep the original logger untouched.
    anchor = """void LogPlain(const char* text) {\n    LogLine('I', "Mod", text);\n}"""
    return once(text, anchor, anchor + '''

#if !defined(BBSC_J4_DIAG_DISABLED)
void LogJ4If(const char* text, bool (*accept)(void*), void* state) {
    std::scoped_lock lock{log_mutex};
    if (!accept(state) || log_file == nullptr) return;
    SYSTEMTIME now;
    GetLocalTime(&now);
    std::fprintf(log_file, "[%02u:%02u:%02u.%03u] I Mod: %s\\n", now.wHour, now.wMinute, now.wSecond,
                 now.wMilliseconds, text);
    std::fflush(log_file);
}
#endif
''')


def apply_http(text):
    if hashlib.sha256(text.encode()).hexdigest() != HTTP_SHA256:
        raise ValueError('Unexpected HTTP source for private relay credential injection')
    text = once(text, '#include <windows.h>', '#include <windows.h>\n#include "bbsc_relay_auth_http.h"')
    text = once(text, 'void RelaySendPlacement(const std::string& placement);', '''void RelaySendPlacement(const std::string& placement);
bool RelayAuthPrivate();
bool RelayAuthInRoom();
std::string RelayAuthHttpTarget(const std::string& url);
std::string RelayAuthBearer(const std::string& url);''')
    text = once(text, 'bool RewriteAreaId(std::string& body) {', '''bool RewriteAreaId(std::string& body) {
    // Retain the accepted coordinates for the native reload, not for another sign.
    if (BbscMod::RelayAuthInRoom()) return false;''')
    for marker in ('int PS4_SYSV_ABI CreateConnectionWithURL(', 'int PS4_SYSV_ABI CreateRequestWithURL('):
        original = function(text, marker)
        updated = once(original, 'const std::string target = Override(url);',
                       'const std::string target = BbscMod::RelayAuthHttpTarget(Override(url));')
        text = once(text, original, updated)
    text = once(text, '    original_add_header(request, "X-ShadPS4-Bloodborne-Seamless", "1", 0);', '''    if (BbscMod::RelayAuthPrivate()) {
        std::string bearer = BbscMod::RelayAuthBearer(url);
        if (bearer.empty()) return -1;
        const int added = original_add_header(request, "Authorization", bearer.c_str(), 0);
        SecureZeroMemory(bearer.data(), bearer.size());
        if (added < 0) return added;
    }
    original_add_header(request, "X-ShadPS4-Bloodborne-Seamless", "1", 0);''')
    text = once(text, '        return original_send(request, data, size); // (review 2026-10-08) the slots never filled',
                '        if (BbscMod::RelayAuthPrivate()) return -1;\n'
                '        return original_send(request, data, size); // (review 2026-10-08) the slots never filled')
    text = once(text, '    std::wstring wide(url.begin(), url.end());', '''    const std::string target = BbscMod::RelayAuthHttpTarget(url);
    std::string bearer = BbscMod::RelayAuthBearer(target);
    if (BbscMod::RelayAuthPrivate() && bearer.empty()) return 0;
    std::wstring wide(target.begin(), target.end());''')
    text = once(text, '        const wchar_t* headers =', '        std::wstring headers =')
    text = once(text, '        if (WinHttpSendRequest(request, headers, static_cast<DWORD>(-1),', '''        if (!bearer.empty()) {
            const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bearer.data(),
                static_cast<int>(bearer.size()), nullptr, 0);
            if (count <= 0) {
                SecureZeroMemory(bearer.data(), bearer.size());
                for (HINTERNET handle : {request, connect, session}) if (handle) WinHttpCloseHandle(handle);
                return 0;
            }
            std::wstring value(count, L'\\0');
            MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bearer.data(), static_cast<int>(bearer.size()), value.data(), count);
            headers += L"Authorization: " + value + L"\\r\\n";
            SecureZeroMemory(value.data(), value.size() * sizeof(wchar_t));
            DWORD disable = WINHTTP_DISABLE_REDIRECTS;
            if (!WinHttpSetOption(request, WINHTTP_OPTION_DISABLE_FEATURE, &disable, sizeof(disable))) {
                SecureZeroMemory(headers.data(), headers.size() * sizeof(wchar_t));
                SecureZeroMemory(bearer.data(), bearer.size());
                for (HINTERNET handle : {request, connect, session}) if (handle) WinHttpCloseHandle(handle);
                return 0;
            }
        }
        if (WinHttpSendRequest(request, headers.c_str(), static_cast<DWORD>(-1),''')
    text = once(text, '    for (HINTERNET h : {request, connect, session}) {', '''    SecureZeroMemory(bearer.data(), bearer.size());
    for (HINTERNET h : {request, connect, session}) {''')
    text = once(text, '    }\n    SecureZeroMemory(bearer.data(), bearer.size());',
                '        SecureZeroMemory(headers.data(), headers.size() * sizeof(wchar_t));\n'
                '    }\n    SecureZeroMemory(bearer.data(), bearer.size());')
    text = once(text, 'bool IsPublicServerAddress(const std::string& url_or_host) {', '''void RelayAuthClearPlacement() {
    placement_received_ms = 0;
    placement_sender = 0;
    Core::Bloodborne::ClearSeamlessHostPlacementHeader();
}

bool IsPublicServerAddress(const std::string& url_or_host) {''')
    placement = function(text, 'void OnHostPlacement(')
    accepted = once(placement, 'void OnHostPlacement(', 'bool OnHostPlacement(')
    accepted = once(accepted, '        return; // hosting, joining or already a guest',
                    '        return false; // hosting, joining or already a guest')
    accepted = once(accepted, '        return;\n    }', '        return false;\n    }')
    accepted = once(accepted, '        last_map = map;\n    }\n}',
                    '        last_map = map;\n        return true;\n    }\n    return false;\n}')
    text = once(text, placement, accepted)
    text = once(text, 'namespace {\n\nconstexpr u64 EbootSize',
                '#if !defined(BBSC_J4_DIAG_DISABLED)\n#include "bbsc_j4_diag.inc"\n#endif\n\n'
                'namespace {\n\nconstexpr u64 EbootSize')
    text = once(text, 'void InstallHttpWrappers() {',
                'void InstallHttpWrappers() {\n#if !defined(BBSC_J4_DIAG_DISABLED)\n'
                '    BbscJ4Diag::Start();\n#endif')
    text = once(text, '    WatchSessionForStaleClaims();',
                '#if !defined(BBSC_J4_DIAG_DISABLED)\n'
                '    BbscJ4Diag::ObserveHttpUrl(url);\n#endif\n    WatchSessionForStaleClaims();')
    text = once(text, '        if (IsSummonCreate(url)) {\n            const std::string_view reply(static_cast<const char*>(data), static_cast<size_t>(read));',
                '        if (IsSummonCreate(url)) {\n            const std::string_view reply(static_cast<const char*>(data), static_cast<size_t>(read));\n'
                '#if !defined(BBSC_J4_DIAG_DISABLED)\n'
                '            BbscJ4Diag::ObserveClaimReply(request,static_cast<std::uint64_t>(read),\n'
                '                reply.find("\\\"UserId\\\"") != std::string_view::npos,\n'
                '                __builtin_return_address(0),__builtin_frame_address(0));\n#endif')
    from prepare_summon_cleanup import apply_http as cleanup_http
    return cleanup_http(text)


def guard_private_join_placement(text):
    # Keep the established normal follow/lighting calls intact. The new join
    # path installs target world state only after the native selector agreed.
    warp = 'bool RequestBbscSessionWarp(u64 global, const SummonPlacementDescriptor& to, std::string_view why) {'
    native = function(text, warp)
    updated = once(native, warp, '''bool RequestBbscSessionWarp(u64 global, const SummonPlacementDescriptor& to,
                            std::string_view why,
                            bool (*prepare)(u32, std::string_view) = nullptr) {''')
    updated = once(updated, '    if (!WriteTransportedSummonPlacement(global, to)) {', '''    // Do not take over a destination already armed by the native game.
    if (prepare && (ReadValue<u8>(global, 0x1520) != 0 ||
                    !HasMemoryAccess(global + 0x14C4, 0x5D, MemoryProt::CpuWrite))) return false;
    const u32 prior_map = prepare ? ReadValue<u32>(global, 0x14C4) : 0;
    const auto cancel = [&] {
        if (!prepare) return;
        WriteValue<u8>(global, 0x1520, 0);
        WriteValue<u32>(global, 0x14C4, prior_map);
    };
    if (!WriteTransportedSummonPlacement(global, to)) {''')
    updated = once(updated, '        LOG_WARNING(Debug, "BBSC {} warp: summoned placement block not writable", why);',
                   '        cancel();\n        LOG_WARNING(Debug, "BBSC {} warp: summoned placement block not writable", why);')
    updated = once(updated, '        return false;\n    }\n    const u64 rules =', '''        cancel();
        return false;
    }
    if (prepare && !prepare(to.packed_region, why)) {
        cancel();
        return false;
    }
    const u64 rules =''')
    updated = once(updated, '    return true;\n}', '''    if (prepare && result == 0) {
        cancel();
        return false;
    }
    return true;
}''')
    text = once(text, native, updated)
    start = 'void TickBbscJoinTeleport() {'
    original = function(text, start)
    guarded = once(original, start, '''enum class BbscPrivateJoinDecision { Wait, Position, Stop };
BbscPrivateJoinDecision TickBbscPrivateJoinRecovery(u64 global, u32 map, s64 now);
void NoteBbscPrivateJoinUnready(bool home);
bool BbscPrivateJoinRequestedLoad();
bool BbscPrivateJoinMayPlace(u32 map, s64 now);

void TickBbscJoinTeleport() {
    const bool private_session = BbscMod::RelayAuthPrivate();
    TheHuntRelayAuth::EffectGuard auth_guard{private_session};''')
    guarded = once(guarded, '        done = false;', '''        if (private_session) {
            const bool home = session_state != 4 && session_state != 5 &&
                ReadablePointer(global, 0x1598) && ReadValue<u8>(global, 0x08) == 0 &&
                ReadValue<u8>(global, 0x1592) != 0;
            NoteBbscPrivateJoinUnready(home);
        }
        done = false;''')
    guarded = once(guarded, '''        ReadValue<u8>(global, 0x1592) != 0) {
        stable_since_ms = 0;''', '''        ReadValue<u8>(global, 0x1592) != 0) {
        if (private_session && !done) NoteBbscPrivateJoinUnready(false);
        stable_since_ms = 0;''')
    guarded = once(guarded, '    if (GetLocalPlayerType() == 2) {', '''    const auto role = GetLocalPlayerType();
    if (role == 2) {
        if (private_session) NoteBbscPrivateJoinUnready(true);''')
    guarded = once(guarded, '    // The host, not just any living partner:', '''    if (private_session) {
        if (role != 1 || !ReadablePointer(player, 0x410) || GetCharacterHp(player) <= 0) {
            NoteBbscPrivateJoinUnready(false);
            stable_since_ms = 0;
            return;
        }
        switch (TickBbscPrivateJoinRecovery(global, GetCurrentPackedMap(), now)) {
        case BbscPrivateJoinDecision::Stop:
            done = true;
            bbsc_join_teleport_done = true;
            [[fallthrough]];
        case BbscPrivateJoinDecision::Wait:
            stable_since_ms = 0;
            return;
        case BbscPrivateJoinDecision::Position:
            break;
        }
    }
    // The host, not just any living partner:''')
    guarded = once(guarded, ': FindLivingPartnerCharacter();',
                   ': (private_session ? 0 : FindLivingPartnerCharacter());')
    guarded = once(guarded, '    // Let the engine do the transition first, once, then position inside the loaded map.',
                   '''    // Recheck immediately before either write path, even after the host actor settles.
    if (private_session && !BbscPrivateJoinMayPlace(GetCurrentPackedMap(), now)) return;

    // Let the engine do the transition first, once, then position inside the loaded map.''')
    guarded = once(guarded, '        !warp_requested) {',
                   '        !warp_requested && (!private_session || !BbscPrivateJoinRequestedLoad())) {')
    text = once(text, original, guarded)
    text = once(text, '''            TickBbscStateDiff();
            TickBbscJoinTeleport();
        }''', '''            TickBbscStateDiff();
        }
        if (BbscMod::RelayAuthPrivate() || BbscSpectateEnabled()) TickBbscJoinTeleport();''')
    receiver = function(text, 'void OnBbscHostFollowPacket(')
    text = once(text, receiver, once(receiver, '    if (!BbscWarpTogetherEnabled()) {',
                '    if (!BbscWarpTogetherEnabled() && !BbscMod::RelayAuthPrivate()) {'))
    handoff = function(text, 'void ApplyCrossMapSummonGuestPlacement(')
    observed = once(handoff, '    if (connect_first_host.has_value()) {\n        BbscWriteConnectFirstPlacement',
                   '''    if (BbscMod::RelayAuthPrivate()) {
        const auto& hint = connect_first_host ? connect_first_host : transported_host;
        LOG_INFO(Debug, "BBSC join handoff: hint {}, local map {:#x}, hint map {:#x}",
                 connect_first_host ? "connect-first" : transported_host ? "transported" : "none",
                 GetCurrentPackedMap(), hint ? hint->packed_region : 0);
    }
    if (connect_first_host.has_value()) {
        BbscWriteConnectFirstPlacement''')
    text = once(text, handoff, observed)
    text = once(text, 'void TickBbscFollowHost() {',
                '#include "bbsc_private_join_recovery.inc"\n\nvoid TickBbscFollowHost() {')
    follow = function(text, 'void TickBbscFollowHost() {')
    revised = once(follow, '    const u32 here = GetCurrentPackedMap();', '''    if (BbscMod::RelayAuthPrivate() && !BbscPrivateJoinAllowsLaterFollow(host)) return;
    const u32 here = GetCurrentPackedMap();''')
    revised = once(revised, '    if (!BbscHostWorldFlagsReady(host.packed_map)) {', '''    const bool world_ready = BbscHostWorldFlagsReady(host.packed_map);
    if (!world_ready) {''')
    revised = once(revised, '''    } else {
        ApplyBbscHostWorldFlags(host.packed_map, "before the follow load");
    }
    ApplyBbscGuestLootView(host.packed_map, "before the follow load");
    if (host.packed_map == BbscDreamStateMap) {
        ApplyBbscHostDreamState("before the follow load");
    } else {
        RestoreBbscOwnDreamState("following the host out of the Dream");
    }''', '''    }
    if (!ApplyBbscGuestTravelState(host.packed_map, "before the follow load",
                                 "following the host out of the Dream", world_ready)) return;''')
    return once(text, follow, revised)


def apply_first_spawn(text):
    text = once(text, 'constexpr u64 CrossMapGuestHandoffOffset = 0x01E4FF3A;',
                'constexpr u64 CrossMapGuestHandoffOffset = 0x01E4FF3A;\n'
                'constexpr u64 BbscFirstSpawnCopyOffset = 0x01E4FEE2;')
    text = once(text, '    {"CSMultiPlayerIns.PlacementCopy",', '''    {"CSMultiPlayerIns.FirstSpawnCopy", BbscFirstSpawnCopyOffset, TraceKind::GlobalState,
     {0xC5, 0xF8, 0x28, 0x80, 0x20, 0x16, 0x00, 0x00}, 8},
    {"CSMultiPlayerIns.PlacementCopy",''')
    verify = function(text, 'bool VerifySites() {')
    checked = once(verify, '    for (const auto& site : Sites) {', '''    for (const auto& site : Sites) {
        // This optional hook is checked at install. A mismatch must not disable existing hooks.
        if (site.offset == BbscFirstSpawnCopyOffset) continue;''')
    text = once(text, verify, checked)
    text = once(text, '    size_t hook_count = 0;\n    for (size_t index = 0;',
                '    size_t hook_count = 0;\n    bool first_spawn_hook = false;\n    for (size_t index = 0;')
    text = once(text, '        const auto expected = std::span<const u8>{site.prologue.data(), site.prologue_size};\n'
                     '        if (!InstallGuestCodeHook(',
                     '''        if (site.offset == BbscFirstSpawnCopyOffset &&
            (site.offset > MemoryPatcher::g_eboot_image_size ||
             site.prologue_size > MemoryPatcher::g_eboot_image_size - site.offset)) continue;
        const auto expected = std::span<const u8>{site.prologue.data(), site.prologue_size};
        if (!InstallGuestCodeHook(''')
    text = once(text, '        ++hook_count;\n    }\n\n    installed = hook_count == Sites.size();', '''        ++hook_count;
        if (site.offset == BbscFirstSpawnCopyOffset) first_spawn_hook = true;
    }

    installed = hook_count + (first_spawn_hook ? 0 : 1) == Sites.size();''')
    original = function(text, 'bool SetSeamlessHostPlacementHeader(')
    continuation = '    if (BbscConnectFirstEnabled()) {'
    start = original.index(continuation)
    parse = original[:start].replace(
        'bool SetSeamlessHostPlacementHeader(',
        'std::optional<SummonPlacementDescriptor> ParseBbscSummonPlacement(', 1)
    parse = parse.replace('return false;', 'return std::nullopt;') + '    return placement;\n}\n'
    setter = '''bool SetSeamlessHostPlacementHeader(std::string_view value) {
    const auto parsed = ParseBbscSummonPlacement(value);
    if (!parsed) return false;
    const auto& placement = *parsed;
'''
    text = once(text, original, setter + original[start:])
    text = once(text, '#include "bbsc_private_join_recovery.inc"',
                '#include "bbsc_private_join_recovery.inc"\n\n' + parse + '\n#include "bbsc_first_spawn.inc"')
    text = once(text, 'void TickBbscJoinTeleport() {', '''bool BbscKeepFirstSpawn(u32 map, const BbscTransform& own, const BbscTransform& host);

void TickBbscJoinTeleport() {''')
    tick = function(text, 'void TickBbscJoinTeleport() {')
    changed = once(tick, '    const auto own = ReadCharacterTransform(player);\n', '')
    changed = once(changed, '    const u64 vtable = ReadValue<u64>(player, 0);', '''    const auto own = ReadCharacterTransform(player);
    if (private_session && !warp_requested && !BbscPrivateJoinRequestedLoad() && own &&
        BbscKeepFirstSpawn(GetCurrentPackedMap(), *own, *host_transform)) {
        LOG_INFO(Debug, "BBSC join teleport: kept native position near the host");
        EndBbscLoadCover("guest already beside the host");
        return;
    }

    const u64 vtable = ReadValue<u64>(player, 0);''')
    text = once(text, tick, changed)
    trace = function(text, 'void PS4_SYSV_ABI TraceEntry(')
    revised = once(trace, '''            return;
        }
    }
    if (site.offset == SummonBuildEntryOffset)''', '''            return;
        }
        if (event == "JoinSession_White") ArmBbscFirstSpawn();
    }
    if (site.offset == BbscFirstSpawnCopyOffset) {
        TryBbscFirstSpawnBeforeCopy(registers->rbx, registers->rax);
        return;
    }
    if (site.offset == SummonBuildEntryOffset)''')
    revised = once(revised, '        BbscFillConnectFirstReceivedPlacement();',
                    '        CloseBbscFirstSpawnOnNativeReload();\n        BbscFillConnectFirstReceivedPlacement();')
    # v4: the host position goes into the summon load's spawn descriptor, after
    # every native refresh and copy of the global placement banks.
    revised = once(revised, '''        ApplySummonReloadStateAtDescriptor(*registers);
    }
''', '''        ApplySummonReloadStateAtDescriptor(*registers);
        ApplyBbscFirstSpawnAtDescriptor(registers->r9, GetGlobalState());
    }
''')
    revised = once(revised, '''        PrepareBbscSummonedType();
        BbscConnectFirstReloadFinished();
''', '''        PrepareBbscSummonedType();
        BbscConnectFirstReloadFinished();
        FinishBbscFirstSpawnAtReload();
''')
    text = once(text, trace, revised)
    return trace_load_cover(text)


def trace_load_cover(text):
    # Test evidence only: when the game's own loading screen closes during the
    # cover, and whether BBSC could put it back up. Behavior is unchanged.
    text = once(text, 'struct BbscLoadCoverState {\n', '''struct BbscLoadCoverState {
    bool closed_logged = false;
    bool reopen_failed_logged = false;
''')
    tick = function(text, 'void TickBbscLoadCover() {')
    changed = once(tick, '            if (BbscLoadingScreen(true)) {\n', '''            if (!bbsc_load_cover.closed_logged) {
                bbsc_load_cover.closed_logged = true;
                LOG_INFO(Debug, "BBSC load cover: the game's loading screen closed after {} ms ({})",
                         now - bbsc_load_cover.started_ms, bbsc_load_cover.why);
            }
            const bool reopened = BbscLoadingScreen(true);
            if (!reopened && !bbsc_load_cover.reopen_failed_logged) {
                bbsc_load_cover.reopen_failed_logged = true;
                LOG_INFO(Debug, "BBSC load cover: could not reopen the loading screen; only the black "
                                "plate covers ({})",
                         bbsc_load_cover.why);
            }
            if (reopened) {
''')
    return once(text, tick, changed)



def apply_load_protection(text):
    text = once(text, 'void HandleBbscLuaSessionEvent(std::string_view name) {', """enum class BbscLoadProtectionKind { Join, Travel, Respawn };
struct BbscFollowLoadProtection { u64 ticket{}, reload_before{}; };
enum class BbscFollowLanding { Done, Retry };
u64 ArmBbscLoadProtection(BbscLoadProtectionKind kind);
u64 BbscLoadProtectionTicket();
void MarkBbscLoadProtectionPlaced(u64 ticket, u32 map);
void MarkBbscLoadProtectionPlaced(u64 ticket, u32 map, u64 actor, u64 generation);
BbscFollowLanding MarkBbscFollowNativeLanding(const BbscFollowLoadProtection& request, u32 map,
                                              u64 actor, u64 generation);

void HandleBbscLuaSessionEvent(std::string_view name) {""")

    def patch_function(text, marker, changes):
        old = function(text, marker)
        new = old
        for before, after in changes:
            new = once(new, before, after)
        return once(text, old, new)

    text = patch_function(text, 'bool RequestBbscSessionWarp(', (
        ('bool (*prepare)(u32, std::string_view) = nullptr) {',
         'bool (*prepare)(u32, std::string_view) = nullptr,\n'
         '                            BbscFollowLoadProtection* protection = nullptr) {'),
        ('    StartBbscLoadCover(why); // already on', """    const u64 ticket = ArmBbscLoadProtection(why == "follow" ? BbscLoadProtectionKind::Travel :
                                             why == "wipe" ? BbscLoadProtectionKind::Respawn :
                                                             BbscLoadProtectionKind::Join);
    StartBbscLoadCover(why); // already on"""),
        ('    const u64 result = reinterpret_cast<StageTransition>',
         '    if (protection) *protection = {ticket, bbsc_character_reload_generation.load()};\n'
         '    const u64 result = reinterpret_cast<StageTransition>'),
    ))
    text = patch_function(text, 'void TickBbscJoinTeleport() {', (
        ('    const auto own = ReadCharacterTransform(player);',
         '    const u64 protection_ticket = BbscLoadProtectionTicket();\n'
         '    const auto own = ReadCharacterTransform(player);'),
        ('        EndBbscLoadCover("guest already beside the host");',
         '        MarkBbscLoadProtectionPlaced(protection_ticket, GetCurrentPackedMap());\n'
         '        EndBbscLoadCover("guest already beside the host");'),
        ('    EndBbscLoadCover("guest placed beside the host");',
         '    MarkBbscLoadProtectionPlaced(protection_ticket, GetCurrentPackedMap());\n'
         '    EndBbscLoadCover("guest placed beside the host");'),
    ))
    text = patch_function(text, 'void TickBbscFollowHost() {', (
        ('    if (BbscMod::RelayAuthPrivate() && !BbscMod::RelayAuthOwner()) return;',
         '    static u64 pending_landing_actor = 0;\n'
         '    if (BbscMod::RelayAuthPrivate() && !BbscMod::RelayAuthOwner()) {\n'
         '        pending_landing_actor = 0;\n'
         '        return;\n'
         '    }'),
        ('    static u32 target_map = 0;',
         '    static u32 target_map = 0;\n    static BbscFollowLoadProtection protection;'),
        ('        target_map = 0;',
         '        target_map = 0;\n        pending_landing_actor = 0;'),
        ('        last_load_ms = now;',
         '        last_load_ms = now;\n        pending_landing_actor = 0;'),
        ('    BbscFollowMessage host{};', """    // A retained native landing does not depend on fresh host position packets.
    if (pending_landing_actor &&
        MarkBbscFollowNativeLanding(protection, target_map, pending_landing_actor,
                                    bbsc_character_reload_generation.load()) != BbscFollowLanding::Retry) {
        pending_landing_actor = 0;
    }

    BbscFollowMessage host{};"""),
        ('        warping = false;\n        const u64 player = GetLocalPlayerCharacter();',
         '        warping = false;\n'
         '        const u64 generation = bbsc_character_reload_generation.load();\n'
         '        const u64 player = GetLocalPlayerCharacter();'),
        ('            EndBbscLoadCover("arrived, host moved on");',
         '            if (MarkBbscFollowNativeLanding(protection, target_map, player, generation) ==\n'
         '                BbscFollowLanding::Retry) pending_landing_actor = player;\n'
         '            EndBbscLoadCover("arrived, host moved on");'),
        ('        EndBbscLoadCover("arrived beside the host");',
         '        MarkBbscLoadProtectionPlaced(protection.ticket, target_map, player, generation);\n'
         '        EndBbscLoadCover("arrived beside the host");'),
        ('    if (!RequestBbscSessionWarp(global, to, "follow")) {',
         '    if (!RequestBbscSessionWarp(global, to, "follow", nullptr, &protection)) {'),
        ('    warping = true;', '    pending_landing_actor = 0;\n    warping = true;'),
    ))
    text = patch_function(text, 'bool StartBbscDeathCurtain(std::string_view why) {', (
        ('    const s64 now = SteadyNowMs();',
         '    ArmBbscLoadProtection(BbscLoadProtectionKind::Respawn);\n'
         '    const s64 now = SteadyNowMs();'),
    ))
    text = patch_function(text, 'bool BbscRespawnInPlace(std::string_view why) {', (
        ('    reinterpret_cast<Warp>(warp)(player, position.data(), rotation.data(), 1);',
         '    ArmBbscLoadProtection(BbscLoadProtectionKind::Respawn);\n'
         '    const u64 protection_ticket = BbscLoadProtectionTicket();\n'
         '    reinterpret_cast<Warp>(warp)(player, position.data(), rotation.data(), 1);'),
        ('    bbsc_inplace_respawn_ms = SteadyNowMs();',
         '    MarkBbscLoadProtectionPlaced(protection_ticket, GetCurrentPackedMap());\n'
         '    bbsc_inplace_respawn_ms = SteadyNowMs();'),
    ))
    text = once(text, 'void PS4_SYSV_ABI TraceEntry(u64 tag, const GuestRegisterSnapshot* registers) {',
                '#include "bbsc_peer_display.inc"\n#include "bbsc_load_protection.inc"\n\n'
                'void PS4_SYSV_ABI TraceEntry(u64 tag, const GuestRegisterSnapshot* registers) {')
    text = once(text, '        if (event == "JoinSession_White") ArmBbscFirstSpawn();', """        if (event == "JoinSession_White") {
            if (GetSessionState() != 3) ArmBbscLoadProtection(BbscLoadProtectionKind::Join);
            ArmBbscFirstSpawn();
        }""")
    text = once(text, '        TickBbscCoopEffectRestore();',
                '        TickBbscCoopEffectRestore();\n        TickBbscLoadProtection();\n        TickBbscPeerDisplay();')
    return once(text, '    InstallBbscEchoPathHooks();',
                '    InstallBbscEchoPathHooks();\n    if (installed) InstallBbscLoadProtectionHooks();\n    if (installed) InstallBbscPeerDisplayHooks();')


def apply_core(text):
    if '#include "bbsc_active_save.inc"' not in text:
        raise ValueError('Run save hardening before relay authentication')
    for marker, digest in CORE_PINS.items():
        if hashlib.sha256(function(text, marker).encode()).hexdigest() != digest:
            raise ValueError('Unexpected relay authority function: ' + marker)
    text = once(text, '#include <mutex>', '#include <mutex>\n#include "bbsc_relay_auth_guard.h"')
    text = once(text, 'namespace Core::Bloodborne {', '''#include "bbsc_visibility_wire.h"
namespace BbscMod {
bool RelayAuthPrivate();
std::uint32_t RelayAuthOwner();
std::uint32_t RelayAuthOwnId();
std::uint64_t RelayAuthRoom();
bool RelayAuthVisibilityContext(TheHuntVisibility::Context&);
void RelayAuthSendVisibility(const TheHuntVisibility::Context&,const TheHuntVisibility::Bytes&);
std::uint64_t RelayAuthNonce();
std::uint64_t RelayAuthArmFirstSpawn();
bool RelayAuthUseFirstSpawn(std::uint64_t epoch, bool (*write)(std::string_view, void*), void* target);
void RelayAuthInvalidate();
}

namespace Core::Bloodborne {''')
    text = once(text, 'namespace Core::Bloodborne {\nnamespace {', '''namespace Core::Bloodborne {
void OnBbscRelayAuthContextChanged(std::uint32_t owner);
namespace {''')
    text = once(text, 'void OnBbscHostBossPacket(std::int32_t boss) {',
                'bool BbscVisibilitySupported() { return bbsc_display_hooks && bbsc_load_protection_hooks && bbsc_active_save_hooks; }\n\n'
                'void OnBbscVisibilityPacket(const TheHuntVisibility::Context& context, std::uint32_t sender, const std::uint8_t* data, std::size_t size) {\n'
                '    CacheBbscVisibilityPacket(context,sender,{data,size});\n}\n\nvoid OnBbscHostBossPacket(std::int32_t boss) {')
    text = once(text, 'u32 BbscRelayHostForPackets() {', '''u32 BbscRelayHostForPackets() {
    if (BbscMod::RelayAuthPrivate()) return BbscMod::RelayAuthOwner();''')
    text = once(text, 'void TickBbscRelayHostLock() {', '''void TickBbscRelayHostLock() {
    if (BbscMod::RelayAuthPrivate()) {
        static s64 previous_leave = 0;
        static u64 previous_matching = 0;
        const s32 state = GetSessionState();
        const auto matching = GetMatchingState();
        const auto leave = bbsc_leave_event_ms.load();
        bbsc_relay_session_state = state;
        if (leave != previous_leave || (image_base == 0 && BbscMod::RelayAuthOwner()))
            BbscMod::RelayAuthInvalidate();
        // This value is a game-memory pointer, not a native room ID. Reloads may
        // replace it while native membership remains unchanged; clear caches only.
        else if (matching && previous_matching && matching != previous_matching)
            OnBbscRelayAuthContextChanged(BbscMod::RelayAuthOwner());
        previous_leave = leave;
        if (matching) previous_matching = matching;
        SelectBbscRelayHost(BbscMod::RelayAuthOwner());
        return; // Private ownership is only the authenticated native server context.
    }''')
    text = once(text, 'bool BbscPvpRequireRoomBinding() {', '''bool BbscPvpRequireRoomBinding() {
    if (BbscMod::RelayAuthPrivate()) return BbscMod::RelayAuthOwner() == 0;''')
    text = once(text, 'void TickBbscPvp() {', '''void TickBbscPvp() {
    TheHuntRelayAuth::EffectGuard auth_guard{BbscMod::RelayAuthPrivate()};''')
    original = function(text, 'void TickBbscPvp() {')
    updated = once(original, '    TickBbscInvadable();', '''    TickBbscInvadable();
    // Kit preparation and ordinary bell maintenance also run before room admission.
    if (BbscMod::RelayAuthPrivate()) {
        const auto owner = BbscMod::RelayAuthOwner();
        const auto own = BbscMod::RelayAuthOwnId();
        const auto state = GetSessionState();
        if (!owner || !own || (state == 3 && owner != own) || (state == 6 && owner == own)) {
            ClearBbscPvpHostState();
            SetBbscPvp(false, "private relay authority unavailable or role changed");
            ApplyBbscPvpTeams(); // Restore only the existing identity-validated snapshots.
            return;
        }
    }''')
    text = once(text, original, updated)
    text = once(text, 'void TickBbscFollowHost() {', '''void TickBbscFollowHost() {
    TheHuntRelayAuth::EffectGuard auth_guard{BbscMod::RelayAuthPrivate()};
    if (BbscMod::RelayAuthPrivate() && !BbscMod::RelayAuthOwner()) return;''')
    text = once(text, 'void ApplyBbscPvpTeams() {', '''void ApplyBbscPvpTeams() {
    TheHuntRelayAuth::EffectGuard auth_guard{BbscMod::RelayAuthPrivate()};''')
    # Scope this condition to the native team writer, not other PvP feature uses.
    original = function(text, 'void ApplyBbscPvpTeams() {')
    updated = once(original, '        if (bbsc_pvp_on) {',
        '        if (bbsc_pvp_on && (!BbscMod::RelayAuthPrivate() || BbscMod::RelayAuthOwner())) {')
    text = once(text, original, updated)
    text = once(text, '    u32 player_id = bbsc_life_own_id.load();', '''    if (BbscMod::RelayAuthPrivate()) {
        const auto own = BbscMod::RelayAuthOwnId();
        if (!own) return;
        bbsc_life_own_id = own;
    }
    u32 player_id = bbsc_life_own_id.load();''')
    text = once(text, 'void OnBbscHostBossPacket(std::int32_t boss) {',
                '#include "bbsc_relay_auth_core.inc"\n\nvoid OnBbscHostBossPacket(std::int32_t boss) {')
    text = once(text, 'bool ApplyBbscHostWorldFlags(u32 packed_map, std::string_view why) {', '''bool ApplyBbscHostWorldFlags(u32 packed_map, std::string_view why) {
    TheHuntRelayAuth::EffectGuard auth_guard{BbscMod::RelayAuthPrivate()};
    if (BbscMod::RelayAuthPrivate() && !BbscMod::RelayAuthOwner()) return false;''')
    return apply_load_protection(apply_first_spawn(guard_private_join_placement(text)))



CHANNEL_SHA256 = 'f3bfa820a22a37e6e8fd9d681af7c309bd9ca980ffad349df98f4e167ab63e23'


def apply_channel(text):
    if hashlib.sha256(text.encode()).hexdigest() != CHANNEL_SHA256:
        raise ValueError('Unexpected channel source for J4 observations')
    text = once(text, 'namespace {\n\nconstexpr u64 EbootSize', '''#if !defined(BBSC_J4_DIAG_DISABLED)
namespace BbscJ4Diag {
void* BeginNet(bool,void*,void*) noexcept;
void* BeginSignal(void*,void*) noexcept;
void ObserveNet(void*,s32,s32,u64,const void*,u32,const u32*) noexcept;
void ObserveSignalStatus(void*,s32,s32,s32,const u32*,const u16*) noexcept;
}
#endif

namespace {

constexpr u64 EbootSize''')
    for marker,begin,end in (
        ('s32 PS4_SYSV_ABI Sendto(',
         '    void* j4 = BbscJ4Diag::BeginNet(true,__builtin_return_address(0),__builtin_frame_address(0));',
         '    BbscJ4Diag::ObserveNet(j4,r,s,len,addr,addrlen,nullptr);'),
        ('s32 PS4_SYSV_ABI Recvfrom(',
         '    void* j4 = BbscJ4Diag::BeginNet(false,__builtin_return_address(0),__builtin_frame_address(0));',
         '    BbscJ4Diag::ObserveNet(j4,r,s,len,addr,0,addrlen);'),
        ('s32 PS4_SYSV_ABI ConnectionStatus(',
         '    void* j4 = BbscJ4Diag::BeginSignal(__builtin_return_address(0),__builtin_frame_address(0));',
         '    BbscJ4Diag::ObserveSignalStatus(j4,r,ctx,conn,status,port);'),
    ):
        original = function(text, marker)
        opening = original[:original.index('{')+2]
        changed = once(original, opening, opening+'#if !defined(BBSC_J4_DIAG_DISABLED)\n'+begin+'\n#endif\n')
        changed = once(changed, '    return r;', '#if !defined(BBSC_J4_DIAG_DISABLED)\n'+end+'\n#endif\n    return r;')
        text = once(text, original, changed)
    return text


def prepare(source):
    emulator = Path(source) / 'emulator'
    core = emulator / 'src/core'
    relay = emulator / 'mod/bbsc_relay.cpp'
    game = core / 'bloodborne_re_zh_cn.cpp'
    http = emulator / 'mod/bbsc_http.cpp'
    channel = emulator / 'mod/bbsc_channel.cpp'
    logger = emulator / 'mod/bbsc_mod_zh_cn.cpp'
    # Validate every replacement before writing either source.
    relay_text = apply_relay(relay.read_text('utf-8'))
    from respawn_experience import apply as apply_respawn_experience, apply_death_ui
    from repeat_boss_state import apply as apply_repeat_boss_state
    from host_ready import apply as apply_host_ready
    from wipe_respawn import apply as apply_wipe_respawn
    from retained_join import apply as apply_retained_join
    from death_local_prompt import apply as apply_death_local_prompt
    from join_cause_diagnostics import (apply_core as apply_join_cause_core,
                                       apply_http as apply_join_cause_http,
                                       apply_j4 as apply_join_cause_j4)
    from visual_diagnostics import apply as apply_visual_diagnostics
    core_text = apply_visual_diagnostics(apply_death_ui(apply_respawn_experience(
        apply_host_ready(apply_core(game.read_text('utf-8'))))))
    core_text = apply_join_cause_core(apply_death_local_prompt(
        apply_retained_join(apply_wipe_respawn(core_text))))
    core_text = apply_repeat_boss_state(core_text)
    http_text = apply_join_cause_http(apply_http(http.read_text('utf-8')))
    channel_text = apply_channel(channel.read_text('utf-8'))
    logger_text = apply_j4_logging(logger.read_text('utf-8'))
    j4_text = apply_join_cause_j4((ROOT / 'patches/bbsc_j4_diag.inc').read_text('utf-8'))
    for name in ('bbsc_visibility_wire.h', 'bbsc_relay_auth.h', 'bbsc_relay_auth_transport.inc', 'bbsc_relay_auth_http.h',
                 'bbsc_j4_diag.inc', 'bbsc_join_cause_adapter.inc', 'bbsc_summon_cleanup.h', 'bbsc_summon_cleanup.inc'):
        shutil.copy2(ROOT / 'patches' / name, emulator / 'mod' / name)
    for name in ('bbsc_relay_auth_core.inc', 'bbsc_relay_auth_guard.h', 'bbsc_private_join_recovery.inc',
                 'bbsc_first_spawn.inc', 'bbsc_load_protection.inc', 'bbsc_death_ui.inc', 'bbsc_visibility_wire.h', 'bbsc_peer_display.inc', 'bbsc_host_ready.inc', 'bbsc_visual_diagnostics.inc', 'bbsc_wipe_respawn.inc', 'bbsc_retained_join.inc', 'bbsc_banner_diagnostics.inc', 'bbsc_join_cause.inc'):
        shutil.copy2(ROOT / 'patches' / name, core)
    (emulator / 'mod/bbsc_j4_diag.inc').write_text(j4_text, encoding='utf-8', newline='\n')
    relay.write_text(relay_text, encoding='utf-8', newline='\n')
    game.write_text(core_text, encoding='utf-8', newline='\n')
    http.write_text(http_text, encoding='utf-8', newline='\n')
    channel.write_text(channel_text, encoding='utf-8', newline='\n')
    logger.write_text(logger_text, encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    prepare(parser.parse_args().source)
    print('Prepared private authenticated relay v2; no legacy/private P2P fallback.')


if __name__ == '__main__':
    main()
