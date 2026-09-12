import unittest
from types import SimpleNamespace

from advertpreneur_cli.cli import AdvertpreneurCLI


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


if __name__ == "__main__":
    unittest.main()
