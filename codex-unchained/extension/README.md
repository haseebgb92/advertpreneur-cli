# ADP Browser Runtime v2

Chrome/Edge Manifest V3 prototype for the liberated Codex/ADP runtime.

The extension communicates only with the local ADP broker at `127.0.0.1:8765`.

Key rules:

- compact semantic control snapshots instead of full-page HTML;
- no form values in snapshots;
- `/teach` events only from trusted user interactions;
- sensitive field changes are never recorded;
- search/query/keyword values are replaced with `[TEACH_KEYWORD]`;
- existing tabs can be bound and reused;
- screenshots are requested separately, so deterministic replay does not consume vision tokens;
- download completion is verified by the extension.

Broker protocol endpoints are `/v2/browser/register`, `/v2/browser/next`, `/v2/browser/result`, and `/v2/browser/learn`.
