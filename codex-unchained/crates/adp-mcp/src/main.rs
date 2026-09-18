use adp_agent::{
    BROWSER_BIND_ACTIVE, BROWSER_CLICK, BROWSER_FILL, BROWSER_INSPECT, BROWSER_NAVIGATE,
    BROWSER_SCROLL, BROWSER_WAIT_DOWNLOAD, WEB_FETCH, WEB_SEARCH, browser_tools, web_tools,
};
use adp_browser_bridge::BrowserAction;
use adp_browser_broker::{BrokerState, serve};
use adp_memory::SemanticTarget;
use adp_model_router::{MODEL_ROUTER_ADDR, ModelRouterState, serve as serve_model_router};
use adp_protocol::ToolDescriptor;
use adp_web::WebClient;
use serde_json::{Value, json};
use std::error::Error;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWrite, AsyncWriteExt, BufReader, BufWriter};
use tokio::net::TcpListener;
use tokio::time::{Instant, sleep};

const TOOL_TIMEOUT: Duration = Duration::from_secs(30);
const BROWSER_CONNECT_TIMEOUT: Duration = Duration::from_secs(20);

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let broker = BrokerState::new();
    let listener = TcpListener::bind("127.0.0.1:8765").await?;
    let broker_server = broker.clone();
    tokio::spawn(async move {
        if let Err(error) = serve(listener, broker_server).await {
            eprintln!("ADP MCP browser broker stopped: {error}");
        }
    });

    let model_listener = TcpListener::bind(MODEL_ROUTER_ADDR).await?;
    let model_state = ModelRouterState::new()?;
    tokio::spawn(async move {
        if let Err(error) = serve_model_router(model_listener, model_state).await {
            eprintln!("ADP model router stopped: {error}");
        }
    });

    let web = WebClient::from_env()?;
    run_stdio(broker, web).await
}

async fn run_stdio(broker: BrokerState, web: WebClient) -> Result<(), Box<dyn Error>> {
    let stdin = tokio::io::stdin();
    let stdout = tokio::io::stdout();
    let mut lines = BufReader::new(stdin).lines();
    let mut writer = BufWriter::new(stdout);

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }

        let request: Value = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(error) => {
                write_message(
                    &mut writer,
                    &json!({
                        "jsonrpc": "2.0",
                        "id": Value::Null,
                        "error": {
                            "code": -32700,
                            "message": format!("parse error: {error}")
                        }
                    }),
                )
                .await?;
                continue;
            }
        };

        if let Some(response) = handle_request(&broker, &web, request).await {
            write_message(&mut writer, &response).await?;
        }
    }

    Ok(())
}

async fn write_message<W>(writer: &mut W, message: &Value) -> Result<(), std::io::Error>
where
    W: AsyncWrite + Unpin,
{
    writer
        .write_all(serde_json::to_string(message).unwrap().as_bytes())
        .await?;
    writer.write_all(b"\n").await?;
    writer.flush().await
}

async fn handle_request(broker: &BrokerState, web: &WebClient, request: Value) -> Option<Value> {
    let id = request.get("id").cloned()?;
    let method = request.get("method").and_then(Value::as_str)?;

    let result = match method {
        "initialize" => {
            let protocol_version = request
                .pointer("/params/protocolVersion")
                .cloned()
                .unwrap_or_else(|| json!("2025-06-18"));
            Ok(json!({
                "protocolVersion": protocol_version,
                "capabilities": {
                    "tools": {
                        "listChanged": false
                    }
                },
                "serverInfo": {
                    "name": "adp-unchained",
                    "version": env!("CARGO_PKG_VERSION")
                },
                "instructions": "ADP host tools provide Browser and Web capabilities independently of the model provider. The same host process serves the Unchained model catalog on 127.0.0.1:8766."
            }))
        }
        "ping" => Ok(json!({})),
        "tools/list" => Ok(json!({
            "tools": tool_descriptors()
                .iter()
                .map(mcp_tool_spec)
                .collect::<Vec<_>>()
        })),
        "tools/call" => call_tool(broker, web, request.get("params")).await,
        "resources/list" => Ok(json!({"resources": []})),
        "resources/templates/list" => Ok(json!({"resourceTemplates": []})),
        "prompts/list" => Ok(json!({"prompts": []})),
        _ => Err((-32601, format!("method not found: {method}"))),
    };

    Some(match result {
        Ok(result) => json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": result
        }),
        Err((code, message)) => json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {
                "code": code,
                "message": message
            }
        }),
    })
}

