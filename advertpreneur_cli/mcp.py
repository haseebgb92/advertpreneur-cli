from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List


class MCPError(RuntimeError):
    pass


@dataclass
class MCPServerInfo:
    name: str
    enabled: bool
    transport_type: str
    transport: Dict[str, Any]
    auth_status: str = "unknown"
    source: str = "Codex"

    @property
    def summary(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        auth = self.auth_status or "unknown"
        return f"{state} · {self.transport_type} · auth {auth}"


class MCPManager:
    """Reuse MCP server definitions already configured in Codex.

    Discovery intentionally goes through `codex mcp list --json` so Advertpreneur
    does not need to understand every Codex config migration. The bridge supports
    stdio MCP servers and streamable-HTTP servers that can authenticate with a
    bearer token already present in an environment variable. OAuth-only servers
    are detected, but Advertpreneur does not copy private Codex OAuth credentials.
    """

    PROTOCOLS = ("2025-06-18", "2024-11-05")

    def __init__(
        self,
        app_dir: Path,
        approve: Callable[[str, str], bool] | None = None,
        approval_mode: str = "safe",
    ) -> None:
        self.app_dir = app_dir
        self.approve = approve or (lambda _kind, _detail: False)
        self.approval_mode = approval_mode
        self.state_path = app_dir / "mcp.json"
        self._state = self._load_state()
        self._cache: list[MCPServerInfo] | None = None
        self._tools_cache: Dict[str, List[Dict[str, Any]]] = {}

    def _load_state(self) -> Dict[str, Any]:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"enabled": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def _adp_enabled(self, server: MCPServerInfo) -> bool:
        value = self._state.setdefault("enabled", {}).get(server.name)
        return server.enabled if value is None else bool(value)

    def set_enabled(self, name: str, enabled: bool) -> None:
        self._state.setdefault("enabled", {})[name] = bool(enabled)
        self._save_state()
        self._cache = None
        self._tools_cache.pop(name.lower(), None)

    def discover(self, refresh: bool = False) -> List[MCPServerInfo]:
        if self._cache is not None and not refresh:
            return list(self._cache)
        if refresh:
            self._tools_cache.clear()
        codex = shutil.which("codex")
        if not codex:
            self._cache = []
            return []
        try:
            proc = subprocess.run(
                [codex, "mcp", "list", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            if proc.returncode != 0:
                raise MCPError((proc.stderr or proc.stdout or "codex mcp list failed").strip())
            data = json.loads(proc.stdout or "[]")
        except Exception as exc:
            self._cache = []
            if isinstance(exc, MCPError):
                raise
            raise MCPError(f"Could not read Codex MCP configuration: {exc}") from exc

        rows = data.get("servers", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            rows = []
        out: List[MCPServerInfo] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            transport = item.get("transport") if isinstance(item.get("transport"), dict) else {}
            ttype = str(transport.get("type") or item.get("transport_type") or "unknown")
            info = MCPServerInfo(
                name=name,
                enabled=bool(item.get("enabled", True)),
                transport_type=ttype,
                transport=dict(transport),
                auth_status=str(item.get("auth_status") or "unknown"),
            )
            info.enabled = self._adp_enabled(info)
            out.append(info)
        self._cache = sorted(out, key=lambda x: x.name.lower())
        return list(self._cache)

    def get(self, name: str) -> MCPServerInfo:
        for server in self.discover():
            if server.name.lower() == name.lower():
                return server
        raise MCPError(f"MCP server not found: {name}")

    def catalog(self, max_chars: int = 1800) -> str:
        try:
            servers = [s for s in self.discover() if s.enabled]
        except MCPError:
            return ""
        if not servers:
            return ""
        lines = ["Configured Codex MCP servers available through the `mcp` tool:"]
        used = len(lines[0])
        for s in servers:
            line = f"- {s.name}: {s.transport_type}; auth={s.auth_status}"
            if used + len(line) + 1 > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
        lines.append("Call mcp(action='list_tools', server='...') before using an unfamiliar server tool.")
        return "\n".join(lines)

    def list_tools(self, server_name: str, refresh: bool = False) -> List[Dict[str, Any]]:
        server = self.get(server_name)
        if not server.enabled:
            raise MCPError(f"MCP server '{server.name}' is disabled in Advertpreneur CLI")
        key = server.name.lower()
        if key in self._tools_cache and not refresh:
            return [dict(x) for x in self._tools_cache[key]]
        result = self._rpc(server, "tools/list", {})
        tools = [x for x in (result.get("tools", []) if isinstance(result, dict) else []) if isinstance(x, dict)]
        self._tools_cache[key] = [dict(x) for x in tools]
        return [dict(x) for x in tools]

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any] | None = None) -> str:
        server = self.get(server_name)
        if not server.enabled:
            raise MCPError(f"MCP server '{server.name}' is disabled in Advertpreneur CLI")
        args = arguments or {}
        detail = f"MCP {server.name}.{tool_name}({json.dumps(args, ensure_ascii=False)[:700]})"
        if self.approval_mode != "full" and not self.approve("mcp", detail):
            raise MCPError("MCP tool call denied by user")
        result = self._rpc(server, "tools/call", {"name": tool_name, "arguments": args})
        if not isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        if result.get("isError"):
            raise MCPError(self._content_text(result.get("content")) or "MCP tool returned an error")
        text = self._content_text(result.get("content"))
        return text or json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _content_text(content: Any) -> str:
        if not isinstance(content, list):
            return ""
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif item.get("type") in {"resource", "resource_link"}:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)

    def _rpc(self, server: MCPServerInfo, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        t = server.transport_type.lower()
        if t == "stdio":
            return self._stdio_rpc(server, method, params)
        if t in {"streamable_http", "http", "sse"}:
            return self._http_rpc(server, method, params)
        raise MCPError(f"Unsupported MCP transport '{server.transport_type}' for {server.name}")

    @staticmethod
    def _resolve_env(raw: Dict[str, Any]) -> Dict[str, str]:
        env = os.environ.copy()
        for k, v in (raw or {}).items():
            env[str(k)] = os.path.expandvars(str(v))
        return env

    def _stdio_rpc(self, server: MCPServerInfo, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        tr = server.transport
        command = tr.get("command")
        if not command:
            raise MCPError(f"MCP stdio server '{server.name}' has no command")
        args = [str(x) for x in (tr.get("args") or [])]
        env = self._resolve_env(tr.get("env") if isinstance(tr.get("env"), dict) else {})
        for key in tr.get("env_vars") or []:
            if key in os.environ:
                env[str(key)] = os.environ[str(key)]
        cwd = tr.get("cwd") or None
        try:
            proc = subprocess.Popen(
                [str(command), *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                env=env,
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"Could not start MCP server {server.name}: {exc}") from exc

        try:
            protocol = self._initialize_stdio(proc, server.name)
            self._send_stdio(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            req_id = 2
            self._send_stdio(proc, {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            reply = self._read_stdio(proc, req_id, timeout=45)
            if "error" in reply:
                raise MCPError(f"{server.name}: {reply['error']}")
            result = reply.get("result", {})
            return result if isinstance(result, dict) else {"value": result, "protocol": protocol}
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass

    def _initialize_stdio(self, proc: subprocess.Popen, server_name: str) -> str:
        last_error = ""
        for protocol in self.PROTOCOLS:
            self._send_stdio(proc, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": protocol,
                    "capabilities": {},
                    "clientInfo": {"name": "advertpreneur-cli", "version": "0.6.0"},
                },
            })
            reply = self._read_stdio(proc, 1, timeout=25)
            if "error" not in reply:
                return str((reply.get("result") or {}).get("protocolVersion") or protocol)
            last_error = str(reply.get("error"))
        raise MCPError(f"MCP initialize failed for {server_name}: {last_error}")

    @staticmethod
    def _send_stdio(proc: subprocess.Popen, payload: Dict[str, Any]) -> None:
        if not proc.stdin:
            raise MCPError("MCP stdin unavailable")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    @staticmethod
    def _read_stdio(proc: subprocess.Popen, request_id: int, timeout: float) -> Dict[str, Any]:
        if not proc.stdout:
            raise MCPError("MCP stdout unavailable")
        deadline = time.monotonic() + timeout
        box: list[str] = []
        done = threading.Event()

        def reader() -> None:
            try:
                while not done.is_set():
                    line = proc.stdout.readline()
                    if not line:
                        return
                    line = line.strip()
                    if line:
                        box.append(line)
                        try:
                            obj = json.loads(line)
                            if obj.get("id") == request_id:
                                done.set()
                                return
                        except Exception:
                            continue
            except Exception:
                return

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        while time.monotonic() < deadline and not done.wait(0.05):
            if proc.poll() is not None:
                break
        done.set()
        for line in reversed(box):
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("id") == request_id:
                return obj
        err = ""
        try:
            if proc.stderr:
                err = proc.stderr.read(1500)
        except Exception:
            pass
        raise MCPError(f"MCP request timed out or server exited. {err}".strip())

    def _http_rpc(self, server: MCPServerInfo, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        tr = server.transport
        url = tr.get("url")
        if not url:
            raise MCPError(f"MCP HTTP server '{server.name}' has no URL")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AdvertpreneurCLI/0.6",
        }
        bearer_env = tr.get("bearer_token_env_var")
        if bearer_env:
            token = os.environ.get(str(bearer_env))
            if not token:
                raise MCPError(f"Environment variable {bearer_env} required by MCP server {server.name} is missing")
            headers["Authorization"] = f"Bearer {token}"
        elif str(server.auth_status).lower() in {"oauth", "authenticated"}:
            raise MCPError(
                f"{server.name} uses Codex-managed OAuth. Advertpreneur can discover it but does not copy Codex OAuth credentials. "
                "Use a bearer-token environment variable or a stdio server for now."
            )

        session_id = None
        init = self._http_post(url, headers, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOLS[0],
                "capabilities": {},
                "clientInfo": {"name": "advertpreneur-cli", "version": "0.6.0"},
            },
        })
        session_id = init.get("_session_id")
        if "error" in init:
            raise MCPError(f"MCP initialize failed for {server.name}: {init['error']}")
        request_headers = dict(headers)
        if session_id:
            request_headers["Mcp-Session-Id"] = session_id
        self._http_post(url, request_headers, {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        reply = self._http_post(url, request_headers, {"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
        if "error" in reply:
            raise MCPError(f"{server.name}: {reply['error']}")
        result = reply.get("result", {})
        return result if isinstance(result, dict) else {"value": result}

    @staticmethod
    def _http_post(url: str, headers: Dict[str, str], payload: Dict[str, Any], expect_reply: bool = True) -> Dict[str, Any]:
        req = urllib.request.Request(
            str(url),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                session_id = resp.headers.get("Mcp-Session-Id")
                if not expect_reply and not raw.strip():
                    return {"_session_id": session_id}
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "text/event-stream" in ctype:
                    objects = []
                    for line in raw.splitlines():
                        if line.startswith("data:"):
                            try:
                                objects.append(json.loads(line[5:].strip()))
                            except Exception:
                                pass
                    obj = objects[-1] if objects else {}
                else:
                    obj = json.loads(raw or "{}")
                if session_id and isinstance(obj, dict):
                    obj["_session_id"] = session_id
                return obj if isinstance(obj, dict) else {"value": obj, "_session_id": session_id}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MCPError(f"MCP HTTP {exc.code}: {detail[:1200]}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"Could not reach MCP server {url}: {exc.reason}") from exc
