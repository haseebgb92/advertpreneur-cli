# Helium 10 Amazon Xray Workflow Design

## Goal

Let Advertpreneur run an authorized, low-volume keyword-research workflow in a
Browser Bridge-controlled tab: generate a keyword list from user samples, use
the user's saved Helium 10 browser session, search one keyword at a time, and
download the account-authorized Xray report.

## Browser and account boundaries

ADP opens and controls its own Browser Bridge tab; it never takes control of
the operator's physical mouse or keyboard. It relies only on the browser's
existing saved session. It never reads, stores, displays, or transmits account
credentials, cookies, MFA codes, or session tokens.

The workflow uses normal observed browser pages and conservative, fixed
one-at-a-time pacing. It must not rotate IP addresses/identities, bypass a
challenge, solve CAPTCHAs, disguise automation, or retry an access warning.
MFA, CAPTCHA, verification, rate-warning, sign-in, or unexpected page state
creates a durable paused checkpoint for the operator to resume after resolving
the visible browser step.

## Workflow

1. ADP accepts a task with keyword samples and a configured/shared Helium 10
   landing URL.
2. It generates a bounded, deduplicated keyword queue locally and writes it to
   a CSV ledger before the first search.
3. It opens the controlled tab, navigates to the user-supplied Helium 10 URL,
   and verifies the expected account landing state. It waits for MFA when
   required.
4. For each queued keyword, it performs one normal visible Amazon/Helium 10
   search, verifies the results surface, requests the report export exposed by
   the authorized account, and waits for the browser download outcome.
5. It moves the downloaded report into a task-specific local directory and
   renames it from the keyword using a safe filename plus a collision-resistant
   suffix. It records the observed path and status in the CSV.
6. It proceeds to the next keyword without per-keyword confirmation until the
   queue completes or a hard-pause condition occurs.

## Local artifacts and learning

Each run writes a CSV under the project-local `.advertpreneur/research/` area
with: keyword, state, started/finished times, report path, original filename,
renamed filename, browser URL, and a concise observed error/pause reason.
Completed rows are skipped on resume. The report directory and CSV never
contain credentials or browser tokens.

ADP stores only successful, evidence-backed semantic navigation checkpoints in
its local browser-routine store after repeated successful runs. Failed steps
are stored as local failure evidence so the workflow does not blindly repeat
them. It does not learn anti-detection techniques or security-challenge
responses.

## Validation

Tests will cover keyword queue creation, CSV creation/update/resume, safe
keyword filenames, downloaded report move/rename, duplicate prevention,
pause/checkpoint state, and enforcement that challenge/warning states never
advance the queue. Browser integration tests will use a local controlled-page
fixture; no Amazon or Helium 10 automation is performed in tests.
