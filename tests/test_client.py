import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
import client
from settings_bridge import sync_settings

NETWORK = {"shadnet": "example.test:31413", "webapi": "http://example.test:31415",
           "community": "http://example.test:20443", "relay": "example.test:20444"}


class SessionTests(unittest.TestCase):
    def test_first_import_corruption_is_refused_and_unpublished_stage_retained(self):
        origin = self.install/'user/savedata'
        file = origin/'1/CUSA00207/SPRJ0005/userdata0000'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'original')
        copy = shutil.copytree
        def corrupt(source, target, *args, **kwargs):
            result = copy(source, target, *args, **kwargs)
            if Path(source) == origin:
                (Path(target)/file.relative_to(origin)).write_bytes(b'corrupt')
            return result
        with patch.object(shutil, 'copytree', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, '副本校验失败'):
                client.prepare(self.profile)
        self.assertFalse((self.root/'data').exists())
        self.assertTrue(list(self.root.glob('data.setup-*')))
        self.assertEqual(file.read_bytes(), b'original')

    def test_first_import_source_mutation_is_refused(self):
        origin = self.install/'user/savedata'
        file = origin/'1/CUSA00207/SPRJ0005/userdata0000'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'original')
        copy = shutil.copytree
        def mutate(source, target, *args, **kwargs):
            result = copy(source, target, *args, **kwargs)
            if Path(source) == origin:
                file.write_bytes(b'new-save')
            return result
        with patch.object(shutil, 'copytree', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                client.prepare(self.profile)
        self.assertFalse((self.root/'data').exists())

    def test_first_import_refuses_old_auxiliary_without_removing_it(self):
        from test_offline_aux import record
        p = self.root/'mod/own-world/own-save-0.bin.restored'
        p.parent.mkdir(parents=True)
        data = record(0x574F4242, [(1, bytes(125))])
        p.write_bytes(data)
        with self.assertRaisesRegex(ValueError, '旧无缝联机记录'):
            client.prepare(self.profile)
        self.assertFalse((self.root/'data').exists())
        self.assertEqual(p.read_bytes(), data)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "package"
        self.root.mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1] / "launcher/feature-catalog.json", self.root)
        self.install = self.base / "normal"
        self.install.mkdir()
        (self.install / "Bloodborne.exe").write_bytes(b"test-launcher")
        self.ini = self.install / "bbport.ini"
        self.ini.write_text("output_res=1280x720\nupscaler=fsr3\npreset=1\n")
        self.game = self.base / "CUSA03173"
        self.game.mkdir()
        (self.game / "eboot.bin").write_bytes(b"test-eboot")
        self.appdata = self.base / "appdata"
        (self.appdata / "bbport-launcher").mkdir(parents=True)
        self.settings = self.appdata / "bbport-launcher/settings.json"
        self.settings.write_text('{"frame_cap":"30","online_password":"private-value","thd_token":"private-token"}')
        self.profile = {"install": str(self.install), "graphics_source": str(self.ini),
                        "game": str(self.game), "network": NETWORK}
        self.addCleanup(patch.stopall)
        patch.object(client, "ROOT", self.root).start()
        patch.object(client, "EBOOT_SHA256", hashlib.sha256(b"test-eboot").hexdigest()).start()
        patch.object(client, 'require_stopped').start()
        patch.dict(os.environ, {"APPDATA": str(self.appdata)}).start()

    def test_prepare_copies_once_and_preserves_independent_progress(self):
        save = self.install / "user/savedata/1/test/save.bin"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"original-save")
        client.prepare(self.profile)
        target = self.root / "data/user/savedata/1/test/save.bin"
        self.assertEqual(target.read_bytes(), b"original-save")
        target.write_bytes(b"independent-progress")
        client.prepare(self.profile)
        self.assertEqual(target.read_bytes(), b"independent-progress")
        self.assertEqual(save.read_bytes(), b"original-save")
        self.assertFalse((self.root / "fsr4_shaders").exists())

    def test_per_pc_settings_refresh_without_account_leak_or_high_pc_override(self):
        env, snapshot = sync_settings(self.root, self.profile, self.settings)
        self.assertEqual(env["BB_FPS_LIMIT"], "30")
        self.assertEqual(snapshot["graphics"]["output_res"], "1280x720")
        self.assertNotIn("BB_RENDER_RES", env)
        self.assertNotIn("BB_HDR", env)
        self.assertNotIn("private-", (self.root / "settings-snapshot.json").read_text())
        self.ini.write_text("output_res=960x540\nupscaler=fsr3\npreset=2\n")
        _, snapshot = sync_settings(self.root, self.profile, self.settings)
        self.assertEqual(snapshot["graphics"]["output_res"], "960x540")

    def test_network_is_required_and_credentials_cannot_be_embedded_in_urls(self):
        self.assertEqual(client.validate_network(NETWORK), NETWORK)
        for key, value in (("shadnet", ""), ("shadnet", "host:99999"), ("relay", "user:pass@host:123"),
                           ("webapi", "file:///etc/passwd"), ("community", "http://user:pass@host:20443")):
            invalid = {**NETWORK, key: value}
            with self.assertRaises(ValueError):
                client.validate_network(invalid)

    def test_194_graphics_options_follow_ini_and_low_vram_preference(self):
        self.ini.write_text("output_res=1280x720\nupscaler=fsr411\npreset=1\n"
                            "fsr411_backend=dx12\nobject_motion=1\nobject_motion_cache=1\nrebirth=1\n")
        self.settings.write_text('{"low_vram":"0"}')
        original = self.ini.read_bytes()
        env, snapshot = sync_settings(self.root, self.profile, self.settings)
        self.assertEqual(env["BB_LOW_VRAM"], "0")
        self.assertEqual(env["BB_SHARED_GRAPHICS_CONFIG"], "1")
        self.assertEqual(env["BB_FSR411_BACKEND"], "dx12")
        self.assertEqual(env["BB_FSR411_NATIVE"], "1")
        self.assertEqual(env["BB_OBJECT_MOTION_CACHE"], "1")
        self.assertEqual(env["BB_REBIRTH"], "0")
        self.assertIn("rebirth=0\n", (self.root / "data/bbport.ini").read_text())
        self.assertNotIn("rebirth=1", (self.root / "data/bbport.ini").read_text())
        self.assertEqual(self.ini.read_bytes(), original)
        self.assertEqual(snapshot["runtime_overrides"], {"rebirth": "0"})
        self.ini.write_text(self.ini.read_text().replace("object_motion=1", "object_motion=0"))
        env, _ = sync_settings(self.root, self.profile, self.settings)
        self.assertEqual(env["BB_OBJECT_MOTION_CACHE"], "0")

    def test_input_refresh_keeps_first_backup_and_preserves_target_if_source_missing(self):
        target = self.root / "data/input.ini"
        target.parent.mkdir()
        target.write_bytes(b"original-isolated-bindings")
        source = self.ini.with_name("input.ini")
        source.write_bytes(b"updated-normal-bindings")
        _, snapshot = sync_settings(self.root, self.profile, self.settings)
        self.assertEqual(target.read_bytes(), b"updated-normal-bindings")
        self.assertEqual(target.with_name("input.before-settings-sync.ini").read_bytes(), b"original-isolated-bindings")
        self.assertTrue(snapshot["input"]["copied"])
        source.write_bytes(b"second-normal-bindings")
        sync_settings(self.root, self.profile, self.settings)
        self.assertEqual(target.read_bytes(), b"second-normal-bindings")
        self.assertEqual(target.with_name("input.before-settings-sync.ini").read_bytes(), b"original-isolated-bindings")
        source.unlink()
        _, snapshot = sync_settings(self.root, self.profile, self.settings)
        self.assertFalse(snapshot["input"]["copied"])
        self.assertEqual(target.read_bytes(), b"second-normal-bindings")

    def test_session_routes_and_saves_use_profile_not_parent_environment(self):
        (self.root / "mod").mkdir()
        (self.root / "mod/mod.json").write_text('{"settings":[]}')
        env, snapshot = sync_settings(self.root, self.profile, self.settings)
        with patch.dict(os.environ, {"BB_SHADNET_SERVER": "wrong:1", "BB_CONFIG": "wrong.ini",
                                   "BBSC_PASSWORD": "do-not-inherit", "BB_RENDER_RES": "2560x1440"}):
            actual, network = client.build_environment(self.profile, "test-user", "test-pass", env, snapshot)
        self.assertEqual(actual["BB_SHADNET_SERVER"], NETWORK["shadnet"])
        self.assertEqual(actual["BB_FPS_LIMIT"], "30")
        self.assertEqual(actual["BB_USER_DIR"], str(self.root / "data/user"))
        self.assertNotIn("BBSC_PASSWORD", actual)
        self.assertNotIn("BB_RENDER_RES", actual)
        self.assertEqual(actual["SHADPS4_BBSC_DISCORD"], "0")
        self.assertEqual(actual["SHADPS4_BBSC_SHARED_PROGRESS"], "0")

    def test_feature_preferences_reach_session_and_snapshot_without_changing_server(self):
        (self.root / "mod").mkdir()
        (self.root / "mod/mod.json").write_text('{"settings":[]}')
        (self.root / "features.json").write_text(json.dumps({"schema": 1, "values": {
            "shared_progress": True, "discord": True, "scale_boss_hp": 60, "invasions": False}}))
        settings_env, snapshot = sync_settings(self.root, self.profile, self.settings)
        actual, network = client.build_environment(self.profile, "test-user", "test-pass", settings_env, snapshot)
        self.assertEqual(actual["SHADPS4_BBSC_SHARED_PROGRESS"], "1")
        self.assertEqual(actual["SHADPS4_BBSC_DISCORD"], "1")
        self.assertEqual(actual["BBSC_SCALE_BOSS_HP"], "60")
        self.assertEqual(actual["SHADPS4_BBSC_INVADE_ANYWHERE"], "0")
        self.assertEqual(network, NETWORK)
        self.assertEqual(snapshot["features"]["scale_boss_hp"], 60)
        self.assertNotIn("test-pass", json.dumps(snapshot))

    def test_wrong_game_stops_before_creating_data(self):
        (self.game / "eboot.bin").write_bytes(b"wrong")
        with self.assertRaises(ValueError):
            client.prepare(self.profile)
        self.assertFalse((self.root / "data").exists())

    def test_chinese_status_does_not_crash_english_windows_console(self):
        buffer = io.BytesIO()
        console = io.TextIOWrapper(buffer, encoding="cp1252")
        with patch.object(client.sys, "stdout", console):
            client.state("preparing", "正在准备猎具…")
        state = json.loads((self.root / "launcher-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["message"], "正在准备猎具…")
        self.assertIn(b"\\u", buffer.getvalue())

    def test_missing_notices_stop_before_launch_or_profile_access(self):
        with self.assertRaisesRegex(ValueError, "署名"):
            client.main()
        self.assertFalse((self.root / "data").exists())

    def test_interrupted_import_blocks_startup_before_game_or_save_access(self):
        (self.root / ".save-import.lock").write_text("unfinished import")
        with self.assertRaisesRegex(ValueError, "存档导入"):
            client.main()
        self.assertFalse((self.root / "data").exists())


if __name__ == "__main__":
    unittest.main()
