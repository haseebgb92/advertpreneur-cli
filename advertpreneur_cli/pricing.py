from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float

    def estimate(self, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
        cached = max(0, min(cached_input_tokens, input_tokens))
        uncached = max(0, input_tokens - cached)
        return (
            uncached * self.input_per_million / 1_000_000
            + cached * self.cached_input_per_million / 1_000_000
            + output_tokens * self.output_per_million / 1_000_000
        )


# Snapshot of Ollama pricing verified on 2026-09-03.
# Prices are a usage-value meter even when the user's plan includes starter/monthly credits.
DEFAULT_PRICES: Dict[str, ModelPrice] = {
    "deepseek-v4-flash": ModelPrice(0.44, 0.014, 1.32),
    "deepseek-v4-pro": ModelPrice(1.32, 0.044, 3.96),
    "gemma4": ModelPrice(0.14, 0.05, 0.40),
    "glm-5.3": ModelPrice(1.40, 0.26, 4.40),
    "glm-5.3-flash": ModelPrice(0.15, 0.03, 0.50),
    "glm-5.2": ModelPrice(1.40, 0.26, 4.40),
    "glm-5.1": ModelPrice(1.00, 0.20, 3.20),
    "gpt-oss:120b": ModelPrice(0.15, 0.014, 0.60),
    "gpt-oss:20b": ModelPrice(0.07, 0.035, 0.30),
    "kimi-k3": ModelPrice(3.00, 0.30, 15.00),
    "kimi-k2.7-code": ModelPrice(0.95, 0.19, 4.00),
    "kimi-k2.6": ModelPrice(0.95, 0.16, 4.00),
    "minimax-m3": ModelPrice(0.60, 0.12, 2.40),
    "minimax-m2.7": ModelPrice(0.30, 0.06, 1.20),
    "mistral-large-3": ModelPrice(0.50, 0.50, 1.50),
    "nemotron-3-nano": ModelPrice(0.06, 0.06, 0.24),
    "nemotron-3-super": ModelPrice(0.015, 0.015, 0.60),
    "nemotron-3-ultra": ModelPrice(0.10, 0.10, 3.00),
    "qwen3.5:397b": ModelPrice(0.60, 0.60, 3.60),
}


# Free-plan starter models observed in the user's Ollama account on 2026-09-03.
# Kept explicit so the CLI cannot silently move from free starter usage to a paid model.
DEFAULT_FREE_CLOUD_MODELS = {
    "gemma4:31b",
    "gpt-oss:120b",
    "gpt-oss:20b",
    "nemotron-3-nano:30b",
    "nemotron-3-super",
    "nemotron-3-ultra",
}


def strip_cloud_suffix(model: str) -> str:
    name = model.strip()
    if name.endswith(":cloud"):
        name = name[:-6]
    if name.endswith("-cloud"):
        name = name[:-6]
    return name


def lookup_price(prices: Mapping[str, ModelPrice], model: str) -> ModelPrice | None:
    """Resolve exact names plus Ollama variant tags such as gemma4:31b or deepseek-v4-flash:0731."""
    name = strip_cloud_suffix(model)
    if name in prices:
        return prices[name]

    # Match the longest configured family prefix, so gemma4:31b -> gemma4 and
    # deepseek-v4-flash:0731 -> deepseek-v4-flash while preserving exact gpt-oss tags.
    matches = [key for key in prices if name.startswith(key + ":")]
    if matches:
        return prices[max(matches, key=len)]
    return None


def is_free_cloud_model(model: str, free_models: set[str]) -> bool:
    return strip_cloud_suffix(model) in free_models
