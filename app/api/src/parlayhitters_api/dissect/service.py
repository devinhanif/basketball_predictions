"""Put it together: packet -> routed provider -> numguard -> text; the template is the backstop."""

from __future__ import annotations

from ..breakdown import Breakdown
from .numguard import check_text
from .packet import Packet, build_packet
from .providers import Provider, ProviderError, Router, TemplateProvider, default_router
from .types import Dissection, Tier


def _attempt(provider: Provider, packet: Packet, tier: Tier) -> tuple[str, str | None, int] | str:
    """(text, model, numbers checked) if the provider wrote text that passed the guard, else the
    reason it did not."""
    try:
        w = provider.write(packet, tier)
    except ProviderError as exc:
        return f"{provider.name}: {exc}"
    g = check_text(w.text, packet)
    if not g.ok:
        return f"{provider.name}: rejected by numguard ({g.reason()})"
    return w.text, w.model, g.checked


def dissect(packet: Packet, tier: Tier, router: Router | None = None) -> Dissection:
    """Write the dissection for a packet. Whatever the routed provider returns must pass the
    guard; if it does not (or cannot run), the template writes it instead, and the reason is
    reported. If even the template fails the guard the text is empty rather than wrong."""
    rt = router or default_router()
    first = rt.pick(tier)
    used: Provider = first
    rejected: list[str] = []
    out = _attempt(first, packet, tier)
    if isinstance(out, str):
        rejected.append(out)
        if first.name != TemplateProvider.name:
            used = TemplateProvider()
            out = _attempt(used, packet, tier)
            if isinstance(out, str):
                rejected.append(out)
    fell_back = used.name != first.name
    if isinstance(out, str):
        return Dissection(
            text="",
            tier=tier,
            provider=used.name,
            model=None,
            guard_ok=False,
            numbers_checked=0,
            fell_back=fell_back,
            rejected=rejected,
        )
    text, model, checked = out
    return Dissection(
        text=text,
        tier=tier,
        provider=used.name,
        model=model,
        guard_ok=True,
        numbers_checked=checked,
        fell_back=fell_back,
        rejected=rejected,
    )


def dissect_leg(b: Breakdown, router: Router | None = None) -> Dissection:
    """The per-leg text (easy tier: cheap model when one is turned on, else the template)."""
    return dissect(build_packet(b), "leg", router)


def parlay_packet(legs: list[Packet]) -> Packet:
    """A ticket's packet from its legs' packets: only the fields a ticket sentence needs."""
    with_model = [x for x in legs if x["model"]["available"]]
    return {
        "n_legs": len(legs),
        "n_with_model": len(with_model),
        "n_without_model": len(legs) - len(with_model),
        "legs": [
            {
                "player": x["player"],
                "side": x["side"],
                "line": x["line"],
                "stat_label": x["stat_label"],
                "p_hit": x["model"]["p_hit"],
            }
            for x in legs
        ],
    }


def dissect_parlay(legs: list[Packet], router: Router | None = None) -> Dissection:
    """The full-ticket text (hard tier: strong model when one is turned on, else the template)."""
    return dissect(parlay_packet(legs), "parlay", router)
