from __future__ import annotations

import base64
import copy
import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from .budget import BudgetTracker
from .client import OllamaClient
from .config import ModelProfile, Settings
from .pricing import is_free_cloud_model, lookup_price
from .tools import ToolError, ToolRegistry
from .ownership import worker_ownership_contract


SYSTEM_PROMPT = """You are Advertpreneur CLI, a coding agent restricted to one project root.

Rules:
1. Inspect relevant existing code before editing. Prefer explicit @mentions, verified session evidence, and the local project_map before broad list/search scans. A clearly requested standalone new file may be created directly.
2. Source discipline: never state implementation details as fact unless verified from current source/tool evidence. If exact paths/lines/runtime behavior are requested, inspect the definitive source and report evidence, not conceptual code.
3. Finish implementation tasks; do not stop at suggestions. Plan/review/explain requests stay read-only unless asked otherwise.
4. Keep changes scoped and consistent. Prefer targeted edits for small changes and full writes for new files.
5. Validation discipline: never claim build/test/lint PASS unless that command actually ran successfully, or current local session evidence explicitly records a successful unchanged-code run. If the user explicitly asks to run/build/test now, execute it now.
6. Reuse current verified session evidence instead of rediscovering unchanged facts. Re-read only when surrounding code is genuinely needed or cached evidence is insufficient/stale.
7. Never escape the project root, disable safeguards, or alter unrelated system files.
8. Installed Codex skills/MCPs may be available as tools. Load only relevant skills; query MCP tools narrowly before calling them.
9. Research order: verified session evidence / project map / engineering handbook first; use web_search/web_fetch only when local knowledge is insufficient or current external documentation is needed. Prefer official/vendor sources.
10. Visual/reference-site work: do not guess layout. Use browser reverse_engineer + screenshot/inspect before coding. Preserve exact reference copy, assets, geometry, typography, colors, pseudo-elements and decoration when evidence is available; never replace known reference details with generic stock art/copy merely for convenience. Browser action success requires observable evidence (URL/title/state/scroll change), not merely a successful tool return. If a learned browser routine matches a repetitive task, replay it instead of rediscovering the steps.
11. Final report is factual and concise: what changed, validation actually performed, and any real remaining issue. Usually <=200 words.
12. Continue the tool loop until complete or genuinely blocked.
13. Live-site autonomy: when a task involves WordPress, wp-admin, Hostinger, cPanel, Plesk, a host file manager, plugin/theme upload, posts/pages, or site settings, use the browser tool yourself. Start from the saved site profile when available; otherwise navigate to the real URL supplied in the task. Browser navigation automatically chooses a supported adapter. Call `site_playbook` for a matching prebuilt workflow before mutation, then inspect the observed live UI at every step and stop if it differs. Do not tell the operator to type CLI browser commands. If the observed page is a login screen, report `Login needed in browser` and wait for the operator to sign in through the browser's own saved-password/session UI; never ask for, receive, or store their credentials. Inspect the live page before mutation, use the staged upload workspace for files, obtain an explicit deletion proposal approval before destructive work, and verify a WordPress/host success notice or changed listing before reporting success.
14. Amazon/Helium 10 research: keep browser surfaces in named slots. Navigate Softzilla in `access`; when Launch Web App opens a new tab, click with `capture_tab:"helium"` and leave that slot open. Navigate Amazon in `amazon`; never reuse or navigate the Helium slot for research. Before the first keyword, inspect the Amazon delivery location; only if it is not New York ZIP 10001, use the observed controls to set and confirm 10001. Generate a keyword list from the operator's samples. On the observed Xray modal, use Load more until data is present; if it is still absent after 30 seconds, use the observed modal refresh control once, then export CSV. Use browser `research_start`, `research_search`, and `research_export` to establish a one-at-a-time, resumable local CSV run. Once observed selectors have succeeded, use `research_run` to process the remaining queue in the Amazon slot. Record each completed download, rename it locally, and refresh Amazon before the next keyword. A sign-in, MFA, CAPTCHA, traffic, access, rate, or verification warning is a hard checkpoint: report it and wait. Never request credentials, bypass a site safeguard, rotate identity/IP, or disguise automation.
"""


