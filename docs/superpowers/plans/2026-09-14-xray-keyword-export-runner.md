# Xray Keyword Export Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Export a verified, resumable Helium 10 Xray CSV for every supplied Amazon keyword through ADP's connected Chrome profile.

**Architecture:** Keep ResearchRun as the durable CSV ledger. Add a pure Xray pagination policy and observed-selector storage. ToolRegistry owns browser interaction and pins it to amazon, while helium stays open and access is never reused for searches.

**Tech Stack:** Python 3.12, pytest, BrowserController, MV3 Browser Bridge, CSV and JSON.

---

## File structure

- advertpreneur_cli/research_workflow.py: durable outcome states and observed Xray selector persistence.
- advertpreneur_cli/xray_workflow.py: pure row-progress policy.
- advertpreneur_cli/tools.py: browser action schema and Xray execution.
- advertpreneur_cli/action_gateway.py and advertpreneur_cli/agent.py: model contract.
- tests/test_research_workflow.py, tests/test_xray_workflow.py, tests/test_v013_harness.py: regression coverage.

### Task 1: Research ledger outcome support

**Files:**
- Modify: advertpreneur_cli/research_workflow.py:12-145
- Test: tests/test_research_workflow.py

- [ ] **Step 1: Write failing tests**

    def test_research_run_records_no_data_without_completion(tmp_path: Path):
        run = ResearchRun.create(tmp_path, 'Xray Run', ['silicone baking mat'])
        keyword = run.next_keyword()
        run.record_outcome(keyword, 'no_data', 'Xray stayed empty after one refresh')
        assert run.status()['no_data'] == ['silicone baking mat']
        assert run.status()['completed'] == []

    def test_research_run_persists_observed_xray_selectors(tmp_path: Path):
        run = ResearchRun.create(tmp_path, 'Xray Run', ['silicone baking mat'])
        run.remember_xray_selectors(open='#open', rows='[data-asin]', load_more='#more', refresh='#refresh', export='#export', csv='#csv')
        assert run.xray_selectors()['load_more'] == '#more'

- [ ] **Step 2: Verify red**

Run: python -m pytest tests/test_research_workflow.py -q

Expected: FAIL because record_outcome, remember_xray_selectors, and xray_selectors do not exist.

- [ ] **Step 3: Implement the minimal ledger APIs**

Add terminal states no_data, download_missing, verification_required, and site_changed; add record_outcome(keyword, state, detail, browser_url=''); extend status(); persist six Xray selector keys in observed-selectors.json. Keep complete_download() as the only completed path.

- [ ] **Step 4: Verify green**

Run: python -m pytest tests/test_research_workflow.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

    git add advertpreneur_cli/research_workflow.py tests/test_research_workflow.py
    git commit -m 'feat(research): record Xray export outcomes'

### Task 2: Pure Xray pagination policy

**Files:**
- Create: advertpreneur_cli/xray_workflow.py
- Create: tests/test_xray_workflow.py

- [ ] **Step 1: Write failing policy tests**

    from advertpreneur_cli.xray_workflow import XrayProgress, next_xray_action

    def test_more_rows_keeps_loading():
        assert next_xray_action(XrayProgress(20, False), 40, True) == 'load_more'

    def test_stall_refreshes_once_then_records_no_data():
        assert next_xray_action(XrayProgress(20, False), 20, True) == 'refresh'
        assert next_xray_action(XrayProgress(20, True), 20, True) == 'no_data'

    def test_hidden_load_more_exports_populated_rows():
        assert next_xray_action(XrayProgress(20, False), 20, False) == 'export'

- [ ] **Step 2: Verify red**

Run: python -m pytest tests/test_xray_workflow.py -q

Expected: FAIL with missing module.

- [ ] **Step 3: Implement**

Create XrayProgress(previous_rows: int = 0, refreshed: bool = False) and next_xray_action(progress, observed_rows, load_more_visible). Return refresh for empty/stalled rows once, no_data after the retry, load_more for observed row growth, and export when populated results have no further page.

- [ ] **Step 4: Verify green and commit**

