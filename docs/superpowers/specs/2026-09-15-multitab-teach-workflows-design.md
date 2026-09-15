# Multi-tab Teach Workflows

## Goal

Expose ADP's existing local browser learner as `/teach <name>` and make it
reliably record a human-demonstrated workflow across named Browser Bridge tabs.
The primary case is `amazon-xray`: Softzilla access, Helium Web App launch,
Amazon/Xray actions, and a later replay with a new keyword list.

## Current gap

`/browser learn` already stores local routines, but it starts against the
default `work` tab and the Browser Bridge accepts human events only from that
one tab. Actions performed in `access`, `helium`, or `amazon` are ignored.
This makes a multi-tab demonstration incomplete and causes the model to fall
back to costly rediscovery.

## User experience

1. `/teach amazon-xray` starts a local recording and enables the Browser Bridge
   recorder for every currently controlled named tab. Those tabs are protected:
   replay must reuse them and must not close, replace, or navigate them except
   through a recorded step.
2. The user demonstrates the workflow in their browser. The extension records
   trusted clicks, safe field changes, scrolling, and page transitions together
   with the tab role that produced each event.
3. `/teach stop` stops extension recording, persists the workflow, and displays
   a review: protected tabs, their observed URL/title fingerprint, and a
   numbered step-by-step list of what will happen during replay.
4. A later task such as `run amazon-xray with <new keyword list>` reuses the
   preserved tabs, injects only the new non-sensitive keywords at the recorded
   search step, executes the recorded selectors, and maintains the existing
   research CSV/outcome ledger.

## Data and safety boundaries

- Saved workflow data is local under `.advertpreneur/browser` and contains only
  tab roles, URL fingerprints without query values, visible-control selectors
  and labels, safe action parameters, and verification metadata.
- Passwords, cookies, tokens, MFA/OTP values, payment data, raw form values,
  screenshots, and typed keyword values are never stored by `/teach`.
- A learned `fill` step becomes a parameter marker only for a clearly
  non-sensitive search field. Other fills are omitted rather than replayed.
- The recorder accepts events from the learned set of tab IDs, not just the
  default Browser Bridge tab. It attributes each event to a stable named slot.
- Replay refuses to proceed on a URL/page fingerprint or selector mismatch,
  login/MFA/CAPTCHA/traffic checkpoint, missing protected tab, or unexpected
  download outcome. It reports the exact failed step and asks for a focused
  re-teach.

## Replay semantics

- `access` and `helium` are setup tabs: they stay open after teaching and are
  not closed by replay.
- `amazon` is the parameterized work tab. The workflow may refresh this tab as
  a recorded completion step, but never creates repeated search tabs.
- Xray's Load More is condition-based: repeat only while observed rows grow;
  if stalled, use the taught Refresh control once and stop with a checkpoint if
  no data appears.
- CSV export is verified through Browser Bridge download evidence. The existing
  research workflow remains responsible for renaming/reporting outcomes.

## Commands and compatibility

- Add `/teach <name>`, `/teach stop`, `/teach cancel`, and `/teach list` as a
  simple public wrapper around the enhanced routine store.
- Keep `/browser learn ...` as an alias for compatibility; both paths share the
  same implementation and saved workflow format.
- The external provider action contract gains a short instruction to prefer a
  named taught workflow before visual rediscovery. No provider receives image
  attachment claims it cannot support.

## Verification

Focused automated tests will prove that multi-tab events are accepted and
attributed, sensitive/keyword values are excluded, `/teach stop` creates a
human-readable review, protected tab metadata is persisted, and replay stops
on mismatches rather than looping. Browser-extension syntax checks and the
existing browser/gateway/research tests remain required.
