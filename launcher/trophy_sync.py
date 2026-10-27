"""Merge BBport's recorded local unlocks, never infer trophies from character saves."""
import hashlib
import json
from pathlib import Path
import uuid

NAME = 'trophies.log'


def read(path):
    # Imported lazily because save_transfer also uses this module.
    from save_transfer import plain_path
    path = plain_path(path)
    try:
        with path.open('rb') as stream:
            before = path.stat()
            raw = stream.read(8 * 1024 * 1024 + 1)
            after = path.stat()
    except FileNotFoundError:
        return {'path': str(path), 'sha256': None, 'records': {}, 'ignored': 0}, None
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('奖杯记录过大，已停止同步；请保留原文件。')
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('读取期间奖杯记录发生变化，请退出游戏后重试。')
    records, ignored = {}, 0
    for line in raw.decode('utf-8-sig', errors='replace').splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if (len(parts) != 2 or not all(p.isascii() and p.isdigit() and len(p) <= 12 for p in parts)
                or not 0 <= int(parts[0]) <= 39 or not 0 < int(parts[1]) < 2**63):
            ignored += 1
            continue
        number, when = map(int, parts)
        key = str(number)
        records[key] = min(records.get(key, when), when)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'records': records, 'ignored': ignored}, raw


def prepare(sources, target):
    target_state, _ = read(target)
    states = [read(path)[0] for path in sources]
    records = dict(target_state['records'])
    for state in states:
        for number, when in state['records'].items():
            records[number] = min(records.get(number, when), when)
    content = ''.join(f'{number} {records[number]}\n' for number in sorted(records, key=int))
    return {'schema': 1, 'sources': states, 'target': target_state,
            'content': content if records else None,
            'summary': {'count': len(records),
                        'added': len(set(records) - set(target_state['records'])),
                        'missing_source_logs': sum(s['sha256'] is None for s in states),
                        'ignored_lines': sum(s['ignored'] for s in [*states, target_state]),
                        'inferred_from_save': False}}


def check(plan, include_target=True):
    for expected in [*plan['sources'], *([plan['target']] if include_target else [])]:
        if read(expected['path'])[0] != expected:
            raise ValueError('奖杯记录已变化，请重新导入或新建转换任务。')


def backup(plan, folder):
    from save_transfer import plain_path
    check(plan)
    folder = plain_path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    for index, state in enumerate([*plan['sources'], plan['target']]):
        current, raw = read(state['path'])
        if current != state:
            raise ValueError('备份期间奖杯记录发生变化，已停止同步。')
        if raw is not None:
            name = 'target.log' if index == len(plan['sources']) else f'source-{index}.log'
            dest = folder / name
            dest.write_bytes(raw)
            if hashlib.sha256(dest.read_bytes()).hexdigest() != state['sha256']:
                raise OSError('Trophy backup verification failed')
    (folder / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), 'utf-8')
    check(plan)


def atomic_write(path, raw):
    from save_transfer import plain_path
    path = plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(raw)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def install(plan):
    check(plan)
    if plan['content'] is None:
        return  # No evidence: leave the destination exactly as it was.
    raw = plan['content'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != plan['target']['sha256']:
        atomic_write(plan['target']['path'], raw)
    if read(plan['target']['path'])[1] != raw:
        raise OSError('Trophy installation verification failed')


def rollback(plan, folder):
    """Restore only our own installed output; never clobber an unexpected concurrent edit."""
    current, _ = read(plan['target']['path'])
    if current['sha256'] == plan['target']['sha256']:
        return
    expected = hashlib.sha256(plan['content'].encode('utf-8')).hexdigest() if plan['content'] else None
    if current['sha256'] != expected:
        raise OSError('Trophy rollback refused: target changed externally')
    if plan['target']['sha256'] is None:
        Path(plan['target']['path']).unlink()
    else:
        raw = (Path(folder) / 'target.log').read_bytes()
        if hashlib.sha256(raw).hexdigest() != plan['target']['sha256']:
            raise OSError('Trophy rollback backup mismatch')
        atomic_write(plan['target']['path'], raw)


def notice(summary):
    text = f"奖杯记录已合并，共 {summary['count']} 项；不根据角色进度补发奖杯。"
    if summary['missing_source_logs']:
        text += ' 来源缺少奖杯记录，无法补齐该来源的历史奖杯。'
    if summary['ignored_lines']:
        text += f" 跳过 {summary['ignored_lines']} 行无效记录，原文件已备份。"
    return text
