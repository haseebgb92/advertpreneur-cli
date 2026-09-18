#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

TOOL_IMPORT_OLD = """use codex_tools::DiscoverableTool;
use codex_tools::ResponsesApiNamespaceTool;
use codex_tools::ToolName;
"""
TOOL_IMPORT_NEW = """use codex_tools::DiscoverableTool;
use codex_tools::ResponsesApiNamespaceTool;
use codex_tools::TOOL_SEARCH_TOOL_NAME;
use codex_tools::ToolName;
"""

FUNCTION_OLD = """            } => {
                let tool_name = ToolName::new(namespace, name).with_default_namespace();
                Ok(Some(ToolCall {
                    tool_name,
                    call_id,
                    payload: ToolPayload::Function { arguments },
                    encrypted_function_args,
                }))
            }
"""
FUNCTION_NEW = """            } => {
                let tool_name = ToolName::new(namespace, name).with_default_namespace();
                if tool_name.is_default_namespace() && tool_name.name == TOOL_SEARCH_TOOL_NAME {
                    let arguments: SearchToolCallParams = serde_json::from_str(&arguments)
                        .map_err(|err| {
                            FunctionCallError::RespondToModel(format!(
                                "failed to parse tool_search arguments: {err}"
                            ))
                        })?;
                    return Ok(Some(ToolCall {
                        tool_name: ToolName::plain(TOOL_SEARCH_TOOL_NAME),
                        call_id,
                        payload: ToolPayload::ToolSearch { arguments },
                        encrypted_function_args: None,
                    }));
                }
                Ok(Some(ToolCall {
                    tool_name,
                    call_id,
                    payload: ToolPayload::Function { arguments },
                    encrypted_function_args,
                }))
            }
"""

TEST_IMPORT_OLD = """use codex_tools::ResponsesApiNamespace;
use codex_tools::ResponsesApiNamespaceTool;
use codex_tools::ToolName;
"""
TEST_IMPORT_NEW = """use codex_tools::ResponsesApiNamespace;
use codex_tools::ResponsesApiNamespaceTool;
use codex_tools::TOOL_SEARCH_TOOL_NAME;
use codex_tools::ToolName;
"""

TESTS = r"""

#[test]
fn build_tool_call_normalizes_function_call_tool_search() -> anyhow::Result<()> {
    let call = ToolRouter::build_tool_call(ResponseItem::FunctionCall {
        id: None,
        name: TOOL_SEARCH_TOOL_NAME.to_string(),
        namespace: None,
        arguments: serde_json::json!({
            "query": "browser computer use",
            "limit": 4
        })
        .to_string(),
        encrypted_function_args: None,
        call_id: "call-tool-search".to_string(),
        internal_chat_message_metadata_passthrough: None,
    })?
    .expect("function_call tool_search should normalize to a tool call");

    assert_eq!(call.tool_name, ToolName::plain(TOOL_SEARCH_TOOL_NAME));
    assert_eq!(call.call_id, "call-tool-search");
    assert_eq!(call.encrypted_function_args, None);
    match call.payload {
        ToolPayload::ToolSearch { arguments } => {
            assert_eq!(arguments.query, "browser computer use");
            assert_eq!(arguments.limit, Some(4));
        }
        other => panic!("expected tool-search payload, got {other:?}"),
    }

    Ok(())
}

#[test]
fn build_tool_call_does_not_rewrite_namespaced_tool_search() -> anyhow::Result<()> {
    let call = ToolRouter::build_tool_call(ResponseItem::FunctionCall {
        id: None,
        name: TOOL_SEARCH_TOOL_NAME.to_string(),
        namespace: Some("mcp__example".to_string()),
        arguments: "{}".to_string(),
        encrypted_function_args: None,
        call_id: "call-namespaced-tool-search".to_string(),
        internal_chat_message_metadata_passthrough: None,
    })?
    .expect("namespaced function should remain a normal function call");

    assert_eq!(
        call.tool_name,
        ToolName::namespaced("mcp__example", TOOL_SEARCH_TOOL_NAME)
    );
    assert!(matches!(call.payload, ToolPayload::Function { .. }));

    Ok(())
}
"""

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one baseline match, found {count}")
    return text.replace(old, new, 1)

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_0001.py /path/to/codex")

    root = pathlib.Path(sys.argv[1]).resolve()
    router = root / "codex-rs/core/src/tools/router.rs"
    tests = root / "codex-rs/core/src/tools/router_tests.rs"

    router_text = router.read_text(encoding="utf-8")
    router_text = replace_once(router_text, TOOL_IMPORT_OLD, TOOL_IMPORT_NEW, "router import")
    router_text = replace_once(router_text, FUNCTION_OLD, FUNCTION_NEW, "function-call normalization")
    router.write_text(router_text, encoding="utf-8")

    test_text = tests.read_text(encoding="utf-8")
    test_text = replace_once(test_text, TEST_IMPORT_OLD, TEST_IMPORT_NEW, "test import")
    if "fn build_tool_call_normalizes_function_call_tool_search()" in test_text:
        raise SystemExit("tests already patched")
    tests.write_text(test_text.rstrip() + TESTS.rstrip() + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
