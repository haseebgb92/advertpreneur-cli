from pathlib import Path
from unittest.mock import MagicMock
from advertpreneur_cli.browser_macro import BrowserMacroStore, BrowserMacro, MacroStep


def test_browser_macro_record_and_save(tmp_path: Path) -> None:
    store = BrowserMacroStore(tmp_path)
    store.start_recording("login_flow", "Log in to portal")
    store.record_step("navigate", target="https://example.com/login")
    store.record_step("fill", target="#email", value="test@example.com")
    store.record_step("fill", target="#password", value="secret")
    store.record_step("click", target="#submit")
    saved = store.stop_recording()

    assert saved is not None
    assert saved.name == "login_flow"
    assert len(saved.steps) == 4

    macros = store.list_macros()
    assert "login_flow" in macros

    loaded = store.load("login_flow")
    assert loaded is not None
    assert loaded.description == "Log in to portal"
    assert loaded.steps[0].action == "navigate"
    assert loaded.steps[0].target == "https://example.com/login"


def test_browser_macro_playback(tmp_path: Path) -> None:
    store = BrowserMacroStore(tmp_path)
    macro = BrowserMacro(
        name="scrape_metrics",
        description="Navigate and screenshot",
        steps=[
            MacroStep(action="navigate", target="https://example.com/dashboard"),
            MacroStep(action="wait", value="500"),
            MacroStep(action="screenshot", value="dash_preview"),
        ],
    )
    store.save(macro)

    mock_controller = MagicMock()
    result = store.play("scrape_metrics", mock_controller)

    assert result["ok"] is True
    assert result["steps_run"] == 3
    mock_controller.navigate.assert_called_once_with("https://example.com/dashboard", tab="work")
    mock_controller.wait.assert_called_once_with(500, tab="work")
    mock_controller.screenshot.assert_called_once_with(name="dash_preview")
