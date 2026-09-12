from __future__ import annotations

import json
import stat
import textwrap
from pathlib import Path
from types import SimpleNamespace

from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.browser_mcp import BrowserMCPServer
from advertpreneur_cli.provider_harness import ExternalProviderHarness
from advertpreneur_cli.sessions import SessionStore
from advertpreneur_cli.tools import ToolRegistry


def _exe(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_codex_normal_turn_is_persistent_and_resume_reuses_exact_thread(tmp_path: Path, monkeypatch):
    script = _exe(tmp_path / "codex", r'''
        #!/usr/bin/env python3
        import json, sys
        args = sys.argv[1:]
        thread = "thread-1"
        print(json.dumps({"type":"thread.started","thread_id":thread}), flush=True)
        print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"OK"}}), flush=True)
        print(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":2,"cached_input_tokens":80,"total_tokens":102}}), flush=True)
    ''')
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: str(script))

    first = h.run_codex("one", model="gpt-5.6-sol", effort="low", cwd=tmp_path)
    assert first.ok and first.conversation_id == "thread-1"
    assert "--ephemeral" not in first.command
    assert "resume" not in first.command

    second = h.run_codex("two", model="gpt-5.6-sol", effort="low", cwd=tmp_path, conversation_id="thread-1")
    assert second.ok and second.conversation_id == "thread-1"
    assert "resume" in second.command
    assert second.session_reused is True
    assert "--ephemeral" not in second.command


def test_codex_mcp_disable_override_preserves_transport():
    config = ExternalProviderHarness._codex_session_config(
        {"aios": {"enabled": False, "command": "node", "args": ["server.mjs"]}},
        plugins_enabled=False,
    )
    assert "mcp_servers.aios.enabled" not in config
    assert config["mcp_servers.aios"] == {"enabled": False, "command": "node", "args": ["server.mjs"]}


def test_cli_builds_complete_transport_override_only_for_irrelevant_mcp():
    cli = object.__new__(AdvertpreneurCLI)
    cli.mcp_manager = SimpleNamespace(discover=lambda: [
        SimpleNamespace(name="aios", enabled=True, transport_type="stdio", transport={"type":"stdio","command":"node","args":["aios.mjs"]}),
        SimpleNamespace(name="hostinger-dns", enabled=True, transport_type="stdio", transport={"type":"stdio","command":"npx.cmd","args":["hostinger-dns-mcp"]}),
    ])
    states = cli._codex_mcp_states("edit a local Python file")
    assert states == {"aios": False, "hostinger-dns": False}
    overrides = cli._codex_mcp_transport_overrides("edit a local Python file")
    assert overrides["aios"] == {"enabled": False, "command": "node", "args": ["aios.mjs"]}
    assert overrides["hostinger-dns"]["enabled"] is False
    # A relevant server inherits the user's full Codex config instead of being
    # replaced by a partial request-level entry.
    dns = cli._codex_mcp_transport_overrides("change Hostinger DNS")
    assert "hostinger-dns" not in dns
    assert dns["aios"]["command"] == "node"


def test_live_wordpress_task_injects_advertpreneur_browser_bridge_mcp():
    cli = object.__new__(AdvertpreneurCLI)
    cli.project = Path("D:/site-project")
    cli.mcp_manager = SimpleNamespace(discover=lambda: [])

    overrides = cli._codex_mcp_transport_overrides(
        "Open the CooCooBabys wp-admin, inspect plugins, and update settings."
    )

    bridge = overrides["advertpreneur-browser"]
    assert bridge["enabled"] is True
    assert bridge["command"]
    assert bridge["args"][:2] == ["-m", "advertpreneur_cli.browser_mcp"]
    assert bridge["args"][-1] == "D:\\site-project"


def test_browser_mcp_exposes_live_site_controls_but_not_delete_actions():
    names = {tool["name"] for tool in BrowserMCPServer.tools()}
    assert {"browser_navigate", "browser_inspect", "browser_click", "browser_fill", "browser_upload"} <= names
    assert not any("delete" in name or "remove" in name for name in names)


