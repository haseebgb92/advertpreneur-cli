use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdpMode {
    Auto,
    Economy,
    Balanced,
    Max,
}

impl AdpMode {
    pub fn from_virtual_model(model: &str) -> Option<Self> {
        match model {
            "adp/auto" => Some(Self::Auto),
            "adp/economy" => Some(Self::Economy),
            "adp/balanced" => Some(Self::Balanced),
            "adp/max" => Some(Self::Max),
            _ => None,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Auto => "Auto",
            Self::Economy => "Economy",
            Self::Balanced => "Balanced",
            Self::Max => "Max",
        }
    }
}

#[derive(Debug, Default, Clone, Copy)]
struct TaskSignals {
    text_len: usize,
    tool_count: usize,
    browser_or_web: bool,
    coding: bool,
    debugging: bool,
    vision: bool,
}

impl TaskSignals {
    fn from_request(request: &Value) -> Self {
        let mut text = String::new();
        if let Some(instructions) = request.get("instructions") {
            collect_strings(instructions, &mut text);
        }
        if let Some(input) = request.get("input") {
            collect_strings(input, &mut text);
        }

        let lower = text.to_ascii_lowercase();
        let tools = request
            .get("tools")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let tool_text = Value::Array(tools.clone()).to_string().to_ascii_lowercase();

        Self {
            text_len: text.len(),
            tool_count: tools.len(),
            browser_or_web: tool_text.contains("browser")
                || tool_text.contains("computer")
                || tool_text.contains("web_search")
                || tool_text.contains("web-search"),
            coding: contains_any(
                &lower,
                &[
                    "code",
                    "coding",
                    "rust",
                    "python",
                    "typescript",
                    "javascript",
                    "compile",
                    "build",
                    "repo",
                    "repository",
                    "refactor",
                    "patch",
                    "function",
                    "class",
                ],
            ),
            debugging: contains_any(
                &lower,
                &[
                    "debug",
                    "error",
                    "failing",
                    "failed",
                    "failure",
                    "stack trace",
                    "compiler",
                    "test failure",
                    "fix",
                ],
            ),
            vision: lower.contains("input_image")
                || lower.contains("screenshot")
                || lower.contains("image")
                || request.to_string().contains("input_image"),
        }
    }

    fn complex(self) -> bool {
        self.text_len > 12_000
            || self.tool_count > 12
            || (self.coding && self.debugging)
            || (self.browser_or_web && self.tool_count > 8)
    }
}

pub fn ordered_candidates(
    mode: AdpMode,
    request: &Value,
    candidates: impl IntoIterator<Item = String>,
) -> Vec<String> {
    let signals = TaskSignals::from_request(request);
    let mut ranked: Vec<(i32, String)> = candidates
        .into_iter()
        // Automatic modes intentionally stay on Ollama. Antigravity can still be
        // pinned explicitly through /model, but ADP never consumes that quota
        // silently.
        .filter(|id| id.starts_with("ollama-local/") || id.starts_with("ollama-cloud/"))
        .map(|id| (score_candidate(mode, signals, &id), id))
        .collect();

    ranked.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(&b.1)));
    ranked.into_iter().map(|(_, id)| id).collect()
}

