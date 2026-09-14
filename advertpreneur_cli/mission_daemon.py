from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .missions import MissionStore


class MissionDaemon:
    """Single-user authenticated loopback owner for durable mission state."""

    protocol_version = 1

    def __init__(self, project: Path, port: int = 0) -> None:
        self.project = Path(project).resolve()
        self.port = int(port)
        self.store = MissionStore(self.project)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.token = ""

    @property
    def descriptor_path(self) -> Path:
        return self.project / ".advertpreneur" / "mission-daemon.json"

    def _write_descriptor(self) -> None:
        target = self.descriptor_path
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"protocol_version": self.protocol_version, "port": self.port, "token": self.token, "pid": os.getpid()}), encoding="utf-8")
        os.replace(temporary, target)

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _handler(self):
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _respond(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self) -> bool:
                return secrets.compare_digest(self.headers.get("X-ADP-Mission-Token", ""), daemon.token)

            def _body(self) -> dict[str, Any]:
                size = int(self.headers.get("Content-Length") or 0)
                try:
                    value = json.loads(self.rfile.read(size).decode("utf-8"))
                    return value if isinstance(value, dict) else {}
                except Exception:
                    return {}

            def do_GET(self) -> None:
                if not self._authorized():
                    self._respond(401, {"error": "unauthorized"}); return
                if self.path == "/health":
                    self._respond(200, {"protocol_version": daemon.protocol_version, "status": "ready"}); return
                if self.path.startswith("/missions/"):
                    try:
                        mission = daemon.store.load(self.path.rsplit("/", 1)[-1])
                        self._respond(200, {"id": mission.id, "request": mission.request, "status": mission.status, "steps": [step.__dict__ for step in mission.steps]})
                    except KeyError:
                        self._respond(404, {"error": "unknown mission"})
                    return
                self._respond(404, {"error": "not found"})

            def do_POST(self) -> None:
                if not self._authorized():
                    self._respond(401, {"error": "unauthorized"}); return
                if self.path != "/missions":
                    self._respond(404, {"error": "not found"}); return
                body = self._body()
                mission = daemon.store.create(str(body.get("request") or ""), list(body.get("steps") or []))
                self._respond(201, {"id": mission.id, "request": mission.request, "status": mission.status})

        return Handler

    def start(self) -> bool:
        if self.descriptor_path.exists():
            try:
                existing = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
                if self._pid_running(int(existing.get("pid") or 0)):
                    return False
            except Exception:
                pass
            try:
                self.descriptor_path.unlink()
            except FileNotFoundError:
                pass
        self.token = secrets.token_urlsafe(32)
        self.store.recover_interrupted()
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
        self.port = int(self._server.server_address[1])
        self._write_descriptor()
        self._thread = threading.Thread(target=self._server.serve_forever, name="AdvertpreneurMissionDaemon", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        try:
            self.descriptor_path.unlink()
        except FileNotFoundError:
            pass
