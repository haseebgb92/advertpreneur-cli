from pathlib import Path

from advertpreneur_cli.agent import CodingAgent
from advertpreneur_cli.budget import BudgetTracker
from advertpreneur_cli.config import Settings
from advertpreneur_cli.tools import ToolRegistry


class FakePlugins:
    def skills(self, enabled_only=True):
        return []


class FakeMCP:
    def discover(self):
        return []


def test_lazy_tool_schemas_for_normal_task(tmp_path: Path):
    reg = ToolRegistry(tmp_path, plugin_manager=FakePlugins(), mcp_manager=FakeMCP())
    names = {x["function"]["name"] for x in reg.schemas("fix login validation")}
    assert {"read_file", "search_text", "project_map", "write_file", "replace_in_file", "run_command"} <= names
    assert "list_files" not in names
    assert "mcp" not in names
    assert "load_skill" not in names


def test_list_files_only_for_broad_repo_task(tmp_path: Path):
    reg = ToolRegistry(tmp_path, plugin_manager=FakePlugins(), mcp_manager=FakeMCP())
    names = {x["function"]["name"] for x in reg.schemas("inspect the repo structure")}
    assert "list_files" in names


def test_completed_assistant_answer_compacts_before_followup(tmp_path: Path):
    reg = ToolRegistry(tmp_path)
    budget = BudgetTracker(tmp_path / "usage.json", 5.0, 1.0)
    agent = CodingAgent(Settings(), reg, budget, None)
    agent.messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "inspect auth"},
        {"role": "assistant", "content": "A" * 5000 + " tests passed src/auth/service.ts"},
        {"role": "user", "content": "now fix it"},
    ]
    view = agent._messages_for_request()
    old_answer = view[2]["content"]
    assert len(old_answer) < 1200
    assert "compacted locally" in old_answer


def test_context_schema_estimate_uses_minimal_set(tmp_path: Path):
    reg = ToolRegistry(tmp_path)
    budget = BudgetTracker(tmp_path / "usage.json", 5.0, 1.0)
    agent = CodingAgent(Settings(), reg, budget, None)
    agent.messages = [{"role": "system", "content": agent.system_prompt()}]
    breakdown = agent.context_breakdown()
    assert breakdown["tool_schemas"] < 1000