fn score_candidate(mode: AdpMode, signals: TaskSignals, id: &str) -> i32 {
    let lower = id.to_ascii_lowercase();
    let is_local = lower.starts_with("ollama-local/");
    let is_cloud = lower.starts_with("ollama-cloud/");

    let mut score = match mode {
        AdpMode::Economy => {
            if is_local {
                85
            } else if is_cloud {
                70
            } else {
                0
            }
        }
        AdpMode::Auto => {
            if is_cloud {
                80
            } else if is_local {
                55
            } else {
                0
            }
        }
        AdpMode::Balanced => {
            if is_cloud {
                95
            } else if is_local {
                35
            } else {
                0
            }
        }
        AdpMode::Max => {
            if is_cloud {
                115
            } else if is_local {
                15
            } else {
                0
            }
        }
    };

    let cheap = contains_any(
        &lower,
        &[
            "flash", "mini", "lite", "20b", "8b", "7b", "4b", "3b", "1.7b",
        ],
    );
    let capable = contains_any(
        &lower,
        &[
            "glm", "deepseek", "qwen", "coder", "code", "gpt-oss", "minimax", "kimi",
        ],
    );
    let flagship = contains_any(
        &lower,
        &[
            "kimi-k3",
            "k3",
            "glm-5.3",
            "qwen3-max",
            "max",
            "pro",
            "671b",
            "480b",
            "235b",
            "120b",
        ],
    );
    let preferred_economy = contains_any(
        &lower,
        &[
            "glm-5.3-flash",
            "deepseek-v4.1-flash",
            "gpt-oss:120b",
            "gpt-oss-120b",
        ],
    );
    let preferred_balanced = contains_any(
        &lower,
        &[
            "glm-5.3-flash",
            "deepseek-v4.1-flash",
            "minimax-m3",
            "kimi-k2.7-code",
            "glm-5.3",
        ],
    );
    let preferred_max = contains_any(
        &lower,
        &[
            "kimi-k3",
            "glm-5.3",
            "kimi-k2.7-code",
            "qwen3-max",
            "qwen3-coder",
        ],
    );

    match mode {
        AdpMode::Economy => {
            if cheap {
                score += 28;
            }
            if preferred_economy {
                score += 40;
            }
            if flagship && !preferred_economy {
                score -= 30;
            }
        }
        AdpMode::Auto => {
            if cheap {
                score += 18;
            }
            if preferred_economy {
                score += 35;
            }
            if signals.complex() && flagship {
                score += 35;
            }
            if signals.complex() && cheap {
                score -= 10;
            }
        }
        AdpMode::Balanced => {
            if preferred_balanced {
                score += 40;
            }
            if capable {
                score += 15;
            }
            if flagship {
                score += 10;
            }
            if cheap && !preferred_balanced {
                score -= 8;
            }
        }
        AdpMode::Max => {
            if preferred_max {
                score += 60;
            }
            if flagship {
                score += 35;
            }
            if cheap {
                score -= 45;
            }
        }
    }

    if signals.coding && contains_any(&lower, &["coder", "code", "glm", "deepseek", "qwen"]) {
        score += 20;
    }
    if signals.debugging && flagship {
        score += 15;
    }
    if signals.browser_or_web {
        // Browser/computer-use turns benefit from capable cloud reasoning, but
        // do not need the most expensive model in Economy/Auto.
        if is_cloud && capable {
            score += 12;
        }
        if is_local && mode != AdpMode::Economy {
            score -= 20;
        }
    }
    if signals.vision
        && contains_any(
            &lower,
            &["vision", "vl", "kimi", "minimax", "glm", "deepseek"],
        )
    {
        score += 15;
    }

    score
}

fn collect_strings(value: &Value, out: &mut String) {
    match value {
        Value::String(s) => {
            out.push_str(s);
            out.push('\n');
        }
        Value::Array(items) => {
            for item in items {
                collect_strings(item, out);
            }
        }
        Value::Object(map) => {
            for (key, value) in map {
                if matches!(
                    key.as_str(),
                    "text" | "content" | "input_text" | "output_text" | "instructions"
                ) {
                    collect_strings(value, out);
                } else if value.is_array() || value.is_object() {
                    collect_strings(value, out);
                }
            }
        }
        _ => {}
    }
}

fn contains_any(haystack: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| haystack.contains(needle))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn candidates() -> Vec<String> {
        vec![
            "ollama-local/qwen3:1.7b".to_string(),
            "ollama-cloud/glm-5.3-flash:cloud".to_string(),
            "ollama-cloud/glm-5.3:cloud".to_string(),
            "ollama-cloud/kimi-k3:cloud".to_string(),
            "antigravity/gemini-pro".to_string(),
        ]
    }

    #[test]
    fn economy_prefers_inexpensive_ollama_candidates() {
        let request =
            json!({"input":"browse a page and collect the title","tools":[{"name":"browser"}]});
        let ranked = ordered_candidates(AdpMode::Economy, &request, candidates());
        assert!(ranked[0].contains("flash") || ranked[0].starts_with("ollama-local/"));
        assert!(!ranked.iter().any(|id| id.starts_with("antigravity/")));
    }

    #[test]
    fn max_prefers_flagship_cloud_models() {
        let request =
            json!({"input":"perform a difficult repository-wide refactor and debug failures"});
        let ranked = ordered_candidates(AdpMode::Max, &request, candidates());
        assert!(ranked[0].contains("kimi-k3") || ranked[0].contains("glm-5.3"));
    }

    #[test]
    fn explicit_antigravity_is_never_selected_automatically() {
        let request = json!({"input":"research this"});
        let ranked = ordered_candidates(AdpMode::Auto, &request, candidates());
        assert!(!ranked.iter().any(|id| id.starts_with("antigravity/")));
    }
}
