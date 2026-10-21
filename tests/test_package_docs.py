"""Server examples may be initialized locally, but release archives stay public."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package import check_public, copy_public_docs, public_docs


class PublicDocumentation(unittest.TestCase):
    def test_initialized_server_state_does_not_enter_documentation_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "docs"
            expected = {"server/README.md", "server/recipe/.env.example",
                        "server/recipe/prepare.py", "server/recipe/shadnet.cfg.example"}
            private = {"server/recipe/.env", "server/recipe/.build/source.tar.gz",
                       "server/recipe/shadnet-state/shadnet.cfg",
                       "server/recipe/game-state/online-data.json",
                       "server/recipe/backups/state.tar.gz",
                       "server/recipe/__pycache__/prepare.pyc"}
            for name in expected | private:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private" if name in private else "template")
            copy_public_docs(source, root / "bundle")
            actual = {p.relative_to(root / "bundle").as_posix()
                      for p in (root / "bundle").rglob("*") if p.is_file()}
            self.assertEqual(actual, expected)
            self.assertEqual({p.relative_to(source).as_posix() for p in public_docs(source)},
                             expected)

    def test_archive_guard_rejects_server_state_and_allows_templates(self):
        check_public(["docs/server/recipe/.env.example",
                      "docs/server/recipe/shadnet.cfg.example"])
        for name in ("docs/server/recipe/.env",
                     "docs/server/recipe/shadnet-state/db.sqlite",
                     "docs/server/recipe/game-state/online-data.json",
                     "docs/server/recipe/.build/source.tar.gz"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_public([name])

    def test_archive_guard_rejects_conversion_backups_and_staging(self):
        for name in ('save-conversion-backups/job/savedata/userdata0001',
                     '.savedata-conversion-abc/userdata0001',
                     '.savedata-conversion-rejected-abc/userdata0001'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_public([name])


if __name__ == "__main__":
    unittest.main()
