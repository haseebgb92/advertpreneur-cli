"""Browser Macro Engine: record, serialize, and replay deterministic zero-token browser workflows."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MacroStep:
    action: str  # navigate, click, fill, wait, screenshot, download
    target: str = ""  # url or selector
    value: str = ""  # text value or wait milliseconds
    tab: str = "work"
    timeout_ms: int = 15000

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MacroStep":
        return cls(
            action=str(data.get("action") or ""),
            target=str(data.get("target") or ""),
            value=str(data.get("value") or ""),
            tab=str(data.get("tab") or "work"),
            timeout_ms=int(data.get("timeout_ms", 15000)),
        )


@dataclass
class BrowserMacro:
    name: str
    description: str
    steps: List[MacroStep] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserMacro":
        return cls(
            name=str(data.get("name") or "untitled"),
            description=str(data.get("description") or ""),
            steps=[MacroStep.from_dict(x) for x in (data.get("steps") or []) if isinstance(x, dict)],
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
        )


class BrowserMacroStore:
    """Persistent storage and execution runner for browser macros in <project>/.advertpreneur/macros/."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self.macros_dir = self.project / ".advertpreneur" / "macros"
        self.macros_dir.mkdir(parents=True, exist_ok=True)
        self._active_recording: Optional[BrowserMacro] = None

    def list_macros(self) -> List[str]:
        return sorted([p.stem for p in self.macros_dir.glob("*.json")])

    def load(self, name: str) -> Optional[BrowserMacro]:
        path = self.macros_dir / f"{name}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BrowserMacro.from_dict(data)
        except Exception:
            return None

    def save(self, macro: BrowserMacro) -> Path:
        macro.updated_at = _now()
        path = self.macros_dir / f"{macro.name}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(macro), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def start_recording(self, name: str, description: str = "") -> BrowserMacro:
        self._active_recording = BrowserMacro(
            name=name,
            description=description,
            steps=[],
        )
        return self._active_recording

    def record_step(self, action: str, target: str = "", value: str = "", tab: str = "work") -> None:
        if self._active_recording is None:
            return
        self._active_recording.steps.append(
            MacroStep(action=action, target=target, value=value, tab=tab)
        )

    def stop_recording(self) -> Optional[BrowserMacro]:
        macro = self._active_recording
        if macro and macro.steps:
            self.save(macro)
        self._active_recording = None
        return macro

    def play(self, name: str, controller: Any) -> Dict[str, Any]:
        """Replay a recorded macro step-by-step against BrowserController."""
        macro = self.load(name)
        if not macro:
            return {"ok": False, "error": f"Macro '{name}' not found", "steps_run": 0}

        steps_run = 0
        for i, step in enumerate(macro.steps):
            try:
                if step.action == "navigate":
                    controller.navigate(step.target, tab=step.tab)
                elif step.action == "click":
                    controller.click(step.target, tab=step.tab)
                elif step.action == "fill":
                    controller.fill(step.target, step.value, tab=step.tab)
                elif step.action == "wait":
                    ms = int(step.value) if step.value.isdigit() else 1000
                    controller.wait(ms, tab=step.tab)
                elif step.action == "screenshot":
                    controller.screenshot(name=step.value or f"macro-{name}-{i}")
                steps_run += 1
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"Step {i+1} ({step.action} {step.target}) failed: {exc}",
                    "steps_run": steps_run,
                }

        return {"ok": True, "error": "", "steps_run": steps_run, "total_steps": len(macro.steps)}
