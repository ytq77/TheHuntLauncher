"""Restore BBport's quit labels after loose-file language mods, without editing them."""
import hashlib
import json
from pathlib import Path
import sys
import uuid

import quit_menu


def patched_archive(raw, language, pc_texts=None):
    bnd=quit_menu.unpack_dcx(raw)
    files={number:bnd[offset:offset+size]
           for _,offset,size,number in quit_menu.bnd4_files(bnd)}
    if not {200,201,204}<=set(files):
        raise ValueError('Quit menu compatibility: incomplete menu text archive')
    strings={category:quit_menu.read_fmg(files[category]) for category in (200,201,204)}
    # Some Chinese translations intentionally live in engus/enggb. Recognize
    # the exact translated carrier label instead of reverting that menu to English.
    carrier=strings[200].get(quit_menu.TITLE_ID)
    if carrier in ('意见箱',quit_menu.TEXTS['zhocn'][0]):language='zhocn'
    elif carrier in ('意見箱',quit_menu.TEXTS['zhotw'][0]):language='zhotw'
    texts=quit_menu.TEXTS.get(language.casefold(),quit_menu.TEXTS['engus'])
    name,title_help,system_help,question=texts
    wanted={200:{quit_menu.TITLE_ID:name,quit_menu.SYSTEM_ID:name},
            201:{quit_menu.TITLE_ID:title_help,quit_menu.SYSTEM_ID:system_help},
            204:{quit_menu.DIALOG_ID:question}}
    # 1.9.5's prepared PC menu adds its own 119xxx texts. Fill only missing
    # entries after language mods, retaining any existing translated PC labels.
    extra={category:{number:text for number,text in entries.items()
                     if not strings[category].get(number)}
           for category,entries in (pc_texts or {}).items()}
    for category,entries in extra.items():wanted[category].update(entries)
    if all(all(strings[category].get(number)==text
               for number,text in entries.items()) for category,entries in wanted.items()):
        return raw
    return quit_menu.menu_texts(raw,texts,extra) if any(extra.values()) else quit_menu.menu_texts(raw,texts)


def prepared_pc_texts(raw):
    bnd=quit_menu.unpack_dcx(raw)
    tables={number:quit_menu.read_fmg(bnd[offset:offset+size])
            for _,offset,size,number in quit_menu.bnd4_files(bnd) if number in (200,201,204)}
    if not all(tables.get(200,{}).get(n) for n in (119001,119002,119003)):
        return {}
    return {category:{n:text for n,text in strings.items() if 119000<=n<120000 and text}
            for category,strings in tables.items()}


def patched_pc_movie(raw, prepared):
    # Use the pinned upstream transforms on the final movie, so a language
    # mod's movie keeps its fonts/layout and still gains the prepared PC page.
    import pc_menu
    _,sprites,_=quit_menu.swf.definitions(prepared)
    root=quit_menu.placements_in(prepared,quit_menu.swf.stage_start(prepared),len(prepared))
    character=root[quit_menu.SYSTEM_PANEL['list']][3]
    rows=quit_menu.row_count(quit_menu.placements_in(prepared,*sprites[character]))
    data=raw
    while (grown:=quit_menu.taller_list(data,rows_wanted=rows)) is not None:data=grown
    return pc_menu.pc_pages(data) or data


def prepare_layer(layers,out,mod_files):
    # The engine supplies quit_text only when it prepared the exit menu feature.
    # A fallback invocation without that layer must keep the original mod view.
    if not any(name=='quit_text' for name,_ in layers):
        return None
    final={};prepared={}
    for name,root in layers:
        for relative,source in mod_files(root):
            parts=relative.parts
            if (len(parts)==4 and parts[0].casefold()=='dvdroot_ps4' and
                    parts[1].casefold()=='msg' and parts[3].casefold()=='menu.msgbnd.dcx'):
                final[relative.as_posix().casefold()]=source
                if name=='quit_text':prepared[relative.as_posix().casefold()]=source
            elif relative.as_posix().casefold()=='dvdroot_ps4/menu/optionsetting.gfx':
                final[relative.as_posix().casefold()]=source
                if name=='quit_text':prepared[relative.as_posix().casefold()]=source
    pc={relative:prepared_pc_texts(source.read_bytes()) for relative,source in prepared.items()
        if relative.endswith('menu.msgbnd.dcx')}
    changed={}
    for relative,source in sorted(final.items()):
        raw=source.read_bytes()
        if relative.endswith('optionsetting.gfx'):
            base=prepared.get(relative)
            if not any(pc.values()) or base is None or base==source:continue
            patched=patched_pc_movie(raw,base.read_bytes())
        else:
            patched=patched_archive(raw,Path(relative).parts[2],pc.get(relative))
        if patched!=raw:
            changed[relative]=patched
    if not changed:
        return None
    # Content-addressed output is reused on later starts. A changed mod selects
    # a different cache; stale files can never override the new selection.
    identity=hashlib.sha256(json.dumps({name:hashlib.sha256(raw).hexdigest()
                           for name,raw in changed.items()},sort_keys=True).encode()).hexdigest()
    out=Path(out).resolve()
    target=out/'quit-text-compat'/identity
    if not target.resolve().is_relative_to(out):
        raise ValueError('Quit menu compatibility: output escaped its cache')
    for relative,raw in changed.items():
        path=target/relative
        if not path.resolve().is_relative_to(out):
            raise ValueError('Quit menu compatibility: unsafe output link')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.is_file() and path.read_bytes()==raw:
            continue
        temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('xb') as stream:stream.write(raw)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    print(f'Mods: quit/PC menus retained after language mods ({len(changed)} files)',file=sys.stderr)
    return 'thehunt_quit_text',target
