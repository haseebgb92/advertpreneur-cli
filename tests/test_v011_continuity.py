from __future__ import annotations

import tempfile
from pathlib import Path

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.keys import Keys

from advertpreneur_cli.agent import SYSTEM_PROMPT
from advertpreneur_cli.cli import AdvertpreneurCLI
from advertpreneur_cli.tui import TerminalUI
from advertpreneur_cli.working_record import SessionWorkingRecord


def test_working_record_reuses_current_exact_source_evidence(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "AuthScreen.kt"
    source.write_text('fun Login() {\n  onClick = { onAuthSuccess() }\n}\n', encoding="utf-8")

    record = SessionWorkingRecord(tmp_path / "evidence", "s1", project)
    record.record_tool(
        "inspect login authentication",
        "read_file",
        {"path": "AuthScreen.kt", "start_line": 1, "max_lines": 20},
        "1: fun Login() {\n2:   onClick = { onAuthSuccess() }\n3: }",
    )
    ctx = record.context("verify login onAuthSuccess")
    assert "AuthScreen.kt:1-3" in ctx
    assert "onAuthSuccess" in ctx
    assert "zero-cloud cache" in ctx


def test_working_record_invalidates_file_evidence_after_change(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "auth.ts"
    source.write_text("export const token = 'demo'\n", encoding="utf-8")
    record = SessionWorkingRecord(tmp_path / "evidence", "s1", project)
    record.record_tool("inspect token", "read_file", {"path": "auth.ts"}, "1: export const token = 'demo'")
    assert record.summary()["current_events"] == 1
    source.write_text("export const token = 'real'\n", encoding="utf-8")
    assert record.summary()["current_events"] == 0
    assert "auth.ts" not in record.context("inspect token")


def test_working_record_preserves_real_build_evidence(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    record = SessionWorkingRecord(tmp_path / "evidence", "s1", project)
    record.record_tool(
        "actually build android",
        "run_command",
        {"command": "gradlew.bat :app:assembleDebug", "cwd": "android"},
        "exit_code=0\nBUILD SUCCESSFUL in 12s",
    )
    ctx = record.context("verify android build")
    assert "gradlew.bat :app:assembleDebug" in ctx
    assert "exit 0" in ctx
    assert "BUILD SUCCESSFUL" in ctx


def test_system_prompt_requires_source_and_validation_evidence():
    low = SYSTEM_PROMPT.lower()
    assert "never state implementation details as fact unless verified" in low
    assert "never claim build/test/lint pass" in low
    assert "reuse current verified session evidence" in low


def test_ctrl_c_has_explicit_safe_composer_binding(tmp_path: Path):
    ui = TerminalUI(tmp_path / "history", lambda: FormattedText([]))
    bindings = ui.kb.get_bindings_for_keys((Keys.ControlC,))
    assert bindings, "Ctrl+C must be intercepted so it cannot terminate Advertpreneur from the composer"


def test_resume_replay_hides_local_map_wrapper():
    raw = "Local map targets (verify before editing):\n- src/auth.ts:1-20 · class AuthService\n\nInspect login auth"
    assert AdvertpreneurCLI._clean_saved_user_text(raw) == "Inspect login auth"


def test_validation_gate_forces_real_command_before_pass(tmp_path: Path):
    from advertpreneur_cli.agent import CodingAgent
    from advertpreneur_cli.budget import BudgetTracker
    from advertpreneur_cli.client import ChatResult
    from advertpreneur_cli.config import ModelProfile, Settings

    class FakeTools:
        def schemas(self, task="", history=None):
            return []

        def execute(self, name, args):
            assert name == "run_command"
            return "exit_code=0\nBUILD SUCCESSFUL"

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def chat(self, model, messages, tools, think=False, max_output_tokens=4096):
            self.calls += 1
            if self.calls == 1:
                # This must not be accepted because the user explicitly requested a real build.
                return ChatResult({"content": "PASS - build looks good"}, 100, 20, 1, {})
            if self.calls == 2:
                return ChatResult({
                    "content": "",
                    "tool_calls": [{"function": {"name": "run_command", "arguments": {"command": "pytest"}}}],
                }, 100, 20, 1, {})
            return ChatResult({"content": "Verified after real test execution."}, 100, 20, 1, {})

    seen = []
    budget = BudgetTracker(tmp_path / "usage.json", 1.0, 1.0)
    agent = CodingAgent(Settings(), FakeTools(), budget, None, event_callback=lambda name, data: seen.append(name))
    fake = FakeClient()
    agent._client = lambda provider: fake  # type: ignore[method-assign]
    result = agent.run_task(
        "Actually run the Android build and report the result.",
        ModelProfile("local", "fake-local"),
        original_task="Actually run the Android build and report the result.",
    )
    assert "Verified after real test execution" in result.text
    assert fake.calls == 3
    assert "validation_required" in seen


def test_working_record_backfills_old_saved_tool_history(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "auth.kt").write_text("fun auth() = true\n", encoding="utf-8")
    record = SessionWorkingRecord(tmp_path / "evidence", "old-session", project)
    messages = [
        {"role": "user", "content": "inspect auth"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "auth.kt", "start_line": 1, "max_lines": 20}}}
        ]},
        {"role": "tool", "tool_name": "read_file", "content": "1: fun auth() = true"},
        {"role": "assistant", "content": "Auth is present."},
    ]
    assert record.backfill_from_messages(messages) == 1
    assert "auth.kt:1-1" in record.context("auth")


def test_bridge_summary_exposes_real_validation_without_source_dump(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "Auth.kt").write_text("fun auth() = true\n", encoding="utf-8")
    record = SessionWorkingRecord(tmp_path / "evidence", "s1", project)
    task = "verify auth and run build"
    record.record_tool(task, "read_file", {"path": "Auth.kt"}, "1: fun auth() = true")
    record.record_tool(task, "run_command", {"command": "gradlew.bat :app:assembleDebug", "cwd": "android"}, "exit_code=0\nBUILD SUCCESSFUL")
    summary = record.bridge_summary(task)
    assert summary["validation_command_executed"] is True
    assert summary["commands"][0]["exit_code"] == 0
    assert summary["commands"][0]["proof"] == ["exit_code=0", "BUILD SUCCESSFUL"]
    assert summary["inspected_files"][0]["path"] == "Auth.kt"
    assert "fun auth" not in str(summary), "Bridge result should carry evidence metadata, not resend source bodies"
