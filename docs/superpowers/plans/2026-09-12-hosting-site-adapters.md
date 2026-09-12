# Hosting Site Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, profile-backed adapters for wp-admin, Hostinger File Manager, cPanel, and Plesk.

**Architecture:** A pure Python adapter registry recognizes the active browser URL and provides named routes, upload input selectors, and observable success signals. A project-local profile persists only host URL/adapter preference, never credentials. CLI and agent browser tools use this one registry.

**Tech Stack:** Python 3.12, pytest, existing Browser Bridge and MV3 extension.

---

### Task 1: Profiles and adapter registry

**Files:** Create `advertpreneur_cli/site_adapters.py`; create `tests/test_site_adapters.py`.

- [ ] Write failing tests asserting URL detection for wp-admin, Hostinger, cPanel, and Plesk; route generation rejects unsupported actions; and profile serialization contains no credential fields.
- [ ] Run `python -m pytest tests/test_site_adapters.py -q` and confirm the module import fails.
- [ ] Implement `SiteAdapter`, `SiteProfile`, and `SiteAdapterRegistry`; support `dashboard`, `plugins`, `themes`, `posts`, `pages`, `settings`, `media`, and `file_manager` routes as appropriate.
- [ ] Re-run the focused tests and confirm pass.

### Task 2: Adapter-aware browser tools

**Files:** Modify `advertpreneur_cli/tools.py`; modify `tests/test_tools.py`.

- [ ] Write failing tests for profile detection, profile save/load, route opening, and rejection of an upload outside the staged workspace.
- [ ] Run `python -m pytest tests/test_tools.py -q` and confirm red.
- [ ] Add `site_detect`, `site_profile`, `site_open`, and `site_upload` browser actions. Use profile-stored base URL plus adapter route and existing upload state verification.
- [ ] Re-run focused tests and confirm pass.

### Task 3: CLI and documentation

**Files:** Modify `advertpreneur_cli/cli.py`; modify `README.md`.

- [ ] Add `/browser site detect`, `site use <adapter> <base-url>`, `site status`, `site open <route>`, and `site upload <workspace-file>`.
- [ ] Render adapter, base URL, available routes, detected state, and next action in browser status.
- [ ] Document the adapter boundaries, supported hosts, WordPress credential boundary, and required host-panel verification.

### Task 4: Verification

**Files:** Test `tests/test_site_adapters.py`, `tests/test_tools.py`, `tests/test_bridge_v010.py`.

- [ ] Run `python -m pytest tests/test_site_adapters.py tests/test_tools.py tests/test_bridge_v010.py -q`.
- [ ] Run `python -m compileall -q advertpreneur_cli` and `node --check browser-extension/background.js`.
