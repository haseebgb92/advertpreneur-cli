from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


class OllamaError(RuntimeError):
    pass


@dataclass
class ChatResult:
    message: Dict[str, Any]
    input_tokens: int
    output_tokens: int
    total_duration_ns: int
    raw: Dict[str, Any]


class OllamaClient:
    def __init__(self, provider: str, api_key: str | None = None, timeout: int = 300) -> None:
        if provider not in {"cloud", "local"}:
            raise ValueError("provider must be cloud or local")
        self.provider = provider
        self.api_key = api_key
        self.timeout = timeout
        self.base = "https://ollama.com/api" if provider == "cloud" else "http://localhost:11434/api"

    def _request(self, path: str, payload: Dict[str, Any] | None = None, method: str = "POST") -> Dict[str, Any]:
        url = self.base + path
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "AdvertpreneurCLI/0.12"}
        if self.provider == "cloud":
            if not self.api_key:
                raise OllamaError("Ollama Cloud is not logged in. Use /login in Advertpreneur CLI.")
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama HTTP {exc.code}: {detail[:1200]}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama ({self.base}): {exc.reason}") from exc

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        think: bool | str = False,
        max_output_tokens: int = 4096,
    ) -> ChatResult:
        options: Dict[str, Any] = {"num_predict": max_output_tokens}
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": think,
            "options": options,
        }
        raw = self._request("/chat", payload)
        return ChatResult(
            message=raw.get("message", {}),
            input_tokens=int(raw.get("prompt_eval_count") or 0),
            output_tokens=int(raw.get("eval_count") or 0),
            total_duration_ns=int(raw.get("total_duration") or 0),
            raw=raw,
        )

    def list_models(self) -> List[str]:
        raw = self._request("/tags", None, method="GET")
        models = raw.get("models", [])
        names = []
        for item in models:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if name:
                    names.append(str(name))
        return sorted(set(names))
