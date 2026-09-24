use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env,
    net::SocketAddr,
    sync::Arc,
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader, BufWriter},
    net::TcpListener,
    sync::{mpsc, oneshot, Mutex, RwLock},
    time::timeout,
};
use uuid::Uuid;

const PROVIDER_HEADER: &str = "x-adp-provider";
const SESSION_HEADER: &str = "x-adp-session";
const PROTOCOL_VERSION: u32 = 2;
const LONG_POLL: Duration = Duration::from_secs(25);
const BROWSER_WAIT: Duration = Duration::from_secs(20);
const ACTION_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Debug, Clone, Deserialize)]
struct ProviderRegistration {
    protocol_version: u32,
    provider_id: String,
    session_key: String,
    #[allow(dead_code)]
    label: String,
    #[allow(dead_code)]
    extension_version: String,
    #[allow(dead_code)]
    capabilities: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct BrowserCommand {
    command_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    tab_id: Option<i64>,
    action: Value,
}

#[derive(Debug, Clone, Deserialize)]
struct BrowserCommandResult {
    command_id: String,
    ok: bool,
    #[serde(default)]
    result: Value,
    #[serde(default)]
    error: Option<String>,
}

struct Provider {
    session_key: String,
    commands_tx: mpsc::Sender<BrowserCommand>,
    commands_rx: Mutex<mpsc::Receiver<BrowserCommand>>,
    pending: Mutex<HashMap<String, oneshot::Sender<BrowserCommandResult>>>,
}

#[derive(Clone, Default)]
struct Broker {
    providers: Arc<RwLock<HashMap<String, Arc<Provider>>>>,
}

impl Broker {
    async fn register(&self, registration: ProviderRegistration) -> Result<(), String> {
        if registration.protocol_version != PROTOCOL_VERSION {
            return Err(format!(
                "browser protocol {} is unsupported; expected {}",
                registration.protocol_version, PROTOCOL_VERSION
            ));
        }

        let mut providers = self.providers.write().await;
        if let Some(existing) = providers.get(&registration.provider_id) {
            if existing.session_key != registration.session_key {
                return Err("browser provider session key does not match".into());
            }
            return Ok(());
        }

        let (commands_tx, commands_rx) = mpsc::channel(64);
        providers.insert(
            registration.provider_id,
            Arc::new(Provider {
                session_key: registration.session_key,
                commands_tx,
                commands_rx: Mutex::new(commands_rx),
                pending: Mutex::new(HashMap::new()),
            }),
        );
        Ok(())
    }

    async fn provider(&self, id: &str, key: &str) -> Result<Arc<Provider>, String> {
        let providers = self.providers.read().await;
        let provider = providers
            .get(id)
            .cloned()
            .ok_or_else(|| "browser provider is not registered".to_string())?;
        if provider.session_key != key {
            return Err("browser provider session key does not match".into());
        }
        Ok(provider)
    }

    async fn first_provider(&self) -> Option<(String, Arc<Provider>)> {
        self.providers
            .read()
            .await
            .iter()
            .next()
            .map(|(id, provider)| (id.clone(), Arc::clone(provider)))
    }

