use adp_browser_bridge::{BrowserAction, BrowserSnapshot};
use adp_browser_broker::{BrokerError, BrokerState};
use adp_memory::{
    PageFingerprint, ProjectMemory, RunStats, SemanticTarget, TEACH_KEYWORD, Verification,
    Workflow, WorkflowStep,
};
use adp_protocol::{ExecutionContext, ExecutionTier};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;

const INSPECT_TIMEOUT: Duration = Duration::from_secs(8);
const ACTION_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplayDivergence {
    pub step_id: String,
    pub reason: String,
    pub observed_signature: Option<String>,
    pub expected_signature: Option<String>,
    pub recommended_tier: ExecutionTier,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ReplayOutcome {
    Completed {
        steps_executed: usize,
    },
    NeedsRepair {
        divergence: ReplayDivergence,
        steps_executed: usize,
    },
}

pub struct WorkflowExecutor<'a> {
    broker: &'a BrokerState,
    memory: &'a ProjectMemory,
    provider_id: &'a str,
    tab_id: Option<i64>,
}

impl<'a> WorkflowExecutor<'a> {
    pub fn new(
        broker: &'a BrokerState,
        memory: &'a ProjectMemory,
        provider_id: &'a str,
        tab_id: Option<i64>,
    ) -> Self {
        Self {
            broker,
            memory,
            provider_id,
            tab_id,
        }
    }

    pub async fn replay(&self, workflow: &Workflow, context: &ExecutionContext) -> ReplayOutcome {
        let mut stats = self.load_stats(&workflow.name);
        stats.runs += 1;

        let mut completed = 0usize;
        for step in &workflow.steps {
            let before = match self.inspect().await {
                Ok(snapshot) => snapshot,
                Err(error) => {
                    return self.finish_repair(
                        workflow,
                        stats,
                        completed,
                        step,
                        format!("browser inspection failed: {error}"),
                        None,
                        step.expected_before
                            .as_ref()
                            .map(|value| value.digest.clone()),
                    );
                }
            };

            let before_fingerprint = fingerprint(&before);
            let Some(expected_before) = step.expected_before.as_ref() else {
                return self.finish_repair(
                    workflow,
                    stats,
                    completed,
                    step,
                    "learned step has no expected pre-action state fingerprint".to_string(),
                    Some(before_fingerprint.digest),
                    None,
                );
            };

            if before_fingerprint.digest != expected_before.digest {
                return self.finish_repair(
                    workflow,
                    stats,
                    completed,
                    step,
                    "browser state differs from the learned workflow".to_string(),
                    Some(before_fingerprint.digest),
                    Some(expected_before.digest.clone()),
                );
            }

            if !step.safe_for_deterministic_replay {
                return self.finish_repair(
                    workflow,
                    stats,
                    completed,
                    step,
                    "step is not marked safe for deterministic replay".to_string(),
                    Some(before_fingerprint.digest),
                    Some(expected_before.digest.clone()),
                );
            }

            if step.verify.is_empty() && step.expected_after.is_none() {
                return self.finish_repair(
                    workflow,
                    stats,
                    completed,
                    step,
                    "step has no post-action verification".to_string(),
                    Some(before_fingerprint.digest),
                    Some(expected_before.digest.clone()),
                );
            }

            let action = match action_for_step(step, context) {
                Ok(action) => action,
                Err(reason) => {
                    return self.finish_repair(
                        workflow,
                        stats,
                        completed,
                        step,
                        reason,
                        Some(before_fingerprint.digest),
                        Some(expected_before.digest.clone()),
                    );
                }
            };

            match self
                .broker
                .call(self.provider_id, self.tab_id, action, ACTION_TIMEOUT)
                .await
            {
                Ok(result) if result.ok => {}
                Ok(result) => {
                    return self.finish_repair(
                        workflow,
                        stats,
                        completed,
                        step,
                        result
                            .error
                            .unwrap_or_else(|| "browser action failed".to_string()),
                        Some(before_fingerprint.digest),
                        Some(expected_before.digest.clone()),
                    );
                }
                Err(error) => {
                    return self.finish_repair(
                        workflow,
                        stats,
                        completed,
                        step,
                        format!("browser action failed: {error}"),
                        Some(before_fingerprint.digest),
                        Some(expected_before.digest.clone()),
                    );
                }
            }

            let after = match self.inspect().await {
                Ok(snapshot) => snapshot,
                Err(error) => {
                    return self.finish_repair(
                        workflow,
                        stats,
                        completed,
                        step,
                        format!("post-action inspection failed: {error}"),
                        None,
                        step.expected_after
                        .as_ref()
                        .map(|value| value.digest.clone()),
                    );
                }
            };

            let after_fingerprint = fingerprint(&after);
            if let Some(expected_after) = step.expected_after.as_ref()
                && after_fingerprint.digest != expected_after.digest
            {
                return self.finish_repair(
                    workflow,
                    stats,
                    completed,
                    step,
                    "post-action browser state differs from learned state".to_string(),
                    Some(after_fingerprint.digest),
                    Some(expected_after.digest.clone()),
                );
            }

            if let Err(reason) = verify_step(&after, &after_fingerprint, &step.verify) {
                return self.finish_repair(
                    workflow,
                    stats,
                    completed,
                    step,
                    reason,
                    Some(after_fingerprint.digest),
                    step.expected_after
                            .as_ref()
                            .map(|value| value.digest.clone()),
                );
            }

            completed += 1;
        }

        stats.deterministic_runs += 1;
        stats.successful_runs += 1;
        let _ = self.memory.save_run_stats(&workflow.name, &stats);
        ReplayOutcome::Completed {
            steps_executed: completed,
        }
    }

