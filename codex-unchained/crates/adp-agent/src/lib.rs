use adp_browser_bridge::{BrowserAction, SemanticTarget};
use adp_browser_broker::{BrokerError, BrokerState};
use adp_model::{ChatMessage, ModelError, OllamaClient};
use adp_protocol::{Capability, ModelEvent, ToolCall, ToolDescriptor};
use serde_json::{Value, json};
use std::time::Duration;
use thiserror::Error;

const TOOL_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_TOOL_RESULT_CHARS: usize = 40_000;

pub const BROWSER_BIND_ACTIVE: &str = "adp_browser_bind_active";
pub const BROWSER_INSPECT: &str = "adp_browser_inspect";
pub const BROWSER_CLICK: &str = "adp_browser_click";
pub const BROWSER_FILL: &str = "adp_browser_fill";
pub const BROWSER_NAVIGATE: &str = "adp_browser_navigate";
pub const BROWSER_SCROLL: &str = "adp_browser_scroll";
pub const BROWSER_WAIT_DOWNLOAD: &str = "adp_browser_wait_download";

#[derive(Debug, Clone)]
pub struct AgentConfig {
    pub max_model_turns: usize,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            max_model_turns: 12,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AgentResult {
    pub final_text: String,
    pub model_turns: usize,
    pub tool_calls: usize,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub executed_tools: Vec<ToolExecutionRecord>,
}

#[derive(Debug, Clone)]
pub struct ToolExecutionRecord {
    pub call: ToolCall,
    pub ok: bool,
    pub error: Option<String>,
}

#[derive(Debug, Error)]
pub enum AgentError {
    #[error(transparent)]
    Model(#[from] ModelError),
    #[error("browser runtime error: {0}")]
    Browser(#[from] BrokerError),
    #[error("browser tool call '{tool}' has invalid arguments: {message}")]
    InvalidArguments { tool: String, message: String },
    #[error("browser tool '{0}' is not registered")]
    UnknownTool(String),
    #[error("browser command failed: {0}")]
    BrowserCommand(String),
    #[error("agent reached the model-turn limit before producing a final response")]
    TurnLimit,
}

pub struct AgentRuntime<'a> {
    model: &'a OllamaClient,
    broker: &'a BrokerState,
    browser_provider_id: Option<String>,
    browser_tab_id: Option<i64>,
    allowed_tool_names: Option<Vec<String>>,
    config: AgentConfig,
}

impl<'a> AgentRuntime<'a> {
    pub fn new(model: &'a OllamaClient, broker: &'a BrokerState) -> Self {
        Self {
            model,
            broker,
            browser_provider_id: None,
            browser_tab_id: None,
            allowed_tool_names: None,
            config: AgentConfig::default(),
        }
    }

    pub fn with_browser_provider(mut self, provider_id: impl Into<String>) -> Self {
        self.browser_provider_id = Some(provider_id.into());
        self
    }

    pub fn with_browser_tab(mut self, tab_id: i64) -> Self {
        self.browser_tab_id = Some(tab_id);
        self
    }

    pub fn with_config(mut self, config: AgentConfig) -> Self {
        self.config = config;
        self
    }

    pub fn with_allowed_tools(
        mut self,
        tool_names: impl IntoIterator<Item = impl Into<String>>,
    ) -> Self {
        self.allowed_tool_names = Some(tool_names.into_iter().map(Into::into).collect());
        self
    }

    pub fn tool_inventory(&self) -> Vec<ToolDescriptor> {
        if self.browser_provider_id.is_none() {
            return Vec::new();
        }

        let mut tools = browser_tools();
        if let Some(allowed) = &self.allowed_tool_names {
            tools.retain(|tool| allowed.iter().any(|name| name == &tool.name));
        }
        tools
    }

