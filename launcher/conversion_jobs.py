"""Experimental same-role export. Every game run writes only to a job copy."""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

import offline_save
import offline_aux

from client import EBOOT_SHA256
from save_transfer import plain_path, overlaps, require_stopped, snapshot, source_path
from settings_bridge import sync_settings

ROOT = Path(__file__).resolve().parent
ORIGINAL = {200, 205, 225, 111}
RINGING = {201, 206, 226}
KIT = {4016, 4121, 11201, 4313, 11251}
ALL_GOODS = ORIGINAL | RINGING | KIT
OFFLINE_FLOW = 'offline-v1'
NEXT = {'created': 'baseline', 'baselined': 'inspect', 'inspected': 'convert',
        'converted': 'verify', 'awaiting-confirmation': 'export'}
MESSAGES = {
    'created': '备份已完成。下一步加载普通版副本中的目标角色，停留 15 秒后正常退出。',
    'baselined': '已记录原版四件道具。下一步检查无缝副本中的同一个角色，并卸下五件道具的快捷栏／随身物品装备。',
    'inspected': '无缝道具状态符合本轮范围。确认已卸下五件道具后，转换另一份副本。',
    'converted': '副本已完成道具处理并正常保存。下一步用不加载模组的核心重新读档。',
    'awaiting-confirmation': '普通版重载已正常退出。确认道具、等级、装备和地点正确后，可导出存档包。',
    'exported': '已导出测试存档包；未覆盖普通版或当前无缝版。仅目标角色完成验证。',
}


