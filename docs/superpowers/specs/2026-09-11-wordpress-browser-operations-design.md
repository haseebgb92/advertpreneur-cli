# WordPress browser operations design

## Goal

Extend Advertpreneur CLI and its Browser Bridge extension so an operator can
control an ADP-owned tab in their normal browser, authenticate to WordPress via
the browser's own credential/session store, manage WordPress resources, prepare
local upload artifacts, and see verified progress without manually pairing each
new tab.

## Scope

### Extension-first browser lifecycle

The CLI will auto-start the localhost Browser Bridge broker when browser work is
requested. A once-installed extension background worker registers with that
broker. The CLI creates and controls an ADP-owned tab through the registered
extension; there is no per-tab pairing or manual connect action.

The existing visible Playwright browser remains a fallback only when the
extension is unavailable. Its status must make clear that it is isolated and
cannot reuse the normal browser's saved WordPress login.

### WordPress authentication

`wp-admin` navigation detects the login screen and reports `login needed in
browser`. The CLI neither asks for nor stores a WordPress username or password.
The operator signs in through the visible browser, including use of its saved
password facility. The controller waits for and verifies a WordPress dashboard
or requested admin screen before continuing.

### WordPress operations

Provide typed browser operations for posts, pages, plugins, themes, settings,
media, and host file-manager navigation. Actions must use observed browser state
and return proof such as a changed URL, a WordPress success notice, a listing
row change, uploaded-file presence, or an activated status. A successful command
dispatch alone is not evidence of a completed WordPress action.

### Local upload workspace

Each project receives `.advertpreneur/wp-files/` for staged source files,
archives, and safe extraction directories. Commands list, select, copy/move
within the workspace, build ZIPs, and extract ZIPs there. They must reject paths
outside the workspace unless a user deliberately stages a source file into it.
The selected file is passed to a browser file-input upload action for wp-admin
or an already-authorized host file-manager tab.

### Destructive-change review

Removals and overwrite-capable operations are two-stage:

1. Inspect and print a proposal with every exact target, target type, path or
   admin name, active/dependency warning, and intended operation.
2. Wait for an explicit user approval before the browser performs the mutation.

The operator can approve, cancel, or amend the proposal. No implicit approval,
bulk wildcard deletion, or credential capture is allowed.

### Status and health

`/browser status` and the normal CLI status surface show broker health,
extension registration, provider mode, controlled tab URL/title, detected login
state, current operation, progress stage, last verified result, and an actionable
reason when the extension or login is unavailable.

## Components and boundaries

| Component | Responsibility |
| --- | --- |
| Browser Bridge broker | Command queue, extension availability, command/result correlation, progress state |
| Extension service worker | Create/select ADP-owned tab, execute tab actions, upload selected local file, send progress/result |
| BrowserController | Normalizes browser commands, tracks local state, exposes verified results and fallback |
| WordPress operations module | Detects admin/login state, constructs safe admin flows, validates post-action evidence |
| Workspace module | Restricts staging/archive operations to `.advertpreneur/wp-files/` |
| CLI/TUI | Commands, explicit review prompts, clear status and outcome rendering |

## Command surface

The final command names will follow the existing `/browser` conventions. The
expected capabilities are:

- `/browser open <url>` and `/browser status`
- WordPress-aware open/status and login-wait actions
- Local workspace list/stage/zip/extract/move operations
- Upload a selected staged file to an observed file input
- Inspect/propose destructive plugin/theme/file actions, followed by explicit
  approve or cancel

The agent tool schema will expose matching structured actions so provider-led
work can use exactly the same approval and verification paths.

## Error handling

- Extension absent: clearly report it, provide its unpacked path, and retain
  visible Playwright fallback without claiming normal-profile access.
- Broker unavailable: auto-start it and report a specific startup failure if it
  cannot become healthy.
- Login required: pause at the browser login state; never solicit credentials in
  the CLI or write them to local config.
- Selector/UI drift: stop before a mutation and return the measured page state;
  do not guess a destructive click.
- Upload failure: retain the staged file and show the browser error/observed
  input state.
- Active/dependent deletion: flag it in the proposal; approval remains required.

## Testing and verification

Test-driven changes will cover broker command handling, extension command
surface packaging, local workspace boundary checks, destructive proposal
approval, WordPress login detection, and status rendering. Each new behavior
will be written as a failing test before its production implementation. Final
verification will run the focused suite, the full pytest suite, Python compile
checks, and extension manifest/source contract checks.

## Non-goals

- Store or transmit WordPress passwords through the CLI or broker.
- Bypass WordPress/host authorization or security controls.
- Delete, overwrite, activate, or deactivate an item without an explicit
  operation request and the destructive-action review when applicable.
- Claim browser success without visible/observable evidence.
