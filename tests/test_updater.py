import hashlib
import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.updater import ReleaseManifest, UpdateError, release_is_newer, verify_sha256


class UpdaterTests(unittest.TestCase):
    def test_release_is_newer_when_tag_exceeds_installed_version(self):
        self.assertTrue(release_is_newer("v0.20.0", "0.19.0"))
        self.assertFalse(release_is_newer("v0.19.0", "0.19.0"))

    def test_manifest_rejects_path_traversal_asset(self):
        with self.assertRaises(UpdateError):
            ReleaseManifest.from_dict({"version": "0.20.0", "asset": "../bad.zip", "sha256": "0" * 64})

    def test_checksum_verification_rejects_modified_download(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            path = Path(temporary) / "release.zip"
            path.write_bytes(b"trusted bytes")
            expected = hashlib.sha256(b"different bytes").hexdigest()
            with self.assertRaises(UpdateError):
                verify_sha256(path, expected)

    def test_client_accepts_timeout_parameter(self):
        from advertpreneur_cli.updater import GitHubReleaseClient
        client = GitHubReleaseClient("haseebgb92/advertpreneur-cli", timeout=5)
        self.assertEqual(client.timeout, 5)


if __name__ == "__main__":
    unittest.main()