fn tool_descriptors() -> Vec<ToolDescriptor> {
    let mut tools = browser_tools();
    tools.extend(web_tools());
    tools
}

fn mcp_tool_spec(tool: &ToolDescriptor) -> Value {
    json!({
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": {
            "readOnlyHint": matches!(
                tool.name.as_str(),
                BROWSER_INSPECT | BROWSER_SCROLL | BROWSER_WAIT_DOWNLOAD | WEB_SEARCH | WEB_FETCH
            )
        }
    })
}

async fn call_tool(
    broker: &BrokerState,
    web: &WebClient,
    params: Option<&Value>,
) -> Result<Value, (i64, String)> {
    let params = params.ok_or_else(|| (-32602, "missing tools/call params".to_string()))?;
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| (-32602, "tools/call name must be text".to_string()))?;
    let arguments = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));

    let result = match name {
        WEB_SEARCH => {
            let query = required_string(&arguments, "query")?;
            let max_results = arguments
                .get("max_results")
                .and_then(Value::as_u64)
                .map(|value| value.min(u32::MAX as u64) as u32);
            serde_json::to_value(
                web.search(&query, max_results)
                    .await
                    .map_err(|error| (-32001, error.to_string()))?,
            )
            .map_err(|error| (-32603, error.to_string()))?
        }
        WEB_FETCH => {
            let url = required_string(&arguments, "url")?;
            serde_json::to_value(
                web.fetch(&url)
                    .await
                    .map_err(|error| (-32001, error.to_string()))?,
            )
            .map_err(|error| (-32603, error.to_string()))?
        }
        BROWSER_BIND_ACTIVE
        | BROWSER_INSPECT
        | BROWSER_CLICK
        | BROWSER_FILL
        | BROWSER_NAVIGATE
        | BROWSER_SCROLL
        | BROWSER_WAIT_DOWNLOAD => {
            let provider = wait_for_browser(broker).await?;
            let action = browser_action(name, &arguments)?;
            let response = broker
                .call(&provider, None, action, TOOL_TIMEOUT)
                .await
                .map_err(|error| (-32002, error.to_string()))?;
            if !response.ok {
                return Ok(tool_result_error(
                    response
                        .error
                        .unwrap_or_else(|| "browser action failed".to_string()),
                ));
            }
            response.result
        }
        _ => return Err((-32602, format!("unknown ADP tool: {name}"))),
    };

    Ok(tool_result_ok(result))
}

fn browser_action(name: &str, arguments: &Value) -> Result<BrowserAction, (i64, String)> {
    match name {
        BROWSER_BIND_ACTIVE => Ok(BrowserAction::BindActive),
        BROWSER_INSPECT => Ok(BrowserAction::Inspect),
        BROWSER_CLICK => Ok(BrowserAction::Click {
            target: parse_target(arguments)?,
        }),
        BROWSER_FILL => Ok(BrowserAction::Fill {
            target: parse_target(arguments)?,
            value: required_string(arguments, "value")?,
        }),
        BROWSER_NAVIGATE => Ok(BrowserAction::Navigate {
            url: required_string(arguments, "url")?,
        }),
        BROWSER_SCROLL => Ok(BrowserAction::Scroll {
            amount: required_i64(arguments, "amount")?,
        }),
        BROWSER_WAIT_DOWNLOAD => Ok(BrowserAction::WaitForDownload {
            after_id: required_i64(arguments, "after_id")?,
            timeout_ms: arguments
                .get("timeout_ms")
                .and_then(Value::as_u64)
                .unwrap_or(45_000),
        }),
        _ => Err((-32602, format!("unknown browser tool: {name}"))),
    }
}

