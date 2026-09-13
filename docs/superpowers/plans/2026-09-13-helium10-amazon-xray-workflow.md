# Helium 10 Amazon Xray Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authorized, resumable, one-keyword-at-a-time Helium 10/Amazon Xray workflow with a local CSV ledger and safe downloaded-report renaming.

**Architecture:** A new local `ResearchRun` component owns the keyword queue, CSV ledger, report directory, safe file movement, and durable pause states. Browser actions remain in `BrowserController`/Browser Bridge and are driven only from observed selectors supplied by the visible session; the component cannot bypass security checks or create hidden Amazon requests.

**Tech Stack:** Python 3.10+, `csv`, `pathlib`, existing Browser Bridge, Prompt Toolkit, unittest/pytest.

---

### Task 1: Local keyword queue and CSV ledger

**Files:**
- Create: `advertpreneur_cli/research_workflow.py`
- Create: `tests/test_research_workflow.py`

- [ ] **Step 1: Write the failing ledger test**

```python
def test_research_run_creates_deduplicated_queue_and_csv(tmp_path):
    run = ResearchRun(tmp_path, "baby blankets", ["organic baby blanket", "Organic Baby Blanket", "muslin blanket"])
    assert run.pending_keywords() == ["organic baby blanket", "muslin blanket"]
    assert run.ledger_path.exists()
    assert "keyword,state,started_at" in run.ledger_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest -q tests/test_research_workflow.py::test_research_run_creates_deduplicated_queue_and_csv`

Expected: FAIL because `ResearchRun` does not exist.

- [ ] **Step 3: Implement the local ledger**

```python
LEDGER_FIELDS = ("keyword", "state", "started_at", "finished_at", "report_path", "original_filename", "renamed_filename", "browser_url", "detail")

class ResearchRun:
    def __init__(self, project: Path, name: str, keywords: Sequence[str]) -> None:
        self.root = project.resolve() / ".advertpreneur" / "research" / safe_slug(name)
        self.report_dir = self.root / "reports"
        self.ledger_path = self.root / "keywords.csv"
        self.root.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._create_rows_if_missing(keywords)
```

Deduplicate case-insensitively, preserve the first spelling, and never store credentials, cookies, or report contents.

- [ ] **Step 4: Run the focused test**

Run: `python -m pytest -q tests/test_research_workflow.py::test_research_run_creates_deduplicated_queue_and_csv`

Expected: PASS.

### Task 2: Report rename, pause, and resume semantics

**Files:**
- Modify: `advertpreneur_cli/research_workflow.py`
- Modify: `tests/test_research_workflow.py`

- [ ] **Step 1: Write failing report/pause tests**

```python
def test_completed_keyword_moves_report_to_safe_keyword_filename(tmp_path):
    run = ResearchRun(tmp_path, "baby blankets", ["Organic / Baby Blanket"])
    source = tmp_path / "downloads" / "xray.csv"; source.parent.mkdir(); source.write_text("report")
    target = run.complete("Organic / Baby Blanket", source, "https://amazon.example/results")
    assert target.name.startswith("organic-baby-blanket")
    assert target.read_text() == "report"
    assert run.pending_keywords() == []

def test_challenge_pause_does_not_advance_queue(tmp_path):
    run = ResearchRun(tmp_path, "baby blankets", ["one", "two"])
    run.pause("one", "MFA required", "https://helium10.example")
    assert run.pending_keywords() == ["one", "two"]
    assert run.rows()[0]["state"] == "paused"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_research_workflow.py::test_completed_keyword_moves_report_to_safe_keyword_filename tests/test_research_workflow.py::test_challenge_pause_does_not_advance_queue`

Expected: FAIL because completion and pause methods do not exist.

- [ ] **Step 3: Implement safe local artifact handling**

