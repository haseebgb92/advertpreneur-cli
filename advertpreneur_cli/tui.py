from __future__ import annotations

import asyncio
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence

from prompt_toolkit import HTML, PromptSession, print_formatted_text
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle, clear
from prompt_toolkit.styles import Style

try:
    from rich.console import Console
    from rich.markdown import Markdown
except Exception:  # pragma: no cover
    Console = None
    Markdown = None


ORANGE = "#f3a126"
ORANGE_2 = "#d78018"
MUTED = "#8a8f98"
GREEN = "#42b883"
RED = "#ef6b73"
BLUE = "#60a5fa"
BAR_BG = "#23252a"

STYLE = Style.from_dict({
    "brand": f"bold {ORANGE}",
    "brand2": f"{ORANGE_2}",
    "title": "bold",
    "muted": MUTED,
    "ok": GREEN,
    "err": RED,
    "warn": ORANGE,
    "info": BLUE,
    "prompt": f"bold {ORANGE}",
    "joke": f"{MUTED}",
    "toolbar": f"bg:{BAR_BG} #d7d9de",
    "toolbar.model": f"bg:{BAR_BG} bold {ORANGE}",
    "toolbar.good": f"bg:{BAR_BG} {GREEN}",
    "toolbar.warn": f"bg:{BAR_BG} {ORANGE}",
    "completion-menu.completion": "bg:#23252a #d7d9de",
    "completion-menu.completion.current": f"bg:{ORANGE_2} #ffffff bold",
    "completion-menu.meta.completion": "bg:#23252a #8f949d",
    "completion-menu.meta.completion.current": f"bg:{ORANGE_2} #ffffff",
    "scrollbar.background": "bg:#23252a",
    "scrollbar.button": f"bg:{ORANGE_2}",
})


@dataclass(frozen=True)
class MenuItem:
    value: str
    label: str
    meta: str = ""


class MenuCompleter(Completer):
    def __init__(self, items: Sequence[MenuItem], match_anywhere: bool = False) -> None:
        self.items = list(items)
        self.match_anywhere = match_anywhere

    def get_completions(self, document: Document, complete_event):
        typed = document.text_before_cursor
        needle = typed.lower()
        for item in self.items:
            hay = f"{item.value} {item.label} {item.meta}".lower()
            ok = needle in hay if self.match_anywhere else item.value.lower().startswith(needle)
            if ok:
                yield Completion(item.value, start_position=-len(typed), display=item.label, display_meta=item.meta)


# Ordered roughly like Codex: active-session controls first, then workspace/extensions.
COMMANDS: List[MenuItem] = [
    MenuItem("/model", "/model", "Choose Ollama primary or Codex/AGY external specialist model"),
    MenuItem("/reasoning", "/reasoning", "Set reasoning effort"),
    MenuItem("/permissions", "/permissions", "Set tool approval policy"),
    MenuItem("/personality", "/personality", "Choose response style"),
    MenuItem("/goal", "/goal", "Set or manage the persistent task goal"),
    MenuItem("/plan", "/plan", "Toggle plan-only mode"),
    MenuItem("/status", "/status", "Session, model, context, Git and auth status"),
    MenuItem("/health", "/health", "On-demand RAM/process/session health · zero model tokens"),
    MenuItem("/contract", "/contract", "Show/refresh inferred project contract"),
    MenuItem("/taskplan", "/taskplan", "Preview local task/context plan without a model"),
    MenuItem("/verify", "/verify", "Run proportional local verification"),
    MenuItem("/package", "/package", "Build/validate/create clean project ZIP"),
    MenuItem("/update", "/update", "Check GitHub Releases and install the latest verified CLI update"),
    MenuItem("/usage", "/usage", "Tokens, context and metered usage"),
    MenuItem("/statusline", "/statusline", "Configure persistent status bar"),
    MenuItem("/title", "/title", "Configure terminal tab title"),
    MenuItem("/raw", "/raw", "Toggle raw output for easier selection/copy"),
    MenuItem("/mention", "/mention", "Pin a file or folder as explicit context"),
    MenuItem("/index", "/index", "Build/update local zero-cloud project code map"),
    MenuItem("/map", "/map", "Search the local project code map"),
    MenuItem("/undo", "/undo", "Restore files to before the last agent change"),
    MenuItem("/checkpoints", "/checkpoints", "Browse recoverable Advertpreneur checkpoints"),
    MenuItem("/agents", "/agents", "Configure senior/reviewer roles and automatic reviews"),
    MenuItem("/providers", "/providers", "Official Codex/AGY account providers and models"),
    MenuItem("/expert", "/expert", "Send a difficult task to the configured Senior Engineer"),
    MenuItem("/frameworks", "/frameworks", "Show locally detected framework intelligence packs"),
    MenuItem("/handbook", "/handbook", "Search validated local engineering experience"),
    MenuItem("/browser", "/browser", "Local browser navigation, screenshots and design maps"),
    MenuItem("/web", "/web", "Search the public web locally without an Ollama call"),
    MenuItem("/hooks", "/hooks", "Configure local lifecycle hooks"),
    MenuItem("/insights", "/insights", "Local daily/weekly harness review · zero model tokens"),
    MenuItem("/settings", "/settings", "Configure models, context, agents, permissions and interface"),
    MenuItem("/bridge", "/bridge", "Link this CLI session to one ChatGPT conversation"),
    MenuItem("/copy", "/copy", "Copy latest assistant result"),
    MenuItem("/diff", "/diff", "Show current Git diff"),
    MenuItem("/review", "/review", "Review current changes"),
    MenuItem("/compact", "/compact", "Compact long conversation context"),
    MenuItem("/new", "/new", "Start a new coding session"),
    MenuItem("/clear", "/clear", "Clear terminal and start a fresh chat"),
    MenuItem("/resume", "/resume", "Resume a saved coding session"),
    MenuItem("/sessions", "/sessions", "Browse saved sessions"),
    MenuItem("/rename", "/rename", "Rename current session"),
    MenuItem("/fork", "/fork", "Fork current session"),
    MenuItem("/archive", "/archive", "Archive current session and exit"),
    MenuItem("/unarchive", "/unarchive", "Restore an archived session"),
    MenuItem("/delete", "/delete", "Permanently delete current session and exit"),
    MenuItem("/init", "/init", "Create AGENTS.md scaffold"),
    MenuItem("/plugins", "/plugins", "Browse imported Codex plugins"),
    MenuItem("/skills", "/skills", "Browse imported Codex skills"),
    MenuItem("/mcp", "/mcp", "Browse imported Codex MCP servers"),
    MenuItem("/refresh", "/refresh", "Refresh model/plugin/MCP discovery caches"),
    MenuItem("/models", "/models", "Browse Ollama + external provider model catalogues"),
    MenuItem("/project", "/project", "Show or switch project root"),
    MenuItem("/config", "/config", "Show Advertpreneur configuration and state paths"),
    MenuItem("/debug-config", "/debug-config", "Print configuration diagnostics"),
    MenuItem("/keymap", "/keymap", "Show keyboard/composer shortcuts"),
    MenuItem("/login", "/login", "Sign in to Ollama Cloud"),
    MenuItem("/logout", "/logout", "Remove saved Ollama Cloud login"),
    MenuItem("/cloudmode", "/cloudmode", "Free-only or all cloud models"),
    MenuItem("/budget", "/budget", "Set per-task metered-value cap"),
    MenuItem("/daily", "/daily", "Set daily metered-value cap"),
    MenuItem("/doctor", "/doctor", "Check runtime/cloud/extension connectivity"),
    MenuItem("/help", "/help", "Show command reference"),
    MenuItem("/exit", "/exit", "Exit Advertpreneur CLI"),
]


