from __future__ import annotations

import json
import os
import stat
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

from advertpreneur_cli.browser_learning import BrowserRoutineStore
from advertpreneur_cli.bridge_server import BrokerState
from advertpreneur_cli.provider_harness import ExternalProviderHarness, ProviderHarnessError, ProviderModel, ProviderQuota, ProviderQuotaWindow
from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.config import ModelProfile


def test_learning_state_survives_controller_rebind(tmp_path: Path):
    browser_dir = tmp_path / ".advertpreneur" / "browser"
    first = BrowserRoutineStore(browser_dir)
    first.start("repeat-me")
    # A different BrowserController/Store instance can record and stop the same active lesson.
    second = BrowserRoutineStore(browser_dir)
    second.record("click", {"selector": "#go"}, {"verified": True, "url": "https://example.test/next"})
    third = BrowserRoutineStore(browser_dir)
    row = third.stop()
    assert row["name"] == "repeat-me"
    assert len(row["steps"]) == 1
    assert row["steps"][0]["args"]["selector"] == "#go"
    assert not (browser_dir / ".learning-active.json").exists()


def test_teach_routine_persists_protected_tabs_and_hides_keyword_value(tmp_path: Path):
    store = BrowserRoutineStore(tmp_path)
    store.start("amazon-xray", protected_tabs={
        "access": {"url": "https://members.softzilla.net/member", "title": "Members"},
        "helium": {"url": "https://app.helium10.com", "title": "Helium"},
        "amazon": {"url": "https://amazon.com", "title": "Amazon"},
    })
    store.record("fill", {"selector": "#twotabsearchtextbox", "value": "bee wax wrap", "tab": "amazon"}, {"url": "https://amazon.com"})
    row = store.stop()

    assert sorted(row["protected_tabs"]) == ["access", "amazon", "helium"]
    assert row["steps"][0]["args"]["value"] == "[TEACH_KEYWORD]"
    assert "bee wax wrap" not in str(row)


def test_teach_routine_omits_non_search_fill_values(tmp_path: Path):
    store = BrowserRoutineStore(tmp_path)
    store.start("portal", protected_tabs={"access": {"url": "https://example.test", "title": "Portal"}})
    store.record("fill", {"selector": "#notes", "value": "private note", "tab": "access"}, {})

    assert store.stop()["steps"] == []


def test_taught_workflow_review_lists_tabs_and_numbered_steps(tmp_path: Path):
    store = BrowserRoutineStore(tmp_path)
    store.start("amazon-xray", protected_tabs={"amazon": {"url": "https://amazon.com", "title": "Amazon"}})
    store.record("click", {"selector": "#analyze", "tab": "amazon"}, {"verified": True})
    store.stop()

    review = store.stop_review("amazon-xray")

    assert "Protected tabs: amazon" in review
    assert "1. [amazon] click #analyze" in review


def test_broker_buffers_human_browser_learning_events(tmp_path: Path):
    state = BrokerState(tmp_path / "pairs.json")
    reg = state.browser_register({"provider_id": "edge1", "token": "secret", "label": "Edge"})
    state.browser_learn_event({
        "provider_id": reg["provider_id"], "token": "secret",
        "event": {"action": "click", "args": {"selector": "a[href='/mission']"}, "evidence": {"verified": True}},
    })
    rows = state.browser_take_learn_events("edge1")
    assert len(rows) == 1
    assert rows[0]["action"] == "click"
    assert state.browser_take_learn_events("edge1") == []


def test_agy_usage_parser_uses_authoritative_five_hour_and_weekly(tmp_path: Path, monkeypatch):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: "agy")
    output = """
    Gemini Models
    Five Hour Limit Remaining    73%
    Weekly Limit Remaining       41%

    Claude and GPT models
    Five Hour Limit Remaining    55%
    Weekly Limit Remaining       22%
    """
    monkeypatch.setattr(h, "_run_capture", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output, stderr=""))
    q = h._agy_quota_live("gemini-3.7-flash-medium")
    values = {w.label: w.remaining_percent for w in q.windows}
    assert values == {"5h": 73.0, "weekly": 41.0}
    q2 = h._agy_quota_live("gpt-5-test")
    values2 = {w.label: w.remaining_percent for w in q2.windows}
    assert values2 == {"5h": 55.0, "weekly": 22.0}
    assert q.source == "official agy /usage"


