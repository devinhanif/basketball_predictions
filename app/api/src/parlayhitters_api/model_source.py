"""Which model a number came from, in plain words.

The nightly export marks one row per (game, player, stat) as the primary one (best_model_schema 1)
and says why: the AVG model, or the production model standing in for it. This module turns those
stored fields into the sentence shown beside a number. It invents nothing: every phrase is chosen
by a stored field, and a field it does not know is shown as stored, never guessed.

Sources: ``avg`` | ``fallback_missing`` | ``fallback_stale`` | ``postseason`` (from the export),
``legacy`` (an older export with no model choice recorded; the app keeps showing the production
model there) and ``fixture`` (the historical fixture slates, which store only a stamp).
"""

from __future__ import annotations

from datetime import date
from typing import Any

Json = dict[str, Any]

AVG_NAME = "AVG (sequence + recent-role blend)"
STALE_WHY = {
    "writer_flag": "the writer flagged the bundle as stale",
    "month_block_mismatch": "the bundle is not the one for this game's month",
    "installed_after_made": "the bundle was installed after the forecast was made",
    "fit_through_not_before_month": "the bundle was fit on data from the game's own month",
    "avg_run_behind_production": "the AVG forecast is older than the production one",
    "avg_inputs_changed": "inputs changed since it ran",
}
P_PLAY_R = "availability model R"
R_WHY = {
    "no_r_row": "R stored no number for him",
    "out_on_report": "R has no number because he is out on the injury report",
    "r_run_behind_production": "R's number is older than production's",
}


def month_name(bundle_month: str | None) -> str | None:
    """'2026-10' as 'October 2026'; None when it does not parse."""
    if not bundle_month:
        return None
    try:
        y, m = (int(x) for x in str(bundle_month).split("-"))
        return date(y, m, 1).strftime("%B %Y")
    except ValueError:
        return None


def model_label(info: Json | None) -> tuple[str, str]:
    """(source key, one plain sentence) for a forecast's ``model`` info dict."""
    if not info:
        return (
            "fixture",
            "Model: out-of-fold forecast from a historical season (no model choice is recorded "
            "in this data).",
        )
    src = str(info.get("source"))
    prod = "production model"
    if src == "avg":
        month = month_name(info.get("bundle_month"))
        bundle = f"{month} bundle" if month else "bundle month not stored"
        return src, f"Model: {AVG_NAME}, {bundle}."
    if src == "fallback_stale":
        raw = str(info.get("detail") or info.get("freshness_reason") or "reason not stored")
        is_bundle = raw.startswith("bundle stale: ")
        raw = raw.removeprefix("bundle stale: ")
        why = STALE_WHY.get(raw, raw)
        lead = "stale bundle, " if is_bundle else ""
        return src, f"Model: {prod} (AVG unavailable: {lead}{why})."
    if src == "non_regular_season":
        return src, f"Model: {prod} (AVG covers regular-season games only)."
    if src == "fallback_missing":
        return src, f"Model: {prod} (AVG unavailable: no AVG forecast was stored before tip)."
    if src == "postseason":
        return src, f"Model: {prod} (postseason: AVG was no better in the playoffs)."
    if src == "legacy":
        return src, f"Model: {prod} (an older export that does not record a model choice)."
    return src, f"Model: {prod} (source {src})."


def model_detail(info: Json | None) -> str | None:
    """The stored model name and version, verbatim (for a tooltip, not for the sentence)."""
    if not info or not info.get("name"):
        return None
    return f"{info['name']} {info.get('version') or ''}".strip()


def p_play_label(info: Json | None) -> str | None:
    """The sentence naming where a chance-to-play came from; None when there is no such number."""
    if not info or info.get("value") is None:
        return None
    src = info.get("source")
    if src == "r":
        return f"Chance to play: from the {P_PLAY_R}."
    if src == "out_on_report":
        return "Chance to play: out on the official injury report."
    if src == "production":
        d = info.get("detail")
        if d == "r_run_behind_production":
            return (
                f"Chance to play: from the production model; the {P_PLAY_R} number was older "
                "than production's."
            )
        extra = f" ({R_WHY.get(str(d), str(d))})" if d else ""
        return f"Chance to play: from the production model; {P_PLAY_R} had no number{extra}."
    if src == "legacy":
        return "Chance to play: as stored with the production forecast (older export)."
    return f"Chance to play: source {src}."
