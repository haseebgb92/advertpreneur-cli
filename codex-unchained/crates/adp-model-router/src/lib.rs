#![recursion_limit = "256"]

mod policy;

use adp_model::{AntigravityClient, AntigravityResponse};
use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Response, StatusCode, header};
use axum::routing::{get, post};
use reqwest::Client;
use serde_json::{Value, json};
use policy::{AdpMode, ordered_candidates};
use std::env;
use std::sync::Arc;
use tokio::net::TcpListener;
use uuid::Uuid;

pub const MODEL_ROUTER_ADDR: &str = "127.0.0.1:8766";
const OLLAMA_LOCAL: &str = "http://127.0.0.1:11434";
const OLLAMA_CLOUD_CATALOG: &str = "https://ollama.com/api/tags";
const ANTIGRAVITY_PREFIX: &str = "antigravity/";
const OLLAMA_LOCAL_PREFIX: &str = "ollama-local/";
const OLLAMA_CLOUD_PREFIX: &str = "ollama-cloud/";

#[derive(Clone)]
pub struct ModelRouterState {
    http: Client,
    antigravity: Arc<AntigravityClient>,
}

impl ModelRouterState {
    pub fn new() -> Result<Self, reqwest::Error> {
        Ok(Self {
            http: Client::builder().build()?,
            antigravity: Arc::new(AntigravityClient::new()),
        })
    }
}

pub async fn serve(listener: TcpListener, state: ModelRouterState) -> std::io::Result<()> {
    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/models", get(list_models))
        .route("/v1/responses", post(responses))
        .with_state(state);
    axum::serve(listener, app).await
}

async fn health() -> &'static str {
    "ok"
}

async fn list_models(State(state): State<ModelRouterState>) -> Response<Body> {
    match build_catalog(&state).await {
        Ok(models) => json_response(StatusCode::OK, json!({ "models": models })),
        Err(error) => json_response(
            StatusCode::BAD_GATEWAY,
            json!({ "error": { "message": error } }),
        ),
    }
}

async fn build_catalog(state: &ModelRouterState) -> Result<Vec<Value>, String> {
    let local_endpoint = format!("{OLLAMA_LOCAL}/api/tags");
    let local = fetch_ollama_tags(&state.http, &local_endpoint);
    let cloud = fetch_ollama_tags(&state.http, OLLAMA_CLOUD_CATALOG);
    let agy = {
        let client = state.antigravity.clone();
        tokio::task::spawn_blocking(move || client.list_models())
    };

    let (local, cloud, agy) = tokio::join!(local, cloud, agy);
    let mut models = Vec::new();

    for (index, (slug, label, description)) in [
        (
            "adp/auto",
            "[ADP] Auto",
            "Automatically uses the least-expensive capable Ollama model and moves upward for harder turns.",
        ),
        (
            "adp/economy",
            "[ADP] Economy",
            "Aggressively minimizes cloud cost while retaining Ollama tool-capable fallbacks.",
        ),
        (
            "adp/balanced",
            "[ADP] Balanced",
            "Prefers strong cost-efficient cloud models for everyday coding and agent work.",
        ),
        (
            "adp/max",
            "[ADP] Max",
            "Prefers the strongest available Ollama Cloud model for difficult work.",
        ),
    ]
    .into_iter()
    .enumerate()
    {
        models.push(model_info(
            slug.to_string(),
            label.to_string(),
            description,
            index as i32,
        ));
    }

    if let Ok(names) = local {
        for (index, name) in names.into_iter().enumerate() {
            models.push(model_info(
                format!("{OLLAMA_LOCAL_PREFIX}{name}"),
                format!("[Ollama Local] {name}"),
                "Runs through the local Ollama daemon.",
                100 + index as i32,
            ));
        }
    }

    if let Ok(names) = cloud {
        for (index, name) in names.into_iter().enumerate() {
            models.push(model_info(
                format!("{OLLAMA_CLOUD_PREFIX}{name}"),
                format!("[Ollama Cloud] {name}"),
                "Runs on Ollama Cloud through the signed-in local Ollama daemon.",
                1_000 + index as i32,
            ));
        }
    }

    if let Ok(Ok(names)) = agy {
        for (index, model) in names.into_iter().enumerate() {
            models.push(model_info(
                format!("{ANTIGRAVITY_PREFIX}{}", model.slug),
                format!("[Antigravity] {}", model.label),
                "Uses the existing authenticated Antigravity CLI session as the reasoning backend.",
                2_000 + index as i32,
            ));
        }
    }

    if models.is_empty() {
        return Err(
            "no models discovered: start Ollama or sign in to Antigravity CLI with agy".to_string(),
        );
    }

    Ok(models)
}

