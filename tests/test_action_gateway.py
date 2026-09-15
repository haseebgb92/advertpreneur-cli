from __future__ import annotations

import pytest
from pathlib import Path
from types import SimpleNamespace

from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.provider_harness import ExternalProviderHarness, ProviderRun
from advertpreneur_cli.tools import ToolError, ToolRegistry


class FakeTools:
    def __init__(self, values=None, error: Exception | None = None):
        self.values = values or {}
        self.error = error
        self.calls = []

    def action_names(self):
        return {"browser", "web_search"}

    def execute(self, name, args):
        self.calls.append((name, args))
        if self.error:
            raise self.error
        return self.values[name]


def test_parse_action_accepts_one_fenced_json_object():
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    request = ExternalActionGateway.parse_action(
        'I will inspect it.\n```adp_action\n'
        '{"tool":"browser","args":{"action":"status"}}\n```'
    )

    assert request.tool == "browser"
    assert request.args == {"action": "status"}
    assert request.error == ""


@pytest.mark.parametrize("text", [
    "```adp_action\n{}\n```",
    "```adp_action\nnot json\n```",
    "```adp_action\n{\"tool\":\"browser\",\"args\":[]}\n```",
    "```adp_action\n{\"tool\":\"browser\",\"args\":{}}\n```\n```adp_action\n{\"tool\":\"web_search\",\"args\":{}}\n```",
])
def test_parse_action_rejects_malformed_or_ambiguous_blocks(text):
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    assert ExternalActionGateway.parse_action(text).error


def test_gateway_dispatches_registered_action_through_shared_registry():
    from advertpreneur_cli.action_gateway import ActionRequest, ExternalActionGateway

    tools = FakeTools({"browser": "Browser bridge connected"})
    result = ExternalActionGateway(tools).execute(ActionRequest("browser", {"action": "status"}))

    assert result.ok is True
    assert result.text == "Browser bridge connected"
    assert tools.calls == [("browser", {"action": "status"})]


def test_gateway_returns_unknown_tool_without_dispatch():
    from advertpreneur_cli.action_gateway import ActionRequest, ExternalActionGateway

    tools = FakeTools()
    result = ExternalActionGateway(tools).execute(ActionRequest("not_a_tool", {}))

    assert result.ok is False
    assert "Unknown ADP action" in result.text
    assert tools.calls == []


def test_gateway_preserves_deletion_and_login_boundaries():
    from advertpreneur_cli.action_gateway import ActionRequest, ExternalActionGateway

    deletion = ExternalActionGateway(FakeTools(error=ToolError("Deletion requires approved proposal"))).execute(
        ActionRequest("browser", {"action": "wordpress_delete"})
    )
    assert deletion.blocked is True
    assert "approved proposal" in deletion.text

    login = ExternalActionGateway(FakeTools({"browser": "Login needed in browser"})).execute(
        ActionRequest("browser", {"action": "wordpress_state"})
    )
    assert login.blocked is True


def test_gateway_formats_verified_result_packet_and_blocks_repeats():
    from advertpreneur_cli.action_gateway import ActionRequest, ActionResult, ExternalActionGateway

    gateway = ExternalActionGateway(FakeTools({"browser": "ok"}), max_repeats=2)
    packet = gateway.result_packet(ActionResult(True, "Navigated · https://example.com"))
    assert "```adp_action_result" in packet
    assert "request one next action or finish" in packet

    request = ActionRequest("browser", {"action": "status"})
    assert gateway.execute(request).ok
    assert gateway.execute(request).ok
    assert gateway.execute(request).blocked


def test_gateway_allows_an_inspection_to_repeat_after_a_different_browser_action():
    from advertpreneur_cli.action_gateway import ActionRequest, ExternalActionGateway

    gateway = ExternalActionGateway(FakeTools({"browser": "ok"}), max_repeats=2)
    inspect = ActionRequest("browser", {"action": "inspect", "selector": "body", "tab": "access"})
    click = ActionRequest("browser", {"action": "click", "selector": "#login", "tab": "access"})

    assert gateway.execute(inspect).ok
    assert gateway.execute(inspect).ok
    assert gateway.execute(click).ok
    assert gateway.execute(inspect).ok


def test_cli_treats_press_login_as_a_browser_continuation():
    assert AdvertpreneurCLI._needs_browser_context("now press the login button") is True
    assert AdvertpreneurCLI._needs_browser_context("create a local Python file") is False


def test_gateway_recovers_once_when_provider_turn_is_blank():
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    tools = FakeTools({"browser": "Browser bridge connected"})
    responses = iter([
        ("", "thread"),
        ('```adp_action\n{"tool":"browser","args":{"action":"status"}}\n```', "thread"),
        ("Browser checked; finished.", "thread"),
    ])

    result = ExternalActionGateway(tools).drive("Inspect the browser", lambda _prompt, _thread: next(responses))

    assert result.text == "Browser checked; finished."
    assert tools.calls == [("browser", {"action": "status"})]


