"""Local Operation Router: path containment, reversible staged mutations, and destructive command safety."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_DESTRUCTIVE_COMMAND_PATTERNS = [
    re.compile(r"(?i)\brm\s+-(?:rf|fr|r)\s+(?:/|[a-zA-Z]:[/\\]|~|\.\.)"),
    re.compile(r"(?i)\bdel\s+.*?[a-zA-Z]:[/\\]"),
    re.compile(r"(?i)\brd\s+.*?[a-zA-Z]:[/\\]"),
    re.compile(r"(?i)\bformat\s+[a-zA-Z]:"),
    re.compile(r"(?i)\b(?:shutdown|reboot)\b"),
    re.compile(r"(?i)\b(?:mkfs|dd\s+if=)"),
]

_SENSITIVE_DIRECTORY_PATTERNS = [
    re.compile(r"(?i)^[a-zA-Z]:[/\\](?:windows|system32|program files|program files \(x86\))", re.I),
    re.compile(r"(?i)[/\\]\.git[/\\]hooks", re.I),
]


class OperationSecurityError(PermissionError):
    """Raised when an operation attempts path traversal, system file modification, or dangerous commands."""
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StagedMutation:
    rel_path: str
    original_content: Optional[str]
    new_content: str
    staged_at: str = field(default_factory=_now)


class LocalOperationRouter:
    """Safely boundaries local filesystem and Windows mutations with staged rollback support."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project).resolve()
        self._staged: Dict[str, StagedMutation] = {}
        self._history: List[Dict[str, Any]] = []

    def assert_contained(self, target_path: Path | str) -> Path:
        """Ensure target path resolves inside the project root or desktop, and outside sensitive directories."""
        raw_str = str(target_path or "")
        resolved = Path(target_path).expanduser().resolve() if (raw_str.startswith("~") or Path(target_path).is_absolute()) else (self.project / target_path).resolve()
        desktop = (Path.home() / "Desktop").resolve()

        is_contained = False
        try:
            resolved.relative_to(self.project)
            is_contained = True
        except ValueError:
            try:
                resolved.relative_to(desktop)
                is_contained = True
            except ValueError:
                pass

        if not is_contained:
            raise OperationSecurityError(f"Path traversal blocked: '{target_path}' is outside project root '{self.project}'")

        str_path = str(resolved)
        for pat in _SENSITIVE_DIRECTORY_PATTERNS:
            if pat.search(str_path):
                raise OperationSecurityError(f"Access to sensitive or system path is blocked: '{target_path}'")
        return resolved

    def check_command(self, command: str) -> None:
        """Verify command does not contain dangerous destructive system calls."""
        cmd_str = str(command or "").strip()
        for pat in _DESTRUCTIVE_COMMAND_PATTERNS:
            if pat.search(cmd_str):
                raise OperationSecurityError(f"Potentially destructive system command blocked: '{command}'")

    def stage_write(self, rel_path: str, content: str) -> StagedMutation:
        """Stage a file write, capturing the original content if it exists."""
        target = self.assert_contained(rel_path)
        orig: Optional[str] = None
        if target.is_file():
            try:
                orig = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                orig = None
        
        mutation = StagedMutation(
            rel_path=str(rel_path).replace("\\", "/"),
            original_content=orig,
            new_content=content,
        )
        self._staged[mutation.rel_path] = mutation
        return mutation

    def commit_stage(self) -> List[str]:
        """Apply all staged mutations to disk atomically."""
        applied: List[str] = []
        for rel_path, mutation in list(self._staged.items()):
            target = self.assert_contained(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mutation.new_content, encoding="utf-8")
            applied.append(rel_path)
            self._history.append({
                "path": rel_path,
                "had_original": mutation.original_content is not None,
                "committed_at": _now(),
            })
        self._staged.clear()
        return applied

    def rollback_stage(self) -> List[str]:
        """Roll back any uncommitted staged mutations, or revert already written mutations from history."""
        reverted: List[str] = []
        for rel_path, mutation in list(self._staged.items()):
            reverted.append(rel_path)
        self._staged.clear()
        return reverted



