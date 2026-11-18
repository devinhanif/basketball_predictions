# ruff: noqa: E501
"""Season-2026 script history and pre-game ``mu_t`` for the combo payload (descriptive counts only).

Everything here reads the export copy's ``box_scores`` rows of the LIVE season (``season = 2026`` AND a
``002 26 #####`` game id, in SQL, every query); season 2025 rows that the export may carry as a display
game log are never read, and neither are 2023 / 2024 rows. A past game is final when it has box rows;
only games dated STRICTLY before the slate date count, and all of them ended before the payload's
``made_at`` (they were played on an earlier ET date, the latest tip of which is hours before the first
production run of the slate day).

Script membership of a past game uses the combo contract's own definitions (``shared.scripts.definitions``)
on the final score: blowout ``|margin| >= 15``, close ``|margin| <= 5``, shootout ``total >= ceil(mu_t +
12)``, grind ``total <= floor(mu_t - 12)``, where ``mu_t`` is that game's own PRE-GAME game-model mean
(``GameModelParams.total_mu`` of the as-of total feature built from completed season-2026 games dated before
that game; no history means the league fallback; rounded to 2 decimals as ``build_game_payload`` does). A
team's score is the sum of its players' points; the 51 completed games of the export's ``games`` table agree
with that sum on both teams (checked 2026-11-17).

Counts only: ``n`` games, ``k`` games in which the stat finished over tonight's main line. No rate, no
smoothing, no blend with the model chance. "Played" is minutes > 0.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb
import polars as pl

from nba.parlay.game_model import GameModelParams, asof_total_features, slate_total_feature

LIVE_SEASON = 2026
SCRIPTS = ("blowout", "close", "shootout", "grind")
MU_T_LIVE = "live-season games only"
MU_T_FALLBACK = "league fallback"
_SEASON_SQL = "season = ? AND substr(game_id, 4, 2) = ? AND game_id LIKE '002%'"


def _season_args() -> list[Any]:
    return [LIVE_SEASON, f"{LIVE_SEASON % 100:02d}"]


def game_model_params(game_params: Mapping[str, Any]) -> GameModelParams:
    import json

    d = dict(game_params)
    d["fit_seasons"] = tuple(d.get("fit_seasons", ()))
    return GameModelParams.from_json(json.dumps({**d, "fit_seasons": list(d["fit_seasons"])}))


def past_games(con: duckdb.DuckDBPyConnection, night: str) -> pl.DataFrame:
    """Completed season-2026 games dated strictly before ``night``: game_id, game_date, two team ids,
    home_pts / away_pts (the first team is just the smaller id: the total feature is symmetric and the
    script definitions use |margin| and the total)."""
    rows = con.execute(
        f"""SELECT game_id, min(game_date) AS game_date, min(team_id) AS home_team, max(team_id) AS away_team,
                   count(DISTINCT team_id) AS n_teams,
                   sum(CASE WHEN team_id = (SELECT min(b2.team_id) FROM box_scores b2 WHERE b2.game_id = b.game_id) THEN pts END) AS home_pts,
                   sum(CASE WHEN team_id = (SELECT max(b2.team_id) FROM box_scores b2 WHERE b2.game_id = b.game_id) THEN pts END) AS away_pts
            FROM box_scores b WHERE {_SEASON_SQL} AND game_date < ?
            GROUP BY game_id ORDER BY game_date, game_id""",  # noqa: S608
        [*_season_args(), night],
    ).fetchall()
    ok = [r[:4] + r[5:] for r in rows if r[4] == 2 and r[5] and r[6]]
    return pl.DataFrame(
        ok,
        schema={
            "game_id": pl.String,
            "game_date": pl.Date,
            "home_team": pl.Int64,
            "away_team": pl.Int64,
            "home_pts": pl.Int64,
            "away_pts": pl.Int64,
        },
        orient="row",
    )


def past_scripts(
    games: pl.DataFrame, gm: GameModelParams, definitions: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Per past game: ``{"mu_t": x, "src": ..., "blowout": bool, ...}`` (pre-game mu_t, 2 dp)."""
    if games.height == 0:
        return {}
    feats = asof_total_features(games, gm.halflife, gm.prior_k)
    f_by = dict(zip(feats["game_id"].to_list(), feats["total_feat"].to_list(), strict=True))
    bl, cl = float(definitions["blowout_abs_margin"]), float(definitions["close_abs_margin"])
    delta = float(definitions["total_delta"])
    out: dict[str, dict[str, Any]] = {}
    for r in games.iter_rows(named=True):
        feat = f_by[r["game_id"]]
        mu_t = round(gm.total_mu(feat), 2)
        margin = abs(int(r["home_pts"]) - int(r["away_pts"]))
        total = int(r["home_pts"]) + int(r["away_pts"])
        out[str(r["game_id"])] = {
            "mu_t": mu_t,
            "src": MU_T_FALLBACK if feat is None else MU_T_LIVE,
            "blowout": margin >= bl,
            "close": margin <= cl,
            "shootout": total >= math.ceil(mu_t + delta),
            "grind": total <= math.floor(mu_t - delta),
        }
    return out


