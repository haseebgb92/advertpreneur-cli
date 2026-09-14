"""Self-healing test and build failure diagnosis and bounded recovery engine."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class FailureTrace:
    command: str
    error_type: str
    failed_target: str
    traceback: str
    summary: str
    timestamp: str = field(default_factory=_now)


class SelfHealingEngine:
    """Diagnoses test/build errors and builds structured minimal-fix proposals."""

    def __init__(self, project: Path, max_attempts: int = 3) -> None:
        self.project = Path(project).resolve()
        self.max_attempts = max(1, int(max_attempts))
        self._repair_history: List[Dict[str, Any]] = []

    def diagnose(self, command: str, output: str) -> Optional[FailureTrace]:
        """Parse raw test or compiler outputs to isolate exact failing files, assertions, or syntax errors."""
        text = str(output or "")
        if not text:
            return None

        # Pytest assertion / failure detection
        pytest_failed = re.findall(r"(?:FAILED\s+([^\s:]+)::([^\s]+)|([^\s:]+)::([^\s]+)\s+FAILED)", text)
        if pytest_failed:
            match = pytest_failed[0]
            target_file = match[0] or match[2]
            test_fn = match[1] or match[3]
            tb_match = re.search(r"_{4,}\s+.*?\s+_{4,}(.*?)(?:={4,}|$)", text, re.S)
            trace = tb_match.group(1).strip() if tb_match else text[-600:]
            return FailureTrace(
                command=command,
                error_type="TestAssertionError",
                failed_target=f"{target_file}::{test_fn}",
                traceback=trace[:1500],
                summary=f"Pytest failure in {target_file}::{test_fn}",
            )

        # Python syntax / import error
        py_err = re.search(r"(?:SyntaxError|ImportError|ModuleNotFoundError|NameError):\s*(.+)", text)
        if py_err:
            return FailureTrace(
                command=command,
                error_type="PythonRuntimeError",
                failed_target="",
                traceback=text[-1200:],
                summary=py_err.group(0).strip(),
            )

        # Node / JS / NPM / Jest test error
        if "FAIL " in text or "npm ERR!" in text or "ReferenceError:" in text:
            return FailureTrace(
                command=command,
                error_type="NodeExecutionError",
                failed_target="",
                traceback=text[-1200:],
                summary="Node / Jest test or execution failure",
            )

        return FailureTrace(
            command=command,
            error_type="CommandFailure",
            failed_target="",
            traceback=text[-1000:],
            summary=f"Command '{command}' exited with an error.",
        )

    def repair_prompt(self, trace: FailureTrace, attempt: int = 1) -> str:
        """Format an isolated self-healing repair instruction for the agent."""
        return (
            f"Self-Healing Repair Attempt {attempt}/{self.max_attempts}:\n"
            f"Command `{trace.command}` failed with {trace.error_type}.\n"
            f"Failure summary: {trace.summary}\n\n"
            f"Traceback:\n```\n{trace.traceback}\n```\n\n"
            f"Please apply the minimal scoped code change to fix this failure without breaking other functionality. "
            f"Inspect the failing file and assertions carefully before modifying."
        )

    def record_attempt(self, trace: FailureTrace, attempt: int, success: bool, patch: str = "") -> None:
        self._repair_history.append({
            "trace": trace.summary,
            "attempt": attempt,
            "success": success,
            "patch_summary": patch[:200],
            "at": _now(),
        })
