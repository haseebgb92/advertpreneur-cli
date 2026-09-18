use adp_agent::{AgentRuntime, BROWSER_CLICK, BROWSER_FILL, BROWSER_INSPECT, ToolExecutionRecord};
use adp_browser_broker::{BrokerState, serve};
use adp_executor::{ReplayOutcome, WorkflowExecutor, verify_repair_candidate};
use adp_memory::{ProjectMemory, SemanticTarget, TEACH_KEYWORD, WorkflowRepair, WorkflowStep};
use adp_model::{OllamaClient, OllamaConfig, OllamaTransport};
use adp_protocol::ExecutionContext;
use adp_teach::{TeachConfig, TeachRecordOutcome, TeachRecorder};
use adp_web::{WebClient, WebConfig};
use clap::{Args, Parser, Subcommand, ValueEnum};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::error::Error;
use std::path::PathBuf;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::time::{Instant, sleep};

#[derive(Debug, Parser)]
#[command(name = "adp-unchained")]
#[command(about = "Provider-neutral ADP/Codex execution kernel")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Run the localhost Chrome/Edge broker on 127.0.0.1:8765.
    Broker,
    /// List models visible through an Ollama transport.
    Models(ModelArgs),
    /// Check provider capabilities and connectivity.
    Doctor(ModelArgs),
    /// Run a model conversation with optional browser and web host tools.
    Chat(ChatArgs),
    /// Search the public web through the Ollama host web API.
    WebSearch(WebSearchArgs),
    /// Fetch readable content for a public URL through the Ollama host web API.
    WebFetch(WebFetchArgs),
    /// Record a browser demonstration into an executable .advertpreneur workflow.
    Teach(TeachArgs),
    /// Replay a learned .advertpreneur workflow without a model when state matches.
    Replay(ReplayArgs),
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum ProviderArg {
    Local,
    Cloud,
}

#[derive(Debug, Clone, Args)]
struct ModelArgs {
    #[arg(long, value_enum, default_value_t = ProviderArg::Local)]
    provider: ProviderArg,
    #[arg(long, default_value = "qwen3:1.7b")]
    model: String,
    #[arg(long)]
    base_url: Option<String>,
    #[arg(long, default_value = "OLLAMA_API_KEY")]
    api_key_env: String,
}

#[derive(Debug, Args)]
struct ChatArgs {
    #[command(flatten)]
    model: ModelArgs,
    #[arg(long)]
    browser: bool,
    #[arg(long)]
    web: bool,
    #[arg(long, default_value = "https://ollama.com")]
    web_base_url: String,
    #[arg(long, default_value = "OLLAMA_API_KEY")]
    web_api_key_env: String,
    #[arg(long, default_value_t = 20)]
    browser_wait_seconds: u64,
    prompt: String,
}

#[derive(Debug, Args)]
struct WebSearchArgs {
    query: String,
    #[arg(long, default_value_t = 5)]
    max_results: u32,
    #[arg(long, default_value = "https://ollama.com")]
    base_url: String,
    #[arg(long, default_value = "OLLAMA_API_KEY")]
    api_key_env: String,
}

#[derive(Debug, Args)]
struct WebFetchArgs {
    url: String,
    #[arg(long, default_value = "https://ollama.com")]
    base_url: String,
    #[arg(long, default_value = "OLLAMA_API_KEY")]
    api_key_env: String,
}

#[derive(Debug, Args)]
struct TeachArgs {
    workflow: String,
    #[arg(long)]
    project: Option<PathBuf>,
    #[arg(long)]
    provider_id: Option<String>,
    #[arg(long)]
    tab_id: Option<i64>,
    #[arg(long, default_value_t = 20)]
    browser_wait_seconds: u64,
}

#[derive(Debug, Args)]
struct ReplayArgs {
    workflow: String,
    #[arg(long)]
    project: Option<PathBuf>,
    #[arg(long)]
    provider_id: Option<String>,
    #[arg(long)]
    tab_id: Option<i64>,
    #[arg(long)]
    keyword: Option<String>,
    #[arg(long, default_value_t = 20)]
    browser_wait_seconds: u64,
    #[arg(long)]
    no_repair: bool,
    #[arg(long, default_value = "qwen3:1.7b")]
    local_repair_model: String,
    #[arg(long)]
    local_repair_base_url: Option<String>,
    #[arg(long)]
    primary_repair_model: Option<String>,
    #[arg(long)]
    primary_repair_base_url: Option<String>,
    #[arg(long, default_value = "OLLAMA_API_KEY")]
    primary_api_key_env: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let cli = Cli::parse();

