from pathlib import Path
from types import SimpleNamespace

from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.provider_harness import ProviderRun


def test_smart_spend_approval_is_marshalled_off_the_task_worker(tmp_path):
    cli = object.__new__(AdvertpreneurCLI)
    cli.project = tmp_path
    cli.codex_effort = cli.agy_effort = "low"
    cli._current_task_plan = None
    cli.framework_intelligence = SimpleNamespace(context=lambda *_args, **_kwargs: "")
    cli.project_contract = SimpleNamespace(context=lambda **_kwargs: "")
    cli.current_session = SimpleNamespace(provider_threads={}, bridge_enabled=False, bridge_autopilot=False, id="session", input_tokens=0, output_tokens=0, requests=0)
    cli.settings = SimpleNamespace(approval_mode="safe")
    cli.subscription_quota_protection = False
    cli.plan_mode = False
    cli.tools = SimpleNamespace()
    cli.budget = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    cli.notifier = SimpleNamespace(quota_warning=lambda *_args, **_kwargs: None)
    cli.agent = SimpleNamespace(messages=[], system_prompt=lambda: "test system prompt")
    cli._micro_coding_task = lambda _text: True
    cli._agy_effective_effort = lambda _model, effort: effort
    cli._quota_consumption = lambda *_args: {}
    cli._provider_thread_should_rollover = lambda _run: False
    cli._save_session = lambda: None
    approvals = []
    cli._request_approval = lambda kind, detail: approvals.append((kind, detail)) or True
    cli.ui = SimpleNamespace(
        muted=lambda *_args: None,
        confirm=lambda *_args: (_ for _ in ()).throw(AssertionError("task worker must not call ui.confirm directly")),
        set_working_state=lambda *_args, **_kwargs: None,
    )

    class Harness:
        def quota(self, *_args, **_kwargs): return None
        def quota_text(self, _quota): return "Quota unavailable"
        def quota_thresholds(self, _quota): return []
        def run(self, selected, prompt, **kwargs):
            return ProviderRun(selected, "model", "Finished.", 0, conversation_id="thread", status="SUCCESS")

    cli.provider_harness = Harness()
    run = cli._run_external_coding("agy", "create a single HTML file", "model")


def test_smart_spend_does_not_block_a_full_approval_session(tmp_path):
    cli = object.__new__(AdvertpreneurCLI)
    cli.project = tmp_path
    cli.codex_effort = cli.agy_effort = "low"
    cli._current_task_plan = None
    cli.framework_intelligence = SimpleNamespace(context=lambda *_args, **_kwargs: "")
    cli.project_contract = SimpleNamespace(context=lambda **_kwargs: "")
    cli.current_session = SimpleNamespace(provider_threads={}, bridge_enabled=False, bridge_autopilot=False, id="session", input_tokens=0, output_tokens=0, requests=0)
    cli.settings = SimpleNamespace(approval_mode="full")
    cli.subscription_quota_protection = False
    cli.plan_mode = False
    cli.tools = SimpleNamespace()
    cli.budget = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    cli.notifier = SimpleNamespace(quota_warning=lambda *_args, **_kwargs: None)
    cli.agent = SimpleNamespace(messages=[], system_prompt=lambda: "test system prompt")
    cli._micro_coding_task = lambda _text: True
    cli._agy_effective_effort = lambda _model, effort: effort
    cli._quota_consumption = lambda *_args: {}
    cli._provider_thread_should_rollover = lambda _run: False
    cli._save_session = lambda: None
    cli._request_approval = lambda *_args: (_ for _ in ()).throw(AssertionError("full approval must not wait for Smart Spend confirmation"))
    cli.ui = SimpleNamespace(muted=lambda *_args: None, set_working_state=lambda *_args, **_kwargs: None)

    class Harness:
        def quota(self, *_args, **_kwargs): return None
        def quota_text(self, _quota): return "Quota unavailable"
        def quota_thresholds(self, _quota): return []
        def run(self, selected, prompt, **kwargs):
            return ProviderRun(selected, "model", "Finished.", 0, conversation_id="thread", status="SUCCESS")

    cli.provider_harness = Harness()
    run = cli._run_external_coding("agy", "create a single HTML file", "model")

    assert run.ok
