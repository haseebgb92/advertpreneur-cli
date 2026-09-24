# Codex Unchained

Codex Unchained keeps the **official OpenAI Codex CLI intact** and changes only the model brain.

The project deliberately does **not** reimplement Codex's TUI, tool loop, approvals, sandbox, MCP handling, browser/computer use, file editing, shell execution, memory, or orchestration. Those remain upstream Codex behavior.

Current upstream lock: **Codex CLI 0.156.1**.

## Brains

### AGY / Google Antigravity

AGY runs as a model-only backend. A tiny local compatibility gateway converts Codex's native Responses API requests into headless `agy` requests and converts the structured answer back into native Codex Responses API events. The Codex context is sent to AGY over its `stream-json` stdin protocol rather than as a command-line argument, so large contexts remain practical on Windows as well as Linux/macOS.

The bundled Antigravity custom agent has `tools: []`, `inheritMcp: false`, and command execution disabled. Codex remains the only component allowed to use host tools. Provider-native server tools such as OpenAI-hosted web search are not impersonated by the gateway; browser/MCP/shell/file actions continue through Codex.

Examples:

```bash
codex-unchained -m agy/gemini-3.8-flash-medium
codex-unchained -m agy/claude-sonnet-4-6
```

### Ollama

Ollama uses Codex's own OSS provider path. No gateway is needed.

```bash
codex-unchained -m ollama/gpt-oss:120b-cloud
codex-unchained -m ollama/qwen3:8b
```

## Install

### Linux / macOS

Requirements:

- `curl`
- Rust/Cargo (only to build the small AGY gateway)
- `agy` if you want Antigravity models
- `ollama` if you want Ollama models

```bash
git clone -b codex-unchained-v0.1 https://github.com/haseebgb92/advertpreneur-cli.git
cd advertpreneur-cli
./install.sh
```

The installer downloads the official Codex CLI version listed in `UPSTREAM_CODEX_VERSION`; it does not compile or patch Codex.

### Windows

```powershell
git clone -b codex-unchained-v0.1 https://github.com/haseebgb92/advertpreneur-cli.git
cd advertpreneur-cli
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

## Usage

```bash
codex-unchained doctor
codex-unchained models
codex-unchained
```

The default brain is:

```text
agy/gemini-3.8-flash-medium
```

Override it per run with normal Codex `-m` / `--model` syntax:

```bash
codex-unchained -m agy/gemini-3.8-flash-high
codex-unchained -m ollama/gpt-oss:120b-cloud
```

All other arguments are forwarded to the official `codex` executable unchanged.

You can also set a default:

```bash
export UNCHAINED_MODEL=agy/claude-sonnet-4-6
```

## Architecture

```text
You
 │
 ▼
official Codex CLI 0.156.1
 │
 ├─ tools / shell / files / browser / MCP / approvals / sandbox → Codex
 │
 └─ inference only
      ├─ Ollama → Codex built-in OSS provider
      └─ AGY → localhost compatibility gateway → agy --model ...
```

For AGY, the model receives the Codex conversation and Codex tool schemas as data. It may choose a Codex tool call, but it cannot execute the tool itself. Codex receives that choice and runs it through its normal upstream tool loop.

That separation is the point of Unchained: **same Codex, different brain**.

## Why this replaces the old implementation

The previous branch duplicated browser, agent, runtime, MCP, model-router, memory, and executor behavior around Codex. That made browser/tool behavior diverge from upstream Codex and created failures such as repeated browser navigation loops.

This version removes that duplicated runtime. If upstream Codex knows how to operate Chrome/MCP correctly, Unchained inherits that behavior because it is the same executable and tool loop.

## Upgrading Codex

Change `UPSTREAM_CODEX_VERSION` only after testing the AGY gateway against that version, then rerun the installer.

The legacy pre-cleanup implementation is preserved on:

```text
codex-unchained-legacy-20260924
```
