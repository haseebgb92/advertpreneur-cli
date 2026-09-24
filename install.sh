#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_VERSION="$(tr -d '[:space:]' < "$ROOT/UPSTREAM_CODEX_VERSION")"
BIN_DIR="${HOME}/.local/bin"
LIB_DIR="${HOME}/.local/lib/codex-unchained"
AGENT_DIR="${HOME}/.gemini/config/agents/codex-unchained-brain"

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v cargo >/dev/null 2>&1 || { echo "Rust/Cargo is required to build the two small Unchained adapters." >&2; exit 1; }

mkdir -p "$BIN_DIR" "$LIB_DIR" "$AGENT_DIR"

echo "==> Installing official OpenAI Codex CLI ${CODEX_VERSION}"
curl -fsSL https://chatgpt.com/codex/install.sh | env CODEX_RELEASE="$CODEX_VERSION" CODEX_NON_INTERACTIVE=1 sh

echo "==> Building model-brain gateway"
cargo build --release --manifest-path "$ROOT/agy-gateway/Cargo.toml"
cp "$ROOT/agy-gateway/target/release/codex-unchained-agy-gateway" "$LIB_DIR/codex-unchained-agy-gateway"
chmod +x "$LIB_DIR/codex-unchained-agy-gateway"

echo "==> Building browser MCP bridge"
cargo build --release --manifest-path "$ROOT/browser-mcp/Cargo.toml"
cp "$ROOT/browser-mcp/target/release/codex-unchained-browser-mcp" "$LIB_DIR/codex-unchained-browser-mcp"
chmod +x "$LIB_DIR/codex-unchained-browser-mcp"

echo "==> Installing Chrome/Edge extension"
rm -rf "$LIB_DIR/extension"
cp -R "$ROOT/extension" "$LIB_DIR/extension"

echo "==> Installing model-only Antigravity agent"
cp "$ROOT/agents/codex-unchained-brain/agent.md" "$AGENT_DIR/agent.md"

echo "==> Installing launcher"
cp "$ROOT/bin/codex-unchained" "$BIN_DIR/codex-unchained"
chmod +x "$BIN_DIR/codex-unchained"
ln -sfn "$BIN_DIR/codex-unchained" "$BIN_DIR/unchained"

echo
echo "Installed."
echo "Run: codex-unchained"
echo "Then use /model inside Codex to pick any discovered AGY or Ollama model."
echo
echo "Browser extension:"
echo "  Chrome: chrome://extensions"
echo "  Edge:   edge://extensions"
echo "Enable Developer mode -> Load unpacked -> $LIB_DIR/extension"
