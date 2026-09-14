from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REPOSITORY = "haseebgb92/advertpreneur-cli"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")
_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class UpdateError(RuntimeError):
    pass


def _version_parts(value: str) -> tuple[int, int, int]:
    match = _VERSION.match(str(value).strip())
    if not match:
        raise UpdateError(f"Unsupported release version: {value}")
    return tuple(int(item) for item in match.groups())


def release_is_newer(candidate: str, installed: str) -> bool:
    return _version_parts(candidate) > _version_parts(installed)


def verify_sha256(path: Path, expected: str) -> None:
    if not _SHA256.fullmatch(str(expected)):
        raise UpdateError("Release checksum is invalid")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.lower() != str(expected).lower():
        raise UpdateError(f"Checksum verification failed for {path.name}")


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    asset: str
    sha256: str
    extension_version: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReleaseManifest":
        version = str(data.get("version") or "")
        asset = str(data.get("asset") or "")
        checksum = str(data.get("sha256") or "")
        _version_parts(version)
        if not _ASSET.fullmatch(asset) or "/" in asset or "\\" in asset:
            raise UpdateError("Release asset name is unsafe")
        if not _SHA256.fullmatch(checksum):
            raise UpdateError("Release checksum is invalid")
        return cls(version=version.lstrip("v"), asset=asset, sha256=checksum.lower(), extension_version=str(data.get("extension_version") or ""))

    @classmethod
    def load(cls, path: Path) -> "ReleaseManifest":
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateError("Could not read the release manifest") from exc
        if not isinstance(body, dict):
            raise UpdateError("Release manifest must be an object")
        return cls.from_dict(body)


class GitHubReleaseClient:
    """Release access through the user's GitHub CLI login.

    Private repositories deliberately use `gh`, so no update token is stored by
    Advertpreneur and GitHub's own credential manager owns authentication.
    """

    def __init__(self, repository: str = DEFAULT_REPOSITORY, timeout: int = 45) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise UpdateError("GitHub repository name is invalid")
        self.repository = repository
        self.timeout = max(1, int(timeout))

    def _run(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError as exc:
            raise UpdateError("GitHub CLI is required for private release updates") from exc
        except subprocess.TimeoutExpired as exc:
            raise UpdateError("GitHub release request timed out") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "GitHub CLI request failed").strip()
            raise UpdateError(detail)
        return completed.stdout

    def latest_tag(self) -> str:
        try:
            raw = self._run(["api", f"repos/{self.repository}/releases/latest"])
        except UpdateError:
            try:
                with urllib.request.urlopen(f"https://api.github.com/repos/{self.repository}/releases/latest", timeout=30) as response:
                    raw = response.read().decode("utf-8")
            except Exception as exc:
                raise UpdateError("Could not read the public GitHub release") from exc
        try:
            body = json.loads(raw)
            return str(body["tag_name"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise UpdateError("GitHub returned an invalid release response") from exc

    def latest_notes(self) -> str:
        """Return the public release body for ADP's post-update changelog."""
        try:
            raw = self._run(["api", f"repos/{self.repository}/releases/latest"])
        except UpdateError:
            try:
                with urllib.request.urlopen(f"https://api.github.com/repos/{self.repository}/releases/latest", timeout=30) as response:
                    raw = response.read().decode("utf-8")
            except Exception:
                return ""
        try:
            return str(json.loads(raw).get("body") or "").strip()
        except (TypeError, json.JSONDecodeError):
            return ""

    def download(self, tag: str, pattern: str, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        try:
            self._run(["release", "download", tag, "--repo", self.repository, "--pattern", pattern, "--dir", str(destination), "--clobber"])
        except UpdateError:
            try:
                urllib.request.urlretrieve(
                    f"https://github.com/{self.repository}/releases/download/{tag}/{pattern}", destination / pattern,
                )
            except Exception as exc:
                raise UpdateError(f"Could not download public release asset {pattern}") from exc
        path = destination / pattern
        if not path.is_file():
            raise UpdateError(f"GitHub did not download {pattern}")
        return path

    def latest_manifest(self) -> tuple[str, ReleaseManifest]:
        tag = self.latest_tag()
        with tempfile.TemporaryDirectory(prefix="advertpreneur-update-") as temporary:
            path = self.download(tag, "update-manifest.json", Path(temporary))
            return tag, ReleaseManifest.load(path)


class StagedUpdater:
    """Verifies a release archive before delegating the package update to pip."""

    def __init__(self, python: str | None = None) -> None:
        self.python = python or sys.executable

    def install(self, archive: Path, manifest: ReleaseManifest) -> None:
        verify_sha256(archive, manifest.sha256)
        with tempfile.TemporaryDirectory(prefix="advertpreneur-stage-") as temporary:
            stage = Path(temporary)
            try:
                with zipfile.ZipFile(archive) as package:
                    for member in package.infolist():
                        target = (stage / member.filename).resolve()
                        if not target.is_relative_to(stage.resolve()):
                            raise UpdateError("Release archive contains an unsafe path")
                    package.extractall(stage)
            except zipfile.BadZipFile as exc:
                raise UpdateError("Release archive is not a valid ZIP") from exc
            if not (stage / "pyproject.toml").is_file() or not (stage / "advertpreneur_cli").is_dir():
                raise UpdateError("Release archive is missing the CLI package")
            completed = subprocess.run(
                [self.python, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "--no-deps", str(stage)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if completed.returncode != 0:
                raise UpdateError((completed.stderr or completed.stdout or "Package installation failed").strip())


def apply_latest_update(installed_version: str, repository: str = DEFAULT_REPOSITORY) -> ReleaseManifest | None:
    client = GitHubReleaseClient(repository)
    _tag, manifest = client.latest_manifest()
    if not release_is_newer(manifest.version, installed_version):
        return None
    with tempfile.TemporaryDirectory(prefix="advertpreneur-download-") as temporary:
        archive = client.download(_tag, manifest.asset, Path(temporary))
        StagedUpdater().install(archive, manifest)
    return manifest
