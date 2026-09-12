import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from advertpreneur_cli.mcp import MCPManager, MCPServerInfo


class MCPTests(unittest.TestCase):
    def test_codex_mcp_json_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = MCPManager(Path(td))
            payload = [{
                "name":"docs",
                "enabled":True,
                "transport":{"type":"stdio","command":"docs-server","args":[]},
                "auth_status":"unsupported"
            }]
            with patch("advertpreneur_cli.mcp.shutil.which", return_value="codex"), patch(
                "advertpreneur_cli.mcp.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps(payload)
                run.return_value.stderr = ""
                rows = mgr.discover(refresh=True)
                self.assertEqual(rows[0].name, "docs")
                self.assertEqual(rows[0].transport_type, "stdio")

    def test_stdio_tools_list_and_call(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "server.py"
            script.write_text(r'''
import json, sys
for line in sys.stdin:
    msg=json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"protocolVersion":msg["params"]["protocolVersion"],"capabilities":{},"serverInfo":{"name":"mock","version":"1"}}}), flush=True)
    elif msg.get("method") == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"tools":[{"name":"echo","description":"echo text","inputSchema":{"type":"object","properties":{"text":{"type":"string"}}}}]}}), flush=True)
    elif msg.get("method") == "tools/call":
        text=msg.get("params",{}).get("arguments",{}).get("text","")
        print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"content":[{"type":"text","text":"ECHO:"+text}],"isError":False}}), flush=True)
''', encoding="utf-8")
            mgr = MCPManager(Path(td), approve=lambda *_: True)
            server = MCPServerInfo("mock", True, "stdio", {"type":"stdio","command":sys.executable,"args":[str(script)]}, "unsupported")
            with patch.object(mgr, "get", return_value=server):
                tools = mgr.list_tools("mock")
                self.assertEqual(tools[0]["name"], "echo")
                self.assertEqual(mgr.call_tool("mock", "echo", {"text":"hi"}), "ECHO:hi")


if __name__ == "__main__":
    unittest.main()
