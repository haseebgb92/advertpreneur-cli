# ADP v0.22 Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a durable local mission control plane with live cockpit, executable plans, evidence-gated completion, attention routing, and a single-user Windows background server.

**Architecture:** Add a focused mission domain module and loopback daemon rather than expanding `cli.py` state further. The CLI remains the interactive owner; it submits/reconnects to daemon-backed missions and projects immutable mission events into `TerminalUI`. Existing checkpoints, working evidence, workforce, browser controller, and tool registry are adapted through explicit bridge methods.

**Tech Stack:** Python 3.12+, Prompt Toolkit, stdlib `http.server`, JSONL/atomic JSON, pytest/unittest, Windows loopback sockets.

---

### Task 1: Durable mission domain model

**Files:**
- Create: `advertpreneur_cli/missions.py`
- Create: `tests/test_missions.py`

- [ ] **Step 1: Write failing tests for a created mission and legal state transitions.**

```python
from advertpreneur_cli.missions import MissionStore

def test_mission_store_creates_plan_and_tracks_step_states(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Update homepage", [{"title": "Inspect", "kind": "inspect"}])
    assert mission.status == "planned"
    assert mission.steps[0].state == "pending"
    store.transition_step(mission.id, mission.steps[0].id, "active")
    assert store.load(mission.id).steps[0].state == "active"

def test_mission_rejects_completion_when_required_evidence_is_missing(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Verify site", [{"title": "Inspect", "kind": "browser", "evidence": ["browser_observation"]}])
    store.transition_step(mission.id, mission.steps[0].id, "verified")
    assert store.complete(mission.id) is False
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `python -m pytest tests/test_missions.py -q`

Expected: FAIL with `ModuleNotFoundError: advertpreneur_cli.missions`.

- [ ] **Step 3: Implement immutable event-backed mission storage.**

```python
@dataclass
class MissionStep:
    id: str
    title: str
    kind: str
    state: str = "pending"
    evidence: list[str] = field(default_factory=list)

class MissionStore:
    def create(self, request: str, steps: list[dict]) -> Mission: ...
    def transition_step(self, mission_id: str, step_id: str, state: str) -> Mission: ...
    def record_evidence(self, mission_id: str, step_id: str, item: EvidenceItem) -> Mission: ...
    def complete(self, mission_id: str) -> bool: ...
