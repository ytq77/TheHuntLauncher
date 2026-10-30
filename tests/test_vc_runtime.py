from pathlib import Path
import hashlib
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import fetch_vc_runtime as runtime


class RuntimeInstaller(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.spec = dict(size=4, sha256=hashlib.sha256(b'good').hexdigest(),
                         version='14.51', url='https://example.invalid/pinned.exe')

    def test_verified_cache_is_reused_without_download(self):
        (self.folder / 'VC_redist.x64.exe').write_bytes(b'good')
        with patch.object(runtime, 'signature', return_value={'signature':'Valid','version':'14.51'}), patch.object(runtime.urllib.request, 'urlopen') as network:
            self.assertEqual(runtime.fetch(self.folder, self.spec).read_bytes(), b'good')
            network.assert_not_called()
        self.assertTrue((self.folder / 'source.json').is_file())

    def test_tampered_cache_is_refused_without_replacing_it(self):
        path=self.folder / 'VC_redist.x64.exe';path.write_bytes(b'evil')
        with patch.object(runtime.urllib.request, 'urlopen') as network, self.assertRaisesRegex(ValueError, 'mismatch'):
            runtime.fetch(self.folder, self.spec)
        network.assert_not_called()
        self.assertEqual(path.read_bytes(), b'evil')

    def test_bad_download_is_not_published(self):
        with patch.object(runtime.urllib.request, 'urlopen', return_value=io.BytesIO(b'evil')), self.assertRaisesRegex(ValueError, 'mismatch'):
            runtime.fetch(self.folder, self.spec)
        self.assertFalse((self.folder / 'VC_redist.x64.exe').exists())
        self.assertFalse((self.folder / 'VC_redist.download.exe').exists())


if __name__ == '__main__':unittest.main()
