# Vision-First Browser Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give ADP compact, human-oriented browser evidence and safe learned navigation while keeping AGY tasks within strict token budgets.

**Architecture:** Browser Bridge exposes a compact visible-control map rather than a full style tree. A local `BrowserVisualMemory` fingerprints state, enforces per-stage screenshot/evidence budgets, and stores only safe intent-to-control mappings. A provider capability registry permits image attachment only for a transport that explicitly implements it; current AGY receives compact UI maps and never falsely claims image vision.

**Tech Stack:** Python 3.12, MV3 JavaScript, pytest, BrowserController, JSON persistence.

---

## File structure

- `advertpreneur_cli/browser_visual.py`: state fingerprinting, capped evidence, and safe learned mappings.
- `advertpreneur_cli/browser_control.py`: compact control-map and screenshot evidence methods.
- `browser-extension/background.js`: visible-control extraction command.
- `advertpreneur_cli/browser_extension/background.js`: packaged copy of the same MV3 command.
- `advertpreneur_cli/tools.py`: `visual_context` browser action and compact inspect default.
- `advertpreneur_cli/action_gateway.py`: provider-neutral visual-navigation contract.
- `advertpreneur_cli/cli.py`: inject bounded browser evidence into browser continuation tasks.
- `advertpreneur_cli/provider_harness.py`: explicit provider image-capability interface; AGY remains false.
- `tests/test_browser_visual.py`, `tests/test_action_gateway.py`, `tests/test_v013_harness.py`: regression coverage.

### Task 1: Add bounded visual evidence and safe learning

**Files:**
- Create: `advertpreneur_cli/browser_visual.py`
- Create: `tests/test_browser_visual.py`

- [ ] **Step 1: Write failing budget and learning tests**

```python
def test_visual_memory_emits_each_state_once(tmp_path: Path):
    memory = BrowserVisualMemory(tmp_path)
    first = memory.observe("access", "https://members.softzilla.net/login", "Softzilla", [{"label": "Login"}])
    again = memory.observe("access", "https://members.softzilla.net/login", "Softzilla", [{"label": "Login"}])
    assert first.capture_screenshot is True
    assert again.capture_screenshot is False

def test_visual_memory_reuses_only_matching_safe_mapping(tmp_path: Path):
    memory = BrowserVisualMemory(tmp_path)
    memory.learn("access", "fingerprint", "open helium", "Helium10 Diamond Access", "button[data-tool='helium']")
    assert memory.recall("access", "fingerprint", "open helium") == "button[data-tool='helium']"
    assert memory.recall("access", "other", "open helium") == ""
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_browser_visual.py -q`

Expected: FAIL because `BrowserVisualMemory` does not exist.

- [ ] **Step 3: Implement the minimal safe store**

```python
@dataclass(frozen=True)
class VisualObservation:
    fingerprint: str
    capture_screenshot: bool
    evidence_allowed: bool

class BrowserVisualMemory:
    def observe(self, tab: str, url: str, title: str, controls: list[dict[str, str]]) -> VisualObservation: ...
    def learn(self, tab: str, fingerprint: str, intent: str, label: str, selector: str) -> None: ...
    def recall(self, tab: str, fingerprint: str, intent: str) -> str: ...
```

Fingerprint only the tab, URL origin/path, title, and visible labels. Cap state captures at three per named tab stage. Persist `tab`, `fingerprint`, normalized intent, label, and selector in `.advertpreneur/browser/visual-memory.json`; reject values containing `password`, `token`, `cookie`, or `session`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_browser_visual.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add advertpreneur_cli/browser_visual.py tests/test_browser_visual.py
git commit -m "feat(browser): persist bounded visual navigation memory"
```

### Task 2: Expose a compact visible-control map from the extension

**Files:**
- Modify: `browser-extension/background.js`
- Modify: `advertpreneur_cli/browser_extension/background.js`
- Modify: `advertpreneur_cli/browser_control.py`
- Test: `tests/test_v013_harness.py`

- [ ] **Step 1: Write failing controller contract test**

```python
def test_browser_controller_returns_compact_visible_controls(tmp_path: Path, monkeypatch):
    controller = BrowserController(tmp_path)
    monkeypatch.setattr(controller, "_extension_available", lambda **_: True)
    monkeypatch.setattr(controller, "_extension_call", lambda *_a, **_k: {
        "url": "https://members.softzilla.net/login", "title": "Login",
        "controls": [{"role": "button", "label": "Login", "selector": "button[type=submit]", "x": 500, "y": 484}],
    })
    assert controller.visible_controls(tab="access")["controls"][0]["label"] == "Login"
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_v013_harness.py::test_browser_controller_returns_compact_visible_controls -q`

Expected: FAIL because `visible_controls` does not exist.

- [ ] **Step 3: Implement the extension command in both copies**

Add `action === "visible_controls"`. In page context, collect up to 24 visible `button`, `a`, `input`, `select`, `[role=button]`, and `[role=link]` elements. Return only `role`, cleaned accessible label (120 chars), generated observed selector, and integer bounding box. Exclude password input values, control-bar nodes, hidden elements, and text/html/style properties. Add `BrowserController.visible_controls(tab="work")` to call it and normalize the response.

- [ ] **Step 4: Verify green and syntax**

Run: `python -m pytest tests/test_v013_harness.py::test_browser_controller_returns_compact_visible_controls -q`

Expected: PASS.

Run: `node --check browser-extension/background.js`

Expected: exit 0.

Run: `node --check advertpreneur_cli/browser_extension/background.js`

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add browser-extension/background.js advertpreneur_cli/browser_extension/background.js advertpreneur_cli/browser_control.py tests/test_v013_harness.py
git commit -m "feat(browser): expose compact visible controls"
```

