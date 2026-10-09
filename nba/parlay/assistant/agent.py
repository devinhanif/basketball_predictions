"""Tool-calling loop. Final answer = Python-rendered engine results + checked LLM prose."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nba.parlay.assistant.backend import LLMBackend
from nba.parlay.assistant.numguard import check_prose, collect_numbers
from nba.parlay.assistant.render import DISCLAIMER, render_result
from nba.parlay.assistant.toolbox import Toolbox

SYSTEM_PROMPT = """You are a read-only analyst for a same-game-parlay probability engine.
Rules:
- You have tools. You cannot compute or recall probabilities, EVs, prices or fees yourself.
  Never write a number that a tool did not return. If you need a number, call a tool.
- Lead with uncertainty: state the probability interval and that estimates are noisy.
- Always report the verdict verbatim (e.g. no_positive_ev_found), ev_low, fees and sample sizes.
- Never recommend placing a real-money bet or sizing one. This is analysis only.
- If a tool returns an error, say so plainly and ask the user to rephrase; never guess a player.
- Disagreement with the market is a hypothesis to test, not a signal to trust.
- Questions like "I have $20, what is the best EV?" use best_for_budget; "win at least $T" uses
  best_for_target. Report its answer verbatim (e.g. "keep your money"); never size a bet.
- Keep the explanation short (a few sentences). The detailed table is rendered separately.
Today's slate date is {date}.
Legs: stat in pts/reb/ast/fg3m (player >= threshold), win, spread, total."""


@dataclass
class ToolRun:
    name: str
    args: dict[str, Any]
    result: dict[str, Any]


@dataclass
class Answer:
    text: str
    runs: list[ToolRun] = field(default_factory=list)
    prose_raw: str = ""
    n_removed: int = 0


class Transcript:
    """Append-only JSONL audit log of the question, tool calls, tool outputs and final text."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def log(self, kind: str, **fields: Any) -> None:
        if self.path is None:
            return
        rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "type": kind, **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")


class Assistant:
    def __init__(
        self,
        backend: LLMBackend,
        toolbox: Toolbox,
        *,
        max_steps: int = 6,
        transcript: Path | None = None,
        on_tool: Callable[[ToolRun], None] | None = None,
    ) -> None:
        self.backend = backend
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.transcript = Transcript(transcript)
        self.on_tool = on_tool
        self._engine_numbers: list[float] = []
        self._messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(date=toolbox.default_date.isoformat()),
            }
        ]

    def _trim(self) -> None:
        while len(self._messages) > 40 and len(self._messages) > 2:
            del self._messages[1]
            while len(self._messages) > 1 and self._messages[1]["role"] != "user":
                del self._messages[1]

    def ask(self, question: str) -> Answer:
        self._messages.append({"role": "user", "content": question})
        self.transcript.log("user", content=question)
        user_nums = [float(t) for t in _numbers_in(question)]
        runs: list[ToolRun] = []
        prose = ""
        tools = self.toolbox.tool_specs()
        for _ in range(self.max_steps):
            reply = self.backend.chat(self._messages, tools)
            self.transcript.log(
                "assistant",
                content=reply.content,
                tool_calls=[{"name": c.name, "arguments": c.arguments} for c in reply.tool_calls],
            )
            if not reply.tool_calls:
                prose = reply.content
                self._messages.append({"role": "assistant", "content": reply.content})
                break
            self._messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": c.arguments}}
                        for c in reply.tool_calls
                    ],
                }
            )
            for c in reply.tool_calls:
                res = self.toolbox.call(c.name, c.arguments)
                run = ToolRun(c.name, c.arguments, res)
                runs.append(run)
                self._engine_numbers += collect_numbers(res) + collect_numbers(c.arguments)
                self.transcript.log("tool", name=c.name, arguments=c.arguments, result=res)
                if self.on_tool:
                    self.on_tool(run)
                self._messages.append(
                    {"role": "tool", "tool_name": c.name, "content": json.dumps(res, default=str)}
                )
        else:
            prose = "(stopped: tool-call step limit reached)"
        self._trim()
        clean, n_removed = check_prose(prose, [*self._engine_numbers, *user_nums])
        text = _compose(runs, clean, n_removed)
        self.transcript.log("final", text=text, prose_raw=prose, n_removed=n_removed)
        return Answer(text, runs, prose, n_removed)


def _numbers_in(text: str) -> list[str]:
    import re

    return re.findall(r"\d+(?:\.\d+)?", text)


def _compose(runs: list[ToolRun], prose: str, n_removed: int) -> str:
    parts = ["=== Engine results (rendered by Python from tool outputs) ==="]
    if runs:
        parts += [render_result(r.name, r.result) for r in runs]
    else:
        parts.append("(no engine tool was called, so no engine numbers are shown)")
    parts.append(
        "=== Explanation (language-model prose; numbers checked against engine output) ==="
    )
    parts.append(prose.strip() or "(no explanation produced)")
    if n_removed:
        parts.append(
            f"[{n_removed} item(s) removed from the prose: numbers the engine did not "
            "produce, or betting recommendations]"
        )
    parts.append(f"-- {DISCLAIMER}")
    return "\n".join(parts)
