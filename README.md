# Advertpreneur CLI v0.21.5

Advertpreneur CLI (`advertpreneur` or `adp`) is a Windows-first coding and site-operations CLI. It can use Ollama/Ollama Cloud, OpenAI Codex, and Google Antigravity while keeping project discovery, context planning, evidence, packaging, resource checks, WordPress/browser operations, and most orchestration local.

## v0.21.5 — reliable multi-tab Amazon / Helium 10 research

- An explicit Softzilla or Amazon URL now opens in its named browser tab before provider planning. Escape interrupts the active external provider turn, prevents its next action, and wakes the composer to dispatch the queued replacement message without another keypress.

- Browser Bridge now keeps named tab slots: `access` for the member portal, `helium` for the launched Helium 10 app, and `amazon` for the research work. Capturing Launch Web App is restricted to the same browser window as its launcher.
- Amazon research is pinned to the `amazon` slot. After each CSV download is recorded and renamed, ADP refreshes that same tab before the next keyword while leaving Helium 10 open.
- The workflow tells every supported provider to verify New York ZIP `10001`, use the observed Xray **Load more** control, and retry the observed modal refresh once after 30 seconds if data has not appeared.
- Normal visible browser clicks and field entry no longer create a per-action approval prompt. Destructive operations remain proposal-gated; any required approval is now handed back to the CLI thread safely instead of crashing Prompt Toolkit.
- Reload the already-installed Browser Bridge extension once after this update so its `0.5.2` tab-slot logic is active.

## v0.21.4 — compliant Amazon / Helium 10 Xray research runs

- ADP can turn provided product samples into a model-generated keyword list, then run a visible, one-keyword-at-a-time Amazon / Helium 10 workflow through the connected Browser Bridge.
- Each run keeps a resumable local CSV ledger in `.advertpreneur/research/<run>/keywords.csv`, records completed reports, and moves/renames each downloaded Xray export into that run’s `reports/` folder.
- The first verified search/export records only its successful observed selectors for reuse; the remaining queue can then run one keyword at a time without rediscovery or a model turn per keyword. Failed actions and completed downloads remain visible in the local ledger.
- Browser Bridge now observes export downloads directly. Reload the already-installed extension once after this update so its new `downloads` permission is active.
- Sign-in, MFA, CAPTCHA, access, traffic, or rate warnings are hard checkpoints. ADP never handles credentials, solves challenges, rotates IPs, or disguises automation.

## v0.21.3 — AGY model-defined thinking

- Claude and GPT-OSS models selected through Antigravity now run without ADP passing an unsupported `--effort`/reasoning level. Their model-defined thinking stays entirely provider-owned; Gemini and other AGY models retain their existing effort selection.

## v0.21.2 — live change panel

- The active work area now sits directly above the fixed composer and shows observed changed files with aggregate `+added/-removed` totals.
- Click the panel to expand or collapse bounded per-file change totals while continuing to type in the composer.

## v0.21.1 — smooth active composer and one-line Windows install

- The active composer now uses Prompt Toolkit’s own 5 FPS renderer for the spinner and elapsed time. It stays responsive without the lower-screen ANSI flicker.
- Windows has a public, no-login bootstrap: `irm https://github.com/haseebgb92/advertpreneur-cli/releases/latest/download/INSTALL-ONLINE.ps1 | iex`. The downloaded installer verifies the release archive SHA-256 before installing it.

## v0.21.0 — local Windows operations

- All model providers can now request the same approval-governed local Windows operations through ADP: inspect readiness, open a verified local path, or launch a small allowlist of local utilities.
- Browser Bridge remains the preferred automatic browser control path whenever its extension is connected; ADP falls back locally only when it is unavailable.

## v0.20.9 — visible update notes

- After a successful `/update`, ADP now displays the GitHub release notes in a `What's new` section.

## v0.20.8 — stable active composer and public installation

- Active tasks no longer run the competing ANSI repaint loop that caused terminal flicker.
- The composer remains open while ADP works: Enter schedules the next instruction; Escape gives a follow-up priority after the current safe task boundary.
- AGY reasoning is selected through the model itself; no second low/medium/high prompt is shown.
- Public releases can install and update without GitHub CLI authentication.

## v0.20.7 — fixed composer surface

- A dedicated `Message Advertpreneur` composer row remains fixed above the joke and status bars while work is active, and the idle prompt uses the same visual composer.

## v0.20.6 — persistent Advertpreneur chat and ownership

