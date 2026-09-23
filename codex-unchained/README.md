# ADP Codex Unchained

Codex Unchained keeps the Codex agent experience and host execution layer while making the reasoning backend replaceable. Browser, Web, shell, MCP, skills, approvals, project memory, Teach/replay, and execution belong to Unchained; the selected model supplies reasoning.

Current development branch: `codex-unchained-v0.2`.

Normal OpenAI Codex remains isolated in `~/.codex`. Codex Unchained owns `~/.codex-unchained`.

## Core idea

```text
same Codex-style agent + tools
          |
          +-- /adp auto
          +-- /adp economy
          +-- /adp balanced
          +-- /adp max
          |
          +-- /model -> exact model pin
```

The `/adp` modes are routing policies. They choose from currently available Ollama Local and Ollama Cloud models. `/model` is the manual override and pins an exact model/provider. The Unchained provider catalog is authoritative, so `/model` is populated from the live ADP router rather than falling back to Codex's bundled GPT-only catalog.

Automatic ADP modes intentionally do not consume an Antigravity account silently. Antigravity models remain visible for explicit selection through `/model` when the user's official `agy` session is authenticated.

## What is included

- Rust provider-neutral protocol and execution kernel;
- native Codex `/adp auto|economy|balanced|max` slash command;
- native Codex `/model` exact-model picker;
- live Ollama Local and Ollama Cloud discovery;
- official Ollama sign-in plus direct `OLLAMA_API_KEY` support;
- official Antigravity/`agy` session detection and model discovery;
- dedicated `~/.codex-unchained` state, separate from normal Codex;
- ADP-owned Chrome/Edge browser broker and extension;
- host-owned Web search/fetch;
- MCP bridge;
- semantic `/teach` workflow compilation into `.advertpreneur`;
- deterministic zero-model replay when learned state still matches;
- local repair first and optional cloud escalation;
- token/run accounting;
- Linux, macOS, and Windows packaging;
- reproducible patches against a pinned upstream OpenAI Codex Rust revision.

## Routing modes

### `/adp auto`

Default. Favors inexpensive capable Ollama models for routine turns and raises the model tier when the request looks materially harder.

### `/adp economy`

Aggressively minimizes cloud usage. Local models and low-cost cloud models receive the strongest preference.

### `/adp balanced`

Favors stronger cost-efficient cloud models for normal coding, research, browser work, and debugging.

### `/adp max`

Favors the strongest available Ollama Cloud candidates for difficult work.

Inside Codex Unchained:

```text
/adp auto
/adp economy
/adp balanced
/adp max
```

To bypass routing and choose an exact model:

```text
/model
```

The live model catalog includes:

```text
[ADP] Auto
[ADP] Economy
[ADP] Balanced
[ADP] Max
[Ollama Local] ...
[Ollama Cloud] ...
[Antigravity] ...
```

## Provider authentication

The package includes the `adp-unchained` helper CLI.

### Ollama account login

Use Ollama's official sign-in flow:

```bash
adp-unchained auth ollama
```

This runs `ollama signin`. After successful sign-in, cloud models can be prepared and used through the signed-in local Ollama daemon.

### Ollama API key

Linux/macOS:

```bash
export OLLAMA_API_KEY="..."
adp-unchained auth ollama --method api
```

PowerShell:

```powershell
$env:OLLAMA_API_KEY = "..."
adp-unchained auth ollama --method api
```

When `OLLAMA_API_KEY` is available, the model router can send Ollama Cloud Responses requests directly. Otherwise it uses the signed-in local Ollama daemon.

### Antigravity / AGY

Use the official `agy` Google sign-in/session:

```bash
adp-unchained auth agy
```

If `agy` is already authenticated, Unchained validates the existing session and lists visible models. Otherwise it launches `agy` interactively so its normal Google sign-in flow can complete.

Antigravity is an explicit provider in v0.2: select one of its discovered models with `/model`. The automatic `/adp` modes do not silently route account quota through AGY.

## Install

