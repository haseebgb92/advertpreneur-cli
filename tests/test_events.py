import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.agent import CodingAgent
from advertpreneur_cli.budget import BudgetTracker
from advertpreneur_cli.config import Settings
from advertpreneur_cli.tools import ToolRegistry


class EventTests(unittest.TestCase):
    def test_tool_event_can_carry_name_payload(self):
        seen = []
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = Settings()
            tools = ToolRegistry(root, "safe", 10000, approve=lambda _k, _d: True)
            budget = BudgetTracker(root / "usage.json", 1.0, 1.0)
            agent = CodingAgent(settings, tools, budget, None, event_callback=lambda event, data: seen.append((event, data)))
            agent._event("tool_start", name="write_file", args={"path": "test.html"})
        self.assertEqual(seen[0][0], "tool_start")
        self.assertEqual(seen[0][1]["name"], "write_file")


if __name__ == "__main__":
    unittest.main()
