import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
import save_transfer as transfer


class SaveTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "bbsc"
        self.destination = self.root / "data/user/savedata"
        self.destination.mkdir(parents=True)
        self.install = self.base / "normal"
        self.install.mkdir()
        (self.install / "Bloodborne.exe").write_bytes(b"launcher")
        self.source = self.install / "user/savedata"
        self.source.mkdir(parents=True)
        self.appdata = self.base / "appdata"
        (self.appdata / "bbport-launcher").mkdir(parents=True)
        self.settings = self.appdata / "bbport-launcher/settings.json"
        self.settings.write_text("{}", encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(transfer, "ROOT", self.root).start()
        patch.dict(os.environ, {"APPDATA": str(self.appdata)}).start()
        patch.object(transfer, "game_running", return_value=False).start()
        self.write_save(self.source, "userdata0000", b"normal-first-character")
        self.write_save(self.source, "userdata0001", b"normal-second-character")
        self.write_save(self.source, "sce_sys/param.sfo", b"save-metadata")
        self.write_save(self.destination, "userdata0000", b"old-bbsc-character")
        self.write_save(self.destination, "userdata0009", b"only-in-bbsc")
        self.write_save(self.destination, "sce_sys/param.sfo", b"old-metadata")

    def write_save(self, folder, name, content):
        path = folder / "1/CUSA00207/SPRJ0005" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def contents(self, folder):
        return {str(path.relative_to(folder)): path.read_bytes()
                for path in folder.rglob("*") if path.is_file()}

    def test_inspection_changes_nothing_and_reports_all_files(self):
        before = transfer.snapshot(self.source)
        old = self.contents(self.destination)
        info, _ = transfer.inspect(self.install)
        self.assertEqual(info["file_count"], 3)
        self.assertIn("全部角色", info["notice"])
        self.assertEqual(transfer.snapshot(self.source), before)
        self.assertEqual(self.contents(self.destination), old)
        self.assertFalse((self.root / "save-backups").exists())

    def test_success_preserves_full_backup_source_timestamps_and_metadata(self):
        before = transfer.snapshot(self.source)
        old = self.contents(self.destination)
        expected = self.contents(self.source)
        result = transfer.apply(self.install)
        self.assertTrue(result["ok"])
        self.assertEqual(self.contents(self.destination), expected)
        self.assertEqual(self.contents(Path(result["backup"])), old)
        self.assertEqual(transfer.snapshot(self.source), before)
        self.assertEqual({name: item[3] for name, item in transfer.snapshot(self.destination)[1].items()},
                         {name: item[3] for name, item in before[1].items()})
        self.assertFalse((self.root / ".save-import.lock").exists())

    def test_missing_destination_is_allowed_after_initialization(self):
        shutil.rmtree(self.destination)
        info = transfer.apply(self.install)
        self.assertIsNone(info["backup"])
        self.assertEqual(self.contents(self.destination), self.contents(self.source))

    def test_custom_user_directory_uses_only_its_savedata(self):
        custom = self.base / "custom"
        shutil.copytree(self.source, custom / "savedata")
        self.settings.write_text(json.dumps({"user_dir": str(custom)}), encoding="utf-8")
        result, _ = transfer.inspect(self.install)
        self.assertEqual(result["source"], str(custom / "savedata"))

    def test_missing_empty_or_invalid_source_never_replaces_destination(self):
        old = self.contents(self.destination)
        for mode in ("missing", "empty", "metadata-only", "zero-userdata"):
            with self.subTest(mode=mode):
                if self.source.exists():
                    shutil.rmtree(self.source)
                if mode != "missing":
                    self.source.mkdir()
                if mode == "metadata-only":
                    self.write_save(self.source, "sce_sys/param.sfo", b"metadata")
                elif mode == "zero-userdata":
                    self.write_save(self.source, "userdata0000", b"")
                with self.assertRaises(ValueError):
                    transfer.apply(self.install)
                self.assertEqual(self.contents(self.destination), old)

    def test_uninitialized_package_requests_first_launch(self):
        shutil.rmtree(self.root / "data")
        with self.assertRaisesRegex(ValueError, "首次启动"):
            transfer.inspect(self.install)

    def test_copy_failure_keeps_original_and_source(self):
        old, source = self.contents(self.destination), transfer.snapshot(self.source)
        with patch.object(transfer.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)
        self.assertEqual(transfer.snapshot(self.source), source)

    def test_failed_final_rename_restores_original(self):
        old = self.contents(self.destination)
        rename = Path.rename

        def fail_stage(path, target):
            if path.name.startswith(".savedata-import-"):
                raise OSError("simulated commit failure")
            return rename(path, target)

        with patch.object(Path, "rename", fail_stage):
            with self.assertRaisesRegex(ValueError, "已保持或恢复"):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)
        self.assertEqual(len(list(self.destination.parent.glob(".savedata-import-*"))), 1)

    def test_failed_backup_rename_does_not_replace_original(self):
        old = self.contents(self.destination)
        with patch.object(Path, "rename", side_effect=OSError("backup refused")):
            with self.assertRaises(OSError):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)

    def test_failed_rollback_keeps_complete_backup_and_reports_location(self):
        old = self.contents(self.destination)
        rename = Path.rename

        def fail_install_and_rollback(path, target):
            if path.name.startswith(".savedata-import-") or path.parent.name.startswith("import-"):
                raise OSError("simulated destination lock")
            return rename(path, target)

        with patch.object(Path, "rename", fail_install_and_rollback):
            with self.assertRaisesRegex(ValueError, "完整旧存档保留在"):
                transfer.apply(self.install)
        backups = list((self.root / "save-backups").glob("import-*/savedata"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.contents(backups[0]), old)

    def test_corrupted_copied_file_fails_checksum_before_replacement(self):
        old = self.contents(self.destination)
        original_copy = shutil.copytree

        def copy_then_corrupt(source, destination, *args, **kwargs):
            result = original_copy(source, destination, *args, **kwargs)
            if Path(source) == self.source:
                self.write_save(destination, "userdata0001", b"damaged copy")
            return result

        with patch.object(transfer.shutil, "copytree", side_effect=copy_then_corrupt):
            with self.assertRaisesRegex(ValueError, "副本校验失败"):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)

    def test_source_changes_during_copy_are_rejected(self):
        old = self.contents(self.destination)
        original_copy = shutil.copytree

        def copy_then_change(source, destination, *args, **kwargs):
            result = original_copy(source, destination, *args, **kwargs)
            if Path(source) == self.source:
                self.write_save(self.source, "userdata0001", b"new autosave")
            return result

        with patch.object(transfer.shutil, "copytree", side_effect=copy_then_change):
            with self.assertRaisesRegex(ValueError, "复制期间"):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)

    def test_identical_and_nested_paths_are_rejected(self):
        for user_dir in (self.destination.parent, self.destination / "nested"):
            with self.subTest(user_dir=user_dir):
                self.settings.write_text(json.dumps({"user_dir": str(user_dir)}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "相同或互相包含"):
                    transfer.apply(self.install)

    def test_reparse_directories_and_symlink_files_are_rejected(self):
        old = self.contents(self.destination)
        lstat = Path.lstat
        cases = ((self.source.parent, stat.S_IFDIR, transfer.REPARSE_POINT),
                 (self.source / "1/CUSA00207/SPRJ0005/userdata0000", stat.S_IFLNK, 0))
        for marked, mode, attributes in cases:
            def linked_stat(path, *args, **kwargs):
                if path == marked:
                    return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                return lstat(path, *args, **kwargs)
            with self.subTest(path=marked), patch.object(Path, "lstat", linked_stat):
                with self.assertRaisesRegex(ValueError, "符号链接或目录联接"):
                    transfer.apply(self.install)
            self.assertEqual(self.contents(self.destination), old)

    def test_game_running_and_lock_conflict_are_rejected(self):
        old = self.contents(self.destination)
        with patch.object(transfer, "game_running", return_value=True):
            with self.assertRaisesRegex(ValueError, "仍在运行"):
                transfer.apply(self.install)
        lock = self.root / ".save-import.lock"
        lock.write_text("other import")
        with self.assertRaisesRegex(ValueError, "导入锁"):
            transfer.apply(self.install)
        self.assertEqual(lock.read_text(), "other import")
        self.assertEqual(self.contents(self.destination), old)

    def test_game_started_during_copy_prevents_commit(self):
        old = self.contents(self.destination)
        with patch.object(transfer, "game_running", side_effect=[False, True]):
            with self.assertRaisesRegex(ValueError, "仍在运行"):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)

    def test_changed_source_since_preview_is_rejected(self):
        old = self.contents(self.destination)
        custom = self.base / "custom"
        shutil.copytree(self.source, custom / "savedata")
        self.settings.write_text(json.dumps({"user_dir": str(custom)}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "路径已发生变化"):
            transfer.apply(self.install, self.source)
        self.assertEqual(self.contents(self.destination), old)

    def test_process_inspection_failure_stops_import(self):
        old = self.contents(self.destination)
        with patch.object(transfer, "game_running", side_effect=OSError("tasklist unavailable")):
            with self.assertRaisesRegex(ValueError, "无法确认"):
                transfer.apply(self.install)
        self.assertEqual(self.contents(self.destination), old)

    def test_cli_writes_utf8_json_for_success_and_failure(self):
        result = self.base / "result.json"
        self.assertEqual(transfer.main(["--install", str(self.install), "--result", str(result)]), 0)
        self.assertTrue(json.loads(result.read_text(encoding="utf-8"))["ok"])
        with patch.object(transfer, "game_running", return_value=True):
            self.assertEqual(transfer.main(["--install", str(self.install), "--result", str(result),
                                            "--apply"]), 1)
        self.assertIn("仍在运行", json.loads(result.read_text(encoding="utf-8"))["error"])
        self.assertEqual(transfer.main(["--install", str(self.base / "missing"),
                                        "--result", str(result)]), 1)
        self.assertIn("Bloodborne.exe", json.loads(result.read_text(encoding="utf-8"))["error"])


if __name__ == "__main__":
    unittest.main()
