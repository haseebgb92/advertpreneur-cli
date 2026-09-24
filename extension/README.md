# Codex Unchained Browser Bridge

This Manifest V3 Chrome/Edge extension is intentionally thin.

It does **not** contain an agent, model router, planner, or browser automation brain. It connects the user's existing Chrome/Edge profile to the local `codex-unchained-browser-mcp` process at `127.0.0.1:8765`.

Official Codex owns:
- planning and tool selection;
- approvals and policy;
- the MCP client/tool loop;
- conversation state.

The extension only performs browser actions Codex selected through MCP: semantic inspect, navigation, click, fill, scroll, screenshot, and download completion.

## Load it

After running the installer, the extension is copied to the Unchained install directory.

Chrome:
1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Choose **Load unpacked**
4. Select the installed `extension` directory

Edge uses the same steps at `edge://extensions`.

The MCP process starts automatically when Codex Unchained starts. The extension reconnects to it automatically.
