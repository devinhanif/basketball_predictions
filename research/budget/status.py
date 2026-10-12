# ruff: noqa: E501
"""As-of team status and star-rest pairs for TEAM_BUDGET habits (research).

Status uses standings from games dated STRICTLY EARLIER than the row's date, within the season:
games back of the 6th and 10th seed of the team's conference, win%, and a late-season flag.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import polars as pl

from nba.features.game_context import TEAM_CONFERENCE

MIN_GAMES_STATUS = 20  # before this many games the status is "middle" (uninformative)
LATE_GAME = 60
STATUSES = ("contending", "middle", "out")


def classify(gb6: float, gb10: float, games: int) -> str:
    """contending: at or above the 6th seed; middle: inside the 10-seed play-in line; out otherwise."""
    if games < MIN_GAMES_STATUS:
        return "middle"
    if gb6 <= 0.0:
        return "contending"
    if gb10 <= 0.0:
        return "middle"
    return "out"


def team_status(games: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id): status, gb6, gb10, win_pct, games_prior, late (games_prior >= 60).

    ``games``: game_id, game_date, season, home_team, away_team, home_pts, away_pts."""
    g = games.sort(["game_date", "game_id"])
    out: list[dict[str, Any]] = []
    for season in g["season"].unique().sort().to_list():
        gs = g.filter(pl.col("season") == season)
        rec: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for date in gs["game_date"].unique().sort().to_list():
            day = gs.filter(pl.col("game_date") == date)
            by_conf: dict[str, list[tuple[float, int, int, int]]] = defaultdict(list)
            for t, (w, lo) in rec.items():
                conf = TEAM_CONFERENCE.get(t)
                if conf is not None and w + lo > 0:
                    by_conf[conf].append((w / (w + lo), w, lo, t))
            seeds: dict[str, tuple[Any, Any]] = {}
            for conf, lst in by_conf.items():
                lst.sort(key=lambda x: (-x[0], -x[1]))
                s6 = lst[5] if len(lst) >= 6 else None
                s10 = lst[9] if len(lst) >= 10 else None
                seeds[conf] = (s6, s10)
            for r in day.iter_rows(named=True):
                for t in (r["home_team"], r["away_team"]):
                    w, lo = rec[t]
                    n = w + lo
                    conf = TEAM_CONFERENCE.get(t, "")
                    s6, s10 = seeds.get(conf, (None, None))
                    gb6 = ((s6[1] - w) + (lo - s6[2])) / 2.0 if s6 else 0.0
                    gb10 = ((s10[1] - w) + (lo - s10[2])) / 2.0 if s10 else 0.0
                    out.append(
                        {
                            "game_id": r["game_id"],
                            "team_id": t,
                            "status": classify(gb6, gb10, n),
                            "gb6": gb6,
                            "gb10": gb10,
                            "win_pct": w / n if n else 0.5,
                            "games_prior": n,
                            "late": n >= LATE_GAME,
                        }
                    )
            # update records AFTER the whole date was labelled (same-day games never see each other)
            for r in day.iter_rows(named=True):
                hw = r["home_pts"] > r["away_pts"]
                rec[r["home_team"]][0 if hw else 1] += 1
                rec[r["away_team"]][1 if hw else 0] += 1
    return pl.DataFrame(out)


K_REST = 10.0  # pseudo-pairs of the shrinkage of a coach's star-rest rate to the league as-of rate


def coach_star_rest_asof(tg: pl.DataFrame, pairs: pl.DataFrame) -> pl.DataFrame:
    """Coach career star-rest propensity per (game_id, team_id), strictly as-of, status-matched.

    ``tg``: game_id, team_id, game_date, coach_key (ACTUAL coach, history only), status (tonight's
    as-of status). ``pairs``: game_id, team_id, status, rest (star on a back-to-back second night who
    played the night before; rest = 1 if rested, 0 if played). The coach looked up is the coach of
    the team's PREVIOUS game; his running (rested, pairs) totals over games dated STRICTLY EARLIER,
    any team, are shrunk to the league as-of rate: ``(R + k L) / (N + k)``. ``h_star_rest_all``
    pools statuses; ``h_star_rest_matched`` uses tonight's status when it is contending/middle and
    the pooled rate when it is ``out`` (that status fails the reliability screen)."""
    agg = pairs.group_by("game_id", "team_id", "status").agg(
        pl.col("rest").sum().cast(pl.Float64).alias("r"), pl.len().cast(pl.Float64).alias("n")
    )
    t = tg.sort(["team_id", "game_date", "game_id"]).with_columns(
        pl.col("coach_key").shift(1).over("team_id").alias("id_key")
    )
    cells = {"all": None, "contending": "contending", "middle": "middle"}
    out = t.select("game_id", "team_id", "game_date", "id_key", "status")
    for name, st in cells.items():
        a = agg if st is None else agg.filter(pl.col("status") == st)
        a = a.group_by("game_id", "team_id").agg(pl.col("r").sum(), pl.col("n").sum())
        per = (
            t.select("game_id", "team_id", "game_date", "coach_key")
            .join(a, on=["game_id", "team_id"], how="left")
            .with_columns(pl.col("r").fill_null(0.0), pl.col("n").fill_null(0.0))
        )
        hist = (
            per.filter(pl.col("coach_key").is_not_null())
            .sort(["coach_key", "game_date", "game_id"])
            .with_columns(
                pl.col("r").cum_sum().over("coach_key").alias("_cr"),
                pl.col("n").cum_sum().over("coach_key").alias("_cn"),
            )
            .select(pl.col("coach_key").alias("id_key"), "game_date", "_cr", "_cn")
            .unique(subset=["id_key", "game_date"], keep="last")
            .sort("game_date")
        )
        lp = (
            per.group_by("game_date")
            .agg(pl.col("r").sum().alias("s"), pl.col("n").sum().alias("m"))
            .sort("game_date")
            .select(
                "game_date", pl.col("s").cum_sum().alias("_ls"), pl.col("m").cum_sum().alias("_lm")
            )
        )
        q = out.select("game_id", "team_id", "game_date", "id_key").sort("game_date")
        q = q.join_asof(lp, on="game_date", strategy="backward", allow_exact_matches=False)
        q = q.join_asof(
            hist, on="game_date", by="id_key", strategy="backward", allow_exact_matches=False
        )
        lg = pl.when(pl.col("_lm") > 0).then(pl.col("_ls") / pl.col("_lm")).otherwise(0.03)
        cr, cn = pl.col("_cr").fill_null(0.0), pl.col("_cn").fill_null(0.0)
        q = q.select("game_id", "team_id", ((cr + K_REST * lg) / (cn + K_REST)).alias(f"_h_{name}"))
        out = out.join(q, on=["game_id", "team_id"], how="left")
    return out.select(
        "game_id",
        "team_id",
        pl.col("_h_all").alias("h_star_rest_all"),
        pl.when(pl.col("status") == "contending")
        .then(pl.col("_h_contending"))
        .when(pl.col("status") == "middle")
        .then(pl.col("_h_middle"))
        .otherwise(pl.col("_h_all"))
        .alias("h_star_rest_matched"),
    ).sort(["game_id", "team_id"])
