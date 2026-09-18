
# Codex Unchained kernel prototype

This directory is the isolated first-stage kernel for the ADP/Codex jailbreak.

It deliberately does **not** rename Codex or replace the existing Advertpreneur CLI. The first milestone establishes interfaces that let a Codex-derived agent runtime expose the same ADP-owned capabilities to any compatible model provider.

## Principles

- Provider identity must not decide whether shell, browser, computer-use, MCP, project memory, or native ADP tools exist.
- Providers adapt to an ADP model/tool protocol; ADP does not shrink itself to a provider.
- Browser and computer execution state lives outside the model.
- Repeated work prefers executable memory over repeated reasoning.
- Escalation order is deterministic replay -> local model -> primary/cloud model.
- Successful repairs can update learned workflow memory.
- `.advertpreneur` remains the project-local durable brain.
- `/teach` stores semantic actions and verification, never credentials, cookies, MFA/OTP, payment data, screenshots, or arbitrary typed values.

## Crates

- `adp-protocol`: provider-neutral capabilities, tool calls, model events, execution tiers and budgets.
- `adp-memory`: `.advertpreneur` workflow/state persistence and conservative redaction.
- `adp-runtime`: provider-neutral tool exposure and repeat-task escalation policy.
- `adp-browser-bridge`: typed protocol shared by the Rust broker and Chrome/Edge MV3 extension.

## Browser extension

`extension/` is a v2 local-only Chrome/Edge bridge prototype. It reuses the existing-browser approach from ADP but changes the payload shape around semantic snapshots and actions. Page snapshots intentionally include only a compact set of interactive controls instead of raw DOM or form values.

## Next integration cut

The next patch targets current upstream Codex Rust at the tool-planning boundary:

1. normalize provider tool-call dialects into one internal call shape;
2. remove provider-name checks from tool exposure;
3. allow direct/deferred tools whenever the adapter can represent calls;
4. route Browser/Computer/MCP through ADP-owned runtimes;
5. attach `.advertpreneur` state to turn/session context without dumping the full store into model context.

See `docs/UPSTREAM_CODEX_INTEGRATION.md`.