async fn fetch_ollama_tags(http: &Client, endpoint: &str) -> Result<Vec<String>, String> {
    let response = http.get(endpoint).send().await.map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("GET {endpoint} returned {}", response.status()));
    }
    let payload: Value = response.json().await.map_err(|e| e.to_string())?;
    let mut names = payload
        .get("models")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|model| {
            model
                .get("name")
                .or_else(|| model.get("model"))
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect::<Vec<_>>();
    names.sort();
    names.dedup();
    Ok(names)
}

fn model_info(slug: String, display_name: String, description: &str, priority: i32) -> Value {
    json!({
        "slug": slug,
        "display_name": display_name,
        "description": description,
        "default_reasoning_level": null,
        "supported_reasoning_levels": [],
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": true,
        "priority": priority,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": null,
        "available_access_programs": null,
        "availability_nux": null,
        "upgrade": null,
        "base_instructions": "You are Codex Unchained. Use the host-provided tools and follow the user's instructions. The selected model is the reasoning backend; Browser, shell, MCP, Web, approvals, and other execution capabilities belong to Codex Unchained.",
        "model_messages": null,
        "include_skills_usage_instructions": true,
        "include_plugin_usage_instructions": true,
        "include_apps_usage_instructions": true,
        "supports_reasoning_summary_parameter": false,
        "support_verbosity": false,
        "default_verbosity": null,
        "apply_patch_tool_type": null,
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "tokens", "limit": 120000},
        "supports_image_detail_original": false,
        "context_window": 131072,
        "max_context_window": null,
        "auto_compact_token_limit": null,
        "comp_hash": null,
        "effective_context_window_percent": 90,
        "experimental_supported_tools": [],
        "supports_search_tool": true,
        "supports_experimental_context": false,
        "use_responses_lite": false,
        "node_repl_auto_review_required": false,
        "node_repl_disabled": false,
        "auto_review_model_override": null,
        "model_specialty": null,
        "tool_mode": null,
        "multi_agent_reasoning_effort": null
    })
}

async fn responses(
    State(state): State<ModelRouterState>,
    axum::Json(mut request): axum::Json<Value>,
) -> Response<Body> {
    let Some(model) = request
        .get("model")
        .and_then(Value::as_str)
        .map(str::to_string)
    else {
        return json_response(
            StatusCode::BAD_REQUEST,
            json!({"error":{"message":"missing model"}}),
        );
    };

    if let Some(mode) = AdpMode::from_virtual_model(&model) {
        return route_adp_mode(&state, mode, request).await;
    }

    if let Some(actual) = model.strip_prefix(OLLAMA_LOCAL_PREFIX) {
        request["model"] = Value::String(actual.to_string());
        return proxy_ollama(&state.http, request).await;
    }

    if let Some(actual) = model.strip_prefix(OLLAMA_CLOUD_PREFIX) {
        return proxy_ollama_cloud(&state.http, actual, request).await;
    }

    if let Some(actual) = model.strip_prefix(ANTIGRAVITY_PREFIX) {
        return antigravity_response(state.antigravity.clone(), actual.to_string(), request).await;
    }

    json_response(
        StatusCode::BAD_REQUEST,
        json!({"error":{"message": format!("unknown Unchained model id: {model}")}}),
    )
}

async fn route_adp_mode(
    state: &ModelRouterState,
    mode: AdpMode,
    mut request: Value,
) -> Response<Body> {
    let local_endpoint = format!("{OLLAMA_LOCAL}/api/tags");
    let local = fetch_ollama_tags(&state.http, &local_endpoint);
    let cloud = fetch_ollama_tags(&state.http, OLLAMA_CLOUD_CATALOG);
    let (local, cloud) = tokio::join!(local, cloud);

    let mut candidates = Vec::new();
    if let Ok(names) = local {
        candidates.extend(
            names
                .into_iter()
                .map(|name| format!("{OLLAMA_LOCAL_PREFIX}{name}")),
        );
    }
    if let Ok(names) = cloud {
        candidates.extend(
            names
                .into_iter()
                .map(|name| format!("{OLLAMA_CLOUD_PREFIX}{name}")),
        );
    }

    let ranked = ordered_candidates(mode, &request, candidates);
    let Some(selected) = ranked.into_iter().next() else {
        return json_response(
            StatusCode::BAD_GATEWAY,
            json!({"error":{"message": format!(
                "ADP {} could not find an available Ollama local/cloud model. Start Ollama, run 'ollama signin', or provide OLLAMA_API_KEY.",
                mode.label()
            )}}),
        );
    };

    eprintln!("ADP {} routed turn to {}", mode.label(), selected);

    if let Some(actual) = selected.strip_prefix(OLLAMA_LOCAL_PREFIX) {
        request["model"] = Value::String(actual.to_string());
        return proxy_ollama(&state.http, request).await;
    }

    if let Some(actual) = selected.strip_prefix(OLLAMA_CLOUD_PREFIX) {
        return proxy_ollama_cloud(&state.http, actual, request).await;
    }

    json_response(
        StatusCode::BAD_GATEWAY,
        json!({"error":{"message": format!("ADP {} selected an invalid backend: {selected}", mode.label())}}),
    )
}

