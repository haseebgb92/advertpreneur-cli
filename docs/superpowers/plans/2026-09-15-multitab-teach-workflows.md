# Multi-tab Teach Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public `/teach` workflow that records human demonstrations across protected Browser Bridge tabs and replays parameterized Amazon Xray work with fresh keywords.

**Architecture:** Extend the existing `BrowserRoutineStore` record format with a protected-tab snapshot and parameterized non-sensitive search step. The extension will accept trusted human events from every tab captured at teach start and annotate them with a stable tab slot. The CLI exposes `/teach` as a thin wrapper and prints a local review of exactly what was saved.

**Tech Stack:** Python 3.10, pytest, existing Chrome MV3 Browser Bridge JavaScript, prompt-toolkit CLI.

---

### Task 1: Persist protected tab metadata and parameter markers

**Files:**
- Modify: `advertpreneur_cli/browser_learning.py:14-147`
- Test: `tests/test_browser_learning.py`

- [ ] **Step 1: Write the failing tests**

```python
from advertpreneur_cli.browser_learning import BrowserRoutineStore


def test_teach_routine_persists_protected_tabs_and_hides_keyword_value(tmp_path):
    store = BrowserRoutineStore(tmp_path)
    store.start("amazon-xray", protected_tabs={
        "access": {"url": "https://members.softzilla.net/member", "title": "Members"},
        "helium": {"url": "https://app.helium10.com", "title": "Helium"},
        "amazon": {"url": "https://amazon.com", "title": "Amazon"},
    })
    store.record("fill", {"selector": "#twotabsearchtextbox", "value": "bee wax wrap", "tab": "amazon"}, {"url": "https://amazon.com"})
    row = store.stop()

    assert sorted(row["protected_tabs"]) == ["access", "amazon", "helium"]
    assert row["steps"][0]["args"]["value"] == "[TEACH_KEYWORD]"
    assert "bee wax wrap" not in str(row)


def test_teach_routine_omits_non_search_fill_values(tmp_path):
    store = BrowserRoutineStore(tmp_path)
    store.start("portal", protected_tabs={"access": {"url": "https://example.test", "title": "Portal"}})
    store.record("fill", {"selector": "#notes", "value": "private note", "tab": "access"}, {})

    assert store.stop()["steps"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests\\test_browser_learning.py::test_teach_routine_persists_protected_tabs_and_hides_keyword_value tests\\test_browser_learning.py::test_teach_routine_omits_non_search_fill_values -q`

Expected: FAIL because `start()` does not accept `protected_tabs` and raw field values are still recorded.

- [ ] **Step 3: Implement the minimal safe record format**

```python
def start(self, name: str, protected_tabs: Dict[str, Dict[str, str]] | None = None) -> str:
    # Normalize only role, URL without query, and title; save no tab ID or cookies.
    self._learning_tabs = self._safe_tabs(protected_tabs or {})
    ...

def record(self, action: str, args: Dict[str, Any] | None = None, evidence: Dict[str, Any] | None = None) -> None:
    ...
    if action == "fill":
        if self._is_search_field(selector):
            safe_args["value"] = "[TEACH_KEYWORD]"
        else:
            return
    ...

def stop(self) -> Dict[str, Any]:
    row = {"name": name, "created_at": ..., "protected_tabs": self._learning_tabs, "steps": self._learning_steps}
```

