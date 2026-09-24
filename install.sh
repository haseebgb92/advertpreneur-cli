#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_VERSION="$(tr -d '[:space:]' < "$ROOT/UPSTREAM_CODEX_VERSION")"
BIN_DIR="${HOME}/.local/bin"
LIB_DIR="${HOME}/.local/lib/codex-unchained"
AGENT_DIR="${HOME}/.gemini/config/agents/codex-unchained-brain"

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v cargo >/dev/null 2>&1 || { echo "Rust/Cargo is required to build the small AGY gateway." >&2; echo "Install rustup, then rerun this installer." >&2; exit 1; }

mkdir -p "$BIN_DIR" "$LIB_DIR" "$AGENT_DIR"

echo "==> Installing official OpenAI Codex CLI ${CODEX_VERSION}"
curl -fsSL https://chatgpt.com/codex/install.sh | env CODEX_RELEASE="$CODEX_VERSION" CODEX_NON_INTERACTIVE=1 sh

echo "==> Building AGY compatibility gateway"
cargo build --release --manifest-path "$ROOT/agy-gateway/Cargo.toml"
cp "$ROOT/agy-gateway/target/release/codex-unchained-agy-gateway" "$LIB_DIR/codex-unchained-agy-gateway"
chmod +x "$LIB_DIR/codex-unchained-agy-gateway"

echo "==> Installing model-only Antigravity agent"
cp "$ROOT/agents/codex-unchained-brain/agent.md" "$AGENT_DIR/agent.md"

echo "==> Installing launcher"
cp "$ROOT/bin/codex-unchained" "$BIN_DIR/codex-unchained"
chmod +x "$BIN_DIR/codex-unchained"
ln -sfn "$BIN_DIR/codex-unchained" "$BIN_DIR/unchained"

echo
echo "Installed. Ensure $BIN_DIR is on PATH, then run:"
echo "  codex-unchained doctor"
echo "  codex-unchained models"
echo "  codex-unchained -m agy/gemini-3.8-flash-medium"
echo "  codex-unchained -m ollama/gpt-oss:120b-cloud"