def test_cli_rollover_discards_the_warm_provider_helper_as_well_as_thread_id():
    cli = object.__new__(AdvertpreneurCLI)
    cli.current_session = SimpleNamespace(provider_threads={"agy": "bloated-thread"})
    released = []
    cli.provider_harness = SimpleNamespace(release_warm_sessions=lambda: released.append(True) or 1)

    cli._rollover_provider_thread("agy")

    assert cli.current_session.provider_threads == {}
    assert released == [True]


def test_browser_inspect_uses_a_compact_default_observation(tmp_path):
    observed = []

    class Browser:
        def inspect(self, _selector, max_elements, **kwargs):
            observed.append((max_elements, kwargs))
            return "{}"

    tools = ToolRegistry(tmp_path)
    tools.browser_controller = Browser()

    assert tools.tool_browser("inspect", tab="access") == "{}"
    assert observed == [(24, {"tab": "access"})]


def test_visual_context_returns_compact_controls_and_one_screenshot(tmp_path):
    class Browser:
        def visible_controls(self, **_kwargs): return {"url": "https://example.test/login", "title": "Login", "controls": [{"role": "button", "label": "Login", "selector": "#login"}]}
        def screenshot(self, **_kwargs): return "Screenshot · test.jpg · viewport capture"
    tools = ToolRegistry(tmp_path)
    tools.browser_controller = Browser()
    first = __import__("json").loads(tools.tool_browser("visual_context", tab="access"))
    second = __import__("json").loads(tools.tool_browser("visual_context", tab="access"))
    assert first["controls"][0]["label"] == "Login"
    assert first["screenshot"]
    assert second["screenshot"] == ""


@pytest.mark.parametrize("provider", ["codex", "agy"])
def test_external_provider_harness_does_not_claim_image_attachment_support(provider, tmp_path):
    harness = ExternalProviderHarness(tmp_path / "app", tmp_path)

    assert harness.supports_image_attachments(provider) is False


def test_gateway_contract_requires_verified_results_and_preserves_login_delete_boundaries():
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    contract = ExternalActionGateway.contract()
    assert "adp_action" in contract
    assert "Login needed in browser" in contract
    assert "saved browser session" in contract
    assert "deletion proposal" in contract


@pytest.mark.parametrize("provider", ["codex", "agy"])
def test_gateway_drives_identical_action_protocol_for_every_external_provider(provider):
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    tools = FakeTools({"browser": "Browser bridge connected"})
    gateway = ExternalActionGateway(tools)
    prompts = []
    responses = iter([
        ('```adp_action\n{"tool":"browser","args":{"action":"status"}}\n```', provider + "-thread"),
        ("Browser checked; finished.", provider + "-thread"),
    ])

    result = gateway.drive(
        gateway.contract(),
        lambda prompt, conversation_id: (prompts.append((prompt, conversation_id)) or next(responses)),
    )

    assert result.text == "Browser checked; finished."
    assert result.conversation_id == provider + "-thread"
    assert tools.calls == [("browser", {"action": "status"})]
    assert "adp_action_result" in prompts[1][0]


def test_gateway_stops_at_login_without_requesting_another_provider_turn():
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    tools = FakeTools({"browser": "Login needed in browser"})
    gateway = ExternalActionGateway(tools)
    calls = []
    result = gateway.drive(
        gateway.contract(),
        lambda prompt, conversation_id: (calls.append(prompt) or ('```adp_action\n{"tool":"browser","args":{"action":"wordpress_state"}}\n```', "thread")),
    )

    assert result.blocked is True
    assert result.text == "Login needed in browser"
    assert len(calls) == 1


def test_gateway_yields_before_dispatching_an_action_requested_after_escape():
    """An immediate composer message must stop at the next safe action boundary."""
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    tools = FakeTools({"browser": "Browser bridge connected"})
    yielded = False

    def provider_turn(_prompt, _conversation_id):
        nonlocal yielded
        yielded = True
        return ('```adp_action\n{"tool":"browser","args":{"action":"status"}}\n```', "thread")

    result = ExternalActionGateway(tools).drive(
        "Inspect the browser",
        provider_turn,
        should_yield=lambda: yielded,
    )

    assert result.yielded is True
    assert result.blocked is False
    assert tools.calls == []


