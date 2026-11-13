"""Exercise the actual prepared spectate tick with synthetic game callbacks."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from prepare_game_hardening_regression import function


HEAD = r'''
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
using u8 = std::uint8_t; using u32 = std::uint32_t; using u64 = std::uint64_t;
using s32 = std::int32_t; using s64 = std::int64_t;
'''

FIXTURE = r'''
bool bbsc_request_death_reload, bbsc_spectate_active, bbsc_spectate_releasing;
bool bbsc_spectate_saw_wipe, bbsc_spectate_wipe_warped, bbsc_spectate_logged_wait;
s64 bbsc_spectate_since_ms, bbsc_boss_killed_ms, bbsc_boss_cleared_since_ms, bbsc_boss_bar_seen_ms;
s64 bbsc_spectate_host_back_ms, bbsc_spectate_alone_since_ms;
u64 bbsc_spectate_chr;
u32 bbsc_spectate_revivals_at_death;
std::atomic<u32> bbsc_peer_revivals{0};
std::string_view bbsc_spectate_name = "fixture";
BbscDeathMode bbsc_death_mode;
s64 now;
s32 session_state;
u64 character;
bool living, curtain_ok, respawn_ok, warp_ok;
int penalties, curtains, respawns, reloads, warnings, warps, scenarios = 0;
std::string respawn_reason, reload_reason, warning;
std::array<u8, 0x1690> boss_gauge;
constexpr u64 image_base = 0x100000, boss_character = 0x123456781000;
s32 boss_hp;
bool gauge_readable;
int skipped_dead_boss_logs;
struct GuestRegisterSnapshot {};
std::array<u8, 2> bbsc_late_final_skip{};
u32 bbsc_echoes_at_death;
bool bbsc_kept_death_this_load;
struct Transform { float position[3]{0, 0, 0}; float rotation[3]{0, 0, 0}; };
struct SummonPlacementDescriptor { u32 packed_region; float x, y, z, heading; };
template<class T> T ReadValue(u64 base, size_t offset) {
    if (base == image_base + BossGaugePointerOffset && offset == 0)
        return static_cast<T>(reinterpret_cast<u64>(boss_gauge.data()));
    if (base != reinterpret_cast<u64>(boss_gauge.data()) || offset + sizeof(T) > boss_gauge.size())
        throw std::runtime_error("unexpected gauge read");
    T value;
    std::memcpy(&value, boss_gauge.data() + offset, sizeof(T));
    return value;
}
bool ReadablePointer(u64 ptr, size_t size) {
    return gauge_readable && ptr == reinterpret_cast<u64>(boss_gauge.data()) && size <= boss_gauge.size();
}
void SetBossSlot(int slot, u64 chr) {
    std::memcpy(boss_gauge.data() + 0x165C + static_cast<size_t>(slot) * 0x14, &chr, sizeof(chr));
}
void RestoreBbscOwnWorldSnapshot() {}
bool BbscSpectateEnabled() { return true; }
bool BbscLateDeathStep(std::string_view) { return false; }
bool IsInvaderType(s32) { return false; }
s32 GetLocalCharacterType() { return 1; }
int RemoteInvaderCount() { return 0; }
void SuspendBbscPersistenceOnce(std::string_view) {}
u32 GetBloodEchoes() { return 100; }
void StashBbscOwnWorldSnapshot() {}
s64 SteadyNowMs() { return now; }
s32 GetSessionState() { return session_state; }
bool LivingPartnerExists() { return living; }
u64 GetLocalPlayerCharacter() { return character; }
u64 FindRemoteHostCharacter() { return 2; }
u64 FindLivingPartnerCharacter() { return 2; }
int GetCharacterHp(u64 chr) {
    if (chr == boss_character) return boss_hp;
    return chr == 2 ? (living ? 100 : 0) : -1;
}
std::optional<Transform> ReadCharacterTransform(u64) { return Transform{}; }
u32 GetCurrentPackedMap() { return 0x18010000; }
u64 GetGlobalState() { return 3; }
bool RequestBbscSessionWarp(u64, const SummonPlacementDescriptor&, std::string_view) {
    ++warps;
    return warp_ok;
}
void BbscApplyDeathPenalty(std::string_view why) { ++penalties; respawn_reason = why; }
bool StartBbscDeathCurtain(std::string_view why) {
    ++curtains; respawn_reason = why; return curtain_ok;
}
bool BbscRespawnInPlace(std::string_view why) {
    ++respawns; respawn_reason = why; return respawn_ok;
}
void RequestBbscDeathReload(std::string_view why) { ++reloads; reload_reason = why; }
template<class... Args> void LogWarning(std::string_view message, Args...) {
    ++warnings; warning = message;
}
#define LOG_WARNING(category, ...) LogWarning(__VA_ARGS__)
template<class... Args> void LogInfo(std::string_view message, Args...) {
    if (message.find("boss bar seen but HP=0, skipped") != std::string_view::npos)
        ++skipped_dead_boss_logs;
}
#define LOG_INFO(category, ...) LogInfo(__VA_ARGS__)
#define PS4_SYSV_ABI
void Check(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
void Reset() {
    ++scenarios;
    bbsc_request_death_reload = bbsc_spectate_releasing = false;
    bbsc_spectate_active = true;
    bbsc_spectate_saw_wipe = bbsc_spectate_wipe_warped = bbsc_spectate_logged_wait = false;
    bbsc_spectate_since_ms = 1000;
    bbsc_boss_killed_ms = bbsc_boss_cleared_since_ms = 0;
    bbsc_boss_bar_seen_ms = 0;
    boss_gauge.fill(0xFF);
    gauge_readable = true;
    boss_hp = skipped_dead_boss_logs = 0;
    bbsc_spectate_host_back_ms = bbsc_spectate_alone_since_ms = 0;
    character = bbsc_spectate_chr = 1;
    bbsc_spectate_revivals_at_death = 0;
    bbsc_peer_revivals = 0;
    bbsc_death_mode = BbscDeathMode::Spectate;
    now = bbsc_spectate_since_ms + 90'001;
    session_state = 6;
    living = curtain_ok = respawn_ok = warp_ok = true;
    penalties = curtains = respawns = reloads = warnings = warps = 0;
    respawn_reason.clear(); reload_reason.clear(); warning.clear();
}
'''

CASES = r'''
int main() {
    try {
        Reset();
        TickBbscSpectate();
        Check(!bbsc_spectate_active && bbsc_death_mode == BbscDeathMode::RespawnNow &&
              respawn_reason == "boss spectate timeout" && warnings == 1 && penalties == 1 &&
              curtains == 1 && respawns == 0 && reloads == 0 &&
              warning.find("boss spectate timeout") != std::string::npos,
              "stuck boss must respawn after 90 seconds with one warning");
        TickBbscSpectate();
        Check(warnings == 1 && penalties == 1, "timeout must not repeat after release");

        Reset();
        now = bbsc_spectate_since_ms + 90'000;
        TickBbscSpectate();
        Check(bbsc_spectate_active && warnings == 0 && curtains == 0,
              "exactly 90 seconds must retain the specified strict timeout boundary");
        ++now;
        TickBbscSpectate();
        Check(!bbsc_spectate_active && warnings == 1, "first tick past boundary must respawn");

        Reset();
        now = bbsc_spectate_since_ms + 30'000;
        bbsc_boss_killed_ms = now;
        TickBbscSpectate();
        now += BbscBossClearedSettleMs - 1;
        TickBbscSpectate();
        Check(bbsc_spectate_active && curtains == 0 && warnings == 0,
              "boss kill must retain its two-second settle");
        ++now;
        TickBbscSpectate();
        Check(!bbsc_spectate_active && respawn_reason == "boss killed" && warnings == 0,
              "normal boss kill must respawn without timeout");

        Reset();
        bbsc_boss_killed_ms = now;
        TickBbscSpectate();
        Check(bbsc_spectate_active && warnings == 0, "boss kill must take priority over timeout");
        now += BbscBossClearedSettleMs;
        TickBbscSpectate();
        Check(respawn_reason == "boss killed" && warnings == 0,
              "boss settle crossing timeout must remain on boss-killed path");

        Reset();
        bbsc_death_mode = BbscDeathMode::RespawnNow;
        TickBbscSpectate();
        Check(!bbsc_spectate_active && respawn_reason == "no boss fight" && warnings == 0,
              "non-boss death must retain immediate respawn");

        Reset();
        living = false;
        TickBbscSpectate();
        Check(bbsc_spectate_active && curtains == 0 && warnings == 0,
              "guest wipe must wait for the host");
        now += BbscSpectateReleaseDebounceMs;
        TickBbscSpectate();
        Check(bbsc_spectate_active && bbsc_spectate_saw_wipe && warnings == 0,
              "guest wipe must retain host-reload grace period");
        now = bbsc_spectate_alone_since_ms + BbscGuestWaitHostMs;
        TickBbscSpectate();
        Check(!bbsc_spectate_active && reloads == 1 &&
              reload_reason == "no living partner and no host reload came" && warnings == 0,
              "guest wipe must retain existing no-host-reload fallback");

        Reset();
        session_state = 3; living = false;
        TickBbscSpectate();
        now += BbscSpectateReleaseDebounceMs;
        TickBbscSpectate();
        Check(reloads == 1 && reload_reason == "no living partner" && warnings == 0,
              "host wipe must retain nobody-alive release");

        Reset();
        session_state = 0;
        TickBbscSpectate();
        Check(reloads == 1 && reload_reason == "session ended" && warnings == 0,
              "session end must retain normal release");

        Reset();
        character = 4;
        TickBbscSpectate();
        Check(respawn_reason == "a load ended the boss fight" && warnings == 0,
              "character reload must retain priority");

        Reset();
        bbsc_spectate_saw_wipe = true;
        TickBbscSpectate();
        now += BbscWipeHostSettleMs;
        TickBbscSpectate();
        Check(warps == 1 && bbsc_spectate_active && warnings == 0 && curtains == 0,
              "host reload after wipe must retain follow warp");

        Reset();
        curtain_ok = false;
        TickBbscSpectate();
        Check(!bbsc_spectate_active && respawns == 1 && reloads == 0 && warnings == 1,
              "timeout must use existing in-place respawn fallback");

        Reset();
        curtain_ok = respawn_ok = false;
        TickBbscSpectate();
        Check(!bbsc_spectate_active && reloads == 1 &&
              reload_reason == "respawn in place failed" && warnings == 1,
              "failed timeout respawn must retain normal reload fallback");

        Reset();
        bbsc_request_death_reload = true;
        TickBbscSpectate();
        Check(reloads == 1 && reload_reason == "no living partner at the death step" &&
              warnings == 0, "pending death reload must retain priority");

        Reset();
        bbsc_spectate_active = false;
        TickBbscSpectate();
        Check(curtains == 0 && reloads == 0 && warnings == 0, "inactive spectate must do nothing");

        Reset();
        bbsc_spectate_releasing = true;
        TickBbscSpectate();
        Check(curtains == 0 && reloads == 0 && warnings == 0, "releasing spectate must do nothing");

        Reset();
        now = bbsc_spectate_since_ms;
        SetBossSlot(0, boss_character);
        NoteBbscBossBar();
        Check(!BossFightActive() && bbsc_boss_bar_seen_ms == 0,
              "dead boss gauge must not refresh live-bar memory");
        GuestRegisterSnapshot registers{};
        BbscSpectateDeathHook(1, &registers);
        Check(bbsc_death_mode == BbscDeathMode::RespawnNow && skipped_dead_boss_logs == 1,
              "HP=0 boss must select immediate respawn and log the skipped slot");
        TickBbscSpectate();
        Check(!bbsc_spectate_active && respawn_reason == "no boss fight" && curtains == 1 &&
              warnings == 0 && reloads == 0 && skipped_dead_boss_logs == 1,
              "dead boss must respawn immediately without spectating or repeated skip logs");

        Reset();
        now = bbsc_spectate_since_ms;
        boss_hp = 100;
        SetBossSlot(2, boss_character);
        NoteBbscBossBar();
        Check(BossFightActive() && bbsc_boss_bar_seen_ms == now,
              "living boss in the third slot must refresh live-bar memory");
        BbscSpectateDeathHook(1, &registers);
        TickBbscSpectate();
        Check(bbsc_death_mode == BbscDeathMode::Spectate && bbsc_spectate_active &&
              curtains == 0 && warnings == 0 && reloads == 0 && skipped_dead_boss_logs == 0,
              "HP>0 boss must preserve the normal spectate path");
        boss_gauge.fill(0xFF);
        Check(BbscBossFightAtDeath(), "cleared gauge must retain recent live-bar memory");
        now += BbscBossBarMemoryMs + 1;
        Check(!BbscBossFightAtDeath(), "live-bar memory must still expire after ten seconds");

        std::cout << "PASS: " << scenarios << " boss spectate scenarios\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
'''


def run(source, compiler=None):
    text = Path(source).read_text('utf-8')
    constants = re.findall(
        r'^constexpr s64 Bbsc(?:SpectateSafety|SpectateReleaseDebounce|GuestWaitHost|'
        r'WipeHostSettle|BossClearedSettle|BossSpectateTimeout|BossBarMemory)Ms = [^;\n]+;', text, re.M)
    constants += re.findall(r'^constexpr u64 BossGaugePointerOffset = [^;\n]+;', text, re.M)
    enum = function(text, 'enum class BbscDeathMode : u8 {') + ';'
    boss_functions = '\n'.join(function(text, marker) for marker in (
        'bool BossFightActive() {', 'void NoteBbscBossBar() {', 'bool BbscBossFightAtDeath() {',
        'void PS4_SYSV_ABI BbscSpectateDeathHook('))
    tick = function(text, 'void TickBbscSpectate() {')
    compiler = compiler or shutil.which('clang++') or r'C:\bbsc-dev\msys64\clang64\bin\clang++.exe'
    env = dict(os.environ, PATH=str(Path(compiler).resolve().parent) + os.pathsep + os.environ.get('PATH', ''))
    with tempfile.TemporaryDirectory(prefix='bbsc-spectate-test-') as temp:
        cpp = Path(temp) / 'boss_spectate.cpp'
        exe = Path(temp) / ('boss_spectate.exe' if os.name == 'nt' else 'boss_spectate')
        cpp.write_text(HEAD + '\n'.join(constants) + '\n' + enum + FIXTURE + boss_functions + tick + CASES,
                       encoding='utf-8', newline='\n')
        subprocess.run([compiler, '-std=c++23', '-O1', str(cpp), '-o', str(exe)], env=env, check=True)
        subprocess.run([str(exe)], env=env, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='prepared bloodborne_re_zh_cn.cpp')
    parser.add_argument('--compiler')
    args = parser.parse_args()
    run(args.source, args.compiler)