fn parse_target(arguments: &Value) -> Result<SemanticTarget, (i64, String)> {
    let target = arguments
        .get("target")
        .cloned()
        .ok_or_else(|| (-32602, "missing semantic target".to_string()))?;
    serde_json::from_value(target).map_err(|error| (-32602, error.to_string()))
}

fn required_string(arguments: &Value, name: &str) -> Result<String, (i64, String)> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| (-32602, format!("'{name}' must be a string")))
}

fn required_i64(arguments: &Value, name: &str) -> Result<i64, (i64, String)> {
    arguments
        .get(name)
        .and_then(Value::as_i64)
        .ok_or_else(|| (-32602, format!("'{name}' must be an integer")))
}

async fn wait_for_browser(broker: &BrokerState) -> Result<String, (i64, String)> {
    let deadline = Instant::now() + BROWSER_CONNECT_TIMEOUT;
    loop {
        if let Some(provider) = broker.provider_ids().await.first().cloned() {
            return Ok(provider);
        }
        if Instant::now() >= deadline {
            return Err((
                -32002,
                "no ADP Chrome/Edge extension connected to 127.0.0.1:8765".to_string(),
            ));
        }
        sleep(Duration::from_millis(200)).await;
    }
}

fn tool_result_ok(value: Value) -> Value {
    json!({
        "content": [{
            "type": "text",
            "text": serde_json::to_string(&value).unwrap()
        }],
        "isError": false
    })
}

fn tool_result_error(message: String) -> Value {
    json!({
        "content": [{
            "type": "text",
            "text": message
        }],
        "isError": true
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(id: i64, method: &str, params: Value) -> Value {
        json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params
        })
    }

    #[tokio::test]
    async fn initialize_echoes_client_protocol_version() {
        let broker = BrokerState::new();
        let web = WebClient::from_env().unwrap();
        let response = handle_request(
            &broker,
            &web,
            request(
                1,
                "initialize",
                json!({
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}
                }),
            ),
        )
        .await
        .unwrap();

        assert_eq!(response["result"]["protocolVersion"], "2025-06-18");
        assert_eq!(response["result"]["serverInfo"]["name"], "adp-unchained");
    }

    #[tokio::test]
    async fn tools_list_exposes_browser_and_web_to_codex() {
        let broker = BrokerState::new();
        let web = WebClient::from_env().unwrap();
        let response = handle_request(&broker, &web, request(2, "tools/list", json!({})))
            .await
            .unwrap();

        let names = response["result"]["tools"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|tool| tool["name"].as_str())
            .collect::<Vec<_>>();

        assert!(names.contains(&BROWSER_INSPECT));
        assert!(names.contains(&BROWSER_CLICK));
        assert!(names.contains(&WEB_SEARCH));
        assert!(names.contains(&WEB_FETCH));
    }

    #[test]
    fn browser_fill_is_translated_to_host_action() {
        let action = browser_action(
            BROWSER_FILL,
            &json!({
                "target": {
                    "role": "input",
                    "name": "Search"
                },
                "value": "bee wax wrap"
            }),
        )
        .unwrap();

        assert_eq!(
            action,
            BrowserAction::Fill {
                target: SemanticTarget {
                    role: Some("input".to_string()),
                    name: Some("Search".to_string()),
                    ..Default::default()
                },
                value: "bee wax wrap".to_string()
            }
        );
    }

    #[tokio::test]
    async fn notification_messages_do_not_generate_responses() {
        let broker = BrokerState::new();
        let web = WebClient::from_env().unwrap();
        let response = handle_request(
            &broker,
            &web,
            json!({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {}
            }),
        )
        .await;
        assert!(response.is_none());
    }
}