def test_cli_marks_an_external_turn_yielded_when_escape_arrives(tmp_path):
    """The immediate composer path must not leave the old provider task active."""
    from advertpreneur_cli.action_gateway import ExternalActionGateway

    cli = object.__new__(AdvertpreneurCLI)
    cli.project = tmp_path
    cli.codex_effort = cli.agy_effort = "low"
    cli._current_task_plan = None
    cli.framework_intelligence = SimpleNamespace(context=lambda *_args, **_kwargs: "")
    cli.project_contract = SimpleNamespace(context=lambda **_kwargs: "")
    cli.current_session = SimpleNamespace(provider_threads={}, bridge_enabled=False, id="session", input_tokens=0, output_tokens=0, requests=0)
    cli.subscription_quota_protection = False
    cli.plan_mode = False
    cli.tools = FakeTools({"browser": "Browser bridge connected"})
    cli.budget = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    cli.notifier = SimpleNamespace(quota_warning=lambda *_args, **_kwargs: None)
    cli.agent = SimpleNamespace(messages=[], system_prompt=lambda: "test system prompt")
    cli._micro_coding_task = lambda _text: False
    cli._agy_effective_effort = lambda _model, effort: effort
    cli._codex_mcp_states = lambda _text: {}
    cli._codex_mcp_transport_overrides = lambda _text: {}
    cli._codex_plugins_needed = lambda _text: False
    cli._quota_consumption = lambda *_args: {}
    cli._provider_thread_should_rollover = lambda _run: False
    cli._save_session = lambda: None
    cli.ui = SimpleNamespace(muted=lambda *_args: None, confirm=lambda *_args: True, set_working_state=lambda *_args, **_kwargs: None)
    cli._yield_requested = __import__("threading").Event()

    class Harness:
        def quota(self, *_args, **_kwargs): return None
        def quota_text(self, _quota): return "Quota unavailable"
        def quota_thresholds(self, _quota): return []
        def run(self, selected, prompt, **kwargs):
            cli._yield_requested.set()
            return ProviderRun(selected, "model", '```adp_action\n{"tool":"browser","args":{"action":"status"}}\n```', 0, conversation_id="thread", status="SUCCESS")

    cli.provider_harness = Harness()
    run = cli._run_external_coding("agy", "inspect browser", "model")

    assert run.status == "YIELDED"
    assert run.returncode == 2
    assert cli.tools.calls == []


def test_provider_harness_interrupts_the_current_provider_turn():
    from advertpreneur_cli.provider_harness import ExternalProviderHarness

    harness = object.__new__(ExternalProviderHarness)
    harness._active_interrupt_lock = __import__("threading").RLock()
    called = []
    harness._active_interrupt = lambda: called.append("interrupted")

    assert harness.interrupt_active() is True
    assert called == ["interrupted"]
    assert harness.interrupt_active() is False


def test_worker_completion_wakes_composer_so_an_escape_message_dispatches_without_another_keypress():
    """A yielded provider must return the main loop to its queued immediate message."""
    cli = object.__new__(AdvertpreneurCLI)
    wakes = []
    cli._task_lock = __import__("threading").RLock()
    cli._task_thread = object()
    cli.ui = SimpleNamespace(interrupt_prompt=lambda result: wakes.append(result) or True)

    cli._on_task_worker_exit()

    assert cli._task_thread is None
    assert wakes == ["\x00ADP_TASK_DONE\x00"]


@pytest.mark.parametrize("provider", ["codex", "agy"])
def test_cli_external_route_gives_agy_and_codex_the_same_adp_action_loop(provider, tmp_path):
    cli = object.__new__(AdvertpreneurCLI)
    cli.project = tmp_path
    cli.codex_effort = cli.agy_effort = "low"
    cli._current_task_plan = None
    cli.framework_intelligence = SimpleNamespace(context=lambda *_args, **_kwargs: "")
    cli.project_contract = SimpleNamespace(context=lambda **_kwargs: "")
    cli.current_session = SimpleNamespace(provider_threads={}, bridge_enabled=False, id="session", input_tokens=0, output_tokens=0, requests=0)
    cli.subscription_quota_protection = False
    cli.plan_mode = False
    cli.tools = FakeTools({"browser": "Browser bridge connected"})
    cli.budget = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    cli.notifier = SimpleNamespace(quota_warning=lambda *_args, **_kwargs: None)
    cli.agent = SimpleNamespace(messages=[], system_prompt=lambda: "test system prompt")
    cli._micro_coding_task = lambda _text: False
    cli._agy_effective_effort = lambda _model, effort: effort
    cli._codex_mcp_states = lambda _text: {}
    cli._codex_mcp_transport_overrides = lambda _text: {}
    cli._codex_plugins_needed = lambda _text: False
    cli._quota_consumption = lambda *_args: {}
    cli._provider_thread_should_rollover = lambda _run: False
    cli._save_session = lambda: None
    cli.ui = SimpleNamespace(muted=lambda *_args: None, confirm=lambda *_args: True, set_working_state=lambda *_args, **_kwargs: None)
    responses = iter([
        '```adp_action\n{"tool":"browser","args":{"action":"status"}}\n```',
        "Finished after verified browser status.",
    ])
    prompts = []

    class Harness:
        def quota(self, *_args, **_kwargs): return None
        def quota_text(self, _quota): return "Quota unavailable"
        def quota_thresholds(self, _quota): return []
        def run(self, selected, prompt, **kwargs):
            prompts.append((selected, prompt, kwargs.get("conversation_id")))
            return ProviderRun(selected, "model", next(responses), 0, conversation_id="shared-thread", status="SUCCESS")

    cli.provider_harness = Harness()
    run = cli._run_external_coding(provider, "inspect browser", "model")

    assert run.ok
    assert run.text == "Finished after verified browser status."
    assert cli.tools.calls == [("browser", {"action": "status"})]
    assert len(prompts) == 2
    assert "adp_action_result" in prompts[1][1]
    assert prompts[1][2] == "shared-thread"
