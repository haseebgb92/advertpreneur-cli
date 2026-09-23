use adp_protocol::{ModelEvent, ProviderCapabilities, ProviderKind, ToolCall, ToolDescriptor};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::env;
use std::io::Write;
use std::process::{Command, Stdio};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OllamaTransport {
    Local,
    Cloud,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OllamaConfig {
    pub transport: OllamaTransport,
    pub model: String,
    #[serde(default)]
    pub base_url: Option<String>,
    #[serde(default)]
    pub api_key_env: Option<String>,
}

impl OllamaConfig {
    pub fn local(model: impl Into<String>) -> Self {
        Self {
            transport: OllamaTransport::Local,
            model: model.into(),
            base_url: None,
            api_key_env: None,
        }
    }

    pub fn cloud(model: impl Into<String>) -> Self {
        Self {
            transport: OllamaTransport::Cloud,
            model: model.into(),
            base_url: None,
            api_key_env: Some("OLLAMA_API_KEY".to_string()),
        }
    }

    pub fn effective_base_url(&self) -> &str {
        self.base_url.as_deref().unwrap_or(match self.transport {
            OllamaTransport::Local => "http://127.0.0.1:11434",
            OllamaTransport::Cloud => "https://ollama.com",
        })
    }

    pub fn api_key(&self) -> Result<Option<String>, ModelError> {
        if self.transport == OllamaTransport::Local {
            return Ok(None);
        }

        let name = self.api_key_env.as_deref().unwrap_or("OLLAMA_API_KEY");
        let value = env::var(name).map_err(|_| ModelError::MissingCredential(name.to_string()))?;
        if value.trim().is_empty() {
            return Err(ModelError::MissingCredential(name.to_string()));
        }
        Ok(Some(value))
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatFunctionCall {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index: Option<u32>,
    pub name: String,
    #[serde(default)]
    pub arguments: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatToolCall {
    #[serde(rename = "type", default = "function_type")]
    pub kind: String,
    pub function: ChatFunctionCall,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub content: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tool_calls: Vec<ChatToolCall>,
}

impl ChatMessage {
    pub fn system(content: impl Into<String>) -> Self {
        Self::plain("system", content)
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self::plain("user", content)
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self::plain("assistant", content)
    }

    pub fn tool(tool_name: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            role: "tool".to_string(),
            content: content.into(),
            tool_name: Some(tool_name.into()),
            tool_calls: Vec::new(),
        }
    }

    fn plain(role: &str, content: impl Into<String>) -> Self {
        Self {
            role: role.to_string(),
            content: content.into(),
            tool_name: None,
            tool_calls: Vec::new(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ModelResponse {
    pub events: Vec<ModelEvent>,
    pub assistant_message: ChatMessage,
    pub assistant_text: String,
    pub tool_calls: Vec<ToolCall>,
}

#[derive(Debug, Error)]
pub enum ModelError {
    #[error("missing required credential environment variable {0}")]
    MissingCredential(String),
    #[error("model provider request failed: {0}")]
    Http(#[from] reqwest::Error),
    #[error("model provider returned HTTP {status}: {body}")]
    ProviderHttp { status: u16, body: String },
    #[error("model provider returned invalid payload: {0}")]
    InvalidPayload(String),
    #[error("provider command {program:?} failed to start: {source}")]
    CommandStart {
        program: String,
        #[source]
        source: std::io::Error,
    },
    #[error("provider command {program:?} failed with status {status}: {stderr}")]
    CommandFailed {
        program: String,
        status: String,
        stderr: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AntigravityModel {
    pub slug: String,
    pub label: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AntigravityUsage {
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub output_tokens: u64,
    #[serde(default)]
    pub thinking_tokens: u64,
    #[serde(default)]
    pub cache_read_tokens: u64,
    #[serde(default)]
    pub total_tokens: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AntigravityResponse {
    #[serde(default)]
    pub conversation_id: String,
    pub status: String,
    #[serde(default)]
    pub response: String,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub structured_output: Option<Value>,
    #[serde(default)]
    pub usage: AntigravityUsage,
}

#[derive(Debug, Clone)]
pub struct AntigravityClient {
    program: String,
}

impl Default for AntigravityClient {
    fn default() -> Self {
        Self::new()
    }
}

impl AntigravityClient {
    pub fn new() -> Self {
        Self {
            program: "agy".to_string(),
        }
    }

    pub fn with_program(program: impl Into<String>) -> Self {
        Self {
            program: program.into(),
        }
    }

    pub fn list_models(&self) -> Result<Vec<AntigravityModel>, ModelError> {
        let output = Command::new(&self.program)
            .arg("models")
            .output()
            .map_err(|source| ModelError::CommandStart {
                program: self.program.clone(),
                source,
            })?;
        if !output.status.success() {
            return Err(ModelError::CommandFailed {
                program: self.program.clone(),
                status: output.status.to_string(),
                stderr: truncate(&String::from_utf8_lossy(&output.stderr), 2_000),
            });
        }

        let stdout = String::from_utf8_lossy(&output.stdout);
        if let Ok(value) = serde_json::from_str::<Value>(&stdout) {
            let mut models = Vec::new();
            collect_antigravity_models_json(&value, &mut models);
            models.sort_by(|a, b| a.slug.cmp(&b.slug));
            models.dedup_by(|a, b| a.slug == b.slug);
            if !models.is_empty() {
                return Ok(models);
            }
        }

        Ok(parse_antigravity_models(&stdout))
    }

    pub fn chat_structured(
        &self,
        model: &str,
        prompt: &str,
        schema: &Value,
        agent: &str,
    ) -> Result<AntigravityResponse, ModelError> {
        let schema = serde_json::to_string(schema)
            .map_err(|err| ModelError::InvalidPayload(err.to_string()))?;
        let mut child = Command::new(&self.program)
            .args([
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--agent",
                agent,
                "--json-schema",
                &schema,
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|source| ModelError::CommandStart {
                program: self.program.clone(),
                source,
            })?;

        let message = json!({
            "event": "user",
            "message": {
                "content": prompt
            }
        });
        if let Some(mut stdin) = child.stdin.take() {
            writeln!(stdin, "{message}").map_err(|source| ModelError::CommandStart {
                program: self.program.clone(),
                source,
            })?;
        }

        let output = child
            .wait_with_output()
            .map_err(|source| ModelError::CommandStart {
                program: self.program.clone(),
                source,
            })?;
        if !output.status.success() {
            return Err(ModelError::CommandFailed {
                program: self.program.clone(),
                status: output.status.to_string(),
                stderr: truncate(&String::from_utf8_lossy(&output.stderr), 2_000),
            });
        }

        for line in String::from_utf8_lossy(&output.stdout).lines().rev() {
            let Ok(event) = serde_json::from_str::<Value>(line) else {
                continue;
            };
            if event.get("event").and_then(Value::as_str) != Some("result") {
                continue;
            }
            let Some(result) = event.get("result") else {
                continue;
            };
            return serde_json::from_value::<AntigravityResponse>(result.clone())
                .map_err(|err| ModelError::InvalidPayload(err.to_string()));
        }

        Err(ModelError::InvalidPayload(
            "Antigravity stream did not contain a result event".to_string(),
        ))
    }

    pub fn chat_text(&self, model: &str, prompt: &str) -> Result<AntigravityResponse, ModelError> {
        let output = Command::new(&self.program)
            .args(["-p", prompt, "--model", model, "--output-format", "json"])
            .output()
            .map_err(|source| ModelError::CommandStart {
                program: self.program.clone(),
                source,
            })?;
        if !output.status.success() {
            return Err(ModelError::CommandFailed {
                program: self.program.clone(),
                status: output.status.to_string(),
                stderr: truncate(&String::from_utf8_lossy(&output.stderr), 2_000),
            });
        }

        serde_json::from_slice::<AntigravityResponse>(&output.stdout)
            .map_err(|err| ModelError::InvalidPayload(err.to_string()))
    }
}

fn collect_antigravity_models_json(value: &Value, models: &mut Vec<AntigravityModel>) {
    match value {
        Value::Array(values) => {
            for value in values {
                collect_antigravity_models_json(value, models);
            }
        }
        Value::Object(map) => {
            let slug = ["slug", "id", "model"]
                .iter()
                .find_map(|key| map.get(*key).and_then(Value::as_str));
            let label = ["label", "display_name", "displayName", "name"]
                .iter()
                .find_map(|key| map.get(*key).and_then(Value::as_str));
            if let Some(slug) = slug {
                let label = label.unwrap_or(slug);
                models.push(AntigravityModel {
                    slug: slug.to_string(),
                    label: label.to_string(),
                });
            }
            for child in map.values() {
                if child.is_array() || child.is_object() {
                    collect_antigravity_models_json(child, models);
                }
            }
        }
        _ => {}
    }
}

pub fn parse_antigravity_models(output: &str) -> Vec<AntigravityModel> {
    output
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if line.is_empty() || line.ends_with(':') {
                return None;
            }
            let mut parts = line.split_whitespace();
            let slug = parts.next()?;
            let label = parts.collect::<Vec<_>>().join(" ");
            if label.is_empty()
                || !slug
                    .chars()
                    .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.' | ':'))
            {
                return None;
            }
            Some(AntigravityModel {
                slug: slug.to_string(),
                label,
            })
        })
        .collect()
}

#[derive(Debug, Clone)]
pub struct OllamaClient {
    config: OllamaConfig,
    http: Client,
}

impl OllamaClient {
    pub fn new(config: OllamaConfig) -> Result<Self, ModelError> {
        Ok(Self {
            config,
            http: Client::builder().build()?,
        })
    }

    pub fn provider_kind(&self) -> ProviderKind {
        match self.config.transport {
            OllamaTransport::Local => ProviderKind::OllamaLocal,
            OllamaTransport::Cloud => ProviderKind::OllamaCloud,
        }
    }

    pub fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            native_tool_calls: true,
            structured_output: true,
            vision: false,
            streaming: true,
        }
    }

    pub fn model(&self) -> &str {
        &self.config.model
    }

    pub async fn chat(
        &self,
        messages: &[ChatMessage],
        tools: &[ToolDescriptor],
    ) -> Result<ModelResponse, ModelError> {
        let request = OllamaChatRequest {
            model: self.config.model.clone(),
            messages: messages.to_vec(),
            tools: tools.iter().map(tool_to_ollama).collect(),
            stream: false,
        };

        let endpoint = format!(
            "{}/api/chat",
            self.config.effective_base_url().trim_end_matches('/')
        );
        let mut builder = self.http.post(endpoint).json(&request);
        if let Some(api_key) = self.config.api_key()? {
            builder = builder.bearer_auth(api_key);
        }

        let response = builder.send().await?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(ModelError::ProviderHttp {
                status: status.as_u16(),
                body: truncate(&body, 2_000),
            });
        }

        let payload: OllamaChatResponse = response.json().await?;
        Ok(normalize_response(payload))
    }

    pub async fn list_models(&self) -> Result<Vec<String>, ModelError> {
        let endpoint = format!(
            "{}/api/tags",
            self.config.effective_base_url().trim_end_matches('/')
        );
        let mut builder = self.http.get(endpoint);
        if let Some(api_key) = self.config.api_key()? {
            builder = builder.bearer_auth(api_key);
        }

        let response = builder.send().await?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(ModelError::ProviderHttp {
                status: status.as_u16(),
                body: truncate(&body, 2_000),
            });
        }

        let payload: OllamaTagsResponse = response.json().await?;
        Ok(payload.models.into_iter().map(|model| model.name).collect())
    }
}

#[derive(Debug, Serialize)]
struct OllamaChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tools: Vec<Value>,
    stream: bool,
}

#[derive(Debug, Deserialize)]
struct OllamaChatResponse {
    message: ChatMessage,
    #[serde(default)]
    prompt_eval_count: Option<u64>,
    #[serde(default)]
    eval_count: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct OllamaTagsResponse {
    #[serde(default)]
    models: Vec<OllamaModel>,
}

#[derive(Debug, Deserialize)]
struct OllamaModel {
    name: String,
}

fn tool_to_ollama(tool: &ToolDescriptor) -> Value {
    json!({
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema
        }
    })
}

fn normalize_response(payload: OllamaChatResponse) -> ModelResponse {
    let assistant_message = payload.message;
    let assistant_text = assistant_message.content.clone();

    let mut events = Vec::new();
    if !assistant_text.is_empty() {
        events.push(ModelEvent::Text {
            text: assistant_text.clone(),
        });
    }

    let mut tool_calls = Vec::new();
    for raw in &assistant_message.tool_calls {
        let call = ToolCall {
            id: format!("ollama-{}", Uuid::new_v4()),
            name: raw.function.name.clone(),
            arguments: raw.function.arguments.clone(),
        };
        events.push(ModelEvent::ToolCall { call: call.clone() });
        tool_calls.push(call);
    }

    if payload.prompt_eval_count.is_some() || payload.eval_count.is_some() {
        events.push(ModelEvent::Usage {
            input_tokens: payload.prompt_eval_count.unwrap_or(0),
            output_tokens: payload.eval_count.unwrap_or(0),
        });
    }
    events.push(ModelEvent::Done);

    ModelResponse {
        events,
        assistant_message,
        assistant_text,
        tool_calls,
    }
}

fn function_type() -> String {
    "function".to_string()
}

fn truncate(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use adp_protocol::Capability;

    #[test]
    fn local_and_cloud_use_same_tool_call_capability() {
        let local = OllamaClient::new(OllamaConfig::local("qwen3:1.7b")).unwrap();
        let cloud = OllamaClient::new(OllamaConfig::cloud("gemma4:31b")).unwrap();
        assert_eq!(
            local.capabilities().native_tool_calls,
            cloud.capabilities().native_tool_calls
        );
        assert!(local.capabilities().native_tool_calls);
    }

    #[test]
    fn tool_schema_is_provider_neutral_until_serialization() {
        let tool = ToolDescriptor {
            name: "adp_browser_inspect".to_string(),
            description: "Inspect the current browser state".to_string(),
            capability: Capability::Browser,
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            deterministic_safe: true,
        };
        let serialized = tool_to_ollama(&tool);
        assert_eq!(serialized["function"]["name"], "adp_browser_inspect");
        assert_eq!(serialized["function"]["parameters"]["type"], "object");
    }

    #[test]
    fn tool_result_message_matches_ollama_agent_loop_wire_shape() {
        let message = ChatMessage::tool("adp_browser_inspect", r#"{"ok":true}"#);
        let json = serde_json::to_value(message).unwrap();
        assert_eq!(json["role"], "tool");
        assert_eq!(json["tool_name"], "adp_browser_inspect");
        assert_eq!(json["content"], r#"{"ok":true}"#);
    }

    #[test]
    fn assistant_tool_calls_round_trip_without_provider_specific_agent_state() {
        let message = ChatMessage {
            role: "assistant".to_string(),
            content: String::new(),
            tool_name: None,
            tool_calls: vec![ChatToolCall {
                kind: "function".to_string(),
                function: ChatFunctionCall {
                    index: Some(0),
                    name: "adp_browser_inspect".to_string(),
                    arguments: json!({}),
                },
            }],
        };
        let wire = serde_json::to_string(&message).unwrap();
        let parsed: ChatMessage = serde_json::from_str(&wire).unwrap();
        assert_eq!(parsed, message);
    }

    #[test]
    fn antigravity_json_model_listing_parser_is_shape_tolerant() {
        let value = json!({
            "models": [
                {"slug":"gemini-3.8-flash-high","label":"Gemini 3.8 Flash (High)"},
                {"id":"claude-sonnet-4-6","display_name":"Claude Sonnet 4.6"}
            ]
        });
        let mut models = Vec::new();
        collect_antigravity_models_json(&value, &mut models);
        assert!(
            models
                .iter()
                .any(|model| model.slug == "gemini-3.8-flash-high")
        );
        assert!(models.iter().any(|model| model.slug == "claude-sonnet-4-6"));
    }

    #[test]
    fn antigravity_model_listing_parser_keeps_slug_and_label() {
        let models = parse_antigravity_models(
            "gemini-3.8-flash-high     Gemini 3.8 Flash (High)\n\
             claude-sonnet-4-6         Claude Sonnet 4.6 (Thinking)\n",
        );
        assert_eq!(
            models,
            vec![
                AntigravityModel {
                    slug: "gemini-3.8-flash-high".to_string(),
                    label: "Gemini 3.8 Flash (High)".to_string(),
                },
                AntigravityModel {
                    slug: "claude-sonnet-4-6".to_string(),
                    label: "Claude Sonnet 4.6 (Thinking)".to_string(),
                },
            ]
        );
    }

    #[test]
    fn antigravity_model_listing_parser_ignores_headings_and_blank_lines() {
        let models = parse_antigravity_models("Available models:\n\nslug-one Model One\n");
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].slug, "slug-one");
    }

    #[test]
    fn credentials_are_not_part_of_serializable_config_by_default() {
        let config = OllamaConfig::cloud("gemma4:31b");
        let json = serde_json::to_string(&config).unwrap();
        assert!(json.contains("OLLAMA_API_KEY"));
        assert!(!json.contains("Bearer"));
    }
}
