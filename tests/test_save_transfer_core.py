"""Explicit transfer primitives plus unchanged ordinary transaction regressions."""
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_save_transfer as legacy

T = legacy.transfer


class TransferCoreTests(unittest.TestCase):
    setUp = legacy.SaveTransferTests.setUp
    write_save = legacy.SaveTransferTests.write_save
    contents = legacy.SaveTransferTests.contents
    auxiliary_records = legacy.SaveTransferTests.auxiliary_records

    def test_explicit_source_and_inspection_ignore_globals_and_auxiliary(self):
        (self.root/'.save-conversion.lock').write_bytes(b'pending')
        with patch.object(T, 'ROOT', self.base/'wrong'), patch.dict(os.environ, {'APPDATA': str(self.base/'wrong')}):
            source = T.resolve_source_path(self.install, self.settings)
            info, inventory = T.inspect_source(source)
        self.assertEqual(source, self.source)
        self.assertEqual(info['file_count'], 4)
        self.assertEqual(inventory, T.snapshot(self.source))
        self.assertFalse((self.root/'save-backups').exists())

    def test_explicit_missing_settings_defaults_and_custom_settings(self):
        self.settings.unlink()
        self.assertEqual(T.resolve_source_path(self.install, self.settings), self.source)
        self.settings.write_text(json.dumps({'user_dir': str(self.base/'custom')}))
        self.assertEqual(T.resolve_source_path(self.install, self.settings), self.base/'custom/savedata')

    def test_settings_io_type_retained_core_legacy_message_unchanged(self):
        failure = PermissionError('settings denied')
        with patch.object(Path, 'read_text', side_effect=failure):
            with self.assertRaises(PermissionError) as caught:
                T.resolve_source_path(self.install, self.settings)
            self.assertIs(caught.exception, failure)
            with self.assertRaisesRegex(ValueError, '无法读取普通版存档目录设置'):
                T.source_path(self.install)

    def test_invalid_json_not_treated_as_corrupt_savedata(self):
        self.settings.write_text('{')
        with self.assertRaises(json.JSONDecodeError):
            T.resolve_source_path(self.install, self.settings)
        with self.assertRaisesRegex(ValueError, '无法读取普通版存档目录设置'):
            T.source_path(self.install)

    def test_source_inspection_io_error_propagates(self):
        failure = PermissionError('snapshot denied')
        with patch.object(T, 'snapshot', side_effect=failure):
            with self.assertRaises(PermissionError) as caught:
                T.inspect_source(self.source)
        self.assertIs(caught.exception, failure)

    def durable_copy(self, source, destination):
        with open(source, 'rb') as inp, open(destination, 'xb') as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        return destination

    def test_durable_callback_used_for_every_file_and_verified(self):
        before = T.snapshot(self.source)
        stage = self.destination.parent/'prepared'
        real = os.fsync
        with patch.object(os, 'fsync', wraps=real) as synced:
            result = T.copy_verified(self.source, stage, before, copy_function=self.durable_copy)
        self.assertEqual(synced.call_count, len(before[1]))
        self.assertEqual(result, before)
        self.assertEqual(T.content_inventory(T.snapshot(stage)), T.content_inventory(before))
        self.assertEqual(T.snapshot(self.source), before)

    def test_durable_callback_io_error_retains_original_and_cause(self):
        error = PermissionError('durable output denied')
        before = T.snapshot(self.source)
        def fail(src, dst):
            raise error
        with self.assertRaises(PermissionError) as caught:
            T.copy_verified(self.source, self.destination.parent/'partial', copy_function=fail)
        self.assertIs(caught.exception, error)
        self.assertIsInstance(error.__cause__, shutil.Error)
        self.assertEqual(T.snapshot(self.source), before)

    def test_prepared_install_single_rename_without_postrename_probe(self):
        stage = self.destination.parent/'prepared'
        expected = T.copy_verified(self.source, stage)
        target = self.destination.parent/'new-savedata'
        real_rename = Path.rename
        with patch.object(Path, 'rename', autospec=True, side_effect=real_rename) as rename, patch.object(T, 'snapshot', wraps=T.snapshot) as snap:
            T.install_prepared_savedata(stage, target, expected)
        self.assertEqual(rename.call_count, 1)
        self.assertEqual([call.args[0] for call in snap.call_args_list], [stage])
        self.assertEqual(self.contents(target), self.contents(self.source))

    def test_prepared_mismatch_and_occupied_target_do_not_rename(self):
        stage = self.destination.parent/'prepared'
        expected = T.copy_verified(self.source, stage)
        (stage/'extra').write_bytes(b'changed')
        with patch.object(Path, 'rename') as rename:
            with self.assertRaisesRegex(ValueError, '校验失败'):
                T.install_prepared_savedata(stage, self.destination.parent/'free', expected)
            with self.assertRaisesRegex(ValueError, '路径冲突'):
                T.install_prepared_savedata(stage, self.destination, expected)
            rename.assert_not_called()

    def test_prepared_rename_failure_type_is_not_masked(self):
        stage = self.destination.parent/'prepared'
        expected = T.copy_verified(self.source, stage)
        error = PermissionError('rename denied')
        with patch.object(Path, 'rename', side_effect=error):
            with self.assertRaises(PermissionError) as caught:
                T.install_prepared_savedata(stage, self.destination.parent/'free', expected)
        self.assertIs(caught.exception, error)
        self.assertTrue(stage.exists())

    def test_ordinary_postrename_verification_failure_rolls_back_save_aux_trophy(self):
        aux = self.auxiliary_records()
        source_trophy = self.source.parent/'trophies.log'
        target_trophy = self.destination.parent/'trophies.log'
        source_trophy.write_bytes(b'14 180\n')
        target_trophy.write_bytes(b'15 250\n')
        old, source = self.contents(self.destination), T.snapshot(self.source)
        helper, snapshot = T.install_prepared_savedata, T.snapshot
        renamed = False
        def install(*args):
            nonlocal renamed
            helper(*args)
            renamed = True
        def fail_after(path):
            if renamed and Path(path) == self.destination:
                raise OSError('post-install verification IO failure')
            return snapshot(path)
        with patch.object(T, 'install_prepared_savedata', side_effect=install), patch.object(T, 'snapshot', side_effect=fail_after):
            with self.assertRaisesRegex(ValueError, '已保持或恢复'):
                T.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)
        self.assertEqual(T.snapshot(self.source), source)
        for name, data in aux.items():
            self.assertEqual((self.root/'mod'/name).read_bytes(), data)
        self.assertEqual(target_trophy.read_bytes(), b'15 250\n')
        self.assertEqual(source_trophy.read_bytes(), b'14 180\n')
        self.assertFalse((self.root/'.save-import.lock').exists())
        self.assertEqual(len(list(self.destination.parent.glob('.savedata-import-rejected-*'))), 1)

    def test_ordinary_success_still_uses_trophy_merge_and_full_old_branch_backup(self):
        old = self.contents(self.destination)
        source = self.source.parent/'trophies.log'
        target = self.destination.parent/'trophies.log'
        source.write_bytes(b'14 180\n35 300\n')
        target.write_bytes(b'14 200\n15 250\n')
        with patch.object(T, 'install_prepared_savedata', wraps=T.install_prepared_savedata) as install:
            info = T.apply(self.install)
        install.assert_called_once()
        self.assertEqual(target.read_bytes(), b'14 180\n15 250\n35 300\n')
        self.assertEqual(source.read_bytes(), b'14 180\n35 300\n')
        self.assertEqual(self.contents(Path(info['backup'])), old)
        self.assertEqual((Path(info['trophy_backup'])/'target.log').read_bytes(), b'14 200\n15 250\n')


if __name__ == '__main__':
    unittest.main()
