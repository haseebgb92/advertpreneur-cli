"""Provider-neutral execution of Advertpreneur actions.

External providers do not receive privileged local browser or shell access.
They request an action in a small protocol, and this gateway executes it via the
same approval-aware ToolRegistry used by ADP's native tool-calling models.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .tools import ToolError
from .ownership import worker_ownership_contract


@dataclass(frozen=True)
class ActionRequest:
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str
    blocked: bool = False


@dataclass(frozen=True)
class GatewayLoopResult:
    text: str
    conversation_id: str
    blocked: bool = False
    actions: int = 0
    provider_turns: int = 0
    yielded: bool = False


class ExternalActionGateway:
    """Validate and execute external provider actions through ToolRegistry."""

    _action_block = re.compile(r"```adp_action\s*\n?(\{.*?\})\s*```", re.I | re.S)

    def __init__(
        self, tools: Any, *, max_actions: int = 24, max_repeats: int = 2,
        on_action: Callable[[ActionRequest], None] | None = None,
    ) -> None:
        self.tools = tools
        self.max_actions = max(1, int(max_actions))
        self.max_repeats = max(1, int(max_repeats))
        self._actions = 0
        self._last_signature = ""
        self._consecutive_repeats = 0
        self.on_action = on_action or (lambda _request: None)

    @staticmethod
    def contract() -> str:
        return (
            "ADP action contract: You can request local ADP capabilities regardless of your model provider. "
            "When an action is needed, emit exactly one fenced `adp_action` JSON object with `tool` and object `args`, "
            "for example ```adp_action {\"tool\":\"browser\",\"args\":{\"action\":\"status\"}} ``` . "
            "ADP executes the action and returns observed evidence in `adp_action_result`; inspect it before requesting one next action or finishing. "
            "Use browser actions yourself for live panels. Never ask for credentials: if the result says `Login needed in browser`, stop and wait. "
            "For Amazon/Helium 10 keyword research, use browser tab slots: `access` for Softzilla, capture Launch Web App into `helium`, and keep searches in `amazon`. Before the first keyword, inspect Amazon's delivery location and, only when it is not New York ZIP 10001, use the observed page controls to set and confirm 10001. Create one local run with browser `research_start`. Inspect the active Amazon/Xray UI and pass only observed selectors for search, submit, open, rows, load_more, refresh, export, and csv as JSON to `research_xray_setup`; never invent static selectors. Then use `research_run`: it opens Xray, uses Load more only after observed row growth, refreshes Xray once after a stall, exports through the observed CSV control, records each verified download, and refreshes only the Amazon tab. "
            "If ADP reports a sign-in, MFA, CAPTCHA, traffic, access, rate, or verification checkpoint, stop immediately and wait; never attempt to bypass it. "
            "Never delete or remove content directly; request a deletion proposal and stop until its explicit approval is returned. "
            + worker_ownership_contract()
        )

    @classmethod
    def parse_action(cls, text: str) -> ActionRequest:
        source = str(text or "")
        blocks = cls._action_block.findall(source)
        if not blocks:
            if "```adp_action" in source.lower():
                return ActionRequest(error="adp_action must contain one JSON object.")
            return ActionRequest()
        if len(blocks) != 1:
            return ActionRequest(error="Exactly one adp_action block is allowed per provider turn.")
        try:
            row = json.loads(blocks[0])
        except json.JSONDecodeError:
            return ActionRequest(error="adp_action must contain valid JSON.")
        if not isinstance(row, dict):
            return ActionRequest(error="adp_action must be a JSON object.")
        tool = str(row.get("tool") or "").strip()
        args = row.get("args")
        if not tool:
            return ActionRequest(error="adp_action requires a non-empty tool.")
        if not isinstance(args, dict):
            return ActionRequest(error="adp_action args must be an object.")
        return ActionRequest(tool=tool, args=args)

    def _action_names(self) -> set[str]:
        names = getattr(self.tools, "action_names", None)
        if callable(names):
            return {str(name) for name in names()}
        return {
            key.removeprefix("tool_") for key in dir(self.tools)
            if key.startswith("tool_") and callable(getattr(self.tools, key, None))
        }

    @staticmethod
    def _blocked(text: str) -> bool:
        low = str(text or "").lower()
        return any(marker in low for marker in (
            "login needed in browser", "requires approved proposal", "deletion requires",
            "approval required", "user declined", "user canceled",
            "research checkpoint", "research paused",
        ))

    @staticmethod
    def _signature(request: ActionRequest) -> str:
        return request.tool + ":" + json.dumps(request.args, sort_keys=True, ensure_ascii=True, separators=(",", ":"))

    def execute(self, request: ActionRequest) -> ActionResult:
        if request.error:
            return ActionResult(False, "TOOL_ERROR: " + request.error)
        if request.tool not in self._action_names():
            return ActionResult(False, f"Unknown ADP action: {request.tool}")
        if self._actions >= self.max_actions:
            return ActionResult(False, f"ADP action limit reached ({self.max_actions}) for this task.", blocked=True)
        signature = self._signature(request)
        repeats = self._consecutive_repeats if signature == self._last_signature else 0
        if repeats >= self.max_repeats:
            return ActionResult(False, "ADP stopped a repeated identical action; inspect the last observed result and choose a different recovery step.", blocked=True)
        self._actions += 1
        self._last_signature = signature
        self._consecutive_repeats = repeats + 1
        try:
            self.on_action(request)
            text = str(self.tools.execute(request.tool, request.args))
        except ToolError as exc:
            text = f"TOOL_ERROR: {exc}"
            return ActionResult(False, text, blocked=self._blocked(text))
        return ActionResult(True, text, blocked=self._blocked(text))

    @staticmethod
    def result_packet(result: ActionResult) -> str:
        payload = {"ok": result.ok, "blocked": result.blocked, "result": result.text}
        return (
            "```adp_action_result\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"
            + ("This action is blocked; report the exact observed boundary and wait for the operator."
               if result.blocked else "Inspect this observed result; request one next action or finish.")
        )

    def drive(
        self,
        initial_prompt: str,
        run_turn: Callable[[str, str], tuple[str, str]],
        conversation_id: str = "",
        should_yield: Callable[[], bool] | None = None,
    ) -> GatewayLoopResult:
        """Run a bounded provider-turn -> ADP-action -> evidence loop.

        ``run_turn`` is deliberately provider agnostic. The CLI owns transport,
        quota, and provider-session details while this class owns only protocol
        validation and execution through the shared ToolRegistry.
        """
        prompt = str(initial_prompt or "")
        current_id = str(conversation_id or "")
        turns = 0
        blank_turns = 0
        while turns <= self.max_actions:
            text, returned_id = run_turn(prompt, current_id)
            turns += 1
            current_id = str(returned_id or current_id)
            if should_yield and should_yield():
                return GatewayLoopResult(
                    "Advertpreneur yielded before the next action; the immediate message will run now.",
                    current_id,
                    actions=self._actions,
                    provider_turns=turns,
                    yielded=True,
                )
            request = self.parse_action(text)
            if not request.tool and not request.error:
                if not str(text or "").strip():
                    blank_turns += 1
                    if blank_turns == 1:
                        prompt = (
                            "Your previous turn returned no final response and requested no ADP action. "
                            "Continue the task now: inspect the latest browser evidence and emit one valid adp_action, "
                            "or return a concise final result."
                        )
                        continue
                    return GatewayLoopResult(
                        "Provider returned no actionable response after a recovery turn.", current_id,
                        True, self._actions, turns,
                    )
                return GatewayLoopResult(str(text or ""), current_id, actions=self._actions, provider_turns=turns)
            result = self.execute(request)
            if result.blocked:
                return GatewayLoopResult(result.text, current_id, True, self._actions, turns)
            prompt = self.result_packet(result)
        return GatewayLoopResult(
            f"ADP action continuation limit reached ({self.max_actions}); inspect the latest observed result before retrying.",
            current_id, True, self._actions, turns,
        )