def atomic_json(path, value):
    path = plain_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def hashes(folder):
    return {name: row[-1] for name, row in snapshot(folder)[1].items()}


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_verified(source, destination):
    source, destination = plain_path(source), plain_path(destination)
    if overlaps(source, destination) or destination.exists():
        raise ValueError('测试副本路径冲突，已停止；不会覆盖已有文件。')
    before = snapshot(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    if before != snapshot(source) or hashes(destination) != {k: v[-1] for k, v in before[1].items()}:
        raise ValueError('复制期间源存档发生变化或校验失败，请退出游戏后新建测试。')
    return before


def pid_alive(pid):
    if type(pid) is not int or pid <= 0:
        return True  # malformed lock is never silently removed
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # only invalid PID proves absence
    try:
        return kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


@contextmanager
def operation_lock():
    require_stopped()
    path = plain_path(ROOT / '.save-conversion.lock')
    if plain_path(ROOT / '.save-import.lock').exists():
        raise ValueError('存档导入尚未结束，请先处理导入状态。')
    try:
        stream = path.open('x', encoding='utf-8')
    except FileExistsError:
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise ValueError('转换锁记录损坏，请保留测试目录并检查，不会启动游戏。') from None
        if pid_alive(old.get('pid')):
            raise ValueError('另一个转换操作仍在进行。')
        # A dead worker and no game: only its lock is removed; its copies remain.
        path.unlink()
        stream = path.open('x', encoding='utf-8')
    with stream:
        stream.write(json.dumps({'pid': os.getpid()}))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def job_path(job_id):
    if not isinstance(job_id, str) or not re.fullmatch('[0-9a-f]{32}', job_id):
        raise ValueError('转换任务编号不正确。')
    return plain_path(ROOT / 'conversion-jobs' / job_id)


def load_job(job_id=None):
    if job_id is None:
        pointer = plain_path(ROOT / 'conversion-current.json')
        if not pointer.exists():
            return None
        job_id = json.loads(pointer.read_text(encoding='utf-8'))['job']
    path = job_path(job_id)
    value = json.loads((path / 'job.json').read_text(encoding='utf-8'))
    if value.get('schema') != 1 or value.get('id') != job_id:
        raise ValueError('转换任务记录不匹配，请新建测试。')
    return value


def save_job(job):
    atomic_json(job_path(job['id']) / 'job.json', job)


def public_status(job):
    if job is None:
        return {'state': 'none', 'message': '先新建独立测试副本。原版与无缝版均保持原样。', 'next': None}
    if job['state'] == 'running' and not pid_alive(job.get('worker_pid')):
        job = dict(job, state='failed', message='上次测试操作中断。请正常退出残留的测试窗口后新建测试；旧副本保留，不能导出。')
    return {key: job.get(key) for key in ('id', 'state', 'message', 'slot', 'created', 'export', 'flow', 'output', 'ordinary_backup', 'validation')} | {
        'directory': str(job_path(job['id'])), 'next': None if job.get('flow') == OFFLINE_FLOW else NEXT.get(job['state'])}


def auxiliary_snapshot():
    result = {}
    for name in ('guest-items', 'own-world', 'shared-progress'):
        folder = plain_path(ROOT / 'mod' / name)
        result[name] = snapshot(folder) if folder.exists() else None
    return result


def create_job(profile, same_role, allow_auxiliary=False):
    if same_role is not True:
        raise ValueError('请先确认两份存档对应同一个角色，本次只验证该角色。')
    profile = {k: profile[k] for k in ('install', 'game', 'graphics_source')}
    for key in profile:
        if not isinstance(profile[key], str) or not Path(profile[key]).is_absolute():
            raise ValueError('请先填写本机普通版、游戏文件和画质配置的绝对路径。')
    ordinary = source_path(profile['install'])
    seamless = plain_path(ROOT / 'data/user/savedata')
    if overlaps(ordinary, seamless) or overlaps(ordinary, ROOT / 'conversion-jobs'):
        raise ValueError('源存档位置冲突，已停止。')
    if file_hash(plain_path(Path(profile['game']) / 'eboot.bin')) != EBOOT_SHA256:
        raise ValueError('当前仅支持已验证的 CUSA03173 1.09 游戏文件。')
    if not Path(profile['graphics_source']).is_file() or not (ROOT / 'mod/save-bridge.dll').is_file():
        raise ValueError('缺少本机画质配置或转换辅助模块，请使用完整测试包。')
    auxiliaries = auxiliary_snapshot()
    if not allow_auxiliary and any(value is not None and value[1] for value in auxiliaries.values()):
        raise ValueError('分步诊断暂不处理辅助记录，请保留记录并使用离线转换入口。')
    job_id = uuid.uuid4().hex
    path = job_path(job_id)
    path.mkdir(parents=True)
    (path / 'job.id').write_text(job_id, encoding='ascii')
    try:
        ordinary_state = copy_verified(ordinary, path / 'backup/ordinary/savedata')
        seamless_state = copy_verified(seamless, path / 'backup/seamless/savedata')
        if allow_auxiliary:
            for name, state in auxiliaries.items():
                if state is not None:
                    copied = copy_verified(ROOT / 'mod' / name, path / 'backup/auxiliary' / name)
                    if normalized(copied) != normalized(state):
                        raise ValueError('辅助记录在备份期间发生变化，请退出游戏后重试。')
        for label in ('ordinary', 'seamless'):
            if not (path / f'backup/{label}/savedata/1/CUSA00207/SPRJ0005/userdata0010').is_file():
                raise ValueError('存档结构与当前已验证布局不同，已停止。')
        mod = path / 'mod'
        mod.mkdir()
        for name in ('bbsc-launch.exe', 'LICENSE.txt', 'NOTICE.txt', 'SUPPORTERS.txt'):
            shutil.copy2(ROOT / 'mod' / name, mod / name)
        shutil.copy2(ROOT / 'mod/save-bridge.dll', mod / 'bbsc.dll')
        job = {'schema': 1, 'id': job_id, 'state': 'created', 'message': MESSAGES['created'],
               'created': datetime.now(timezone.utc).isoformat(), 'profile': profile,
               'ordinary': str(ordinary), 'seamless': str(seamless),
               'source_snapshots': {'ordinary': ordinary_state, 'seamless': seamless_state},
               'auxiliary_snapshots': auxiliaries, 'helper_sha256': file_hash(mod / 'bbsc.dll')}
        save_job(job)
        atomic_json(ROOT / 'conversion-current.json', {'job': job_id})
        assert_sources(job)
        return job
    except Exception:
        atomic_json(path / 'failure.json', {'stage': 'create', 'message': '创建或校验失败；保留现场，未替换源存档。'})
        raise


def normalized(value):
    return json.loads(json.dumps(value))


def assert_sources(job):
    for label in ('ordinary', 'seamless'):
        if normalized(snapshot(plain_path(job[label]))) != normalized(job['source_snapshots'][label]):
            raise ValueError('原始存档在测试期间有更新。请新建测试，以免导出旧进度；当前副本保留。')
    if normalized(auxiliary_snapshot()) != normalized(job['auxiliary_snapshots']):
        raise ValueError('辅助记录发生变化，请新建测试。')


def validate_snapshot(value, job, step, converted):
    if step == 'inspect' and value.get('kit_display_ready') is not True:
        raise ValueError('无缝道具显示定义尚未恢复，无法可靠检查装备槽；请保留副本并新建测试。')
    if (value.get('schema') != 1 or value.get('job') != job['id'] or value.get('step') != step or
            value.get('normal_bbsc_initialized') is not False or value.get('own_world') is not True or
            type(value.get('session_state')) is not int or value['session_state'] != 0 or
            value.get('flag_6622') is not True or type(value.get('slot')) is not int or
            not 0 <= value['slot'] <= 9):
        raise ValueError('角色／世界／领铃状态不在当前测试范围。尚未领铃或处于其他世界时不会继续转换。')
    if step != 'baseline' and value['slot'] != job.get('slot'):
        raise ValueError('本轮加载的角色槽位与原版检查不一致。')
    rows = value.get('goods')
    if not isinstance(rows, list) or len(rows) != len(ALL_GOODS):
        raise ValueError('道具检查记录不完整。')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] not in ALL_GOODS or row['id'] in seen:
            raise ValueError('道具记录编号异常。')
        seen.add(row['id'])
        want = int(row['id'] in (ORIGINAL if converted else KIT))
        if (type(row.get('carried')) is not int or row['carried'] != want or
                type(row.get('storage')) is not int or row['storage'] != 0):
            raise ValueError('道具数量或存放位置超出已验证范围；本版要求原版四件各一件、无缝五件各一件且均在背包。')
    return value


