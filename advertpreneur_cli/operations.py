from __future__ import annotations

import json
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProjectIdentity:
    url: str
    environment: str
    adapter: str = ""

    @classmethod
    def from_url(cls, url: str, adapter: str = "") -> "ProjectIdentity":
        host = urlparse(url).hostname or ""
        low = host.lower()
        environment = "local" if low in {"localhost", "127.0.0.1", "::1"} else "staging" if any(x in low for x in ("staging", "stage", "test", "dev.")) else "production"
        return cls(url=str(url), environment=environment, adapter=adapter)


@dataclass
class OperationPlan:
    id: str
    token: str
    action: str
    identity: ProjectIdentity
    targets: list[str]
    requires_approval: bool
    approved: bool = False
    stage: str = "planned"


class OperationLedger:
    def __init__(self, project: Path) -> None:
        self.path = Path(project) / ".advertpreneur" / "operations.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception: return {"plans": [], "evidence": [], "identity": {}}

    def _save(self) -> None:
        self.data["plans"] = self.data["plans"][-100:]; self.data["evidence"] = self.data["evidence"][-500:]
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def identity(self, value: ProjectIdentity) -> None:
        self.data["identity"] = asdict(value); self._save()

    def plan(self, action: str, identity: ProjectIdentity, targets: list[str]) -> OperationPlan:
        mutation = action in {"upload_plugin", "upload_theme", "delete", "activate", "deactivate", "publish", "settings", "deploy"}
        plan = OperationPlan(str(uuid.uuid4()), secrets.token_urlsafe(12), action, identity, list(targets), mutation and identity.environment == "production")
        self.data["plans"].append({**asdict(plan), "identity": asdict(identity), "at": time.time()}); self._save(); return plan

    def _get(self, plan_id: str) -> dict:
        row = next((x for x in reversed(self.data["plans"]) if x["id"] == plan_id), None)
        if not row: raise KeyError("operation plan not found")
        return row

    def approve(self, plan_id: str, token: str) -> bool:
        row = self._get(plan_id); row["approved"] = bool(token and secrets.compare_digest(token, row["token"])); self._save(); return bool(row["approved"])

    def evidence(self, plan_id: str, kind: str, detail: str, url: str = "") -> None:
        self.data["evidence"].append({"plan_id": plan_id, "kind": kind, "detail": detail[:1000], "url": url[:2000], "at": time.time()}); self._save()

    def checkpoint(self, plan_id: str, stage: str) -> None:
        row = self._get(plan_id); row["stage"] = stage[:80]; self._save()

    def resume(self) -> OperationPlan | None:
        if not self.data["plans"]: return None
        row = self.data["plans"][-1]; identity = ProjectIdentity(**row["identity"])
        return OperationPlan(row["id"], row["token"], row["action"], identity, list(row["targets"]), bool(row["requires_approval"]), bool(row.get("approved")), str(row.get("stage") or "planned"))
