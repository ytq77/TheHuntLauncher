"""DGRAM_P2P bind needs a nonzero physical port even though transport is shared."""

def apply(text):
    before = '    bind_to.sin_family = OrbisAfInet;\n    bind_to.sin_vport = '
    after = ('    bind_to.sin_family = OrbisAfInet;\n'
             '    // bbport 1.9.7 validates sin_port for DGRAM_P2P; this call only\n'
             '    // binds a virtual port on its existing transport, not a new UDP port.\n'
             '    bind_to.sin_port = static_cast<u16>((3658 >> 8) | (3658 << 8));\n'
             '    bind_to.sin_vport = ')
    if text.count(before) != 1:
        raise ValueError('Unexpected P2P bind address construction')
    return text.replace(before, after, 1)