class SlashCompleter(Completer):
    """Compatibility slash-only completer used by tests and integrations."""
    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " not in text:
            for item in COMMANDS:
                if item.value.startswith(text):
                    yield Completion(item.value, start_position=-len(text), display=item.label, display_meta=item.meta)
            return
        cmd, rest = text.split(" ", 1)
        inline: dict[str, Sequence[MenuItem]] = {
            "/permissions": [MenuItem(x, x, "tool approval") for x in ("ask", "safe", "full")],
            "/approval": [MenuItem(x, x, "tool approval") for x in ("ask", "safe", "full")],
            "/reasoning": [MenuItem(x, x, "reasoning effort") for x in ("off", "low", "medium", "high")],
            "/thinking": [MenuItem(x, x, "reasoning effort") for x in ("off", "low", "medium", "high")],
            "/cloudmode": [MenuItem("free", "free", "Free starter pool only"), MenuItem("all", "all", "Allow priced models")],
            "/personality": [MenuItem(x, x, "response style") for x in ("pragmatic", "friendly", "none")],
            "/raw": [MenuItem(x, x, "raw output") for x in ("on", "off")],
            "/index": [MenuItem(x, x, "local index action") for x in ("status", "on", "off", "rebuild", "enrich")],
            "/usage": [MenuItem("why", "why", "show largest next-call context contributors")],
            "/bridge": [MenuItem(x, x, "Browser Bridge action") for x in ("on", "off", "status", "code", "extension")],
            "/browser": [MenuItem(x, x, "local browser action") for x in ("status", "map", "screenshot", "visible on", "visible off", "close")],
            "/hooks": [MenuItem(x, x, "lifecycle hooks") for x in ("status", "init", "on", "off")],
            "/providers": [MenuItem(x, x, "provider harness") for x in ("status", "login codex", "login agy", "test codex", "test agy", "models codex", "models agy", "usage codex", "usage agy", "model codex", "model agy", "effort codex", "effort agy")],
            "/review": [MenuItem(x, x, "review provider") for x in ("codex", "agy", "ollama")],
            "/expert": [MenuItem(x, x, "senior provider") for x in ("codex", "agy", "ollama")],
            "/insights": [MenuItem(x, x, "local harness review") for x in ("today", "week")],
            "/contract": [MenuItem(x, x, "project contract") for x in ("status", "refresh")],
            "/health": [MenuItem(x, x, "resource action") for x in ("cleanup", "guard on", "guard off")],
            "/verify": [MenuItem(x, x, "verification depth") for x in ("safe", "full")],
        }
        for item in inline.get(cmd, []):
            if item.value.startswith(rest):
                yield Completion(item.value, start_position=-len(rest), display=item.label, display_meta=item.meta)


