use adp_protocol::{ModelEvent, ProviderCapabilities, ProviderKind, ToolCall, ToolDescriptor};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::env;
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

impl ChatMessage {
    pub fn system(content: impl Into<String>) -> Self {
        Self {
            role: "system".to_string(),
            content: content.into(),
        }
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self {
            role: "user".to_string(),
            content: content.into(),
        }
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self {
            role: "assistant".to_string(),
            content: content.into(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ModelResponse {
    pub events: Vec<ModelEvent>,
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
    message: OllamaMessage,
    #[serde(default)]
    prompt_eval_count: Option<u64>,
    #[serde(default)]
    eval_count: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct OllamaMessage {
    #[serde(default)]
    content: String,
    #[serde(default)]
    tool_calls: Vec<OllamaToolCall>,
}

#[derive(Debug, Deserialize)]
struct OllamaToolCall {
    function: OllamaFunctionCall,
}

#[derive(Debug, Deserialize)]
struct OllamaFunctionCall {
    name: String,
    #[serde(default)]
    arguments: Value,
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
    let mut events = Vec::new();
    if !payload.message.content.is_empty() {
        events.push(ModelEvent::Text {
            text: payload.message.content.clone(),
        });
    }

    let mut tool_calls = Vec::new();
    for raw in payload.message.tool_calls {
        let call = ToolCall {
            id: format!("ollama-{}", Uuid::new_v4()),
            name: raw.function.name,
            arguments: raw.function.arguments,
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
        assistant_text: payload.message.content,
        tool_calls,
    }
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
            name: "adp.browser.inspect".to_string(),
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
        assert_eq!(serialized["function"]["name"], "adp.browser.inspect");
        assert_eq!(
            serialized["function"]["parameters"]["type"],
            "object"
        );
    }

    #[test]
    fn credentials_are_not_part_of_serializable_config_by_default() {
        let config = OllamaConfig::cloud("gemma4:31b");
        let json = serde_json::to_string(&config).unwrap();
        assert!(json.contains("OLLAMA_API_KEY"));
        assert!(!json.contains("Bearer"));
    }
}
