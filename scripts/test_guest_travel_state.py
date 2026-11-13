"""Exercise real Dream restore, baseline publication, follow and private join."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from prepare_game_hardening_regression import function

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    'follow-no-op', 'follow-overlay', 'follow-purchase', 'follow-roundtrip', 'baseline-write-failure',
    'request-write-failure', 'initial-direct', 'initial-dream', 'initial-write-failure',
    'initial-callback-change', 'slot-changed', 'lineage-changed', 'no-token',
    'title-exit', 'waiting-ack', 'incomplete-baseline', 'sync-disabled',
    'legacy-fallback', 'recovery-disabled', 'no-owner', 'load-generation-change',
    'follow-host-walked-next-block',
    'follow-native-landing-delayed', 'follow-native-landing-stale-ticket',
    'follow-native-landing-room-changed', 'follow-native-landing-role-changed',
    'follow-native-landing-actor-changed', 'follow-native-landing-reloaded',
    'follow-native-landing-map-changed', 'follow-native-landing-expired',
)


def generate(source, destination, template=None):
    source = Path(source).resolve()
    core = source.parent
    text = source.read_text('utf-8')
    recovery = (core / 'bbsc_world_recovery.inc').read_text('utf-8')
    helper = (core / 'bbsc_private_join_recovery.inc').read_text('utf-8')
    guard_path = core / 'bbsc_load_protection.inc'
    guard = guard_path.read_text('utf-8')
    manifest = []

    def actual(body, marker, origin, suffix=''):
        result = function(body, marker) + suffix
        manifest.append({'source': str(origin), 'marker': marker,
                         'sha256_lf': hashlib.sha256(result.encode()).hexdigest()})
        return result

    scalars = (
        'BbscWorldGroupBytes', 'BbscWorldGroupCount', 'BbscWorldAppliedGroups',
        'BbscSnapshotMaxGroups', 'BbscDreamStateMap', 'BbscDreamSoldGroup',
        'BbscOwnWorldMagic', 'WorldFlagManPointerOffset', 'BbscWorldBlockFreshMs',
        'BbscOwnWorldRecoverMs', 'BbscWorldFlagsFreshMs', 'BbscWorldFlagsWaitMs',
        'BbscFollowStaleMs', 'BbscFollowSettleMs', 'BbscFollowArriveTimeoutMs',
        'BbscFollowMaxAttempts', 'BbscFollowMismatchMs', 'BbscFollowAfterLoadMs',
        'BbscJoinTeleportSettleMs', 'BbscGuestRejoinGraceMs',
    )
    protocol = (core / 'libraries/np/np_signaling/np_signaling_bbsc.h').read_text('utf-8')
    size = re.search(r'inline constexpr size_t kBbscWorldFlagGroupBytes = [^;]+;', protocol).group()
    declarations = ['namespace Libraries::Np::NpSignaling {' + size + '}',
                    actual(text, 'constexpr std::array<u32, 4> BbscWorldFlagKinds', source, ';')]
    for name in scalars:
        matches = re.findall(r'^constexpr \w+ ' + name + r'\s*=[^;]+;', text, re.M)
        if len(matches) != 1:
            raise ValueError('Unexpected travel constant: ' + name)
        declarations.append(matches[0])
    for marker in ('constexpr std::array<u32, 57> BbscDreamStateKeys',
                   'constexpr std::array<u16, 130> BbscDreamShopUnlockFlags'):
        declarations.append(actual(text, marker, source, ';'))
    declarations.append('constexpr size_t BbscDreamGroupCount=BbscDreamStateKeys.size();')
    for marker in ('struct BbscWorldSnapshot {', 'struct BbscDreamOwnState {',
                   'struct BbscFollowMessage {', 'struct BbscFollowSpot {'):
        declarations.append(actual(text, marker, source, ';'))
    declarations.append(actual(recovery, 'struct BbscRecoveryRow {', core / 'bbsc_world_recovery.inc', ';'))
    declarations.append(re.search(r'constexpr u32 BbscRecoveryCapture =[\s\S]*?;', recovery).group())

    markers = (
        'bool IsUsablePackedMap(u32 packed_region) {',
        'u32 BbscWorldGroupKey(u32 packed_map, size_t index) {',
        'bool BbscIsMapFlagGroup(u32 key) {',
        'void KeepBbscOwnWorldGroup(u32 key, const u8* live) {',
        'const u8* BbscOwnDreamBaseline(u32 key, const u8* live) {',
        'bool BbscOwnWorldCaptureStillCurrent() {', 'bool BbscOwnWorldReadyForGuest() {',
        'bool BbscHostBlockSnapshot(u32 packed_map, BbscWorldSnapshot& out) {',
        'bool BbscHostWorldFlagsReady(u32 packed_map) {',
        'bool ApplyBbscHostWorldFlags(u32 packed_map, std::string_view why) {',
        'void LogBbscHostWorldFlagsDiff(u32 packed_map, std::string_view why) {',
        'size_t KeepBbscOwnDreamShopFlags(size_t index, u8* live) {',
        'bool BbscHostDreamStateReady() {',
        'void ApplyBbscHostDreamState(std::string_view why) {',
        'void NoteBbscOwnDreamRestored(u32 key, const u8* live) {',
        'void RestoreBbscOwnDreamState(std::string_view why) {',
        'bool BbscGuestLootActive() {', 'void TickBbscDreamStateRestore() {',
        'void ApplyBbscHostWorldFlagsAtLoad() {', 'void WriteBbscOwnWorldFile() {',
        'bool BbscHomeForOwnWorld() {', 'void TickBbscOwnWorld() {',
        'BbscFollowSpot BbscFollowSpotOf(const BbscFollowMessage& host) {',
    )
    functions = [actual(recovery, marker, core / 'bbsc_world_recovery.inc') for marker in (
        'bool BbscWorldRecoveryJournalFrozen() {', 'bool BbscWorldRecoveryWaiting() {',
        'std::filesystem::path BbscRecoveryFolder() {',
        'bool BbscRecoveryWriteFile(', 'bool BbscRecordWorldRecovery(u32 mode) {')]
    functions += [actual(text, marker, source) for marker in markers]
    functions += [actual(text, 'bool RequestBbscSessionWarp(', source)]
    functions += [actual(guard, marker, guard_path) for marker in (
        'bool BbscLoadProtectionRoomChanged(', 'bool BbscLoadProtectionContextMatches(',
        'BbscFollowLanding MarkBbscFollowNativeLanding(')]
    follow = actual(text, 'void TickBbscFollowHost() {', source)
    rendered = (Path(template) if template else ROOT / 'tests/guest_travel_state.cpp.in').read_text('utf-8')
    for marker, value in (
        ('// ACTUAL_DECLARATIONS', '\n'.join(declarations)),
        ('// ACTUAL_FUNCTIONS', '\n\n'.join(functions)),
        ('// ACTUAL_PRIVATE_JOIN', helper), ('// ACTUAL_FOLLOW', follow),
    ):
        if rendered.count(marker) != 1:
            raise ValueError('Unexpected travel fixture marker: ' + marker)
        rendered = rendered.replace(marker, value, 1)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    cpp = destination / 'guest_travel_state.cpp'
    cpp.write_text(rendered, encoding='utf-8', newline='\n')
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        source, guard_path, core / 'bbsc_private_join_recovery.inc', core / 'bbsc_world_recovery.inc')}
    (destination / 'extraction-manifest.json').write_text(json.dumps(
        {'input_sha256': hashes, 'extractions': manifest,
         'scope': 'Unaltered function bodies; synthetic native memory/callbacks. Real baseline and recovery-request serialization and filesystem I/O in fresh fixture directories.'},
        indent=2), encoding='utf-8')
    return cpp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('core', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--compiler', default='clang++')
    parser.add_argument('--template', type=Path)
    args = parser.parse_args()
    cpp = generate(args.core, args.output, args.template)
    exe = cpp.with_suffix('.exe' if os.name == 'nt' else '')
    compiled = subprocess.run([args.compiler, '-std=c++23', '-DFMT_HEADER_ONLY', '-O1',
                               str(cpp), '-o', str(exe)], capture_output=True, text=True)
    (args.output / 'compile.log').write_text(compiled.stdout + compiled.stderr, encoding='utf-8')
    compiled.check_returncode()
    import tempfile
    results = []
    for scenario in SCENARIOS:
        folder = Path(tempfile.mkdtemp(prefix=scenario + '-', dir=args.output))
        result = subprocess.run([str(exe.resolve()), scenario, str(folder)], capture_output=True, text=True)
        print(result.stdout + result.stderr, end='', flush=True)
        results.append({'scenario': scenario, 'exit_code': result.returncode,
                        'stdout': result.stdout, 'stderr': result.stderr, 'fixture': str(folder)})
    (args.output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    if any(row['exit_code'] for row in results):
        raise SystemExit('Guest travel regression failed; see results.json')
    print(f'PASS: {len(results)} actual guest travel scenarios; baseline and request publication precede native load')


if __name__ == '__main__':
    main()
