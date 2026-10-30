"""Read lamp evidence without replaying journals or modifying saves."""
import json
from pathlib import Path

def native_flags(context,mapping):
    groups=context.get('groups',{})
    values={}
    for flag in mapping['flags']:
        raw=groups.get(str(flag//1000))
        if raw is None:values[str(flag)]=None;continue
        try:data=bytes.fromhex(raw)
        except (TypeError,ValueError):raise ValueError('Invalid native group bytes') from None
        if len(data)!=125:raise ValueError('Incomplete native group')
        bit=flag%1000;values[str(flag)]=bool(data[bit//8]&(0x80>>(bit%8)))
    return values

def disk_flags(raw,mapping):
    import offline_aux
    bank=offline_aux.flag_bank(raw)
    values={}
    for flag in mapping['flags']:
        index=mapping['groups'].get(str(flag//1000))
        if type(index) is not int or not 0<=index<1200:raise ValueError('Unverified disk flag mapping')
        bit=flag%1000
        values[str(flag)]=bool(raw[bank+index*125+bit//8]&(0x80>>(bit%8)))
    return values

def removed(before,after):
    return sorted(int(flag) for flag,value in before.items() if value is True and after.get(flag) is False)

def summarize_native(path,mapping):
    if not Path(path).exists():return {'available':False,'changes':[],'unknown_groups_observed':False}
    changes=[];entries={};rows=0;unknown=False;cached=0;last=None;hints=set();maximum_known=0;last_sequence=0
    for line in Path(path).read_text('utf-8').splitlines():
        row=json.loads(line);rows+=1
        values=native_flags(row['context'],mapping)
        maximum_known=max(maximum_known,sum(value is not None for value in values.values()))
        last_sequence=max(last_sequence,row.get('sequence',0))
        hint=row['context'].get('active_slot_hint',-1)
        if type(hint) is int and hint>=0:hints.add(hint)
        unknown|=any(value is None for value in values.values())
        if row.get('cached'):
            cached+=1;continue  # A cached save-entry snapshot is not a new mutation observation.
        reason=row['reason'];operation=row.get('operation',0)
        if reason.endswith('.enter'):entries[operation]=(row,values)
        elif reason.endswith('.exit') and operation in entries:
            before,old=entries.pop(operation)
            lost=removed(old,values)
            if lost:changes.append({'operation':operation,'stage':reason[:-5],
                'removed':lost,'sample_epoch_ms':row['sample_epoch_ms'],
                'session':row['context'].get('session'),'own_world':row['context'].get('own_world'),
                'active_slot_hint':row['context'].get('active_slot_hint'),
                'view':'own-world candidate' if row['context'].get('own_world')==1 else 'host/guest temporary view',
                'enter_sample_epoch_ms':before['sample_epoch_ms']})
        if reason=='tick' and last:
            lost=removed(last[1],values)
            if lost:changes.append({'operation':0,'stage':'between-tick-samples','removed':lost,
                'sample_epoch_ms':row['sample_epoch_ms'],'session':row['context'].get('session'),
                'own_world':row['context'].get('own_world'),'view':'unattributed; inspect surrounding operation pairs'})
        if reason=='tick':last=(row,values)
    changes.sort(key=lambda item:item['sample_epoch_ms'])
    return {'available':True,'rows':rows,'cached_save_entries':cached,'changes':changes,
            'unknown_groups_observed':unknown,'maximum_known_lamps':maximum_known,'active_slot_hints':sorted(hints),'line_limit_may_have_been_reached':last_sequence>=4096,
            'unpaired_operations':sorted(entries)}

def summarize_case(folder,mapping):
    folder=Path(folder)
    result={'native':summarize_native(folder/'native-lamps.jsonl',mapping),'disk':{}}
    case=json.loads((folder/'case.json').read_text('utf-8'))
    result['expected_slot']=case['slot']
    result['unexpected_slot_hints']=[slot for slot in result['native'].get('active_slot_hints',[]) if slot!=case['slot']]
    observed=folder/'disk-observations.jsonl'
    observations=[json.loads(line) for line in observed.read_text('utf-8').splitlines()] if observed.exists() else []
    result['disk_observation_rows']=len(observations)
    result['disk_read_errors']=sum('read_error' in row for row in observations)
    result['disk_snapshot_limit_reached']=any(row.get('snapshot_limit_reached') for row in observations)
    for stem in ('userdata','backup'):
        before=list((folder/'before').glob(stem+'[0-9][0-9][0-9][0-9]'))
        if len(before)!=1:raise ValueError('Missing or ambiguous before save')
        after=folder/'after'/before[0].name
        if not after.is_file():result['disk'][stem]={'after_missing':True};continue
        old=disk_flags(before[0].read_bytes(),mapping);new=disk_flags(after.read_bytes(),mapping)
        result['disk'][stem]={'before_count':sum(v is True for v in old.values()),'after_count':sum(v is True for v in new.values()),'removed_after_run':removed(old,new)}
    result['interpretation']='Observed differences only. Guest host-view changes can be expected; this does not diagnose historical corruption.'
    return result
