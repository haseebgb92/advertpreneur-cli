from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


INDEX_VERSION = 1
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts",
    ".go", ".rs", ".php", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".swift",
    ".rb", ".vue", ".svelte", ".dart", ".sql", ".html", ".css", ".scss", ".md",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".gradle",
}
IMPORTANT_BASENAMES = {
    "package.json", "pyproject.toml", "requirements.txt", "composer.json", "cargo.toml", "go.mod",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml", ".env.example", "schema.prisma", "androidmanifest.xml",
}
EXCLUDED_DIRS = {
    ".git", ".advertpreneur", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist",
    "build", ".next", ".idea", ".gradle", "target", "vendor", "coverage", ".cache", ".turbo",
}
STOP_WORDS = {
    "a", "an", "the", "this", "that", "it", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "for", "with", "and", "or", "but", "if", "then", "when", "where", "how", "why",
    "can", "could", "would", "should", "please", "make", "fix", "issue", "problem", "code", "file",
    "work", "working", "something", "my", "our", "we", "i", "do", "does", "not", "now", "new",
}


def _now() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _words(text: str) -> List[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[A-Za-z0-9_.$-]+", text.lower())
    out: List[str] = []
    for token in words:
        for part in re.split(r"[_.$/-]+", token):
            if len(part) >= 2 and part not in STOP_WORDS:
                out.append(part)
    return out


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 256), b""):
            h.update(block)
    return h.hexdigest()


def _entry_id(path: str, symbol: str, kind: str) -> str:
    raw = f"{path}|{symbol}|{kind}".encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:16]


@dataclass
class CodeEntry:
    id: str
    path: str
    symbol: str
    kind: str
    line_start: int
    line_end: int
    tags: List[str]
    summary: str = ""
    enriched_summary: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CodeEntry":
        return cls(
            id=str(d.get("id") or ""), path=str(d.get("path") or ""), symbol=str(d.get("symbol") or ""),
            kind=str(d.get("kind") or "symbol"), line_start=int(d.get("line_start") or 1),
            line_end=int(d.get("line_end") or d.get("line_start") or 1),
            tags=[str(x) for x in (d.get("tags") or [])], summary=str(d.get("summary") or ""),
            enriched_summary=str(d.get("enriched_summary") or ""),
        )


