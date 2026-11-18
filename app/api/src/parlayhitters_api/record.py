"""Prediction record and distribution: the stored provenance and distribution of the forecast the
app shows as primary (the store's own selection), and the time, lead and model of every other
stored snapshot. Stored fields only; the one thing worked out here is the T-60 boolean, read off
the stored lead. Nothing is multiplied, priced or re-derived; a piece that is not stored is null
and named in ``missing``. Contracts: docs/contracts/record_v1.md, distribution_v1.md.
"""

from __future__ import annotations

from typing import Any

from .forecast import TAUS
from .honesty_data import FIXTURE_REASON, T60_MINUTES, Records, Snap
from .honesty_models import (
    ConditionalPieces,
    Distribution,
    PlayStored,
    Record,
    SnapshotRef,
    Support,
    SurvivalStored,
)
from .scenarios import LOCK_TEXT
from .trackrecord import MODEL_LOCK_FROM

Json = dict[str, Any]

T60_RULE = (
    "Meets T-60 when the stored lead (tip-off minus made_at) is at least 60 minutes: the forward "
    "scoring rule's cutoff (docs/FORWARD_PREREG_2026_27.md:37-39; nba/export/reader.py:8)."
)
SNAPSHOTS_NOTE = (
    "Every stored forecast for the same prop or game, by time, lead and model. Their numbers are "
    "never served on this route."
)
KALSHI_DNP_NOTE = (
    "On Kalshi, a player who withdraws before tip-off does not settle as No: the market resolves "
    "at the last fair price, at the exchange's discretion (Kalshi BASKETBALLENTITYSTAT terms, read "
    "2026-11-10). A player who plays at least one second settles on his end-of-game stats. "
    "Sportsbooks have their own rules."
)


def _meets(lead: float | None) -> bool | None:
    return None if lead is None else lead >= T60_MINUTES


def _locked(g: Json) -> bool:
    return bool(g.get("final")) and str(g.get("date", "")) >= MODEL_LOCK_FROM


def _snaps(rows: list[Snap]) -> list[SnapshotRef]:
    out: list[SnapshotRef] = []
    for made, lead, model, version, primary, comparison, latest in rows:
        out.append(
            SnapshotRef(
                made_at=made,
                lead_minutes=lead,
                meets_t60=_meets(lead),
                model_name=model,
                version=version,
                is_primary=primary,
                comparison_only=comparison,
                is_latest=latest,
            )  # fmt: skip
        )
    return out


def _empty_record(kind: str, gid: str, pid: int | None, stat: str | None, reason: str) -> Record:
    return Record(
        kind=kind, game_id=gid, player_id=pid, stat=stat, available=False, reason=reason,
        model_name=None, version=None, weights_sha256=None, weights_sha256_short=None,
        weights_scope=None, fit_through=None, n_train=None, made_at=None, tipoff=None,
        lead_minutes=None, meets_t60=None, t60_rule=T60_RULE, data_version=None, git_sha=None,
        git_dirty=None, stamped_at=None, primary_source=None, primary_detail=None,
        fallback_reason=None, source_table=None, bundle=None, snapshots=[],
        snapshots_note=SNAPSHOTS_NOTE, missing=["model stamp", "stored snapshots"],
    )  # fmt: skip


def _record(kind: str, gid: str, pid: int | None, stat: str | None, r: Json,
            snaps: list[Snap]) -> Record:  # fmt: skip
    st = r["stamp"]
    sha = st.get("weights_sha256")
    missing = [f"stamp.{k}" for k, v in st.items() if v is None]
    bundle = None
    if kind == "prop" and any(r.get(k) is not None for k in ("bundle_id", "bundle_month")):
        keys = ("bundle_id", "bundle_month", "bundle_made_at", "freshness", "freshness_reason")
        bundle = {k: r.get(k) for k in keys}
    if kind == "prop" and r.get("primary_source") is None:
        missing.append("primary_source")
    return Record(
        kind=kind,
        game_id=gid,
        player_id=pid,
        stat=stat,
        available=True,
        reason=None,
        model_name=r["model_name"],
        version=r["version"],
        weights_sha256=sha,
        weights_sha256_short=None if sha is None else str(sha)[:12],
        weights_scope=st.get("weights_scope"),
        fit_through=st.get("fit_through"),
        n_train=None if st.get("n_train") is None else int(st["n_train"]),
        made_at=r["made_at"],
        tipoff=r["tipoff"],
        lead_minutes=r["lead_minutes"],
        meets_t60=_meets(r["lead_minutes"]),
        t60_rule=T60_RULE,
        data_version=st.get("data_version"),
        git_sha=st.get("git_sha"),
        git_dirty=st.get("git_dirty"),
        stamped_at=st.get("stamped_at"),
        primary_source=r.get("primary_source"),
        primary_detail=r.get("primary_detail"),
        fallback_reason=r.get("fallback_reason"),
        source_table=r.get("source_table"),
        bundle=bundle,
        snapshots=_snaps(snaps),
        snapshots_note=SNAPSHOTS_NOTE,
        missing=missing,
    )


def prop_record(records: Records | None, gid: str, pid: int, stat: str) -> Record | None:
    """None when the export holds no primary row for the prop (the route answers no_forecast)."""
    if records is None:
        return _empty_record("prop", gid, pid, stat, FIXTURE_REASON)
    r = records.props.get((gid, pid, stat))
    if r is None:
        return None
    return _record("prop", gid, pid, stat, r, records.prop_snaps.get((gid, pid, stat), []))


