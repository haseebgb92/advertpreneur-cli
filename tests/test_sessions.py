import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.sessions import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_save_resume_and_fork(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "project"
            project.mkdir()
            store = SessionStore(root / "sessions")
            rec = store.create(project, "cloud", "gpt-oss:20b")
            rec.name = "Homepage work"
            rec.messages = [{"role": "user", "content": "hello"}]
            store.save(rec)

            loaded = store.load(rec.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "Homepage work")
            self.assertEqual(loaded.messages[0]["content"], "hello")

            fork = store.fork(loaded)
            self.assertNotEqual(fork.id, loaded.id)
            self.assertIn("fork", fork.name.lower())
            self.assertEqual(fork.messages, loaded.messages)


if __name__ == "__main__":
    unittest.main()
