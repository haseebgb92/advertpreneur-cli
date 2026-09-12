# Operations Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Advertpreneur a profile-aware, evidence-first live-site operations CLI.

**Architecture:** `operations.py` owns project identity, operation planning, evidence, and recoverable checkpoints. Tools feed it browser mutations and adapter state; the CLI/extension surface the current state. Existing browser and deletion safety remain the mutation boundary.

**Tech Stack:** Python, pytest, JSON project state, MV3 extension.

---

### Task 1: Operations state

**Files:** Create `advertpreneur_cli/operations.py`; create `tests/test_operations.py`.

- [ ] Write failing tests for environment classification, plan approval requirement on production mutations, evidence append, and resumable checkpoint retrieval.
- [ ] Run `python -m pytest tests/test_operations.py -q` and confirm import failure.
- [ ] Implement `ProjectIdentity`, `OperationPlan`, and `OperationLedger` under `.advertpreneur/operations.json` without credentials.
- [ ] Re-run focused tests.

### Task 2: Tool integration

**Files:** Modify `advertpreneur_cli/tools.py`; modify `advertpreneur_cli/site_adapters.py`; modify `tests/test_tools.py`.

- [ ] Add automatic identity updates for site navigation and structured operation planning/approval/status actions.
- [ ] Record browser action evidence and use plan gates for production mutation classes.
- [ ] Re-run tool tests.

### Task 3: CLI and extension visibility

**Files:** Modify `advertpreneur_cli/cli.py`, `browser-extension/popup.html`, `browser-extension/popup.js`, `README.md`.

- [ ] Show current controlled tab, adapter, environment, checkpoint, and last verified evidence in status/popup.
- [ ] Add stop-control to close only ADP-owned tab; do not affect user tabs.
- [ ] Re-run Python and JavaScript checks.