async fn proxy_ollama_cloud(http: &Client, model: &str, mut request: Value) -> Response<Body> {
    request["model"] = Value::String(model.to_string());

    if let Ok(api_key) = env::var("OLLAMA_API_KEY")
        && !api_key.trim().is_empty()
    {
        return proxy_ollama_api(http, request, &api_key).await;
    }

    if let Err(error) = ensure_ollama_model(http, model).await {
        return json_response(StatusCode::BAD_GATEWAY, json!({"error":{"message": error}}));
    }
    proxy_ollama(http, request).await
}

async fn proxy_ollama_api(http: &Client, request: Value, api_key: &str) -> Response<Body> {
    let upstream = match http
        .post("https://ollama.com/v1/responses")
        .bearer_auth(api_key)
        .json(&request)
        .send()
        .await
    {
        Ok(response) => response,
        Err(error) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({"error":{"message": format!("Ollama Cloud API request failed: {error}")}}),
            );
        }
    };

    stream_upstream(upstream)
}

async fn proxy_ollama(http: &Client, request: Value) -> Response<Body> {
    let upstream = match http
        .post(format!("{OLLAMA_LOCAL}/v1/responses"))
        .json(&request)
        .send()
        .await
    {
        Ok(response) => response,
        Err(error) => {
            return json_response(
                StatusCode::BAD_GATEWAY,
                json!({"error":{"message": format!("Ollama request failed: {error}")}}),
            );
        }
    };

    stream_upstream(upstream)
}

fn stream_upstream(upstream: reqwest::Response) -> Response<Body> {
    let status =
        StatusCode::from_u16(upstream.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    let content_type = upstream
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("application/json")
        .to_string();
    let stream = upstream.bytes_stream();

    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, content_type)
        .body(Body::from_stream(stream))
        .unwrap()
}

async fn ensure_ollama_model(http: &Client, model: &str) -> Result<(), String> {
    let installed = fetch_ollama_tags(http, &format!("{OLLAMA_LOCAL}/api/tags"))
        .await
        .unwrap_or_default();
    if installed.iter().any(|name| name == model) {
        return Ok(());
    }

    let response = http
        .post(format!("{OLLAMA_LOCAL}/api/pull"))
        .json(&json!({"model": model, "stream": false}))
        .send()
        .await
        .map_err(|e| format!("could not prepare Ollama Cloud model {model}: {e}"))?;
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(format!(
            "could not prepare Ollama Cloud model {model}: {status} {body}"
        ));
    }
    Ok(())
}

async fn antigravity_response(
    client: Arc<AntigravityClient>,
    model: String,
    request: Value,
) -> Response<Body> {
    let prompt = antigravity_prompt(&request);
    let schema = decision_schema();
    let result = tokio::task::spawn_blocking(move || {
        client.chat_structured(&model, &prompt, &schema, "adp-unchained-brain")
    })
    .await;

    let response = match result {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => {
            return sse_error(format!("Antigravity request failed: {error}"));
        }
        Err(error) => {
            return sse_error(format!("Antigravity worker failed: {error}"));
        }
    };

    if response.status != "SUCCESS" {
        return sse_error(
            response
                .error
                .unwrap_or_else(|| format!("Antigravity status {}", response.status)),
        );
    }

    let decision = response
        .structured_output
        .clone()
        .or_else(|| serde_json::from_str::<Value>(&response.response).ok())
        .unwrap_or_else(
            || json!({"kind":"message","text":response.response,"name":"","arguments":{}}),
        );

    let id = format!("resp_{}", Uuid::new_v4().simple());
    let item_id = format!("item_{}", Uuid::new_v4().simple());
    let mut events = vec![json!({
        "type":"response.created",
        "response":{"id":id}
    })];

    match decision.get("kind").and_then(Value::as_str) {
        Some("function_call") => {
            let name = decision
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let arguments = decision
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));
            events.push(json!({
                "type":"response.output_item.done",
                "item":{
                    "type":"function_call",
                    "id": item_id,
                    "call_id": format!("call_{}", Uuid::new_v4().simple()),
                    "name": name,
                    "arguments": serde_json::to_string(&arguments).unwrap_or_else(|_| "{}".to_string())
                }
            }));
        }
        _ => {
            let text = decision
                .get("text")
                .and_then(Value::as_str)
                .unwrap_or(&response.response);
            events.push(json!({
                "type":"response.output_item.done",
                "item":{
                    "type":"message",
                    "role":"assistant",
                    "id": item_id,
                    "content":[{"type":"output_text","text":text}]
                }
            }));
        }
    }

    events.push(completed_event(&id, &response));
    sse_response(events)
}

