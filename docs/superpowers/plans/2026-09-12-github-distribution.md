# GitHub Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Advertpreneur CLI from GitHub Releases and let installed users safely discover and apply updates from the terminal.

**Architecture:** Add a small release-update module that reads versioned GitHub release metadata and performs checksum-verified staged replacements. Keep installer state under `%LOCALAPPDATA%\AdvertpreneurCLI`, while preserving user state under its existing app-data directory. Bundle the extension into the CLI release and always extract it to its stable unpacked-extension folder.

**Tech Stack:** Python 3.12, standard-library HTTP/ZIP/hash utilities, PowerShell installer, GitHub Actions, GitHub Releases.

---

### Task 1: Define release metadata and updater behavior

**Files:**
- Create: `advertpreneur_cli/updater.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `tests/test_updater.py`

- [ ] **Step 1: Write failing tests for semver comparison, manifest validation, and checksum failures.**

```python
def test_release_is_newer_when_tag_exceeds_installed_version():
    assert release_is_newer("v0.20.0", "0.19.0") is True

def test_manifest_rejects_path_traversal_asset():
    with pytest.raises(UpdateError):
        ReleaseManifest.from_dict({"asset": "../bad.zip", "sha256": "0" * 64})
```

- [ ] **Step 2: Run `python -m unittest tests.test_updater` and confirm the missing module causes the intended failure.**

- [ ] **Step 3: Implement `ReleaseClient`, `ReleaseManifest`, and `StagedUpdater` with standard-library HTTP, SHA-256, and ZIP extraction.**

```python
class StagedUpdater:
    def apply(self, archive: Path, target: Path) -> None:
        # extract into a sibling staging directory, validate contents,
        # then replace only application paths.
        ...
```

- [ ] **Step 4: Add `/update` and background startup notice wiring in `cli.py`.**

- [ ] **Step 5: Re-run `python -m unittest tests.test_updater`.**

### Task 2: Stabilize installer and extension location

**Files:**
- Modify: `INSTALL.ps1`
- Modify: `START_ADVERTPRENEUR_CLI.ps1`
- Modify: `browser-extension/README.md`
- Modify: `advertpreneur_cli/browser_extension/README.md`
- Test: `tests/test_release_package.py`

- [ ] **Step 1: Write a failing package/install contract test for the extension destination and protected data paths.**

```python
def test_release_archive_contains_extension_and_installer():
    names = package_release(source_root, output_zip)
    assert "browser-extension/manifest.json" in names
```

- [ ] **Step 2: Run `python -m unittest tests.test_release_package` and confirm it fails before the packager exists.**

- [ ] **Step 3: Update PowerShell scripts to install application files and extension files beneath `%LOCALAPPDATA%\AdvertpreneurCLI`, preserving existing user configuration.**

- [ ] **Step 4: Add a release packager that produces a ZIP, extension ZIP, checksum file, and manifest.**

- [ ] **Step 5: Re-run `python -m unittest tests.test_release_package`.**

### Task 3: Automate tagged GitHub Releases

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/test_release_package.py`

- [ ] **Step 1: Write a failing test that verifies the generated manifest package version matches `pyproject.toml`.**

- [ ] **Step 2: Run the package test and verify the version mismatch is caught.**

- [ ] **Step 3: Add a tag-triggered workflow that runs tests, builds the release artifacts, validates the tag, and uploads checksums and both ZIPs to GitHub Releases.**

- [ ] **Step 4: Document first install, `/update`, and extension Reload steps.**

- [ ] **Step 5: Run focused updater/package tests and Python compilation.**

### Task 4: Create and publish the initial repository

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Inspect the exact repository contents and confirm `.advertpreneur`, build artifacts, caches, and credentials remain excluded.**

- [ ] **Step 2: Initialize Git, add the private GitHub remote, and create the initial commit.**

- [ ] **Step 3: Authenticate GitHub interactively if required and create `haseebgb92/advertpreneur-cli` as private.**

- [ ] **Step 4: Push `main`, tag the initial release, and verify the GitHub release assets after the workflow completes.**
