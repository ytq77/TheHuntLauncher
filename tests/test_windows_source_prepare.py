"""Fresh extraction tests use synthetic tar/cache data and the real hash checker."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_source_prepare', ROOT / 'server/windows/prepare-sources.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def put(root, name, raw=b'// synthetic source\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def tar(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w:gz') as archive:
        for name, raw in files.items():
            entry = tarfile.TarInfo('synthetic-source/' + name)
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))


class FreshWindowsSources(unittest.TestCase):
    def fixture(self, root):
        windows = root / 'windows'
        recipe = root / 'recipe'
        # Only patch transforms are stubs; download/cache hash verification is real.
        recipe_code = f'''import importlib.util
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location("actual_recipe", {str(ROOT / 'server/recipe/prepare.py')!r})
actual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actual)
fetch = actual.fetch
def patch_gameserver(data): return data + b"# patched WebAPI\\n"
def patch_local_login(data): return data + b"# patched login\\n"
def patch_shadnet(root): (root / "security-applied.txt").write_text("synthetic security transform")
security = SimpleNamespace(patch_shadnet=patch_shadnet)
relay_auth = SimpleNamespace(patch_shadnet=lambda root: (root / "relay-auth-applied.txt").write_text("synthetic relay transform"))
'''
        put(recipe, 'prepare.py', recipe_code.encode())
        put(recipe, 'relay_auth.py', b'def patch_shadnet(root): (root / "relay-auth-applied.txt").write_text("synthetic relay transform")\n')
        for name in ('stun_server.cpp', 'stun_server.h'):
            put(recipe, 'fixes/' + name)
        put(windows, 'stdin_account.cpp')
        files = {'shadnet.tar.gz': {'CMakeLists.txt': b'project(Synthetic)\n',
                 'clientsample/CMakeLists.txt': b'# synthetic client\n', 'src/engine.cpp': b'// pinned clean\n'},
                 'protobuf.tar.gz': {'LICENSE': b'synthetic protobuf'},
                 'abseil.tar.gz': {'LICENSE': b'synthetic abseil'}}
        archives = []
        for name, members in files.items():
            path = windows / 'downloads' / name
            tar(path, members)
            archives.append({'name': name, 'repo': 'synthetic/unused', 'revision': '0' * 40,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        game = put(windows, 'downloads/bbsc/server/bbsc_gameserver.py', b'# synthetic game service\n')
        manifest = {'archives': archives, 'bbsc': {'repo': 'synthetic/unused', 'revision': '1' * 40,
                    'files': {'server/bbsc_gameserver.py': hashlib.sha256(game.read_bytes()).hexdigest()}}}
        put(recipe, 'sources.json', json.dumps(manifest).encode())
        return windows, recipe

    def run_prepare(self, windows, recipe):
        # A missing cache must never escape to a network fetch in these tests.
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            prepare.prepare(windows, recipe)

    def test_matching_revision_stamp_cannot_preserve_modified_sources_or_extra_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            windows, recipe = self.fixture(Path(temporary))
            self.run_prepare(windows, recipe)
            expected = {p.relative_to(windows / 'src').as_posix(): p.read_bytes()
                        for p in (windows / 'src').rglob('*') if p.is_file()}
            put(windows, 'src/src/engine.cpp', b'local modified input')
            put(windows, 'src/externals/protobuf/LICENSE', b'modified dependency')
            put(windows, 'src/local-secret.txt', b'private marker')
            put(windows, 'bbsc-game/local-secret.py', b'private marker')
            put(windows, 'bbsc-game/bbsc_gameserver.py', b'modified game input')
            self.run_prepare(windows, recipe)
            actual = {p.relative_to(windows / 'src').as_posix(): p.read_bytes()
                      for p in (windows / 'src').rglob('*') if p.is_file()}
            self.assertEqual(actual, expected)
            self.assertFalse((windows / 'bbsc-game/local-secret.py').exists())
            self.assertIn(b'# patched login', (windows / 'bbsc-game/bbsc_gameserver.py').read_bytes())
            self.assertEqual((windows / 'src/CMakeLists.txt').read_text().count('add_subdirectory(clientsample)'), 1)

    def test_corrupt_cache_and_failed_patch_preserve_previous_generated_inputs(self):
        for failure in ('hash', 'patch'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                windows, recipe = self.fixture(Path(temporary))
                self.run_prepare(windows, recipe)
                before = {p.relative_to(windows).as_posix(): p.read_bytes()
                          for folder in ('src', 'bbsc-game') for p in (windows / folder).rglob('*') if p.is_file()}
                if failure == 'hash':
                    put(windows, 'downloads/abseil.tar.gz', b'corrupt synthetic archive')
                else:
                    (windows / 'stdin_account.cpp').unlink()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.run_prepare(windows, recipe)
                for name, raw in before.items():
                    self.assertEqual((windows / name).read_bytes(), raw)
                self.assertFalse(list(windows.glob('.prepare-sources-*')))

    def test_tar_traversal_is_rejected_without_writing_outside_the_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'bad.tar.gz'
            tar(archive, {'../escaped.cpp': b'escape'})
            with self.assertRaises(tarfile.OutsideDestinationError):
                prepare.extract_source(archive, root / 'stage')
            self.assertFalse((root / 'escaped.cpp').exists())


if __name__ == '__main__':
    unittest.main()
