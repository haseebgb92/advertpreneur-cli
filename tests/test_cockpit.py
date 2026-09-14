from unittest import mock

from advertpreneur_cli.tui import TerminalUI
from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.missions import MissionStore


def test_cockpit_renders_mission_step_attention_and_evidence():
    ui = object.__new__(TerminalUI)
    ui.toolbar = lambda: [("class:toolbar", " STATUS BAR ")]
    ui._joke = "A stable joke row."
    ui._live_active = False
    ui._cockpit = {}
    ui.set_cockpit(
        {
            "mission": "Update homepage",
            "step": "Verify",
            "state": "waiting",
            "attention": "approval",
            "evidence": "2/3",
        }
    )

    with mock.patch("advertpreneur_cli.tui.time.monotonic", return_value=12.4):
        rendered = "".join(fragment[1] for fragment in ui._composer_toolbar())

    assert "Update homepage" in rendered
    assert "waiting for input" in rendered
    assert "Evidence 2/3" in rendered


def test_cli_projects_durable_mission_state_into_cockpit(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Update homepage", [{"title": "Inspect", "kind": "inspect"}])

    class UI:
        def __init__(self):
            self.state = None

        def set_cockpit(self, state):
            self.state = state

    cli = object.__new__(AdvertpreneurCLI)
    cli.missions = store
    cli.ui = UI()
    cli._current_mission_id = mission.id

    cli._refresh_mission_cockpit()

    assert cli.ui.state["mission"] == "Update homepage"
    assert cli.ui.state["step"] == "Inspect"
    assert cli.ui.state["evidence"] == "0/0"


def test_cli_creates_and_activates_mission_from_planner_before_provider_work():
    calls = []

    class Store:
        def create(self, request, steps):
            calls.append(("create", request, steps))
            return type("Mission", (), {"id": "mission-1", "steps": [type("Step", (), {"id": "step-1"})()]})()

        def transition_step(self, mission_id, step_id, state):
            calls.append(("transition", mission_id, step_id, state))

    cli = object.__new__(AdvertpreneurCLI)
    cli.missions = Store()
    cli._current_task_plan = type("Plan", (), {"needs_browser": True, "task_class": "code change", "wants_full_validation": True, "wants_package": False})()
    cli._refresh_mission_cockpit = lambda: calls.append(("cockpit",))

    assert cli._begin_task_mission("Update the site") == "mission-1"
    assert calls[0][0:2] == ("create", "Update the site")
    assert calls[1] == ("transition", "mission-1", "step-1", "active")
    assert calls[2] == ("cockpit",)