    pub async fn run(&self, prompt: &str) -> Result<AgentResult, AgentError> {
        let tools = self.tool_inventory();
        let mut messages = vec![
            ChatMessage::system(
                "You are running inside ADP. Use host tools when they are useful.                  Browser tools operate the user's already-open Chrome/Edge session.                  Inspect semantic browser state before acting. Never invent tool results.",
            ),
            ChatMessage::user(prompt),
        ];
        let mut total_tool_calls = 0usize;
        let mut input_tokens = 0u64;
        let mut output_tokens = 0u64;
        let mut executed_tools = Vec::new();

        for turn in 1..=self.config.max_model_turns {
            let response = self.model.chat(&messages, &tools).await?;
            for event in &response.events {
                if let ModelEvent::Usage {
                    input_tokens: input,
                    output_tokens: output,
                } = event
                {
                    input_tokens = input_tokens.saturating_add(*input);
                    output_tokens = output_tokens.saturating_add(*output);
                }
            }
            messages.push(response.assistant_message.clone());

            if response.tool_calls.is_empty() {
                return Ok(AgentResult {
                    final_text: response.assistant_text,
                    model_turns: turn,
                    tool_calls: total_tool_calls,
                    input_tokens,
                    output_tokens,
                    executed_tools,
                });
            }

            total_tool_calls += response.tool_calls.len();
            for call in response.tool_calls {
                let tool_name = call.name.clone();
                let trace_call = call.clone();
                let result = self.dispatch_tool(&call).await;
                let (wire, record) = match result {
                    Ok(value) => (
                        json!({"ok": true, "result": value}),
                        ToolExecutionRecord {
                            call: trace_call,
                            ok: true,
                            error: None,
                        },
                    ),
                    Err(error) => {
                        let message = error.to_string();
                        (
                            json!({"ok": false, "error": message}),
                            ToolExecutionRecord {
                                call: trace_call,
                                ok: false,
                                error: Some(error.to_string()),
                            },
                        )
                    }
                };
                executed_tools.push(record);
                messages.push(ChatMessage::tool(
                    tool_name,
                    truncate(&wire.to_string(), MAX_TOOL_RESULT_CHARS),
                ));
            }
        }

        Err(AgentError::TurnLimit)
    }

    async fn dispatch_tool(&self, call: &ToolCall) -> Result<Value, AgentError> {
        let provider_id = self
            .browser_provider_id
            .as_deref()
            .ok_or_else(|| AgentError::UnknownTool(call.name.clone()))?;

        let action = match call.name.as_str() {
            BROWSER_BIND_ACTIVE => BrowserAction::BindActive,
            BROWSER_INSPECT => BrowserAction::Inspect,
            BROWSER_CLICK => BrowserAction::Click {
                target: parse_target(call)?,
            },
            BROWSER_FILL => BrowserAction::Fill {
                target: parse_target(call)?,
                value: required_string(call, "value")?,
            },
            BROWSER_NAVIGATE => BrowserAction::Navigate {
                url: required_string(call, "url")?,
            },
            BROWSER_SCROLL => BrowserAction::Scroll {
                amount: required_i64(call, "amount")?,
            },
            BROWSER_WAIT_DOWNLOAD => BrowserAction::WaitForDownload {
                after_id: required_i64(call, "after_id")?,
                timeout_ms: call
                    .arguments
                    .get("timeout_ms")
                    .and_then(Value::as_u64)
                    .unwrap_or(45_000),
            },
            _ => return Err(AgentError::UnknownTool(call.name.clone())),
        };

        let result = self
            .broker
            .call(provider_id, self.browser_tab_id, action, TOOL_TIMEOUT)
            .await?;

        if result.ok {
            Ok(result.result)
        } else {
            Err(AgentError::BrowserCommand(
                result
                    .error
                    .unwrap_or_else(|| "browser action failed".to_string()),
            ))
        }
    }
}

pub fn browser_tools() -> Vec<ToolDescriptor> {
    vec![
        descriptor(
            BROWSER_BIND_ACTIVE,
            "Bind ADP to the user's currently active browser tab.",
            json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            true,
        ),
        descriptor(
            BROWSER_INSPECT,
            "Inspect the current browser tab as a compact semantic list of visible interactive controls. Does not return form values.",
            json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            true,
        ),
        descriptor(
            BROWSER_CLICK,
            "Click a semantic browser target. Prefer role/name, data-testid or aria-label from a recent inspect result.",
            json!({
                "type": "object",
                "required": ["target"],
                "properties": {
                    "target": target_schema()
                },
                "additionalProperties": false
            }),
            false,
        ),
        descriptor(
            BROWSER_FILL,
            "Fill a non-sensitive browser input. Password, token, OTP, payment and other sensitive controls are rejected by the browser runtime.",
            json!({
                "type": "object",
                "required": ["target", "value"],
                "properties": {
                    "target": target_schema(),
                    "value": {"type": "string"}
                },
                "additionalProperties": false
            }),
            false,
        ),
        descriptor(
            BROWSER_NAVIGATE,
            "Navigate the bound browser tab to an explicit URL.",
            json!({
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string"}
                },
                "additionalProperties": false
            }),
            false,
        ),
        descriptor(
            BROWSER_SCROLL,
            "Scroll the bound page vertically by a signed pixel amount.",
            json!({
                "type": "object",
                "required": ["amount"],
                "properties": {
                    "amount": {"type": "integer"}
                },
                "additionalProperties": false
            }),
            true,
        ),
        descriptor(
            BROWSER_WAIT_DOWNLOAD,
            "Wait for a browser download newer than a known download id to complete.",
            json!({
                "type": "object",
                "required": ["after_id"],
                "properties": {
                    "after_id": {"type": "integer"},
                    "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 120000}
                },
                "additionalProperties": false
            }),
            true,
        ),
    ]
}

fn descriptor(
    name: &str,
    description: &str,
    input_schema: Value,
    deterministic_safe: bool,
) -> ToolDescriptor {
    ToolDescriptor {
        name: name.to_string(),
        description: description.to_string(),
        capability: Capability::Browser,
        input_schema,
        deterministic_safe,
    }
}

fn target_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "role": {"type": ["string", "null"]},
            "name": {"type": ["string", "null"]},
            "test_id": {"type": ["string", "null"]},
            "aria_label": {"type": ["string", "null"]},
            "css": {"type": ["string", "null"]},
            "near": {"type": ["string", "null"]}
        },
        "additionalProperties": false
    })
}

