import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
import feature_settings


class GameplayFeatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(Path(__file__).resolve().parents[1] / "launcher/feature-catalog.json", self.root)

    def write(self, values, schema=1):
        (self.root / "features.json").write_text(json.dumps({"schema": schema, "values": values}))

    def test_recommendations_keep_coop_and_leave_permanent_merge_off(self):
        env, values = feature_settings.environment(self.root)
        self.assertTrue(values["warp_together"])
        self.assertTrue(values["guest_items"])
        self.assertTrue(values["death_penalty"])
        self.assertTrue(values["invasions"])
        self.assertFalse(values["shared_progress"])
        self.assertFalse(values["scale_npc_hunters"])
        self.assertEqual(env["SHADPS4_BBSC_DISCORD"], "0")
        self.assertEqual(env["BBSC_SCALE_BOSS_HP"], "100")
        self.assertFalse((self.root / "features.json").exists())

    def test_warp_join_reload_catalog_matches_reviewed_default(self):
        settings = feature_settings.catalog(self.root)
        matches = [item for item in settings if item["id"] == "warp_join_reload"]
        self.assertEqual(matches, [{
            "id": "warp_join_reload",
            "description": "旧的补救办法：跨图入房后再完整读一次图，用来避免灯光忽明忽暗。关闭时只读一次图，然后直接移到房主身边。如果入房后灯光不正常，可以打开。",
            "type": "bool", "default": False, "env": "SHADPS4_BBSC_WARP_JOIN",
            "category": "合作与探索", "title": "跨图入房后再读一次图",
            "scope": "本机，下次启动生效", "editable": True,
        }])
        self.assertEqual(sum(item.get("env") == "SHADPS4_BBSC_WARP_JOIN" for item in settings), 1)
        env, values = feature_settings.environment(self.root)
        self.assertIs(values["warp_join_reload"], False)
        self.assertEqual(env["SHADPS4_BBSC_WARP_JOIN"], "0")
        self.assertFalse((self.root / "features.json").exists())

    def test_warp_join_reload_legacy_preferences_and_repeated_toggles(self):
        legacy = {"shared_progress": True, "scale_boss_hp": 60, "invasions": False}
        for preference in (legacy, *(dict(legacy, warp_join_reload=value)
                                     for value in (True, False, True, False))):
            with self.subTest(preference=preference):
                self.write(preference)
                before = (self.root / "features.json").read_bytes()
                resolved = feature_settings.validate(self.root, preference)
                env, values = feature_settings.environment(self.root)
                expected = preference.get("warp_join_reload", False)
                self.assertIs(resolved["warp_join_reload"], expected)
                self.assertEqual(values, resolved)
                self.assertEqual(env["SHADPS4_BBSC_WARP_JOIN"], str(int(expected)))
                for key, value in legacy.items():
                    self.assertEqual(values[key], value)
                self.assertEqual((self.root / "features.json").read_bytes(), before)

    def test_warp_join_reload_rejects_non_boolean_without_rewriting_preferences(self):
        for value in (0, 1, "0", "false", None, [], {}):
            with self.subTest(value=value):
                preference = {"warp_join_reload": value}
                with self.assertRaisesRegex(ValueError, "跨图入房后再读一次图"):
                    feature_settings.validate(self.root, preference)
                self.write(preference)
                before = (self.root / "features.json").read_bytes()
                with self.assertRaisesRegex(ValueError, "跨图入房后再读一次图"):
                    feature_settings.environment(self.root)
                self.assertEqual((self.root / "features.json").read_bytes(), before)

    def test_saved_preferences_change_only_catalogued_environment(self):
        self.write({"shared_progress": True, "discord": True, "scale_boss_hp": 60,
                    "scale_damage": 0, "invasions": False})
        env, _ = feature_settings.environment(self.root)
        self.assertEqual(env["SHADPS4_BBSC_SHARED_PROGRESS"], "1")
        self.assertEqual(env["SHADPS4_BBSC_DISCORD"], "1")
        self.assertEqual(env["BBSC_SCALE_BOSS_HP"], "60")
        self.assertEqual(env["BBSC_SCALE_DAMAGE"], "0")
        self.assertEqual(env["SHADPS4_BBSC_INVADE_ANYWHERE"], "0")
        self.assertNotIn("BBSC_RELAY", env)
        self.assertNotIn("BB_SHADNET_PASSWORD", env)

    def test_malformed_and_unknown_values_are_rejected(self):
        for values in ({"shared_progress": "false"}, {"scale_boss_hp": True},
                       {"scale_boss_hp": -1}, {"scale_boss_hp": 301},
                       {"scale_damage": 1.5}, {"BBSC_PASSWORD": "secret"},
                       {"hooks": False}, {"seamless": False}, [], None):
            with self.subTest(values=values):
                self.write(values)
                with self.assertRaises(ValueError):
                    feature_settings.environment(self.root)

    def test_broken_json_schema_or_missing_catalog_fails_closed(self):
        self.write({}, schema=2)
        with self.assertRaises(ValueError):
            feature_settings.environment(self.root)
        (self.root / "features.json").write_text("not-json")
        with self.assertRaises(ValueError):
            feature_settings.environment(self.root)
        (self.root / "features.json").unlink()
        (self.root / "feature-catalog.json").unlink()
        with self.assertRaisesRegex(ValueError, "功能说明"):
            feature_settings.environment(self.root)

    def test_private_preferences_cannot_be_packaged(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from package import check_public
        with self.assertRaises(ValueError):
            check_public(["features.json"])

    def test_source_archive_excludes_initialized_launcher_state(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from source_archive import launcher_sources
        for name in ("features.json", "profile.json", "feature-catalog.json", "client.py"):
            (self.root / name).write_text("test")
        (self.root / ".private").mkdir()
        (self.root / ".private/account.clixml").write_text("private")
        from unittest.mock import patch
        with patch('source_archive.tracked', return_value=[
                'features.json', 'profile.json', 'feature-catalog.json', 'client.py', '.private/account.clixml']):
            included = {path.name for path in launcher_sources(self.root)}
        self.assertEqual(included, {"feature-catalog.json", "client.py"})


if __name__ == "__main__":
    unittest.main()
