# Provider-Neutral Action Gateway Design

## Goal

Give every Advertpreneur provider the same ADP action capabilities: project
workspace operations, web research, Browser Bridge control, site-panel work,
upload staging, and approval-aware destructive operations. Selecting Codex,
AGY/Gemini, Ollama, or a cloud model changes only the model/runtime, not what
the agent is allowed to ask ADP to do.

## Scope

The gateway is the authoritative action boundary for external providers. It
uses the existing `ToolRegistry` as the single implementation of browser, web,
filesystem, WordPress, upload, and approval behavior. Existing local/Ollama
tool calling remains supported and continues to execute through that registry.

AGY/Gemini and Codex external runs receive a concise action contract. When an
external provider asks for an ADP action, ADP validates the request against the
existing tool schemas, executes it locally, records the result, and sends the
verified result back to the same provider conversation. The provider continues
until it returns a completion response, reaches a real login/approval block, or
hits the existing repetition/turn guard.

## Action Protocol

External providers emit one JSON request per action in a fenced `adp_action`
block:

```json
{"tool":"browser","args":{"action":"navigate","url":"https://example.com"}}
```

ADP accepts only a registered `ToolRegistry` tool name and object arguments.
Malformed requests, unknown tools, and invalid arguments become structured
tool-error results; they never cause shell execution or bypass the registry.

ADP returns the tool result in an `adp_action_result` block on the next turn.
Providers are instructed to inspect that evidence and either request the next
action or finish. This protocol keeps each external provider independent of
MCP support while preserving the actual local browser and approval boundaries.

## Safety and State

- All providers use the current `ToolRegistry` approval mode and operation
  controls; no provider gets a less restrictive route.
- Read-only and currently permitted reversible operations run as they do for
  the local agent.
- Browser login is never automated with credentials. A visible login result
  stops the action loop and reports `Login needed in browser`.
- WordPress deletion remains proposal → displayed targets → explicit approval
  → deletion. The gateway cannot collapse these steps.
- Browser operations use Browser Bridge when its extension is available,
  creating/focusing the controlled browser tab without manual connection.
- Every action result is recorded in existing working evidence/telemetry paths
  so the final report is based on observed results.

## Components

1. `advertpreneur_cli/action_gateway.py`
   - Parses the fenced action/result protocol.
   - Validates and dispatches tool calls through `ToolRegistry`.
   - Applies bounded turn/action and duplicate-action safeguards.
   - Produces provider-facing continuation prompts.

2. `advertpreneur_cli/cli.py`
   - Routes all external providers through the gateway rather than one opaque
     provider call.
   - Keeps the current provider session ID across gateway turns.
   - Displays action activity in the persistent terminal status surface.

3. `advertpreneur_cli/provider_harness.py`
   - Remains the provider transport layer.
   - Runs one provider turn per gateway iteration and retains Codex MCP as an
     optional acceleration, not the only way to access ADP actions.

4. Tests
   - Verify identical action dispatch for AGY and Codex.
   - Verify a public browser navigation request reaches `ToolRegistry`.
   - Verify malformed protocol output, repeated requests, login-needed, and
     deletion approval are handled safely.

## Error Handling

An unavailable Browser Bridge, failed web request, blocked approval, or invalid
action returns a concise observed error to the selected provider. The provider
may choose a valid recovery action. Login-needed and deletion-approval states
stop the loop for the operator rather than letting the provider guess.

## Verification

Tests will first prove that AGY and Codex receive the same gateway action
contract and dispatch through the same `ToolRegistry`. A public-page smoke test
will then verify a selected external provider causes ADP to open a Browser
Bridge tab without any manual browser command. Live WordPress work remains
read-only unless separately requested.
