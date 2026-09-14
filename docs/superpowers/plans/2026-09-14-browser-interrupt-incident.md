# Browser Open and Immediate Interrupt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make supported explicit browser URLs open before provider planning and make Escape interrupt the active external turn before its replacement instruction is dispatched.

**Architecture:** `AdvertpreneurCLI` owns the composer and task lifecycle. `ExternalProviderHarness` owns the currently cancellable provider transport, while `ExternalActionGateway` rejects a newly proposed action once CLI interruption has been requested. Explicit URL bootstrapping uses the existing named browser slots rather than a new browser-control path.

**Tech Stack:** Python 3.10+, pytest, prompt_toolkit, existing BrowserController and provider harness.

---

### Task 1: Prove and retain explicit browser URL routing

**Files:**
- Modify: `tests/test_research_workflow.py`
- Modify: `advertpreneur_cli/cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_explicit_helium_access_url_bootstraps_access_tab_before_provider_turn():
    calls = []
    cli = object.__new__(AdvertpreneurCLI)
    cli.tools = type("Tools", (), {"tool_browser": lambda _self, action, **kwargs: calls.append((action, kwargs))})()

    assert cli._bootstrap_explicit_browser_tabs("First go to https://members.softzilla.net/member") is True
    assert calls == [("navigate", {"url": "https://members.softzilla.net/member", "tab": "access"})]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research_workflow.py::test_explicit_helium_access_url_bootstraps_access_tab_before_provider_turn -q`

Expected: FAIL because `AdvertpreneurCLI` has no local URL bootstrap helper.

- [ ] **Step 3: Write minimal implementation**

```python
def _bootstrap_explicit_browser_tabs(self, instruction: str) -> bool:
    for raw_url in re.findall(r"https?://[^\s<>'\"]+", instruction, flags=re.I):
        url = raw_url.rstrip(".,;:)")
        host = (urlparse(url).hostname or "").lower()
        if host.endswith("softzilla.net"):
            self.tools.tool_browser("navigate", url=url, tab="access")
            return True
        if host.endswith("amazon.com"):
            self.tools.tool_browser("navigate", url=url, tab="amazon")
            return True
    return False
```

Call it after `begin_working()` and before index/planner work.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_research_workflow.py::test_explicit_helium_access_url_bootstraps_access_tab_before_provider_turn -q`

Expected: PASS.

### Task 2: Prove and retain Escape safe-boundary interruption

**Files:**
- Modify: `tests/test_action_gateway.py`
- Modify: `advertpreneur_cli/action_gateway.py`
- Modify: `advertpreneur_cli/provider_harness.py`
- Modify: `advertpreneur_cli/cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_gateway_yields_before_dispatching_an_action_requested_after_escape():
    tools = FakeTools({"browser": "connected"})
    result = ExternalActionGateway(tools).drive(
        "Inspect", lambda _prompt, _id: ('```adp_action\n{"tool":"browser","args":{"action":"status"}}\n```', "thread"),
        should_yield=lambda: True,
    )
    assert result.yielded is True
    assert tools.calls == []
```

```python
def test_provider_harness_interrupts_the_current_provider_turn():
    harness = object.__new__(ExternalProviderHarness)
    harness._active_interrupt_lock = threading.RLock()
    called = []
    harness._active_interrupt = lambda: called.append("interrupted")
    assert harness.interrupt_active() is True
    assert called == ["interrupted"]
    assert harness.interrupt_active() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_action_gateway.py -q`

Expected: FAIL because the gateway dispatches the proposed action and the harness exposes no interrupt hook.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class GatewayLoopResult:
    yielded: bool = False

def drive(..., should_yield: Callable[[], bool] | None = None) -> GatewayLoopResult:
    ...
    if should_yield and should_yield():
        return GatewayLoopResult("Advertpreneur yielded before the next action; the immediate message will run now.", current_id, actions=self._actions, provider_turns=turns, yielded=True)
```

Store exactly one provider interrupt callback under a lock. Register Codex `turn/interrupt` after its turn ID is accepted and register AGY driver close while it is asking. In the active-composer path, Escape sets `_yield_requested` and invokes `interrupt_active()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_action_gateway.py -q`

Expected: PASS.

### Task 3: Release validation and delivery

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document operator behavior**

Add a release note stating that an explicit Amazon/Softzilla URL opens in its named tab before provider planning and Escape stops the active external provider turn at the next safe boundary.

- [ ] **Step 2: Run focused and full validation**

Run: `pytest tests/test_action_gateway.py tests/test_research_workflow.py tests/test_mission_daemon.py -q`

Run: `pytest -q`

Expected: exit code 0 for both commands.

- [ ] **Step 3: Commit and push the release**

```bash
git add advertpreneur_cli/action_gateway.py advertpreneur_cli/cli.py advertpreneur_cli/provider_harness.py tests/test_action_gateway.py tests/test_research_workflow.py README.md docs/superpowers
git commit -m "fix: open research URLs and interrupt provider turns"
git push origin main
```
