from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Specialist:
    key: str
    title: str
    triggers: tuple[str, ...]
    policy: str


@dataclass
class WorkforceTask:
    id: str
    specialist: str
    instruction: str
    due_at: float
    mutation: bool = False
    production: bool = False
    state: str = "queued"


class Workforce:
    """Local, inspect-first specialist queue. It never stores credentials or hidden reasoning."""

    SPECIALISTS = (
        Specialist("operations_commander", "Operations Commander", ("due", "next", "status", "coordinate", "schedule"), "Inspect queue, prioritize, and delegate; never silently mutate production."),
        Specialist("wordpress_steward", "WordPress Site Steward", ("wordpress", "wp-admin", "plugin", "theme", "post", "page", "woocommerce", "media"), "Use browser/site playbooks, stage files, and verify live results."),
        Specialist("hosting_deployment", "Hosting & Deployment Agent", ("hostinger", "cpanel", "plesk", "deploy", "file manager", "archive", "cache"), "Use hosting playbooks, preserve rollback evidence, and verify target files/routes."),
        Specialist("incident_investigator", "Incident Investigator", ("failed", "failure", "error", "broken", "500", "incident", "outage", "timeout"), "Inspect evidence/logs first, research official sources when necessary, propose minimal repair."),
        Specialist("research_resolution", "Research & Resolution Agent", ("research", "official", "search", "resolution", "documentation", "advisory"), "Search current official sources, cite evidence, and create an actionable recommendation."),
        Specialist("qa_conversion", "QA & Conversion Agent", ("qa", "responsive", "checkout", "form", "conversion", "visual", "regression"), "Use browser evidence and verify real user paths without inventing success."),
        Specialist("release_guardian", "Release Guardian", ("release", "package", "zip", "build", "publish", "rollback"), "Validate artifacts, protect secrets, create recoverable release evidence."),
        Specialist("resource_cost", "Resource & Cost Agent", ("usage", "cost", "ram", "resource", "quota", "slow"), "Monitor local resource pressure and provider limits without autonomous spend."),
    )

    def __init__(self, project: Path) -> None:
        self.path = Path(project) / ".advertpreneur" / "workforce.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception: return {"tasks": [], "learning": {}, "last_activation": ""}

    def _save(self) -> None:
        self.data["tasks"] = self.data["tasks"][-250:]
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def select(self, instruction: str) -> Specialist:
        text = str(instruction).lower()
        if any(term in text for term in ("research", "official", "search", "resolution", "documentation", "advisory")):
            return self._specialist("research_resolution")
        if any(term in text for term in ("failed", "failure", "error", "broken", "500", "incident", "outage", "timeout")):
            return self._specialist("incident_investigator")
        ranked = []
        for row in self.SPECIALISTS:
            score = sum(1 for term in row.triggers if term in text)
            ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1] if ranked[0][0] else self.SPECIALISTS[0]

    def enqueue(self, specialist: str, instruction: str, due_at: float | None = None, mutation: bool = False, production: bool = False) -> WorkforceTask:
        self._specialist(specialist)
        task = WorkforceTask(str(uuid.uuid4()), specialist, str(instruction), float(time.time() if due_at is None else due_at), bool(mutation), bool(production))
        self.data["tasks"].append(asdict(task)); self._save(); return task

    def _specialist(self, key: str) -> Specialist:
        row = next((x for x in self.SPECIALISTS if x.key == key), None)
        if not row: raise KeyError(f"Unknown specialist: {key}")
        return row

    def due(self, now: float | None = None) -> list[WorkforceTask]:
        tick = time.time() if now is None else float(now); rows = []
        for row in self.data["tasks"]:
            if row.get("state") != "queued" or float(row.get("due_at", 0)) > tick: continue
            row["state"] = "awaiting_approval" if row.get("mutation") and row.get("production") else "ready"
            rows.append(WorkforceTask(**row))
        if rows: self._save()
        return rows

    def context(self, instruction: str) -> str:
        specialist = self.select(instruction)
        learned = self.learned(specialist.key)
        hint = f" Active role: {specialist.title}. Policy: {specialist.policy}"
        if learned: hint += " Proven local routines: " + "; ".join(x["instruction"] for x in learned[:3])
        return hint

    def record_outcome(self, specialist: str, instruction: str, evidence: str, verified: bool) -> None:
        if not verified: return
        key = f"{specialist}:{' '.join(str(instruction).lower().split())[:160]}"
        row = self.data["learning"].setdefault(key, {"specialist": specialist, "instruction": str(instruction)[:300], "evidence": [], "successes": 0})
        row["successes"] += 1; row["evidence"].append(str(evidence)[:800]); row["evidence"] = row["evidence"][-5:]
        self._save()

    def learned(self, specialist: str) -> list[dict]:
        rows = []
        for row in self.data["learning"].values():
            if row.get("specialist") == specialist:
                rows.append({**row, "confidence": "proven" if int(row.get("successes", 0)) >= 2 else "observed"})
        return sorted(rows, key=lambda x: int(x.get("successes", 0)), reverse=True)

    def summary(self) -> dict:
        due = self.due(); return {"roles": len(self.SPECIALISTS), "ready": len([x for x in due if x.state == "ready"]), "awaiting_approval": len([x for x in due if x.state == "awaiting_approval"]), "learned": len(self.data.get("learning", {}))}
