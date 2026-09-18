#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

HANDLER_IMPORT_OLD = """use bm25::SearchEngineBuilder;
use codex_tools::LoadableToolSpec;
"""
HANDLER_IMPORT_NEW = """use bm25::SearchEngineBuilder;
use codex_protocol::models::SearchToolCallParams;
use codex_tools::LoadableToolSpec;
"""

HANDLER_MATCH_OLD = """        let args = match payload {
            ToolPayload::ToolSearch { arguments } => arguments,
            _ => {
                return Err(FunctionCallError::Fatal(format!(
                    "{TOOL_SEARCH_TOOL_NAME} handler received unsupported payload"
                )));
            }
        };
"""
HANDLER_MATCH_NEW = """        let args = parse_tool_search_payload(payload)?;
"""

HANDLER_HELPER = r"""fn parse_tool_search_payload(
    payload: ToolPayload,
) -> Result<SearchToolCallParams, FunctionCallError> {
    match payload {
        ToolPayload::ToolSearch { arguments } => Ok(arguments),
        ToolPayload::Function { arguments } => serde_json::from_str(&arguments).map_err(|err| {
            FunctionCallError::RespondToModel(format!(
                "failed to parse tool_search arguments: {err}"
            ))
        }),
        ToolPayload::Custom { .. } => Err(FunctionCallError::Fatal(format!(
            "{TOOL_SEARCH_TOOL_NAME} handler received unsupported payload"
        ))),
    }
}

#[cfg(test)]
mod function_payload_tests {
    use super::*;

    #[test]
    fn function_tool_search_payload_is_accepted() {
        let args = parse_tool_search_payload(ToolPayload::Function {
            arguments: r#"{"query":"browser computer use","limit":4}"#.to_string(),
        })
        .expect("ordinary function payload should be accepted for tool_search");

        assert_eq!(args.query, "browser computer use");
        assert_eq!(args.limit, Some(4));
    }
}

"""

CONTEXT_OUTPUT_OLD = """    fn to_response_item(&self, call_id: &str, _payload: &ToolPayload) -> ResponseInputItem {
        ResponseInputItem::ToolSearchOutput {
            call_id: call_id.to_string(),
            status: "completed".to_string(),
            execution: "client".to_string(),
            tools: self
                .tools
                .iter()
                .map(|tool| {
                    serde_json::to_value(tool).unwrap_or_else(|err| {
                        JsonValue::String(format!("failed to serialize tool_search output: {err}"))
                    })
                })
                .collect(),
        }
    }
"""
CONTEXT_OUTPUT_NEW = """    fn to_response_item(&self, call_id: &str, payload: &ToolPayload) -> ResponseInputItem {
        let tools = self
            .tools
            .iter()
            .map(|tool| {
                serde_json::to_value(tool).unwrap_or_else(|err| {
                    JsonValue::String(format!("failed to serialize tool_search output: {err}"))
                })
            })
            .collect::<Vec<_>>();

        if matches!(payload, ToolPayload::Function { .. }) {
            return ResponseInputItem::FunctionCallOutput {
                call_id: call_id.to_string(),
                output: FunctionCallOutputPayload {
                    body: FunctionCallOutputBody::Text(JsonValue::Array(tools).to_string()),
                    success: Some(true),
                },
            };
        }

        ResponseInputItem::ToolSearchOutput {
            call_id: call_id.to_string(),
            status: "completed".to_string(),
            execution: "client".to_string(),
            tools,
        }
    }
"""

CONTEXT_TEST = r"""

#[test]
fn function_tool_search_payloads_roundtrip_as_function_outputs() {
    let payload = ToolPayload::Function {
        arguments: json!({
            "query": "browser computer use",
            "limit": 4
        })
        .to_string(),
    };
    let response =
        ToolSearchOutput { tools: Vec::new() }.to_response_item("function-search-1", &payload);

    match response {
        ResponseInputItem::FunctionCallOutput { call_id, output } => {
            assert_eq!(call_id, "function-search-1");
            assert_eq!(output.body.to_text().as_deref(), Some("[]"));
            assert_eq!(output.success, Some(true));
        }
        other => panic!("expected FunctionCallOutput, got {other:?}"),
    }
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
    handler = root / "codex-rs/core/src/tools/handlers/tool_search.rs"
    context = root / "codex-rs/core/src/tools/context.rs"
    context_tests = root / "codex-rs/core/src/tools/context_tests.rs"

    handler_text = handler.read_text(encoding="utf-8")
    handler_text = replace_once(
        handler_text,
        HANDLER_IMPORT_OLD,
        HANDLER_IMPORT_NEW,
        "tool-search handler import",
    )
    handler_text = replace_once(
        handler_text,
        HANDLER_MATCH_OLD,
        HANDLER_MATCH_NEW,
        "tool-search function payload parsing",
    )
    if "fn parse_tool_search_payload(" in handler_text:
        raise SystemExit("tool-search handler already patched")
    handler_text = replace_once(
        handler_text,
        "impl CoreToolRuntime for ToolSearchHandler {}\n",
        HANDLER_HELPER + "impl CoreToolRuntime for ToolSearchHandler {}\n",
        "tool-search payload helper insertion",
    )
    handler.write_text(handler_text, encoding="utf-8")

    context_text = context.read_text(encoding="utf-8")
    context_text = replace_once(
        context_text,
        CONTEXT_OUTPUT_OLD,
        CONTEXT_OUTPUT_NEW,
        "tool-search provider-shaped output",
    )
    context.write_text(context_text, encoding="utf-8")

    tests_text = context_tests.read_text(encoding="utf-8")
    if "fn function_tool_search_payloads_roundtrip_as_function_outputs()" in tests_text:
        raise SystemExit("tool-search context tests already patched")
    context_tests.write_text(
        tests_text.rstrip() + CONTEXT_TEST.rstrip() + "\n",
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
