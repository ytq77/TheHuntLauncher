"""Keep the native chest-opening gate out of the guest pickup repair."""


def apply(text):
    before = '''                } else if (off.size() < 256) {
                    off.push_back(ReadValue<u32>(item, 0x20));
'''
    after = '''                } else if ((ReadValue<u16>(item, 0x48) & 0x100) == 0 && off.size() < 256) {
                    // Native treasure creation copies MSB InChest (+0x40) to
                    // item flags bit 0x100 (CUSA03173 1.09, 0x017DCAD7).
                    // A closed chest is intentionally not pickable. Its own
                    // open action must enable it; this periodic repair must not.
                    off.push_back(ReadValue<u32>(item, 0x20));
'''
    if text.count(before) != 1:
        raise ValueError('Unexpected guest pickup repair anchor')
    return text.replace(before, after, 1)
