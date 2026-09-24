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
use std::{env, net::SocketAddr, process::Stdio, sync::Arc};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use uuid::Uuid;

#[derive(Clone)]
struct AppState {
    agy_bin: String,
    codex_bin: String,
    ollama_bin: String,
    ollama_url: String,
    agent: String,
    timeout: String,
    http: reqwest::Client,
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

#[derive(Debug, Clone)]
struct DiscoveredModel {
    slug: String,
    display_name: String,
    description: String,
    priority: i32,
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
        codex_bin: env::var("UNCHAINED_CODEX_BIN").unwrap_or_else(|_| "codex".into()),
        ollama_bin: env::var("UNCHAINED_OLLAMA_BIN").unwrap_or_else(|_| "ollama".into()),
        ollama_url: env::var("OLLAMA_HOST")
            .unwrap_or_else(|_| "http://127.0.0.1:11434".into())
            .trim_end_matches('/')
            .to_string(),
        agent: env::var("UNCHAINED_AGY_AGENT")
            .unwrap_or_else(|_| "codex-unchained-brain".into()),
        timeout: env::var("UNCHAINED_AGY_TIMEOUT").unwrap_or_else(|_| "10m".into()),
        http: reqwest::Client::new(),
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
        .expect("bind Unchained brain gateway");

