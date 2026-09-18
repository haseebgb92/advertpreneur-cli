use adp_protocol::{
    ExecutionBudget, ExecutionTier, InvocationDialect, ProviderCapabilities, ToolDescriptor,
};

#[derive(Debug, Clone, PartialEq)]
pub struct ToolPlan {
    pub dialect: InvocationDialect,
    pub tools: Vec<ToolDescriptor>,
}

#[derive(Debug, Default)]
pub struct ToolExposurePolicy;

impl ToolExposurePolicy {
    /// Tool availability belongs to ADP, not to a provider brand.
    pub fn plan(
        &self,
        capabilities: &ProviderCapabilities,
        available_tools: &[ToolDescriptor],
    ) -> ToolPlan {
        let dialect = capabilities.invocation_dialect();
        let tools = match dialect {
            InvocationDialect::Unsupported => Vec::new(),
            InvocationDialect::NativeFunction | InvocationDialect::StructuredJson => {
                available_tools.to_vec()
            }
        };
        ToolPlan { dialect, tools }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannerInput {
    pub workflow_available: bool,
    pub state_signature_matches: bool,
    pub previous_step_verified: bool,
    pub action_is_deterministic_safe: bool,
    pub local_model_available: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionDecision {
    pub tier: ExecutionTier,
    pub reason: &'static str,
}

#[derive(Debug, Clone, Default)]
pub struct ExecutionPlanner {
    pub budget: ExecutionBudget,
}

impl ExecutionPlanner {
    pub fn decide(&self, input: &PlannerInput) -> ExecutionDecision {
        if input.workflow_available
            && input.state_signature_matches
            && input.previous_step_verified
            && input.action_is_deterministic_safe
        {
            return ExecutionDecision {
                tier: ExecutionTier::Deterministic,
                reason: "known verified state; replay without a model call",
            };
        }

        if input.local_model_available {
            return ExecutionDecision {
                tier: ExecutionTier::LocalModel,
                reason: "state diverged; ask the local model for a bounded repair",
            };
        }

        ExecutionDecision {
            tier: ExecutionTier::PrimaryModel,
            reason: "state diverged and no local repair model is available",
        }
    }

    pub fn primary_escalation_allowed(&self, used: u32) -> bool {
        used < self.budget.max_primary_escalations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use adp_protocol::{Capability, ToolDescriptor};
    use serde_json::json;

    fn browser_tool() -> ToolDescriptor {
        ToolDescriptor {
            name: "adp.browser.click".into(),
            description: "Click a semantic browser target".into(),
            capability: Capability::Browser,
            input_schema: json!({"type":"object"}),
            deterministic_safe: true,
        }
    }

    #[test]
    fn native_and_structured_models_receive_the_same_capability_inventory() {
        let policy = ToolExposurePolicy;
        let tools = vec![browser_tool()];

        let native = policy.plan(
            &ProviderCapabilities {
                native_tool_calls: true,
                ..Default::default()
            },
            &tools,
        );
        let structured = policy.plan(
            &ProviderCapabilities {
                structured_output: true,
                ..Default::default()
            },
            &tools,
        );

        assert_eq!(native.tools, structured.tools);
        assert_eq!(native.dialect, InvocationDialect::NativeFunction);
        assert_eq!(structured.dialect, InvocationDialect::StructuredJson);
    }

    #[test]
    fn repeat_work_uses_zero_model_tier_when_state_is_verified() {
        let planner = ExecutionPlanner::default();
        let decision = planner.decide(&PlannerInput {
            workflow_available: true,
            state_signature_matches: true,
            previous_step_verified: true,
            action_is_deterministic_safe: true,
            local_model_available: true,
        });
        assert_eq!(decision.tier, ExecutionTier::Deterministic);
    }

    #[test]
    fn divergence_prefers_local_model_before_primary_model() {
        let planner = ExecutionPlanner::default();
        let decision = planner.decide(&PlannerInput {
            workflow_available: true,
            state_signature_matches: false,
            previous_step_verified: true,
            action_is_deterministic_safe: true,
            local_model_available: true,
        });
        assert_eq!(decision.tier, ExecutionTier::LocalModel);
    }
}