class ProjectIndex:
    """Local, deterministic code map. It never calls a cloud model.

    The index lives in <project>/.advertpreneur/ and is excluded from Git locally via
    .git/info/exclude where possible. The optional local-model enrichment layer is
    deliberately separate so ordinary indexing remains model-free and fast.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.store = self.root / ".advertpreneur"
        self.map_path = self.store / "project-map.json"
        self.memory_path = self.store / "project-memory.md"
        self.data: Dict[str, Any] = self._load()
        self._ensure_git_exclude()

    @property
    def ready(self) -> bool:
        return bool(self.data.get("files"))

    @property
    def entry_count(self) -> int:
        return len(self.data.get("entries") or [])

    @property
    def file_count(self) -> int:
        return len(self.data.get("files") or {})

    def _empty(self) -> Dict[str, Any]:
        return {"version": INDEX_VERSION, "project": str(self.root), "updated_at": "", "files": {}, "entries": []}

    def _load(self) -> Dict[str, Any]:
        if self.map_path.exists():
            try:
                raw = json.loads(self.map_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and int(raw.get("version") or 0) == INDEX_VERSION:
                    return raw
            except Exception:
                pass
        return self._empty()

    def _ensure_git_exclude(self) -> None:
        git = self.root / ".git"
        if not git.exists():
            return
        # Worktrees may store .git as a file; avoid guessing the real gitdir here.
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

    def _candidate_files(self) -> List[Path]:
        rels: List[str] = []
        if (self.root / ".git").exists():
            try:
                proc = subprocess.run(
                    ["git", "-C", str(self.root), "ls-files", "-co", "--exclude-standard"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                )
                if proc.returncode == 0:
                    rels = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
            except Exception:
                rels = []
        if rels:
            paths = [self.root / r for r in rels]
        else:
            paths = []
            for base, dirs, names in os.walk(self.root):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                for name in names:
                    paths.append(Path(base) / name)
        selected: List[Path] = []
        for p in paths:
            try:
                rel = p.relative_to(self.root)
            except ValueError:
                continue
            if any(part in EXCLUDED_DIRS for part in rel.parts):
                continue
            if not p.is_file():
                continue
            basename = p.name.lower()
            if p.suffix.lower() not in CODE_EXTENSIONS and basename not in IMPORTANT_BASENAMES:
                continue
            try:
                if p.stat().st_size > 1_500_000:
                    continue
            except OSError:
                continue
            selected.append(p)
        return selected

    def build(self, force: bool = False) -> Dict[str, Any]:
        started = time.monotonic()
        self.store.mkdir(parents=True, exist_ok=True)
        old_files: Dict[str, Any] = dict(self.data.get("files") or {})
        old_entries = [CodeEntry.from_dict(x) for x in (self.data.get("entries") or [])]
        entries_by_path: Dict[str, List[CodeEntry]] = {}
        for e in old_entries:
            entries_by_path.setdefault(e.path, []).append(e)

        files: Dict[str, Any] = {}
        entries: List[CodeEntry] = []
        changed = 0
        unchanged = 0
        failed = 0
        candidates = self._candidate_files()
        for path in candidates:
            rel = path.relative_to(self.root).as_posix()
            try:
                stat = path.stat()
                previous = old_files.get(rel) or {}
                quick_same = (
                    not force and previous and int(previous.get("size", -1)) == stat.st_size
                    and int(previous.get("mtime_ns", -1)) == stat.st_mtime_ns
                )
                if quick_same:
                    files[rel] = previous
                    entries.extend(entries_by_path.get(rel, []))
                    unchanged += 1
                    continue
                digest = _sha1(path)
                if not force and previous and previous.get("sha1") == digest:
                    files[rel] = {**previous, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha1": digest}
                    entries.extend(entries_by_path.get(rel, []))
                    unchanged += 1
                    continue
                parsed = self._parse_file(path, rel)
                files[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha1": digest}
                entries.extend(parsed)
                changed += 1
            except Exception:
                failed += 1

        removed = len(set(old_files) - set(files))
        if not force and changed == 0 and removed == 0 and old_files and len(files) == len(old_files):
            return {
                "files": len(files), "entries": len(old_entries), "changed": 0, "unchanged": unchanged,
                "removed": 0, "failed": failed, "seconds": time.monotonic() - started, "cloud_tokens": 0,
            }
        self.data = {
            "version": INDEX_VERSION,
            "project": str(self.root),
            "updated_at": _now(),
            "files": files,
            "entries": [asdict(e) for e in entries],
        }
        self.map_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_memory(entries)
        return {
            "files": len(files), "entries": len(entries), "changed": changed, "unchanged": unchanged,
            "removed": removed, "failed": failed, "seconds": time.monotonic() - started,
            "cloud_tokens": 0,
        }

    def _parse_file(self, path: Path, rel: str) -> List[CodeEntry]:
        text = path.read_text(encoding="utf-8", errors="replace")
        ext = path.suffix.lower()
        if ext == ".py":
            return self._parse_python(rel, text)
        return self._parse_regex(rel, text)

    def _base_tags(self, rel: str, symbol: str = "") -> List[str]:
        tags = _words(rel + " " + symbol)
        # Useful semantic aliases inferred deterministically from common path/name terms.
        aliases = {
            "auth": ["authentication", "login", "credential", "credentials", "session"],
            "login": ["auth", "authentication", "credential", "credentials"],
            "credential": ["credentials", "login", "auth"],
            "credentials": ["credential", "login", "auth"],
            "password": ["credentials", "login", "auth"],
            "token": ["auth", "session", "jwt"],
            "checkout": ["cart", "payment", "order"],
            "payment": ["checkout", "billing", "stripe"],
            "user": ["account", "profile"],
            "settings": ["config", "configuration"],
            "config": ["settings", "configuration"],
            "database": ["db", "storage", "repository"],
            "db": ["database", "storage"],
            "api": ["endpoint", "route", "controller"],
            "test": ["tests", "spec", "validation"],
        }
        expanded = list(tags)
        for t in tags:
            expanded.extend(aliases.get(t, []))
        return list(dict.fromkeys(expanded))[:28]

    def _entry(self, rel: str, symbol: str, kind: str, start: int, end: int, summary: str = "") -> CodeEntry:
        return CodeEntry(
            id=_entry_id(rel, symbol, kind), path=rel, symbol=symbol, kind=kind,
            line_start=max(1, start), line_end=max(start, end), tags=self._base_tags(rel, symbol), summary=summary,
        )

    def _parse_python(self, rel: str, text: str) -> List[CodeEntry]:
        out: List[CodeEntry] = []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return self._parse_regex(rel, text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                out.append(self._entry(rel, node.name, kind, node.lineno, getattr(node, "end_lineno", node.lineno + 8), f"Python {kind} {node.name}"))
            elif isinstance(node, ast.ClassDef):
                out.append(self._entry(rel, node.name, "class", node.lineno, getattr(node, "end_lineno", node.lineno + 12), f"Python class {node.name}"))
        # Route decorators deserve their own searchable entries.
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if re.search(r"@(app|router|bp|blueprint)\.(get|post|put|patch|delete|route)\s*\(", line, re.I):
                out.append(self._entry(rel, line.strip()[:120], "route", i, min(len(lines), i + 8), "HTTP route"))
        if not out and (Path(rel).name.lower() in IMPORTANT_BASENAMES or rel.lower().endswith(("settings.py", "config.py"))):
            out.append(self._entry(rel, Path(rel).name, "config", 1, min(80, len(lines)), "Project configuration"))
        return out

    def _parse_regex(self, rel: str, text: str) -> List[CodeEntry]:
        out: List[CodeEntry] = []
        lines = text.splitlines()
        patterns: Sequence[tuple[str, str]] = (
            (r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", "function"),
            (r"\b(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", "class"),
            (r"\b(?:interface|type)\s+([A-Za-z_$][\w$]*)", "type"),
            (r"\b(?:public|private|protected|internal|static|suspend|async|final|open|override|virtual|abstract|inline|fun|func|fn|def)\s+(?:[\w<>,?\[\].:]+\s+)?([A-Za-z_$][\w$]*)\s*\(", "function"),
            (r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", "function"),
            (r"\b(?:struct|enum|trait|record|data\s+class)\s+([A-Za-z_]\w*)", "type"),
            (r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", "function"),
            (r"\b(?:app|router)\.(?:get|post|put|patch|delete|use)\s*\(\s*['\"]([^'\"]+)", "route"),
            (r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(([^)]*)\)", "route"),
            (r"\bRoute::(?:get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)", "route"),
        )
        compiled = [(re.compile(p), kind) for p, kind in patterns]
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "#", "*")):
                continue
            for rx, kind in compiled:
                m = rx.search(line)
                if not m:
                    continue
                symbol = next((g for g in m.groups()[::-1] if g), stripped[:100])
                if kind == "route":
                    symbol = f"route {symbol}"
                out.append(self._entry(rel, str(symbol)[:140], kind, i, min(len(lines), i + 16), f"{kind.replace('_',' ')} in {Path(rel).name}"))
                break
        basename = Path(rel).name.lower()
        if basename in IMPORTANT_BASENAMES or basename.startswith(("config.", "settings.")):
            out.append(self._entry(rel, Path(rel).name, "config", 1, min(100, max(1, len(lines))), "Project configuration"))
        # HTML/CSS often have no symbols; index ids/classes/headings sparingly.
        if not out and Path(rel).suffix.lower() in {".html", ".vue", ".svelte"}:
            for i, line in enumerate(lines, 1):
                m = re.search(r"<(?:section|main|nav|form)[^>]+(?:id|class)=['\"]([^'\"]+)", line, re.I)
                if m:
                    out.append(self._entry(rel, m.group(1)[:100], "ui_section", i, min(len(lines), i + 30), "UI section"))
                    if len(out) >= 20:
                        break
        return out

    def _write_memory(self, entries: Sequence[CodeEntry]) -> None:
        grouped: Dict[str, List[CodeEntry]] = {}
        for e in entries:
            top = e.path.split("/", 1)[0] if "/" in e.path else "root"
            grouped.setdefault(top, []).append(e)
        lines = ["# Advertpreneur Project Memory", "", f"Generated locally: {self.data.get('updated_at','')}", "", "Cloud tokens used for indexing: **0**", ""]
        for group in sorted(grouped)[:40]:
            lines.append(f"## {group}")
            for e in grouped[group][:80]:
                loc = f"L{e.line_start}-L{e.line_end}"
                lines.append(f"- `{e.path}` · `{e.symbol}` · {e.kind} · {loc}")
            lines.append("")
        self.memory_path.write_text("\n".join(lines)[:180_000], encoding="utf-8")

    def entries(self) -> List[CodeEntry]:
        return [CodeEntry.from_dict(x) for x in (self.data.get("entries") or [])]

    def search(self, query: str, limit: int = 8) -> List[tuple[int, CodeEntry]]:
        qwords = _words(query)
        if not qwords:
            return []
        ranked: List[tuple[int, CodeEntry]] = []
        for e in self.entries():
            hay_path = e.path.lower()
            hay_symbol = e.symbol.lower()
            tagset = set(e.tags)
            score = 0
            matches = 0
            for q in qwords:
                hit = False
                if q in hay_symbol:
                    score += 8
                    hit = True
                if q in tagset:
                    score += 5
                    hit = True
                if q in hay_path:
                    score += 4
                    hit = True
                if q in (e.summary or "").lower() or q in (e.enriched_summary or "").lower():
                    score += 2
                    hit = True
                matches += int(hit)
            if score:
                score += min(matches, 4) * 3
                if e.kind in {"route", "class", "config"}:
                    score += 1
                ranked.append((score, e))
        ranked.sort(key=lambda x: (-x[0], x[1].path, x[1].line_start))
        # Avoid six symbols from the same file swamping the hints.
        out: List[tuple[int, CodeEntry]] = []
        per_file: Dict[str, int] = {}
        for score, e in ranked:
            if per_file.get(e.path, 0) >= 2:
                continue
            out.append((score, e))
            per_file[e.path] = per_file.get(e.path, 0) + 1
            if len(out) >= limit:
                break
        return out

    def hints(self, query: str, limit: int = 4, min_score: int = 8) -> str:
        rows = [(s, e) for s, e in self.search(query, limit=limit) if s >= min_score]
        if not rows:
            return ""
        lines = ["Local map targets (verify before editing):"]
        for _score, e in rows:
            lines.append(f"- {e.path}:{e.line_start}-{e.line_end} · {e.kind} {e.symbol}")
        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        return {
            "ready": self.ready, "files": self.file_count, "entries": self.entry_count,
            "updated_at": self.data.get("updated_at") or "never", "map_path": str(self.map_path),
            "memory_path": str(self.memory_path), "cloud_tokens": 0,
        }

    def enrich_local(self, client: Any, model: str, max_entries: int = 50) -> Dict[str, Any]:
        """Optional local-only semantic enrichment. Never pass a cloud client here."""
        if getattr(client, "provider", None) != "local":
            raise RuntimeError("Index enrichment is local-only by policy.")
        entries = self.entries()
        candidates = [e for e in entries if e.kind in {"class", "function", "route", "config"} and not e.enriched_summary][:max_entries]
        if not candidates:
            return {"enriched": 0, "local_input_tokens": 0, "local_output_tokens": 0, "cloud_tokens": 0}
        total_in = total_out = 0
        enriched = 0
        # Small batches keep the 1.7B local model responsive and avoid huge local contexts.
        for start in range(0, len(candidates), 10):
            batch = candidates[start:start + 10]
            payload = [{"id": e.id, "path": e.path, "symbol": e.symbol, "kind": e.kind, "tags": e.tags[:10]} for e in batch]
            messages = [
                {"role": "system", "content": "You enrich a local code index. Return JSON only: an array of {id,summary,tags}. Summaries <=18 words. Do not invent implementation details beyond names/paths."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            result = client.chat(model, messages, [], think=False, max_output_tokens=500)
            total_in += result.input_tokens
            total_out += result.output_tokens
            text = str((result.message or {}).get("content") or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
            try:
                rows = json.loads(text)
            except Exception:
                continue
            if not isinstance(rows, list):
                continue
            lookup = {e.id: e for e in entries}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                e = lookup.get(str(row.get("id") or ""))
                if not e:
                    continue
                e.enriched_summary = str(row.get("summary") or "")[:220]
                extra = [str(x).lower() for x in (row.get("tags") or []) if str(x).strip()]
                e.tags = list(dict.fromkeys([*e.tags, *extra]))[:36]
                enriched += 1
        self.data["entries"] = [asdict(e) for e in entries]
        self.data["updated_at"] = _now()
        self.map_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_memory(entries)
        return {"enriched": enriched, "local_input_tokens": total_in, "local_output_tokens": total_out, "cloud_tokens": 0}