    match cli.command {
        Command::Broker => run_broker().await?,
        Command::Models(args) => list_models(args).await?,
        Command::Doctor(args) => doctor(args).await?,
        Command::Chat(args) => chat(args).await?,
        Command::WebSearch(args) => web_search(args).await?,
        Command::WebFetch(args) => web_fetch(args).await?,
        Command::Teach(args) => teach(args).await?,
        Command::Replay(args) => replay(args).await?,
    }

    Ok(())
}

async fn run_broker() -> Result<(), Box<dyn Error>> {
    let state = BrokerState::new();
    let listener = bind_broker().await?;
    println!("ADP browser broker listening on http://127.0.0.1:8765");
    serve(listener, state).await?;
    Ok(())
}

async fn list_models(args: ModelArgs) -> Result<(), Box<dyn Error>> {
    let client = OllamaClient::new(model_config(&args))?;
    let models = client.list_models().await?;
    for model in models {
        println!("{model}");
    }
    Ok(())
}

async fn doctor(args: ModelArgs) -> Result<(), Box<dyn Error>> {
    let client = OllamaClient::new(model_config(&args))?;
    let capabilities = client.capabilities();
    let models = client.list_models().await?;

    println!("provider: {:?}", client.provider_kind());
    println!("model: {}", client.model());
    println!("native_tool_calls: {}", capabilities.native_tool_calls);
    println!("structured_output: {}", capabilities.structured_output);
    println!("streaming: {}", capabilities.streaming);
    println!("visible_models: {}", models.len());
    println!(
        "selected_model_visible: {}",
        models.iter().any(|model| model == client.model())
    );
    Ok(())
}

async fn chat(args: ChatArgs) -> Result<(), Box<dyn Error>> {
    let model = OllamaClient::new(model_config(&args.model))?;
    let broker = BrokerState::new();

    let browser_provider = if args.browser {
        let listener = bind_broker().await?;
        let broker_for_server = broker.clone();
        tokio::spawn(async move {
            let _ = serve(listener, broker_for_server).await;
        });

        println!("Waiting for the ADP Chrome/Edge extension...");
        Some(
            wait_for_browser(
                &broker,
                None,
                Duration::from_secs(args.browser_wait_seconds),
            )
            .await?,
        )
    } else {
        None
    };

    let web = if args.web {
        Some(WebClient::new(WebConfig {
            base_url: args.web_base_url.clone(),
            api_key_env: args.web_api_key_env.clone(),
        })?)
    } else {
        None
    };

    let mut agent = AgentRuntime::new(&model, &broker);
    if let Some(web) = web.as_ref() {
        agent = agent.with_web_client(web);
    }
    if let Some(provider_id) = browser_provider {
        println!("Browser runtime connected: {provider_id}");
        agent = agent.with_browser_provider(provider_id);
    }

    let result = agent.run(&args.prompt).await?;
    if !result.final_text.is_empty() {
        println!("{}", result.final_text);
    }
    eprintln!(
        "model_turns={} tool_calls={} input_tokens={} output_tokens={}",
        result.model_turns, result.tool_calls, result.input_tokens, result.output_tokens
    );
    Ok(())
}

