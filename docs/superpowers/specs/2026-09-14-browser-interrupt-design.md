# Browser Open and Immediate Interrupt Design

## Scope

This incident release fixes two operator-visible failures before the remaining ADP operating-system releases: an explicit Amazon or Softzilla URL can stall in provider thinking instead of opening, and Escape must interrupt the active provider turn before dispatching the replacement composer message.

## Decision

An explicit supported research URL is treated as a local browser instruction. ADP opens it in its named mission slot (`access` for Softzilla, `amazon` for Amazon) before provider planning. It records the attempt in the live UI. Authentication remains a browser-owned checkpoint; this release does not request credentials or automate sign-in.

Escape remains a safe-boundary interrupt. It signals the gateway, invokes the current provider transport's interrupt hook when available, prevents any newly proposed action from dispatching, and then releases the queued replacement instruction after the interrupted worker exits. Browser and filesystem mutations already in progress are not force-killed.

## Error Handling

Failure to open an explicit URL is rendered as a visible browser-mission error and normal planning continues. A provider transport that has already ended reports a safe-boundary yield. Provider exceptions not caused by an interrupt continue to propagate as task failures.

## Validation

Regression coverage proves that an explicit Softzilla URL is routed to `access`, an Escape signal prevents the next provider-proposed action, and each provider transport exposes a one-shot interrupt hook. The focused suite and full test suite must pass before the release is committed and pushed.
