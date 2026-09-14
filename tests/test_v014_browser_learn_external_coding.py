from __future__ import annotations

import json
from pathlib import Path

from advertpreneur_cli.browser_learning import BrowserRoutineStore
from advertpreneur_cli.provider_harness import ExternalProviderHarness
from advertpreneur_cli.tools import BROWSER_SCHEMAS


def test_browser_routine_store_records_and_persists(tmp_path: Path):
    store = BrowserRoutineStore(tmp_path)
    store.start("mission loop")
    store.record("navigate", {"url": "https://example.com"}, {"url": "https://example.com", "verified": True})
    store.record("scroll", {"amount": 700}, {"url": "https://example.com", "verified": True})
    store.record("click", {"selector": "a.mission"}, {"before_url": "https://example.com", "url": "https://example.com/about", "navigated": True, "verified": True})
    row = store.stop()
    assert row["name"] == "mission loop"
    assert len(row["steps"]) == 3
    again = BrowserRoutineStore(tmp_path)
    loaded = again.get("MISSION LOOP")
    assert loaded["steps"][2]["evidence"]["navigated"] is True


def test_browser_routine_redacts_sensitive_fill(tmp_path: Path):
    store = BrowserRoutineStore(tmp_path)
    store.start("login")
    store.record("fill", {"selector": "input[type=password]", "value": "dont-store-me"}, {"verified": True})
    row = store.stop()
    assert row["steps"][0]["args"]["value"] == "[NOT_STORED_SENSITIVE_VALUE]"


def test_browser_schema_has_zero_model_routine_actions():
    actions = BROWSER_SCHEMAS[0]["function"]["parameters"]["properties"]["action"]["enum"]
    assert "run_routine" in actions
    assert "scroll" in actions
    assert "wait" in actions


def test_codex_coding_uses_workspace_write(monkeypatch, tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_codex_sdk_available", lambda: False)
    monkeypatch.setattr(h, "_which", lambda provider: "codex" if provider == "codex" else "")
    captured = {}

    class P:
        returncode = 0
        stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}) + "\n"
        stderr = ""

    def fake(argv, cwd, timeout=30):
        captured["argv"] = argv
        return P()

    monkeypatch.setattr(h, "_run_capture", fake)
    h.run_codex("edit it", write=True)
    assert "workspace-write" in captured["argv"]


def test_agy_coding_uses_accept_edits_sandbox(monkeypatch, tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: "agy" if provider == "agy" else "")
    captured = {}

    class P:
        returncode = 0
        stdout = json.dumps({"response": "ok", "status": "SUCCESS", "usage": {}})
        stderr = ""

    def fake(argv, cwd, timeout=30):
        captured["argv"] = argv
        return P()

    monkeypatch.setattr(h, "_run_capture", fake)
    h.run_agy("edit it", write=True)
    assert "--sandbox" in captured["argv"]
    assert "--mode=accept-edits" in captured["argv"]


def test_persistent_agy_stream_session_uses_print_mode_required_by_agy():
    """AGY documents stream-json input as a print-mode protocol."""
    from advertpreneur_cli.provider_harness import _AgyStreamDriver

    driver = _AgyStreamDriver("agy", Path.cwd(), "", "medium", write=True)

    assert "--print=" in driver._argv()


def test_extension_has_visible_control_bar_and_verified_click():
    js = (Path(__file__).parents[1] / "advertpreneur_cli" / "browser_extension" / "background.js").read_text(encoding="utf-8")
    assert "Advertpreneur is controlling this tab" in js
    assert "adp-browser-control-bar" in js
    assert "before_url" in js
    assert "navigated" in js
    assert 'action === "scroll"' in js
