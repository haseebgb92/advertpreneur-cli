#!/usr/bin/env sh
set -eu

repo="${CODEX_UNCHAINED_REPOSITORY:-haseebgb92/advertpreneur-cli}"
version="${CODEX_UNCHAINED_VERSION:-latest}"
install_dir="${CODEX_UNCHAINED_INSTALL_DIR:-$HOME/.local/bin}"
unchained_home="${CODEX_UNCHAINED_HOME:-$HOME/.codex-unchained}"

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "tar is required" >&2; exit 1; }

os="$(uname -s)"
arch="$(uname -m)"
case "$os/$arch" in
  Linux/x86_64|Linux/amd64) asset="patched-codex-linux-x64" ;;
  Linux/aarch64|Linux/arm64) asset="patched-codex-linux-arm64" ;;
  Darwin/x86_64) asset="patched-codex-macos-x64" ;;
  Darwin/arm64|Darwin/aarch64) asset="patched-codex-macos-arm64" ;;
  *) echo "Unsupported platform: $os/$arch" >&2; exit 1 ;;
esac

if [ "$version" = latest ]; then
  releases_json="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$repo/releases?per_page=50")"
  version=""
  case "$os/$arch" in
    Linux/x86_64|Linux/amd64)
      version="$(printf '%s' "$releases_json" | tr ',' '\n' | sed -n 's/.*"tag_name":[[:space:]]*"\(codex-unchained-linux-v[^"]*\)".*/\1/p' | head -n 1)"
      ;;
  esac
  if [ -z "$version" ]; then
    version="$(printf '%s' "$releases_json" | tr ',' '\n' | sed -n 's/.*"tag_name":[[:space:]]*"\(codex-unchained-v[^"]*\)".*/\1/p' | head -n 1)"
  fi
  if [ -z "$version" ]; then
    echo "No compatible Codex Unchained release was found." >&2
    exit 1
  fi
fi
base="https://github.com/$repo/releases/download/$version"
echo "Installing Codex Unchained $version ($asset)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fL "$base/$asset.tar.gz" -o "$tmp/package.tar.gz"
tar -xzf "$tmp/package.tar.gz" -C "$tmp"
pkg="$tmp/$asset"

mkdir -p "$install_dir" "$unchained_home"
cp "$pkg/codex-unchained" "$install_dir/codex-unchained-core"
cp "$pkg/adp-mcp" "$install_dir/adp-mcp"
cp "$pkg/adp-unchained" "$install_dir/adp-unchained"
chmod +x "$install_dir/codex-unchained-core" "$install_dir/adp-mcp" "$install_dir/adp-unchained"

cat > "$install_dir/codex-unchained" <<'EOF'
#!/usr/bin/env sh
set -eu

self_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ "${1:-}" = "providers" ]; then
  shift
  case "${1:-status}" in
    status|models)
      exec "$self_dir/adp-unchained" providers
      ;;
    refresh)
      rm -f "${CODEX_UNCHAINED_HOME:-$HOME/.codex-unchained}/models_cache.json"
      exec "$self_dir/adp-unchained" providers
      ;;
    login)
      shift
      provider="${1:-}"
      case "$provider" in
        agy|ollama|codex)
          exec "$self_dir/adp-unchained" auth "$provider"
          ;;
        *)
          echo "Usage: codex-unchained providers login agy|ollama|codex" >&2
          exit 2
          ;;
      esac
      ;;
    *)
      echo "Usage: codex-unchained providers [status|models|refresh|login agy|ollama|codex]" >&2
      exit 2
      ;;
  esac
fi

exec "$self_dir/codex-unchained-core" "$@"
EOF
chmod +x "$install_dir/codex-unchained"

rm -rf "$unchained_home/extension"
cp -R "$pkg/extension" "$unchained_home/extension"

agy_root="$HOME/.gemini/config/agents"
mkdir -p "$agy_root"
rm -rf "$agy_root/adp-unchained-brain"
cp -R "$pkg/antigravity-agent/adp-unchained-brain" "$agy_root/adp-unchained-brain"

config="$unchained_home/config.toml"
mcp_path="$install_dir/adp-mcp"

if [ ! -f "$config" ]; then
  cat > "$config" <<EOF
# Codex Unchained owns this config. Normal Codex continues to use ~/.codex/config.toml.
model = "adp/auto"
model_provider = "unchained"
oss_provider = "ollama"

[model_providers.unchained]
name = "ADP Unchained Model Router"
base_url = "http://127.0.0.1:8766/v1"
wire_api = "responses"
requires_openai_auth = false

[mcp_servers.adp]
command = "$mcp_path"
startup_timeout_sec = 20
EOF
else
  # Upgrade only the old v0.1 default; preserve any explicitly pinned model.
  if grep -q '^model[[:space:]]*=[[:space:]]*"ollama-local/qwen3:1.7b"' "$config"; then
    sed 's#^model[[:space:]]*=[[:space:]]*"ollama-local/qwen3:1.7b"#model = "adp/auto"#' "$config" > "$config.tmp"
    mv "$config.tmp" "$config"
  fi

  if ! grep -q '^model[[:space:]]*=' "$config"; then
    { printf '%s\n' 'model = "adp/auto"'; cat "$config"; } > "$config.tmp"
    mv "$config.tmp" "$config"
  fi
  if ! grep -q '^model_provider[[:space:]]*=' "$config"; then
    { printf '%s\n' 'model_provider = "unchained"'; cat "$config"; } > "$config.tmp"
    mv "$config.tmp" "$config"
  fi
  if ! grep -q '^oss_provider[[:space:]]*=' "$config"; then
    { printf '%s\n' 'oss_provider = "ollama"'; cat "$config"; } > "$config.tmp"
    mv "$config.tmp" "$config"
  fi

  if ! grep -q '^\[model_providers\.unchained\]$' "$config"; then
    cat >> "$config" <<EOF

[model_providers.unchained]
name = "ADP Unchained Model Router"
base_url = "http://127.0.0.1:8766/v1"
model_catalog_url = "http://127.0.0.1:8766/v1/models"
wire_api = "responses"
requires_openai_auth = false
EOF
  fi

  # v0.2 alpha.26 wrote a model_catalog_url key that the pinned Codex config does not own.
  if grep -q '^model_catalog_url[[:space:]]*=' "$config"; then
    grep -v '^model_catalog_url[[:space:]]*=' "$config" > "$config.tmp"
    mv "$config.tmp" "$config"
  fi

  if ! grep -q '^\[mcp_servers\.adp\]$' "$config"; then
    cat >> "$config" <<EOF

[mcp_servers.adp]
command = "$mcp_path"
startup_timeout_sec = 20
EOF
  fi
fi

# The live Unchained router owns model metadata. Remove stale cache on upgrade.
rm -f "$unchained_home/models_cache.json"

case ":$PATH:" in
  *":$install_dir:"*) ;;
  *) echo "Add $install_dir to PATH, or restart your shell if your profile already exports ~/.local/bin." ;;
esac

echo "Codex Unchained installed: $install_dir/codex-unchained"
echo "Default routing: /adp auto"
echo ""
echo "Provider setup:"
echo "  Status/models:   codex-unchained providers"
echo "  Ollama login:    codex-unchained providers login ollama"
echo "  Antigravity:     codex-unchained providers login agy"
echo "  OpenAI/ChatGPT:  codex-unchained providers login codex"
echo "  Refresh catalog: codex-unchained providers refresh"
echo ""
echo "Then run: codex-unchained"