- The active task now keeps a stable Advertpreneur work card, composer, joke row, and status bar visible together.
- Every task ends with a durable Advertpreneur result card, including failures, interruptions, login boundaries, and approval boundaries.
- Provider workers are never credited in chat or generated text artifacts. Explicit worker credits are normalized in responses and fail task verification in changed text files.

## v0.20.5 — unified actions for every provider

- Every selected provider reaches the same ADP capabilities—Browser Bridge, web research, workspace files, WordPress operations, uploads, and safe command actions. Codex and Antigravity use the local `adp_action` contract; Ollama continues to use ADP's native tool loop.
- Each requested action is executed only through ADP's existing local tool registry, then its observed result is returned to the same provider thread for the next decision. The working footer changes to `Action · <tool>` while it is running.
- Login and destructive-operation safeguards are provider-neutral: a visible login stops with `Login needed in browser`; deletions remain a reviewed proposal that needs explicit approval.
- Native Codex MCP remains available as an optional direct connection, but it is no longer the only way a provider can request ADP operations.

## v0.20.4 — verified Codex Browser Bridge handoff

- Fixes the Codex app-server transport serialization so the automatic Browser Bridge MCP tools are actually visible to external Codex turns.
- Verified with a public-page external-Codex smoke test: Codex opened and inspected `example.com` through the extension-backed controlled tab.

## v0.20.3 — Codex-controlled live-site browser work

- Codex live-site tasks now receive an Advertpreneur-owned local Browser Bridge MCP server automatically when the request mentions WordPress, wp-admin, Hostinger, hosting panels, uploads, posts/pages, plugins/themes, or site settings.
- `browser_navigate` creates and focuses the controlled Edge/Chrome tab through the extension; it does not require a manually connected tab or a pasted CLI command.
- Codex can inspect, navigate, fill, click, upload project-local files, and verify the observed browser state. It must pause only for browser login or an explicit deletion proposal approval.
- The MCP server cannot delete or remove items. WordPress deletion remains a reviewed two-step operation.

## v0.20.2 — GitHub releases and verified terminal updates

- The public GitHub Releases page is the authoritative distribution channel.
- Every `v*` tag runs the release workflow: tests, Windows CLI ZIP, Browser Bridge ZIP, SHA-256 checksum file, and update manifest.
- On startup, the CLI checks for a newer release in the background and shows a non-blocking update notice.
- `/update` asks for approval, downloads the public release, verifies its SHA-256, updates the installed package, and asks for a restart.
- The Browser Bridge is bundled in every CLI release. Its unpacked extension folder stays stable, so users load it once and click **Reload** only after an extension update.

> No GitHub account, GitHub CLI, or token is required for public installation or `/update`.

## What it can do

- Work in existing codebases with checkpoints, project maps, validation, packaging, review, and persistent evidence.
- Operate a visible existing Chrome/Edge profile through Browser Bridge, including WordPress/wp-admin, hosting panels, file upload staging, plugins, themes, pages, posts, and settings.
- Keep WordPress credentials in the browser's own saved-password/session system; the CLI does not request or store passwords.
- Route work to proactive specialist roles and keep production mutations behind explicit approval.

## Previous releases

## V0.15.1 — lightweight Resource Guard

V0.15.1 keeps the v0.15 project-intelligence maturity layer and adds a transition-driven Resource Guard for low-power Windows machines and users running multiple Advertpreneur CLI instances. It adds **no monitoring daemon** and makes no model/provider calls.

- `/health` now shows physical RAM, instantaneous CPU, pressure band, this ADP process tree, and the cross-instance **ADP estate**.
- Pressure bands: Green `>=35%` RAM available; Yellow `20–35%`; Orange `12–20%`; Red `<12%`.
- Provider tasks register globally but are never auto-killed.
- Builds, large tests, full verification and packaging coordinate through a tiny cross-ADP heavy-work lock when pressure exists.
- New browser processes are blocked only at critical pressure; an already-running Edge controlled by Browser Bridge remains usable.
- Warm Codex/AGY sessions are adaptive: kept when RAM is healthy for token/cache reuse, trimmed under high pressure, and trimmed in Yellow when 3+ ADPs are open.
- `/health cleanup` releases this ADP instance's idle provider/browser helpers; `/health guard on|off` controls the guard.
- ADP itself is warned above a 100 MB lightweight target and strongly flagged above 200 MB.

## V0.15.0 — maturity release

This release deliberately removes the always-on Beacon and replaces that complexity with useful on-demand intelligence. Task sounds and desktop notifications remain. There is no Beacon process, WPF window, heartbeat, session-status directory, or Beacon asset.

