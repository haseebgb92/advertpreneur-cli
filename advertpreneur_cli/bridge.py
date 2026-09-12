from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .bridge_server import DEFAULT_PORT, PROTOCOL_VERSION, SERVICE_NAME


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeDispatch:
    event_id: str
    paired: bool
    conversation_id: str = ""
    conversation_url: str = ""


@dataclass(frozen=True)
class BridgeReply:
    event_id: str
    text: str
    conversation_id: str

    @property
    def done(self) -> bool:
        return self.text.strip().upper() == "ADP_BRIDGE_DONE"


class BridgeClient:
    def __init__(self, app_dir: Path, port: int = DEFAULT_PORT, auto_start: bool = True) -> None:
        self.app_dir = app_dir
        self.port = int(port)
        self.auto_start = bool(auto_start)
        self.base = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen | None = None

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def new_cli_token() -> str:
        return secrets.token_urlsafe(24)

    def extension_path(self) -> Path:
        """Return a stable per-user unpacked extension folder and refresh it from the package when needed."""
        source = Path(__file__).resolve().parent / "browser_extension"
        target = self.app_dir / "browser-extension"
        try:
            self.app_dir.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copytree(source, target)
            else:
                for s_file in source.glob("*"):
                    t_file = target / s_file.name
                    if s_file.is_file():
                        shutil.copy2(s_file, t_file)
                    elif s_file.is_dir():
                        shutil.copytree(s_file, t_file, dirs_exist_ok=True)
            return target
        except Exception:
            return source

    def _creationflags(self) -> int:
        if sys.platform == "win32":
            flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            return flags
        return 0

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 3.0) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-Advertpreneur-Bridge": "cli"},
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                body = json.loads(raw or "{}")
                return dict(body) if isinstance(body, dict) else {}
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
                detail = body.get("error") or str(exc)
            except Exception:
                detail = str(exc)
            raise BridgeError(str(detail)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BridgeError(str(exc)) from exc

    def health(self) -> bool:
        try:
            body = self._request("GET", "/health", timeout=0.6)
            if not (body.get("ok") is True and body.get("service") == SERVICE_NAME and int(body.get("protocol") or 0) == PROTOCOL_VERSION):
                return False
            sess = self._request("GET", "/v1/public/sessions", timeout=0.6)
            return "sessions" in sess
        except Exception:
            return False

    def ensure_server(self) -> None:
        if self.health():
            return
        if not self.auto_start:
            raise BridgeError("Browser Bridge broker is not running")
        self.app_dir.mkdir(parents=True, exist_ok=True)
        pkg_root = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{pkg_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else pkg_root
        cmd = [sys.executable, "-m", "advertpreneur_cli.bridge_server", "--port", str(self.port)]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=pkg_root,
                env=env,
                creationflags=self._creationflags(),
                start_new_session=(sys.platform != "win32"),
            )
        except Exception as exc:
            raise BridgeError(f"Could not start local Browser Bridge broker: {exc}") from exc
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(0.1)
        raise BridgeError(f"Browser Bridge broker did not start on 127.0.0.1:{self.port}")

    def register(
        self,
        session_id: str,
        session_name: str,
        project: str,
        model: str,
        cli_token: str,
        pair_token: str,
    ) -> dict[str, Any]:
        self.ensure_server()
        return self._request("POST", "/v1/cli/register", {
            "session_id": session_id,
            "session_name": session_name,
            "project": project,
            "model": model,
            "cli_token": cli_token,
            "pair_token": pair_token,
        })

    def rotate_code(self, session_id: str, cli_token: str) -> str:
        self.ensure_server()
        body = self._request("POST", "/v1/cli/rotate-code", {"session_id": session_id, "cli_token": cli_token})
        return str(body.get("pair_code") or "")

    def status(self, session_id: str, cli_token: str) -> dict[str, Any]:
        self.ensure_server()
        qs = urlencode({"session_id": session_id, "cli_token": cli_token})
        return self._request("GET", "/v1/cli/status?" + qs)

    def update_status(self, session_id: str, cli_token: str, status: str, detail: str = "", model: str = "") -> None:
        try:
            self._request("POST", "/v1/cli/status", {
                "session_id": session_id,
                "cli_token": cli_token,
                "status": status,
                "detail": detail,
                "model": model,
            }, timeout=1.0)
        except Exception:
            pass

    def publish(self, session_id: str, cli_token: str, payload: dict[str, Any]) -> BridgeDispatch:
        self.ensure_server()
        body = self._request("POST", "/v1/cli/publish", {
            "session_id": session_id,
            "cli_token": cli_token,
            "payload": payload,
        })
        return BridgeDispatch(
            event_id=str(body.get("event_id") or ""),
            paired=bool(body.get("paired", False)),
            conversation_id=str(body.get("conversation_id") or ""),
            conversation_url=str(body.get("conversation_url") or ""),
        )

    def poll_reply(self, session_id: str, cli_token: str, event_id: str) -> BridgeReply | None:
        self.ensure_server()
        qs = urlencode({"session_id": session_id, "cli_token": cli_token, "event_id": event_id})
        try:
            body = self._request("GET", "/v1/cli/reply?" + qs, timeout=2.0)
        except BridgeError:
            return None
        row = body.get("reply")
        if not isinstance(row, dict):
            return None
        return BridgeReply(
            event_id=str(row.get("event_id") or event_id),
            text=str(row.get("text") or ""),
            conversation_id=str(row.get("conversation_id") or ""),
        )

    def wait_for_reply(
        self,
        session_id: str,
        cli_token: str,
        event_id: str,
        timeout_seconds: int = 600,
        poll_seconds: float = 1.0,
    ) -> BridgeReply | None:
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        while time.monotonic() < deadline:
            try:
                reply = self.poll_reply(session_id, cli_token, event_id)
                if reply:
                    return reply
            except BridgeError:
                # Broker may be briefly restarting; keep the wait bounded and retry.
                pass
            time.sleep(max(0.2, float(poll_seconds)))
        return None

    def browser_status(self) -> dict[str, Any]:
        self.ensure_server()
        return self._request("GET", "/v1/cli/browser-status", timeout=1.5)

    def browser_command(self, action: str, args: dict[str, Any] | None = None, timeout: float = 45.0) -> dict[str, Any]:
        self.ensure_server()
        queued = self._request("POST", "/v1/cli/browser-command", {"action": action, "args": args or {}}, timeout=2.0)
        command_id = str(queued.get("command_id") or "")
        provider_id = str(queued.get("provider_id") or "")
        if not command_id:
            raise BridgeError("Browser extension did not accept the command")
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            wait = min(20.0, max(1.0, deadline - time.monotonic()))
            qs = urlencode({"command_id": command_id, "provider_id": provider_id, "wait": f"{wait:.1f}"})
            body = self._request("GET", "/v1/cli/browser-result?" + qs, timeout=wait + 3.0)
            row = body.get("result")
            if isinstance(row, dict):
                if not row.get("ok"):
                    raise BridgeError(str(row.get("error") or "Browser extension command failed"))
                result = row.get("result")
                return dict(result) if isinstance(result, dict) else {"value": result}
        raise BridgeError(f"Browser extension command timed out after {int(timeout)}s")

    def browser_learn_events(self, provider_id: str = "") -> list[dict[str, Any]]:
        self.ensure_server()
        qs = urlencode({"provider_id": provider_id}) if provider_id else ""
        row = self._request("GET", "/v1/cli/browser-learn-events" + (("?" + qs) if qs else ""), timeout=2.0)
        events = row.get("events") if isinstance(row, dict) else []
        return list(events) if isinstance(events, list) else []

    def unregister(self, session_id: str, cli_token: str) -> None:
        try:
            self._request("POST", "/v1/cli/unregister", {"session_id": session_id, "cli_token": cli_token}, timeout=1.0)
        except Exception:
            pass
