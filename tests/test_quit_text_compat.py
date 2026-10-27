"""Synthetic menu archives; optional pinned upstream helpers are needed to run."""
import os
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PORT=Path(os.environ.get('BBSC_PORT_SOURCE',ROOT/'.build/bbport'))
AVAILABLE=(PORT/'scripts/quit_menu.py').is_file()
sys.path.insert(0,str(PORT/'scripts'))
sys.path.insert(0,str(ROOT/'scripts'))
if AVAILABLE:
    import quit_menu as q
    import mods
    import quit_text_compat as compat
from port_compat import patched_mods_script


def archive(label='意见箱',extra='保留原来的译文'):
    entries={200:{401307:label,110000:extra},201:{110000:extra},204:{110000:extra},
             30:{102001:'同伴已来到你的世界。'}}
    fmg=bytearray(40);fmg[2]=2
    blobs={number:q.write_fmg(bytes(fmg),texts) for number,texts in entries.items()}
    raw=bytearray(64+36*len(blobs));raw[:4]=b'BND4'
    struct.pack_into('<I',raw,12,len(blobs));struct.pack_into('<Q',raw,32,36)
    for index,(number,body) in enumerate(blobs.items()):
        raw+=b'\0'*(-len(raw)%16);offset=len(raw);entry=64+index*36
        struct.pack_into('<QQII',raw,entry+8,len(body),len(body),offset,number)
        raw+=body
    dcx=bytearray(76);dcx[:4]=b'DCX\0';dcx[40:44]=b'DFLT'
    return q.pack_dcx(dcx,raw)


def bodies(raw):
    raw=q.unpack_dcx(raw)
    return {number:raw[offset:offset+size] for _,offset,size,number in q.bnd4_files(raw)}


