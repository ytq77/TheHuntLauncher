import hashlib
from contextlib import ExitStack
import io
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
import client
from synthetic_world_fixture import fixture_pair
RAW, GOOD = fixture_pair()
from settings_bridge import sync_settings

NETWORK = {"shadnet": "example.test:31413", "webapi": "http://example.test:31415",
           "community": "http://example.test:20443", "relay": "example.test:20444"}


def account_snapshot(**values):
    snapshot = {'state': 'connected', 'reconnects': 0, 'attempts': 0, 'since_disconnect_s': None,
                'last_disconnect': None, 'remote': '198.18.6.61:31313', 'already_in_for_s': None, 'message': ''}
    snapshot.update(values)
    return snapshot


def bridge_stats(**values):
    stats = {'failures': 0, 'offline_rejections': 0, 'upstream_failures': 0, 'consecutive_failures': 0,
             'reskind_errors': 0, 'last_failure': None, 'last_reskind_error': None}
    stats.update(values)
    return stats


class PublicOnlineStatusTests(unittest.TestCase):
    def test_public_state_notice_thresholds(self):
        notice = client.public_notice
        self.assertEqual(notice(account_snapshot()), '')
        for down in (10, 19.9):
            self.assertEqual(notice(account_snapshot(state='reconnecting', since_disconnect_s=down, attempts=2)), '')
        for down in (20, 60, 119.9):
            self.assertEqual(notice(account_snapshot(state='reconnecting', since_disconnect_s=down, attempts=3)),
                             '公共账号连接中断，正在自动重连（已尝试 3 次）。私服召唤不受影响。')
        late = notice(account_snapshot(state='reconnecting', since_disconnect_s=200, attempts=7))
        self.assertEqual(late, notice(account_snapshot(state='reconnecting', since_disconnect_s=120)))
        self.assertIn('公共内容暂时不可用，正在后台重连', late)
        self.assertIn('无法连接至服务器', late); self.assertNotIn('别处登录', late)
        self.assertEqual(notice(account_snapshot(state='reconnecting', since_disconnect_s=200, already_in_for_s=179)), late)
        lingering = notice(account_snapshot(state='reconnecting', since_disconnect_s=200, already_in_for_s=181))
        self.assertEqual(lingering, late + '公共账号可能在别处登录，或旧连接尚未释放。')
        self.assertEqual(notice(account_snapshot(state='failed', message='公共账号密码不正确。')),
                         '公共账号密码不正确。请在启动器中重新验证公共账号后重启游戏。')
        self.assertEqual(notice(account_snapshot(state='closed')), '')
        drop = {'at': '2026-11-08T17:08:20+08:00', 'reason': 'eof', 'connected_for_s': 95.2}
        bridge = SimpleNamespace(login_ok=True, account=SimpleNamespace(snapshot=lambda: account_snapshot(
            state='reconnecting', since_disconnect_s=60, attempts=1, reconnects=2, last_disconnect=drop)),
            stats=lambda: bridge_stats(failures=9, offline_rejections=5, upstream_failures=4,
                                       consecutive_failures=1, reskind_errors=2,
                                       last_reskind_error={'at': 'x', 'endpoint': '/a/b', 'MessageId': 'M'}))
        state = client.public_state(bridge, 'ui-fixture')
        self.assertEqual(state, {
            'ui_session': 'ui-fixture', 'account_connected': False, 'game_authenticated': True, 'failures': 9,
            'account_state': 'reconnecting', 'reconnects': 2, 'reconnect_attempts': 1, 'last_disconnect': drop,
            'consecutive_failures': 1, 'offline_rejections': 5, 'upstream_failures': 4, 'reskind_errors': 2,
            'last_reskind_error': {'at': 'x', 'endpoint': '/a/b', 'MessageId': 'M'},
            'notice': '公共账号连接中断，正在自动重连（已尝试 1 次）。私服召唤不受影响。'})
        self.assertEqual(json.loads(json.dumps(state)), state)

    def test_public_events_log_rotates_and_never_fails(self):
        with tempfile.TemporaryDirectory() as area, patch.object(client, 'report_line') as report:
            root = Path(area)
            log = client.PublicOnlineEvents(root/'data/user/public-online-events.log', anchor=root/'data', limit=400)
            log.write({'at': '2026-11-08T17:08:20+08:00', 'kind': 'connected',
                       'remote': '198.18.6.61:31313', 'keep_alive': True})
            # data/ marks a finished first initialization; the log must never create it.
            self.assertFalse((root/'data').exists())
            (root/'data').mkdir(); log.flush()
            self.assertEqual(log.path.read_text(encoding='utf-8'),
                             '2026-11-08T17:08:20+08:00 connected remote=198.18.6.61:31313 keep_alive=true\n')
            for attempt in range(40):
                log.write({'kind': 'retry_failed', 'attempt': attempt, 'reason': 'rejected:6', 'message': '含 空格'})
            rotated = log.path.with_name(log.path.name + '.1')
            self.assertTrue(rotated.is_file())
            self.assertFalse(log.path.with_name(log.path.name + '.2').exists())
            self.assertLessEqual(log.path.stat().st_size, 400 + 120)
            lines = (rotated.read_text(encoding='utf-8') + log.path.read_text(encoding='utf-8')).splitlines()
            self.assertTrue(lines[-1].endswith(' retry_failed attempt=39 reason=rejected:6 message="含 空格"'), lines[-1])
            self.assertEqual(report.call_count, 41)
            self.assertTrue(report.call_args.args[0].startswith('public online: '))
            # Unwritable target and malformed events never raise into the game session.
            (root/'blocked').mkdir()
            blocked = client.PublicOnlineEvents(root/'blocked', anchor=root)
            blocked.write({'kind': 'drop', 'reason': 'eof'}); blocked.flush()
            blocked.write(None)
            self.assertTrue((root/'blocked').is_dir())
            under_file = client.PublicOnlineEvents(log.path/'nested.log')
            under_file.write({'kind': 'drop'}); under_file.flush()


