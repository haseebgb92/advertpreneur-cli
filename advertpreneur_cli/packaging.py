from __future__ import annotations

import fnmatch
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .project_contract import ProjectContract


@dataclass
class PackageResult:
    path: Path
    files: int
    size_bytes: int
    warnings: List[str]


class ProjectPackager:
    """Deterministic clean ZIP packaging driven by the project contract."""

    def __init__(self, root: Path, contract: ProjectContract) -> None:
        self.root = root.resolve(); self.contract = contract

    def _excluded(self, rel: str) -> bool:
        rel = rel.replace("\\", "/"); parts = rel.split("/")
        patterns = self.contract.data.package_excludes
        for pat in patterns:
            p = str(pat).replace("\\", "/")
            while p.startswith("./"):
                p = p[2:]
            if p.startswith("/"):
                p = p[1:]
            if not p: continue
            if p.endswith("/"):
                stem = p.rstrip("/")
                if rel == stem or rel.startswith(stem + "/") or stem in parts:
                    return True
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(Path(rel).name, p):
                return True
            if p.startswith("**/") and fnmatch.fnmatch(rel, p[3:]):
                return True
        return False

    def files(self) -> List[Path]:
        out = []
        for base, dirs, names in os.walk(self.root):
            basep = Path(base)
            kept = []
            for d in dirs:
                rel = (basep / d).relative_to(self.root).as_posix() + "/"
                if not self._excluded(rel): kept.append(d)
            dirs[:] = kept
            for name in names:
                p = basep / name
                rel = p.relative_to(self.root).as_posix()
                if not self._excluded(rel): out.append(p)
        return sorted(out)

    def create(self, destination: Path | None = None) -> PackageResult:
        name = self.contract.data.package_name or (self.root.name + ".zip")
        out_dir = self.root.parent / "packages"
        out_dir.mkdir(parents=True, exist_ok=True)
        destination = (destination or (out_dir / name)).resolve()
        source_files = [p for p in self.files() if p.resolve() != destination]
        warnings: List[str] = []; written = 0
        prefix = Path(name).stem if self.contract.data.package_kind in {"wordpress-theme", "wordpress-plugin"} else ""
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in source_files:
                rel = p.relative_to(self.root).as_posix()
                # Never package obvious secrets even if an override accidentally removes the default patterns.
                lname = p.name.lower()
                secret_like = (
                    lname == ".env" or lname.startswith(".env.") or lname in {".npmrc", ".pypirc", ".netrc", ".htpasswd"}
                    or "credentials" in lname or "secret" in lname or "service-account" in lname
                    or lname.endswith((".pem", ".key", ".p12", ".pfx"))
                )
                if secret_like:
                    warnings.append(f"secret-like file excluded: {rel}")
                    continue
                arc = f"{prefix}/{rel}" if prefix else rel
                zf.write(p, arc); written += 1
        with zipfile.ZipFile(destination, "r") as zf:
            bad = zf.testzip()
            if bad: warnings.append(f"ZIP CRC verification failed at {bad}")
            names = zf.namelist()
            stripped = [n[len(prefix)+1:] if prefix and n.startswith(prefix + "/") else n for n in names]
            if any(n.startswith((".git/", ".advertpreneur/", "node_modules/")) for n in stripped):
                warnings.append("development/runtime directory leaked into package")
            if self.contract.data.package_kind == "wordpress-theme" and not any(n.endswith("/style.css") or n == "style.css" for n in names):
                warnings.append("WordPress theme package is missing style.css")
            if self.contract.data.package_kind == "wordpress-plugin" and not any(n.lower().endswith(".php") for n in names):
                warnings.append("WordPress plugin package contains no PHP entry file")
        return PackageResult(destination, written, destination.stat().st_size, warnings)
