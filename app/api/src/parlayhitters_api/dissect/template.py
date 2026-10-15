"""Template writer: a fact packet in, two to four plain sentences out. No model, no randomness.

Every number printed here is formatted straight from a packet leaf with the same rounding the
guard reads back (one decimal for averages and minutes, whole percent for chances), so the output
passes numguard by construction. Nothing is computed here; counts come from the packet.
The words never recommend a bet: the model's number is reported, and so are the reasons it may
be wrong (small samples, a forecast that stops short of the line, recent results far from it).
"""

from __future__ import annotations

from typing import Any

from .packet import Packet

MAX_SENTENCES = 4


def _g(x: float) -> str:
    return f"{x:g}"  # 24.5 stays 24.5; a whole-number line prints without a decimal


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _stat_words(packet: Packet) -> str:
    return str(packet["stat_label"]).lower()


def _form(p: Packet) -> str:
    name, side = p["player"], p["side"]
    l10, l5, win = p["last10"], p["last5"], p["windows"]
    if p["flags"]["no_history"]:
        return (
            f"We have no earlier games in our data for {name}, so there is no recent record "
            "to show against this line."
        )
    small = (
        f" (a small sample: fewer than {win['small_below']} games)"
        if p["flags"]["small_last10"]
        else ""
    )
    s = (
        f"{name} went {side} {_g(p['line'])} {_stat_words(p)} in {l10['hits']} of his last "
        f"{l10['n']} games{small}, averaging {l10['avg']:.1f}"
    )
    if l5["n"] > 0 and l5["n"] != l10["n"]:
        s += f", and in {l5['hits']} of his last {l5['n']}"
    return s + "."


def _model(p: Packet) -> str:
    m = p["model"]
    if not m["available"] or m["p_hit"] is None:
        return (
            "We have no model forecast for this stat, so there is nothing to set these recent "
            "games against."
        )
    notes: list[str] = []
    if p["line_kind"] != "posted":
        notes.append("a reference line of ours, not a sportsbook line")
    if p["flags"]["extrapolated"]:
        notes.append("the forecast does not reach this far into the tail")
    aside = f" ({'; '.join(notes)})" if notes else ""
    s = f"Our model gives {_pct(m['p_hit'])} to go {p['side']} {_g(p['line'])}{aside}"
    if m["typical"] is not None:
        s += f", with a typical night near {m['typical']:.1f}"
    return s + "."


def _status(p: Packet) -> str | None:
    st = p["flags"]["player_status"]
    if st in ("out", "doubtful", "questionable"):
        return (
            f"{p['player']} is listed {st} on the latest injury report, and every forecast "
            "here assumes he plays."
        )
    return None


def _disagree(p: Packet) -> str | None:
    """Name both rates. The difference between two rates is in percentage points, not basketball
    points, so it is never printed (F02, app audit 2026-10-15)."""
    f, m, l10 = p["flags"], p["model"], p["last10"]
    if not f["disagree"] or m["p_hit"] is None or l10["rate"] is None:
        return None
    return (
        f"He went {p['side']} in {_pct(l10['rate'])} of his last {l10['n']} games, against "
        f"{_pct(m['p_hit'])} from the model; the two measure different things, and neither is a "
        "promise."
    )


def _teammate(p: Packet) -> str | None:
    if not p["teammates_out"]:
        return None
    t = p["teammates_out"][0]
    if t["n_without"] == 0:
        return (
            f"{t['name']} is {t['status']}, but our data has no earlier game without him to "
            "compare against."
        )
    small = ", a small sample" if t["small"] else ""
    return (
        f"{t['name']} is {t['status']}; in {t['n_without']} earlier games without him "
        f"{p['player']} averaged {t['avg_without']:.1f}{small}."
    )


def _minutes(p: Packet) -> str | None:
    mn = p["minutes"]
    if mn["avg"] is None:
        return None
    s = f"He averaged {mn['avg']:.1f} minutes over his last {mn['n']} games"
    if mn["projected"] is not None:
        s += f", and the model projects about {mn['projected']:.1f}"
    return s + "."


def _opponent(p: Packet) -> str | None:
    o = p["opponent_allowed"]
    if o is None:
        return None
    return (
        f"{o['opp']} allow {o['avg_allowed']:.1f} {_stat_words(p)} a game to opposing "
        f"{o['position_noun']}, rank {o['rank']} of {o['of']} over {o['n_games']} games."
    )


def leg_sentences(packet: Packet) -> list[str]:
    """Two to four sentences: the recent record, the model, then the most useful caveats and
    context in a fixed priority (injury status, disagreement, teammate out, minutes, opponent)."""
    sentences = [_form(packet), _model(packet)]
    extras = (_status, _disagree, _teammate, _minutes, _opponent)
    for fn in extras:
        if len(sentences) >= MAX_SENTENCES:
            break
        s = fn(packet)
        if s:
            sentences.append(s)
    return sentences


def write_leg(packet: Packet) -> str:
    return " ".join(leg_sentences(packet))


def parlay_sentences(packet: dict[str, Any]) -> list[str]:
    """A ticket of several legs: which legs, how many have a forecast, and why the chances are not
    multiplied. ``packet`` is built by ``service.parlay_packet`` from leg packets."""
    legs = packet["legs"]
    names = "; ".join(
        f"{leg['player']} {leg['side']} {_g(leg['line'])} {leg['stat_label'].lower()}"
        for leg in legs
    )
    s1 = f"This ticket has {packet['n_legs']} legs: {names}."
    s2 = f"Our model has a forecast for {packet['n_with_model']} of them" + (
        "."
        if packet["n_with_model"] == packet["n_legs"]
        else f", and none for the other {packet['n_without_model']}."
    )
    s3 = (
        "Every leg has to hit, and legs from one game move together, so we do not multiply the "
        "chances into a single number."
    )
    return [s1, s2, s3]


def write_parlay(packet: dict[str, Any]) -> str:
    return " ".join(parlay_sentences(packet))
