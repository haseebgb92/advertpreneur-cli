from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .project_contract import ProjectContract


@dataclass
class CheckResult:
    label: str
    command: List[str]
    ok: bool
    output: str


class ProportionalVerifier:
    """Local, bounded verification based on changed file types and the project contract."""

    def __init__(self, root: Path, contract: ProjectContract) -> None:
        self.root = root.resolve(); self.contract = contract

    def safe_checks(self, changed: List[str]) -> List[tuple[str, List[str]]]:
        out: List[tuple[str, List[str]]] = []
        for rel in changed[:80]:
            p = self.root / rel
            ext = p.suffix.lower()
            if not p.exists() or not p.is_file(): continue
            if ext == ".php" and shutil.which("php"):
                out.append((f"PHP syntax · {rel}", [shutil.which("php") or "php", "-l", str(p)]))
            elif ext == ".py":
                code = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); compile(p.read_text(encoding='utf-8'), str(p), 'exec')"
                out.append((f"Python syntax · {rel}", [sys.executable, "-c", code, str(p)]))
            elif ext in {".js", ".mjs", ".cjs"} and shutil.which("node"):
                out.append((f"JavaScript syntax · {rel}", [shutil.which("node") or "node", "--check", str(p)]))
        # De-duplicate commands while preserving order.
        seen = set(); uniq = []
        for label, cmd in out:
            key = tuple(cmd)
            if key not in seen:
                seen.add(key); uniq.append((label, cmd))
        return uniq[:40]

    def contract_checks(self) -> List[tuple[str, List[str]]]:
        out = []
        for text in self.contract.data.validation_commands[:8]:
            if "{changed_" in text: continue
            argv = self._shell_argv(text)
            if argv: out.append(("Project validation", argv))
        return out

    def build_checks(self) -> List[tuple[str, List[str]]]:
        out = []
        for text in self.contract.data.build_commands[:4]:
            argv = self._shell_argv(text)
            if argv: out.append(("Project build", argv))
        return out

    def _shell_argv(self, command: str) -> List[str]:
        # Contract commands are locally inferred; run through the platform shell only when explicitly /verify full or /package.
        if not command.strip(): return []
        if sys.platform == "win32": return ["cmd.exe", "/d", "/s", "/c", command]
        return ["sh", "-lc", command]

    def run(self, changed: List[str], full: bool = False, include_build: bool = False, timeout: int = 180) -> List[CheckResult]:
        checks = self.safe_checks(changed)
        if full: checks += self.contract_checks()
        if include_build: checks += self.build_checks()
        results: List[CheckResult] = []
        for label, cmd in checks:
            try:
                p = subprocess.run(cmd, cwd=str(self.root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
                text = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
                results.append(CheckResult(label, cmd, p.returncode == 0, text[-2000:]))
            except Exception as exc:
                results.append(CheckResult(label, cmd, False, str(exc)))
        return results
