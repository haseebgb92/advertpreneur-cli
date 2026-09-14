from __future__ import annotations

import importlib
import importlib.metadata
import json
import queue
import threading
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, List


@dataclass
class ProviderStatus:
    provider: str
    installed: bool
    command: str = ""
    version: str = ""
    auth: str = "unknown"
    detail: str = ""


@dataclass
class ProviderModel:
    provider: str
    id: str
    label: str = ""
    detail: str = ""
    reasoning_levels: List[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return self.label or self.id


@dataclass
class ProviderRun:
    provider: str
    model: str
    text: str
    returncode: int
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    conversation_id: str = ""
    status: str = ""
    stderr: str = ""
    command: List[str] = field(default_factory=list)
    raw: Any = None
    reasoning_effort: str = ""
    tool_calls: int = 0
    activity_events: int = 0
    quota_before: str = ""
    quota_after: str = ""
    quota_consumed: Dict[str, float] = field(default_factory=dict)
    provider_turns: int = 1
    session_reused: bool = False
    # Provider token semantics differ. Codex reports cachedInputTokens as a subset
    # of inputTokens. AGY reports input_tokens (new/non-cache input) and
    # cache_read_tokens as separate counters, so cache reads are additive when
    # estimating total context processed for the turn.
    cache_read_additive: bool = False

    @property
    def context_input_tokens(self) -> int:
        if self.cache_read_additive:
            return max(0, int(self.input_tokens)) + max(0, int(self.cache_read_tokens))
        return max(0, int(self.input_tokens))

    @property
    def uncached_input_tokens(self) -> int:
        if self.cache_read_additive:
            return max(0, int(self.input_tokens))
        return max(0, int(self.input_tokens) - int(self.cache_read_tokens))

    @property
    def cache_percent(self) -> float:
        total = self.context_input_tokens
        return (100.0 * max(0, int(self.cache_read_tokens)) / total) if total > 0 else 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.status.upper() not in {"ERROR", "FAILED", "CANCELED", "INTERRUPTED", "INVALID"}


@dataclass
class ProviderQuotaWindow:
    label: str
    remaining_percent: float
    used_percent: float = 0.0
    resets_at: int = 0
    window_minutes: int = 0
    state: str = "reported"


@dataclass
class ProviderQuota:
    provider: str
    model: str = ""
    source: str = ""
    fetched_at: float = 0.0
    windows: List[ProviderQuotaWindow] = field(default_factory=list)
    detail: str = ""
    raw: Any = None

    @property
    def effective_remaining(self) -> float | None:
        values = [w.remaining_percent for w in self.windows if w.state == "reported"]
        return min(values) if values else None

    @property
    def exhausted(self) -> bool:
        value = self.effective_remaining
        return value is not None and value <= 0


@dataclass
class ProviderActivity:
    kind: str
    label: str
    detail: str = ""
    tool: str = ""
    signature: str = ""
    timestamp: float = field(default_factory=time.time)


class ProviderHarnessError(RuntimeError):
    pass


class _CodexAppServerClient:
    """Tiny reusable JSON-RPC client for Codex metadata.

    This never starts a model turn. It is intentionally limited to metadata methods
    such as ``model/list`` and ``account/rateLimits/read`` plus sparse quota
    notifications. Credentials remain entirely inside the official Codex runtime.
    """

    def __init__(self, command: str, cwd: Path, on_notification: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.on_notification = on_notification
        self.proc: subprocess.Popen[str] | None = None
        self._responses: Dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._next_id = 1
        self._reader: threading.Thread | None = None
        self._subscribers: List[queue.Queue[dict[str, Any]]] = []

    def start(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            cmd = ExternalProviderHarness._normalize_command_argv([self.command, "app-server", "--listen", "stdio://"])
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=ExternalProviderHarness._creationflags(),
            )
            self._reader = threading.Thread(target=self._read_loop, name="adp-codex-app-server", daemon=True)
            self._reader.start()
        # Do not hold _lock while waiting: the reader needs it to route responses.
        self.request("initialize", {
            "clientInfo": {"name": "advertpreneur_cli", "title": "Advertpreneur CLI", "version": "0.15.1"},
            "capabilities": {"experimentalApi": True},
        }, timeout=8)
        self.notify("initialized", {})

    def _read_loop(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                with self._lock:
                    waiter = self._responses.get(int(row["id"]))
                if waiter:
                    waiter.put(row)
                    continue
            if isinstance(row, dict):
                with self._lock:
                    subscribers = list(self._subscribers)
                for subscriber in subscribers:
                    try: subscriber.put_nowait(row)
                    except Exception: pass
                if self.on_notification:
                    try:
                        self.on_notification(row)
                    except Exception:
                        pass


    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            try: self._subscribers.remove(q)
            except ValueError: pass

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.start_if_needed_for_notify()
        proc = self.proc
        if not proc or not proc.stdin:
            raise ProviderHarnessError("Codex app-server stdin is unavailable")
        proc.stdin.write(json.dumps({"method": method, "params": params or {}}, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def start_if_needed_for_notify(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            # Only ``start`` itself calls notify before the client is fully initialized.
            if self._reader is None:
                return
            self.start()

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        if method != "initialize":
            self.start()
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._responses[rid] = waiter
            proc = self.proc
            if not proc or not proc.stdin:
                self._responses.pop(rid, None)
                raise ProviderHarnessError("Codex app-server is unavailable")
            proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params or {}}, separators=(",", ":")) + "\n")
            proc.stdin.flush()
        try:
            row = waiter.get(timeout=max(0.5, float(timeout)))
        except queue.Empty as exc:
            raise ProviderHarnessError(f"Codex app-server {method} timed out") from exc
        finally:
            with self._lock:
                self._responses.pop(rid, None)
        if isinstance(row.get("error"), dict):
            raise ProviderHarnessError(str(row["error"].get("message") or row["error"]))
        result = row.get("result")
        return dict(result) if isinstance(result, dict) else {}

    def close(self) -> None:
        with self._lock:
            proc, self.proc = self.proc, None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class _AgyStreamDriver:
    """Persistent AGY stream-json session.

    AGY 1.1.19+ can keep one headless conversation process open and accept one
    JSON user event per turn. This avoids re-paying startup/context bootstrap on
    every ADP message while preserving AGY's own conversation/cache semantics.
    """

    def __init__(
        self,
        command: str,
        cwd: Path,
        model: str,
        effort: str,
        write: bool,
        conversation_id: str = "",
        timeout: int = 900,
    ) -> None:
        self.command = command
        self.cwd = cwd.resolve()
        self.model = model
        self.effort = effort
        self.write = bool(write)
        self.resume_id = conversation_id.strip()
        self.timeout = max(60, int(timeout))
        self.proc: subprocess.Popen[str] | None = None
        self.out_q: queue.Queue[str | None] = queue.Queue()
        self.err_lines: List[str] = []
        self.conversation_id = self.resume_id
        self._prev_usage: Dict[str, int] = {}
        self._prev_turns = 0
        self.last_used_at = time.monotonic()
        self._lock = threading.Lock()

    @staticmethod
    def _model_defined_effort(model: str) -> str:
        m = re.search(r"-(low|medium|high)$", str(model or "").strip().lower())
        return m.group(1) if m else ""

    @staticmethod
    def _uses_effort_flag(model: str) -> bool:
        low = str(model or "").strip().lower()
        return not (
            _AgyStreamDriver._model_defined_effort(low)
            or "claude" in low
            or any(marker in low for marker in ("gpt-oss", "gpt_oss", "gptoss"))
        )

    @property
    def effective_effort(self) -> str:
        if not self._uses_effort_flag(self.model):
            return self._model_defined_effort(self.model)
        return self.effort

    def compatible(self, cwd: Path, model: str, effort: str, write: bool) -> bool:
        incoming = self._model_defined_effort(model) if not self._uses_effort_flag(model) else effort
        return self.cwd == cwd.resolve() and self.model == model and self.effective_effort == incoming and self.write == bool(write)

    def _argv(self) -> List[str]:
        argv = [
            self.command,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--print-timeout", f"{max(1, self.timeout // 60)}m",
            "--sandbox",
        ]
        if self.write:
            argv += ["--mode=accept-edits"]
        if self.model:
            argv += ["--model", self.model]
        if self._uses_effort_flag(self.model):
            argv += ["--effort", self.effort]
        if self.resume_id:
            argv += ["--conversation", self.resume_id]
        return ExternalProviderHarness._normalize_command_argv(argv)

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.out_q = queue.Queue()
        self.err_lines = []
        self.proc = subprocess.Popen(
            self._argv(), cwd=str(self.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=ExternalProviderHarness._creationflags(),
        )
        proc = self.proc
        def read_out() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.out_q.put(line)
            finally:
                self.out_q.put(None)
        def read_err() -> None:
            try:
                assert proc.stderr is not None
                for line in proc.stderr:
                    self.err_lines.append(line)
                    if len(self.err_lines) > 500:
                        del self.err_lines[:-500]
            except Exception:
                pass
        threading.Thread(target=read_out, name="adp-agy-session-out", daemon=True).start()
        threading.Thread(target=read_err, name="adp-agy-session-err", daemon=True).start()

    @staticmethod
    def _usage(row: Dict[str, Any]) -> Dict[str, int]:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        out: Dict[str, int] = {}
        for key in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"):
            try: out[key] = int(usage.get(key) or 0)
            except Exception: out[key] = 0
        return out

    def ask(
        self,
        prompt: str,
        on_event: Callable[[ProviderActivity], None] | None,
        mapper: Callable[[Dict[str, Any]], ProviderActivity | None],
        timeout: int,
    ) -> ProviderRun:
        with self._lock:
            self.last_used_at = time.monotonic()
            self.start()
            proc = self.proc
            if not proc or not proc.stdin:
                raise ProviderHarnessError("AGY streaming session stdin is unavailable")
            started = time.monotonic()
            message = {"event": "user", "message": {"content": prompt}}
            proc.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            proc.stdin.flush()
            deadline = started + max(1, int(timeout))
            raw_lines: List[str] = []
            tool_calls = 0
            activity_events = 0
            signatures: Dict[str, int] = {}
            seen_tool_steps: set[tuple[str, str]] = set()
            looped = False
            final: Dict[str, Any] = {}
            turn_usage: Dict[str, int] = {}
            _heartbeat_at = started
            _HEARTBEAT_INTERVAL = 8.0  # emit a "still thinking" event every N seconds
            while time.monotonic() < deadline:
                try:
                    line = self.out_q.get(timeout=min(0.4, max(0.05, deadline - time.monotonic())))
                except queue.Empty:
                    if proc.poll() is not None or self.proc is None:
                        break
                    # Emit a lightweight heartbeat while AGY is in a silent reasoning
                    # phase (no stream events).  This keeps the TUI spinner alive and
                    # shows elapsed time so the session doesn't appear frozen.
                    now = time.monotonic()
                    if on_event and now - _heartbeat_at >= _HEARTBEAT_INTERVAL:
                        elapsed_s = int(now - started)
                        detail = (
                            f"provider reasoning · {elapsed_s}s elapsed"
                            if not tool_calls
                            else f"provider reasoning · {elapsed_s}s · {tool_calls} tool call(s) done"
                        )
                        try:
                            on_event(ProviderActivity("thinking", "Thinking", detail))
                        except Exception:
                            pass
                        _heartbeat_at = now
                    continue
                if line is None:
                    break
                raw_lines.append(line)
                if len(raw_lines) > 4000:
                    raw_lines = raw_lines[-4000:]
                try: row = json.loads(line)
                except Exception: continue
                if not isinstance(row, dict): continue
                if str(row.get("event") or row.get("type") or "").lower() == "init":
                    cid = str(row.get("conversation_id") or (row.get("init") or {}).get("conversation_id") or "")
                    if cid: self.conversation_id = cid
                activity = mapper(row)
                if activity:
                    emit_activity = True
                    activity_events += 1
                    if activity.kind == "tool" and activity.signature:
                        step = row.get("step_update") if isinstance(row.get("step_update"), dict) else {}
                        step_key = (str(step.get("step_index") or ""), str(activity.tool or activity.signature))
                        if step_key in seen_tool_steps:
                            # AGY can emit ACTIVE then DONE for one tool step. The
                            # spinner/timeline should show and count the invocation once.
                            emit_activity = False
                        else:
                            seen_tool_steps.add(step_key)
                            tool_calls += 1
                            # Strip the step index from the loop signature so repeated
                            # identical operations across different steps are still caught.
                            loop_sig = activity.signature.split(":step=", 1)[0]
                            signatures[loop_sig] = signatures.get(loop_sig, 0) + 1
                            if signatures[loop_sig] >= 12 or tool_calls >= 120:
                                looped = True
                                if on_event:
                                    try: on_event(ProviderActivity("guard", "Tool loop stopped", f"{tool_calls} tool calls · repeated operation detected"))
                                    except Exception: pass
                                self.close()
                                break
                    if emit_activity and on_event:
                        try: on_event(activity)
                        except Exception: pass
                if str(row.get("event") or row.get("type") or "").lower() == "step_update":
                    step = row.get("step_update") if isinstance(row.get("step_update"), dict) else {}
                    if str(step.get("step_type") or "").lower() == "agent_response" and str(step.get("state") or "").upper() == "DONE":
                        candidate = self._usage(step)
                        if any(candidate.values()):
                            turn_usage = candidate
                if str(row.get("event") or row.get("type") or "").lower() == "result":
                    payload = row.get("result") if isinstance(row.get("result"), dict) else row
                    final = dict(payload)
                    cid = str(final.get("conversation_id") or "")
                    if cid: self.conversation_id = cid
                    break
            if not final and not looped:
                if proc.poll() is None and time.monotonic() >= deadline:
                    self.close()
                    raise ProviderHarnessError("AGY streaming turn timed out")
                error = "".join(self.err_lines[-80:]).strip()
                raise ProviderHarnessError(error or "AGY streaming session ended before a result event")
            if looped:
                return ProviderRun(
                    "agy", self.model or "provider-default",
                    "Advertpreneur stopped the AGY run because the same tool operation repeated excessively. Review the task/tool trace before retrying.",
                    3, time.monotonic() - started, conversation_id=self.conversation_id,
                    status="GUARD_STOP", stderr="".join(self.err_lines[-80:]).strip(),
                    command=self._argv(), raw=raw_lines, reasoning_effort=self.effective_effort,
                    tool_calls=tool_calls, activity_events=activity_events, provider_turns=1,
                    session_reused=bool(self.resume_id or self._prev_turns), cache_read_additive=True,
                )
            cumulative = self._usage(final)
            # AGY result.usage is cumulative for a streaming conversation, while the
            # terminal agent_response step carries the current turn's usage. Prefer
            # that per-turn accounting so resumed ADP sessions never attribute old
            # conversation tokens to the new task. Fall back to a cumulative delta.
            if turn_usage:
                delta = turn_usage
            else:
                delta: Dict[str, int] = {}
                for key, value in cumulative.items():
                    prior = int(self._prev_usage.get(key, 0))
                    delta[key] = max(0, value - prior) if self._prev_usage else value
            try: turns = int(final.get("num_turns") or 0)
            except Exception: turns = 0
            provider_turns = 1
            reused = bool(self._prev_turns or self.resume_id)
            self._prev_usage = cumulative
            self._prev_turns = max(self._prev_turns, turns)
            self.resume_id = self.conversation_id
            self.last_used_at = time.monotonic()
            status = str(final.get("status") or "SUCCESS")
            return ProviderRun(
                "agy", self.model or "provider-default", str(final.get("response") or "").strip(),
                0 if status.upper() == "SUCCESS" else 1, time.monotonic() - started,
                delta.get("input_tokens", 0), delta.get("output_tokens", 0), delta.get("cache_read_tokens", 0),
                delta.get("thinking_tokens", 0), delta.get("total_tokens", 0), self.conversation_id,
                status, str(final.get("error") or "") or "".join(self.err_lines[-80:]).strip(),
                self._argv(), final, self.effective_effort, tool_calls, activity_events,
                provider_turns=provider_turns, session_reused=reused, cache_read_additive=True,
            )

    def close(self) -> None:
        proc, self.proc = self.proc, None
        try:
            self.out_q.put_nowait(None)
        except Exception:
            pass
        if not proc:
            return
        try:
            if proc.stdin: proc.stdin.close()
        except Exception:
            pass
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=2)
            except Exception:
                pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass


class ExternalProviderHarness:
    """Lightweight adapters around official provider runtimes.

    Codex prefers OpenAI's published ``openai-codex`` Python SDK. The SDK owns
    ChatGPT authentication and ships its matching Codex runtime, so users do not
    need a separately installed ``codex`` command. If the SDK is not present,
    Advertpreneur can install it on demand from PyPI when the user explicitly
    connects Codex. An already installed official Codex CLI remains a fallback.

    Antigravity paid-account OAuth is currently owned by Google's official ``agy``
    client/keyring flow. Advertpreneur can install that official runtime on demand
    from antigravity.google, then invokes it headlessly for model calls. ADP never
    reads/copies/parses provider OAuth or keyring credentials.
    """

    PROVIDERS = {"codex", "agy"}

    def __init__(self, app_dir: Path, project: Path) -> None:
        self.app_dir = app_dir
        self.project = project.resolve()
        self.run_dir = app_dir / "provider-runs"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.model_cache_path = self.app_dir / "provider-models-cache.json"
        self._model_cache: Dict[str, tuple[float, List[ProviderModel]]] = {}
        self._quota_cache: Dict[str, ProviderQuota] = {}
        self._quota_threshold_state: Dict[str, set[int]] = {}
        self._codex_app: _CodexAppServerClient | None = None
        self._agy_streams: Dict[str, "_AgyStreamDriver"] = {}
        self._active_interrupt_lock = threading.RLock()
        self._active_interrupt: Callable[[], None] | None = None
        self._codex_loaded_threads: set[str] = set()
        self._codex_thread_configs: Dict[str, str] = {}
        self._prune_run_dirs()

    def close(self) -> None:
        for driver in list(self._agy_streams.values()):
            try: driver.close()
            except Exception: pass
        self._agy_streams.clear()
        if self._codex_app is not None:
            try: self._codex_app.close()
            except Exception: pass
            self._codex_app = None

    def _set_active_interrupt(self, callback: Callable[[], None] | None) -> None:
        with self._active_interrupt_lock:
            self._active_interrupt = callback

    def interrupt_active(self) -> bool:
        """Ask the one in-flight external turn to stop without closing ADP itself."""
        with self._active_interrupt_lock:
            callback = self._active_interrupt
            self._active_interrupt = None
        if callback is None:
            return False
        try:
            callback()
        except Exception:
            # The provider can already have completed between Escape and this call.
            pass
        return True

    def _prune_run_dirs(self, max_age_days: int = 7) -> None:
        cutoff = time.time() - max_age_days * 86400
        try:
            for path in self.run_dir.iterdir():
                try:
                    if path.is_dir() and path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    pass
        except OSError:
            pass

    @staticmethod
    def _which(provider: str) -> str:
        if provider == "codex":
            return shutil.which("codex") or shutil.which("codex.cmd") or ""
        if provider == "agy":
            # PATH first, then Google's documented Windows user install location.
            found = shutil.which("agy") or shutil.which("agy.exe") or ""
            if found:
                return found
            if os.name == "nt":
                candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
                if candidate.exists():
                    return str(candidate)
                candidate2 = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
                if candidate2.exists():
                    return str(candidate2)
            return ""
        return ""

    @staticmethod
    def _creationflags() -> int:
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

    @staticmethod
    def _normalize_command_argv(argv: List[str]) -> List[str]:
        if not argv:
            return list(argv)
        _argv = list(argv)
        cmd = str(_argv[0])
        if os.name == "nt":
            if cmd.lower().endswith(".py"):
                return [sys.executable] + _argv
            try:
                p = Path(cmd)
                if p.is_file():
                    with open(p, "rb") as f:
                        header = f.read(128)
                    if header.startswith(b"#!") and b"python" in header.lower():
                        return [sys.executable] + _argv
            except Exception:
                pass
        return _argv

    @staticmethod
    def _run_capture(argv: List[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        _argv = ExternalProviderHarness._normalize_command_argv(argv)
        return subprocess.run(
            _argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=ExternalProviderHarness._creationflags(),
        )

    @staticmethod
    def _codex_sdk_available() -> bool:
        try:
            return importlib.util.find_spec("openai_codex") is not None
        except Exception:
            return False

    @staticmethod
    def _codex_sdk_version() -> str:
        try:
            return importlib.metadata.version("openai-codex")
        except Exception:
            return ""

    def ensure_runtime(self, provider: str) -> str:
        """Install the provider's official runtime only after an explicit user action."""
        provider = provider.lower().strip()
        if provider == "codex":
            # Reuse an already installed official Codex CLI first. Installing a
            # second SDK/runtime is unnecessary and can create version ambiguity.
            if self._which("codex"):
                return "cli"
            if self._codex_sdk_available():
                return "sdk"
            # Official OpenAI SDK fallback when no Codex runtime exists.
            p = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "openai-codex"],
                text=True,
            )
            importlib.invalidate_caches()
            if p.returncode != 0 or not self._codex_sdk_available():
                if self._which("codex"):
                    return "cli"
                raise ProviderHarnessError("Could not install OpenAI's official openai-codex SDK/runtime")
            return "sdk"

        if provider == "agy":
            command = self._which("agy")
            if command:
                return command
            if os.name == "nt":
                # Google's documented Windows installer. User explicitly requested
                # /providers login agy, so this is an intentional provider install.
                ps = shutil.which("powershell") or shutil.which("powershell.exe")
                if not ps:
                    raise ProviderHarnessError("PowerShell is required to install Google's official Antigravity runtime")
                p = subprocess.run(
                    [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://antigravity.google/cli/install.ps1 | iex"],
                    cwd=str(self.project),
                )
            else:
                shell = shutil.which("bash")
                curl = shutil.which("curl")
                if not shell or not curl:
                    raise ProviderHarnessError("bash + curl are required to install Google's official Antigravity runtime")
                p = subprocess.run([shell, "-lc", "curl -fsSL https://antigravity.google/cli/install.sh | bash"], cwd=str(self.project))
            if p.returncode != 0:
                raise ProviderHarnessError(f"Google Antigravity official installer exited with code {p.returncode}")
            command = self._which("agy")
            if not command:
                raise ProviderHarnessError("Antigravity installed, but agy was not found. Open a new shell or add Google's agy bin directory to PATH.")
            return command
        raise ProviderHarnessError(f"Unknown external provider: {provider}")

    def status(self, provider: str) -> ProviderStatus:
        provider = provider.lower().strip()
        if provider not in self.PROVIDERS:
            raise ProviderHarnessError(f"Unknown external provider: {provider}")

        if provider == "codex":
            # Prefer the official Python SDK because it is a complete first-party
            # runtime and can enumerate the authenticated account's model catalogue.
            if self._codex_sdk_available():
                version = f"openai-codex {self._codex_sdk_version()}"
                auth = "unknown"
                detail = "Official OpenAI Codex SDK/runtime"
                try:
                    from openai_codex import Codex
                    with Codex() as codex:
                        acct = codex.account()
                    data = self._obj_to_dict(acct)
                    account = data.get("account") if isinstance(data, dict) else None
                    if account:
                        auth = "authenticated"
                        detail = "Signed in with ChatGPT via official OpenAI Codex SDK"
                    else:
                        auth = "signed-out"
                except Exception as exc:
                    low = str(exc).lower()
                    if "login" in low or "auth" in low or "signed" in low:
                        auth = "signed-out"
                    detail = f"Official OpenAI Codex SDK · {str(exc)[:220]}"
                return ProviderStatus(provider, True, "openai_codex", version, auth, detail)

            command = self._which("codex")
            if not command:
                return ProviderStatus(provider, False, detail="Codex runtime not installed; /providers login codex installs OpenAI's official SDK/runtime automatically")
            version = ""
            try:
                p = self._run_capture([command, "--version"], self.project, timeout=8)
                version = (p.stdout or p.stderr or "").strip().splitlines()[0][:180]
            except Exception:
                pass
            auth = "unknown"
            detail = "Official Codex CLI fallback"
            try:
                p = self._run_capture([command, "login", "status"], self.project, timeout=12)
                detail = (p.stdout or p.stderr or "").strip()[:500]
                low = detail.lower()
                if p.returncode == 0 and "logged in" in low and "not logged" not in low:
                    auth = "authenticated"
                elif "not logged" in low:
                    auth = "signed-out"
            except Exception as exc:
                detail = str(exc)
            return ProviderStatus(provider, True, command, version, auth, detail)

        command = self._which("agy")
        if not command:
            return ProviderStatus(provider, False, detail="Antigravity runtime not installed; /providers login agy installs Google's official runtime automatically")
        version = ""
        try:
            p = self._run_capture([command, "--version"], self.project, timeout=8)
            version = (p.stdout or p.stderr or "").strip().splitlines()[0][:180]
        except Exception:
            pass
        return ProviderStatus(provider, True, command, version, "provider-managed", "Official AGY keyring session; /providers test agy verifies it with a tiny call")

    def login(self, provider: str) -> int:
        provider = provider.lower().strip()
        if provider == "codex":
            self.ensure_runtime("codex")
            if self._codex_sdk_available():
                try:
                    from openai_codex import Codex
                    with Codex() as codex:
                        handle = codex.login_chatgpt()
                        url = str(getattr(handle, "auth_url", "") or "")
                        if url:
                            webbrowser.open(url, new=2)
                            print(f"Official ChatGPT sign-in: {url}")
                        result = handle.wait()
                        data = self._obj_to_dict(result)
                        success = bool(data.get("success", True)) if isinstance(data, dict) else True
                        return 0 if success else 1
                except Exception as exc:
                    raise ProviderHarnessError(f"Official Codex SDK login failed: {exc}") from exc
            command = self._which("codex")
            if not command:
                raise ProviderHarnessError("OpenAI Codex runtime unavailable after setup")
            return int(subprocess.run([command, "login"], cwd=str(self.project)).returncode)

        if provider == "agy":
            command = self.ensure_runtime("agy")
            # Google's official local flow opens its official browser login when no
            # secure keyring session exists. ADP never sees the credentials.
            return int(subprocess.run([command], cwd=str(self.project)).returncode)
        raise ProviderHarnessError(f"Unknown external provider: {provider}")

    @staticmethod
    def _obj_to_dict(obj: Any) -> Dict[str, Any]:
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        for name in ("model_dump", "dict"):
            fn = getattr(obj, name, None)
            if callable(fn):
                try:
                    value = fn()
                    if isinstance(value, dict):
                        return value
                except Exception:
                    pass
        try:
            return dict(vars(obj))
        except Exception:
            return {}

    def _model_catalog_live(self, provider: str, timeout: int = 30) -> List[ProviderModel]:
        provider = provider.lower().strip()
        if provider == "codex":
            self.ensure_runtime("codex")
            # Prefer the same native app-server catalogue used by current Codex.
            if self._which("codex"):
                try:
                    response = self._codex_app_client().request("model/list", {}, timeout=min(timeout, 15))
                    rows: Any = response.get("data") or response.get("models") or []
                    out: List[ProviderModel] = []
                    for row in rows if isinstance(rows, list) else []:
                        if not isinstance(row, dict):
                            continue
                        mid = str(row.get("id") or row.get("model") or row.get("slug") or "").strip()
                        if not mid:
                            continue
                        if row.get("hidden") is True or row.get("isHidden") is True or row.get("available") is False:
                            continue
                        label = str(row.get("displayName") or row.get("display_name") or row.get("name") or mid).strip()
                        levels = row.get("supportedReasoningEfforts") or row.get("reasoningEfforts") or row.get("supported_reasoning_efforts") or []
                        if isinstance(levels, dict): levels = list(levels.keys())
                        levels = [str(x.get("effort") or x.get("id") or x) if isinstance(x, dict) else str(x) for x in (levels if isinstance(levels, list) else [])]
                        levels = [x.lower() for x in levels if x.lower() in {"low", "medium", "high"}]
                        desc = str(row.get("description") or "").strip()
                        detail = " · ".join(x for x in (("reasoning " + "/".join(levels)) if levels else "", desc) if x)
                        out.append(ProviderModel("codex", mid, label, detail[:220], levels))
                    if out:
                        return out
                except Exception:
                    pass
            if self._codex_sdk_available():
                try:
                    from openai_codex import Codex
                    with Codex() as codex:
                        response = codex.models()
                    data = self._obj_to_dict(response)
                    rows: Any = data.get("models") or data.get("data") or []
                    if not rows and isinstance(response, (list, tuple)):
                        rows = response
                    out: List[ProviderModel] = []
                    for row in rows or []:
                        d = self._obj_to_dict(row)
                        mid = str(d.get("slug") or d.get("id") or d.get("model") or d.get("name") or "").strip()
                        if not mid:
                            continue
                        label = str(d.get("display_name") or d.get("displayName") or d.get("name") or mid).strip()
                        effort = str(d.get("default_reasoning_level") or d.get("defaultReasoningLevel") or "").strip().lower()
                        levels = [effort] if effort in {"low", "medium", "high"} else []
                        desc = str(d.get("description") or "").strip()
                        detail = " · ".join(x for x in (effort and f"default {effort}", desc) if x)
                        out.append(ProviderModel("codex", mid, label, detail[:220], levels))
                    seen: set[str] = set(); unique: List[ProviderModel] = []
                    for item in out:
                        if item.id.lower() not in seen:
                            seen.add(item.id.lower()); unique.append(item)
                    if unique:
                        return unique
                except Exception as exc:
                    raise ProviderHarnessError(f"Could not fetch Codex model catalogue: {exc}") from exc
            raise ProviderHarnessError("OpenAI Codex model catalogue unavailable")

        if provider == "agy":
            command = self._which("agy")
            if not command:
                raise ProviderHarnessError("Antigravity runtime is not installed. Use /providers login agy; ADP will install Google's official runtime.")
            p = self._run_capture([command, "models"], self.project, timeout=timeout)
            if p.returncode != 0:
                raise ProviderHarnessError((p.stderr or p.stdout or "agy models failed").strip())
            out: List[ProviderModel] = []
            ansi = re.compile(r"\x1b\[[0-9;]*m")
            for raw in (p.stdout or "").splitlines():
                line = ansi.sub("", raw).strip()
                if not line or line.lower().startswith(("available models", "model ")):
                    continue
                m = re.match(r"^([A-Za-z0-9_.:/-]+)\s{2,}(.+)$", line)
                if not m:
                    m = re.match(r"^([A-Za-z0-9_.:/-]+)\s+(.+)$", line)
                if not m:
                    continue
                mid, label = m.group(1).strip(), m.group(2).strip()
                if mid.lower() in {"slug", "id", "name"}:
                    continue
                fixed = self.agy_model_effort(mid)
                detail = "Google Antigravity account model" + (f" · model-defined reasoning {fixed}" if fixed else "")
                out.append(ProviderModel("agy", mid, label, detail, [fixed] if fixed else []))
            if not out:
                raise ProviderHarnessError("AGY returned no parseable models")
            return out
        raise ProviderHarnessError(f"Unknown external provider: {provider}")

    def _read_model_cache(self, provider: str, max_age: int = 86400) -> List[ProviderModel]:
        try:
            data = json.loads(self.model_cache_path.read_text(encoding="utf-8")) if self.model_cache_path.exists() else {}
            row = data.get(provider) if isinstance(data, dict) else None
            if not isinstance(row, dict) or time.time() - float(row.get("at") or 0) > max_age:
                return []
            out = []
            for item in row.get("models") or []:
                if isinstance(item, dict) and item.get("id"):
                    out.append(ProviderModel(provider, str(item["id"]), str(item.get("label") or ""), str(item.get("detail") or ""), [str(x) for x in (item.get("reasoning_levels") or [])]))
            return out
        except Exception:
            return []

    def _write_model_cache(self, provider: str, rows: List[ProviderModel]) -> None:
        try:
            data = json.loads(self.model_cache_path.read_text(encoding="utf-8")) if self.model_cache_path.exists() else {}
            if not isinstance(data, dict): data = {}
            data[provider] = {"at": time.time(), "models": [{"id": x.id, "label": x.label, "detail": x.detail, "reasoning_levels": x.reasoning_levels} for x in rows]}
            tmp = self.model_cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.model_cache_path)
        except Exception:
            pass

    def model_catalog(self, provider: str, timeout: int = 30, refresh: bool = False) -> List[ProviderModel]:
        provider = provider.lower().strip()
        now = time.time()
        cached = self._model_cache.get(provider)
        if not refresh and cached and now - cached[0] < 300:
            return list(cached[1])
        if not refresh:
            disk = self._read_model_cache(provider)
            if disk:
                self._model_cache[provider] = (now, disk)
                return list(disk)
        rows = self._model_catalog_live(provider, timeout=timeout)
        self._model_cache[provider] = (now, list(rows))
        self._write_model_cache(provider, rows)
        return rows

    @staticmethod
    def _window_label(minutes: int) -> str:
        if 285 <= minutes <= 315: return "5h"
        if 9576 <= minutes <= 10584: return "weekly"
        if 1368 <= minutes <= 1512: return "daily"
        return f"{minutes}m" if minutes else "quota"

    @staticmethod
    def _agy_quota_group(model: str) -> str:
        low = (model or "").lower()
        if "gemini" in low:
            return "gemini"
        if any(x in low for x in ("claude", "gpt", "oss")):
            return "claude-gpt"
        return "default"

    def _quota_key(self, provider: str, model: str = "") -> str:
        provider = provider.lower().strip()
        return provider if provider != "agy" else f"agy:{self._agy_quota_group(model)}"

    def quota_cached(self, provider: str, model: str = "") -> ProviderQuota | None:
        provider = provider.lower().strip()
        key = self._quota_key(provider, model)
        # AGY has independent Gemini vs Claude/GPT pools. Never let a cached
        # Gemini snapshot masquerade as Claude/GPT (or vice versa).
        if provider == "agy":
            return self._quota_cache.get(key)
        return self._quota_cache.get(key) or self._quota_cache.get(provider)

    @staticmethod
    def _parse_codex_quota_response(response: Dict[str, Any]) -> ProviderQuota:
        snapshots = response.get("rateLimitsByLimitId") if isinstance(response.get("rateLimitsByLimitId"), dict) else {}
        snap = snapshots.get("codex") if isinstance(snapshots, dict) else None
        if not isinstance(snap, dict):
            snap = response.get("rateLimits") if isinstance(response.get("rateLimits"), dict) else {}
        windows: List[ProviderQuotaWindow] = []
        for key in ("primary", "secondary"):
            w = snap.get(key) if isinstance(snap, dict) else None
            if not isinstance(w, dict):
                continue
            raw_used = w.get("usedPercent")
            # A missing percentage is NOT 0% used / 100% remaining. Omit the
            # unreported window so callers render an explicit em dash.
            if not isinstance(raw_used, (int, float)):
                continue
            used = max(0.0, min(100.0, float(raw_used)))
            mins = int(w.get("windowDurationMins") or 0)
            reset = int(w.get("resetsAt") or 0)
            windows.append(ProviderQuotaWindow(
                ExternalProviderHarness._window_label(mins),
                max(0.0, min(100.0, 100.0 - used)), used, reset, mins, "reported"
            ))
        if not windows:
            raise ProviderHarnessError("Codex did not expose quota windows for this account")
        return ProviderQuota(
            "codex", source="official codex app-server account/rateLimits/read",
            fetched_at=time.time(), windows=windows, raw=response
        )

    def _on_codex_notification(self, row: dict[str, Any]) -> None:
        method = str(row.get("method") or "")
        if method != "account/rateLimits/updated":
            return
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        try:
            # Notifications can be sparse. Merge only windows they actually contain
            # into the last authoritative snapshot; never manufacture missing data.
            update = self._parse_codex_quota_response(params)
        except Exception:
            return
        current = self._quota_cache.get("codex")
        if current:
            by_label = {w.label: w for w in current.windows}
            by_label.update({w.label: w for w in update.windows})
            update.windows = list(by_label.values())
        self._quota_cache["codex"] = update

    def _codex_app_client(self) -> _CodexAppServerClient:
        command = self._which("codex")
        if not command:
            raise ProviderHarnessError("Official Codex CLI is unavailable for native metadata")
        if self._codex_app is None:
            self._codex_app = _CodexAppServerClient(command, self.project, self._on_codex_notification)
        return self._codex_app

    def _codex_quota_live(self, timeout: int = 12) -> ProviderQuota:
        # Prefer the installed official app-server because one connection can be
        # reused for both snapshots and sparse rolling notifications.
        if self._which("codex"):
            try:
                response = self._codex_app_client().request("account/rateLimits/read", {}, timeout=timeout)
                return self._parse_codex_quota_response(response)
            except Exception:
                pass
        # SDK fallback still uses the current no-argument rate-limit request.
        if self._codex_sdk_available():
            try:
                from openai_codex.client import CodexClient
                from openai_codex.generated.v2_all import GetAccountRateLimitsResponse
                with CodexClient() as client:
                    client.initialize()
                    response = client.request("account/rateLimits/read", {}, response_model=GetAccountRateLimitsResponse)
                if hasattr(response, "model_dump"):
                    response = response.model_dump(by_alias=True, mode="json")
                if isinstance(response, dict):
                    return self._parse_codex_quota_response(response)
            except Exception as exc:
                raise ProviderHarnessError(f"Codex quota unavailable: {exc}") from exc
        raise ProviderHarnessError("Official Codex runtime did not expose authoritative subscription quota")

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text or "")

    @staticmethod
    def _agy_text_from_payload(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("response", "text", "content", "message", "output"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload or "")

    def _parse_agy_quota_payload(self, payload: Any, model: str = "") -> ProviderQuota:
        # Current AGY JSON exposes the authoritative buckets under
        # command.data.groups[].buckets[]. Prefer those raw remaining_fraction values
        # over the human response string, which rounds 99.87% up to "100%".
        if isinstance(payload, dict):
            command = payload.get("command") if isinstance(payload.get("command"), dict) else {}
            data = command.get("data") if isinstance(command.get("data"), dict) else {}
            groups = data.get("groups") if isinstance(data.get("groups"), list) else []
            if groups:
                wanted = self._agy_quota_group(model)
                chosen = None
                for group_row in groups:
                    if not isinstance(group_row, dict):
                        continue
                    name = str(group_row.get("name") or "").lower()
                    if wanted == "gemini" and "gemini" in name:
                        chosen = group_row; break
                    if wanted == "claude-gpt" and ("claude" in name or "gpt" in name):
                        chosen = group_row; break
                if chosen is None:
                    chosen = next((x for x in groups if isinstance(x, dict)), None)
                windows: List[ProviderQuotaWindow] = []
                if isinstance(chosen, dict):
                    for bucket in chosen.get("buckets") or []:
                        if not isinstance(bucket, dict):
                            continue
                        window = str(bucket.get("window") or "").lower().strip()
                        label = "5h" if window in {"5h", "five-hour", "five_hour"} else ("weekly" if "week" in window else "")
                        if not label:
                            continue
                        value = bucket.get("remaining_fraction")
                        if not isinstance(value, (int, float)) or isinstance(value, bool):
                            value = bucket.get("remaining_percent")
                            if not isinstance(value, (int, float)) or isinstance(value, bool):
                                continue
                            remaining = float(value)
                        else:
                            remaining = float(value) * 100.0 if 0.0 <= float(value) <= 1.0 else float(value)
                        reset = 0
                        raw_reset = bucket.get("reset_time") or bucket.get("resetAt") or bucket.get("reset_at")
                        if isinstance(raw_reset, str) and raw_reset.strip():
                            try:
                                dt = datetime.fromisoformat(raw_reset.strip().replace("Z", "+00:00"))
                                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                                reset = int(dt.timestamp())
                            except Exception:
                                reset = 0
                        remaining = max(0.0, min(100.0, remaining))
                        windows.append(ProviderQuotaWindow(label, remaining, 100.0 - remaining, resets_at=reset, state="reported"))
                if windows:
                    return ProviderQuota(
                        "agy", model=model, source="official agy /usage", fetched_at=time.time(),
                        windows=windows, detail=str(chosen.get("description") or "")[:3000], raw=payload,
                    )

        text = self._strip_ansi(self._agy_text_from_payload(payload))
        # Some AGY builds render the read-only slash result as formatted text even
        # under JSON output. Select only the quota pool applicable to this model.
        lines = text.splitlines(); lower_model = model.lower(); selected = lines
        starts: List[tuple[int, str]] = []
        for i, line in enumerate(lines):
            low = line.lower()
            if "gemini models" in low or "claude and gpt" in low or "claude & gpt" in low:
                starts.append((i, low))
        if starts:
            target = "gemini models" if "gemini" in lower_model else ("claude" if any(x in lower_model for x in ("claude", "gpt", "oss")) else "")
            choice = next((idx for idx, label in starts if target and target in label), starts[0][0])
            end = next((idx for idx, _ in starts if idx > choice), len(lines))
            selected = lines[choice:end]
        section = "\n".join(selected)

        # Flexible key/value extraction for JSON-shaped quota summaries.
        flat: List[tuple[str, float]] = []
        flat_values: List[tuple[str, Any]] = []
        def walk(value: Any, path: str = "") -> None:
            if isinstance(value, dict):
                for k, v in value.items(): walk(v, f"{path}.{k}" if path else str(k))
            elif isinstance(value, list):
                for i, v in enumerate(value): walk(v, f"{path}[{i}]")
            else:
                flat_values.append((path.lower(), value))
                if isinstance(value, (int, float)) and not isinstance(value, bool): flat.append((path.lower(), float(value)))
        walk(payload)

        def terms_for(label: str) -> tuple[str, ...]:
            return ("5h", "fivehour", "five_hour", "five-hour") if label == "5h" else ("week", "weekly")

        def path_matches_group(compact: str) -> bool:
            group = self._agy_quota_group(model)
            if group == "gemini" and any(x in compact for x in ("claude", "gpt")): return False
            if group == "claude-gpt" and "gemini" in compact: return False
            return True

        def json_remaining(label: str) -> float | None:
            terms = terms_for(label)
            for path, value in flat:
                compact = path.replace(" ", "").replace("-", "_")
                if not any(t.replace("-", "_") in compact for t in terms):
                    continue
                if not path_matches_group(compact):
                    continue
                if "remaining" in compact or "remain" in compact:
                    # APIs sometimes report fractions rather than percentages.
                    return max(0.0, min(100.0, value * 100.0 if 0.0 <= value <= 1.0 else value))
            return None

        def json_reset(label: str) -> int:
            terms = terms_for(label)
            for path, value in flat_values:
                compact = path.replace(" ", "").replace("-", "_")
                if not any(t.replace("-", "_") in compact for t in terms) or "reset" not in compact or not path_matches_group(compact):
                    continue
                try:
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        n = float(value)
                        if n > 1e12: return int(n / 1000.0)
                        if n > 1e9: return int(n)
                        if n >= 0 and any(x in compact for x in ("after", "until", "seconds", "duration")):
                            return int(time.time() + n)
                    if isinstance(value, str) and value.strip():
                        raw = value.strip()
                        if raw.isdigit():
                            n = int(raw); return int(n / 1000) if n > 1e12 else (n if n > 1e9 else 0)
                        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                        return int(dt.timestamp())
                except Exception:
                    continue
            return 0

        windows: List[ProviderQuotaWindow] = []
        patterns = {
            "5h": [r"Five\s*Hour\s*Limit\s*Remaining[^0-9]*(\d+(?:\.\d+)?)\s*%", r"5\s*h(?:our)?[^\n%]*(\d+(?:\.\d+)?)\s*%"],
            "weekly": [r"Weekly\s*Limit\s*Remaining[^0-9]*(\d+(?:\.\d+)?)\s*%", r"Weekly[^\n%]*(\d+(?:\.\d+)?)\s*%"],
        }
        for label in ("5h", "weekly"):
            val = json_remaining(label)
            if val is None:
                for pat in patterns[label]:
                    m = re.search(pat, section, re.I)
                    if m:
                        val = float(m.group(1)); break
            if val is not None:
                val = max(0.0, min(100.0, float(val)))
                windows.append(ProviderQuotaWindow(label, val, 100.0 - val, resets_at=json_reset(label), state="reported"))
        if not windows:
            raise ProviderHarnessError("AGY /usage returned no parseable authoritative quota percentages")
        return ProviderQuota(
            "agy", model=model, source="official agy /usage", fetched_at=time.time(),
            windows=windows, detail=section[:3000], raw=payload
        )

    def _agy_quota_live(self, model: str = "", timeout: int = 20) -> ProviderQuota:
        command = self._which("agy")
        if not command:
            raise ProviderHarnessError("Official AGY runtime is not installed")
        # AGY 1.1.12+ documents read-only slash commands in print mode as metadata
        # operations that do not start an agent turn or consume model quota.
        p = self._run_capture([command, "-p", "/usage", "--output-format", "json"], self.project, timeout=timeout)
        combined = self._strip_ansi((p.stdout or "") + "\n" + (p.stderr or ""))
        if p.returncode != 0:
            raise ProviderHarnessError(combined.strip() or f"agy /usage exited {p.returncode}")
        raw = (p.stdout or "").strip()
        try:
            payload: Any = json.loads(raw.splitlines()[-1]) if raw else {}
        except Exception:
            payload = combined
        try:
            return self._parse_agy_quota_payload(payload, model)
        except ProviderHarnessError:
            return self._parse_agy_quota_payload(combined, model)

    def quota(self, provider: str, model: str = "", refresh: bool = False, timeout: int = 20) -> ProviderQuota:
        provider = provider.lower().strip(); key = self._quota_key(provider, model)
        cached = self._quota_cache.get(key) if provider == "agy" else (self._quota_cache.get(key) or self._quota_cache.get(provider))
        if not refresh and cached and time.time() - cached.fetched_at < 45:
            return cached
        row = self._codex_quota_live(timeout=min(timeout, 15)) if provider == "codex" else self._agy_quota_live(model, timeout=timeout)
        row.model = model
        self._quota_cache[key] = row
        # Codex has one account-wide snapshot. AGY does not: its quota is grouped
        # by model family, so a generic provider cache would cross-contaminate pools.
        if provider != "agy":
            self._quota_cache[provider] = row
        return row

    @staticmethod
    def quota_text(quota: ProviderQuota | None) -> str:
        if not quota:
            return "5h — · wk —"
        parts: List[str] = []
        for label in ("5h", "weekly"):
            w = next((x for x in quota.windows if x.label == label and x.state == "reported"), None)
            short = "wk" if label == "weekly" else label
            if w:
                value = float(w.remaining_percent)
                if 99.0 < value < 100.0:
                    rendered = f"{value:.2f}".rstrip("0").rstrip(".")
                elif abs(value - round(value)) >= 0.05:
                    rendered = f"{value:.1f}".rstrip("0").rstrip(".")
                else:
                    rendered = f"{value:.0f}"
                parts.append(f"{short} {rendered}%")
            else:
                parts.append(f"{short} —")
        return " · ".join(parts)

    def quota_thresholds(self, quota: ProviderQuota) -> List[int]:
        """Return the strongest newly crossed remaining-quota threshold.

        A jump from healthy directly to 4% emits one critical warning, not three
        stacked 25/10/5 notifications. Sequential crossings still fire once each.
        """
        key = self._quota_key(quota.provider, quota.model)
        remaining = quota.effective_remaining
        if remaining is None: return []
        armed = self._quota_threshold_state.setdefault(key, set())
        if remaining > 25:
            armed.clear(); return []
        target = 0 if remaining <= 0 else (5 if remaining <= 5 else (10 if remaining <= 10 else 25))
        if target in armed: return []
        # Crossing a stronger threshold implicitly crosses all weaker ones.
        for t in (25, 10, 5, 0):
            if t >= target: armed.add(t)
        return [target]

    def models(self, provider: str, timeout: int = 30) -> str:
        rows = self.model_catalog(provider, timeout=timeout)
        return "\n".join(f"{m.id:<34} {m.display}" for m in rows)

    @staticmethod
    def _codex_parse(stdout: str) -> tuple[str, Dict[str, int], str]:
        text_parts: List[str] = []
        usage: Dict[str, int] = {}
        thread_id = ""
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            typ = str(row.get("type") or "")
            if typ == "thread.started":
                thread_id = str(row.get("thread_id") or row.get("thread", {}).get("id") or "")
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            if typ in {"item.completed", "item.updated"} and item:
                item_type = str(item.get("type") or "")
                if item_type in {"agent_message", "message"}:
                    value = item.get("text") or item.get("content") or ""
                    if isinstance(value, list):
                        value = "\n".join(str(x.get("text") or x) if isinstance(x, dict) else str(x) for x in value)
                    if value:
                        text_parts.append(str(value))
            if typ == "turn.completed":
                u = row.get("usage") or {}
                if isinstance(u, dict):
                    for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_read_tokens", "total_tokens"):
                        try:
                            usage[key] = int(u.get(key) or 0)
                        except Exception:
                            pass
        text = text_parts[-1].strip() if text_parts else ""
        return text, usage, thread_id

    @staticmethod
    def _codex_turn_count(stdout: str) -> int:
        count = 0
        for line in (stdout or "").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and str(row.get("type") or "") == "turn.completed":
                count += 1
        return max(1, count)

    @staticmethod
    def _usage_from_obj(obj: Any) -> Dict[str, int]:
        d = ExternalProviderHarness._obj_to_dict(obj)
        result: Dict[str, int] = {}
        aliases = {
            "input_tokens": ("input_tokens", "inputTokens"),
            "output_tokens": ("output_tokens", "outputTokens"),
            "cache_read_tokens": ("cached_input_tokens", "cache_read_tokens", "cachedInputTokens"),
            "total_tokens": ("total_tokens", "totalTokens"),
        }
        for dest, keys in aliases.items():
            for key in keys:
                if key in d:
                    try:
                        result[dest] = int(d.get(key) or 0)
                    except Exception:
                        result[dest] = 0
                    break
        return result

    @staticmethod
    def normalize_effort(effort: str) -> str:
        """External-provider effort policy: explicit low/medium/high only.

        Advertpreneur intentionally does not expose Codex xhigh/max. Unknown or
        legacy values fall back to medium so a model choice can never silently
        become a maximum-reasoning run.
        """
        value = (effort or "medium").strip().lower()
        return value if value in {"low", "medium", "high"} else "medium"

    @staticmethod
    def agy_model_effort(model: str) -> str:
        """Return a reasoning tier encoded by an AGY model slug, if present."""
        m = re.search(r"-(low|medium|high)$", str(model or "").strip().lower())
        return m.group(1) if m else ""

    @classmethod
    def agy_uses_effort_flag(cls, model: str) -> bool:
        low = str(model or "").strip().lower()
        return not (
            cls.agy_model_effort(low)
            or "claude" in low
            or any(marker in low for marker in ("gpt-oss", "gpt_oss", "gptoss"))
        )

    @classmethod
    def agy_effective_effort(cls, model: str, requested: str) -> str:
        if not cls.agy_uses_effort_flag(model):
            return cls.agy_model_effort(model)
        return cls.normalize_effort(requested)

    @staticmethod
    def _command_activity(command: Any) -> tuple[str, str]:
        if isinstance(command, list):
            text = " ".join(str(x) for x in command)
        else:
            text = str(command or "").strip()
        low = text.lower()
        if any(x in low for x in ("pytest", "unittest", "npm test", "pnpm test", "gradlew test", "test:")):
            return "Running tests", text[:180]
        if any(x in low for x in ("npm run build", "pnpm build", "gradlew assemble", "gradlew build", "mvn ", "cargo build", "dotnet build")):
            return "Running build", text[:180]
        if any(x in low for x in ("rg ", "grep ", "findstr ", "select-string")):
            return "Searching code", text[:180]
        if low.startswith("git ") or " git " in low:
            return "Checking Git", text[:180]
        return "Running command", text[:180]

    @staticmethod
    def _activity_from_codex_row(row: Dict[str, Any]) -> ProviderActivity | None:
        typ = str(row.get("type") or "")
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        started = typ == "item.started"
        if item_type in {"reasoning", "reasoning_summary"}:
            # Never surface the reasoning body. This is lifecycle telemetry only.
            return ProviderActivity("reasoning", "Reasoning")
        if item_type in {"command_execution", "command", "shell_command"}:
            label, detail = ExternalProviderHarness._command_activity(item.get("command") or item.get("cmd"))
            sig = "command:" + detail[:240]
            return ProviderActivity("tool" if started else "progress", label, detail, "command", sig if started else "")
        if item_type in {"file_change", "file_write", "edit", "patch"}:
            path = str(item.get("path") or item.get("file_path") or "")
            changes = item.get("changes")
            if not path and isinstance(changes, list) and changes and isinstance(changes[0], dict):
                path = str(changes[0].get("path") or changes[0].get("file_path") or "")
            name = Path(path).name if path else "code"
            sig = f"edit:{path}" if started else ""
            return ProviderActivity("tool" if started else "progress", f"Writing code · {name}", path[:180], "edit", sig)
        if item_type in {"mcp_tool_call", "tool_call", "function_call"}:
            tool = str(item.get("tool") or item.get("name") or item.get("server") or "tool")
            args = item.get("arguments") or item.get("args") or item.get("parameters") or {}
            try: sig_args = json.dumps(args, sort_keys=True, ensure_ascii=False)[:500]
            except Exception: sig_args = str(args)[:500]
            return ProviderActivity("tool" if started else "progress", f"Using {tool}", "", tool, f"{tool}:{sig_args}" if started else "")
        if item_type in {"web_search", "search"}:
            query = str(item.get("query") or "")
            return ProviderActivity("tool" if started else "progress", "Searching web", query[:160], "web_search", f"web:{query}" if started else "")
        if item_type in {"agent_message", "message"} and typ in {"item.started", "item.updated", "item.completed"}:
            return ProviderActivity("progress", "Preparing response")
        if typ == "turn.started":
            return ProviderActivity("reasoning", "Reasoning")
        if typ == "turn.completed":
            return ProviderActivity("progress", "Preparing result")
        return None

    @staticmethod
    def _find_dict_with_key(value: Any, key: str) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if key in value:
                return value
            for child in value.values():
                found = ExternalProviderHarness._find_dict_with_key(child, key)
                if found: return found
        elif isinstance(value, list):
            for child in value:
                found = ExternalProviderHarness._find_dict_with_key(child, key)
                if found: return found
        return None

    @staticmethod
    def _activity_basename(path: str) -> str:
        text = str(path or "")
        if not text:
            return ""
        try:
            return (PureWindowsPath(text).name if "\\" in text else Path(text).name) or text
        except Exception:
            return text

    @staticmethod
    def _agy_param_value(value: Any, *keys: str) -> str:
        """Find a useful AGY tool parameter without depending on key casing/layout.

        AGY stream-json places tool parameters under step_update.tool_info.parameters
        and currently uses names such as TargetFile and CommandLine. Older builds and
        tests used path/file_path/command. Keep this parser tolerant across both.
        """
        wanted = {str(k).replace("_", "").lower() for k in keys}
        if isinstance(value, dict):
            for key, child in value.items():
                norm = str(key).replace("_", "").lower()
                if norm in wanted and child not in (None, ""):
                    return str(child)
            for child in value.values():
                found = ExternalProviderHarness._agy_param_value(child, *keys)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = ExternalProviderHarness._agy_param_value(child, *keys)
                if found:
                    return found
        return ""

    @staticmethod
    def _activity_from_agy_row(row: Dict[str, Any]) -> ProviderActivity | None:
        typ = str(row.get("type") or row.get("event") or "").lower()
        step = row.get("step_update") if isinstance(row.get("step_update"), dict) else {}
        step_type = str(step.get("step_type") or "").lower()
        state = str(step.get("state") or "").upper()

        if typ == "init":
            return ProviderActivity("progress", "Inspecting project")

        if typ == "step_update" and step_type == "tool":
            info = step.get("tool_info") if isinstance(step.get("tool_info"), dict) else {}
            tool = str(step.get("tool_name") or info.get("name") or "tool")
            params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
            if not params:
                params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
            low = tool.lower()
            label = f"Using {tool}"
            detail = ""
            if any(x in low for x in ("write", "edit", "patch", "replace")):
                path = ExternalProviderHarness._agy_param_value(
                    params, "path", "file_path", "file", "filename", "target_file", "target_path", "targetfile"
                )
                label = f"Writing code · {ExternalProviderHarness._activity_basename(path) if path else 'file'}"
                detail = path[:180]
            elif any(x in low for x in ("read", "view", "cat")):
                path = ExternalProviderHarness._agy_param_value(
                    params, "path", "file_path", "file", "filename", "target_file", "target_path", "targetfile"
                )
                label = f"Reading {ExternalProviderHarness._activity_basename(path) if path else 'file'}"
                detail = path[:180]
            elif any(x in low for x in ("search", "grep", "find")):
                label = "Searching code"
                detail = ExternalProviderHarness._agy_param_value(params, "query", "pattern", "search_term")[:180]
            elif any(x in low for x in ("shell", "command", "terminal", "run")):
                cmd = ExternalProviderHarness._agy_param_value(params, "command", "command_line", "commandline", "cmd")
                label, detail = ExternalProviderHarness._command_activity(cmd)
            try:
                sig_params = json.dumps(params, sort_keys=True, ensure_ascii=False)[:500]
            except Exception:
                sig_params = str(params)[:500]
            # AGY may emit ACTIVE then DONE for the same step. Count/display the
            # invocation once on ACTIVE; if a build only emits DONE, use DONE.
            sig = f"{tool}:{sig_params}:step={step.get('step_index','')}"
            return ProviderActivity("tool", label, detail, tool, sig)

        if typ == "step_update":
            if step_type == "agent_response":
                if step.get("text_delta"):
                    return ProviderActivity("progress", "Preparing response")
                return ProviderActivity("progress", "Analyzing task")
            if step_type == "checkpoint":
                return ProviderActivity("progress", "Saving checkpoint")
            if step_type == "user_input":
                return None
            if state == "ACTIVE":
                return ProviderActivity("progress", "Working")
            return None
        if typ in {"result", "completed", "done"}:
            return ProviderActivity("progress", "Preparing result")
        if typ == "started":
            return ProviderActivity("progress", "Inspecting project")
        return None

    def _stream_json_command(
        self, argv: List[str], cwd: Path, timeout: int, provider: str,
        on_event: Callable[[ProviderActivity], None] | None,
        mapper: Callable[[Dict[str, Any]], ProviderActivity | None],
    ) -> tuple[int, str, str, int, int, bool]:
        _argv = ExternalProviderHarness._normalize_command_argv(argv)
        proc = subprocess.Popen(
            _argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=self._creationflags(),
        )
        out_q: queue.Queue[str | None] = queue.Queue()
        err_lines: List[str] = []
        def read_out() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout: out_q.put(line)
            finally: out_q.put(None)
        def read_err() -> None:
            try:
                assert proc.stderr is not None
                for line in proc.stderr: err_lines.append(line)
            except Exception:
                pass
        threading.Thread(target=read_out, name=f"adp-{provider}-stream", daemon=True).start()
        threading.Thread(target=read_err, name=f"adp-{provider}-stderr", daemon=True).start()
        deadline = time.monotonic() + max(1, timeout)
        raw_lines: List[str] = []
        tool_calls = 0; activity_events = 0; stopped_for_loop = False
        def _stop_stream() -> None:
            try: out_q.put_nowait(None)
            except Exception: pass
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=2)
                except Exception:
                    pass
            try: proc.terminate()
            except Exception: pass
            try: proc.kill()
            except Exception: pass
        self._set_active_interrupt(_stop_stream)
        try:
            while time.monotonic() < deadline:
                try: line = out_q.get(timeout=min(0.4, max(0.05, deadline - time.monotonic())))
                except queue.Empty:
                    if proc.poll() is not None: break
                    continue
                if line is None: break
                raw_lines.append(line)
                if len(raw_lines) > 4000: raw_lines = raw_lines[-4000:]
                try: row = json.loads(line)
                except Exception: continue
                if not isinstance(row, dict): continue
                activity = mapper(row)
                if not activity: continue
                activity_events += 1
                if activity.kind == "tool" and activity.signature:
                    tool_calls += 1
                    signatures[activity.signature] = signatures.get(activity.signature, 0) + 1
                    # High enough not to break normal WP work; low enough to stop an
                    # obvious stuck loop before it consumes a large subscription window.
                    if signatures[activity.signature] >= 12 or tool_calls >= 120:
                        stopped_for_loop = True
                        activity = ProviderActivity("guard", "Tool loop stopped", f"{tool_calls} tool calls · repeated operation detected")
                        if on_event:
                            try: on_event(activity)
                            except Exception: pass
                        try: proc.terminate()
                        except Exception: pass
                        break
                if on_event:
                    try: on_event(activity)
                    except Exception: pass
            if proc.poll() is None:
                if time.monotonic() >= deadline:
                    try: proc.terminate()
                    except Exception: pass
                try: proc.wait(timeout=3)
                except Exception:
                    try: proc.kill()
                    except Exception: pass
            try: proc.wait(timeout=1)
            except Exception: pass
            return int(proc.returncode or 0), "".join(raw_lines), "".join(err_lines), tool_calls, activity_events, stopped_for_loop
        finally:
            self._set_active_interrupt(None)

    @staticmethod
    def _codex_session_config(
        mcp_server_overrides: Dict[str, Dict[str, Any]] | None = None,
        plugins_enabled: bool = False,
    ) -> Dict[str, Any]:
        config: Dict[str, Any] = {"features.plugins": bool(plugins_enabled)}
        # IMPORTANT: app-server request config uses dotted scalar/table-field
        # assignments. Passing a whole dict at ``mcp_servers.<name>`` is accepted
        # by the request parser but does not register its tools in the Codex turn.
        # Send every complete transport field separately so the server is both
        # valid and visible to the model. Relevant user servers are omitted so
        # they inherit the user's real Codex configuration unchanged.
        for raw, entry in (mcp_server_overrides or {}).items():
            name = str(raw or "").strip()
            if re.fullmatch(r"[A-Za-z0-9_.-]+", name) and isinstance(entry, dict):
                for raw_key, value in entry.items():
                    key = str(raw_key or "").strip()
                    if re.fullmatch(r"[A-Za-z0-9_.-]+", key):
                        config[f"mcp_servers.{name}.{key}"] = value
        return config

    @staticmethod
    def _codex_config_fingerprint(config: Dict[str, Any]) -> str:
        return json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _codex_native_activity(row: Dict[str, Any]) -> ProviderActivity | None:
        method = str(row.get("method") or "")
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        kind = str(item.get("type") or "").lower()
        if method == "turn/started":
            return ProviderActivity("reasoning", "Reasoning")
        if method == "item/started":
            if kind in {"commandexecution", "command_execution"}:
                cmd = item.get("command")
                if isinstance(cmd, list): cmd = " ".join(str(x) for x in cmd)
                detail = str(cmd or "").strip()[:180]
                low = detail.lower()
                label = "Running tests" if any(x in low for x in ("pytest", " test", "gradlew", "phpunit", "npm test", "pnpm test")) else "Running command"
                return ProviderActivity("tool", label, detail, "command", "command:" + detail)
            if kind in {"filechange", "file_change"}:
                changes = item.get("changes") if isinstance(item.get("changes"), list) else []
                path = ""
                if changes and isinstance(changes[0], dict): path = str(changes[0].get("path") or "")
                name = Path(path).name if path else "file"
                return ProviderActivity("tool", f"Writing code · {name}", path[:180], "file", "file:" + path)
            if "mcp" in kind or "tool" in kind:
                name = str(item.get("tool") or item.get("name") or "tool")
                return ProviderActivity("tool", f"Using {name}", str(item.get("server") or "")[:180], name, name)
            if "reason" in kind:
                return ProviderActivity("reasoning", "Reasoning")
        if method == "turn/completed":
            return ProviderActivity("status", "Preparing result")
        return None

    def _run_codex_app_server(
        self, prompt: str, model: str, effort: str, work: Path, timeout: int, write: bool,
        on_event: Callable[[ProviderActivity], None] | None, conversation_id: str,
        mcp_server_overrides: Dict[str, Dict[str, Any]] | None, plugins_enabled: bool, developer_instructions: str,
    ) -> ProviderRun:
        client = self._codex_app_client()
        q = client.subscribe()
        started = time.monotonic()
        requested = str(conversation_id or "").strip()
        config = self._codex_session_config(mcp_server_overrides, plugins_enabled)
        fingerprint = self._codex_config_fingerprint(config)
        thread_id = requested
        reused = False
        turn_accepted = False
        try:
            common: Dict[str, Any] = {
                "cwd": str(work),
                "approvalPolicy": "never",
                "sandbox": "workspaceWrite" if write else "readOnly",
                "config": config,
            }
            if model.strip(): common["model"] = model.strip()
            if developer_instructions: common["developerInstructions"] = developer_instructions
            if not thread_id:
                params = dict(common); params["ephemeral"] = False
                response = client.request("thread/start", params, timeout=15)
                thread = response.get("thread") if isinstance(response.get("thread"), dict) else {}
                thread_id = str(thread.get("id") or "")
                if not thread_id:
                    raise ProviderHarnessError("Codex app-server thread/start returned no thread id")
                self._codex_loaded_threads.add(thread_id)
                self._codex_thread_configs[thread_id] = fingerprint
            elif thread_id not in self._codex_loaded_threads or self._codex_thread_configs.get(thread_id) != fingerprint:
                params = dict(common); params["threadId"] = thread_id; params["excludeTurns"] = True
                response = client.request("thread/resume", params, timeout=15)
                thread = response.get("thread") if isinstance(response.get("thread"), dict) else {}
                actual = str(thread.get("id") or thread_id)
                reused = actual == thread_id
                thread_id = actual
                self._codex_loaded_threads.add(thread_id)
                self._codex_thread_configs[thread_id] = fingerprint
            else:
                reused = True

            turn_params: Dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": str(work),
                "approvalPolicy": "never",
                "model": model.strip() or None,
                "effort": effort,
            }
            response = client.request("turn/start", turn_params, timeout=15)
            turn = response.get("turn") if isinstance(response.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise ProviderHarnessError("Codex app-server turn/start returned no turn id")
            turn_accepted = True
            self._set_active_interrupt(
                lambda: client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
            )
            deadline = time.monotonic() + max(30, timeout)
            final_text = ""; status = ""; error = ""
            usage: Dict[str, Any] = {}; tools = 0; events = 0
            signatures: Dict[str, int] = {}
            completed_raw: Dict[str, Any] = {}
            while time.monotonic() < deadline:
                try: row = q.get(timeout=min(1.0, max(0.05, deadline-time.monotonic())))
                except queue.Empty: continue
                method = str(row.get("method") or "")
                params = row.get("params") if isinstance(row.get("params"), dict) else {}
                if str(params.get("threadId") or thread_id) != thread_id:
                    continue
                if method == "thread/tokenUsage/updated" and str(params.get("turnId") or "") == turn_id:
                    token_usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
                    last = token_usage.get("last") if isinstance(token_usage.get("last"), dict) else {}
                    usage = last or usage
                if method == "item/completed" and str(params.get("turnId") or "") == turn_id:
                    item = params.get("item") if isinstance(params.get("item"), dict) else {}
                    typ = str(item.get("type") or "").lower()
                    if typ in {"agentmessage", "agent_message"}:
                        final_text = str(item.get("text") or final_text)
                    if typ in {"commandexecution", "command_execution", "filechange", "file_change"} or "mcp" in typ or "tool" in typ:
                        tools += 1
                activity = self._codex_native_activity(row)
                if activity:
                    events += 1
                    if activity.kind == "tool":
                        sig = activity.signature or activity.label + ":" + activity.detail
                        signatures[sig] = signatures.get(sig, 0) + 1
                        if tools > 120 or signatures[sig] > 12:
                            try: client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
                            except Exception: pass
                            return ProviderRun(
                                "codex", model or "provider-default",
                                "Advertpreneur stopped the Codex run because tool activity repeated excessively.", 3,
                                time.monotonic()-started, conversation_id=thread_id, status="GUARD_STOP",
                                command=[self._which("codex") or "codex", "app-server", "turn/start"], reasoning_effort=effort,
                                tool_calls=tools, activity_events=events, provider_turns=1, session_reused=reused,
                            )
                    if on_event:
                        try: on_event(activity)
                        except Exception: pass
                if method == "turn/completed":
                    turn_row = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                    if str(turn_row.get("id") or "") != turn_id:
                        continue
                    completed_raw = turn_row
                    status = str(turn_row.get("status") or "completed")
                    err = turn_row.get("error")
                    if isinstance(err, dict): error = str(err.get("message") or err)
                    elif err: error = str(err)
                    # Newer app-server responses can also summarize completed items.
                    for item in turn_row.get("items") or []:
                        if isinstance(item, dict) and str(item.get("type") or "").lower() in {"agentmessage", "agent_message"}:
                            final_text = str(item.get("text") or final_text)
                    break
            else:
                try: client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
                except Exception: pass
                raise ProviderHarnessError("Codex app-server turn timed out after it had started; task was interrupted and was not retried")
            if not status:
                raise ProviderHarnessError("Codex app-server ended without turn/completed after starting a turn")
            rc = 0 if status.lower() == "completed" else 1
            return ProviderRun(
                "codex", model or "provider-default", final_text.strip(), rc, time.monotonic()-started,
                int(usage.get("inputTokens") or 0), int(usage.get("outputTokens") or 0),
                int(usage.get("cachedInputTokens") or 0), int(usage.get("reasoningOutputTokens") or 0),
                int(usage.get("totalTokens") or 0), thread_id, status.upper(), error,
                [self._which("codex") or "codex", "app-server", "turn/start"], completed_raw, effort, tools, events,
                provider_turns=1, session_reused=reused,
            )
        except Exception as exc:
            if turn_accepted:
                if isinstance(exc, ProviderHarnessError): raise
                raise ProviderHarnessError(f"Codex app-server turn failed after start; not retried: {exc}") from exc
            raise
        finally:
            self._set_active_interrupt(None)
            client.unsubscribe(q)

    @staticmethod
    def _codex_mcp_overrides(
        disabled_mcp_servers: List[str] | None = None,
        mcp_server_states: Dict[str, bool] | None = None,
    ) -> List[str]:
        argv: List[str] = []
        states: Dict[str, bool] = dict(mcp_server_states or {})
        for raw in disabled_mcp_servers or []:
            name = str(raw or "").strip()
            if name:
                states[name] = False
        for raw, enabled in states.items():
            name = str(raw or "").strip()
            if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                continue
            escaped = name.replace('"', '\\"')
            argv += ["-c", f'mcp_servers."{escaped}".enabled={"true" if enabled else "false"}']
        return argv

    def run_codex(
        self, prompt: str, model: str = "", effort: str = "medium", *, cwd: Path | None = None,
        timeout: int = 600, write: bool = False, on_event: Callable[[ProviderActivity], None] | None = None,
        conversation_id: str = "", disabled_mcp_servers: List[str] | None = None,
        mcp_server_states: Dict[str, bool] | None = None,
        mcp_server_overrides: Dict[str, Dict[str, Any]] | None = None, plugins_enabled: bool = False,
        developer_instructions: str = "",
    ) -> ProviderRun:
        work = (cwd or self.project).resolve()
        effort = self.normalize_effort(effort)
        command = self._which("codex")
        requested_thread = str(conversation_id or "").strip()
        # Partial -c mcp_servers.<name>.enabled overrides can erase transport
        # fields in current Codex. Native app-server receives full safe overrides;
        # compatibility fallbacks intentionally leave the user's MCP config intact.
        mcp_overrides: List[str] = []

        # Preferred path: one long-lived native app-server connection with one loaded
        # Codex thread and many turn/start calls. This avoids the known exec-resume
        # bootstrap reinjection/context-growth behavior and preserves exact per-turn usage.
        if command and on_event is not None:
            try:
                return self._run_codex_app_server(
                    prompt, model, effort, work, timeout, write, on_event, requested_thread,
                    mcp_server_overrides, plugins_enabled, developer_instructions,
                )
            except ProviderHarnessError as exc:
                # Safe compatibility fallback is allowed only when setup/thread-start
                # failed before a model turn was accepted. Post-turn failures from the
                # native driver explicitly say "not retried" to prevent duplicate spend.
                if "not retried" in str(exc).lower() or "after it had started" in str(exc).lower():
                    raise
            except Exception:
                pass

        cli_prompt = prompt
        if not requested_thread and developer_instructions:
            cli_prompt = developer_instructions.strip() + "\n\nUSER TASK:\n" + prompt

        def cli_argv() -> List[str]:
            if requested_thread:
                argv = [command, "exec", "resume", requested_thread, "--json", "--skip-git-repo-check"]
                argv += ["-c", f'sandbox_mode="{"workspace-write" if write else "read-only"}"']
            else:
                # Persist the native Codex thread. ADP used to pass --ephemeral here,
                # which forced every tiny follow-up to pay a fresh provider bootstrap.
                argv = [command, "exec", "--json", "--sandbox", ("workspace-write" if write else "read-only"), "--skip-git-repo-check"]
            if model.strip(): argv += ["--model", model.strip()]
            argv += ["-c", f'model_reasoning_effort="{effort}"']
            argv += ["-c", f'features.plugins={"true" if plugins_enabled else "false"}']
            argv += mcp_overrides
            argv += [cli_prompt]
            return argv

        # Prefer the installed official CLI for live activity. Non-ephemeral threads
        # are resumed by exact ID on later ADP turns, preserving provider cache/context.
        if command:
            argv = cli_argv()
            started = time.monotonic()
            if on_event is not None:
                rc, stdout, stderr, tools, events, looped = self._stream_json_command(
                    argv, work, timeout, "codex", on_event, self._activity_from_codex_row
                )
            else:
                p = self._run_capture(argv, work, timeout=timeout)
                rc, stdout, stderr, tools, events, looped = int(p.returncode), p.stdout or "", p.stderr or "", 0, 0, False
            text, usage, thread_id = self._codex_parse(stdout)
            turns = self._codex_turn_count(stdout)
            if not text and not looped: text = stdout.strip()
            if looped:
                text = "Advertpreneur stopped the Codex run because the same tool operation repeated excessively. Review the task/tool trace before retrying."
                rc = rc or 3
            reused = bool(requested_thread and thread_id == requested_thread)
            status = "GUARD_STOP" if looped else ("SUCCESS" if rc == 0 else "ERROR")
            if requested_thread and thread_id and thread_id != requested_thread and rc == 0:
                # Codex can silently create a new thread for a stale/missing ID. Do
                # not pretend continuity survived; surface it and adopt the new ID.
                status = "SESSION_RESTARTED"
                reused = False
                stderr = (stderr + f"\nAdvertpreneur: requested Codex thread {requested_thread} was not resumed; provider returned {thread_id}.").strip()
            return ProviderRun(
                "codex", model or "provider-default", text, rc, time.monotonic()-started,
                int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
                int(usage.get("cache_read_tokens") or usage.get("cached_input_tokens") or 0), 0,
                int(usage.get("total_tokens") or 0), thread_id,
                status, stderr.strip(), argv, stdout, effort, tools, events,
                provider_turns=turns, session_reused=reused,
            )

        # SDK fallback for machines without a standalone Codex CLI. Use persisted
        # threads there too; never create an ephemeral thread for ordinary ADP work.
        if self._codex_sdk_available():
            started = time.monotonic()
            try:
                from openai_codex import Codex, Sandbox
                with Codex() as codex:
                    sandbox = Sandbox.workspace_write if write else Sandbox.read_only
                    config = {"model_reasoning_effort": effort, "features.plugins": bool(plugins_enabled)}
                    states = dict(mcp_server_states or {})
                    for name in disabled_mcp_servers or []:
                        states[str(name or "")] = False
                    for name, enabled in states.items():
                        if re.fullmatch(r"[A-Za-z0-9_.-]+", str(name or "")):
                            config[f'mcp_servers.{name}.enabled'] = bool(enabled)
                    if requested_thread:
                        thread = codex.thread_resume(requested_thread, cwd=str(work), model=model.strip() or None, config=config, sandbox=sandbox, developer_instructions=developer_instructions or None)
                    else:
                        thread = codex.thread_start(cwd=str(work), ephemeral=False, model=model.strip() or None, config=config, sandbox=sandbox, developer_instructions=developer_instructions or None)
                    result = thread.run(prompt, effort=effort, model=model.strip() or None, sandbox=sandbox)
                usage = self._usage_from_obj(getattr(result, "usage", None))
                status_obj = getattr(result, "status", "SUCCESS")
                status = str(getattr(status_obj, "value", status_obj) or "SUCCESS")
                err = getattr(result, "error", None)
                thread_id = str(getattr(thread, "id", "") or requested_thread)
                return ProviderRun(
                    provider="codex", model=model or "provider-default", text=str(getattr(result, "final_response", "") or "").strip(),
                    returncode=0 if status.upper() not in {"ERROR", "FAILED", "CANCELED", "INTERRUPTED"} else 1,
                    duration_seconds=time.monotonic()-started, input_tokens=usage.get("input_tokens",0), output_tokens=usage.get("output_tokens",0),
                    cache_read_tokens=usage.get("cache_read_tokens",0), total_tokens=usage.get("total_tokens",0),
                    conversation_id=thread_id, status=status, stderr=str(err or ""), command=["openai_codex","thread.run"], raw=result,
                    reasoning_effort=effort, provider_turns=1, session_reused=bool(requested_thread and thread_id == requested_thread),
                )
            except Exception as exc:
                raise ProviderHarnessError(f"Codex SDK run failed: {exc}") from exc
        self.ensure_runtime("codex")
        raise ProviderHarnessError("Official Codex runtime is unavailable")

    def release_warm_sessions(self, preserve_provider: str = "", preserve_session_key: str = "") -> int:
        """Close idle provider helpers without deleting saved provider thread IDs.

        ``preserve_provider`` is used by Resource Guard before a task: under RAM
        pressure it can drop unrelated warm helpers while keeping the provider that
        is about to reuse its cached conversation. Active work never calls this.
        """
        preserve_provider = str(preserve_provider or "").lower()
        preserve_session_key = str(preserve_session_key or "")
        count = 0
        for key, driver in list(self._agy_streams.items()):
            if preserve_provider == "agy" and (not preserve_session_key or str(key) == preserve_session_key):
                continue
            self._agy_streams.pop(key, None)
            count += 1
            try: driver.close()
            except Exception: pass
        if self._codex_app is not None and preserve_provider != "codex":
            count += 1
            try: self._codex_app.close()
            except Exception: pass
            self._codex_app = None
            self._codex_loaded_threads.clear(); self._codex_thread_configs.clear()
        return count

    def warm_session_counts(self) -> dict[str, int]:
        codex = int(bool(self._codex_app is not None and getattr(self._codex_app, "proc", None) and self._codex_app.proc.poll() is None))
        agy = sum(1 for d in self._agy_streams.values() if getattr(d, "proc", None) is not None and d.proc.poll() is None)
        return {"codex": codex, "agy": agy}

    def run_agy(
        self, prompt: str, model: str = "", effort: str = "medium", *, cwd: Path | None = None,
        timeout: int = 600, write: bool = False, on_event: Callable[[ProviderActivity], None] | None = None,
        conversation_id: str = "", session_key: str = "",
    ) -> ProviderRun:
        command = self._which("agy")
        if not command:
            raise ProviderHarnessError("Official Antigravity runtime is not installed. Use /providers login agy; ADP installs it automatically.")
        work=(cwd or self.project).resolve(); effort=self.agy_effective_effort(model, effort)
        prior = str(conversation_id or "").strip()

        # AGY 1.1.19+ explicitly supports a persistent stream-json stdin driver.
        # Keep one process per ADP session so follow-ups reuse the warmed conversation
        # and cache instead of paying startup/context bootstrap every time.
        if session_key:
            key = str(session_key)
            driver = self._agy_streams.get(key)
            if driver and not driver.compatible(work, model.strip(), effort, write):
                prior = driver.conversation_id or prior
                try: driver.close()
                except Exception: pass
                self._agy_streams.pop(key, None)
                driver = None
            if driver is None:
                # Warm sessions save quota, but ADP must remain lightweight. Evict
                # drivers idle for 30 minutes and cap the resident pool at one per ADP process.
                now = time.monotonic()
                for stale_key, stale in list(self._agy_streams.items()):
                    if now - float(getattr(stale, "last_used_at", now)) >= 1800:
                        self._agy_streams.pop(stale_key, None)
                        try: stale.close()
                        except Exception: pass
                while len(self._agy_streams) >= 1:
                    old_key, old = min(self._agy_streams.items(), key=lambda item: float(getattr(item[1], "last_used_at", 0.0)))
                    self._agy_streams.pop(old_key, None)
                    try: old.close()
                    except Exception: pass
                driver = _AgyStreamDriver(command, work, model.strip(), effort, write, prior, timeout=max(timeout, 120))
                self._agy_streams[key] = driver
            try:
                self._set_active_interrupt(driver.close)
                return driver.ask(prompt, on_event, self._activity_from_agy_row, timeout)
            except Exception:
                # Drop a broken driver. The next turn can resume from the last known
                # conversation ID rather than retaining a dead process.
                try: driver.close()
                except Exception: pass
                self._agy_streams.pop(key, None)
                raise
            finally:
                self._set_active_interrupt(None)

        # Compatibility fallback: resume by explicit conversation ID even when a
        # persistent driver cannot be held (specialist/one-shot invocations).
        fmt = "stream-json" if on_event is not None else "json"
        argv=[command,"-p",prompt,"--output-format",fmt,"--print-timeout",f"{max(1,timeout//60)}m","--sandbox"]
        if write: argv += ["--mode=accept-edits"]
        if model.strip(): argv += ["--model",model.strip()]
        if self.agy_uses_effort_flag(model): argv += ["--effort",effort]
        if prior: argv += ["--conversation", prior]
        started=time.monotonic()
        if on_event is not None:
            rc,stdout,stderr,tools,events,looped=self._stream_json_command(argv,work,timeout+20,"agy",on_event,self._activity_from_agy_row)
            final: Dict[str,Any]={}
            for line in reversed(stdout.splitlines()):
                try:
                    row=json.loads(line)
                    if isinstance(row,dict) and str(row.get("event") or row.get("type") or "").lower() in {"result","completed","done"}:
                        final=row.get("result") if isinstance(row.get("result"),dict) else row; break
                except Exception: pass
            usage=final.get("usage") if isinstance(final.get("usage"),dict) else {}
            text=str(final.get("response") or final.get("result") or final.get("text") or "").strip()
            if looped:
                text="Advertpreneur stopped the AGY run because the same tool operation repeated excessively. Review the task/tool trace before retrying."; rc=rc or 3
            cid=str(final.get("conversation_id") or "")
            return ProviderRun("agy",model or "provider-default",text,rc,float(final.get("duration_seconds") or (time.monotonic()-started)),
                int(usage.get("input_tokens") or 0),int(usage.get("output_tokens") or 0),int(usage.get("cache_read_tokens") or 0),int(usage.get("thinking_tokens") or 0),int(usage.get("total_tokens") or 0),cid,
                "GUARD_STOP" if looped else str(final.get("status") or ("SUCCESS" if rc==0 else "ERROR")),stderr.strip(),argv,final,effort,tools,events,
                provider_turns=1, session_reused=bool(prior and cid == prior), cache_read_additive=True)
        p=self._run_capture(argv,work,timeout=timeout+20)
        try: row=json.loads((p.stdout or "{}").strip().splitlines()[-1])
        except Exception: row={}
        usage=row.get("usage") if isinstance(row.get("usage"),dict) else {}
        cid=str(row.get("conversation_id") or "")
        return ProviderRun("agy",model or "provider-default",str(row.get("response") or (p.stdout or "")).strip(),int(p.returncode),float(row.get("duration_seconds") or (time.monotonic()-started)),
            int(usage.get("input_tokens") or 0),int(usage.get("output_tokens") or 0),int(usage.get("cache_read_tokens") or 0),int(usage.get("thinking_tokens") or 0),int(usage.get("total_tokens") or 0),cid,str(row.get("status") or ("SUCCESS" if p.returncode==0 else "ERROR")),(p.stderr or "").strip(),argv,row,effort,
            provider_turns=1, session_reused=bool(prior and cid == prior), cache_read_additive=True)

    def run(
        self, provider: str, prompt: str, model: str = "", effort: str = "medium", *, cwd: Path | None = None,
        timeout: int = 600, write: bool = False, on_event: Callable[[ProviderActivity], None] | None = None,
        conversation_id: str = "", session_key: str = "", disabled_mcp_servers: List[str] | None = None,
        mcp_server_states: Dict[str, bool] | None = None,
        mcp_server_overrides: Dict[str, Dict[str, Any]] | None = None, plugins_enabled: bool = False,
        developer_instructions: str = "",
    ) -> ProviderRun:
        provider = provider.lower().strip()
        if provider == "codex":
            return self.run_codex(prompt, model, effort, cwd=cwd, timeout=timeout, write=write, on_event=on_event,
                                  conversation_id=conversation_id, disabled_mcp_servers=disabled_mcp_servers,
                                  mcp_server_states=mcp_server_states, mcp_server_overrides=mcp_server_overrides,
                                  plugins_enabled=plugins_enabled, developer_instructions=developer_instructions)
        if provider == "agy":
            return self.run_agy(prompt, model, effort, cwd=cwd, timeout=timeout, write=write, on_event=on_event,
                                conversation_id=conversation_id, session_key=session_key)
        raise ProviderHarnessError(f"Unknown external provider: {provider}")

    def run_packet(self, provider: str, packet: str, instruction: str, model: str = "", effort: str = "medium", timeout: int = 600) -> ProviderRun:
        """Run a specialist against a bounded local packet outside the live project.

        A persistent unique directory is used rather than ``TemporaryDirectory``.
        On Windows, provider subprocesses can briefly retain file/directory handles
        after exit; deleting a TemporaryDirectory in ``__exit__`` then raised
        WinError 32 and incorrectly turned successful AGY calls into failures.
        Cleanup is now best-effort and never masks the provider result.
        """
        root = Path(tempfile.mkdtemp(prefix="adp-provider-", dir=str(self.run_dir)))
        try:
            (root / "ADP_PACKET.md").write_text(packet, encoding="utf-8")
            prompt = (
                "You are a specialist reviewer/reasoner invoked by Advertpreneur CLI. "
                "Work read-only. The only supplied project evidence is ADP_PACKET.md. "
                "Do not invent unprovided source facts.\n\n"
                f"Instruction: {instruction}\n\nRead ADP_PACKET.md and return a concise evidence-based answer."
            )
            return self.run(provider, prompt, model, effort, cwd=root, timeout=timeout)
        finally:
            # Do not let Windows file locks destroy a completed provider response.
            # Stale run dirs are pruned on future harness startups.
            try:
                shutil.rmtree(root, ignore_errors=True)
            except Exception:
                pass

    def auth_test(self, provider: str, model: str = "", effort: str = "low", timeout: int = 90) -> ProviderRun:
        return self.run_packet(provider, "# Authentication smoke\nNo project data.\n", "Reply exactly ADP_AUTH_OK.", model=model, effort=effort or "low", timeout=timeout)
