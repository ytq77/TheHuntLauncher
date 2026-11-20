"""Verify the Windows source recipe with a real Git repository above an extracted tree."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_windows_join import apply

BEFORE = b'set(CMAKE_CXX_STANDARD 17)\n'
AFTER = b'set(CMAKE_CXX_STANDARD 23)\n'


class PatchRootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.tree = self.repo / 'server/windows/src'
        self.tree.mkdir(parents=True)
        self.target = self.tree / 'CMakeLists.txt'
        self.target.write_bytes(BEFORE)
        self.outer = self.repo / self.target.name
        self.outer.write_bytes(BEFORE)
        self.patches = self.repo / 'patches'
        self.patches.mkdir()
        self.manifest = {'server': {'CMakeLists.txt': {
            'before': hashlib.sha256(BEFORE).hexdigest(),
            'after': hashlib.sha256(AFTER).hexdigest()}}}
        self.write_manifest()
        (self.patches / 'server.patch').write_bytes(
            b'diff --git a/CMakeLists.txt b/CMakeLists.txt\n'
            b'--- a/CMakeLists.txt\n+++ b/CMakeLists.txt\n@@ -1 +1 @@\n-' + BEFORE + b'+' + AFTER)

    def write_manifest(self):
        (self.patches / 'inputs.json').write_text(json.dumps(self.manifest), encoding='utf8')

    def test_patch_uses_the_extracted_tree_inside_a_checkout(self):
        for _ in range(2):
            apply('server', self.tree, self.patches)
            self.assertEqual(self.target.read_bytes(), AFTER)
            self.assertEqual(self.outer.read_bytes(), BEFORE)
        self.assertFalse((self.tree / '.git').exists())

    def test_wrong_input_is_rejected_without_writing(self):
        self.target.write_bytes(b'another revision\n')
        with self.assertRaisesRegex(ValueError, 'Unexpected input'):
            apply('server', self.tree, self.patches)
        self.assertEqual(self.target.read_bytes(), b'another revision\n')
        self.assertEqual(self.outer.read_bytes(), BEFORE)

    def test_unapproved_output_is_rejected(self):
        self.manifest['server']['CMakeLists.txt']['after'] = '0' * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'Output checksum mismatch'):
            apply('server', self.tree, self.patches)
        self.assertEqual(self.outer.read_bytes(), BEFORE)
