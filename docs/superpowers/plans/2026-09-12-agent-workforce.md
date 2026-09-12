# Agent Workforce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, proactive, evidence-learning workforce for operational CLI tasks.

**Architecture:** `workforce.py` provides specialist definitions, due-task queueing, trigger matching, result learning, and local state. CLI routes task context through the selected specialist, displays upcoming work, and wakes safe due work for review without silently mutating production.

**Tech Stack:** Python 3.12, JSON state, pytest, existing agent/browser/evidence systems.

---

### Task 1: Workforce domain model

**Files:** Create `advertpreneur_cli/workforce.py`; create `tests/test_workforce.py`.

- [ ] Write failing tests for specialist selection, due activation, production policy, and verified-routine promotion.
- [ ] Run `python -m pytest tests/test_workforce.py -q` and confirm module import failure.
- [ ] Implement persistent roles, tasks, due queue, trigger matching, result recording, and confidence-gated learning.
- [ ] Re-run tests and confirm pass.

### Task 2: CLI task integration

**Files:** Modify `advertpreneur_cli/cli.py`; modify `advertpreneur_cli/agent.py`; modify `tests/test_workforce.py`.

- [ ] Select an agent role before each task and add its policy to task context.
- [ ] Record verified outcomes, show next due task in status, and announce safe queued proactive work without mutation.
- [ ] Re-run focused tests.

### Task 3: Documentation and verification

**Files:** Modify `README.md`; test focused workforce/browser suite.

- [ ] Document roles, activation, learning, web-research boundary, and production approvals.
- [ ] Run focused tests, compile checks, and CLI version check.
