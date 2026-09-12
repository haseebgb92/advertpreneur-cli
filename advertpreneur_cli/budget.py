from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    requests: int = 0

    def add(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.input_tokens += max(0, int(input_tokens or 0))
        self.output_tokens += max(0, int(output_tokens or 0))
        self.estimated_cost_usd += max(0.0, float(cost or 0.0))
        self.requests += 1


class BudgetTracker:
    def __init__(self, state_path: Path, daily_limit: float, task_limit: float) -> None:
        self.state_path = state_path
        self.daily_limit = daily_limit
        self.task_limit = task_limit
        self.task = Usage()
        self._state = self._load()

    @property
    def today(self) -> str:
        return datetime.now().astimezone().date().isoformat()

    def _load(self) -> Dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"days": {}}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def daily_record(self) -> Dict:
        return self._state.setdefault("days", {}).setdefault(self.today, {
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "requests": 0, "models": {}
        })

    def daily_cost(self) -> float:
        return float(self.daily_record().get("cost_usd", 0.0))

    def all_time(self) -> Usage:
        out = Usage()
        for day in self._state.get("days", {}).values():
            out.input_tokens += int(day.get("input_tokens", 0))
            out.output_tokens += int(day.get("output_tokens", 0))
            out.estimated_cost_usd += float(day.get("cost_usd", 0.0))
            out.requests += int(day.get("requests", 0))
        return out

    def can_request(self) -> tuple[bool, str]:
        if self.task.estimated_cost_usd >= self.task_limit:
            return False, f"Task budget reached (${self.task.estimated_cost_usd:.4f}/${self.task_limit:.2f})."
        if self.daily_cost() >= self.daily_limit:
            return False, f"Daily budget reached (${self.daily_cost():.4f}/${self.daily_limit:.2f})."
        return True, ""

    def record(self, model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.task.add(input_tokens, output_tokens, cost)
        day = self._state.setdefault("days", {}).setdefault(self.today, {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "requests": 0,
            "models": {},
        })
        day["input_tokens"] = int(day.get("input_tokens", 0)) + int(input_tokens or 0)
        day["output_tokens"] = int(day.get("output_tokens", 0)) + int(output_tokens or 0)
        day["cost_usd"] = float(day.get("cost_usd", 0.0)) + float(cost or 0.0)
        day["requests"] = int(day.get("requests", 0)) + 1
        model_state = day.setdefault("models", {}).setdefault(model, {
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "requests": 0
        })
        model_state["input_tokens"] += int(input_tokens or 0)
        model_state["output_tokens"] += int(output_tokens or 0)
        model_state["cost_usd"] += float(cost or 0.0)
        model_state["requests"] += 1
        self._save()

    def reset_task(self) -> None:
        self.task = Usage()
