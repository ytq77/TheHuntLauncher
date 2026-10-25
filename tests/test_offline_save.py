"""Synthetic format fixtures; no user's saves are stored in the repository."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import offline_save as save


def fixture(kit=False):
    data = bytearray(0x140000)
    name = 0xC0F7
    face = name + 34497
    end = name + 68545 + 136
    struct.pack_into('<III', data, 52, end + 241, 150001, end + 241 + 150001)
    for offset, value in {0:0x41, 12:84, 16:end+157, 20:84, 24:end-84,
                          28:end+158, 36:end, 40:150, 44:end+150, 48:8}.items():
        struct.pack_into('<I', data, offset, value)
    data[name:name+12] = 'Hunter'.encode('utf-16-le')
    for area, delta, capacity in save.AREAS:
        for index in range(capacity):
            struct.pack_into('<IIII', data, name+delta+4+index*16, 0, 0xFFFFFFFF, 0, 0)
    goods = (save.KIT if kit else save.ORIGINAL) + (120,)
    struct.pack_into('<I', data, name+453, len(goods))
    for index, goods_id in enumerate(goods):
        struct.pack_into('<IIII', data, name+457+index*16,
                         0xB0000000|goods_id, 0x40000000|goods_id, 1, 64+index)
    for offset in range(face-236, face-92, 8):
        struct.pack_into('<II', data, offset, 0xFFFFFFFF, 0)
    for offset in range(face-84, face-36, 8):
        struct.pack_into('<II', data, offset, 0, 0xFFFFFFFF)
    for delta in (-92,-88):
        struct.pack_into('<I', data, face+delta, 0xFFFFFFFF)
    if kit:
        for delta, index in ((-132,64),(-124,65),(-88,65)):
            struct.pack_into('<I', data, face+delta, index)
        struct.pack_into('<II', data, face-84, 0xB0000000|save.KIT[2], 66)
        struct.pack_into('<II', data, face-76, 0xB0000000|120, 69)
    data[face:face+4] = b'FACE'
    return bytes(data)


class OfflineSave(unittest.TestCase):
    def test_three_originals_preserve_ownership_without_granting_sinister_bell(self):
        baseline = bytearray(fixture())
        role = save.parse(baseline)
        row = role.goods(225)[0]
        struct.pack_into('<IIII',baseline,row.offset,0,0xFFFFFFFF,0,0)
        struct.pack_into('<I',baseline,role.username+453,save.u32(baseline,role.username+453)-1)
        source = fixture(True)
        output,patches = save.convert(source,bytes(baseline))
        after = save.parse(output)
        save.expect_goods(after,(200,205,111))
        self.assertEqual(save.original_ownership(save.parse(baseline)),(200,205,111))
        self.assertEqual(after.count(225),0)
        self.assertEqual(save.u32(source,save.parse(source).username+453)-2,
                         save.u32(output,after.username+453))
        for number in (200,205,111):
            row = after.goods(number)[0]
            self.assertEqual(list(save.occurrences(output[:after.primary_end],struct.pack('<I',row.handle))),[row.offset])
        self.assertEqual(len([p for p in patches if p['purpose']=='remove-extra-kit-record']),2)
        untouched = {i for p in patches for i in range(p['offset'],p['offset']+p['length'])}
        self.assertTrue(all(a==b or i in untouched for i,(a,b) in enumerate(zip(source,output))))

    def test_every_missing_bell_combination_uses_baseline_ownership(self):
        for mask in range(16):
            with self.subTest(mask=mask):
                baseline = bytearray(fixture())
                role = save.parse(baseline)
                owned = tuple(n for i,n in enumerate(save.ORIGINAL) if mask & (1 << i))
                for number in save.ORIGINAL:
                    if number not in owned:
                        struct.pack_into('<IIII',baseline,role.goods(number)[0].offset,0,0xFFFFFFFF,0,0)
                struct.pack_into('<I',baseline,role.username+453,len(owned)+1)
                if not owned:
                    with self.assertRaisesRegex(save.Unsupported,'未找到任何'):
                        save.convert(fixture(True),bytes(baseline))
                    continue
                output,patches = save.convert(fixture(True),bytes(baseline))
                result = save.parse(output)
                save.expect_goods(result,owned)
                self.assertEqual(save.original_ownership(save.parse(baseline)),owned)
                self.assertEqual(save.u32(output,result.username+453),len(owned)+1)
                self.assertEqual(len([p for p in patches if p['purpose']=='remove-extra-kit-record']),5-len(owned))
                self.assertEqual(result.count(120),1)
                for number in owned:
                    row = result.goods(number)[0]
                    self.assertEqual(list(save.occurrences(output[:result.primary_end],struct.pack('<I',row.handle))),[row.offset])

    def test_both_bars_cleared_originals_inventory_only_other_item_preserved(self):
        source, ordinary = fixture(True), fixture()
        output, patches = save.convert(source, ordinary)
        role = save.parse(output)
        save.expect_goods(role, save.ORIGINAL)
        for delta in (-132,-124,-88):
            self.assertEqual(save.u32(output, role.face+delta), 0xFFFFFFFF)
        self.assertEqual(output[role.face-84:role.face-76], struct.pack('<II',0,0xFFFFFFFF))
        self.assertEqual(output[role.face-76:role.face-68], source[role.face-76:role.face-68])
        allowed = {i for p in patches for i in range(p['offset'],p['offset']+p['length'])}
        self.assertTrue(all(a==b or i in allowed for i,(a,b) in enumerate(zip(source,output))))

    def test_stale_copy_outside_header_range_is_preserved(self):
        source = bytearray(fixture(True))
        end = save.parse(source).primary_end
        source[0xB0000:0xB0000+end] = source[:end]
        output,_ = save.convert(bytes(source),fixture())
        self.assertEqual(output[0xB0000:],source[0xB0000:])

    def test_bad_count_or_role_rejected(self):
        source = bytearray(fixture(True))
        struct.pack_into('<I', source, save.parse(source).username+453, 999)
        with self.assertRaises(save.Unsupported): save.convert(bytes(source),fixture())
        baseline = bytearray(fixture())
        baseline[save.parse(baseline).username] ^= 1
        with self.assertRaises(save.Unsupported): save.convert(fixture(True),bytes(baseline))

    def test_missing_item_bad_reference_duplicate_conversion_rejected(self):
        for offset, value in ((save.parse(fixture(True)).face-132,999),
                              (save.parse(fixture(True)).entries[0].offset+8,2)):
            source = bytearray(fixture(True))
            struct.pack_into('<I',source,offset,value)
            with self.assertRaises(save.Unsupported): save.convert(bytes(source),fixture())
        converted,_ = save.convert(fixture(True),fixture())
        with self.assertRaises(save.Unsupported): save.convert(converted,fixture())


if __name__ == '__main__': unittest.main()
