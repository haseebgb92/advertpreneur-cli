"""Live Web Sidecar Dashboard: lightweight zero-dependency web dashboard for ADP OS."""
from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .automation_memory import AutomationMemory
from .browser_macro import BrowserMacroStore
from .checkpoints import CheckpointManager


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class DashboardEvent:
    id: str
    kind: str  # task, turn, checkpoint, macro, swarm, error
    title: str
    detail: str
    timestamp: str = field(default_factory=_now)


class DashboardState:
    """In-memory state and project hooks for the live web dashboard."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path).resolve()
        self.events: List[DashboardEvent] = []
        self.active_task: Optional[Dict[str, Any]] = None
        self.swarm_state: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    def add_event(self, kind: str, title: str, detail: str = "") -> None:
        with self._lock:
            evt = DashboardEvent(
                id=f"evt-{len(self.events) + 1}",
                kind=kind,
                title=title,
                detail=detail,
            )
            self.events.append(evt)
            if len(self.events) > 200:
                self.events = self.events[-200:]

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            events_data = [asdict(e) for e in reversed(self.events[-50:])]
            return {
                "project_name": self.project_path.name,
                "project_dir": str(self.project_path),
                "active_task": self.active_task,
                "swarm_state": self.swarm_state,
                "events": events_data,
            }


_GLOBAL_STATE: Optional[DashboardState] = None


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the dashboard SPA and JSON REST APIs."""

    def do_GET(self) -> None:
        global _GLOBAL_STATE
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("", "/", "/index.html"):
            self._serve_html()
        elif path == "/api/status":
            self._serve_json(_GLOBAL_STATE.get_summary() if _GLOBAL_STATE else {})
        elif path == "/api/checkpoints":
            self._serve_checkpoints()
        elif path == "/api/memory":
            self._serve_memory()
        elif path == "/api/macros":
            self._serve_macros()
        elif path == "/api/events":
            self._serve_events()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/events/clear":
            if _GLOBAL_STATE:
                with _GLOBAL_STATE._lock:
                    _GLOBAL_STATE.events.clear()
            self._serve_json({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _serve_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        global _GLOBAL_STATE
        if not _GLOBAL_STATE:
            self._serve_json({"events": []})
            return
        with _GLOBAL_STATE._lock:
            self._serve_json({"events": [asdict(e) for e in reversed(_GLOBAL_STATE.events)]})

    def _serve_checkpoints(self) -> None:
        global _GLOBAL_STATE
        if not _GLOBAL_STATE:
            self._serve_json([])
            return
        mgr = CheckpointManager(_GLOBAL_STATE.project_path)
        cps = mgr.list(limit=30)
        self._serve_json([asdict(c) for c in cps])

    def _serve_memory(self) -> None:
        global _GLOBAL_STATE
        if not _GLOBAL_STATE:
            self._serve_json([])
            return
        mem = AutomationMemory(_GLOBAL_STATE.project_path)
        items = mem.query(limit=50)
        self._serve_json([asdict(m) for m in items])

    def _serve_macros(self) -> None:
        global _GLOBAL_STATE
        if not _GLOBAL_STATE:
            self._serve_json([])
            return
        store = BrowserMacroStore(_GLOBAL_STATE.project_path)
        names = store.list_macros()
        macros = []
        for name in names:
            m = store.load(name)
            if m:
                macros.append(asdict(m))
        self._serve_json(macros)

    def _serve_html(self) -> None:
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ADP OS Live Web Sidecar</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --accent: #58a6ff;
      --accent-green: #3fb950;
      --accent-purple: #bc8cff;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --header: #f0f6fc;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
    body { background-color: var(--bg); color: var(--text); padding: 20px; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
    h1 { color: var(--header); font-size: 20px; display: flex; align-items: center; gap: 8px; }
    .status-badge { font-size: 12px; padding: 4px 8px; background: rgba(63, 185, 80, 0.15); color: var(--accent-green); border-radius: 12px; border: 1px solid var(--accent-green); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; min-height: 280px; display: flex; flex-direction: column; }
    .card h2 { font-size: 15px; color: var(--header); margin-bottom: 12px; display: flex; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .feed { flex: 1; overflow-y: auto; max-height: 380px; display: flex; flex-direction: column; gap: 8px; }
    .item { background: #21262d; border-radius: 6px; padding: 10px; border-left: 3px solid var(--accent); font-size: 13px; }
    .item.checkpoint { border-left-color: var(--accent-purple); }
    .item.macro { border-left-color: #d29922; }
    .item.swarm { border-left-color: #f778ba; }
    .item-title { font-weight: 600; color: var(--header); margin-bottom: 4px; display: flex; justify-content: space-between; }
    .item-time { font-size: 11px; color: var(--text-muted); }
    .item-detail { font-size: 12px; color: var(--text-muted); white-space: pre-wrap; }
    .empty { color: var(--text-muted); font-style: italic; text-align: center; padding: 40px 0; }
  </style>
</head>
<body>
  <header>
    <h1>⚡ ADP OS Live Sidecar Dashboard</h1>
    <div>
      <span class="status-badge" id="conn-status">● Live Connected</span>
      <span id="proj-name" style="margin-left: 12px; color: var(--text-muted); font-size: 13px;"></span>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <h2>Live Mission & Agent Feed <span style="font-size:12px; color:var(--text-muted);" id="event-count">0 events</span></h2>
      <div class="feed" id="events-feed">
        <div class="empty">No mission events recorded yet.</div>
      </div>
    </div>

    <div class="card">
      <h2>Time-Travel Checkpoints <span style="font-size:12px; color:var(--text-muted);" id="cp-count">0 snapshots</span></h2>
      <div class="feed" id="checkpoints-feed">
        <div class="empty">No snapshots found.</div>
      </div>
    </div>

    <div class="card">
      <h2>Persistent Automation Memory <span style="font-size:12px; color:var(--text-muted);" id="mem-count">0 keys</span></h2>
      <div class="feed" id="memory-feed">
        <div class="empty">No persistent memory items stored.</div>
      </div>
    </div>

    <div class="card">
      <h2>Browser Macros & Swarm Status <span style="font-size:12px; color:var(--text-muted);" id="macro-count">0 macros</span></h2>
      <div class="feed" id="macros-feed">
        <div class="empty">No browser macros recorded.</div>
      </div>
    </div>
  </div>

  <script>
    async function refresh() {
      try {
        const [statusRes, cpRes, memRes, macroRes] = await Promise.all([
          fetch('/api/status').then(r => r.json()),
          fetch('/api/checkpoints').then(r => r.json()),
          fetch('/api/memory').then(r => r.json()),
          fetch('/api/macros').then(r => r.json())
        ]);

        document.getElementById('proj-name').textContent = statusRes.project_name || '';

        // Events
        const events = statusRes.events || [];
        document.getElementById('event-count').textContent = `${events.length} events`;
        const ef = document.getElementById('events-feed');
        if (events.length) {
          ef.innerHTML = events.map(e => `
            <div class="item ${e.kind}">
              <div class="item-title"><span>[${e.kind.toUpperCase()}] ${escapeHtml(e.title)}</span><span class="item-time">${e.timestamp.split('T')[1]||''}</span></div>
              ${e.detail ? `<div class="item-detail">${escapeHtml(e.detail)}</div>` : ''}
            </div>
          `).join('');
        } else {
          ef.innerHTML = '<div class="empty">No mission events recorded yet.</div>';
        }

        // Checkpoints
        document.getElementById('cp-count').textContent = `${cpRes.length} snapshots`;
        const cf = document.getElementById('checkpoints-feed');
        if (cpRes.length) {
          cf.innerHTML = cpRes.map(c => `
            <div class="item checkpoint">
              <div class="item-title"><span>${escapeHtml(c.label)}</span><span class="item-time">${c.id.slice(0, 8)}</span></div>
              <div class="item-detail">${c.changed_files.length} modified file(s) · mode: ${c.mode}</div>
            </div>
          `).join('');
        } else {
          cf.innerHTML = '<div class="empty">No snapshots found.</div>';
        }

        // Memory
        document.getElementById('mem-count').textContent = `${memRes.length} keys`;
        const mf = document.getElementById('memory-feed');
        if (memRes.length) {
          mf.innerHTML = memRes.map(m => `
            <div class="item">
              <div class="item-title"><span>${escapeHtml(m.key)}</span><span class="item-time">${m.category}</span></div>
              <div class="item-detail">${escapeHtml(m.value)}</div>
            </div>
          `).join('');
        } else {
          mf.innerHTML = '<div class="empty">No persistent memory items stored.</div>';
        }

        // Macros
        document.getElementById('macro-count').textContent = `${macroRes.length} macros`;
        const mcf = document.getElementById('macros-feed');
        if (macroRes.length) {
          mcf.innerHTML = macroRes.map(m => `
            <div class="item macro">
              <div class="item-title"><span>⚡ ${escapeHtml(m.name)}</span><span class="item-time">${m.steps.length} steps</span></div>
              <div class="item-detail">${escapeHtml(m.description || 'Recorded macro')}</div>
            </div>
          `).join('');
        } else {
          mcf.innerHTML = '<div class="empty">No browser macros recorded.</div>';
        }

      } catch (err) {
        document.getElementById('conn-status').textContent = '● Disconnected';
        document.getElementById('conn-status').style.borderColor = '#f85149';
        document.getElementById('conn-status').style.color = '#f85149';
      }
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    setInterval(refresh, 2000);
    refresh();
  </script>
</body>
</html>
"""
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Mute default request spam in CLI console
        pass


class DashboardServer:
    """Threaded web sidecar manager."""

    def __init__(self, project_path: Path, port: int = 4141) -> None:
        self.project_path = Path(project_path).resolve()
        self.port = port
        self.state = DashboardState(self.project_path)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, open_browser: bool = False) -> str:
        global _GLOBAL_STATE
        _GLOBAL_STATE = self.state

        if self._server is not None:
            url = f"http://127.0.0.1:{self.port}"
            if open_browser:
                webbrowser.open(url)
            return url

        # Try designated port or fall back
        for p in range(self.port, self.port + 10):
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", p), DashboardHandler)
                self.port = p
                break
            except OSError:
                continue

        if not self._server:
            raise RuntimeError(f"Could not bind Dashboard Server to ports {self.port}-{self.port+10}")

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://127.0.0.1:{self.port}"
        self.state.add_event("system", "Dashboard Started", f"Serving live metrics on {url}")

        if open_browser:
            webbrowser.open(url)
        return url

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
