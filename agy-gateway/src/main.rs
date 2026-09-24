use axum::{
    body::Body,
    extract::State,
    http::{header, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    env,
    net::SocketAddr,
    process::Stdio,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use uuid::Uuid;

#[derive(Clone)]
struct AppState {
    agy_bin: String,
    agent: String,
    timeout: String,
}

#[derive(Debug, Deserialize)]
struct ResponsesRequest {
    model: String,
    #[serde(default)]
    instructions: String,
    #[serde(default)]
    input: Vec<Value>,
    #[serde(default)]
    tools: Vec<Value>,
    #[serde(default)]
    tool_choice: Value,
    #[serde(default)]
    parallel_tool_calls: bool,
    #[allow(dead_code)]
    #[serde(default)]
    stream: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum ToolKind {
    Function,
    Custom,
    ToolSearch,
}

#[derive(Debug, Clone)]
struct ToolRecord {
    namespace: Option<String>,
    name: String,
    kind: ToolKind,
}

#[tokio::main]
async fn main() {
    let host = env::var("UNCHAINED_AGY_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port: u16 = env::var("UNCHAINED_AGY_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(41415);
    let state = Arc::new(AppState {
        agy_bin: env::var("UNCHAINED_AGY_BIN").unwrap_or_else(|_| "agy".into()),
        agent: env::var("UNCHAINED_AGY_AGENT")
            .unwrap_or_else(|_| "codex-unchained-brain".into()),
        timeout: env::var("UNCHAINED_AGY_TIMEOUT").unwrap_or_else(|_| "10m".into()),
    });

    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/models", get(models))
        .route("/v1/responses", post(responses))
        .with_state(state);

    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .expect("valid listen address");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("bind AGY gateway");
    eprintln!("Codex Unchained AGY gateway listening on http://{addr}/v1");
    axum::serve(listener, app)
        .await
        .expect("serve AGY gateway");
}

async fn health() -> impl IntoResponse {
    Json(json!({"ok": true, "service": "codex-unchained-agy-gateway"}))
}

async fn models(State(state): State<Arc<AppState>>) -> Response {
    match Command::new(&state.agy_bin).arg("models").output().await {
        Ok(out) if out.status.success() => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let data: Vec<Value> = stdout
                .lines()
                .filter_map(|line| line.split_whitespace().next())
                .filter(|slug| !slug.is_empty())
                .map(|slug| {
                    json!({
                        "id": slug,
                        "object": "model",
                        "owned_by": "antigravity"
                    })
                })
                .collect();
            Json(json!({"object": "list", "data": data})).into_response()
        }
        Ok(out) => error_response(
            StatusCode::BAD_GATEWAY,
            format!(
                "agy models failed: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ),
        ),
        Err(err) => error_response(
            StatusCode::BAD_GATEWAY,
            format!("could not run agy: {err}"),
        ),
    }
}

async fn responses(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ResponsesRequest>,
) -> Response {
    let available_tools = flatten_tools(&req.tools);
    let prompt = build_prompt(&req);

    let schema = json!({
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["assistant", "tool_calls"]
            },
            "content": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "tool_namespace": {"type": "string"},
                        "arguments": {
                            "type": "object",
                            "additionalProperties": true
                        },
                        "input": {"type": "string"}
                    },
                    "required": [
                        "tool_name",
                        "tool_namespace",
                        "arguments",
                        "input"
                    ],
                    "additionalProperties": false
                }
            }
        },
        "required": ["type", "content", "tool_calls"],
        "additionalProperties": false
    })
    .to_string();

    let mut cmd = Command::new(&state.agy_bin);
    cmd.arg("--input-format")
        .arg("stream-json")
        .arg("--output-format")
        .arg("stream-json")
        .arg("--model")
        .arg(&req.model)
        .arg("--agent")
        .arg(&state.agent)
        .arg("--json-schema")
        .arg(schema)
        .arg("--print-timeout")
        .arg(&state.timeout)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(err) => {
            return error_response(
                StatusCode::BAD_GATEWAY,
                format!("could not run agy: {err}"),
            );
        }
    };

    let user_event = json!({
        "event": "user",
        "message": {"content": prompt}
    })
    .to_string();

    if let Some(mut stdin) = child.stdin.take() {
        if let Err(err) = stdin.write_all(format!("{user_event}\n").as_bytes()).await {
            let _ = child.kill().await;
            return error_response(
                StatusCode::BAD_GATEWAY,
                format!("could not send prompt to agy: {err}"),
            );
        }
        // Closing stdin after the one prompt is the documented graceful way
        // to finish a stream-json session once the active turn completes.
        drop(stdin);
    }

    let output = match child.wait_with_output().await {
        Ok(out) => out,
        Err(err) => {
            return error_response(
                StatusCode::BAD_GATEWAY,
                format!("could not wait for agy: {err}"),
            );
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut result = None;
    for line in stdout.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(event) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if event.get("event").and_then(Value::as_str) == Some("result") {
            result = event.get("result").cloned();
        }
    }

    let Some(result) = result else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return error_response(
            StatusCode::BAD_GATEWAY,
            format!(
                "agy returned no result event (exit {}): {}",
                output.status,
                stderr.trim()
            ),
        );
    };

    if !output.status.success()
        || result.get("status").and_then(Value::as_str) != Some("SUCCESS")
    {
        let message = result
            .get("error")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned)
            .unwrap_or_else(|| {
                let stderr = String::from_utf8_lossy(&output.stderr);
                if stderr.trim().is_empty() {
                    format!("AGY exited with {}", output.status)
                } else {
                    stderr.trim().to_string()
                }
            });
        return error_response(StatusCode::BAD_GATEWAY, message);
    }

    let decision = match result.get("structured_output") {
        Some(Value::Object(map)) => Value::Object(map.clone()),
        _ => {
            return error_response(
                StatusCode::BAD_GATEWAY,
                "AGY did not return structured_output".into(),
            );
        }
    };

    let decision_type = decision
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("assistant");

    if decision_type == "tool_calls" {
        let calls = decision
            .get("tool_calls")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();

        if calls.is_empty() {
            return error_response(
                StatusCode::BAD_GATEWAY,
                "AGY selected tool_calls but returned no calls".into(),
            );
        }
        if !req.parallel_tool_calls && calls.len() > 1 {
            return error_response(
                StatusCode::BAD_GATEWAY,
                "AGY returned parallel tool calls when Codex disabled them".into(),
            );
        }
        if req.tool_choice.as_str() == Some("none") {
            return error_response(
                StatusCode::BAD_GATEWAY,
                "AGY selected a tool while Codex tool_choice was none".into(),
            );
        }

        for call in &calls {
            let name = call
                .get("tool_name")
                .and_then(Value::as_str)
                .unwrap_or("");
            let namespace = call
                .get("tool_namespace")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            if find_tool(&available_tools, namespace, name).is_none() {
                let display = namespace
                    .map(|ns| format!("{ns}.{name}"))
                    .unwrap_or_else(|| name.to_string());
                return error_response(
                    StatusCode::BAD_GATEWAY,
                    format!("AGY selected unavailable Codex tool: {display}"),
                );
            }
        }
    }

    render_responses_sse(&req, &decision, &available_tools)
}

