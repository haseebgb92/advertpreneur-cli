from __future__ import annotations

import json
import re
from typing import Any, Dict, List


_PATH_RX = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+[\\/])+(?:[A-Za-z0-9_.@()-]+)")


def _short(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


class LocalContextManager:
    """Deterministic zero-cloud context compactor.

    It only runs between completed tasks. Old tool payloads/reasoning are replaced by
    a concise extractive memory while recent conversational turns are retained.
    """

    def __init__(self, threshold_tokens: int = 24000, target_tokens: int = 9000) -> None:
        self.threshold_tokens = max(4000, int(threshold_tokens))
        self.target_tokens = max(2500, min(int(target_tokens), self.threshold_tokens - 1000))

    def should_compact(self, approximate_tokens: int) -> bool:
        return approximate_tokens >= self.threshold_tokens

    def compact_messages(self, messages: List[Dict[str, Any]], system_prompt: str) -> tuple[List[Dict[str, Any]], str]:
        if not messages:
            return messages, ""
        users: List[str] = []
        outcomes: List[str] = []
        actions: List[str] = []
        errors: List[str] = []
        paths: List[str] = []

        for m in messages:
            role = m.get("role")
            content = str(m.get("content") or "")
            for p in _PATH_RX.findall(content):
                if len(p) < 180:
                    paths.append(p.replace("\\", "/"))
            if role == "user":
                users.append(_short(content, 420))
            elif role == "assistant":
                if content.strip():
                    outcomes.append(_short(content, 520))
                for call in m.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") or {}
                    name = str(fn.get("name") or "tool")
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    if isinstance(args, dict):
                        if name in {"write_file", "replace_in_file", "read_file"}:
                            actions.append(f"{name}: {args.get('path','')}")
                        elif name == "run_command":
                            actions.append(f"run: {_short(str(args.get('command','')), 160)}")
                        elif name == "load_skill":
                            actions.append(f"skill: {args.get('skill','')}")
                        elif name == "mcp":
                            actions.append(f"mcp: {args.get('server','')} {args.get('tool') or args.get('action','')}")
            elif role == "tool":
                if "TOOL_ERROR" in content or "error" in content.lower()[:80]:
                    errors.append(_short(content, 380))

        # Recent plain conversational turns remain verbatim-ish, but never retain old tool-call payloads.
        recent: List[Dict[str, Any]] = []
        for m in reversed(messages):
            if m.get("role") not in {"user", "assistant"}:
                continue
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            recent.append({"role": m.get("role"), "content": content[:3000]})
            if len(recent) >= 6:
                break
        recent.reverse()

        def uniq(items: List[str], cap: int) -> List[str]:
            return list(dict.fromkeys(x for x in items if x))[-cap:]

        lines = ["Local session memory (deterministically compacted; no cloud model used):"]
        if uniq(users, 8):
            lines.append("Recent requirements:")
            lines.extend(f"- {x}" for x in uniq(users, 8))
        if uniq(outcomes, 8):
            lines.append("Completed/outcome notes:")
            lines.extend(f"- {x}" for x in uniq(outcomes, 8))
        if uniq(actions, 18):
            lines.append("Important actions:")
            lines.extend(f"- {x}" for x in uniq(actions, 18))
        if uniq(paths, 24):
            lines.append("Referenced paths:")
            lines.extend(f"- {x}" for x in uniq(paths, 24))
        if uniq(errors, 6):
            lines.append("Recent unresolved/tool errors:")
            lines.extend(f"- {x}" for x in uniq(errors, 6))
        memory = "\n".join(lines)[:9000]
        result = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": memory},
            *recent,
        ]
        return result, memory
