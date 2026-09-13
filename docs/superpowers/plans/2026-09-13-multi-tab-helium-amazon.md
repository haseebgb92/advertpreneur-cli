# Multi-tab Helium 10 and Amazon Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Helium 10 while ADP works through Amazon Xray exports in a separate tab.

**Architecture:** Replace Browser Bridge's singleton controlled-tab record with named slots and pass the slot through each browser operation. Keep research state local and make the queue runner refresh the Amazon slot after every completed report. Route approvals back to the main CLI event loop.

**Tech Stack:** Python 3.12+, Prompt Toolkit, Chrome Extension Manifest V3, pytest.

---

### Task 1: Named Browser Bridge tabs

**Files:**
- Modify: `browser-extension/background.js`
- Modify: `advertpreneur_cli/browser_extension/background.js`
- Modify: `advertpreneur_cli/browser_control.py`
- Test: `tests/test_browser_tabs.py`

- [ ] Write failing tests for separate `helium` and `amazon` slots plus spawned-tab capture.
- [ ] Implement slot storage, slot-aware navigation/actions, and capture-on-click in both extension copies.
- [ ] Add Python slot arguments to BrowserController and verify the focused tests pass.

### Task 2: Research runner behavior

**Files:**
- Modify: `advertpreneur_cli/tools.py`
- Modify: `advertpreneur_cli/research_workflow.py`
- Test: `tests/test_research_workflow.py`

- [ ] Write failing tests proving research always targets `amazon` and refreshes after a completed report.
- [ ] Implement the slot pinning and refresh-after-download path.
- [ ] Verify the workflow tests pass.

### Task 3: False challenge and approval crash

**Files:**
- Modify: `advertpreneur_cli/tools.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `advertpreneur_cli/tui.py`
- Test: `tests/test_tui.py`

- [ ] Write failing tests for concrete challenge detection and main-thread approval dispatch.
- [ ] Replace bare MFA matching and direct worker-thread Prompt Toolkit prompts.
- [ ] Verify task-thread approval and existing TUI tests pass.

### Task 4: Release

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `advertpreneur_cli/__init__.py`
- Modify: `advertpreneur_cli/cli.py`

- [ ] Update release notes and versions.
- [ ] Run browser/research/TUI regression tests, package the release, push tag, and verify GitHub assets.