    async fn wait_for_provider(&self) -> Result<(String, Arc<Provider>), String> {
        let deadline = tokio::time::Instant::now() + BROWSER_WAIT;
        loop {
            if let Some(provider) = self.first_provider().await {
                return Ok(provider);
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(
                    "no Codex Unchained Chrome/Edge extension is connected to 127.0.0.1:8765"
                        .into(),
                );
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    }

    async fn call(&self, action: Value) -> Result<BrowserCommandResult, String> {
        let (_id, provider) = self.wait_for_provider().await?;
        let command_id = Uuid::new_v4().to_string();
        let command = BrowserCommand {
            command_id: command_id.clone(),
            tab_id: None,
            action,
        };
        let (tx, rx) = oneshot::channel();
        provider.pending.lock().await.insert(command_id.clone(), tx);
        if provider.commands_tx.send(command).await.is_err() {
            provider.pending.lock().await.remove(&command_id);
            return Err("browser extension command queue is closed".into());
        }
        match timeout(ACTION_TIMEOUT, rx).await {
            Ok(Ok(result)) => Ok(result),
            Ok(Err(_)) => Err("browser extension result channel closed".into()),
            Err(_) => {
                provider.pending.lock().await.remove(&command_id);
                Err("browser extension command timed out".into())
            }
        }
    }
}

fn credentials(headers: &HeaderMap) -> Result<(String, String), String> {
    let provider = headers
        .get(PROVIDER_HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "missing browser provider header".to_string())?;
    let session = headers
        .get(SESSION_HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "missing browser session header".to_string())?;
    Ok((provider.to_string(), session.to_string()))
}

async fn health(State(broker): State<Broker>) -> Json<Value> {
    let connected = broker.providers.read().await.len();
    Json(json!({
        "ok": true,
        "service": "codex-unchained-browser-mcp",
        "connected_browsers": connected
    }))
}

async fn register(
    State(broker): State<Broker>,
    headers: HeaderMap,
    Json(registration): Json<ProviderRegistration>,
) -> (StatusCode, Json<Value>) {
    let header_creds = credentials(&headers);
    let body_matches = header_creds.as_ref().is_ok_and(|(provider, session)| {
        provider == &registration.provider_id && session == &registration.session_key
    });
    if !body_matches {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "browser registration credentials do not match"})),
        );
    }

    match broker.register(registration).await {
        Ok(()) => (StatusCode::OK, Json(json!({"ok": true}))),
        Err(error) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": error})),
        ),
    }
}

async fn next_command(
    State(broker): State<Broker>,
    headers: HeaderMap,
) -> (StatusCode, Json<Value>) {
    let Ok((id, key)) = credentials(&headers) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "missing browser credentials"})),
        );
    };
    let Ok(provider) = broker.provider(&id, &key).await else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "browser provider is not registered"})),
        );
    };

    let mut rx = provider.commands_rx.lock().await;
    let command = timeout(LONG_POLL, rx.recv()).await.ok().flatten();
    (StatusCode::OK, Json(json!({"command": command})))
}

async fn submit_result(
    State(broker): State<Broker>,
    headers: HeaderMap,
    Json(result): Json<BrowserCommandResult>,
) -> (StatusCode, Json<Value>) {
    let Ok((id, key)) = credentials(&headers) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "missing browser credentials"})),
        );
    };
    let Ok(provider) = broker.provider(&id, &key).await else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "browser provider is not registered"})),
        );
    };
    let sender = provider.pending.lock().await.remove(&result.command_id);
    if let Some(sender) = sender {
        let _ = sender.send(result);
        (StatusCode::OK, Json(json!({"ok": true})))
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"ok": false, "error": "browser command is no longer pending"})),
        )
    }
}

async fn learn(
    State(_broker): State<Broker>,
    headers: HeaderMap,
    Json(_event): Json<Value>,
) -> (StatusCode, Json<Value>) {
    if credentials(&headers).is_err() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "missing browser credentials"})),
        );
    }
    // Teach/recording is deliberately not part of the clean Unchained browser adapter.
    (StatusCode::OK, Json(json!({"ok": true, "recorded": false})))
}

