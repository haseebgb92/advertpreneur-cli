use adp_agent::AgentRuntime;
use adp_browser_broker::{BrokerState, serve};
use adp_executor::{ReplayOutcome, WorkflowExecutor};
use adp_memory::ProjectMemory;
use adp_model::{OllamaClient, OllamaConfig, OllamaTransport};
use adp_protocol::ExecutionContext;
use adp_teach::{TeachConfig, TeachRecordOutcome, TeachRecorder};
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
    /// Run a model conversation, optionally with the existing browser as a tool.
    Chat(ChatArgs),
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
    #[arg(long, default_value_t = 20)]
    browser_wait_seconds: u64,
    prompt: String,
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
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let cli = Cli::parse();

    match cli.command {
        Command::Broker => run_broker().await?,
        Command::Models(args) => list_models(args).await?,
        Command::Doctor(args) => doctor(args).await?,
        Command::Chat(args) => chat(args).await?,
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

    let mut agent = AgentRuntime::new(&model, &broker);
    if let Some(provider_id) = browser_provider {
        println!("Browser runtime connected: {provider_id}");
        agent = agent.with_browser_provider(provider_id);
    }

    let result = agent.run(&args.prompt).await?;
    if !result.final_text.is_empty() {
        println!("{}", result.final_text);
    }
    eprintln!(
        "model_turns={} tool_calls={}",
        result.model_turns, result.tool_calls
    );
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
    let project_root = match args.project {
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

    match outcome {
        ReplayOutcome::Completed { steps_executed } => {
            println!(
                "{}",
                json!({
                    "status": "completed",
                    "workflow": workflow.name,
                    "steps_executed": steps_executed,
                    "model_calls": 0
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
                    "workflow": workflow.name,
                    "steps_executed": steps_executed,
                    "divergence": divergence
                })
            );
        }
    }

    Ok(())
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
                Some(provider) => format!(
                    "browser provider '{provider}' did not connect before the timeout"
                ),
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
