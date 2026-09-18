# Upstream Codex integration plan

Reference upstream inspected: `openai/codex` current `main` on 2026-09-18.

## Why the seam is viable

Current Codex Rust already separates `ToolRouter`, model-visible tool specs, executable runtimes, provider capabilities, Ollama/LM Studio support, extensions, skills, MCP, and web search.

The liberation patch should therefore stay small and surgical.

## Rules

1. Provider identity never removes an ADP capability.
2. Decide tool representation from invocation dialect: native function calls, structured JSON fallback, or unsupported.
3. Normalize provider wire calls into one internal tool call before routing.
4. Keep browser, computer-use, shell, MCP and ADP-native functions in host runtimes.
5. Load `.advertpreneur` as project/session state and retrieve only the relevant fragment for a model turn.
6. Repeat tasks escalate in this order: deterministic replay -> local model repair -> primary/cloud model.
7. Persist only verified successful repairs.

## First upstream targets

- `codex-rs/core/src/tools/router.rs`
- `codex-rs/core/src/config/mod.rs`
- `codex-rs/model-provider*`
- `codex-rs/tools`
- `codex-rs/ollama`

The first Codex patch should prove that an Ollama-capable adapter receives the same Browser/MCP inventory as another function-calling provider and that a normal function call named `tool_search` can enter the same local discovery path when its arguments satisfy the tool-search contract.

## Browser/computer note

Apache-2.0 covers the public Codex repository. It should not be assumed to cover every separately bundled desktop helper or plugin. This prototype therefore keeps an ADP-owned MV3 existing-browser bridge. If an installed Codex Browser/Computer runtime later exposes a lawful public integration surface, we can add an adapter without making that runtime a redistribution dependency.
