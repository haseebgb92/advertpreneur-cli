from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _words(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "into", "only", "actual", "current",
        "project", "code", "file", "files", "inspect", "verify", "check", "work", "working", "using",
        "what", "where", "when", "then", "than", "have", "has", "had", "does", "did", "not", "are",
        "was", "were", "will", "would", "should", "could", "please", "exact", "real", "source",
    }
    return {x for x in re.findall(r"[a-zA-Z0-9_./:-]{3,}", str(text or "").lower()) if x not in stop}


def _short(text: str, n: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def _is_validation_command(command: str) -> bool:
    low = " ".join(str(command or "").lower().split())
    markers = (
        "gradlew", "gradlew.bat", "pytest", "python -m pytest", "npm test", "npm run test",
        "npm run build", "npm run lint", "npm run typecheck", "pnpm test", "pnpm run test",
        "pnpm build", "pnpm run build", "pnpm lint", "pnpm typecheck", "yarn test", "yarn build",
        "yarn lint", "dotnet test", "dotnet build", "cargo test", "cargo check", "go test",
        "mvn test", "mvn verify", "cmake --build", "ctest", "flutter test", "flutter build",
        "phpunit", "composer test", "wp-env run tests", "wp-env run phpunit",
    )
    return any(x in low for x in markers)


class SessionWorkingRecord:
    """Local, deterministic evidence ledger for one coding session.

    Full read/search outputs are stored on disk locally so follow-up tasks can reuse
    already-proven evidence. Only a small, task-relevant extract is injected into a
    cloud request. File-backed evidence is trusted only while the file hash matches.
    No model/provider API is used here.
    """

    VERSION = 1

    def __init__(self, directory: Path, session_id: str, project: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_id = str(session_id)
        self.project = project.resolve()
        self.path = self.directory / f"{self.session_id}.json"
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("events", [])
                    data.setdefault("tasks", [])
                    return data
            except Exception:
                pass
        return {
            "version": self.VERSION,
            "session_id": self.session_id,
            "project": str(self.project),
            "created_at": _now(),
            "updated_at": _now(),
            "events": [],
            "tasks": [],
        }

    def rebind(self, session_id: str, project: Path) -> None:
        self.session_id = str(session_id)
        self.project = project.resolve()
        self.path = self.directory / f"{self.session_id}.json"
        self.data = self._load()

    def _save(self) -> None:
        self.data["updated_at"] = _now()
        self.data["project"] = str(self.project)
        # Bound the ledger. Recent evidence is more valuable and this avoids unbounded disk growth.
        self.data["events"] = list(self.data.get("events") or [])[-500:]
        self.data["tasks"] = list(self.data.get("tasks") or [])[-80:]
        try:
            self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _file_hash(self, relative: str) -> str:
        try:
            p = (self.project / relative).resolve()
            p.relative_to(self.project)
            if not p.exists() or not p.is_file():
                return ""
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(131072), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _range_from_numbered(result: str) -> tuple[int, int]:
        nums: List[int] = []
        for line in str(result or "").splitlines():
            m = re.match(r"^(\d+):", line)
            if m:
                nums.append(int(m.group(1)))
        return (min(nums), max(nums)) if nums else (0, 0)

    def record_tool(self, task: str, name: str, args: Dict[str, Any], result: str) -> None:
        name = str(name or "")
        args = dict(args or {})
        event: Dict[str, Any] = {
            "at": _now(),
            "task": _short(task, 1000),
            "name": name,
            "args": args,
        }
        if name == "read_file":
            path = str(args.get("path") or "")
            start, end = self._range_from_numbered(result)
            event.update({
                "path": path,
                "start_line": start or int(args.get("start_line") or 1),
                "end_line": end,
                "file_hash": self._file_hash(path),
                "result": str(result or "")[:12000],
            })
        elif name == "search_text":
            event["result"] = str(result or "")[:10000]
        elif name == "project_map":
            event["result"] = str(result or "")[:6000]
        elif name == "run_command":
            raw = str(result or "")
            first = raw.splitlines()[0] if raw else ""
            code = None
            m = re.match(r"exit_code=(-?\d+)", first)
            if m:
                code = int(m.group(1))
            event.update({
                "exit_code": code,
                "result": raw[:12000],
            })
        elif name in {"write_file", "replace_in_file"}:
            path = str(args.get("path") or "")
            event.update({"path": path, "file_hash": self._file_hash(path), "result": _short(result, 1000)})
            # Earlier source evidence for a mutated file is immediately stale.
            for old in self.data.get("events") or []:
                if old.get("path") == path and old.get("name") == "read_file":
                    old["stale"] = True
        else:
            event["result"] = str(result or "")[:3000]
        self.data.setdefault("events", []).append(event)
        self._save()

    def record_error(self, task: str, name: str, args: Dict[str, Any], error: str) -> None:
        self.data.setdefault("events", []).append({
            "at": _now(), "task": _short(task, 1000), "name": str(name or "tool"),
            "args": dict(args or {}), "error": _short(error, 1800), "failed": True,
        })
        self._save()

    def record_task(self, task: str, result: str, status: str, changed_files: Iterable[str] = ()) -> None:
        text = str(result or "")
        unresolved: List[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            low = sentence.lower()
            if any(x in low for x in ("remaining", "unresolved", "not verified", "not run", "failed", "missing", "unknown", "blocked")):
                unresolved.append(_short(sentence, 260))
            if len(unresolved) >= 5:
                break
        self.data.setdefault("tasks", []).append({
            "at": _now(),
            "task": _short(task, 1800),
            "status": status,
            "result": _short(text, 1800),
            "changed_files": list(changed_files)[:100],
            "unresolved": unresolved,
        })
        self._save()

    def _event_current(self, event: Dict[str, Any]) -> bool:
        if event.get("stale"):
            return False
        path = str(event.get("path") or "")
        expected = str(event.get("file_hash") or "")
        if not path or not expected:
            return True
        return self._file_hash(path) == expected

    @staticmethod
    def _relevant_lines(result: str, query_terms: set[str], cap: int = 8) -> List[str]:
        lines = [x for x in str(result or "").splitlines() if x.strip()]
        if not lines:
            return []
        scored: List[tuple[int, int, str]] = []
        for i, line in enumerate(lines):
            low = line.lower()
            score = sum(3 for t in query_terms if t in low)
            if re.match(r"^\d+:", line) or re.search(r":\d+:", line):
                score += 1
            if any(k in low for k in ("build successful", "build failed", "passed", "failed", "exit_code=", "authorization", "bearer", "token", "auth")):
                score += 1
            if score:
                scored.append((score, -i, line))
        scored.sort(reverse=True)
        chosen = [line for _s, _i, line in scored[:cap]]
        if chosen:
            return chosen
        return lines[: min(3, cap)]

    def context(self, query: str, max_chars: int = 1800) -> str:
        terms = _words(query)
        if not terms:
            return ""
        candidates: List[tuple[int, Dict[str, Any]]] = []
        for event in self.data.get("events") or []:
            if event.get("failed") or not self._event_current(event):
                continue
            hay = " ".join([
                str(event.get("task") or ""), str(event.get("name") or ""),
                json.dumps(event.get("args") or {}, ensure_ascii=False), str(event.get("path") or ""),
            ]).lower()
            score = sum(2 for t in terms if t in hay)
            # Authentication/token/build/test evidence is often relevant even if the follow-up wording shifts.
            result_low = str(event.get("result") or "").lower()
            score += min(4, sum(1 for t in terms if t in result_low))
            if score > 0:
                candidates.append((score, event))
        candidates.sort(key=lambda x: (x[0], x[1].get("at", "")), reverse=True)

        task_rows: List[tuple[int, Dict[str, Any]]] = []
        for task in self.data.get("tasks") or []:
            hay = (str(task.get("task") or "") + " " + str(task.get("result") or "")).lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                task_rows.append((score, task))
        task_rows.sort(key=lambda x: (x[0], x[1].get("at", "")), reverse=True)

        if not candidates and not task_rows:
            return ""

        lines = [
            "Verified local session evidence (zero-cloud cache; file-backed entries below are current by hash).",
            "Reuse this before re-reading unchanged code. Do not treat prior conclusions as proof when exact source/build evidence is absent.",
        ]
        for _score, task in task_rows[:2]:
            lines.append(f"Prior outcome: {_short(task.get('result', ''), 360)}")
            for u in task.get("unresolved") or []:
                lines.append(f"Unresolved: {_short(u, 220)}")

        seen: set[tuple[str, str]] = set()
        for _score, event in candidates[:7]:
            name = str(event.get("name") or "")
            args = event.get("args") or {}
            path = str(event.get("path") or args.get("path") or "")
            key = (name, path or json.dumps(args, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            if name == "read_file":
                start = int(event.get("start_line") or 0)
                end = int(event.get("end_line") or 0)
                rng = f":{start}-{end}" if start and end else ""
                lines.append(f"Inspected: {path}{rng} (unchanged)")
                for evidence_line in self._relevant_lines(str(event.get("result") or ""), terms, cap=4):
                    lines.append("  " + _short(evidence_line, 300))
            elif name == "search_text":
                pattern = str(args.get("pattern") or "")
                scope = str(args.get("path") or ".")
                snippets = self._relevant_lines(str(event.get("result") or ""), terms, cap=3)
                lines.append(f"Searched: {pattern!r} in {scope}")
                lines.extend("  " + _short(x, 300) for x in snippets)
            elif name == "run_command":
                cmd = _short(str(args.get("command") or ""), 260)
                code = event.get("exit_code")
                lines.append(f"Executed: {cmd} · exit {code if code is not None else '?'}")
                for x in self._relevant_lines(str(event.get("result") or ""), terms, cap=3):
                    if not x.startswith("exit_code="):
                        lines.append("  " + _short(x, 280))
            elif name == "project_map":
                lines.append(f"Mapped: {_short(str(args.get('query') or ''), 220)}")
            elif name == "browser":
                action = str(args.get("action") or "")
                lines.append(f"Browser evidence ({action}): {_short(str(event.get('result') or ''), 520)}")
            elif name == "web_search":
                lines.append(f"Web researched: {_short(str(args.get('query') or ''), 220)}")
                for x in self._relevant_lines(str(event.get("result") or ""), terms, cap=3):
                    lines.append("  " + _short(x, 300))
            elif name == "web_fetch":
                lines.append(f"Web source fetched: {_short(str(args.get('url') or ''), 260)}")
            if sum(len(x) + 1 for x in lines) >= max_chars:
                break
        text = "\n".join(lines)
        return text[:max_chars]

    def backfill_from_messages(self, messages: List[Dict[str, Any]], max_messages: int = 240) -> int:
        """Recover evidence from older saved sessions created before this ledger existed."""
        if self.data.get("events"):
            return 0
        current_task = "restored session"
        pending: List[tuple[str, Dict[str, Any]]] = []
        added = 0
        for msg in list(messages or [])[-max_messages:]:
            role = msg.get("role")
            if role == "user":
                current_task = _short(str(msg.get("content") or ""), 1200)
            elif role == "assistant":
                for call in msg.get("tool_calls") or []:
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
                    pending.append((name, dict(args) if isinstance(args, dict) else {}))
            elif role == "tool" and pending:
                name, args = pending.pop(0)
                content = str(msg.get("content") or "")
                # Ignore receipts that no longer contain source/command evidence.
                if "result already consumed" in content or "DUPLICATE_SKIPPED" in content:
                    continue
                self.record_tool(current_task, name, args, content)
                added += 1
        return added

    def bridge_summary(self, task: str) -> Dict[str, Any]:
        """Small structured evidence delta for the completed Browser Bridge result."""
        key = _short(task, 1000)
        events = [e for e in (self.data.get("events") or []) if str(e.get("task") or "") == key]
        inspected: List[Dict[str, Any]] = []
        searches: List[Dict[str, Any]] = []
        commands: List[Dict[str, Any]] = []
        changes: List[str] = []
        browser_evidence: List[Dict[str, Any]] = []
        research: List[Dict[str, Any]] = []
        errors: List[str] = []
        for e in events[-80:]:
            name = str(e.get("name") or "")
            args = e.get("args") or {}
            if e.get("failed"):
                errors.append(_short(str(e.get("error") or ""), 260))
                continue
            if name == "read_file" and self._event_current(e):
                inspected.append({
                    "path": str(e.get("path") or args.get("path") or ""),
                    "lines": [int(e.get("start_line") or 0), int(e.get("end_line") or 0)],
                    "current": True,
                })
            elif name == "search_text":
                searches.append({
                    "pattern": str(args.get("pattern") or "")[:180],
                    "path": str(args.get("path") or ".")[:240],
                })
            elif name == "run_command":
                result = str(e.get("result") or "")
                proof = []
                for line in result.splitlines():
                    low = line.lower()
                    if any(k in low for k in ("build successful", "build failed", "passed", "failed", "tests", "exit_code=")):
                        proof.append(_short(line, 240))
                    if len(proof) >= 4:
                        break
                commands.append({
                    "command": str(args.get("command") or "")[:360],
                    "cwd": str(args.get("cwd") or ".")[:220],
                    "exit_code": e.get("exit_code"),
                    "proof": proof,
                })
            elif name == "browser":
                browser_evidence.append({
                    "action": str(args.get("action") or "")[:60],
                    "url": str(args.get("url") or "")[:360],
                    "selector": str(args.get("selector") or "")[:180],
                    "result": _short(str(e.get("result") or ""), 700),
                })
            elif name in {"web_search", "web_fetch"}:
                research.append({
                    "tool": name,
                    "query_or_url": str(args.get("query") or args.get("url") or "")[:420],
                    "result": _short(str(e.get("result") or ""), 600),
                })
            elif name in {"write_file", "replace_in_file"}:
                path = str(e.get("path") or args.get("path") or "")
                if path:
                    changes.append(path)
        def dedupe_dicts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            seen = set()
            for row in rows:
                raw = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
                if raw in seen:
                    continue
                seen.add(raw)
                out.append(row)
            return out
        return {
            "inspected_files": dedupe_dicts(inspected)[:20],
            "searches": dedupe_dicts(searches)[:15],
            "commands": dedupe_dicts(commands)[:12],
            "changed_paths": list(dict.fromkeys(changes))[:30],
            "browser_evidence": dedupe_dicts(browser_evidence)[:12],
            "research": dedupe_dicts(research)[:12],
            "tool_errors": list(dict.fromkeys(errors))[:8],
            "validation_command_executed": any(_is_validation_command(c.get("command", "")) for c in commands),
            "validation_passed": any(_is_validation_command(c.get("command", "")) and c.get("exit_code") == 0 for c in commands),
            "cloud_tokens": 0,
        }

    def summary(self) -> Dict[str, Any]:
        current = sum(1 for e in self.data.get("events") or [] if not e.get("failed") and self._event_current(e))
        return {
            "path": str(self.path),
            "events": len(self.data.get("events") or []),
            "current_events": current,
            "tasks": len(self.data.get("tasks") or []),
        }
