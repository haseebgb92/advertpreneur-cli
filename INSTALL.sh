#!/usr/bin/env sh
set -eu

repo="${ADVERTPRENEUR_REPOSITORY:-haseebgb92/advertpreneur-cli}"
base="https://github.com/$repo/releases/latest/download"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

command -v python3 >/dev/null 2>&1 || { echo "Python 3.10+ is required." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }

curl -fsSL "$base/update-manifest.json" -o "$stage/manifest.json"
asset="$(python3 -c 'import json; print(json.load(open("'"$stage"'/manifest.json"))["asset"])')"
sha="$(python3 -c 'import json; print(json.load(open("'"$stage"'/manifest.json"))["sha256"])')"
curl -fsSL "$base/$asset" -o "$stage/$asset"
actual="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$stage/$asset")"
[ "$actual" = "$sha" ] || { echo "Release checksum verification failed." >&2; exit 1; }
unzip -q "$stage/$asset" -d "$stage/source"
python3 -m pip install --user --upgrade --no-deps "$stage/source"
echo "Advertpreneur CLI installed. Run: advertpreneur"
