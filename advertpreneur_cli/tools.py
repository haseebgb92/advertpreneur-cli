from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, TYPE_CHECKING

from .browser_control import BrowserController, BrowserUnavailable
from .wordpress import DeleteTarget, DeletionProposal
from .wp_workspace import WordPressWorkspace, WorkspaceError
from .site_adapters import SiteAdapterRegistry, SiteAdapterError, SiteProfile
from .operations import OperationLedger, ProjectIdentity
from .panel_playbooks import PanelPlaybookRegistry, PlaybookError
from .research_workflow import ResearchRun
from .xray_workflow import XrayProgress, next_xray_action

if TYPE_CHECKING:
    from .resource_guard import ResourceGuard
from .web_tools import search_web, fetch_web

if TYPE_CHECKING:
    from .plugins import PluginManager
    from .mcp import MCPManager
    from .project_index import ProjectIndex


class ToolError(RuntimeError):
    pass


def tool_schema(name: str, description: str, properties: Dict[str, Any], required: List[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


BASE_SCHEMAS = [
    tool_schema("list_files", "List project files/folders.", {
        "path": {"type": "string"}, "recursive": {"type": "boolean"}, "max_entries": {"type": "integer"},
    }),
    tool_schema("read_file", "Read a project text file with line numbers.", {
        "path": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer"},
    }, ["path"]),
    tool_schema("search_text", "Search project text. Use project_map first when the local code map may know the relevant symbol/file.", {
        "pattern": {"type": "string"}, "path": {"type": "string"}, "regex": {"type": "boolean"}, "max_matches": {"type": "integer"},
    }, ["pattern"]),
    tool_schema("project_map", "Search Advertpreneur's local zero-cloud project code map for relevant files/symbols before broad scanning.", {
        "query": {"type": "string"}, "limit": {"type": "integer"},
    }, ["query"]),
    tool_schema("write_file", "Create or fully replace a project text file.", {
        "path": {"type": "string"}, "content": {"type": "string"},
    }, ["path", "content"]),
    tool_schema("replace_in_file", "Replace an exact text block in one project file.", {
        "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}, "replace_all": {"type": "boolean"},
    }, ["path", "old_text", "new_text"]),
    tool_schema("run_command", "Run a build/test/git/diagnostic command inside the project.", {
        "command": {"type": "string"}, "cwd": {"type": "string"}, "timeout_seconds": {"type": "integer"},
    }, ["command"]),
    tool_schema("git_status", "Get concise Git status.", {}),
    tool_schema("git_diff", "Get Git diff.", {"staged": {"type": "boolean"}}),
]


RESEARCH_SCHEMAS = [
    tool_schema("web_search", "Search the public web when local code/project evidence and the engineering handbook are insufficient. Prefer official/vendor documentation for technical facts.", {
        "query": {"type": "string"}, "max_results": {"type": "integer"},
    }, ["query"]),
    tool_schema("web_fetch", "Fetch readable text from one http/https page found through research. Keep use targeted; do not fetch unrelated pages.", {
        "url": {"type": "string"}, "max_chars": {"type": "integer"},
    }, ["url"]),
]

BROWSER_SCHEMAS = [
    tool_schema("browser", "Control the user's local browser. Prefer measured reverse-engineering over visual guessing. Browser actions must be verified by observable state. Learned routines replay deterministic browser steps without model rediscovery.", {
        "action": {"type": "string", "enum": ["status", "operations_status", "navigate", "open", "site_detect", "site_profile", "site_playbook", "site_open", "site_upload", "inspect", "screenshot", "reverse_engineer", "click", "fill", "scroll", "wait", "upload", "wordpress_state", "workspace_list", "workspace_stage", "workspace_zip", "workspace_extract", "workspace_move", "wordpress_propose_delete", "wordpress_approve_delete", "wordpress_delete", "research_start", "research_xray_setup", "research_status", "research_search", "research_export", "research_run", "research_pause", "research_resume", "run_routine", "routine_list", "close"]},
        "url": {"type": "string"}, "selector": {"type": "string"}, "name": {"type": "string"}, "tab": {"type": "string", "description": "Named Browser Bridge tab slot, such as access, helium, or amazon"}, "capture_tab": {"type": "string", "description": "Save a new tab opened by click into this named slot"},
        "value": {"type": "string"}, "amount": {"type": ["integer", "string"]}, "milliseconds": {"type": "integer"}, "repeat": {"type": "integer"},
        "max_elements": {"type": "integer"}, "full_page": {"type": "boolean"}, "file_path": {"type": "string"}, "proposal_id": {"type": "string"}, "approval_token": {"type": "string"},
    }, ["action"]),
]

WINDOWS_SCHEMAS = [
    tool_schema("windows", "Perform an approval-governed local Windows operation. Use for opening a verified local folder/file or launching a named local app; never use it to bypass browser/site safeguards.", {
        "action": {"type": "string", "enum": ["status", "open_path", "open_app"]},
        "path": {"type": "string"}, "app": {"type": "string"},
    }, ["action"]),
]


EXTENSION_SCHEMAS = [
    tool_schema("load_skill", "Load an installed Codex skill by exact id/name or search term. Use when a relevant installed skill is listed in the system capability catalog.", {
        "skill": {"type": "string", "description": "Exact skill id/name, or search term"},
    }, ["skill"]),
    tool_schema("mcp", "Use MCP servers configured in Codex. Query tool names narrowly; call only the tool needed.", {
        "action": {"type": "string", "enum": ["list_servers", "list_tools", "call"]},
        "server": {"type": "string"},
        "query": {"type": "string", "description": "Optional keywords to filter list_tools"},
        "limit": {"type": "integer", "description": "Maximum tools returned; default 12"},
        "tool": {"type": "string"},
        "arguments": {"type": "object", "additionalProperties": True},
    }, ["action"]),
]

# Compatibility constant; CodingAgent now asks the registry for the smallest
# relevant schema set at runtime.
SCHEMAS = BASE_SCHEMAS + RESEARCH_SCHEMAS + BROWSER_SCHEMAS + WINDOWS_SCHEMAS + EXTENSION_SCHEMAS


class ToolRegistry:
    def __init__(
        self,
        root: Path,
        approval_mode: str = "safe",
        max_output_chars: int = 16000,
        approve: Callable[[str, str], bool] | None = None,
        plugin_manager: "PluginManager | None" = None,
        mcp_manager: "MCPManager | None" = None,
        project_index: "ProjectIndex | None" = None,
        browser_visible: bool = True,
        resource_guard: "ResourceGuard | None" = None,
    ) -> None:
        self.root = root.resolve()
        self.approval_mode = approval_mode
        self.max_output_chars = max_output_chars
        self.approve = approve or (lambda _kind, _detail: False)
        self.plugin_manager = plugin_manager
        self.mcp_manager = mcp_manager
        self.project_index = project_index
        self.browser_visible = bool(browser_visible)
        self.resource_guard = resource_guard
        self.browser_controller = BrowserController(self.root, visible=self.browser_visible)
        self.wp_workspace = WordPressWorkspace(self.root)
        self._delete_proposals: dict[str, DeletionProposal] = {}
        self.site_adapters = SiteAdapterRegistry()
        self.site_profile_path = self.root / ".advertpreneur" / "site-profile.json"
        self.operations = OperationLedger(self.root)
        self.panel_playbooks = PanelPlaybookRegistry()

    def schemas(self, task: str = "", history: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
        """Return the smallest useful tool schema set for the current task.

        Session/menu features are implemented by the CLI and never exposed to the model.
        Extension tools are lazy: skill/MCP schemas appear only when the task explicitly
        references them or a matching installed extension. This keeps routine coding calls
        from paying for unrelated capability descriptions.
        """
        text = (task or "").lower()
        history = history or []
        used = set()
        for m in history[-12:]:
            if m.get("role") == "tool":
                used.add(str(m.get("tool_name") or ""))
            for call in m.get("tool_calls") or [] if isinstance(m, dict) else []:
                fn = (call or {}).get("function", {}) if isinstance(call, dict) else {}
                used.add(str(fn.get("name") or ""))

        # Deterministic intent gating keeps Ollama/local/cloud requests from paying
        # for coding tool schemas when the user is merely chatting or asking a
        # general question. Once a task is clearly project/code related, expose only
        # the minimum capability family needed for that task.
        coding_terms = (
            "code", "file", "project", "repo", "repository", "function", "class", "bug", "error", "stack trace",
            "wordpress", "woocommerce", "shopify", "android", "python", "javascript", "typescript", "php", "html", "css",
            "build", "test", "lint", "compile", "implement", "fix", "edit", "change", "create", "add", "remove", "refactor",
            "inspect", "review", "debug", "search", "find", "read", "write", "replace", "@",
        )
        mutate_terms = (
            "implement", "fix", "edit", "change", "create", "add", "remove", "refactor", "update", "modify", "write",
            "build", "compile", "test", "lint", "run", "install", "migrate", "patch",
        )
        inspect_terms = (
            "inspect", "review", "debug", "explain this", "where is", "find", "search", "read", "trace", "source", "code",
            "file", "project", "repo", "repository", "wordpress", "woocommerce", "shopify", "android",
        )
        project_related = any(x in text for x in coding_terms) or bool(used)
        mutating_task = any(x in text for x in mutate_terms) or {"write_file", "replace_in_file", "run_command"} & used
        inspect_task = any(x in text for x in inspect_terms) or mutating_task or {"read_file", "search_text", "project_map"} & used

        names = set()
        if project_related and inspect_task:
            names.update({"read_file", "search_text", "project_map"})
        if project_related and mutating_task:
            names.update({"write_file", "replace_in_file", "run_command"})

        broad_terms = ("scan", "list files", "project structure", "repo structure", "repository structure", "explore the repo", "inspect the repo")
        if any(x in text for x in broad_terms) or "list_files" in used:
            names.add("list_files")
        git_terms = ("git", "diff", "review", "uncommitted", "staged", "branch")
        if (self.root / ".git").exists() and (any(x in text for x in git_terms) or {"git_status", "git_diff"} & used):
            names.update({"git_status", "git_diff"})

        schemas = [x for x in BASE_SCHEMAS if x.get("function", {}).get("name") in names]

        skill_needed = any(x in text for x in ("skill", "plugin", "impeccable", "uiux", "ui/ux", "design", "frontend", "html", "css", "wordpress", "woocommerce", "shopify", "screenshot", "reverse engineer")) or "load_skill" in used
        if self.plugin_manager:
            try:
                task_terms = {x for x in re.findall(r"[a-z0-9_-]{4,}", text) if x not in {"this", "that", "with", "from", "into", "make", "file", "code"}}
                for sk in self.plugin_manager.skills(enabled_only=True):
                    hay = f"{sk.name} {sk.id} {sk.plugin_name} {getattr(sk, 'description', '')}".lower()
                    if sk.name.lower() in text or sk.id.lower() in text or sk.plugin_name.lower() in text or len([t for t in task_terms if t in hay]) >= 2:
                        skill_needed = True
                        break
            except Exception:
                pass
        if skill_needed and self.plugin_manager:
            try:
                if self.plugin_manager.skills(enabled_only=True):
                    schemas.append(EXTENSION_SCHEMAS[0])
            except Exception:
                pass

        mcp_needed = "mcp" in text or "mcp" in used
        if self.mcp_manager:
            try:
                servers = [s for s in self.mcp_manager.discover() if s.enabled]
                if any(s.name.lower() in text for s in servers):
                    mcp_needed = True
                if mcp_needed and servers:
                    schemas.append(EXTENSION_SCHEMAS[1])
            except Exception:
                pass
        research_terms = ("search the web", "web search", "internet", "online", "official docs", "documentation", "latest", "current version", "look up", "error", "exception", "failed", "failure", "deprecated", "unsupported", "dependency version", "api docs")
        if any(x in text for x in research_terms) or {"web_search", "web_fetch"} & used:
            schemas.extend(RESEARCH_SCHEMAS)

        browser_terms = ("browser", "website", "web page", "reference site", "screenshot", "reverse engineer", "reverse-engineer", "pixel", "design map", "navigate to", "open the site", "open website", "http://", "https://", "wp-admin", "wordpress", "hostinger", "cpanel", "plesk", "file manager", "upload plugin", "upload theme", "install plugin", "activate theme", "edit post", "edit page", "wordpress settings", "amazon", "helium 10", "helium10", "xray report", "xray", "keyword research", "keywords")
        if any(x in text for x in browser_terms) or "browser" in used:
            schemas.extend(BROWSER_SCHEMAS)
        windows_terms = ("windows", "desktop", "local folder", "file explorer", "open folder", "open app", "launch app")
        if any(x in text for x in windows_terms) or "windows" in used:
            schemas.extend(WINDOWS_SCHEMAS)
        return schemas

    def _path(self, relative: str | None) -> Path:
        rel = relative or "."
        candidate = Path(rel).expanduser().resolve() if (Path(rel).is_absolute() or str(rel).startswith("~")) else (self.root / rel).resolve()
        desktop = (Path.home() / "Desktop").resolve()
        try:
            candidate.relative_to(self.root)
            return candidate
        except ValueError:
            try:
                candidate.relative_to(desktop)
                return candidate
            except ValueError:
                pass
            raise ToolError(f"Path escapes project root: {relative}")

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        fn = getattr(self, f"tool_{name}", None)
        if not fn:
            raise ToolError(f"Unknown tool: {name}")
        try:
            result = fn(**args)
        except TypeError as exc:
            raise ToolError(f"Invalid arguments for {name}: {exc}") from exc
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        if len(result) > self.max_output_chars:
            omitted = len(result) - self.max_output_chars
            result = result[: self.max_output_chars] + f"\n...[truncated {omitted} chars]"
        return result

    def tool_windows(self, action: str, path: str = "", app: str = "") -> str:
        if os.name != "nt":
            raise ToolError("Windows operations are available only on Windows hosts.")
        action = str(action or "").lower().strip()
        if action == "status":
            return "Windows local operations ready · approval required for launch actions"
        if action == "open_path":
            candidate = Path(path).expanduser().resolve()
            if not candidate.exists():
                raise ToolError(f"Local path not found: {candidate}")
            if not self.approve("windows", f"open local path {candidate}"):
                raise ToolError("User declined Windows open operation.")
            os.startfile(str(candidate))
            return f"Opened local path · {candidate}"
        if action == "open_app":
            allowed = {"notepad", "explorer", "calc", "mspaint"}
            target = str(app or "").lower().strip()
            if target not in allowed:
                raise ToolError("Allowed local apps: notepad, explorer, calc, mspaint.")
            if not self.approve("windows", f"open local app {target}"):
                raise ToolError("User declined Windows app launch.")
            subprocess.Popen([target], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return f"Opened local app · {target}"
        raise ToolError("Windows action must be status, open_path, or open_app.")

    def tool_list_files(self, path: str = ".", recursive: bool = False, max_entries: int = 300) -> str:
        base = self._path(path)
        if not base.exists() or not base.is_dir():
            raise ToolError(f"Directory not found: {path}")
        max_entries = min(max(1, int(max_entries)), 1000)
        ignore = {".git", ".advertpreneur", "node_modules", ".venv", "venv", "dist", "build", ".next", "__pycache__"}
        entries = []
        iterator = base.rglob("*") if recursive else base.iterdir()
        for p in iterator:
            if any(part in ignore for part in p.relative_to(self.root).parts):
                continue
            suffix = "/" if p.is_dir() else ""
            entries.append(str(p.relative_to(self.root)).replace("\\", "/") + suffix)
            if len(entries) >= max_entries:
                entries.append("...[entry limit reached]")
                break
        return "\n".join(sorted(entries)) or "(empty)"

    def tool_read_file(self, path: str, start_line: int = 1, max_lines: int = 300) -> str:
        p = self._path(path)
        if not p.exists() or not p.is_file():
            raise ToolError(f"File not found: {path}")
        if p.stat().st_size > 2_000_000:
            raise ToolError("File is over 2 MB; use search_text or a narrower source file.")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(start_line))
        count = min(max(1, int(max_lines)), 1000)
        selected = lines[start - 1 : start - 1 + count]
        return "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start=start)) or "(no lines)"

    def tool_search_text(self, pattern: str, path: str = ".", regex: bool = False, max_matches: int = 100) -> str:
        base = self._path(path)
        if not base.exists():
            raise ToolError(f"Path not found: {path}")
        max_matches = min(max(1, int(max_matches)), 500)
        rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
        ignore_dirs = {".git", ".advertpreneur", "node_modules", ".venv", "venv", "dist", "build", ".next", "__pycache__"}
        files = [base] if base.is_file() else base.rglob("*")
        out: List[str] = []
        for p in files:
            if not p.is_file() or any(part in ignore_dirs for part in p.relative_to(self.root).parts):
                continue
            try:
                if p.stat().st_size > 1_000_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for idx, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    out.append(f"{p.relative_to(self.root)}:{idx}: {line[:500]}")
                    if len(out) >= max_matches:
                        return "\n".join(out) + "\n...[match limit reached]"
        return "\n".join(out) or "(no matches)"


    def tool_project_map(self, query: str, limit: int = 8) -> str:
        if not self.project_index or not self.project_index.ready:
            return "Project map is not ready yet. Use a targeted search/read; /index can build the local map without cloud tokens."
        limit = min(max(1, int(limit or 8)), 20)
        rows = self.project_index.search(query, limit=limit)
        if not rows:
            return "(no project-map matches)"
        lines = [f"Local project-map matches for {query!r} (cloud lookup tokens: 0)"]
        for score, e in rows:
            summary = e.enriched_summary or e.summary
            tail = f" — {summary[:140]}" if summary else ""
            lines.append(f"- {e.path}:{e.line_start}-{e.line_end} · {e.kind} `{e.symbol}` · score {score}{tail}")
        return "\n".join(lines)

    def _approve_write(self, detail: str) -> None:
        if self.approval_mode == "ask" and not self.approve("write", detail):
            raise ToolError("User declined write operation.")

    def tool_write_file(self, path: str, content: str) -> str:
        p = self._path(path)
        self._approve_write(f"write {p.relative_to(self.root)} ({len(content)} chars)")
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"{'Updated' if existed else 'Created'} {p.relative_to(self.root)} ({len(content)} chars)."

    def tool_replace_in_file(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        p = self._path(path)
        if not p.exists() or not p.is_file():
            raise ToolError(f"File not found: {path}")
        text = p.read_text(encoding="utf-8", errors="strict")
        count = text.count(old_text)
        if count == 0:
            raise ToolError("old_text was not found exactly. Re-read the relevant file section and retry.")
        if count > 1 and not replace_all:
            raise ToolError(f"old_text occurs {count} times; provide a more specific block or set replace_all=true.")
        self._approve_write(f"edit {p.relative_to(self.root)} ({count} replacement{'s' if count != 1 else ''})")
        result = text.replace(old_text, new_text, -1 if replace_all else 1)
        p.write_text(result, encoding="utf-8")
        return f"Updated {p.relative_to(self.root)} ({count if replace_all else 1} replacement(s))."

    def _command_risk(self, command: str) -> str:
        c = command.strip().lower()
        hard = [
            r"\bformat\b", r"\bdiskpart\b", r"\bshutdown\b", r"\brestart-computer\b",
            r"remove-item\s+.*-[a-z]*recurse", r"\brm\s+-rf\b", r"\brmdir\s+/s\b",
            r"del\s+/[a-z]*[sq]", r"\breg\s+delete\b", r"\bsc\s+delete\b",
        ]
        if any(re.search(p, c) for p in hard):
            return "hard"
        safe_prefixes = (
            "git status", "git diff", "git log", "git branch", "git show", "git rev-parse",
            "python -m pytest", "python -m unittest", "pytest", "npm test", "npm run test",
            "npm run build", "npm run lint", "pnpm test", "pnpm run test", "pnpm build", "pnpm run build",
            "pnpm lint", "pnpm run lint", "yarn test", "yarn build", "composer test", "php -l",
            "dotnet test", "dotnet build", "cargo test", "cargo check", "go test", "mvn test", "gradlew test",
            ".\\gradlew test", "gradlew.bat test", "cmake --build", "ctest", "dir", "ls", "where ", "which ",
        )
        if c.startswith(safe_prefixes):
            return "safe"
        return "ask"

    @staticmethod
    def _resource_heavy_command(command: str) -> bool:
        c = " ".join(str(command or "").lower().split())
        heavy_terms = (
            "gradlew", "gradle ", "npm run build", "pnpm run build", "pnpm build", "yarn build",
            "next build", "vite build", "dotnet build", "dotnet test", "cargo build", "cargo test",
            "mvn test", "mvn package", "cmake --build", "ctest", "pytest", "python -m pytest",
            "npm test", "pnpm test", "yarn test", "playwright test", "flutter build", "flutter test",
        )
        return any(x in c for x in heavy_terms)

    def tool_run_command(self, command: str, cwd: str = ".", timeout_seconds: int = 180) -> str:
        workdir = self._path(cwd)
        if not workdir.exists() or not workdir.is_dir():
            raise ToolError(f"Working directory not found: {cwd}")
        risk = self._command_risk(command)
        if risk == "hard":
            raise ToolError("Command blocked by V0 hard safety policy. Run it manually if you truly intend it.")
        if self.approval_mode == "ask" or (self.approval_mode == "safe" and risk != "safe"):
            if not self.approve("command", f"[{workdir.relative_to(self.root) or Path('.')}] {command}"):
                raise ToolError("User declined command.")
        timeout_seconds = min(max(1, int(timeout_seconds)), 1200)
        lease = None
        if self.resource_guard and self._resource_heavy_command(command):
            decision, lease = self.resource_guard.begin_heavy("build/test")
            if not decision.allowed:
                raise ToolError(decision.message or "Resource Guard deferred this heavy command to protect system responsiveness.")
        try:
            proc = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"Command timed out after {timeout_seconds}s") from exc
        finally:
            if lease:
                lease.close()
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        output = output.strip() or "(no output)"
        return f"exit_code={proc.returncode}\n{output}"

    def tool_web_search(self, query: str, max_results: int = 6) -> str:
        try:
            return search_web(query, max_results=max_results)
        except Exception as exc:
            raise ToolError(f"Web search failed: {exc}") from exc

    def tool_web_fetch(self, url: str, max_chars: int = 14000) -> str:
        try:
            return fetch_web(url, max_chars=max_chars)
        except Exception as exc:
            raise ToolError(f"Web fetch failed: {exc}") from exc

    def _auto_site_profile(self, url: str) -> str:
        """Persist a supported live surface after normal navigation; never stores auth."""
        try:
            adapter = self.site_adapters.detect(url)
        except SiteAdapterError:
            return ""
        parsed = re.match(r"^(https?://[^/]+)", url, flags=re.I)
        if not parsed:
            return ""
        profile = SiteProfile(adapter=adapter.key, base_url=parsed.group(1))
        profile.save(self.site_profile_path)
        self.operations.identity(ProjectIdentity.from_url(url, adapter.key))
        return f" · site adapter {adapter.key}"

    @staticmethod
    def _research_keywords(value: str) -> list[str]:
        return [" ".join(row.split()) for row in re.split(r"[\r\n,]+", str(value or "")) if " ".join(row.split())]

    @staticmethod
    def _research_pause_reason(observation: str) -> str:
        text = str(observation or "").lower()
        warnings = (
            ("verify you are human", "human verification page detected"),
            ("validatecaptcha", "verification page detected"),
            ("captcha", "CAPTCHA page detected"),
            ("unusual traffic", "traffic warning detected"),
            ("access denied", "access warning detected"),
            ("too many requests", "rate warning detected"),
            ("rate limit", "rate warning detected"),
            ("two-factor authentication", "two-factor authentication is required"),
            ("multi-factor authentication", "multi-factor authentication is required"),
            ("enter your mfa code", "multi-factor authentication is required"),
            ("authentication code", "multi-factor authentication is required"),
            ("sign in", "sign-in is required"),
            ("log in", "sign-in is required"),
        )
        return next((reason for needle, reason in warnings if needle in text), "")

    def _research_run(self, name: str) -> ResearchRun:
        if not str(name or "").strip():
            raise ToolError("Research actions require a run name")
        return ResearchRun.open(self.root, name)

    def _run_observed_xray(self, run: ResearchRun, known: dict[str, str], xray: dict[str, str], milliseconds: int) -> str:
        """Process a selector-backed Xray queue without guessing page state."""
        before = len(run.status()["completed"])
        search_wait = max(10_000, min(15_000, int(milliseconds)))
        while True:
            active = run.status()["active"]
            if active:
                keyword = str(active[0])
            else:
                observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                reason = self._research_pause_reason(observed)
                if reason:
                    return f"Research checkpoint · {reason} · {len(run.status()['completed']) - before} completed"
                keyword = run.next_keyword()
                if not keyword:
                    status = run.status()
                    return f"Research run complete · {len(status['completed']) - before} completed · {len(status['paused'])} paused"
                try:
                    self.browser_controller.fill(known["search"], keyword, tab="amazon")
                    self.browser_controller.click(known["submit"], tab="amazon")
                    self.browser_controller.wait(search_wait, tab="amazon")
                    observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                    reason = self._research_pause_reason(observed)
                    if reason:
                        run.record_outcome(keyword, "verification_required", reason, self.browser_controller.current_url)
                        return f"Research checkpoint · {keyword} · {reason} · {len(run.status()['completed']) - before} completed"
                except Exception as exc:
                    run.record_outcome(keyword, "site_changed", f"search action failed: {exc}", self.browser_controller.current_url)
                    continue

            phase = "open Xray"
            try:
                self.browser_controller.click(xray["open"], tab="amazon")
                self.browser_controller.wait(search_wait, tab="amazon")
                progress = XrayProgress()
                while True:
                    observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                    reason = self._research_pause_reason(observed)
                    if reason:
                        run.record_outcome(keyword, "verification_required", reason, self.browser_controller.current_url)
                        return f"Research checkpoint · {keyword} · {reason} · {len(run.status()['completed']) - before} completed"
                    rows = self.browser_controller.selector_state(xray["rows"], tab="amazon")
                    more = self.browser_controller.selector_state(xray["load_more"], tab="amazon")
                    action = next_xray_action(progress, int(rows.get("count") or 0), bool(more.get("visible")))
                    if action == "load_more":
                        phase = "load more"
                        self.browser_controller.click(xray["load_more"], tab="amazon")
                        self.browser_controller.wait(search_wait, tab="amazon")
                        progress = XrayProgress(previous_rows=int(rows.get("count") or 0), refreshed=progress.refreshed)
                        continue
                    if action == "refresh":
                        phase = "refresh Xray"
                        self.browser_controller.click(xray["refresh"], tab="amazon")
                        self.browser_controller.wait(search_wait, tab="amazon")
                        progress = XrayProgress(previous_rows=int(rows.get("count") or 0), refreshed=True)
                        continue
                    if action == "no_data":
                        run.record_outcome(keyword, "no_data", "Xray stayed empty or stalled after one refresh", self.browser_controller.current_url)
                        self.browser_controller.navigate(self.browser_controller.current_url, tab="amazon")
                        break
                    phase = "download"
                    marker = self.browser_controller.mark_download(tab="amazon")
                    self.browser_controller.click(xray["export"], tab="amazon")
                    self.browser_controller.wait(search_wait, tab="amazon")
                    self.browser_controller.click(xray["csv"], tab="amazon")
                    source = self.browser_controller.wait_for_download(marker, timeout_seconds=120, tab="amazon")
                    run.complete_download(keyword, source, self.browser_controller.current_url)
                    self.browser_controller.navigate(self.browser_controller.current_url, tab="amazon")
                    break
            except FileNotFoundError as exc:
                run.record_outcome(keyword, "download_missing", str(exc), self.browser_controller.current_url)
            except Exception as exc:
                state = "download_missing" if phase == "download" else "site_changed"
                run.record_outcome(keyword, state, f"{phase} failed: {exc}", self.browser_controller.current_url)

    def tool_browser(
        self, action: str, url: str = "", selector: str = "", name: str = "design-map", tab: str = "work", capture_tab: str = "",
        value: str = "", amount: int | str = 650, milliseconds: int = 750, repeat: int = 1,
        max_elements: int = 120, full_page: bool = True, file_path: str = "", proposal_id: str = "", approval_token: str = "",
    ) -> str:
        action = str(action or "").strip().lower()
        try:
            if self.resource_guard and action not in {"status", "close", "wait"} and not self.browser_controller.running:
                try:
                    extension_ready = bool(self.browser_controller._extension_available(wait_seconds=0.1))
                except Exception:
                    extension_ready = False
                if not extension_ready:
                    decision = self.resource_guard.preflight("browser", cpu=True)
                    if not decision.allowed:
                        raise ToolError(decision.message or "Resource Guard blocked a new browser process under critical memory pressure.")
            if action == "status":
                return self.browser_controller.status()
            if action == "operations_status":
                current = self.operations.resume()
                return json.dumps({"identity": self.operations.data.get("identity", {}), "checkpoint": current.__dict__ if current else None, "evidence_count": len(self.operations.data.get("evidence", []))}, ensure_ascii=False, default=lambda x: x.__dict__)
            if action in {"navigate", "open"}:
                if not url:
                    raise ToolError("browser navigate requires url")
                try:
                    result = self.browser_controller.navigate(url, tab=tab) if tab is not None else self.browser_controller.navigate(url)
                except TypeError:
                    result = self.browser_controller.navigate(url)
                return result + self._auto_site_profile(url)
            if action == "site_detect":
                adapter = self.site_adapters.detect(url or self.browser_controller.current_url)
                return json.dumps({"adapter": adapter.key, "label": adapter.label, "routes": sorted(adapter.routes)}, ensure_ascii=False)
            if action == "site_profile":
                if url and name:
                    profile = SiteProfile(adapter=name, base_url=url); self.site_adapters.get(profile.adapter); profile.save(self.site_profile_path)
                    return f"Site profile saved · {profile.adapter} · {profile.base_url}"
                profile = SiteProfile.load(self.site_profile_path)
                return json.dumps(profile.__dict__ if profile else {"configured": False})
            if action == "site_playbook":
                profile = SiteProfile.load(self.site_profile_path)
                if not profile: raise ToolError("No site profile; navigate to the live panel or configure one first")
                return json.dumps(self.panel_playbooks.get(profile.adapter, name).export(), ensure_ascii=False)
            if action == "site_open":
                profile = SiteProfile.load(self.site_profile_path)
                if not profile: raise ToolError("No site profile; use site_profile with adapter and base URL first")
                return self.browser_controller.navigate(self.site_adapters.get(profile.adapter).route(name, profile.base_url))
            if action == "site_upload":
                profile = SiteProfile.load(self.site_profile_path)
                if not profile: raise ToolError("No site profile; use site_profile first")
                return self.browser_controller.upload(self.wp_workspace.resolve(file_path), selector or self.site_adapters.get(profile.adapter).upload_selector)
            if action == "wordpress_state":
                return json.dumps(self.browser_controller.wordpress_state(), ensure_ascii=False)
            if action == "upload":
                staged = self.wp_workspace.resolve(file_path)
                return self.browser_controller.upload(staged, selector or 'input[type="file"]')
            if action == "workspace_list":
                return "Workspace files: " + (", ".join(self.wp_workspace.list_files()) or "none")
            if action == "workspace_stage":
                return f"Staged · {self.wp_workspace.stage(file_path, name or '').relative_to(self.wp_workspace.root).as_posix()}"
            if action == "workspace_zip":
                return f"Archive created · {self.wp_workspace.create_zip(file_path, name).relative_to(self.wp_workspace.root).as_posix()}"
            if action == "workspace_extract":
                return f"Archive extracted · {self.wp_workspace.extract(file_path, name).relative_to(self.wp_workspace.root).as_posix()}"
            if action == "workspace_move":
                return f"Workspace item moved · {self.wp_workspace.move(file_path, name).relative_to(self.wp_workspace.root).as_posix()}"
            if action == "wordpress_propose_delete":
                try:
                    target_rows = json.loads(value)
                except Exception as exc:
                    raise ToolError("wordpress_propose_delete value must be a JSON target list") from exc
                targets = [DeleteTarget(str(row.get("name") or ""), str(row.get("kind") or "item"), str(row.get("warning") or ""), str(row.get("path") or "")) for row in target_rows if isinstance(row, dict) and row.get("name")]
                proposal = DeletionProposal.create(targets); self._delete_proposals[proposal.token] = proposal
                return proposal.describe()
            if action == "wordpress_approve_delete":
                proposal = self._delete_proposals.get(proposal_id or approval_token)
                if not proposal or not proposal.approve(approval_token):
                    raise ToolError("Deletion proposal approval token is invalid")
                return "Deletion proposal approved. Use wordpress_delete with this proposal token and an observed selector."
            if action == "wordpress_delete":
                proposal = self._delete_proposals.get(proposal_id)
                if not proposal or not proposal.approved:
                    raise ToolError("Deletion needs a reviewed and approved proposal")
                if not selector:
                    raise ToolError("wordpress_delete requires the observed destructive-action selector")
                result = self.browser_controller.click(selector)
                self._delete_proposals.pop(proposal_id, None)
                return f"Deletion action dispatched after approved review · {result}"
            if action == "research_start":
                keywords = self._research_keywords(value)
                if not keywords:
                    raise ToolError("research_start requires a comma- or line-separated keyword list in value")
                if not self.browser_controller._extension_available(wait_seconds=0.8):
                    raise ToolError("Research runs require the connected Advertpreneur Browser Bridge extension and its existing browser session")
                run = ResearchRun.create(self.root, name, keywords)
                return json.dumps({"research_run": run.name, "ledger": str(run.ledger_path.relative_to(self.root)), **run.status()}, ensure_ascii=False)
            if action == "research_xray_setup":
                run = self._research_run(name)
                try:
                    selectors = json.loads(value)
                except Exception as exc:
                    raise ToolError("research_xray_setup value must be a JSON selector object") from exc
                if not isinstance(selectors, dict):
                    raise ToolError("research_xray_setup value must be a JSON selector object")
                required = ("search", "submit", "open", "rows", "load_more", "refresh", "export", "csv")
                missing = [key for key in required if not str(selectors.get(key) or "").strip()]
                if missing:
                    raise ToolError("research_xray_setup missing observed selector(s): " + ", ".join(missing))
                run.remember_selectors(search=str(selectors["search"]), submit=str(selectors["submit"]), export=str(selectors["export"]))
                run.remember_xray_selectors(**{key: str(selectors[key]) for key in ("open", "rows", "load_more", "refresh", "export", "csv")})
                return "Xray selectors saved · ready for observed Amazon-tab processing"
            if action == "research_status":
                return json.dumps(self._research_run(name).status(), ensure_ascii=False)
            if action == "research_pause":
                run = self._research_run(name)
                active = run.status()["active"]
                if not active:
                    raise ToolError("No active keyword to pause")
                run.pause(str(active[0]), value or "Research paused", self.browser_controller.current_url)
                return f"Research paused · {active[0]} · {value or 'Research paused'}"
            if action == "research_resume":
                if not value:
                    raise ToolError("research_resume requires the paused keyword in value")
                self._research_run(name).resume(value)
                return f"Research resumed · {value}"
            if action == "research_search":
                run = self._research_run(name)
                known = run.selectors()
                selector = selector or known.get("search", "")
                submit_selector = value or known.get("submit", "")
                if not selector or not submit_selector:
                    raise ToolError("research_search requires selector for the observed search field and value for the observed submit control")
                observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                reason = self._research_pause_reason(observed)
                if reason:
                    raise ToolError(f"Research checkpoint: {reason}. Complete it in the browser, then retry this action.")
                keyword = run.next_keyword()
                if not keyword:
                    return "Research run has no pending keywords"
                try:
                    self.browser_controller.fill(selector, keyword, tab="amazon")
                    self.browser_controller.click(submit_selector, tab="amazon")
                    self.browser_controller.wait(max(250, min(8_000, int(milliseconds))), tab="amazon")
                    after = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                    reason = self._research_pause_reason(after)
                    if reason:
                        run.pause(keyword, reason, self.browser_controller.current_url)
                        return f"Research paused · {keyword} · {reason}"
                    run.remember_selectors(search=selector, submit=submit_selector)
                    return f"Research search submitted · {keyword} · ready for observed Xray export"
                except Exception as exc:
                    run.pause(keyword, f"search action failed: {exc}", self.browser_controller.current_url)
                    raise
            if action == "research_export":
                run = self._research_run(name)
                selector = selector or run.selectors().get("export", "")
                if not selector:
                    raise ToolError("research_export requires the observed Xray export selector")
                active = run.status()["active"]
                if not active:
                    raise ToolError("No active keyword to export; use research_search first")
                keyword = str(active[0])
                observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                reason = self._research_pause_reason(observed)
                if reason:
                    run.pause(keyword, reason, self.browser_controller.current_url)
                    return f"Research paused · {keyword} · {reason}"
                try:
                    marker = self.browser_controller.mark_download(tab="amazon")
                    self.browser_controller.click(selector, tab="amazon")
                    source = self.browser_controller.wait_for_download(marker, timeout_seconds=max(5, min(120, int(milliseconds)),), tab="amazon")
                    target = run.complete_download(keyword, source, self.browser_controller.current_url)
                    run.remember_selectors(export=selector)
                    self.browser_controller.navigate(self.browser_controller.current_url, tab="amazon")
                    return f"Xray report recorded · {keyword} · {target.relative_to(self.root).as_posix()}"
                except Exception as exc:
                    run.pause(keyword, f"export action failed: {exc}", self.browser_controller.current_url)
                    raise
            if action == "research_run":
                run = self._research_run(name)
                known = run.selectors()
                missing = [label for label in ("search", "submit", "export") if not known.get(label)]
                if missing:
                    raise ToolError("research_run needs successful observed selectors first: " + ", ".join(missing))
                xray = run.xray_selectors()
                if xray:
                    missing_xray = [label for label in ("open", "rows", "load_more", "refresh", "export", "csv") if not xray.get(label)]
                    if missing_xray:
                        raise ToolError("research_run needs observed Xray selectors first: " + ", ".join(missing_xray))
                    return self._run_observed_xray(run, known, xray, milliseconds)
                before = len(run.status()["completed"])
                while True:
                    active = run.status()["active"]
                    if active:
                        keyword = str(active[0])
                    else:
                        observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                        reason = self._research_pause_reason(observed)
                        if reason:
                            return f"Research checkpoint · {reason} · {len(run.status()['completed']) - before} completed"
                        keyword = run.next_keyword()
                        if not keyword:
                            return f"Research run complete · {len(run.status()['completed']) - before} completed · {len(run.status()['paused'])} paused"
                        try:
                            self.browser_controller.fill(known["search"], keyword, tab="amazon")
                            self.browser_controller.click(known["submit"], tab="amazon")
                            self.browser_controller.wait(max(250, min(8_000, int(milliseconds))), tab="amazon")
                            after = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                            reason = self._research_pause_reason(after)
                            if reason:
                                run.pause(keyword, reason, self.browser_controller.current_url)
                                return f"Research paused · {keyword} · {reason} · {len(run.status()['completed']) - before} completed"
                        except Exception as exc:
                            run.pause(keyword, f"search action failed: {exc}", self.browser_controller.current_url)
                            raise
                    try:
                        observed = self.browser_controller.inspect("body", max_elements=80, tab="amazon")
                        reason = self._research_pause_reason(observed)
                        if reason:
                            run.pause(keyword, reason, self.browser_controller.current_url)
                            return f"Research paused · {keyword} · {reason} · {len(run.status()['completed']) - before} completed"
                        marker = self.browser_controller.mark_download(tab="amazon")
                        self.browser_controller.click(known["export"], tab="amazon")
                        source = self.browser_controller.wait_for_download(marker, timeout_seconds=max(5, min(120, int(milliseconds))), tab="amazon")
                        run.complete_download(keyword, source, self.browser_controller.current_url)
                        self.browser_controller.navigate(self.browser_controller.current_url, tab="amazon")
                    except Exception as exc:
                        run.pause(keyword, f"export action failed: {exc}", self.browser_controller.current_url)
                        raise
            if action == "inspect":
                return self.browser_controller.inspect(selector or "body", max_elements=max_elements, tab=tab)
            if action == "screenshot":
                return self.browser_controller.screenshot(name=name or "page", selector=selector, full_page=full_page)
            if action == "reverse_engineer":
                return self.browser_controller.reverse_engineer(selector=selector or "body", name=name or "design-map", max_elements=max_elements)
            if action == "click":
                if not selector:
                    raise ToolError("browser click requires selector")
                return self.browser_controller.click(selector, tab=tab, capture_tab=capture_tab)
            if action == "fill":
                if not selector:
                    raise ToolError("browser fill requires selector")
                return self.browser_controller.fill(selector, value, tab=tab)
            if action == "scroll":
                return self.browser_controller.scroll(amount, tab=tab)
            if action == "wait":
                return self.browser_controller.wait(milliseconds, tab=tab)
            if action == "routine_list":
                rows = self.browser_controller.routine_names()
                return "Learned browser routines: " + (", ".join(rows) if rows else "none")
            if action == "run_routine":
                if not name:
                    raise ToolError("browser run_routine requires name")
                return self.browser_controller.run_routine(name, repeat=repeat)
            if action == "close":
                return self.browser_controller.close()
        except (BrowserUnavailable, WorkspaceError, SiteAdapterError, PlaybookError) as exc:
            raise ToolError(str(exc)) from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Browser {action or 'action'} failed: {exc}") from exc
        raise ToolError("Unsupported browser action")

    def tool_load_skill(self, skill: str) -> str:
        if not self.plugin_manager:
            raise ToolError("No Codex skill manager is configured.")
        return self.plugin_manager.load_skill(skill)

    def tool_mcp(
        self,
        action: str,
        server: str = "",
        query: str = "",
        limit: int = 12,
        tool: str = "",
        arguments: Dict[str, Any] | None = None,
    ) -> str:
        if not self.mcp_manager:
            raise ToolError("No MCP bridge is configured.")
        action = str(action).lower().strip()
        if action == "list_servers":
            rows = [s for s in self.mcp_manager.discover() if s.enabled]
            if not rows:
                return "No enabled Codex MCP servers were discovered."
            return "\n".join(f"{s.name}: {s.summary}" for s in rows)
        if action == "list_tools":
            if not server:
                raise ToolError("mcp list_tools requires server")
            tools = self.mcp_manager.list_tools(server)
            if not tools:
                return f"{server}: no tools reported"
            q = str(query or "").strip().lower()
            terms = [x for x in re.split(r"[^a-z0-9_-]+", q) if x]
            if terms:
                ranked = []
                for item in tools:
                    hay = f"{item.get('name','')} {item.get('description','')}".lower()
                    score = sum(3 if t in str(item.get('name','')).lower() else 1 for t in terms if t in hay)
                    if score:
                        ranked.append((score, item))
                tools = [x for _, x in sorted(ranked, key=lambda x: (-x[0], str(x[1].get('name',''))))]
            limit = min(max(1, int(limit or 12)), 30)
            tools = tools[:limit]
            lines = [f"{server} tools" + (f" matching '{query}'" if query else "") + f" · {len(tools)} shown"]
            for item in tools:
                name = str(item.get("name") or "")
                desc = " ".join(str(item.get("description") or "").split())[:180]
                schema = item.get("inputSchema") or {}
                required = schema.get("required") if isinstance(schema, dict) else []
                props = schema.get("properties") if isinstance(schema, dict) else {}
                arg_names = list(props.keys()) if isinstance(props, dict) else []
                req = [str(x) for x in (required or [])]
                optional = [str(x) for x in arg_names if str(x) not in req]
                arg_text = ""
                if req or optional:
                    arg_text = " | args: " + ", ".join([*(f"{x}*" for x in req), *optional[:8]])
                lines.append(f"- {name}{arg_text}" + (f" — {desc}" if desc else ""))
            if not query and len(self.mcp_manager.list_tools(server)) > limit:
                lines.append("- … use list_tools with query keywords for a narrower result")
            return "\n".join(lines)
        if action == "call":
            if not server or not tool:
                raise ToolError("mcp call requires server and tool")
            try:
                return self.mcp_manager.call_tool(server, tool, arguments or {})
            except Exception as exc:
                raise ToolError(str(exc)) from exc
        raise ToolError("mcp action must be list_servers, list_tools, or call")

    def tool_git_status(self) -> str:
        return self.tool_run_command("git status --short --branch")

    def tool_git_diff(self, staged: bool = False) -> str:
        return self.tool_run_command("git diff --cached" if staged else "git diff")
