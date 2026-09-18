use adp_browser_bridge::{
    BrowserCommand, BrowserCommandResult, LearnEvent, PROTOCOL_VERSION, ProviderRegistration,
    sanitize_learn_event,
};
use adp_memory::safe_url;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::io;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use thiserror::Error;
use tokio::net::TcpListener;
use tokio::sync::{Mutex, RwLock, broadcast, mpsc, oneshot};
use tokio::time::timeout;

const PROVIDER_HEADER: &str = "x-adp-provider";
const SESSION_HEADER: &str = "x-adp-session";
const DEFAULT_QUEUE_DEPTH: usize = 64;
const DEFAULT_LONG_POLL: Duration = Duration::from_secs(25);

#[derive(Debug, Error)]
pub enum BrokerError {
    #[error("browser provider is not registered")]
    UnknownProvider,
    #[error("browser provider session key does not match")]
    SessionMismatch,
    #[error("browser provider queue is closed")]
    QueueClosed,
    #[error("browser command timed out")]
    Timeout,
    #[error("browser protocol version {0} is unsupported")]
    UnsupportedProtocol(u32),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LearnEnvelope {
    pub provider_id: String,
    pub tab_id: Option<i64>,
    pub url: String,
    pub title: String,
    pub event: LearnEvent,
}

#[derive(Debug, Clone, Deserialize)]
struct LearnRequest {
    #[serde(default)]
    tab_id: Option<i64>,
    #[serde(default)]
    url: String,
    #[serde(default)]
    title: String,
    event: LearnEvent,
}

#[derive(Debug, Clone, Serialize)]
struct NextResponse {
    command: Option<BrowserCommand>,
}

struct ProviderConnection {
    session_key: String,
    registration: RwLock<ProviderRegistration>,
    commands_tx: mpsc::Sender<BrowserCommand>,
    commands_rx: Mutex<mpsc::Receiver<BrowserCommand>>,
    pending: Mutex<HashMap<String, oneshot::Sender<BrowserCommandResult>>>,
}

struct BrokerInner {
    providers: RwLock<HashMap<String, Arc<ProviderConnection>>>,
    learn_tx: broadcast::Sender<LearnEnvelope>,
    next_call_id: AtomicU64,
}

#[derive(Clone)]
pub struct BrokerState {
    inner: Arc<BrokerInner>,
}

impl Default for BrokerState {
    fn default() -> Self {
        Self::new()
    }
}

impl BrokerState {
    pub fn new() -> Self {
        let (learn_tx, _) = broadcast::channel(256);
        Self {
            inner: Arc::new(BrokerInner {
                providers: RwLock::new(HashMap::new()),
                learn_tx,
                next_call_id: AtomicU64::new(1),
            }),
        }
    }

    pub async fn register(&self, registration: ProviderRegistration) -> Result<(), BrokerError> {
        if registration.protocol_version != PROTOCOL_VERSION {
            return Err(BrokerError::UnsupportedProtocol(
                registration.protocol_version,
            ));
        }

        let mut providers = self.inner.providers.write().await;
        if let Some(existing) = providers.get(&registration.provider_id) {
            if existing.session_key != registration.session_key {
                return Err(BrokerError::SessionMismatch);
            }
            *existing.registration.write().await = registration;
            return Ok(());
        }

        let (commands_tx, commands_rx) = mpsc::channel(DEFAULT_QUEUE_DEPTH);
        providers.insert(
            registration.provider_id.clone(),
            Arc::new(ProviderConnection {
                session_key: registration.session_key.clone(),
                registration: RwLock::new(registration),
                commands_tx,
                commands_rx: Mutex::new(commands_rx),
                pending: Mutex::new(HashMap::new()),
            }),
        );
        Ok(())
    }

    pub async fn provider_ids(&self) -> Vec<String> {
        let mut ids = self
            .inner
            .providers
            .read()
            .await
            .keys()
            .cloned()
            .collect::<Vec<_>>();
        ids.sort();
        ids
    }

    pub fn subscribe_learn(&self) -> broadcast::Receiver<LearnEnvelope> {
        self.inner.learn_tx.subscribe()
    }

    pub async fn call(
        &self,
        provider_id: &str,
        tab_id: Option<i64>,
        action: adp_browser_bridge::BrowserAction,
        wait: Duration,
    ) -> Result<BrowserCommandResult, BrokerError> {
        let connection = self.connection(provider_id).await?;
        let call_id = self.inner.next_call_id.fetch_add(1, Ordering::Relaxed);
        let command = BrowserCommand {
            command_id: format!("adp-{call_id}"),
            tab_id,
            action,
        };

        let (result_tx, result_rx) = oneshot::channel();
        connection
            .pending
            .lock()
            .await
            .insert(command.command_id.clone(), result_tx);

        if connection.commands_tx.send(command.clone()).await.is_err() {
            connection.pending.lock().await.remove(&command.command_id);
            return Err(BrokerError::QueueClosed);
        }

        match timeout(wait, result_rx).await {
            Ok(Ok(result)) => Ok(result),
            Ok(Err(_)) => Err(BrokerError::QueueClosed),
            Err(_) => {
                connection.pending.lock().await.remove(&command.command_id);
                Err(BrokerError::Timeout)
            }
        }
    }

