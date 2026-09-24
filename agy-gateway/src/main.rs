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
use std::{collections::HashSet, env, net::SocketAddr, process::Stdio, sync::Arc, time::{SystemTime, UNIX_EPOCH}};
use tokio::process::Command;
use uuid::Uuid;

#[derive(Clone)]
struct AppState {
    agy_bin: String,
    agent: String,
    timeout: String,
}

#[derive(Debug, Deserialize)]
struct ChatRequest {
    model: String,
    #[serde(default)]
    messages: Vec<Value>,
    #[serde(default)]
    tools: Vec<Value>,
    #[serde(default)]
    stream: bool,
    #[serde(default)]
    tool_choice: Option<Value>,
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
        agent: env::var("UNCHAINED_AGY_AGENT").unwrap_or_else(|_| "codex-unchained-brain".into()),
        timeout: env::var("UNCHAINED_AGY_TIMEOUT").unwrap_or_else(|_| "10m".into()),
    });

    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/models", get(models))
        .route("/v1/chat/completions", post(chat_completions))
        .with_state(state);

    let addr: SocketAddr = format!("{host}:{port}").parse().expect("valid listen address");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind AGY gateway");
    eprintln!("Codex Unchained AGY gateway listening on http://{addr}/v1");
    axum::serve(listener, app).await.expect("serve AGY gateway");
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
                .map(|slug| json!({"id": slug, "object": "model", "owned_by": "antigravity"}))
                .collect();
            Json(json!({"object": "list", "data": data})).into_response()
        }
        Ok(out) => error_response(StatusCode::BAD_GATEWAY, format!(
            "agy models failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        )),
        Err(err) => error_response(StatusCode::BAD_GATEWAY, format!("could not run agy: {err}")),
    }
}

async fn chat_completions(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ChatRequest>,
) -> Response {
    let allowed_tools = tool_names(&req.tools);
    let prompt = build_prompt(&req);
    let schema = json!({
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["assistant", "tool_call"]},
            "content": {"type": "string"},
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"}
        },
        "required": ["type", "content", "tool_name", "arguments"],
        "additionalProperties": false
    })
    .to_string();

    let mut cmd = Command::new(&state.agy_bin);
    cmd.arg("-p")
        .arg(prompt)
        .arg("--model")
        .arg(&req.model)
        .arg("--agent")
        .arg(&state.agent)
        .arg("--output-format")
        .arg("json")
        .arg("--json-schema")
        .arg(schema)
        .arg("--print-timeout")
        .arg(&state.timeout)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = match cmd.output().await {
        Ok(out) => out,
        Err(err) => return error_response(StatusCode::BAD_GATEWAY, format!("could not run agy: {err}")),
    };

    if !output.status.success() {
        return error_response(
            StatusCode::BAD_GATEWAY,
            format!(
                "agy exited with {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            ),
        );
    }

    let envelope: Value = match serde_json::from_slice(&output.stdout) {
        Ok(value) => value,
        Err(err) => {
            return error_response(
                StatusCode::BAD_GATEWAY,
                format!("invalid AGY JSON envelope: {err}"),
            )
        }
    };

    if envelope.get("status").and_then(Value::as_str) != Some("SUCCESS") {
        let msg = envelope
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("AGY returned a non-success status");
        return error_response(StatusCode::BAD_GATEWAY, msg.to_string());
    }

    let decision = match envelope.get("structured_output") {
        Some(Value::Object(map)) => Value::Object(map.clone()),
        _ => {
            return error_response(
                StatusCode::BAD_GATEWAY,
                "AGY did not return structured_output".into(),
            )
        }
    };

    let kind = decision.get("type").and_then(Value::as_str).unwrap_or("assistant");
    if kind == "tool_call" {
        let name = decision.get("tool_name").and_then(Value::as_str).unwrap_or("");
        if !allowed_tools.contains(name) {
            return error_response(
                StatusCode::BAD_GATEWAY,
                format!("AGY selected unavailable Codex tool: {name}"),
            );
        }
    }

    render_completion(&req, &decision)
}

