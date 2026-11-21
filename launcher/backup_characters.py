"""Read-only character details from the files bound to a save inventory."""
import hashlib
import struct

import offline_aux
import offline_save
import save_transfer as transfer


SLOT_BYTES = 0x140000
EMPTY_SLOT = {'bytes': SLOT_BYTES, 'sha256': hashlib.sha256(bytes(SLOT_BYTES)).hexdigest()}


def _character(raw):
    try:
        role = offline_save.parse(raw)
        offline_aux.flag_bank(raw)
    except (ValueError, IndexError, struct.error):
        return {'issue': 'unrecognized'}
    name = transfer.character_name(raw, role, unknown=None)
    detail = {'_identity': transfer.character_identity(raw, role)}
    if name and name.strip():
        detail['name'] = name
    try:
        from engine.launcher import bbport_character
        # The stock reader also works whenever its own name check accepts the slot.
        if hasattr(bbport_character, 'read_with_name'):
            character = bbport_character.read_with_name(raw, detail.get('name'))
        else:
            character = bbport_character.read(raw)
    except (ImportError, ValueError, IndexError, struct.error):
        character = None
    if character is None:
        detail['issue'] = 'stats_unavailable'
    else:
        detail.update(level=character.level, echoes=character.echoes)
        if 'name' not in detail:
            detail['issue'] = 'name_unavailable'
    return detail


def _generation(path, expected, cache):
    if expected is None or expected == EMPTY_SLOT:
        return None
    if expected['bytes'] != SLOT_BYTES:
        return {'issue': 'unrecognized'}
    try:
        with transfer.plain_path(path).open('rb') as stream:
            raw = stream.read(SLOT_BYTES + 1)
    except (OSError, transfer.OperationUnavailable):
        return {'issue': 'read_failed'}
    digest = expected['sha256']
    if len(raw) != SLOT_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        return {'issue': 'read_failed'}
    if digest not in cache:
        cache[digest] = _character(raw)
    return cache[digest]


def character_preview(savedata, inventory):
    files = {name.casefold(): (name, info) for name, info in inventory['files'].items()} if inventory else {}
    preview, cache = [], {}
    for slot in range(10):
        generations = {}
        for source, stem in (('primary', 'userdata'), ('backup', 'backup')):
            name = f'1/CUSA00207/SPRJ0005/{stem}{slot:04}'
            stored_name, expected = files.get(name.casefold(), (name, None))
            detail = _generation(savedata/stored_name, expected, cache)
            if detail is not None:
                generations[source] = detail
        primary, backup = generations.get('primary', {}), generations.get('backup', {})
        conflict = (primary.get('_identity') and backup.get('_identity') and primary['_identity'] != backup['_identity'])
        # Qualified detail contains exactly the release identity and the three visible fields.
        if primary and 'issue' not in primary and primary == backup:
            generations = {'both': primary}
        for source, detail in generations.items():
            line = {'slot': slot, 'source': source, **{k: v for k, v in detail.items() if k != '_identity'}}
            if conflict and source == 'backup':
                line['identity_conflict'] = True
            preview.append(line)
    return preview


def with_character_preview(row):
    """Project a backup after its manifest and fingerprint have been validated."""
    if row['kind'] == 'resolution-before':
        folder, inventory = row['_folder']/'original/savedata', row['_original']['trees']['savedata']
    else:
        folder, inventory = row['_folder']/'savedata', row['_inventory']
    public = {key: value for key, value in row.items() if not key.startswith('_')}
    return {**public, 'character_preview': character_preview(folder, inventory)}


def generation(info, detail):
    """Keep identity and content digest private to comparisons."""
    state = ('missing' if info is None else 'empty' if info == EMPTY_SLOT else
             'unreadable' if detail.get('issue') == 'unrecognized' else 'readable')
    return {'state': state, **{k: detail.get(k) for k in ('name', 'level', 'echoes', 'issue')},
            '_identity': detail.get('_identity'), '_sha': info['sha256'] if info else None}


def public_generation(value):
    return {k: v for k, v in value.items() if not k.startswith('_')}


def display_slot(slot, primary, backup):
    comparable = primary['_identity'] is not None and backup['_identity'] is not None
    same = comparable and primary['_identity'] == backup['_identity']
    fields = ('name', 'level', 'echoes')
    complete = all(g[k] is not None for g in (primary, backup) for k in fields)
    return {'slot': slot, 'primary': public_generation(primary), 'backup': public_generation(backup),
            'identity_relation': ('same' if same else 'different') if comparable else 'unknown',
            'summary_equal': all(primary[k] == backup[k] for k in fields) if same and complete else None,
            'bytes_equal': primary['_sha'] == backup['_sha'] if primary['_sha'] and backup['_sha'] else None}


def inventory_slots(savedata, inventory):
    """Both generations of all ten slots, strictly bound to a plan's inventory."""
    files = {k.casefold(): (k, v) for k, v in inventory['files'].items()} if inventory else {}
    cache, slots = {}, []
    for slot in range(10):
        row = {}
        for source, stem in (('primary', 'userdata'), ('backup', 'backup')):
            name = f'1/CUSA00207/SPRJ0005/{stem}{slot:04}'
            stored, info = files.get(name.casefold(), (name, None))
            detail = _generation(savedata/stored, info, cache) or {}
            if detail.get('issue') == 'read_failed':
                raise transfer.OperationUnavailable('角色文件暂时无法按本次指纹读取，请重新检查。')
            row[source] = generation(info, detail)
        slots.append(row)
    return slots
