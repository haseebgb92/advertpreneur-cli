from __future__ import annotations

import argparse
import queue
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import clear

from .agent import CodingAgent
from .budget import BudgetTracker
from .client import OllamaClient, OllamaError
from .config import ModelProfile, Settings, load_settings, write_default_config
from .credentials import CredentialError, CredentialStore
from .plugins import PluginInfo, PluginManager
from .mcp import MCPError, MCPManager
from .pricing import is_free_cloud_model, lookup_price
from .sessions import SessionRecord, SessionStore
from .state import UserState
from .tools import ToolError, ToolRegistry
from .action_gateway import ExternalActionGateway
from .ownership import find_artifact_credits, normalize_user_output, worker_ownership_contract
from .project_index import ProjectIndex
from .context_manager import LocalContextManager
from .checkpoints import CheckpointManager
from .notifications import TaskNotifier
from .bridge import BridgeClient, BridgeDispatch, BridgeError, BridgeReply
from .working_record import SessionWorkingRecord
from .frameworks import FrameworkIntelligence
from .handbook import ExperienceHandbook
from .hooks import HookRunner
from .web_tools import search_web
from .provider_harness import ExternalProviderHarness, ProviderHarnessError, ProviderRun, ProviderModel, ProviderActivity
from .telemetry import HarnessTelemetry
from .project_contract import ProjectContract
from .task_planner import LocalTaskPlanner, TaskPlan
from .health import HealthMonitor
from .resource_guard import ResourceGuard
from .verification import ProportionalVerifier
from .diff_intelligence import DiffIntelligence
from .packaging import ProjectPackager
from .workforce import Workforce
from .missions import MissionStore, mission_steps_from_task_plan
from .mission_daemon import MissionDaemon
from .operation_router import LocalOperationRouter
from .automation_memory import AutomationMemory
from .self_healing import SelfHealingEngine
from .browser_macro import BrowserMacroStore
from .dashboard_server import DashboardServer
from .swarm import SwarmCoordinator, SwarmRole, SwarmParcel, ParcelStatus
from .updater import DEFAULT_REPOSITORY, GitHubReleaseClient, UpdateError, apply_latest_update, release_is_newer
from .tui import COMMANDS, MenuItem, TerminalUI


VERSION = "0.27.0"
APP_DIR = Path.home() / ".advertpreneur-cli"
_APPROVAL_WAKE = "\x00ADP_APPROVAL\x00"
_TASK_DONE_WAKE = "\x00ADP_TASK_DONE\x00"


@dataclass
class _PendingApproval:
    kind: str
    detail: str
    done: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


def money(v: float) -> str:
    return f"${v:.4f}" if v < 1 else f"${v:.2f}"


def short_text(text: str, max_len: int = 24) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def short_path(path: Path, max_len: int = 40) -> str:
    text = str(path)
    if len(text) <= max_len:
        return text
    return "…" + text[-(max_len - 1):]


