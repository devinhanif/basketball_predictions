# ruff: noqa: E501
"""In-game checkpoints: the line model at every 5 game-minutes, as a paper log and a silent card block.

The JARVIS side of ``nba.markets.checkpoint_scorer``. On each tick it runs the scorer for the games in
play (nothing is read that the capture did not already hold; no request is made), appends rows to the
sibling paper store, and builds a small per-game summary of the LATEST checkpoint for the game card
(``nba.jarvis.cards``), which shows it by editing the card in place. An edit never notifies anyone.

This module defines no alert, sends nothing and has no stake: it never calls the delivery functions of
``nba.jarvis.runner`` (a test greps for them). UNPROVEN: the model has no live track record, the market
usually knows more; if an edge exists the T508 attack points at 30+ game-minutes, none is shown; earlier marks are
descriptive. Over / Yes only; no outcome is read.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.jarvis import halftime_overs as HO
from nba.jarvis.config import JarvisConfig
from nba.jarvis.live import live_dir

log = logging.getLogger("nba.jarvis.ingame_checkpoints")

PRIMARY_MIN = (
    30  # mirrors nba.markets.checkpoint_scorer.PRIMARY_MIN (the pre-declared late-game family)
)


def settings(cfg: JarvisConfig) -> dict[str, Any]:
    return dict(cfg.ingame_checkpoints)


def enabled(cfg: JarvisConfig) -> bool:
    return bool(cfg.ingame_checkpoints.get("enabled", False)) and HO.enabled(cfg)


def build_views(
    rows_by_game: dict[str, list[dict[str, Any]]],
    min_edge: float,
    top_n: int,
    min_bid_c: int,
    max_spread_c: int,
) -> dict[str, dict[str, Any]]:
    """The newest checkpoint of each game as plain data for the card: counts and the best few overs
    by model edge (same display filter as the halftime line; the paper table keeps every row)."""
    out: dict[str, dict[str, Any]] = {}
    for game, rows in rows_by_game.items():
        if not rows:
            continue
        r0 = max(rows, key=lambda r: (r["checkpoint"], r["known_at"]))
        top = HO.top_overs(rows, game, min_edge, top_n, min_bid_c, max_spread_c)
        units = {(r["player_name"], r["stat"]) for r in rows}
        flagged = {
            (r["player_name"], r["stat"]) for r in rows if r["edge_model"] >= min_edge - 1e-12
        }
        out[game] = {
            "game": game,
            "checkpoint": int(r0["checkpoint"]),
            "e_used": float(r0["e_used"]),
            "period": int(r0["period"]),
            "clock": str(r0["clock"]),
            "known_at": r0["known_at"],
            "support": str(r0["model_support"]),
            "primary": int(r0["checkpoint"]) >= PRIMARY_MIN,
            "n_units": len(units),
            "n_flagged": len(flagged),
            "top": [
                {
                    k: r[k]
                    for k in (
                        "player_name",
                        "stat",
                        "k",
                        "p_model",
                        "p_glm_e",
                        "ask_c",
                        "bid_c",
                        "fee_c",
                    )
                }
                for r in top
            ],
        }
    return out


def score_checkpoints(
    cfg: JarvisConfig,
    slate: date,
    now: datetime,
    games: list[str],
    *,
    dry_run: bool,
    nba_db: Path | None = None,
) -> dict[str, Any]:
    """Score and store the checkpoints the games in ``games`` ("AWAY@HOME") have reached.

    Never raises: any failure comes back as ``{"ok": False, "error": ...}``. ``dry_run`` scores into an
    in-memory store (nothing persists, and a restart there rescores: the idempotence is the file's).
    """
    if not enabled(cfg) or not games:
        return {"ok": True, "skipped": "disabled or no games in play", "latest": {}}
    try:
        from nba.markets.checkpoint_scorer import score_night_checkpoints
        from nba.markets.checkpoint_store import CheckpointStore
        from nba.markets.halftime_model import load_artifact
        from nba.markets.halftime_scorer import RecencyProvider

        h = HO.settings(cfg)
        s = settings(cfg)
        art_path = HO._resolve(cfg, "artifact")
        if not art_path.exists():
            return {"ok": False, "error": "artifact missing", "latest": {}}
        ck = ("art", f"{art_path}:{art_path.stat().st_mtime_ns}")
        if ck not in HO._CACHE:
            HO._CACHE[ck] = load_artifact(art_path)
        art = HO._CACHE[ck]
        if art.digest != str(h["artifact_digest"]):  # the served model is the pinned one
            return {"ok": False, "error": "artifact digest mismatch", "latest": {}}
        db = nba_db or cfg.paths["nba_db"]
        mgd = h.get("recency_min_game_date")
        mgd_d = None if mgd in (None, "") else date.fromisoformat(str(mgd))
        rk = ("rec", f"{db}:{slate}:{mgd}")
        if rk not in HO._CACHE:
            HO._CACHE[rk] = RecencyProvider(db, slate, min_game_date=mgd_d)
        rec = HO._CACHE[rk]
        sp = Path(str(s["store"]))
        store = CheckpointStore.open(
            ":memory:" if dry_run else (sp if sp.is_absolute() else cfg.root / sp), mode="live"
        )
        try:
            rep = score_night_checkpoints(
                live_dir(cfg, slate), art, rec, cfg.fees["kalshi"], store, slate, now,
                dict(h["fee_pin"]), only_games=set(games),
            )  # fmt: skip
            rows_by_game = store.latest_by_game(slate)
            latest = build_views(
                rows_by_game, float(h["min_edge"]), int(s.get("top_n", 3)),
                int(h["display_min_bid_c"]), int(h["display_max_spread_c"]),
            )  # fmt: skip
        finally:
            store.close()
        refused = {k: v for k, v in rep.counts.items() if k in _REFUSALS}
        snap = publish_snapshot(cfg, slate, now, rows_by_game, rep.latest_states, dry_run=dry_run)
        return {
            "ok": True, "stored": rep.stored, "fires": rep.counts.get("fires", 0),
            "refused": refused, "latest": latest, "snapshot": snap,
        }  # fmt: skip
    except Exception as exc:  # fail soft: a scorer problem never costs a check-in or the tick
        log.warning("ingame checkpoints failed (%s: %s)", type(exc).__name__, str(exc)[:120])
        return {"ok": False, "error": type(exc).__name__, "latest": {}}


def publish_snapshot(
    cfg: JarvisConfig,
    slate: date,
    now: datetime,
    rows_by_game: dict[str, list[dict[str, Any]]],
    states: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Write the app's snapshot file (docs/contracts/ingame_checkpoint_snapshot_v2.md): only when the
    flag is on (the caller returned earlier otherwise), never in a dry run, atomic temp + rename, and
    never raises (``nba.markets.checkpoint_snapshot.publish`` contains every exception)."""
    if dry_run:
        return {"ok": True, "skipped": "dry_run"}
    try:
        from nba.markets import checkpoint_snapshot as CS

        s = settings(cfg)
        out = Path(str(s.get("snapshot", "data/jarvis/ingame_checkpoints_latest.json")))
        out = out if out.is_absolute() else cfg.root / out
        pre = CS.pregame_path(
            cfg.root, slate, str(s.get("script_priors_dir", "data/export/latest"))
        )
        return CS.publish(out, pre, rows_by_game, states, now)
    except Exception as exc:  # noqa: BLE001 - the snapshot never costs the tick
        return {"ok": False, "error": type(exc).__name__}


_REFUSALS = (
    "checkpoint_missed_late", "state_stale", "state_no_plays", "state_not_live", "overtime_excluded",
    "game_over_before_quote", "refused_late_write", "series_no_page_in_window", "tick_not_closed",
)  # fmt: skip