async fn web_search(args: WebSearchArgs) -> Result<(), Box<dyn Error>> {
    let client = WebClient::new(WebConfig {
        base_url: args.base_url,
        api_key_env: args.api_key_env,
    })?;
    let result = client.search(&args.query, Some(args.max_results)).await?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

async fn web_fetch(args: WebFetchArgs) -> Result<(), Box<dyn Error>> {
    let client = WebClient::new(WebConfig {
        base_url: args.base_url,
        api_key_env: args.api_key_env,
    })?;
    let result = client.fetch(&args.url).await?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

async fn teach(args: TeachArgs) -> Result<(), Box<dyn Error>> {
    let project_root = match args.project {
        Some(path) => path,
        None => std::env::current_dir()?,
    };
    let memory = ProjectMemory::open(&project_root)?;
    let broker = BrokerState::new();
    let listener = bind_broker().await?;
    let broker_for_server = broker.clone();
    tokio::spawn(async move {
        let _ = serve(listener, broker_for_server).await;
    });

    println!("Waiting for the ADP Chrome/Edge extension...");
    let provider_id = wait_for_browser(
        &broker,
        args.provider_id.as_deref(),
        Duration::from_secs(args.browser_wait_seconds),
    )
    .await?;

    let mut events = broker.subscribe_learn();
    let mut config = TeachConfig::new(&args.workflow, &provider_id);
    config.tab_id = args.tab_id;
    let mut recorder = TeachRecorder::start(&broker, &memory, config).await?;

    println!(
        "Teaching '{}' on browser tab {}. Demonstrate the workflow, then press Ctrl+C to finish.",
        args.workflow,
        recorder.tab_id()
    );

    loop {
        tokio::select! {
            signal = tokio::signal::ctrl_c() => {
                signal?;
                break;
            }
            event = events.recv() => {
                match event {
                    Ok(event) => match recorder.record(event).await? {
                        TeachRecordOutcome::Ignored => {}
                        TeachRecordOutcome::Recorded {
                            step_id,
                            deterministic_safe,
                        } => {
                            println!(
                                "learned {step_id} deterministic_safe={deterministic_safe}"
                            );
                        }
                    },
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(skipped)) => {
                        eprintln!("teach event stream lagged; skipped {skipped} events");
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                        return Err("teach event stream closed".into());
                    }
                }
            }
        }
    }

    println!(
        "Saved workflow '{}' with {} steps under {}",
        recorder.workflow().name,
        recorder.workflow().steps.len(),
        memory.root().display()
    );
    Ok(())
}

async fn replay(args: ReplayArgs) -> Result<(), Box<dyn Error>> {
    let project_root = match args.project.clone() {
        Some(path) => path,
        None => std::env::current_dir()?,
    };
    let memory = ProjectMemory::open(&project_root)?;
    let workflow = memory.load_workflow(&args.workflow)?;

    let broker = BrokerState::new();
    let listener = bind_broker().await?;
    let broker_for_server = broker.clone();
    tokio::spawn(async move {
        let _ = serve(listener, broker_for_server).await;
    });

    println!("Waiting for the ADP Chrome/Edge extension...");
    let provider_id = wait_for_browser(
        &broker,
        args.provider_id.as_deref(),
        Duration::from_secs(args.browser_wait_seconds),
    )
    .await?;

    let variables = args
        .keyword
        .clone()
        .map(|keyword| BTreeMap::from([("keyword".to_string(), Value::String(keyword))]))
        .unwrap_or_default();

    let context = ExecutionContext {
        project_root: Some(project_root.display().to_string()),
        workflow: Some(workflow.name.clone()),
        workflow_version: Some(workflow.version),
        current_state: None,
        variables,
    };

    let executor = WorkflowExecutor::new(&broker, &memory, &provider_id, args.tab_id);
    let outcome = executor.replay(&workflow, &context).await;

    let ReplayOutcome::NeedsRepair {
        divergence,
        steps_executed,
    } = outcome
    else {
        print_replay_outcome(&workflow.name, outcome);
        return Ok(());
    };

    if args.no_repair {
        print_replay_outcome(
            &workflow.name,
            ReplayOutcome::NeedsRepair {
                divergence,
                steps_executed,
            },
        );
        return Ok(());
    }

    let Some(step) = workflow.steps.get(steps_executed) else {
        return Err("repair requested for a workflow step that does not exist".into());
    };
    if !step.safe_for_deterministic_replay {
        eprintln!("Automatic repair skipped: the demonstrated step is not deterministic-safe.");
        print_replay_outcome(
            &workflow.name,
            ReplayOutcome::NeedsRepair {
                divergence,
                steps_executed,
            },
        );
        return Ok(());
    }

    let Some(observed_signature) = divergence.observed_signature.clone() else {
        eprintln!("Automatic repair skipped: no observed browser-state signature is available.");
        print_replay_outcome(
            &workflow.name,
            ReplayOutcome::NeedsRepair {
                divergence,
                steps_executed,
            },
        );
        return Ok(());
    };

    let local_config = OllamaConfig {
        transport: OllamaTransport::Local,
        model: args.local_repair_model.clone(),
        base_url: args.local_repair_base_url.clone(),
        api_key_env: None,
    };

    let repair = match attempt_safe_repair(
        &broker,
        &executor,
        &provider_id,
        args.tab_id,
        step,
        &context,
        local_config,
    )
    .await
    {
        Ok(repair) => {
            record_repair_usage(&memory, &workflow.name, &repair, false)?;
            repair
        }
        Err(local_error) => {
            let Some(primary_model) = args.primary_repair_model.clone() else {
                eprintln!("Local repair did not succeed: {local_error}");
                print_replay_outcome(
                    &workflow.name,
                    ReplayOutcome::NeedsRepair {
                        divergence,
                        steps_executed,
                    },
                );
                return Ok(());
            };

            eprintln!("Local repair did not succeed; trying configured cloud repair model.");
            let primary_config = OllamaConfig {
                transport: OllamaTransport::Cloud,
                model: primary_model,
                base_url: args.primary_repair_base_url.clone(),
                api_key_env: Some(args.primary_api_key_env.clone()),
            };
            let repair = attempt_safe_repair(
                &broker,
                &executor,
                &provider_id,
                args.tab_id,
                step,
                &context,
                primary_config,
            )
            .await?;
            record_repair_usage(&memory, &workflow.name, &repair, true)?;
            repair
        }
    };

    let updated_workflow = memory.save_verified_repair(
        &workflow.name,
        WorkflowRepair {
            observed_signature,
            after_signature: Some(repair.after_signature.clone()),
            step_id: step.id.clone(),
            previous_target: step.target.clone(),
            repaired_target: Some(repair.target.clone()),
            verified: true,
        },
    )?;

    println!(
        "Learned verified repair for {} using {} model tokens ({} in / {} out).",
        step.id,
        if repair.cloud { "cloud" } else { "local" },
        repair.input_tokens,
        repair.output_tokens
    );

    let resumed = executor
        .replay_from(&updated_workflow, &context, steps_executed + 1)
        .await;
    print_replay_outcome(&updated_workflow.name, resumed);
    Ok(())
}

#[derive(Debug)]
struct RepairAttempt {
    target: SemanticTarget,
    after_signature: String,
    input_tokens: u64,
    output_tokens: u64,
    cloud: bool,
}

async fn attempt_safe_repair(
    broker: &BrokerState,
    executor: &WorkflowExecutor<'_>,
    provider_id: &str,
    tab_id: Option<i64>,
    step: &WorkflowStep,
    context: &ExecutionContext,
    model_config: OllamaConfig,
) -> Result<RepairAttempt, Box<dyn Error>> {
    let action_tool = match step.action.as_str() {
        "click" => BROWSER_CLICK,
        "fill" => BROWSER_FILL,
        other => {
            return Err(format!(
                "automatic semantic-target repair is not enabled for '{other}' steps"
            )
            .into());
        }
    };

    let model = OllamaClient::new(model_config.clone())?;
    let value_hint = repair_value_hint(step, context);
    let prompt = format!(
        "Repair exactly one previously learned SAFE browser step. \
         Inspect the current page, then perform the semantic equivalent of the old step and stop. \
         Do not perform any other state-changing action. \
         Step action: {}. Old semantic target: {}. {}",
        step.action,
        serde_json::to_string(&step.target)?,
        value_hint
    );

    let agent = AgentRuntime::new(&model, broker)
        .with_browser_provider(provider_id)
        .with_allowed_tools([BROWSER_INSPECT, action_tool]);
    let agent = match tab_id {
        Some(tab_id) => agent.with_browser_tab(tab_id),
        None => agent,
    };

    let result = agent.run(&prompt).await?;
    let successful_actions = result
        .executed_tools
        .iter()
        .filter(|record| record.ok && record.call.name == action_tool)
        .collect::<Vec<_>>();

    if successful_actions.len() != 1 {
        return Err(format!(
            "repair model must complete exactly one successful '{}' action; observed {}",
            action_tool,
            successful_actions.len()
        )
        .into());
    }

    let target = repaired_target(successful_actions[0])?;
    let snapshot = executor.inspect_snapshot().await?;
    let fingerprint = verify_repair_candidate(step, &snapshot)?;

    Ok(RepairAttempt {
        target,
        after_signature: fingerprint.digest,
        input_tokens: result.input_tokens,
        output_tokens: result.output_tokens,
        cloud: model_config.transport == OllamaTransport::Cloud,
    })
}

fn repaired_target(record: &ToolExecutionRecord) -> Result<SemanticTarget, Box<dyn Error>> {
    let target = record
        .call
        .arguments
        .get("target")
        .cloned()
        .ok_or("successful repair action returned no semantic target")?;
    Ok(serde_json::from_value(target)?)
}

fn repair_value_hint(step: &WorkflowStep, context: &ExecutionContext) -> String {
    if step.action != "fill" {
        return String::new();
    }

    if step.args.get("value").and_then(Value::as_str) == Some(TEACH_KEYWORD)
        && let Some(keyword) = context.variables.get("keyword").and_then(Value::as_str)
    {
        return format!("Use this non-sensitive workflow keyword value: {keyword:?}.");
    }

    "Use only the runtime value required by the demonstrated safe step.".to_string()
}

fn record_repair_usage(
    memory: &ProjectMemory,
    workflow_name: &str,
    repair: &RepairAttempt,
    primary: bool,
) -> Result<(), Box<dyn Error>> {
    let mut stats = memory.load_run_stats(workflow_name)?;
    if primary {
        stats.primary_model_escalations = stats.primary_model_escalations.saturating_add(1);
    } else {
        stats.local_model_assists = stats.local_model_assists.saturating_add(1);
    }
    stats.model_input_tokens = stats.model_input_tokens.saturating_add(repair.input_tokens);
    stats.model_output_tokens = stats
        .model_output_tokens
        .saturating_add(repair.output_tokens);
    memory.save_run_stats(workflow_name, &stats)?;
    Ok(())
}

fn print_replay_outcome(workflow_name: &str, outcome: ReplayOutcome) {
    match outcome {
        ReplayOutcome::Completed { steps_executed } => {
            println!(
                "{}",
                json!({
                    "status": "completed",
                    "workflow": workflow_name,
                    "steps_executed": steps_executed
                })
            );
        }
        ReplayOutcome::NeedsRepair {
            divergence,
            steps_executed,
        } => {
            println!(
                "{}",
                json!({
                    "status": "needs_repair",
                    "workflow": workflow_name,
                    "steps_executed": steps_executed,
                    "divergence": divergence
                })
            );
        }
    }
}

fn model_config(args: &ModelArgs) -> OllamaConfig {
    let transport = match args.provider {
        ProviderArg::Local => OllamaTransport::Local,
        ProviderArg::Cloud => OllamaTransport::Cloud,
    };

    OllamaConfig {
        transport,
        model: args.model.clone(),
        base_url: args.base_url.clone(),
        api_key_env: match transport {
            OllamaTransport::Local => None,
            OllamaTransport::Cloud => Some(args.api_key_env.clone()),
        },
    }
}

async fn bind_broker() -> Result<TcpListener, Box<dyn Error>> {
    Ok(TcpListener::bind("127.0.0.1:8765").await?)
}

async fn wait_for_browser(
    broker: &BrokerState,
    requested_provider: Option<&str>,
    timeout: Duration,
) -> Result<String, Box<dyn Error>> {
    let deadline = Instant::now() + timeout;

    loop {
        let ids = broker.provider_ids().await;
        if let Some(requested) = requested_provider {
            if ids.iter().any(|id| id == requested) {
                return Ok(requested.to_string());
            }
        } else if let Some(first) = ids.first() {
            return Ok(first.clone());
        }

        if Instant::now() >= deadline {
            let message = match requested_provider {
                Some(provider) => {
                    format!("browser provider '{provider}' did not connect before the timeout")
                }
                None => "no ADP browser extension connected before the timeout".to_string(),
            };
            return Err(message.into());
        }

        sleep(Duration::from_millis(200)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_config_never_requires_an_api_key_env() {
        let args = ModelArgs {
            provider: ProviderArg::Local,
            model: "qwen3:1.7b".to_string(),
            base_url: None,
            api_key_env: "SHOULD_NOT_BE_USED".to_string(),
        };
        let config = model_config(&args);
        assert_eq!(config.transport, OllamaTransport::Local);
        assert_eq!(config.api_key_env, None);
    }

    #[test]
    fn cloud_config_stores_only_the_environment_variable_name() {
        let args = ModelArgs {
            provider: ProviderArg::Cloud,
            model: "gemma4:31b".to_string(),
            base_url: None,
            api_key_env: "OLLAMA_API_KEY".to_string(),
        };
        let config = model_config(&args);
        assert_eq!(config.transport, OllamaTransport::Cloud);
        assert_eq!(config.api_key_env.as_deref(), Some("OLLAMA_API_KEY"));
    }
}
