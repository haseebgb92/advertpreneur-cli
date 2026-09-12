from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class FileRisk:
    path: str
    added: int
    removed: int
    risk: str
    reason: str


class DiffIntelligence:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def analyze(self, changed: List[str], baseline_ref: str = "") -> List[FileRisk]:
        stats: dict[str, tuple[int, int]] = {}
        if baseline_ref and (self.root / ".git").exists():
            try:
                p = subprocess.run(["git", "-C", str(self.root), "diff", "--numstat", baseline_ref, "--"], capture_output=True, text=True, timeout=8)
                if p.returncode == 0:
                    for line in p.stdout.splitlines():
                        parts = line.split("\t", 2)
                        if len(parts) == 3:
                            try: a = int(parts[0]) if parts[0] != "-" else 0; r = int(parts[1]) if parts[1] != "-" else 0
                            except ValueError: a = r = 0
                            stats[parts[2].replace("\\", "/")] = (a, r)
            except Exception:
                pass
        out: List[FileRisk] = []
        for rel in changed[:200]:
            norm = rel.replace("\\", "/")
            a, r = stats.get(norm, (0, 0))
            low = norm.lower(); total = a + r
            risk, reason = "low", "localized source/asset change"
            if any(x in low for x in ("auth", "security", "payment", "checkout", "database", "migration", "schema", "permission", "manifest", "gradle", "functions.php")):
                risk, reason = "high", "sensitive runtime/configuration path"
            elif any(low.endswith(x) for x in ("package.json", "composer.json", "pyproject.toml", "theme.json", "wp-config.php")) or total >= 200:
                risk, reason = "medium", "configuration or broad change"
            elif total >= 80:
                risk, reason = "medium", "larger edit surface"
            out.append(FileRisk(norm, a, r, risk, reason))
        return out
