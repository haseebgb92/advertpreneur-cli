#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

CAPABILITIES_OLD = """        include_skills_usage_instructions: false,
        include_plugin_usage_instructions: false,
        include_apps_usage_instructions: false,
"""
CAPABILITIES_NEW = """        include_skills_usage_instructions: true,
        include_plugin_usage_instructions: true,
        include_apps_usage_instructions: true,
"""

SEARCH_OLD = """        used_fallback_model_metadata: true, // this is the fallback model metadata
        supports_search_tool: false,
"""
SEARCH_NEW = """        used_fallback_model_metadata: true, // this is the fallback model metadata
        // ADP/Codex Unchained owns client-side tool discovery. Unknown provider
        // models are admitted to that surface instead of being downgraded by
        // catalog metadata.
        supports_search_tool: true,
"""

TEST = r"""

#[test]
fn unknown_model_keeps_local_tool_discovery_and_extension_instructions() {
    let model = model_info_from_slug("ollama-cloud-model");

    assert!(model.used_fallback_model_metadata);
    assert!(model.supports_search_tool);
    assert!(!model.node_repl_disabled);
    assert!(model.include_skills_usage_instructions);
    assert!(model.include_plugin_usage_instructions);
    assert!(model.include_apps_usage_instructions);
}
"""

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one baseline match, found {count}")
    return text.replace(old, new, 1)

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_0002.py /path/to/codex")

    root = pathlib.Path(sys.argv[1]).resolve()
    model_info = root / "codex-rs/models-manager/src/model_info.rs"
    tests = root / "codex-rs/models-manager/src/model_info_tests.rs"

    text = model_info.read_text(encoding="utf-8")
    text = replace_once(text, CAPABILITIES_OLD, CAPABILITIES_NEW, "fallback instruction capabilities")
    text = replace_once(text, SEARCH_OLD, SEARCH_NEW, "fallback tool discovery capability")
    model_info.write_text(text, encoding="utf-8")

    test_text = tests.read_text(encoding="utf-8")
    if "fn unknown_model_keeps_local_tool_discovery_and_extension_instructions()" in test_text:
        raise SystemExit("tests already patched")
    tests.write_text(test_text.rstrip() + TEST.rstrip() + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