def test_codex_app_server_quota_parser_reads_official_windows(tmp_path: Path, monkeypatch):
    script = tmp_path / "fake_codex.py"
    script.write_text(textwrap.dedent('''
        #!/usr/bin/env python3
        import json, sys
        for line in sys.stdin:
            row=json.loads(line)
            if row.get("id") == 1:
                print(json.dumps({"id":1,"result":{"userAgent":"fake"}}), flush=True)
            if row.get("id") == 2:
                print(json.dumps({"id":2,"result":{
                    "ordinaryUsageAllowed": True,
                    "rateLimits": {"limitId":"codex","primary":{"usedPercent":37,"windowDurationMins":300,"resetsAt":2000000000},"secondary":{"usedPercent":12,"windowDurationMins":10080,"resetsAt":2000001000}},
                    "rateLimitsByLimitId": {"codex":{"limitId":"codex","primary":{"usedPercent":37,"windowDurationMins":300,"resetsAt":2000000000},"secondary":{"usedPercent":12,"windowDurationMins":10080,"resetsAt":2000001000}}}
                }}), flush=True)
                break
    ''').lstrip(), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: str(script))
    q = h._codex_quota_live(timeout=4)
    values = {w.label: w.remaining_percent for w in q.windows}
    assert values["5h"] == 63.0
    assert values["weekly"] == 88.0
    assert "account/rateLimits/read" in q.source


def test_provider_model_catalog_uses_persistent_cache(tmp_path: Path, monkeypatch):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    calls = {"n": 0}
    def live(provider, timeout=30):
        calls["n"] += 1
        return [ProviderModel(provider, "m1", "Model One", "fast")]
    monkeypatch.setattr(h, "_model_catalog_live", live)
    assert h.model_catalog("agy")[0].id == "m1"
    assert calls["n"] == 1
    # A new harness instance should use disk cache, not hit provider catalogue again.
    h2 = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h2, "_model_catalog_live", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live fetch should not run")))
    assert h2.model_catalog("agy")[0].display == "Model One"


def test_external_toolbar_shows_subscription_quota_not_ollama_budget():
    cli = AdvertpreneurCLI.__new__(AdvertpreneurCLI)
    cli.active = ModelProfile("agy", "gemini-3.7-flash-medium", False, 4096)
    cli.agent = SimpleNamespace(context_breakdown=lambda: {"system": 1000, "conversation": 500})
    cli.project = Path("/tmp/demo")
    cli.git_branch = "main"
    cli.git_dirty_count = 0
    cli.plan_mode = False
    cli.current_session = SimpleNamespace(bridge_enabled=False)
    cli.statusline_mode = "balanced"
    cli.settings = SimpleNamespace(approval_mode="full", free_cloud_models=[], cloud_access_mode="free")
    cli.provider_harness = SimpleNamespace(
        quota_cached=lambda p: ProviderQuota("agy", windows=[ProviderQuotaWindow("5h", 73), ProviderQuotaWindow("weekly", 41)]),
        quota_text=ExternalProviderHarness.quota_text,
    )
    text = "".join(piece for _style, piece in cli.toolbar())
    assert "gemini-3.7-flash-medium" in text
    assert "AGY" in text
    assert "5h 73%" in text and "wk 41%" in text
    assert "SUB" in text
    assert "FREE" not in text
    assert "task $" not in text and "day $" not in text


def test_extension_manifest_loads_human_recorder():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))
    assert any("browser-recorder.js" in row.get("js", []) for row in data.get("content_scripts", []))
    assert (root / "browser-extension" / "browser-recorder.js").exists()


def test_extension_marks_all_learned_tabs_as_learning():
    root = Path(__file__).resolve().parents[1]
    source = (root / "browser-extension" / "background.js").read_text(encoding="utf-8")

    assert "learn?.tabs?.[String(tabId)]" in source
    assert "await setControlBar(senderTab.id, `Advertpreneur Learn Mode" in source


def test_codex_missing_five_hour_is_not_invented():
    q = ExternalProviderHarness._parse_codex_quota_response({
        "rateLimits": {"primary": {"usedPercent": 7, "windowDurationMins": 10080, "resetsAt": 2000001000}, "secondary": None}
    })
    assert {w.label: w.remaining_percent for w in q.windows} == {"weekly": 93.0}
    assert ExternalProviderHarness.quota_text(q) == "5h — · wk 93%"