class WorkspaceIndex:
    """Small cached index used only for @ mention completion."""

    EXCLUDED = {".git", ".advertpreneur", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".idea"}

    def __init__(self, project_getter: Callable[[], Path]) -> None:
        self.project_getter = project_getter
        self._project: Path | None = None
        self._items: list[tuple[str, str]] = []
        self._stamp = 0.0

    def invalidate(self) -> None:
        self._project = None
        self._items = []
        self._stamp = 0.0

    def _build(self) -> None:
        root = self.project_getter().resolve()
        if self._project == root and self._items and time.monotonic() - self._stamp < 15.0:
            return
        files: list[str] = []
        # Git gives a fast, useful project view and respects ignored build/dependency trees.
        if (root / ".git").exists() and shutil.which("git"):
            try:
                proc = subprocess.run(
                    ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
                )
                if proc.returncode == 0:
                    files = [x.strip().replace("\\", "/") for x in proc.stdout.splitlines() if x.strip()][:6000]
            except Exception:
                files = []
        if not files:
            for base, dirs, names in os.walk(root):
                dirs[:] = [d for d in dirs if d not in self.EXCLUDED]
                rel_base = Path(base).relative_to(root)
                for name in names:
                    rel = (rel_base / name).as_posix()
                    files.append(rel)
                    if len(files) >= 6000:
                        break
                if len(files) >= 6000:
                    break
        dirs = set()
        for f in files:
            p = Path(f)
            for parent in p.parents:
                s = parent.as_posix()
                if s not in {".", ""}:
                    dirs.add(s + "/")
        self._items = [(f, "file") for f in sorted(set(files))] + [(d, "folder") for d in sorted(dirs)]
        self._project = root
        self._stamp = time.monotonic()

    def search(self, query: str, limit: int = 80) -> list[tuple[str, str]]:
        self._build()
        q = query.lower().strip()
        if not q:
            return self._items[:limit]
        ranked: list[tuple[int, str, str]] = []
        for path, kind in self._items:
            lp = path.lower()
            if q not in lp:
                continue
            name = Path(path.rstrip("/")).name.lower()
            score = (100 if name.startswith(q) else 0) + (40 if f"/{q}" in lp else 0) - len(path) // 8
            ranked.append((score, path, kind))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        return [(p, k) for _, p, k in ranked[:limit]]


class ComposerCompleter(Completer):
    def __init__(self, workspace: WorkspaceIndex) -> None:
        self.workspace = workspace

    @staticmethod
    def _mention_fragment(text: str) -> tuple[int, str] | None:
        # Mentions are shell-like: @src/app.py or @"folder with spaces/file.py".
        quote = text.rfind('@"')
        plain = text.rfind("@")
        start = max(quote, plain)
        if start < 0:
            return None
        # Only treat it as an active mention if it starts at whitespace/start or follows punctuation.
        if start > 0 and not text[start - 1].isspace() and text[start - 1] not in "([,{":
            return None
        frag = text[start + 1:]
        if frag.startswith('"'):
            frag = frag[1:]
            if '"' in frag:
                return None
        elif any(ch.isspace() for ch in frag):
            return None
        return start, frag

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            if " " not in text:
                for item in COMMANDS:
                    if item.value.startswith(text):
                        yield Completion(item.value, start_position=-len(text), display=item.label, display_meta=item.meta)
                return
            cmd, rest = text.split(" ", 1)
            inline: dict[str, Sequence[MenuItem]] = {
                "/permissions": [MenuItem(x, x, "tool approval") for x in ("ask", "safe", "full")],
                "/approval": [MenuItem(x, x, "tool approval") for x in ("ask", "safe", "full")],
                "/reasoning": [MenuItem(x, x, "reasoning effort") for x in ("off", "low", "medium", "high")],
                "/thinking": [MenuItem(x, x, "reasoning effort") for x in ("off", "low", "medium", "high")],
                "/cloudmode": [MenuItem("free", "free", "Free starter pool only"), MenuItem("all", "all", "Allow priced models")],
                "/personality": [MenuItem(x, x, "response style") for x in ("pragmatic", "friendly", "none")],
                "/raw": [MenuItem(x, x, "raw output") for x in ("on", "off")],
            "/index": [MenuItem(x, x, "local index action") for x in ("status", "on", "off", "rebuild", "enrich")],
            "/usage": [MenuItem("why", "why", "show largest next-call context contributors")],
            "/bridge": [MenuItem(x, x, "Browser Bridge action") for x in ("on", "off", "status", "code", "extension")],
            "/browser": [MenuItem(x, x, "local browser action") for x in ("status", "map", "screenshot", "visible on", "visible off", "close")],
            "/hooks": [MenuItem(x, x, "lifecycle hooks") for x in ("status", "init", "on", "off")],
            "/providers": [MenuItem(x, x, "provider harness") for x in ("status", "login codex", "login agy", "test codex", "test agy", "models codex", "models agy", "usage codex", "usage agy", "model codex", "model agy", "effort codex", "effort agy")],
            "/review": [MenuItem(x, x, "review provider") for x in ("codex", "agy", "ollama")],
            "/expert": [MenuItem(x, x, "senior provider") for x in ("codex", "agy", "ollama")],
            "/insights": [MenuItem(x, x, "local harness review") for x in ("today", "week")],
            "/contract": [MenuItem(x, x, "project contract") for x in ("status", "refresh")],
            "/health": [MenuItem(x, x, "resource action") for x in ("cleanup", "guard on", "guard off")],
            "/verify": [MenuItem(x, x, "verification depth") for x in ("safe", "full")],
            }
            for item in inline.get(cmd, []):
                if item.value.startswith(rest):
                    yield Completion(item.value, start_position=-len(rest), display=item.label, display_meta=item.meta)
            return

        mention = self._mention_fragment(text)
        if not mention:
            return
        start, query = mention
        replace_len = len(text) - start
        for path, kind in self.workspace.search(query):
            value = f'@"{path}"' if " " in path else f"@{path}"
            yield Completion(value, start_position=-replace_len, display=value, display_meta=kind)


