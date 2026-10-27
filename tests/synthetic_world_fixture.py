"""Synthetic pinned-format save; contains no personal character or game assets."""
import struct
import offline_save as save


def fixture_pair():
    data = bytearray(0x140000)
    name = 0xC0F7
    face = name+34497
    end = name+68545+136
    for offset, value in {0: 0x41, 12: 84, 16: end+157, 20: 84, 24: end-84,
                          28: end+158, 36: end, 40: 150, 44: end+150, 48: 8}.items():
        struct.pack_into('<I', data, offset, value)
    struct.pack_into('<III', data, 52, end+241, 150001, end+241+150001)
    data[name:name+12] = 'Hunter'.encode('utf-16-le')
    for _, relative, count in save.AREAS:
        for index in range(count):
            struct.pack_into('<IIII', data, name+relative+4+index*16, 0, 0xffffffff, 0, 0)
    goods = save.KIT+(120,)
    struct.pack_into('<I', data, name+453, len(goods))
    for index, number in enumerate(goods):
        struct.pack_into('<IIII', data, name+457+index*16,
                         0xb0000000|number, 0x40000000|number, 1, 64+index)
    data[face:face+4] = b'FACE'
    bank = end+241
    for flag in (1104, 1160, 1185, 1220, 1304, 9800, 9801):
        group, bit = divmod(flag, 1000)
        data[bank+group*625+bit//8] |= 0x80>>(bit%8)
    before = bytes(data)
    for old, after in ((1160, 1164), (1220, 1224)):
        for flag, on in ((old, False), (after, True)):
            group, bit = divmod(flag, 1000)
            offset, mask = bank+group*625+bit//8, 0x80>>(bit%8)
            data[offset] = data[offset] | mask if on else data[offset] & ~mask
    return before, bytes(data)
