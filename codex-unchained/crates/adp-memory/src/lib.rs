
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use thiserror::Error;
use url::Url;

pub const TEACH_KEYWORD: &str = "[TEACH_KEYWORD]";

#[derive(Debug, Error)]
pub enum MemoryError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid workflow name")]
    InvalidWorkflowName,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct SemanticTarget {
    pub role: Option<String>,
    pub name: Option<String>,
    pub test_id: Option<String>,
    pub aria_label: Option<String>,
    pub css: Option<String>,
    pub near: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Verification {
    pub kind: String,
    #[serde(default)]
    pub expected: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkflowStep {
    pub id: String,
    pub action: String,
    #[serde(default)]
    pub tab: String,
    #[serde(default)]
    pub target: Option<SemanticTarget>,
    #[serde(default)]
    pub args: Value,
    #[serde(default)]
    pub verify: Vec<Verification>,
    #[serde(default)]
    pub safe_for_deterministic_replay: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkflowRepair {
    pub observed_signature: String,
    pub step_id: String,
    pub previous_target: Option<SemanticTarget>,
    pub repaired_target: Option<SemanticTarget>,
    pub verified: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Workflow {
    pub schema_version: u32,
    pub name: String,
    pub version: u32,
    #[serde(default)]
    pub protected_tabs: Value,
    #[serde(default)]
    pub steps: Vec<WorkflowStep>,
    #[serde(default)]
    pub repairs: Vec<WorkflowRepair>,
}

impl Workflow {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            schema_version: 1,
            name: name.into(),
            version: 1,
            protected_tabs: Value::Object(Default::default()),
            steps: Vec::new(),
            repairs: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageFingerprint {
    pub safe_url: String,
    pub title: String,
    pub digest: String,
}

impl PageFingerprint {
    pub fn from_observation(url: &str, title: &str, landmarks: &[String]) -> Self {
        let safe_url = safe_url(url).unwrap_or_default();
        let title = normalize_text(title, 200);
        let mut normalized_landmarks: Vec<String> = landmarks
            .iter()
            .map(|value| normalize_text(value, 200))
            .filter(|value| !value.is_empty())
            .collect();
        normalized_landmarks.sort();
        normalized_landmarks.dedup();

        let mut hasher = Sha256::new();
        hasher.update(safe_url.as_bytes());
        hasher.update(b"\n");
        hasher.update(title.as_bytes());
        for landmark in normalized_landmarks {
            hasher.update(b"\n");
            hasher.update(landmark.as_bytes());
        }

        Self {
            safe_url,
            title,
            digest: hex::encode(hasher.finalize()),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct RunStats {
    pub runs: u64,
    pub deterministic_runs: u64,
    pub local_model_assists: u64,
    pub primary_model_escalations: u64,
    pub learned_repairs: u64,
    pub successful_runs: u64,
}

#[derive(Debug, Clone)]
pub struct ProjectMemory {
    root: PathBuf,
}

impl ProjectMemory {
    pub fn open(project_root: impl AsRef<Path>) -> Result<Self, MemoryError> {
        let root = project_root.as_ref().join(".advertpreneur");
        for child in [
            "workflows",
            "changes",
            "decisions",
            "checkpoints",
            "knowledge",
            "cache",
        ] {
            fs::create_dir_all(root.join(child))?;
        }
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn save_workflow(&self, workflow: &Workflow) -> Result<PathBuf, MemoryError> {
        let slug = workflow_slug(&workflow.name)?;
        let path = self.root.join("workflows").join(format!("{slug}.json"));
        atomic_json_write(&path, workflow)?;
        Ok(path)
    }

    pub fn load_workflow(&self, name: &str) -> Result<Workflow, MemoryError> {
        let slug = workflow_slug(name)?;
        let bytes = fs::read(self.root.join("workflows").join(format!("{slug}.json")))?;
        Ok(serde_json::from_slice(&bytes)?)
    }

    pub fn save_run_stats(&self, workflow_name: &str, stats: &RunStats) -> Result<PathBuf, MemoryError> {
        let slug = workflow_slug(workflow_name)?;
        let path = self.root.join("workflows").join(format!("{slug}.stats.json"));
        atomic_json_write(&path, stats)?;
        Ok(path)
    }
}

pub fn safe_url(input: &str) -> Option<String> {
    let mut url = Url::parse(input).ok()?;
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    Some(url.to_string())
}

pub fn sanitize_fill(selector_hint: &str, value: &str) -> Option<String> {
    let hint = selector_hint.to_ascii_lowercase();
    const SENSITIVE: &[&str] = &[
        "password", "passwd", "secret", "token", "otp", "mfa", "pin", "cvv",
        "card", "auth", "passcode",
    ];
    if SENSITIVE.iter().any(|needle| hint.contains(needle)) {
        return None;
    }
    const PARAMETERIZED: &[&str] = &["search", "query", "keyword"];
    if PARAMETERIZED.iter().any(|needle| hint.contains(needle)) {
        return Some(TEACH_KEYWORD.to_string());
    }
    let _ = value;
    None
}

fn normalize_text(value: &str, max: usize) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(max).collect()
}

fn workflow_slug(name: &str) -> Result<String, MemoryError> {
    let mut out = String::new();
    for ch in name.trim().to_ascii_lowercase().chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
        } else if matches!(ch, '-' | '_' | ' ') && !out.ends_with('-') {
            out.push('-');
        }
        if out.len() >= 80 {
            break;
        }
    }
    let out = out.trim_matches('-').to_string();
    if out.is_empty() {
        Err(MemoryError::InvalidWorkflowName)
    } else {
        Ok(out)
    }
}

fn atomic_json_write(path: &Path, value: &impl Serialize) -> Result<(), MemoryError> {
    let tmp = path.with_extension("tmp");
    let bytes = serde_json::to_vec_pretty(value)?;
    fs::write(&tmp, bytes)?;
    fs::rename(tmp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_query_fragment_and_basic_auth() {
        let safe = safe_url("https://user:pass@example.com/a/b?q=secret#frag").unwrap();
        assert!(!safe.contains("user"));
        assert!(!safe.contains("pass"));
        assert!(!safe.contains("q="));
        assert!(!safe.contains("#"));
        assert!(safe.contains("example.com/a/b"));
    }

    #[test]
    fn teach_fill_keeps_only_parameter_marker() {
        assert_eq!(
            sanitize_fill("input[name=keyword]", "private search"),
            Some(TEACH_KEYWORD.to_string())
        );
        assert_eq!(sanitize_fill("#password", "hunter2"), None);
        assert_eq!(sanitize_fill("#shipping-address", "somewhere"), None);
    }

    #[test]
    fn fingerprints_ignore_query_values() {
        let a = PageFingerprint::from_observation(
            "https://example.com/search?q=one",
            " Results ",
            &["Export".into(), "Load more".into()],
        );
        let b = PageFingerprint::from_observation(
            "https://example.com/search?q=two",
            "Results",
            &["Load more".into(), "Export".into()],
        );
        assert_eq!(a.digest, b.digest);
    }
}