TECH_JOKES = (
    "The tests passed. Please remain calm.",
    "CSS is easy until one div develops opinions.",
    "The API said 200. Emotionally, it still feels like a 500.",
    "Nothing is more permanent than a temporary workaround.",
    "Git happens. Commit accordingly.",
    "The code is compiling. Suspicion is appropriate.",
    "Undefined is not a value. It is a lifestyle choice.",
    "I refactored it. The bugs now have cleaner architecture.",
    "Works on my machine is not a deployment strategy. Apparently.",
    "The stack trace has entered the chat.",
    "Today's forecast: scattered commits with a chance of rollback.",
    "Latency builds character. We are trying to build less character.",
    "A cache miss is just your CPU asking for directions.",
    "One does not simply rename a production database.",
    "Your regex has a regex problem now.",
    "The cloud is just someone else's computer with better marketing.",
    "I found the edge case. It was living in production.",
    "The feature is simple. The requirements have other plans.",
    "The bug has been promoted to undocumented behavior.",
    "No code was harmed. Several assumptions were.",
    "The build is thinking about what it has done.",
    "If this works first try, please check the logs anyway.",
    "Dependency resolution: where optimism goes to negotiate.",
    "The compiler has opinions and, annoyingly, evidence.",
    "There are two hard problems in computer science. Naming things is three of them.",
    "I deleted the dead code. It has filed an appeal.",
    "The happy path is currently under construction.",
    "One tiny change has requested a migration plan.",
)

WORKING_STATES = (
    "Thinking",
    "Reading the room… and the repo",
    "Connecting dots",
    "Negotiating with the codebase",
    "Checking assumptions",
    "Working",
)