def snapshots_for(job, step):
    files = sorted((job_path(job['id']) / 'mod').glob(step + '-*.json'), key=lambda p: p.stat().st_mtime_ns)
    return [json.loads(plain_path(p).read_text(encoding='utf-8')) for p in files]


def game_environment(job, step):
    path = job_path(job['id'])
    data = path / step
    profile = job['profile']
    settings, _ = sync_settings(data / 'settings-sync', profile)
    shutil.copy2(data / 'settings-sync/data/bbport.ini', data / 'bbport.ini')
    inputs = data / 'settings-sync/data/input.ini'
    if inputs.is_file():
        shutil.copy2(inputs, data / 'input.ini')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('BB_', 'BBSC_', 'SHADPS4_'))}
    env.update(settings)
    env.update(BB_GAME_DIR=profile['game'], BB_DATA_DIR=str(data), BB_CONFIG=str(data / 'bbport.ini'),
               BB_USER_DIR=str(data / 'user'), BB_GPU_USER_DIR=str(data / 'user'),
               BB_FSR4_DIR=str(ROOT / 'fsr4_shaders'), BB_ONLINE='0', BB_MODS_ENABLED='0',
               BB_FSR4VK_DIR=str(ROOT / 'fsr4vk'),
               BB_MODS_DIR=str(data / 'mods'), BB_MODS_CONFIG=str(data / 'mods.json'),
               BB_PATCHES_DIR=str(data / 'patches'), BB_PATCHES_CONFIG=str(data / 'patches.json'),
               BB_USER_NAME='SaveConversion', BB_SOUND_HACK='0',
               BBSC_SAVE_JOB_ROOT=str(path), BBSC_SAVE_JOB_TOKEN=job['id'], BBSC_SAVE_STEP=step,
               BBSC_SAVE_DIR=str(data / 'user/savedata/1/CUSA00207/SPRJ0005'),
               PATH=str(ROOT / 'runtime/bin') + os.pathsep + env.get('PATH', ''))
    if step == 'verify':
        env['BB_PROBE'] = str(ROOT / 'bin/bb-probe.exe')
    else:
        env.update(BB_PROBE=str(path / 'mod/bbsc-launch.exe'), BBSC_EMULATOR=str(ROOT / 'bin/bb-probe.exe'))
    if step == 'convert':
        env['BBSC_CONVERSION_QUICK_SLOTS_CLEARED'] = '1'
    return env


