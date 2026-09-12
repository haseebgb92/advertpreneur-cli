import tempfile
import unittest
from pathlib import Path

from advertpreneur_cli.agent import CodingAgent
from advertpreneur_cli.budget import BudgetTracker
from advertpreneur_cli.config import Settings
from advertpreneur_cli.tools import ToolRegistry


class AgentSessionTests(unittest.TestCase):
    def test_context_estimate_and_reset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            budget = BudgetTracker(root / "usage.json", 1.0, 1.0)
            agent = CodingAgent(Settings(), ToolRegistry(root), budget, None)
            agent.messages = [{"role": "user", "content": "hello world" * 20}]
            self.assertGreater(agent.context_estimate_tokens(), 0)
            agent.reset_session()
            self.assertEqual(agent.context_estimate_tokens(), 0)
            self.assertEqual(agent.session_tasks, 0)


if __name__ == "__main__":
    unittest.main()
