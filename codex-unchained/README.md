# ADP Codex Unchained

Provider-neutral Rust execution kernel for ADP. This branch makes the model provider the reasoning engine rather than the owner of Browser, Web, Teach, project memory, or execution capabilities.

Current branch: `codex-unchained-v0.1`.

## What works

The workspace currently contains:

- provider-neutral tool/capability protocol;
- Ollama local and Ollama Cloud model transport;
- host-owned Chrome/Edge Browser runtime over an authenticated localhost Rust broker;
- host-owned web search and web fetch tools;
- semantic `/teach` workflow compilation into `.advertpreneur`;
- zero-model deterministic workflow replay when the browser state matches;
- local-model repair first, optional primary/cloud repair second;
- verified repair persistence and resume-after-repair;
- token/run accounting;
- a runnable `adp-unchained` CLI;
- reproducible compatibility patches against a pinned OpenAI Codex Rust revision.

The normal CI compiles every workspace crate, runs rustfmt, clippy with warnings denied, all Rust tests, extension syntax checks, and CLI smoke commands.

## Build locally

From this directory:

```powershell
cargo build -p adp-unchained --release
.\target\release\adp-unchained.exe --help
```

## Ollama local

Make sure Ollama is running, then:

```powershell
adp-unchained doctor --provider local --model qwen3:1.7b
adp-unchained chat --provider local --model qwen3:1.7b "Say hello and report your available host tools."
```

Cloud models exposed through your signed-in local Ollama daemon can still be selected with `--provider local`; the CLI talks to the local daemon and does not need an OpenAI API key.

## Browser

Load `extension/` as an unpacked Chrome or Edge extension.

Then:

```powershell
adp-unchained chat --browser --provider local --model qwen3:1.7b "Inspect the current page and summarize it."
```

Browser execution happens in the user's already-open browser profile through `127.0.0.1:8765`. The model receives semantic controls rather than raw form values.

## Web

Host web search/fetch is independent of the reasoning model. Set the Ollama web credential:

```powershell
$env:OLLAMA_API_KEY = "..."
adp-unchained web-search "latest Rust release"
adp-unchained web-fetch "https://example.com/"
adp-unchained chat --web --provider local --model qwen3:1.7b "Research the latest Rust release and summarize it."
```

This means a small local model can request web search even though the model itself has no built-in web implementation. Without a web API credential, browser tools remain available for browser-driven research.

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

Replay first tries the learned workflow with zero model calls. On state divergence it can use the configured local Ollama repair model. A primary/cloud repair model is only used when explicitly configured and local repair fails. Repairs are saved only after verification.

## Privacy and safety of learned workflows

Teach/replay intentionally does not persist passwords, tokens, OTP/MFA values, payment fields, cookies, screenshots, raw DOM, or arbitrary typed form values. Search/query/keyword text is represented as the runtime placeholder `[TEACH_KEYWORD]`.

Potentially consequential clicks are learned but are not marked safe for deterministic replay.

## Upstream Codex compatibility surgery

`upstream-patches/` pins an exact OpenAI Codex revision and contains reproducible source transformations that:

1. normalize compatible providers that emit `tool_search` as an ordinary function call into Codex's client-side tool-search payload;
2. stop unknown/custom model fallback metadata from automatically disabling local tool discovery and skill/plugin/app guidance.

`.github/workflows/codex-upstream-compat.yml` fetches the pinned upstream revision, applies both transformations, checks formatting, and runs focused upstream Codex tests.

The ADP Browser/Web runtimes remain ADP-owned rather than depending on a proprietary browser helper.

## Windows package

The branch packaging workflow builds:

- `adp-unchained.exe`;
- the unpacked Chrome/Edge extension;
- this README;
- `INSTALL-WINDOWS.ps1`.

Run the installer with:

```powershell
.\INSTALL-WINDOWS.ps1 -AddToPath
```

A separate job also attempts a reproducible Windows build of the pinned patched upstream `codex.exe` after applying the compatibility transformations.

## Branch isolation

This implementation is intentionally isolated from `main`. It does not overwrite the older Python ADP tree or any newer local Rust migration that has not been pushed to GitHub.