    async fn inspect(&self) -> Result<BrowserSnapshot, String> {
        let result = self
            .broker
            .call(
                self.provider_id,
                self.tab_id,
                BrowserAction::Inspect,
                INSPECT_TIMEOUT,
            )
            .await
            .map_err(broker_error)?;

        if !result.ok {
            return Err(result
                .error
                .unwrap_or_else(|| "inspection failed".to_string()));
        }

        serde_json::from_value::<BrowserSnapshot>(result.result)
            .map(BrowserSnapshot::privacy_safe)
            .map_err(|error| format!("invalid browser snapshot: {error}"))
    }

    fn finish_repair(
        &self,
        workflow: &Workflow,
        mut stats: RunStats,
        completed: usize,
        step: &WorkflowStep,
        reason: String,
        observed_signature: Option<String>,
        expected_signature: Option<String>,
    ) -> ReplayOutcome {
        stats.divergences += 1;
        let _ = self.memory.save_run_stats(&workflow.name, &stats);

        ReplayOutcome::NeedsRepair {
            divergence: ReplayDivergence {
                step_id: step.id.clone(),
                reason,
                observed_signature,
                expected_signature,
                recommended_tier: ExecutionTier::LocalModel,
            },
            steps_executed: completed,
        }
    }

    fn load_stats(&self, workflow_name: &str) -> RunStats {
        self.memory
            .load_run_stats(workflow_name)
            .unwrap_or_default()
    }
}

fn action_for_step(
    step: &WorkflowStep,
    context: &ExecutionContext,
) -> Result<BrowserAction, String> {
    match step.action.as_str() {
        "click" => Ok(BrowserAction::Click {
            target: required_target(step)?,
        }),
        "fill" => Ok(BrowserAction::Fill {
            target: required_target(step)?,
            value: resolve_fill_value(step, context)?,
        }),
        "navigate" => {
            let url = step
                .args
                .get("url")
                .and_then(Value::as_str)
                .ok_or_else(|| "navigate step has no URL".to_string())?;
            Ok(BrowserAction::Navigate {
                url: url.to_string(),
            })
        }
        "scroll" => {
            let amount = step
                .args
                .get("amount")
                .and_then(Value::as_i64)
                .ok_or_else(|| "scroll step has no integer amount".to_string())?;
            Ok(BrowserAction::Scroll { amount })
        }
        "screenshot" => Err(
            "screenshots are divergence evidence, not deterministic workflow actions".to_string(),
        ),
        other => Err(format!(
            "step action '{other}' is not supported by deterministic browser replay"
        )),
    }
}

fn required_target(step: &WorkflowStep) -> Result<SemanticTarget, String> {
    step.target
        .clone()
        .ok_or_else(|| "step has no semantic target".to_string())
}

fn resolve_fill_value(step: &WorkflowStep, context: &ExecutionContext) -> Result<String, String> {
    let value = step
        .args
        .get("value")
        .ok_or_else(|| "fill step has no value binding".to_string())?;

    if let Some(literal) = value.as_str() {
        if literal == TEACH_KEYWORD {
            return context
                .variables
                .get("keyword")
                .and_then(Value::as_str)
                .map(str::to_string)
                .ok_or_else(|| "workflow requires a 'keyword' variable".to_string());
        }
        return Err(
            "deterministic replay refuses persisted literal form values; bind a variable instead"
                .to_string(),
        );
    }

    if let Some(variable) = value.get("variable").and_then(Value::as_str) {
        return context
            .variables
            .get(variable)
            .and_then(Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| format!("workflow variable '{variable}' is missing or not text"));
    }

    Err("fill value must be a workflow variable binding".to_string())
}

fn fingerprint(snapshot: &BrowserSnapshot) -> PageFingerprint {
    let landmarks = snapshot
        .elements
        .iter()
        .filter(|element| !element.disabled)
        .map(|element| {
            format!(
                "{}|{}|{}|{}",
                element.role.as_deref().unwrap_or_default(),
                element.name.as_deref().unwrap_or_default(),
                element.test_id.as_deref().unwrap_or_default(),
                element.aria_label.as_deref().unwrap_or_default(),
            )
        })
        .collect::<Vec<_>>();

    PageFingerprint::from_observation(&snapshot.url, &snapshot.title, &landmarks)
}

