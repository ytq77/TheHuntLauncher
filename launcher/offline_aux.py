"""Validate settled BBSC journals without replaying world state or editing flags.

Pinned CUSA03173 1.09: the event writer at 0x01399DE0 serializes the
1200 x 125-byte persistent flag bank, followed by one state byte. Map item
group indexing is independently derived from 0x013BC710 and checked against
submitted saves. Pending own-world/shared-progress records remain unsupported.
"""
import hashlib
from pathlib import Path
import re
import struct

import offline_save as save

GROUP_BYTES = 125
FLAG_BYTES = 150001
MAP_BLOCKS = ((21,0),(21,1),(22,0),(23,0),(24,0),(24,1),(24,2),(25,0),
              (26,0),(27,0),(28,0),(29,0),(32,0),(33,0),(34,0),(35,0),(36,0))


def journal(data, magic):
    save.require(12 <= len(data) <= 12 + 10000 * 129, '辅助记录长度异常，已停止转换。')
    actual, size, count = struct.unpack_from('<III', data)
    save.require(actual == magic and size == GROUP_BYTES and 0 < count <= 10000 and
                 len(data) == 12 + count * 129, '辅助记录格式不受支持，已停止转换。')
    groups = {}
    for index in range(count):
        offset = 12 + index * 129
        key = save.u32(data, offset)
        save.require(key not in groups, '辅助记录包含重复组，已停止转换。')
        groups[key] = data[offset+4:offset+129]
    return groups


def item_group_slot(key):
    kind, rest = divmod(key, 10000)
    area, rest = divmod(rest, 100)
    block, sub = divmod(rest, 10)
    save.require(kind == 5, '客机拾取记录包含非物品标记，已停止转换。')
    if (area, block) in MAP_BLOCKS:
        index = MAP_BLOCKS.index((area, block))
    elif area == 40 and block == 0:
        index = 11
    elif 41 <= area <= 46 and block == 0:
        index = area - 24
    else:
        raise save.Unsupported('客机拾取记录包含未知地图组，已停止转换。')
    return 73 + 115 * sub + index


def flag_bank(raw):
    role = save.parse(raw)
    start, length, end = struct.unpack_from('<III', raw, 52)
    save.require(length == FLAG_BYTES and start >= role.primary_end and
                 end == start + length and end <= len(raw),
                 '存档事件标记区布局不受支持，已停止转换。')
    return start


def validate(folder, savedata, slot):
    """Check backed-up journals against both role files; never mutate either."""
    folder, savedata = Path(folder), Path(savedata)
    result = []
    for category in ('own-world', 'shared-progress', 'guest-items'):
        root = folder / category
        if not root.exists():
            continue
        for path in sorted(root.rglob('*')):
            if path.is_dir():
                raise save.Unsupported('辅助记录包含未知子目录，已停止转换。')
            relative = path.relative_to(folder).as_posix()
            if category == 'shared-progress':
                raise save.Unsupported('存在共享进度辅助记录，暂不支持离线转换；请保留记录。')
            if category == 'own-world' and re.fullmatch(r'own-save-[0-9]\.bin', path.name):
                raise save.Unsupported('存在尚待恢复的自己世界记录。请用 The Hunt 加载该角色，在自己的世界等候恢复，'
                                       '从游戏菜单正常退出后重试；不要删除 own-world 记录。')
            pattern = (r'own-save-([0-9])\.bin\.restored' if category == 'own-world' else
                       r'taken-save-([0-9])\.bin')
            match = re.fullmatch(pattern, path.name)
            save.require(match is not None, '辅助记录文件名不受支持：' + relative)
            save.require(path.stat().st_size <= 12 + 10000 * 129, '辅助记录过大，已停止转换。')
            data = path.read_bytes()
            groups = journal(data, 0x574F4242 if category == 'own-world' else 0x544C4242)
            record = {'file': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                      'groups': len(groups), 'slot': int(match[1])}
            if category == 'own-world':
                # .restored is historical: replaying it would roll back later progress.
                record['policy'] = 'archive-only; never-replay-restored-world'
            else:
                save.require(int(match[1]) == slot,
                             '客机拾取记录属于另一角色，本版暂不处理多个角色的辅助记录。')
                positions = {key: item_group_slot(key) for key in groups}
                checks = []
                for stem in ('userdata', 'backup'):
                    source = savedata / f'1/CUSA00207/SPRJ0005/{stem}{slot:04}'
                    raw = source.read_bytes()
                    start = flag_bank(raw)
                    missing = 0
                    for key, bits in groups.items():
                        offset = start + GROUP_BYTES * positions[key]
                        current = raw[offset:offset+GROUP_BYTES]
                        missing += sum((old & ~now).bit_count() for old, now in zip(bits, current))
                    advice = ('游戏自带备份仍落后一代，请再加载自己的角色并正常保存退出一次。' if stem == 'backup' else
                              '请先在 The Hunt 回到自己的世界，等待恢复并从游戏菜单正常退出。')
                    save.require(missing == 0,
                                 f'{source.name} 尚有 {missing} 个客机拾取标记未保存。' + advice +
                                 '不会自动补写世界标记。')
                    checks.append({'file': source.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                   'missing_bits': missing})
                record.update(policy='verify-already-persisted; no-flag-writes', files=checks,
                              taken_bits=sum(value.bit_count() for bits in groups.values() for value in bits))
            result.append(record)
    return {'schema': 1, 'records': result, 'world_flag_writes': False}
