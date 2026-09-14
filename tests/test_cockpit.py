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