fn parse_target(call: &ToolCall) -> Result<SemanticTarget, AgentError> {
    let target = call
        .arguments
        .get("target")
        .cloned()
        .ok_or_else(|| invalid(call, "missing target"))?;
    serde_json::from_value(target).map_err(|error| invalid(call, &error.to_string()))
}

fn required_string(call: &ToolCall, name: &str) -> Result<String, AgentError> {
    call.arguments
        .get(name)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| invalid(call, &format!("'{name}' must be a string")))
}

fn required_i64(call: &ToolCall, name: &str) -> Result<i64, AgentError> {
    call.arguments
        .get(name)
        .and_then(Value::as_i64)
        .ok_or_else(|| invalid(call, &format!("'{name}' must be an integer")))
}

fn invalid(call: &ToolCall, message: &str) -> AgentError {
    AgentError::InvalidArguments {
        tool: call.name.clone(),
        message: message.to_string(),
    }
}

fn truncate(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_inventory_is_model_provider_independent() {
        let inventory = browser_tools();
        assert!(inventory.iter().any(|tool| tool.name == BROWSER_INSPECT));
        assert!(inventory.iter().any(|tool| tool.name == BROWSER_FILL));
        assert!(
            inventory
                .iter()
                .all(|tool| tool.capability == Capability::Browser)
        );
    }

    #[test]
    fn fill_target_arguments_parse_semantically() {
        let call = ToolCall {
            id: "1".to_string(),
            name: BROWSER_FILL.to_string(),
            arguments: json!({
                "target": {
                    "role": "input",
                    "name": "Search",
                    "test_id": null,
                    "aria_label": "Search",
                    "css": null,
                    "near": null
                },
                "value": "bee wax wrap"
            }),
        };

        let target = parse_target(&call).unwrap();
        assert_eq!(target.role.as_deref(), Some("input"));
        assert_eq!(required_string(&call, "value").unwrap(), "bee wax wrap");
    }
}