class SessionTests(unittest.TestCase):
    def test_main_keeps_public_account_alive_with_event_log(self):
        mod = self.root/'mod'
        mod.mkdir()
        for file, marker in (("LICENSE.txt", "GNU GENERAL PUBLIC LICENSE"),
                             ("NOTICE.txt", "github.com/Classmg22/BBSC"), ("SUPPORTERS.txt", "BBSC SUPPORTERS")):
            (mod/file).write_text(marker, encoding='utf-8')
        (self.root/'profile.json').write_text(json.dumps(dict(self.profile, public_online=True)), encoding='utf-8')
        environment = {'BBSC_ACCOUNT': 'fixture', 'BBSC_PASSWORD': 'private-value',
                       'THEHUNT_PUBLIC_ACCOUNT': 'Public_fixture', 'THEHUNT_PUBLIC_PASSWORD': 'public-secret'}
        with (patch.dict(os.environ, environment),
              patch.object(client, 'Account', return_value=MagicMock()) as make_account,
              patch.object(client, 'Bridge', return_value=MagicMock()),
              patch.object(client, 'run_session', return_value=0) as run,
              patch.object(client, 'state')):
            self.assertEqual(client.main(), 0)
            self.assertNotIn('THEHUNT_PUBLIC_PASSWORD', os.environ)
        self.assertEqual(make_account.call_args.args, ('Public_fixture', 'public-secret'))
        self.assertTrue(make_account.call_args.kwargs['keep_alive'])
        events = make_account.call_args.kwargs['on_event'].__self__
        self.assertIsInstance(events, client.PublicOnlineEvents)
        self.assertEqual((events.path, events.anchor), (self.root/'data/user/public-online-events.log', self.root/'data'))
        self.assertIs(run.call_args.args[4], events)
        self.assertFalse((self.root/'data').exists())

    def test_session_waits_for_public_reconnect_before_launch(self):
        account = SimpleNamespace(wait_connected=Mock(return_value=False))
        events = Mock()
        with (patch.object(client, 'prepare') as prepare,
              patch.object(client.world_recovery, 'begin') as begin):
            with self.assertRaisesRegex(ValueError, '初始化期间公共账号连接已断开'):
                client.run_session(self.profile, 'fixture', 'fixture', SimpleNamespace(account=account), events)
        prepare.assert_called_once_with(self.profile)
        events.flush.assert_called_once_with()
        account.wait_connected.assert_called_once_with(30)
        begin.assert_not_called()
    def test_observation_failures_keep_owned_child_until_real_exit(self):
        recovery = client.world_recovery
        engine = self.root/'engine'
        engine.mkdir()
        (engine/'run.py').write_text(
            "from pathlib import Path\nimport time\ntime.sleep(.35)\n"
            "Path('child-finished').write_text('finished')\nraise SystemExit(17)\n", encoding='utf-8')
        notices = Mock()
        notices.poll.side_effect = RuntimeError('unexpected trophy parser failure')
        account = SimpleNamespace(connected=True, wait_connected=Mock(return_value=True),
                                  snapshot=account_snapshot)
        bridge = SimpleNamespace(account=account, origin='http://fixture',
                                 login_ok=False, failures=0, stats=bridge_stats)
        mapping = Mock()
        json_file = recovery.json_file

        def fail_status(path, value):
            if path.name in {'launcher-state.json', 'public-online-state.json',
                             'latest-observation.json', 'last-exit.json', 'latest-result.json'}:
                raise PermissionError('fixture status write denied')
            json_file(path, value)

        finalize = recovery.finalize

        def finish(*args, **kwargs):
            if args[1] is not None:
                self.assertTrue((engine/'child-finished').is_file())
                run = recovery.layout(self.root)[0]/'runs'/f'{args[1]}.json'
                self.assertEqual(json.loads(run.read_text())['exit_code'], 17)
            return finalize(*args, **kwargs)

        with ExitStack() as mocks:
            mocks.enter_context(patch.object(client, 'prepare'))
            mocks.enter_context(patch.object(recovery, 'require_stopped'))
            mocks.enter_context(patch.object(client, 'TrophyNotices', return_value=notices))
            mocks.enter_context(patch.object(client, 'sync_settings', return_value=({}, {})))
            mocks.enter_context(patch.object(client, 'build_environment',
                                             return_value=(os.environ.copy(), NETWORK)))
            mocks.enter_context(patch.object(client.relay_auth, 'environment', return_value={}))
            mocks.enter_context(patch.object(recovery, 'json_file', side_effect=fail_status))
            open_seal = mocks.enter_context(patch.object(recovery, 'quit_seal',
                                                        side_effect=[OSError('mapping busy'), mapping]))
            mocks.enter_context(patch.object(recovery, 'read_quit_seal', return_value=0))
            observe = mocks.enter_context(patch.object(recovery, 'poll',
                                                       side_effect=TypeError('malformed observation')))
            completed = mocks.enter_context(patch.object(recovery, 'finalize', side_effect=finish))
            mocks.enter_context(patch.object(client, 'report_failure', side_effect=lambda *_: None))
            self.assertEqual(client.run_session(self.profile, 'fixture', 'fixture', bridge), 17)
        self.assertEqual(open_seal.call_count, 2)
        account.wait_connected.assert_called_once_with(30)
        self.assertEqual(len([call for call in completed.call_args_list if call.args[1] is not None]), 1)
        self.assertGreaterEqual(notices.poll.call_count, 1)
        self.assertGreaterEqual(observe.call_count, 1)
        mapping.close.assert_called_once_with()

    def test_exit_record_failure_cannot_authorize_replay_or_skip_finalization(self):
        recovery = client.world_recovery
        user = self.root/'data/user'
        with (patch.object(recovery, 'process_exited', side_effect=PermissionError('runs is read-only')),
              patch.object(recovery, 'read_quit_seal', return_value=0) as seal,
              patch.object(recovery, 'finalize', side_effect=ValueError('fixture blocked')) as finish):
            self.assertEqual(client.finish_game(user, 'fixture', object(), 0, 'start', 'fixture.log'), 1)
        seal.assert_called_once()
        finish.assert_called_once_with(self.root, 'fixture', quit_sequence=0, observed_exit=0)
        self.assertEqual(json.loads((user/'last-exit.json').read_text())['exit_code'], 0)
        outcome = json.loads((user/'world-recovery/latest-result.json').read_text())
        self.assertEqual((outcome['state'], outcome['game_exit_code']), ('review-required', 0))

    def test_startup_failure_text_survives_status_path_guard(self):
        import runpy
        entry = self.root/'client.py'
        shutil.copyfile(client.__file__, entry)
        (self.root/'.save-conversion-pending.json').write_text('{}', encoding='utf-8')
        stderr = io.StringIO()
        with (patch.object(client.world_recovery, 'plain_path', side_effect=ValueError('fixture path guard')),
              patch('sys.stderr', stderr), patch('sys.stdout', io.StringIO())):
            with self.assertRaises(SystemExit) as exit:
                runpy.run_path(str(entry), run_name='__main__')
        self.assertEqual(exit.exception.code, 1)
        status = json.loads((self.root/'launcher-state.json').read_text(encoding='utf-8'))
        self.assertEqual(status['phase'], 'error')
        self.assertIn('存在未完成的原版存档替换', status['message'])
        self.assertIn(status['message'], stderr.getvalue())

    def test_first_initialization_imports_real_trophy_log_without_touching_source(self):
        source = self.install/'user/trophies.log'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'14 180\n35 300\n')
        client.prepare(self.profile)
        self.assertEqual((self.root/'data/user/trophies.log').read_bytes(), source.read_bytes())
        self.assertEqual((self.root/'data/trophy-import-backup/source-0.log').read_bytes(), source.read_bytes())

    def test_failed_trophy_write_never_publishes_first_save_branch(self):
        source = self.install/'user/trophies.log'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'14 180\n')
        with patch.object(client.trophy_sync, 'install', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                client.prepare(self.profile)
        self.assertFalse((self.root/'data').exists())
        self.assertEqual(source.read_bytes(), b'14 180\n')

    def test_first_import_corruption_is_refused_and_unpublished_stage_retained(self):
        origin = self.install/'user/savedata'
        file = origin/'1/CUSA00207/SPRJ0005/userdata0000'
        file.parent.mkdir(parents=True)
        file.write_bytes(RAW)
        (file.parent/'userdata0010').write_bytes(b'profile'+bytes(84))
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
        self.assertEqual(file.read_bytes(), RAW)

    def test_first_import_source_mutation_is_refused(self):
        origin = self.install/'user/savedata'
        file = origin/'1/CUSA00207/SPRJ0005/userdata0000'
        file.parent.mkdir(parents=True)
        file.write_bytes(RAW)
        (file.parent/'userdata0010').write_bytes(b'profile'+bytes(84))
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
        save = self.install / "user/savedata/1/CUSA00207/SPRJ0005/userdata0000"
        save.parent.mkdir(parents=True)
        save.write_bytes(RAW)
        (save.parent/'userdata0010').write_bytes(b'profile'+bytes(84))
        client.prepare(self.profile)
        target = self.root / "data/user/savedata/1/CUSA00207/SPRJ0005/userdata0000"
        self.assertEqual(target.read_bytes(), RAW)
        target.write_bytes(b"independent-progress")
        client.prepare(self.profile)
        self.assertEqual(target.read_bytes(), b"independent-progress")
        self.assertEqual(save.read_bytes(), RAW)
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