fn build_prompt(req: &ChatRequest) -> String {
    let messages = serde_json::to_string_pretty(&req.messages).unwrap_or_else(|_| "[]".into());
    let tools = serde_json::to_string_pretty(&req.tools).unwrap_or_else(|_| "[]".into());
    let tool_choice = req
        .tool_choice
        .as_ref()
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "null".into()))
        .unwrap_or_else(|| "null".into());

    format!(
        r#"You are the reasoning/model backend for OpenAI Codex CLI. Codex itself owns the terminal, files, browser, MCP servers, approvals, patching, sandbox, and every other host capability. You must NEVER execute an Antigravity tool or act on the host. Decide only what Codex should say or which ONE Codex-provided tool it should call next.

Return the enforced JSON object only:
- type="assistant": put the complete assistant reply in content; tool_name=""; arguments={{}}.
- type="tool_call": content=""; choose exactly one tool_name from AVAILABLE_CODEX_TOOLS and put valid arguments in arguments.

Do not invent tools. Do not repeat a tool call merely because the previous action already appears in conversation history; use the newest tool result and advance the task. If the requested work is complete, return an assistant message instead of another tool call.

MODEL: {model}
TOOL_CHOICE: {tool_choice}

CONVERSATION_JSON:
{messages}

AVAILABLE_CODEX_TOOLS:
{tools}
"#,
        model = req.model,
        tool_choice = tool_choice,
        messages = messages,
        tools = tools,
    )
}

fn tool_names(tools: &[Value]) -> HashSet<String> {
    tools
        .iter()
        .filter_map(|tool| {
            tool.get("function")
                .and_then(|f| f.get("name"))
                .and_then(Value::as_str)
                .or_else(|| tool.get("name").and_then(Value::as_str))
        })
        .map(ToOwned::to_owned)
        .collect()
}

fn render_completion(req: &ChatRequest, decision: &Value) -> Response {
    let id = format!("chatcmpl-{}", Uuid::new_v4().simple());
    let created = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let kind = decision.get("type").and_then(Value::as_str).unwrap_or("assistant");

    if req.stream {
        let first = if kind == "tool_call" {
            let call_id = format!("call_{}", Uuid::new_v4().simple());
            let name = decision.get("tool_name").and_then(Value::as_str).unwrap_or("");
            let args = decision.get("arguments").cloned().unwrap_or_else(|| json!({}));
            json!({
                "id": id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [{
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": args.to_string()}
                        }]
                    },
                    "finish_reason": null
                }]
            })
        } else {
            let content = decision.get("content").and_then(Value::as_str).unwrap_or("");
            json!({
                "id": id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": null
                }]
            })
        };

        let finish_reason = if kind == "tool_call" { "tool_calls" } else { "stop" };
        let last = json!({
            "id": id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": req.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
        });
        let payload = format!("data: {first}\n\ndata: {last}\n\ndata: [DONE]\n\n");
        let mut response = Response::new(Body::from(payload));
        *response.status_mut() = StatusCode::OK;
        response.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
        response.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
        response
    } else {
        let message = if kind == "tool_call" {
            let call_id = format!("call_{}", Uuid::new_v4().simple());
            let name = decision.get("tool_name").and_then(Value::as_str).unwrap_or("");
            let args = decision.get("arguments").cloned().unwrap_or_else(|| json!({}));
            json!({
                "role": "assistant",
                "content": null,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": args.to_string()}
                }]
            })
        } else {
            json!({
                "role": "assistant",
                "content": decision.get("content").and_then(Value::as_str).unwrap_or("")
            })
        };
        let finish_reason = if kind == "tool_call" { "tool_calls" } else { "stop" };
        Json(json!({
            "id": id,
            "object": "chat.completion",
            "created": created,
            "model": req.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]
        }))
        .into_response()
    }
}

fn error_response(status: StatusCode, message: String) -> Response {
    (status, Json(json!({"error": {"message": message, "type": "codex_unchained_gateway_error"}}))).into_response()
}