def win_record(records: Records | None, gid: str) -> Record | None:
    if records is None:
        return _empty_record("game_winner", gid, None, None, FIXTURE_REASON)
    r = records.wins.get(gid)
    if r is None:
        return None
    return _record("game_winner", gid, None, None, r, records.win_snaps.get(gid, []))


# --------------------------------------------------------------------------- distribution


def _support(q: list[float] | None, has_full: bool) -> Support | None:
    if q is None:
        return None
    int_q = all(float(v).is_integer() for v in q)
    words = (
        "The stored quantiles are whole numbers"
        if int_q
        else "The stored quantiles are continuous values"
    ) + (
        "; the stored survival table gives P(stat >= k) for whole numbers k (integer support)."
        if has_full
        else "; no integer survival table is stored for this row."
    )
    return Support(
        q_grid="integer" if int_q else "continuous",
        survival="integer" if has_full else None,
        words=words,
    )


def _float_map(d: Any) -> dict[str, float] | None:
    if not isinstance(d, dict):
        return None
    try:
        return {str(k): float(v) for k, v in d.items()}
    except (TypeError, ValueError):
        return None


def distribution(
    records: Records | None, g: Json, pid: int, stat: str, forecast: Json | None
) -> Distribution:
    """The stored distribution of the primary row. ``forecast`` is the store's shown forecast
    (fixture mode has no record; its stored grid, mean and chance to play are served instead)."""
    gid = str(g["game_id"])
    locked = _locked(g)
    r = None if records is None else records.props.get((gid, pid, stat))
    missing: list[str] = []
    if r is None:
        if forecast is None:
            raise KeyError((gid, pid, stat))
        surv = forecast.get("surv")
        pp = forecast.get("p_play_info") or None
        r = {
            "model_name": (forecast.get("model") or {}).get("name"),
            "version": (forecast.get("model") or {}).get("version"),
            "made_at": None, "mean": forecast.get("mean"), "std": None,
            "q_grid": [float(v) for v in forecast["q"]],
            "p_ge_full_counts": None if not surv else surv["c"],
            "p_ge_full_n": None if not surv else surv["n"],
            "p_play_primary": forecast.get("p_play"),
            "p_play_source": None if not isinstance(pp, dict) else pp.get("source"),
            "proj_minutes": forecast.get("proj_minutes"), "p_play_row": None, "p_ge": None,
            "p_ge_uncond": None, "mean_uncond": None,
        }  # fmt: skip
    pp_detail = None
    if forecast is not None and isinstance(forecast.get("p_play_info"), dict):
        pp_detail = forecast["p_play_info"].get("detail")
    full = (
        SurvivalStored(
            n=int(r["p_ge_full_n"]),
            counts=[int(c) for c in r["p_ge_full_counts"]],
            k_from=1,
            meaning="P(stat >= k) = counts[k-1] / n for k = 1..len(counts); 0 beyond, 1 at k = 0",
        )  # fmt: skip
        if r.get("p_ge_full_counts") and r.get("p_ge_full_n")
        else None
    )
    for name, val in (("std", r.get("std")), ("p_ge_full", full),
                      ("proj_minutes", r.get("proj_minutes"))):  # fmt: skip
        if val is None:
            missing.append(name)
    play = PlayStored(value=r.get("p_play_primary"), source=r.get("p_play_source"),
                      detail=pp_detail)  # fmt: skip
    if play.value is None:
        missing.append("p_play")
    given = _float_map(r.get("p_ge"))
    uncond = _float_map(r.get("p_ge_uncond"))
    pieces = {"p_play_used": r.get("p_play_row"), "p_ge_given_plays": given,
              "p_ge_unconditional": uncond}  # fmt: skip
    for k, v in pieces.items():
        if v is None:
            missing.append(f"conditional.{k}")
    conditional = (
        ConditionalPieces(
            p_play_used=r.get("p_play_row"),
            p_ge_given_plays=given,
            p_ge_unconditional=uncond,
            mean_given_plays=r.get("mean"),
            mean_unconditional=r.get("mean_uncond"),
            source=(
                "Stored on the row: p_play (the chance to play the row was built with), p_ge (the "
                "threshold chances given he plays) and p_ge_uncond (the row's own product of the "
                "two; nba/props/forward.py:700-717). The app multiplies nothing. p_play_used can "
                "differ from the chance to play shown elsewhere, which comes from the availability "
                "model R when it is stored."
            ),
        )  # fmt: skip
        if all(v is not None for v in pieces.values())
        else None
    )
    q = None if r.get("q_grid") is None else [float(v) for v in r["q_grid"]]
    if locked:
        return Distribution(
            game_id=gid, player_id=pid, stat=stat, available=True, locked=True,
            lock_text=LOCK_TEXT, model_name=r.get("model_name"), version=r.get("version"),
            made_at=r.get("made_at"), taus=list(TAUS), q_grid=None, mean=None, std=None,
            support=None, p_ge_full=None, p_play=None, proj_minutes=None, conditional=None,
            kalshi_dnp_note=KALSHI_DNP_NOTE,
            missing=["withheld: finished game inside the locked test window"],
        )  # fmt: skip
    return Distribution(
        game_id=gid, player_id=pid, stat=stat, available=True, locked=False, lock_text=None,
        model_name=r.get("model_name"), version=r.get("version"), made_at=r.get("made_at"),
        taus=list(TAUS), q_grid=q, mean=r.get("mean"), std=r.get("std"),
        support=_support(q, full is not None), p_ge_full=full, p_play=play,
        proj_minutes=r.get("proj_minutes"), conditional=conditional,
        kalshi_dnp_note=KALSHI_DNP_NOTE, missing=missing,
    )  # fmt: skip
