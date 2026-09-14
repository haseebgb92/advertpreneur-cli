from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


STEP_STATES = {"pending", "active", "waiting", "verified", "failed", "skipped"}
_ALLOWED_TRANSITIONS = {
    "pending": {"active", "waiting", "verified", "skipped"},
    "active": {"waiting", "verified", "failed", "skipped"},
    "waiting": {"active", "failed", "skipped"},
    "verified": set(),
    "failed": {"active", "skipped"},
    "skipped": set(),
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class MissionStep:
    id: str
    title: str
    kind: str
    state: str = "pending"
    evidence: list[str] = field(default_factory=list)
    observed_evidence: list["EvidenceItem"] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MissionStep":
        return cls(
            id=str(value.get("id") or ""),
            title=str(value.get("title") or "Untitled step"),
            kind=str(value.get("kind") or "inspect"),
            state=str(value.get("state") or "pending"),
            evidence=[str(item) for item in value.get("evidence", [])],
            observed_evidence=[EvidenceItem.from_dict(item) for item in value.get("observed_evidence", []) if isinstance(item, dict)],
        )


@dataclass
class EvidenceItem:
    kind: str
    location: str
    observed_at: str = field(default_factory=_now)
    producer: str = "local"
    summary: str = ""
    verified: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceItem":
        return cls(
            kind=str(value.get("kind") or "inspection"),
            location=str(value.get("location") or ""),
            observed_at=str(value.get("observed_at") or _now()),
            producer=str(value.get("producer") or "local"),
            summary=str(value.get("summary") or ""),
            verified=bool(value.get("verified", True)),
        )


@dataclass
class Mission:
    id: str
    request: str
    status: str
    created_at: str
    updated_at: str
    steps: list[MissionStep]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Mission":
        return cls(
            id=str(value.get("id") or ""),
            request=str(value.get("request") or ""),
            status=str(value.get("status") or "planned"),
            created_at=str(value.get("created_at") or _now()),
            updated_at=str(value.get("updated_at") or _now()),
            steps=[MissionStep.from_dict(item) for item in value.get("steps", []) if isinstance(item, dict)],
        )


@dataclass
class AttentionRequest:
    id: str
    mission_id: str
    step_id: str
    reason: str
    options: list[str]
    created_at: str
    resolved_at: str = ""
    response: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AttentionRequest":
        return cls(
            id=str(value.get("id") or ""),
            mission_id=str(value.get("mission_id") or ""),
            step_id=str(value.get("step_id") or ""),
            reason=str(value.get("reason") or "attention"),
            options=[str(item) for item in value.get("options", [])],
            created_at=str(value.get("created_at") or _now()),
            resolved_at=str(value.get("resolved_at") or ""),
            response=str(value.get("response") or ""),
        )


class MissionStore:
    """Project-local mission snapshots with append-only lifecycle events."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self.directory = self.project / ".advertpreneur" / "missions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, mission_id: str) -> Path:
        return self.directory / f"{mission_id}.json"

    def _events_path(self, mission_id: str) -> Path:
        return self.directory / f"{mission_id}.jsonl"

    @property
    def _attention_path(self) -> Path:
        return self.directory / "attention.json"

    def _load_attention(self) -> list[AttentionRequest]:
        try:
            raw = json.loads(self._attention_path.read_text(encoding="utf-8"))
            return [AttentionRequest.from_dict(item) for item in raw if isinstance(item, dict)]
        except Exception:
            return []

    def _save_attention(self, rows: list[AttentionRequest]) -> None:
        target = self._attention_path
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps([asdict(row) for row in rows[-200:]], indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)

    def _save(self, mission: Mission, event: str, payload: dict[str, Any]) -> Mission:
        mission.updated_at = _now()
        target = self._path(mission.id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(mission), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
        row = {"at": mission.updated_at, "event": event, "payload": payload}
        with self._events_path(mission.id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return mission

    def create(self, request: str, steps: list[dict[str, Any]]) -> Mission:
        created = _now()
        mission = Mission(
            id=uuid.uuid4().hex,
            request=str(request),
            status="planned",
            created_at=created,
            updated_at=created,
            steps=[
                MissionStep(
                    id=uuid.uuid4().hex[:12],
                    title=str(item.get("title") or "Untitled step"),
                    kind=str(item.get("kind") or "inspect"),
                    evidence=[str(entry) for entry in item.get("evidence", [])],
                )
                for item in steps
            ],
        )
        return self._save(mission, "created", {"request": mission.request})

    def load(self, mission_id: str) -> Mission:
        try:
            return Mission.from_dict(json.loads(self._path(mission_id).read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise KeyError(f"Unknown mission: {mission_id}") from exc

    def transition_step(self, mission_id: str, step_id: str, state: str) -> Mission:
        if state not in STEP_STATES:
            raise ValueError(f"Unknown mission step state: {state}")
        mission = self.load(mission_id)
        step = next((item for item in mission.steps if item.id == step_id), None)
        if step is None:
            raise KeyError(f"Unknown mission step: {step_id}")
        if state not in _ALLOWED_TRANSITIONS[step.state]:
            raise ValueError(f"Illegal mission transition: {step.state} -> {state}")
        step.state = state
        mission.status = "active" if state == "active" else "waiting" if state == "waiting" else mission.status
        return self._save(mission, "step_transition", {"step_id": step_id, "state": state})

    def complete(self, mission_id: str) -> bool:
        mission = self.load(mission_id)
        if not mission.steps or any(step.state != "verified" for step in mission.steps):
            return False
        mission.status = "completed"
        self._save(mission, "completed", {})
        return True

    def record_evidence(self, mission_id: str, step_id: str, item: EvidenceItem) -> Mission:
        mission = self.load(mission_id)
        step = next((entry for entry in mission.steps if entry.id == step_id), None)
        if step is None:
            raise KeyError(f"Unknown mission step: {step_id}")
        step.observed_evidence.append(item)
        return self._save(mission, "evidence", {"step_id": step_id, "kind": item.kind, "location": item.location})

    def verify_step(self, mission_id: str, step_id: str) -> bool:
        mission = self.load(mission_id)
        step = next((entry for entry in mission.steps if entry.id == step_id), None)
        if step is None:
            raise KeyError(f"Unknown mission step: {step_id}")
        observed = {item.kind for item in step.observed_evidence if item.verified}
        if not set(step.evidence).issubset(observed):
            return False
        if step.state != "verified":
            if "verified" not in _ALLOWED_TRANSITIONS[step.state]:
                raise ValueError(f"Illegal mission transition: {step.state} -> verified")
            step.state = "verified"
            self._save(mission, "step_verified", {"step_id": step_id})
        return True

    def request_attention(self, mission_id: str, step_id: str, reason: str, options: list[str]) -> AttentionRequest:
        mission = self.load(mission_id)
        step = next((entry for entry in mission.steps if entry.id == step_id), None)
        if step is None:
            raise KeyError(f"Unknown mission step: {step_id}")
        if step.state != "waiting":
            self.transition_step(mission_id, step_id, "waiting")
            mission = self.load(mission_id)
        request = AttentionRequest(uuid.uuid4().hex, mission_id, step_id, str(reason), [str(item) for item in options], _now())
        rows = self._load_attention()
        rows.append(request)
        self._save_attention(rows)
        return request

    def resolve_attention(self, request_id: str, response: str) -> AttentionRequest:
        rows = self._load_attention()
        request = next((item for item in rows if item.id == request_id), None)
        if request is None:
            raise KeyError(f"Unknown attention request: {request_id}")
        if request.resolved_at:
            return request
        if response not in request.options:
            raise ValueError(f"Unsupported attention response: {response}")
        request.response = response
        request.resolved_at = _now()
        mission = self.load(request.mission_id)
        step = next((entry for entry in mission.steps if entry.id == request.step_id), None)
        if step is None:
            raise KeyError(f"Unknown mission step: {request.step_id}")
        step.state = "active" if response in {"approve", "continue", "resume"} else "failed"
        mission.status = "active" if step.state == "active" else "failed"
        self._save(mission, "attention_resolved", {"request_id": request.id, "response": response})
        self._save_attention(rows)
        return request
