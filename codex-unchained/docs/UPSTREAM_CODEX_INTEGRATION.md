# Upstream Codex integration

Pinned upstream revision:

```
7498521d288b9b3b96ffba4eedf089d8d6e06a84
```

ADP Codex Unchained integrates with upstream Codex in three layers.

## Layer 1: provider compatibility surgery

`upstream-patches/apply_0001.py` makes client-side tool search bidirectional across provider dialects. Native Codex `tool_search_call` keeps the specialized ToolSearch payload/output path. A compatible provider that emits `tool_search` as an ordinary function call keeps a normal Function payload; the search handler parses it, and the result is returned as a normal `function_call_output`.

`upstream-patches/apply_0002.py` changes fallback metadata for unknown/custom models so they are not automatically admitted with local tool discovery and skill/plugin/app guidance disabled.

`upstream-patches/apply_0003.py` separates the distribution from normal Codex:

- default state root: `~/.codex-unchained`;
- command/help identity: `codex-unchained`;
- normal `~/.codex` remains outside the Unchained default path.

Compatibility CI fetches the exact pinned source, applies all transformations, runs `git diff --check`, runs rustfmt, and executes focused upstream tests.

## Layer 2: ADP host runtime

The `adp-mcp` Rust binary is registered only in the Unchained config:

```toml
[mcp_servers.adp]
command = "C:\\Users\\YOUR_USER\\AppData\\Local\\Advertpreneur\\Unchained\\adp-mcp.exe"
startup_timeout_sec = 20
```

The bridge exposes ADP Browser/Web tools. It owns the localhost browser broker on `127.0.0.1:8765`, accepts the Chrome/Edge extension, and routes semantic commands through the user's existing logged-in browser session.

Provider identity does not decide whether these tools exist.

## Layer 3: unified model router

The same `adp-mcp.exe` process hosts an HTTP model router on:

```text
127.0.0.1:8766
```

Unchained config selects it as a normal Responses provider:

```toml
model_provider = "unchained"

[model_providers.unchained]
name = "ADP Unchained Model Router"
base_url = "http://127.0.0.1:8766/v1"
wire_api = "responses"
requires_openai_auth = false
```

The router serves a Codex-specific `/v1/models` catalog. Upstream Codex's existing `/model` slash command therefore remains the model-switching UI; ADP does not implement a competing picker.

Catalog entries are namespaced:

```text
ollama-local/<model>
ollama-cloud/<model>
antigravity/<slug>
```

### Ollama

Local models are discovered from the local Ollama daemon. Cloud models are discovered from the live Ollama Cloud catalog. Both execute through the signed-in local Ollama daemon and its Responses-compatible endpoint.

### Antigravity

Antigravity models are discovered via the user's authenticated `agy models --output-format json` command.

For inference, the adapter runs `agy` in headless stream-JSON mode with a strict JSON schema and the custom `adp-unchained-brain` agent. That agent has an empty Antigravity tool list. It only reasons over the Codex conversation and available host-tool schemas, returning either:

- a final assistant message; or
- one host function call.

The router converts that decision into normal Responses SSE events. Codex then performs the host tool and returns the result on the next model turn.

This keeps execution ownership in Codex/ADP:

```text
Ollama Local / Ollama Cloud / Antigravity
                |
         ADP model router
                |
       patched Codex agent loop
                |
      Codex ToolRouter + MCP
                |
             adp-mcp
        ________|________
       |                 |
 ADP Browser          ADP Web
       |
 Chrome / Edge
```

## Capability ownership

The model decides what action it wants. The host decides what capability exists and performs it.

Changing `/model` changes the reasoning backend, not Browser/Web/shell/MCP availability.

## Project memory and replay

`.advertpreneur` is runtime/project state rather than prompt history. Taught workflows store semantic targets, before/after state fingerprints, verification rules, and safe runtime variables.

The repeat-work execution ladder is:

1. deterministic replay when the learned browser state still matches;
2. local model repair for bounded semantic divergence;
3. optional configured primary/cloud repair only if local repair fails;
4. persist a repair only after post-action verification;
5. resume the remaining deterministic workflow.

## Browser and Computer Use boundary

ADP does not assume separately distributed Browser/Computer helpers are redistributable. The ADP Browser runtime is an independent MV3 Chrome/Edge bridge.

Codex safety/approval controls are not removed. Provider compatibility expands which reasoning backends can use host tools; it does not disable consequential-action controls.

## Validation

The branch has separate CI surfaces:

- `codex-unchained.yml`: entire ADP Rust workspace, rustfmt, clippy with warnings denied, all tests, extension syntax, and runnable CLI smoke checks.
- `codex-upstream-compat.yml`: exact pinned upstream Codex transformations plus focused upstream tests.
- `codex-unchained-package.yml`: Windows ADP runtime package, patched upstream Codex Windows build, command-identity smoke, and isolated installer smoke.

The Windows patched-Codex artifact is deliberately named `codex-unchained.exe` so it can coexist with a normal installed `codex.exe`.
