from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class SessionRecord:
    id: str
    name: str
    project: str
    provider: str
    model: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    metered_value_usd: float = 0.0
    archived: bool = False
    goal: str = ""
    pinned_mentions: List[str] = field(default_factory=list)
    bridge_enabled: bool = False
    bridge_autopilot: bool = True
    bridge_pair_token: str = ""
    provider_threads: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionRecord":
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name") or "Untitled session"),
            project=str(data.get("project") or "."),
            provider=str(data.get("provider") or "cloud"),
            model=str(data.get("model") or "gpt-oss:20b"),
            messages=list(data.get("messages") or []),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            requests=int(data.get("requests") or 0),
            metered_value_usd=float(data.get("metered_value_usd") or 0.0),
            archived=bool(data.get("archived", False)),
            goal=str(data.get("goal") or ""),
            pinned_mentions=[str(x) for x in (data.get("pinned_mentions") or [])],
            bridge_enabled=bool(data.get("bridge_enabled", False)),
            bridge_autopilot=bool(data.get("bridge_autopilot", True)),
            bridge_pair_token=str(data.get("bridge_pair_token") or ""),
            provider_threads={str(k): str(v) for k, v in dict(data.get("provider_threads") or {}).items() if v},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "project": self.project,
            "provider": self.provider,
            "model": self.model,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "requests": self.requests,
            "metered_value_usd": self.metered_value_usd,
            "archived": self.archived,
            "goal": self.goal,
            "pinned_mentions": self.pinned_mentions,
            "bridge_enabled": self.bridge_enabled,
            "bridge_autopilot": self.bridge_autopilot,
            "bridge_pair_token": self.bridge_pair_token,
            "provider_threads": dict(self.provider_threads),
        }


class SessionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    def create(self, project: Path, provider: str, model: str, name: str | None = None) -> SessionRecord:
        stamp = datetime.now().astimezone().strftime("%b %d %H:%M")
        record = SessionRecord(
            id=str(uuid.uuid4()),
            name=name or f"{project.name or 'Project'} · {stamp}",
            project=str(project.resolve()),
            provider=provider,
            model=model,
        )
        self.save(record)
        return record

    def save(self, record: SessionRecord) -> None:
        record.updated_at = _now()
        path = self._path(record.id)
        path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, session_id: str) -> SessionRecord | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            return SessionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list(self, project: Path | None = None, limit: int = 50, include_archived: bool = False) -> List[SessionRecord]:
        records: List[SessionRecord] = []
        project_text = str(project.resolve()) if project else None
        for path in self.directory.glob("*.json"):
            try:
                rec = SessionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if project_text and rec.project != project_text:
                continue
            if rec.archived and not include_archived:
                continue
            records.append(rec)
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[:limit]


    def archive(self, session_id: str, archived: bool = True) -> None:
        rec = self.load(session_id)
        if not rec:
            return
        rec.archived = archived
        self.save(rec)

    def delete(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink()
        except FileNotFoundError:
            pass

    def fork(self, source: SessionRecord) -> SessionRecord:
        record = SessionRecord(
            id=str(uuid.uuid4()),
            name=f"{source.name} (fork)",
            project=source.project,
            provider=source.provider,
            model=source.model,
            messages=json.loads(json.dumps(source.messages)),
            input_tokens=source.input_tokens,
            output_tokens=source.output_tokens,
            requests=source.requests,
            metered_value_usd=source.metered_value_usd,
            goal=source.goal,
            pinned_mentions=list(source.pinned_mentions),
            bridge_enabled=False,
            bridge_autopilot=source.bridge_autopilot,
            bridge_pair_token="",
            provider_threads={},
        )
        self.save(record)
        return record
