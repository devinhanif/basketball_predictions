"""LLM backend Protocol and the local Ollama implementation (httpx, no new packages).

An optional hosted backend could satisfy the same Protocol later; none is implemented
here (a paid API needs explicit maintainer approval).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

INSTALL_HELP = (
    "Install and start Ollama, then pull a 3B-class model:\n"
    "  brew install ollama\n"
    "  ollama serve            # keep running in another terminal\n"
    "  ollama pull qwen2.5:3b\n"
    "Set OLLAMA_HOST if it is not at the default http://localhost:11434."
)


class OllamaUnavailable(RuntimeError):
    """Ollama is not reachable (or the model is missing). Message says how to fix it."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMBackend(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply: ...


def _norm_host(host: str) -> str:
    h = host.strip().rstrip("/")
    return h if "://" in h else f"http://{h}"


class OllamaBackend:
    """``POST {host}/api/chat`` with tool calling; temperature 0 and a fixed seed."""

    def __init__(
        self,
        model: str,
        host: str,
        *,
        seed: int = 0,
        timeout_s: float = 300.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.host = _norm_host(host)
        self.seed = seed
        self._client = client or httpx.Client(timeout=timeout_s)

    def _down(self, why: str) -> OllamaUnavailable:
        return OllamaUnavailable(f"Ollama is not reachable at {self.host} ({why}).\n{INSTALL_HELP}")

    def ping(self) -> None:
        """Raise ``OllamaUnavailable`` unless the server is up and has ``self.model``."""
        try:
            r = self._client.get(f"{self.host}/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise self._down(type(e).__name__) from e
        names = {str(m.get("name", "")) for m in r.json().get("models", [])}
        want = self.model if ":" in self.model else f"{self.model}:latest"
        if want not in names:
            raise OllamaUnavailable(
                f"Ollama is running but model {self.model!r} is not pulled. Run: "
                f"ollama pull {self.model}"
            )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0, "seed": self.seed},
        }
        try:
            r = self._client.post(f"{self.host}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise self._down(type(e).__name__) from e
        if r.status_code == 404:
            raise OllamaUnavailable(
                f"Ollama has no model {self.model!r}. Run: ollama pull {self.model}"
            )
        if r.status_code >= 400:
            raise OllamaUnavailable(f"Ollama returned HTTP {r.status_code}: {r.text[:200]}")
        msg = r.json().get("message", {})
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_unparseable": args}
            calls.append(ToolCall(str(fn.get("name", "")), args if isinstance(args, dict) else {}))
        return LLMReply(str(msg.get("content") or ""), calls)
