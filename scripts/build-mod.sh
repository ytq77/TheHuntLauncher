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
    clang++ "${flags[@]}" -c "$src/mod/$name.cpp" -o "$out/$name.o"
    objects+=("$out/$name.o")
done
for name in bbsc_memscan bloodborne_re_zh_cn; do
    clang++ "${flags[@]}" -c "$src/src/core/$name.cpp" -o "$out/$name.o"
    objects+=("$out/$name.o")
done
clang++ -shared "${objects[@]}" -o "$out/bbsc.dll" -lws2_32 -lwinhttp -luser32
clang++ "${flags[@]}" "$src/mod/bbsc_launch.cpp" -o "$out/bbsc-launch.exe" \
    -municode -mwindows -luser32 -lcomdlg32
# Exercise the real safe scanner against changing page permissions and region boundaries.
clang++ -std=c++23 -fms-extensions -I"$src/src" \
    '-DBBSC_SCAN_SOURCE="../patches/bbsc_memscan.cpp"' tests/test_memscan.cpp -o "$out/test-memscan.exe"
"$out/test-memscan.exe"
"$out/test-memscan.exe" race
sha256sum "$out/bbsc.dll" "$out/bbsc-launch.exe"
