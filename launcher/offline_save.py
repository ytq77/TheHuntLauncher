"""Restricted byte transform for the pinned CUSA03173 1.09 save layout.

Only the observed carried-kit case is supported. Indexed equipment and selected
quick-item fields were identified by a controlled game-save comparison.
File backups, export and optional replacement are owned by conversion_jobs.
Other layouts/ownership cases are rejected; character progress is never merged.
"""
from dataclasses import dataclass
import struct

ORIGINAL = (200, 205, 225, 111)
KIT = (4016, 4121, 11201, 4313, 11251)
RINGING = (201, 206, 226)
TARGETS = ORIGINAL + KIT + RINGING
AREAS = (('inventory', 453, 1984), ('keys', 32201, 64), ('keys_extra', 33229, 64),
         ('storage', 34737, 1984), ('storage_keys', 66485, 64), ('storage_keys_extra', 67513, 64))


class Unsupported(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Unsupported(message)


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def occurrences(data, pattern):
    start = 0
    while (offset := data.find(pattern, start)) >= 0:
        yield offset
        start = offset + 1


@dataclass(frozen=True)
class Entry:
    area: str
    offset: int
    handle: int
    code: int
    quantity: int
    index: int

    @property
    def goods_id(self):
        if self.handle >> 24 == 0xB0 and self.code >> 24 == 0x40:
            return self.code & 0xFFFFFF
        return None


@dataclass
class Role:
    raw: bytes
    face: int
    username: int
    primary_end: int
    entries: list

    def goods(self, goods_id):
        return [row for row in self.entries if row.goods_id == goods_id]

    def count(self, goods_id, area='inventory'):
        return sum(row.quantity for row in self.goods(goods_id) if row.area == area)


def parse(data):
    require(len(data) == 0x140000, 'Unsupported role size')
    require(u32(data, 0) == 0x41 and u32(data, 12) == 84, 'Unsupported role header')
    # Header declares the live character segment. The remainder of the fixed-size
    # file can contain stale serialized copies, including perfectly valid FACE
    # and inventory records. Never parse or replace those by global matching.
    start, length = u32(data, 20), u32(data, 24)
    primary_end = start + length
    require(start == 84 and primary_end == u32(data, 36) and
            primary_end + u32(data, 40) == u32(data, 44) and
            u32(data, 44) + u32(data, 48) == u32(data, 28) and
            u32(data, 28) == u32(data, 16)+1 and primary_end < len(data),
            'Inconsistent primary section header')
    faces = [p for p in occurrences(data[:primary_end], b'FACE') if p >= 0xF000]
    require(len(faces) == 1, 'Ambiguous/missing appearance anchor')
    face = faces[0]
    username = face - 34497
    require(username > 84 and username + 68545 + 136 == primary_end, 'Invalid section bounds')
    entries = []
    for area, relative, capacity in AREAS:
        header = username + relative
        active = []
        for slot in range(capacity):
            offset = header + 4 + slot * 16
            handle, code, quantity, opaque = struct.unpack_from('<IIII', data, offset)
            if handle == 0:
                require(code in (0, 0xFFFFFFFF) and quantity == 0, 'Malformed empty inventory slot')
                continue
            require(code != 0xFFFFFFFF and quantity > 0, 'Malformed active inventory slot')
            row = Entry(area, offset, handle, code, quantity, opaque & 0xFFF)
            active.append(row)
        require(u32(data, header) == len(active), 'Inventory count does not match its records')
        entries.extend(active)
    return Role(data, face, username, primary_end, entries)


def expect_goods(role, expected):
    for goods_id in TARGETS:
        rows = role.goods(goods_id)
        require(all(row.area == 'inventory' for row in rows),
                f'联机道具 {goods_id} 位于背包以外，本版暂不转换仓库中的目标道具。')
        require(len(rows) == int(goods_id in expected) and
                all(row.quantity == 1 and row.handle == (0xB0000000 | goods_id) for row in rows),
                f'联机道具 {goods_id} 的归属或数量不受支持：预期 {int(goods_id in expected)} 件，'
                f'实际记录 {len(rows)} 条、数量 {[row.quantity for row in rows]}。')


def original_ownership(role):
    owned = tuple(number for number in ORIGINAL if role.goods(number))
    require(bool(owned), '原版备份未找到任何已持有的原版联机道具，无法确认恢复范围；不会自动补发。')
    expect_goods(role, owned)
    return owned


def convert(source, ordinary):
    """Return transformed bytes and exact patch manifest, leaving arguments intact."""
    before, baseline = parse(source), parse(ordinary)
    owned = original_ownership(baseline)
    require(source[before.username:before.username+32] == ordinary[baseline.username:baseline.username+32]
            and source[before.face+4:before.face+239] == ordinary[baseline.face+4:baseline.face+239],
            'Character name/appearance does not match baseline; no role merge is attempted')
    expect_goods(before, KIT)
    kit_rows = sorted([row for row in before.entries if row.goods_id in KIT], key=lambda row: row.offset)
    output = bytearray(source)
    patches = []

    def patch(offset, replacement, purpose):
        old = bytes(output[offset:offset+len(replacement)])
        require(not any(offset < p['offset'] + p['length'] and p['offset'] < offset + len(replacement)
                        for p in patches), 'Overlapping field patches')
        output[offset:offset+len(replacement)] = replacement
        patches.append({'offset': offset, 'length': len(replacement), 'purpose': purpose,
                        'before': old.hex(), 'after': replacement.hex()})

    # Indexed equipment block; do not call all 18 entries quick slots. The game
    # sample changed -132/-124/-116/-108 and selected-item field -88.
    carried = [row for row in before.entries if row.area not in ('storage', 'storage_keys', 'storage_keys_extra')]
    indexes = {row.index: row for row in carried}
    require(len(indexes) == len(carried), 'Duplicate carried item indexes')
    kit_indexes = {row.index for row in kit_rows}
    cleared = []
    for offset in range(before.face-236, before.face-92, 8):
        index, reserved = struct.unpack_from('<II', source, offset)
        require(reserved == 0 and (index == 0xFFFFFFFF or index in indexes),
                'Unsupported indexed equipment slot')
        if index in kit_indexes:
            cleared.append((offset, 4))
            patch(offset, struct.pack('<I', 0xFFFFFFFF), 'clear-kit-indexed-equipment')
    for delta in (-92, -88):
        selected = before.face+delta
        selected_index = u32(source, selected)
        require(selected_index == 0xFFFFFFFF or selected_index in indexes, 'Invalid selected quick item')
        if selected_index in kit_indexes:
            cleared.append((selected, 4))
            patch(selected, struct.pack('<I', 0xFFFFFFFF), 'clear-selected-kit-quick-item')

    # Six personal-effects slots use a handle/index pair instead.
    allowed_handles = {row.offset for row in kit_rows}
    kit_handles = {row.handle: row for row in kit_rows}
    for offset in range(before.face-84, before.face-36, 8):
        handle, index = struct.unpack_from('<II', source, offset)
        if handle not in kit_handles:
            continue
        require(index == kit_handles[handle].index,
                'Personal-effects reference does not match the item index')
        allowed_handles.add(offset)
        cleared.append((offset, 8))
        patch(offset, struct.pack('<II', 0, 0xFFFFFFFF), 'clear-kit-equipment-reference')
    for row in kit_rows:
        for offset in occurrences(source[:before.primary_end], struct.pack('<I', row.handle)):
            require(offset in allowed_handles, 'Unmapped kit equipment reference; more evidence required')
        require(list(occurrences(source[:before.primary_end], struct.pack('<I', row.code))) == [row.offset+4],
                'Unexpected duplicate item code outside its inventory record')

    for row, goods_id in zip(kit_rows, owned):
        patch(row.offset, struct.pack('<III', 0xB0000000 | goods_id, 0x40000000 | goods_id, 1),
              'restore-original-to-inventory-only')
    for removed in kit_rows[len(owned):]:
        patch(removed.offset, struct.pack('<IIII', 0, 0xFFFFFFFF, 0, 0), 'remove-extra-kit-record')
    count_at = before.username + 453
    patch(count_at, struct.pack('<I', u32(source, count_at)-len(KIT)+len(owned)), 'update-carried-record-count')

    result = bytes(output)
    after = parse(result)
    expect_goods(after, owned)
    # Other inventories/items and every non-patch byte must remain identical.
    untouched_before = [(row.area, row.offset, row.handle, row.code, row.quantity, row.index)
                        for row in before.entries if row.goods_id not in KIT]
    untouched_after = [(row.area, row.offset, row.handle, row.code, row.quantity, row.index)
                       for row in after.entries if row.goods_id not in owned]
    require(untouched_before == untouched_after, 'Unrelated inventory entry changed')
    allowed = {offset for p in patches for offset in range(p['offset'], p['offset']+p['length'])}
    require(all(a == b or offset in allowed for offset, (a, b) in enumerate(zip(source, result))),
            'Unexpected byte change')
    for goods_id in KIT:
        require(not list(occurrences(result[:after.primary_end], struct.pack('<I', 0xB0000000 | goods_id))),
                'Kit handle remained after conversion')
    for goods_id in owned:
        restored = after.goods(goods_id)[0]
        require(list(occurrences(result[:after.primary_end], struct.pack('<I', restored.handle))) == [restored.offset],
                'An original bell has an equipment reference; inventory-only result required')
    for offset, size in cleared:
        empty = struct.pack('<I', 0xFFFFFFFF) if size == 4 else struct.pack('<II', 0, 0xFFFFFFFF)
        require(result[offset:offset+size] == empty,
                'An equipment slot was repopulated instead of cleared')
    return result, patches
