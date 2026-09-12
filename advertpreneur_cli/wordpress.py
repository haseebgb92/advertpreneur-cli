from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Mapping


def wordpress_login_state(url: str, signals: Mapping[str, object] | None = None) -> str:
    """Classify an observed WordPress page without reading credentials."""
    lower_url = str(url or "").lower()
    observed = signals or {}
    if "wp-login.php" in lower_url or bool(observed.get("password")):
        return "login_needed"
    if bool(observed.get("dashboard")) or "wp-admin/" in lower_url and bool(observed.get("admin_bar")):
        return "authenticated"
    return "unknown"


@dataclass(frozen=True)
class DeleteTarget:
    name: str
    kind: str
    warning: str = ""
    path: str = ""


@dataclass
class DeletionProposal:
    token: str
    targets: list[DeleteTarget] = field(default_factory=list)
    approved: bool = False

    @classmethod
    def create(cls, targets: list[DeleteTarget]) -> "DeletionProposal":
        if not targets:
            raise ValueError("At least one exact deletion target is required")
        return cls(token=secrets.token_urlsafe(12), targets=list(targets))

    def approve(self, token: str) -> bool:
        self.approved = bool(token and secrets.compare_digest(str(token), self.token))
        return self.approved

    def describe(self) -> str:
        rows = ["Deletion proposal (no action has run):"]
        for index, target in enumerate(self.targets, start=1):
            location = f" · {target.path}" if target.path else ""
            warning = f" · WARNING: {target.warning}" if target.warning else ""
            rows.append(f"  {index}. {target.kind}: {target.name}{location}{warning}")
        rows.append(f"Approve with token: {self.token}")
        return "\n".join(rows)
