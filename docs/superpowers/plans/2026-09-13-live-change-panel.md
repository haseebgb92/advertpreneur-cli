# Live Change Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clickable, evidence-backed change summary above the persistent Advertpreneur composer.

**Architecture:** `TerminalUI` owns the panel state and renders it inside Prompt Toolkit's dynamic bottom toolbar, directly above the composer prompt. `ADPCLI` snapshots local Git/checkpoint changes during active work and supplies bounded file-level additions/removals to the UI. Mouse clicks toggle the expanded view without consuming composer input.

**Tech Stack:** Python 3.10+, Prompt Toolkit, existing task checkpoints, existing `DiffIntelligence`, unittest/pytest.

---

### Task 1: Define and render the compact panel

**Files:**
- Modify: `advertpreneur_cli/tui.py:406-805`
- Modify: `tests/test_tui.py`

- [ ] **Step 1: Write the failing render test**

```python
def test_live_change_panel_renders_observed_file_summary(self):
    ui = object.__new__(TerminalUI)
    ui.toolbar = lambda: [("class:toolbar", " STATUS BAR ")]
    ui._joke = "A stable joke row."
    ui._live_active = True
    ui._live_started = 0.0
    ui._live_label = "Action"
    ui._live_detail = "browser"
    ui._live_changes = [("app.py", 8, 2), ("README.md", 3, 0)]
    ui._live_changes_expanded = False
    with mock.patch("advertpreneur_cli.tui.time.monotonic", return_value=12.4):
        rendered = "".join(text for _style, text in ui._composer_toolbar())
    self.assertIn("2 files", rendered)
    self.assertIn("+11 -2", rendered)
    self.assertIn("app.py", rendered)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_tui.TuiTests.test_live_change_panel_renders_observed_file_summary`

Expected: FAIL because `_composer_toolbar()` has no live change-panel rendering.

- [ ] **Step 3: Add bounded UI state and rendering**

```python
def set_live_changes(self, changes: Sequence[tuple[str, int, int]]) -> None:
    self._live_changes = [(str(path), max(0, int(added)), max(0, int(removed))) for path, added, removed in changes][:12]
    self._invalidate_live()

def _live_change_parts(self) -> list[tuple[str, str]]:
    files = len(self._live_changes)
    added = sum(row[1] for row in self._live_changes)
    removed = sum(row[2] for row in self._live_changes)
    names = " · ".join(row[0] for row in self._live_changes[:3]) or "No file changes observed yet"
    return [("class:working", f"  {files} files · +{added} -{removed} · {names}  [click to expand]\n")]
```

Render `_live_change_parts()` after the spinner status and before the joke row.

- [ ] **Step 4: Run the focused test**

Run: `python -m unittest tests.test_tui.TuiTests.test_live_change_panel_renders_observed_file_summary`

Expected: PASS.

### Task 2: Add mouse expansion behavior

**Files:**
- Modify: `advertpreneur_cli/tui.py:433-507`
- Modify: `tests/test_tui.py`

- [ ] **Step 1: Write the failing expanded-render test**

```python
def test_live_change_panel_expands_per_file_counts(self):
    ui = object.__new__(TerminalUI)
    ui._live_changes = [("app.py", 8, 2), ("README.md", 3, 0)]
    ui._live_changes_expanded = True
    rendered = "".join(text for _style, text in ui._live_change_parts())
    self.assertIn("app.py  +8 -2", rendered)
    self.assertIn("README.md  +3 -0", rendered)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_tui.TuiTests.test_live_change_panel_expands_per_file_counts`

Expected: FAIL because expanded per-file rows do not exist.

- [ ] **Step 3: Implement expanded rows and click toggle**

```python
def _toggle_live_changes(self, mouse_event) -> None:
    if mouse_event.event_type == MouseEventType.MOUSE_UP and self._live_changes:
        self._live_changes_expanded = not self._live_changes_expanded
        self._invalidate_live()
```

Use `FormattedText` mouse-handler fragments for the panel header. When expanded,
append at most 8 rows in the exact form `    {path}  +{added} -{removed}`. Keep
the header click target active in both states.

- [ ] **Step 4: Run focused UI tests**

Run: `python -m unittest tests.test_tui`

Expected: PASS.

### Task 3: Feed observed local changes during work

**Files:**
- Modify: `advertpreneur_cli/cli.py:3310-3520`
- Modify: `tests/test_working_ui.py` or add `tests/test_live_change_tracking.py`

- [ ] **Step 1: Write the failing change-normalization test**

```python
def test_live_change_rows_use_observed_diff_counts(self):
    rows = ADPCLI._live_change_rows([Risk(path="app.py", added=8, removed=2), Risk(path="README.md", added=3, removed=0)])
    self.assertEqual(rows, [("app.py", 8, 2), ("README.md", 3, 0)])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m unittest tests.test_live_change_tracking.LiveChangeTrackingTests.test_live_change_rows_use_observed_diff_counts`

Expected: FAIL because the normalizer does not exist.

- [ ] **Step 3: Implement task lifecycle updates**

```python
@staticmethod
def _live_change_rows(risks) -> list[tuple[str, int, int]]:
    return [(str(item.path), max(0, int(item.added)), max(0, int(item.removed))) for item in risks[:12]]
```

Add `CheckpointManager.live_changes()` that reads active Git `diff --numstat`
against its checkpoint tree and includes untracked paths with zero counts. Start
a daemon tracker when the task checkpoint starts; it samples at one-second
intervals and calls `self.ui.set_live_changes(...)` only when rows change. Stop
and join it before checkpoint finalization. If Git statistics are unavailable,
the tracker does not invent counts; final checkpoint paths are sent as zero
count rows. Reset the UI change state in `begin_working()` and `end_working()`.

- [ ] **Step 4: Run the change tracking test**

Run: `python -m unittest tests.test_live_change_tracking`

Expected: PASS.

### Task 4: Verify, document, and release

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `advertpreneur_cli/__init__.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `advertpreneur_cli/browser_mcp.py`

- [ ] **Step 1: Add the release note**

Document that the active panel shows observed file names and additions/removals, and that clicking it expands bounded per-file totals.

- [ ] **Step 2: Run verification**

Run: `python -m compileall -q advertpreneur_cli; python -m pytest -q tests/test_tui.py tests/test_working_ui.py tests/test_action_gateway.py tests/test_tools.py; python -m unittest tests.test_tui tests.test_working_ui tests.test_release_package`

Expected: every selected test passes and compilation exits zero.

- [ ] **Step 3: Build and inspect release artifacts**

Run: `python -m advertpreneur_cli.release_packaging --source . --output dist/release-check --version <next-version> --extension-version 0.5.0`

Expected: the release ZIP, manifest, checksum, and Browser Bridge ZIP are created.

- [ ] **Step 4: Commit, tag, push, and run updater verification**

Commit with `feat: add live change panel`, tag the matching semantic version, push `main` and tag, wait for the GitHub release workflow, then invoke `apply_latest_update()` from outside `D:\Cli` and verify `advertpreneur --version` reports the released version.