def play(job, step):
    path = job_path(job['id'])
    env = game_environment(job, step)
    with (path / step / 'run.log').open('w', encoding='utf-8') as output:
        process = subprocess.Popen([str(ROOT / 'runtime/bin/python.exe'), str(ROOT / 'engine/run.py')],
            cwd=ROOT / 'engine', env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        while process.poll() is None:
            # These are progress hints only. Exit code and saved output are checked later.
            if step != 'verify' and any((path / 'mod').glob(step + '-*.json')):
                text = '已采集角色数据，请从游戏菜单正常退出。'
                if step == 'convert':
                    outcome = path / 'mod/conversion-outcome.json'
                    try:
                        status = json.loads(outcome.read_text(encoding='utf-8'))['status'] if outcome.exists() else ''
                        text = ('道具已处理，请保存并从游戏菜单正常退出。' if status == 'memory-ready' else
                                '转换未完成或已拒绝。请正常退出后查看结果。' if status in ('failed', 'rejected') else
                                '角色已就绪，正在等待一次性转换；请再等约 15 秒。')
                    except (ValueError, OSError, KeyError):
                        text = '正在读取转换结果，请稍候。'
                if job['message'] != text:
                    job['message'] = text
                    save_job(job)
            time.sleep(1)
    return process.returncode


def run_step(job, step, cleared=False):
    if NEXT.get(job['state']) != step or step not in ('baseline', 'inspect', 'convert', 'verify'):
        raise ValueError('步骤顺序不正确，请按页面提示继续。')
    if step == 'convert' and cleared is not True:
        raise ValueError('请先确认五件无缝道具均未装备在快捷栏或随身物品。')
    try:
        assert_sources(job)
        path = job_path(job['id'])
        if file_hash(path / 'mod/bbsc.dll') != job['helper_sha256']:
            raise ValueError('转换辅助模块已变化，请新建测试。')
        source = {'baseline': path / 'backup/ordinary/savedata', 'inspect': path / 'backup/seamless/savedata',
                  'convert': path / 'inspect/user/savedata', 'verify': path / 'convert/user/savedata'}[step]
        before = copy_verified(source, path / step / 'user/savedata')
        if step != 'baseline':
            (path / 'slot.txt').write_text(str(job['slot']), encoding='ascii')
        job.update(state='running', active_step=step, worker_pid=os.getpid(), message='请加载目标角色，等待约 20 秒后从游戏菜单正常退出。')
        save_job(job)
        code = play(job, step)
        require_stopped()
        if code != 0:
            raise ValueError('本轮未正常退出，不能继续。副本已保留；请新建测试，原始存档没有变化。')
        assert_sources(job)
        if step != 'verify':
            records = snapshots_for(job, step)
            if len(records) < 2:
                raise ValueError('没有采集到稳定角色数据，请新建测试并在场景内停留至少 15 秒。')
            if len({record.get('slot') for record in records}) != 1:
                raise ValueError('一轮中切换了角色，本测试版不处理多个角色。请新建测试。')
            selected = records[-1:] if step == 'convert' else records[-2:]
            for value in selected:
                validate_snapshot(value, job, step, step in ('baseline', 'convert'))
            if step == 'baseline':
                job['slot'] = selected[-1]['slot']
            if step == 'convert':
                result = json.loads((path / 'mod/conversion-outcome.json').read_text(encoding='utf-8'))
                if result.get('job') != job['id'] or result.get('status') != 'memory-ready':
                    raise ValueError('道具转换未完整完成，当前副本不能导出。')
                after = hashes(path / step / 'user/savedata')
                slot = f"{job['slot']:04}"
                allowed = {f'1/CUSA00207/SPRJ0005/{name}' for name in
                           (f'userdata{slot}', f'backup{slot}', 'userdata0010', 'backup0010')}
                changed = {k for k, v in before[1].items() if after.get(k) != v[-1]}
                if set(after) != set(before[1]) or not changed or changed - allowed:
                    raise ValueError('保存文件变化范围异常，已停止导出，请保留现场。')
        job.update(state={'baseline': 'baselined', 'inspect': 'inspected', 'convert': 'converted',
                          'verify': 'awaiting-confirmation'}[step], active_step=None)
        job['message'] = MESSAGES[job['state']]
        job.setdefault('completed', []).append(step)
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '本轮测试未完成；原存档未替换，全部副本保留。'
        job.update(state='failed', active_step=None, message=message)
        save_job(job)
        raise
    save_job(job)
    return job


def export_job(job, confirmed):
    if job['state'] != 'awaiting-confirmation' or confirmed is not True:
        raise ValueError('请先完成普通版重载，并确认道具、等级、装备和地点均正确。')
    assert_sources(job)
    path = job_path(job['id'])
    source = path / 'verify/user/savedata'
    manifest = hashes(source)
    archive = path / 'converted-save.zip'
    if archive.exists():
        raise ValueError('导出文件已存在，不会覆盖。')
    temporary = path / 'export.tmp'
    try:
        with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED) as output:
            for name in manifest:
                output.write(source / name, 'savedata/' + name)
            output.writestr('savedata-sha256.json', json.dumps(manifest, indent=2))
            output.writestr('使用说明.txt', (('离线转换输出：本次已检查存档结构，未启动游戏，未覆盖原存档。\n'
                '无缝道具已移除，两栏相关引用已清空，原版联机道具只放背包。\n' if job.get('flow') == OFFLINE_FLOW else
                '当前角色转换测试输出，未覆盖任何现有存档。\n') +
                '本包是整份 savedata，只有本轮角色完成检查；恢复范围依据原版备份已有道具，不补发未持有的道具。\n'
                '请保留原始备份，勿混合两个分支的 userdata 文件。当前无需安装，先完成测试验收。\n').encode('utf-8-sig'))
        with zipfile.ZipFile(temporary) as check:
            if check.testzip() is not None or any(hashlib.sha256(check.read('savedata/' + name)).hexdigest() != value
                                                for name, value in manifest.items()):
                raise ValueError('导出校验失败，未生成可用包。')
        if hashes(source) != manifest:
            raise ValueError('导出期间测试存档发生变化，未生成可用包。')
        temporary.rename(archive)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    job.update(state='exported', export=str(archive), export_sha256=file_hash(archive),
               message=MESSAGES['exported'], player_confirmed=True)
    save_job(job)
    return job


