import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from advertpreneur_cli.plugins import PluginManager


class PluginTests(unittest.TestCase):
    def test_repo_plugin_skill_is_discovered_and_loaded_lazily(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "project"
            plugin = project / ".agents" / "plugins" / "demo"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / "skills" / "coding").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": "demo", "version": "1.0.0", "skills": "./skills/"}),
                encoding="utf-8",
            )
            (plugin / "skills" / "coding" / "SKILL.md").write_text(
                "---\nname: Coding Pro\ndescription: Verify every code change.\n---\nAlways run the verifier.",
                encoding="utf-8",
            )
            manager = PluginManager(root / "app", project)
            with patch("advertpreneur_cli.plugins.shutil.which", return_value=None):
                found = manager.discover()
                self.assertEqual(len(found), 1)
                skills = manager.skills()
                self.assertEqual(len(skills), 1)
                self.assertIn("Coding Pro", manager.catalog())
                self.assertNotIn("Always run the verifier", manager.catalog())
                self.assertIn("Always run the verifier", manager.load_skill(skills[0].id))

    def test_codex_plugin_json_installed_path_is_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "project"
            project.mkdir()
            plugin = root / "cache" / "impeccable"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / "skills" / "ui").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name":"impeccable"}), encoding="utf-8")
            (plugin / "skills" / "ui" / "SKILL.md").write_text("UI polish rules", encoding="utf-8")
            manager = PluginManager(root / "app", project)
            fake = json.dumps({"installed":[{"name":"impeccable","installedPath":str(plugin),"enabled":True}]})
            with patch("advertpreneur_cli.plugins.shutil.which", return_value="codex"), patch(
                "advertpreneur_cli.plugins.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = fake
                run.return_value.stderr = ""
                found = manager.discover(refresh=True)
                self.assertEqual(found[0].name, "impeccable")
                self.assertEqual(len(found[0].skills), 1)


if __name__ == "__main__":
    unittest.main()