fn build_prompt(req: &ResponsesRequest) -> String {
    let input = serde_json::to_string_pretty(&req.input).unwrap_or_else(|_| "[]".into());
    let tools = serde_json::to_string_pretty(&req.tools).unwrap_or_else(|_| "[]".into());
    let tool_choice =
        serde_json::to_string(&req.tool_choice).unwrap_or_else(|_| "\"auto\"".into());

    format!(
        r#"You are ONLY the inference brain for the official OpenAI Codex CLI.
Codex itself owns the terminal, files, browser/computer control, MCP servers,
approvals, patching, sandbox, and every other host capability.

Never execute an Antigravity tool. Never browse. Never run commands. Never
read or write files. Never call an AGY MCP server. Your only job is to decide
what Codex should say next or which Codex-provided tool(s) Codex should execute.

Respect the Codex instructions and conversation below. Tool results already
present in INPUT_JSON are observations from Codex; consume them and advance the
task instead of blindly repeating the preceding tool call.

Return only the enforced structured object:
- type="assistant": put the complete reply in content and return tool_calls=[].
- type="tool_calls": content="" and return Codex tool calls.
- For a normal function tool, put its JSON arguments in arguments and input="".
- For a custom/freeform tool, put its raw input in input and arguments={{}}.
- For a namespaced tool, copy both its child tool_name and parent tool_namespace.
- For a top-level tool, tool_namespace="".
- If parallel_tool_calls is false, return at most one tool call.
- Never invent a tool. Use exactly the names and schemas from AVAILABLE_CODEX_TOOLS.
- Hosted/server-only tools that cannot be represented as Codex client calls must
  not be selected.
- When the requested work is complete, return an assistant response.

MODEL:
{model}

CODEX_INSTRUCTIONS:
{instructions}

TOOL_CHOICE:
{tool_choice}

PARALLEL_TOOL_CALLS:
{parallel}

INPUT_JSON:
{input}

AVAILABLE_CODEX_TOOLS:
{tools}
"#,
        model = req.model,
        instructions = req.instructions,
        tool_choice = tool_choice,
        parallel = req.parallel_tool_calls,
        input = input,
        tools = tools,
    )
}