    async fn connection(&self, provider_id: &str) -> Result<Arc<ProviderConnection>, BrokerError> {
        self.inner
            .providers
            .read()
            .await
            .get(provider_id)
            .cloned()
            .ok_or(BrokerError::UnknownProvider)
    }

    async fn authenticate(
        &self,
        provider_id: &str,
        session_key: &str,
    ) -> Result<Arc<ProviderConnection>, BrokerError> {
        let connection = self.connection(provider_id).await?;
        if connection.session_key != session_key {
            return Err(BrokerError::SessionMismatch);
        }
        Ok(connection)
    }

    async fn next_command(
        &self,
        provider_id: &str,
        session_key: &str,
    ) -> Result<Option<BrowserCommand>, BrokerError> {
        let connection = self.authenticate(provider_id, session_key).await?;
        let mut receiver = connection.commands_rx.lock().await;
        match timeout(DEFAULT_LONG_POLL, receiver.recv()).await {
            Ok(command) => Ok(command),
            Err(_) => Ok(None),
        }
    }

    async fn submit_result(
        &self,
        provider_id: &str,
        session_key: &str,
        result: BrowserCommandResult,
    ) -> Result<(), BrokerError> {
        let connection = self.authenticate(provider_id, session_key).await?;
        if let Some(sender) = connection.pending.lock().await.remove(&result.command_id) {
            let _ = sender.send(result);
        }
        Ok(())
    }

    async fn submit_learn(
        &self,
        provider_id: &str,
        session_key: &str,
        request: LearnRequest,
    ) -> Result<bool, BrokerError> {
        self.authenticate(provider_id, session_key).await?;
        let Some(event) = sanitize_learn_event(request.event) else {
            return Ok(false);
        };

        let envelope = LearnEnvelope {
            provider_id: provider_id.to_string(),
            tab_id: request.tab_id,
            url: safe_url(&request.url).unwrap_or_default(),
            title: normalize_text(&request.title, 200),
            event,
        };
        let _ = self.inner.learn_tx.send(envelope);
        Ok(true)
    }
}

pub fn router(state: BrokerState) -> Router {
    Router::new()
        .route("/v2/browser/register", post(register_handler))
        .route("/v2/browser/next", get(next_handler))
        .route("/v2/browser/result", post(result_handler))
        .route("/v2/browser/learn", post(learn_handler))
        .with_state(state)
}

pub async fn serve(listener: TcpListener, state: BrokerState) -> io::Result<()> {
    axum::serve(listener, router(state)).await
}

async fn register_handler(
    State(state): State<BrokerState>,
    headers: HeaderMap,
    Json(registration): Json<ProviderRegistration>,
) -> (StatusCode, Json<Value>) {
    let Ok((provider_id, session_key)) = credentials(&headers) else {
        return error_response(StatusCode::UNAUTHORIZED, "missing broker credentials");
    };

    if registration.provider_id != provider_id || registration.session_key != session_key {
        return error_response(StatusCode::UNAUTHORIZED, "registration identity mismatch");
    }

    match state.register(registration).await {
        Ok(()) => (StatusCode::OK, Json(json!({"ok": true}))),
        Err(error) => broker_error_response(error),
    }
}

async fn next_handler(
    State(state): State<BrokerState>,
    headers: HeaderMap,
) -> (StatusCode, Json<Value>) {
    let Ok((provider_id, session_key)) = credentials(&headers) else {
        return error_response(StatusCode::UNAUTHORIZED, "missing broker credentials");
    };

    match state.next_command(&provider_id, &session_key).await {
        Ok(command) => (
            StatusCode::OK,
            Json(
                serde_json::to_value(NextResponse { command }).unwrap_or_else(|_| {
                    json!({
                        "command": null
                    })
                }),
            ),
        ),
        Err(error) => broker_error_response(error),
    }
}

async fn result_handler(
    State(state): State<BrokerState>,
    headers: HeaderMap,
    Json(result): Json<BrowserCommandResult>,
) -> (StatusCode, Json<Value>) {
    let Ok((provider_id, session_key)) = credentials(&headers) else {
        return error_response(StatusCode::UNAUTHORIZED, "missing broker credentials");
    };

    match state
        .submit_result(&provider_id, &session_key, result)
        .await
    {
        Ok(()) => (StatusCode::OK, Json(json!({"ok": true}))),
        Err(error) => broker_error_response(error),
    }
}

async fn learn_handler(
    State(state): State<BrokerState>,
    headers: HeaderMap,
    Json(request): Json<LearnRequest>,
) -> (StatusCode, Json<Value>) {
    let Ok((provider_id, session_key)) = credentials(&headers) else {
        return error_response(StatusCode::UNAUTHORIZED, "missing broker credentials");
    };

    match state
        .submit_learn(&provider_id, &session_key, request)
        .await
    {
        Ok(recorded) => (
            StatusCode::OK,
            Json(json!({"ok": true, "recorded": recorded})),
        ),
        Err(error) => broker_error_response(error),
    }
}

fn credentials(headers: &HeaderMap) -> Result<(String, String), ()> {
    let provider_id = headers
        .get(PROVIDER_HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .ok_or(())?;
    let session_key = headers
        .get(SESSION_HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .ok_or(())?;
    Ok((provider_id.to_string(), session_key.to_string()))
}

fn broker_error_response(error: BrokerError) -> (StatusCode, Json<Value>) {
    let status = match error {
        BrokerError::UnknownProvider | BrokerError::SessionMismatch => StatusCode::UNAUTHORIZED,
        BrokerError::UnsupportedProtocol(_) => StatusCode::BAD_REQUEST,
        BrokerError::QueueClosed => StatusCode::SERVICE_UNAVAILABLE,
        BrokerError::Timeout => StatusCode::GATEWAY_TIMEOUT,
    };
    error_response(status, &error.to_string())
}

fn error_response(status: StatusCode, message: &str) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"ok": false, "error": message})))
}

