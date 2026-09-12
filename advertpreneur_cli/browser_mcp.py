"""Local MCP bridge exposing Advertpreneur's existing-browser controller to Codex.

This deliberately uses the extension-backed :class:`BrowserController`: every
navigation opens/focuses the extension's controlled tab rather than asking the
operator to copy a URL into a separate browser window.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .browser_control import BrowserController


PROTOCOL_VERSION = "2024-11-05"


class BrowserMCPServer:
    """Small dependency-free stdio MCP server for live-site tasks.

    It intentionally does not expose delete operations. Deletions remain on
    Advertpreneur's proposal/approval path even when Codex controls wp-admin.
    """

    def __init__(self, project: Path) -> None:
        self.project = project.resolve()
        self.browser = BrowserController(self.project, visible=True)

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        obj = {"type": "object", "properties": {}}
        return [
            {"name": "browser_status", "description": "Read the local Advertpreneur Browser Bridge status and controlled-tab URL. Use before a live-site task.", "inputSchema": obj, "annotations": {"readOnlyHint": True}},
            {"name": "browser_navigate", "description": "Open and focus a URL in Advertpreneur's controlled browser tab. Use this yourself for wp-admin, Hostinger, and other live panels; never ask the operator to paste the URL.", "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "description": "http or https URL"}}, "required": ["url"]}},
            {"name": "browser_inspect", "description": "Inspect the observed DOM and page state of the controlled tab before and after a live-site action.", "inputSchema": {"type": "object", "properties": {"selector": {"type": "string", "default": "body"}, "max_elements": {"type": "integer", "default": 70}}}, "annotations": {"readOnlyHint": True}},
            {"name": "browser_wordpress_state", "description": "Detect whether the current WordPress page is authenticated or needs an interactive browser login.", "inputSchema": obj, "annotations": {"readOnlyHint": True}},
            {"name": "browser_click", "description": "Click a verified CSS selector in the controlled tab. Inspect first and verify the observed result afterwards. Do not use for delete/remove actions.", "inputSchema": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]}},
            {"name": "browser_fill", "description": "Fill a verified form field in the controlled tab. Never request or store passwords; if login is needed, let the operator use the browser's saved-password/session UI.", "inputSchema": {"type": "object", "properties": {"selector": {"type": "string"}, "value": {"type": "string"}}, "required": ["selector", "value"]}},
            {"name": "browser_upload", "description": "Select a project-local file in a verified file input. The path must stay inside the current Advertpreneur project.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "selector": {"type": "string", "default": "input[type=file]"}}, "required": ["path"]}},
            {"name": "browser_wait", "description": "Wait briefly for a controlled-tab operation and return its observed URL/title.", "inputSchema": {"type": "object", "properties": {"milliseconds": {"type": "integer", "default": 750}}}},
        ]

    @staticmethod
    def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def _call(self, name: str, args: dict[str, Any]) -> str:
        if name == "browser_status":
            return self.browser.status()
        if name == "browser_navigate":
            return self.browser.navigate(str(args.get("url") or ""))
        if name == "browser_inspect":
            return self.browser.inspect(str(args.get("selector") or "body"), int(args.get("max_elements") or 70))
        if name == "browser_wordpress_state":
            return json.dumps(self.browser.wordpress_state(), ensure_ascii=False)
        if name == "browser_click":
            return self.browser.click(str(args.get("selector") or ""))
        if name == "browser_fill":
            return self.browser.fill(str(args.get("selector") or ""), str(args.get("value") or ""))
        if name == "browser_upload":
            requested = Path(str(args.get("path") or ""))
            candidate = (self.project / requested).resolve() if not requested.is_absolute() else requested.resolve()
            try:
                candidate.relative_to(self.project)
            except ValueError as exc:
                raise ValueError("browser_upload path must stay inside the current project") from exc
            return self.browser.upload(candidate, str(args.get("selector") or "input[type=file]"))
        if name == "browser_wait":
            return self.browser.wait(int(args.get("milliseconds") or 750))
        raise ValueError(f"Unknown browser tool: {name}")

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = str(request.get("method") or "")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._ok(request_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": {"name": "advertpreneur-browser", "version": "0.20.6"}})
        if method == "tools/list":
            return self._ok(request_id, {"tools": self.tools()})
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            name = str(params.get("name") or "")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            try:
                text = self._call(name, args)
                return self._ok(request_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                return self._ok(request_id, {"content": [{"type": "text", "text": f"BROWSER_ERROR: {exc}"}], "isError": True})
        return self._error(request_id, -32601, f"Method not found: {method}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advertpreneur Browser Bridge MCP server")
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    server = BrowserMCPServer(Path(args.project))
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                continue
            response = server.handle(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps(BrowserMCPServer._error(None, -32700, str(exc))), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
