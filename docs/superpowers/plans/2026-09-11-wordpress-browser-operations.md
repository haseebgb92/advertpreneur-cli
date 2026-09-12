# WordPress Browser Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Advertpreneur CLI extension-first, verified WordPress browser operations, safe local upload staging, and explicit destructive-operation review.

**Architecture:** The existing localhost broker remains the command transport and tracks operation progress. The extension owns a tab in the user's normal browser profile; `BrowserController` normalizes extension and Playwright fallback behavior. New focused WordPress and workspace modules keep policy, staging, and login detection outside the generic controller.

**Tech Stack:** Python 3.12, pytest, Chrome/Edge MV3 extension APIs, Playwright fallback, prompt-toolkit CLI.

---

### Task 1: Safe WordPress upload workspace

**Files:**
- Create: `advertpreneur_cli/wp_workspace.py`
- Create: `tests/test_wp_workspace.py`

- [ ] **Step 1: Write failing workspace tests**

```python
def test_stage_zip_extract_and_list_remain_inside_workspace(tmp_path):
    workspace = WordPressWorkspace(tmp_path)
    staged = workspace.stage(source_zip)
    assert staged.is_file() and staged.parent == workspace.root
    assert workspace.extract(staged, "unpacked").is_dir()

def test_workspace_rejects_escaping_paths(tmp_path):
    with pytest.raises(WorkspaceError):
        WordPressWorkspace(tmp_path).resolve("../outside.zip")
```

- [ ] **Step 2: Run the focused test and confirm it fails because the module is absent**

Run: `python -m pytest tests/test_wp_workspace.py -q`

- [ ] **Step 3: Implement the bounded workspace**

```python
class WordPressWorkspace:
    def __init__(self, project: Path):
        self.root = (project / ".advertpreneur" / "wp-files").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError("Path must stay inside .advertpreneur/wp-files")
        return candidate
```

- [ ] **Step 4: Re-run focused tests and confirm pass**

Run: `python -m pytest tests/test_wp_workspace.py -q`

### Task 2: WordPress state and destructive-review model

**Files:**
- Create: `advertpreneur_cli/wordpress.py`
- Create: `tests/test_wordpress.py`

- [ ] **Step 1: Write failing tests for login detection and proposal approval**

```python
def test_wordpress_login_detection_is_url_and_form_aware():
    assert wordpress_login_state("https://site.test/wp-login.php", {"password": True}) == "login_needed"

def test_delete_proposal_requires_exact_approval():
    proposal = DeletionProposal.create([DeleteTarget("akismet", "plugin", "inactive")])
    assert proposal.approve("wrong") is False
    assert proposal.approve(proposal.token) is True
```

- [ ] **Step 2: Run and confirm red**

Run: `python -m pytest tests/test_wordpress.py -q`

- [ ] **Step 3: Implement pure state/proposal helpers**

```python
@dataclass(frozen=True)
class DeleteTarget:
    name: str; kind: str; warning: str = ""

@dataclass
class DeletionProposal:
    token: str; targets: list[DeleteTarget]; approved: bool = False
    def approve(self, token: str) -> bool:
        self.approved = secrets.compare_digest(token, self.token)
        return self.approved
```

- [ ] **Step 4: Re-run tests and confirm pass**

Run: `python -m pytest tests/test_wordpress.py -q`

### Task 3: Broker progress and extension browser commands

**Files:**
- Modify: `advertpreneur_cli/bridge_server.py`
- Modify: `advertpreneur_cli/bridge.py`
- Modify: `browser-extension/background.js`
- Modify: `browser-extension/manifest.json`
- Modify: `tests/test_bridge_v010.py`

- [ ] **Step 1: Write failing broker/extension contract tests**

```python
def test_browser_status_includes_live_progress(tmp_path):
    state = create_server(0, tmp_path / "pairs.json").state
    registered = state.browser_register({"provider_id":"b", "token":"t"})
    state.browser_progress({"provider_id":"b", "token":"t", "stage":"login_needed"})
    assert state.browser_status()["progress"]["stage"] == "login_needed"

def test_extension_contains_upload_and_wordpress_state_actions():
    js = (root / "browser-extension" / "background.js").read_text()
    assert 'action === "upload"' in js
    assert 'action === "wordpress_state"' in js
```

- [ ] **Step 2: Run and confirm red**

Run: `python -m pytest tests/test_bridge_v010.py -q`

- [ ] **Step 3: Implement progress, upload, and state observation**

Add broker-side authenticated progress storage and `/v1/browser/progress`; add a client method. In the extension, use `chrome.tabs.create` for open/navigation, `chrome.scripting.executeScript` to detect WordPress login/dashboard/notices/list rows, and `chrome.debugger` with the manifest `debugger` permission for `DOM.setFileInputFiles` on the observed input selector. Upload returns the selected filename plus visible success/error state.

- [ ] **Step 4: Re-run bridge contracts**

Run: `python -m pytest tests/test_bridge_v010.py -q`

### Task 4: Controller and tool-schema integration

**Files:**
- Modify: `advertpreneur_cli/browser_control.py`
- Modify: `advertpreneur_cli/tools.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for extension-first open, WordPress status, workspace upload, and proposal gating**

```python
def test_browser_tool_requires_approved_proposal_before_delete(tmp_path):
    tools = LocalTools(tmp_path)
    proposal = tools.tool_browser("wordpress_propose_delete", targets=[...])
    with pytest.raises(ToolError):
        tools.tool_browser("wordpress_delete", proposal_id=proposal["id"])
```

- [ ] **Step 2: Run and confirm red**

Run: `python -m pytest tests/test_tools.py -q`

- [ ] **Step 3: Implement structured operations**

Expose `open`, `wordpress_state`, `upload`, workspace operations, `wordpress_propose_delete`, `wordpress_approve_delete`, and `wordpress_delete`. Require an approved in-memory proposal token before a destructive browser command. Map broker progress into `BrowserController.status()` and return observable evidence text.

- [ ] **Step 4: Re-run focused tool tests**

Run: `python -m pytest tests/test_tools.py -q`

### Task 5: Interactive CLI commands and health display

**Files:**
- Modify: `advertpreneur_cli/cli.py`
- Modify: `tests/test_tui.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing command/status tests**

```python
def test_browser_help_mentions_workspace_and_delete_review():
    assert "wp-files" in BrowserCommandHelp.render()
    assert "propose-delete" in BrowserCommandHelp.render()
```

- [ ] **Step 2: Run and confirm red**

Run: `python -m pytest tests/test_tui.py -q`

- [ ] **Step 3: Implement commands and confirmation UI**

Add `/browser open`, `/browser wp status`, `/browser files ...`, `/browser upload ...`, and `/browser wp propose-delete` / `approve-delete` / `delete`. Render broker/extension/tab/login/progress/last-result status; print exact target rows and ask for the shown proposal token before execution. Document the normal-browser credential boundary and unpacked-extension install path.

- [ ] **Step 4: Re-run focused UI tests**

Run: `python -m pytest tests/test_tui.py -q`

### Task 6: Full verification

**Files:**
- Modify: `README.md`
- Test: `tests/`

- [ ] **Step 1: Run focused new/changed tests**

Run: `python -m pytest tests/test_wp_workspace.py tests/test_wordpress.py tests/test_bridge_v010.py tests/test_tools.py tests/test_tui.py -q`

- [ ] **Step 2: Run full suite and source checks**

Run: `python -m pytest -q; python -m compileall -q advertpreneur_cli; node --check browser-extension/background.js`

- [ ] **Step 3: Inspect final command/status and report evidence**

Run: `python advertpreneur.py --help`
