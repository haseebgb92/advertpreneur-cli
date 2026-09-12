from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class SkillInfo:
    id: str
    name: str
    description: str
    path: Path
    plugin_name: str


@dataclass
class PluginInfo:
    name: str
    version: str
    description: str
    path: Path
    source: str
    enabled: bool = True
    skills: List[Path] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name


class PluginManager:
    """Imports installed Codex plugin *skills* without copying a provider runtime.

    Advertpreneur asks the installed `codex` executable for the authoritative plugin
    inventory (`codex plugin list --json`) and reads each installed plugin's SKILL.md
    files. Full skill bodies are loaded lazily through the `load_skill` tool instead
    of being injected into every model request. This both mirrors how skills are meant
    to be task-specific and materially reduces token overhead.
    """

    def __init__(self, app_dir: Path, project: Path) -> None:
        self.app_dir = app_dir
        self.project = project
        self.state_path = app_dir / "plugins.json"
        self._state = self._load_state()
        self._cache: List[PluginInfo] | None = None
        self._skills_cache: List[SkillInfo] | None = None

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"enabled": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def _enabled(self, name: str, default: bool = True) -> bool:
        return bool(self._state.setdefault("enabled", {}).get(name, default))

    def set_enabled(self, name: str, enabled: bool) -> None:
        self._state.setdefault("enabled", {})[name] = bool(enabled)
        self._save_state()
        self._cache = None
        self._skills_cache = None

    @staticmethod
    def _skill_metadata(path: Path, fallback_name: str) -> tuple[str, str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return fallback_name, ""
        name = fallback_name
        desc = ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                front = text[3:end]
                for line in front.splitlines():
                    key, sep, value = line.partition(":")
                    if not sep:
                        continue
                    key = key.strip().lower()
                    value = value.strip().strip('"\'')
                    if key == "name" and value:
                        name = value
                    elif key in {"description", "summary"} and value:
                        desc = value
        if not desc:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith(("#", "---", "```")):
                    desc = stripped
                    break
        return name, re.sub(r"\s+", " ", desc)[:220]

    @staticmethod
    def _skill_paths(root: Path, skill_ref: Any) -> List[Path]:
        refs: List[str] = []
        if isinstance(skill_ref, str):
            refs = [skill_ref]
        elif isinstance(skill_ref, list):
            refs = [str(x) for x in skill_ref if isinstance(x, (str, Path))]
        elif isinstance(skill_ref, dict):
            refs = [str(x) for x in skill_ref.values() if isinstance(x, (str, Path))]
        if not refs:
            refs = ["./skills/"]
        out: List[Path] = []
        for ref in refs:
            candidate = (root / ref).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.is_file() and candidate.name.upper() == "SKILL.MD":
                out.append(candidate)
            elif candidate.is_dir():
                direct = candidate / "SKILL.md"
                if direct.exists():
                    out.append(direct)
                out.extend(sorted(candidate.glob("*/SKILL.md")))
        # Be forgiving of plugin manifests that omit skills metadata.
        fallback = root / "skills"
        if fallback.exists():
            out.extend(sorted(fallback.glob("*/SKILL.md")))
            if (fallback / "SKILL.md").exists():
                out.append(fallback / "SKILL.md")
        return list(dict.fromkeys(p.resolve() for p in out if p.exists()))

    def _from_manifest(self, root: Path, source: str, overrides: Dict[str, Any] | None = None) -> PluginInfo | None:
        manifest_path = root / ".codex-plugin" / "plugin.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        overrides = overrides or {}
        interface = data.get("interface") if isinstance(data.get("interface"), dict) else {}
        name = str(overrides.get("name") or interface.get("displayName") or data.get("name") or root.name)
        version = str(overrides.get("version") or data.get("version") or "")
        description = str(
            overrides.get("description")
            or interface.get("shortDescription")
            or data.get("description")
            or "Codex plugin"
        )
        skills = self._skill_paths(root, data.get("skills"))
        mcp_names: List[str] = []
        mcp_ref = data.get("mcpServers") or data.get("mcp_servers")
        if isinstance(mcp_ref, str):
            mcp_path = (root / mcp_ref).resolve()
            if mcp_path.exists():
                try:
                    mcp_data = json.loads(mcp_path.read_text(encoding="utf-8"))
                    server_map = mcp_data.get("mcp_servers", mcp_data) if isinstance(mcp_data, dict) else {}
                    if isinstance(server_map, dict):
                        mcp_names = [str(k) for k in server_map]
                except Exception:
                    pass
        elif isinstance(mcp_ref, dict):
            mcp_names = [str(k) for k in mcp_ref]
        return PluginInfo(
            name=name,
            version=version,
            description=description,
            path=root,
            source=source,
            enabled=self._enabled(name, bool(overrides.get("enabled", True))),
            skills=skills,
            mcp_servers=mcp_names,
        )

    def _discover_codex_cli(self) -> List[PluginInfo]:
        codex = shutil.which("codex")
        if not codex:
            return []
        try:
            proc = subprocess.run(
                [codex, "plugin", "list", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            if proc.returncode != 0:
                return []
            data = json.loads(proc.stdout or "{}")
        except Exception:
            return []
        if isinstance(data, dict):
            installed = data.get("installed") or data.get("plugins") or []
        elif isinstance(data, list):
            installed = data
        else:
            installed = []
        if not isinstance(installed, list):
            return []
        out: List[PluginInfo] = []
        for item in installed:
            if not isinstance(item, dict):
                continue
            raw_path = item.get("installedPath") or item.get("installed_path") or item.get("path")
            if not raw_path:
                continue
            info = self._from_manifest(
                Path(str(raw_path)).expanduser(),
                "Codex installed plugin",
                {
                    "name": item.get("name") or item.get("pluginId") or item.get("plugin_id"),
                    "version": item.get("version"),
                    "enabled": item.get("enabled", True),
                },
            )
            if info:
                out.append(info)
        return out

    def _scan_manifests(self) -> List[PluginInfo]:
        roots = [
            self.project / ".agents" / "plugins",
            self.project / ".codex" / "plugins",
            Path.home() / ".agents" / "plugins",
            Path.home() / ".codex" / "plugins",
        ]
        out: List[PluginInfo] = []
        seen: set[Path] = set()
        for base in roots:
            if not base.exists():
                continue
            try:
                manifests = list(base.glob("*/.codex-plugin/plugin.json"))[:200]
            except OSError:
                continue
            for manifest in manifests:
                root = manifest.parent.parent.resolve()
                if root in seen:
                    continue
                seen.add(root)
                info = self._from_manifest(root, "repo/personal plugin")
                if info:
                    out.append(info)
        return out

    def _standalone_skills_plugin(self) -> PluginInfo | None:
        # Import loose SKILL.md folders too. This makes personal Codex skills usable
        # even when they are not packaged inside a marketplace plugin.
        roots = [
            self.project / ".agents" / "skills",
            self.project / ".codex" / "skills",
            Path.home() / ".agents" / "skills",
            Path.home() / ".codex" / "skills",
        ]
        paths: List[Path] = []
        for base in roots:
            if base.exists():
                paths.extend(sorted(base.glob("*/SKILL.md")))
                if (base / "SKILL.md").exists():
                    paths.append(base / "SKILL.md")
        paths = list(dict.fromkeys(p.resolve() for p in paths))
        if not paths:
            return None
        return PluginInfo(
            name="Personal Codex Skills",
            version="",
            description="Standalone personal/project Codex skills",
            path=Path.home() / ".codex",
            source="Codex skills folders",
            enabled=self._enabled("Personal Codex Skills", True),
            skills=paths,
        )

    def discover(self, refresh: bool = False) -> List[PluginInfo]:
        if self._cache is not None and not refresh:
            return list(self._cache)
        if refresh:
            self._skills_cache = None
        found = self._discover_codex_cli() + self._scan_manifests()
        loose = self._standalone_skills_plugin()
        if loose:
            found.append(loose)
        dedup: Dict[str, PluginInfo] = {}
        for p in found:
            key = str(p.path.resolve()).lower() + "::" + p.name.lower()
            dedup[key] = p
        self._cache = sorted(dedup.values(), key=lambda p: p.name.lower())
        return list(self._cache)

    def skills(self, enabled_only: bool = True) -> List[SkillInfo]:
        if self._skills_cache is None:
            out: List[SkillInfo] = []
            used_ids: set[str] = set()
            for plugin in self.discover():
                for path in plugin.skills:
                    name, desc = self._skill_metadata(path, path.parent.name)
                    base = re.sub(r"[^a-z0-9_-]+", "-", f"{plugin.name}-{name}".lower()).strip("-") or path.parent.name.lower()
                    skill_id = base
                    n = 2
                    while skill_id in used_ids:
                        skill_id = f"{base}-{n}"
                        n += 1
                    used_ids.add(skill_id)
                    out.append(SkillInfo(skill_id, name, desc, path, plugin.name))
            self._skills_cache = out
        if not enabled_only:
            return list(self._skills_cache)
        enabled = {p.name for p in self.discover() if p.enabled}
        return [s for s in self._skills_cache if s.plugin_name in enabled]

    def catalog(self, max_chars: int = 1800) -> str:
        skills = self.skills(enabled_only=True)
        if not skills:
            return ""
        lines = [
            "Installed Codex skills available through the `load_skill` tool. Load a relevant skill before doing work it governs; do not guess its instructions:"
        ]
        used = len(lines[0])
        for s in skills:
            line = f"- {s.id}: {s.name} [{s.plugin_name}] — {s.description or 'skill instructions'}"
            if used + len(line) + 1 > max_chars:
                lines.append("- … additional skills available; call load_skill with a search term to discover them")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def search_skills(self, query: str = "") -> List[SkillInfo]:
        skills = self.skills(enabled_only=True)
        q = query.strip().lower()
        if not q:
            return skills
        terms = [x for x in re.split(r"[^a-z0-9]+", q) if len(x) > 1]
        scored: List[tuple[int, SkillInfo]] = []
        for s in skills:
            hay = f"{s.id} {s.name} {s.description} {s.plugin_name}".lower()
            score = 100 if q == s.id.lower() else sum(3 if term in s.name.lower() else 1 for term in terms if term in hay)
            if score:
                scored.append((score, s))
        return [s for _, s in sorted(scored, key=lambda x: (-x[0], x[1].name.lower()))]

    def load_skill(self, query: str) -> str:
        matches = self.search_skills(query)
        exact = [s for s in matches if query.lower() in {s.id.lower(), s.name.lower()}]
        if exact:
            matches = exact
        if not matches:
            return f"No enabled installed skill matched '{query}'."
        if len(matches) > 1 and not exact:
            lines = [f"Multiple installed skills match '{query}'. Call load_skill again with an exact id:"]
            lines.extend(f"- {s.id}: {s.name} [{s.plugin_name}] — {s.description}" for s in matches[:20])
            return "\n".join(lines)
        skill = matches[0]
        try:
            body = skill.path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            return f"Could not read skill {skill.id}: {exc}"
        # Skill bodies can be very large. Load enough instruction text to govern the
        # task without injecting an entire handbook into every agent loop. The full
        # source remains inspectable through /skills.
        max_body = 12000
        clipped = body[:max_body]
        if len(body) > max_body:
            clipped += f"\n\n…[skill clipped by Advertpreneur CLI: {len(body) - max_body:,} chars omitted for token efficiency]"
        return f"SKILL {skill.id}\nPlugin: {skill.plugin_name}\nSource: {skill.path}\n\n{clipped}"

    # Backwards-compatible method used by older tests/callers. Keep it intentionally
    # compact rather than injecting full skill bodies into every request.
    def skill_context(self, max_chars: int = 1800) -> str:
        return self.catalog(max_chars=max_chars)