fn browser_tools() -> Vec<Value> {
    let target_schema = json!({
        "type": "object",
        "properties": {
            "role": {"type": ["string", "null"]},
            "name": {"type": ["string", "null"]},
            "test_id": {"type": ["string", "null"]},
            "aria_label": {"type": ["string", "null"]},
            "css": {"type": ["string", "null"]}
        },
        "additionalProperties": false
    });

    vec![
        tool(
            "browser_bind_active",
            "Bind Codex Unchained to the currently active Chrome/Edge tab.",
            json!({"type":"object","properties":{},"additionalProperties":false}),
            false,
        ),
        tool(
            "browser_inspect",
            "Read a compact semantic snapshot of the active browser tab, including URL, title and interactive controls.",
            json!({"type":"object","properties":{},"additionalProperties":false}),
            true,
        ),
        tool(
            "browser_navigate",
            "Navigate the bound Chrome/Edge tab to a URL.",
            json!({
                "type":"object",
                "properties":{"url":{"type":"string"}},
                "required":["url"],
                "additionalProperties":false
            }),
            false,
        ),
        tool(
            "browser_click",
            "Click an element in the bound tab using a semantic target.",
            json!({
                "type":"object",
                "properties":{"target":target_schema.clone()},
                "required":["target"],
                "additionalProperties":false
            }),
            false,
        ),
        tool(
            "browser_fill",
            "Fill a non-sensitive input in the bound tab using a semantic target.",
            json!({
                "type":"object",
                "properties":{
                    "target":target_schema,
                    "value":{"type":"string"}
                },
                "required":["target","value"],
                "additionalProperties":false
            }),
            false,
        ),
        tool(
            "browser_scroll",
            "Scroll the bound tab vertically by a pixel amount.",
            json!({
                "type":"object",
                "properties":{"amount":{"type":"integer"}},
                "required":["amount"],
                "additionalProperties":false
            }),
            false,
        ),
        tool(
            "browser_screenshot",
            "Capture the currently visible area of the bound Chrome/Edge tab.",
            json!({"type":"object","properties":{},"additionalProperties":false}),
            true,
        ),
        tool(
            "browser_wait_download",
            "Wait for a Chrome/Edge download newer than the given download id to complete.",
            json!({
                "type":"object",
                "properties":{
                    "after_id":{"type":"integer"},
                    "timeout_ms":{"type":"integer","minimum":1000,"maximum":120000}
                },
                "required":["after_id"],
                "additionalProperties":false
            }),
            true,
        ),
    ]
}

fn tool(name: &str, description: &str, input_schema: Value, read_only: bool) -> Value {
    json!({
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "annotations": {"readOnlyHint": read_only}
    })
}

fn action_for_tool(name: &str, args: &Value) -> Result<Value, String> {
    match name {
        "browser_bind_active" => Ok(json!({"action":"bind_active"})),
        "browser_inspect" => Ok(json!({"action":"inspect"})),
        "browser_navigate" => Ok(json!({
            "action":"navigate",
            "url": required_string(args, "url")?
        })),
        "browser_click" => Ok(json!({
            "action":"click",
            "target": required_value(args, "target")?
        })),
        "browser_fill" => Ok(json!({
            "action":"fill",
            "target": required_value(args, "target")?,
            "value": required_string(args, "value")?
        })),
        "browser_scroll" => Ok(json!({
            "action":"scroll",
            "amount": args.get("amount").and_then(Value::as_i64)
                .ok_or_else(|| "'amount' must be an integer".to_string())?
        })),
        "browser_screenshot" => Ok(json!({"action":"screenshot"})),
        "browser_wait_download" => Ok(json!({
            "action":"wait_for_download",
            "after_id": args.get("after_id").and_then(Value::as_i64)
                .ok_or_else(|| "'after_id' must be an integer".to_string())?,
            "timeout_ms": args.get("timeout_ms").and_then(Value::as_u64).unwrap_or(45000)
        })),
        _ => Err(format!("unknown browser tool: {name}")),
    }
}

fn required_string(args: &Value, key: &str) -> Result<String, String> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| format!("'{key}' must be a string"))
}

fn required_value(args: &Value, key: &str) -> Result<Value, String> {
    args.get(key)
        .cloned()
        .ok_or_else(|| format!("missing '{key}'"))
}

