"""Safer cross-session automation memory with sensitive-data rejection and bounded context."""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_CATEGORIES = {
    "selectors",
    "conventions",
    "commands",
    "paths",
    "recovery",
    "evidence_links",
    "project_notes",
}

ALLOWED_SCOPES = {"project", "domain", "global"}

_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(?:password|passwd|secret|api_key|token|auth_token|bearer\s+[a-zA-Z0-9_\-\.]{16,})"),
    re.compile(r"(?i)(?:ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{40,}|sk-[a-zA-Z0-9]{32,})"),
    re.compile(r"(?i)(?:sessionid|csrf_token|set-cookie)\s*[:=]"),
    re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),  # Credit card pattern
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


class MemorySecurityError(ValueError):
    """Raised when an entry contains sensitive or unapproved information."""
    pass


def is_sensitive(text: str) -> bool:
    """Return True if the text contains credentials, keys, tokens, or sensitive patterns."""
    source = str(text or "")
    return any(pat.search(source) for pat in _SENSITIVE_PATTERNS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MemoryEntry:
    id: str
    key: str
    value: str
    category: str
    scope: str = "project"
    confidence: float = 1.0
    provenance: str = "observed"
    created_at: str = field(default_factory=_now)
    expires_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            key=str(data.get("key") or ""),
            value=str(data.get("value") or ""),
            category=str(data.get("category") or "project_notes"),
            scope=str(data.get("scope") or "project"),
            confidence=float(data.get("confidence", 1.0)),
            provenance=str(data.get("provenance") or "observed"),
            created_at=str(data.get("created_at") or _now()),
            expires_at=str(data.get("expires_at") or ""),
        )


class AutomationMemory:
    """Persistent, sanitized knowledge store for learned project/domain automation signals."""

    def __init__(self, memory_dir: Path) -> None:
        self.directory = Path(memory_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.directory / "automation_memory.json"

    def _load(self) -> list[MemoryEntry]:
        if not self.memory_file.exists():
            return []
        try:
            data = json.loads(self.memory_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [MemoryEntry.from_dict(item) for item in data if isinstance(item, dict)]
        except Exception:
            return []
        return []

    def _save(self, entries: list[MemoryEntry]) -> None:
        temporary = self.memory_file.with_suffix(".json.tmp")
        payload = [asdict(e) for e in entries[-500:]]
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.memory_file)

    def save_entry(
        self,
        key: str,
        value: str,
        category: str,
        scope: str = "project",
        confidence: float = 1.0,
        provenance: str = "observed",
        expires_at: str = "",
    ) -> MemoryEntry:
        clean_cat = str(category).lower().strip()
        if clean_cat not in ALLOWED_CATEGORIES:
            raise ValueError(f"Category '{category}' is not in allowed categories: {ALLOWED_CATEGORIES}")
        clean_scope = str(scope).lower().strip()
        if clean_scope not in ALLOWED_SCOPES:
            raise ValueError(f"Scope '{scope}' is not in allowed scopes: {ALLOWED_SCOPES}")
        if is_sensitive(key) or is_sensitive(value):
            raise MemorySecurityError("Sensitive credentials or secrets cannot be stored in automation memory.")

        entries = self._load()
        # Deduplicate or update existing matching key in same category and scope
        existing = next((e for e in entries if e.key == key and e.category == clean_cat and e.scope == clean_scope), None)
        if existing:
            existing.value = str(value)
            existing.confidence = float(confidence)
            existing.provenance = str(provenance)
            existing.expires_at = str(expires_at)
            entry = existing
        else:
            entry = MemoryEntry(
                id=uuid.uuid4().hex[:12],
                key=str(key),
                value=str(value),
                category=clean_cat,
                scope=clean_scope,
                confidence=float(confidence),
                provenance=str(provenance),
                expires_at=str(expires_at),
            )
            entries.append(entry)

        self._save(entries)
        return entry

    def query(self, category: str = "", scope: str = "", query_text: str = "") -> list[MemoryEntry]:
        entries = self._load()
        result: list[MemoryEntry] = []
        q_low = str(query_text or "").lower()
        for e in entries:
            if category and e.category != category:
                continue
            if scope and e.scope != scope:
                continue
            if q_low and (q_low not in e.key.lower() and q_low not in e.value.lower()):
                continue
            result.append(e)
        return result

    def context(self, task_text: str = "", max_chars: int = 1200) -> str:
        """Render relevant automation memory items into a compact LLM prompt context block."""
        entries = self._load()
        if not entries:
            return ""
        q_words = set(re.findall(r"\w+", str(task_text or "").lower()))
        scored: list[tuple[int, MemoryEntry]] = []
        for e in entries:
            score = 0
            e_words = set(re.findall(r"\w+", f"{e.key} {e.value}".lower()))
            common = q_words.intersection(e_words)
            score += len(common) * 2
            if e.scope == "project":
                score += 1
            scored.append((score, e))

        scored.sort(key=lambda x: (x[0], x[1].confidence), reverse=True)
        lines: list[str] = ["## Learned Project & Automation Memory:"]
        total_len = len(lines[0])
        for _, entry in scored[:8]:
            line = f"- [{entry.category} / {entry.scope}] {entry.key}: {entry.value}"
            if total_len + len(line) + 1 > max_chars:
                break
            lines.append(line)
            total_len += len(line) + 1

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)
