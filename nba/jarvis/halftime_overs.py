# ruff: noqa: E501
"""Halftime overs: the in-game line model against the Kalshi Yes ask, as a paper log and one short line.

At each game's halftime the scorer (``nba.markets.halftime_scorer``) runs T508 arm M and the
player-aware baseline glm_e on every on-grid Kalshi player-prop Yes quote and appends the rows to its
own append-only store (``halftime_overs.store``), stamped before the second half starts and refused
after. This module is the JARVIS side: it runs the scorer on the tick, never lets it break the tick,
and renders at most ``top_n`` lines for the check-in. UNPROVEN, paper, display only: the model has no
live track record (draft rule docs/prereg/HALFTIME_OVERS_VS_PRICE.md is not frozen), the market usually
knows more, and no stake is sized or suggested. Over / Yes only; nothing here reads an outcome.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.jarvis.config import JarvisConfig
from nba.jarvis.live import live_dir

log = logging.getLogger("nba.jarvis.halftime_overs")

STAT_LABEL = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "3PM"}
_CACHE: dict[tuple[str, str], Any] = {}


def settings(cfg: JarvisConfig) -> dict[str, Any]:
    return dict(cfg.halftime_overs)


def enabled(cfg: JarvisConfig) -> bool:
    return bool(cfg.halftime_overs.get("enabled", False))


def _resolve(cfg: JarvisConfig, key: str) -> Path:
    p = Path(str(cfg.halftime_overs[key]))
    return p if p.is_absolute() else cfg.root / p


def score_halftimes(
    cfg: JarvisConfig,
    slate: date,
    now: datetime,
    games: list[str],
    *,
    dry_run: bool,
    nba_db: Path | None = None,
) -> dict[str, Any]:
    """Score and store the halftime games named in ``games`` ("AWAY@HOME", Kalshi abbreviations).

    Never raises: any failure is returned as ``{"ok": False, "error": ...}`` so the tick goes on.
    ``dry_run`` scores into an in-memory store (nothing persists).
    """
    if not enabled(cfg) or not games:
        return {"ok": True, "skipped": "disabled or no halftime games"}
    try:
        from nba.markets.halftime_model import load_artifact
        from nba.markets.halftime_scorer import RecencyProvider, score_night
        from nba.markets.halftime_store import HalftimeStore

        s = settings(cfg)
        art_path = _resolve(cfg, "artifact")
        if not art_path.exists():
            return {"ok": False, "error": "artifact missing", "path": str(art_path)}
        ck = ("art", f"{art_path}:{art_path.stat().st_mtime_ns}")
        if ck not in _CACHE:
            _CACHE[ck] = load_artifact(art_path)
        art = _CACHE[ck]
        db = nba_db or cfg.paths["nba_db"]
        mgd = s.get("recency_min_game_date")
        mgd_d = None if mgd in (None, "") else date.fromisoformat(str(mgd))
        rk = ("rec", f"{db}:{slate}:{mgd}")
        if rk not in _CACHE:
            _CACHE[rk] = RecencyProvider(db, slate, min_game_date=mgd_d)
        rec = _CACHE[rk]
        store = HalftimeStore.open(":memory:" if dry_run else _resolve(cfg, "store"))
        try:
            rep = score_night(
                live_dir(cfg, slate),
                art,
                rec,
                cfg.fees["kalshi"],
                store,
                slate,
                now,
                dict(s["fee_pin"]),
                only_games=set(games),
            )
            rows = store.rows(slate_date=slate)
        finally:
            store.close()
        return {
            "ok": True,
            "stored": rep.stored,
            "counts": dict(rep.counts),
            "refused": rep.refused,
            "rows": rows,
        }
    except Exception as exc:  # fail soft: a scorer problem never costs a check-in or the tick
        log.warning("halftime overs failed (%s: %s)", type(exc).__name__, str(exc)[:120])
        return {"ok": False, "error": type(exc).__name__}


def top_overs(
    rows: list[dict[str, Any]], game: str, min_edge: float, top_n: int
) -> list[dict[str, Any]]:
    """Best strike per (player, stat) in one game with model edge >= ``min_edge``, largest first."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r["game"] != game or r["edge_model"] < min_edge - 1e-12:
            continue
        key = (r["player_name"], r["stat"])
        if key not in best or (r["edge_model"], -r["k"]) > (
            best[key]["edge_model"],
            -best[key]["k"],
        ):
            best[key] = r
    return sorted(best.values(), key=lambda r: (-r["edge_model"], r["player_name"]))[:top_n]


def overs_lines(game: str, picks: list[dict[str, Any]]) -> list[str]:
    """The Discord text: a label, then at most a few 'model P vs ask + fee' lines. No stake."""
    if not picks:
        return []
    out = [f"**JARVIS halftime overs {game}** (UNPROVEN, paper; the market usually knows more)"]
    for r in picks:
        out.append(
            f"- {r['player_name']} {r['k']}+ {STAT_LABEL[r['stat']]} (Yes): "
            f"model {round(100 * r['p_model'])}% vs ask {r['ask_c']}c + {r['fee_c']}c fee"
        )
    out.append("No track record yet; shown for the paper log only, no stake suggested.")
    return out
