# Upstream Codex integration

Pinned upstream revision:

```
7498521d288b9b3b96ffba4eedf089d8d6e06a84
```

ADP Codex Unchained integrates with upstream Codex in two layers.

## Layer 1: provider compatibility surgery

`upstream-patches/apply_0001.py` makes client-side tool search bidirectional across provider dialects. Native Codex `tool_search_call` keeps the specialized `ToolSearch` payload/output path. A compatible provider that emits `tool_search` as an ordinary function call keeps a normal Function payload; the search handler parses it, and the result is returned as a normal `function_call_output`.

`upstream-patches/apply_0002.py` changes fallback metadata for unknown/custom models so they are not automatically admitted with local tool discovery and skill/plugin/app guidance disabled.

The compatibility CI fetches the exact pinned source, applies both transformations, runs `git diff --check`, runs rustfmt, and executes focused upstream tests.

## Layer 2: ADP host tools over MCP

The `adp-mcp` Rust binary is a local stdio MCP server intended to be registered in Codex:

```toml
[mcp_servers.adp]
command = "C:\\Users\\YOUR_USER\\AppData\\Local\\Advertpreneur\\Unchained\\adp-mcp.exe"
startup_timeout_sec = 20
```

Codex namespaces these tools as MCP tools while the implementation remains outside the model provider.

The MCP bridge exposes the ADP Browser tools and ADP Web tools. It starts the localhost Browser broker on `127.0.0.1:8765`, accepts the Chrome/Edge extension, and routes semantic Browser commands through that existing logged-in browser session. Web search and fetch are host operations and therefore do not require the reasoning model itself to implement web browsing.

This is the intended path for running an Ollama-backed Codex session while retaining Browser/Web capabilities:

```text
Ollama model
    |
patched Codex ToolRouter
    |
MCP tool discovery
    |
adp-mcp.exe
    |----------------------|
ADP Browser           ADP Web
    |                      |
Chrome/Edge           web search/fetch
```

## Capability ownership

Provider identity does not decide whether an ADP capability exists.

The model is responsible for deciding when to request an action. Host runtimes perform the action and return the observation. The same Browser/Web runtime can therefore be used by a small local Qwen model, an Ollama cloud model, or another compatible provider.

## Project memory and replay

`.advertpreneur` is runtime/project state rather than prompt history. Taught workflows store semantic targets, before/after state fingerprints, verification rules, and safe runtime variables.

The repeat-work execution ladder is:

1. deterministic replay when the learned browser state still matches;
2. local model repair for bounded semantic divergence;
3. optional configured primary/cloud repair only if local repair fails;
4. persist a repair only after post-action verification;
5. resume the remaining deterministic workflow.

## Browser and Computer Use boundary

The public OpenAI Codex repository is Apache-2.0, but ADP does not assume every separately distributed Browser/Computer helper is redistributable. The ADP Browser runtime is therefore an independent MV3 Chrome/Edge bridge rather than a copied proprietary helper.

Codex safety/approval controls are not removed by these patches. The compatibility change expands which reasoning providers can access host tools; it does not disable consequential-action controls.

## Validation

The branch has separate CI surfaces:

- `codex-unchained.yml`: entire ADP Rust workspace, rustfmt, clippy with warnings denied, all tests, extension syntax, and runnable CLI smoke checks.
- `codex-upstream-compat.yml`: exact pinned upstream Codex transformations plus focused upstream tests.
- `codex-unchained-package.yml`: Windows ADP runtime package and a patched upstream Codex Windows build.

The Windows patched-Codex artifact is deliberately named `codex-unchained.exe` so it can coexist with a normal installed `codex.exe`.
