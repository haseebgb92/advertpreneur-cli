# ADP Operating System Design

## Goal

Deliver the eleven requested ADP capabilities as a dependable local operating environment: visible task control, provider-neutral actions, safe autonomous operations, durable evidence, and resumable proactive work. Every release must be independently usable, testable, updateable, and reversible.

## Scope and Releases

### v0.22 — Control Plane

1. **Task cockpit.** The fixed terminal surface shows one compact mission row, current state/tool, elapsed time, attention state, browser/download counts, changed-file totals, and verified result status. Mouse/keyboard expansion reveals bounded details without hiding the composer.
2. **Plans that turn into execution.** Each task has a locally stored `Mission`: intent, ordered steps, action/evidence states, retry count, and checkpoint reference. The planner creates an execution-ready plan before the first provider action; tool events move steps through `pending`, `active`, `waiting`, `verified`, `failed`, or `skipped`.
3. **Evidence-first completion.** A task may only report `completed` when every required evidence item is recorded. Code changes require checkpoint/diff and appropriate verification; browser changes require an observed success state; downloads require a local file and ledger entry; no-change tasks require observed inspection evidence.
4. **Human attention routing.** Sign-in, MFA, CAPTCHA, payment, irreversible action, missing choice, or failed verification creates a durable attention request. The task transitions to `waiting`, the composer remains active, and a response resumes the exact mission step. Approval requests are owned by the CLI event loop, never a worker thread.
5. **Improved terminal interaction.** The composer preserves drafts and queued messages while missions run. The cockpit, attention cards, plan, evidence, browser tabs, and changed-file details are navigable with keyboard and mouse. Full-screen panes restore the same composer and current mission state.
6. **Windows background server foundation.** A single-user local daemon owns mission persistence, broker registration, and recovery metadata. The CLI can reconnect after a window restart; it never runs arbitrary tasks without an explicit queued mission. A PID/lock, version handshake, health endpoint, graceful shutdown, and stale-process recovery prevent duplicate daemons.

### v0.23 — Operations Fabric

7. **Unified action layer.** All providers receive the identical typed action contract: inspect/read, browser, filesystem, Windows, Git, WordPress, verification, and controlled external search. Action schemas declare risk, idempotency, required evidence, and approval policy; provider text is never treated as an action without schema validation.
8. **Browser mission control.** Browser slots are persisted as named mission resources. The cockpit reports slot, URL/title, bridge health, last action, download marker, and routine status. Browser actions carry a mission ID and slot; spawned tabs are captured only within the launcher window. Captured screenshots/DOM observations and downloads are attached as evidence. No credential, CAPTCHA, identity rotation, or stealth capability is added.
9. **Operation-specific specialists.** Deterministic routing selects specialists from task intent and observed project/browser state: browser researcher, WordPress operator, release engineer, local-file operator, diagnostic investigator, project reviewer, and content/SEO operator. Specialists provide constraints, plans, evidence requirements, and recovery rules; they do not bypass the unified action layer or grant extra permissions.
10. **Windows/local operation control.** Local file and Windows operations are routed through typed operations with allowlisted launchers, path containment, explicit mutation classification, reversible staging where possible, and post-action observations. The local model may decide *which* action to request but cannot run an unbounded shell command.

### v0.24 — Intelligence Layer

11. **Project intelligence.** An incremental local project profile detects repository state, framework, package manager, tests, build/release commands, deployment clues, WordPress structure, risk files, and verified historical commands. Each field has source, timestamp, confidence, and invalidation rules; a model must inspect low-confidence data instead of treating it as fact.
12. **Safer automation memory.** Local memory records only operational facts: successful selectors, file conventions, verified commands, preferred project paths, recovery instructions, and evidence links. It stores scope, confidence, expiry, provenance, and an invalidation condition. Passwords, tokens, form secrets, session cookies, one-time codes, raw prompt content, and browsing content are rejected before persistence.
13. **Proactive activation.** The background server evaluates scheduled workforce items and incomplete missions. It may prepare index/profile refreshes and emit a notification or ready-to-run mission, but it cannot start an external mutation, browser action, or network-dependent workflow without the user’s existing policy and a safe, persisted mission.
14. **Learning with operations.** A successful, evidence-backed operation can propose a reusable routine or memory entry. ADP stores it automatically only when it is non-sensitive, scoped to the same project/site, and passes validation; otherwise it presents a reviewable proposal. Failed actions record a compact recovery observation, never speculative fixes.