def begin_offline(profile, same_role, output):
    """Back up, transform and export without starting or injecting a game process."""
    if output not in ('export', 'replace'):
        raise ValueError('请选择导出存档包或转换并替换原版。')
    job = create_job(profile, same_role, allow_auxiliary=True)
    path = job_path(job['id'])
    job.update(flow=OFFLINE_FLOW, output=output, state='running', worker_pid=os.getpid(),
               message='正在离线检查副本并清理联机道具装备引用；不会打开游戏。')
    save_job(job)
    try:
        source = path / 'backup/seamless/savedata'
        folder = source / '1/CUSA00207/SPRJ0005'
        kit_slots = []
        for slot in range(10):
            raw = (folder / f'userdata{slot:04}').read_bytes()
            if not any(raw):
                continue
            parsed = offline_save.parse(raw)
            if any(parsed.goods(number) for number in KIT):
                kit_slots.append(slot)
        if len(kit_slots) != 1:
            raise ValueError('当前仅支持一名角色持有完整无缝五件道具；未找到或多个角色持有时停止转换。')
        slot = kit_slots[0]
        auxiliary = offline_aux.validate(path / 'backup/auxiliary', source, slot)
        atomic_json(path / 'auxiliary-check.json', auxiliary)
        baseline = (path / f'backup/ordinary/savedata/1/CUSA00207/SPRJ0005/userdata{slot:04}').read_bytes()
        offline_aux.flag_bank(baseline)
        restored_goods = offline_save.original_ownership(offline_save.parse(baseline))
        manifests, transformed = {}, {}
        for stem in ('userdata', 'backup'):
            relative = f'1/CUSA00207/SPRJ0005/{stem}{slot:04}'
            raw = (source / relative).read_bytes()
            flag_start = offline_aux.flag_bank(raw)
            converted, patches = offline_save.convert(raw, baseline)
            converted_start = offline_aux.flag_bank(converted)
            original_flags = raw[flag_start:flag_start + offline_aux.FLAG_BYTES]
            if converted[converted_start:converted_start + offline_aux.FLAG_BYTES] != original_flags:
                raise ValueError('转换改变了事件进度，已停止；原始存档未替换。')
            transformed[relative] = converted
            manifests[relative] = {'before_sha256': hashlib.sha256(raw).hexdigest(),
                'after_sha256': hashlib.sha256(converted).hexdigest(), 'patches': patches,
                'event_flags_sha256': hashlib.sha256(original_flags).hexdigest(),
                'event_flags_unchanged': True}
        destination = path / 'verify/user/savedata'
        copy_verified(source, destination)
        for relative, converted in transformed.items():
            (destination / relative).write_bytes(converted)
        expected = hashes(source)
        expected.update({name: value['after_sha256'] for name, value in manifests.items()})
        if hashes(destination) != expected:
            raise ValueError('离线输出文件校验失败，已停止。')
        assert_sources(job)
        atomic_json(path / 'offline-fields.json', {'schema': 1, 'slot': slot, 'files': manifests,
                    'restored_original_goods': list(restored_goods),
                    'equipment_policy': 'clear-kit-references; original-bells-in-inventory-only',
                    'validation': 'offline-structure; no game process started'})
        job.update(state='awaiting-confirmation', slot=slot, completed=['offline'],
                   validation='offline-structure', message='离线字段检查通过，正在生成存档包。')
        job = export_job(job, True)
        job['message'] = '已离线转换并导出存档包；未启动游戏，原版与无缝版存档均未替换。原版联机道具只放背包。'
        save_job(job)
        if output == 'replace':
            job = install_converted_job(job, True)
        return job
    except Exception as error:
        job.update(state='failed', message=('离线检查未通过：' + str(error) if isinstance(error, ValueError)
                   else '离线转换未完成；原存档未替换，备份和诊断副本已保留。'))
        save_job(job)
        raise