def tonight_mu_t(
    games: pl.DataFrame, gm: GameModelParams, home: int, away: int, night: str
) -> tuple[float, str]:
    from datetime import date

    feat = (
        slate_total_feature(games, gm, home, away, date.fromisoformat(night))
        if games.height
        else None
    )
    return round(gm.total_mu(feat), 2), MU_T_FALLBACK if feat is None else MU_T_LIVE


def player_logs(
    con: duckdb.DuckDBPyConnection, night: str, player_ids: Sequence[int]
) -> dict[int, list[dict[str, Any]]]:
    """Games each player PLAYED (minutes > 0) this season before ``night``."""
    if not player_ids:
        return {}
    marks = ",".join("?" for _ in player_ids)
    rows = con.execute(
        f"""SELECT player_id, game_id, pts, reb, ast, fg3m FROM box_scores
            WHERE {_SEASON_SQL} AND game_date < ? AND minutes > 0 AND player_id IN ({marks})""",  # noqa: S608
        [*_season_args(), night, *[int(p) for p in player_ids]],
    ).fetchall()
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pid, gid, pts, reb, ast, fg3m in rows:
        out[int(pid)].append(
            {"game_id": str(gid), "pts": pts, "reb": reb, "ast": ast, "fg3m": fg3m}
        )
    return out


def hist_entry(
    log: Sequence[Mapping[str, Any]],
    stat: str,
    main_line: float,
    scripts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """``{"n_all", "k_all", script: [n, k]}``: integer counts of finished games."""
    out: dict[str, Any] = {"n_all": len(log), "k_all": sum(1 for g in log if g[stat] > main_line)}
    for s in SCRIPTS:
        sel = [g for g in log if scripts.get(g["game_id"], {}).get(s)]
        out[s] = [len(sel), sum(1 for g in sel if g[stat] > main_line)]
    return out


def attach_hist(
    con: duckdb.DuckDBPyConnection,
    payload: dict[str, Any],
    night: str,
    gm: GameModelParams,
    definitions: Mapping[str, Any],
) -> dict[str, Any]:
    """Add ``hist`` to every stat entry that has a ``main_line`` and return the log block."""
    games = past_games(con, night)
    scripts = past_scripts(games, gm, definitions)
    ids = sorted({p["player_id"] for g in payload["games"] for p in g["players"]})
    logs = player_logs(con, night, ids)
    n_entries = 0
    for g in payload["games"]:
        for p in g["players"]:
            for stat, e in p["stats"].items():
                if e.get("main_line") is not None:
                    e["hist"] = hist_entry(
                        logs.get(p["player_id"], []), stat, float(e["main_line"]), scripts
                    )
                    n_entries += 1
    season = {"n_games": len(scripts)} | {
        s: sum(1 for v in scripts.values() if v[s]) for s in SCRIPTS
    }
    return {
        "season": LIVE_SEASON, "before": night, "n_entries": n_entries, "past_games": season,
        "mu_t_source_past": dict(sorted(_count(v["src"] for v in scripts.values()).items())),
    }  # fmt: skip


def _count(it: Any) -> dict[str, int]:
    d: dict[str, int] = defaultdict(int)
    for x in it:
        d[x] += 1
    return d
