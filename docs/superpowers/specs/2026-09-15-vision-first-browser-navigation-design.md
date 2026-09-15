# Vision-First Browser Navigation Design

## Goal

Let ADP navigate ordinary websites from human intent such as “click Login” or
“open Helium10 Diamond Access” without repeatedly sending oversized DOM/style
snapshots or requiring the provider to invent selectors.

## Approach

Use a budgeted hybrid observation loop:

1. The Browser Bridge captures a compressed viewport screenshot only when a
   named tab reaches a new page, modal, or otherwise changed visual state.
2. ADP sends the active provider a compact UI map containing only visible
   interactive controls: role, accessible label/text, position, and a stable
   observed selector when available.
3. The provider chooses an intent-level action from this evidence. ADP executes
   the observed selector and verifies a concrete state change (URL, title,
   modal, tab capture, download, or changed UI map).
4. DOM selector retries are a fallback for ambiguous visual states; full style
   snapshots are reserved for explicit reverse-engineering/design work.

## Token and Image Budget

- One screenshot at most per observed visual state.
- At most three screenshots per task stage; a stage is access, helium, amazon,
  or Xray modal.
- Screenshots are resized/compressed before provider attachment.
- UI maps are capped to visible interactive controls and a small character
  budget.
- Browser evidence is not appended repeatedly when its state fingerprint has
  not changed.
- On budget exhaustion, ADP uses the compact UI map only and reports an
  ambiguity rather than looping.

## Saved-Session Login Boundary

When the operator explicitly authorizes use of the saved browser session and
the observed login form is already filled, ADP may click the observed Login
control once. It never reads, requests, stores, or types credentials. MFA,
CAPTCHA, empty fields, and other verification remain hard stop boundaries.

## Durable Browser Learning

Store only non-sensitive navigation knowledge under the project’s
`.advertpreneur/browser/` state: page fingerprints, user-visible control
labels, successful intent-to-selector associations, and verification signals.
Never store screenshots, credentials, cookies, page text containing personal
data, session tokens, or raw form values. Reuse a learned association only when
the current page fingerprint and visible label match; otherwise rediscover it.

## Provider Compatibility

The same action registry remains provider-neutral. Providers that support image
attachments receive the bounded screenshot plus UI map. Providers without image
input receive the same UI map and verification results, so capability remains
available without pretending that a model saw an image it did not receive.

## Validation

Tests will cover screenshot/UI-map deduplication, state and image budgets,
saved-session login behavior, learned-action fingerprint matching, provider
fallback, and verification after click/tab capture.
