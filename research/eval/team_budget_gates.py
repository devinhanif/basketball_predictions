# ruff: noqa: E501
# mypy: disable-error-code="arg-type,operator,unused-ignore"
"""TEAM_BUDGET minimum-data gates (docs/prereg/TEAM_BUDGET.md; DRAFT). No arm is fitted here.

    M="-m research.eval.team_budget_gates"
    .venv/bin/python $M sql      # stints coverage, minutes = 240 + 25 OT, stint-vs-box reconciliation
    .venv/bin/python $M coach    # coach coverage, coach-season sample sizes, as-of history depth, T-60 usability
    .venv/bin/python $M a0       # A0 (production and lower-tail mixture) from the stored components vs candidates.json
    .venv/bin/python $M roster   # as-of roster coverage and the sum of production minutes forecasts vs the budget
    .venv/bin/python $M all

Read-only: ``nba.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every query, season 2025 never
loaded. The gates are light (SQL aggregates, polars frames, stored components); they never take
``data/ops/heavy.lock``. Output ``reports/prereg_team_budget/gates.json`` (merged per stage).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

MAX_SEASON = 2024
SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
OUT = Path("reports/prereg_team_budget/gates.json")
LT_DIR = Path("reports/lower_tail")
DB = "nba.duckdb"
#: gate limits (frozen with the rule)
COVER_MIN = 0.95
SUM_TOL = 0.5  # minutes: box team minutes within this of 240 + 25 OT
STINT_TOL = 1.0  # minutes: stint-derived vs box player minutes
A0_TOL = 1e-6
ROSTER_COVER_MIN = 0.97  # share of realised team minutes held by as-of rostered players
REACH_MIN_SD = 0.02  # sd of the rake factor below this: T1 cannot move a forecast by the floor


def _save(stage: str, payload: dict[str, Any]) -> None:
    cur: dict[str, Any] = json.loads(OUT.read_text()) if OUT.exists() else {}
    cur[stage] = payload
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cur, indent=2, default=float))


def _f(x: Any) -> float:
    return float(x)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB, read_only=True)


# --------------------------------------------------------------------------- sql gates
def gate_sql(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """G1 stints coverage and clock identity; G2 minutes sum; G2b stint vs box player minutes."""
    base = f"""
    WITH g AS (SELECT game_id, season FROM games WHERE season BETWEEN 2022 AND {MAX_SEASON}),
    t AS (SELECT game_id, team_id, SUM(COALESCE(minutes, 0)) AS m, SUM((minutes > 0)::INT) AS np
          FROM player_game_stats GROUP BY 1, 2),
    s AS (SELECT game_id, team_id, MAX(period) AS mp, SUM(start_clock - end_clock) AS secs
          FROM stints GROUP BY 1, 2)
    SELECT g.season, COUNT(*) AS team_games, SUM((s.game_id IS NOT NULL)::INT) AS with_stints,
      SUM((ABS(s.secs - (2880 + 300 * GREATEST(s.mp - 4, 0))) <= 1)::INT) AS clock_ok,
      SUM((ABS(t.m - (240 + 25 * GREATEST(s.mp - 4, 0))) <= {SUM_TOL})::INT) AS minutes_ok,
      AVG(t.m - (240 + 25 * GREATEST(s.mp - 4, 0))) AS mean_gap,
      MAX(ABS(t.m - (240 + 25 * GREATEST(s.mp - 4, 0)))) AS max_gap,
      SUM((s.mp > 4)::INT) AS ot_team_games, AVG(t.np) AS mean_players
    FROM g JOIN t USING (game_id) LEFT JOIN s USING (game_id, team_id) GROUP BY 1 ORDER BY 1
    """
    rows = con.execute(base).fetchall()
    cols = [d[0] for d in con.description]
    by_season = [dict(zip(cols, r, strict=True)) for r in rows]
    rec = con.execute(
        f"""
    WITH st AS (SELECT game_id, team_id, UNNEST(players) AS pid, start_clock - end_clock AS d FROM stints),
    a AS (SELECT game_id, team_id, pid AS player_id, SUM(d) / 60.0 AS sm FROM st GROUP BY 1, 2, 3)
    SELECT COUNT(*), AVG(ABS(a.sm - p.minutes)), SUM((ABS(a.sm - p.minutes) > {STINT_TOL})::INT)
    FROM a JOIN player_game_stats p USING (game_id, player_id) JOIN games g USING (game_id)
    WHERE g.season BETWEEN 2022 AND {MAX_SEASON} AND p.minutes > 0
    """
    ).fetchone()
    assert rec is not None
    n_tg = sum(r["team_games"] for r in by_season)
    out = {
        "by_season": by_season,
        "coverage_stints": sum(r["with_stints"] for r in by_season) / n_tg,
        "clock_identity": sum(r["clock_ok"] for r in by_season) / n_tg,
        "minutes_identity": sum(r["minutes_ok"] for r in by_season) / n_tg,
        "stint_vs_box": {
            "n_player_games": rec[0],
            "mean_abs_gap_min": rec[1],
            "n_gap_gt_1min": rec[2],
            "share_gap_gt_1min": rec[2] / rec[0],
        },
    }
    out["pass"] = bool(
        out["coverage_stints"] >= COVER_MIN
        and out["clock_identity"] >= COVER_MIN
        and out["minutes_identity"] >= COVER_MIN
        and out["stint_vs_box"]["share_gap_gt_1min"] <= 0.01
    )
    return out


# --------------------------------------------------------------------------- coach gates
def load_team_games(
    con: duckdb.DuckDBPyConnection,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(games, box, coach) for seasons 2022-2024; coach has coach_key, known_at."""
    games = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games "
        f"WHERE season BETWEEN 2022 AND {MAX_SEASON}"
    ).pl()
    box = con.execute(
        "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.starter FROM player_game_stats s "
        f"JOIN games g USING (game_id) WHERE g.season BETWEEN 2022 AND {MAX_SEASON}"
    ).pl()
    coach = con.execute(
        "SELECT c.game_id, c.team_id, c.coach_id, c.coach_name, c.known_at FROM team_coach_games c "
        f"JOIN games g USING (game_id) WHERE g.season BETWEEN 2022 AND {MAX_SEASON}"
    ).pl()
    coach = coach.with_columns(
        pl.coalesce(pl.col("coach_id").cast(pl.Utf8), pl.col("coach_name")).alias("coach_key")
    )
    return games, box, coach