### Task 3: Provide visual context and learned actions through the shared registry

**Files:**
- Modify: `advertpreneur_cli/tools.py`
- Modify: `advertpreneur_cli/action_gateway.py`
- Modify: `advertpreneur_cli/cli.py`
- Test: `tests/test_action_gateway.py`

- [ ] **Step 1: Write failing visual-context regression**

```python
def test_visual_context_returns_one_compact_state_and_reuses_unchanged_evidence(tmp_path: Path):
    class FakeBrowserWithControls:
        current_url = "https://members.softzilla.net/login"
        current_title = "Login"
        def visible_controls(self, **_kwargs):
            return {"url": self.current_url, "title": self.current_title, "controls": [{"role": "button", "label": "Login", "selector": "button[type=submit]", "x": 500, "y": 484}]}
        def screenshot(self, **_kwargs): return "Screenshot · test.jpg · viewport capture"
    tools = ToolRegistry(tmp_path)
    tools.browser_controller = FakeBrowserWithControls()
    first = json.loads(tools.tool_browser("visual_context", tab="access"))
    second = json.loads(tools.tool_browser("visual_context", tab="access"))
    assert first["controls"][0]["label"] == "Login"
    assert first["screenshot"]
    assert second["screenshot"] == ""
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_action_gateway.py::test_visual_context_returns_one_compact_state_and_reuses_unchanged_evidence -q`

Expected: FAIL because browser action `visual_context` is unknown.

- [ ] **Step 3: Implement the browser action and prompt contract**

Add `visual_context` to `BROWSER_SCHEMAS`. It calls `visible_controls`, records `BrowserVisualMemory.observe`, captures a viewport screenshot only when `capture_screenshot` is true, and returns a JSON packet capped to 24 controls and 4,000 characters. Add `browser_visual_context()` in CLI to include the latest compact packet for browser tasks and follow-ups. Update the gateway contract: call `visual_context` first on a new page/modal; choose by visible label/role; click only an observed control; then request another visual context to verify state. Permit saved-session Login click only when the user explicitly asked and the observed controls expose Login.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_action_gateway.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add advertpreneur_cli/tools.py advertpreneur_cli/action_gateway.py advertpreneur_cli/cli.py tests/test_action_gateway.py
git commit -m "feat(browser): drive navigation from compact visual context"
```

### Task 4: Make image use explicit and provider-safe

**Files:**
- Modify: `advertpreneur_cli/provider_harness.py`
- Modify: `advertpreneur_cli/cli.py`
- Test: `tests/test_action_gateway.py`

- [ ] **Step 1: Write failing capability test**

```python
def test_agy_does_not_claim_image_attachment_support(tmp_path: Path):
    harness = ExternalProviderHarness(tmp_path / "app", tmp_path)
    assert harness.supports_image_attachments("agy") is False
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_action_gateway.py::test_agy_does_not_claim_image_attachment_support -q`

Expected: FAIL because the capability method does not exist.

- [ ] **Step 3: Implement the capability seam**

```python
def supports_image_attachments(self, provider: str) -> bool:
    return False
```

Use it in CLI when building the browser evidence prompt. If false, send only the compact UI map and the screenshot’s local evidence ID; never say the provider saw the image. Keep the method isolated so a future verified provider transport can override it with real attachment support and tests for that transport.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_action_gateway.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add advertpreneur_cli/provider_harness.py advertpreneur_cli/cli.py tests/test_action_gateway.py
git commit -m "feat(provider): declare browser image capability"
```

### Task 5: Final integration verification and release

**Files:**
- Modify: `pyproject.toml`
- Modify: `advertpreneur_cli/__init__.py`
- Modify: `advertpreneur_cli/cli.py`

- [ ] **Step 1: Run focused integration suite**

Run: `python -m pytest tests/test_browser_visual.py tests/test_action_gateway.py tests/test_research_workflow.py tests/test_xray_workflow.py tests/test_v013_harness.py -q`

Expected: PASS.

- [ ] **Step 2: Run static verification**

Run: `node --check browser-extension/background.js`

Expected: exit 0.

Run: `node --check advertpreneur_cli/browser_extension/background.js`

Expected: exit 0.

Run: `git diff --check`

Expected: exit 0.

- [ ] **Step 3: Smoke-check without navigation**

Run: `python -c "from pathlib import Path; from advertpreneur_cli.browser_control import BrowserController; print(BrowserController(Path.home()).status())"`

Expected: `Browser bridge connected` with no page mutation.

- [ ] **Step 4: Bump, package, and release**

Set the next patch version in all three version locations. Run:

```bash
python -m advertpreneur_cli.release_packaging --source . --output dist/release --version 0.28.12 --extension-version 0.5.2
```

Verify the CLI archive, manifest, checksums, and Browser Bridge archive exist. Commit the version bump, tag `v0.28.12`, push `main` and tag, then confirm the GitHub release workflow and release assets.

## Plan self-review

- Spec coverage: compact state evidence (Tasks 1-3), image/state budgets (Tasks 1 and 3), saved-session login (Task 3), safe durable learning (Task 1), provider fallback without false vision claims (Task 4), and release verification (Task 5).
- Placeholder scan: no incomplete implementation steps or unresolved release values.
- Type consistency: `BrowserVisualMemory`, `VisualObservation`, `visible_controls`, `visual_context`, and `supports_image_attachments` are defined before use.