def test_agy_current_usage_json_uses_raw_fraction_and_correct_pool(tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    payload = {
        "command": {"name": "usage", "data": {"groups": [
            {"name":"Gemini Models","buckets":[
                {"id":"gemini-weekly","window":"weekly","remaining_fraction":0.9997795820236206,"reset_time":"2026-09-11T09:10:48Z"},
                {"id":"gemini-5h","window":"5h","remaining_fraction":0.998677670955658,"reset_time":"2026-09-04T14:10:48Z"},
            ]},
            {"name":"Claude and GPT models","buckets":[
                {"id":"3p-weekly","window":"weekly","remaining_fraction":1.0,"reset_time":"2026-09-11T13:16:59Z"},
                {"id":"3p-5h","window":"5h","remaining_fraction":1.0,"reset_time":"2026-09-04T18:16:59Z"},
            ]},
        ]}}
    }
    gemini = h._parse_agy_quota_payload(payload, "gemini-3.6-flash")
    gv = {w.label: w.remaining_percent for w in gemini.windows}
    assert 99.86 < gv["5h"] < 99.88
    assert 99.97 < gv["weekly"] < 99.99
    third = h._parse_agy_quota_payload(payload, "gpt-oss")
    tv = {w.label: w.remaining_percent for w in third.windows}
    assert tv == {"weekly": 100.0, "5h": 100.0}


def test_agy_stream_session_reuses_process_and_reports_per_turn_delta(tmp_path: Path, monkeypatch):
    script = _exe(tmp_path / "agy", r'''
        #!/usr/bin/env python3
        import json, sys
        print(json.dumps({"event":"init","conversation_id":"agy-thread","init":{"cwd":"."}}), flush=True)
        turn = 0
        total_in = total_out = total_cache = 0
        for line in sys.stdin:
            row = json.loads(line)
            if row.get("event") != "user":
                continue
            turn += 1
            inc_in = 100 if turn == 1 else 30
            inc_cache = 20 if turn == 1 else 25
            total_in += inc_in; total_out += 2; total_cache += inc_cache
            turn_usage = {"input_tokens":inc_in,"output_tokens":2,"thinking_tokens":0,"cache_read_tokens":inc_cache,"total_tokens":inc_in+2}
            cumulative = {"input_tokens":total_in,"output_tokens":total_out,"thinking_tokens":0,"cache_read_tokens":total_cache,"total_tokens":total_in+total_out}
            print(json.dumps({"event":"step_update","step_update":{"conversation_id":"agy-thread","step_index":turn,"state":"DONE","step_type":"agent_response","usage":turn_usage}}), flush=True)
            print(json.dumps({"event":"result","result":{"conversation_id":"agy-thread","status":"SUCCESS","response":"OK","num_turns":turn,"usage":cumulative}}), flush=True)
    ''')
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: str(script))
    first = h.run_agy("one", model="gemini-test", effort="low", cwd=tmp_path, session_key="s1")
    second = h.run_agy("two", model="gemini-test", effort="low", cwd=tmp_path, session_key="s1", conversation_id=first.conversation_id)
    assert first.input_tokens == 100 and first.cache_read_tokens == 20
    assert second.input_tokens == 30 and second.cache_read_tokens == 25
    assert second.session_reused is True
    assert second.conversation_id == "agy-thread"
    assert "--input-format" in second.command and "-p" not in second.command
    h.close()


def test_agy_tiered_model_does_not_send_conflicting_effort_flag(tmp_path: Path, monkeypatch):
    script = _exe(tmp_path / "agy", r'''
        #!/usr/bin/env python3
        import json
        print(json.dumps({"conversation_id":"agy-fixed","status":"SUCCESS","response":"OK","duration_seconds":0,"usage":{"input_tokens":4,"output_tokens":1,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":5}}))
    ''')
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: str(script))
    run = h.run_agy("one", model="gemini-3.7-flash-medium", effort="low", cwd=tmp_path)
    assert run.ok
    assert run.reasoning_effort == "medium"
    assert "--model" in run.command and "gemini-3.7-flash-medium" in run.command
    assert "--effort" not in run.command


