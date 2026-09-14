from __future__ import annotations

import asyncio
import base64
import json
import threading
from pathlib import Path

from advertpreneur_cli.browser_control import BrowserController
from advertpreneur_cli.provider_harness import ExternalProviderHarness
from advertpreneur_cli.telemetry import HarnessTelemetry
from advertpreneur_cli.tui import TerminalUI


def test_safe_prompt_call_moves_off_running_asyncio_loop():
    seen = {}

    async def main():
        caller = threading.current_thread().name
        result = TerminalUI._safe_prompt_call(lambda: (seen.setdefault("thread", threading.current_thread().name), "ok")[1])
        return caller, result

    caller, result = asyncio.run(main())
    assert result == "ok"
    assert seen["thread"] == "AdvertpreneurPrompt"
    assert seen["thread"] != caller


def test_existing_edge_extension_is_preferred_for_navigation(tmp_path: Path):
    ctl = BrowserController(tmp_path, visible=True)
    ctl._extension_available = lambda wait_seconds=0.0: True

    class FakeBridge:
        def browser_command(self, action, args, timeout=45):
            assert action == "navigate"
            assert args["url"] == "https://example.com"
            return {"url": "https://example.com/", "title": "Example", "provider": "existing-edge/extension"}

    ctl.bridge = FakeBridge()
    # If extension routing regresses and Playwright is touched, fail loudly.
    ctl._rpc = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Playwright fallback used"))
    out = ctl.navigate("https://example.com")
    assert "existing Edge" in out
    assert ctl.provider == "existing-edge/extension"
    assert ctl.current_url == "https://example.com/"


def test_extension_reverse_engineer_saves_local_map_and_screenshot(tmp_path: Path):
    ctl = BrowserController(tmp_path, visible=True)
    ctl._extension_available = lambda wait_seconds=0.0: True
    jpg = base64.b64encode(b"fake-jpeg").decode()

    class FakeBridge:
        def browser_command(self, action, args, timeout=45):
            if action == "reverse_engineer":
                return {"url": "https://example.com", "title": "Example", "viewport": {"width": 1200, "height": 800}, "elements": [{"tag":"section","rect":{"width":1200,"height":600},"style":{"fontSize":"16px"},"text":"Hero"}]}
            if action == "screenshot":
                return {"url":"https://example.com", "title":"Example", "data_url":"data:image/jpeg;base64," + jpg}
            raise AssertionError(action)

    ctl.bridge = FakeBridge()
    out = ctl.reverse_engineer("body", "hero")
    assert "cloud tokens 0" in out
    assert (tmp_path / ".advertpreneur" / "browser" / "hero.json").exists()
    assert (tmp_path / ".advertpreneur" / "browser" / "hero.jpg").read_bytes() == b"fake-jpeg"
    row = json.loads((tmp_path / ".advertpreneur" / "browser" / "hero.json").read_text())
    assert row["browser_provider"] == "existing-edge/extension"


def test_codex_jsonl_parser_extracts_message_and_usage(tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    stdout = "\n".join([
        json.dumps({"type":"thread.started","thread_id":"t1"}),
        json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"Looks good"}}),
        json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20,"cached_input_tokens":70,"total_tokens":120}}),
    ])
    text, usage, thread = h._codex_parse(stdout)
    assert text == "Looks good"
    assert thread == "t1"
    assert usage["input_tokens"] == 100
    assert usage["cached_input_tokens"] == 70


def test_telemetry_summary_is_local_and_content_free(tmp_path: Path):
    t = HarnessTelemetry(tmp_path)
    t.record("task", provider="cloud", model="gemma4:31b", input_tokens=100, output_tokens=10, metered_usd=0.001, ok=True, prompt="secret prompt")
    t.record("browser", browser_provider="existing-edge/extension", ok=True)
    raw = t.path.read_text()
    assert "secret prompt" not in raw
    summary = t.summary("today")
    assert "gemma4:31b" not in summary  # summary rolls up by provider, not source/prompt content
    assert "existing-edge/extension" in summary
    assert "0 model tokens" in summary


def test_browser_provider_broker_round_trip(tmp_path: Path):
    import time
    from advertpreneur_cli.bridge import BridgeClient
    from advertpreneur_cli.bridge_server import create_server

    server = create_server(0, tmp_path / "pairs.json")
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        ident = server.state.browser_register({"provider_id":"edge-1","token":"edge-secret","label":"Existing Edge","version":"0.2.0"})
        client = BridgeClient(tmp_path / "app", port=port, auto_start=False)

        def extension_worker():
            command = server.state.browser_next("edge-1", "edge-secret", wait_seconds=3)
            assert command and command["action"] == "navigate"
            server.state.browser_result({
                "provider_id":"edge-1", "token":"edge-secret", "command_id":command["command_id"],
                "ok":True, "result":{"url":command["args"]["url"], "title":"Taiyo", "provider":"existing-edge/extension"}
            })

        worker = threading.Thread(target=extension_worker, daemon=True)
        worker.start()
        row = client.browser_command("navigate", {"url":"https://www.taiyomunch.com"}, timeout=5)
        worker.join(timeout=2)
        assert row["title"] == "Taiyo"
        assert row["provider"] == "existing-edge/extension"
    finally:
        server.shutdown(); server.server_close()


def test_agy_catalog_parses_official_models_output(tmp_path: Path, monkeypatch):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: "agy.exe" if provider == "agy" else "")

    class P:
        returncode = 0
        stdout = """gemini-3.8-flash-high     Gemini 3.8 Flash (High)\ngemini-3.8-flash-medium   Gemini 3.8 Flash (Medium)\nclaude-sonnet-4-6          Claude Sonnet 4.6 (Thinking)\n"""
        stderr = ""

    monkeypatch.setattr(h, "_run_capture", lambda *a, **k: P())
    rows = h.model_catalog("agy")
    assert [x.id for x in rows] == ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "claude-sonnet-4-6"]
    assert rows[0].display == "Gemini 3.8 Flash (High)"


def test_codex_sdk_catalog_uses_public_models_api(tmp_path: Path, monkeypatch):
    import sys
    import types

    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: "")
    monkeypatch.setattr(h, "_codex_sdk_available", lambda: True)
    monkeypatch.setattr(h, "ensure_runtime", lambda provider: "sdk")

    class Resp:
        def model_dump(self):
            return {"models": [
                {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "default_reasoning_level": "medium"},
                {"slug": "gpt-5.5", "display_name": "GPT-5.5", "default_reasoning_level": "high"},
            ]}

    class FakeCodex:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def models(self): return Resp()

    mod = types.ModuleType("openai_codex")
    mod.Codex = FakeCodex
    monkeypatch.setitem(sys.modules, "openai_codex", mod)
    rows = h.model_catalog("codex")
    assert [x.id for x in rows] == ["gpt-5.6-sol", "gpt-5.5"]
    assert "default medium" in rows[0].detail


def test_provider_packet_windows_cleanup_never_masks_completed_run(tmp_path: Path, monkeypatch):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    expected = __import__("advertpreneur_cli.provider_harness", fromlist=["ProviderRun"]).ProviderRun(
        provider="agy", model="m", text="ADP_AUTH_OK", returncode=0, status="SUCCESS"
    )
    monkeypatch.setattr(h, "run", lambda *a, **k: expected)
    import advertpreneur_cli.provider_harness as ph
    monkeypatch.setattr(ph.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError(32, "locked")))
    run = h.run_packet("agy", "packet", "test", model="m")
    assert run.ok
    assert run.text == "ADP_AUTH_OK"
