
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    Shell,
    FileSystem,
    Patch,
    Mcp,
    Browser,
    ComputerUse,
    WebSearch,
    ProjectMemory,
    Teach,
    UiAudit,
    SeoAudit,
    Research,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderKind {
    OpenAi,
    OllamaLocal,
    OllamaCloud,
    Anthropic,
    Gemini,
    OpenAiCompatible,
    Custom(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InvocationDialect {
    NativeFunction,
    StructuredJson,
    Unsupported,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ProviderCapabilities {
    pub native_tool_calls: bool,
    pub structured_output: bool,
    pub vision: bool,
    pub streaming: bool,
}

impl ProviderCapabilities {
    pub fn invocation_dialect(&self) -> InvocationDialect {
        if self.native_tool_calls {
            InvocationDialect::NativeFunction
        } else if self.structured_output {
            InvocationDialect::StructuredJson
        } else {
            InvocationDialect::Unsupported
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolDescriptor {
    pub name: String,
    pub description: String,
    pub capability: Capability,
    pub input_schema: Value,
    #[serde(default)]
    pub deterministic_safe: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub arguments: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolResult {
    pub call_id: String,
    pub ok: bool,
    #[serde(default)]
    pub output: Value,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ModelEvent {
    Text { text: String },
    ReasoningSummary { text: String },
    ToolCall { call: ToolCall },
    Usage { input_tokens: u64, output_tokens: u64 },
    Done,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionTier {
    Deterministic,
    LocalModel,
    PrimaryModel,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionBudget {
    pub local_token_budget: u64,
    pub primary_token_budget: u64,
    pub max_primary_escalations: u32,
    pub screenshots_on_divergence_only: bool,
    pub compact_state_deltas: bool,
}

impl Default for ExecutionBudget {
    fn default() -> Self {
        Self {
            local_token_budget: 8_000,
            primary_token_budget: 24_000,
            max_primary_escalations: 2,
            screenshots_on_divergence_only: true,
            compact_state_deltas: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ExecutionContext {
    pub project_root: Option<String>,
    pub workflow: Option<String>,
    pub workflow_version: Option<u32>,
    pub current_state: Option<String>,
    #[serde(default)]
    pub variables: BTreeMap<String, Value>,
}
