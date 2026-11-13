"""Guard source drift and real-callback extraction without game data or services."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_game_hardening as hardening
from prepare_game_hardening_regression import function, generate


class GameHardeningPreparationTests(unittest.TestCase):
    def test_missing_and_ambiguous_anchors_fail(self):
        for text in ('before after', 'needle needle'):
            with self.assertRaises(ValueError):
                hardening.once(text, 'needle', 'replacement')

    def test_fixed_function_refuses_source_drift(self):
        source = 'void example() { return; }'
        marker = 'void example() {'
        with patch.dict(hardening.PINNED, {marker: hashlib.sha256(source.encode()).hexdigest()}):
            self.assertEqual(hardening.pinned(source, marker), source)
            for changed in (source.replace('return;', 'throw 1;'), source + source):
                with self.assertRaises(ValueError):
                    hardening.pinned(changed, marker)

    def test_callback_parser_handles_digit_separators_and_quoted_braces(self):
        callback = '''void example() {
    if (now < 5'000) return;
    // Braces and quotes in comments: } ' {
    auto nested = [] { return "}"; };
    char close = '}';
    if (now < 6'000) { nested(); }
}'''
        self.assertEqual(function(callback + '\nvoid next() {}', 'void example() {'), callback)

    def test_wrong_stage_fails_without_outputs(self):
        with tempfile.TemporaryDirectory(prefix='game-hardening-test-') as temp:
            source = Path(temp) / 'input.cpp'
            source.write_text('void example() {}', encoding='utf-8')
            output = Path(temp) / 'output'
            with self.assertRaises(ValueError):
                generate(source, output)
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                hardening.apply(source.read_text('utf-8'))

    def test_relay_metadata_preserves_dispatch_and_original_header_declaration(self):
        dispatch = '''void Dispatch(const std::vector<u8>& wrapped) {
    Core::Bloodborne::OnBbscPvpPacket(frame[5], frame[6]);
}'''
        updated = hardening.apply_relay(dispatch)
        self.assertIn('void Dispatch(const std::vector<u8>& wrapped)', updated)
        self.assertIn('OnBbscPvpPacket(frame[5], frame[6], sender);', updated)
        with self.assertRaises(ValueError):
            hardening.apply_relay(updated)
        original = 'void OnBbscPvpPacket(std::uint8_t kind, std::uint8_t on);'
        header = hardening.apply_header(original)
        self.assertIn(original, header)
        self.assertIn('std::uint32_t sender);', header)
        with self.assertRaises(ValueError):
            hardening.apply_header(header)


if __name__ == '__main__':
    unittest.main()
