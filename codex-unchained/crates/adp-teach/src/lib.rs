use adp_browser_bridge::{BrowserAction, BrowserSnapshot, LearnEvent, TEACH_KEYWORD};
use adp_browser_broker::{BrokerError, BrokerState, LearnEnvelope};
use adp_executor::fingerprint_snapshot;
use adp_memory::{ProjectMemory, Verification, Workflow, WorkflowStep};
use serde_json::json;
use std::time::Duration;
use thiserror::Error;
use tokio::time::sleep;

const INSPECT_TIMEOUT: Duration = Duration::from_secs(8);
const INSPECT_RETRIES: usize = 20;

#[derive(Debug, Clone)]
pub struct TeachConfig {
    pub workflow_name: String,
    pub provider_id: String,
    pub tab_id: Option<i64>,
    pub settle_delay: Duration,
}

impl TeachConfig {
    pub fn new(workflow_name: impl Into<String>, provider_id: impl Into<String>) -> Self {
        Self {
            workflow_name: workflow_name.into(),
            provider_id: provider_id.into(),
            tab_id: None,
            settle_delay: Duration::from_millis(150),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TeachRecordOutcome {
    Ignored,
    Recorded {
        step_id: String,
        deterministic_safe: bool,
    },
}

#[derive(Debug, Error)]
pub enum TeachError {
    #[error(transparent)]
    Broker(#[from] BrokerError),
    #[error("browser runtime returned an error: {0}")]
    Browser(String),
    #[error("browser snapshot was invalid: {0}")]
    Snapshot(String),
    #[error("teach event is missing a semantic target")]
    MissingTarget,
    #[error(transparent)]
    Memory(#[from] adp_memory::MemoryError),
}

pub struct TeachRecorder<'a> {
    broker: &'a BrokerState,
    memory: &'a ProjectMemory,
    config: TeachConfig,
    workflow: Workflow,
    tab_id: i64,
    previous_snapshot: BrowserSnapshot,
}

impl<'a> TeachRecorder<'a> {
    pub async fn start(
        broker: &'a BrokerState,
        memory: &'a ProjectMemory,
        mut config: TeachConfig,
    ) -> Result<Self, TeachError> {
        let tab_id = match config.tab_id {
            Some(tab_id) => tab_id,
            None => bind_active_tab(broker, &config.provider_id).await?,
        };
        config.tab_id = Some(tab_id);

        let previous_snapshot = inspect_with_retry(broker, &config.provider_id, tab_id).await?;
        let mut workflow = Workflow::new(config.workflow_name.clone());
        workflow.protected_tabs = json!({
            "primary": {
                "tab_id": tab_id,
                "start_url": previous_snapshot.url
            }
        });
        memory.save_workflow(&workflow)?;

        Ok(Self {
            broker,
            memory,
            config,
            workflow,
            tab_id,
            previous_snapshot,
        })
    }

    pub fn workflow(&self) -> &Workflow {
        &self.workflow
    }

    pub fn tab_id(&self) -> i64 {
        self.tab_id
    }

    pub async fn record(
        &mut self,
        envelope: LearnEnvelope,
    ) -> Result<TeachRecordOutcome, TeachError> {
        if envelope.provider_id != self.config.provider_id {
            return Ok(TeachRecordOutcome::Ignored);
        }
        if envelope.tab_id.is_some_and(|tab_id| tab_id != self.tab_id) {
            return Ok(TeachRecordOutcome::Ignored);
        }

        let Some(mut step) = compile_event(
            &envelope.event,
            self.workflow.steps.len() + 1,
            self.tab_id,
        )? else {
            return Ok(TeachRecordOutcome::Ignored);
        };

        step.expected_before = Some(fingerprint_snapshot(&self.previous_snapshot));

        sleep(self.config.settle_delay).await;
        let after = inspect_with_retry(self.broker, &self.config.provider_id, self.tab_id).await?;
        let after_fingerprint = fingerprint_snapshot(&after);
        step.expected_after = Some(after_fingerprint.clone());
        step.verify.push(Verification {
            kind: "state_digest".to_string(),
            expected: json!(after_fingerprint.digest),
        });

        let step_id = step.id.clone();
        let deterministic_safe = step.safe_for_deterministic_replay;
        self.workflow.steps.push(step);
        self.workflow.version = self.workflow.version.saturating_add(1);
        self.memory.save_workflow(&self.workflow)?;
        self.previous_snapshot = after;

        Ok(TeachRecordOutcome::Recorded {
            step_id,
            deterministic_safe,
        })
    }
}

async fn bind_active_tab(
    broker: &BrokerState,
    provider_id: &str,
) -> Result<i64, TeachError> {
    let result = broker
        .call(
            provider_id,
            None,
            BrowserAction::BindActive,
            INSPECT_TIMEOUT,
        )
        .await?;
    if !result.ok {
        return Err(TeachError::Browser(
            result
                .error
                .unwrap_or_else(|| "failed to bind the active tab".to_string()),
        ));
    }

    result
        .result
        .get("tab_id")
        .and_then(serde_json::Value::as_i64)
        .ok_or_else(|| TeachError::Browser("bind_active returned no tab id".to_string()))
}

async fn inspect_with_retry(
    broker: &BrokerState,
    provider_id: &str,
    tab_id: i64,
) -> Result<BrowserSnapshot, TeachError> {
    let mut last_error = String::new();

    for _ in 0..INSPECT_RETRIES {
        match broker
            .call(
                provider_id,
                Some(tab_id),
                BrowserAction::Inspect,
                INSPECT_TIMEOUT,
            )
            .await
        {
            Ok(result) if result.ok => {
                return serde_json::from_value::<BrowserSnapshot>(result.result)
                    .map(BrowserSnapshot::privacy_safe)
                    .map_err(|error| TeachError::Snapshot(error.to_string()));
            }
            Ok(result) => {
                last_error = result
                    .error
                    .unwrap_or_else(|| "browser inspection failed".to_string());
            }
            Err(error) => last_error = error.to_string(),
        }
        sleep(Duration::from_millis(250)).await;
    }

    Err(TeachError::Browser(if last_error.is_empty() {
        "browser inspection did not become available".to_string()
    } else {
        last_error
    }))
}

fn compile_event(
    event: &LearnEvent,
    index: usize,
    tab_id: i64,
) -> Result<Option<WorkflowStep>, TeachError> {
    let target = event.target.clone();

    let (args, safe) = match event.action.as_str() {
        "fill" => {
            let target = target.as_ref().ok_or(TeachError::MissingTarget)?;
            let value = event.value.as_deref();
            if value != Some(TEACH_KEYWORD) {
                return Ok(None);
            }
            (
                json!({"value": TEACH_KEYWORD}),
                !target_looks_consequential(target),
            )
        }
        "click" => {
            let target = target.as_ref().ok_or(TeachError::MissingTarget)?;
            (json!({}), !target_looks_consequential(target))
        }
        _ => return Ok(None),
    };

    Ok(Some(WorkflowStep {
        id: format!("step-{index:03}"),
        action: event.action.clone(),
        tab: format!("primary:{tab_id}"),
        target,
        args,
        verify: Vec::new(),
        expected_before: None,
        expected_after: None,
        safe_for_deterministic_replay: safe,
    }))
}

fn target_looks_consequential(target: &adp_memory::SemanticTarget) -> bool {
    let text = [
        target.role.as_deref(),
        target.name.as_deref(),
        target.aria_label.as_deref(),
        target.test_id.as_deref(),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join(" ")
    .to_ascii_lowercase();

    const CONSEQUENTIAL: &[&str] = &[
        "delete",
        "remove",
        "pay",
        "purchase",
        "buy",
        "checkout",
        "place order",
        "submit",
        "send",
        "publish",
        "post",
        "sign",
        "confirm",
        "save",
        "unsubscribe",
        "cancel subscription",
        "transfer",
        "withdraw",
        "deposit",
        "book",
        "reserve",
    ];

    CONSEQUENTIAL.iter().any(|needle| text.contains(needle))
}

#[cfg(test)]
mod tests {
    use super::*;
    use adp_memory::SemanticTarget;

    fn event(action: &str, name: &str, value: Option<&str>) -> LearnEvent {
        LearnEvent {
            action: action.to_string(),
            selector_hint: String::new(),
            target: Some(SemanticTarget {
                role: Some(if action == "fill" { "input" } else { "button" }.to_string()),
                name: Some(name.to_string()),
                ..Default::default()
            }),
            value: value.map(str::to_string),
            evidence: json!({}),
        }
    }

    #[test]
    fn parameterized_search_fill_can_be_deterministic() {
        let step = compile_event(
            &event("fill", "Search", Some(TEACH_KEYWORD)),
            1,
            7,
        )
        .unwrap()
        .unwrap();

        assert!(step.safe_for_deterministic_replay);
        assert_eq!(step.args["value"], TEACH_KEYWORD);
    }

    #[test]
    fn arbitrary_typed_values_are_not_compiled() {
        let result = compile_event(
            &event("fill", "Address", Some("private value")),
            1,
            7,
        )
        .unwrap();
        assert!(result.is_none());
    }

    #[test]
    fn consequential_click_is_learned_but_not_zero_model_safe() {
        let step = compile_event(&event("click", "Delete account", None), 1, 7)
            .unwrap()
            .unwrap();

        assert!(!step.safe_for_deterministic_replay);
    }

    #[test]
    fn ordinary_export_click_can_be_replayed_when_state_matches() {
        let step = compile_event(&event("click", "Export CSV", None), 1, 7)
            .unwrap()
            .unwrap();

        assert!(step.safe_for_deterministic_replay);
    }
}