fn flatten_tools(tools: &[Value]) -> Vec<ToolRecord> {
    let mut out = Vec::new();

    for tool in tools {
        match tool.get("type").and_then(Value::as_str).unwrap_or("") {
            "function" => {
                if let Some(name) = tool.get("name").and_then(Value::as_str) {
                    out.push(ToolRecord {
                        namespace: None,
                        name: name.to_string(),
                        kind: ToolKind::Function,
                    });
                }
            }
            "custom" => {
                if let Some(name) = tool.get("name").and_then(Value::as_str) {
                    out.push(ToolRecord {
                        namespace: None,
                        name: name.to_string(),
                        kind: ToolKind::Custom,
                    });
                }
            }
            "namespace" => {
                let Some(namespace) = tool.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let children = tool
                    .get("tools")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                for child in children {
                    let Some(name) = child.get("name").and_then(Value::as_str) else {
                        continue;
                    };
                    let kind = match child
                        .get("type")
                        .and_then(Value::as_str)
                        .unwrap_or("function")
                    {
                        "custom" => ToolKind::Custom,
                        _ => ToolKind::Function,
                    };
                    out.push(ToolRecord {
                        namespace: Some(namespace.to_string()),
                        name: name.to_string(),
                        kind,
                    });
                }
            }
            "tool_search" => {
                out.push(ToolRecord {
                    namespace: None,
                    name: "tool_search".into(),
                    kind: ToolKind::ToolSearch,
                });
            }
            // web_search is provider/server executed rather than a Codex client
            // tool, so the model-only AGY adapter cannot honestly execute it.
            _ => {}
        }
    }

    out
}

fn find_tool<'a>(
    tools: &'a [ToolRecord],
    namespace: Option<&str>,
    name: &str,
) -> Option<&'a ToolRecord> {
    tools.iter().find(|tool| {
        tool.name == name
            && match (&tool.namespace, namespace) {
                (None, None) => true,
                (Some(actual), Some(requested)) => actual == requested,
                _ => false,
            }
    })
}

fn render_responses_sse(
    req: &ResponsesRequest,
    decision: &Value,
    available_tools: &[ToolRecord],
) -> Response {
    let response_id = format!("resp_{}", Uuid::new_v4().simple());
    let mut events = Vec::new();

    events.push(json!({
        "type": "response.created",
        "response": {"id": response_id}
    }));

    let decision_type = decision
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("assistant");

    if decision_type == "tool_calls" {
        let calls = decision
            .get("tool_calls")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();

        for call in calls {
            let name = call
                .get("tool_name")
                .and_then(Value::as_str)
                .unwrap_or("");
            let namespace = call
                .get("tool_namespace")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty());
            let Some(tool) = find_tool(available_tools, namespace, name) else {
                continue;
            };

            let call_id = format!("call_{}", Uuid::new_v4().simple());
            let item = match tool.kind {
                ToolKind::Function => {
                    let arguments = call
                        .get("arguments")
                        .cloned()
                        .unwrap_or_else(|| json!({}))
                        .to_string();
                    let mut item = json!({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": tool.name,
                        "arguments": arguments
                    });
                    if let Some(namespace) = &tool.namespace {
                        item["namespace"] = Value::String(namespace.clone());
                    }
                    item
                }
                ToolKind::Custom => {
                    let input = call
                        .get("input")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    let mut item = json!({
                        "type": "custom_tool_call",
                        "call_id": call_id,
                        "name": tool.name,
                        "input": input
                    });
                    if let Some(namespace) = &tool.namespace {
                        item["namespace"] = Value::String(namespace.clone());
                    }
                    item
                }
                ToolKind::ToolSearch => {
                    let arguments = call
                        .get("arguments")
                        .cloned()
                        .unwrap_or_else(|| json!({}));
                    json!({
                        "type": "tool_search_call",
                        "call_id": call_id,
                        "execution": "client",
                        "arguments": arguments
                    })
                }
            };

            events.push(json!({
                "type": "response.output_item.done",
                "item": item
            }));
        }
    } else {
        let content = decision
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or("");
        events.push(json!({
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "id": format!("msg_{}", Uuid::new_v4().simple()),
                "content": [{
                    "type": "output_text",
                    "text": content
                }]
            }
        }));
    }

    events.push(json!({
        "type": "response.completed",
        "response": {
            "id": response_id,
            "model": req.model,
            "usage": {
                "input_tokens": 0,
                "input_tokens_details": null,
                "output_tokens": 0,
                "output_tokens_details": null,
                "total_tokens": 0
            }
        }
    }));

    let mut payload = String::new();
    for event in events {
        let event_type = event
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("message");
        payload.push_str("event: ");
        payload.push_str(event_type);
        payload.push('\n');
        payload.push_str("data: ");
        payload.push_str(&event.to_string());
        payload.push_str("\n\n");
    }

    let mut response = Response::new(Body::from(payload));
    *response.status_mut() = StatusCode::OK;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream"),
    );
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("no-cache"),
    );
    response
}

fn error_response(status: StatusCode, message: String) -> Response {
    (
        status,
        Json(json!({
            "error": {
                "message": message,
                "type": "codex_unchained_gateway_error"
            }
        })),
    )
        .into_response()
}
