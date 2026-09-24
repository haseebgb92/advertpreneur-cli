# Codex Unchained

Codex Unchained keeps the **official OpenAI Codex CLI intact** and swaps only the inference brain.

Current upstream lock: **Codex CLI 0.156.1**.

Codex still owns the TUI, conversation/tool loop, shell, file editing, patches, approvals, sandbox, MCP, browser tools, session state, and execution. AGY and Ollama only decide the next assistant response or Codex tool call.

## Model selection: use Codex /model

There is no separate model-fetch workflow.

Every time `codex-unchained` starts, the brain gateway discovers:

- models exposed by `agy models`;
- models exposed by `ollama list`.

It converts them into a native Codex model catalog using metadata from the **installed official Codex build** and starts Codex with that catalog.

Run:

```bash
codex-unchained
```

Then inside Codex:

```text
/model
```

AGY and Ollama brains appear in the normal Codex model picker. Their internal slugs are namespaced so the gateway knows where inference should go, for example:

```text
agy/gemini-3.8-flash-medium
agy/claude-sonnet-4-6
ollama/qwen3:8b
```

You can still use normal Codex `-m` when useful:

```bash
codex-unchained -m agy/gemini-3.8-flash-high
codex-unchained -m ollama/qwen3:8b
```

Or set an optional startup default:

```bash
export UNCHAINED_MODEL=agy/gemini-3.8-flash-medium
```

If no default is forced, Codex uses the first available picker model and you can switch with `/model`.

## Brain architecture

```text
                               ┌─ AGY / Antigravity model
                               │
You → official Codex CLI → brain gateway
          │                    │
          │                    └─ Ollama model
          │
          ├─ shell / files / patches
          ├─ approvals / sandbox
          ├─ MCP
          └─ browser extension via MCP
```

### AGY

AGY runs through a model-only Antigravity agent:

- `tools: []`
- `inheritMcp: false`
- command execution disabled

The Codex request is transported to AGY through its `stream-json` stdin protocol, which avoids command-line size limits on Windows.

### Ollama

Ollama is also treated as a model-only brain. The gateway sends the same Codex conversation/tool descriptions to Ollama and requires the same structured decision format. Ollama does **not** independently execute host tools.

That keeps the rule consistent across providers:

> **Codex acts. The selected model thinks.**

## Browser extension

The Chrome/Edge extension is restored, but not the old ADP browser runtime.

The extension is now a thin execution bridge:

```text
Codex tool loop
     │
     ▼
Codex MCP client
     │
     ▼
codex-unchained-browser-mcp
     │ localhost :8765
     ▼
Chrome / Edge extension
     │
     ▼
existing browser profile + active tab
```

The browser MCP currently exposes:

- bind active tab;
- semantic page inspection;
- navigate;
- click;
- fill non-sensitive controls;
- scroll;
- screenshot;
- wait for completed downloads.

The model does not talk directly to the extension. It selects a Codex tool; Codex invokes the MCP server; the MCP server transports the action to the extension.

### Load the extension

The installer copies the unpacked extension into the Unchained install directory.

On Linux/macOS the default path is:

```text
~/.local/lib/codex-unchained/extension
```

On Windows the installer prints the exact `%LOCALAPPDATA%\CodexUnchained\extension` path.

Chrome:

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the installed `extension` directory

Edge uses `edge://extensions`.

After that, `codex-unchained` automatically registers the browser MCP with official Codex.

## Install

### Linux / macOS

Requirements:

- `curl`
- Rust/Cargo
- `agy` for AGY models
- `ollama` for Ollama models

```bash
git clone -b codex-unchained-v0.1 https://github.com/haseebgb92/advertpreneur-cli.git
cd advertpreneur-cli
chmod +x install.sh
./install.sh
```

### Windows

```powershell
git clone -b codex-unchained-v0.1 https://github.com/haseebgb92/advertpreneur-cli.git
cd advertpreneur-cli
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer downloads the official Codex version listed in `UPSTREAM_CODEX_VERSION`. It does not compile or patch Codex itself. It builds only the small provider gateway and browser MCP adapter.

## Diagnostics

```bash
codex-unchained doctor
```

This reports the official Codex version, AGY/Ollama availability, brain gateway, browser MCP binary, extension path, and last imported catalog.

## Why the old runtime is gone

The earlier implementation duplicated browser, agent, executor, model-router, MCP, memory, and runtime behavior around Codex. That caused Unchained behavior to diverge from upstream Codex.

The current design removes those competing layers. The only custom pieces are adapters at the edges:

1. **brain gateway** — translates official Codex inference requests to AGY or Ollama;
2. **browser MCP bridge + extension** — gives official Codex access to the user's existing browser.

Everything in between stays upstream Codex.

The pre-cleanup implementation is preserved at:

```text
codex-unchained-legacy-20260924
```

## Upgrading Codex

Update `UPSTREAM_CODEX_VERSION`, run the compatibility CI, and rerun the installer. Because Codex itself is not forked or patched, upstream upgrades remain intentionally small.