def gate_coach(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    from nba.features.game_tipoff import build_game_tipoff
    from research.budget import asof as A

    games, box, coach = load_team_games(con)
    tg = A.team_game_shape(box, games)
    tg = tg.join(
        coach.select("game_id", "team_id", "coach_key", "known_at"),
        on=["game_id", "team_id"],
        how="left",
    )
    n_tg = tg.height
    have = int(tg["coach_key"].is_not_null().sum())
    tips = build_game_tipoff().select("game_id", "tipoff_utc")
    tg = tg.join(tips, on="game_id", how="left").sort(["team_id", "game_date", "game_id"])
    # The stored known_at is EACH GAME'S OWN tip, so tonight's coach is never stamped knowable at T-60.
    # The rule therefore uses the coach of the team's previous game: usable iff that game's known_at is
    # earlier than tonight's tip - 60 min.
    tg = tg.with_columns(
        pl.col("known_at").shift(1).over("team_id").alias("prev_known_at"),
        pl.col("coach_key").shift(1).over("team_id").alias("prev_key"),
    ).with_columns(
        (
            pl.col("prev_key").is_not_null()
            & (pl.col("prev_known_at") < (pl.col("tipoff_utc") - pl.duration(minutes=60)))
        )
        .fill_null(False)
        .alias("usable"),
        (pl.col("known_at") < (pl.col("tipoff_utc") - pl.duration(minutes=60)))
        .fill_null(False)
        .alias("own_usable"),
        (pl.col("prev_key") == pl.col("coach_key")).alias("same_coach"),
    )
    hab = A.coach_habits_asof(
        tg.select(
            "game_id",
            "team_id",
            "game_date",
            "coach_key",
            "depth",
            "conc",
            "top5_min",
            "b2b",
            "abs_margin",
        )
    )
    j = tg.join(hab, on=["game_id", "team_id"]).join(
        games.select("game_id", "season"), on="game_id"
    )
    per_season = []
    for s in (2022, *SEASONS):
        d = j.filter(pl.col("season") == s)
        n = d.height
        per_season.append(
            {
                "season": s,
                "team_games": n,
                "tip_missing": int(d["tipoff_utc"].is_null().sum()),
                "usable_at_t60": int(d["usable"].sum()),
                "usable_share": _f(d["usable"].mean() or 0.0),
                "own_coach_stamped_before_t60": int(d["own_usable"].sum()),
                "lag_equals_actual_coach_share": float(
                    d.filter(pl.col("prev_key").is_not_null())["same_coach"].mean() or 0.0
                ),
                "n_prior_lt10": int((d["coach_n_prior"] < 10).sum()),
                "n_prior_lt20": int((d["coach_n_prior"] < 20).sum()),
                "share_lt10": float((d["coach_n_prior"] < 10).mean() or 0.0),
                "share_lt20": float((d["coach_n_prior"] < 20).mean() or 0.0),
                "share_ge40": float((d["coach_n_prior"] >= 40).mean() or 0.0),
            }
        )
    # coach-season sizes (games with a team in a season, by coach)
    cs = (
        tg.join(games.select("game_id", "season"), on="game_id")
        .filter(pl.col("coach_key").is_not_null())
        .group_by("season", "team_id", "coach_key")
        .len()
    )
    sizes = {
        str(s): {
            "coach_team_seasons": int(cs.filter(pl.col("season") == s).height),
            "lt10_games": int(cs.filter((pl.col("season") == s) & (pl.col("len") < 10)).height),
            "lt20_games": int(cs.filter((pl.col("season") == s) & (pl.col("len") < 20)).height),
            "median_games": float(cs.filter(pl.col("season") == s)["len"].median() or 0.0),
        }
        for s in (2022, *SEASONS)
    }
    # conditional-habit sample sizes: back-to-back and blowout games per coach-season
    cond = (
        tg.join(games.select("game_id", "season"), on="game_id")
        .filter(pl.col("coach_key").is_not_null())
        .group_by("season", "team_id", "coach_key")
        .agg(
            pl.col("b2b").sum().alias("n_b2b"),
            (pl.col("abs_margin") >= 20).sum().alias("n_blow"),
            (pl.col("abs_margin") <= 10).sum().alias("n_close"),
        )
    )
    cond_s = {
        str(s): {
            "median_b2b_games": float(cond.filter(pl.col("season") == s)["n_b2b"].median() or 0.0),
            "median_blowout_games": float(
                cond.filter(pl.col("season") == s)["n_blow"].median() or 0.0
            ),
            "median_close_games": float(
                cond.filter(pl.col("season") == s)["n_close"].median() or 0.0
            ),
        }
        for s in (2022, *SEASONS)
    }
    # habit dispersion across coach-games (do coaches differ at all, as-of?)
    d2 = j.filter(pl.col("season").is_in(list(SEASONS)) & (pl.col("coach_n_prior") >= 20))
    disp = {c: float(d2[c].std() or 0.0) for c in A.HABIT_COLS}
    # Reliability of each raw habit across seasons 2022 -> 2023 (coaches with >= 30 games in both; box only,
    # no stat outcome, 2024 untouched): a habit that does not persist cannot be a coach feature.
    rel = {}
    raw = (
        tg.join(games.select("game_id", "season"), on="game_id")
        .filter(pl.col("coach_key").is_not_null() & pl.col("depth").is_not_null())
        .group_by("coach_key", "season")
        .agg(
            pl.len().alias("n"),
            pl.col("depth").mean().alias("depth"),
            pl.col("conc").mean().alias("conc"),
            (
                pl.col("top5_min").filter(pl.col("b2b")).mean()
                - pl.col("top5_min").filter(~pl.col("b2b")).mean()
            ).alias("b2b"),
            (
                pl.col("top5_min").filter(pl.col("abs_margin") >= 20).mean()
                - pl.col("top5_min").filter(pl.col("abs_margin") <= 10).mean()
            ).alias("blowout"),
        )
        .filter(pl.col("n") >= 30)
    )
    a22, a23 = raw.filter(pl.col("season") == 2022), raw.filter(pl.col("season") == 2023)
    both = a22.join(a23, on="coach_key", suffix="_23")
    for col in ("depth", "conc", "b2b", "blowout"):
        x, y = both[col].to_numpy(), both[f"{col}_23"].to_numpy()
        ok = np.isfinite(x) & np.isfinite(y)
        rel[col] = {
            "n_coaches": int(ok.sum()),
            "corr_2022_2023": float(np.corrcoef(x[ok], y[ok])[0, 1])
            if ok.sum() > 3
            else float("nan"),
        }
    out = {
        "team_games": n_tg,
        "coach_key_present": have,
        "coach_id_null_name_present": int(
            coach.filter(pl.col("coach_id").is_null() & pl.col("coach_name").is_not_null()).height
        ),
        "per_season": per_season,
        "coach_season_sizes": sizes,
        "conditional_sizes": cond_s,
        "habit_sd_across_coach_games_n_prior_ge20": disp,
        "habit_reliability_across_seasons": rel,
    }
    sel = [r for r in per_season if r["season"] in SEASONS]
    out["pass"] = bool(
        have / n_tg >= COVER_MIN
        and all(r["usable_share"] >= COVER_MIN for r in sel)
        and all(r["tip_missing"] == 0 for r in sel)
    )
    return out


# --------------------------------------------------------------------------- A0 reproduction
def gate_a0() -> dict[str, Any]:
    """A0 off and mixture integer CRPS recomputed from the stored components, vs candidates.json."""
    from nba.eval.lower_tail_eval import arm_grid, arm_scores, load_components

    ref = json.loads((LT_DIR / "candidates.json").read_text())
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    cells = []
    for st in STATS:
        comp = load_components(st, LT_DIR)
        if not (
            np.array_equal(comp["row_gid"], pic["row_gid"])
            and np.array_equal(comp["row_pid"], pic["row_pid"])
        ):
            raise RuntimeError("pi components not row-aligned")
        if int(comp["row_season"].max()) > MAX_SEASON:
            raise RuntimeError("season > 2024 in components")
        for kind, key in (("off", "off"), ("mixture", "mixture")):
            t0 = time.time()
            grid = arm_grid(kind, comp, pic, st)
            for s in SEASONS:
                m = comp["row_season"] == s
                sc = arm_scores(grid[m], comp["row_y"][m], st)
                got = float(sc["crps_int"].mean())
                want = float(ref[st][str(s)][key]["crps_int"])
                n_ref = ref[st][str(s)]["off"]["n"]
                cells.append(
                    {
                        "stat": st,
                        "season": s,
                        "arm": kind,
                        "crps_int": got,
                        "reference": want,
                        "abs_diff": abs(got - want),
                        "n": int(m.sum()),
                        "n_ref": n_ref,
                    }
                )
            _log(f"A0 {st} {kind} done in {time.time() - t0:.0f}s")
        del comp
    out = {
        "cells": cells,
        "max_abs_diff": max(c["abs_diff"] for c in cells),
        "n_match": all(c["n"] == c["n_ref"] for c in cells),
    }
    out["pass"] = bool(out["max_abs_diff"] <= A0_TOL and out["n_match"])
    return out


# --------------------------------------------------------------------------- roster and budget
def gate_roster() -> dict[str, Any]:
    """As-of roster coverage of realised minutes and the sum of production minutes forecasts."""
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import ContextResidualConfig, flagged_from_availability
    from nba.props.minutes import build_minutes_features, predict_minutes
    from research.budget import asof as A
    from research.budget.constraint import rake_to_budget, team_budget

    games, pgs, _static, avail = load_inputs(DB, None)
    assert int(games["season"].max()) <= MAX_SEASON  # type: ignore[arg-type,unused-ignore]
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    # production minutes path on an in-memory copy (season <= 2024)
    con = connect()
    sc = duckdb.connect(":memory:")
    try:
        for name, sql in {
            "games": f"SELECT * FROM games WHERE season <= {MAX_SEASON}",
            "player_game_stats": f"SELECT s.* FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}",
        }.items():
            df = con.execute(sql).pl()
            sc.register("_src", df)
            sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
            sc.unregister("_src")
    finally:
        con.close()
    mf = build_minutes_features(sc, use_game_context=False)
    dists = predict_minutes(mf)
    pm = mf.select("game_id", "player_id", "team_id", "game_date").with_columns(
        pl.Series("p", [d.p_play for d in dists]), pl.Series("mu", [d.mu for d in dists])
    )
    _log(f"production minutes rows {pm.height}")
    tgm = pl.concat(
        [
            games.select("game_id", "game_date", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", pl.col("away_team").alias("team_id")),
        ]
    )
    box = pgs.select("game_id", "team_id", "player_id", "minutes", "starter").with_columns(
        pl.col("minutes").fill_null(0.0)
    )
    roster = A.asof_roster(box, tgm)
    out_rows = [(g, int(p)) for g, ps in flagged.items() for p in ps]
    outdf = (
        pl.DataFrame(out_rows, schema={"game_id": pl.Utf8, "player_id": pl.Int32}, orient="row")
        if out_rows
        else pl.DataFrame()
    )
    roster = roster.with_columns(pl.col("player_id").cast(pl.Int32))
    if outdf.height:
        roster = (
            roster.join(
                outdf.with_columns(pl.lit(True).alias("_out")),
                on=["game_id", "player_id"],
                how="left",
            )
            .filter(pl.col("_out").is_null())
            .drop("_out")
        )
    # production forecast for roster players: tonight's mf row if present, else the player's latest earlier row
    pm = pm.with_columns(pl.col("player_id").cast(pl.Int32))
    r = roster.join(
        pm.select("game_id", "player_id", "p", "mu"), on=["game_id", "player_id"], how="left"
    )
    prev = pm.sort(["player_id", "game_date", "game_id"]).select(
        "player_id", "game_date", pl.col("p").alias("p_prev"), pl.col("mu").alias("mu_prev")
    )
    dated = r.join(tgm.select("game_id", "team_id", "game_date"), on=["game_id", "team_id"]).sort(
        "game_date"
    )
    dated = dated.join_asof(
        prev.sort("game_date"),
        on="game_date",
        by="player_id",
        strategy="backward",
        allow_exact_matches=False,
    )
    dated = dated.with_columns(
        pl.col("p").is_null().alias("_imputed"),
        pl.coalesce(pl.col("p"), pl.col("p_prev"), pl.lit(0.5)).alias("p_f"),
        pl.coalesce(pl.col("mu"), pl.col("mu_prev"), pl.col("mean_min")).alias("mu_f"),
    ).with_columns((pl.col("p_f") * pl.col("mu_f")).alias("e"))
    ssum = dated.group_by("game_id", "team_id").agg(
        pl.col("e").sum().alias("S"),
        pl.len().alias("n_roster"),
        pl.col("_imputed").mean().alias("imputed_share"),
    )
    # realised OT per team-game from box minutes; realised coverage of minutes by rostered players
    act = box.group_by("game_id", "team_id").agg(pl.col("minutes").sum().alias("T"))
    inroster = box.join(
        roster.select("game_id", "team_id", "player_id").with_columns(pl.lit(True).alias("_in")),
        on=["game_id", "team_id", "player_id"],
        how="left",
    )
    cover = inroster.group_by("game_id", "team_id").agg(
        (pl.col("minutes") * pl.col("_in").fill_null(False).cast(pl.Float64)).sum().alias("m_in"),
        pl.col("minutes").sum().alias("m_all"),
    )
    ot_asof = (
        act.join(games.select("game_id", "game_date"), on="game_id")
        .with_columns((pl.col("T") > 241).cast(pl.Float64).alias("ot"))
        .group_by("game_date")
        .agg(pl.col("ot").sum().alias("s"), pl.len().alias("n"))
        .sort("game_date")
        .with_columns((pl.col("s").cum_sum() / pl.col("n").cum_sum()).alias("rate"))
        .select("game_date", "rate")
        .with_columns(pl.col("game_date").alias("_d"))
    )
    j = (
        ssum.join(cover, on=["game_id", "team_id"])
        .join(act, on=["game_id", "team_id"])
        .join(games.select("game_id", "game_date", "season"), on="game_id")
        .sort("game_date")
        .join_asof(
            ot_asof.select("game_date", pl.col("rate").alias("ot_rate")),
            on="game_date",
            strategy="backward",
            allow_exact_matches=False,
        )
        .filter(pl.col("season").is_in(list(SEASONS)))
        .with_columns(pl.col("ot_rate").fill_null(0.06))
    )
    b = team_budget(j["ot_rate"].to_numpy())
    k = b / j["S"].to_numpy()
    j = j.with_columns(
        pl.Series("budget", b), pl.Series("k", k), (pl.col("m_in") / pl.col("m_all")).alias("cover")
    )
    summ: dict[str, Any] = {}
    for s in SEASONS:
        d = j.filter(pl.col("season") == s)
        kk = d["k"].to_numpy()
        summ[str(s)] = {
            "team_games": d.height,
            "roster_players_mean": _f(d["n_roster"].mean() or 0.0),
            "imputed_share_mean": _f(d["imputed_share"].mean() or 0.0),
            "minutes_cover_mean": _f(d["cover"].mean() or 0.0),
            "minutes_cover_p05": float(np.quantile(d["cover"].to_numpy(), 0.05)),
            "forecast_sum_mean": _f(d["S"].mean() or 0.0),
            "forecast_sum_sd": _f(d["S"].std() or 0.0),
            "abs_gap_to_budget_mean": float(
                np.abs(d["S"].to_numpy() - d["budget"].to_numpy()).mean()
            ),
            "k_mean": float(kk.mean()),
            "k_sd": float(kk.std()),
            "k_p05": float(np.quantile(kk, 0.05)),
            "k_p95": float(np.quantile(kk, 0.95)),
            "share_abs_k_gt_5pct": float((np.abs(kk - 1) > 0.05).mean()),
            "ot_rate_final": float(d["ot_rate"].tail(1)[0]),
        }
    # tonight-box roster sum (the leaky comparison): production forecast summed over box-listed players
    leak = (
        pm.join(games.select("game_id", "season"), on="game_id")
        .filter(pl.col("season").is_in(list(SEASONS)))
        .with_columns((pl.col("p") * pl.col("mu")).alias("e"))
        .group_by("game_id", "team_id")
        .agg(pl.col("e").sum().alias("S_box"))
    )
    summ["box_membership_sum"] = {
        "mean": float(leak["S_box"].mean() or 0.0),
        "sd": float(leak["S_box"].std() or 0.0),
    }
    # constraint is satisfiable and exact on real rows (sample of team-games)
    samp = (
        dated.join(games.select("game_id", "season"), on="game_id")
        .filter(pl.col("season").is_in(list(SEASONS)))
        .head(60000)
    )
    worst = 0.0
    for (_g, _t), d in list(samp.group_by("game_id", "team_id", maintain_order=True))[:2000]:
        rk = rake_to_budget(d["e"].to_numpy(), 241.5)
        worst = max(worst, abs(float(rk.sum()) - 241.5))
    summ["rake_exactness_max_abs_err_2000_team_games"] = worst
    # ---- missingness audit by realised-minutes bucket on the A0 rows (the leak shape) -------------
    comp = np.load(LT_DIR / "components_pts.npz", allow_pickle=True)
    rows = pl.DataFrame(
        {
            "game_id": comp["row_gid"].astype(str),
            "player_id": comp["row_pid"].astype(np.int32),
            "minutes": comp["row_minutes"],
        }
    ).join(
        box.select("game_id", "player_id", "team_id").with_columns(
            pl.col("player_id").cast(pl.Int32)
        ),
        on=["game_id", "player_id"],
        how="left",
    )
    con2 = connect()
    try:
        games2, box2, coach = load_team_games(con2)
    finally:
        con2.close()
    tgs = A.team_game_shape(box2, games2).join(
        coach.select("game_id", "team_id", "coach_key"), on=["game_id", "team_id"], how="left"
    )
    hab = A.coach_habits_asof(
        tgs.select(
            "game_id",
            "team_id",
            "game_date",
            "coach_key",
            "depth",
            "conc",
            "top5_min",
            "b2b",
            "abs_margin",
        )
    )
    rr = (
        rows.join(hab, on=["game_id", "team_id"], how="left")
        .join(
            roster.select("game_id", "team_id", "player_id").with_columns(
                pl.lit(1.0).alias("in_roster")
            ),
            on=["game_id", "team_id", "player_id"],
            how="left",
        )
        .join(
            dated.select("game_id", "team_id", "player_id", "_imputed"),
            on=["game_id", "team_id", "player_id"],
            how="left",
        )
        .with_columns(
            pl.col("in_roster").fill_null(0.0),
            pl.col("minutes")
            .cut([5.0, 10.0, 20.0], labels=["<5", "5-10", "10-20", ">20"])
            .alias("bucket"),
        )
    )
    audit: dict[str, Any] = {}
    for col, expr in {
        **{c: pl.col(c).is_null().cast(pl.Float64) for c in A.HABIT_COLS},
        "coach_n_prior_is_0": (pl.col("coach_n_prior") == 0).cast(pl.Float64),
        "in_roster": 1.0 - pl.col("in_roster"),
    }.items():
        t = rr.group_by("bucket").agg(expr.mean().alias("rate"), pl.len().alias("n")).sort("bucket")
        rates = {r["bucket"]: float(r["rate"]) for r in t.iter_rows(named=True)}
        audit[col] = {
            "rate_by_bucket": rates,
            "n_by_bucket": {r["bucket"]: r["n"] for r in t.iter_rows(named=True)},
            "max_gap_pp": 100.0 * (max(rates.values()) - min(rates.values())),
        }
    # placebo: the same audit for a permuted minutes label (what a pure-noise gap looks like)
    rng = np.random.default_rng(0)
    perm = rr.with_columns(pl.Series("bucket_p", rng.permutation(rr["bucket"].to_numpy())))
    tp = perm.group_by("bucket_p").agg((1.0 - pl.col("in_roster")).mean().alias("rate"))
    audit["placebo_in_roster_gap_pp"] = 100.0 * float(tp["rate"].max() - tp["rate"].min())  # type: ignore[operator]
    audit["null_columns_max_gap_pp"] = max(audit[c]["max_gap_pp"] for c in A.HABIT_COLS)
    audit["pass_null_asymmetry_le_2pp"] = bool(audit["null_columns_max_gap_pp"] <= 2.0)
    summ["missingness_audit"] = audit
    # ---- decision-score legs: DK T-60 legs that fall on A0 rows -------------------------------------
    legs = {}
    for s in SEASONS:
        pr = pl.read_parquet(
            f"data/ops/model_clv_pairs/pairs_{s}.parquet",
            columns=["game_id", "player_id", "stat", "book", "snapshot_kind"],
        )
        pr = pr.filter((pl.col("book") == "draftkings") & (pl.col("snapshot_kind") == "t60"))
        onrows = pr.join(
            rows.select("game_id", "player_id").with_columns(pl.col("player_id").cast(pl.Int64)),
            on=["game_id", "player_id"],
        )
        legs[str(s)] = {
            "dk_t60_legs": pr.height,
            "on_a0_rows": onrows.height,
            "by_stat": {
                r["stat"]: r["len"] for r in onrows.group_by("stat").len().iter_rows(named=True)
            },
        }
    summ["decision_legs"] = legs
    sel = [summ[str(s)] for s in SEASONS]
    summ["pass_cover"] = bool(all(x["minutes_cover_mean"] >= ROSTER_COVER_MIN for x in sel))
    summ["reach_k_sd_ok"] = bool(all(x["k_sd"] >= REACH_MIN_SD for x in sel))
    summ["pass"] = bool(
        summ["pass_cover"] and summ["reach_k_sd_ok"] and audit["pass_null_asymmetry_le_2pp"]
    )
    return summ


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["sql", "coach", "a0", "roster", "reliability", "all"])
    args = ap.parse_args(argv)
    stages = ["sql", "coach", "a0", "roster"] if args.stage == "all" else [args.stage]
    for st in stages:
        t0 = time.time()
        if st == "a0":
            res = gate_a0()
        elif st == "roster":
            res = gate_roster()
        elif st == "reliability":
            con0 = connect()
            try:
                res = gate_reliability(con0)
            finally:
                con0.close()
        else:
            con = connect()
            try:
                res = gate_sql(con) if st == "sql" else gate_coach(con)
            finally:
                con.close()
        res["wall_s"] = time.time() - t0
        _save(st, res)
        _log(f"{st}: pass={res.get('pass')} ({res['wall_s']:.0f}s)")
    return 0


# --------------------------------------------------------------------------- reliability (amendment 1)
#: Declared BEFORE computing (TEAM_BUDGET amendment 2026-10-11): career-pooled habits over a coach's 2022-23
#: games on any team (2024 untouched), split-half by ALTERNATE GAMES (odd/even order by date), Pearson r across
#: coaches with >= 60 games, Spearman-Brown corrected 2r/(1+r); keep a habit iff the corrected value >= 0.30.
REL_MIN_GAMES = 60
REL_KEEP = 0.30
REL_MIN_EVENTS = 3  # conditional means need this many games of each kind per half
REL_MIN_STAR_PAIRS = 5


def star_pairs(
    box: pl.DataFrame, tgm: pl.DataFrame, avail: pl.DataFrame, b2b: pl.DataFrame
) -> pl.DataFrame:
    """Per (game_id, team_id, player_id): star on the second night of a back-to-back who played the first
    night; ``rest`` = 1 if absent with a rest/management reason, 0 if he played; absent for another
    reason (injury etc.) is dropped. Stars = top-2 mean minutes in the team's previous 10 games."""
    from research.budget import asof as A

    roster = A.asof_roster(box, tgm)
    stars = (
        roster.sort(["game_id", "team_id", "mean_min"], descending=[False, False, True])
        .group_by("game_id", "team_id", maintain_order=True)
        .head(2)
        .join(b2b.filter(pl.col("b2b")).select("game_id", "team_id"), on=["game_id", "team_id"])
    )
    played = (
        box.filter(pl.col("minutes") > 0)
        .select("game_id", "team_id", "player_id")
        .with_columns(pl.lit(1).alias("_pl"))
    )
    prev = tgm.sort(["team_id", "game_date", "game_id"]).with_columns(
        pl.col("game_id").shift(1).over("team_id").alias("prev_game")
    )
    s = stars.join(prev.select("game_id", "team_id", "prev_game"), on=["game_id", "team_id"])
    s = s.join(
        played.rename({"game_id": "prev_game", "_pl": "_pp"}),
        on=["prev_game", "team_id", "player_id"],
        how="left",
    ).filter(pl.col("_pp").is_not_null())
    s = s.join(played, on=["game_id", "team_id", "player_id"], how="left")
    rest_rows = (
        avail.filter(pl.col("reason").str.to_lowercase().str.contains("rest|management"))
        .select("game_id", "player_id")
        .unique()
        .with_columns(pl.lit(1).alias("_rest"))
    )
    other = avail.select("game_id", "player_id").unique().with_columns(pl.lit(1).alias("_any"))
    s = s.join(rest_rows, on=["game_id", "player_id"], how="left").join(
        other, on=["game_id", "player_id"], how="left"
    )
    s = s.with_columns(
        pl.when(pl.col("_pl").is_not_null())
        .then(0)
        .when(pl.col("_rest").is_not_null())
        .then(1)
        .otherwise(None)
        .alias("rest")
    ).filter(pl.col("rest").is_not_null())
    return s.select("game_id", "team_id", "player_id", "rest")


def gate_reliability(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    from research.budget import asof as A
    from research.budget.status import STATUSES, team_status

    games, box, coach = load_team_games(con)
    avail = con.execute(
        "SELECT game_id, player_id, reason FROM player_availability WHERE game_id IS NOT NULL"
    ).pl()
    shape = A.team_game_shape(box, games).join(
        coach.select("game_id", "team_id", "coach_key"), on=["game_id", "team_id"], how="left"
    )
    st = team_status(games)
    tgm = shape.select("game_id", "team_id", "game_date")
    box_i = box.with_columns(pl.col("minutes").fill_null(0.0))
    pairs = star_pairs(box_i, tgm, avail, shape.select("game_id", "team_id", "b2b"))
    pairs = pairs.join(
        st.select("game_id", "team_id", "status", "late"), on=["game_id", "team_id"]
    ).join(shape.select("game_id", "team_id", "coach_key", "game_date"), on=["game_id", "team_id"])
    base = (
        shape.join(st.select("game_id", "team_id", "status", "late"), on=["game_id", "team_id"])
        .join(games.select("game_id", "season"), on="game_id")
        .filter(
            pl.col("season").is_in([2022, 2023])
            & pl.col("coach_key").is_not_null()
            & pl.col("depth").is_not_null()
        )
        .sort(["coach_key", "game_date", "game_id"])
        .with_columns((pl.int_range(pl.len()).over("coach_key") % 2).alias("half"))
    )
    n_by = base.group_by("coach_key").len().filter(pl.col("len") >= REL_MIN_GAMES)
    keep = set(n_by["coach_key"].to_list())
    base = base.filter(pl.col("coach_key").is_in(list(keep)))
    pairs = pairs.join(games.select("game_id", "season"), on="game_id").filter(
        pl.col("season").is_in([2022, 2023]) & pl.col("coach_key").is_in(list(keep))
    )
    pairs = pairs.sort(["coach_key", "game_date", "game_id"]).with_columns(
        (pl.int_range(pl.len()).over("coach_key") % 2).alias("half")
    )

    def corr(per: dict[tuple[str, int], float]) -> dict[str, Any]:
        xs, ys = [], []
        for c in keep:
            a, b = per.get((c, 0)), per.get((c, 1))
            if a is not None and b is not None and np.isfinite(a) and np.isfinite(b):
                xs.append(a)
                ys.append(b)
        if len(xs) < 5:
            return {
                "n_coaches": len(xs),
                "r_split_half": float("nan"),
                "spearman_brown": float("nan"),
                "keep": False,
            }
        r = float(np.corrcoef(xs, ys)[0, 1])
        sb = 2 * r / (1 + r) if r > -1 else float("nan")
        return {
            "n_coaches": len(xs),
            "r_split_half": r,
            "spearman_brown": sb,
            "keep": bool(sb >= REL_KEEP),
        }

    out: dict[str, Any] = {"coaches": len(keep), "games": base.height}
    for name in ("depth", "conc"):
        per = {
            (r["coach_key"], r["half"]): r["v"]
            for r in base.group_by("coach_key", "half")
            .agg(pl.col(name).mean().alias("v"))
            .iter_rows(named=True)
        }
        out[name] = corr(per)
    per_b: dict[tuple[str, int], float] = {}
    per_w: dict[tuple[str, int], float] = {}
    for (c, h), d in base.group_by("coach_key", "half"):
        nb, nr = int(d["b2b"].sum()), int((~d["b2b"]).sum())
        if nb >= REL_MIN_EVENTS and nr >= REL_MIN_EVENTS:
            per_b[(c, h)] = float(
                d.filter(pl.col("b2b"))["top5_min"].mean()
                - d.filter(~pl.col("b2b"))["top5_min"].mean()
            )
        bl, cl = d.filter(pl.col("abs_margin") >= 20), d.filter(pl.col("abs_margin") <= 10)
        if bl.height >= REL_MIN_EVENTS and cl.height >= REL_MIN_EVENTS:
            per_w[(c, h)] = float(bl["top5_min"].mean() - cl["top5_min"].mean())
    out["b2b_top5_career"] = corr(per_b)
    out["blowout_pull_career"] = corr(per_w)
    for sname in STATUSES:
        per_s: dict[tuple[str, int], float] = {}
        for (c, h), d in pairs.filter(pl.col("status") == sname).group_by("coach_key", "half"):
            if d.height >= REL_MIN_STAR_PAIRS:
                per_s[(c, h)] = float(d["rest"].mean())
        out[f"star_rest_{sname}"] = corr(per_s) | {
            "pairs": int(pairs.filter(pl.col("status") == sname).height),
            "rest_events": int(pairs.filter(pl.col("status") == sname)["rest"].sum()),
        }
    per_all = {
        (c, h): float(d["rest"].mean())
        for (c, h), d in pairs.group_by("coach_key", "half")
        if d.height >= REL_MIN_STAR_PAIRS
    }
    out["star_rest_all_status"] = corr(per_all) | {
        "pairs": pairs.height,
        "rest_events": int(pairs["rest"].sum()),
    }
    # status-conditioned blowout pull: sample check (declared: conditioned only if the median per half >= 5)
    bl = base.filter(pl.col("abs_margin") >= 20).group_by("coach_key", "status").len()
    out["blowout_games_per_coach_status_median"] = {
        s: float(bl.filter(pl.col("status") == s)["len"].median() or 0.0) for s in STATUSES
    }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
