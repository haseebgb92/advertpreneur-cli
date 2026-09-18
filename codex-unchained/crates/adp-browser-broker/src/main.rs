use adp_browser_broker::{BrokerState, serve};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:8765").await?;
    serve(listener, BrokerState::new()).await
}
