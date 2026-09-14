import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.operation_router import (
    LocalOperationRouter,
    OperationSecurityError,
)


class LocalOperationRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temp_dir.name)
        self.router = LocalOperationRouter(self.project_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_contained_path_resolves_cleanly(self):
        resolved = self.router.assert_contained("src/index.js")
        self.assertEqual(resolved, (self.project_path / "src/index.js").resolve())

    def test_path_traversal_is_blocked(self):
        with self.assertRaises(OperationSecurityError):
            self.router.assert_contained("../outside_file.txt")

    def test_destructive_commands_are_blocked(self):
        with self.assertRaises(OperationSecurityError):
            self.router.check_command("rm -rf /")

        with self.assertRaises(OperationSecurityError):
            self.router.check_command("del /s /q C:\\Windows")

        with self.assertRaises(OperationSecurityError):
            self.router.check_command("format C:")

        # Safe commands pass without exception
        self.router.check_command("pytest tests/ -v")
        self.router.check_command("git status")

    def test_staged_write_and_commit(self):
        mutation = self.router.stage_write("config.json", '{"key": "value"}')
        self.assertEqual(mutation.rel_path, "config.json")
        self.assertFalse((self.project_path / "config.json").exists())

        committed = self.router.commit_stage()
        self.assertIn("config.json", committed)
        self.assertTrue((self.project_path / "config.json").exists())
        self.assertEqual((self.project_path / "config.json").read_text(encoding="utf-8"), '{"key": "value"}')

    def test_fast_route_interactive_html(self):
        prompt = "create an html on desktop, make it annoying but fun. use all skils that might be needed."
        res = self.router.fast_route(prompt)
        self.assertIsNotNone(res)
        report, committed = res
        self.assertTrue(len(committed) > 0)
        self.assertIn("Created interactive HTML", report)
        
        # Verify file exists on desktop
        desktop = (Path.home() / "Desktop").resolve()
        target = desktop / "test.html"
        self.assertTrue(target.exists())
        content = target.read_text(encoding="utf-8")
        self.assertIn("ANNOYING", content)
        self.assertIn("CLICK ME IF YOU CAN", content)


if __name__ == "__main__":
    unittest.main()

