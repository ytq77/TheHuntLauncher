"""Extract hardened game callbacks into synthetic native regression fixtures.

Usage: python prepare_game_hardening_regression.py CORE_CPP OUTPUT_DIRECTORY
Only the kit's absolute native event setter is redirected to a synthetic stub;
the callback bodies, inventory order, Lua behavior and team logic remain intact.
"""
import argparse
from pathlib import Path
import re

from prepare_npc_regression import block

ROOT = Path(__file__).resolve().parents[1]


def function(text, marker):
    if text.count(marker) != 1:
        raise ValueError('Unexpected regression marker: ' + marker)
    start = text.index(marker)
    first = text.index('{', start)
    depth = 0
    tokens = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|(?<![\w])\'(?:\\.|[^\'\\\r\n])*\'|[{}]')
    for token in tokens.finditer(text, first):
        value = token.group()
        depth += (value == '{') - (value == '}')
        if depth == 0:
            return text[start:token.end()]
    raise ValueError('Unclosed regression function: ' + marker)


def generate(source, destination):
    text = Path(source).read_text('utf-8')
    target = Path(destination)
    if text.count('struct BbscPvpTeamSnapshot {') != 1:
        raise ValueError('Run prepare_game_hardening before regression extraction')
    trace = function(text, 'void PS4_SYSV_ABI TraceEntry(')
    dispatch_start = trace.index('    if (site.kind == TraceKind::LuaSessionEvent) {')
    # Select the exact pre-sampling dispatch, without reimplementing its condition.
    dispatch_end = trace.index('\n    }', dispatch_start) + len('\n    }')
    dispatch = trace[dispatch_start:dispatch_end]
    sample_start = trace.index('    const u64 hit = site_hits[tag].fetch_add(')
    sample_end = trace.index('    const u64 sequence =', sample_start)
    if not dispatch_start < sample_start < trace.index('if (!capture_file)'):
        raise ValueError('Lua behavior must precede sampling and capture availability checks')
    if trace.count('HandleBbscLuaSessionEvent(') != 1:
        raise ValueError('Lua behavior dispatched more than once')
    reload_hook = '''    if (site.offset == 0x0178D9C0) {
        bbsc_character_reload_generation.fetch_add(1);
        PrepareBbscSummonedType();'''
    if trace.count(reload_hook) != 1 or text.count('bbsc_character_reload_generation.fetch_add(1)') != 1:
        raise ValueError('Generation must advance only at the pinned native ReloadFinish hook')
    kit = function(text, 'void TickBbscDuelBellGive() {')
    endpoint = 'image_base + 0x0132E5A0'
    if kit.count(endpoint) != 1:
        raise ValueError('Unexpected native flag setter')
    kit = kit.replace(endpoint, 'image_base + KitSetEventFlagOffset')
    relay = Path(source).parents[2] / 'mod/bbsc_relay.cpp'
    relay_text = relay.read_text('utf-8')
    if relay_text.count('Core::Bloodborne::OnBbscPvpPacket(frame[5], frame[6], sender);') != 1:
        raise ValueError('Prepared relay must propagate existing sender metadata to PvP')
    values = {
        'relay': {'// ACTUAL_RELAY_DISPATCH': function(relay_text, 'void Dispatch(const std::vector<u8>& wrapped) {')},
        'pvp': {'// RELAY_AUTH_GUARD': (ROOT/'patches/bbsc_relay_auth_guard.h').read_text('utf-8'),
                '// RELAY_DECLARATIONS': block(text, 'struct BbscRelayHostSeen {', ';'),
                '// CALLBACKS': '\n'.join([
            block(text, 'struct BbscPvpTeamSnapshot {', ';'),
            *(function(text, marker) for marker in (
                'void SelectBbscRelayHost(', 'u64 BbscRelayNativeHost() {',
                'u32 BbscRelayHostForPackets() {', 'void TickBbscRelayHostLock() {',
                'bool BbscPvpRequireRoomBinding() {', 'void ClearBbscPvpHostState() {',
                'bool BbscPvpTeamIdentity(', 'void SetBbscPvp(', 'void ApplyBbscPvpTeams() {',
                'void TickBbscPvp() {', 'void OnBbscPvpPacket(')),
        ])},
        'kit': {'// DECLARATIONS': block(text, 'struct BbscKitItem {', ';') + '\n' +
                block(text, 'constexpr BbscKitItem BbscKit[] =', ';'), '// CALLBACKS': kit},
        'lua': {'// CALLBACKS': function(text, 'void HandleBbscLuaSessionEvent(') + '\n' +
                function(text, 'void WriteLuaSessionEvent('),
                '// SAVE_GUARD_STAGE': 'constexpr bool SaveJoinGuard = ' +
                ('true;' if 'invocation->skip_original = 1;' in dispatch else 'false;'),
                '// ACTUAL_DISPATCH_AND_SAMPLING': dispatch + '\n' + trace[sample_start:sample_end]},
    }
    rendered = {}
    for name, replacements in values.items():
        template = (ROOT / f'tests/game_hardening_{name}.cpp.in').read_text('utf-8')
        for marker, value in replacements.items():
            if template.count(marker) != 1:
                raise ValueError('Unexpected game fixture marker: ' + marker)
            template = template.replace(marker, value, 1)
        rendered[name] = template
    target.mkdir(parents=True, exist_ok=True)
    for name, template in rendered.items():
        (target / f'game_hardening_{name}.cpp').write_text(template, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    generate(args.source, args.destination)