Run: python -m pytest tests/test_xray_workflow.py -q

Expected: PASS.

    git add advertpreneur_cli/xray_workflow.py tests/test_xray_workflow.py
    git commit -m 'feat(research): add Xray pagination policy'

### Task 3: Observed-selector Xray runner

**Files:**
- Modify: advertpreneur_cli/tools.py:84-86,607-726
- Modify: advertpreneur_cli/action_gateway.py:60-72
- Modify: advertpreneur_cli/agent.py:30-38
- Test: tests/test_research_workflow.py

- [ ] **Step 1: Write failing integration tests**

Use a fake browser that reports row counts 20, 40, 40 and Load More visibility True, True, False. Start a run, configure research_xray_setup with JSON selectors for search, submit, open, rows, load_more, refresh, export, and csv, then assert one completed report and that every fake call used tab amazon.

Add companion tests for empty rows after one refresh (no_data), missing downloaded file (download_missing), and an observed verification page (verification_required).

- [ ] **Step 2: Verify red**

Run: python -m pytest tests/test_research_workflow.py -q

Expected: FAIL with unknown research_xray_setup.

- [ ] **Step 3: Implement setup and execution**

Add research_xray_setup to the action schema. Parse value as JSON and reject each missing selector by name. Per keyword: fill/search in amazon, wait 15 seconds, open Xray, inspect observed rows, invoke next_xray_action, click Load More only when the observed row count grows, wait 10-15 seconds after every click, and click refresh only once after a stall.

Before CSV export use mark_download(tab='amazon'); click observed export then observed CSV; use wait_for_download; use complete_download; refresh only the amazon tab. Return terminal ledger states instead of retrying indefinitely. Stop at login, MFA, CAPTCHA, access denial, traffic warning, rate limit, or changed UI.

- [ ] **Step 4: Update provider contract**

Require the provider to inspect first and pass only selectors observed in the active tab to research_xray_setup. Preserve access and helium; never embed static Amazon, Softzilla, Helium, or extension selectors.

- [ ] **Step 5: Verify and commit**

Run: python -m pytest tests/test_research_workflow.py tests/test_action_gateway.py -q

Expected: PASS.

    git add advertpreneur_cli/tools.py advertpreneur_cli/action_gateway.py advertpreneur_cli/agent.py tests/test_research_workflow.py
    git commit -m 'feat(research): automate observed Xray CSV exports'

### Task 4: Connected-extension status smoke test

**Files:**
- Modify: advertpreneur_cli/browser_control.py:142-160
- Test: tests/test_v013_harness.py

- [ ] **Step 1: Write a failing status-label regression**

Create a BrowserController whose successful status command returns only provider='existing-edge/extension'; assert status() includes connected.

- [ ] **Step 2: Verify red**

Run: python -m pytest tests/test_v013_harness.py::test_extension_status_labels_successful_status_command_connected -q

Expected: FAIL because a status result does not echo available.

- [ ] **Step 3: Implement and validate**

Treat a successful extension status command as connected unless it explicitly reports available: false. Do not change routing.

Run: python -m pytest tests/test_v013_harness.py tests/test_research_workflow.py tests/test_xray_workflow.py tests/test_action_gateway.py -q

Expected: PASS.

Run: python -c "from pathlib import Path; from advertpreneur_cli.browser_control import BrowserController; print(BrowserController(Path.home()).status())"

Expected: the connected extension is named. Do not navigate, click, download, or enter credentials during this smoke check.

- [ ] **Step 4: Commit**

    git add advertpreneur_cli/browser_control.py tests/test_v013_harness.py
    git commit -m 'fix(browser): report connected extension status'

## Plan self-review

- Spec coverage: durable outcomes, tab isolation, ZIP 10001 provider contract, observed row growth, repeated Load More, one refresh retry, verified download/rename, and safe checkpoint states each map to a task.
- Placeholder scan: no TBD/TODO items or unbounded retries.
- Type consistency: XrayProgress, next_xray_action, record_outcome, xray_selectors, remember_xray_selectors, and research_xray_setup are defined before later use.
