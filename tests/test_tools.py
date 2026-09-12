import tempfile
import unittest
from unittest.mock import patch
import json
from pathlib import Path

from advertpreneur_cli.tools import ToolError, ToolRegistry


def test_windows_operation_schema_is_exposed_only_for_local_desktop_tasks(tmp_path):
    reg = ToolRegistry(tmp_path)
    names = {row["function"]["name"] for row in reg.schemas("open a local Windows folder")}
    assert "windows" in names
    names = {row["function"]["name"] for row in reg.schemas("read project config")}
    assert "windows" not in names


class ToolTests(unittest.TestCase):
    def test_root_confinement_and_edit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("hello world", encoding="utf-8")
            tools = ToolRegistry(root, approval_mode="safe")
            out = tools.execute("replace_in_file", {"path": "a.txt", "old_text": "world", "new_text": "there"})
            self.assertIn("Updated", out)
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "hello there")
            with self.assertRaises(ToolError):
                tools.execute("read_file", {"path": "../outside.txt"})

    def test_hard_command_block(self):
        with tempfile.TemporaryDirectory() as td:
            tools = ToolRegistry(Path(td), approval_mode="full")
            with self.assertRaises(ToolError):
                tools.execute("run_command", {"command": "rm -rf /"})

    def test_wordpress_delete_needs_proposal_approval(self):
        with tempfile.TemporaryDirectory() as td:
            tools = ToolRegistry(Path(td), approval_mode="full")
            proposal = tools.tool_browser("wordpress_propose_delete", value=json.dumps([
                {"name": "Old Plugin", "kind": "plugin", "warning": "inactive"}
            ]))
            token = proposal.rsplit(" ", 1)[-1]
            with self.assertRaises(ToolError):
                tools.tool_browser("wordpress_delete", proposal_id=token, selector="#delete")
            self.assertIn("approved", tools.tool_browser("wordpress_approve_delete", proposal_id=token, approval_token=token))

    def test_site_profile_and_adapter_route(self):
        with tempfile.TemporaryDirectory() as td:
            tools = ToolRegistry(Path(td), approval_mode="full")
            self.assertIn("hostinger", tools.tool_browser("site_detect", url="https://hpanel.hostinger.com/websites"))
            self.assertIn("saved", tools.tool_browser("site_profile", name="wordpress", url="https://example.test"))
            with patch.object(tools.browser_controller, "navigate", return_value="Navigated") as navigate:
                self.assertEqual(tools.tool_browser("site_open", name="plugins"), "Navigated")
            navigate.assert_called_once_with("https://example.test/wp-admin/plugins.php")

    def test_site_playbook_is_available_from_saved_profile(self):
        with tempfile.TemporaryDirectory() as td:
            tools = ToolRegistry(Path(td), approval_mode="full")
            tools.tool_browser("site_profile", name="wordpress", url="https://example.test")
            playbook = tools.tool_browser("site_playbook", name="upload_plugin")
            self.assertIn("upload_plugin", playbook)
            self.assertIn("selected filename visible", playbook)

    def test_normal_browser_navigation_automatically_remembers_supported_site(self):
        with tempfile.TemporaryDirectory() as td:
            tools = ToolRegistry(Path(td), approval_mode="full")
            with patch.object(tools.browser_controller, "navigate", return_value="Navigated"):
                tools.tool_browser("navigate", url="https://hpanel.hostinger.com/websites")
            self.assertIn("hostinger", tools.tool_browser("site_profile"))
            self.assertIn("production", tools.tool_browser("operations_status"))


if __name__ == "__main__":
    unittest.main()
