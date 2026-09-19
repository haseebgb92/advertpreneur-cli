#!/usr/bin/env sh
set -eu

repo="${CODEX_UNCHAINED_REPOSITORY:-haseebgb92/advertpreneur-cli}"
version="${CODEX_UNCHAINED_VERSION:-latest}"
install_dir="${CODEX_UNCHAINED_INSTALL_DIR:-$HOME/.local/bin}"

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
  base="https://github.com/$repo/releases/latest/download"
else
  base="https://github.com/$repo/releases/download/$version"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fL "$base/$asset.tar.gz" -o "$tmp/package.tar.gz"
tar -xzf "$tmp/package.tar.gz" -C "$tmp"
mkdir -p "$install_dir"
cp "$tmp/$asset/codex-unchained" "$install_dir/codex-unchained"
cp "$tmp/$asset/adp-mcp" "$install_dir/adp-mcp"
chmod +x "$install_dir/codex-unchained" "$install_dir/adp-mcp"

case ":$PATH:" in
  *":$install_dir:"*) ;;
  *) echo "Add $install_dir to PATH to run codex-unchained globally." ;;
esac
echo "Codex Unchained installed: $install_dir/codex-unchained"
