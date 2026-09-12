# Provider-Neutral Action Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make all ADP actions available under the same safety rules to Codex, AGY/Gemini, Ollama, and cloud models.

**Architecture:** Create `ExternalActionGateway`, which parses one provider action request, dispatches it through the existing `ToolRegistry`, and returns verified results to the same provider session. Change the external-provider path in `cli.py` from one opaque call into a bounded provider-turn/action/continuation loop. Local/Ollama retains its current tool loop over the same registry.

**Tech Stack:** Python 3.10+, pytest, unittest, `ToolRegistry`, `ExternalProviderHarness`, Browser Bridge.

---

## File structure

- Create `advertpreneur_cli/action_gateway.py` for the provider-neutral protocol and safeguards.
- Modify `advertpreneur_cli/cli.py` for external-provider looping and activity status.
- Create `tests/test_action_gateway.py` for action protocol, shared dispatch, and both provider paths.
- Modify `tests/test_v014_browser_learn_external_coding.py` to preserve one-turn no-action behavior.
- Modify `README.md` to state model selection never removes ADP actions.

### Task 1: Add a strict action request parser

**Files:** Create `advertpreneur_cli/action_gateway.py`; create `tests/test_action_gateway.py`.

- [ ] Write a failing test that passes exactly one fenced `adp_action` JSON object containing `{"tool":"browser","args":{"action":"status"}}` and asserts parsed tool `browser` and args `{"action":"status"}`.
- [ ] Run `python -m pytest -q tests/test_action_gateway.py -k parse_action`; expected result is failure because `action_gateway` does not exist.
- [ ] Add `ActionRequest` and `ExternalActionGateway.parse_action()`. It accepts exactly one JSON object with a non-empty tool string and object args. It rejects malformed JSON, missing fields, non-object args, and multiple action blocks without executing anything.
- [ ] Re-run `python -m pytest -q tests/test_action_gateway.py -k parse_action`; expected result is pass.
- [ ] Commit parser files with message `feat: parse external ADP action requests`.

### Task 2: Dispatch every request through ToolRegistry

**Files:** Modify `advertpreneur_cli/action_gateway.py`; modify `tests/test_action_gateway.py`.

- [ ] Write a failing test with a realistic fake registry whose `execute("browser", {"action":"status"})` returns `Browser bridge connected`; assert the gateway called that exact registry method once.
- [ ] Run `python -m pytest -q tests/test_action_gateway.py -k dispatches`; expected result is failure because `execute()` does not exist.
- [ ] Add `ActionResult` and `ExternalActionGateway.execute()`. It permits only public registered tool names, calls only `ToolRegistry.execute(tool, args)`, and turns `ToolError` into observed `TOOL_ERROR` text. It must not call browser or shell code directly.
- [ ] Add a test that a deletion approval error and `Login needed in browser` set `blocked=True`; re-run `python -m pytest -q tests/test_action_gateway.py`; expected result is pass.
- [ ] Commit with message `feat: dispatch external actions through ADP tools`.

### Task 3: Bound and continue the provider action loop

**Files:** Modify `advertpreneur_cli/action_gateway.py`; modify `tests/test_action_gateway.py`.

- [ ] Write a failing test asserting `result_packet(ActionResult(True, "Navigated"))` contains an `adp_action_result` fenced block and instructs the provider to request one next action or finish.
- [ ] Write a failing test asserting the third identical action is blocked after a two-repeat allowance.
- [ ] Run `python -m pytest -q tests/test_action_gateway.py -k "result_packet or repeated"`; expected result is failure.
- [ ] Implement continuation packets, stable tool-plus-args signatures, a 24-action total limit, and a two-repeat identical-action limit. Login and deletion approval return their observed state and end the loop.
- [ ] Re-run the same test command; expected result is pass.
- [ ] Commit with message `feat: bound external action continuation loops`.

### Task 4: Route AGY and Codex through the same loop

**Files:** Modify `advertpreneur_cli/cli.py`; modify `tests/test_action_gateway.py`; modify `tests/test_v014_browser_learn_external_coding.py`.

- [ ] Write a parameterized failing test for providers `codex` and `agy`. Each fake provider first responds with a browser-status action request, then with a final report. Assert each caused the same `ToolRegistry.execute("browser", {"action":"status"})` call and received an `adp_action_result` on its second prompt.
- [ ] Run `python -m pytest -q tests/test_action_gateway.py -k external_provider_action_loop`; expected result is failure because `_run_external_coding` performs one provider call.
- [ ] Extract the existing provider invocation into a session-preserving turn helper. Use it in a gateway loop: send the shared contract initially, dispatch each requested action locally, return the result packet to the same conversation ID, and retain the last normal provider response as the final report.
- [ ] Preserve quota accounting, model/effort settings, session IDs, persistent footer, existing MCP/plugin configuration, and existing Codex Browser MCP as an optional acceleration. Display `Action · <tool>` while ADP executes the shared tool.
- [ ] Add a no-action compatibility test showing normal external code work makes one provider call and returns unchanged. Run `python -m pytest -q tests/test_action_gateway.py tests/test_v014_browser_learn_external_coding.py`; expected result is pass except separately reported existing Windows fake-executable fixture failures.
- [ ] Commit with message `feat: unify external provider actions`.

### Task 5: Publish the shared contract and verify

**Files:** Modify `advertpreneur_cli/action_gateway.py`; modify `README.md`; modify `tests/test_action_gateway.py`.

- [ ] Write a failing contract test requiring `adp_action`, `Login needed in browser`, and `deletion proposal` in `ExternalActionGateway.contract()`.
- [ ] Run `python -m pytest -q tests/test_action_gateway.py -k gateway_contract`; expected result is failure.
- [ ] Implement the contract: one action per turn, inspect verified results, never request credentials, and stop at login or deletion approval. Update README to state every selected model has the same ADP action layer and policy.
- [ ] Run `python -m unittest tests.test_tui tests.test_working_ui tests.test_updater tests.test_release_package`, `python -m pytest -q tests/test_action_gateway.py tests/test_tools.py tests/test_v012_intelligence_browser.py`, `python -m compileall -q advertpreneur_cli`, and `git diff --check`.
- [ ] Confirm selected tests pass, compilation exits zero, and diff check is empty. List unrelated full-suite failures with their exact cause.
- [ ] Commit with message `docs: describe provider-neutral ADP actions`.

## Plan self-review

- Tasks 1 through 3 create and test the safe shared protocol, registry dispatch, and stop conditions.
- Task 4 makes AGY and Codex use that protocol while leaving the local/Ollama path on the same registry.
- Task 5 supplies user-facing guarantees and release-level verification.
