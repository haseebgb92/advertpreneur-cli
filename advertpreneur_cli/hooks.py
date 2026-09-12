from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List


class HookRunner:
    """Small local lifecycle hook runner. Hooks are disabled unless explicitly configured.

    Project hooks live in .advertpreneur/hooks.json. They never call a model; each command
    runs in the project root with a short timeout and captured output.
    """

    EVENTS = {"pre_task", "post_task", "post_success", "post_failure"}

    def __init__(self, project: Path, enabled: bool = True) -> None:
        self.project = project.resolve()
        self.enabled = bool(enabled)
        self.path = self.project / ".advertpreneur" / "hooks.json"

    def config(self) -> Dict[str, List[str]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(k): [str(x) for x in (v or []) if str(x).strip()] for k, v in data.items() if k in self.EVENTS and isinstance(v, list)}
        except Exception:
            return {}

    def run(self, event: str) -> List[str]:
        if not self.enabled or event not in self.EVENTS:
            return []
        commands = self.config().get(event, [])[:8]
        rows: List[str] = []
        for command in commands:
            try:
                p = subprocess.run(command, cwd=str(self.project), shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                output = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
                rows.append(f"{event}: {command} · exit {p.returncode}" + (f" · {output[:500]}" if output else ""))
            except Exception as exc:
                rows.append(f"{event}: {command} · ERROR {exc}")
        return rows

    def init_example(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({
                "pre_task": [],
                "post_success": [],
                "post_failure": [],
                "post_task": [],
            }, indent=2), encoding="utf-8")
        return self.path
