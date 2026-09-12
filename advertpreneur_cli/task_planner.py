from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .project_contract import ProjectContract
from .project_index import ProjectIndex


@dataclass
class TaskPlan:
    task_class: str
    complexity: str
    likely_files: List[str]
    needs_browser: bool
    needs_web: bool
    needs_mcp: bool
    needs_plugins: bool
    wants_package: bool
    wants_full_validation: bool
    framework_chars: int
    evidence_chars: int
    handbook_chars: int

    def context(self, contract: ProjectContract, max_chars: int = 1500) -> str:
        rows = [f"Local task plan: {self.task_class} · complexity {self.complexity}."]
        if self.likely_files:
            rows.append("Start with: " + ", ".join(self.likely_files[:8]) + ". Inspect these before broad scanning.")
        capabilities = []
        if self.needs_browser: capabilities.append("browser")
        if self.needs_web: capabilities.append("web")
        if self.needs_mcp: capabilities.append("MCP")
        if self.needs_plugins: capabilities.append("skills/plugins")
        rows.append("Extra capabilities: " + (", ".join(capabilities) if capabilities else "none; keep tools/context lean"))
        if contract.data.validation_commands:
            checks = contract.data.validation_commands[:4 if self.wants_full_validation else 2]
            rows.append("Verification: " + " ; ".join(checks) + ("." if checks else ""))
        if self.wants_package:
            rows.append(f"Packaging requested: produce/verify {contract.data.package_name} using the project contract; do not include secrets/dev/runtime state.")
        return "\n".join(rows)[:max_chars]


class LocalTaskPlanner:
    """Zero-model task classifier used to decide what context/capabilities deserve tokens."""

    def __init__(self, contract: ProjectContract, project_index: ProjectIndex) -> None:
        self.contract = contract
        self.project_index = project_index

    def plan(self, task: str) -> TaskPlan:
        raw = task or ""
        low = raw.lower()
        package = any(x in low for x in ("package", "zip", "release", "ship", "distribution", "publish build"))
        full_validation = package or any(x in low for x in ("test", "verify", "validate", "build", "lint", "compile"))
        browser = any(x in low for x in ("browser", "screenshot", "visual", "reference site", "reverse engineer", "responsive", "rendered"))
        web = any(x in low for x in ("latest", "documentation", "docs", "research", "current version", "look up", "web search"))
        mcp = "mcp" in low
        plugins = any(x in low for x in ("skill", "codex plugin", "provider plugin", "design system", "figma", "impeccable"))
        # A project can itself be a WordPress/plugin codebase; that must not load provider plugins.
        if "wordpress plugin" in low or "wp plugin" in low:
            plugins = any(x in low for x in ("skill", "codex plugin", "provider plugin", "figma", "impeccable"))
        debug = any(x in low for x in ("bug", "error", "broken", "debug", "stack trace", "regression", "not working", "fix"))
        inspect = any(x in low for x in ("review", "inspect", "audit", "explain", "find", "locate", "trace"))
        edit = any(x in low for x in ("create", "add", "change", "edit", "update", "implement", "make", "fix", "remove", "refactor"))
        if package:
            task_class = "release/package"
        elif debug:
            task_class = "debug/fix"
        elif edit:
            task_class = "code change"
        elif inspect:
            task_class = "inspection"
        else:
            task_class = "general"

        ranked = self.project_index.search(raw, limit=8) if self.project_index.ready else []
        likely: List[str] = []
        for score, entry in ranked:
            if score >= 8 and entry.path not in likely:
                likely.append(entry.path)
        # Literal references and high-value contract paths are useful even before the map is rich.
        for path in self.contract.data.important_files:
            token = path.rstrip("/").split("/")[-1].lower()
            if token and token in low and path not in likely:
                likely.append(path)
        explicit = re.findall(r"(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+|\b[A-Za-z0-9_.-]+\.(?:php|py|js|ts|tsx|jsx|html|css|kt|kts|json|liquid)\b", raw)
        for p in explicit:
            p = p.replace("\\", "/")
            if p not in likely:
                likely.insert(0, p)

        signals = sum((package, full_validation, browser, web, mcp, plugins, debug)) + min(len(likely), 4)
        if len(raw) > 900 or signals >= 6:
            complexity = "high"
        elif len(raw) > 260 or signals >= 3:
            complexity = "medium"
        else:
            complexity = "low"
        budgets = {
            "low": (700, 650, 550),
            "medium": (1300, 1100, 900),
            "high": (1900, 1600, 1200),
        }
        framework_chars, evidence_chars, handbook_chars = budgets[complexity]
        return TaskPlan(task_class, complexity, likely[:8], browser, web, mcp, plugins, package, full_validation,
                        framework_chars, evidence_chars, handbook_chars)
