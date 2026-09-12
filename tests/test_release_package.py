import tempfile
import unittest
import zipfile
from pathlib import Path

from advertpreneur_cli.release_packaging import build_release


class ReleasePackageTests(unittest.TestCase):
    def test_release_archive_contains_extension_and_installer(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            archive = Path(temporary) / "AdvertpreneurCLI-0.20.0-windows.zip"
            build_release(Path.cwd(), archive, "0.20.0")
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
        self.assertIn("browser-extension/manifest.json", names)
        self.assertIn("INSTALL.ps1", names)
        self.assertIn("pyproject.toml", names)

    def test_github_bootstrap_resolves_the_actual_latest_tag(self):
        installer = (Path.cwd() / "INSTALL.ps1").read_text(encoding="utf-8")
        self.assertIn("releases/latest", installer)
        self.assertIn("gh release download $releaseTag", installer)


if __name__ == "__main__":
    unittest.main()