```

Persist a compact snapshot at `.advertpreneur/missions/<id>.json` using a temporary sibling followed by `Path.replace`; append each reducer event to `.advertpreneur/missions/<id>.jsonl`. Permit only `planned → active → waiting|verified|failed|skipped`; keep illegal transitions as `ValueError`.

- [ ] **Step 4: Run the test to verify it passes.**

Run: `python -m pytest tests/test_missions.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/missions.py tests/test_missions.py
git commit -m "feat: add durable mission state"
```

### Task 2: Evidence requirements and result reducer

**Files:**
- Modify: `advertpreneur_cli/missions.py`
- Modify: `advertpreneur_cli/working_record.py`
- Modify: `tests/test_missions.py`
- Modify: `tests/test_cli_completion_cards.py`

- [ ] **Step 1: Write failing tests for evidence-gated result state.**

```python
def test_code_step_requires_checkpoint_and_verification_evidence(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Patch", [{"title": "Patch", "kind": "code", "evidence": ["diff", "verification"]}])
    step = mission.steps[0]
    store.record_evidence(mission.id, step.id, EvidenceItem("diff", "checkpoint:abc"))
    assert store.verify_step(mission.id, step.id) is False
    store.record_evidence(mission.id, step.id, EvidenceItem("verification", "pytest:pass"))
    assert store.verify_step(mission.id, step.id) is True
```

- [ ] **Step 2: Run tests to verify failure.**

Run: `python -m pytest tests/test_missions.py tests/test_cli_completion_cards.py -q`

Expected: FAIL because `EvidenceItem` and `verify_step` do not exist.

- [ ] **Step 3: Implement typed evidence and a single completion decision.**

Add `EvidenceItem(kind, location, observed_at, producer, summary, verified)` and a `MissionStore.verify_step` that checks exact required kinds. Add `SessionWorkingRecord.record_mission_evidence(...)` as a bounded, redacted mirror; it must never accept secrets or raw provider output. Update `_finalize_task_card` to accept a mission result and emit `completed` only when `mission.complete` succeeded; otherwise emit `needs_attention` with the missing evidence names.

- [ ] **Step 4: Run tests to verify passing behavior.**

Run: `python -m pytest tests/test_missions.py tests/test_cli_completion_cards.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/missions.py advertpreneur_cli/working_record.py advertpreneur_cli/cli.py tests/test_missions.py tests/test_cli_completion_cards.py
git commit -m "feat: gate task completion on evidence"
```

### Task 3: Attention request queue and safe resume

**Files:**
- Modify: `advertpreneur_cli/missions.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `advertpreneur_cli/tui.py`
- Modify: `tests/test_missions.py`
- Modify: `tests/test_tui.py`

- [ ] **Step 1: Write failing tests for attention creation, resolution, and prompt ownership.**

```python
def test_attention_pauses_only_its_mission_and_resume_targets_same_step(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Upload", [{"title": "Confirm upload", "kind": "browser"}])
    request = store.request_attention(mission.id, mission.steps[0].id, "approval", ["approve", "deny"])
    assert store.load(mission.id).status == "waiting"
    store.resolve_attention(request.id, "approve")
    assert store.load(mission.id).steps[0].state == "active"
```

- [ ] **Step 2: Run the tests to verify failure.**

Run: `python -m pytest tests/test_missions.py tests/test_tui.py -q`

Expected: FAIL because attention APIs are absent.

- [ ] **Step 3: Implement attention reducer and UI rendering.**

Create `AttentionRequest` with stable ID, mission/step ID, reason, allowed responses, created/expiry times, and resolution. Add `TerminalUI.set_attention(request)` and a keyboard/mouse action that returns only listed responses. In `AdvertpreneurCLI`, replace direct worker confirmations with `MissionStore.request_attention`; `_serve_pending_approval` resolves the pending request on the main prompt loop. Login/MFA/CAPTCHA/tool errors map to attention reasons without treating page text as credentials.

- [ ] **Step 4: Run the tests to verify passing behavior.**

Run: `python -m pytest tests/test_missions.py tests/test_tui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/missions.py advertpreneur_cli/cli.py advertpreneur_cli/tui.py tests/test_missions.py tests/test_tui.py
git commit -m "feat: add durable human attention routing"
```

### Task 4: Planner-to-mission adapter

**Files:**
- Modify: `advertpreneur_cli/task_planner.py`
- Modify: `advertpreneur_cli/missions.py`
- Modify: `advertpreneur_cli/cli.py`
- Create: `tests/test_mission_planning.py`

- [ ] **Step 1: Write failing tests that convert an existing local plan into executable mission steps.**

```python
def test_task_plan_maps_to_mission_with_evidence_requirements(tmp_path):
    planner = LocalTaskPlanner(FakeContract(), FakeIndex())
    plan = planner.plan("Update a WordPress page and verify it")
    mission = mission_from_task_plan(tmp_path, "Update a WordPress page and verify it", plan)
    assert any(step.kind == "browser" for step in mission.steps)
    assert any("browser_observation" in step.evidence for step in mission.steps)
```

- [ ] **Step 2: Run the test to verify failure.**

Run: `python -m pytest tests/test_mission_planning.py -q`

Expected: FAIL because `mission_from_task_plan` is absent.

- [ ] **Step 3: Implement deterministic task-plan translation.**

Add `mission_from_task_plan(store, request, plan)` in `missions.py`. Map inspection to `inspection`, code to `diff` plus `verification`, browser mutation to `browser_observation`, downloads to `download` plus `ledger`, and release work to `release_asset`. Unknown plan details become one `inspect` step, never a generic mutation step. In `run_task`, create the mission before provider execution and set current mission ID for tool-event projection.

- [ ] **Step 4: Run the test to verify passing behavior.**

Run: `python -m pytest tests/test_mission_planning.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/task_planner.py advertpreneur_cli/missions.py advertpreneur_cli/cli.py tests/test_mission_planning.py
git commit -m "feat: convert task plans into executable missions"
```

### Task 5: Cockpit projection in the persistent terminal

**Files:**
- Modify: `advertpreneur_cli/tui.py`
- Modify: `advertpreneur_cli/cli.py`
- Create: `tests/test_cockpit.py`

- [ ] **Step 1: Write failing rendering tests for active mission, attention, and evidence summary.**

```python
def test_cockpit_renders_mission_step_attention_and_evidence():
    ui = make_ui_for_rendering()
    ui.set_cockpit({"mission":"Update homepage","step":"Verify","state":"waiting","attention":"approval","evidence":"2/3"})
    rendered = render_toolbar(ui)
    assert "Update homepage" in rendered
    assert "waiting for input" in rendered
    assert "Evidence 2/3" in rendered
```

- [ ] **Step 2: Run the test to verify failure.**

Run: `python -m pytest tests/test_cockpit.py -q`

Expected: FAIL because cockpit state is absent.

- [ ] **Step 3: Implement bounded cockpit state.**

Add `TerminalUI.set_cockpit`, `clear_cockpit`, and `cockpit_details`. Render at most one mission row, one step/attention row, browser/download counts, changed-file aggregate, and evidence fraction above the persistent composer. Bind click and a `/mission` command to show expanded bounded evidence/plan rows. Refresh with the existing Prompt Toolkit invalidation path only; do not add ANSI redraw loops.

- [ ] **Step 4: Run the test to verify passing behavior.**

Run: `python -m pytest tests/test_cockpit.py tests/test_tui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/tui.py advertpreneur_cli/cli.py tests/test_cockpit.py
git commit -m "feat: add persistent mission cockpit"
```

### Task 6: Loopback mission daemon protocol

**Files:**
- Create: `advertpreneur_cli/mission_daemon.py`
- Create: `advertpreneur_cli/mission_client.py`
- Modify: `advertpreneur_cli/cli.py`
- Create: `tests/test_mission_daemon.py`

- [ ] **Step 1: Write failing daemon lifecycle and reconnect tests.**

```python
def test_daemon_starts_once_and_client_recovers_saved_mission(tmp_path):
    daemon = MissionDaemon(tmp_path, port=0)
    daemon.start()
    client = MissionClient(tmp_path, daemon.port)
    mission = client.create_mission("Inspect repo", [{"title":"Inspect","kind":"inspect"}])
    assert client.get_mission(mission["id"])["request"] == "Inspect repo"
    second = MissionDaemon(tmp_path, port=daemon.port)
    assert second.start() is False
```

- [ ] **Step 2: Run the test to verify failure.**

Run: `python -m pytest tests/test_mission_daemon.py -q`

Expected: FAIL because daemon classes are absent.

- [ ] **Step 3: Implement authenticated loopback daemon.**

Use `ThreadingHTTPServer(("127.0.0.1", port), Handler)` with a random token written atomically under `~/.advertpreneur-cli/daemon.json`, mode-restricted where supported. Implement only `GET /health`, `POST /missions`, `GET /missions/<id>`, `POST /missions/<id>/events`, and `POST /shutdown`. Add a lock/PID record; validate a live PID before refusing a second start, and replace a stale record. `MissionClient` must negotiate `protocol_version=1` before calls.

- [ ] **Step 4: Run the test to verify passing behavior.**

Run: `python -m pytest tests/test_mission_daemon.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/mission_daemon.py advertpreneur_cli/mission_client.py advertpreneur_cli/cli.py tests/test_mission_daemon.py
git commit -m "feat: add local mission daemon"
```

### Task 7: CLI reconnection, recovery, and operational commands

**Files:**
- Modify: `advertpreneur_cli/cli.py`
- Modify: `advertpreneur_cli/tui.py`
- Modify: `advertpreneur_cli/sessions.py`
- Modify: `tests/test_mission_daemon.py`
- Modify: `tests/test_sessions.py`

- [ ] **Step 1: Write failing tests for restart recovery and command surface.**

```python
def test_recover_marks_abandoned_active_mission_interrupted(tmp_path):
    store = MissionStore(tmp_path)
    mission = store.create("Build", [{"title":"Build","kind":"code"}])
    store.transition_step(mission.id, mission.steps[0].id, "active")
    assert store.recover_interrupted()[0].status == "interrupted"
```

- [ ] **Step 2: Run the tests to verify failure.**

Run: `python -m pytest tests/test_mission_daemon.py tests/test_sessions.py -q`

Expected: FAIL because recovery and mission session linkage are absent.

- [ ] **Step 3: Implement recovery and commands.**

Persist `mission_id` in `SessionRecord`. On CLI startup, connect to an existing daemon or start one, mark abandoned `active` missions as `interrupted`, and show a resumable cockpit card. Add `/mission`, `/mission resume <id>`, `/mission attention <id>`, `/daemon status`, and `/daemon stop`; commands operate through `MissionClient`, never direct files. `/mission resume` restores only the next unverified step and existing provider/session context.

- [ ] **Step 4: Run the tests to verify passing behavior.**

Run: `python -m pytest tests/test_mission_daemon.py tests/test_sessions.py tests/test_tui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/cli.py advertpreneur_cli/tui.py advertpreneur_cli/sessions.py tests/test_mission_daemon.py tests/test_sessions.py
git commit -m "feat: recover and resume durable missions"
```

### Task 8: Tool-event evidence projection

**Files:**
- Modify: `advertpreneur_cli/tools.py`
- Modify: `advertpreneur_cli/agent.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `advertpreneur_cli/missions.py`
- Create: `tests/test_mission_events.py`

- [ ] **Step 1: Write failing tests that browser, command, and file actions produce structured evidence.**

```python
def test_browser_download_records_download_and_ledger_evidence(tmp_path):
    sink = RecordingMissionSink()
    tools = ToolRegistry(tmp_path, mission_sink=sink)
    tools.record_browser_download("report.csv", "run/keywords.csv")
    assert [item.kind for item in sink.items] == ["download", "ledger"]
```

- [ ] **Step 2: Run the test to verify failure.**

Run: `python -m pytest tests/test_mission_events.py -q`

Expected: FAIL because `mission_sink` is unsupported.

- [ ] **Step 3: Implement event sink without coupling tools to daemon transport.**

Define a protocol with `record_evidence`, `request_attention`, and `set_step_state`. Inject it into `ToolRegistry` and `CodingAgent`. Map read/inspect results to `inspection`; successful browser actions to `browser_observation`; downloads to `download` plus `ledger`; writes/checkpoints to `diff`; verifier success/failure to `verification`. Redact argument values for secret fields before creating evidence. The CLI provides a client-backed sink for live missions and a no-op sink for unit tests.

- [ ] **Step 4: Run the test to verify passing behavior.**

Run: `python -m pytest tests/test_mission_events.py tests/test_research_workflow.py tests/test_tools.py -q`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add advertpreneur_cli/tools.py advertpreneur_cli/agent.py advertpreneur_cli/cli.py advertpreneur_cli/missions.py tests/test_mission_events.py
git commit -m "feat: project tool evidence into missions"
```

### Task 9: Release integration and verification

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `advertpreneur_cli/__init__.py`
- Modify: `advertpreneur_cli/cli.py`
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_package.py`

- [ ] **Step 1: Write failing package coverage for the new daemon modules.**

```python
def test_release_archive_contains_mission_control_modules(tmp_path):
    archive = build_release(Path.cwd(), tmp_path / "release.zip", "0.22.0")
    with ZipFile(archive) as package:
        assert "advertpreneur_cli/missions.py" in package.namelist()
        assert "advertpreneur_cli/mission_daemon.py" in package.namelist()
```

- [ ] **Step 2: Run the test to verify failure.**

Run: `python -m pytest tests/test_release_package.py -q`

Expected: FAIL until packaging includes the modules.

- [ ] **Step 3: Finish versioning, documentation, and CI.**

Bump all CLI version sources to `0.22.0`. Document daemon lifecycle, mission recovery, attention routing, evidence completion rules, and the explicit local-only security boundary. Update release CI to run mission, cockpit, daemon, and existing release tests before packaging. Ensure the release archive includes all Python modules through the existing packaging mechanism.

- [ ] **Step 4: Run full verification and package.**

Run:

```powershell
python -m unittest
python -m pytest -q
python -m compileall -q advertpreneur_cli
python -m advertpreneur_cli.release_packaging --source . --output dist/release --version 0.22.0 --extension-version 0.5.2
```

Expected: all tests pass; `dist/release/AdvertpreneurCLI-0.22.0-windows.zip`, manifest, and checksums exist.

- [ ] **Step 5: Commit, tag, and publish only after green verification.**

```bash
git add README.md pyproject.toml advertpreneur_cli .github/workflows/release.yml tests
git commit -m "feat: ship ADP control plane"
git push origin main
git tag v0.22.0
git push origin v0.22.0
```

Verify the completed GitHub workflow and release assets before reporting the release.

