from types import SimpleNamespace

import pytest

from advertpreneur_cli.cli import AdvertpreneurCLI


class RecordingUI:
    def __init__(self):
        self.cards = []

    def result_card(self, outcome, summary, **kwargs):
        self.cards.append((outcome, summary, kwargs))


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted", "login_needed", "approval_needed"])
def test_task_finalization_emits_one_advertpreneur_result_card(status):
    cli = object.__new__(AdvertpreneurCLI)
    cli.ui = RecordingUI()
    cli._task_result_card_emitted = False

    cli._finalize_task_card(status, "Updated homepage", changed_files=["page.html"], tool_calls=2)

    assert len(cli.ui.cards) == 1
    outcome, summary, details = cli.ui.cards[0]
    assert outcome == status
    assert summary == "Updated homepage"
    assert details["files"] == ["page.html"]
    assert details["actions"] == 2


def test_task_finalization_is_idempotent():
    cli = object.__new__(AdvertpreneurCLI)
    cli.ui = RecordingUI()
    cli._task_result_card_emitted = False

    cli._finalize_task_card("failed", "First result")
    cli._finalize_task_card("failed", "Second result")

    assert [card[1] for card in cli.ui.cards] == ["First result"]
