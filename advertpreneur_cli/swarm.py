"""Multi-Agent Autonomous Swarm Mode: Architect, Coder, and Reviewer orchestration."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SwarmRole(str, Enum):
    ARCHITECT = "architect"
    CODER = "coder"
    REVIEWER = "reviewer"


class ParcelStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SwarmParcel:
    id: str
    role: SwarmRole
    title: str
    instruction: str
    status: ParcelStatus = ParcelStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    output: str = ""
    review_approved: bool = False
    review_feedback: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class SwarmMission:
    goal: str
    parcels: List[SwarmParcel] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None


class SwarmCoordinator:
    """Orchestrates multi-agent swarm parcel division, execution, and review loops."""

    def __init__(self, project_path: Path, event_sink: Optional[Callable[[str, str, str], None]] = None) -> None:
        self.project_path = Path(project_path).resolve()
        self.event_sink = event_sink
        self.active_mission: Optional[SwarmMission] = None

    def plan_mission(self, goal: str, breakdown: Optional[List[Dict[str, Any]]] = None) -> SwarmMission:
        """Create a structured multi-agent parcel execution plan."""
        parcels: List[SwarmParcel] = []
        if breakdown:
            for idx, item in enumerate(breakdown, 1):
                role_val = str(item.get("role", "coder")).lower()
                role = SwarmRole.REVIEWER if "review" in role_val else (
                    SwarmRole.ARCHITECT if "arch" in role_val else SwarmRole.CODER
                )
                parcels.append(
                    SwarmParcel(
                        id=f"p-{idx}",
                        role=role,
                        title=str(item.get("title") or f"Subtask {idx}"),
                        instruction=str(item.get("instruction") or ""),
                        dependencies=[str(d) for d in (item.get("dependencies") or [])],
                    )
                )
        else:
            # Default 3-stage triad: Architect plan -> Coder implement -> Reviewer audit
            parcels = [
                SwarmParcel(
                    id="p-1",
                    role=SwarmRole.ARCHITECT,
                    title="System Architecture & Task Scoping",
                    instruction=f"Analyze repository context and draft atomic implementation steps for: '{goal}'",
                ),
                SwarmParcel(
                    id="p-2",
                    role=SwarmRole.CODER,
                    title="Code Implementation & Verification",
                    instruction=f"Implement required changes across affected modules for: '{goal}'",
                    dependencies=["p-1"],
                ),
                SwarmParcel(
                    id="p-3",
                    role=SwarmRole.REVIEWER,
                    title="Quality, Security & Regression Audit",
                    instruction=f"Inspect code diffs and run verification suite for: '{goal}'",
                    dependencies=["p-2"],
                ),
            ]

        self.active_mission = SwarmMission(goal=goal, parcels=parcels)
        self._emit("swarm", "Swarm Mission Planned", f"Goal: {goal} ({len(parcels)} parcels)")
        return self.active_mission

    def next_pending_parcel(self) -> Optional[SwarmParcel]:
        if not self.active_mission:
            return None
        completed_ids = {p.id for p in self.active_mission.parcels if p.status == ParcelStatus.COMPLETED}
        for parcel in self.active_mission.parcels:
            if parcel.status == ParcelStatus.PENDING:
                if all(dep in completed_ids for dep in parcel.dependencies):
                    return parcel
        return None

    def start_parcel(self, parcel_id: str) -> Optional[SwarmParcel]:
        if not self.active_mission:
            return None
        for p in self.active_mission.parcels:
            if p.id == parcel_id:
                p.status = ParcelStatus.IN_PROGRESS
                self._emit("swarm", f"Swarm [{p.role.value.upper()}] Started", p.title)
                return p
        return None

    def complete_parcel(self, parcel_id: str, output: str, approved: bool = True, feedback: str = "") -> Optional[SwarmParcel]:
        if not self.active_mission:
            return None
        for p in self.active_mission.parcels:
            if p.id == parcel_id:
                p.status = ParcelStatus.COMPLETED if approved else ParcelStatus.FAILED
                p.output = output
                p.review_approved = approved
                p.review_feedback = feedback
                self._emit(
                    "swarm",
                    f"Swarm [{p.role.value.upper()}] Completed",
                    f"{p.title} - {'Approved' if approved else 'Needs Refinement'}",
                )
                self._check_mission_done()
                return p
        return None

    def _check_mission_done(self) -> None:
        if not self.active_mission:
            return
        all_done = all(p.status in (ParcelStatus.COMPLETED, ParcelStatus.FAILED) for p in self.active_mission.parcels)
        if all_done:
            self.active_mission.status = "completed"
            self.active_mission.completed_at = _now()
            self._emit("swarm", "Swarm Mission Concluded", f"Goal: {self.active_mission.goal}")

    def _emit(self, kind: str, title: str, detail: str = "") -> None:
        if self.event_sink:
            try:
                self.event_sink(kind, title, detail)
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        if not self.active_mission:
            return {}
        return asdict(self.active_mission)
