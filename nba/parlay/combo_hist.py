# ruff: noqa: E501
"""Season-2026 script history and the as-of ``mu_t`` for the combo payload (descriptive counts only).

ONE source of past games for both numbers: the rows of ``nba.export.combo_stage.history_rows`` (the
``games`` table of the live season: ``season = 2026``, dated before the slate, final, not preseason or
all-star; season 2025 is never read). The nightly export passes those rows; the offline rebuild
(``nba.parlay.combo_night``) passes the same query run on a read-only copy of the source database, or,
when it has only an export copy, rows of the same shape built from that copy's season-2026 box scores
(it says so in ``build_log``). Nothing here opens a database.

``mu_t`` of tonight's game is ``GameModelParams.total_mu(slate_total_feature(...))`` on those rows, the
number GAME_SCRIPTS_FORWARD scores (its rule line 2), rounded to 2 decimals by ``build_game_payload``.
Each PAST game's pre-game ``mu_t`` is the same feature computed only from games dated before it
(``asof_total_features`` processes dates in order: the same arithmetic as ``slate_total_feature`` on that
game's own date), rounded to 2 decimals. Script membership of a past game uses the combo contract's own
definitions (``shared.scripts.definitions``) on the final score: blowout ``|margin| >= 15``, close
``|margin| <= 5``, shootout ``total >= ceil(mu_t + 12)``, grind ``total <= floor(mu_t - 12)``.

Player game logs come from season-2026 box rows (``minutes > 0`` is a game played; a NULL or 0 is a
DNP), dated strictly before the slate, with the game id's season digits re-checked (``00X 26 #####``:
a row labelled 2026 with a 2025 id is dropped, as are preseason and all-star ids). Counts only: ``n``
games, ``k`` of them over tonight's main line. No rate, no smoothing, no blend with the model chance.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

import polars as pl

from nba.parlay.game_model import GameModelParams, asof_total_features, slate_total_feature

LIVE_SEASON = 2026
SCRIPTS = ("blowout", "close", "shootout", "grind")
MU_T_LIVE = "live-season games only"
MU_T_FALLBACK = "league fallback"
STATS = ("pts", "reb", "ast", "fg3m")


def season_id_ok(game_id: str) -> bool:
    """A live-season id (``00X 26 #####``) that is not preseason (001) or all-star (003)."""
    g = str(game_id)
    return g[3:5] == f"{LIVE_SEASON % 100:02d}" and not g.startswith(("001", "003"))


def hist_frame(rows: Sequence[Sequence[Any]]) -> pl.DataFrame:
    """The past-games frame from ``history_rows``-shaped tuples
    ``(game_id, game_date, home_team, away_team, home_pts, away_pts)``."""
    return pl.DataFrame(
        {
            "game_id": [str(r[0]) for r in rows],
            "game_date": [r[1] for r in rows],
            "home_team": [int(r[2]) for r in rows],
            "away_team": [int(r[3]) for r in rows],
            "home_pts": [float(r[4]) for r in rows],
            "away_pts": [float(r[5]) for r in rows],
        },
        schema={
            "game_id": pl.Utf8, "game_date": pl.Date, "home_team": pl.Int64,
            "away_team": pl.Int64, "home_pts": pl.Float64, "away_pts": pl.Float64,
        },
    )  # fmt: skip


def game_mu_t(
    frame: pl.DataFrame | None, gm: GameModelParams, home: int, away: int, day: date
) -> tuple[float, str]:
    """(unrounded ``mu_t``, source) for one game of ``day``: the quantity GAME_SCRIPTS_FORWARD scores,
    before the payload's 2-decimal rounding. No frame, or no completed game, is the league fallback."""
    feat = None
    if frame is not None and frame.height:
        feat = slate_total_feature(frame, gm, int(home), int(away), day)
    return gm.total_mu(feat), (MU_T_FALLBACK if feat is None else MU_T_LIVE)


def past_scripts(
    frame: pl.DataFrame | None, gm: GameModelParams, definitions: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Per past game: ``{"mu_t": x, "src": ..., "blowout": bool, ...}`` (pre-game mu_t, 2 dp)."""
    if frame is None or frame.height == 0:
        return {}
    feats = asof_total_features(frame, gm.halflife, gm.prior_k)
    f_by = dict(zip(feats["game_id"].to_list(), feats["total_feat"].to_list(), strict=True))
    bl, cl = float(definitions["blowout_abs_margin"]), float(definitions["close_abs_margin"])
    delta = float(definitions["total_delta"])
    out: dict[str, dict[str, Any]] = {}
    for r in frame.iter_rows(named=True):
        feat = f_by[r["game_id"]]
        mu_t = round(gm.total_mu(feat), 2)
        hp, ap = int(round(r["home_pts"])), int(round(r["away_pts"]))
        margin, total = abs(hp - ap), hp + ap
        out[str(r["game_id"])] = {
            "mu_t": mu_t,
            "src": MU_T_FALLBACK if feat is None else MU_T_LIVE,
            "blowout": margin >= bl,
            "close": margin <= cl,
            "shootout": total >= math.ceil(mu_t + delta),
            "grind": total <= math.floor(mu_t - delta),
        }
    return out


def player_logs(
    box_rows: Iterable[Mapping[str, Any]], day: date, player_ids: Iterable[int]
) -> dict[int, list[dict[str, Any]]]:
    """Games each listed player PLAYED (minutes > 0) in season 2026 strictly before ``day``. Rows of any
    other season (the export carries season-2025 box rows as display history) are dropped here."""
    want = {int(p) for p in player_ids}
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    for r in box_rows:
        if r.get("season") != LIVE_SEASON or not season_id_ok(str(r["game_id"])):
            continue
        pid = int(r["player_id"])
        gd = r.get("game_date")
        if pid not in want or gd is None or gd >= day:
            continue
        mins = r.get("minutes")
        if mins is None or float(mins) <= 0.0:
            continue
        key = (pid, str(r["game_id"]))
        if key in seen:  # the export's box rows can hold a game twice (window rows + history rows)
            continue
        seen.add(key)
        out[pid].append({"game_id": str(r["game_id"]), **{s: r.get(s) for s in STATS}})
    return out


def hist_entry(
    log: Sequence[Mapping[str, Any]],
    stat: str,
    main_line: float,
    scripts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """``{"n_all", "k_all", script: [n, k]}``: integer counts of finished games (a played game whose
    stat is not recorded is not counted)."""
    played = [g for g in log if g.get(stat) is not None]
    out: dict[str, Any] = {
        "n_all": len(played),
        "k_all": sum(1 for g in played if g[stat] > main_line),
    }
    for s in SCRIPTS:
        sel = [g for g in played if scripts.get(g["game_id"], {}).get(s)]
        out[s] = [len(sel), sum(1 for g in sel if g[stat] > main_line)]
    return out


def attach_hist(
    payload: dict[str, Any],
    frame: pl.DataFrame | None,
    logs: Mapping[int, Sequence[Mapping[str, Any]]],
    gm: GameModelParams,
    definitions: Mapping[str, Any],
    day: date,
) -> dict[str, Any]:
    """Add ``hist`` to every stat entry that has a ``main_line``; return the ``build_log.hist`` block.

    The block carries ``past_games: {"n_games": N}`` ONLY: no season-wide count of how often each script
    happened (adversary M2, 2026-11-17: a nightly realised-script aggregate would falsify
    GAME_SCRIPTS_FORWARD's exposure answer). The per-player ``[n, k]`` pairs on stat entries are the
    display Devin chose; they are per player, never summed across the season here."""
    scripts = past_scripts(frame, gm, definitions)
    n_entries = 0
    for g in payload["games"]:
        for p in g["players"]:
            for stat, e in p["stats"].items():
                if e.get("main_line") is not None:
                    e["hist"] = hist_entry(
                        logs.get(int(p["player_id"]), []), stat, float(e["main_line"]), scripts
                    )
                    n_entries += 1
    season = {"n_games": len(scripts)}
    src: dict[str, int] = defaultdict(int)
    for v in scripts.values():
        src[str(v["src"])] += 1
    return {
        "season": LIVE_SEASON, "before": day.isoformat(), "n_entries": n_entries,
        "past_games": season, "mu_t_source_past": dict(sorted(src.items())),
    }  # fmt: skip