`_safe_tabs()` must preserve only keys `url` (without query/fragment) and `title` (max 160 characters). `_is_search_field()` must accept selectors containing `search`, `query`, or `keyword`, and reject sensitive fields using the existing `SENSITIVE_HINT`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests\\test_browser_learning.py::test_teach_routine_persists_protected_tabs_and_hides_keyword_value tests\\test_browser_learning.py::test_teach_routine_omits_non_search_fill_values -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add advertpreneur_cli/browser_learning.py tests/test_browser_learning.py
git commit -m "feat(browser): persist safe taught workflow maps"
```

### Task 2: Record trusted human events from all protected named tabs

**Files:**
- Modify: `browser-extension/background.js:337-347,512-529`
- Modify: `advertpreneur_cli/browser_extension/background.js:337-347,512-529`
- Modify: `advertpreneur_cli/browser_control.py:627-660`
- Test: `tests/test_v013_harness.py`

- [ ] **Step 1: Write the failing controller test**

```python
def test_learn_start_arms_all_named_tabs_and_stop_preserves_event_tab(tmp_path: Path):
    ctl = BrowserController(tmp_path, visible=True)
    ctl._extension_available = lambda wait_seconds=0.0: True
    calls = []
    ctl.get_slots = lambda: {
        "access": {"url": "https://members.softzilla.net/member", "title": "Members"},
        "helium": {"url": "https://app.helium10.com", "title": "Helium"},
        "amazon": {"url": "https://amazon.com", "title": "Amazon"},
    }
    ctl._extension_call = lambda action, **kwargs: calls.append((action, kwargs)) or {"url": "https://amazon.com", "title": "Amazon"}
    ctl.bridge.browser_learn_events = lambda: [{"action": "click", "args": {"selector": "#analyze", "tab": "amazon"}, "evidence": {"url": "https://amazon.com"}}]

    ctl.learn_start("amazon-xray")
    summary = ctl.learn_stop()

    assert calls[0] == ("learn_start", {"timeout": 10, "name": "amazon-xray", "tabs": ["access", "helium", "amazon"]})
    assert "1 step" in summary
    assert ctl.routines.get("amazon-xray")["steps"][0]["args"]["tab"] == "amazon"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests\\test_v013_harness.py::test_learn_start_arms_all_named_tabs_and_stop_preserves_event_tab -q`

Expected: FAIL because the controller has no named-tab snapshot and the extension start call lacks `tabs`.

- [ ] **Step 3: Implement multi-tab capture without tab IDs in local persistence**

```python
def learn_start(self, name: str) -> str:
    tabs = self.get_slots()
    actual = self.routines.start(name, protected_tabs=tabs)
    self._extension_call("learn_start", timeout=10, name=actual, tabs=list(tabs))
    ...
```

In both `background.js` copies, `learn_start` must resolve each requested slot to its tab ID and persist an in-extension-only map `{tabId: slot}`. The `ADP_BROWSER_LEARN_EVENT` listener must accept an event whose sender tab ID is in that map, add `args.tab = slot`, and forward it to the existing local bridge event path. `learn_stop` clears the in-extension map. Do not persist raw tab IDs in routine JSON.

- [ ] **Step 4: Run controller and extension tests to verify they pass**

Run: `python -m pytest tests\\test_v013_harness.py::test_learn_start_arms_all_named_tabs_and_stop_preserves_event_tab tests\\test_v0141_learn_quota_status.py -q; node --check browser-extension\\background.js; node --check advertpreneur_cli\\browser_extension\\background.js`

Expected: pytest passes; both syntax checks produce no error output.

- [ ] **Step 5: Commit**

```powershell
git add advertpreneur_cli/browser_control.py browser-extension/background.js advertpreneur_cli/browser_extension/background.js tests/test_v013_harness.py
git commit -m "feat(browser): teach across protected named tabs"
```

### Task 3: Show the `/teach stop` review and expose public commands

**Files:**
- Modify: `advertpreneur_cli/tui.py:90-160`
- Modify: `advertpreneur_cli/cli.py:2491-2664`
- Test: `tests/test_codex_comfort.py`
- Test: `tests/test_browser_learning.py`

- [ ] **Step 1: Write failing command/review tests**

```python
def test_teach_command_is_available_in_the_command_palette():
    assert "/teach" in {item.value for item in COMMANDS}