fn verify_step(
    snapshot: &BrowserSnapshot,
    fingerprint: &PageFingerprint,
    verifications: &[Verification],
) -> Result<(), String> {
    for verification in verifications {
        match verification.kind.as_str() {
            "url_contains" => {
                let needle = verification
                    .expected
                    .as_str()
                    .ok_or_else(|| "url_contains verification must be text".to_string())?;
                if !snapshot.url.contains(needle) {
                    return Err(format!(
                        "verification failed: URL does not contain '{needle}'"
                    ));
                }
            }
            "title_contains" => {
                let needle = verification
                    .expected
                    .as_str()
                    .ok_or_else(|| "title_contains verification must be text".to_string())?;
                if !snapshot.title.contains(needle) {
                    return Err(format!(
                        "verification failed: page title does not contain '{needle}'"
                    ));
                }
            }
            "state_digest" => {
                let digest = verification
                    .expected
                    .as_str()
                    .ok_or_else(|| "state_digest verification must be text".to_string())?;
                if fingerprint.digest != digest {
                    return Err("verification failed: state digest changed".to_string());
                }
            }
            "element_present" => {
                let target: SemanticTarget = serde_json::from_value(verification.expected.clone())
                    .map_err(|error| format!("invalid element_present target: {error}"))?;
                if !snapshot
                    .elements
                    .iter()
                    .any(|element| semantic_element_matches(element, &target))
                {
                    return Err("verification failed: expected element is absent".to_string());
                }
            }
            other => {
                return Err(format!(
                    "verification kind '{other}' is not supported by deterministic replay"
                ));
            }
        }
    }
    Ok(())
}

fn semantic_element_matches(
    element: &adp_browser_bridge::SemanticElement,
    target: &SemanticTarget,
) -> bool {
    optional_matches(target.role.as_deref(), element.role.as_deref())
        && optional_matches(target.name.as_deref(), element.name.as_deref())
        && optional_matches(target.test_id.as_deref(), element.test_id.as_deref())
        && optional_matches(target.aria_label.as_deref(), element.aria_label.as_deref())
}

fn optional_matches(expected: Option<&str>, actual: Option<&str>) -> bool {
    expected.is_none_or(|expected| actual == Some(expected))
}

fn broker_error(error: BrokerError) -> String {
    error.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use adp_browser_bridge::SemanticElement;
    use serde_json::json;
    use std::collections::BTreeMap;

    fn snapshot(url: &str) -> BrowserSnapshot {
        BrowserSnapshot {
            tab_id: 1,
            url: url.to_string(),
            title: "Products".to_string(),
            elements: vec![SemanticElement {
                role: Some("button".to_string()),
                name: Some("Export".to_string()),
                test_id: None,
                aria_label: None,
                css: Some("#export".to_string()),
                disabled: false,
            }],
            page_signature: None,
        }
    }

    #[test]
    fn fingerprint_ignores_url_query_values() {
        let a = fingerprint(&snapshot("https://example.com/search?q=one"));
        let b = fingerprint(&snapshot("https://example.com/search?q=two"));
        assert_eq!(a.digest, b.digest);
    }

    #[test]
    fn keyword_fill_resolves_from_runtime_context_not_persisted_memory() {
        let step = WorkflowStep {
            id: "search".to_string(),
            action: "fill".to_string(),
            tab: String::new(),
            target: Some(SemanticTarget {
                css: Some("#search".to_string()),
                ..Default::default()
            }),
            args: json!({"value": TEACH_KEYWORD}),
            verify: Vec::new(),
            expected_before: None,
            expected_after: None,
            safe_for_deterministic_replay: true,
        };
        let context = ExecutionContext {
            variables: BTreeMap::from([("keyword".to_string(), json!("bee wax wrap"))]),
            ..Default::default()
        };

        let action = action_for_step(&step, &context).unwrap();
        assert_eq!(
            action,
            BrowserAction::Fill {
                target: step.target.unwrap(),
                value: "bee wax wrap".to_string(),
            }
        );
    }

    #[test]
    fn literal_form_values_are_not_replayed_from_memory() {
        let step = WorkflowStep {
            id: "unsafe-fill".to_string(),
            action: "fill".to_string(),
            tab: String::new(),
            target: Some(SemanticTarget::default()),
            args: json!({"value": "persisted secret-ish value"}),
            verify: Vec::new(),
            expected_before: None,
            expected_after: None,
            safe_for_deterministic_replay: true,
        };

        assert!(action_for_step(&step, &ExecutionContext::default()).is_err());
    }

    #[test]
    fn semantic_element_verification_uses_meaning_not_coordinates() {
        let page = snapshot("https://example.com/products");
        let verification = Verification {
            kind: "element_present".to_string(),
            expected: json!({
                "role": "button",
                "name": "Export"
            }),
        };
        let fp = fingerprint(&page);
        assert!(verify_step(&page, &fp, &[verification]).is_ok());
    }
}
