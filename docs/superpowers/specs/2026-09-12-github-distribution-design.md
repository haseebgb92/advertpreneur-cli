# GitHub Distribution Design

## Goal

Distribute Advertpreneur CLI from a private GitHub repository with safe, opt-in terminal updates. Ship the browser extension in every CLI release without changing its installed browser path.

## Distribution contract

- The private GitHub repository `haseebgb92/advertpreneur-cli` is the authoritative source and release origin.
- A release publishes a Windows update ZIP, `SHA256SUMS.txt`, and a browser-extension ZIP.
- `INSTALL.ps1` installs the CLI to `%LOCALAPPDATA%\AdvertpreneurCLI` and extracts the extension to `%LOCALAPPDATA%\AdvertpreneurCLI\browser-extension`.
- `/update` checks GitHub Releases, asks for confirmation, downloads the ZIP and checksum, verifies SHA-256, extracts to a staging directory, and atomically replaces the installed application files. It never overwrites user configuration/data.
- Startup checks for a newer release asynchronously and shows a non-blocking update notice. It never downloads or installs without an explicit `/update` confirmation.
- The extension is loaded unpacked from the stable extension path once. A CLI update refreshes those files in place; the user only clicks Reload in `chrome://extensions` or `edge://extensions` after an extension change.

## Package layout

```text
AdvertpreneurCLI-<version>-windows.zip
  advertpreneur_cli/
  assets/
  pyproject.toml
  requirements.txt
  INSTALL.ps1
  START_ADVERTPRENEUR_CLI.ps1
  browser-extension/
  update-manifest.json
```

`update-manifest.json` records the release version, artifact name, artifact SHA-256, minimum supported installer version, and the included extension version. The updater rejects malformed manifests, unexpected asset names, and checksum mismatches.

## Versioning

- The Python package version in `pyproject.toml` is the release version.
- The extension manifest version is updated for every release that changes extension files.
- Git tags use `v<package-version>` and GitHub Actions only releases an exact tag/version match.

## Failure behavior

- Network/auth/release lookup errors leave the installed version untouched and show an actionable message.
- A failed download or checksum leaves the existing installation untouched and removes only the staging directory.
- If the CLI is running from a development checkout rather than the managed install path, `/update` explains that it must be run from the managed installation.

## Verification

- Unit tests cover release version comparison, release asset selection, checksum validation, manifest parsing, and protected user-data paths.
- A package smoke test creates a local ZIP and verifies its expected contents.
- GitHub Actions runs the unit suite and package smoke test before publishing a release.