def test_taught_workflow_review_lists_tabs_and_numbered_steps(tmp_path):
    store = BrowserRoutineStore(tmp_path)
    store.start("amazon-xray", protected_tabs={"amazon": {"url": "https://amazon.com", "title": "Amazon"}})
    store.record("click", {"selector": "#analyze", "tab": "amazon"}, {"verified": True})
    review = store.stop_review()

    assert "Protected tabs: amazon" in review
    assert "1. [amazon] click #analyze" in review
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests\\test_codex_comfort.py::test_teach_command_is_available_in_the_command_palette tests\\test_browser_learning.py::test_taught_workflow_review_lists_tabs_and_numbered_steps -q`

Expected: FAIL because `/teach` and `stop_review()` do not exist.

- [ ] **Step 3: Add the public command and review renderer**

```python
# tui.py
MenuItem("/teach", "/teach", "Record a safe multi-tab browser workflow: /teach <name>|stop|cancel|list"),

# cli.py, before /browser parsing
if command == "/teach":
    self.teach_command(arg)
    return

def teach_command(self, arg: str | None = None) -> None:
    text = (arg or "").strip()
    if text.lower() == "stop":
        self.ui.success(self.tools.browser_controller.learn_stop())
        self.ui.info(self.tools.browser_controller.routines.stop_review())
    elif text.lower() == "cancel":
        self.ui.muted(self.tools.browser_controller.learn_cancel())
    elif text.lower() == "list":
        ...
    elif text:
        self.ui.success(self.tools.browser_controller.learn_start(text))
    else:
        self.ui.error("Usage: /teach <name> | stop | cancel | list")
```

`stop_review()` returns a plain-text local report with routine name, protected tab names and URL/title lines, then numbered `[tab] action selector` lines. It must not render a value field.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests\\test_codex_comfort.py::test_teach_command_is_available_in_the_command_palette tests\\test_browser_learning.py::test_taught_workflow_review_lists_tabs_and_numbered_steps -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add advertpreneur_cli/tui.py advertpreneur_cli/cli.py advertpreneur_cli/browser_learning.py tests/test_codex_comfort.py tests/test_browser_learning.py
git commit -m "feat(cli): expose teach workflow review"
```

### Task 4: Parameterized replay guards and provider contract

**Files:**
- Modify: `advertpreneur_cli/browser_control.py:670-709`
- Modify: `advertpreneur_cli/action_gateway.py:60-71`
- Test: `tests/test_v013_harness.py`
- Test: `tests/test_action_gateway.py`

- [ ] **Step 1: Write failing replay tests**

```python
def test_taught_routine_requires_protected_tabs_and_injects_only_new_keyword(tmp_path: Path):
    ctl = BrowserController(tmp_path, visible=True)
    ctl.routines._save({"amazon-xray": {
        "name": "amazon-xray",
        "protected_tabs": {"amazon": {"url": "https://amazon.com", "title": "Amazon"}},
        "steps": [{"action": "fill", "args": {"selector": "#search", "value": "[TEACH_KEYWORD]", "tab": "amazon"}}],
    }})
    ctl.get_slots = lambda: {"amazon": {"url": "https://amazon.com", "title": "Amazon"}}
    fills = []; ctl.fill = lambda selector, value, tab="work": fills.append((selector, value, tab))

    ctl.run_routine("amazon-xray", keyword="stainless steel knife sharpener")

    assert fills == [("#search", "stainless steel knife sharpener", "amazon")]


def test_taught_routine_stops_when_a_protected_tab_url_does_not_match(tmp_path: Path):
    ctl = BrowserController(tmp_path, visible=True)
    ctl.routines._save({"amazon-xray": {"name": "amazon-xray", "protected_tabs": {"amazon": {"url": "https://amazon.com", "title": "Amazon"}}, "steps": []}})
    ctl.get_slots = lambda: {"amazon": {"url": "https://example.test", "title": "Changed"}}

    with pytest.raises(BrowserRoutineError, match="protected tab mismatch: amazon"):
        ctl.run_routine("amazon-xray", keyword="bee wax wrap")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests\\test_v013_harness.py::test_taught_routine_requires_protected_tabs_and_injects_only_new_keyword tests\\test_v013_harness.py::test_taught_routine_stops_when_a_protected_tab_url_does_not_match -q`

