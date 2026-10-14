"""Writers behind one interface, and the router that picks one by how hard the job is.

* ``TemplateProvider`` (the default): deterministic text from the packet. Always available.
* ``ClaudeProvider``: a stub. It is DISABLED unless the key variable exists in the environment
  (the key is never read from, or written to, any file). Even with a key it makes no call:
  ``write`` raises ``NotWired`` until Devin decides to turn model calls on. No network client is
  imported in this package.

Routing by difficulty ("jev engineering", docs/APP_ARCHITECTURE.md section 9): one leg is an easy
job and goes to the cheap model; a whole ticket is a hard one and goes to the strong model. The
model names come from the environment (``PARLAYHITTERS_MODEL_CHEAP`` / ``_STRONG``), not from the
code, because which model to buy is Devin's decision. A tier with no model configured is not
routed to the stub at all; the template answers it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from . import template
from .packet import Packet
from .types import Tier

KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV: dict[str, str] = {
    "cheap": "PARLAYHITTERS_MODEL_CHEAP",
    "strong": "PARLAYHITTERS_MODEL_STRONG",
}
#: difficulty -> model tier. One leg is easy; a whole ticket (legs that interact) is hard.
TIER_ROUTE: dict[Tier, str] = {"leg": "cheap", "parlay": "strong"}


class ProviderError(Exception):
    """A provider could not write this text (disabled, unconfigured, or not wired)."""


class NotWired(ProviderError):
    """Model calls are not turned on: Devin's decision, not a build step."""


@dataclass(frozen=True)
class Written:
    text: str
    model: str | None


class Provider(Protocol):
    name: str

    def enabled_for(self, tier: Tier) -> bool: ...

    def write(self, packet: Packet, tier: Tier) -> Written: ...


class TemplateProvider:
    name = "template"

    def enabled_for(self, tier: Tier) -> bool:
        return True

    def write(self, packet: Packet, tier: Tier) -> Written:
        text = template.write_leg(packet) if tier == "leg" else template.write_parlay(packet)
        return Written(text=text, model=None)


class ClaudeProvider:
    """Disabled unless the key variable is set; routes by tier; makes no call in this build."""

    name = "claude"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    @property
    def enabled(self) -> bool:
        return bool(self._env.get(KEY_ENV))

    def model_for(self, tier: Tier) -> str | None:
        return self._env.get(MODEL_ENV[TIER_ROUTE[tier]]) or None

    def enabled_for(self, tier: Tier) -> bool:
        return self.enabled and self.model_for(tier) is not None

    def write(self, packet: Packet, tier: Tier) -> Written:
        if not self.enabled:
            raise ProviderError(f"disabled: {KEY_ENV} is not set")
        model = self.model_for(tier)
        if model is None:
            raise ProviderError(f"no model configured for the {TIER_ROUTE[tier]} tier")
        raise NotWired(f"model calls are not turned on (would route to {model})")


@dataclass(frozen=True)
class Router:
    """The first provider that is enabled for the tier; the template is always last."""

    providers: tuple[Provider, ...]

    def pick(self, tier: Tier) -> Provider:
        for p in self.providers:
            if p.enabled_for(tier):
                return p
        return TemplateProvider()


def default_router(env: Mapping[str, str] | None = None) -> Router:
    return Router((ClaudeProvider(env), TemplateProvider()))