async fn call_tool(broker: &Broker, params: Option<&Value>) -> Result<Value, String> {
    let params = params.ok_or_else(|| "missing tools/call params".to_string())?;
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| "tools/call name must be text".to_string())?;
    let args = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let action = action_for_tool(name, &args)?;
    let response = broker.call(action).await?;

    if !response.ok {
        return Err(response
            .error
            .unwrap_or_else(|| "browser extension action failed".to_string()));
    }

    if name == "browser_screenshot" {
        if let Some(data_url) = response.result.get("data_url").and_then(Value::as_str) {
            if let Some(data) = data_url.strip_prefix("data:image/png;base64,") {
                return Ok(json!({
                    "content": [{
                        "type": "image",
                        "data": data,
                        "mimeType": "image/png"
                    }],
                    "isError": false
                }));
            }
        }
    }

    Ok(json!({
        "content": [{
            "type": "text",
            "text": serde_json::to_string(&response.result).unwrap_or_else(|_| "{}".into())
        }],
        "isError": false
    }))
}

async fn run_mcp(broker: Broker) -> Result<(), Box<dyn std::error::Error>> {
    let stdin = tokio::io::stdin();
    let stdout = tokio::io::stdout();
    let mut lines = BufReader::new(stdin).lines();
    let mut writer = BufWriter::new(stdout);

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }

        let request: Value = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(error) => {
                write_json(
                    &mut writer,
                    &json!({
                        "jsonrpc":"2.0",
                        "id":null,
                        "error":{"code":-32700,"message":format!("parse error: {error}")}
                    }),
                )
                .await?;
                continue;
            }
        };

        let Some(id) = request.get("id").cloned() else {
            continue;
        };
        let method = request
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or("");

        let result = match method {
            "initialize" => {
                let version = request
                    .pointer("/params/protocolVersion")
                    .cloned()
                    .unwrap_or_else(|| json!("2025-06-18"));
                Ok(json!({
                    "protocolVersion": version,
                    "capabilities":{"tools":{"listChanged":false}},
                    "serverInfo":{
                        "name":"codex-unchained-browser",
                        "version":env!("CARGO_PKG_VERSION")
                    },
                    "instructions":"Chrome/Edge browser tools. Codex owns planning and tool selection; this MCP server only transports selected browser actions to the local extension."
                }))
            }
            "ping" => Ok(json!({})),
            "tools/list" => Ok(json!({"tools":browser_tools()})),
            "tools/call" => call_tool(&broker, request.get("params")).await,
            "resources/list" => Ok(json!({"resources":[]})),
            "resources/templates/list" => Ok(json!({"resourceTemplates":[]})),
            "prompts/list" => Ok(json!({"prompts":[]})),
            _ => Err(format!("method not found: {method}")),
        };

        let response = match result {
            Ok(result) => json!({"jsonrpc":"2.0","id":id,"result":result}),
            Err(message) => json!({
                "jsonrpc":"2.0",
                "id":id,
                "error":{"code":-32000,"message":message}
            }),
        };
        write_json(&mut writer, &response).await?;
    }

    Ok(())
}

async fn write_json<W: tokio::io::AsyncWrite + Unpin>(
    writer: &mut W,
    value: &Value,
) -> Result<(), std::io::Error> {
    writer.write_all(serde_json::to_string(value).unwrap().as_bytes()).await?;
    writer.write_all(b"\n").await?;
    writer.flush().await
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let host = env::var("UNCHAINED_BROWSER_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let port: u16 = env::var("UNCHAINED_BROWSER_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(8765);
    let broker = Broker::default();

    let app = Router::new()
        .route("/health", get(health))
        .route("/v2/browser/register", post(register))
        .route("/v2/browser/next", get(next_command))
        .route("/v2/browser/result", post(submit_result))
        .route("/v2/browser/learn", post(learn))
        .with_state(broker.clone());

    let addr: SocketAddr = format!("{host}:{port}").parse()?;
    let listener = TcpListener::bind(addr).await?;
    tokio::spawn(async move {
        if let Err(error) = axum::serve(listener, app).await {
            eprintln!("Codex Unchained browser bridge stopped: {error}");
        }
    });

    run_mcp(broker).await
}