### `/health` — RAM and process health, on demand

`/health` uses no model and leaves no monitoring daemon behind. It shows:

- Advertpreneur's own current and peak RAM;
- child/provider/browser processes spawned below the current ADP process when Windows exposes them;
- total managed RAM at that instant;
- project-local `.advertpreneur` state size;
- user-state/session/evidence/telemetry size;
- current warm Codex/AGY helper-session count;
- lightweight warnings when ADP itself or its managed tree becomes unusually large.

`/health cleanup` closes warm Codex/AGY helper processes without deleting provider conversation IDs or ADP sessions. The next provider task can resume normally, but may pay a cold-start/cache cost. AGY warm processes are capped at one per ADP process and stale sessions are evicted on provider reuse, balancing RAM against quota/cache efficiency.

### Project Contract — ADP understands how this project works

Every project gets a deterministic local contract at:

`.advertpreneur/project-contract.json`

It infers, where possible:

- project type/framework;
- important files and directories;
- protected paths that should not be edited casually;
- generated/build paths;
- build commands;
- validation commands;
- package type/name;
- package exclusions.

Use:

- `/contract` — inspect the current inferred contract;
- `/contract refresh` — rebuild deterministic inference;
- `.advertpreneur/project-contract.override.json` — intentionally override inferred fields for a particular project.

The override survives contract refreshes. This is how ADP can learn that one project has a special packaging command, protected directory, artifact name, or entry point without re-teaching every model on every task.

### WordPress / WooCommerce awareness

For standalone WordPress themes/plugins, contract inference understands common production structure including:

- `style.css` Theme Name / Text Domain;
- plugin `Plugin Name` headers;
- `functions.php`, `theme.json`, templates, `template-parts`, `inc`, assets and WooCommerce overrides;
- WordPress core and dependency/vendor boundaries;
- PHP syntax validation;
- production ZIP naming;
- top-level theme/plugin folder structure inside the ZIP;
- `.distignore` when present.

ADP never treats WordPress core, `.git`, `.advertpreneur`, `.env`, `node_modules` or dependency source as ordinary edit targets simply because they are visible on disk.

### `/taskplan` — local context planning before spending tokens

Before every real task, ADP classifies it locally with zero model calls. The task plan determines:

- task class: inspection, code change, debug/fix, release/package, etc.;
- low/medium/high complexity;
- likely files from the local project map and explicit paths;
- whether browser, web, MCP, plugins/skills are actually warranted;
- proportional framework/evidence/handbook context budgets;
- whether full validation or packaging is requested.

Use `/taskplan <instruction>` to preview this decision without calling a provider.

The plan is intentionally compact. Codex receives project/task rules through its native developer/thread configuration; AGY receives a bounded workspace/task plan; Ollama receives the same project intelligence through its task-aware system extension. ADP does not duplicate the full plan into every message path.

### Smarter context/tool exposure

- trivial deterministic greetings/probes stay on the zero-token local fast path;
- project evidence and project-map targets are preferred to broad rescans;
- framework/handbook/evidence blocks scale to task complexity rather than fixed maximums;
- plugin catalogues are not injected merely because a task mentions HTML/CSS;
- MCPs remain task-scoped and Codex transport definitions are preserved correctly;
- Ollama coding tool schemas remain lazy;
- repeated identical reads/tool calls are guarded;
- provider thread/session reuse remains enabled to preserve cache efficiency.

### Proportional verification

ADP now performs bounded local safe checks after changed files are checkpointed:

- PHP: `php -l` when PHP is available;
- Python: in-memory compile check without creating `__pycache__`;
- JavaScript: `node --check` when Node is available.

The final notification happens after these safe checks, so a model cannot finish with a success notification and then silently fail local syntax verification.

Use:

- `/verify` — safe checks for the latest/current change set;
- `/verify full` — inferred project validation + build commands as well.

Full project checks remain explicit because a complete Android/Node build can be expensive in time/CPU even though it costs zero model tokens.

### Diff intelligence

After a task checkpoint, ADP locally summarizes the edit surface and risk:

- changed-file count;
- line delta when a Git checkpoint permits exact comparison;
- low/medium/high risk based on sensitive paths/configuration and change size.

Authentication, payment/checkout, migrations/schema, permissions, manifests/Gradle and important runtime configuration are treated more cautiously than a small CSS/asset change.

### `/package` — contract-driven clean release artifacts

`/package` is deterministic and provider-free. It:

1. collects production files according to the project contract;
2. syntax-checks applicable package code;
3. runs inferred validation/build commands;
4. refuses packaging when those required checks fail;
5. creates a clean ZIP;
6. verifies ZIP CRC;
7. verifies common runtime/development directories did not leak into the artifact;
8. always excludes obvious secret-like files even if an override accidentally weakens normal exclusions.

Default exclusions include `.git`, `.advertpreneur`, `node_modules`, virtualenvs, test/coverage/editor/runtime folders, logs, existing ZIPs and `.env*`. WordPress theme/plugin archives use the package slug as the top-level ZIP directory.

Package output is written to a sibling `packages` directory so the generated ZIP cannot recursively package itself.

### Resource/cost accounting

Local harness telemetry now records top-level task metadata such as:

- provider/model;
- provider turns;
- tool calls;
- input/output;
- provider cache reads when available;
- thinking tokens when exposed as safe usage metadata;
- task duration;
- ADP RAM at task start/end;
- changed-file count;
- task classification.

`/insights today` and `/insights week` use top-level task rows as the accounting source of truth, avoiding the previous possibility of double-counting an external coding run once as a task and again as a provider event.

No source bodies, prompts, credentials, auth tokens, or hidden reasoning are stored in this telemetry.

## Existing provider efficiency retained

### Codex

- persistent native app-server connection/thread for normal ADP coding sessions;
- native `turn/start` follow-ups instead of a fresh `--ephemeral` CLI on every prompt;
- native quota windows and model discovery;
- task-scoped MCP configuration with complete transport-preserving overrides;
- explicit Low / Medium / High reasoning only; no silent max/xhigh;
- context rollover only when provider input growth becomes clearly abnormal.

### Antigravity (AGY)

- persistent `stream-json` process/conversation per compatible ADP session;
- raw `/usage` quota fractions parsed without consuming model turns;
- separate Gemini and Claude/GPT quota pools;
- model slugs that already encode `-low/-medium/-high` are not combined with a conflicting effort flag;
- additive AGY cache semantics are displayed as new input + cached context + context-reuse percentage;
- workspace grounding keeps requested files in the ADP project rather than AGY scratch artifacts.

### Ollama / Ollama Cloud

- local/project context compaction;
- task-aware coding tools;
- optional project-map enrichment remains local-model-only;
- no coding tool schemas for ordinary general chat;
- repeated read/tool result compaction and loop protection;
- metered pricing/budget safeguards for configured cloud models.

## Browser Bridge

The local Browser Bridge remains an optional one-session ↔ one-ChatGPT-conversation relay on `127.0.0.1:8765`.

Use:

```text
/bridge on
/bridge extension
/bridge status
/bridge autopilot on
/bridge autopilot off
```

Bridge events are turn-correlated and idempotent. It relays completed structured results, not hidden reasoning, raw source bodies, credentials or continuous terminal streams.

### Direct WordPress browser operations

Install the unpacked Browser Bridge extension once using `/bridge extension`. Afterwards `/browser <url>` starts the local broker automatically and opens an ADP-owned tab in your normal Edge/Chrome profile—no per-tab connection step. WordPress passwords remain in the browser's saved-password/session store; the CLI never asks for or saves them.

```text
/browser https://example.com/wp-admin/
/browser wp status
/browser files stage C:\Downloads\my-plugin.zip my-plugin.zip
/browser upload my-plugin.zip
/browser wp propose-delete plugin:Old Plugin:inactive|theme:Old Theme:inactive
/browser wp approve-delete <shown-token>
/browser wp delete <shown-token> <observed-delete-selector>
```

`/browser status` reports broker/extension availability, current operation, login-needed state, and verified result. Workspace commands stage files under `.advertpreneur/wp-files/`; `files zip`, `files extract`, and `files move` never operate outside it. A delete is always a two-stage review: the proposal prints every exact target and warning, then requires its shown approval token before the click can occur.

### Hosting site adapters

The browser workflow supports **WordPress wp-admin**, **Hostinger hPanel File Manager**, **cPanel File Manager**, and **Plesk File Manager**. A project profile stores only adapter and base URL; it never contains credentials, cookies, or host API tokens.

```text
/browser site detect
/browser site use hostinger https://hpanel.hostinger.com
/browser site open file_manager
/browser site use wordpress https://example.com
/browser site open plugins
/browser site upload my-plugin.zip
```

Adapters open only supported routes and reuse the visible browser session. They do not bypass host login or authorization; confirm the displayed panel and resulting listing after every live mutation.