    eprintln!("Codex Unchained brain gateway listening on http://{addr}/v1");
    axum::serve(listener, app)
        .await
        .expect("serve Unchained brain gateway");
}

async fn health() -> impl IntoResponse {
    Json(json!({"ok": true, "service": "codex-unchained-brain-gateway"}))
}

async fn models(State(state): State<Arc<AppState>>) -> Response {
    match build_codex_catalog(&state).await {
        Ok(catalog) => Json(catalog).into_response(),
        Err(message) => error_response(StatusCode::BAD_GATEWAY, message),
    }
}

async fn build_codex_catalog(state: &AppState) -> Result<Value, String> {
    let output = Command::new(&state.codex_bin)
        .args(["debug", "models", "--bundled"])
        .output()
        .await
        .map_err(|err| format!("could not run '{} debug models --bundled': {err}", state.codex_bin))?;

    if !output.status.success() {
        return Err(format!(
            "could not read bundled Codex model metadata: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }

    let bundled: Value = serde_json::from_slice(&output.stdout)
        .map_err(|err| format!("Codex bundled model catalog was not valid JSON: {err}"))?;

    let templates = bundled
        .get("models")
        .and_then(Value::as_array)
        .ok_or_else(|| "Codex bundled catalog did not contain a models array".to_string())?;

    let template = templates
        .iter()
        .filter(|model| model.get("visibility").and_then(Value::as_str) == Some("list"))
        .min_by_key(|model| model.get("priority").and_then(Value::as_i64).unwrap_or(i64::MAX))
        .or_else(|| templates.first())
        .cloned()
        .ok_or_else(|| "Codex bundled catalog is empty".to_string())?;

    let mut discovered = discover_agy_models(state).await;
    discovered.extend(discover_ollama_models(state).await);

    if discovered.is_empty() {
        return Err(
            "No Unchained brains were discovered. Install/login to AGY or install/start Ollama."
                .into(),
        );
    }

    discovered.sort_by_key(|model| model.priority);

    let mut models = Vec::with_capacity(discovered.len());
    for discovered in discovered {
        let mut model = template.clone();
        model["slug"] = Value::String(discovered.slug);
        model["display_name"] = Value::String(discovered.display_name);
        model["description"] = Value::String(discovered.description);
        model["priority"] = Value::Number(discovered.priority.into());
        model["visibility"] = Value::String("list".into());
        model["supported_in_api"] = Value::Bool(true);

        // Provider-agnostic brains receive Codex's complete prompt/tool schema
        // through the gateway. Provider-specific request knobs must stay off.
        model["default_reasoning_level"] = Value::Null;
        model["supported_reasoning_levels"] = Value::Array(Vec::new());
        model["additional_speed_tiers"] = Value::Array(Vec::new());
        model["service_tiers"] = Value::Array(Vec::new());
        model["default_service_tier"] = Value::Null;
        model["available_access_programs"] = Value::Null;
        model["availability_nux"] = Value::Null;
        model["upgrade"] = Value::Null;
        model["supports_reasoning_summary_parameter"] = Value::Bool(false);
        model["support_verbosity"] = Value::Bool(false);
        model["default_verbosity"] = Value::Null;
        model["supports_search_tool"] = Value::Bool(false);
        model["input_modalities"] = json!(["text"]);
        model["tool_mode"] = Value::String("direct".into());
        model["use_responses_lite"] = Value::Bool(false);

        // Keep Codex's own base instructions, shell/apply-patch, compaction
        // policy, and other runtime metadata. The direct tool presentation is
        // selected deliberately because AGY/Ollama consume ordinary Codex tool
        // schemas rather than Astra's provider-specific code-mode transport.
        // behavior, compaction policy, and other runtime metadata from the
        // currently installed official Codex build.
        models.push(model);
    }

    Ok(json!({"models": models}))
}

async fn discover_agy_models(state: &AppState) -> Vec<DiscoveredModel> {
    let Ok(output) = Command::new(&state.agy_bin).arg("models").output().await else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut models = Vec::new();
    for (index, line) in stdout.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let mut parts = line.split_whitespace();
        let Some(raw_slug) = parts.next() else {
            continue;
        };
        let label = parts.collect::<Vec<_>>().join(" ");
        let display = if label.is_empty() {
            raw_slug.to_string()
        } else {
            label
        };
        let preferred = raw_slug == "gemini-3.8-flash-medium";
        models.push(DiscoveredModel {
            slug: format!("agy/{raw_slug}"),
            display_name: format!("{display} · AGY"),
            description: format!("AGY brain: {raw_slug}"),
            priority: if preferred { 0 } else { 10 + index as i32 },
        });
    }
    models
}

async fn discover_ollama_models(state: &AppState) -> Vec<DiscoveredModel> {
    let Ok(output) = Command::new(&state.ollama_bin).arg("list").output().await else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .lines()
        .skip_while(|line| line.trim().is_empty())
        .enumerate()
        .filter_map(|(index, line)| {
            let slug = line.split_whitespace().next()?;
            if slug.eq_ignore_ascii_case("name") || slug.is_empty() {
                return None;
            }
            Some(DiscoveredModel {
                slug: format!("ollama/{slug}"),
                display_name: format!("{slug} · Ollama"),
                description: format!("Ollama brain: {slug}"),
                priority: 100 + index as i32,
            })
        })
        .collect()
}

async fn responses(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ResponsesRequest>,
) -> Response {
    let available_tools = flatten_tools(&req.tools);
    let prompt = build_prompt(&req);
    let schema_value = decision_schema();
    let schema_string = schema_value.to_string();

    let decision = if let Some(model) = req.model.strip_prefix("agy/") {
        match infer_agy(&state, model, prompt, schema_string).await {
            Ok(decision) => decision,
            Err(message) => return error_response(StatusCode::BAD_GATEWAY, message),
        }
    } else if let Some(model) = req.model.strip_prefix("ollama/") {
        match infer_ollama(&state, model, prompt, schema_value).await {
            Ok(decision) => decision,
            Err(message) => return error_response(StatusCode::BAD_GATEWAY, message),
        }
    } else {
        return error_response(
            StatusCode::BAD_REQUEST,
            format!(
                "Unknown Unchained model '{}'. Select a model from Codex /model.",
                req.model
            ),
        );
    };

    if let Err(message) = validate_decision(&req, &decision, &available_tools) {
        return error_response(StatusCode::BAD_GATEWAY, message);
    }

    render_responses_sse(&req, &decision, &available_tools)
}

fn decision_schema() -> Value {
    json!({
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
}

async fn infer_agy(
    state: &AppState,
    model: &str,
    prompt: String,
    schema: String,
) -> Result<Value, String> {
    let mut cmd = Command::new(&state.agy_bin);
    cmd.arg("--input-format")
        .arg("stream-json")
        .arg("--output-format")
        .arg("stream-json")
        .arg("--model")
        .arg(model)
        .arg("--agent")
        .arg(&state.agent)
        .arg("--json-schema")
        .arg(schema)
        .arg("--print-timeout")
        .arg(&state.timeout)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd
        .spawn()
        .map_err(|err| format!("could not run AGY: {err}"))?;

    let user_event = json!({
        "event": "user",
        "message": {"content": prompt}
    })
    .to_string();

    if let Some(mut stdin) = child.stdin.take() {
        if let Err(err) = stdin.write_all(format!("{user_event}\n").as_bytes()).await {
            let _ = child.kill().await;
            return Err(format!("could not send prompt to AGY: {err}"));
        }
        drop(stdin);
    }

    let output = child
        .wait_with_output()
        .await
        .map_err(|err| format!("could not wait for AGY: {err}"))?;

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
        return Err(format!(
            "AGY returned no result event (exit {}): {}",
            output.status,
            stderr.trim()
        ));
    };

    if !output.status.success()
        || result.get("status").and_then(Value::as_str) != Some("SUCCESS")
    {
        return Err(
            result
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
                }),
        );
    }

    match result.get("structured_output") {
        Some(Value::Object(map)) => Ok(Value::Object(map.clone())),
        _ => Err("AGY did not return structured_output".into()),
    }
}

async fn infer_ollama(
    state: &AppState,
    model: &str,
    prompt: String,
    schema: Value,
) -> Result<Value, String> {
    let url = format!("{}/api/chat", state.ollama_url);
    let response = state
        .http
        .post(url)
        .json(&json!({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": false,
            "format": schema
        }))
        .send()
        .await
        .map_err(|err| {
            format!(
                "could not reach Ollama at {}: {err}. Start Ollama first.",
                state.ollama_url
            )
        })?;

    let status = response.status();
    let payload: Value = response
        .json()
        .await
        .map_err(|err| format!("Ollama returned invalid JSON: {err}"))?;

    if !status.is_success() {
        let message = payload
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Ollama request failed");
        return Err(format!("Ollama HTTP {}: {message}", status.as_u16()));
    }

    let content = payload
        .pointer("/message/content")
        .and_then(Value::as_str)
        .ok_or_else(|| "Ollama response did not contain message.content".to_string())?;

    serde_json::from_str(content)
        .map_err(|err| format!("Ollama brain did not return the required structured decision: {err}"))
}

fn validate_decision(
    req: &ResponsesRequest,
    decision: &Value,
    available_tools: &[ToolRecord],
) -> Result<(), String> {
    let decision_type = decision
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("assistant");

    if decision_type != "tool_calls" {
        return Ok(());
    }

    let calls = decision
        .get("tool_calls")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();

    if calls.is_empty() {
        return Err("brain selected tool_calls but returned no calls".into());
    }
    if !req.parallel_tool_calls && calls.len() > 1 {
        return Err("brain returned parallel tool calls when Codex disabled them".into());
    }
    if req.tool_choice.as_str() == Some("none") {
        return Err("brain selected a tool while Codex tool_choice was none".into());
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

        if find_tool(available_tools, namespace, name).is_none() {
            let display = namespace
                .map(|ns| format!("{ns}.{name}"))
                .unwrap_or_else(|| name.to_string());
            return Err(format!("brain selected unavailable Codex tool: {display}"));
        }
    }

    Ok(())
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

Never execute provider-native tools. Never browse on your own. Never run
commands on your own. Never read or write files on your own. Your only job is
to decide what Codex should say next or which Codex-provided tool(s) Codex
should execute.

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
