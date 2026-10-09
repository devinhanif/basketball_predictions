"""Read-only, local-LLM chat assistant over the parlay engine.

The language model can only call engine tools. Every number the user sees is rendered by
Python from tool results; model prose is post-checked and any number the engine did not
produce is stripped. No order code, no credentials, no network except the local Ollama host.
"""

from nba.parlay.assistant.agent import Answer, Assistant
from nba.parlay.assistant.backend import (
    LLMBackend,
    LLMReply,
    OllamaBackend,
    OllamaUnavailable,
    ToolCall,
)
from nba.parlay.assistant.toolbox import EnginePaths, Toolbox

__all__ = [
    "Answer",
    "Assistant",
    "EnginePaths",
    "LLMBackend",
    "LLMReply",
    "OllamaBackend",
    "OllamaUnavailable",
    "ToolCall",
    "Toolbox",
]