def install_converted_job(job, confirmed):
    """Replace the ordinary directory only after a verified export; retain a full backup."""
    if job['state'] != 'exported' or confirmed is not True or job.get('player_confirmed') is not True:
        raise ValueError('请先完成重载和导出验证，再明确选择替换原版存档。')
    require_stopped()
    assert_sources(job)
    destination = source_path(job['profile']['install'])
    if destination != plain_path(job['ordinary']):
        raise ValueError('原版存档目录设置已改变，不能替换。')
    root = job_path(job['id'])
    source = plain_path(root / 'verify/user/savedata')
    expected = hashes(source)
    archive = plain_path(job['export'])
    if file_hash(archive) != job['export_sha256']:
        raise ValueError('导出包已改变，不能替换。')
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or json.loads(bundle.read('savedata-sha256.json')) != expected:
            raise ValueError('验证副本与导出包不一致，不能替换。')
    if overlaps(source, destination) or overlaps(destination, ROOT):
        raise ValueError('原版与无缝目录存在重叠，不能替换。')
    stage = plain_path(destination.parent / ('.savedata-conversion-' + uuid.uuid4().hex))
    copy_verified(source, stage)
    backup = plain_path(destination.parent / 'save-conversion-backups' /
                        (job['id'] + '-' + uuid.uuid4().hex[:8]) / 'savedata')
    backup.parent.mkdir(parents=True)
    require_stopped()
    assert_sources(job)
    if hashes(stage) != expected or hashes(source) != expected:
        raise ValueError('待替换副本校验失败，原版保持不变。')
    # Stage and backup share the ordinary save volume; no cross-volume move.
    destination.rename(backup)
    try:
        stage.rename(destination)
        original = {name: row[-1] for name, row in job['source_snapshots']['ordinary'][1].items()}
        if hashes(destination) != expected or hashes(backup) != original:
            raise OSError('Installed save verification failed')
    except (OSError, ValueError):
        rejected = plain_path(destination.parent / ('.savedata-conversion-rejected-' + uuid.uuid4().hex))
        try:
            if destination.exists(): destination.rename(rejected)
            backup.rename(destination)
        except OSError:
            raise ValueError(f'替换及恢复未完成。完整旧存档保留在：{backup}；请保留现场，暂勿启动游戏。') from None
        raise ValueError('替换未完成，原版存档已恢复；导出包和失败副本保留。') from None
    job.update(state='applied', ordinary_backup=str(backup), installed_sha256=expected,
               message=f'已替换原版存档。旧原版完整备份：{backup}。无缝版和导出包保留；两个分支的进度没有合并。')
    save_job(job)
    return job


def dispatch(request):
    action = request.get('action')
    if action == 'status':
        return public_status(load_job())
    with operation_lock():
        if action == 'begin':
            if request.get('flow') != OFFLINE_FLOW:
                raise ValueError('未知转换流程。')
            return public_status(begin_offline(request.get('profile', {}), request.get('same_role'), request.get('output')))
        if action == 'create':
            return public_status(create_job(request.get('profile', {}), request.get('same_role')))
        job = load_job(request.get('job'))
        if job is None:
            raise ValueError('请先新建独立测试。')
        if action in ('baseline', 'inspect', 'convert', 'verify'):
            job = run_step(job, action, request.get('cleared'))
        elif action == 'export':
            job = export_job(job, request.get('confirmed'))
        else:
            raise ValueError('未知转换操作。')
        return public_status(job)


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError('转换请求格式不正确。')
        result = {'ok': True, 'job': dispatch(request)}
    except (ValueError, KeyError, TypeError, OSError) as error:
        result = {'ok': False, 'error': str(error) if isinstance(error, ValueError) else
                  '转换文件无法读取或复制。请保留 conversion-jobs 目录并检查路径、权限和空间。'}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
