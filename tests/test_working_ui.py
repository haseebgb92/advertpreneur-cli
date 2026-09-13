import unittest
from types import SimpleNamespace

from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.diff_intelligence import FileRisk


class _UI:
    def __init__(self):
        self.calls = []

    def set_working_state(self, *args, **kwargs):
        self.calls.append(("set", args, kwargs))

    def begin_working(self, *args, **kwargs):
        self.calls.append(("begin", args, kwargs))

    def heading(self, *args, **kwargs):
        self.calls.append(("heading", args, kwargs))


class WorkingUiTests(unittest.TestCase):
    def test_agent_start_updates_the_existing_footer_without_restarting_it(self):
        app = object.__new__(AdvertpreneurCLI)
        app.ui = _UI()
        app.active = SimpleNamespace(model="test-model")
        app.current_session = SimpleNamespace(bridge_enabled=False)

        app.on_agent_event("task_start", {"model": "test-model"})

        self.assertEqual(app.ui.calls, [("set", ("Starting",), {"model": "test-model", "turn": 1})])

    def test_live_change_rows_keep_observed_diff_counts(self):
        risks = [
            FileRisk("app.py", 8, 2, "low", "localized"),
            FileRisk("README.md", 3, 0, "low", "localized"),
        ]

        rows = AdvertpreneurCLI._live_change_rows(risks) if hasattr(AdvertpreneurCLI, "_live_change_rows") else []

        self.assertEqual(rows, [("app.py", 8, 2), ("README.md", 3, 0)])


if __name__ == "__main__":
    unittest.main()