def test_codex_missing_used_percent_does_not_become_one_hundred():
    q = ExternalProviderHarness._parse_codex_quota_response({
        "rateLimits": {
            "primary": {"windowDurationMins": 300, "resetsAt": 2000000000},
            "secondary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 2000001000},
        }
    })
    assert {w.label: w.remaining_percent for w in q.windows} == {"weekly": 95.0}
    assert "5h —" in ExternalProviderHarness.quota_text(q)


def test_quota_thresholds_dedupe_and_rearm(tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    def q(rem):
        return ProviderQuota("codex", model="gpt-5.6-terra", windows=[ProviderQuotaWindow("5h", rem)])
    assert h.quota_thresholds(q(26)) == []
    assert h.quota_thresholds(q(25)) == [25]
    assert h.quota_thresholds(q(24)) == []
    assert h.quota_thresholds(q(10)) == [10]
    assert h.quota_thresholds(q(5)) == [5]
    assert h.quota_thresholds(q(0)) == [0]
    assert h.quota_thresholds(q(0)) == []
    assert h.quota_thresholds(q(100)) == []
    assert h.quota_thresholds(q(24)) == [25]


def test_external_effort_never_silently_uses_xhigh():
    assert ExternalProviderHarness.normalize_effort("") == "medium"
    assert ExternalProviderHarness.normalize_effort("xhigh") == "medium"
    assert ExternalProviderHarness.normalize_effort("MAX") == "medium"
    assert ExternalProviderHarness.normalize_effort("high") == "high"


def test_codex_activity_exposes_lifecycle_not_reasoning_body():
    row = {"type": "item.started", "item": {"type": "reasoning", "text": "PRIVATE CHAIN OF THOUGHT"}}
    activity = ExternalProviderHarness._activity_from_codex_row(row)
    assert activity is not None and activity.label == "Reasoning"
    assert "PRIVATE" not in activity.label and "PRIVATE" not in activity.detail
    cmd = ExternalProviderHarness._activity_from_codex_row({"type": "item.started", "item": {"type": "command_execution", "command": "pytest -q"}})
    assert cmd is not None and cmd.label == "Running tests" and cmd.kind == "tool"


def test_codex_stream_counts_tools_and_sanitizes_legacy_effort(tmp_path: Path, monkeypatch):
    script = tmp_path / "fake_codex_stream.py"
    script.write_text(textwrap.dedent('''
        #!/usr/bin/env python3
        import json
        print(json.dumps({"type":"thread.started","thread_id":"t1"}), flush=True)
        print(json.dumps({"type":"item.started","item":{"type":"reasoning","text":"SECRET"}}), flush=True)
        print(json.dumps({"type":"item.started","item":{"type":"command_execution","command":"pytest -q"}}), flush=True)
        print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"PASS"}}), flush=True)
        print(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120}}), flush=True)
    ''').lstrip(), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: str(script))
    monkeypatch.setattr(h, "_run_codex_app_server", lambda *a, **k: (_ for _ in ()).throw(ProviderHarnessError("native setup unavailable")))
    events = []
    run = h.run_codex("tiny task", model="gpt-5.6-terra", effort="xhigh", cwd=tmp_path, on_event=events.append)
    assert run.ok and run.text == "PASS"
    assert run.reasoning_effort == "medium"
    assert run.tool_calls == 1
    assert any(x.label == "Reasoning" for x in events)
    assert any(x.label == "Running tests" for x in events)
    assert all("SECRET" not in (x.label + x.detail) for x in events)
    assert 'model_reasoning_effort="medium"' in " ".join(run.command)


def test_agy_json_quota_parses_provider_reset_timestamps(tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    q = h._parse_agy_quota_payload({
        "GeminiModels": {
            "fiveHour": {"remainingPercent": 73, "resetAt": "2026-09-04T18:00:00Z"},
            "weekly": {"remainingPercent": 41, "resetAt": "2026-09-11T18:00:00Z"},
        }
    }, "gemini-test")
    rows = {w.label: w for w in q.windows}
    assert rows["5h"].remaining_percent == 73
    assert rows["weekly"].remaining_percent == 41
    assert rows["5h"].resets_at > 0 and rows["weekly"].resets_at > rows["5h"].resets_at
