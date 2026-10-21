"""Settled records can pass; pending world recovery or missing pickups cannot."""
import struct
import tempfile
import unittest
from pathlib import Path

from test_offline_save import fixture
import offline_aux as aux
import offline_save as save


def record(magic, rows):
    return struct.pack('<III', magic, 125, len(rows)) + b''.join(
        struct.pack('<I', key) + bits for key, bits in rows)


def with_flags(data, groups):
    raw = bytearray(data)
    start = save.parse(raw).primary_end + 241
    struct.pack_into('<III', raw, 52, start, 150001, start + 150001)
    for key, bits in groups:
        offset = start + 125 * aux.item_group_slot(key)
        raw[offset:offset+125] = bits
    return bytes(raw)


class Auxiliary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.side = self.root/'auxiliary'
        self.savedata = self.root/'savedata'
        self.role = self.savedata/'1/CUSA00207/SPRJ0005'
        self.role.mkdir(parents=True)
        # Twelve old pickup bits and one new bit, at the two observed map groups.
        self.rows = [(52800, bytes.fromhex('ff0f') + bytes(123)),
                     (52420, b'\x08' + bytes(124))]
        self.raw = with_flags(fixture(True), self.rows)
        for stem in ('userdata','backup'):
            (self.role/f'{stem}0001').write_bytes(self.raw)

    def put(self, relative, data):
        path = self.side/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_settled_guest_records_and_historical_restore_are_read_only(self):
        guest = self.put('guest-items/taken-save-1.bin', record(0x544C4242,self.rows))
        old = self.put('own-world/own-save-1.bin.restored', record(0x574F4242,[(12304,b'\xff'*125)]))
        before = guest.read_bytes(),old.read_bytes(),(self.role/'userdata0001').read_bytes()
        result = aux.validate(self.side,self.savedata,1)
        self.assertFalse(result['world_flag_writes'])
        loot = result['records'][1]
        self.assertEqual(loot['taken_bits'],13)
        self.assertTrue(all(row['missing_bits']==0 for row in loot['files']))
        self.assertEqual(before,(guest.read_bytes(),old.read_bytes(),(self.role/'userdata0001').read_bytes()))

    def test_pending_own_world_not_ignored_even_when_guest_bits_saved(self):
        self.put('own-world/own-save-1.bin',record(0x574F4242,[(12400,bytes(125))]))
        self.put('own-world/own-save-1.bin.restored',record(0x574F4242,[(12400,bytes(125))]))
        with self.assertRaisesRegex(ValueError,'尚待恢复'):
            aux.validate(self.side,self.savedata,1)

    def test_one_new_pickup_missing_from_either_role_file_is_refused(self):
        self.put('guest-items/taken-save-1.bin',record(0x544C4242,self.rows))
        for stem in ('userdata','backup'):
            with self.subTest(stem=stem):
                path = self.role/f'{stem}0001'
                path.write_bytes(with_flags(fixture(True),self.rows[:1]))
                with self.assertRaisesRegex(ValueError,stem+'0001 尚有 1 个'):
                    aux.validate(self.side,self.savedata,1)
                path.write_bytes(self.raw)

    def test_journal_corruption_unknown_files_and_pending_progress_refused(self):
        valid = record(0x544C4242,self.rows)
        cases = [('guest-items/taken-save-1.bin',valid[:-1]),
                 ('guest-items/taken-save-1.bin',valid+b'x'),
                 ('guest-items/taken-save-1.bin',b'xxxx'+valid[4:]),
                 ('guest-items/taken-save-1.bin',record(0x544C4242,self.rows*2)),
                 ('guest-items/taken-save-1.bin.tmp',valid),
                 ('shared-progress/pending-save-1.bin',b'pending')]
        for name,data in cases:
            with self.subTest(name=name,data=data[:12]):
                path = self.put(name,data)
                with self.assertRaises(ValueError):aux.validate(self.side,self.savedata,1)
                path.unlink()

    def test_other_character_or_foreign_user_ledger_is_not_silently_ignored(self):
        for name in ('taken-save-0.bin','taken-sd1-1.bin'):
            path = self.put('guest-items/'+name,record(0x544C4242,self.rows))
            with self.assertRaises(ValueError):aux.validate(self.side,self.savedata,1)
            path.unlink()

    def test_unknown_flag_layout_and_non_item_group_refused(self):
        self.put('guest-items/taken-save-1.bin',record(0x544C4242,self.rows))
        raw = bytearray(self.raw)
        struct.pack_into('<I',raw,56,150000)
        (self.role/'userdata0001').write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'事件标记区'):
            aux.validate(self.side,self.savedata,1)
        for key in (12400,52000,52430):
            with self.assertRaises(ValueError):aux.item_group_slot(key)

    def test_pinned_group_positions(self):
        # Positions checked against the pinned game's lookup and save samples.
        for key,slot in ((52110,74),(52200,75),(52300,76),(52420,79),(52800,83),
                         (52411,193),(54000,84),(54100,90),(54609,1130)):
            self.assertEqual(aux.item_group_slot(key),slot)


if __name__=='__main__':unittest.main()
