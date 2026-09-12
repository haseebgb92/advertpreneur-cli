import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from advertpreneur_cli.credentials import CredentialStore


class CredentialStoreTests(unittest.TestCase):
    def test_environment_fallback(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OLLAMA_API_KEY": "  test-key  "}, clear=False):
            store = CredentialStore(Path(td) / "credentials.json")
            self.assertEqual(store.source(), "environment")
            self.assertEqual(store.load(), "test-key")

    def test_saved_credential_precedes_environment(self):
        # Non-Windows test environment uses the restricted-permissions fallback format.
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OLLAMA_API_KEY": "env-key"}, clear=False):
            store = CredentialStore(Path(td) / "credentials.json")
            store.save("saved-key")
            self.assertEqual(store.source(), "saved")
            self.assertEqual(store.load(), "saved-key")
            store.delete()
            self.assertEqual(store.load(), "env-key")


if __name__ == "__main__":
    unittest.main()
