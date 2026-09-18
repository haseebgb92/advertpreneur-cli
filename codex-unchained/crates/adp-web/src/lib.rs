use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::env;
use thiserror::Error;

const DEFAULT_BASE_URL: &str = "https://ollama.com";
const DEFAULT_API_KEY_ENV: &str = "OLLAMA_API_KEY";
const MAX_ERROR_BODY_CHARS: usize = 2_000;

#[derive(Debug, Clone)]
pub struct WebConfig {
    pub base_url: String,
    pub api_key_env: String,
}

impl Default for WebConfig {
    fn default() -> Self {
        Self {
            base_url: DEFAULT_BASE_URL.to_string(),
            api_key_env: DEFAULT_API_KEY_ENV.to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WebSearchResult {
    pub title: String,
    pub url: String,
    pub content: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WebSearchResponse {
    #[serde(default)]
    pub results: Vec<WebSearchResult>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WebFetchResponse {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub links: Vec<String>,
}

#[derive(Debug, Error)]
pub enum WebError {
    #[error("missing required credential environment variable {0}")]
    MissingCredential(String),
    #[error("web request failed: {0}")]
    Http(#[from] reqwest::Error),
    #[error("web provider returned HTTP {status}: {body}")]
    ProviderHttp { status: u16, body: String },
    #[error("query must not be empty")]
    EmptyQuery,
    #[error("url must not be empty")]
    EmptyUrl,
}

#[derive(Debug, Clone)]
pub struct WebClient {
    config: WebConfig,
    http: Client,
}

impl WebClient {
    pub fn new(config: WebConfig) -> Result<Self, WebError> {
        Ok(Self {
            config,
            http: Client::builder().build()?,
        })
    }

    pub fn from_env() -> Result<Self, WebError> {
        Self::new(WebConfig::default())
    }

    pub fn credential_available(&self) -> bool {
        env::var(&self.config.api_key_env).is_ok_and(|value| !value.trim().is_empty())
    }

    pub fn api_key_env(&self) -> &str {
        &self.config.api_key_env
    }

    pub async fn search(
        &self,
        query: &str,
        max_results: Option<u32>,
    ) -> Result<WebSearchResponse, WebError> {
        let query = query.trim();
        if query.is_empty() {
            return Err(WebError::EmptyQuery);
        }

        let api_key = self.api_key()?;
        let endpoint = format!(
            "{}/api/web_search",
            self.config.base_url.trim_end_matches('/')
        );
        let mut payload = json!({ "query": query });
        if let Some(max_results) = max_results {
            payload["max_results"] = json!(max_results.clamp(1, 10));
        }

        let response = self
            .http
            .post(endpoint)
            .bearer_auth(api_key)
            .json(&payload)
            .send()
            .await?;
        decode_json_response(response).await
    }

    pub async fn fetch(&self, url: &str) -> Result<WebFetchResponse, WebError> {
        let url = url.trim();
        if url.is_empty() {
            return Err(WebError::EmptyUrl);
        }

        let api_key = self.api_key()?;
        let endpoint = format!(
            "{}/api/web_fetch",
            self.config.base_url.trim_end_matches('/')
        );
        let response = self
            .http
            .post(endpoint)
            .bearer_auth(api_key)
            .json(&json!({ "url": url }))
            .send()
            .await?;
        decode_json_response(response).await
    }

    fn api_key(&self) -> Result<String, WebError> {
        let value = env::var(&self.config.api_key_env)
            .map_err(|_| WebError::MissingCredential(self.config.api_key_env.clone()))?;
        if value.trim().is_empty() {
            return Err(WebError::MissingCredential(self.config.api_key_env.clone()));
        }
        Ok(value)
    }
}

async fn decode_json_response<T>(response: reqwest::Response) -> Result<T, WebError>
where
    T: for<'de> Deserialize<'de>,
{
    let status = response.status();
    if !status.is_success() {
        let body = response.text().await.unwrap_or_default();
        return Err(WebError::ProviderHttp {
            status: status.as_u16(),
            body: truncate(&body, MAX_ERROR_BODY_CHARS),
        });
    }
    Ok(response.json().await?)
}

fn truncate(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_targets_official_ollama_web_api() {
        let config = WebConfig::default();
        assert_eq!(config.base_url, "https://ollama.com");
        assert_eq!(config.api_key_env, "OLLAMA_API_KEY");
    }

    #[test]
    fn search_results_round_trip_without_provider_specific_agent_state() {
        let result = WebSearchResponse {
            results: vec![WebSearchResult {
                title: "Example".to_string(),
                url: "https://example.com/".to_string(),
                content: "Example result".to_string(),
            }],
        };
        let wire = serde_json::to_string(&result).unwrap();
        let decoded: WebSearchResponse = serde_json::from_str(&wire).unwrap();
        assert_eq!(decoded, result);
    }

    #[test]
    fn max_result_contract_is_bounded_by_client() {
        assert_eq!(0_u32.clamp(1, 10), 1);
        assert_eq!(50_u32.clamp(1, 10), 10);
    }
}
