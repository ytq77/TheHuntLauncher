from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_session_recovery import canonical_patch

class SessionPatch(unittest.TestCase):
    def test_crlf_and_empty_context_are_canonicalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'change.patch'
            path.write_bytes(b'@@ -1,2 +1,2 @@\r\n\r\n-a\r\n+b\r\n')
            self.assertEqual(canonical_patch(path),b'@@ -1,2 +1,2 @@\n \n-a\n+b\n')

    def test_nonblank_context_and_patch_operations_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'change.patch'
            raw=b'--- a/test\n+++ b/test\n@@ -1 +1 @@\n-a\n+b\n'
            path.write_bytes(raw)
            self.assertEqual(canonical_patch(path),raw)

if __name__=='__main__':unittest.main()
