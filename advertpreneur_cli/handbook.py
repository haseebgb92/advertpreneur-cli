from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _words(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "your", "project", "file", "files", "code", "task", "work", "working", "make", "use", "using"}
    return {x for x in re.findall(r"[a-z0-9_./:-]{3,}", str(text or "").lower()) if x not in stop}


def _flat(text: str, n: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= n else value[: n - 1] + "…"


def _is_validation_command(command: str) -> bool:
    """Only successful build/test/lint/typecheck commands can promote experience to PROVEN."""
    low = " ".join(str(command or "").lower().split())
    markers = (
        "gradlew", "gradlew.bat", "pytest", "python -m pytest", "npm test", "npm run test",
        "npm run build", "npm run lint", "npm run typecheck", "pnpm test", "pnpm run test",
        "pnpm build", "pnpm run build", "pnpm lint", "pnpm typecheck", "yarn test",
        "yarn build", "yarn lint", "dotnet test", "dotnet build", "cargo test", "cargo check",
        "go test", "mvn test", "mvn verify", "cmake --build", "ctest", "flutter test",
        "flutter build", "phpunit", "composer test", "wp-env run tests", "wp-env run phpunit",
    )
    return any(x in low for x in markers)


class ExperienceHandbook:
    """Validated local experience memory.

    Automatic entries are conservative. A successful task becomes PROVEN only when
    a real command with exit code 0 was recorded in the task evidence. Otherwise the
    entry is PROJECT-SPECIFIC and is never presented as proof. Failed commands are
    recorded as FAILED so the model can avoid repeating the same dead end.
    """

    VERSION = 1

    def __init__(self, app_dir: Path, project: Path) -> None:
        self.app_dir = app_dir
        self.project = project.resolve()
        self.path = app_dir / "engineering-handbook.json"
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("entries", [])
                    return data
            except Exception:
                pass
        return {"version": self.VERSION, "entries": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["entries"] = list(self.data.get("entries") or [])[-800:]
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def learn_task(self, task: str, result: str, status: str, evidence: Dict[str, Any], changed_files: Iterable[str] = ()) -> str | None:
        commands = list(evidence.get("commands") or [])
        successful_commands = [c for c in commands if c.get("exit_code") == 0]
        good = [c for c in successful_commands if _is_validation_command(c.get("command", ""))]
        bad = [c for c in commands if isinstance(c.get("exit_code"), int) and c.get("exit_code") != 0]
        changed = list(changed_files or [])
        # Do not turn ordinary read-only explanations into permanent "learning".
        # Keep the handbook for executed attempts, validated work, or actual changes.
        if not changed and not good and not bad:
            return None
        # Record failed attempts separately; they are useful even when the task later succeeds.
        for cmd in bad[-3:]:
            self.data.setdefault("entries", []).append({
                "id": str(uuid.uuid4()), "at": _now(), "project": str(self.project),
                "status": "FAILED", "task": _flat(task, 700),
                "recipe": f"Command failed: {_flat(cmd.get('command', ''), 320)}",
                "evidence": _flat(" ".join(cmd.get("proof") or []), 600),
                "changed_files": changed[:20],
            })
        if status not in {"completed", "ok", "success"} and not good:
            if bad:
                self._save()
            return None
        # A harmless command such as `git status` or `pwd` must never promote a recipe to PROVEN.
        confidence = "PROVEN" if good else "PROJECT-SPECIFIC"
        validation = "; ".join(_flat(c.get("command", ""), 220) for c in good[-3:])
        recipe = _flat(result, 1100)
        entry = {
            "id": str(uuid.uuid4()), "at": _now(), "project": str(self.project),
            "status": confidence, "task": _flat(task, 800), "recipe": recipe,
            "validation": validation, "changed_files": changed[:30],
        }
        # Avoid writing near-duplicate entries every bridge turn.
        terms = _words(task)
        for old in reversed(self.data.get("entries") or []):
            if str(old.get("project")) != str(self.project) or old.get("status") == "FAILED":
                continue
            overlap = len(terms & _words(old.get("task", "")))
            if overlap >= max(3, min(8, len(terms) // 2)) and _flat(old.get("recipe", ""), 220) == _flat(recipe, 220):
                return old.get("id")
        self.data.setdefault("entries", []).append(entry)
        self._save()
        return entry["id"]

    def search(self, query: str, limit: int = 5, include_other_projects: bool = True) -> List[Dict[str, Any]]:
        terms = _words(query)
        rows = []
        for entry in self.data.get("entries") or []:
            hay = f"{entry.get('task','')} {entry.get('recipe','')} {entry.get('validation','')}".lower()
            score = sum(3 if t in str(entry.get("task", "")).lower() else 1 for t in terms if t in hay)
            if str(entry.get("project")) == str(self.project):
                score += 4
            elif not include_other_projects:
                continue
            status = str(entry.get("status") or "")
            score += {"PROVEN": 4, "PROJECT-SPECIFIC": 1, "FAILED": 2}.get(status, 0)
            if score:
                rows.append((score, str(entry.get("at") or ""), entry))
        rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [dict(r[2]) for r in rows[: max(1, min(20, int(limit)))]]

    def context(self, query: str, max_chars: int = 1500) -> str:
        rows = self.search(query, limit=4)
        if not rows:
            return ""
        out = ["Advertpreneur engineering handbook (local past experience; prefer PROVEN entries, never repeat FAILED recipes blindly):"]
        for e in rows:
            status = e.get("status") or "PROJECT-SPECIFIC"
            out.append(f"[{status}] {_flat(e.get('task',''), 220)}")
            out.append("  " + _flat(e.get("recipe", ""), 420))
            if e.get("validation"):
                out.append("  Validated by: " + _flat(e.get("validation", ""), 280))
        return "\n".join(out)[:max_chars]

    def stats(self) -> Dict[str, int]:
        stats = {"PROVEN": 0, "PROJECT-SPECIFIC": 0, "FAILED": 0}
        for e in self.data.get("entries") or []:
            s = str(e.get("status") or "PROJECT-SPECIFIC")
            stats[s] = stats.get(s, 0) + 1
        return stats