## Core Domain Model

`Mission` is the durable unit of work. It includes `id`, `project`, `request`, `status`, `created_at`, `updated_at`, `provider_context`, `plan`, `resources`, `attention`, `evidence`, `checkpoints`, and `result`.

`MissionStep` includes `id`, `title`, `action_kind`, `state`, `risk`, `depends_on`, `attempts`, `last_error`, and `evidence_requirements`.

`EvidenceItem` includes `kind`, `location`, `observed_at`, `producer`, `hash_or_summary`, `verified`, and `mission_step_id`. Valid kinds include `inspection`, `diff`, `verification`, `browser_observation`, `browser_screenshot`, `download`, `ledger`, `approval`, and `release_asset`.

`AttentionRequest` includes a stable ID, reason, safe response options, linked mission/step, expiry, and resolution. It pauses only the affected mission.

`AutomationMemory` is a separate local store with `scope`, `fact`, `provenance`, `confidence`, `expires_at`, `sensitive=false`, and `invalidated=false`.

## Data Flow

1. The user submits a message to the persistent composer.
2. The daemon creates or resumes a mission and records its execution plan.
3. The cockpit renders mission and plan state from durable events.
4. A selected provider or specialist proposes typed actions through the Unified Action Gateway.
5. The gateway validates risk, mission state, project containment, approval policy, and required prerequisites.
6. The action executor returns structured observation/evidence. The mission reducer updates the plan.
7. If the action needs human attention, the reducer emits `AttentionRequest`; the user response resumes the specific step.
8. Completion requires all mandatory evidence and verification. The final card states both completion and any unresolved boundary.

## Security and Safety Boundaries

- Credentials remain in browser/provider-owned stores. ADP never reads, receives, persists, or transmits them.
- CAPTCHA, MFA, traffic controls, payment approvals, and identity checks are hard stops, never targets for avoidance.
- Every mutation has a declared risk: `safe`, `review`, or `irreversible`. Irreversible actions require a specific reviewable proposal and explicit approval.
- Browser, Windows, filesystem, and Git actions are project/mission scoped; the action gateway rejects paths/resources outside permitted scope.
- The daemon binds only to loopback, authenticates each CLI connection with a local rotating token, and exposes no remote listener.
- Memory writes pass a sensitive-data classifier and a strict field allowlist before disk persistence.

## Reliability and Recovery

- Mission events are append-only JSONL plus a compact state snapshot written atomically.
- The daemon recovers pending missions as `interrupted`, not `running`, after a crash; the user can resume them from the last verified step.
- Idempotent actions may retry with bounded attempts and evidence re-checks. Non-idempotent actions never auto-retry after an unknown outcome.
- The background server uses one lock, PID validation, protocol/version negotiation, and health status to avoid split-brain operation.
- Browser bridge outages preserve tab metadata and transition the relevant step to attention; they do not silently fall back to unmanaged browser control.

## Acceptance Criteria

### v0.22

- A task can be closed and reopened with mission, plan, attention, evidence, and queued follow-up intact.
- The composer remains usable while a mission runs and while attention is requested.
- A task cannot become completed without its declared evidence requirements.
- A tool approval from a worker is presented exactly once by the main UI thread.
- Daemon start/reconnect/duplicate/stale-lock paths have automated tests.

### v0.23

- Codex, AGY, Ollama, and cloud providers see identical action schema names and risk semantics.
- Browser slots, action evidence, downloads, and health are visible in the cockpit and survive CLI restart.
- A specialist can narrow actions and evidence requirements but cannot bypass policy.
- Local file/Windows mutation tests prove containment, approval, staged recovery, and observation.

### v0.24

- Project intelligence updates incrementally and exposes confidence/provenance.
- Sensitive strings are rejected from automation memory in unit tests.
- Learned routines are only saved after observed success and scope validation.
- Scheduled/incomplete work produces ready missions or notifications; unsafe external actions never start autonomously.

## Non-Goals

- Replacing every model with an ADP-owned model.
- Hidden browser automation, anti-detection, CAPTCHA bypass, credential scraping, or IP rotation.
- A multi-user network service or cloud storage of user task history.
- Executing arbitrary shell text from a provider without action schema validation.

