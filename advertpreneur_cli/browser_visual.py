from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


_SENSITIVE = ("password", "token", "cookie", "session")


@dataclass(frozen=True)
class VisualObservation:
    fingerprint: str
    capture_screenshot: bool
    evidence_allowed: bool


class BrowserVisualMemory:
    def __init__(self, root: Path, max_captures_per_tab: int = 3) -> None:
        self.path = Path(root) / ".advertpreneur" / "browser" / "visual-memory.json"
        self.max_captures_per_tab = max(1, int(max_captures_per_tab))
        self._seen: dict[str, set[str]] = {}

    @staticmethod
    def _text(value: object) -> str:
        return " ".join(str(value or "").lower().split())

    def _fingerprint(self, tab: str, url: str, title: str, controls: list[dict[str, object]]) -> str:
        labels = [self._text(row.get("label"))[:120] for row in controls[:24] if isinstance(row, dict)]
        payload = "\n".join((self._text(tab), str(url).split("?", 1)[0], self._text(title), *labels))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def observe(self, tab: str, url: str, title: str, controls: list[dict[str, object]]) -> VisualObservation:
        tab_key = self._text(tab) or "work"
        fingerprint = self._fingerprint(tab_key, url, title, controls)
        seen = self._seen.setdefault(tab_key, set())
        capture = fingerprint not in seen and len(seen) < self.max_captures_per_tab
        seen.add(fingerprint)
        return VisualObservation(fingerprint, capture, capture)

    def _read(self) -> list[dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def _write(self, rows: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def learn(self, tab: str, fingerprint: str, intent: str, label: str, selector: str) -> None:
        values = (tab, fingerprint, intent, label, selector)
        if not all(self._text(value) for value in values):
            return
        if any(word in self._text(value) for value in values for word in _SENSITIVE):
            return
        row = {"tab": self._text(tab), "fingerprint": str(fingerprint), "intent": self._text(intent), "label": str(label)[:120], "selector": str(selector)[:300]}
        rows = [item for item in self._read() if not (item.get("tab") == row["tab"] and item.get("fingerprint") == row["fingerprint"] and item.get("intent") == row["intent"])]
        rows.append(row)
        self._write(rows[-100:])

    def recall(self, tab: str, fingerprint: str, intent: str) -> str:
        wanted = (self._text(tab), str(fingerprint), self._text(intent))
        for row in reversed(self._read()):
            if (row.get("tab"), row.get("fingerprint"), row.get("intent")) == wanted:
                return str(row.get("selector") or "")
        return ""
