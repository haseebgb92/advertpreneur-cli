#!/usr/bin/env python3
"""Make the ADP Unchained provider's local /models endpoint authoritative."""

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
        self.provider_info.is_openai()
    }

    fn has_authoritative_unauthenticated_catalog(&self) -> bool {
        self.provider_info.name == "ADP Unchained Model Router"
    }

    fn identity(&self) -> Option<String> {""",
        "ADP unauthenticated model catalog capability",
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

    /// Whether this provider owns an authoritative catalog that does not need auth.
    fn has_authoritative_unauthenticated_catalog(&self) -> bool {
        false
    }

    /// Fetches the latest remote model catalog and optional ETag.""",
        "model endpoint unauthenticated catalog capability",
    )
    manager_text = replace_once(
        manager_text,
        """    async fn should_refresh_models(&self) -> bool {
        self.endpoint_client.uses_codex_backend().await
            || self.endpoint_client.has_command_auth()
            || self.supports_api_key_discovery()
    }""",
        """    async fn should_refresh_models(&self) -> bool {
        self.endpoint_client
            .has_authoritative_unauthenticated_catalog()
            || self.endpoint_client.uses_codex_backend().await
            || self.endpoint_client.has_command_auth()
            || self.supports_api_key_discovery()
    }""",
        "ADP catalog refresh policy",
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
        """        // ADP, visible ChatGPT, and OpenAI API-key catalogs are authoritative.
        let remote_only = entry
            .models
            .iter()
            .any(|model| model.visibility == ModelVisibility::List)
            && (self
                .endpoint_client
                .has_authoritative_unauthenticated_catalog()
                || self.supports_api_key_discovery()
                || self.auth_manager.as_ref().is_some_and(|auth_manager| {
                    auth_manager
                        .auth_mode()
                        .is_some_and(AuthMode::has_chatgpt_account)
                }));""",
        "ADP catalog authoritative merge policy",
    )
    manager.write_text(manager_text, encoding="utf-8")


if __name__ == "__main__":
    main()
