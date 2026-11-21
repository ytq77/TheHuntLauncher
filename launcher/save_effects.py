"""Read-only action effects for the source and inventories bound by save_resolution."""
from backup_characters import inventory_slots, public_generation


# These describe the durable actions in save_resolution._steps, including the review gate.
# world_after describes the operation on pending evidence, never a gameplay verification.
EFFECTS = {
    'accept-bound': (['全部角色的当前存档', '其他角色的联机记录'],
                     ['停止补回这一角色本次尚未核验的世界变化；门、Boss、商店或任务可能保留房主状态。'],
                     'bound-retired-unverified'),
    'use-current': (['所有当前角色和正常等待中的联机记录'], ['结束此次换回后的选择提示。'], 'resume-checks'),
    'restore-case': (['选择时刻的全部角色、主备档和联机记录', '另存当前完整现场'],
                     ['当前版本独有的进度不并入所选版本。'], 'restored-needs-review'),
    'restore-all': (['所选备份的全部角色及主备档', '另存当前完整现场'],
                    ['备份之后的角色进度不并入，当前待补的联机记录退出使用。'], 'pending-retired'),
    'accept-all': (['全部角色的当前存档'], ['所有旧联机待补记录退出使用，可能放弃尚未落盘的个人世界、拾取或共享进度。'],
                   'all-retired-unverified'),
    'restore-generation': (['该槽的游戏备档、其他角色和联机记录'],
                           ['该槽主档被游戏备档取代，可能失去两代之间的进度。'], 'needs-review'),
    'reimport-ordinary': (['所选原版来源的全部角色', '另存当前完整现场'],
                          ['当前无缝存档独有的进度不合并，当前待补联机记录退出使用。'], 'pending-retired'),
    'restart': (['封存当前全部原件'], ['当前安装从空存档开始，原有角色退出当前存档。'], 'pending-retired'),
    'preserve': (['完整另存现场，保留当前角色与待办'], [], 'unchanged'),
    'choose-ordinary': (['当前存档和待办'], [], 'unchanged'),
    'abandon': (['完整另存中断现场，保留此刻的全部角色和联机记录'],
                ['放弃继续执行上次选择，其余未执行步骤结束。'], 'needs-review'),
}


def change(before, after):
    state = 'comparable'
    values = {'level_delta': None, 'echoes_delta': None}
    if 'unreadable' in (before['state'], after['state']):
        state = 'unreadable'
    elif before['state'] in ('empty', 'missing') or after['state'] in ('empty', 'missing'):
        state = 'absent'
    elif not before['_identity'] or not after['_identity']:
        state = 'unknown'
    elif before['_identity'] != after['_identity']:
        state = 'different-character'
    else:
        for k in ('level', 'echoes'):
            if before[k] is not None and after[k] is not None:
                values[k+'_delta'] = after[k] - before[k]
        if None in values.values():
            state = 'unknown'
    return {'state': state, **values}


def effect(action, current, after, can_list_original):
    keeps, loses, world_after = EFFECTS[action]
    if action in ('preserve', 'choose-ordinary'):
        undo = {'available': False, 'scope': '当前角色和待办无变更，无需撤销。'}
    elif can_list_original or action == 'restart':
        scope = '可换回本次处理前保全的整套角色及联机记录。换回后重新复核，两个版本的后续进度不合并。'
        if action == 'abandon':
            scope = '可换回保全时的角色及联机记录。旧处理锁仅供留证，换回后重新选择。'
        undo = {'available': True, 'scope': scope}
    else:
        undo = {'available': False, 'scope': '原件会封存，目前没有可列为换回来源的角色代次。'}
    roles = []
    for slot, (previous, target) in enumerate(zip(current, after, strict=True)):
        roles.append({'slot': slot, 'before': {k: public_generation(v) for k, v in previous.items()},
                      'after': {k: public_generation(v) for k, v in target.items()},
                      **{k: change(previous[k], target[k]) for k in ('primary', 'backup')}})
    return {'keeps': keeps, 'loses': loses, 'reversible': undo, 'world_after': world_after, 'roles': roles}


def option_effects(options, root, current_inventory, source, ordinary, can_list_original):
    current = inventory_slots(root/'data/user/savedata', current_inventory)
    source_slots = None
    if source:
        case = source['kind'] == 'resolution-before'
        path = source['_folder']/('original/savedata' if case else 'savedata')
        inventory = source['_original']['trees']['savedata'] if case else source['_inventory']
        source_slots = inventory_slots(path, inventory)
    for option in options:
        action = option['id']
        after = current
        if action.startswith('restore-generation-'):
            slot = int(action.rsplit('-', 1)[1])
            after = list(current)
            after[slot] = {**current[slot], 'primary': current[slot]['backup']}
            action = 'restore-generation'
        elif action in ('restore-case', 'restore-all'):
            after = source_slots
        elif action == 'restart':
            after = inventory_slots(root, None)
        elif action == 'reimport-ordinary':
            after = inventory_slots(ordinary['path'], ordinary['inventory'])
        option['effects'] = effect(action, current, after, can_list_original)