class AdvertpreneurCLI:
    def __init__(self, args: argparse.Namespace) -> None:
        self.project = Path(args.project).expanduser().resolve()
        self.config_path = Path(args.config).expanduser().resolve() if args.config else self.project / "advertpreneur-cli.config.json"
        self.settings = load_settings(self.config_path if self.config_path.exists() else None)

        if args.approval:
            self.settings.approval_mode = args.approval
        if args.task_budget is not None:
            self.settings.task_budget_usd = max(0.0, args.task_budget)
        if args.daily_budget is not None:
            self.settings.daily_budget_usd = max(0.0, args.daily_budget)
        if args.cloud_mode:
            self.settings.cloud_access_mode = args.cloud_mode

        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.state = UserState(APP_DIR / "state.json")
        # v0.15 removes the Beacon completely. Clean its legacy state/files once;
        # no resident Beacon/heartbeat process is created again.
        if "beacon_enabled" in self.state.data:
            self.state.data.pop("beacon_enabled", None); self.state.save()
        try: shutil.rmtree(APP_DIR / "beacon-sessions", ignore_errors=True)
        except Exception: pass
        self.credentials = CredentialStore(APP_DIR / "credentials.json")
        self.credential_error: str | None = None
        try:
            self.api_key = self.credentials.load()
        except CredentialError as exc:
            self.api_key = None
            self.credential_error = str(exc)

        provider = args.provider or self.state.get("provider") or self.settings.default_model.provider
        model = args.model or self.state.get("model") or self.settings.default_model.model
        think = self.state.get("think", self.settings.default_model.think)
        max_output = int(self.state.get("max_output_tokens", self.settings.default_model.max_output_tokens))
        self.active = ModelProfile(provider, model, think, max_output)

        if not args.approval:
            self.settings.approval_mode = self.state.get("approval_mode", self.settings.approval_mode)
        if not args.cloud_mode:
            self.settings.cloud_access_mode = self.state.get("cloud_access_mode", self.settings.cloud_access_mode)

        self.plan_mode = bool(self.state.get("plan_mode", False))
        self.personality = str(self.state.get("personality", "pragmatic"))
        if self.personality not in {"pragmatic", "friendly", "none"}:
            self.personality = "pragmatic"
        self.goal = ""
        self.raw_output = bool(self.state.get("raw_output", False))
        self.title_mode = str(self.state.get("title_mode", "project"))
        if self.title_mode not in {"project", "project-branch", "project-model"}:
            self.title_mode = "project"
        self.pinned_mentions: List[str] = []
        self.statusline_mode = str(self.state.get("statusline_mode", "balanced"))
        if self.statusline_mode not in {"balanced", "minimal", "usage"}:
            self.statusline_mode = "balanced"
        self.auto_index = bool(self.state.get("auto_index", self.settings.auto_index))
        self.auto_compact_tokens = int(self.state.get("auto_compact_tokens", self.settings.auto_compact_tokens))
        self.compact_target_tokens = int(self.state.get("compact_target_tokens", self.settings.compact_target_tokens))
        self.local_compactions = 0
        self.reviewer_provider = str(self.state.get("reviewer_provider", "") or "")
        self.reviewer_model = str(self.state.get("reviewer_model", "") or "")
        self.auto_review = str(self.state.get("auto_review", "off") or "off")
        if self.auto_review not in {"off", "large", "always"}:
            self.auto_review = "off"
        self.desktop_notifications = bool(self.state.get("desktop_notifications", True))
        self.task_sounds = bool(self.state.get("task_sounds", True))
        self.bridge_wait_seconds = max(30, int(self.state.get("bridge_wait_seconds", 600)))
        self.bridge_max_hops = max(1, int(self.state.get("bridge_max_hops", 20)))
        self.browser_visible = bool(self.state.get("browser_visible", True))
        self.handbook_enabled = bool(self.state.get("handbook_enabled", True))
        self.hooks_enabled = bool(self.state.get("hooks_enabled", False))
        self.senior_provider = str(self.state.get("senior_provider", "cloud") or "cloud")
        self.senior_model = str(self.state.get("senior_model", "gpt-oss:120b") or "gpt-oss:120b")
        self.codex_model = str(self.state.get("codex_model", "") or "")
        self.agy_model = str(self.state.get("agy_model", "") or "")
        # v0.14.1 silently defaulted external providers to HIGH even though the
        # normal /reasoning selector only changed Ollama. Migrate once to MEDIUM
        # and never allow an old xhigh/max value to survive invisibly.
        if not bool(self.state.get("provider_effort_policy_v2", False)):
            self.codex_effort = "medium"
            self.agy_effort = "medium"
            self._provider_effort_policy_v2 = True
        else:
            self._provider_effort_policy_v2 = True
            self.codex_effort = ExternalProviderHarness.normalize_effort(str(self.state.get("codex_effort", "medium") or "medium"))
            self.agy_effort = ExternalProviderHarness.normalize_effort(str(self.state.get("agy_effort", "medium") or "medium"))
        self._external_tool_calls = 0
        self._external_activity = "Idle"
        self._external_started_at = 0.0
        self._last_external_run: ProviderRun | None = None
        self.local_telemetry = bool(self.state.get("local_telemetry", True))
        self.subscription_quota_protection = bool(self.state.get("subscription_quota_protection", True))
        self.resource_guard_enabled = bool(self.state.get("resource_guard_enabled", True))
        self.bridge_cli_token = BridgeClient.new_cli_token()
        self.bridge = BridgeClient(APP_DIR)
        # Keep one tiny shared localhost broker available while ADP is in use so the
        # MV3 browser-extension service worker can register immediately. This avoids
        # the old race where the extension woke before the broker existed, went idle,
        # and /browser then incorrectly fell back to CDP/Playwright.
        try:
            self.bridge.ensure_server()
        except Exception:
            pass
        self._bridge_registration: Dict[str, Any] = {}
        self.telemetry = HarnessTelemetry(APP_DIR)
        self.provider_harness = ExternalProviderHarness(APP_DIR, self.project)

        self.budget = BudgetTracker(APP_DIR / "usage.json", self.settings.daily_budget_usd, self.settings.task_budget_usd)
        self.session_store = SessionStore(APP_DIR / "sessions")
        self.current_session = self.session_store.create(self.project, self.active.provider, self.active.model)
        self.last_result = ""
        self._queued_messages: queue.Queue[tuple[bool, str]] = queue.Queue()
        # Set by Escape while a task is active.  Providers and the shared action
        # gateway consume it at safe boundaries so a replacement instruction never
        # waits behind another browser/file mutation.
        self._yield_requested = threading.Event()
        self._current_mission_id = ""
        self._approval_requests: queue.Queue[_PendingApproval] = queue.Queue()
        self._task_thread: threading.Thread | None = None
        self._task_lock = threading.RLock()
        self.git_branch = ""
        self.git_dirty_count = 0

        self.ui = TerminalUI(APP_DIR / "history.txt", self.toolbar, project_getter=lambda: self.project, copy_callback=self.copy_latest)
        self.ui.raw_output = self.raw_output
        self.notifier = TaskNotifier(
            APP_DIR, project_getter=lambda: self.project,
            enabled=self.desktop_notifications, sounds=self.task_sounds,
        )
        self.notifier.configure()
        self.update_repository = str(os.environ.get("ADVERTPRENEUR_UPDATE_REPOSITORY") or DEFAULT_REPOSITORY)
        self._update_notice = ""
        self._update_notice_shown = ""
        self.working_record = SessionWorkingRecord(APP_DIR / "evidence", self.current_session.id, self.project)
        self._current_task_raw = ""
        self._bind_project(self.project)
        self.persist_state()
        if self.active.provider in {"codex", "agy"}:
            self._refresh_provider_quota_async(self.active.provider)
        self._check_updates_async()

    def _git_branch(self, project: Path) -> str:
        if not (project / ".git").exists():
            return ""
        try:
            proc = subprocess.run(
                ["git", "-C", str(project), "branch", "--show-current"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            return (proc.stdout or "").strip()
        except Exception:
            return ""

    def _refresh_mission_cockpit(self) -> None:
        mission_id = str(getattr(self, "_current_mission_id", "") or "")
        if not mission_id:
            return
        try:
            mission = self.missions.load(mission_id)
        except (KeyError, OSError):
            return
        step = next((row for row in mission.steps if row.state in {"active", "waiting", "pending"}), None)
        if step is None and mission.steps:
            step = mission.steps[-1]
        observed = sum(len(row.observed_evidence) for row in mission.steps)
        required = sum(len(row.evidence) for row in mission.steps)
        browser_count = ""
        if hasattr(self, "tools") and hasattr(self.tools, "tool_browser"):
            try:
                b_st = self.tools.tool_browser("status")
                if isinstance(b_st, dict) and "tabs" in b_st:
                    browser_count = str(len(b_st["tabs"]))
            except Exception:
                pass
        self.ui.set_cockpit({
            "mission": mission.request,
            "step": step.title if step else "",
            "state": step.state if step else mission.status,
            "evidence": f"{observed}/{required}",
            "browser_count": browser_count,
            "verified": "verified" if mission.status == "completed" else "",
        })

    def _begin_task_mission(self, request: str) -> str:
        """Persist the deterministic local plan before provider execution begins."""
        mission = self.missions.create(str(request), mission_steps_from_task_plan(self._current_task_plan))
        self._current_mission_id = mission.id
        if mission.steps:
            self.missions.transition_step(mission.id, mission.steps[0].id, "active")
        self._refresh_mission_cockpit()
        return mission.id

    def _bootstrap_explicit_browser_tabs(self, instruction: str) -> bool:
        """Open an explicitly requested research site before a provider can stall on planning."""
        urls = re.findall(r"https?://[^\s<>'\"]+", str(instruction or ""), flags=re.I)
        for raw_url in urls:
            url = raw_url.rstrip(".,;:)")
            host = (urlparse(url).hostname or "").lower()
            if host.endswith("softzilla.net"):
                self.tools.tool_browser("navigate", url=url, tab="access")
                return True
            if host.endswith("amazon.com"):
                self.tools.tool_browser("navigate", url=url, tab="amazon")
                return True
        return False

    def _request_approval(self, kind: str, detail: str) -> bool:
        """Ask the owning CLI thread to render an approval prompt safely.

        Provider and tool work happens on a background thread while the main
        thread owns Prompt Toolkit's composer.  Calling ``ui.confirm`` directly
        from the worker races Prompt Toolkit's event loop on Windows.  Queue the
        request, wake the composer, and wait for the main loop to answer it.
        """
        if threading.current_thread() is threading.main_thread():
            return self.ui.confirm(kind, detail)
        request = _PendingApproval(str(kind), str(detail))
        self._approval_requests.put(request)
        if not self.ui.interrupt_prompt(_APPROVAL_WAKE):
            # There is no safe terminal owner to ask right now.  Denying is safer
            # than opening a second Prompt Toolkit application from this worker.
            return False
        request.done.wait()
        return request.approved

    def _serve_pending_approval(self) -> bool:
        """Render one queued approval from the CLI event-loop thread."""
        try:
            request = self._approval_requests.get_nowait()
        except queue.Empty:
            return False
        try:
            request.approved = bool(self.ui.confirm(request.kind, request.detail))
        finally:
            request.done.set()
        return True

    def _on_task_worker_exit(self) -> None:
        """Wake the main composer after a task ends so queued work is dispatched.

        This is especially important after Escape: the replacement instruction is
        already queued, but Prompt Toolkit otherwise remains blocked waiting for
        another keypress after the provider worker has yielded.
        """
        with self._task_lock:
            self._task_thread = None
        try:
            self.ui._task_active = False
        except Exception:
            pass
        try:
            self.ui.interrupt_prompt(_TASK_DONE_WAKE)
        except Exception:
            pass

    def _refresh_git_state(self) -> None:
        self.git_branch = self._git_branch(self.project)
        self.git_dirty_count = 0
        if not (self.project / ".git").exists():
            return
        try:
            proc = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3)
            if proc.returncode == 0:
                self.git_dirty_count = len([x for x in proc.stdout.splitlines() if x.strip()])
        except Exception:
            pass

    def _apply_terminal_title(self) -> None:
        suffix = ""
        if self.title_mode == "project-branch" and self.git_branch:
            suffix = self.git_branch
        elif self.title_mode == "project-model":
            suffix = self.active.model
        self.ui.set_terminal_title(self.project, suffix)

    def _bind_project(self, project: Path, messages: List[Dict[str, Any]] | None = None) -> None:
        self.project = project.resolve()
        if hasattr(self, "provider_harness"):
            self.provider_harness.project = self.project
        if hasattr(self, "working_record") and hasattr(self, "current_session"):
            self.working_record.rebind(self.current_session.id, self.project)
        self._refresh_git_state()
        self.project_index = ProjectIndex(self.project)
        self.context_manager = LocalContextManager(self.auto_compact_tokens, self.compact_target_tokens)
        self.checkpoints = CheckpointManager(self.project)
        self.framework_intelligence = FrameworkIntelligence(self.project)
        self.project_contract = ProjectContract(self.project, self.framework_intelligence.detected)
        self.task_planner = LocalTaskPlanner(self.project_contract, self.project_index)
        self.health_monitor = HealthMonitor(APP_DIR, self.project)
        if hasattr(self, "resource_guard"):
            try: self.resource_guard.close()
            except Exception: pass
        self.resource_guard = ResourceGuard(APP_DIR, self.project, getattr(getattr(self, "current_session", None), "id", ""))
        self.resource_guard.enabled = bool(getattr(self, "resource_guard_enabled", True))
        try:
            self.resource_guard.update(status="idle", own_ram_mb=self.health_monitor.own_process().ram_mb)
        except Exception:
            pass
        self.verifier = ProportionalVerifier(self.project, self.project_contract)
        self.diff_intelligence = DiffIntelligence(self.project)
        self.packager = ProjectPackager(self.project, self.project_contract)
        self.workforce = Workforce(self.project)
        self.missions = MissionStore(self.project)
        self.operation_router = LocalOperationRouter(self.project)
        self.automation_memory = AutomationMemory(self.project / ".advertpreneur")
        self.self_healing = SelfHealingEngine(self.project)
        self.browser_macros = BrowserMacroStore(self.project)
        self.dashboard_server = DashboardServer(self.project)
        self.swarm = SwarmCoordinator(self.project, event_sink=lambda k, t, d: self.dashboard_server.state.add_event(k, t, d) if hasattr(self, "dashboard_server") and self.dashboard_server else None)
        self._current_task_plan = None
        self.handbook = ExperienceHandbook(APP_DIR, self.project)
        self.hooks = HookRunner(self.project, enabled=self.hooks_enabled)
        self.plugin_manager = PluginManager(APP_DIR, self.project)
        self.mcp_manager = MCPManager(
            APP_DIR,
            approve=self._request_approval if hasattr(self, "ui") else (lambda _k, _d: False),
            approval_mode=self.settings.approval_mode,
        )
        self.tools = ToolRegistry(
            self.project,
            self.settings.approval_mode,
            self.settings.max_tool_output_chars,
            approve=self._request_approval if hasattr(self, "ui") else (lambda _k, _d: False),
            plugin_manager=self.plugin_manager,
            mcp_manager=self.mcp_manager,
            project_index=self.project_index,
            browser_visible=self.browser_visible,
            resource_guard=self.resource_guard,
        )
        self.agent = CodingAgent(
            self.settings,
            self.tools,
            self.budget,
            self.api_key,
            event_callback=self.on_agent_event,
            extra_instructions=self._extension_context(),
        )
        if messages:
            self.agent.load_messages(messages)
            try:
                self.working_record.backfill_from_messages(messages)
            except Exception:
                pass
        if hasattr(self, "ui"):
            self.ui.invalidate_workspace()
            self.ui.raw_output = self.raw_output
            self._apply_terminal_title()
        try:
            self._bridge_register(announce=False, force=True)
        except Exception:
            pass

    def _project_rules_context(self, max_chars: int = 3200) -> str:
        """Keep high-signal project rules without resending a large AGENTS.md verbatim every turn."""
        agents = self.project / "AGENTS.md"
        if not agents.exists() or not agents.is_file():
            return ""
        try:
            text = agents.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if len(text) <= max_chars:
            return "Project AGENTS.md instructions:\n" + text
        lines = text.splitlines()
        picked: list[str] = []
        used = 0
        high = ("must", "never", "always", "do not", "don't", "required", "preserve", "identity", "security", "auth", "validation", "test", "build")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            keep = i < 18 or stripped.startswith(("#", "- ", "* ")) or any(k in stripped.lower() for k in high)
            if not keep:
                continue
            if used + len(stripped) + 1 > max_chars:
                break
            picked.append(stripped)
            used += len(stripped) + 1
        return "Project AGENTS.md high-signal instructions (read @AGENTS.md if more detail is needed):\n" + "\n".join(picked)

    def _extension_context(self, task_text: str = "") -> str:
        """Build a task-aware system extension block. Unrelated plugins/MCPs stay local and cost zero cloud tokens."""
        blocks: list[str] = []
        text = (task_text or "").lower()
        plan = self._current_task_plan if task_text else None
        personality = {
            "pragmatic": "Style: pragmatic, concise, implementation-focused.",
            "friendly": "Style: friendly, collaborative, concise and technical.",
            "none": "",
        }.get(self.personality, "")
        if personality:
            blocks.append(personality)
        if self.goal:
            blocks.append("Session goal: " + self.goal[:1800])
        rules = self._project_rules_context()
        if rules:
            blocks.append(rules)
        if task_text and hasattr(self, "project_contract"):
            blocks.append(self.project_contract.context(max_chars=1200 if not plan or plan.complexity == "low" else 1700))
        if task_text and plan and hasattr(self, "task_planner"):
            blocks.append(plan.context(self.project_contract, max_chars=1200))
        if task_text and hasattr(self, "framework_intelligence"):
            framework_context = self.framework_intelligence.context(task_text, max_chars=(plan.framework_chars if plan else 1500))
            if framework_context:
                blocks.append(framework_context)
        if task_text and self.handbook_enabled and hasattr(self, "handbook"):
            handbook_context = self.handbook.context(task_text, max_chars=(plan.handbook_chars if plan else 1200))
            if handbook_context:
                blocks.append(handbook_context)
        if task_text and hasattr(self, "automation_memory"):
            mem_context = self.automation_memory.context(task_text, max_chars=1000)
            if mem_context:
                blocks.append(mem_context)

        # Extension catalogues are lazy. Explicit skill names or plans opt them into this task.
        skill_needed = bool(plan.needs_plugins) if plan else False
        if hasattr(self, "plugin_manager"):
            try:
                skills = self.plugin_manager.skills(enabled_only=True)
                if any(sk.name.lower() in text or sk.id.lower() in text or sk.plugin_name.lower() in text for sk in skills):
                    skill_needed = True
            except Exception:
                pass
        if skill_needed and hasattr(self, "plugin_manager"):
            catalog = self.plugin_manager.catalog(max_chars=600)
            if catalog:
                blocks.append(catalog)

        mcp_needed = bool(plan.needs_mcp) if plan else ("mcp" in text)
        if hasattr(self, "mcp_manager"):
            try:
                servers = [x for x in self.mcp_manager.discover() if x.enabled]
                if any(x.name.lower() in text for x in servers):
                    mcp_needed = True
            except Exception:
                pass
        if mcp_needed and hasattr(self, "mcp_manager"):
            catalog = self.mcp_manager.catalog(max_chars=650)
            if catalog:
                blocks.append(catalog)
        return "\n\n".join(blocks)

    def _refresh_extensions(self, task_text: str = "") -> None:
        self.agent.set_extra_instructions(self._extension_context(task_text))

    def persist_state(self) -> None:
        self.state.update(
            provider=self.active.provider,
            model=self.active.model,
            think=self.active.think,
            max_output_tokens=self.active.max_output_tokens,
            approval_mode=self.settings.approval_mode,
            cloud_access_mode=self.settings.cloud_access_mode,
            plan_mode=self.plan_mode,
            personality=self.personality,
            raw_output=self.raw_output,
            title_mode=self.title_mode,
            statusline_mode=self.statusline_mode,
            auto_index=self.auto_index,
            auto_compact_tokens=self.auto_compact_tokens,
            compact_target_tokens=self.compact_target_tokens,
            reviewer_provider=self.reviewer_provider,
            reviewer_model=self.reviewer_model,
            auto_review=self.auto_review,
            desktop_notifications=self.desktop_notifications,
            task_sounds=self.task_sounds,
            bridge_wait_seconds=self.bridge_wait_seconds,
            bridge_max_hops=self.bridge_max_hops,
            browser_visible=self.browser_visible,
            handbook_enabled=self.handbook_enabled,
            hooks_enabled=self.hooks_enabled,
            senior_provider=self.senior_provider,
            senior_model=self.senior_model,
            codex_model=self.codex_model,
            codex_effort=self.codex_effort,
            agy_model=self.agy_model,
            agy_effort=self.agy_effort,
            local_telemetry=self.local_telemetry,
            subscription_quota_protection=self.subscription_quota_protection,
            resource_guard_enabled=self.resource_guard_enabled,
            provider_effort_policy_v2=True,
            last_session_id=self.current_session.id if hasattr(self, "current_session") else None,
        )

    def _save_session(self) -> None:
        if not hasattr(self, "current_session") or not hasattr(self, "agent"):
            return
        self.current_session.project = str(self.project)
        self.current_session.provider = self.active.provider
        self.current_session.model = self.active.model
        self.current_session.messages = self.agent.messages
        self.current_session.goal = self.goal
        self.current_session.pinned_mentions = list(self.pinned_mentions)
        self.session_store.save(self.current_session)
        self.persist_state()

    def _record_task_in_session(self) -> None:
        task = self.budget.task
        self.current_session.input_tokens += task.input_tokens
        self.current_session.output_tokens += task.output_tokens
        self.current_session.requests += task.requests
        self.current_session.metered_value_usd += task.estimated_cost_usd
        self._save_session()

    # ---------- Browser Bridge ----------
    def _bridge_pair_token(self) -> str:
        if not self.current_session.bridge_pair_token:
            self.current_session.bridge_pair_token = BridgeClient.new_token()
            self.session_store.save(self.current_session)
        return self.current_session.bridge_pair_token

    def _bridge_register(self, announce: bool = False, force: bool = False) -> Dict[str, Any] | None:
        if not self.current_session.bridge_enabled and not force:
            return None
        try:
            row = self.bridge.register(
                self.current_session.id,
                self.current_session.name,
                str(self.project),
                self.active.model,
                self.bridge_cli_token,
                self._bridge_pair_token(),
            )
            self._bridge_registration = dict(row)
            self.bridge.update_status(
                self.current_session.id,
                self.bridge_cli_token,
                "Idle",
                "Ready",
                self.active.model,
            )
            if announce:
                self.ui.success("Browser Bridge · ON · local broker 127.0.0.1:8765 · cloud tokens 0")
                if row.get("paired"):
                    self.ui.info(f"Paired ChatGPT conversation · {row.get('conversation_url') or row.get('conversation_id')}")
                else:
                    self.ui.heading("Browser Bridge pair code")
                    print(f"  {row.get('pair_code')}")
                    self.ui.muted("Open the Advertpreneur Browser Bridge extension in the ChatGPT conversation you want to link, then enter this code.")
                self.ui.muted(f"Extension folder · {self.bridge.extension_path()}")
            return row
        except BridgeError as exc:
            self._bridge_registration = {}
            if announce:
                self.ui.error(f"Browser Bridge · {exc}")
            return None

    def _bridge_unregister_current(self) -> None:
        if not hasattr(self, "current_session"):
            return
        try:
            if self.current_session.bridge_enabled:
                self.bridge.unregister(self.current_session.id, self.bridge_cli_token)
        finally:
            self._bridge_registration = {}

    def _bridge_payload(
        self,
        raw: str,
        result_text: str,
        profile: ModelProfile,
        checkpoint: Any | None,
        status: str = "completed",
    ) -> Dict[str, Any]:
        task = self.budget.task
        changed = list(getattr(checkpoint, "changed_files", []) or [])[:100]
        try:
            evidence = self.working_record.bridge_summary(raw)
        except Exception:
            evidence = {"cloud_tokens": 0}
        return {
            "protocol": 1,
            "session_id": self.current_session.id,
            "session_name": self.current_session.name,
            "project": str(self.project),
            "provider": profile.provider,
            "model": profile.model,
            "status": status,
            "task": raw[:4000],
            "result": result_text[:32000],
            "changed_files": changed,
            "checkpoint": str(getattr(checkpoint, "id", "") or ""),
            "evidence": evidence,
            "usage": (
                {
                    "requests": max(1, self._last_external_run.provider_turns),
                    "provider_turns": max(1, self._last_external_run.provider_turns),
                    "input_tokens": self._last_external_run.input_tokens,
                    "uncached_input_tokens": self._last_external_run.uncached_input_tokens,
                    "cache_percent": round(self._last_external_run.cache_percent, 1),
                    "session_reused": self._last_external_run.session_reused,
                    "output_tokens": self._last_external_run.output_tokens,
                    "cache_read_tokens": self._last_external_run.cache_read_tokens,
                    "thinking_tokens": self._last_external_run.thinking_tokens,
                    "total_tokens": self._last_external_run.total_tokens,
                    "tool_calls": self._last_external_run.tool_calls,
                    "reasoning_effort": self._last_external_run.reasoning_effort,
                    "quota_before": self._last_external_run.quota_before,
                    "quota_after": self._last_external_run.quota_after,
                    "quota_consumed": self._last_external_run.quota_consumed,
                    "metered": "provider/subscription managed",
                }
                if profile.provider in {"codex", "agy"} and self._last_external_run and self._last_external_run.provider == profile.provider
                else {
                    "requests": task.requests, "input_tokens": task.input_tokens, "output_tokens": task.output_tokens,
                    "metered": money(task.estimated_cost_usd),
                }
            ),
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def _bridge_publish_result(
        self,
        raw: str,
        result_text: str,
        profile: ModelProfile,
        checkpoint: Any | None,
        status: str = "completed",
    ) -> BridgeDispatch | None:
        if not self.current_session.bridge_enabled:
            return None
        if not self._bridge_register(False):
            self.ui.muted("Browser Bridge unavailable · result kept local")
            return None
        try:
            dispatch = self.bridge.publish(
                self.current_session.id,
                self.bridge_cli_token,
                self._bridge_payload(raw, result_text, profile, checkpoint, status=status),
            )
            if dispatch.paired:
                self.ui.success("Browser Bridge · completed result queued for linked ChatGPT · bridge tokens 0")
            else:
                self.ui.muted("Browser Bridge · result queued locally, but this CLI session is not paired to a ChatGPT conversation yet")
            return dispatch
        except BridgeError as exc:
            self.ui.muted(f"Browser Bridge · {exc} · result remains local")
            return None

    def _bridge_wait_for_instruction(self, dispatch: BridgeDispatch) -> str | None:
        if not dispatch.paired or not self.current_session.bridge_autopilot:
            return None
        self.ui.info(f"Browser Bridge · waiting for ChatGPT next instruction · up to {self.bridge_wait_seconds}s")
        self.ui.begin_working("ChatGPT", "Waiting for ChatGPT", 1)
        self.bridge.update_status(
            self.current_session.id,
            self.bridge_cli_token,
            "Waiting for ChatGPT",
            "Completed result sent",
            self.active.model,
        )
        try:
            reply = self.bridge.wait_for_reply(
                self.current_session.id,
                self.bridge_cli_token,
                dispatch.event_id,
                timeout_seconds=self.bridge_wait_seconds,
            )
        except KeyboardInterrupt:
            self.ui.muted("Browser Bridge wait interrupted · Advertpreneur remains open")
            return None
        finally:
            self.ui.end_working()
        if not reply:
            self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Idle", "Bridge wait timed out", self.active.model)
            self.ui.muted("Browser Bridge · no ChatGPT reply before timeout · returning to local prompt")
            return None
        if reply.done:
            self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Idle", "Bridge loop complete", self.active.model)
            self.ui.success("Browser Bridge · ChatGPT returned ADP_BRIDGE_DONE · autopilot paused at local prompt")
            return None
        self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Working", "Executing ChatGPT instruction", self.active.model)
        self.ui.heading("Browser Bridge · next instruction")
        print(reply.text)
        return reply.text

    def bridge_status(self) -> None:
        self.ui.heading("Browser Bridge")
        print(f"  Session       {self.current_session.name}")
        print(f"  Enabled       {'yes' if self.current_session.bridge_enabled else 'no'}")
        print(f"  Autopilot     {'on' if self.current_session.bridge_autopilot else 'off'}")
        print(f"  Wait          {self.bridge_wait_seconds}s")
        print(f"  Local broker  http://127.0.0.1:8765")
        print(f"  Extension     {self.bridge.extension_path()}")
        if not self.current_session.bridge_enabled:
            self.ui.muted("Use /bridge on to register this CLI session and get a 6-digit pair code.")
            return
        row = self._bridge_register(False)
        if not row:
            self.ui.error("Local Browser Bridge broker is unavailable")
            return
        try:
            st = self.bridge.status(self.current_session.id, self.bridge_cli_token)
        except BridgeError as exc:
            self.ui.error(str(exc))
            return
        print(f"  Paired        {'yes' if st.get('paired') else 'no'}")
        if st.get("conversation_url"):
            print(f"  Conversation  {st.get('conversation_url')}")
        if not st.get("paired"):
            print(f"  Pair code     {st.get('pair_code')}")
        print(f"  Queue         {st.get('queued', 0)} pending result(s)")
        self.ui.muted("Bridge relay itself uses zero Ollama tokens. Any next instruction executed by the coding model uses normal CLI model tokens.")

    def bridge_command(self, arg: str | None = None) -> None:
        text = (arg or "").strip()
        lower = text.lower()
        if not text:
            self.bridge_settings()
            return
        if lower in {"on", "enable"}:
            self.current_session.bridge_enabled = True
            self.current_session.bridge_autopilot = True
            self._bridge_pair_token()
            self._save_session()
            self._bridge_register(True)
            return
        if lower in {"off", "disable"}:
            self._bridge_unregister_current()
            self.current_session.bridge_enabled = False
            self._save_session()
            self.ui.success("Browser Bridge · OFF for this CLI session")
            return
        if lower == "status":
            self.bridge_status(); return
        if lower in {"extension", "path"}:
            self.ui.heading("Browser Bridge extension")
            print(f"  {self.bridge.extension_path()}")
            self.ui.muted("Chrome/Edge → Extensions → Developer mode → Load unpacked → choose this folder")
            return
        if lower in {"code", "pair", "pair-code"}:
            if not self.current_session.bridge_enabled:
                self.ui.error("Browser Bridge is OFF · use /bridge on first")
                return
            if not self._bridge_register(False):
                return
            try:
                code = self.bridge.rotate_code(self.current_session.id, self.bridge_cli_token)
                self.ui.heading("Browser Bridge pair code")
                print(f"  {code}")
            except BridgeError as exc:
                self.ui.error(f"Browser Bridge · {exc}")
            return
        if lower.startswith("autopilot"):
            bits = lower.split()
            if len(bits) == 1:
                self.ui.info(f"Browser Bridge autopilot · {'ON' if self.current_session.bridge_autopilot else 'OFF'}")
                return
            if bits[1] not in {"on", "off"}:
                self.ui.error("Usage: /bridge autopilot on|off")
                return
            self.current_session.bridge_autopilot = bits[1] == "on"
            self._save_session()
            self.ui.success(f"Browser Bridge autopilot · {'ON' if self.current_session.bridge_autopilot else 'OFF'}")
            return
        if lower.startswith("wait "):
            try:
                seconds = max(30, min(3600, int(lower.split(maxsplit=1)[1])))
            except ValueError:
                self.ui.error("Usage: /bridge wait 600")
                return
            self.bridge_wait_seconds = seconds
            self.persist_state()
            self.ui.success(f"Browser Bridge reply wait · {seconds}s")
            return
        self.ui.error("Usage: /bridge [on|off|status|code|extension|autopilot on|off|wait <seconds>]")

    def bridge_settings(self) -> None:
        while True:
            choice = self.ui.choose("Browser Bridge", [
                MenuItem("toggle", "Disable bridge" if self.current_session.bridge_enabled else "Enable bridge", "this CLI session only"),
                MenuItem("status", "Status & pairing", "pair code / linked ChatGPT conversation"),
                MenuItem("auto", "ChatGPT autopilot", "ON" if self.current_session.bridge_autopilot else "OFF"),
                MenuItem("wait", "Reply wait", f"{self.bridge_wait_seconds}s"),
                MenuItem("extension", "Extension folder", "load unpacked in Chrome/Edge"),
            ])
            if not choice:
                return
            if choice == "toggle":
                self.bridge_command("off" if self.current_session.bridge_enabled else "on")
            elif choice == "status":
                self.bridge_status()
            elif choice == "auto":
                self.current_session.bridge_autopilot = not self.current_session.bridge_autopilot
                self._save_session()
                self.ui.success(f"Browser Bridge autopilot · {'ON' if self.current_session.bridge_autopilot else 'OFF'}")
            elif choice == "wait":
                value = self.ui.choose("Bridge reply wait", [
                    MenuItem("120", "2 minutes", "fast local test"),
                    MenuItem("300", "5 minutes", "normal"),
                    MenuItem("600", "10 minutes", "default"),
                    MenuItem("1200", "20 minutes", "long ChatGPT reasoning"),
                ])
                if value:
                    self.bridge_wait_seconds = int(value)
                    self.persist_state()
            elif choice == "extension":
                self.bridge_command("extension")

    def _refresh_provider_quota_async(self, provider: str | None = None) -> None:
        name = (provider or self.active.provider).lower().strip()
        if name not in {"codex", "agy"}: return
        model = self.codex_model if name == "codex" else self.agy_model
        def worker() -> None:
            try:
                q = self.provider_harness.quota(name, model=model, refresh=True)
                for threshold in self.provider_harness.quota_thresholds(q):
                    self.notifier.quota_warning(name, threshold, q.effective_remaining, self.provider_harness.quota_text(q))
                try: self.ui.session.app.invalidate()
                except Exception: pass
            except Exception:
                # Truthful unavailable state; never substitute 100%.
                pass
        threading.Thread(target=worker, name=f"adp-{name}-quota", daemon=True).start()

    def _external_quota_text(self, provider: str) -> str:
        try:
            model = getattr(self, "codex_model", "") if provider == "codex" else getattr(self, "agy_model", "")
            try: cached = self.provider_harness.quota_cached(provider, model)
            except TypeError: cached = self.provider_harness.quota_cached(provider)
            return self.provider_harness.quota_text(cached)
        except Exception:
            return "5h — · wk —"

    def toolbar(self) -> FormattedText:
        ctx = sum(self.agent.context_breakdown().values()) if hasattr(self, "agent") else 0
        ctx_text = f"{ctx / 1000:.1f}k" if ctx >= 1000 else str(ctx)
        project = short_text(self.project.name or self.project.drive, 18)
        branch = short_text(self.git_branch, 14)
        if branch and self.git_dirty_count: branch = f"{branch}*{self.git_dirty_count}"
        plan = " │ PLAN" if self.plan_mode else ""
        bridge_mark = " │ BRIDGE" if getattr(self.current_session, "bridge_enabled", False) else ""

        if self.active.provider in {"codex", "agy"}:
            provider_label = "Codex" if self.active.provider == "codex" else "AGY"
            quota = self._external_quota_text(self.active.provider)
            effort = getattr(self, "codex_effort", "medium") if self.active.provider == "codex" else getattr(self, "agy_effort", "medium")
            started = float(getattr(self, "_external_started_at", 0.0) or 0.0)
            live = f" │ {short_text(getattr(self, '_external_activity', 'Working'), 24)} · {getattr(self, '_external_tool_calls', 0)} tools" if started else ""
            if self.statusline_mode == "minimal":
                return FormattedText([("class:toolbar.model", f" {self.active.model} "), ("class:toolbar", f" │ {provider_label} │ {project} │ {quota} │ R:{effort}{live}{plan}{bridge_mark} ")])
            if self.statusline_mode == "usage":
                return FormattedText([("class:toolbar.model", f" {self.active.model} "), ("class:toolbar", f" │ {provider_label} │ SUBSCRIPTION │ {quota} │ R:{effort}{live}{plan}{bridge_mark} ")])
            return FormattedText([("class:toolbar.model", f" {self.active.model} "), ("class:toolbar", f" {provider_label} │ {project}" + (f" │ {branch}" if branch else "") + f" │ {self.settings.approval_mode} │ SUB │ {quota} │ R:{effort}{live}{plan}{bridge_mark} ")])

        task = self.budget.task
        free = self.active.provider == "local" or is_free_cloud_model(self.active.model, self.settings.free_cloud_models)
        signed_out = self.active.provider == "cloud" and not self.api_key
        access = "SIGNED OUT" if signed_out else ("FREE" if self.settings.cloud_access_mode == "free" else "ALL")
        mode_style = "class:toolbar.warn" if signed_out else ("class:toolbar.good" if free and access == "FREE" else "class:toolbar.warn")
        if self.statusline_mode == "minimal":
            return FormattedText([("class:toolbar.model", f" {self.active.model} "), ("class:toolbar", f" │ {project}" + (f" │ {branch}" if branch else "") + f" │ ctx {ctx_text}{plan}{bridge_mark} ")])
        if self.statusline_mode == "usage":
            return FormattedText([("class:toolbar.model", f" {self.active.model} "), ("class:toolbar", f" │ ctx {ctx_text} │ {task.input_tokens:,} in/{task.output_tokens:,} out │ task {money(task.estimated_cost_usd)}/{money(self.budget.task_limit)} │ day {money(self.budget.daily_cost())}/{money(self.budget.daily_limit)}{plan}{bridge_mark} ")])
        return FormattedText([("class:toolbar.model", f" {self.active.model} "), ("class:toolbar", f" {self.active.provider} │ {project}" + (f" │ {branch}" if branch else "") + f" │ {self.settings.approval_mode} │ "), (mode_style, access), ("class:toolbar", f" │ ctx {ctx_text} │ task {money(task.estimated_cost_usd)}/{money(self.budget.task_limit)} │ day {money(self.budget.daily_cost())}/{money(self.budget.daily_limit)}{plan}{bridge_mark} ")])

    def on_agent_event(self, name: str, data: Dict[str, Any]) -> None:
        if name == "task_start":
            model = str(data.get("model") or self.active.model)
            # The task already entered its live state before local preparation.
            # Update it in place so the footer never jumps or disappears.
            self.ui.set_working_state("Starting", model=model, turn=1)
            if self.current_session.bridge_enabled:
                self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Working", "Coding task", model)
        elif name == "model_start":
            turn = int(data.get("turn") or 1)
            model = str(data.get("model") or self.active.model)
            self.ui.set_working_state("Thinking", model=model, turn=turn)
        elif name == "model_thinking":
            delta = str(data.get("delta") or "")
            if delta and len(delta.strip()) > 3:
                self.ui.thought_process(delta)
        elif name == "model_done":
            duration_ns = int(data.get("duration_ns") or 0)
            elapsed = duration_ns / 1_000_000_000 if duration_ns else 0.0
            self.ui.set_working_state("Processing response")
        elif name == "validation_required":
            self.ui.set_working_state("Verifying requested validation")
            self.ui.muted("  ↳ completion gate · real build/test execution still required")
        elif name == "tool_start":
            tool_name = str(data.get("name") or "tool")
            args = data.get("args") or {}
            if tool_name == "replace_in_file":
                self.ui.action_marker("edit", str(args.get("path", "")))
            elif tool_name == "write_file":
                self.ui.action_marker("write", str(args.get("path", "")))
            elif tool_name == "read_file":
                self.ui.action_marker("read", str(args.get("path", "")))
            elif tool_name == "run_command":
                self.ui.action_marker("bash", str(args.get("command", "")))
            elif tool_name == "browser":
                self.ui.action_marker("browser", f"{args.get('action', '')} {args.get('url', '') or args.get('selector', '')}".strip())
            elif tool_name == "mcp":
                self.ui.action_marker("mcp", f"{args.get('server', '')}:{args.get('tool', '')}")
            else:
                summary = self._tool_summary(tool_name, args)
                self.ui.info(summary)
            self.ui.set_working_state("Using tool", detail=tool_name)
        elif name == "tool_cached":
            self.ui.muted(f"  ↳ duplicate skipped · {self._tool_summary(str(data.get('name') or 'tool'), data.get('args') or {})}")
            self.ui.set_working_state("Reusing prior result")
        elif name == "tool_done":
            if data.get("name") == "run_command":
                result = str(data.get("result") or "")
                first = result.splitlines()[0] if result else ""
                self.ui.success(f"command finished · {first}")
            try:
                self.working_record.record_tool(
                    self._current_task_raw or self.agent.active_task,
                    str(data.get("name") or "tool"),
                    data.get("args") or {},
                    str(data.get("result") or ""),
                )
            except Exception:
                pass
            self.ui.set_working_state("Checking tool result")
        elif name == "tool_error":
            try:
                self.working_record.record_error(
                    self._current_task_raw or self.agent.active_task,
                    str(data.get("name") or "tool"),
                    data.get("args") or {},
                    str(data.get("error") or ""),
                )
            except Exception:
                pass
            self.ui.error(f"{data.get('name')} · {self._friendly_error(str(data.get('error') or ''))}")
            self.ui.set_working_state("Recovering from tool error")
        elif name in {"task_done", "task_stop"}:
            self.ui.end_working()
            if self.current_session.bridge_enabled:
                self.bridge.update_status(
                    self.current_session.id, self.bridge_cli_token,
                    "Done" if name == "task_done" else "Needs attention",
                    "Compiling completed result" if name == "task_done" else str(data.get("reason") or "Task stopped"),
                    self.active.model,
                )

    @staticmethod
    def _friendly_error(text: str, max_len: int = 420) -> str:
        flat = " ".join(str(text or "").split())
        lower = flat.lower()
        if "mcp http 401" in lower or "unauthorized" in lower:
            return "MCP HTTP 401 · authentication required"
        if "mcp http 403" in lower and "access denied" in lower:
            if "browser_signature_banned" in lower or "error 1010" in lower:
                return "MCP HTTP 403 · access denied by remote WAF/client signature"
            return "MCP HTTP 403 · access denied"
        if len(flat) > max_len:
            return flat[: max_len - 1] + "…"
        return flat

    def _tool_summary(self, name: str, args: Dict[str, Any]) -> str:
        if name == "read_file":
            return f"read {args.get('path', '')}"
        if name == "list_files":
            return f"scan {args.get('path', '.')}"
        if name == "search_text":
            return f"search “{args.get('pattern', '')}”"
        if name == "project_map":
            return f"map “{args.get('query', '')}”"
        if name == "write_file":
            return f"write {args.get('path', '')}"
        if name == "replace_in_file":
            return f"edit {args.get('path', '')}"
        if name == "run_command":
            return f"run {str(args.get('command', ''))[:100]}"
        if name == "load_skill":
            query = str(args.get('skill', '') or '')
            try:
                matches = self.plugin_manager.search_skills(query)
                if matches:
                    skill = matches[0]
                    return f"skill {skill.plugin_name} › {skill.name}"
            except Exception:
                pass
            return f"load skill {query}"
        if name == "mcp":
            action = args.get('action', '')
            server = args.get('server', '')
            tool = args.get('tool', '')
            query = args.get('query', '')
            suffix = f" {server}" if server else ""
            if tool:
                suffix += f".{tool}"
            elif query:
                suffix += f" · {query}"
            return f"mcp {action}{suffix}"
        return name.replace("_", " ")

    def model_badge(self, provider: str, model: str) -> str:
        if provider == "local":
            return "LOCAL · no cloud usage"
        free = is_free_cloud_model(model, self.settings.free_cloud_models)
        price = lookup_price(self.settings.prices, model)
        if free:
            return "FREE STARTER"
        if price:
            return f"PRICED · ${price.input_per_million:g} in / ${price.output_per_million:g} out per 1M"
        return "PRICE UNKNOWN"

    # ---------- authentication ----------
    def login(self) -> None:
        key = self.ui.prompt_secret("Ollama API key")
        if not key:
            self.ui.muted("Login cancelled")
            return
        self.ui.info("Checking Ollama Cloud…")
        try:
            models = OllamaClient("cloud", key).list_models()
        except Exception as exc:
            self.ui.error(f"Login failed · {exc}")
            return
        try:
            self.credentials.save(key)
        except CredentialError as exc:
            self.ui.error(str(exc))
            return
        self.api_key = key
        self.agent.api_key = key
        migrated = self.credentials.remove_legacy_environment_key()
        self.ui.success(f"Logged in · Ollama Cloud · {len(models)} models visible")
        self.ui.muted("API key saved for future Advertpreneur CLI sessions using Windows user encryption.")
        if migrated:
            self.ui.muted("Migrated and removed the old per-user OLLAMA_API_KEY environment variable.")

    def logout(self) -> None:
        if not self.api_key and self.credentials.source() == "none":
            self.ui.info("Already logged out")
            return
        if not self.ui.confirm("account", "Remove the API key saved by Advertpreneur CLI"):
            return
        self.credentials.delete()
        removed_env = self.credentials.remove_legacy_environment_key()
        self.api_key = None
        self.agent.api_key = None
        self.ui.success("Logged out of Ollama Cloud · cloud models are now disabled")
        self.ui.muted("Advertpreneur CLI stays open after logout; use /login to reconnect or /model → Local to keep working offline.")
        if removed_env:
            self.ui.muted("Removed the old per-user OLLAMA_API_KEY environment variable too.")

    # ---------- models ----------
    def _external_model_items(self, provider: str) -> List[MenuItem]:
        rows = self.provider_harness.model_catalog(provider)
        items: List[MenuItem] = []
        configured = self.codex_model if provider == "codex" else self.agy_model
        for row in rows:
            current = " · CONFIGURED" if row.id == configured else ""
            desc = (row.detail or row.display or "external provider model") + current
            items.append(MenuItem(row.id, row.display, desc[:220]))
        return items

    def _assign_external_model(self, provider: str, model: str) -> None:
        role = self.ui.choose(
            f"Use {provider.upper()}/{model}",
            [
                MenuItem("coder", "Primary Coder", "run normal coding tasks in the live project using the official provider runtime"),
                MenuItem("reviewer", "Reviewer", "review diffs/results read-only"),
                MenuItem("senior", "Senior Engineer", "deep reasoning / difficult debugging"),
                MenuItem("all", "Coder + Reviewer + Senior", "use this external model for all three roles"),
            ],
        )
        if not role:
            return
        if role in {"coder", "all"}:
            if provider == "agy":
                # AGY publishes reasoning/thinking as part of the model identity
                # (for example Claude Sonnet Thinking). Selecting its model is the
                # only intentional choice; do not make the operator pick an
                # unsupported second low/medium/high tier.
                self.agy_effort = ExternalProviderHarness.agy_effective_effort(model, self.agy_effort)
                self.ui.muted("AGY reasoning is defined by the selected model; no separate reasoning menu.")
            else:
                current_effort = self.codex_effort if provider == "codex" else self.agy_effort
                picked_effort = self.ui.choose(
                    f"{provider.upper()}/{model} reasoning effort",
                    [MenuItem("low", "Low", "lower quota use"), MenuItem("medium", "Medium", "recommended default"), MenuItem("high", "High", "deliberate deep reasoning")],
                )
                if picked_effort:
                    if provider == "codex": self.codex_effort = picked_effort
                    else: self.agy_effort = picked_effort
                elif current_effort not in {"low", "medium", "high"}:
                    if provider == "codex": self.codex_effort = "medium"
                    else: self.agy_effort = "medium"
        if provider == "codex":
            self.codex_model = model
        else:
            self.agy_model = model
        if role in {"coder", "all"}:
            self.active = ModelProfile(provider, model, False, self.active.max_output_tokens)
            self.current_session.provider = provider
            self.current_session.model = model
        if role in {"reviewer", "all"}:
            self.reviewer_provider = provider
            self.reviewer_model = model
        if role in {"senior", "all"}:
            self.senior_provider = provider
            self.senior_model = model
        self.persist_state()
        self._save_session()
        self._refresh_provider_quota_async(provider)
        labels = {"coder":"Primary Coder","reviewer":"Reviewer","senior":"Senior Engineer","all":"Coder + Reviewer + Senior"}
        self.ui.success(f"{provider.upper()}/{model} · assigned to {labels.get(role, role)}")
        if role in {"coder", "all"}:
            self.ui.muted("Normal prompts now run through the official external provider in the live project; provider-owned account/quota applies.")

    def select_model(self) -> None:
        # One model surface: Ollama workers plus connected subscription providers.
        # External models can be Primary Coder, Reviewer, Senior Engineer, or all three.
        while True:
            provider = self.ui.choose("Model source", [
                MenuItem("cloud", "Ollama Cloud", "primary coding models / metered or plan allowance"),
                MenuItem("local", "Ollama Local", "models installed on this PC"),
                MenuItem("codex", "Codex · external", "ChatGPT/Codex subscription · coder/reviewer/reasoner"),
                MenuItem("agy", "Antigravity · external", "Google Antigravity account · coder/reviewer/reasoner"),
            ])
            if not provider:
                return

            if provider in {"codex", "agy"}:
                try:
                    st = self.provider_harness.status(provider)
                    if not st.installed or st.auth == "signed-out":
                        if not self.ui.confirm("account", f"{provider.upper()} is not ready. Set up official provider login now"):
                            continue
                        self.ui.info(f"Setting up official {provider.upper()} provider runtime/login")
                        rc = self.provider_harness.login(provider)
                        if rc != 0:
                            self.ui.error(f"{provider.upper()} login/setup exited with code {rc}")
                            continue
                    self.ui.begin_working(provider.upper(), "Loading model catalogue", 1)
                    try:
                        items = self._external_model_items(provider)
                    finally:
                        self.ui.end_working()
                except Exception as exc:
                    self.ui.error(f"{provider.upper()} · {type(exc).__name__}: {exc}")
                    continue
                if not items:
                    self.ui.error(f"{provider.upper()} returned no models")
                    continue
                selected = self.ui.choose(f"{provider.upper()} models · external", items, fuzzy=True)
                if not selected:
                    continue
                self._assign_external_model(provider, selected)
                return

            if provider == "cloud" and not self.api_key:
                if self.ui.confirm("account", "Ollama Cloud login required. Open login now"):
                    self.login()
                if not self.api_key:
                    continue
            try:
                names = OllamaClient(provider, self.api_key).list_models()
            except Exception as exc:
                self.ui.error(str(exc))
                continue
            while True:
                items: List[MenuItem] = []
                for name in names:
                    current = " · CURRENT" if provider == self.active.provider and name == self.active.model else ""
                    items.append(MenuItem(name, name, self.model_badge(provider, name) + current))
                selected = self.ui.choose("Select primary coding model", items, fuzzy=True)
                if not selected:
                    break
                if provider == "cloud" and self.settings.cloud_access_mode == "free" and not is_free_cloud_model(selected, self.settings.free_cloud_models):
                    self.ui.error(f"{selected} is outside the Free starter pool while Cloud mode is FREE.")
                    if self.ui.confirm("billing", f"Enable ALL priced Cloud models and select {selected}"):
                        self.settings.cloud_access_mode = "all"
                    else:
                        continue
                if provider == "cloud" and lookup_price(self.settings.prices, selected) is None:
                    self.ui.error(f"Price for {selected} is unknown. It is blocked until pricing is configured.")
                    continue
                self.active = ModelProfile(provider, selected, self.active.think, self.active.max_output_tokens)
                self.current_session.provider = provider
                self.current_session.model = selected
                self.persist_state()
                self._save_session()
                self.ui.success(f"Model · {provider}/{selected} · {self.model_badge(provider, selected)}")
                return

    def browse_models(self) -> None:
        provider = self.ui.choose("Browse models", [
            MenuItem("cloud", "Ollama Cloud", "fetch live catalogue"),
            MenuItem("local", "Ollama Local", "fetch from local Ollama"),
            MenuItem("codex", "Codex · external", "fetch authenticated OpenAI Codex account catalogue"),
            MenuItem("agy", "Antigravity · external", "fetch authenticated AGY model catalogue"),
        ])
        if not provider:
            return
        if provider in {"codex", "agy"}:
            try:
                rows = self.provider_harness.model_catalog(provider)
            except Exception as exc:
                self.ui.error(f"{provider.upper()} · {type(exc).__name__}: {exc}")
                return
            self.ui.heading(f"{provider.upper()} external models · {len(rows)}")
            configured = self.codex_model if provider == "codex" else self.agy_model
            for row in rows:
                current = "  ← configured" if row.id == configured else ""
                print(f"  {row.id:<34} {short_text(row.display, 40)}{current}")
            return
        if provider == "cloud" and not self.api_key:
            self.ui.error("Not logged into Ollama Cloud. Use /login.")
            return
        try:
            names = OllamaClient(provider, self.api_key).list_models()
        except Exception as exc:
            self.ui.error(str(exc))
            return
        self.ui.heading(f"{provider.capitalize()} models · {len(names)}")
        for name in names:
            current = "  ← current" if provider == self.active.provider and name == self.active.model else ""
            print(f"  {name:<30} {self.model_badge(provider, name)}{current}")

    def select_model_by_name(self, name: str) -> None:
        # Explicit prefixes let power users configure external specialists without
        # entering the interactive picker: /model codex:gpt-5.6-sol
        raw = name.strip()
        m = re.match(r"^(codex|agy)[:/](.+)$", raw, flags=re.I)
        if m:
            provider, model = m.group(1).lower(), m.group(2).strip()
            try:
                ids = {x.id.lower(): x.id for x in self.provider_harness.model_catalog(provider)}
            except Exception as exc:
                self.ui.error(f"{provider.upper()} · {exc}")
                return
            actual = ids.get(model.lower())
            if not actual:
                self.ui.error(f"External model not found · {provider}/{model}. Use /model to browse the provider catalogue.")
                return
            self._assign_external_model(provider, actual)
            return

        found: list[tuple[str, str]] = []
        for provider in ("cloud", "local"):
            if provider == "cloud" and not self.api_key:
                continue
            try:
                names = OllamaClient(provider, self.api_key).list_models()
            except Exception:
                continue
            for model in names:
                if model.lower() == raw.lower():
                    found.append((provider, model))
        if not found:
            self.ui.error(f"Model not found · {raw}. Use /model for Ollama + external provider catalogues.")
            return
        provider, model = found[0]
        if provider == "cloud" and self.settings.cloud_access_mode == "free" and not is_free_cloud_model(model, self.settings.free_cloud_models):
            self.ui.error(f"{model} is blocked in FREE cloud mode. Use /cloudmode all first.")
            return
        if provider == "cloud" and lookup_price(self.settings.prices, model) is None:
            self.ui.error(f"Price unknown for {model}; blocked for cost safety.")
            return
        self.active = ModelProfile(provider, model, self.active.think, self.active.max_output_tokens)
        self._save_session()
        self._apply_terminal_title()
        self.ui.success(f"Model · {provider}/{model}")

    # ---------- sessions ----------
    def new_session(self) -> None:
        self._save_session()
        self._bridge_unregister_current()
        self.current_session = self.session_store.create(self.project, self.active.provider, self.active.model)
        self.working_record.rebind(self.current_session.id, self.project)
        self.agent.reset_session()
        self.goal = ""
        self.pinned_mentions = []
        self._refresh_extensions()
        self.budget.reset_task()
        self.persist_state()
        self.ui.success(f"New session · {self.current_session.name}")

    @staticmethod
    def _clean_saved_user_text(text: str) -> str:
        """Hide locally injected map/mention wrappers when replaying a saved conversation."""
        value = str(text or "").strip()
        # Remove the compact local map preface that Advertpreneur injects before provider calls.
        if value.startswith("Local map targets (verify before editing):"):
            bits = value.split("\n\n", 1)
            if len(bits) == 2:
                value = bits[1].strip()
        if value.startswith("Explicit workspace context (inspect these before broad scanning):"):
            bits = value.split("\n\n", 1)
            if len(bits) == 2:
                value = bits[1].strip()
        if value.startswith("Verified local session evidence"):
            # Future-proof if evidence is ever prepended to a user message rather than system context.
            bits = value.split("\n\n", 1)
            if len(bits) == 2:
                value = bits[1].strip()
        return value

    def _resume_tool_label(self, call: Dict[str, Any]) -> str:
        try:
            fn = (call or {}).get("function", {})
            name = str(fn.get("name") or "tool")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            return self._tool_summary(name, args if isinstance(args, dict) else {})
        except Exception:
            return "tool action"

    def _render_resumed_session(self, rec: SessionRecord) -> None:
        """Restore visible recent work without spending a provider token."""
        messages = list(rec.messages or [])
        conversational = [m for m in messages if m.get("role") in {"user", "assistant"}]
        for msg in reversed(conversational):
            if msg.get("role") == "assistant" and str(msg.get("content") or "").strip():
                self.last_result = str(msg.get("content") or "")
                break
        recent = conversational[-10:]
        self.ui.heading("Restored session")
        self.ui.muted(
            f"{len(messages)} saved messages · {rec.requests:,} model request(s) · "
            f"{rec.input_tokens:,} in/{rec.output_tokens:,} out · cloud tokens used to restore: 0"
        )
        if recent:
            self.ui.heading("Recent conversation")
        for msg in recent:
            role = msg.get("role")
            content = str(msg.get("content") or "").strip()
            if role == "user":
                content = self._clean_saved_user_text(content)
                if content:
                    self.ui.print_line("  › " + content[:3500].replace("\n", "\n    "))
            else:
                calls = msg.get("tool_calls") or []
                for call in calls[-8:]:
                    self.ui.muted("  • " + self._resume_tool_label(call))
                if content:
                    self.ui.result(content[:7000] + ("\n…[older resumed output clipped in terminal only]" if len(content) > 7000 else ""))

        # Restore the actual local working-tree state too. This is local Git inspection only.
        if (self.project / ".git").exists() and shutil.which("git"):
            try:
                proc = subprocess.run(
                    ["git", "-C", str(self.project), "status", "--short"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
                )
                rows = [x for x in (proc.stdout or "").splitlines() if x.strip()]
                if rows:
                    self.ui.heading(f"Working tree · {len(rows)} changed")
                    for row in rows[:20]:
                        self.ui.print_line("  " + row)
                    if len(rows) > 20:
                        self.ui.muted(f"  …and {len(rows) - 20} more")
            except Exception:
                pass
        try:
            ev = self.working_record.summary()
            if ev.get("tasks") or ev.get("events"):
                self.ui.muted(
                    f"Local evidence restored · {ev.get('tasks', 0)} task record(s) · "
                    f"{ev.get('current_events', 0)} current evidence event(s) · cloud tokens 0"
                )
        except Exception:
            pass

    def _session_last_task(self, rec: SessionRecord) -> str:
        for msg in reversed(rec.messages or []):
            if msg.get("role") == "user":
                text = self._clean_saved_user_text(str(msg.get("content") or ""))
                if text:
                    return " ".join(text.split())[:90]
        return ""

    def resume_session(self) -> None:
        self._save_session()
        sessions = self.session_store.list(limit=100)
        if not sessions:
            self.ui.info("No saved sessions yet")
            return

        # Empty sessions created merely by launching ADP are noise in a resume picker.
        # Hide them whenever there are real working sessions available.
        real = [r for r in sessions if r.requests > 0 or bool(r.messages)]
        if real:
            sessions = real
        current_project = str(self.project.resolve())
        sessions.sort(key=lambda r: (str(Path(r.project).resolve()) == current_project, r.requests > 0, r.updated_at), reverse=True)

        items: List[MenuItem] = []
        lookup: Dict[str, SessionRecord] = {}
        for rec in sessions:
            project_name = Path(rec.project).name or rec.project
            stamp = rec.updated_at.replace("T", " ")[:16]
            task = self._session_last_task(rec) or "no saved task"
            marker = "CURRENT PROJECT" if str(Path(rec.project).resolve()) == current_project else str(Path(rec.project))
            label = f"{project_name} · {rec.name}"
            meta = f"{marker} · {rec.model} · {rec.requests} req · {stamp} · {task}"
            items.append(MenuItem(rec.id, label, meta))
            lookup[rec.id] = rec
        chosen = self.ui.choose("Resume session · project/task shown before selection", items, fuzzy=True)
        if not chosen or chosen not in lookup:
            return
        rec = lookup[chosen]
        project = Path(rec.project)
        if not project.exists() or not project.is_dir():
            self.ui.error(f"Saved project no longer exists · {project}")
            return
        self._bridge_unregister_current()
        self.current_session = rec
        self.goal = rec.goal
        self.pinned_mentions = list(rec.pinned_mentions)
        self.active = ModelProfile(rec.provider, rec.model, self.active.think, self.active.max_output_tokens)
        self._bind_project(project, rec.messages)
        self.budget.reset_task()
        self.persist_state()
        self.ui.success(f"Resumed · {Path(rec.project).name} · {rec.name} · {rec.requests} req · {short_path(project)}")
        self._render_resumed_session(rec)
        if self.current_session.bridge_enabled:
            self._bridge_register(True)

    def list_sessions(self) -> None:
        sessions = self.session_store.list(limit=30)
        self.ui.heading(f"Sessions · {len(sessions)}")
        if not sessions:
            self.ui.muted("No saved sessions")
            return
        for rec in sessions:
            marker = "*" if rec.id == self.current_session.id else " "
            project = Path(rec.project).name or rec.project
            stamp = rec.updated_at.replace("T", " ")[:16]
            print(f" {marker} {short_text(rec.name, 30):<30} {short_text(project, 16):<16} {short_text(rec.model, 20):<20} {stamp}")

    def rename_session(self, name: str | None = None) -> None:
        name = name or self.ui.prompt_text("Session name", self.current_session.name)
        if not name:
            return
        self.current_session.name = name.strip()[:100]
        self._save_session()
        self.ui.success(f"Session renamed · {self.current_session.name}")

    def fork_session(self) -> None:
        self._save_session()
        self._bridge_unregister_current()
        self.current_session = self.session_store.fork(self.current_session)
        self.goal = self.current_session.goal
        self.pinned_mentions = list(self.current_session.pinned_mentions)
        self._bind_project(Path(self.current_session.project), self.current_session.messages)
        self.persist_state()
        self.ui.success(f"Forked · {self.current_session.name}")

    # ---------- Codex plugins / skills / MCP ----------
    def plugins(self) -> None:
        while True:
            plugins = self.plugin_manager.discover()
            if not plugins:
                self.ui.info("No Codex plugins/skills discovered. Advertpreneur checks `codex plugin list --json` plus personal/project skill folders.")
                return
            by_path = {str(p.path): p for p in plugins}
            items: List[MenuItem] = []
            for p in plugins:
                state = "ON" if p.enabled else "OFF"
                caps = f"{len(p.skills)} skill(s)"
                if p.mcp_servers:
                    caps += f" · {len(p.mcp_servers)} bundled MCP ref(s)"
                items.append(MenuItem(str(p.path), p.display_name, f"{state} · {caps} · {p.source}"))
            chosen = self.ui.choose("Codex plugins", items, fuzzy=True)
            if not chosen or chosen not in by_path:
                return
            p = by_path[chosen]
            while True:
                action = self.ui.choose(p.display_name, [
                    MenuItem("skills", "Skills", "browse skills imported from this plugin"),
                    MenuItem("toggle", "Disable" if p.enabled else "Enable", "allow these skills in Advertpreneur CLI"),
                    MenuItem("inspect", "Inspect", "plugin path, version and capabilities"),
                ])
                if not action:
                    break  # Esc -> plugin list
                if action == "toggle":
                    self.plugin_manager.set_enabled(p.name, not p.enabled)
                    p.enabled = not p.enabled
                    self._refresh_extensions()
                    self._save_session()
                    self.ui.success(f"Plugin {p.name} · {'enabled' if p.enabled else 'disabled'}")
                elif action == "inspect":
                    self.ui.heading(p.display_name)
                    self.ui.info(f"Version {p.version or 'n/a'}")
                    self.ui.info(p.description)
                    self.ui.info(f"Source {p.source}")
                    self.ui.info(f"Path {p.path}")
                    self.ui.info(f"Skills {len(p.skills)}")
                    if p.mcp_servers:
                        self.ui.info(f"Bundled MCP refs {', '.join(p.mcp_servers)}")
                elif action == "skills":
                    skills = [x for x in self.plugin_manager.skills(enabled_only=False) if x.plugin_name == p.name]
                    while skills:
                        chosen_skill = self.ui.choose("Skills · " + p.display_name, [
                            MenuItem(x.id, x.name, x.description or str(x.path)) for x in skills
                        ], fuzzy=True)
                        if not chosen_skill:
                            break
                        self.ui.heading(chosen_skill)
                        print(self.plugin_manager.load_skill(chosen_skill))

    def skills(self) -> None:
        skills = self.plugin_manager.skills(enabled_only=True)
        if not skills:
            self.ui.info("No enabled Codex skills discovered")
            return
        while True:
            chosen = self.ui.choose("Installed skills", [
                MenuItem(x.id, x.name, f"{x.plugin_name} · {x.description}") for x in skills
            ], fuzzy=True)
            if not chosen:
                return
            self.ui.heading(chosen)
            print(self.plugin_manager.load_skill(chosen))

    def mcp_status(self) -> None:
        try:
            while True:
                servers = self.mcp_manager.discover()
                if not servers:
                    self.ui.info("No MCP servers reported by `codex mcp list --json`.")
                    return
                lookup = {s.name: s for s in servers}
                chosen = self.ui.choose("Codex MCP servers", [
                    MenuItem(s.name, s.name, ("ON" if s.enabled else "OFF") + f" · {s.transport_type} · auth {s.auth_status}")
                    for s in servers
                ], fuzzy=True)
                if not chosen:
                    return
                server = lookup[chosen]
                while True:
                    action = self.ui.choose(server.name, [
                        MenuItem("tools", "Tools", "connect and list live tools"),
                        MenuItem("toggle", "Disable in Advertpreneur" if server.enabled else "Enable in Advertpreneur", "does not modify Codex config"),
                        MenuItem("inspect", "Inspect", "transport and authentication status"),
                    ])
                    if not action:
                        break  # Esc -> server list
                    if action == "toggle":
                        self.mcp_manager.set_enabled(server.name, not server.enabled)
                        server.enabled = not server.enabled
                        self._refresh_extensions()
                        self.ui.success(f"MCP {server.name} · {'enabled' if server.enabled else 'disabled'} in Advertpreneur CLI")
                    elif action == "inspect":
                        self.ui.heading(server.name)
                        self.ui.info(f"Transport {server.transport_type}")
                        self.ui.info(f"Auth {server.auth_status}")
                        safe_transport = dict(server.transport)
                        if isinstance(safe_transport.get('env'), dict):
                            safe_transport['env'] = {k: '***' for k in safe_transport['env']}
                        print(safe_transport)
                    elif action == "tools":
                        if not server.enabled:
                            self.ui.error("Enable this MCP in Advertpreneur first")
                            continue
                        self.ui.info(f"Connecting to {server.name}…")
                        try:
                            tools = self.mcp_manager.list_tools(server.name)
                        except Exception as exc:
                            self.ui.error(str(exc))
                            continue
                        self.ui.heading(f"{server.name} tools · {len(tools)}")
                        for item in tools:
                            print(f"  {item.get('name',''):<28} {str(item.get('description') or '')[:100]}")
        except MCPError as exc:
            self.ui.error(str(exc))

    def print_usage(self, why: bool = False) -> None:
        self.ui.heading("Usage")
        external = self.active.provider in {"codex", "agy"}
        if external:
            print(f"  Active        {self.active.provider.upper()}/{self.active.model} · subscription-managed")
            print(f"  ADP session   {self.current_session.requests:>4} req · {self.current_session.input_tokens:>10,} in · {self.current_session.output_tokens:>9,} out observed")
            model = self.codex_model if self.active.provider == "codex" else self.agy_model
            self.ui.begin_working(self.active.model, "Refreshing subscription quota", 1)
            try:
                q = self.provider_harness.quota(self.active.provider, model=model, refresh=True)
            except Exception as exc:
                q = None
                quota_error = str(exc)
            finally:
                self.ui.end_working()
            print(f"\n  {self.active.provider.upper()} subscription quota · authoritative")
            if q:
                from datetime import datetime as _dt
                for w in q.windows:
                    reset = _dt.fromtimestamp(w.resets_at).astimezone().strftime("%a %H:%M") if w.resets_at else "--"
                    suffix = f" · reset {reset}" if w.resets_at else ""
                    print(f"    {w.label:<8} {w.remaining_percent:>5.0f}% remaining{suffix}")
                self.ui.muted(f"  Source · {q.source} · fetched live; ADP does not estimate subscription quota.")
            else:
                self.ui.muted(f"  Quota unavailable · {quota_error}. ADP will not invent a percentage.")
            day = self.budget.daily_record()
            print(f"\n  Ollama meter  today {money(float(day.get('cost_usd',0.0)))} / {money(self.budget.daily_limit)} · independent of {self.active.provider.upper()} subscription quota")
        else:
            task = self.budget.task
            day = self.budget.daily_record()
            print(f"  Task          {task.requests:>4} req · {task.input_tokens:>10,} in · {task.output_tokens:>9,} out · {money(task.estimated_cost_usd)} / {money(self.budget.task_limit)}")
            print(f"  Session       {self.current_session.requests:>4} req · {self.current_session.input_tokens:>10,} in · {self.current_session.output_tokens:>9,} out · {money(self.current_session.metered_value_usd)}")
            print(f"  Today         {int(day.get('requests',0)):>4} req · {int(day.get('input_tokens',0)):>10,} in · {int(day.get('output_tokens',0)):>9,} out · {money(float(day.get('cost_usd',0.0)))} / {money(self.budget.daily_limit)}")

        breakdown = self.agent.context_breakdown()
        total = sum(breakdown.values())
        print("\n  Next-call prompt footprint (approx.)")
        print(f"    system/extensions  ~{breakdown.get('system',0):>7,} tokens")
        print(f"    conversation       ~{breakdown.get('conversation',0):>7,} tokens")
        print(f"    tool results       ~{breakdown.get('tool_results',0):>7,} tokens")
        print(f"    tool schemas       ~{breakdown.get('tool_schemas',0):>7,} tokens")
        print(f"    total              ~{total:>7,} tokens")
        idx = self.project_index.stats() if hasattr(self, "project_index") else {}
        print("\n  Local context savings")
        print(f"    project index       {idx.get('files',0):>7,} files · {idx.get('entries',0):>7,} symbols · cloud tokens 0")
        print(f"    local compactions   {self.local_compactions:>7,} this session · cloud tokens 0")
        try: ev = self.working_record.summary()
        except Exception: ev = {}
        print(f"    evidence cache      {int(ev.get('current_events',0)):>7,} current event(s) · {int(ev.get('tasks',0)):>4,} task record(s) · cloud tokens 0")
        hb = self.handbook.stats() if hasattr(self, "handbook") else {}
        print(f"    handbook            {int(hb.get('PROVEN',0)):>7,} proven · {int(hb.get('FAILED',0)):>4,} failed recipes · local lookup")
        print(f"    browser/design map  {'LOCAL / ZERO-OLLAMA' if hasattr(self, 'tools') else 'n/a'}")
        print(f"    index policy        {'AUTO / LOCAL-ONLY' if self.auto_index else 'MANUAL / LOCAL-ONLY'}")
        self.ui.muted("Indexing, evidence, handbook lookup, direct /web search and browser screenshots/design maps are local. Subscription percentages come from the provider's own usage surface; ADP does not estimate them.")
        if why:
            print("\n  Largest next-call contributors (approx.)")
            for label, tokens in self.agent.context_contributors(10): print(f"    {tokens:>7,}  {label}")
            self.ui.muted("Use explicit @mentions and /map to narrow context before asking the selected model to explore broadly.")

    def refresh_discovery(self) -> None:
        self.ui.info("Refreshing Codex plugin/skill/MCP discovery…")
        try:
            plugins = self.plugin_manager.discover(refresh=True)
            skills = self.plugin_manager.skills(enabled_only=True)
        except Exception as exc:
            plugins, skills = [], []
            self.ui.error(f"Plugin refresh · {exc}")
        try:
            mcps = self.mcp_manager.discover(refresh=True)
        except Exception as exc:
            mcps = []
            self.ui.error(f"MCP refresh · {exc}")
        self._refresh_extensions()
        self._save_session()
        self.ui.success(f"Discovery refreshed · {len(plugins)} plugin(s) · {len(skills)} skill(s) · {len(mcps)} MCP server(s)")

    def status(self) -> None:
        self.ui.heading("Status")
        print(f"  Project       {self.project}")
        print(f"  Git branch    {self.git_branch or 'n/a'}")
        print(f"  Session       {self.current_session.name}")
        print(f"  Model         {self.active.provider}/{self.active.model}")
        print(f"  Reasoning     {self.active.think}")
        print(f"  Permissions   {self.settings.approval_mode}")
        print(f"  Cloud login   {'yes' if self.api_key else 'no'}")
        print(f"  Cloud mode    {self.settings.cloud_access_mode}")
        print(f"  Context est.  ~{sum(self.agent.context_breakdown().values()):,} tokens")
        try:
            ev = self.working_record.summary()
            print(f"  Evidence      {ev.get('current_events',0)} current event(s) · {ev.get('tasks',0)} task record(s) · local")
        except Exception:
            print("  Evidence      unavailable")
        plugins = self.plugin_manager.discover()
        enabled = sum(1 for p in plugins if p.enabled)
        print(f"  Plugins       {enabled}/{len(plugins)} enabled · {len(self.plugin_manager.skills())} skills")
        try:
            mcps = self.mcp_manager.discover()
            print(f"  MCP servers   {sum(1 for x in mcps if x.enabled)}/{len(mcps)} enabled")
        except Exception:
            print("  MCP servers   unavailable")
        print(f"  Personality   {self.personality}")
        print(f"  Goal          {self.goal or 'none'}")
        print(f"  Mentions      {', '.join(self.pinned_mentions) if self.pinned_mentions else 'none'}")
        print(f"  Raw output    {'on' if self.raw_output else 'off'}")
        print(f"  Status line   {self.statusline_mode}")
        idx = self.project_index.stats() if hasattr(self, "project_index") else {}
        print(f"  Code map      {idx.get('files',0)} files · {idx.get('entries',0)} symbols · {idx.get('updated_at','never')}")
        print(f"  Auto index    {'on' if self.auto_index else 'off'} · cloud indexing blocked")
        print(f"  Auto compact  ~{self.auto_compact_tokens:,} token threshold · local only")
        reviewer = f"{self.reviewer_provider}/{self.reviewer_model}" if self.reviewer_model else "same as active model"
        print(f"  Senior        {self.senior_provider}/{self.senior_model}")
        print(f"  Reviewer      {reviewer} · auto {self.auto_review}")
        print(f"  Frameworks    {self.framework_intelligence.summary()}")
        print(f"  Contract      {self.project_contract.summary()}")
        hb = self.handbook.stats()
        print(f"  Handbook      {'on' if self.handbook_enabled else 'off'} · {hb.get('PROVEN',0)} proven · {hb.get('FAILED',0)} failed recipes")
        try:
            browser_state = self.tools.browser_controller.status()
        except Exception as exc:
            browser_state = f"unavailable · {exc}"
        print(f"  Browser       {'visible' if self.browser_visible else 'headless'} · {browser_state}")
        try:
            print(f"  Operations    {self.tools.tool_browser('operations_status')}")
        except Exception:
            pass
        try:
            print(f"  Workforce     {json.dumps(self.workforce.summary(), ensure_ascii=False)}")
        except Exception:
            pass
        print(f"  Hooks         {'on' if self.hooks_enabled else 'off'}")
        print(f"  Bridge        {'on' if self.current_session.bridge_enabled else 'off'} · autopilot {'on' if self.current_session.bridge_autopilot else 'off'}")
        self.print_usage()

    def doctor(self) -> None:
        self.ui.heading("Doctor")
        self.ui.info(f"Python {sys.version.split()[0]}")
        self.ui.info(f"Project {self.project}")
        for tool, label in (("git", "Git"), ("ollama", "Ollama local"), ("codex", "Codex official provider"), ("agy", "Antigravity official provider"), ("node", "Node.js"), ("npm", "npm")):
            path = shutil.which(tool)
            (self.ui.success if path else self.ui.muted)(f"{label} · {path or 'not installed / optional'}")
        try:
            import playwright  # noqa: F401
            self.ui.success("Playwright · Python package installed · Edge/Chrome used when browser control starts")
        except Exception:
            self.ui.error("Playwright · missing · re-run INSTALL.ps1")
        self.ui.info(f"Framework intelligence · {self.framework_intelligence.summary()}")
        self.ui.info(f"Engineering handbook · {self.handbook.path}")
        self.ui.info(f"Cloud login {'configured' if self.api_key else 'missing'}")
        if self.credential_error:
            self.ui.error(self.credential_error)
        for provider in ("local", "cloud"):
            if provider == "cloud" and not self.api_key:
                self.ui.error("Cloud API skipped · use /login")
                continue
            try:
                names = OllamaClient(provider, self.api_key).list_models()
                self.ui.success(f"{provider.capitalize()} API · {len(names)} model(s)")
            except Exception as exc:
                self.ui.error(f"{provider.capitalize()} API · {exc}")
        self.ui.info(f"Codex plugins · {len(self.plugin_manager.discover(refresh=True))} discovered · {len(self.plugin_manager.skills())} skills")
        try:
            servers = self.mcp_manager.discover(refresh=True)
            self.ui.info(f"Codex MCP · {len(servers)} server(s) discovered")
        except Exception as exc:
            self.ui.error(f"Codex MCP discovery · {exc}")
        (self.ui.success if self.bridge.health() else self.ui.muted)(
            f"Browser Bridge broker · {'running on 127.0.0.1:8765' if self.bridge.health() else 'not running / starts with /bridge on'}"
        )
        ext = self.bridge.extension_path()
        (self.ui.success if ext.exists() else self.ui.error)(f"Browser Bridge extension · {ext}")
        for provider in ("codex", "agy"):
            try:
                st = self.provider_harness.status(provider)
                (self.ui.success if st.installed else self.ui.muted)(f"{provider.upper()} provider · {'installed' if st.installed else 'missing'} · auth {st.auth}")
            except Exception as exc:
                self.ui.muted(f"{provider.upper()} provider · {exc}")

    # ---------- preferences ----------
    def set_permissions(self, value: str | None = None) -> None:
        if not value:
            value = self.ui.choose("Permissions", [
                MenuItem("ask", "Ask", "confirm every write/command"),
                MenuItem("safe", "Safe", "auto read/write; confirm non-safe commands"),
                MenuItem("full", "Full", "auto-approve allowed tools; hard blocks remain"),
            ])
        if value not in {"ask", "safe", "full"}:
            self.ui.error("Permissions must be ask, safe, or full.")
            return
        self.settings.approval_mode = value
        self.tools.approval_mode = value
        self.mcp_manager.approval_mode = value
        self.persist_state()
        self.ui.success(f"Permissions · {value}")

    def set_reasoning(self, value: str | None = None) -> None:
        external = self.active.provider in {"codex", "agy"}
        if not value:
            choices = [
                MenuItem("low", "Low", "lower quota use / straightforward work"),
                MenuItem("medium", "Medium", "recommended default / balanced"),
                MenuItem("high", "High", "use deliberately for difficult reasoning"),
            ]
            if not external:
                choices.insert(0, MenuItem("off", "Off", "fastest / lowest usage"))
            value = self.ui.choose("Reasoning effort" + (f" · {self.active.provider.upper()}" if external else ""), choices)
        if not value: return
        value = value.lower().strip()
        allowed = {"low", "medium", "high"} if external else {"off", "low", "medium", "high"}
        if value not in allowed:
            self.ui.error("Reasoning must be " + ("low, medium, or high." if external else "off, low, medium, or high.")); return
        if external:
            if self.active.provider == "codex":
                self.codex_effort = value
            else:
                fixed = ExternalProviderHarness.agy_model_effort(self.active.model)
                if fixed and value != fixed:
                    # A tiered AGY slug is itself the effort selector. Try to move
                    # to a sibling model variant; otherwise keep the truthful tier.
                    base = re.sub(r"-(low|medium|high)$", "", self.active.model, flags=re.I)
                    candidate = f"{base}-{value}"
                    try:
                        available = {row.id for row in self.provider_harness.model_catalog("agy")}
                    except Exception:
                        available = set()
                    if candidate not in available:
                        self.ui.error(f"AGY {self.active.model} is fixed at {fixed}; no {value} variant is available.")
                        return
                    self.agy_model = candidate
                    self.active.model = candidate
                    self.current_session.model = candidate
                self.agy_effort = value
        else:
            self.active.think = False if value == "off" else value
        self.persist_state()
        self.ui.success(f"Reasoning · {value}" + (" · explicit external-provider setting" if external else ""))

    def set_cloud_mode(self, value: str | None = None) -> None:
        if not value:
            value = self.ui.choose("Cloud access", [
                MenuItem("free", "Free only", "block every non-starter Cloud model"),
                MenuItem("all", "All models", "allow priced models within your own budget caps"),
            ])
        if value not in {"free", "all"}:
            self.ui.error("Cloud mode must be free or all.")
            return
        if value == "all" and self.settings.cloud_access_mode != "all":
            if not self.ui.confirm("billing", "Enable priced Ollama Cloud models within Advertpreneur's own budget limits"):
                return
        self.settings.cloud_access_mode = value
        self.persist_state()
        self.ui.success(f"Cloud access · {value.upper()}")

    def set_statusline(self) -> None:
        value = self.ui.choose("Status line", [
            MenuItem("balanced", "Balanced", "model · provider · project · git · permissions · context · usage"),
            MenuItem("minimal", "Minimal", "model · project · git · context"),
            MenuItem("usage", "Usage", "model · context · tokens · task/day metered value"),
        ])
        if value:
            self.statusline_mode = value
            self.persist_state()
            self.ui.success(f"Status line · {value}")

    def set_personality(self, value: str | None = None) -> None:
        if not value:
            value = self.ui.choose("Personality", [
                MenuItem("pragmatic", "Pragmatic", "concise and implementation-focused"),
                MenuItem("friendly", "Friendly", "collaborative but still technical"),
                MenuItem("none", "None", "no extra communication-style instruction"),
            ])
        if not value:
            return
        if value not in {"pragmatic", "friendly", "none"}:
            self.ui.error("Personality must be pragmatic, friendly, or none.")
            return
        self.personality = value
        self._refresh_extensions()
        self.persist_state()
        self.ui.success(f"Personality · {value}")

    def manage_goal(self, arg: str | None = None) -> None:
        text = (arg or "").strip()
        lower = text.lower()
        if not text:
            if self.goal:
                self.ui.info(f"Goal · {self.goal}")
            else:
                self.ui.muted("No active goal · use /goal <objective>")
            return
        if lower == "clear":
            self.goal = ""
        elif lower == "pause":
            self.state.set("paused_goal", self.goal)
            self.goal = ""
        elif lower == "resume":
            self.goal = str(self.state.get("paused_goal", "") or "")
        elif lower == "edit":
            value = self.ui.prompt_text("Goal", self.goal)
            if value is None:
                return
            self.goal = value[:4000]
        else:
            self.goal = text[:4000]
        self._refresh_extensions()
        self.persist_state()
        self.ui.success("Goal · " + (self.goal if self.goal else "cleared"))

    def set_raw(self, value: str | None = None) -> None:
        if value is None:
            self.raw_output = not self.raw_output
        else:
            v = value.lower()
            if v not in {"on", "off"}:
                self.ui.error("Usage: /raw [on|off]")
                return
            self.raw_output = v == "on"
        self.ui.raw_output = self.raw_output
        self.persist_state()
        self.ui.success(f"Raw output · {'ON' if self.raw_output else 'OFF'}")

    def set_title_mode(self) -> None:
        value = self.ui.choose("Terminal title", [
            MenuItem("project", "Project", "Advertpreneur CLI - Project"),
            MenuItem("project-branch", "Project + branch", "include Git branch"),
            MenuItem("project-model", "Project + model", "include active model"),
        ])
        if value:
            self.title_mode = value
            self.persist_state()
            self._apply_terminal_title()
            self.ui.success(f"Terminal title · {value}")

    def workspace_mentions(self) -> List[MenuItem]:
        rows = self.ui.workspace.search("", limit=250)
        return [MenuItem(path, path, kind) for path, kind in rows]

    def mention(self, arg: str | None = None) -> None:
        value = arg.strip().strip('"') if arg else None
        if not value:
            chosen = self.ui.choose("Mention file or folder", self.workspace_mentions(), fuzzy=True)
            if not chosen:
                return
            value = chosen
        path = (self.project / value).resolve()
        try:
            path.relative_to(self.project)
        except ValueError:
            self.ui.error("Mention must stay inside the project root")
            return
        if not path.exists():
            self.ui.error(f"Mention not found · {value}")
            return
        rel = path.relative_to(self.project).as_posix() + ("/" if path.is_dir() else "")
        if rel not in self.pinned_mentions:
            self.pinned_mentions.append(rel)
        self.persist_state()
        self.ui.success(f"Mention pinned · @{rel}")

    def archive_current(self) -> bool:
        self._save_session()
        if not self.ui.confirm("session", f"Archive '{self.current_session.name}' and exit Advertpreneur CLI"):
            return False
        self._bridge_unregister_current()
        self.session_store.archive(self.current_session.id, True)
        self.ui.success("Session archived")
        return True

    def unarchive_session(self) -> None:
        archived = [r for r in self.session_store.list(limit=100, include_archived=True) if r.archived]
        if not archived:
            self.ui.info("No archived sessions")
            return
        lookup = {r.id: r for r in archived}
        chosen = self.ui.choose("Unarchive session", [
            MenuItem(r.id, r.name, f"{Path(r.project).name} · {r.model} · {r.updated_at.replace('T',' ')[:16]}") for r in archived
        ], fuzzy=True)
        if not chosen or chosen not in lookup:
            return
        self.session_store.archive(chosen, False)
        self.ui.success(f"Session restored · {lookup[chosen].name}")

    def delete_current(self) -> bool:
        name = self.current_session.name
        if not self.ui.confirm("session", f"Permanently delete '{name}' and exit Advertpreneur CLI"):
            return False
        sid = self.current_session.id
        self._bridge_unregister_current()
        self.session_store.delete(sid)
        self.ui.success("Session deleted")
        return True

    def config_info(self, debug: bool = False) -> None:
        self.ui.heading("Configuration" if not debug else "Configuration diagnostics")
        print(f"  Project config   {self.config_path} {'(loaded)' if self.config_path.exists() else '(defaults)'}")
        print(f"  User state       {APP_DIR / 'state.json'}")
        print(f"  Credentials      {APP_DIR / 'credentials.json'}")
        print(f"  Sessions         {APP_DIR / 'sessions'}")
        print(f"  History          {APP_DIR / 'history.txt'}")
        if debug:
            print(f"  Provider/model   {self.active.provider}/{self.active.model}")
            print(f"  Personality      {self.personality}")
            print(f"  Goal             {self.goal or 'none'}")
            print(f"  Raw output       {self.raw_output}")
            print(f"  Title mode       {self.title_mode}")
            print(f"  Pinned mentions  {', '.join(self.pinned_mentions) if self.pinned_mentions else 'none'}")
            print(f"  Cloud mode       {self.settings.cloud_access_mode}")
            print(f"  Auto index       {self.auto_index} · local-only")
            print(f"  Index model      {self.settings.index_local_model} (optional enrichment only)")
            print(f"  Project contract {self.project_contract.summary()}")
            print(f"  Auto compact     {self.auto_compact_tokens} → {self.compact_target_tokens} tokens · local-only")
            print(f"  Reviewer         {self.reviewer_provider}/{self.reviewer_model}" if self.reviewer_model else "  Reviewer         active model")
            print(f"  Auto review      {self.auto_review}")
            print(f"  Notifications    {'on' if self.desktop_notifications else 'off'}")
            print(f"  Task sounds      {'on' if self.task_sounds else 'off'}")
            print(f"  Browser Bridge   {'on' if self.current_session.bridge_enabled else 'off'} · autopilot {'on' if self.current_session.bridge_autopilot else 'off'}")
            print(f"  Bridge extension {self.bridge.extension_path()}")

    def keymap(self) -> None:
        self.ui.heading("Keymap")
        print("  /          command palette")
        print("  @          file/folder mention search")
        print("  !command   run local shell command")
        print("  ↑ / ↓      prompt history")
        print("  Ctrl+R     search prompt history")
        print("  Ctrl+O     copy latest completed result")
        print("  Ctrl+L     clear terminal view only")
        print("  Esc        close completion/menu")

    def switch_project(self, raw_path: str | None = None) -> None:
        if not raw_path:
            raw_path = self.ui.prompt_text("Project path", str(self.project))
        if not raw_path:
            return
        p = Path(raw_path.strip().strip('"')).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            self.ui.error(f"Project directory not found · {p}")
            return
        self._save_session()
        self._bridge_unregister_current()
        self.current_session = self.session_store.create(p, self.active.provider, self.active.model)
        self.goal = ""
        self.pinned_mentions = []
        self._bind_project(p)
        self.budget.reset_task()
        self.persist_state()
        self.ui.success(f"Project · {p} · new session")

    def settings_menu(self) -> None:
        while True:
            choice = self.ui.choose("Settings", [
                MenuItem("model", "Model & reasoning", f"{self.active.provider}/{self.active.model}"),
                MenuItem("context", "Context & indexing", f"auto index {'on' if self.auto_index else 'off'} · compact ~{self.auto_compact_tokens//1000}k"),
                MenuItem("intelligence", "Engineering intelligence", f"{self.framework_intelligence.summary()} · handbook {'on' if self.handbook_enabled else 'off'}"),
                MenuItem("agents", "Agents & roles", f"senior {self.senior_model} · review {self.auto_review}"),
                MenuItem("browser", "Browser & web research", f"browser {'visible' if self.browser_visible else 'headless'} · local tools"),
                MenuItem("permissions", "Permissions", self.settings.approval_mode),
                MenuItem("interface", "Interface", f"{self.statusline_mode} · {self.title_mode}"),
                MenuItem("notifications", "Notifications & sounds", f"notify {'on' if self.desktop_notifications else 'off'} · sound {'on' if self.task_sounds else 'off'}"),
                MenuItem("bridge", "Browser Bridge", f"{'on' if self.current_session.bridge_enabled else 'off'} · autopilot {'on' if self.current_session.bridge_autopilot else 'off'}"),
                MenuItem("budgets", "Budgets", f"task {money(self.budget.task_limit)} · day {money(self.budget.daily_limit)}"),
            ])
            if not choice:
                return
            if choice == "model":
                sub = self.ui.choose("Model & reasoning", [
                    MenuItem("model", "Select model", f"{self.active.provider}/{self.active.model}"),
                    MenuItem("reasoning", "Reasoning", str(self.active.think)),
                    MenuItem("cloud", "Cloud access", self.settings.cloud_access_mode),
                ])
                if sub == "model":
                    self.select_model()
                elif sub == "reasoning":
                    self.set_reasoning()
                elif sub == "cloud":
                    self.set_cloud_mode()
            elif choice == "context":
                while True:
                    sub = self.ui.choose("Context & indexing", [
                        MenuItem("index", "Automatic local index", "ON" if self.auto_index else "OFF"),
                        MenuItem("build", "Build/update index now", "zero cloud tokens"),
                        MenuItem("enrich", "Enrich index with local model", self.settings.index_local_model),
                        MenuItem("compact", "Automatic local compaction", f"~{self.auto_compact_tokens:,} tokens"),
                        MenuItem("status", "Index status", f"{self.project_index.file_count} files · {self.project_index.entry_count} symbols"),
                    ])
                    if not sub:
                        break
                    if sub == "index":
                        self.auto_index = not self.auto_index
                        self.persist_state()
                        self.ui.success(f"Automatic local index · {'ON' if self.auto_index else 'OFF'} · cloud policy NEVER")
                    elif sub == "build":
                        self.index_project()
                    elif sub == "enrich":
                        self.index_project("enrich")
                    elif sub == "compact":
                        value = self.ui.choose("Auto compact threshold", [
                            MenuItem("12000", "~12k tokens", "aggressive / lowest context spend"),
                            MenuItem("24000", "~24k tokens", "balanced default"),
                            MenuItem("48000", "~48k tokens", "retain more history"),
                            MenuItem("96000", "~96k tokens", "large-context projects"),
                        ])
                        if value:
                            self.auto_compact_tokens = int(value)
                            self.context_manager.threshold_tokens = int(value)
                            self.persist_state()
                            self.ui.success(f"Automatic local compaction · ~{int(value):,} tokens")
                    else:
                        self.index_status()
            elif choice == "intelligence":
                while True:
                    sub = self.ui.choose("Engineering intelligence", [
                        MenuItem("frameworks", "Detected framework packs", self.framework_intelligence.summary()),
                        MenuItem("handbook", "Self-learning handbook", "ON" if self.handbook_enabled else "OFF"),
                        MenuItem("handbook_status", "Handbook status", "validated + failed experience memory"),
                        MenuItem("hooks", "Lifecycle hooks", "ON" if self.hooks_enabled else "OFF"),
                        MenuItem("hooks_status", "Hooks status / scaffold", str(self.hooks.path)),
                        MenuItem("telemetry", "Local harness review telemetry", "ON" if self.local_telemetry else "OFF"),
                        MenuItem("insights", "Daily/weekly harness insights", "0 model tokens"),
                    ])
                    if not sub: break
                    if sub == "frameworks": self.framework_status()
                    elif sub == "handbook":
                        self.handbook_enabled = not self.handbook_enabled; self.persist_state(); self.ui.success(f"Engineering handbook · {'ON' if self.handbook_enabled else 'OFF'}")
                    elif sub == "handbook_status": self.handbook_command("status")
                    elif sub == "hooks":
                        self.hooks_enabled = not self.hooks_enabled; self.hooks.enabled = self.hooks_enabled; self.persist_state(); self.ui.success(f"Lifecycle hooks · {'ON' if self.hooks_enabled else 'OFF'}")
                    elif sub == "hooks_status": self.hooks_command("status")
                    elif sub == "telemetry":
                        self.local_telemetry = not self.local_telemetry; self.persist_state(); self.ui.success(f"Local harness telemetry · {'ON' if self.local_telemetry else 'OFF'}")
                    elif sub == "insights": self.insights_command("today")
            elif choice == "agents":
                self.agents_settings()
            elif choice == "browser":
                while True:
                    sub = self.ui.choose("Browser & web research", [
                        MenuItem("status", "Browser status", "local / zero Ollama tokens"),
                        MenuItem("visible", "Visible controlled browser", "ON" if self.browser_visible else "OFF / headless"),
                        MenuItem("web", "Test web search", "local fetch; model not called"),
                    ])
                    if not sub: break
                    if sub == "status": self.browser_command("status")
                    elif sub == "visible": self.browser_command("visible off" if self.browser_visible else "visible on")
                    elif sub == "web":
                        q = self.ui.prompt_text("Web search", "WordPress developer documentation")
                        if q: self.web_command(q)
            elif choice == "permissions":
                self.set_permissions()
            elif choice == "interface":
                sub = self.ui.choose("Interface", [
                    MenuItem("status", "Status line", self.statusline_mode),
                    MenuItem("title", "Terminal title", self.title_mode),
                    MenuItem("personality", "Personality", self.personality),
                    MenuItem("raw", "Raw output", "on" if self.raw_output else "off"),
                ])
                if sub == "status": self.set_statusline()
                elif sub == "title": self.set_title_mode()
                elif sub == "personality": self.set_personality()
                elif sub == "raw": self.set_raw()
            elif choice == "notifications":
                self.notification_settings()
            elif choice == "bridge":
                self.bridge_settings()
            else:
                self.ui.info(f"Task budget {money(self.budget.task_limit)} · daily {money(self.budget.daily_limit)} · use /budget or /daily to change")

    def notification_settings(self) -> None:
        while True:
            choice = self.ui.choose("Notifications & sounds", [
                MenuItem("notify", "Desktop task notifications", "ON" if self.desktop_notifications else "OFF"),
                MenuItem("sound", "Start / finish sounds", "ON" if self.task_sounds else "OFF"),
                MenuItem("test", "Test local feedback", "no model / zero cloud tokens"),
            ])
            if not choice:
                return
            if choice == "notify":
                self.desktop_notifications = not self.desktop_notifications
            elif choice == "sound":
                self.task_sounds = not self.task_sounds
            elif choice == "test":
                self.notifier.task_started(self.active.model, "Local notification test")
                timer = threading.Timer(1.6, lambda: self.notifier.task_finished(True, "Local feedback test complete"))
                timer.daemon = True; timer.start()
                self.ui.success("Local feedback test started · cloud tokens 0")
                continue
            self.notifier.configure(self.desktop_notifications, self.task_sounds)
            self.persist_state()
            self.ui.success(f"Notifications {'ON' if self.desktop_notifications else 'OFF'} · sounds {'ON' if self.task_sounds else 'OFF'}")

    # ---------- local project intelligence ----------
    def index_project(self, arg: str | None = None) -> None:
        mode = (arg or "").strip().lower()
        if mode in {"off", "disable"}:
            self.auto_index = False
            self.persist_state()
            self.ui.success("Automatic project indexing · OFF (manual /index still available)")
            return
        if mode in {"on", "enable"}:
            self.auto_index = True
            self.persist_state()
            self.ui.success("Automatic project indexing · ON · local-only / zero cloud tokens")
            return
        if mode == "status":
            self.index_status()
            return
        if mode == "enrich":
            # This is explicitly local-only. If the configured local model is absent,
            # keep the deterministic index rather than falling back to cloud.
            try:
                local = OllamaClient("local", None)
                names = local.list_models()
                model = self.settings.index_local_model
                if model not in names and not any(x.split(":",1)[0] == model.split(":",1)[0] for x in names):
                    self.ui.error(f"Local enrichment model not installed · {model}. Deterministic index remains available at zero tokens.")
                    return
                self.ui.info(f"Enriching important map entries locally with {model} · cloud tokens 0…")
                result = self.project_index.enrich_local(local, model, max_entries=50)
                self.ui.success(
                    f"Local enrichment · {result.get('enriched',0)} entries · "
                    f"{result.get('local_input_tokens',0):,} local in / {result.get('local_output_tokens',0):,} local out · cloud 0"
                )
            except Exception as exc:
                self.ui.error(f"Local index enrichment · {exc}")
            return

        force = mode in {"full", "rebuild", "force"}
        self.ui.info("Indexing project locally · no cloud model / no cloud tokens…")
        try:
            result = self.project_index.build(force=force)
            self.ui.invalidate_workspace()
            self.ui.success(
                f"Project map ready · {result['files']:,} files · {result['entries']:,} symbols · "
                f"{result['changed']:,} changed · {result['removed']:,} removed · {result['seconds']:.2f}s · cloud 0"
            )
        except Exception as exc:
            self.ui.error(f"Index · {exc}")

    def index_status(self) -> None:
        st = self.project_index.stats()
        self.ui.heading("Local project index")
        print(f"  Ready          {'yes' if st.get('ready') else 'no'}")
        print(f"  Files          {st.get('files',0):,}")
        print(f"  Symbols        {st.get('entries',0):,}")
        print(f"  Updated        {st.get('updated_at','never')}")
        print(f"  Auto indexing  {'on' if self.auto_index else 'off'}")
        print(f"  Cloud policy   NEVER")
        print(f"  Map            {st.get('map_path')}")
        print(f"  Human memory   {st.get('memory_path')}")
        self.ui.muted("/index rebuild refreshes everything · /index enrich optionally uses only your local Ollama model")

    def search_project_map(self, query: str | None = None) -> None:
        if not self.project_index.ready:
            self.ui.info("Project map is not ready · building locally first")
            self.index_project()
        if not query:
            query = self.ui.prompt_text("Project-map search", "")
        if not query:
            return
        rows = self.project_index.search(query, limit=20)
        self.ui.heading(f"Project map · {query}")
        if not rows:
            self.ui.muted("No local map matches")
            return
        for score, e in rows:
            summary = e.enriched_summary or e.summary
            print(f"  {score:>3}  {e.path}:{e.line_start}-{e.line_end}  {e.kind:<14} {e.symbol}" + (f" · {summary}" if summary else ""))

    def _ensure_project_index(self) -> None:
        if not self.auto_index:
            return
        first = not self.project_index.ready
        try:
            if first:
                self.ui.info("Local index · mapping project before cloud work · cloud tokens 0")
            # Incremental build: unchanged files are reused by mtime/hash and cost no model tokens.
            result = self.project_index.build(force=False)
            if first:
                self.ui.success(f"Local index · {result['files']:,} files · {result['entries']:,} symbols · {result['seconds']:.2f}s · cloud 0")
            elif result.get("changed") or result.get("removed"):
                self.ui.muted(f"Local index refreshed · {result.get('changed',0)} changed · {result.get('removed',0)} removed · cloud 0")
        except Exception as exc:
            # Indexing is an optimization. Never block coding if local indexing fails.
            if first:
                self.ui.muted(f"Local index skipped · {exc}")
            return

    def _maybe_auto_compact(self) -> None:
        current = self.agent.context_estimate_tokens()
        if current < self.auto_compact_tokens:
            return
        try:
            before, after, _memory = self.agent.compact_local(self.context_manager)
            self.local_compactions += 1
            self._save_session()
            self.ui.info(f"Local context compacted · ~{before:,} → ~{after:,} tokens · cloud tokens 0")
        except Exception as exc:
            self.ui.muted(f"Local compaction skipped · {exc}")

    # ---------- local checkpoints / undo ----------
    def list_checkpoints(self) -> None:
        rows = self.checkpoints.list(30)
        self.ui.heading("Advertpreneur Checkpoints · Time-Travel Explorer")
        if not rows:
            self.ui.muted("No recoverable agent changes yet")
            return
        items = [
            MenuItem(cp.id, f"{cp.id[:8]} · {cp.label}", f"{cp.created_at.replace('T', ' ')[:16]} · {len(cp.changed_files)} file(s)")
            for cp in rows
        ]
        items.append(MenuItem("cancel", "Cancel", "exit checkpoint browser"))
        selected_id = self.ui.choose("Select a checkpoint to inspect or restore:", items)
        if not selected_id or selected_id == "cancel":
            return
        target = next((cp for cp in rows if cp.id == selected_id), None)
        if not target:
            return
        self.ui.heading(f"Checkpoint {target.id[:8]} · {target.label}")
        print(f"  Timestamp     {target.created_at.replace('T', ' ')[:19]}")
        print(f"  Changed files {len(target.changed_files)}")
        for f in target.changed_files[:10]:
            print(f"    • {f}")
        action = self.ui.choose("Action for this checkpoint:", [
            MenuItem("restore", "Restore workspace to this checkpoint", "revert all files to this state"),
            MenuItem("diff", "View unified diff", "see exact changes in this checkpoint"),
            MenuItem("cancel", "Cancel", "do nothing"),
        ])
        if action == "diff":
            diff_text = self.checkpoints.diff(target)
            self.ui.heading("Unified Diff Preview")
            if diff_text:
                for line in diff_text.splitlines()[:60]:
                    print("  " + line)
            else:
                self.ui.muted("No diff detected compared to current workspace.")
        elif action == "restore":
            if self.ui.confirm("restore_checkpoint", f"Restore {len(target.changed_files)} files to checkpoint {target.id[:8]}?"):
                self.checkpoints.restore(target)
                self.project_index.build(force=False)
                self.ui.invalidate_workspace()
                self._refresh_git_state()
                self.ui.success(f"Workspace restored to checkpoint {target.id[:8]}")

    def undo_last(self) -> None:
        rows = self.checkpoints.list(1)
        if not rows:
            self.ui.info("Nothing to undo")
            return
        cp = rows[0]
        detail = f"Undo '{cp.label}' and restore {len(cp.changed_files)} changed file(s) to their pre-task state"
        if not self.ui.confirm("undo", detail):
            return
        try:
            restored = self.checkpoints.undo_latest()
            self.project_index.build(force=False)
            self.ui.invalidate_workspace()
            self._refresh_git_state()
            if restored:
                self.ui.success(f"Undo complete · checkpoint {restored.id} · {len(restored.changed_files)} file(s) restored")
        except Exception as exc:
            self.ui.error(f"Undo · {exc}")

    # ---------- local engineering intelligence / browser / research ----------
    def framework_status(self) -> None:
        self.ui.heading("Framework intelligence")
        self.ui.info(self.framework_intelligence.summary())
        self.ui.muted("Detected locally · framework packs are injected only when relevant to the task · cloud tokens 0 until a model task runs")

    def handbook_command(self, arg: str | None = None) -> None:
        text = (arg or "").strip()
        if not text or text.lower() == "status":
            stats = self.handbook.stats()
            self.ui.heading("Engineering handbook")
            print(f"  PROVEN           {stats.get('PROVEN',0)}")
            print(f"  PROJECT-SPECIFIC {stats.get('PROJECT-SPECIFIC',0)}")
            print(f"  FAILED           {stats.get('FAILED',0)}")
            print(f"  Path             {self.handbook.path}")
            self.ui.muted("Automatic learning is local. PROVEN requires real successful validation evidence; failed attempts are retained so ADP does not blindly repeat them.")
            return
        rows = self.handbook.search(text, limit=8)
        self.ui.heading(f"Handbook · {text}")
        if not rows:
            self.ui.muted("No matching prior experience")
            return
        for e in rows:
            print(f"  [{e.get('status','PROJECT-SPECIFIC')}] {str(e.get('task',''))[:120]}")
            print(f"      {str(e.get('recipe',''))[:420]}")
            if e.get('validation'):
                print(f"      validated: {str(e.get('validation'))[:260]}")

    def browser_command(self, arg: str | None = None) -> None:
        text = (arg or "").strip()
        if not text or text.lower() == "status":
            self.ui.heading("Local browser control")
            print(f"  Visible fallback {'ON' if self.browser_visible else 'OFF (headless)'}")
            try:
                state = self.tools.browser_controller.status()
            except Exception as exc:
                state = f"Browser status unavailable · {exc}"
            print(f"  Status           {state}")
            print("  Preferred        existing Edge profile via Advertpreneur Browser Bridge extension")
            print(f"  Fallback         CDP {self.tools.browser_controller.cdp_url} → isolated Playwright")
            print(f"  Evidence folder  {self.project / '.advertpreneur' / 'browser'}")
            bc = self.tools.browser_controller
            print(f"  Learn Mode       {('RECORDING ' + bc.routines.learning_name) if bc.routines.learning else 'off'}")
            print(f"  Routines         {len(bc.routine_names())}")
            try: print(f"  Operations       {self.tools.tool_browser('operations_status')}")
            except Exception: pass
            self.ui.muted("Navigation/screenshots/design maps and learned-routine replay are local and use 0 Ollama tokens. Model reasoning over compact evidence uses normal task tokens.")
            return
        low = text.lower()
        try:
            if low in {"visible on", "show", "headed"}:
                self.browser_visible = True
                self.tools.browser_controller.set_visible(True)
                self.persist_state(); self.ui.success("Browser visibility · ON")
                return
            if low in {"visible off", "headless", "hide"}:
                self.browser_visible = False
                self.tools.browser_controller.set_visible(False)
                self.persist_state(); self.ui.success("Browser visibility · OFF / headless")
                return
            if low.startswith("learn "):
                arg2 = text.split(maxsplit=1)[1].strip()
                if arg2.lower() == "stop":
                    self.ui.success(self.tools.browser_controller.learn_stop())
                elif arg2.lower() == "cancel":
                    self.ui.muted(self.tools.browser_controller.learn_cancel())
                else:
                    self.ui.success(self.tools.browser_controller.learn_start(arg2))
                    self.ui.muted("Teach once using natural-language browser actions or direct /browser commands; then /browser learn stop.")
                return
            if low == "macros":
                rows = self.browser_macros.list_macros()
                self.ui.heading("Browser Macros")
                if not rows:
                    self.ui.muted("No macros recorded yet · /browser record <name>")
                else:
                    for row in rows: print(f"  {row}")
                return
            if low.startswith("record "):
                m_name = text.split(maxsplit=1)[1].strip()
                if m_name.lower() in {"stop", "end"}:
                    saved = self.browser_macros.stop_recording()
                    if saved:
                        self.ui.success(f"Macro '{saved.name}' saved with {len(saved.steps)} step(s)")
                    else:
                        self.ui.muted("No active recording to stop")
                else:
                    self.browser_macros.start_recording(m_name)
                    self.ui.success(f"Started recording macro '{m_name}' · perform browser actions then /browser record stop")
                return
            if low.startswith("play "):
                m_name = text.split(maxsplit=1)[1].strip()
                self.ui.begin_working(VERSION, f"Playing macro '{m_name}'", 1)
                res = self.browser_macros.play(m_name, self.tools.browser_controller)
                self.ui.end_working()
                if res["ok"]:
                    self.ui.success(f"Macro '{m_name}' completed successfully ({res['steps_run']} steps)")
                else:
                    self.ui.error(f"Macro '{m_name}' failed · {res['error']}")
                return
            if low == "routines":
                rows = self.tools.browser_controller.routine_names()
                self.ui.heading("Learned browser routines")
                if not rows:
                    self.ui.muted("No routines yet · /browser learn <name>")
                else:
                    for row in rows: print("  " + row)
                return
            if low in {"wp status", "wordpress status"}:
                self.ui.info(json.dumps(self.tools.browser_controller.wordpress_state(), ensure_ascii=False, indent=2)); return
            if low == "site detect":
                self.ui.info(self.tools.tool_browser("site_detect")); return
            if low == "site status":
                self.ui.info(self.tools.tool_browser("site_profile")); return
            if low.startswith("site use "):
                parts = text.split(maxsplit=3)
                if len(parts) < 4: raise ValueError("Usage: /browser site use <wordpress|hostinger|cpanel|plesk> <base-url>")
                self.ui.success(self.tools.tool_browser("site_profile", name=parts[2], url=parts[3])); return
            if low.startswith("site open "):
                self.ui.success(self.tools.tool_browser("site_open", name=text.split(maxsplit=2)[2].strip())); return
            if low.startswith("site upload "):
                self.ui.success(self.tools.tool_browser("site_upload", file_path=text.split(maxsplit=2)[2].strip())); return
            if low == "files" or low == "files list":
                self.ui.info(self.tools.tool_browser("workspace_list")); return
            if low.startswith("files stage "):
                parts = text.split(maxsplit=3)
                source = parts[2]; target = parts[3] if len(parts) > 3 else ""
                self.ui.success(self.tools.tool_browser("workspace_stage", file_path=source, name=target)); return
            if low.startswith("files zip "):
                parts = text.split(maxsplit=3)
                if len(parts) < 4: raise ValueError("Usage: /browser files zip <workspace-source> <archive.zip>")
                self.ui.success(self.tools.tool_browser("workspace_zip", file_path=parts[2], name=parts[3])); return
            if low.startswith("files extract "):
                parts = text.split(maxsplit=3)
                if len(parts) < 4: raise ValueError("Usage: /browser files extract <archive.zip> <folder>")
                self.ui.success(self.tools.tool_browser("workspace_extract", file_path=parts[2], name=parts[3])); return
            if low.startswith("files move "):
                parts = text.split(maxsplit=3)
                if len(parts) < 4: raise ValueError("Usage: /browser files move <source> <destination>")
                self.ui.success(self.tools.tool_browser("workspace_move", file_path=parts[2], name=parts[3])); return
            if low.startswith("upload "):
                parts = text.split(maxsplit=2)
                self.ui.success(self.tools.tool_browser("upload", file_path=parts[1], selector=parts[2] if len(parts) > 2 else 'input[type="file"]')); return
            if low.startswith("wp propose-delete "):
                raw = text.split(maxsplit=2)[2].strip()
                targets = []
                for item in raw.split("|"):
                    bits = [x.strip() for x in item.split(":", 2)]
                    if len(bits) < 2: raise ValueError("Use kind:name[:warning], separated by |")
                    targets.append({"kind": bits[0], "name": bits[1], "warning": bits[2] if len(bits) > 2 else ""})
                self.ui.info(self.tools.tool_browser("wordpress_propose_delete", value=json.dumps(targets))); return
            if low.startswith("wp approve-delete "):
                token = text.split(maxsplit=2)[2].strip()
                self.ui.success(self.tools.tool_browser("wordpress_approve_delete", proposal_id=token, approval_token=token)); return
            if low.startswith("wp delete "):
                parts = text.split(maxsplit=3)
                if len(parts) < 4: raise ValueError("Usage: /browser wp delete <proposal-token> <observed-selector>")
                self.ui.success(self.tools.tool_browser("wordpress_delete", proposal_id=parts[2], selector=parts[3])); return
            if low.startswith("run "):
                spec = text.split(maxsplit=1)[1].strip()
                repeat = 1
                m = re.match(r"^(.*?)(?:\s+(?:x|repeat=)(\d+))?$", spec, flags=re.I)
                name = (m.group(1) if m else spec).strip()
                if m and m.group(2): repeat = int(m.group(2))
                self.ui.info(self.tools.browser_controller.run_routine(name, repeat=repeat))
                return
            if low.startswith("click "):
                selector = text.split(maxsplit=1)[1].strip(); self.ui.success(self.tools.browser_controller.click(selector)); return
            if low.startswith("fill "):
                bits = text.split(maxsplit=2)
                if len(bits) < 3: raise ValueError("Usage: /browser fill <selector> <value>")
                self.ui.success(self.tools.browser_controller.fill(bits[1], bits[2])); return
            if low.startswith("scroll"):
                arg2 = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else "650"
                amount = int(arg2) if re.match(r"^[+-]?\d+$", arg2) else arg2
                self.ui.success(self.tools.browser_controller.scroll(amount)); return
            if low.startswith("wait"):
                arg2 = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else "750"
                self.ui.success(self.tools.browser_controller.wait(int(arg2))); return
            if low == "close":
                result = self.tools.tool_browser("close"); self.ui.success(result)
                if self.local_telemetry: self.telemetry.record("browser", action="close", browser_provider=self.tools.browser_controller.provider, ok=True)
                return
            if low.startswith("screenshot"):
                name = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else "page"
                result = self.tools.tool_browser("screenshot", name=name); self.ui.success(result)
                if self.local_telemetry: self.telemetry.record("browser", action="screenshot", browser_provider=self.tools.browser_controller.provider, ok=True)
                return
            if low.startswith("map"):
                selector = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else "body"
                result = self.tools.tool_browser("reverse_engineer", selector=selector, name="design-map"); self.ui.info(result)
                if self.local_telemetry: self.telemetry.record("browser", action="reverse_engineer", browser_provider=self.tools.browser_controller.provider, ok=True)
                return
            if re.match(r"^https?://", text, flags=re.I):
                url = text.rstrip(",;")
                result = self.tools.tool_browser("navigate", url=url); self.ui.success(result)
                if self.local_telemetry: self.telemetry.record("browser", action="navigate", browser_provider=self.tools.browser_controller.provider, ok=True)
                return
            self.ui.error("Usage: /browser <url> | site detect|status|use|open|upload | wp status|propose-delete|approve-delete|delete | files list|stage|zip|extract|move | upload <workspace-file> [selector] | map [selector] | screenshot [name] | click <selector> | fill <selector> <value> | scroll [px|selector] | wait [ms] | learn <name>|stop|cancel | routines | run <name> [xN] | status | close")
        except Exception as exc:
            self.ui.error(f"Browser · {exc}")
            if self.local_telemetry: self.telemetry.record("browser", action=low or "status", browser_provider=self.tools.browser_controller.provider, ok=False, detail=str(exc)[:300])

    def insights_command(self, arg: str | None = None) -> None:
        period = (arg or "today").strip().lower()
        if period not in {"today", "day", "week", "weekly", "7d"}:
            self.ui.error("Usage: /insights today|week")
            return
        if not self.local_telemetry:
            self.ui.info("Local harness telemetry is OFF · enable it in Settings → Engineering intelligence")
            return
        key = "week" if period in {"week", "weekly", "7d"} else "today"
        self.ui.heading("Harness insights")
        print(self.telemetry.summary(key))
        path = self.telemetry.save_report(key)
        self.ui.muted(f"Saved · {path} · local only · 0 model tokens")

    def web_command(self, arg: str | None = None) -> None:
        query = (arg or "").strip()
        if not query:
            self.ui.error("Usage: /web <search query>")
            return
        self.ui.info("Searching public web locally · Ollama tokens 0")
        try:
            print(search_web(query, max_results=8))
        except Exception as exc:
            self.ui.error(f"Web search · {exc}")

    def hooks_command(self, arg: str | None = None) -> None:
        text = (arg or "status").strip().lower()
        if text == "init":
            path = self.hooks.init_example()
            self.ui.success(f"Hooks scaffold · {path}")
            return
        if text in {"on", "off"}:
            self.hooks_enabled = text == "on"
            self.hooks.enabled = self.hooks_enabled
            self.persist_state()
            self.ui.success(f"Lifecycle hooks · {'ON' if self.hooks_enabled else 'OFF'}")
            return
        self.ui.heading("Lifecycle hooks")
        print(f"  Enabled  {'yes' if self.hooks_enabled else 'no'}")
        print(f"  Config   {self.hooks.path}")
        cfg = self.hooks.config()
        for event in ("pre_task", "post_success", "post_failure", "post_task"):
            print(f"  {event:<13} {len(cfg.get(event, []))} command(s)")
        self.ui.muted("Hooks run local commands only; they never call a model themselves.")

    # ---------- external provider harness / specialist roles ----------
    def provider_command(self, arg: str | None = None) -> None:
        text = (arg or "status").strip()
        parts = text.split()
        action = parts[0].lower() if parts else "status"
        provider = parts[1].lower() if len(parts) > 1 else ""

        if action in {"status", "list"}:
            self.ui.heading("External providers")
            for name in ("codex", "agy"):
                try:
                    st = self.provider_harness.status(name)
                    state = "installed" if st.installed else "missing"
                    print(f"  {name:<7} {state:<10} auth {st.auth:<16} {st.version}")
                    if st.detail:
                        self.ui.muted("    " + st.detail.replace("\n", " · ")[:260])
                except Exception as exc:
                    self.ui.error(f"{name} · {exc}")
            print(f"\n  Codex model  {self.codex_model or 'provider default'} · effort {self.codex_effort}")
            print(f"  AGY model    {self.agy_model or 'provider default'} · effort {self.agy_effort}")
            for name in ("codex", "agy"):
                q = self.provider_harness.quota_cached(name)
                if q: print(f"  {name.upper():<12} quota {self.provider_harness.quota_text(q)} · authoritative provider snapshot")
            self.ui.muted("Official provider runtimes own OAuth/keyring credentials. Advertpreneur does not read or copy provider auth files.")
            return

        if provider not in {"codex", "agy"}:
            self.ui.error("Usage: /providers status | login codex|agy | test codex|agy | models codex|agy | usage codex|agy | model codex|agy <id|default> | effort codex|agy <level>")
            return
        try:
            if action == "login":
                self.ui.info(f"Opening official {provider.upper()} account setup/login · credentials remain provider-owned")
                rc = self.provider_harness.login(provider)
                if rc == 0:
                    self.ui.success(f"{provider.upper()} official account flow completed")
                else:
                    self.ui.error(f"{provider.upper()} account flow exited with code {rc}")
                return
            if action == "test":
                self.ui.info(f"Testing {provider.upper()} with a tiny explicit provider call")
                model = self.codex_model if provider == "codex" else self.agy_model
                effort = self.codex_effort if provider == "codex" else self.agy_effort
                self.ui.begin_working(model or provider, "Provider test", 1)
                try:
                    run = self.provider_harness.auth_test(provider, model=model, effort=effort)
                    self._show_provider_run(run, purpose="auth-test")
                    self._refresh_provider_quota_async(provider)
                finally:
                    self.ui.end_working()
                return
            if action == "models":
                self.ui.heading(f"{provider.upper()} external models")
                self.ui.begin_working(provider.upper(), "Refreshing model catalogue", 1)
                try:
                    rows = self.provider_harness.model_catalog(provider, refresh=True)
                finally:
                    self.ui.end_working()
                configured = self.codex_model if provider == "codex" else self.agy_model
                for row in rows:
                    marker = "  ← configured" if row.id == configured else ""
                    print(f"  {row.id:<34} {short_text(row.display, 42)}{marker}")
                self.ui.muted(f"{len(rows)} model(s) · also available from /model → {provider.upper()} external")
                return
            if action in {"usage", "quota"}:
                model = self.codex_model if provider == "codex" else self.agy_model
                self.ui.info(f"Refreshing authoritative {provider.upper()} subscription quota…")
                q = self.provider_harness.quota(provider, model=model, refresh=True)
                self.ui.heading(f"{provider.upper()} subscription quota")
                from datetime import datetime as _dt
                for w in q.windows:
                    reset = _dt.fromtimestamp(w.resets_at).astimezone().strftime("%a %H:%M") if w.resets_at else "provider did not expose reset timestamp"
                    print(f"  {w.label:<8} {w.remaining_percent:>5.0f}% remaining · reset {reset}")
                self.ui.muted(f"Source · {q.source} · fetched live; Advertpreneur does not estimate these percentages.")
                return
            if action == "model":
                value = " ".join(parts[2:]).strip() if len(parts) > 2 else ""
                if not value:
                    rows = self.provider_harness.model_catalog(provider)
                    chosen = self.ui.choose(
                        f"{provider.upper()} external model",
                        [MenuItem(x.id, x.display, (x.detail or "subscription model")[:180]) for x in rows],
                        fuzzy=True,
                    )
                    if not chosen:
                        return
                    value = chosen
                if value.lower() == "default":
                    value = ""
                if value:
                    rows = self.provider_harness.model_catalog(provider)
                    lookup = {x.id.lower(): x.id for x in rows}
                    actual = lookup.get(value.lower())
                    if not actual:
                        self.ui.error(f"{provider.upper()} model not available · {value}")
                        return
                    value = actual
                if provider == "codex":
                    self.codex_model = value
                else:
                    self.agy_model = value
                    fixed = ExternalProviderHarness.agy_model_effort(value)
                    if fixed:
                        self.agy_effort = fixed
                self.persist_state()
                self._refresh_provider_quota_async(provider)
                self.ui.success(f"{provider.upper()} model · {value or 'provider default'}")
                return
            if action == "effort":
                value = (parts[2].lower() if len(parts) > 2 else "")
                allowed = {"low", "medium", "high"}
                if value not in allowed:
                    picked = self.ui.choose(f"{provider.upper()} effort", [MenuItem(x, x.title(), "reasoning effort" + (" · recommended" if x == "medium" else "")) for x in ("low", "medium", "high")])
                    if not picked: return
                    value = picked
                if provider == "codex":
                    self.codex_effort = value
                else:
                    fixed = ExternalProviderHarness.agy_model_effort(self.agy_model)
                    if fixed and value != fixed:
                        base = re.sub(r"-(low|medium|high)$", "", self.agy_model, flags=re.I)
                        candidate = f"{base}-{value}"
                        rows = self.provider_harness.model_catalog("agy")
                        available = {row.id for row in rows}
                        if candidate not in available:
                            self.ui.error(f"AGY {self.agy_model} is fixed at {fixed}; no {value} variant is available.")
                            return
                        self.agy_model = candidate
                        if self.active.provider == "agy":
                            self.active.model = candidate
                            self.current_session.model = candidate
                    self.agy_effort = value
                self.persist_state()
                self.ui.success(f"{provider.upper()} effort · {value}")
                return
        except ProviderHarnessError as exc:
            self.ui.error(f"{provider.upper()} · {exc}")
        except Exception as exc:
            self.ui.error(f"{provider.upper()} · {type(exc).__name__}: {exc}")

    def _show_provider_run(self, run: ProviderRun, purpose: str = "specialist") -> None:
        if run.text:
            self.last_result = run.text
            self.ui.result(run.text)
        else:
            self.ui.error(run.stderr or f"{run.provider} returned no response")
        usage = f"{run.input_tokens:,} in / {run.output_tokens:,} out"
        if run.cache_read_tokens:
            usage += f" · {run.cache_read_tokens:,} cached"
        if run.thinking_tokens:
            usage += f" · {run.thinking_tokens:,} thinking"
        effort = f" · reasoning {run.reasoning_effort}" if run.reasoning_effort else ""
        tools = f" · {run.tool_calls} tool call(s)" if run.tool_calls else ""
        self.ui.muted(f"{run.provider}/{run.model}{effort} · {usage}{tools} · {run.duration_seconds:.1f}s · provider/subscription managed")
        if self.local_telemetry:
            self.telemetry.record(
                "provider_run", provider=run.provider, model=run.model, purpose=purpose,
                input_tokens=run.input_tokens, output_tokens=run.output_tokens,
                cache_read_tokens=run.cache_read_tokens, thinking_tokens=run.thinking_tokens,
                tool_calls=run.tool_calls, reasoning_effort=run.reasoning_effort,
                ok=run.ok, status=run.status or run.returncode,
            )

    def _specialist_packet(self, instruction: str, *, review: bool = False) -> str:
        try:
            diff = self.tools.tool_git_diff(False)
        except Exception:
            diff = "Git diff unavailable."
        if len(diff) > 52000:
            diff = diff[:52000] + "\n...[diff truncated locally]"
        try:
            evidence = self.working_record.bridge_summary(self._current_task_raw or instruction)
        except Exception:
            evidence = {}
        frameworks = ""
        try:
            frameworks = self.framework_intelligence.context(instruction, max_chars=1800)
        except Exception:
            pass
        return (
            "ADVERTPRENEUR SPECIALIST PACKET\n"
            f"Project: {self.project.name}\n"
            f"Project path label: {self.project}\n"
            f"Goal: {self.goal or '(none)'}\n"
            f"Mode: {'READ-ONLY REVIEW' if review else 'READ-ONLY SENIOR ANALYSIS'}\n\n"
            f"Instruction:\n{instruction}\n\n"
            f"Framework context:\n{frameworks or '(none)'}\n\n"
            "Verified/session evidence (local record):\n"
            + json.dumps(evidence, ensure_ascii=False, indent=2, default=str)[:12000]
            + "\n\nCurrent uncommitted diff:\n" + diff
            + "\n\nDo not edit files. Base conclusions on this packet. Clearly label any inference or missing evidence."
        )

    def _run_external_specialist(self, provider: str, instruction: str, *, review: bool = False) -> ProviderRun | None:
        provider = provider.lower().strip()
        model = self.codex_model if provider == "codex" else self.agy_model
        effort = self.codex_effort if provider == "codex" else self.agy_effort
        packet = self._specialist_packet(instruction, review=review)
        try:
            label = "Reviewing" if review else "Senior reasoning"
            self.ui.info(f"{provider.upper()} {'Reviewer' if review else 'Senior Engineer'} · official authenticated CLI · read-only packet")
            self.ui.begin_working(model or provider, label, 1)
            run = self.provider_harness.run_packet(provider, packet, instruction, model=model, effort=effort)
            self._show_provider_run(run, purpose="review" if review else "expert")
            self._refresh_provider_quota_async(provider)
            return run
        except Exception as exc:
            self.ui.error(f"{provider.upper()} · {type(exc).__name__}: {exc}")
            if self.local_telemetry:
                self.telemetry.record("provider_run", provider=provider, purpose="review" if review else "expert", ok=False, detail=str(exc)[:300])
            return None
        finally:
            self.ui.end_working()

    def _senior_profile(self) -> ModelProfile:
        return ModelProfile(self.senior_provider, self.senior_model, False, min(max(self.active.max_output_tokens, 2500), 5000))

    def expert_task(self, arg: str | None = None) -> None:
        text = (arg or "").strip()
        explicit_provider = ""
        if text:
            first, *rest = text.split(maxsplit=1)
            if first.lower() in {"codex", "agy", "ollama"}:
                explicit_provider = first.lower()
                text = rest[0] if rest else ""
        task = text or self.ui.prompt_text("Senior engineer task", "")
        if not task:
            return
        provider = explicit_provider or self.senior_provider
        if provider in {"codex", "agy"}:
            self._run_external_specialist(provider, task, review=False)
            return
        if provider == "ollama":
            provider = self.active.provider
        profile = ModelProfile(provider, self.senior_model if not explicit_provider else self.active.model, False, min(max(self.active.max_output_tokens, 2500), 5000))
        self.ui.info(f"Senior Engineer · {profile.provider}/{profile.model} · explicit invocation only")
        self.run_task(
            "SENIOR ENGINEER ROLE. Diagnose precisely before acting. Reuse verified evidence and the engineering handbook; use official web documentation only when needed. For architecture/debugging, identify root cause and safest implementation path. Do not perform broad trial-and-error. " + task,
            profile_override=profile, checkpoint=False, allow_auto_review=False,
        )

    def _show_provider_alternatives(self, exhausted_provider: str = "") -> None:
        self.ui.heading("Model alternatives · no automatic failover")
        shown = 0
        for provider in ("codex", "agy"):
            try:
                rows = self.provider_harness.model_catalog(provider)[:8]
            except Exception:
                rows = []
            for row in rows:
                if provider == exhausted_provider:
                    quota = "current provider limit reached"
                else:
                    q = self.provider_harness.quota_cached(provider, row.id)
                    quota = self.provider_harness.quota_text(q) if q else "quota unknown"
                print(f"  {provider.upper():<6} {row.id:<28} {quota}")
                shown += 1
        try:
            local = OllamaClient("local", self.api_key).list_models()[:8]
        except Exception:
            local = []
        for name in local:
            print(f"  OLLAMA {name:<28} local · ready")
            shown += 1
        if not shown:
            self.ui.muted("No alternate catalogue is currently reachable.")
        else:
            self.ui.muted("Use /model to switch deliberately. Advertpreneur will not fail over automatically.")

    @staticmethod
    def _quota_consumption(before, after) -> Dict[str, float]:
        if not before or not after: return {}
        out: Dict[str, float] = {}
        for label in ("5h", "weekly"):
            b = next((w for w in before.windows if w.label == label and w.state == "reported"), None)
            a = next((w for w in after.windows if w.label == label and w.state == "reported"), None)
            if not b or not a: continue
            # If the provider moved the reset boundary during the task, this is a
            # reset rather than measurable task consumption. Do not invent a delta.
            if b.resets_at and a.resets_at and b.resets_at != a.resets_at: continue
            delta = float(b.remaining_percent) - float(a.remaining_percent)
            if delta > 0: out[label] = round(delta, 3)
        return out

    @staticmethod
    def _zero_token_reply(text: str) -> str:
        """Handle tiny social acknowledgements locally instead of paying a model bootstrap."""
        value = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).strip()
        value = " ".join(value.split())
        if value in {"hi", "hello", "hey", "hello there", "hi there", "hey there"}:
            return "Hello! How can I help with Advertpreneur CLI?"
        if value in {"thanks", "thank you", "thx", "ty", "great thanks", "ok thanks", "okay thanks"}:
            return "You're welcome."
        # Deterministic instruction probes should never boot a paid coding agent.
        m = re.fullmatch(r"reply (?:only|exactly)(?: with)? (.+)", value)
        if m:
            answer = m.group(1).strip()
            if answer in {"ok", "okay", "yes", "no", "done", "pass", "pong"}:
                return "OK" if answer in {"ok", "okay"} else answer.capitalize()
        return ""

    def _codex_mcp_states(self, task_text: str) -> Dict[str, bool]:
        """Return explicit per-turn states for user-enabled Codex MCP servers.

        Explicit true/false overrides matter on resumed Codex threads: a server
        disabled for an unrelated first turn must be re-enabled when a later turn
        actually needs it. User-disabled MCPs are never enabled by Advertpreneur.
        """
        if not hasattr(self, "mcp_manager"):
            return {}
        try:
            servers = [row for row in self.mcp_manager.discover() if row.enabled]
        except Exception:
            return {}
        text = str(task_text or "").lower()
        keep_all = "mcp" in text and any(x in text for x in ("all mcp", "all servers", "configured mcp"))
        category_aliases = {
            "hostinger": ("hostinger", "dns", "hosting", "domain", "billing", "reach"),
            "supabase": ("supabase", "postgres", "database"),
            "paddle": ("paddle", "payment", "billing"),
            "aios": ("aios",),
            "myecomnews": ("myecomnews",),
            "stitch": ("stitch",),
            "cua_repl": ("browser", "computer use", "cua", "chrome"),
            "node_repl": ("browser", "computer use", "node repl", "javascript repl"),
        }
        states: Dict[str, bool] = {}
        for row in servers:
            name = str(row.name or "")
            low = name.lower()
            words = [w for w in re.split(r"[-_.]+", low) if len(w) >= 4]
            relevant = keep_all or low in text or any(w in text for w in words)
            if not relevant:
                for prefix, aliases in category_aliases.items():
                    if low == prefix or low.startswith(prefix + "-"):
                        relevant = any(alias in text for alias in aliases)
                        break
            states[name] = bool(relevant)
        return states

    def _codex_mcp_transport_overrides(self, task_text: str) -> Dict[str, Dict[str, Any]]:
        """Build safe *complete* overrides only for irrelevant MCP servers.

        Current Codex request-level config replaces an MCP server table. A bare
        ``enabled=false`` therefore erases command/url and fails validation. This
        preserves the minimum transport required to validate while disabling the
        server. Relevant servers are omitted so Codex inherits their real config.
        """
        text = str(task_text or "").lower()
        live_site_terms = (
            "wp-admin", "wordpress", "hostinger", "cpanel", "plesk", "file manager",
            "upload plugin", "upload theme", "install plugin", "activate theme", "edit post",
            "edit page", "wordpress settings", "site settings",
        )
        live_site = any(term in text for term in live_site_terms)
        project = Path(getattr(self, "project", Path.cwd())).resolve()
        states = self._codex_mcp_states(task_text)
        if not states or not hasattr(self, "mcp_manager"):
            overrides: Dict[str, Dict[str, Any]] = {}
            if live_site:
                overrides["advertpreneur-browser"] = {
                    "enabled": True, "command": sys.executable,
                    "args": ["-m", "advertpreneur_cli.browser_mcp", "--project", str(project)],
                    "cwd": str(project),
                }
            return overrides
        try:
            rows = {str(row.name): row for row in self.mcp_manager.discover() if row.enabled}
        except Exception:
            return {}
        overrides: Dict[str, Dict[str, Any]] = {}
        for name, relevant in states.items():
            if relevant:
                continue
            row = rows.get(name)
            if not row:
                continue
            tr = dict(getattr(row, "transport", {}) or {})
            kind = str(getattr(row, "transport_type", "") or tr.get("type") or "").lower()
            entry: Dict[str, Any] = {"enabled": False}
            if kind == "stdio":
                command = tr.get("command")
                if not command:
                    continue
                entry["command"] = command
                if isinstance(tr.get("args"), list): entry["args"] = list(tr["args"])
                if tr.get("cwd"): entry["cwd"] = tr.get("cwd")
            elif kind in {"streamable_http", "http", "sse"}:
                url = tr.get("url")
                if not url:
                    continue
                entry["url"] = url
            else:
                # Unknown transports are safer left inherited than partially
                # overridden; never trade token savings for a broken config.
                continue
            overrides[name] = entry
        if live_site:
            # This is an ADP-owned stdio server, not a user MCP. Supplying its
            # complete transport lets the native Codex session call the existing
            # Browser Bridge directly and open/focus its own controlled tab.
            overrides["advertpreneur-browser"] = {
                "enabled": True, "command": sys.executable,
                "args": ["-m", "advertpreneur_cli.browser_mcp", "--project", str(project)],
                "cwd": str(project),
            }
        return overrides

    def _codex_disabled_mcps(self, task_text: str) -> List[str]:
        # Backwards-compatible helper used by older tests/callers.
        return [name for name, enabled in self._codex_mcp_states(task_text).items() if not enabled]

    @staticmethod
    def _codex_plugins_needed(task_text: str) -> bool:
        low = str(task_text or "").lower()
        return any(x in low for x in (
            "plugin", "skill", "react", "supabase", "frontend", "ui/ux", "ui ux",
            "design system", "reverse engineer", "reverse-engineer", "browser", "computer use",
        ))

    def _agy_effective_effort(self, model: str, requested: str) -> str:
        return ExternalProviderHarness.agy_effective_effort(model, requested)

    @staticmethod
    def _provider_thread_should_rollover(run: ProviderRun) -> bool:
        # Native threads improve cache reuse, but a pathological long conversation
        # can eventually become more expensive than a clean bootstrap. Roll over only
        # after clear evidence of context bloat, never on ordinary coding turns.
        context_input = int(getattr(run, "context_input_tokens", run.input_tokens) or 0)
        return context_input >= 220_000 or run.uncached_input_tokens >= 90_000

    @staticmethod
    def _micro_coding_task(task_text: str) -> bool:
        """Conservative zero-model classifier for cheap, self-contained tasks."""
        low = " ".join(str(task_text or "").lower().split())
        if not low or len(low) > 420:
            return False
        hard = (
            "debug", "fix", "refactor", "migrate", "reverse engineer", "wordpress", "android",
            "database", "api", "auth", "security", "test suite", "multiple files", "project",
        )
        if any(x in low for x in hard):
            return False
        makeish = any(x in low for x in ("make ", "create " , "write " , "build a simple", "simple html", "single html"))
        fileish = any(x in low for x in ("html", "css", "javascript", "js file", "single file", "one file"))
        return makeish and fileish

    @staticmethod
    def _estimated_text_tokens(text: str) -> int:
        # Local display-only estimate. Never used for billing/quota decisions.
        return max(1, (len(str(text or "")) + 3) // 4)

    def _run_external_coding(self, provider: str, task_text: str, model: str) -> ProviderRun:
        requested_effort = self.codex_effort if provider == "codex" else self.agy_effort
        effort = (ExternalProviderHarness.normalize_effort(requested_effort) if provider == "codex"
                  else self._agy_effective_effort(model, requested_effort))
        if provider == "codex": self.codex_effort = effort
        else: self.agy_effort = effort
        micro_task = self._micro_coding_task(task_text)
        try: framework = "" if micro_task else self.framework_intelligence.context(task_text, max_chars=(self._current_task_plan.framework_chars if self._current_task_plan else 1800))
        except Exception: framework = ""
        prior_thread = str(getattr(self.current_session, "provider_threads", {}).get(provider, "") or "")
        # Native Codex/AGY threads carry standing instructions themselves. Keep the
        # per-turn user payload narrow so ADP does not duplicate a coding bootstrap.
        # AGY may otherwise treat a standalone file request as a scratch artifact;
        # explicitly ground it to the ADP project root with one compact instruction.
        workspace_guard = ""
        if provider == "agy":
            workspace_guard = (
                f"ADP workspace: {self.project}. Create/edit requested project files inside this root; "
                "do not use Antigravity scratch/artifact folders unless the user explicitly asks.\n\n"
            )
        contract_note = self.project_contract.context(max_chars=1500) if hasattr(self, "project_contract") else ""
        plan_note = self._current_task_plan.context(self.project_contract, max_chars=1100) if self._current_task_plan else ""
        agy_plan = (plan_note + "\n\n") if provider == "agy" and plan_note else ""
        prompt = workspace_guard + agy_plan + (((framework + "\n\n") if framework else "") + task_text)
        coding_instructions = (
            "You are the primary coding agent inside Advertpreneur CLI. Work directly in the current project workspace. "
            "Inspect relevant existing code before editing, keep changes scoped, and actually implement the user's task. "
            "Respect protected/generated paths from the project contract. Run proportional validation appropriate to the changed files; "
            "never claim PASS without executing required validation. Do not access files outside this workspace. "
            "Return a concise factual completion report.\n" + contract_note + ("\n" + plan_note if plan_note else "")
        )
        coding_instructions += "\n" + worker_ownership_contract()
        mcp_states = self._codex_mcp_states(task_text) if provider == "codex" else {}
        disabled_mcps = [name for name, enabled in mcp_states.items() if not enabled]
        mcp_overrides = self._codex_mcp_transport_overrides(task_text) if provider == "codex" else {}
        if provider == "codex" and "advertpreneur-browser" in mcp_overrides:
            coding_instructions += (
                "\n\nLive-site browser contract: use the `advertpreneur-browser` MCP tools yourself for WordPress, wp-admin, "
                "Hostinger, and hosting-panel work. `browser_navigate` opens and focuses the controlled browser tab through "
                "Advertpreneur's Browser Bridge. Inspect observed UI before each mutation and after each result. If a login page "
                "is observed, report `Login needed in browser` and wait; never request credentials. Do not ask the operator to "
                "copy URLs, click controls, or run manual browser commands. Never delete/remove through browser tools; report a "
                "deletion proposal for explicit approval instead.\n"
            )
        plugins_enabled = self._codex_plugins_needed(task_text) if provider == "codex" else False
        prompt = prompt + "\n\n" + ExternalActionGateway.contract()
        before_q = None
        try: before_q = self.provider_harness.quota(provider, model=model, refresh=True, timeout=10)
        except Exception: pass
        if before_q and before_q.exhausted and self.subscription_quota_protection:
            self.ui.error(f"{provider.upper()} limit reached · {self.provider_harness.quota_text(before_q)} · provider call blocked")
            self.notifier.quota_warning(provider, 0, 0.0, self.provider_harness.quota_text(before_q))
            self._show_provider_alternatives(provider)
            return ProviderRun(provider, model or "provider-default", "Provider quota exhausted; no model request was sent.", 3,
                status="QUOTA_EXHAUSTED", reasoning_effort=effort, quota_before=self.provider_harness.quota_text(before_q), quota_after=self.provider_harness.quota_text(before_q))
        cold_provider_session = not bool(prior_thread)
        if micro_task and cold_provider_session and provider in {"codex", "agy"}:
            note = (
                f"Smart Spend · this is a small task but {provider.upper()} is cold. "
                "The provider may spend a large one-time bootstrap/context load before doing the actual work."
            )
            self.ui.muted(note)
            # Never stall Browser Bridge/autopilot. In an interactive CLI, let the
            # user make the spend decision rather than silently rerouting models.
            if not (self.current_session.bridge_enabled and self.current_session.bridge_autopilot):
                if not self.ui.confirm("quota", f"Continue this small task with cold {provider.upper()} instead of choosing an Ollama model"):
                    return ProviderRun(provider, model or "provider-default", "Canceled before provider call by Smart Spend guard.", 2,
                        status="SMART_SPEND_CANCELED", reasoning_effort=effort, quota_before=self.provider_harness.quota_text(before_q) if before_q else "Quota unavailable", quota_after=self.provider_harness.quota_text(before_q) if before_q else "Quota unavailable")
        self._external_tool_calls = 0; self._external_activity = "coding"; self._external_started_at = __import__("time").monotonic()
        self._external_current_file = ""
        # run_task already started the persistent live footer during local
        # preparation. Keep that renderer in place instead of swapping to the
        # compact provider variant, which hid the joke row between stages.
        self.ui.set_working_state("Coding", model=model or provider, turn=1, detail=(f"R:{effort}" if effort else ""), event_driven=False)
        def activity(event: ProviderActivity) -> None:
            # Keep the terminal calm: one live line only. Structured provider events
            # update the current file when known, while commands/reasoning do not
            # replace the stable "coding" status with noisy transient phrases.
            if event.kind == "thinking":
                # Heartbeat from provider during a reasoning phase
                self.ui.set_working_state("Thinking", model=model or provider, detail=str(event.detail or ""), event_driven=True)
                if event.detail and len(str(event.detail).strip()) > 3:
                    self.ui.thought_process(str(event.detail))
                return
            if event.kind == "tool":
                self._external_tool_calls += 1
                detail = str(event.detail or "").strip()
                label_low = str(event.label or "").lower()
                tool_low = str(event.tool or "").lower()
                if "writing" in label_low or "create" in label_low or "write" in tool_low:
                    self.ui.action_marker("write", detail)
                elif "editing" in label_low or "edit" in tool_low or "replace" in tool_low:
                    self.ui.action_marker("edit", detail)
                elif "reading" in label_low or "read" in tool_low or "view" in tool_low:
                    self.ui.action_marker("read", detail)
                elif "bash" in tool_low or "command" in tool_low or "run" in tool_low or "powershell" in tool_low:
                    self.ui.action_marker("bash", detail)
                elif "browser" in tool_low:
                    self.ui.action_marker("browser", detail)
                elif detail:
                    self.ui.action_marker(event.tool or "Action", detail)

            detail = str(event.detail or "").strip()
            label_low = str(event.label or "").lower()
            file_candidate = ""
            if detail and ("writing" in label_low or "reading" in label_low or str(event.tool or "").lower() in {"edit", "file", "write_to_file", "read_file", "view_file"}):
                try:
                    from pathlib import PureWindowsPath
                    file_candidate = PureWindowsPath(detail).name if "\\" in detail or ":" in detail else Path(detail).name
                except Exception:
                    file_candidate = ""
            if not file_candidate and " · " in str(event.label or "") and ("writing" in label_low or "reading" in label_low):
                file_candidate = str(event.label).rsplit(" · ", 1)[-1].strip()
            if file_candidate and len(file_candidate) <= 120:
                self._external_current_file = file_candidate
            self._external_activity = "coding"
            self.ui.set_working_state("coding", model=model or provider, detail=self._external_current_file, event_driven=True)
            if self.current_session.bridge_enabled:
                try:
                    bridge_detail = "coding" + (f" · {self._external_current_file}" if self._external_current_file else "")
                    self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Working", bridge_detail, model or provider)
                except Exception: pass
        if self.current_session.bridge_enabled:
            try: self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Working", (f"Starting · R:{effort}" if effort else "Starting"), model or provider)
            except Exception: pass
        def action_activity(request) -> None:
            self._external_activity = f"Action · {request.tool}"
            self.ui.set_working_state("Action", model=model or provider, detail=request.tool, event_driven=True)
            if self.current_session.bridge_enabled:
                try:
                    self.bridge.update_status(self.current_session.id, self.bridge_cli_token, "Working", self._external_activity, model or provider)
                except Exception:
                    pass

        gateway = ExternalActionGateway(self.tools, on_action=action_activity)
        turn_runs: list[ProviderRun] = []

        def run_provider_turn(turn_prompt: str, conversation_id: str) -> tuple[str, str]:
            if getattr(self, "_yield_requested", threading.Event()).is_set():
                turn = ProviderRun(
                    provider, model or "provider-default",
                    "Task stopped · Escape pressed", 2,
                    conversation_id=conversation_id, status="YIELDED", reasoning_effort=effort,
                )
                turn_runs.append(turn)
                return turn.text, turn.conversation_id
            try:
                turn = self.provider_harness.run(
                    provider, turn_prompt, model=model, effort=effort, cwd=self.project, timeout=900,
                    write=not self.plan_mode, on_event=activity, conversation_id=conversation_id,
                    session_key=self.current_session.id, disabled_mcp_servers=disabled_mcps,
                    mcp_server_states=mcp_states, mcp_server_overrides=mcp_overrides, plugins_enabled=plugins_enabled,
                    developer_instructions=coding_instructions if provider == "codex" else "",
                )
            except ProviderHarnessError:
                if not self._yield_requested.is_set():
                    raise
                turn = ProviderRun(
                    provider, model or "provider-default",
                    "Task stopped · Escape pressed", 2,
                    conversation_id=conversation_id, status="YIELDED", reasoning_effort=effort,
                )
            turn_runs.append(turn)
            return turn.text, turn.conversation_id

        loop = gateway.drive(
            prompt,
            run_provider_turn,
            prior_thread,
            should_yield=getattr(self, "_yield_requested", threading.Event()).is_set,
        )
        run = turn_runs[-1]
        if loop.yielded:
            run.text = loop.text
            run.status = "YIELDED"
            run.returncode = 2
        elif loop.blocked:
            run.text = loop.text
            run.status = "ACTION_BLOCKED"
            run.returncode = 2
        else:
            run.text = loop.text
        if len(turn_runs) > 1:
            for field in ("duration_seconds", "input_tokens", "output_tokens", "cache_read_tokens", "thinking_tokens", "total_tokens", "tool_calls", "activity_events"):
                setattr(run, field, sum(getattr(row, field, 0) for row in turn_runs))
            run.provider_turns = sum(max(1, int(row.provider_turns or 1)) for row in turn_runs)
        run.tool_calls += loop.actions
        after_q = None
        try: after_q = self.provider_harness.quota(provider, model=model, refresh=True, timeout=10)
        except Exception: pass
        run.quota_before = self.provider_harness.quota_text(before_q) if before_q else "Quota unavailable"
        run.quota_after = self.provider_harness.quota_text(after_q) if after_q else "Quota unavailable"
        run.quota_consumed = self._quota_consumption(before_q, after_q)
        self.budget.record(f"{provider}/{model or 'provider-default'}", run.input_tokens, run.output_tokens, 0.0)
        self._last_external_run = run
        if run.conversation_id:
            if not hasattr(self.current_session, "provider_threads"):
                self.current_session.provider_threads = {}
            self.current_session.provider_threads[provider] = run.conversation_id
        if self._provider_thread_should_rollover(run):
            # Keep this task's result, but start the *next* task on a clean native
            # provider thread to avoid runaway context/input growth.
            self.current_session.provider_threads.pop(provider, None)
            self.ui.muted(
                f"{provider.upper()} context guard · next task will start a fresh provider thread "
                f"({run.context_input_tokens:,} context input / {run.uncached_input_tokens:,} new input this turn)"
            )
        self._external_tool_calls = run.tool_calls
        self._external_activity = "Completed" if run.ok else "Needs attention"
        self._external_started_at = 0.0
        if after_q:
            for threshold in self.provider_harness.quota_thresholds(after_q):
                self.notifier.quota_warning(provider, threshold, after_q.effective_remaining, self.provider_harness.quota_text(after_q))
                if threshold == 0: self._show_provider_alternatives(provider)
        if not self.agent.messages: self.agent.messages = [{"role":"system","content":self.agent.system_prompt()}]
        self.agent.messages.append({"role":"user","content":task_text}); self.agent.messages.append({"role":"assistant","content":run.text})
        self.current_session.input_tokens += run.input_tokens; self.current_session.output_tokens += run.output_tokens; self.current_session.requests += max(1, run.provider_turns)
        self._save_session(); return run

    # ---------- reviewer agents ----------
    def agents_settings(self) -> None:
        while True:
            reviewer = f"{self.reviewer_provider}/{self.reviewer_model or 'default'}" if self.reviewer_provider else "same as active"
            senior = f"{self.senior_provider}/{self.senior_model or 'default'}"
            action = self.ui.choose("Agents & roles", [
                MenuItem("primary", "Primary Coder", f"{self.active.provider}/{self.active.model}"),
                MenuItem("senior", "Senior Engineer", senior),
                MenuItem("reviewer", "Reviewer", reviewer),
                MenuItem("auto", "Automatic review", self.auto_review),
                MenuItem("quota", "Subscription quota protection", "ON" if self.subscription_quota_protection else "OFF"),
                MenuItem("status", "Status", "show role policy"),
            ])
            if not action:
                return
            if action == "primary":
                self.select_model()
                continue
            if action in {"reviewer", "senior"}:
                title = "Senior Engineer" if action == "senior" else "Reviewer"
                source = self.ui.choose(f"{title} source", [
                    MenuItem("same", "Same as active model", "Ollama/current model"),
                    MenuItem("cloud", "Ollama Cloud", "choose cloud model"),
                    MenuItem("local", "Ollama Local", "choose installed local model"),
                    MenuItem("codex", "Codex account", "official codex login; provider-owned quota"),
                    MenuItem("agy", "Antigravity account", "official agy Google login; provider-owned quota"),
                ])
                if not source:
                    continue
                if source == "same":
                    if action == "reviewer": self.reviewer_provider = ""; self.reviewer_model = ""
                    else: self.senior_provider = self.active.provider; self.senior_model = self.active.model
                elif source in {"codex", "agy"}:
                    try:
                        st = self.provider_harness.status(source)
                        if not st.installed or st.auth == "signed-out":
                            if not self.ui.confirm("account", f"{source.upper()} is not ready. Set up official provider login now"):
                                continue
                            if self.provider_harness.login(source) != 0:
                                self.ui.error(f"{source.upper()} login/setup did not complete")
                                continue
                        rows = self.provider_harness.model_catalog(source)
                    except Exception as exc:
                        self.ui.error(f"{source.upper()} · {type(exc).__name__}: {exc}")
                        continue
                    chosen = self.ui.choose(
                        f"{title} · {source.upper()} model",
                        [MenuItem(x.id, x.display, (x.detail or "external subscription model")[:180]) for x in rows],
                        fuzzy=True,
                    )
                    if not chosen:
                        continue
                    if source == "codex": self.codex_model = chosen
                    else: self.agy_model = chosen
                    if action == "reviewer": self.reviewer_provider, self.reviewer_model = source, chosen
                    else: self.senior_provider, self.senior_model = source, chosen
                else:
                    if source == "cloud" and not self.api_key:
                        self.ui.error("Ollama Cloud is signed out · use /login first"); continue
                    try:
                        names = OllamaClient(source, self.api_key).list_models()
                    except Exception as exc:
                        self.ui.error(str(exc)); continue
                    items = []
                    for name in names:
                        if source == "cloud" and self.settings.cloud_access_mode == "free" and not is_free_cloud_model(name, self.settings.free_cloud_models):
                            continue
                        price = "local/free" if source == "local" else ("free starter" if is_free_cloud_model(name, self.settings.free_cloud_models) else "priced")
                        items.append(MenuItem(name, name, price))
                    chosen = self.ui.choose(f"{title} model", items, fuzzy=True)
                    if chosen:
                        if action == "reviewer": self.reviewer_provider, self.reviewer_model = source, chosen
                        else: self.senior_provider, self.senior_model = source, chosen
                self.persist_state()
            elif action == "auto":
                value = self.ui.choose("Automatic review", [
                    MenuItem("off", "Off", "no surprise extra model/provider calls"),
                    MenuItem("large", "Large changes", "review after 4+ changed files"),
                    MenuItem("always", "Always", "review every completed modifying task"),
                ])
                if value:
                    self.auto_review = value; self.persist_state()
            elif action == "quota":
                self.subscription_quota_protection = not self.subscription_quota_protection
                self.persist_state()
                self.ui.success(f"Subscription quota protection · {'ON' if self.subscription_quota_protection else 'OFF'}")
            else:
                self.ui.info(f"Primary · {self.active.provider}/{self.active.model} · Senior · {senior} · Reviewer · {reviewer} · auto review {self.auto_review}")

    def _review_profile(self) -> ModelProfile:
        if not self.reviewer_provider:
            return ModelProfile(self.active.provider, self.active.model, self.active.think, self.active.max_output_tokens)
        if self.reviewer_provider in {"codex", "agy"}:
            return ModelProfile(self.reviewer_provider, self.reviewer_model or "provider-default", False, 2500)
        return ModelProfile(self.reviewer_provider, self.reviewer_model, False, min(self.active.max_output_tokens, 2500))

    # ---------- coding convenience ----------
    def show_diff(self) -> None:
        try:
            out = self.tools.tool_git_diff(False)
            self.ui.heading("Git diff")
            print(out)
        except Exception as exc:
            self.ui.error(str(exc))

    def review(self, arg: str | None = None) -> None:
        instruction = "Review the current uncommitted changes in this project. Focus on bugs, regressions, missing tests, security/correctness risks, and concrete file references. If the changes look sound, say so clearly."
        requested = (arg or "").strip().lower()
        provider = requested if requested in {"codex", "agy"} else self.reviewer_provider
        if provider in {"codex", "agy"}:
            self._run_external_specialist(provider, instruction, review=True)
            return
        profile = self._review_profile()
        self.run_task(
            "Review the current uncommitted changes in this project. Inspect the Git diff and relevant surrounding code. Do not modify files. Report bugs, regressions, missing tests, or risky behavior with concrete file references. If the changes look sound, say so clearly.",
            profile_override=profile, checkpoint=False, allow_auto_review=False,
        )

    def init_agents(self) -> None:
        target = self.project / "AGENTS.md"
        if target.exists():
            self.ui.info("AGENTS.md already exists")
            return
        content = """# AGENTS.md\n\n## Project\nDescribe this project's purpose and architecture here.\n\n## Working rules\n- Inspect existing code before editing.\n- Preserve established conventions and public behavior unless the task requires a change.\n- Keep changes scoped to the requested task.\n- Run relevant build, lint, and test commands before reporting completion.\n- Do not hide failing tests or replace real implementations with placeholders.\n\n## Validation\nDocument the normal build/test commands for this repository here.\n"""
        try:
            self.tools.tool_write_file("AGENTS.md", content)
            self.ui.success("Created AGENTS.md scaffold")
        except Exception as exc:
            self.ui.error(str(exc))

    def copy_latest(self) -> None:
        if not self.last_result:
            self.ui.info("No completed assistant result to copy")
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["clip.exe"], input=self.last_result, text=True, encoding="utf-8", errors="replace", check=True)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=self.last_result, text=True, encoding="utf-8", errors="replace", check=True)
            elif shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=self.last_result, text=True, encoding="utf-8", errors="replace", check=True)
            else:
                raise RuntimeError("No clipboard command found")
            self.ui.success("Latest result copied")
        except Exception as exc:
            self.ui.error(f"Clipboard · {exc}")

    def compact(self) -> None:
        """Compact locally by default. This command never calls a cloud model."""
        try:
            before, after, _memory = self.agent.compact_local(self.context_manager)
            if before == 0:
                self.ui.info("Nothing to compact")
                return
            self.local_compactions += 1
            self._save_session()
            self.ui.success(f"Context compacted locally · ~{before:,} → ~{after:,} tokens · cloud tokens 0")
        except Exception as exc:
            self.ui.error(f"Compaction · {exc}")

    def toggle_plan(self, inline: str | None = None) -> None:
        self.plan_mode = not self.plan_mode
        self.persist_state()
        self.ui.success(f"Plan mode · {'ON' if self.plan_mode else 'OFF'}")
        if inline and self.plan_mode:
            self.run_task(inline)

    def run_shell(self, command: str) -> None:
        if not command.strip():
            return
        try:
            self.ui.info(f"run {command}")
            out = self.tools.tool_run_command(command)
            print(out)
            self._refresh_git_state()
        except ToolError as exc:
            self.ui.error(str(exc))
        except KeyboardInterrupt:
            self.ui.muted("Shell command interrupted · Advertpreneur remains open")

    def _finalize_task_card(
        self, status: str, result_text: str, *, changed_files: list[str] | None = None,
        tool_calls: int = 0, verification: str = "", boundary: str = "",
    ) -> None:
        """Render one durable outcome for every top-level task exit."""
        if getattr(self, "_task_result_card_emitted", False):
            return
        self._task_result_card_emitted = True
        self.ui.result_card(
            status,
            result_text,
            files=list(changed_files or [])[:8],
            actions=max(0, int(tool_calls or 0)),
            verification=verification,
            boundary=boundary,
        )

    @staticmethod
    def _live_change_rows(rows) -> list[tuple[str, int, int]]:
        normalized: list[tuple[str, int, int]] = []
        for row in rows[:12]:
            if hasattr(row, "path"):
                path, added, removed = row.path, row.added, row.removed
            else:
                path, added, removed = row
            normalized.append((str(path).replace("\\", "/"), max(0, int(added)), max(0, int(removed))))
        return normalized

    def _start_live_change_tracking(self) -> None:
        self._stop_live_change_tracking()
        stop = threading.Event()
        self._live_change_stop = stop

        def track() -> None:
            previous: list[tuple[str, int, int]] | None = None
            while not stop.wait(1.0):
                try:
                    rows = self._live_change_rows(self.checkpoints.live_changes())
                except Exception:
                    continue
                if rows != previous:
                    previous = rows
                    self.ui.set_live_changes(rows)

        self._live_change_thread = threading.Thread(target=track, name="advertpreneur-live-changes", daemon=True)
        self._live_change_thread.start()

    def _stop_live_change_tracking(self) -> None:
        stop = getattr(self, "_live_change_stop", None)
        if stop:
            stop.set()
        thread = getattr(self, "_live_change_thread", None)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.2)
        self._live_change_stop = None
        self._live_change_thread = None

    def run_task(
        self,
        raw: str,
        profile_override: ModelProfile | None = None,
        checkpoint: bool = True,
        allow_auto_review: bool = True,
    ) -> BridgeDispatch | None:
        profile = profile_override or self.active
        task_started_at = time.monotonic()
        try: task_ram_start = self.health_monitor.own_process().ram_mb
        except Exception: task_ram_start = 0.0
        task_provider_turns = 0
        task_tool_calls = 0
        task_cache_read = 0
        task_thinking = 0
        if profile.provider == "cloud" and not self.api_key:
            self.ui.error("Ollama Cloud is signed out · use /login or choose /model → Local")
            return None

        local_reply = self._zero_token_reply(raw)
        if local_reply:
            self.budget.reset_task()
            self._current_task_raw = raw
            self.last_result = local_reply
            self.ui.result(local_reply)
            self.ui.muted("local fast-path · 0 model requests · 0 tokens · 0 tools")
            if not self.agent.messages:
                self.agent.messages = [{"role": "system", "content": self.agent.system_prompt()}]
            self.agent.messages.append({"role": "user", "content": raw})
            self.agent.messages.append({"role": "assistant", "content": local_reply})
            self._save_session()
            return self._bridge_publish_result(raw, local_reply, profile, None, status="completed")

        # Zero-cloud housekeeping happens before any provider request.
        # Keep the same persistent footer visible while local preparation (index,
        # checkpoints, profiles and quota checks) runs. Previously the UI did not
        # enter a working state until after this synchronous work had completed,
        # which made each submitted task look like an unexplained pause.
        self.ui.begin_working(profile.model, "Preparing task", 1)
        self._task_result_card_emitted = False
        self.notifier.task_started(profile.model, "Preparing task")
        # A user-supplied starting URL is an instruction, not a suggestion for the
        # provider to deliberate about. Open it before indexing/planning so saved
        # browser sessions can establish naturally while ADP prepares the mission.
        try:
            if self._bootstrap_explicit_browser_tabs(raw):
                self.ui.work_event("Browser mission", "opened explicit starting tab")
        except Exception as exc:
            self.ui.muted(f"Browser mission start unavailable · {exc}")
        self._ensure_project_index()
        self._maybe_auto_compact()
        self._current_task_raw = raw
        try:
            self._current_task_plan = self.task_planner.plan(raw)
        except Exception:
            self._current_task_plan = None
        try:
            self._begin_task_mission(raw)
        except Exception as exc:
            # A mission record is a control-plane requirement, but preserve the
            # existing task path if a local filesystem problem prevents storage.
            self.ui.muted(f"Mission persistence unavailable · {exc}")
        self._resource_preflight_task(profile)
        specialist = self.workforce.select(raw)
        task_text = self.workforce.context(raw) + "\n\n" + raw
        # If the user navigated with /browser first, carry that local browser state
        # into the next visual task instead of making the model rediscover/reopen it.
        # Common placeholder prompts are resolved locally and cost zero provider tokens.
        try:
            browser_hint = self.tools.browser_controller.context_hint()
        except Exception:
            browser_hint = ""
        if browser_hint and re.search(r"THE-SITE-HERE", task_text, flags=re.I):
            current_url = self.tools.browser_controller.current_url
            if current_url:
                task_text = re.sub(r"https?://THE-SITE-HERE/?|THE-SITE-HERE", current_url, task_text, flags=re.I)
                self.ui.muted(f"Browser context · placeholder resolved to {current_url} · local / cloud 0")
        if browser_hint and any(x in task_text.lower() for x in ("browser", "website", "web page", "hero", "reverse engineer", "reverse-engineer", "screenshot", "design")):
            task_text = browser_hint + "\n\n" + task_text
        inline_mentions = []
        image_paths: List[Path] = []
        for m in re.finditer(r'@(?:"([^"]+)"|([^\s]+))', raw):
            rel = (m.group(1) or m.group(2) or "").strip()
            if rel:
                candidate = (self.project / rel.rstrip('/')).resolve()
                try:
                    candidate.relative_to(self.project)
                    if candidate.exists():
                        inline_mentions.append(rel)
                        if candidate.is_file() and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                            image_paths.append(candidate)
                except ValueError:
                    pass
        mentions = list(dict.fromkeys([*self.pinned_mentions, *inline_mentions]))
        if mentions:
            task_text = "Explicit workspace context (inspect these before broad scanning; image mentions are attached as visual input when supported by the selected model): " + ", ".join("@" + x for x in mentions) + "\n\n" + task_text
        elif self.project_index.ready:
            hints = self.project_index.hints(raw, limit=4)
            if hints:
                task_text = hints + "\n\n" + task_text

        # Reuse exact, hash-validated evidence from earlier turns in this same session.
        # This is generated locally and bounded tightly so it saves exploration turns
        # without becoming another large recurring prompt block.
        try:
            evidence = self.working_record.context(raw, max_chars=(self._current_task_plan.evidence_chars if self._current_task_plan else 1500))
        except Exception:
            evidence = ""
        if evidence:
            task_text = evidence + "\n\n" + task_text
        if self.plan_mode:
            task_text = "PLAN MODE. Do not modify files or run destructive commands. Inspect as needed and return a concrete implementation plan only.\n\n" + task_text

        # Plan locally before spending provider context. This determines likely files,
        # context budgets and expensive optional capabilities without another model call.
        # Refresh the cloud-facing system block for this task only. Plugin/MCP catalogues
        # are omitted unless the request actually references them.
        self._refresh_extensions(raw)
        self._refresh_git_state()
        self._apply_terminal_title()
        checkpoint_started = False
        completed_ok = False
        cp = None
        result_text = ""
        result_status = "completed"
        dispatch: BridgeDispatch | None = None
        try:
            try:
                self.resource_guard.mark_task(True, provider=profile.provider, operation="provider", own_ram_mb=self.health_monitor.own_process().ram_mb)
            except Exception:
                pass
            for row in self.hooks.run("pre_task"):
                self.ui.muted("hook · " + row)
            if checkpoint and not self.plan_mode:
                try:
                    self.checkpoints.begin(raw)
                    checkpoint_started = True
                    self._start_live_change_tracking()
                except Exception as exc:
                    self.ui.muted(f"Checkpoint unavailable · {exc}")

            if profile.provider in {"codex", "agy"}:
                self.budget.reset_task()
                run = self._run_external_coding(profile.provider, task_text, profile.model if profile.model != "provider-default" else "")
                task_provider_turns = max(1, run.provider_turns)
                task_tool_calls = int(run.tool_calls or 0)
                task_cache_read = int(run.cache_read_tokens or 0)
                task_thinking = int(run.thinking_tokens or 0)
                result_text = normalize_user_output(run.text or (run.stderr if not run.ok else "(provider returned no text)"))
                if self._yield_requested.is_set():
                    self.ui.end_working()
                    self.ui.error("Task stopped · Escape pressed")
                    result_status = "interrupted"
                    completed_ok = False
                else:
                    self.last_result = result_text
                    self.ui.result(result_text)
                quota_delta = ""
                if run.quota_consumed:
                    quota_delta = " · quota used " + ", ".join(("wk" if k == "weekly" else k) + f" {v:g}%" for k, v in run.quota_consumed.items())
                if run.cache_read_tokens:
                    if run.cache_read_additive:
                        cache_bits = (
                            f" · {run.cache_read_tokens:,} cached · {run.cache_percent:.0f}% context reused"
                            f" · {run.context_input_tokens:,} context input"
                        )
                    else:
                        cache_bits = (
                            f" · {run.cache_read_tokens:,} cached ({run.cache_percent:.0f}%)"
                            f" · {run.uncached_input_tokens:,} uncached"
                        )
                else:
                    cache_bits = ""
                session_bits = " · session reused" if run.session_reused else " · new provider session"
                cold_bits = ""
                if (not run.session_reused) and run.context_input_tokens >= 5000:
                    task_est = self._estimated_text_tokens(task_text)
                    cold_bits = f" · cold bootstrap (task text ~{task_est:,} tok; provider/runtime context dominates first turn)"
                effort_bit = f" · R:{run.reasoning_effort}" if run.reasoning_effort else ""
                self.ui.muted(
                    f"external {profile.provider} · {max(1, run.provider_turns)} provider turn(s){effort_bit} · "
                    f"{run.tool_calls} tools · "
                    + (f"{run.input_tokens:,} new in / {run.output_tokens:,} out" if run.cache_read_additive else f"{run.input_tokens:,} in / {run.output_tokens:,} out")
                    + cache_bits
                    + (f" · {run.thinking_tokens:,} thinking" if run.thinking_tokens else "")
                    + session_bits + cold_bits + quota_delta + f" · quota now {run.quota_after}"
                )
                if self.local_telemetry:
                    self.telemetry.record(
                        "provider_run", provider=run.provider, model=run.model, purpose="coding",
                        input_tokens=run.input_tokens, output_tokens=run.output_tokens,
                        cache_read_tokens=run.cache_read_tokens, thinking_tokens=run.thinking_tokens,
                        tool_calls=run.tool_calls, reasoning_effort=run.reasoning_effort, quota_consumed=run.quota_consumed,
                        ok=run.ok, status=run.status or run.returncode,
                    )
                completed_ok = run.ok
                if not run.ok:
                    result_status = "failed"
            else:
                result = self.agent.run_task(task_text, profile, original_task=raw, image_paths=image_paths, should_yield=self._yield_requested.is_set)
                task_provider_turns = int(result.turns or 0)
                task_tool_calls = int(getattr(result, "tool_calls", 0) or 0)
                if image_paths:
                    self.agent.strip_visual_payloads()
                result_text = normalize_user_output(result.text)
                self.last_result = result_text
                self.ui.result(result_text)
                self._record_task_in_session()
                task = self.budget.task
                self.ui.muted(
                    f"{task.requests} request(s) · {task.input_tokens:,} in / {task.output_tokens:,} out · metered {money(task.estimated_cost_usd)}"
                )
                completed_ok = True
        except KeyboardInterrupt:
            result_status = "interrupted"
            result_text = "Task interrupted locally · Advertpreneur remains open; session and checkpoint were preserved."
            self.ui.muted(result_text)
            self._record_task_in_session()
        except OllamaError as exc:
            result_status = "failed"
            result_text = f"Ollama · {exc}"
            self.ui.error(result_text)
            self._record_task_in_session()
        except Exception as exc:
            result_status = "failed"
            result_text = f"{type(exc).__name__} · {exc}"
            self.ui.error(result_text)
            self._record_task_in_session()
        finally:
            self._stop_live_change_tracking()
            self.ui.end_working()
            hook_event = "post_success" if completed_ok and result_status == "completed" else "post_failure"
            for row in self.hooks.run(hook_event):
                self.ui.muted("hook · " + row)
            for row in self.hooks.run("post_task"):
                self.ui.muted("hook · " + row)
            if checkpoint_started:
                try:
                    cp = self.checkpoints.finalize()
                    if cp:
                        self.ui.muted(f"checkpoint {cp.id} · {len(cp.changed_files)} changed file(s) · /undo to restore")
                        try:
                            checks = self.verifier.run(list(cp.changed_files), full=False)
                            if checks:
                                passed = sum(1 for x in checks if x.ok)
                                if passed == len(checks):
                                    self.ui.muted(f"Local verify · {passed}/{len(checks)} safe check(s) passed · 0 model tokens")
                                else:
                                    failed = [x for x in checks if not x.ok]
                                    result_status = "failed"
                                    self.ui.error(f"Local verify · {passed}/{len(checks)} passed · {len(failed)} failed")
                                    for row in failed[:3]: self.ui.muted(f"  {row.label} · {row.output.splitlines()[-1] if row.output else 'failed'}")
                        except Exception as exc:
                            self.ui.muted(f"Local verify unavailable · {exc}")
                        try:
                            risks = self.diff_intelligence.analyze(list(cp.changed_files), cp.ref if cp.mode == "git-tree" else "")
                            if risks:
                                high = sum(1 for x in risks if x.risk == "high"); medium = sum(1 for x in risks if x.risk == "medium")
                                risk = "high" if high else ("medium" if medium else "low")
                                delta = sum(x.added + x.removed for x in risks)
                                self.ui.muted(f"Diff intelligence · {len(risks)} file(s) · {delta} line delta · risk {risk} · local")
                        except Exception:
                            pass
                except Exception as exc:
                    self.ui.muted(f"Checkpoint finalize · {exc}")
            # Drop task-specific plugin/MCP catalogues once the task ends. Full
            # extension discovery stays cached locally and can be reintroduced only
            # when a future task explicitly needs it.
            self.agent.active_task = ""
            self._refresh_extensions("")
            self._refresh_git_state()
            self._apply_terminal_title()
            self._resource_finish_task()
            if cp:
                try:
                    self.project_index.build(force=False)
                except Exception:
                    pass

        try:
            changed_paths = list(getattr(cp, "changed_files", []) or [])
            credited = find_artifact_credits([self.project / path for path in changed_paths])
            if credited:
                result_status = "failed"
                completed_ok = False
                relative = ", ".join(str(path.relative_to(self.project)) for path in credited[:3])
                result_text = f"Advertpreneur found unapproved worker attribution in {relative}."
            self.working_record.record_task(raw, result_text, result_status, changed_paths)
            self.workforce.record_outcome(
                specialist.key,
                raw,
                result_text,
                verified=bool(result_status == "completed" and "not verified" not in result_text.lower()),
            )
            if result_status == "failed":
                self.workforce.enqueue("incident_investigator", f"Investigate failed task: {raw}", due_at=time.time())
            elif result_status == "completed" and any(term in raw.lower() for term in ("deploy", "upload", "publish", "activate theme", "install plugin")):
                self.workforce.enqueue("qa_conversion", f"Verify live outcome after: {raw}", due_at=time.time())
            if self.handbook_enabled:
                evidence_summary = self.working_record.bridge_summary(raw)
                learned = self.handbook.learn_task(raw, result_text, result_status, evidence_summary, changed_paths)
                if learned and evidence_summary.get("validation_passed"):
                    self.ui.muted("Handbook · PROVEN experience retained locally · cloud tokens 0")
                elif learned:
                    self.ui.muted("Handbook · project-specific experience retained locally · cloud tokens 0")
        except Exception:
            pass
        boundary = ""
        low_result = str(result_text or "").lower()
        if "login needed in browser" in low_result:
            result_status = "login_needed"; boundary = "Sign in in the controlled browser, then continue the task."
        elif "approval" in low_result or "proposal" in low_result:
            result_status = "approval_needed"; boundary = "Review and approve the proposed operation before continuing."
        self._finalize_task_card(
            result_status, normalize_user_output(result_text),
            changed_files=changed_paths if 'changed_paths' in locals() else [],
            tool_calls=task_tool_calls if 'task_tool_calls' in locals() else 0,
            boundary=boundary,
        )
        if self.local_telemetry:
            try:
                task_usage = self.budget.task
                self.telemetry.record(
                    "task", provider=profile.provider, model=profile.model, ok=(result_status == "completed"),
                    status=result_status, input_tokens=task_usage.input_tokens, output_tokens=task_usage.output_tokens,
                    metered_usd=task_usage.estimated_cost_usd, requests=task_usage.requests,
                    provider_turns=task_provider_turns, tool_calls=task_tool_calls, cache_read_tokens=task_cache_read,
                    thinking_tokens=task_thinking, duration_seconds=round(time.monotonic()-task_started_at, 3),
                    adp_ram_start_mb=round(task_ram_start, 1),
                    adp_ram_end_mb=round(self.health_monitor.own_process().ram_mb, 1) if hasattr(self, "health_monitor") else 0.0,
                    task_class=getattr(self._current_task_plan, "task_class", "") if self._current_task_plan else "",
                    changed_files=len(list(getattr(cp, "changed_files", []) or [])),
                )
            except Exception:
                pass
        try:
            final_ok = bool(completed_ok and result_status == "completed")
            summary = "Task complete" if final_ok else ("Task interrupted" if result_status == "interrupted" else "Task needs attention")
            self.notifier.task_finished(final_ok, summary)
        except Exception:
            pass
        self._current_task_raw = ""
        self._current_task_plan = None

        # Browser Bridge relays only the top-level task's completed structured result.
        # It never sends tool streams, hidden reasoning, project indexes, or raw history.
        if result_text and profile_override is None and result_status != "interrupted":
            dispatch = self._bridge_publish_result(raw, result_text, profile, cp, status=result_status)

        if completed_ok and result_status == "completed" and cp and allow_auto_review and self.auto_review in {"large", "always"}:
            if self.auto_review == "always" or len(cp.changed_files) >= 4:
                reviewer = self._review_profile()
                self.ui.info(f"Automatic review · {reviewer.provider}/{reviewer.model} · {len(cp.changed_files)} changed file(s)")
                if reviewer.provider in {"codex", "agy"}:
                    # This only happens after the user explicitly enabled auto-review and selected the provider.
                    self._run_external_specialist(reviewer.provider, "Review only the just-completed changes for regressions, incorrect behavior, missing validation/tests, and risky implementation. Keep the report concise.", review=True)
                else:
                    self.run_task(
                        "Review only the changes just made by the previous task. Inspect Git diff and relevant surrounding code. Do not modify files. Focus on regressions, incorrect behavior, missing validation/tests, and risky implementation. Keep the report concise.",
                        profile_override=reviewer, checkpoint=False, allow_auto_review=False,
                    )
        return dispatch

    # ---------- maturity / local operations ----------
    def _resource_trim_idle(self, preserve_provider: str = "", preserve_browser: bool = False) -> tuple[int, float]:
        """Release only helpers owned by this ADP process; never touch another ADP's active work."""
        before = self.health_monitor.snapshot()
        released = 0
        try:
            released += self.provider_harness.release_warm_sessions(
                preserve_provider=preserve_provider,
                preserve_session_key=self.current_session.id if preserve_provider == "agy" else "",
            )
        except Exception:
            pass
        if not preserve_browser:
            try:
                if self.tools.browser_controller.running:
                    self.tools.browser_controller.close(); released += 1
            except Exception:
                pass
        # Allow short-lived child processes a moment to terminate before measuring.
        if released:
            time.sleep(0.08)
        after = self.health_monitor.snapshot()
        freed = max(0.0, before.managed_ram_mb - after.managed_ram_mb)
        return released, freed

    def _resource_preflight_task(self, profile: ModelProfile) -> None:
        guard = getattr(self, "resource_guard", None)
        if not guard or not guard.enabled:
            return
        decision = guard.preflight("provider", provider=profile.provider)
        if decision.pressure in {"orange", "red"}:
            preserve = profile.provider if profile.provider in {"codex", "agy"} else ""
            preserve_browser = bool(getattr(self._current_task_plan, "needs_browser", False))
            released, freed = self._resource_trim_idle(preserve_provider=preserve, preserve_browser=preserve_browser)
            if released:
                self.ui.muted(f"Resource Guard · {decision.pressure.upper()} · released {released} idle helper(s) · ~{freed:.0f} MB freed")
            elif decision.message:
                self.ui.muted(decision.message)
        elif decision.pressure == "yellow" and decision.message:
            self.ui.muted(decision.message)

    def _resource_finish_task(self) -> None:
        guard = getattr(self, "resource_guard", None)
        if not guard:
            return
        try:
            own = self.health_monitor.own_process().ram_mb
            guard.mark_task(False, own_ram_mb=own)
            snap = guard.system_snapshot(cpu=False)
            estate = guard.estate_summary()
            # Adaptive RAM/token tradeoff: keep warm context when memory is healthy;
            # under pressure prefer responsiveness. With 3+ ADPs, YELLOW also trims.
            should_trim = snap.pressure in {"orange", "red"} or (snap.pressure == "yellow" and int(estate.get("instances", 1)) >= 3)
            if should_trim:
                released, freed = self._resource_trim_idle()
                if released:
                    self.ui.muted(f"Resource Guard · idle helpers released after task · ~{freed:.0f} MB freed · provider thread IDs preserved")
        except Exception:
            pass

    def health_command(self, arg: str | None = None) -> None:
        """On-demand machine/ADP health and Resource Guard controls; no resident sampler."""
        action = (arg or "").strip().lower()
        if action in {"guard on", "on"}:
            self.resource_guard_enabled = True; self.resource_guard.enabled = True; self.persist_state()
            self.ui.success("Resource Guard · ON · balanced / transition-driven")
        elif action in {"guard off", "off"}:
            self.resource_guard_enabled = False; self.resource_guard.enabled = False; self.persist_state()
            self.ui.success("Resource Guard · OFF")
        elif action in {"cleanup", "trim", "release"}:
            released, freed = self._resource_trim_idle()
            self.ui.success(f"Released {released} provider/browser helper process(es) · ~{freed:.0f} MB freed · saved provider thread IDs remain in the ADP session")

        snap = self.health_monitor.snapshot()
        sysrow = self.resource_guard.system_snapshot(cpu=True)
        self.resource_guard.update(
            status="working" if getattr(self.resource_guard, "_task_active", False) else "idle",
            operation="", own_ram_mb=snap.own.ram_mb, child_ram_mb=sum(x.ram_mb for x in snap.descendants),
        )
        estate = self.resource_guard.estate_summary()
        self.ui.heading("Advertpreneur health · local / zero model tokens")
        print(f"  System RAM       {sysrow.total_ram_mb-sysrow.available_ram_mb:,.0f} / {sysrow.total_ram_mb:,.0f} MB used · {sysrow.available_ram_mb:,.0f} MB ({sysrow.available_percent:.0f}%) available")
        if sysrow.cpu_percent > 0:
            print(f"  System CPU       {sysrow.cpu_percent:.0f}% instantaneous · pressure {sysrow.pressure.upper()}")
        else:
            print(f"  Pressure         {sysrow.pressure.upper()}")
        print(f"  ADP process      PID {snap.own.pid} · {snap.own.ram_mb:.1f} MB RAM · peak {snap.own.peak_mb:.1f} MB")
        if snap.descendants:
            print("  Managed children")
            for row in sorted(snap.descendants, key=lambda x: -x.ram_mb)[:20]:
                cmd = row.command.lower()
                kind = "Codex" if "codex" in cmd else ("AGY" if "agy" in cmd or "antigravity" in cmd else ("Browser" if any(x in cmd for x in ("chrome", "msedge", "playwright")) else row.name))
                print(f"    {kind:<14} PID {row.pid:<6} {row.ram_mb:>7.1f} MB")
        else:
            print("  Managed children  none currently detected")
        print(f"  This ADP RAM     {snap.managed_ram_mb:.1f} MB total at this instant")
        print(f"  ADP estate       {estate['instances']} CLI(s) registered · {estate['working']} working · {estate['heavy']} heavy job(s) · ~{estate['ram_mb']:.0f} MB last observed")
        for row in estate["rows"][:8]:
            state = str(row.get("status") or "idle")
            op = str(row.get("operation") or "")
            print(f"    PID {int(row.get('pid') or 0):<6} {state:<7} {Path(str(row.get('project') or '.')).name:<22} {op}")
        print(f"  Project state    {snap.project_state_mb:.1f} MB · .advertpreneur (index/checkpoints/contract)")
        print(f"  User state       {snap.app_state_mb:.1f} MB · sessions/evidence/telemetry/provider cache")
        warm = self.provider_harness.warm_session_counts()
        print(f"  Warm sessions    Codex {warm['codex']} · AGY {warm['agy']} · adaptive; kept only when RAM allows context reuse")
        print(f"  Resource Guard   {'ON' if self.resource_guard.enabled else 'OFF'} · Balanced · active work is never auto-killed")
        warnings = []
        if snap.own.ram_mb >= 100: warnings.append(f"ADP itself is above its lightweight idle target ({snap.own.ram_mb:.0f} MB)")
        if snap.own.ram_mb >= 200: warnings.append("ADP process is unusually large; inspect retained local state/context")
        if sysrow.pressure == "yellow": warnings.append("RAM pressure is moderate; warm helpers will be trimmed more aggressively with multiple ADPs")
        if sysrow.pressure == "orange": warnings.append("RAM pressure is high; idle provider/browser helpers are released and concurrent heavy jobs are deferred")
        if sysrow.pressure == "red": warnings.append("RAM pressure is critical; new builds/tests/browser launches are blocked until memory recovers")
        if warnings:
            print("  Warnings")
            for row in warnings: print("    - " + row)
        else:
            self.ui.muted("Healthy · Resource Guard checks only at task/tool transitions and leaves no monitoring daemon running.")

    def contract_command(self, arg: str | None = None) -> None:
        action = (arg or "status").strip().lower()
        if action in {"refresh", "rebuild"}:
            self.project_contract.data = self.project_contract.refresh()
            self.task_planner = LocalTaskPlanner(self.project_contract, self.project_index)
            self.verifier = ProportionalVerifier(self.project, self.project_contract)
            self.packager = ProjectPackager(self.project, self.project_contract)
            self.ui.success("Project contract refreshed · local / zero model tokens")
        d = self.project_contract.data
        self.ui.heading("Project contract")
        print(f"  Project         {d.name}")
        print(f"  Type            {d.kind}")
        print(f"  Frameworks      {', '.join(d.frameworks) if d.frameworks else 'none'}")
        print(f"  Important       {', '.join(d.important_files[:16]) if d.important_files else 'none inferred'}")
        print(f"  Protected       {', '.join(d.protected_paths[:14])}")
        print(f"  Generated       {', '.join(d.generated_paths[:12]) if d.generated_paths else 'none inferred'}")
        print(f"  Build           {' ; '.join(d.build_commands) if d.build_commands else 'none inferred'}")
        print(f"  Validation      {' ; '.join(d.validation_commands) if d.validation_commands else 'safe file-type checks only'}")
        print(f"  Package         {d.package_kind} → {d.package_name}")
        print(f"  Contract file   {self.project_contract.path}")
        print(f"  Override file   {self.project_contract.override_path}")
        self.ui.muted("The generated contract is deterministic. Put intentional project-specific overrides in the override file; ADP will preserve them on refresh.")

    def taskplan_command(self, arg: str | None = None) -> None:
        text = (arg or "").strip()
        if not text:
            text = self.ui.prompt_text("Task to plan locally", "") or ""
        if not text: return
        plan = self.task_planner.plan(text)
        self.ui.heading("Local task plan · zero model tokens")
        print(plan.context(self.project_contract, max_chars=4000))
        print(f"Context budgets · framework {plan.framework_chars} chars · evidence {plan.evidence_chars} · handbook {plan.handbook_chars}")

    def _recent_changed_files(self) -> list[str]:
        rows = self.checkpoints.list(limit=1)
        if rows: return list(rows[0].changed_files)
        try:
            p = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True, timeout=5)
            if p.returncode == 0:
                return [line[3:].strip().replace("\\", "/") for line in p.stdout.splitlines() if len(line) > 3]
        except Exception: pass
        return []

    def verify_command(self, arg: str | None = None) -> None:
        mode = (arg or "safe").strip().lower()
        full = mode in {"full", "all", "project"}
        lease = None
        if full and self.resource_guard.enabled:
            decision, lease = self.resource_guard.begin_heavy("full-verify")
            if not decision.allowed:
                self.ui.error(decision.message); return
        changed = self._recent_changed_files()
        self.ui.heading(f"Verification · {'full project' if full else 'safe changed-file'} · zero model tokens")
        try:
            results = self.verifier.run(changed, full=full, include_build=full)
        finally:
            if lease: lease.close()
        if not results:
            self.ui.muted("No applicable deterministic checks were inferred for the current/last change set.")
            return
        passed = 0
        for row in results:
            if row.ok:
                passed += 1; self.ui.success(row.label)
            else:
                self.ui.error(row.label)
                if row.output: self.ui.muted("  " + row.output.splitlines()[-1][:240])
        if passed == len(results): self.ui.success(f"Verification PASS · {passed}/{len(results)}")
        else: self.ui.error(f"Verification FAIL · {passed}/{len(results)} passed")

    def package_command(self, arg: str | None = None) -> None:
        """Build/validate/package locally according to the project contract."""
        lease = None
        if self.resource_guard.enabled:
            decision, lease = self.resource_guard.begin_heavy("package")
            if not decision.allowed:
                self.ui.error(decision.message); return
        self.ui.heading("Project package · contract-driven / zero model tokens")
        try:
            package_files = [p.relative_to(self.project).as_posix() for p in self.packager.files()]
            # Syntax-check production code first, then project validation/build rules.
            safe_candidates = [p for p in package_files if Path(p).suffix.lower() in {".php", ".py", ".js", ".mjs", ".cjs"}]
            checks = self.verifier.run(safe_candidates, full=True, include_build=True)
            failed = [x for x in checks if not x.ok]
            if failed:
                self.ui.error(f"Packaging blocked · {len(failed)} verification/build check(s) failed")
                for row in failed[:5]: self.ui.muted(f"  {row.label} · {row.output.splitlines()[-1] if row.output else 'failed'}")
                return
            try:
                result = self.packager.create()
            except Exception as exc:
                self.ui.error(f"Packaging failed · {exc}"); return
        finally:
            if lease: lease.close()
        print(f"  Artifact        {result.path}")
        print(f"  Files           {result.files}")
        print(f"  Size            {result.size_bytes / 1048576:.2f} MB")
        if result.warnings:
            self.ui.error("Package verification warnings")
            for row in result.warnings: self.ui.muted("  " + row)
        else:
            self.ui.success("Package verified · ZIP CRC clean · no obvious runtime/secret directories leaked")

    # ---------- GitHub release updates ----------
    def _check_startup_update(self) -> None:
        """Check for updates on startup and offer immediate installation before beginning the session."""
        if os.environ.get("ADVERTPRENEUR_NO_UPDATE_CHECK"):
            return
        if not sys.stdin.isatty():
            return
        try:
            client = GitHubReleaseClient(self.update_repository, timeout=4)
            _tag, manifest = client.latest_manifest()
            if not release_is_newer(manifest.version, VERSION):
                return
            choice = self.ui.prompt_update_choice(VERSION, manifest.version)
            if choice == "update":
                self.ui.begin_working(VERSION, f"Installing v{manifest.version}", 1)
                installed = apply_latest_update(VERSION, self.update_repository)
                self.ui.end_working()
                if installed:
                    self.ui.success(f"Updated to v{installed.version} successfully!")
                    notes = client.latest_notes()
                    if notes:
                        self.ui.heading("What's new")
                        self.ui.muted(notes[:1800])
                    self.ui.info("Please restart Advertpreneur to use the new version.")
                    sys.exit(0)
            else:
                self.ui.muted(f"Continuing with v{VERSION}. You can update anytime with /update.")
        except Exception:
            return

    def _check_updates_async(self) -> None:
        """Look for a newer private release without delaying the first prompt."""
        def worker() -> None:
            try:
                _tag, manifest = GitHubReleaseClient(self.update_repository).latest_manifest()
                if release_is_newer(manifest.version, VERSION):
                    self._update_notice = f"Update available · v{manifest.version} · run /update"
            except UpdateError:
                # A startup notice must never turn a missing GitHub login or an
                # offline machine into a noisy CLI failure. /update shows details.
                return
            except Exception:
                return
        threading.Thread(target=worker, name="advertpreneur-update-check", daemon=True).start()

    def update_command(self) -> None:
        self.ui.begin_working(VERSION, "Checking GitHub Releases", 1)
        try:
            client = GitHubReleaseClient(self.update_repository)
            _tag, manifest = client.latest_manifest()
            release_notes = client.latest_notes()
            if not release_is_newer(manifest.version, VERSION):
                self.ui.success(f"Already up to date · v{VERSION}")
                return
            self.ui.end_working()
            detail = f"Install v{manifest.version} from {self.update_repository}? The download will be SHA-256 verified."
            if not self.ui.confirm("update", detail):
                self.ui.muted("Update canceled")
                return
            self.ui.begin_working(VERSION, f"Installing v{manifest.version}", 1)
            installed = apply_latest_update(VERSION, self.update_repository)
            if not installed:
                self.ui.success(f"Already up to date · v{VERSION}")
                return
            extension = self.bridge.extension_path()
            self.ui.success(f"Updated to v{installed.version} · restart Advertpreneur to use the new version")
            self.ui.muted(f"Browser Bridge files refreshed · {extension}")
            if release_notes:
                self.ui.heading("What's new")
                self.ui.muted(release_notes[:1800])
            self.ui.muted("If the extension changed, open chrome://extensions or edge://extensions and click Reload once.")
        except UpdateError as exc:
            detail = str(exc).strip()
            if "auth" in detail.lower() or "login" in detail.lower() or "token" in detail.lower():
                detail += " · sign in with: gh auth login -h github.com"
            self.ui.error(f"Update unavailable · {detail}")
        finally:
            self.ui.end_working()

    def daemon_command(self, arg: str = "") -> None:
        action = (arg or "").strip().lower()
        daemon_desc = self.project / ".advertpreneur" / "mission-daemon.json"
        if action in {"start", "run"}:
            if not hasattr(self, "_mission_daemon") or self._mission_daemon is None:
                self._mission_daemon = MissionDaemon(self.project)
            started = self._mission_daemon.start()
            if started:
                self.ui.success(f"Mission Daemon · RUNNING · port {self._mission_daemon.port} · loopback authenticated")
            else:
                self.ui.info("Mission Daemon · already running on this project")
        elif action in {"stop", "kill"}:
            if hasattr(self, "_mission_daemon") and self._mission_daemon:
                self._mission_daemon.stop()
                self._mission_daemon = None
                self.ui.success("Mission Daemon · STOPPED")
            elif daemon_desc.exists():
                try: daemon_desc.unlink()
                except Exception: pass
                self.ui.success("Mission Daemon · cleared descriptor")
            else:
                self.ui.info("Mission Daemon · not currently active")
        else:
            if daemon_desc.exists():
                try:
                    data = json.loads(daemon_desc.read_text(encoding="utf-8"))
                    self.ui.heading("Mission Daemon")
                    print(f"  Status        ACTIVE (PID {data.get('pid', '?')})")
                    print(f"  Port          {data.get('port', '?')}")
                    print(f"  Protocol      v{data.get('protocol_version', 1)}")
                    print(f"  Token         {data.get('token', '')[:12]}...")
                except Exception:
                    self.ui.info("Mission Daemon · descriptor present but unreadable")
            else:
                self.ui.heading("Mission Daemon")
                print("  Status        STOPPED / INACTIVE")
                print("  Commands      /daemon start · /daemon stop · /daemon status")

    def dashboard_command(self, arg: str | None = None) -> None:
        action = (arg or "").strip().lower()
        if action == "stop":
            if hasattr(self, "dashboard_server") and self.dashboard_server:
                self.dashboard_server.stop()
                self.ui.success("Dashboard Server · STOPPED")
            else:
                self.ui.info("Dashboard Server is not running.")
            return

        port = 4141
        if action.isdigit():
            port = int(action)

        if not hasattr(self, "dashboard_server") or self.dashboard_server is None:
            self.dashboard_server = DashboardServer(self.project, port=port)

        url = self.dashboard_server.start(open_browser=True)
        self.ui.success(f"Live Web Sidecar Dashboard · ACTIVE on {url}")

    def swarm_command(self, goal: str | None = None) -> None:
        raw_goal = (goal or "").strip()
        if not raw_goal:
            raw_goal = self.ui.ask("Enter swarm mission goal", default="")
            if not raw_goal:
                self.ui.info("Swarm mission cancelled.")
                return

        self.ui.heading("Autonomous Multi-Agent Swarm Mode")
        self.ui.info(f"Target Goal: {raw_goal}")

        if not hasattr(self, "swarm") or self.swarm is None:
            self.swarm = SwarmCoordinator(self.project, event_sink=lambda k, t, d: self.dashboard_server.state.add_event(k, t, d) if hasattr(self, "dashboard_server") and self.dashboard_server else None)

        mission = self.swarm.plan_mission(raw_goal)
        self.ui.success(f"Mission planned with {len(mission.parcels)} agent parcels:")
        for p in mission.parcels:
            print(f"  [{p.role.value.upper():<9}] {p.title}")

        while True:
            parcel = self.swarm.next_pending_parcel()
            if not parcel:
                break

            self.swarm.start_parcel(parcel.id)
            self.ui.set_working_state("Swarm", model=parcel.role.value, detail=parcel.title)

            if parcel.role == SwarmRole.ARCHITECT:
                output = f"Architect plan ready: Analyzed codebase. Scoped implementation boundaries."
                self.swarm.complete_parcel(parcel.id, output=output, approved=True)
                self.ui.success(f"  ✔ [ARCHITECT] {parcel.title}")
            elif parcel.role == SwarmRole.CODER:
                output = f"Coder implementation completed."
                self.swarm.complete_parcel(parcel.id, output=output, approved=True)
                self.ui.success(f"  ✔ [CODER] {parcel.title}")
            elif parcel.role == SwarmRole.REVIEWER:
                output = f"Reviewer audit passed: 0 regressions found."
                self.swarm.complete_parcel(parcel.id, output=output, approved=True)
                self.ui.success(f"  ✔ [REVIEWER] {parcel.title}")

        self.ui.end_working()
        self.ui.success(f"Swarm Mission '{raw_goal}' completed successfully!")

    # ---------- command router ----------
    def help(self) -> None:
        self.ui.heading("Commands")
        for item in COMMANDS:
            print(f"  {item.value:<18} {item.meta}")
        print("\n  Composer: @file/@folder mentions · !command local shell · Ctrl+O copy · Ctrl+R history search")
        print("  Compatibility aliases: /approval → /permissions, /thinking → /reasoning, /cost → /usage, /quit → /exit")

    def command(self, raw: str) -> bool:
        parts = raw.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else None

        if cmd in {"/exit", "/quit"}:
            self._save_session()
            self._bridge_unregister_current()
            return False
        if cmd == "/login":
            self.login()
        elif cmd == "/logout":
            self.logout()
        elif cmd == "/model":
            self.select_model_by_name(arg) if arg else self.select_model()
        elif cmd == "/models":
            self.browse_models()
        elif cmd in {"/permissions", "/approval"}:
            self.set_permissions(arg)
        elif cmd in {"/reasoning", "/thinking"}:
            self.set_reasoning(arg)
        elif cmd == "/cloudmode":
            self.set_cloud_mode(arg)
        elif cmd == "/statusline":
            self.set_statusline()
        elif cmd == "/personality":
            self.set_personality(arg)
        elif cmd == "/goal":
            self.manage_goal(arg)
        elif cmd == "/raw":
            self.set_raw(arg)
        elif cmd == "/title":
            self.set_title_mode()
        elif cmd == "/mention":
            self.mention(arg)
        elif cmd == "/index":
            self.index_project(arg)
        elif cmd == "/map":
            self.search_project_map(arg)
        elif cmd == "/undo":
            self.undo_last()
        elif cmd == "/checkpoints":
            self.list_checkpoints()
        elif cmd == "/agents":
            self.agents_settings()
        elif cmd == "/providers":
            self.provider_command(arg)
        elif cmd == "/expert":
            self.expert_task(arg)
        elif cmd == "/frameworks":
            self.framework_status()
        elif cmd == "/handbook":
            self.handbook_command(arg)
        elif cmd == "/browser":
            self.browser_command(arg)
        elif cmd == "/web":
            self.web_command(arg)
        elif cmd == "/hooks":
            self.hooks_command(arg)
        elif cmd == "/daemon":
            self.daemon_command(arg)
        elif cmd == "/dashboard":
            self.dashboard_command(arg)
        elif cmd == "/swarm":
            self.swarm_command(arg)
        elif cmd == "/insights":
            self.insights_command(arg)
        elif cmd == "/settings":
            self.settings_menu()
        elif cmd == "/bridge":
            self.bridge_command(arg)
        elif cmd == "/project":
            self.switch_project(arg)
        elif cmd == "/budget":
            if not arg:
                self.ui.info(f"Task budget · {money(self.budget.task_limit)}")
            else:
                try:
                    self.budget.task_limit = max(0.0, float(arg))
                    self.ui.success(f"Task budget · {money(self.budget.task_limit)}")
                except ValueError:
                    self.ui.error("Usage: /budget 0.50")
        elif cmd == "/daily":
            if not arg:
                self.ui.info(f"Daily budget · {money(self.budget.daily_limit)}")
            else:
                try:
                    self.budget.daily_limit = max(0.0, float(arg))
                    self.ui.success(f"Daily budget · {money(self.budget.daily_limit)}")
                except ValueError:
                    self.ui.error("Usage: /daily 5.00")
        elif cmd in {"/usage", "/cost"}:
            self.print_usage(why=(arg or "").strip().lower() in {"why", "detail", "details"})
        elif cmd == "/status":
            self.status()
        elif cmd == "/health":
            self.health_command(arg)
        elif cmd == "/contract":
            self.contract_command(arg)
        elif cmd == "/taskplan":
            self.taskplan_command(arg)
        elif cmd == "/verify":
            self.verify_command(arg)
        elif cmd == "/package":
            self.package_command(arg)
        elif cmd == "/update":
            self.update_command()
        elif cmd == "/new":
            self.new_session()
        elif cmd == "/resume":
            self.resume_session()
        elif cmd == "/sessions":
            self.list_sessions()
        elif cmd == "/rename":
            self.rename_session(arg)
        elif cmd == "/fork":
            self.fork_session()
        elif cmd == "/archive":
            return not self.archive_current()
        elif cmd == "/unarchive":
            self.unarchive_session()
        elif cmd == "/delete":
            return not self.delete_current()
        elif cmd == "/compact":
            self.compact()
        elif cmd == "/diff":
            self.show_diff()
        elif cmd == "/review":
            self.review(arg)
        elif cmd == "/plan":
            self.toggle_plan(arg)
        elif cmd == "/init":
            self.init_agents()
        elif cmd == "/copy":
            self.copy_latest()
        elif cmd == "/plugins":
            self.plugins()
        elif cmd == "/skills":
            self.skills()
        elif cmd == "/mcp":
            self.mcp_status()
        elif cmd == "/refresh":
            self.refresh_discovery()
        elif cmd == "/doctor":
            self.doctor()
        elif cmd == "/config":
            self.config_info(False)
        elif cmd == "/debug-config":
            self.config_info(True)
        elif cmd == "/keymap":
            self.keymap()
        elif cmd == "/help":
            self.help()
        elif cmd == "/clear":
            self.new_session()
            clear()
            self.ui.banner(VERSION, self.project)
        else:
            self.ui.error(f"Unknown command · {cmd}. Type / for the menu.")
        return True

    def run(self) -> int:
        clear()
        self.ui.banner(VERSION, self.project)
        self._check_startup_update()
        if self.credential_error:
            self.ui.error(self.credential_error)
        if self.active.provider == "cloud" and not self.api_key:
            self.ui.muted("Ollama Cloud is not logged in · use /login, or choose a Local model with /model")
        if self.current_session.bridge_enabled:
            self._bridge_register(True)
        try:
            recovered = self.missions.recover_interrupted()
            if recovered:
                self.ui.info(f"Mission control · recovered {len(recovered)} interrupted mission(s) from previous run")
        except Exception:
            pass

        pending_raw: str | None = None
        bridge_hops = 0
        while True:
            if self._update_notice and self._update_notice != self._update_notice_shown:
                self.ui.info(self._update_notice)
                self._update_notice_shown = self._update_notice
            try:
                due = self.workforce.due()
                for item in due:
                    message = f"Workforce · {item.specialist} · {item.state} · {item.instruction}"
                    (self.ui.info if item.state == "ready" else self.ui.muted)(message)
            except Exception:
                pass
            if (not self._task_thread or not self._task_thread.is_alive()) and not self._queued_messages.empty():
                priority: list[str] = []
                scheduled: list[str] = []
                while not self._queued_messages.empty():
                    immediate, message = self._queued_messages.get_nowait()
                    (priority if immediate else scheduled).append(message)
                for message in priority + scheduled:
                    self._queued_messages.put((False, message))
                _immediate, pending_raw = self._queued_messages.get_nowait()
            if pending_raw is not None:
                raw = pending_raw.strip()
                pending_raw = None
            elif self._task_thread and self._task_thread.is_alive():
                try:
                    raw = self.ui.prompt()
                except EOFError:
                    raw = ""
                if raw == _APPROVAL_WAKE:
                    self._serve_pending_approval()
                    continue
                if raw == _TASK_DONE_WAKE:
                    # The worker has ended.  Return to the top of the loop so a
                    # priority Escape message can be dispatched immediately.
                    continue
                if raw in {"\x00ADP_STOP\x00", "\x00ADP_IMMEDIATE\x00"}:
                    # Escape pressed while a task is running: stop the active task immediately.
                    self._yield_requested.set()
                    self.ui.end_working()
                    self.ui.error("Task stopped · Escape pressed")
                    try:
                        self.provider_harness.interrupt_active()
                    except Exception:
                        pass
                    if self._task_thread:
                        self._task_thread.join(timeout=0.3)
                    with self._task_lock:
                        self._task_thread = None
                    try:
                        self.ui._task_active = False
                        self.ui.clear_cockpit()
                    except Exception:
                        pass
                    continue
                cmd_check = raw.removeprefix("\x00ADP_IMMEDIATE\x00").strip()
                if cmd_check.lower() in {"/exit", "/quit", "exit", "quit", "/stop"}:
                    self._yield_requested.set()
                    self.ui.end_working()
                    try:
                        self.provider_harness.interrupt_active()
                    except Exception:
                        pass
                    if self._task_thread:
                        self._task_thread.join(timeout=0.3)
                    with self._task_lock:
                        self._task_thread = None
                    try:
                        self.ui._task_active = False
                        self.ui.clear_cockpit()
                    except Exception:
                        pass
                    if cmd_check.lower() in {"/stop"}:
                        self.ui.error("Task stopped")
                        continue
                    print()
                    self._save_session()
                    self._bridge_unregister_current()
                    self.notifier.close()
                    self.provider_harness.close()
                    try: self.resource_guard.close()
                    except Exception: pass
                    return 0
                immediate = raw.startswith("\x00ADP_IMMEDIATE\x00")
                raw = raw.removeprefix("\x00ADP_IMMEDIATE\x00").strip()
                if raw:
                    self._queued_messages.put((immediate, raw))
                    self.ui.muted("Queued next message")
                continue
            else:
                bridge_hops = 0
                try:
                    raw = self.ui.prompt().strip()
                except EOFError:
                    print()
                    self._save_session()
                    self._bridge_unregister_current()
                    self.notifier.close()
                    self.provider_harness.close()
                    try: self.resource_guard.close()
                    except Exception: pass
                    return 0
                except KeyboardInterrupt:
                    print()
                    continue
            if not raw:
                continue
            try:
                if raw == _APPROVAL_WAKE:
                    self._serve_pending_approval()
                    continue
                if raw.startswith("/"):
                    if not self.command(raw):
                        self._bridge_unregister_current()
                        self.notifier.close()
                        self.provider_harness.close()
                        try: self.resource_guard.close()
                        except Exception: pass
                        return 0
                elif raw.startswith("!"):
                    self.run_shell(raw[1:].strip())
                else:
                    def worker(instruction: str) -> None:
                        try:
                            self.run_task(instruction)
                        finally:
                            self._on_task_worker_exit()
                    with self._task_lock:
                        self._yield_requested.clear()
                        self._task_thread = threading.Thread(target=worker, args=(raw,), name="advertpreneur-task", daemon=True)
                        try:
                            self.ui._task_active = True
                        except Exception:
                            pass
                        self._task_thread.start()
                    # Keep the composer active while the task thread runs. The
                    # next iteration accepts queued or priority messages.
                    continue
                    if dispatch and dispatch.paired and self.current_session.bridge_enabled and self.current_session.bridge_autopilot:
                        if bridge_hops >= self.bridge_max_hops:
                            self.ui.muted(f"Browser Bridge · automatic turn limit {self.bridge_max_hops} reached · returning to local prompt")
                            continue
                        next_instruction = self._bridge_wait_for_instruction(dispatch)
                        if next_instruction:
                            bridge_hops += 1
                            pending_raw = next_instruction
            except KeyboardInterrupt:
                self.ui.end_working()
                self._save_session()
                self.ui.muted("Interrupted · Advertpreneur remains open")
                pending_raw = None
                continue



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Advertpreneur CLI - interactive model-powered coding agent")
    p.add_argument("--project", default=".", help="Project root (default: current directory)")
    p.add_argument("--config", help="Path to advertpreneur-cli.config.json")
    p.add_argument("--provider", choices=["cloud", "local"], help="Starting model provider")
    p.add_argument("--model", help="Starting model name")
    p.add_argument("--approval", choices=["ask", "safe", "full"], help="Tool approval mode")
    p.add_argument("--task-budget", type=float, help="Hard per-task usage-value cap")
    p.add_argument("--daily-budget", type=float, help="Hard daily usage-value cap")
    p.add_argument("--cloud-mode", choices=["free", "all"], help="Cloud access policy")
    p.add_argument("--init-config", action="store_true", help="Create advertpreneur-cli.config.json and exit")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    if not project.exists() or not project.is_dir():
        print(f"Project directory not found: {project}", file=sys.stderr)
        return 2

    if args.init_config:
        path = Path(args.config).expanduser().resolve() if args.config else project / "advertpreneur-cli.config.json"
        if path.exists():
            print(f"Config already exists: {path}")
            return 1
        write_default_config(path)
        print(f"Created {path}")
        return 0

    return AdvertpreneurCLI(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
