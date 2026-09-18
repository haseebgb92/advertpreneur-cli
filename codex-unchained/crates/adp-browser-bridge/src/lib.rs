use adp_memory::{SemanticTarget, TEACH_KEYWORD, safe_url, sanitize_fill};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const PROTOCOL_VERSION: u32 = 2;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderRegistration {
    pub protocol_version: u32,
    pub provider_id: String,
    pub session_key: String,
    pub label: String,
    pub extension_version: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SemanticElement {
    pub role: Option<String>,
    pub name: Option<String>,
    pub test_id: Option<String>,
    pub aria_label: Option<String>,
    pub css: Option<String>,
    pub disabled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BrowserSnapshot {
    pub tab_id: i64,
    pub url: String,
    pub title: String,
    #[serde(default)]
    pub elements: Vec<SemanticElement>,
    pub page_signature: Option<String>,
}

impl BrowserSnapshot {
    pub fn privacy_safe(mut self) -> Self {
        self.url = safe_url(&self.url).unwrap_or_default();
        self.title = self.title.split_whitespace().collect::<Vec<_>>().join(" ");
        self
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum BrowserAction {
    BindActive,
    Navigate {
        url: String,
    },
    Inspect,
    Click {
        target: SemanticTarget,
    },
    Fill {
        target: SemanticTarget,
        value: String,
    },
    Scroll {
        amount: i64,
    },
    Screenshot,
    WaitForDownload {
        after_id: i64,
        timeout_ms: u64,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrowserCommand {
    pub command_id: String,
    pub tab_id: Option<i64>,
    pub action: BrowserAction,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrowserCommandResult {
    pub command_id: String,
    pub ok: bool,
    #[serde(default)]
    pub result: Value,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LearnEvent {
    pub action: String,
    #[serde(default)]
    pub selector_hint: String,
    #[serde(default)]
    pub target: Option<SemanticTarget>,
    #[serde(default)]
    pub value: Option<String>,
    #[serde(default)]
    pub evidence: Value,
}

pub fn sanitize_learn_event(mut event: LearnEvent) -> Option<LearnEvent> {
    event.evidence = sanitize_evidence(event.evidence);

    if event.action == "fill" {
        let value = event.value.take().unwrap_or_default();
        event.value = sanitize_fill(&event.selector_hint, &value);
        event.value.as_ref()?;
        debug_assert_eq!(event.value.as_deref(), Some(TEACH_KEYWORD));
    }
    Some(event)
}

fn sanitize_evidence(value: Value) -> Value {
    let Value::Object(mut object) = value else {
        return Value::Object(Default::default());
    };
    for key in ["url", "before_url"] {
        if let Some(Value::String(raw)) = object.get_mut(key) {
            *raw = safe_url(raw).unwrap_or_default();
        }
    }
    for key in [
        "value",
        "password",
        "token",
        "cookie",
        "authorization",
        "screenshot",
        "html",
        "dom",
    ] {
        object.remove(key);
    }
    Value::Object(object)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn teach_keyword_is_parameterized_and_query_is_removed() {
        let event = LearnEvent {
            action: "fill".into(),
            selector_hint: "input[name=keyword]".into(),
            target: None,
            value: Some("bee wax wrap".into()),
            evidence: json!({
                "url": "https://example.com/search?q=private",
                "cookie": "secret"
            }),
        };
        let safe = sanitize_learn_event(event).unwrap();
        assert_eq!(safe.value.as_deref(), Some(TEACH_KEYWORD));
        let text = safe.evidence.to_string();
        assert!(!text.contains("private"));
        assert!(!text.contains("secret"));
    }

    #[test]
    fn sensitive_or_arbitrary_fill_is_dropped() {
        let sensitive = LearnEvent {
            action: "fill".into(),
            selector_hint: "#password".into(),
            target: None,
            value: Some("secret".into()),
            evidence: Value::Null,
        };
        assert!(sanitize_learn_event(sensitive).is_none());

        let arbitrary = LearnEvent {
            action: "fill".into(),
            selector_hint: "#address".into(),
            target: None,
            value: Some("home".into()),
            evidence: Value::Null,
        };
        assert!(sanitize_learn_event(arbitrary).is_none());
    }
}