Use `Path.replace()` only after checking that the source is a file and the resolved target remains under `report_dir`. Append a deterministic numeric suffix when a sanitized keyword filename already exists. Mark completion only after the moved target exists. `pause()` writes state/detail/URL and leaves the row pending for explicit resume.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest -q tests/test_research_workflow.py`

Expected: PASS.

### Task 3: Expose guarded browser workflow tools

**Files:**
- Modify: `advertpreneur_cli/tools.py:70-105,484-605`
- Modify: `advertpreneur_cli/browser_control.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write the failing challenge guard test**

```python
def test_research_browser_state_blocks_challenge_words(tmp_path):
    tools = ToolRegistry(tmp_path)
    assert tools._research_pause_reason("Verify you are human") == "verification challenge observed"
    assert tools._research_pause_reason("Amazon results") == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m unittest tests.test_tools.ToolRegistryTests.test_research_browser_state_blocks_challenge_words`

Expected: FAIL because the challenge classifier is absent.

- [ ] **Step 3: Add controlled research actions**

Add `research_start`, `research_status`, `research_next`, `research_complete_download`, and `research_pause` actions to the Browser tool schema. `research_next` returns the next keyword and requires the agent to inspect the currently visible page before a fill/click. `research_complete_download` accepts only a local download path and observed current URL. Any page text/title containing sign-in, MFA, CAPTCHA, verification, access denied, or rate warning pauses the active keyword and refuses the next action.

Do not add Amazon HTTP requests, proxy/IP settings, CAPTCHA handlers, credential fields, cookie storage, or automatic challenge retries.

- [ ] **Step 4: Run the focused tool tests**

Run: `python -m unittest tests.test_tools`

Expected: PASS.

### Task 4: Agent instructions and local learning evidence

**Files:**
- Modify: `advertpreneur_cli/agent.py`
- Modify: `advertpreneur_cli/action_gateway.py`
- Modify: `advertpreneur_cli/task_planner.py`
- Modify: `tests/test_action_gateway.py`

- [ ] **Step 1: Write a failing action-contract test**

```python
def test_action_contract_exposes_research_workflow_without_evasion_language():
    text = ExternalActionGateway.contract()
    assert "research_start" in text
    assert "research_complete_download" in text
    assert "captcha" not in text.lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest -q tests/test_action_gateway.py::test_action_contract_exposes_research_workflow_without_evasion_language`

Expected: FAIL because the research action contract is absent.

- [ ] **Step 3: Add workflow instructions**

Route research/Xray tasks to browser evidence. Require one keyword per cycle, a visible-page inspect before each action, and a local ledger update only after an observed download. Permit browser routine learning only after successful evidence-backed steps; do not retain or replay secrets or challenge interactions.

- [ ] **Step 4: Run the focused action test**

Run: `python -m pytest -q tests/test_action_gateway.py::test_action_contract_exposes_research_workflow_without_evasion_language`

Expected: PASS.

### Task 5: Documentation, verification, and release

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `advertpreneur_cli/__init__.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `advertpreneur_cli/browser_mcp.py`

- [ ] **Step 1: Document the workflow**

Describe the supplied URL/sample-keyword task format, `.advertpreneur/research/<run>/keywords.csv`, report naming, resume behavior, and security pause boundary.

- [ ] **Step 2: Run verification**

Run: `python -m compileall -q advertpreneur_cli; python -m pytest -q tests/test_research_workflow.py tests/test_tools.py tests/test_action_gateway.py tests/test_tui.py; python -m unittest tests.test_tools tests.test_release_package tests.test_updater`

Expected: all selected tests pass and compilation exits zero.

- [ ] **Step 3: Build, publish, and updater-test the release**

Build with `python -m advertpreneur_cli.release_packaging --source . --output dist/release-check --version <next-version> --extension-version 0.5.0`, inspect the archive, commit/tag/push, wait for the GitHub workflow, then run `apply_latest_update()` from outside `D:\Cli` and verify `advertpreneur --version` equals the tagged version.