class TerminalUI:
    @staticmethod
    def _safe_prompt_call(fn):
        """Run prompt_toolkit safely even if this thread already owns an asyncio loop.

        Browser/automation libraries can leave a running loop visible on the CLI thread.
        prompt_toolkit's synchronous ``prompt()`` uses asyncio.run(), which would otherwise
        raise and terminate ADP. In that situation execute the prompt on a clean helper
        thread and propagate its result/exception back.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return fn()

        results: queue.Queue = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                results.put((True, fn()))
            except BaseException as exc:
                results.put((False, exc))

        thread = threading.Thread(target=worker, name="AdvertpreneurPrompt", daemon=False)
        thread.start()
        ok, value = results.get()
        thread.join()
        if ok:
            return value
        raise value

    def __init__(
        self,
        history_path: Path,
        toolbar: Callable[[], FormattedText],
        project_getter: Callable[[], Path] | None = None,
        copy_callback: Callable[[], None] | None = None,
    ) -> None:
        self.toolbar = toolbar
        self.project_getter = project_getter or (lambda: Path.cwd())
        self.copy_callback = copy_callback
        self.raw_output = False
        self.kb = KeyBindings()
        self.workspace = WorkspaceIndex(self.project_getter)
        self.completer = ComposerCompleter(self.workspace)
        self._live_lock = threading.RLock()
        self._live_stop = threading.Event()
        self._live_thread: threading.Thread | None = None
        self._live_active = False
        self._live_drawn = False
        self._prompt_active = False
        self._live_changes: list[tuple[str, int, int]] = []
        self._live_changes_expanded = False
        self._live_started = 0.0
        self._live_label = "Thinking"
        self._live_model = ""
        self._live_turn = 1
        self._joke = random.choice(TECH_JOKES)
        self._joke_changed = time.monotonic()
        self._state_changed = 0.0
        self._cockpit: dict[str, str] = {}
        # Set True by the CLI while a task thread is live so the Escape binding
        # knows whether an empty-buffer stop signal is meaningful.
        self._task_active = False

        @self.kb.add("c-l")
        def _(event) -> None:
            clear()

        @self.kb.add("c-o")
        def _(event) -> None:
            if self.copy_callback:
                self.copy_callback()

        # Ctrl+C must never terminate Advertpreneur. At the composer it only
        # cancels completion/current draft; active work is protected separately
        # by the CLI task loop. This intentionally is not advertised as an exit key.
        @self.kb.add("c-c", eager=True)
        def _(event) -> None:
            buf = event.current_buffer
            if buf.complete_state:
                buf.cancel_completion()
            if buf.text:
                buf.reset()
            event.app.invalidate()

        @self.kb.add("/")
        def _(event) -> None:
            buf = event.current_buffer
            if not buf.text:
                buf.insert_text("/")
                # Open the palette without pre-filling /model. The composer stays at
                # a plain '/' so users can type directly or navigate with arrow keys.
                buf.start_completion(select_first=False)
            else:
                buf.insert_text("/")

        @self.kb.add("@")
        def _(event) -> None:
            buf = event.current_buffer
            buf.insert_text("@")
            buf.start_completion(select_first=True)

        @self.kb.add("escape", eager=True)
        def _(event) -> None:
            buf = event.current_buffer
            if buf.complete_state:
                buf.cancel_completion()
                if buf.text.strip() in {"/", "@"}:
                    buf.text = ""
                    buf.cursor_position = 0
                return
            if buf.text.strip():
                # The main loop treats this prefix as a priority follow-up while
                # a task is active. It is intentionally never shown to the user.
                event.app.exit(result="\x00ADP_IMMEDIATE\x00" + buf.text)
                return
            # Empty buffer: only emit a stop signal when a task is actually running.
            # Without this guard the signal fires at idle too, causing the event
            # loop to dispatch a null-byte instruction and terminate the session.
            if self._task_active:
                event.app.exit(result="\x00ADP_IMMEDIATE\x00")
            # If no task is running, Escape on an empty buffer does nothing
            # (same behaviour as before — clears any completion menu already
            # handled above, otherwise a no-op so the composer stays open).


        @self.kb.add("enter")
        def _(event) -> None:
            buf = event.current_buffer
            state = buf.complete_state
            if state and state.current_completion:
                buf.apply_completion(state.current_completion)
                if buf.text.strip().split(maxsplit=1)[0] in {c.value for c in COMMANDS}:
                    buf.validate_and_handle()
                return
            buf.validate_and_handle()

        self.session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=self.completer,
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            style=STYLE,
            key_bindings=self.kb,
            bottom_toolbar=self._composer_toolbar,
            enable_history_search=True,
            # mouse_support disabled: prompt_toolkit's mouse capture intercepts the
            # scroll wheel and right-click, blocking native terminal scroll, text
            # selection, and clipboard copy.  Disable it so users can scroll output
            # and copy text normally.  The live-change footer toggle is rendered
            # outside PT's event loop so it is unaffected.
            mouse_support=False,
        )
        try:
            self.session.app.ttimeoutlen = 0.03
        except Exception:
            pass
        self.console = Console(highlight=False, soft_wrap=False) if Console else None
        self._joke_thread = threading.Thread(target=self._idle_joke_worker, name="advertpreneur-idle-jokes", daemon=True)
        self._joke_thread.start()

    def set_copy_callback(self, callback: Callable[[], None]) -> None:
        self.copy_callback = callback

    def invalidate_workspace(self) -> None:
        self.workspace.invalidate()

    def _idle_joke_worker(self) -> None:
        while True:
            time.sleep(0.5)
            if self._live_active:
                continue
            now = time.monotonic()
            if now - self._joke_changed < 7.0:
                continue
            choices = [j for j in TECH_JOKES if j != self._joke]
            self._joke = random.choice(choices or TECH_JOKES)
            self._joke_changed = now
            try:
                self.session.app.invalidate()
            except Exception:
                pass

    def _composer_toolbar(self) -> FormattedText:
        parts: list[tuple[str, str]] = []
        parts.extend(self._cockpit_parts())
        if self._live_active:
            parts.extend(self._live_status_parts())
            parts.extend(self._live_change_parts())
        parts.append(("class:joke", f"  {self._joke}\n"))
        parts.extend(list(self.toolbar()))
        return FormattedText(parts)

    def set_cockpit(self, state: dict[str, object]) -> None:
        """Project a bounded durable mission summary into the fixed composer UI."""
        self._cockpit = {str(key): str(value) for key, value in dict(state or {}).items() if value not in (None, "")}
        self._invalidate_live()

    def clear_cockpit(self) -> None:
        self._cockpit = {}
        self._invalidate_live()

    def _cockpit_parts(self) -> list[tuple[str, str]]:
        state = getattr(self, "_cockpit", {}) or {}
        if not state:
            return []
        mission = state.get("mission", "Mission")[:96]
        step = state.get("step", "")[:96]
        status = state.get("state", "active").lower()
        activity = "waiting for input" if status == "waiting" else status
        attention = state.get("attention", "")
        evidence = state.get("evidence", "")
        line = f"  Mission · {mission}"
        if step:
            line += f" · {step}"
        line += f" · {activity}"
        if evidence:
            line += f" · Evidence {evidence}"
        if attention:
            line += f" · Attention: {attention}"
        return [("class:working", line + "\n")]

    def _live_status_parts(self) -> list[tuple[str, str]]:
        """Render changing work state inside Prompt Toolkit's owned footer."""
        elapsed = max(0.0, time.monotonic() - self._live_started)
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        frame = frames[int(elapsed * 10) % len(frames)]
        activity = {
            "action": "acting",
            "coding": "working",
            "preparing task": "preparing the task",
            "starting": "starting",
            "thinking": "thinking",
            "needs attention": "waiting for input",
        }.get(str(self._live_label or "").lower(), str(self._live_label or "working").lower())
        detail = f" · {self._live_detail}" if getattr(self, "_live_detail", "") else ""
        return [("class:working", f"  {frame} Advertpreneur is {activity}{detail} · {elapsed:.1f}s\n")]

    def set_live_changes(self, changes: Sequence[tuple[str, int, int]]) -> None:
        normalized: list[tuple[str, int, int]] = []
        for path, added, removed in changes[:12]:
            normalized.append((str(path).replace("\\", "/"), max(0, int(added)), max(0, int(removed))))
        with self._live_lock:
            self._live_changes = normalized
        self._invalidate_live()

    def _live_change_parts(self) -> list[tuple[str, str]]:
        changes = list(getattr(self, "_live_changes", []) or [])
        count = len(changes)
        added = sum(row[1] for row in changes)
        removed = sum(row[2] for row in changes)
        names = " · ".join(row[0] for row in changes[:3]) or "No file changes observed yet"
        action = "click to collapse" if getattr(self, "_live_changes_expanded", False) else "click to expand"
        parts = [("class:working", f"  {count} files · +{added} -{removed} · {names}  [{action}]\n", self._toggle_live_changes)]
        if getattr(self, "_live_changes_expanded", False):
            parts.extend(("class:muted", f"    {path}  +{file_added} -{file_removed}\n") for path, file_added, file_removed in changes[:8])
        return parts

    def _toggle_live_changes(self, mouse_event) -> None:
        if mouse_event.event_type != MouseEventType.MOUSE_UP:
            return
        with self._live_lock:
            if not self._live_changes:
                return
            self._live_changes_expanded = not self._live_changes_expanded
        self._invalidate_live()

    def _invalidate_live(self) -> None:
        try:
            self.session.app.invalidate()
        except Exception:
            pass

    def interrupt_prompt(self, result: str) -> bool:
        """Return the active composer to the CLI loop without touching it cross-thread.

        Task workers use this only to request a main-thread interaction, such as a
        destructive-operation approval.  Prompt Toolkit owns the terminal loop, so
        the worker schedules ``exit`` on that loop instead of calling ``prompt``.
        """
        if not getattr(self, "_prompt_active", False):
            return False
        try:
            app = self.session.app
            loop = app.loop
            if loop is None:
                return False
            loop.call_soon_threadsafe(lambda: app.exit(result=result))
            return True
        except Exception:
            return False

    def _live_refresh_worker(self) -> None:
        """Request a prompt-toolkit repaint at 5 FPS without touching stdout."""
        while not self._live_stop.wait(0.2):
            if not self._live_active:
                return
            self._invalidate_live()

    def set_terminal_title(self, project: Path, suffix: str = "") -> None:
        name = project.name or str(project)
        title = f"Advertpreneur CLI - {name}" + (f" - {suffix}" if suffix else "")
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW(title)
            except Exception:
                pass
        try:
            sys.stdout.write(f"\x1b]0;{title}\x07")
            sys.stdout.flush()
        except Exception:
            pass

    @staticmethod
    def _plain_toolbar(parts: FormattedText) -> str:
        return "".join(text for _style, text in parts).strip()

    @staticmethod
    def _fit(text: str, width: int) -> str:
        if width <= 4:
            return text[:width]
        if len(text) > width:
            return text[: width - 1] + "…"
        return text + " " * (width - len(text))

    def _erase_live_locked(self) -> None:
        if not self._live_drawn:
            return
        lines = max(1, int(getattr(self, "_live_lines_drawn", 3) or 3))
        sys.stdout.write(f"\x1b[{lines}A")
        for i in range(lines):
            sys.stdout.write("\r\x1b[2K")
            if i < lines - 1:
                sys.stdout.write("\n")
        if lines > 1:
            sys.stdout.write(f"\x1b[{lines - 1}A\r")
        else:
            sys.stdout.write("\r")
        self._live_drawn = False

    def _render_live_locked(self) -> None:
        if not self._live_active:
            return
        if self._live_drawn:
            self._erase_live_locked()
        width = max(30, shutil.get_terminal_size((120, 30)).columns)
        elapsed = max(0.0, time.monotonic() - self._live_started)
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        frame = frames[int(elapsed * 10) % len(frames)]
        bar = self._plain_toolbar(self.toolbar())
        composer = "  ╭─ Message Advertpreneur ─────────────────────────────────────────────╮"
        activity = {
            "action": "acting",
            "coding": "working",
            "preparing task": "preparing the task",
            "starting": "starting",
            "thinking": "thinking",
            "needs attention": "waiting for input",
        }.get(str(getattr(self, "_live_label", "") or "").lower(), str(getattr(self, "_live_label", "working") or "working").lower())
        if getattr(self, "_live_compact", False):
            file_part = f" · {self._live_detail}" if getattr(self, "_live_detail", "") else ""
            working = f" {frame} coding{file_part} · {elapsed:.1f}s"
            sys.stdout.write("\r\x1b[2K" + self._fit(working, width) + "\n")
            sys.stdout.write("\r\x1b[2K\x1b[38;2;215;217;222m" + self._fit(composer, width) + "\x1b[0m\n")
            sys.stdout.write("\r\x1b[2K\x1b[38;2;138;143;152m" + self._fit(f"  {self._joke}", width) + "\x1b[0m\n")
            sys.stdout.write("\r\x1b[2K\x1b[48;2;35;37;42m\x1b[38;2;215;217;222m" + self._fit(bar, width) + "\x1b[0m\n")
            self._live_lines_drawn = 4
        else:
            detail = f" · {self._live_detail}" if getattr(self, "_live_detail", "") else ""
            working = f" {frame} Advertpreneur is {activity}{detail} · {elapsed:.1f}s"
            second = f"  {self._joke}"
            sys.stdout.write("\r\x1b[2K" + self._fit(working, width) + "\n")
            sys.stdout.write("\r\x1b[2K\x1b[38;2;215;217;222m" + self._fit(composer, width) + "\x1b[0m\n")
            sys.stdout.write("\r\x1b[2K\x1b[38;2;138;143;152m" + self._fit(second, width) + "\x1b[0m\n")
            sys.stdout.write("\r\x1b[2K\x1b[48;2;35;37;42m\x1b[38;2;215;217;222m" + self._fit(bar, width) + "\x1b[0m\n")
            self._live_lines_drawn = 4
        sys.stdout.flush()
        self._live_drawn = True

    def begin_working(self, model: str, label: str = "Thinking", turn: int = 1, compact: bool = False) -> None:
        self.end_working()
        self._live_stop = threading.Event()
        self._live_active = True
        self._live_started = time.monotonic()
        self._live_label = label
        self._live_model = model
        self._live_turn = turn
        self._live_detail = ""
        self._live_changes = []
        self._live_changes_expanded = False
        self._live_event_driven = False
        self._live_compact = bool(compact)
        self._live_lines_drawn = 4
        self._state_changed = self._live_started

        # Prompt Toolkit owns the active composer. Its renderer is invalidated at
        # 5 FPS so the spinner/elapsed time progress without ANSI cursor races.
        self._live_thread = threading.Thread(target=self._live_refresh_worker, name="advertpreneur-live-ui", daemon=True)
        self._live_thread.start()
        with self._live_lock:
            if self._prompt_active:
                self._invalidate_live()
            else:
                self._render_live_locked()

    def work_event(self, label: str, detail: str = "") -> None:
        """Persist one real tool/activity event in terminal scrollback.

        The animated footer is intentionally ephemeral; fast AGY tool calls can
        otherwise complete between refreshes and look like nothing happened.
        Keep only truthful structured-provider events here, never reasoning text.
        """
        text = f"  ↳ {label}"
        if detail:
            text += f" · {str(detail)[:180]}"
        self._print_raw(text)

    def set_working_state(self, label: str, model: str | None = None, turn: int | None = None, detail: str | None = None, event_driven: bool | None = None) -> None:
        with self._live_lock:
            if not self._live_active:
                self.begin_working(model or self._live_model or "model", label, turn or self._live_turn)
            self._live_label = label
            self._state_changed = time.monotonic()
            if model is not None:
                self._live_model = model
            if turn is not None:
                self._live_turn = turn
            if detail is not None:
                self._live_detail = str(detail)[:220]
            if event_driven is not None:
                self._live_event_driven = bool(event_driven)
            if self._prompt_active:
                self._invalidate_live()
            else:
                self._render_live_locked()

    def end_working(self) -> None:
        if not self._live_active:
            return
        self._live_stop.set()
        thread = self._live_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.4)
        with self._live_lock:
            self._erase_live_locked()
            self._live_active = False
            self._live_drawn = False
            self._live_changes = []
            self._live_changes_expanded = False
        self._live_thread = None
        self._invalidate_live()

    def start_activity(self, label: str, states: Sequence[str] | None = None) -> None:
        state = (states or ("Thinking",))[0]
        if self._live_active:
            self.set_working_state(state)
        else:
            self.begin_working(label, state, 1)

    def stop_activity(self, final: str | None = None) -> None:
        if final:
            self.print_line(f"  \x1b[38;2;66;184;131m✓\x1b[0m {final}")

    def result_card(
        self, outcome: str, summary: str, *, files: Sequence[str] = (), actions: int = 0,
        verification: str = "", boundary: str = "",
    ) -> None:
        """Print a durable, product-owned task result above the fixed composer rows."""
        status = {
            "completed": "Completed",
            "failed": "Needs attention",
            "interrupted": "Interrupted",
            "login_needed": "Login needed",
            "approval_needed": "Approval needed",
        }.get(str(outcome or "").lower(), "Result")
        icon = "✓" if str(outcome).lower() == "completed" else "!"
        lines = [f"  {icon} Advertpreneur result · {status}", f"    {str(summary or '').strip() or 'No result details were returned.'}"]
        if files:
            lines.append("    Files · " + ", ".join(str(item) for item in files[:8]))
        if actions:
            lines.append(f"    Actions · {int(actions)}")
        if verification:
            lines.append("    Verification · " + str(verification).strip())
        if boundary:
            lines.append("    Next · " + str(boundary).strip())
        self._print_raw("\n".join(lines))

    def _print_raw(self, text: str = "") -> None:
        with self._live_lock:
            was_live = self._live_active
            # patch_stdout keeps Prompt Toolkit's composer in place.  ANSI
            # cursor movement here would fight its renderer and flicker.
            if was_live and not self._prompt_active:
                self._erase_live_locked()
            sys.stdout.write(text + ("" if text.endswith("\n") else "\n"))
            sys.stdout.flush()
            if was_live and not self._prompt_active:
                self._render_live_locked()
            elif was_live:
                self._invalidate_live()

    def print_line(self, text: str = "") -> None:
        self._print_raw(text)

    def banner(self, version: str, project: Path) -> None:
        self.set_terminal_title(project)
        print()
        ansi_path = Path(__file__).resolve().parent / "assets" / "advertpreneur-logo.ansi.txt"
        try:
            logo_lines = ansi_path.read_text(encoding="utf-8").splitlines() if ansi_path.exists() else []
        except OSError:
            logo_lines = []

        info = [
            f"\x1b[1;38;2;243;161;38mADVERTPRENEUR CLI\x1b[0m  \x1b[38;2;138;143;152mv{version}\x1b[0m",
            "",
            f"\x1b[38;2;138;143;152mProject\x1b[0m  {project}",
            "",
            "\x1b[38;2;138;143;152mType\x1b[0m \x1b[1;38;2;243;161;38m/\x1b[0m \x1b[38;2;138;143;152mcommands · \x1b[1;38;2;243;161;38m@\x1b[0m \x1b[38;2;138;143;152mfiles/folders · ! shell · ↑↓ history · Ctrl+R search · Ctrl+O copy\x1b[0m",
        ]
        if logo_lines:
            pad_top = max(0, (len(logo_lines) - len(info)) // 2)
            for i, logo in enumerate(logo_lines):
                text = info[i - pad_top] if pad_top <= i < pad_top + len(info) else ""
                print(f"  {logo}  {text}")
        else:
            print_formatted_text(HTML(f"<brand>ADVERTPRENEUR CLI</brand>  <muted>v{version}</muted>"), style=STYLE)
            print_formatted_text(HTML(f"<muted>Project</muted>  {escape_html(str(project))}"), style=STYLE)
            print_formatted_text(HTML("<muted>Type</muted> <brand>/</brand> <muted>commands ·</muted> <brand>@</brand> <muted>files/folders · ! shell · Ctrl+O copy</muted>"), style=STYLE)
        print()

    def prompt(self) -> str:
        def composer() -> str:
            with self._live_lock:
                self._prompt_active = True
                # The old non-interactive fallback card is safe to remove before
                # Prompt Toolkit takes ownership of these rows.
                self._erase_live_locked()
            self._invalidate_live()
            try:
                with patch_stdout(raw=True):
                    return self.session.prompt(HTML("<muted>╭─ Message Advertpreneur ─</muted>\n<prompt>› </prompt>"))
            finally:
                with self._live_lock:
                    self._prompt_active = False

        return self._safe_prompt_call(composer)

    def _menu_toolbar(self) -> FormattedText:
        parts: list[tuple[str, str]] = [("class:joke", f"  {self._joke}\n")]
        parts.extend(list(self.toolbar()))
        parts.append(("class:toolbar", " │ ↑↓ Enter · Esc back "))
        return FormattedText(parts)

    def choose(self, title: str, items: Sequence[MenuItem], fuzzy: bool = False) -> str | None:
        if not items:
            return None
        live_snapshot = None
        if self._live_active:
            live_snapshot = (self._live_model, self._live_label, self._live_turn)
            self.end_working()

        kb = KeyBindings()
        cancelled = {"value": False}

        @kb.add("escape", eager=True)
        def _(event) -> None:
            cancelled["value"] = True
            event.app.exit(result="")

        @kb.add("enter")
        def _(event) -> None:
            buf = event.current_buffer
            state = buf.complete_state
            if state and state.current_completion:
                buf.apply_completion(state.current_completion)
            event.app.exit(result=buf.text)

        session = PromptSession(
            completer=MenuCompleter(items, match_anywhere=fuzzy),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            style=STYLE,
            key_bindings=kb,
            bottom_toolbar=self._menu_toolbar,
            mouse_support=False,
        )
        try:
            session.app.ttimeoutlen = 0.02
        except Exception:
            pass

        def start() -> None:
            session.default_buffer.start_completion(select_first=True)

        try:
            value = self._safe_prompt_call(lambda: session.prompt(HTML(f"<brand>{escape_html(title)}</brand>  <prompt>› </prompt>"), pre_run=start)).strip()
        except (EOFError, KeyboardInterrupt):
            value = ""
            cancelled["value"] = True
        finally:
            if live_snapshot:
                model, label, turn = live_snapshot
                self.begin_working(model, label, turn)

        if cancelled["value"] or not value:
            return None
        for item in items:
            if value == item.value or value == item.label:
                return item.value
        return value

    def prompt_text(self, title: str, default: str = "") -> str | None:
        try:
            session = PromptSession(style=STYLE, bottom_toolbar=self._menu_toolbar)
            session.app.ttimeoutlen = 0.02
            return self._safe_prompt_call(lambda: session.prompt(HTML(f"<brand>{escape_html(title)}</brand>  <prompt>› </prompt>"), default=default)).strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def prompt_secret(self, title: str) -> str | None:
        try:
            session = PromptSession(style=STYLE, bottom_toolbar=self._menu_toolbar)
            session.app.ttimeoutlen = 0.02
            return self._safe_prompt_call(lambda: session.prompt(HTML(f"<brand>{escape_html(title)}</brand>  <prompt>› </prompt>"), is_password=True)).strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def confirm(self, kind: str, detail: str) -> bool:
        self.heading(f"Approval required [{kind.upper()}]")
        self.print_line(f"  {detail}")
        choice = self.choose("Allow?", [MenuItem("yes", "Yes", "approve"), MenuItem("no", "No", "deny")])
        return choice == "yes"

    def heading(self, text: str) -> None:
        self.print_line(f"\n\x1b[1m{text}\x1b[0m")

    def info(self, text: str) -> None:
        self.print_line(f"\x1b[38;2;96;165;250m•\x1b[0m {text}")

    def success(self, text: str) -> None:
        self.print_line(f"\x1b[38;2;66;184;131m✓\x1b[0m {text}")

    def error(self, text: str) -> None:
        self.print_line(f"\x1b[38;2;239;107;115m✕\x1b[0m {text}")

    def muted(self, text: str) -> None:
        self.print_line(f"\x1b[38;2;138;143;152m{text}\x1b[0m")

    def result(self, text: str) -> None:
        self.end_working()
        self.heading("Result")
        if not self.raw_output and self.console and Markdown:
            try:
                self.console.print(Markdown(text.rstrip()))
                return
            except Exception:
                pass
        self.print_line(text.rstrip())


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
