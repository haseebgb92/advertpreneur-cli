from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


RELEASE_PATHS = (
    "advertpreneur_cli",
    "browser-extension",
    "assets",
    "INSTALL.ps1",
    "INSTALL-ONLINE.ps1",
    "START_ADVERTPRENEUR_CLI.ps1",
    "advertpreneur.py",
    "pyproject.toml",
    "requirements.txt",
    "README.md",
)
SKIP_PARTS = {"__pycache__", ".advertpreneur", ".pytest_cache", "build", "dist", ".git"}


def _include(path: Path) -> bool:
    return not any(part in SKIP_PARTS for part in path.parts) and path.suffix != ".pyc"


def build_release(source: Path, archive: Path, version: str) -> Path:
    source = source.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for entry in RELEASE_PATHS:
            origin = source / entry
            if origin.is_file():
                package.write(origin, origin.relative_to(source).as_posix())
            elif origin.is_dir():
                for file in origin.rglob("*"):
                    if file.is_file() and _include(file.relative_to(source)):
                        package.write(file, file.relative_to(source).as_posix())
    return archive


def build_artifacts(source: Path, output: Path, version: str, extension_version: str) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    archive = build_release(source, output / f"AdvertpreneurCLI-{version}-windows.zip", version)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = output / "update-manifest.json"
    manifest.write_text(json.dumps({"version": version, "asset": archive.name, "sha256": checksum, "extension_version": extension_version}, indent=2) + "\n", encoding="utf-8")
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    extension_zip = output / f"AdvertpreneurBrowserBridge-{extension_version}.zip"
    with zipfile.ZipFile(extension_zip, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for file in (source / "browser-extension").rglob("*"):
            if file.is_file() and _include(file.relative_to(source)):
                package.write(file, file.relative_to(source / "browser-extension").as_posix())
    return {"archive": archive, "manifest": manifest, "checksums": checksums, "extension": extension_zip}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Advertpreneur CLI GitHub release artifacts")
    parser.add_argument("--source", default=".")
    parser.add_argument("--output", default="dist/release")
    parser.add_argument("--version", required=True)
    parser.add_argument("--extension-version", required=True)
    args = parser.parse_args()
    artifacts = build_artifacts(Path(args.source), Path(args.output), args.version, args.extension_version)
    for name, path in artifacts.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