@unittest.skipUnless(AVAILABLE,'run after fetching pinned BBport sources')
class QuitTextCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'output'

    def layer(self,name,raw,lang='zhocn'):
        root=self.root/name;p=root/'dvdroot_ps4/msg'/lang/'menu.msgbnd.dcx'
        p.parent.mkdir(parents=True);p.write_bytes(raw)
        return name,root

    def test_only_five_quit_entries_change(self):
        original=archive();patched=compat.patched_archive(original,'zhocn')
        before,after=bodies(original),bodies(patched)
        self.assertEqual(set(before),set(after));self.assertEqual(before[30],after[30])
        expected={200:{401307,110006},201:{401307,110006},204:{110005}}
        for kind in expected:
            a,b=q.read_fmg(before[kind]),q.read_fmg(after[kind])
            changed={n for n in set(a)|set(b) if a.get(n)!=b.get(n)}
            self.assertEqual(changed,expected[kind])
            self.assertEqual(b[110000],'保留原来的译文')

    def test_chinese_in_english_folder_stays_chinese(self):
        raw=compat.patched_archive(archive(),'engus')
        self.assertEqual(q.read_fmg(bodies(raw)[200])[401307],'退出到桌面')

    def test_english_menu_stays_english(self):
        raw=compat.patched_archive(archive('Opinion Box','Keep original text'),'engus')
        self.assertEqual(q.read_fmg(bodies(raw)[200])[401307],'Quit to Desktop')

    def test_traditional_chinese_in_english_folder(self):
        raw=compat.patched_archive(archive('意見箱'),'enggb')
        self.assertEqual(q.read_fmg(bodies(raw)[200])[401307],'結束並返回桌面')

    def test_already_correct_archive_is_byte_identical(self):
        raw=compat.patched_archive(archive(),'zhocn')
        self.assertIs(compat.patched_archive(raw,'zhocn'),raw)

    def test_disabled_feature_does_not_inspect_or_create_outputs(self):
        def forbidden(_):raise AssertionError('disabled feature inspected mods')
        self.assertIsNone(compat.prepare_layer([('mod',self.root)],self.out,forbidden))
        self.assertFalse(self.out.exists())

    def test_final_mod_wins_while_source_files_stay_unchanged(self):
        lower=self.layer('quit_text',compat.patched_archive(archive(),'zhocn'))
        upper=self.layer('translation',archive(extra='最终优先的译文'))
        source=upper[1]/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx';before=source.read_bytes()
        generated=compat.prepare_layer([lower,upper],self.out,mods.mod_files)
        self.assertIsNotNone(generated);self.assertEqual(source.read_bytes(),before)
        data=bodies((generated[1]/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx').read_bytes())
        self.assertEqual(q.read_fmg(data[200])[110000],'最终优先的译文')
        self.assertEqual(q.read_fmg(data[204])[110005],'要退出游戏并返回桌面吗？')

    def test_correct_final_layer_needs_no_cache(self):
        layer=self.layer('quit_text',compat.patched_archive(archive(),'zhocn'))
        self.assertIsNone(compat.prepare_layer([layer],self.out,mods.mod_files))
        self.assertFalse(self.out.exists())

    def test_unchanged_input_reuses_output_without_rewriting(self):
        layers=[self.layer('quit_text',archive())]
        first=compat.prepare_layer(layers,self.out,mods.mod_files)
        file=first[1]/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx';mtime=file.stat().st_mtime_ns
        second=compat.prepare_layer(layers,self.out,mods.mod_files)
        self.assertEqual(first,second);self.assertEqual(file.stat().st_mtime_ns,mtime)

    def test_changed_mod_uses_new_cache_and_keeps_new_translation(self):
        layer=self.layer('quit_text',archive());a=compat.prepare_layer([layer],self.out,mods.mod_files)
        p=layer[1]/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx';p.write_bytes(archive(extra='更新后的译文'))
        b=compat.prepare_layer([layer],self.out,mods.mod_files);self.assertNotEqual(a[1],b[1])
        self.assertEqual(q.read_fmg(bodies((b[1]/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx').read_bytes())[200])[110000],'更新后的译文')

    def test_corrupted_menu_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):compat.patched_archive(b'bad','zhocn')

    def test_upstream_layer_hook_is_pinned_and_last(self):
        original=(PORT/'scripts/mods.py').read_text('utf-8')
        result=patched_mods_script(original)
        compile(result,'mods.py','exec')
        self.assertLess(result.index('layers.append(compatibility)'),result.index('print(build_overlay(args.game, args.out, layers))'))
        with self.assertRaises(ValueError):patched_mods_script(result)

    def test_actual_mods_cli_keeps_translation_and_final_quit_text(self):
        engine=self.root/'engine';engine.mkdir()
        (engine/'mods.py').write_text(patched_mods_script((PORT/'scripts/mods.py').read_text('utf-8')),encoding='utf-8')
        shutil.copyfile(ROOT/'scripts/quit_text_compat.py',engine/'quit_text_compat.py')
        game=self.root/'game';(game/'dvdroot_ps4').mkdir(parents=True)
        lower=self.layer('quit_text',compat.patched_archive(archive(),'zhocn'))
        upper=self.layer('mods/translation',archive(extra='整合测试的中文译文'))
        src=upper[1]/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx';before=src.read_bytes()
        env=dict(os.environ,PYTHONPATH=str(PORT/'scripts'))
        completed=subprocess.run([sys.executable,str(engine/'mods.py'),str(game),'--out',str(self.out),
                    '--mods-dir',str(self.root/'mods'),'--layer',str(lower[1])],
                    capture_output=True,env=env,timeout=20)
        self.assertEqual(completed.returncode,0,completed.stderr[-2000:])
        view=Path(completed.stdout.decode('utf-8').strip())
        strings=q.read_fmg(bodies((view/'dvdroot_ps4/msg/zhocn/menu.msgbnd.dcx').read_bytes())[200])
        self.assertEqual(strings[110000],'整合测试的中文译文')
        self.assertEqual(strings[110006],'退出到桌面')
        self.assertEqual(src.read_bytes(),before)

if __name__=='__main__':unittest.main(verbosity=2)
