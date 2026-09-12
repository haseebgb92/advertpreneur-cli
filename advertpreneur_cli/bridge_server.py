from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SERVICE_NAME = "advertpreneur-browser-bridge"
PROTOCOL_VERSION = 3
DEFAULT_PORT = 8765


def _now() -> float:
    return time.time()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _pair_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


@dataclass
class BridgeSession:
    session_id: str
    session_name: str
    project: str
    model: str
    cli_token: str
    pair_token: str
    pair_code: str = field(default_factory=_pair_code)
    conversation_id: str = ""
    conversation_url: str = ""
    status: str = "Idle"
    detail: str = ""
    created_at: float = field(default_factory=_now)
    last_seen: float = field(default_factory=_now)
    outbound: list[dict[str, Any]] = field(default_factory=list)
    inbound: list[dict[str, Any]] = field(default_factory=list)

    @property
    def paired(self) -> bool:
        return bool(self.conversation_id)


@dataclass
class BrowserProvider:
    provider_id: str
    token: str
    label: str = "Edge Browser Bridge"
    version: str = ""
    user_agent: str = ""
    last_seen: float = field(default_factory=_now)
    commands: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    learn_events: list[dict[str, Any]] = field(default_factory=list)
    progress: dict[str, Any] = field(default_factory=dict)


