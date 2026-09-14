# Xray Keyword Export Runner

## Goal

Make Advertpreneur CLI run a resumable, browser-observed Amazon/Helium 10 Xray keyword-export workflow without handling credentials, bypassing site protections, or claiming downloads that were not verified.

## Browser surfaces

- `access`: the logged-in Softzilla Members Area.
- `helium`: the Helium 10 Web App launched from the Diamond Access page. It remains open and is never reused for Amazon navigation.
- `amazon`: Amazon searches and Xray interactions.

The runner may navigate the `access` tab to the provided Softzilla URL. It uses only controls observed in the current page to open Diamond Access and capture the launched Web App into `helium`. A login, MFA, CAPTCHA, rate limit, verification page, or changed UI is a checkpoint that preserves progress and stops the affected keyword.

## Run and ledger

The operator supplies a comma- or newline-separated keyword list. ADP creates one `ResearchRun` with a durable keyword ledger and a master CSV. Each keyword has one of these terminal states:

- `completed`: an observed download exists, is renamed to the keyword, and is recorded with its local path.
- `no_data`: Xray did not populate data after the defined wait and one modal refresh.
- `download_missing`: export was invoked but no file appeared before the bounded download wait.
- `verification_required`: an observed site safeguard stopped the action.
- `site_changed`: an expected, observed control or result surface was unavailable.

Interrupted and checkpointed keywords remain resumable; no state is silently discarded.

## Per-keyword flow

1. Keep `helium` open. In `amazon`, inspect the delivery location and set ZIP `10001` only if the observed location differs.
2. Search the current keyword in `amazon`, then inspect the results page and invoke the observed Xray Analyze Products control.
3. Wait up to 15 seconds for populated Xray rows.
4. Click the observed Load More control while the observed ASIN/row count increases. After each click, wait 10-15 seconds.
5. If a wait yields no new rows, click the observed Xray modal refresh control once and wait again. If rows remain unavailable, record `no_data`.
6. When more rows are no longer available, click the observed Export control and its CSV menu item. Mark the download before export, wait for the actual download, and rename the file to a safe filename derived from the keyword.
7. Record the outcome, refresh `amazon`, and continue with the next keyword.

## Reliability boundaries

- Browser selectors are learned only after a successful observed action and are stored with the run for resume.
- Every click/fill/wait is pinned to its named tab; the runner does not infer a tab from the active browser surface.
- Progress is based on observed row/ASIN counts, not a fixed number of Load More clicks.
- One bounded modal refresh is permitted per stalled Load More state. Repeated failures are recorded rather than retried indefinitely.
- Browser control is only for the operator's existing authenticated session. ADP never asks for, stores, or enters credentials and never attempts to bypass a challenge.

## Validation

- Unit tests cover tab pinning, wait/refresh/load-more state transitions, download rename, and every terminal ledger status.
- Browser smoke test uses the connected extension to inspect existing tabs and verifies no controller exception occurs. It stops at any login or verification boundary.