Normal users should install a prebuilt package. Building upstream Codex locally is only for development.

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/haseebgb92/advertpreneur-cli/codex-unchained-v0.2/codex-unchained/install.sh | sh
```

Supported release targets:

```text
Linux x64
Linux ARM64
```

### macOS

The same installer detects Intel versus Apple Silicon automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/haseebgb92/advertpreneur-cli/codex-unchained-v0.2/codex-unchained/install.sh | sh
```

Supported release targets:

```text
macOS Intel (x64)
macOS Apple Silicon (ARM64)
```

### Windows

Run in PowerShell:

```powershell
irm https://raw.githubusercontent.com/haseebgb92/advertpreneur-cli/codex-unchained-v0.2/codex-unchained/install.ps1 | iex
```

Supported release target:

```text
Windows x64
```

The installers resolve only GitHub releases tagged `codex-unchained-v*`, download the correct platform bundle, install the binaries, browser extension files and AGY brain adapter, create/update the isolated `~/.codex-unchained` configuration, and default to `adp/auto`.

After installation:

```bash
adp-unchained auth ollama
codex-unchained
```

Optional providers:

```bash
adp-unchained auth agy
```

For an Ollama API key, set `OLLAMA_API_KEY` and run:

```bash
adp-unchained auth ollama --method api
```

## Build locally

From `codex-unchained/`:

```bash
cargo build -p adp-unchained -p adp-mcp --release
cargo test --workspace
```

The patched Codex binary itself is produced by the upstream compatibility/package workflows, which fetch the pinned OpenAI Codex revision and apply the reproducible files in `upstream-patches/`.

## Browser and computer-use layer

`adp-mcp` hosts:

```text
127.0.0.1:8765  ADP browser broker
127.0.0.1:8766  Codex Unchained model router
```

The packaged extension binds the broker to the user's already-open Chrome/Edge profile. Models receive host tools rather than owning a separate browser runtime, so changing the reasoning backend does not change the browser/shell/MCP layer.

A useful first browser test is:

```text
Use the ADP browser tools to inspect the active browser tab. Tell me the title,
URL, and visible interactive elements. Do not click or modify anything.
```

## Model router

The router exposes:

```text
GET  http://127.0.0.1:8766/v1/models
POST http://127.0.0.1:8766/v1/responses
```

Virtual `adp/*` model IDs are resolved per request by the ADP policy engine. Exact `ollama-local/*`, `ollama-cloud/*`, and `antigravity/*` IDs remain available through `/model`.

The invariant is:

```text
switch the brain
do not switch the agent
```

## Teach and replay

Record a browser workflow:

```bash
adp-unchained teach helium-export --project /path/to/project
```

Replay it:

```bash
adp-unchained replay helium-export --project /path/to/project --keyword "bee wax wrap"
```

Replay first attempts the learned semantic workflow with zero model calls. On state divergence it can use the configured local repair model and optionally escalate to a cloud repair model. A repair is persisted only after verification.

Teach/replay does not persist passwords, tokens, OTP/MFA values, payment fields, cookies, screenshots, raw DOM, or arbitrary typed form values. Search/query/keyword text is represented as `[TEACH_KEYWORD]`.

## Upstream Codex compatibility

`upstream-patches/` pins an exact OpenAI Codex revision and applies reproducible transformations that:

1. preserve provider wire dialect for tool search;
2. keep local tool discovery and skill/plugin/app guidance enabled for custom models;
3. give this distribution its own `~/.codex-unchained` home and `codex-unchained` command identity;
4. add the native `/adp` routing command without replacing native `/model`.

Codex approval and sandbox machinery remain part of the patched Codex distribution. ADP browser/web execution remains ADP-owned.

## CI and packages

CI validates the Rust workspace, formatting, Clippy/tests, extension syntax, upstream Codex patch application, focused upstream tests, and package smoke tests.

Cross-platform package targets:

- Linux x64;
- Linux arm64;
- macOS x64;
- macOS arm64;
- Windows x64.

The v0.2 work stays isolated from `main` and the stable `codex-unchained-v0.1` branch until validation is complete.