def test_provider_thread_ids_survive_adp_session_save_and_load(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    row = store.create(tmp_path, "codex", "gpt-5.6-sol")
    row.provider_threads = {"codex":"codex-thread", "agy":"agy-thread"}
    store.save(row)
    loaded = store.load(row.id)
    assert loaded is not None
    assert loaded.provider_threads == row.provider_threads


def test_ollama_general_chat_does_not_receive_coding_tools(tmp_path: Path):
    reg = ToolRegistry(tmp_path)
    assert reg.schemas("Explain photosynthesis simply.", []) == []
    names = {x["function"]["name"] for x in reg.schemas("Fix the bug in src/app.py and run tests", [])}
    assert {"read_file", "search_text", "project_map", "write_file", "replace_in_file", "run_command"} <= names


def test_zero_token_greeting_fast_path():
    assert AdvertpreneurCLI._zero_token_reply("Hello!").startswith("Hello")
    assert AdvertpreneurCLI._zero_token_reply("Thanks") == "You're welcome."
    assert AdvertpreneurCLI._zero_token_reply("reply only: Ok") == "OK"
    assert AdvertpreneurCLI._zero_token_reply("Fix the WordPress theme") == ""


def test_context_rollover_only_on_clear_bloat():
    small = SimpleNamespace(input_tokens=30000, uncached_input_tokens=15000)
    huge = SimpleNamespace(input_tokens=230000, uncached_input_tokens=20000)
    waste = SimpleNamespace(input_tokens=120000, uncached_input_tokens=95000)
    assert AdvertpreneurCLI._provider_thread_should_rollover(small) is False
    assert AdvertpreneurCLI._provider_thread_should_rollover(huge) is True
    assert AdvertpreneurCLI._provider_thread_should_rollover(waste) is True


def test_agy_quota_cache_never_cross_contaminates_model_pools(tmp_path: Path):
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    from advertpreneur_cli.provider_harness import ProviderQuota, ProviderQuotaWindow
    gemini = ProviderQuota(
        "agy", model="gemini-x", source="test", fetched_at=1_900_000_000.0,
        windows=[ProviderQuotaWindow("5h", 88.0, 12.0, 0, 300, "reported")],
    )
    h._quota_cache["agy:gemini"] = gemini
    assert h.quota_cached("agy", "gemini-x") is gemini
    assert h.quota_cached("agy", "gpt-oss") is None


def test_codex_native_app_server_reuses_loaded_thread_without_exec_resume(tmp_path: Path, monkeypatch):
    log = tmp_path / "rpc.log"
    script = _exe(tmp_path / "codex", f'''\
        #!/usr/bin/env python3
        import json, sys
        log = {str(log)!r}
        turn = 0
        for line in sys.stdin:
            row = json.loads(line)
            method = row.get("method", "")
            with open(log, "a", encoding="utf-8") as f:
                f.write(method + " " + json.dumps(row.get("params") or {{}}, sort_keys=True) + "\\n")
            rid = row.get("id")
            if method == "initialize":
                print(json.dumps({{"id":rid,"result":{{}}}}), flush=True)
            elif method == "thread/start":
                print(json.dumps({{"id":rid,"result":{{"thread":{{"id":"native-thread"}}}}}}), flush=True)
            elif method == "thread/resume":
                print(json.dumps({{"id":rid,"result":{{"thread":{{"id":"native-thread"}}}}}}), flush=True)
            elif method == "turn/start":
                turn += 1
                tid = f"turn-{{turn}}"
                print(json.dumps({{"id":rid,"result":{{"turn":{{"id":tid,"status":"inProgress"}}}}}}), flush=True)
                print(json.dumps({{"method":"turn/started","params":{{"threadId":"native-thread","turn":{{"id":tid}}}}}}), flush=True)
                print(json.dumps({{"method":"thread/tokenUsage/updated","params":{{"threadId":"native-thread","turnId":tid,"tokenUsage":{{"last":{{"inputTokens":100 if turn == 1 else 12,"cachedInputTokens":80 if turn == 1 else 95,"outputTokens":2,"reasoningOutputTokens":1,"totalTokens":102 if turn == 1 else 14}},"total":{{"inputTokens":112,"cachedInputTokens":175,"outputTokens":4,"reasoningOutputTokens":2,"totalTokens":116}}}}}}}}), flush=True)
                print(json.dumps({{"method":"item/completed","params":{{"threadId":"native-thread","turnId":tid,"item":{{"type":"agentMessage","text":"OK"}}}}}}), flush=True)
                print(json.dumps({{"method":"turn/completed","params":{{"threadId":"native-thread","turn":{{"id":tid,"status":"completed","items":[{{"type":"agentMessage","text":"OK"}}]}}}}}}), flush=True)
    ''')
    h = ExternalProviderHarness(tmp_path / "app", tmp_path)
    monkeypatch.setattr(h, "_which", lambda provider: str(script))
    events = []
    first = h.run_codex(
        "one", model="gpt-5.6-sol", effort="low", cwd=tmp_path, on_event=events.append,
        mcp_server_overrides={"hostinger-dns":{"enabled":False,"command":"node","args":[]}}, plugins_enabled=False,
        developer_instructions="coding rules",
    )
    second = h.run_codex(
        "two", model="gpt-5.6-sol", effort="low", cwd=tmp_path, on_event=events.append,
        conversation_id=first.conversation_id, mcp_server_overrides={"hostinger-dns":{"enabled":False,"command":"node","args":[]}},
        plugins_enabled=False, developer_instructions="coding rules",
    )
    assert first.ok and second.ok
    assert first.command[1:3] == ["app-server", "turn/start"]
    assert second.command[1:3] == ["app-server", "turn/start"]
    assert first.conversation_id == second.conversation_id == "native-thread"
    assert first.session_reused is False and second.session_reused is True
    assert second.input_tokens == 12 and second.cache_read_tokens == 95
    # A later turn can enable a capability without spawning a second app-server.
    third = h.run_codex(
        "dns", model="gpt-5.6-sol", effort="low", cwd=tmp_path, on_event=events.append,
        conversation_id=second.conversation_id, mcp_server_overrides={},
        plugins_enabled=False, developer_instructions="coding rules",
    )
    assert third.ok and third.session_reused is True
    lines = log.read_text(encoding="utf-8").splitlines()
    methods = [line.split(" ", 1)[0] for line in lines]
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 1
    assert methods.count("turn/start") == 3
    first_thread_line = next(line for line in lines if line.startswith("thread/start "))
    resume_line = next(line for line in lines if line.startswith("thread/resume "))
    assert '"mcp_servers.hostinger-dns": {"args": [], "command": "node", "enabled": false}' in first_thread_line
    assert '"features.plugins": false' in first_thread_line
    assert 'mcp_servers.hostinger-dns' not in resume_line
    assert '"excludeTurns": true' in resume_line
    h.close()


def test_codex_native_file_activity_shows_filename():
    row = {"method":"item/started","params":{"item":{"type":"fileChange","changes":[{"path":"src/provider_harness.py"}]}}}
    event = ExternalProviderHarness._codex_native_activity(row)
    assert event is not None
    assert event.label == "Writing code · provider_harness.py"
    assert event.detail == "src/provider_harness.py"