fn antigravity_prompt(request: &Value) -> String {
    let instructions = request
        .get("instructions")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let input = request.get("input").cloned().unwrap_or_else(|| json!([]));
    let tools = request.get("tools").cloned().unwrap_or_else(|| json!([]));

    format!(
        "You are the reasoning backend for Codex Unchained. Do not use Antigravity's own tools. \
Only reason over the supplied conversation and choose either a final assistant message or exactly \
one host tool call. Host tools are executed by Codex, not by you. If a tool is needed, use its exact \
name and valid JSON arguments. If no tool is needed, return a message.\n\nSYSTEM INSTRUCTIONS:\n{instructions}\n\nCONVERSATION ITEMS JSON:\n{input}\n\nAVAILABLE HOST TOOLS JSON:\n{tools}"
    )
}

fn decision_schema() -> Value {
    json!({
        "type":"object",
        "properties":{
            "kind":{"type":"string","enum":["message","function_call"]},
            "text":{"type":"string"},
            "name":{"type":"string"},
            "arguments":{"type":"object"}
        },
        "required":["kind","text","name","arguments"],
        "additionalProperties":false
    })
}

fn completed_event(id: &str, response: &AntigravityResponse) -> Value {
    json!({
        "type":"response.completed",
        "response":{
            "id": id,
            "usage":{
                "input_tokens": response.usage.input_tokens,
                "input_tokens_details":{"cached_tokens":response.usage.cache_read_tokens},
                "output_tokens": response.usage.output_tokens,
                "output_tokens_details":{"reasoning_tokens":response.usage.thinking_tokens},
                "total_tokens": response.usage.total_tokens
            }
        }
    })
}

fn sse_error(message: String) -> Response<Body> {
    sse_response(vec![json!({
        "type":"response.failed",
        "response":{"error":{"code":"adp_provider_error","message":message}}
    })])
}

fn sse_response(events: Vec<Value>) -> Response<Body> {
    let mut body = String::new();
    for event in events {
        body.push_str("data: ");
        body.push_str(&serde_json::to_string(&event).unwrap());
        body.push_str("\n\n");
    }
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream")
        .body(Body::from(body))
        .unwrap()
}

fn json_response(status: StatusCode, value: Value) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(serde_json::to_vec(&value).unwrap()))
        .unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_catalog_entries_are_visible_and_tool_capable() {
        let value = model_info(
            "ollama-local/qwen3:1.7b".to_string(),
            "[Ollama Local] qwen3:1.7b".to_string(),
            "test",
            1,
        );
        assert_eq!(value["visibility"], "list");
        assert_eq!(value["supports_search_tool"], true);
        assert_eq!(value["node_repl_disabled"], false);
        assert!(
            value["base_instructions"]
                .as_str()
                .is_some_and(|instructions| instructions.contains("Codex Unchained"))
        );
    }

    #[test]
    fn antigravity_schema_allows_message_or_host_tool_call() {
        let schema = decision_schema();
        assert_eq!(schema["properties"]["kind"]["enum"][0], "message");
        assert_eq!(schema["properties"]["kind"]["enum"][1], "function_call");
    }

    #[test]
    fn prompt_contains_codex_tools_but_forbids_antigravity_tools() {
        let request = json!({
            "instructions":"be concise",
            "input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"inspect"}]}],
            "tools":[{"type":"function","name":"adp_browser_inspect","parameters":{"type":"object"}}]
        });
        let prompt = antigravity_prompt(&request);
        assert!(prompt.contains("adp_browser_inspect"));
        assert!(prompt.contains("Do not use Antigravity's own tools"));
    }
}