class BrokerState:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.lock = threading.RLock()
        self.browser_condition = threading.Condition(self.lock)
        self.sessions: dict[str, BridgeSession] = {}
        self.browsers: dict[str, BrowserProvider] = {}
        self.persisted_pairs: dict[str, dict[str, Any]] = self._load_pairs()

    def _load_pairs(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_pairs(self) -> None:
        payload: dict[str, dict[str, Any]] = {}
        for sid, session in self.sessions.items():
            if session.conversation_id:
                payload[sid] = {
                    "pair_token_hash": _hash_token(session.pair_token),
                    "conversation_id": session.conversation_id,
                    "conversation_url": session.conversation_url,
                    "updated_at": _now(),
                }
        # Retain valid pairs for sessions that are temporarily not running.
        for sid, row in self.persisted_pairs.items():
            payload.setdefault(sid, row)
        self.persisted_pairs = payload
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def _require(self, session_id: str) -> BridgeSession:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError("bridge session is not registered")
        return session

    @staticmethod
    def _matches(candidate: str, expected: str) -> bool:
        return bool(candidate and expected and secrets.compare_digest(candidate, expected))

    def register(self, data: dict[str, Any]) -> dict[str, Any]:
        sid = str(data.get("session_id") or "").strip()
        cli_token = str(data.get("cli_token") or "").strip()
        pair_token = str(data.get("pair_token") or "").strip()
        if not sid or not cli_token or not pair_token:
            raise ValueError("session_id, cli_token and pair_token are required")
        with self.lock:
            existing = self.sessions.get(sid)
            if existing and not self._matches(cli_token, existing.cli_token):
                # A restarted CLI process may reclaim its own persisted session using
                # the stable per-session pair token. A different token cannot hijack it.
                if not self._matches(pair_token, existing.pair_token):
                    raise PermissionError("CLI token mismatch")
                existing.cli_token = cli_token
            if existing:
                session = existing
                session.session_name = str(data.get("session_name") or session.session_name)
                session.project = str(data.get("project") or session.project)
                session.model = str(data.get("model") or session.model)
                session.pair_token = pair_token
                session.last_seen = _now()
            else:
                session = BridgeSession(
                    session_id=sid,
                    session_name=str(data.get("session_name") or sid),
                    project=str(data.get("project") or ""),
                    model=str(data.get("model") or ""),
                    cli_token=cli_token,
                    pair_token=pair_token,
                )
                saved = self.persisted_pairs.get(sid) or {}
                if saved and saved.get("pair_token_hash") == _hash_token(pair_token):
                    session.conversation_id = str(saved.get("conversation_id") or "")
                    session.conversation_url = str(saved.get("conversation_url") or "")
                self.sessions[sid] = session
            return {
                "session_id": sid,
                "pair_code": session.pair_code,
                "paired": session.paired,
                "conversation_id": session.conversation_id,
                "conversation_url": session.conversation_url,
            }

    def rotate_code(self, sid: str, cli_token: str) -> str:
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                session = BridgeSession(
                    session_id=sid,
                    session_name=sid,
                    project="",
                    model="",
                    cli_token=cli_token,
                    pair_token=secrets.token_urlsafe(32),
                )
                saved = self.persisted_pairs.get(sid) or {}
                if saved:
                    session.conversation_id = str(saved.get("conversation_id") or "")
                    session.conversation_url = str(saved.get("conversation_url") or "")
                self.sessions[sid] = session
            elif not self._matches(cli_token, session.cli_token):
                raise PermissionError("CLI token mismatch")
            session.pair_code = _pair_code()
            session.last_seen = _now()
            return session.pair_code

    def public_sessions(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = []
            cutoff = _now() - 6 * 3600
            for session in self.sessions.values():
                if session.last_seen < cutoff:
                    continue
                rows.append({
                    "session_id": session.session_id,
                    "session_name": session.session_name,
                    "project": session.project,
                    "model": session.model,
                    "paired": session.paired,
                    "status": session.status,
                    "detail": session.detail,
                    "last_seen": session.last_seen,
                })
            rows.sort(key=lambda r: r["last_seen"], reverse=True)
            return rows

    def pair(self, data: dict[str, Any]) -> dict[str, Any]:
        sid = str(data.get("session_id") or "").strip()
        code = str(data.get("pair_code") or "").strip()
        conversation_id = str(data.get("conversation_id") or "").strip()
        conversation_url = str(data.get("conversation_url") or "").strip()
        replace = bool(data.get("replace", False))
        if not sid or not code or not conversation_id:
            raise ValueError("session_id, pair_code and conversation_id are required")
        with self.lock:
            session = self._require(sid)
            if not self._matches(code, session.pair_code):
                raise PermissionError("Pair code is invalid")
            if session.conversation_id and session.conversation_id != conversation_id and not replace:
                raise RuntimeError("CLI session is already linked to another ChatGPT conversation")
            session.conversation_id = conversation_id
            session.conversation_url = conversation_url
            session.last_seen = _now()
            self._save_pairs()
            return {
                "session_id": sid,
                "pair_token": session.pair_token,
                "conversation_id": session.conversation_id,
                "conversation_url": session.conversation_url,
            }

    def _auth_extension(self, sid: str, pair_token: str, conversation_id: str) -> BridgeSession:
        session = self.sessions.get(sid)
        if not session:
            saved = self.persisted_pairs.get(sid) or {}
            if saved and saved.get("pair_token_hash") == _hash_token(pair_token):
                session = BridgeSession(
                    session_id=sid,
                    session_name=sid,
                    project="",
                    model="",
                    cli_token=secrets.token_urlsafe(24),
                    pair_token=pair_token,
                    conversation_id=str(saved.get("conversation_id") or ""),
                    conversation_url=str(saved.get("conversation_url") or ""),
                )
                self.sessions[sid] = session
            else:
                session = self._require(sid)
        if not self._matches(pair_token, session.pair_token):
            raise PermissionError("Pair token mismatch")
        if session.conversation_id and session.conversation_id != conversation_id:
            raise PermissionError("This CLI session is paired to a different ChatGPT conversation")
        if not session.conversation_id:
            raise PermissionError("CLI session is not paired")
        session.last_seen = _now()
        return session

    def unpair(self, data: dict[str, Any]) -> None:
        sid = str(data.get("session_id") or "").strip()
        pair_token = str(data.get("pair_token") or "").strip()
        conversation_id = str(data.get("conversation_id") or "").strip()
        with self.lock:
            session = self._auth_extension(sid, pair_token, conversation_id)
            session.conversation_id = ""
            session.conversation_url = ""
            self.persisted_pairs.pop(sid, None)
            self._save_pairs()

    def publish(self, data: dict[str, Any]) -> dict[str, Any]:
        sid = str(data.get("session_id") or "").strip()
        cli_token = str(data.get("cli_token") or "").strip()
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                session = BridgeSession(
                    session_id=sid,
                    session_name=sid,
                    project="",
                    model="",
                    cli_token=cli_token,
                    pair_token=secrets.token_urlsafe(32),
                )
                saved = self.persisted_pairs.get(sid) or {}
                if saved:
                    session.conversation_id = str(saved.get("conversation_id") or "")
                    session.conversation_url = str(saved.get("conversation_url") or "")
                self.sessions[sid] = session
            elif not self._matches(cli_token, session.cli_token):
                raise PermissionError("CLI token mismatch")
            event = {
                "event_id": str(uuid.uuid4()),
                "created_at": _now(),
                "state": "queued",
                "payload": payload,
                "submitted_at": None,
                "replied_at": None,
                "acknowledged_at": None,
            }
            session.outbound.append(event)
            session.outbound[:] = session.outbound[-30:]
            session.last_seen = _now()
            return {
                "event_id": event["event_id"],
                "paired": session.paired,
                "conversation_id": session.conversation_id,
                "conversation_url": session.conversation_url,
            }

    def next_outbound(self, sid: str, pair_token: str, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            session = self._auth_extension(sid, pair_token, conversation_id)
            for event in session.outbound:
                if event.get("state") in {"queued", "submitted"}:
                    return {
                        "event_id": event["event_id"],
                        "state": event["state"],
                        "created_at": event["created_at"],
                        "payload": event["payload"],
                    }
            return None

    def submitted(self, data: dict[str, Any]) -> None:
        """Confirm the exact bridge user turn exists in the linked ChatGPT DOM."""
        sid=str(data.get("session_id") or ""); token=str(data.get("pair_token") or ""); cid=str(data.get("conversation_id") or ""); event_id=str(data.get("event_id") or "")
        with self.lock:
            session=self._auth_extension(sid,token,cid)
            for event in session.outbound:
                if event["event_id"] == event_id:
                    if event.get("state") == "queued":
                        event["state"]="submitted"; event["submitted_at"]=_now()
                    return
            raise KeyError("outbound event not found")

    def delivered(self, data: dict[str, Any]) -> None:
        # Backward-compatible endpoint name for older extension copies. In v3 it
        # means DOM-confirmed submission, not merely a send-button click.
        self.submitted(data)

    def reply(self, data: dict[str, Any]) -> None:
        sid=str(data.get("session_id") or ""); token=str(data.get("pair_token") or ""); cid=str(data.get("conversation_id") or ""); event_id=str(data.get("event_id") or "")
        text=str(data.get("text") or "").strip()
        if not text: raise ValueError("reply text is empty")
        with self.lock:
            session=self._auth_extension(sid,token,cid)
            found=next((e for e in session.outbound if e["event_id"]==event_id),None)
            if not found: raise KeyError("outbound event not found")
            # The browser may retry after a service-worker/tab reconnect. The same
            # event must create at most one executable inbound instruction.
            existing=next((r for r in session.inbound if r.get("event_id")==event_id),None)
            if existing:
                return
            if found.get("state") not in {"submitted", "replied"}:
                raise RuntimeError("reply arrived before bridge turn submission was confirmed")
            found["state"]="replied"; found["replied_at"]=_now()
            session.inbound.append({"event_id":event_id,"text":text,"conversation_id":cid,"created_at":_now(),"consumed":False})
            session.inbound[:]=session.inbound[-30:]

    def pop_reply(self, sid: str, cli_token: str, event_id: str) -> dict[str, Any] | None:
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                return None
            if not self._matches(cli_token, session.cli_token):
                raise PermissionError("CLI token mismatch")
            session.last_seen = _now()
            for row in session.inbound:
                if row["event_id"] == event_id and not row.get("consumed"):
                    row["consumed"] = True
                    for event in session.outbound:
                        if event.get("event_id") == event_id:
                            event["state"] = "acknowledged"; event["acknowledged_at"] = _now(); break
                    return {"event_id": event_id, "text": row["text"], "conversation_id": row["conversation_id"]}
            return None

    def update_status(self, data: dict[str, Any]) -> None:
        sid = str(data.get("session_id") or "")
        cli_token = str(data.get("cli_token") or "")
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                return
            if not self._matches(cli_token, session.cli_token):
                raise PermissionError("CLI token mismatch")
            session.status = str(data.get("status") or session.status)[:80]
            session.detail = str(data.get("detail") or "")[:200]
            session.model = str(data.get("model") or session.model)[:120]
            session.last_seen = _now()

    def cli_status(self, sid: str, cli_token: str) -> dict[str, Any]:
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                saved = self.persisted_pairs.get(sid) or {}
                return {
                    "session_id": sid,
                    "paired": bool(saved.get("conversation_id")),
                    "pair_code": "",
                    "conversation_id": str(saved.get("conversation_id") or ""),
                    "conversation_url": str(saved.get("conversation_url") or ""),
                    "status": "Idle",
                    "detail": "Unregistered",
                    "queued": 0,
                }
            if not self._matches(cli_token, session.cli_token):
                raise PermissionError("CLI token mismatch")
            session.last_seen = _now()
            return {
                "session_id": sid,
                "paired": session.paired,
                "pair_code": session.pair_code,
                "conversation_id": session.conversation_id,
                "conversation_url": session.conversation_url,
                "status": session.status,
                "detail": session.detail,
                "queued": sum(1 for x in session.outbound if x.get("state") in {"queued", "submitted"}),
            }

    def unregister(self, sid: str, cli_token: str) -> None:
        with self.lock:
            session = self.sessions.get(sid)
            if not session:
                return
            if not self._matches(cli_token, session.cli_token):
                raise PermissionError("CLI token mismatch")
            # Keep pair persistence, but remove the live process entry.
            if session.paired:
                self._save_pairs()
            self.sessions.pop(sid, None)


    # ---------- existing-browser provider ----------
    def browser_register(self, data: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(data.get("provider_id") or "").strip() or str(uuid.uuid4())
        token = str(data.get("token") or "").strip() or secrets.token_urlsafe(24)
        with self.browser_condition:
            existing = self.browsers.get(provider_id)
            if existing and not self._matches(token, existing.token):
                raise PermissionError("Browser provider token mismatch")
            if existing:
                row = existing
                row.last_seen = _now()
                row.version = str(data.get("version") or row.version)[:80]
                row.user_agent = str(data.get("user_agent") or row.user_agent)[:300]
                row.label = str(data.get("label") or row.label)[:100]
            else:
                row = BrowserProvider(
                    provider_id=provider_id,
                    token=token,
                    label=str(data.get("label") or "Edge Browser Bridge")[:100],
                    version=str(data.get("version") or "")[:80],
                    user_agent=str(data.get("user_agent") or "")[:300],
                )
                self.browsers[provider_id] = row
            self.browser_condition.notify_all()
            return {"provider_id": row.provider_id, "token": row.token, "protocol": PROTOCOL_VERSION}

    def _browser_require(self, provider_id: str, token: str) -> BrowserProvider:
        row = self.browsers.get(provider_id)
        if not row:
            raise KeyError("browser provider is not registered")
        if not self._matches(token, row.token):
            raise PermissionError("Browser provider token mismatch")
        row.last_seen = _now()
        return row

    def browser_status(self) -> dict[str, Any]:
        with self.lock:
            cutoff = _now() - 45
            rows = [b for b in self.browsers.values() if b.last_seen >= cutoff]
            rows.sort(key=lambda x: x.last_seen, reverse=True)
            if not rows:
                return {"available": False, "providers": []}
            return {
                "available": True,
                "provider_id": rows[0].provider_id,
                "label": rows[0].label,
                "version": rows[0].version,
                "last_seen": rows[0].last_seen,
                "progress": dict(rows[0].progress),
                "providers": [
                    {"provider_id": b.provider_id, "label": b.label, "version": b.version, "last_seen": b.last_seen}
                    for b in rows[:8]
                ],
            }

    def browser_progress(self, data: dict[str, Any]) -> None:
        provider_id = str(data.get("provider_id") or "").strip()
        token = str(data.get("token") or "").strip()
        stage = str(data.get("stage") or "").strip()[:80]
        if not stage:
            raise ValueError("browser progress stage is required")
        with self.browser_condition:
            row = self._browser_require(provider_id, token)
            row.progress = {
                "stage": stage,
                "detail": str(data.get("detail") or "")[:300],
                "url": str(data.get("url") or "")[:2000],
                "title": str(data.get("title") or "")[:300],
                "at": _now(),
            }
            self.browser_condition.notify_all()

    def browser_enqueue(self, data: dict[str, Any]) -> dict[str, Any]:
        action = str(data.get("action") or "").strip()
        if not action:
            raise ValueError("browser action is required")
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        with self.browser_condition:
            status = self.browser_status()
            provider_id = str(data.get("provider_id") or status.get("provider_id") or "")
            row = self.browsers.get(provider_id)
            if not row or row.last_seen < _now() - 45:
                raise RuntimeError("No live Advertpreneur browser extension provider is connected")
            command_id = str(uuid.uuid4())
            command = {"command_id": command_id, "action": action, "args": args, "created_at": _now()}
            row.commands.append(command)
            row.commands[:] = row.commands[-50:]
            self.browser_condition.notify_all()
            return {"command_id": command_id, "provider_id": provider_id}

    def browser_next(self, provider_id: str, token: str, wait_seconds: float = 20.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0.1, min(float(wait_seconds), 25.0))
        with self.browser_condition:
            row = self._browser_require(provider_id, token)
            while True:
                row.last_seen = _now()
                if row.commands:
                    return row.commands.pop(0)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.browser_condition.wait(timeout=min(remaining, 2.0))

    def browser_result(self, data: dict[str, Any]) -> None:
        provider_id = str(data.get("provider_id") or "").strip()
        token = str(data.get("token") or "").strip()
        command_id = str(data.get("command_id") or "").strip()
        if not command_id:
            raise ValueError("command_id is required")
        with self.browser_condition:
            row = self._browser_require(provider_id, token)
            row.results[command_id] = {
                "ok": bool(data.get("ok", False)),
                "result": data.get("result"),
                "error": str(data.get("error") or ""),
                "at": _now(),
            }
            if len(row.results) > 100:
                for key in list(row.results)[:-100]:
                    row.results.pop(key, None)
            self.browser_condition.notify_all()

    def browser_learn_event(self, data: dict[str, Any]) -> None:
        provider_id = str(data.get("provider_id") or "").strip()
        token = str(data.get("token") or "").strip()
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        action = str(event.get("action") or "").strip().lower()
        if action not in {"click", "fill", "scroll", "navigate", "wait"}:
            return
        with self.browser_condition:
            row = self._browser_require(provider_id, token)
            row.learn_events.append({**event, "at": _now()})
            row.learn_events[:] = row.learn_events[-500:]
            self.browser_condition.notify_all()

    def browser_take_learn_events(self, provider_id: str = "") -> list[dict[str, Any]]:
        with self.browser_condition:
            status = self.browser_status()
            pid = provider_id or str(status.get("provider_id") or "")
            row = self.browsers.get(pid)
            if not row:
                return []
            events = list(row.learn_events)
            row.learn_events.clear()
            return events

    def browser_wait_result(self, command_id: str, provider_id: str = "", wait_seconds: float = 30.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0.1, min(float(wait_seconds), 75.0))
        with self.browser_condition:
            while True:
                rows = [self.browsers.get(provider_id)] if provider_id else list(self.browsers.values())
                for row in [x for x in rows if x is not None]:
                    result = row.results.pop(command_id, None)
                    if result is not None:
                        return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.browser_condition.wait(timeout=min(remaining, 1.0))


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "AdvertpreneurBridge/1"

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - intentionally quiet
        return

    @property
    def state(self) -> BrokerState:
        return self.server.state  # type: ignore[attr-defined]

    def _cors(self) -> None:
        # Browser access is intentionally limited to the installed extension.
        # CLI requests use urllib and have no Origin header.
        origin = str(self.headers.get("Origin") or "")
        if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Advertpreneur-Bridge")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if str(self.headers.get("Access-Control-Request-Private-Network") or "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 2_000_000))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            if parsed.path in {"/health", "/", "/healthz"}:
                self._json(200, {"ok": True, "service": SERVICE_NAME, "protocol": PROTOCOL_VERSION})
                return
            if parsed.path in {"/status", "/v1/status"}:
                self._json(200, {"ok": True, "service": SERVICE_NAME, "protocol": PROTOCOL_VERSION, "sessions": len(self.state.sessions)})
                return
            if parsed.path == "/v1/public/sessions":
                self._json(200, {"sessions": self.state.public_sessions()})
                return
            if parsed.path == "/v1/public/outbound":
                sid = (qs.get("session_id") or [""])[0]
                token = (qs.get("pair_token") or [""])[0]
                cid = (qs.get("conversation_id") or [""])[0]
                self._json(200, {"event": self.state.next_outbound(sid, token, cid)})
                return
            if parsed.path == "/v1/cli/reply":
                sid = (qs.get("session_id") or [""])[0]
                token = (qs.get("cli_token") or [""])[0]
                event_id = (qs.get("event_id") or [""])[0]
                self._json(200, {"reply": self.state.pop_reply(sid, token, event_id)})
                return
            if parsed.path == "/v1/cli/status":
                sid = (qs.get("session_id") or [""])[0]
                token = (qs.get("cli_token") or [""])[0]
                self._json(200, self.state.cli_status(sid, token))
                return
            if parsed.path == "/v1/cli/browser-status":
                self._json(200, self.state.browser_status())
                return
            if parsed.path == "/v1/cli/browser-learn-events":
                provider_id = (qs.get("provider_id") or [""])[0]
                self._json(200, {"events": self.state.browser_take_learn_events(provider_id)})
                return
            if parsed.path == "/v1/cli/browser-result":
                command_id = (qs.get("command_id") or [""])[0]
                provider_id = (qs.get("provider_id") or [""])[0]
                wait = float((qs.get("wait") or ["30"])[0])
                self._json(200, {"result": self.state.browser_wait_result(command_id, provider_id, wait)})
                return
            if parsed.path == "/v1/browser/next":
                provider_id = (qs.get("provider_id") or [""])[0]
                token = (qs.get("token") or [""])[0]
                wait = float((qs.get("wait") or ["20"])[0])
                self._json(200, {"command": self.state.browser_next(provider_id, token, wait)})
                return
            self._json(404, {"error": "not found"})
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except KeyError as exc:
            self._json(404, {"error": str(exc)})
        except Exception as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            data = self._body()
            if parsed.path == "/v1/cli/register":
                self._json(200, self.state.register(data)); return
            if parsed.path == "/v1/cli/rotate-code":
                code = self.state.rotate_code(str(data.get("session_id") or ""), str(data.get("cli_token") or ""))
                self._json(200, {"pair_code": code}); return
            if parsed.path == "/v1/cli/publish":
                self._json(200, self.state.publish(data)); return
            if parsed.path == "/v1/cli/status":
                self.state.update_status(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/cli/unregister":
                self.state.unregister(str(data.get("session_id") or ""), str(data.get("cli_token") or ""))
                self._json(200, {"ok": True}); return
            if parsed.path == "/v1/public/pair":
                self._json(200, self.state.pair(data)); return
            if parsed.path == "/v1/public/unpair":
                self.state.unpair(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/public/submitted":
                self.state.submitted(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/public/delivered":
                self.state.delivered(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/public/reply":
                self.state.reply(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/cli/browser-command":
                if str(self.headers.get("X-Advertpreneur-Bridge") or "") != "cli":
                    raise PermissionError("CLI browser command header required")
                self._json(200, self.state.browser_enqueue(data)); return
            if parsed.path == "/v1/browser/register":
                self._json(200, self.state.browser_register(data)); return
            if parsed.path == "/v1/browser/result":
                self.state.browser_result(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/browser/learn-event":
                self.state.browser_learn_event(data); self._json(200, {"ok": True}); return
            if parsed.path == "/v1/browser/progress":
                self.state.browser_progress(data); self._json(200, {"ok": True}); return
            self._json(404, {"error": "not found"})
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})
        except KeyError as exc:
            self._json(404, {"error": str(exc)})
        except Exception as exc:
            self._json(400, {"error": str(exc)})


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: BrokerState):
        super().__init__(address, BridgeRequestHandler)
        self.state = state


def create_server(port: int = DEFAULT_PORT, state_path: Path | None = None) -> BridgeHTTPServer:
    path = state_path or (Path.home() / ".advertpreneur-cli" / "bridge-pairs.json")
    return BridgeHTTPServer(("127.0.0.1", int(port)), BrokerState(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advertpreneur local Browser Bridge broker")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args(argv)
    server = create_server(args.port, args.state)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
