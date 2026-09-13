# Multi-tab Helium 10 and Amazon Research Design

## Goal

Keep the authenticated Helium 10 web app open in a persistent browser tab while ADP searches Amazon and exports one report per keyword from a separate Amazon tab.

## Design

Browser Bridge stores controlled tab slots rather than one global tab. `access` holds the Softzilla member page, `helium` captures the new Helium web-app tab opened by Launch Web App, and `amazon` is a separately created work tab. Browser actions accept an optional slot and never navigate another slot by accident. A click can wait briefly for a spawned tab and save it under a requested slot.

The research runner is pinned to the `amazon` slot. It checks New York ZIP 10001 through observed Amazon UI, processes each saved keyword one at a time, waits for the Xray export download, records and renames the report, then refreshes the Amazon tab before the next keyword. It preserves existing challenge/login safeguards and never handles credentials or bypasses verification.

## Reliability

The approval prompt is brokered to the CLI's main thread instead of being invoked directly by the task worker. MFA detection is based on concrete verification/login indicators, not a bare `mfa` text match.

## Validation

Unit tests cover named browser slots, spawned-tab capture, Amazon refresh after completed download, specific challenge detection, and thread-safe approval brokering. Browser-extension sources remain mirrored between release and package folders.
