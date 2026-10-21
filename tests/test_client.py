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
