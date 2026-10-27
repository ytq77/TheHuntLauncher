"""Restore BBport's quit labels after loose-file language mods, without editing them."""
import hashlib
import json
from pathlib import Path
import sys
import uuid

import quit_menu


def patched_archive(raw, language):
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
    if all(all(strings[category].get(number)==text
               for number,text in entries.items()) for category,entries in wanted.items()):
        return raw
    return quit_menu.menu_texts(raw,texts)


def prepare_layer(layers,out,mod_files):
    # The engine supplies quit_text only when it prepared the exit menu feature.
    # A fallback invocation without that layer must keep the original mod view.
    if not any(name=='quit_text' for name,_ in layers):
        return None
    final={}
    for _,root in layers:
        for relative,source in mod_files(root):
            parts=relative.parts
            if (len(parts)==4 and parts[0].casefold()=='dvdroot_ps4' and
                    parts[1].casefold()=='msg' and parts[3].casefold()=='menu.msgbnd.dcx'):
                final[relative.as_posix().casefold()]=source
    changed={}
    for relative,source in sorted(final.items()):
        raw=source.read_bytes()
        patched=patched_archive(raw,Path(relative).parts[2])
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
    print(f'Mods: quit menu labels retained after language mods ({len(changed)} languages)',file=sys.stderr)
    return 'thehunt_quit_text',target