fn normalize_text(value: &str, max: usize) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(max)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use adp_browser_bridge::{BrowserAction, LearnEvent};
    use serde_json::json;

    fn registration(key: &str) -> ProviderRegistration {
        ProviderRegistration {
            protocol_version: PROTOCOL_VERSION,
            provider_id: "browser-1".to_string(),
            session_key: key.to_string(),
            label: "test browser".to_string(),
            extension_version: "0.1.0".to_string(),
            capabilities: vec!["semantic_snapshot".to_string()],
        }
    }

    #[tokio::test]
    async fn registration_rejects_session_key_rotation() {
        let state = BrokerState::new();
        state.register(registration("key-a")).await.unwrap();
        let error = state.register(registration("key-b")).await.unwrap_err();
        assert!(matches!(error, BrokerError::SessionMismatch));
    }

    #[tokio::test]
    async fn command_round_trip_resolves_waiting_caller() {
        let state = BrokerState::new();
        state.register(registration("key-a")).await.unwrap();

        let caller = {
            let state = state.clone();
            tokio::spawn(async move {
                state
                    .call(
                        "browser-1",
                        Some(42),
                        BrowserAction::Inspect,
                        Duration::from_secs(2),
                    )
                    .await
            })
        };

        let command = state
            .next_command("browser-1", "key-a")
            .await
            .unwrap()
            .expect("queued command");
        assert_eq!(command.tab_id, Some(42));

        state
            .submit_result(
                "browser-1",
                "key-a",
                BrowserCommandResult {
                    command_id: command.command_id,
                    ok: true,
                    result: json!({"url": "https://example.com/"}),
                    error: None,
                },
            )
            .await
            .unwrap();

        let result = caller.await.unwrap().unwrap();
        assert!(result.ok);
        assert_eq!(result.result["url"], "https://example.com/");
    }

    #[tokio::test]
    async fn learn_events_are_sanitized_before_broadcast() {
        let state = BrokerState::new();
        state.register(registration("key-a")).await.unwrap();
        let mut events = state.subscribe_learn();

        let recorded = state
            .submit_learn(
                "browser-1",
                "key-a",
                LearnRequest {
                    tab_id: Some(7),
                    url: "https://example.com/search?q=private#frag".to_string(),
                    title: "  Results   page ".to_string(),
                    event: LearnEvent {
                        action: "fill".to_string(),
                        selector_hint: "input[name=keyword]".to_string(),
                        target: None,
                        value: Some("private keyword".to_string()),
                        evidence: json!({
                            "url": "https://example.com/search?q=private",
                            "cookie": "secret"
                        }),
                    },
                },
            )
            .await
            .unwrap();

        assert!(recorded);
        let event = events.recv().await.unwrap();
        assert_eq!(event.url, "https://example.com/search");
        assert_eq!(event.title, "Results page");
        assert_eq!(event.event.value.as_deref(), Some("[TEACH_KEYWORD]"));
        assert!(!event.event.evidence.to_string().contains("secret"));
        assert!(!event.event.evidence.to_string().contains("private"));
    }
}
