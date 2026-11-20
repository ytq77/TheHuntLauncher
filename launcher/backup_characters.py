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
