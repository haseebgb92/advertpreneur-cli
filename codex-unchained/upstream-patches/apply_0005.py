#!/usr/bin/env python3
"""Allow explicit Unchained model catalogs to refresh without OpenAI/API-key auth."""

from __future__ import annotations

import pathlib
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one baseline match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_0005.py /path/to/codex")

    root = pathlib.Path(sys.argv[1]).resolve()
    endpoint = root / "codex-rs/model-provider/src/models_endpoint.rs"
    manager = root / "codex-rs/models-manager/src/manager.rs"

    endpoint_text = endpoint.read_text(encoding="utf-8")
    endpoint_text = replace_once(
        endpoint_text,
        """impl ModelsEndpointClient for OpenAiModelsEndpoint {
    fn supports_api_key_models(&self) -> bool {
        self.provider_info.is_openai()
    }

    fn identity(&self) -> Option<String> {""",
        """impl ModelsEndpointClient for OpenAiModelsEndpoint {
    fn supports_api_key_models(&self) -> bool {
        self.provider_info.model_catalog_url.is_some() || self.provider_info.is_openai()
    }

    fn has_explicit_model_catalog(&self) -> bool {
        self.provider_info.model_catalog_url.is_some()
    }

    fn identity(&self) -> Option<String> {""",
        "explicit model catalog endpoint capability",
    )
    endpoint.write_text(endpoint_text, encoding="utf-8")

    manager_text = manager.read_text(encoding="utf-8")
    manager_text = replace_once(
        manager_text,
        """    fn supports_api_key_models(&self) -> bool {
        false
    }

    /// Fetches the latest remote model catalog and optional ETag.""",
        """    fn supports_api_key_models(&self) -> bool {
        false
    }

    /// Returns whether the provider explicitly configured its own model catalog endpoint.
    fn has_explicit_model_catalog(&self) -> bool {
        false
    }

    /// Fetches the latest remote model catalog and optional ETag.""",
        "model endpoint explicit catalog capability",
    )
    manager_text = replace_once(
        manager_text,
        """    async fn should_refresh_models(&self) -> bool {
        self.endpoint_client.uses_codex_backend().await
            || self.endpoint_client.has_command_auth()
            || self.supports_api_key_discovery()
    }""",
        """    async fn should_refresh_models(&self) -> bool {
        self.endpoint_client.has_explicit_model_catalog()
            || self.endpoint_client.uses_codex_backend().await
            || self.endpoint_client.has_command_auth()
            || self.supports_api_key_discovery()
    }""",
        "explicit catalog refresh policy",
    )
    manager_text = replace_once(
        manager_text,
        """        // Visible ChatGPT and OpenAI API-key catalogs are authoritative.
        let remote_only = entry
            .models
            .iter()
            .any(|model| model.visibility == ModelVisibility::List)
            && (self.supports_api_key_discovery()
                || self.auth_manager.as_ref().is_some_and(|auth_manager| {
                    auth_manager
                        .auth_mode()
                        .is_some_and(AuthMode::has_chatgpt_account)
                }));""",
        """        // Explicit provider catalogs, ChatGPT catalogs, and API-key catalogs are authoritative.
        let remote_only = entry
            .models
            .iter()
            .any(|model| model.visibility == ModelVisibility::List)
            && (self.endpoint_client.has_explicit_model_catalog()
                || self.supports_api_key_discovery()
                || self.auth_manager.as_ref().is_some_and(|auth_manager| {
                    auth_manager
                        .auth_mode()
                        .is_some_and(AuthMode::has_chatgpt_account)
                }));""",
        "explicit catalog authoritative merge policy",
    )
    manager.write_text(manager_text, encoding="utf-8")


if __name__ == "__main__":
    main()
