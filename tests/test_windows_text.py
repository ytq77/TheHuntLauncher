import ast
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from localize_windows import GROUP, generate, translate_source


class WindowsTextTests(unittest.TestCase):
    def test_multiline_wide_strings_translate_without_changing_cpp(self):
        before = 'Message(L"Please pick "\n L"the game.", ERROR_FLAG); // untouched'
        actual, used = translate_source(before, {'Please pick the game.': '请选择游戏。'})
        self.assertEqual(ast.literal_eval(GROUP.search(actual)[0][1:]), '请选择游戏。')
        self.assertEqual(actual[GROUP.search(actual).end():], ', ERROR_FLAG); // untouched')
        self.assertEqual(used['Please pick the game.'], 1)

    def test_file_filter_preserves_patterns_pairs_and_terminators(self):
        before = r'Pick(L"All files\0*.exe\0All files\0*.*\0");'
        after, used = translate_source(before, {'All files': '所有文件', '*.exe': 'must not translate'})
        value = ast.literal_eval(GROUP.search(after)[0][1:])
        self.assertEqual(value.split('\0'), ['所有文件', '*.exe', '所有文件', '*.*', ''])
        self.assertNotIn('*.exe', used)

    def test_paths_codes_and_credential_markers_are_preserved(self):
        source = r'Fail(L"Missing: " + path); Check(L"LICENSE.txt", "GNU GENERAL PUBLIC LICENSE");'
        result, _ = translate_source(source, {'Missing: ': '缺失：'})
        self.assertIn(' + path)', result)
        self.assertIn('Check(L"LICENSE.txt", "GNU GENERAL PUBLIC LICENSE")', result)

    def test_changed_upstream_stops_before_publishing_partial_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('bbsc_mod', 'bbsc_launch'):
                (root / (name + '.cpp')).write_text('Message(L"different");')
            (root / 'catalog.json').write_text('{"expected":"replacement"}')
            with self.assertRaises(ValueError):
                generate(root, root / 'catalog.json')
            self.assertEqual(list(root.glob('*_zh_cn.cpp')), [])


if __name__ == '__main__':
    unittest.main()
