#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
root="$PWD"
src="$root/.build/bbsc/emulator"
port="${BBSC_PORT_SOURCE:-$root/.build/bbport}"
out="$root/.build/mod"
mkdir -p "$out"
flags=(-std=c++23 -O2 -g -fms-extensions -DNOMINMAX -DWIN32_LEAN_AND_MEAN
       -D_CRT_SECURE_NO_WARNINGS -DBBSC_MOD -DFMT_HEADER_ONLY
       -I"$src/mod/shim" -I"$src/src" -I"$port/gpu/third_party/xbyak")
objects=()
for name in bbsc_mod bbsc_hooks bbsc_relay bbsc_channel bbsc_http bbsc_discord; do
    source="$src/mod/$name.cpp"
    if [[ "$name" == bbsc_mod ]]; then source="$src/mod/bbsc_mod_zh_cn.cpp"; fi
    clang++ "${flags[@]}" -c "$source" -o "$out/$name.o"
    objects+=("$out/$name.o")
done
for name in bbsc_memscan bloodborne_re_zh_cn; do
    clang++ "${flags[@]}" -c "$src/src/core/$name.cpp" -o "$out/$name.o"
    objects+=("$out/$name.o")
done
clang++ -shared "${objects[@]}" -o "$out/bbsc.dll" -lws2_32 -lwinhttp -luser32 -lbcrypt
clang++ "${flags[@]}" "$src/mod/bbsc_launch_zh_cn.cpp" -o "$out/bbsc-launch.exe" \
    -municode -mwindows -luser32 -lcomdlg32
# Exercise the real safe scanner against changing page permissions and region boundaries.
clang++ -std=c++23 -fms-extensions -I"$src/src" \
    '-DBBSC_SCAN_SOURCE="../patches/bbsc_memscan.cpp"' tests/test_memscan.cpp -o "$out/test-memscan.exe"
"$out/test-memscan.exe"
"$out/test-memscan.exe" race
clang++ -std=c++23 tests/test_banner_diagnostics.cpp -o "$out/test-banner.exe"
"$out/test-banner.exe"
clang++ -std=c++23 tests/test_insight_refund.cpp -o "$out/test-insight-refund.exe"
"$out/test-insight-refund.exe"
clang++ -std=c++23 tests/test_empty_host_cancel.cpp -o "$out/test-empty-host-cancel.exe"
"$out/test-empty-host-cancel.exe"
clang++ -std=c++23 tests/test_empty_host_cancel_runtime.cpp -o "$out/test-empty-host-cancel-runtime.exe"
"$out/test-empty-host-cancel-runtime.exe"
clang++ -std=c++23 tests/test_personal_rewards.cpp -o "$out/test-personal-rewards.exe"
"$out/test-personal-rewards.exe"
python scripts/prepare_personal_rewards_regression.py "$src/src/core/bloodborne_re_zh_cn.cpp" "$out/test-personal-rewards-runtime.cpp"
clang++ -std=c++23 -I"$src/src/core" "$out/test-personal-rewards-runtime.cpp" -o "$out/test-personal-rewards-runtime.exe"
"$out/test-personal-rewards-runtime.exe"
python scripts/prepare_npc_regression.py "$src/src/core/bloodborne_re_zh_cn.cpp" "$out/test-npc-prejoin.cpp"
clang++ -std=c++23 -DEXPECT_FIXED "$out/test-npc-prejoin.cpp" -o "$out/test-npc-prejoin.exe"
"$out/test-npc-prejoin.exe"
clang++ -std=c++23 tests/test_save_proof.cpp -o "$out/test-save-proof.exe" -lbcrypt
mkdir -p "$out/save-proof-fixture"
"$out/test-save-proof.exe" "$out/save-proof-fixture"
clang++ "${flags[@]}" tests/test_home_save_proof.cpp -o "$out/test-home-save-proof.exe" -lbcrypt
"$out/test-home-save-proof.exe" "$out/home-save-proof-$(date +%s%N)"
clang++ -std=c++23 tests/test_slot_guard.cpp -o "$out/test-slot-guard.exe"
for mode in noaccess unmapped null guest already protect-failure write-failure loader-change normal readonly race-protect race-decommit; do
    "$out/test-slot-guard.exe" "$mode"
done
clang++ "${flags[@]}" tests/test_menu_quit.cpp "$out/bbsc_hooks.o" -o "$out/test-menu-quit.exe"
"$out/test-menu-quit.exe" valid
"$out/test-menu-quit.exe" mismatch
python scripts/test_world_recovery_native.py
python scripts/prepare_save_bridge.py .build/bbsc .build/save-bridge
bridge="$root/.build/save-bridge"
clang++ "${flags[@]}" -I"$src/src/core" -I"$src/mod" -c "$bridge/bbsc_mod_save.cpp" -o "$bridge/bbsc_mod.o"
clang++ "${flags[@]}" -I"$src/src/core" -c "$bridge/bloodborne_re_save.cpp" -o "$bridge/bloodborne_re.o"
clang++ -shared "$bridge/bbsc_mod.o" "$bridge/bloodborne_re.o" \
    "$out/bbsc_hooks.o" "$out/bbsc_relay.o" "$out/bbsc_channel.o" "$out/bbsc_http.o" \
    "$out/bbsc_discord.o" "$out/bbsc_memscan.o" -o "$bridge/save-bridge.dll" -lws2_32 -lwinhttp -luser32 -lbcrypt
sha256sum "$out/bbsc.dll" "$out/bbsc-launch.exe" "$bridge/save-bridge.dll"
