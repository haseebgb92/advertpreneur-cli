from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class OllamaError(RuntimeError):
    pass


@dataclass
class ChatResult:
    message: Dict[str, Any]
    input_tokens: int
    output_tokens: int
    total_duration_ns: int
    raw: Dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    def __init__(self, provider: str, api_key: str | None = None, timeout: int = 120) -> None:
        if provider not in {"cloud", "local"}:
            raise ValueError("provider must be cloud or local")
        self.provider = provider
        self.api_key = api_key
        self.timeout = timeout
        self.base = "https://ollama.com/api" if provider == "cloud" else "http://localhost:11434/api"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "AdvertpreneurCLI/0.26"}
        if self.provider == "cloud":
            if not self.api_key:
                raise OllamaError("Ollama Cloud is not logged in. Use /login in Advertpreneur CLI.")
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, path: str, payload: Dict[str, Any] | None = None, method: str = "POST") -> Dict[str, Any]:
        url = self.base + path
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
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
        on_chunk: Optional[Callable[[str, str], None]] = None,
        should_yield: Optional[Callable[[], bool]] = None,
    ) -> ChatResult:
        """Stream Ollama chat response with immediate cancellation support and token streaming."""
        options: Dict[str, Any] = {"num_predict": max_output_tokens}
        
        # Only pass think when strictly requested as a boolean or high effort for reasoning models
        think_param: bool | None = None
        if isinstance(think, bool):
            think_param = think
        elif isinstance(think, str) and think.lower() in {"true", "1", "high"}:
            think_param = True
        elif isinstance(think, str) and think.lower() in {"false", "0", "low", "off"}:
            think_param = False

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options,
        }
        if tools:
            payload["tools"] = tools
        if think_param is not None:
            payload["think"] = think_param

        url = self.base + "/chat"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")

        accumulated_content = []
        accumulated_thinking = []
        accumulated_tool_calls: List[Dict[str, Any]] = []
        prompt_eval_count = 0
        eval_count = 0
        total_duration = 0
        final_raw: Dict[str, Any] = {}

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    if should_yield and should_yield():
                        break
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue
                    try:
                        chunk = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get("message", {})
                    c_delta = str(msg.get("content") or "")
                    t_delta = str(msg.get("thinking") or "")
                    t_calls = msg.get("tool_calls") or []

                    if c_delta:
                        accumulated_content.append(c_delta)
                    if t_delta:
                        accumulated_thinking.append(t_delta)
                    if t_calls:
                        accumulated_tool_calls.extend(t_calls)

                    if on_chunk and (c_delta or t_delta):
                        on_chunk(c_delta, t_delta)

                    if chunk.get("done"):
                        prompt_eval_count = int(chunk.get("prompt_eval_count") or 0)
                        eval_count = int(chunk.get("eval_count") or len(accumulated_content))
                        total_duration = int(chunk.get("total_duration") or 0)
                        final_raw = chunk
                        break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama HTTP {exc.code}: {detail[:1200]}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"Could not reach Ollama ({self.base}): {exc.reason}") from exc

        assembled_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(accumulated_content),
        }
        if accumulated_thinking:
            assembled_msg["thinking"] = "".join(accumulated_thinking)
        if accumulated_tool_calls:
            assembled_msg["tool_calls"] = accumulated_tool_calls

        return ChatResult(
            message=assembled_msg,
            input_tokens=prompt_eval_count,
            output_tokens=eval_count if eval_count else max(1, len(assembled_msg["content"]) // 4),
            total_duration_ns=total_duration,
            raw=final_raw,
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
