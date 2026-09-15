from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from typing import Any, Dict, List


class BrowserRoutineError(RuntimeError):
    pass


class BrowserRoutineStore:
    """Small deterministic browser macro store.

    Learning is local: supported browser actions are recorded as semantic steps and
    can later be replayed without asking any model to rediscover the workflow.
    """

    RECORDABLE = {"navigate", "click", "fill", "scroll", "wait"}
    SENSITIVE_HINT = re.compile(r"password|passwd|secret|token|otp|pin|cvv|card|auth", re.I)

    def __init__(self, browser_dir: Path) -> None:
        self.browser_dir = browser_dir
        self.path = browser_dir / "routines.json"
        self.learning_path = browser_dir / ".learning-active.json"
        self.browser_dir.mkdir(parents=True, exist_ok=True)
        self._learning_name = ""
        self._learning_steps: List[Dict[str, Any]] = []
        self._learning_tabs: Dict[str, Dict[str, str]] = {}
        self._hydrate_learning()

    def _hydrate_learning(self) -> None:
        if self._learning_name:
            return
        try:
            row = json.loads(self.learning_path.read_text(encoding="utf-8")) if self.learning_path.exists() else {}
            if isinstance(row, dict):
                self._learning_name = str(row.get("name") or "").strip()[:80]
                steps = row.get("steps")
                self._learning_steps = list(steps) if isinstance(steps, list) else []
                self._learning_tabs = self._safe_tabs(row.get("protected_tabs") if isinstance(row.get("protected_tabs"), dict) else {})
        except Exception:
            self._learning_name = ""
            self._learning_steps = []
            self._learning_tabs = {}

    def _persist_learning(self) -> None:
        if not self._learning_name:
            try:
                self.learning_path.unlink(missing_ok=True)
            except Exception:
                pass
            return
        payload = {"name": self._learning_name, "protected_tabs": self._learning_tabs, "steps": self._learning_steps, "updated_at": time.time()}
        tmp = self.learning_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.learning_path)

    @property
    def learning(self) -> bool:
        self._hydrate_learning()
        return bool(self._learning_name)

    @property
    def learning_name(self) -> str:
        self._hydrate_learning()
        return self._learning_name

    def _load(self) -> Dict[str, Any]:
        try:
            row = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            return row if isinstance(row, dict) else {}
        except Exception:
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def names(self) -> List[str]:
        return sorted(self._load().keys(), key=str.lower)

    @staticmethod
    def _safe_url(value: object) -> str:
        try:
            parsed = urlsplit(str(value or ""))
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:500]
        except Exception:
            return ""

    def _safe_tabs(self, tabs: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        safe: Dict[str, Dict[str, str]] = {}
        for slot, row in tabs.items():
            key = "".join(ch for ch in str(slot or "").strip().lower() if ch.isalnum() or ch in "-_")[:40]
            if not key or not isinstance(row, dict):
                continue
            url = self._safe_url(row.get("url"))
            if not url:
                continue
            safe[key] = {"url": url, "title": " ".join(str(row.get("title") or "").split())[:160]}
        return safe

    def start(self, name: str, protected_tabs: Dict[str, Dict[str, str]] | None = None) -> str:
        clean = " ".join(str(name or "").strip().split())[:80]
        if not clean:
            raise BrowserRoutineError("Learn mode needs a routine name")
        self._learning_name = clean
        self._learning_steps = []
        self._learning_tabs = self._safe_tabs(protected_tabs or {})
        self._persist_learning()
        return clean

    def cancel(self) -> None:
        self._learning_name = ""
        self._learning_steps = []
        self._learning_tabs = {}
        self._persist_learning()

    def record(self, action: str, args: Dict[str, Any] | None = None, evidence: Dict[str, Any] | None = None) -> None:
        self._hydrate_learning()
        if not self.learning or action not in self.RECORDABLE:
            return
        safe_args = dict(args or {})
        if action == "fill":
            selector = str(safe_args.get("selector") or "")
            if self.SENSITIVE_HINT.search(selector):
                return
            if re.search(r"search|query|keyword", selector, re.I):
                safe_args["value"] = "[TEACH_KEYWORD]"
            else:
                return
        step: Dict[str, Any] = {"action": action, "args": safe_args}
        ev = dict(evidence or {})
        keep = {k: ev[k] for k in ("url", "before_url", "navigated", "verified", "title") if k in ev}
        if keep:
            step["evidence"] = keep
        self._learning_steps.append(step)
        self._persist_learning()

    def stop(self) -> Dict[str, Any]:
        self._hydrate_learning()
        if not self.learning:
            raise BrowserRoutineError("Learn mode is not active")
        name = self._learning_name
        data = self._load()
        row = {
            "name": name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "protected_tabs": self._learning_tabs,
            "steps": self._learning_steps,
        }
        data[name] = row
        self._save(data)
        self.cancel()
        return row

    def get(self, name: str) -> Dict[str, Any]:
        data = self._load()
        if name in data:
            return data[name]
        lookup = {k.lower(): k for k in data}
        actual = lookup.get(str(name or "").strip().lower())
        if actual:
            return data[actual]
        raise BrowserRoutineError(f"Browser routine not found: {name}")

    def delete(self, name: str) -> bool:
        data = self._load()
        key = next((k for k in data if k.lower() == str(name or "").lower()), None)
        if not key:
            return False
        del data[key]
        self._save(data)
        return True