Expected: FAIL because `run_routine()` has no `keyword` argument and does not validate protected tabs.

- [ ] **Step 3: Implement guarded parameterized replay**

```python
def run_routine(self, name: str, repeat: int = 1, keyword: str = "") -> str:
    row = self.routines.get(name)
    self._verify_protected_tabs(row.get("protected_tabs") or {})
    ...
    if action == "fill" and value == "[TEACH_KEYWORD]":
        if not keyword.strip():
            raise BrowserRoutineError("Routine needs a keyword for its taught search step")
        self.fill(selector, keyword, tab=str(args.get("tab") or "work"))
```

`_verify_protected_tabs()` compares the saved and live `get_slots()` URL origins/path without query/fragment. It raises `BrowserRoutineError("protected tab mismatch: <slot>; re-teach this changed step")` before performing any action. Pass `tab` through every replayed operation. Add the action-contract sentence: “When a named taught browser routine matches, reuse it before visual rediscovery; provide only new task parameters and preserve protected tabs.”

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests\\test_v013_harness.py::test_taught_routine_requires_protected_tabs_and_injects_only_new_keyword tests\\test_v013_harness.py::test_taught_routine_stops_when_a_protected_tab_url_does_not_match tests\\test_action_gateway.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add advertpreneur_cli/browser_control.py advertpreneur_cli/action_gateway.py tests/test_v013_harness.py tests/test_action_gateway.py
git commit -m "feat(browser): replay taught workflows with guarded parameters"
```

### Task 5: Focused regression verification and release handoff

**Files:**
- Modify: `pyproject.toml`
- Modify: `advertpreneur_cli/__init__.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `browser-extension/manifest.json`
- Modify: `advertpreneur_cli/browser_extension/manifest.json`

- [ ] **Step 1: Run the focused regression suite**

Run: `python -m pytest tests\\test_browser_learning.py tests\\test_v013_harness.py tests\\test_action_gateway.py tests\\test_research_workflow.py tests\\test_xray_workflow.py tests\\test_codex_comfort.py -q`

Expected: all selected tests pass. Do not run the session-ending full `pytest -q` command.

- [ ] **Step 2: Validate both Browser Bridge copies and the patch**

Run: `node --check browser-extension\\background.js; node --check advertpreneur_cli\\browser_extension\\background.js; git diff --check`

Expected: no JavaScript syntax errors and no whitespace errors.

- [ ] **Step 3: Bump release versions only after green verification**

```text
CLI: 0.28.12 -> 0.28.13
Browser Bridge: 0.5.3 -> 0.5.4
```

- [ ] **Step 4: Re-run release package tests and build artifacts**

Run: `python -m pytest tests\\test_release_package.py -q; python -m advertpreneur_cli.release_packaging --source . --output dist\\release --version 0.28.13 --extension-version 0.5.4`

Expected: tests pass and artifacts include `AdvertpreneurCLI-0.28.13-windows.zip`, `AdvertpreneurBrowserBridge-0.5.4.zip`, `update-manifest.json`, and `SHA256SUMS.txt`.

- [ ] **Step 5: Commit release metadata and publish only with user authority**

```powershell
git add pyproject.toml advertpreneur_cli/__init__.py advertpreneur_cli/cli.py browser-extension/manifest.json advertpreneur_cli/browser_extension/manifest.json
git commit -m "chore(release): prepare v0.28.13"
git tag -a v0.28.13 -m "Advertpreneur CLI v0.28.13"
git push origin main v0.28.13
```

Create the GitHub release with the four verified artifacts. Before reporting completion, read back the release URL and uploaded asset names.
