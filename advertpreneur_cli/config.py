from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from .pricing import DEFAULT_FREE_CLOUD_MODELS, DEFAULT_PRICES, ModelPrice


@dataclass
class ModelProfile:
    provider: str
    model: str
    think: bool | str = False
    max_output_tokens: int = 4096


@dataclass
class Settings:
    default_model: ModelProfile = field(default_factory=lambda: ModelProfile("cloud", "gpt-oss:20b", False, 4096))
    prices: Dict[str, ModelPrice] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    free_cloud_models: set[str] = field(default_factory=lambda: set(DEFAULT_FREE_CLOUD_MODELS))
    cloud_access_mode: str = "free"  # free | all
    task_budget_usd: float = 1.00
    daily_budget_usd: float = 5.00
    max_agent_turns: int = 24
    max_tool_output_chars: int = 10000
    approval_mode: str = "safe"
    auto_index: bool = True
    index_local_model: str = "qwen3:1.7b"
    index_cloud_policy: str = "never"
    auto_compact_tokens: int = 24000
    compact_target_tokens: int = 9000


def default_config_dict() -> Dict[str, Any]:
    p = Settings().default_model
    return {
        "default_model": {
            "provider": p.provider,
            "model": p.model,
            "think": p.think,
            "max_output_tokens": p.max_output_tokens,
        },
        "cloud": {
            "access_mode": "free",
            "free_models": sorted(DEFAULT_FREE_CLOUD_MODELS),
        },
        "budgets": {
            "task_usd": 1.00,
            "daily_usd": 5.00,
        },
        "agent": {
            "max_turns": 24,
            "max_tool_output_chars": 10000,
            "approval_mode": "safe",
        },
        "indexing": {
            "auto": True,
            "local_model": "qwen3:1.7b",
            "cloud_policy": "never"
        },
        "context": {
            "auto_compact_tokens": 24000,
            "compact_target_tokens": 9000
        },
        "prices": {
            model: {
                "input": p.input_per_million,
                "cached_input": p.cached_input_per_million,
                "output": p.output_per_million,
            }
            for model, p in DEFAULT_PRICES.items()
        },
    }


def load_settings(path: Path | None) -> Settings:
    data = default_config_dict()
    if path and path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        _deep_merge(data, user)

    dm = data.get("default_model", {})
    default_model = ModelProfile(
        provider=str(dm.get("provider", "cloud")),
        model=str(dm.get("model", "gpt-oss:20b")),
        think=dm.get("think", False),
        max_output_tokens=int(dm.get("max_output_tokens", 4096)),
    )
    prices = {
        model: ModelPrice(float(v["input"]), float(v.get("cached_input", v["input"])), float(v["output"]))
        for model, v in data.get("prices", {}).items()
    }
    cloud = data.get("cloud", {})
    budgets = data.get("budgets", {})
    agent = data.get("agent", {})
    indexing = data.get("indexing", {})
    context = data.get("context", {})
    access_mode = str(cloud.get("access_mode", "free")).lower()
    if access_mode not in {"free", "all"}:
        access_mode = "free"
    approval = str(agent.get("approval_mode", "safe")).lower()
    if approval not in {"ask", "safe", "full"}:
        approval = "safe"

    return Settings(
        default_model=default_model,
        prices=prices,
        free_cloud_models=set(cloud.get("free_models", DEFAULT_FREE_CLOUD_MODELS)),
        cloud_access_mode=access_mode,
        task_budget_usd=float(budgets.get("task_usd", 1.0)),
        daily_budget_usd=float(budgets.get("daily_usd", 5.0)),
        max_agent_turns=int(agent.get("max_turns", 24)),
        max_tool_output_chars=int(agent.get("max_tool_output_chars", 10000)),
        approval_mode=approval,
        auto_index=bool(indexing.get("auto", True)),
        index_local_model=str(indexing.get("local_model", "qwen3:1.7b")),
        index_cloud_policy="never",
        auto_compact_tokens=int(context.get("auto_compact_tokens", 24000)),
        compact_target_tokens=int(context.get("compact_target_tokens", 9000)),
    )


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def write_default_config(path: Path) -> None:
    path.write_text(json.dumps(default_config_dict(), indent=2), encoding="utf-8")


def get_api_key() -> str | None:
    return os.environ.get("OLLAMA_API_KEY")
