from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


EXCLUDED = {".git", ".advertpreneur", "node_modules", ".venv", "venv", "dist", "build", ".next", "__pycache__", "target", "vendor"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Checkpoint:
    id: str
    created_at: str
    label: str
    mode: str
    ref: str
    changed_files: List[str]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Checkpoint":
        return cls(str(d.get("id") or ""), str(d.get("created_at") or ""), str(d.get("label") or ""), str(d.get("mode") or ""), str(d.get("ref") or ""), [str(x) for x in (d.get("changed_files") or [])])


class CheckpointManager:
    """Local task checkpoints that never create visible Git commits."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.store = self.root / ".advertpreneur" / "checkpoints"
        self.meta_path = self.store / "index.json"
        self.store.mkdir(parents=True, exist_ok=True)
        self._ensure_git_exclude()
        self._active: Dict[str, Any] | None = None

    def _ensure_git_exclude(self) -> None:
        git = self.root / ".git"
        if not git.is_dir():
            return
        target = git / "info" / "exclude"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            if ".advertpreneur/" not in {x.strip() for x in existing.splitlines()}:
                with target.open("a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(".advertpreneur/\n")
        except OSError:
            pass

    def _run_git(self, args: List[str], env: Dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        e = os.environ.copy()
        if env:
            e.update(env)
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=e)

    def _is_git(self) -> bool:
        try:
            return self._run_git(["rev-parse", "--is-inside-work-tree"], timeout=4).stdout.strip() == "true"
        except Exception:
            return False

    def _load(self) -> List[Checkpoint]:
        if not self.meta_path.exists():
            return []
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return [Checkpoint.from_dict(x) for x in raw if isinstance(x, dict)]
        except Exception:
            return []

    def _save(self, rows: List[Checkpoint]) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        dropped = rows[:-50] if len(rows) > 50 else []
        kept = rows[-50:]
        for cp in dropped:
            if cp.mode == "git-tree":
                try:
                    self._run_git(["update-ref", "-d", f"refs/advertpreneur/checkpoints/{cp.id}"])
                except Exception:
                    pass
            elif cp.ref:
                try:
                    Path(cp.ref).unlink()
                except OSError:
                    pass
        self.meta_path.write_text(json.dumps([c.__dict__ for c in kept], indent=2), encoding="utf-8")

    def _git_tree(self, checkpoint_id: str | None = None) -> str:
        git_dir = self._run_git(["rev-parse", "--git-dir"]).stdout.strip()
        gd = (self.root / git_dir).resolve() if not Path(git_dir).is_absolute() else Path(git_dir)
        tmp_index = gd / f"advertpreneur-index-{uuid.uuid4().hex}"
        env = {"GIT_INDEX_FILE": str(tmp_index)}
        try:
            head = self._run_git(["rev-parse", "--verify", "HEAD"], timeout=4)
            if head.returncode == 0:
                p = self._run_git(["read-tree", "HEAD"], env=env)
            else:
                p = self._run_git(["read-tree", "--empty"], env=env)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or "git read-tree failed")
            p = self._run_git(["add", "-A", "--", "."], env=env, timeout=60)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or "git add snapshot failed")
            tree = self._run_git(["write-tree"], env=env).stdout.strip()
            if not tree:
                raise RuntimeError("git write-tree returned no object")
            if checkpoint_id:
                self._run_git(["update-ref", f"refs/advertpreneur/checkpoints/{checkpoint_id}", tree])
            return tree
        finally:
            try:
                tmp_index.unlink()
            except OSError:
                pass

    def _manifest(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for base, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED]
            for name in names:
                p = Path(base) / name
                try:
                    if p.stat().st_size > 8_000_000:
                        continue
                    rel = p.relative_to(self.root).as_posix()
                    h = hashlib.sha1(p.read_bytes()).hexdigest()
                    out[rel] = h
                except OSError:
                    continue
        return out

    def begin(self, label: str) -> str:
        cid = uuid.uuid4().hex[:12]
        if self._is_git():
            tree = self._git_tree(cid)
            self._active = {"id": cid, "mode": "git-tree", "ref": tree, "label": label, "before": tree}
        else:
            archive = self.store / f"{cid}.zip"
            manifest = self._manifest()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
                for rel in manifest:
                    p = self.root / rel
                    try:
                        zf.write(p, rel)
                    except OSError:
                        pass
                zf.writestr(".advertpreneur-checkpoint-manifest.json", json.dumps(manifest))
            self._active = {"id": cid, "mode": "zip", "ref": str(archive), "label": label, "before_manifest": manifest}
        return cid

    def live_changes(self) -> List[tuple[str, int, int]]:
        """Return active Git checkpoint changes without creating a new snapshot."""
        active = self._active
        if not active or active.get("mode") != "git-tree":
            return []
        rows: dict[str, tuple[int, int]] = {}
        try:
            proc = self._run_git(["diff", "--numstat", str(active["before"]), "--"])
            if proc.returncode != 0:
                return []
            for line in proc.stdout.splitlines():
                added, removed, path = line.split("\t", 2)
                rows[path.replace("\\", "/")] = (
                    int(added) if added.isdigit() else 0,
                    int(removed) if removed.isdigit() else 0,
                )
            untracked = self._run_git(["ls-files", "--others", "--exclude-standard"])
            if untracked.returncode == 0:
                for path in untracked.stdout.splitlines():
                    path = path.strip().replace("\\", "/")
                    if path:
                        rows.setdefault(path, (0, 0))
        except Exception:
            return []
        return [(path, added, removed) for path, (added, removed) in sorted(rows.items())][:200]

    def finalize(self) -> Checkpoint | None:
        active = self._active
        self._active = None
        if not active:
            return None
        changed: List[str] = []
        if active["mode"] == "git-tree":
            after = self._git_tree(None)
            if after == active["before"]:
                self._run_git(["update-ref", "-d", f"refs/advertpreneur/checkpoints/{active['id']}"])
                return None
            proc = self._run_git(["diff", "--name-only", active["before"], after, "--"])
            changed = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
        else:
            before = active.get("before_manifest") or {}
            after = self._manifest()
            changed = sorted({*before.keys(), *after.keys()} - {k for k in before.keys() & after.keys() if before[k] == after[k]})
            if not changed:
                try:
                    Path(active["ref"]).unlink()
                except OSError:
                    pass
                return None
        cp = Checkpoint(active["id"], _now(), active["label"][:180], active["mode"], active["ref"], changed[:500])
        rows = self._load()
        rows.append(cp)
        self._save(rows)
        return cp

    def cancel(self) -> None:
        active = self._active
        self._active = None
        if not active:
            return
        if active.get("mode") == "git-tree":
            self._run_git(["update-ref", "-d", f"refs/advertpreneur/checkpoints/{active['id']}"])
        elif active.get("ref"):
            try:
                Path(active["ref"]).unlink()
            except OSError:
                pass

    def list(self, limit: int = 20) -> List[Checkpoint]:
        return list(reversed(self._load()))[:limit]

    def undo_latest(self) -> Checkpoint | None:
        rows = self._load()
        if not rows:
            return None
        cp = rows[-1]
        self.restore(cp)
        rows.pop()
        self._save(rows)
        return cp

    def restore(self, cp: Checkpoint) -> None:
        if cp.mode == "git-tree":
            git_dir = self._run_git(["rev-parse", "--git-dir"]).stdout.strip()
            gd = (self.root / git_dir).resolve() if not Path(git_dir).is_absolute() else Path(git_dir)
            tmp_index = gd / f"advertpreneur-restore-{uuid.uuid4().hex}"
            env = {"GIT_INDEX_FILE": str(tmp_index)}
            try:
                # Populate temp index with current working state so files created after the
                # checkpoint are known and can be removed by reset.
                head = self._run_git(["rev-parse", "--verify", "HEAD"], timeout=4)
                self._run_git(["read-tree", "HEAD"] if head.returncode == 0 else ["read-tree", "--empty"], env=env)
                self._run_git(["add", "-A", "--", "."], env=env, timeout=60)
                p = self._run_git(["read-tree", "--reset", "-u", cp.ref], env=env, timeout=60)
                if p.returncode != 0:
                    raise RuntimeError(p.stderr.strip() or "checkpoint restore failed")
            finally:
                try:
                    tmp_index.unlink()
                except OSError:
                    pass
            self._run_git(["update-ref", "-d", f"refs/advertpreneur/checkpoints/{cp.id}"])
            return

        archive = Path(cp.ref)
        if not archive.exists():
            raise RuntimeError("Checkpoint archive is missing")
        with zipfile.ZipFile(archive, "r") as zf:
            raw = json.loads(zf.read(".advertpreneur-checkpoint-manifest.json").decode("utf-8"))
            before = set(raw.keys())
            now = set(self._manifest().keys())
            for rel in now - before:
                try:
                    (self.root / rel).unlink()
                except OSError:
                    pass
            for rel in before:
                try:
                    data = zf.read(rel)
                except KeyError:
                    continue
                target = (self.root / rel).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        try:
            archive.unlink()
        except OSError:
            pass

    def diff(self, cp: Checkpoint) -> str:
        """Return the unified diff between the checkpoint state and current workspace."""
        if cp.mode == "git-tree":
            p = self._run_git(["diff", cp.ref, "--", "."], timeout=15)
            return p.stdout.strip()
        return f"Checkpoint {cp.id[:8]} ({cp.label}) · {len(cp.changed_files)} changed file(s)"
