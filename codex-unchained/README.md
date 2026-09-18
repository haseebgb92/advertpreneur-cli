# ADP Codex Unchained

Provider-neutral Rust execution kernel for ADP. The model provider is the reasoning engine; Browser, Web, shell, MCP, Teach, project memory, and execution capabilities belong to Codex Unchained and ADP.

Current branch: `codex-unchained-v0.1`.

## What works

The workspace currently contains:

- provider-neutral tool/capability protocol;
- a unified model router for Ollama Local, Ollama Cloud, and Antigravity;
- native Codex `/model` switching through one live model catalog;
- a dedicated Unchained home at `~/.codex-unchained`, separate from normal `~/.codex`;
- host-owned Chrome/Edge Browser runtime over an authenticated localhost Rust broker;
- host-owned web search and web fetch tools;
- semantic `/teach` workflow compilation into `.advertpreneur`;
- zero-model deterministic workflow replay when browser state matches;
- local-model repair first, optional primary/cloud repair second;
- verified repair persistence and resume-after-repair;
- token/run accounting;
- a runnable `adp-unchained` CLI;
- reproducible compatibility patches against a pinned OpenAI Codex Rust revision.

The normal CI compiles every workspace crate, runs rustfmt, clippy with warnings denied, all Rust tests, extension syntax checks, and CLI smoke commands.

## Build locally

From this directory:

```powershell
cargo build -p adp-unchained -p adp-mcp --release
.\target\release\adp-unchained.exe --help
```

## Use the patched Codex Unchained CLI

The Windows package contains:

- `codex-unchained.exe`;
- `adp-mcp.exe`;
- the unpacked Chrome/Edge extension;
- the brain-only Antigravity agent;
- `INSTALL-CODEX-WINDOWS.ps1`.

Install it with:

```powershell
.\INSTALL-CODEX-WINDOWS.ps1 -AddToPath
```

The installer creates and owns:

```text
~/.codex-unchained/
    config.toml
    sessions/
    history/
    ...
```

Normal Codex continues to use `~/.codex`. The installer does not create or modify the normal Codex home.

The generated Unchained config points Codex at the local ADP model router on `127.0.0.1:8766` and registers `adp-mcp.exe`. Load the packaged `extension/` folder as an unpacked Chrome/Edge extension, then launch:

```powershell
codex-unchained
```

No provider flags are required for normal use.

## /model

Inside Codex Unchained:

```text
/model
```

uses Codex's native model picker. The ADP model router supplies a live catalog with namespaced entries:

```text
[Ollama Local] ...
[Ollama Cloud] ...
[Antigravity] ...
```

The model changes; the agent and host tools do not.

### Ollama Local

Local models are discovered from the running Ollama daemon on `127.0.0.1:11434`.

The default fresh-install model is:

```text
ollama-local/qwen3:1.7b
```

### Ollama Cloud

Cloud models are discovered dynamically from Ollama's current cloud catalog rather than hard-coded into ADP. A selected cloud model is prepared through the signed-in local Ollama daemon and then uses the same Responses-compatible route as local Ollama.

### Antigravity

Antigravity models are discovered dynamically through the user's existing authenticated `agy` CLI session.

The installer adds a dedicated global custom agent:

```text
~/.gemini/config/agents/adp-unchained-brain/agent.md
```

That agent has no Antigravity-native tools. Antigravity acts only as the reasoning backend and returns a schema-constrained decision: either a final message or one Codex host-tool request. Codex Unchained remains responsible for Browser, shell, MCP, approvals, and tool execution.

## Browser

The packaged extension talks to the ADP browser broker on:

```text
127.0.0.1:8765
```

Browser execution happens in the user's already-open Chrome/Edge profile. The model receives semantic controls rather than raw form values.

A useful first test inside `codex-unchained` is:

```text
Use the ADP browser tools to inspect the active browser tab. Tell me the title,
URL, and visible interactive elements. Do not click or modify anything.
```

## Model router

`adp-mcp.exe` hosts two local services:

```text
127.0.0.1:8765  ADP browser broker
127.0.0.1:8766  Codex Unchained model router
```

The router exposes a Codex-compatible `/v1/models` catalog and `/v1/responses` route. Ollama requests are proxied through the local daemon. Antigravity requests are translated between Codex Responses events and the authenticated `agy` headless interface.

This keeps the invariant:

```text
switch the brain
do not switch the agent
```

## Web

Host web search/fetch is independent of the reasoning model. If using ADP's Ollama-backed host web endpoint, set:

```powershell
$env:OLLAMA_API_KEY = "..."
```

Browser tools remain available without that web credential.

## Teach and replay

Teach a workflow by demonstrating it in the bound browser:

```powershell
adp-unchained teach helium-export --project D:\MyProject
```

Press Ctrl+C when the demonstration is finished. The recorder persists semantic steps incrementally.

Replay it later:

```powershell
adp-unchained replay helium-export --project D:\MyProject --keyword "bee wax wrap"
```

Replay first tries the learned workflow with zero model calls. On state divergence it can use the configured local repair model. A primary/cloud repair model is only used when configured and local repair fails. Repairs are saved only after verification.

## Privacy and safety of learned workflows

Teach/replay intentionally does not persist passwords, tokens, OTP/MFA values, payment fields, cookies, screenshots, raw DOM, or arbitrary typed form values. Search/query/keyword text is represented as the runtime placeholder `[TEACH_KEYWORD]`.

Potentially consequential clicks are learned but are not marked safe for deterministic replay.

## Upstream Codex compatibility surgery

`upstream-patches/` pins an exact OpenAI Codex revision and contains reproducible source transformations that:

1. preserve provider wire dialect for `tool_search`, supporting both native specialized calls and compatible function-call providers;
2. keep local tool discovery plus skill/plugin/app guidance enabled for unknown/custom model fallback metadata;
3. give the patched distribution its own `~/.codex-unchained` home and `codex-unchained` command identity.

`.github/workflows/codex-upstream-compat.yml` fetches the pinned upstream revision, applies all transformations, checks formatting, and runs focused upstream Codex tests.

The ADP Browser/Web runtimes remain ADP-owned rather than depending on a proprietary browser helper. Codex approval and sandbox machinery is retained.

## Windows package

The packaging workflow builds two artifacts:

- `adp-unchained-windows-x64`;
- `patched-codex-windows-x64`.

The patched package smoke-tests:

- upstream patch application;
- `codex-unchained` command identity;
- ADP MCP/model-router compilation;
- isolated installer behavior;
- dedicated `~/.codex-unchained` config generation;
- no creation of normal `~/.codex` by the installer.

## Branch isolation

This implementation remains intentionally isolated from `main`. It does not overwrite the older Python ADP tree or any newer local Rust migration that has not been pushed to GitHub.