EventCallback = Callable[[str, Dict[str, Any]], None]


@dataclass
class TaskResult:
    text: str
    model: str
    provider: str
    turns: int
    tool_calls: int = 0


class CodingAgent:
    def __init__(
        self,
        settings: Settings,
        tools: ToolRegistry,
        budget: BudgetTracker,
        api_key: str | None,
        event_callback: EventCallback | None = None,
        extra_instructions: str = "",
    ) -> None:
        self.settings = settings
        self.tools = tools
        self.budget = budget
        self.api_key = api_key
        self.messages: List[Dict[str, Any]] = []
        self.session_tasks = 0
        self.event_callback = event_callback or (lambda _name, _data: None)
        self.extra_instructions = extra_instructions
        self.last_context_breakdown: Dict[str, int] = {}
        self.active_task: str = ""

    def _event(self, event_name: str, **data: Any) -> None:
        try:
            self.event_callback(event_name, data)
        except Exception:
            pass

    def _client(self, provider: str) -> OllamaClient:
        return OllamaClient(provider, self.api_key)

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT + "\n" + worker_ownership_contract() + ("\n" + self.extra_instructions.strip() if self.extra_instructions.strip() else "")

    def set_extra_instructions(self, text: str) -> None:
        self.extra_instructions = text or ""
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt()

    def load_messages(self, messages: List[Dict[str, Any]]) -> None:
        self.messages = json.loads(json.dumps(messages or []))
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system_prompt()

    def _cost(self, profile: ModelProfile, input_tokens: int, output_tokens: int) -> float:
        if profile.provider == "local":
            return 0.0
        price = lookup_price(self.settings.prices, profile.model)
        if not price:
            raise RuntimeError(
                f"No price configured for cloud model '{profile.model}'. Add it to config before using this model."
            )
        return price.estimate(input_tokens, output_tokens, cached_input_tokens=0)

    def _assert_cloud_access(self, profile: ModelProfile) -> None:
        if profile.provider != "cloud":
            return
        if self.settings.cloud_access_mode == "free" and not is_free_cloud_model(profile.model, self.settings.free_cloud_models):
            raise RuntimeError(
                f"Cloud model '{profile.model}' is blocked by Free-only mode. "
                "Select a Free starter model or switch Cloud access to All from the CLI menu."
            )

    @staticmethod
    def _approx_tokens(value: Any) -> int:
        # Image payloads are base64 and would wildly distort a text-token estimate.
        # Count each image as a small marker here; provider image accounting remains authoritative.
        def strip_images(v: Any) -> Any:
            if isinstance(v, dict):
                return {k: (["[image]"] * len(x) if k == "images" and isinstance(x, list) else strip_images(x)) for k, x in v.items()}
            if isinstance(v, list):
                return [strip_images(x) for x in v]
            return v
        try:
            chars = len(json.dumps(strip_images(value), ensure_ascii=False))
        except Exception:
            chars = len(str(value))
        # Code/JSON tends to tokenize a little denser than prose; this is intentionally conservative.
        return max(1, chars // 3)

    @staticmethod
    def _skill_receipt(content: str) -> str:
        """Keep provenance + high-signal rules after a skill body has been consumed once."""
        lines = content.splitlines()
        head = lines[:8]
        signals: list[str] = []
        for line in lines[8:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "- ", "* ", "> ")) or re.match(r"^\d+[.)]\s", stripped):
                signals.append(stripped)
            if sum(len(x) + 1 for x in signals) > 1800:
                break
        body = "\n".join(head + signals)
        return (body[:2400] + ("\n…[skill body compacted after first use]" if len(content) > 2400 else "")).strip()

    @classmethod
    def _consumed_tool_receipt(cls, message: Dict[str, Any]) -> str:
        name = str(message.get("tool_name") or "tool")
        content = str(message.get("content") or "")
        if name == "load_skill":
            return cls._skill_receipt(content)
        if name == "run_command":
            # Preserve exit status and a useful sliver of validation output.
            return content[:1200] + ("\n…[older command output compacted]" if len(content) > 1200 else "")
        if name == "mcp":
            return content[:1400] + ("\n…[older MCP result compacted]" if len(content) > 1400 else "")
        if content.startswith("TOOL_ERROR"):
            return content[:1000]
        return f"[{name} result already consumed in a prior turn; re-run only if the data is needed again.]"

    @staticmethod
    def _assistant_receipt(content: str) -> str:
        """Compact a completed prior answer while preserving useful continuity locally."""
        text = " ".join(str(content or "").split())
        if len(text) <= 420:
            return text
        # Keep opening conclusion plus paths/symbol-ish fragments and validation language.
        paths = re.findall(r"(?:[A-Za-z0-9_.-]+[/\\]){1,8}[A-Za-z0-9_.-]+", text)[:8]
        signals = []
        for sent in re.split(r"(?<=[.!?])\s+", text):
            low = sent.lower()
            if any(k in low for k in ("changed", "created", "updated", "fixed", "test", "build", "remaining", "error", "failed", "passed")):
                signals.append(sent[:180])
            if len(signals) >= 3:
                break
        receipt = text[:240]
        if paths:
            receipt += " Paths: " + ", ".join(dict.fromkeys(paths))
        if signals:
            receipt += " Notes: " + " ".join(signals)
        return receipt[:620] + (" …[prior answer compacted locally]" if len(text) > 620 else "")

    def _messages_for_request(self) -> List[Dict[str, Any]]:
        """Create a token-efficient view of history without destroying the saved session.

        A tool result is sent in full exactly once: the model turn immediately after
        that tool call. After a later assistant response exists, old read/search/scan
        payloads collapse to receipts. Historical model thinking is never resent.
        """
        src = self.messages
        later_assistant = [False] * len(src)
        later_user = [False] * len(src)
        seen_assistant = False
        seen_user = False
        for i in range(len(src) - 1, -1, -1):
            later_assistant[i] = seen_assistant
            later_user[i] = seen_user
            role_i = src[i].get("role")
            if role_i == "assistant":
                seen_assistant = True
            elif role_i == "user":
                seen_user = True

        out: List[Dict[str, Any]] = []
        for i, original in enumerate(src):
            msg = copy.deepcopy(original)
            role = msg.get("role")
            if role == "assistant":
                # Never pay to resend hidden reasoning generated by a previous turn.
                msg.pop("thinking", None)
                # Once a later user turn exists, the full nicely-formatted prior answer
                # is local UI history, not useful cloud context. Keep a concise receipt.
                if later_user[i] and str(msg.get("content") or "").strip():
                    msg["content"] = self._assistant_receipt(str(msg.get("content") or ""))
                # Keep historical tool-call structure so Ollama still sees a valid
                # assistant->tool sequence, but remove bulky arguments (especially full
                # file bodies) after that tool result has already been consumed once.
                if later_assistant[i] and msg.get("tool_calls"):
                    compact_calls = []
                    for call in msg.get("tool_calls") or []:
                        fn = (call or {}).get("function", {}) if isinstance(call, dict) else {}
                        name = str(fn.get("name") or "tool")
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        args = dict(args) if isinstance(args, dict) else {}
                        if name == "write_file":
                            args = {"path": args.get("path", ""), "content": "[omitted after successful write]"}
                        elif name == "replace_in_file":
                            args = {"path": args.get("path", ""), "old_text": "[omitted]", "new_text": "[omitted]"}
                        elif name == "mcp" and args.get("action") == "call":
                            args = {"action": "call", "server": args.get("server", ""), "tool": args.get("tool", ""), "arguments": {}}
                        compact_calls.append({"function": {"name": name, "arguments": args}})
                    msg["tool_calls"] = compact_calls
                    if not str(msg.get("content") or "").strip():
                        msg["content"] = "[completed tool actions]"
            elif role == "user" and msg.get("images"):
                single_use = bool(msg.pop("adp_single_use_image", False))
                # Tool-produced browser screenshots are for the next reasoning call only.
                # User-supplied visual references stay through the current task, then are
                # compacted once a later user turn exists. This avoids paying image cost
                # repeatedly during browser/tool loops.
                if (single_use and later_assistant[i]) or later_user[i]:
                    msg.pop("images", None)
                    msg["content"] = str(msg.get("content") or "") + "\n[prior visual evidence retained locally; image bytes compacted after use]"
            elif role == "tool" and later_assistant[i]:
                msg["content"] = self._consumed_tool_receipt(msg)
            out.append(msg)

        # Protect the next provider call from parallel tool fan-out. The saved session
        # keeps full results, but only a bounded amount of the *current* trailing tool
        # payload is sent at once. This is especially important for multiple file reads
        # or verbose test commands in one model turn.
        trailing = []
        for idx in range(len(out) - 1, -1, -1):
            if out[idx].get("role") == "tool":
                trailing.append(idx)
                continue
            break
        if trailing:
            trailing.reverse()
            budget_chars = 24000
            total_chars = sum(len(str(out[i].get("content") or "")) for i in trailing)
            if total_chars > budget_chars:
                per = max(1800, budget_chars // len(trailing))
                remaining = budget_chars
                for i in trailing:
                    content = str(out[i].get("content") or "")
                    allowance = min(per, remaining)
                    if len(content) > allowance:
                        out[i]["content"] = content[:allowance] + "\n…[current tool payload locally clipped to control cloud context]"
                    remaining = max(0, remaining - min(len(content), allowance))
        return out

    def context_contributors(self, limit: int = 10) -> List[tuple[str, int]]:
        messages = self._messages_for_request() if self.messages else [{"role": "system", "content": self.system_prompt()}]
        rows: List[tuple[str, int]] = []
        for i, m in enumerate(messages):
            role = str(m.get("role") or "message")
            if role == "tool":
                label = f"tool:{m.get('tool_name') or 'tool'}"
            elif role == "system":
                label = "system/extensions" if i == 0 else "session memory"
            else:
                content = " ".join(str(m.get("content") or "").split())
                label = f"{role}: {content[:52]}" if content else role
            rows.append((label, self._approx_tokens(m)))
        rows.append(("tool schemas", self._approx_tokens(self.tools.schemas(self.active_task, messages))))
        rows.sort(key=lambda x: -x[1])
        return rows[:max(1, int(limit))]

    def context_breakdown(self) -> Dict[str, int]:
        messages = self._messages_for_request() if self.messages else [{"role": "system", "content": self.system_prompt()}]
        schemas = self.tools.schemas(self.active_task, messages)
        system = [m for m in messages if m.get("role") == "system"]
        tools = [m for m in messages if m.get("role") == "tool"]
        conversation = [m for m in messages if m.get("role") not in {"system", "tool"}]
        return {
            "system": self._approx_tokens(system) if system else 0,
            "conversation": self._approx_tokens(conversation) if conversation else 0,
            "tool_results": self._approx_tokens(tools) if tools else 0,
            "tool_schemas": self._approx_tokens(schemas) if schemas else 0,
        }

    def _budgeted_output_cap(self, profile: ModelProfile) -> int:
        if profile.provider == "local":
            return profile.max_output_tokens
        price = lookup_price(self.settings.prices, profile.model)
        if not price:
            raise RuntimeError(f"No price configured for cloud model '{profile.model}'.")
        remaining = min(
            self.budget.task_limit - self.budget.task.estimated_cost_usd,
            self.budget.daily_limit - self.budget.daily_cost(),
        )
        remaining = max(0.0, remaining) * 0.90
        send_messages = self._messages_for_request()
        schemas = self.tools.schemas(self.active_task, send_messages)
        estimated_input_tokens = self._approx_tokens(send_messages) + self._approx_tokens(schemas)
        estimated_input_cost = estimated_input_tokens * price.input_per_million / 1_000_000
        available_for_output = remaining - estimated_input_cost
        if available_for_output <= 0:
            return 0
        affordable_output = int(available_for_output * 1_000_000 / max(price.output_per_million, 1e-12))
        return max(0, min(profile.max_output_tokens, affordable_output))

    def strip_visual_payloads(self) -> int:
        """Remove base64 image bytes after a task while keeping a small local-history note."""
        removed = 0
        for msg in self.messages:
            if msg.get("role") == "user" and msg.get("images"):
                removed += len(msg.get("images") or [])
                msg.pop("images", None)
                content = str(msg.get("content") or "")
                if "[visual reference was attached" not in content:
                    msg["content"] = content + "\n[visual reference was attached for this task; image bytes removed from saved session after use]"
        return removed

    def reset_session(self) -> None:
        self.messages = []
        self.session_tasks = 0
        self.last_context_breakdown = {}
        self.active_task = ""

    def context_estimate_tokens(self) -> int:
        if not self.messages:
            return 0
        b = self.context_breakdown()
        return sum(b.values())

    def compact_local(self, context_manager: Any) -> tuple[int, int, str]:
        """Compact completed-session history locally with zero provider calls."""
        before = self.context_estimate_tokens()
        if not self.messages:
            return before, before, ""
        new_messages, memory = context_manager.compact_messages(self.messages, self.system_prompt())
        self.messages = new_messages
        after = self.context_estimate_tokens()
        self.last_context_breakdown = self.context_breakdown()
        return before, after, memory

    def compact(self, profile: ModelProfile) -> str:
        if not self.messages:
            return "Nothing to compact."
        self._assert_cloud_access(profile)
        self.budget.reset_task()
        snapshot = json.dumps(self._messages_for_request(), ensure_ascii=False)
        snapshot = snapshot[-90_000:]
        prompt = [
            {"role": "system", "content": "Compact this coding-agent session. Preserve file paths, decisions, requirements, changes, validation, errors and remaining work. Omit chatter and old tool payloads."},
            {"role": "user", "content": snapshot},
        ]
        cap = min(1000, max(128, profile.max_output_tokens))
        result = self._client(profile.provider).chat(profile.model, prompt, [], think=False, max_output_tokens=cap)
        cost = self._cost(profile, result.input_tokens, result.output_tokens)
        self.budget.record(profile.model, result.input_tokens, result.output_tokens, cost)
        summary = str((result.message or {}).get("content") or "").strip()
        if not summary:
            return "Compaction failed: model returned no summary."
        self.messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "system", "content": "Compacted session context:\n" + summary},
        ]
        return summary

    @staticmethod
    def _requires_live_validation(task: str) -> bool:
        """Detect an explicit request to actually execute build/test/lint/compile validation."""
        low = " ".join(str(task or "").lower().split())
        patterns = (
            r"\b(?:actually|really|real)\s+(?:run|build|compile|test|lint)\b",
            r"\b(?:run|execute|perform)\b.{0,50}\b(?:tests?|test suite|build|compile|lint|gradle|pytest|assembledebug)\b",
            r"\b(?:build|compile|lint)\s+(?:the\s+)?(?:app|application|project|android|frontend|backend|code)\b",
            r"\b(?:run|execute)\s+[^\n]{0,80}\b(?:gradlew|pytest|npm|pnpm|yarn|cargo|ctest|mvn|dotnet)\b",
        )
        return any(re.search(pattern, low) for pattern in patterns)

    @staticmethod
    def _is_validation_command(command: str) -> bool:
        low = " ".join(str(command or "").lower().split())
        markers = (
            "gradlew", "gradlew.bat", "pytest", "python -m pytest", "npm test", "npm run test",
            "npm run build", "npm run lint", "pnpm test", "pnpm run test", "pnpm build",
            "pnpm run build", "pnpm lint", "yarn test", "yarn build", "dotnet test", "dotnet build",
            "cargo test", "cargo check", "go test", "mvn test", "cmake --build", "ctest",
            "flutter test", "flutter build", "phpunit", "composer test",
        )
        return any(x in low for x in markers)

    def run_task(self, task: str, profile: ModelProfile, original_task: str | None = None, image_paths: List[Path] | None = None) -> TaskResult:
        self.budget.reset_task()
        self.active_task = task
        self._assert_cloud_access(profile)
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt()}]
        user_message: Dict[str, Any] = {"role": "user", "content": task}
        if image_paths:
            images = []
            for path in image_paths[:4]:
                try:
                    raw = Path(path).read_bytes()
                    if len(raw) <= 12_000_000:
                        images.append(base64.b64encode(raw).decode("ascii"))
                except OSError:
                    continue
            if images:
                user_message["images"] = images
        self.messages.append(user_message)
        self.session_tasks += 1
        last_content = ""
        self._event("task_start", task=task, provider=profile.provider, model=profile.model)

        # Identical read-only calls within one task are usually accidental loops. Return a
        # tiny receipt on repeats until a mutation invalidates the observation cache.
        seen_reads: set[str] = set()
        read_only = {"list_files", "read_file", "search_text", "project_map", "git_status", "git_diff", "web_search", "web_fetch"}
        mutating = {"write_file", "replace_in_file", "run_command"}
        tool_calls_total = 0
        tool_signatures: Dict[str, int] = {}
        requires_live_validation = self._requires_live_validation(original_task if original_task is not None else task)
        validation_attempted = False
        browser_visual_attached = False

        for turn in range(1, self.settings.max_agent_turns + 1):
            ok, reason = self.budget.can_request()
            if not ok:
                self._event("task_stop", reason=reason)
                self.active_task = ""
                return TaskResult(f"STOPPED BY BUDGET: {reason}", profile.model, profile.provider, turn - 1, tool_calls_total)

            output_cap = self._budgeted_output_cap(profile)
            if output_cap < 64:
                text = f"STOPPED BY BUDGET PREFLIGHT: not enough budget remains for a safe next call (cap {output_cap} tokens)."
                self._event("task_stop", reason=text)
                self.active_task = ""
                return TaskResult(text, profile.model, profile.provider, turn - 1, tool_calls_total)

            send_messages = self._messages_for_request()
            schemas = self.tools.schemas(self.active_task, send_messages)
            self.last_context_breakdown = self.context_breakdown()
            self._event(
                "model_start",
                turn=turn,
                provider=profile.provider,
                model=profile.model,
                context_breakdown=dict(self.last_context_breakdown),
            )
            result = self._client(profile.provider).chat(
                profile.model,
                send_messages,
                schemas,
                think=profile.think,
                max_output_tokens=output_cap,
            )
            cost = self._cost(profile, result.input_tokens, result.output_tokens)
            self.budget.record(profile.model, result.input_tokens, result.output_tokens, cost)
            self._event(
                "model_done",
                turn=turn,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                metered_value=cost,
                duration_ns=result.total_duration_ns,
            )

            msg = result.message or {}
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": msg.get("content", "")}
            # Keep thinking in the saved session for inspection if the provider returns it,
            # but _messages_for_request never sends it back to the model.
            if msg.get("thinking"):
                assistant_msg["thinking"] = msg["thinking"]
            if msg.get("tool_calls"):
                assistant_msg["tool_calls"] = msg["tool_calls"]
            self.messages.append(assistant_msg)
            last_content = str(msg.get("content") or "")

            calls = msg.get("tool_calls") or []
            if not calls:
                if requires_live_validation and not validation_attempted:
                    self.messages.append({
                        "role": "system",
                        "content": (
                            "VALIDATION REQUIRED: the user's original instruction explicitly requires a real "
                            "build/test/lint/compile execution, but no matching validation command has been run "
                            "in this task. Use run_command now. Do not report PASS from source inspection alone; "
                            "if execution is impossible, report the concrete blocker instead."
                        ),
                    })
                    self._event("validation_required", turn=turn)
                    continue
                self._event("task_done", turn=turn)
                self.active_task = ""
                return TaskResult(last_content or "(model returned no content)", profile.model, profile.provider, turn, tool_calls_total)

            for call in calls:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                args = dict(args)
                self._event("tool_start", name=name, args=args)
                signature = name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
                tool_calls_total += 1
                tool_signatures[signature] = tool_signatures.get(signature, 0) + 1
                if tool_calls_total > 100 or tool_signatures[signature] > 10:
                    text = (
                        f"STOPPED BY TOOL-LOOP GUARD: {tool_calls_total} tool calls in this task; "
                        f"the same operation repeated {tool_signatures[signature]} times. "
                        "No further model turn was sent. Review the task/tool trace before retrying."
                    )
                    self._event("task_stop", reason=text, tool_calls=tool_calls_total)
                    self.active_task = ""
                    return TaskResult(text, profile.model, profile.provider, turn, tool_calls_total)
                if name == "run_command" and self._is_validation_command(str(args.get("command") or "")):
                    validation_attempted = True
                try:
                    if name in read_only and signature in seen_reads:
                        content = f"DUPLICATE_SKIPPED: identical {name} call already returned in this task; reuse the prior result."
                        self._event("tool_cached", name=name, args=args)
                    else:
                        content = self.tools.execute(name, args)
                        if name in read_only:
                            seen_reads.add(signature)
                        if name in mutating:
                            seen_reads.clear()
                        self._event("tool_done", name=name, args=args, result=content)
                except (ToolError, Exception) as exc:
                    content = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
                    self._event("tool_error", name=name, args=args, error=str(exc))
                self.messages.append({"role": "tool", "tool_name": name, "content": content})
                # Reverse-engineering was previously giving the model only DOM/CSS text while
                # the screenshot stayed on disk. For visual fidelity, attach the first current
                # browser evidence screenshot to the very next model turn. Old image bytes are
                # compacted out of later turns by _messages_for_request/strip_visual_payloads.
                if (
                    name == "browser" and not browser_visual_attached
                    and str(args.get("action") or "") in {"reverse_engineer", "screenshot"}
                    and not content.startswith("TOOL_ERROR:")
                ):
                    match = re.search(r"(?:Screenshot saved|Screenshot) · ([^\n·]+)", content)
                    if match:
                        raw_path = match.group(1).strip()
                        path = Path(raw_path)
                        if not path.is_absolute():
                            path = (self.tools.root / raw_path).resolve()
                        try:
                            if path.is_file() and path.stat().st_size <= 12_000_000:
                                image = base64.b64encode(path.read_bytes()).decode("ascii")
                                self.messages.append({
                                    "role": "user",
                                    "content": "Current browser visual evidence. Compare this screenshot against the measured design map before coding; do not invent missing reference details.",
                                    "images": [image],
                                    "adp_single_use_image": True,
                                })
                                browser_visual_attached = True
                                self._event("visual_evidence_attached", path=str(path))
                        except OSError:
                            pass

        text = f"STOPPED: agent reached max turn limit ({self.settings.max_agent_turns}). Last model content:\n{last_content}"
        self._event("task_stop", reason=text)
        self.active_task = ""
        return TaskResult(text, profile.model, profile.provider, self.settings.max_agent_turns, tool_calls_total)