For ordinary natural-language work, these commands are not required. When a task mentions a supported host, wp-admin, a WordPress upload, posts/pages, or site settings, Advertpreneur exposes browser control to its agent, starts the bridge when needed, detects the site adapter after navigation, and continues the workflow itself. With Codex, this is a direct local MCP tool connection to the Browser Bridge, so Codex opens and controls its own tab rather than asking you to run a browser command. It only pauses when the visible browser reports that you must complete a login or approve a destructive proposal.

Each detected site is classified as local, staging, or production and recorded without credentials in `.advertpreneur/operations.json`, along with current operation checkpoints and compact browser evidence. `/status` and the extension popup show the active operation surface so interrupted work can be resumed from its latest verified checkpoint.

Prebuilt, fail-closed workflows cover WordPress plugin/theme uploads and post publishing, plus archive deployment through Hostinger, cPanel, and Plesk file managers. The agent loads the matching playbook automatically, observes each panel before continuing, and stops for login, overwrite, deletion, or UI mismatch instead of guessing.

### Proactive agent workforce

Advertpreneur has local specialist roles: Operations Commander, WordPress Site Steward, Hosting & Deployment, Incident Investigator, Research & Resolution, QA & Conversion, Release Guardian, and Resource & Cost. Each task is routed to the strongest matching role before provider work begins. The role policy and only proven local routines are included in task context.

Due work stored in `.advertpreneur/workforce.json` wakes on the next interactive loop. Safe work is marked ready; production mutations are marked `awaiting_approval`. Verified outcomes are promoted from observed to proven routines after two successful evidence-backed runs. Incident and research roles inspect local evidence first and use the existing official-source web research path only when needed. No passwords, cookies, tokens, hidden reasoning, or autonomous production mutations are retained.

## Install and update on Windows

Install from any PowerShell window without GitHub CLI, a GitHub login, a ZIP download, or manual extraction:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
irm https://github.com/haseebgb92/advertpreneur-cli/releases/latest/download/INSTALL-ONLINE.ps1 | iex
```

The installer downloads the release archive, verifies its SHA-256 checksum, installs the CLI, and refreshes the Browser Bridge in its stable unpacked folder under `~/.advertpreneur-cli/browser-extension`. It preserves existing sessions, history, evidence, settings, and provider credentials.

### Install the Browser Bridge once

1. Start `advertpreneur` once after installation.
2. In the CLI, run `/bridge extension` to show the stable extension folder.
3. Open `chrome://extensions` or `edge://extensions`, enable **Developer mode**, choose **Load unpacked**, and select that folder.
4. After a CLI release that changes the extension, return to that browser page and click **Reload**. Do not load another ZIP or choose a new folder.

After that, start the CLI normally. It checks GitHub Releases in the background and displays an update notice when a newer version is available. Run:

```text
/update
```

`/update` asks before making changes, downloads the public release, verifies SHA-256, and updates the Python package. Restart the CLI after a successful update. If the extension changed, open `chrome://extensions` or `edge://extensions` and click **Reload** once; the browser keeps using the same folder and does not need another ZIP or installation.

For an offline/local source installation, run:

```powershell
cd D:\Cli
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\INSTALL.ps1
```

Verify:

```powershell
advertpreneur --version
```

Expected:

```text
0.21.5
```

Existing login/session/history/evidence/provider state under `~/.advertpreneur-cli` is preserved. The installer removes no provider credentials. On first v0.15 start, obsolete Beacon state/status files are cleaned locally.

## Useful commands

```text
/health                 RAM/process/session health
/health cleanup         release warm provider helper processes
/contract               inspect project contract
/contract refresh       re-infer project contract
/taskplan <task>        preview zero-model task/context plan
/verify                 safe changed-file verification
/verify full            full inferred project validation/build
/package                build/verify a clean release ZIP
/update                 check, verify and install the latest GitHub release
/index                   update deterministic project map
/map <query>             search project map
/handbook <query>        search validated local experience
/usage                   current usage/context footprint
/usage why               largest next-call context contributors
/providers usage codex   native Codex quota
/providers usage agy     native AGY quota
/insights today          local task/resource review
/insights week           local weekly review
/undo                    restore the latest task checkpoint
```

## Design principle

ADP should be light while idle and intelligent when working. Project understanding is persisted as small local metadata, expensive provider sessions are reused only when they save quota, optional capabilities are loaded only when relevant, and operational checks run on demand rather than through always-on monitoring services.
