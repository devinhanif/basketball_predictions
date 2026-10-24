# ruff: noqa: E501
# mypy: disable-error-code="arg-type,operator,index,assignment,no-any-return,misc,call-overload,attr-defined"
"""ROTATION_DEPTH_PPLAY minimum-data gate and live-regime audit (docs/prereg/ROTATION_DEPTH_PPLAY.md, DRAFT).

    cd <worktree> && .venv/bin/python -W ignore -m research.eval.rotation_depth_pplay_gate \
        --data-root <main checkout> --out reports/prereg_rotation_depth_pplay/gate.json

Read-only on ``nba.duckdb``. History queries cap ``season <= 2024`` (2025 is the holdout and is never
loaded; the stored RECENT_ROLE / PPLAY_MINUTES_VALID arm blocks are filtered to seasons 2023-24 on load).
The only live read is ``season = 2026`` (the forward log, exploratory, part L). Nothing is fitted and no
candidate arm is scored. Outcome uses, all disclosed: (1) the stored play flag ``y`` and the stored
arm probabilities (already frozen and scored, T458/T482) for the per-row sd of a planted log-loss
difference; (2) realised minutes to count the depth outcome (players >= 8 min) per team-game, descriptive
(its sd and mean); (3) the 2026 live log (part L), descriptive.

Parts: A edge-row counts per season (as-of definition); B depth-outcome moments; C power of the
planted-shift test (Devin's cited scratch effect sizes are inputs, not reproduced here); D CRPS
sensitivity of the unconditional integer CRPS to a p shift (analytic, stored conditional grids);
L live-regime audit (box rows per team-game 2022-24 vs 2026; decomposition of the 2026 forecast
over-count by population).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
EDGE_RANKS = (8, 12)  # as-of minutes rank within the team-game's eligible rows, inclusive
EDGE_MAX_STARTER10 = 0.5  # usual starters excluded
DEPTH_MIN = 8.0  # depth outcome: players with >= 8 minutes (Devin's scratch definition)
PACE_N = 10  # prior team games in the as-of pace
WIN_N = 20  # prior team games in the as-of win%
MIN_PRIOR_GAMES = 3  # as-of windows need >= 3 games, else NaN
# Devin's scratch effects (docs/FAN_KNOWLEDGE.md 2026-10-23, close games 2022-24): inputs to the power
# calculation only, NOT reproduced here.
SCRATCH_PACE_PER_SD = 0.167  # players >= 8 min per +1 SD prior pace
SCRATCH_WIN_PER_10PTS = -0.072  # players per +10 pts prior win%
SCALES = (0.5, 1.0, 2.0)
DEFF = (1.3, 2.0)  # design effects measured on P(play) deltas (PPLAY_MINUTES_VALID: 1.3-2.0)
LL_FLOOR = -0.010  # house P(play) log-loss floor
CRPS_FLOOR = -0.005
HW_BAR = 0.005
POWER_BAR = 0.5
ARMS_DIR = "data/ops/pplay_minutes_valid_cache/arms"


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load_blocks(root: Path) -> pl.DataFrame:
    """Stored arm probabilities per row (P0, P0c, R19), seasons 2023-24 only (other seasons never concatenated)."""
    cols = [
        "gid",
        "pid",
        "season",
        "y",
        "p_P0",
        "p_P0c",
        "p_R19",
        "m_starter10",
        "m_is_b2b_second",
        "m_n_played_prior",
    ]
    frames = []
    for f in sorted((root / ARMS_DIR).glob("block_*.npz")):
        z = np.load(f, allow_pickle=True)
        s = z["season"]
        keep = np.isin(s, SEASONS)
        if not keep.any():
            continue
        frames.append(pl.DataFrame({c: z[c][keep] for c in cols}))
    df = pl.concat(frames).rename({"gid": "game_id", "pid": "player_id"})
    df = df.unique(["game_id", "player_id"], keep="last")  # blocks must not double-count a row
    n_all = df.height
    df = df.filter(
        pl.col("game_id").str.starts_with("002")
    )  # regular season only (playoff rotations are a different regime)
    print(f"blocks: {n_all} rows, regular season {df.height}", flush=True)
    assert int(df["season"].max()) <= C.MAX_SEASON
    return df.with_columns(pl.col("player_id").cast(pl.Int64), pl.col("game_id").cast(pl.Utf8))


def team_game_table(con: Any) -> pl.DataFrame:
    """Per (game, team): box possessions, pace, win flag, depth outcome, as-of pace_10 / win20 / rest / game number."""
    tg = C.query(
        con,
        "SELECT g.game_id, g.game_date, g.season, s.team_id, "
        "SUM(coalesce(s.fga,0)) AS fga, SUM(coalesce(s.fta,0)) AS fta, SUM(coalesce(s.oreb,0)) AS oreb, "
        "SUM(coalesce(s.tov,0)) AS tov, SUM(CASE WHEN s.minutes >= 8 THEN 1 ELSE 0 END) AS depth8, "
        "SUM(CASE WHEN s.minutes > 0 THEN 1 ELSE 0 END) AS n_played "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id "
        "WHERE g.season <= 2024 AND g.game_id LIKE '002%' GROUP BY 1,2,3,4",
    )
    games = C.query(
        con,
        "SELECT game_id, home_team, away_team, home_pts, away_pts FROM games WHERE season <= 2024 AND game_id LIKE '002%'",
    )
    tg = tg.with_columns(
        (pl.col("fga") + 0.44 * pl.col("fta") - pl.col("oreb") + pl.col("tov")).alias("poss")
    )
    opp = tg.select(
        "game_id", pl.col("team_id").alias("opp_team"), pl.col("poss").alias("poss_opp")
    )
    tg = tg.join(games, on="game_id").with_columns(
        pl.when(pl.col("team_id") == pl.col("home_team"))
        .then(pl.col("away_team"))
        .otherwise(pl.col("home_team"))
        .alias("opp_team"),
        pl.when(pl.col("team_id") == pl.col("home_team"))
        .then(pl.col("home_pts") > pl.col("away_pts"))
        .otherwise(pl.col("away_pts") > pl.col("home_pts"))
        .cast(pl.Float64)
        .alias("win"),
    )
    tg = tg.join(opp, on=["game_id", "opp_team"], how="left").with_columns(
        ((pl.col("poss") + pl.col("poss_opp")) / 2.0).alias("pace_game")
    )
    tg = tg.sort(["team_id", "game_date", "game_id"])
    # as-of (strictly earlier games of the same team, across seasons): shift(1) before rolling
    tg = tg.with_columns(
        pl.col("pace_game")
        .shift(1)
        .rolling_mean(PACE_N, min_samples=MIN_PRIOR_GAMES)
        .over("team_id")
        .alias("pace10"),
        pl.col("win")
        .shift(1)
        .rolling_mean(WIN_N, min_samples=MIN_PRIOR_GAMES)
        .over("team_id")
        .alias("win20"),
        pl.col("game_date").diff().over("team_id").dt.total_days().alias("rest_days"),
        pl.int_range(pl.len()).over(["team_id", "season"]).alias("k_game"),
    )
    return tg


def attach_min10(blocks: pl.DataFrame, con: Any) -> pl.DataFrame:
    """As-of mean minutes over the player's last 10 PLAYED games strictly before the row's date."""
    pl_played = C.query(
        con,
        "SELECT s.player_id, g.game_date, g.game_id, s.minutes FROM player_game_stats s JOIN games g ON g.game_id = s.game_id "
        "WHERE g.season <= 2024 AND s.minutes > 0",
    ).sort(["player_id", "game_date", "game_id"])
    pl_played = pl_played.with_columns(
        pl.col("minutes")
        .shift(1)
        .rolling_mean(10, min_samples=3)
        .over("player_id")
        .alias("min10_after"),
    )
    # min10 as of a row = mean of the 10 played games strictly before; rows of DNP games take the player's latest played state before the date
    st = pl_played.select(
        "player_id",
        "game_date",
        pl.col("minutes").rolling_mean(10, min_samples=3).over("player_id").alias("state_min10"),
    )
    st = st.unique(["player_id", "game_date"], keep="last").sort("game_date")
    dates = C.query(con, "SELECT game_id, game_date FROM games WHERE season <= 2024")
    b = blocks.join(dates, on="game_id", how="left").sort("game_date")
    # strictly earlier: shift the state's date forward by one day
    st = st.with_columns(
        pl.col("player_id").cast(pl.Int64),
        (pl.col("game_date") + pl.duration(days=1)).alias("avail_from"),
    ).sort("avail_from")
    out = b.join_asof(
        st.select("player_id", "avail_from", "state_min10"),
        left_on="game_date",
        right_on="avail_from",
        by="player_id",
        strategy="backward",
    )
    return out.rename({"state_min10": "min10"})


def mark_edge(rows: pl.DataFrame, tgt: pl.DataFrame) -> pl.DataFrame:
    """Edge = not a usual starter and as-of minutes rank 8..12 within the team-game's eligible rows (rank by min10 desc, ties by player_id)."""
    team = C.query(
        C.connect(),
        "SELECT s.game_id, s.player_id, s.team_id FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024",
    )
    r = rows.join(team.unique(["game_id", "player_id"]), on=["game_id", "player_id"], how="left")
    r = r.with_columns(pl.col("min10").fill_null(0.0))
    r = r.sort(["game_id", "team_id", "min10", "player_id"], descending=[False, False, True, False])
    r = r.with_columns((pl.int_range(pl.len()).over(["game_id", "team_id"]) + 1).alias("rank"))
    r = r.with_columns(
        (
            (pl.col("rank") >= EDGE_RANKS[0])
            & (pl.col("rank") <= EDGE_RANKS[1])
            & (pl.col("m_starter10") < EDGE_MAX_STARTER10)
        ).alias("edge")
    )
    return r.join(
        tgt.select(
            "game_id", "team_id", "pace10", "win20", "rest_days", "k_game", "depth8", "n_played"
        ),
        on=["game_id", "team_id"],
        how="left",
    )


def counts(r: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    games = C.query(C.connect(), "SELECT game_id, game_date FROM games WHERE season <= 2024")
    r = r.join(games.rename({"game_date": "gd"}), on="game_id", how="left")
    for s in SEASONS:
        a = r.filter(pl.col("season") == s)
        e = a.filter(pl.col("edge"))
        wk = (
            e.with_columns(
                pl.col("gd").dt.iso_year().alias("iy"), pl.col("gd").dt.week().alias("iw")
            )
            .select("iy", "iw")
            .unique()
            .height
        )
        out[str(s)] = {
            "cellP_rows": a.height,
            "cellP_games": a["game_id"].n_unique(),
            "edge_rows": e.height,
            "edge_games": e["game_id"].n_unique(),
            "edge_team_games": e.select("game_id", "team_id").unique().height,
            "edge_player_seasons": e["player_id"].n_unique(),
            "edge_iso_weeks": wk,
            "edge_rows_per_team_game": e.height
            / max(1, e.select("game_id", "team_id").unique().height),
            "edge_play_rate": float(e["y"].mean()),
            "edge_p_P0_mean": float(e["p_P0"].mean()),
            "edge_pace10_nan_share": float(e["pace10"].is_null().mean()),
            "edge_win20_nan_share": float(e["win20"].is_null().mean()),
            "edge_min10_nan_share": float(e["min10"].is_null().mean()),
            "slice_first20_games_rows": e.filter(pl.col("k_game") < 20).height,
            "slice_bad_team_win20_lt035_rows": e.filter(pl.col("win20") < 0.35).height,
            "slice_second_night_b2b_rows": e.filter(pl.col("rest_days") == 1).height,
            "slice_starter_rows_untouched": a.filter(
                pl.col("m_starter10") >= EDGE_MAX_STARTER10
            ).height,
        }
    return out


def depth_moments(tg: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in SEASONS:
        a = tg.filter(pl.col("season") == s)
        out[str(s)] = {
            "team_games": a.height,
            "depth8_mean": float(a["depth8"].mean()),
            "depth8_sd": float(a["depth8"].std()),
            "n_played_mean": float(a["n_played"].mean()),
            "pace10_sd": float(a["pace10"].std()),
            "win20_sd": float(a["win20"].std()),
            "first20_games_depth8_mean": float(a.filter(pl.col("k_game") < 20)["depth8"].mean()),
            "later_depth8_mean": float(a.filter(pl.col("k_game") >= 20)["depth8"].mean()),
        }
    return out


def planted(r: pl.DataFrame, s: int, base: str, scale: float) -> pl.DataFrame:
    """Edge rows of season ``s`` with the planted probability: logit(p1) = logit(p0) + scale * (g_p * z_pace + g_w * z_win), gamma set so
    the team-level expected depth moves by the scratch effect x scale (pace +0.167 players per SD, win% -0.072 per 10 pts)."""
    e = r.filter(
        (pl.col("season") == s)
        & pl.col("edge")
        & pl.col("pace10").is_not_null()
        & pl.col("win20").is_not_null()
    )
    z_p = ((e["pace10"] - e["pace10"].mean()) / e["pace10"].std()).to_numpy()
    z_w = ((e["win20"] - e["win20"].mean()) / e["win20"].std()).to_numpy()
    per_sd_win = SCRATCH_WIN_PER_10PTS * e["win20"].std() / 0.10  # players per +1 SD win%
    n_edge_tg = e.height / e.select("game_id", "team_id").unique().height
    p0 = np.clip(e[base].to_numpy(), 1e-6, 1 - 1e-6)
    lg = np.log(p0 / (1 - p0))
    pv = float((p0 * (1 - p0)).mean())
    g_p = SCRATCH_PACE_PER_SD / (n_edge_tg * pv)
    g_w = per_sd_win / (n_edge_tg * pv)
    p1 = 1.0 / (1.0 + np.exp(-(lg + scale * (g_p * z_p + g_w * z_w))))
    wk = e["game_date"].dt.iso_year().cast(pl.Utf8) + "-" + e["game_date"].dt.week().cast(pl.Utf8)
    ts = e["team_id"].cast(pl.Utf8) + "-" + e["season"].cast(pl.Utf8)
    return e.select("game_id", "player_id", "season").with_columns(
        pl.Series("p0", p0), pl.Series("p1", p1), wk.alias("week"), ts.alias("team_season")
    )


def _clustered(d: np.ndarray, keys: dict[str, np.ndarray], n_total: float) -> dict[str, float]:
    """Half-widths of the mean of ``d`` (sampled rows) projected to ``n_total`` rows, by clustering key; plus the binding (widest) one."""
    out: dict[str, float] = {}
    for name, key in keys.items():
        st = C.cluster_stats(d, key)
        out[f"hw95_{name}"] = C.halfwidth(st["sd_row"], st["deff"], n_total)
        out[f"deff_{name}"] = st["deff"]
    out["hw95_binding"] = max(v for k, v in out.items() if k.startswith("hw95_"))
    return out


def power(r: pl.DataFrame, seasons: tuple[int, ...] = SEASONS) -> dict[str, Any]:
    """Expected log-loss gain and the sd of the per-row difference under y ~ Bernoulli(p_new), for the planted shift (part C), plus one
    simulated draw (seed 0) for the game / team-season / ISO-week clustered half-widths of the simulated per-row differences."""
    rng = np.random.default_rng(0)
    out: dict[str, Any] = {}
    for s in seasons:
        for base in ("p_P0", "p_P0c", "p_R19"):
            for sc in SCALES:
                pl_df = planted(r, s, base, sc)
                p0, p1 = pl_df["p0"].to_numpy(), pl_df["p1"].to_numpy()
                g = C.bernoulli_gain(p0, p1)
                n = len(p0)
                kl = float(g["kl"].mean())
                sd = float(
                    np.sqrt(g["var"].mean())
                )  # pooled sd of the per-row log-loss difference under y ~ Bernoulli(p_new)
                row: dict[str, Any] = {
                    "n_rows": n,
                    "mean_abs_dp": float(np.abs(p1 - p0).mean()),
                    "rms_dp": float(np.sqrt(((p1 - p0) ** 2).mean())),
                    "expected_ll_gain": kl,
                    "sd_row": sd,
                }
                for d in DEFF:
                    hw = C.halfwidth(sd, d, n)
                    se = hw / 1.96
                    row[f"hw95_deff{d}"] = hw
                    row[f"mde80_deff{d}"] = 2.8 * se
                    row[f"power_floor_deff{d}"] = C.pass_power(-kl, min(LL_FLOOR, -hw), se)
                if base == "p_P0c" and sc == 1.0:
                    y = (rng.random(n) < p1).astype(float)
                    dd = -(y * np.log(p1) + (1 - y) * np.log(1 - p1)) + (
                        y * np.log(p0) + (1 - y) * np.log(1 - p0)
                    )
                    keys = {
                        "game": pl_df["game_id"].to_numpy(),
                        "team_season": pl_df["team_season"].to_numpy(),
                        "week": pl_df["week"].to_numpy(),
                    }
                    row["simulated_draw_clustered"] = _clustered(dd, keys, n)
                out[f"{s}|{base}|scale{sc}"] = row
    return out


def _crps_int(q: np.ndarray, p: np.ndarray, y: np.ndarray, kmax: int) -> np.ndarray:
    """Integer-support CRPS of the mixture (1-p) at 0 + p * (equal-mass integer atoms q) at the realised y, summed over k = 0..kmax."""
    ks = np.arange(kmax + 1)
    g = (q[:, :, None] <= ks[None, None, :]).mean(axis=1)  # conditional cdf at each integer
    f = (1.0 - p)[:, None] + p[:, None] * g
    ind = (y[:, None] <= ks[None, :]).astype(float)
    return np.asarray(((f - ind) ** 2).sum(axis=1))


def crps_mc(
    root: Path, r: pl.DataFrame, base: str = "p_P0c", scale: float = 1.0, seed: int = 0
) -> dict[str, Any]:
    """Monte Carlo of the unconditional integer-CRPS difference (new minus old) when the planted shift is the truth, per stat, using the stored
    conditional integer grids of the edge rows (20 equal-mass atoms). Played rows carry the grid; n for the half-width is the season's edge
    row count (DNP rows take a played row's grid in the real harness). S = sum_k (1 - G(k))^2, the curvature of CRPS in p. The sd is the
    simulated per-row difference; the deff is the measured design effect of that simulated difference by game / team-season / ISO week
    (labels are simulated independently, so team-season and week deff here reflect only the persistence of the planted signal itself)."""
    from nba.eval.lower_tail_eval import load_components, production_grid

    rng = np.random.default_rng(seed)
    out: dict[str, Any] = {}
    plant = {s: planted(r, s, base, scale) for s in SEASONS}
    n_edge = {s: plant[s].height for s in SEASONS}
    for stat in STATS:
        c = load_components(stat, root / "reports/lower_tail")
        keys = pl.DataFrame(
            {
                "game_id": c["row_gid"].astype(str),
                "player_id": c["row_pid"].astype(np.int64),
                "season": c["row_season"].astype(np.int64),
            }
        ).with_row_index("i")
        pooled_d: list[np.ndarray] = []
        pooled_k: dict[str, list[np.ndarray]] = {"game": [], "team_season": [], "week": []}
        for s in SEASONS:
            j = keys.filter(pl.col("season") == s).join(
                plant[s].select("game_id", "player_id", "p0", "p1", "week", "team_season"),
                on=["game_id", "player_id"],
                how="inner",
            )
            sel = j["i"].to_numpy()
            q = production_grid(c, np.linspace(0.025, 0.975, 20))[sel]
            q = np.maximum.accumulate(np.maximum(np.ceil(q - 0.5), 0.0), axis=1)
            p0, p1 = j["p0"].to_numpy(), j["p1"].to_numpy()
            kk = q.shape[1]
            s_row = (q * (2 * (kk - np.arange(1, kk + 1)) + 1)).sum(axis=1) / (kk * kk)
            plays = rng.random(len(p1)) < p1
            y = np.where(plays, q[np.arange(len(q)), rng.integers(0, kk, len(q))], 0.0)
            kmax = int(q.max()) + 1
            d = np.concatenate(
                [
                    _crps_int(q[a : a + 2000], p1[a : a + 2000], y[a : a + 2000], kmax)
                    - _crps_int(q[a : a + 2000], p0[a : a + 2000], y[a : a + 2000], kmax)
                    for a in range(0, len(q), 2000)
                ]
            )
            ck = {
                "game": j["game_id"].to_numpy(),
                "team_season": j["team_season"].to_numpy(),
                "week": j["week"].to_numpy(),
            }
            pooled_d.append(d)
            for k2 in pooled_k:
                pooled_k[k2].append(ck[k2])
            sd = float(d.std(ddof=1))
            row: dict[str, Any] = {
                "n_grid_rows": len(q),
                "n_edge_rows": n_edge[s],
                "S_mean": float(s_row.mean()),
                "cond_mean": float(q.mean()),
                "expected_delta_new_minus_old": float(d.mean()),
                "sd_row": sd,
            }
            for dd in DEFF:
                hw = C.halfwidth(sd, dd, n_edge[s])
                se = hw / 1.96
                row[f"hw95_deff{dd}"] = hw
                row[f"power_floor_deff{dd}"] = C.pass_power(
                    float(d.mean()), min(CRPS_FLOOR, -hw), se
                )
            row["clustered"] = _clustered(d, ck, n_edge[s])
            out[f"{stat}|{s}"] = row
        dp = np.concatenate(pooled_d)
        n_tot = float(sum(n_edge.values()))
        sd = float(dp.std(ddof=1))
        prow: dict[str, Any] = {
            "n_edge_rows": n_tot,
            "expected_delta_new_minus_old": float(dp.mean()),
            "sd_row": sd,
        }
        for dd in DEFF:
            hw = C.halfwidth(sd, dd, n_tot)
            prow[f"hw95_deff{dd}"] = hw
            prow[f"power_floor_deff{dd}"] = C.pass_power(
                float(dp.mean()), min(CRPS_FLOOR, -hw), hw / 1.96
            )
        out[f"{stat}|pooled"] = prow
    return out


def live_regime(root: Path, t0: float) -> dict[str, Any]:
    """Part L (exploratory, descriptive): box rows per team-game in 2022-24 vs the 2026 live log; decomposition of the 2026 forecast
    over-count by population. Reads season = 2026 and seasons <= 2024 only."""
    import duckdb

    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    box = con.execute(
        "SELECT g.season, g.game_id, s.team_id, (s.minutes > 0)::INT AS played FROM player_game_stats s JOIN games g ON g.game_id = s.game_id "
        "WHERE (g.season <= 2024 OR g.season = 2026) AND g.game_id LIKE '002%'"
    ).pl()
    tgm = box.group_by(["season", "game_id", "team_id"]).agg(
        pl.len().alias("rows"), pl.col("played").sum().alias("played")
    )
    tgm = tgm.join(
        con.execute(
            "SELECT game_id, game_date FROM games WHERE season <= 2026 AND season <> 2025"
        ).pl(),
        on="game_id",
    )
    tgm = tgm.sort(["season", "game_date", "game_id", "team_id"]).with_columns(
        pl.int_range(pl.len()).over("season").alias("ord")
    )
    first = tgm.filter(pl.col("ord") < 34)  # first 17 games = the live sample size
    out: dict[str, Any] = {
        "rows_per_team_game_by_season": {
            str(a): float(b)
            for a, b in tgm.group_by("season").agg(pl.col("rows").mean()).sort("season").rows()
        },
        "played_per_team_game_by_season": {
            str(a): float(b)
            for a, b in tgm.group_by("season").agg(pl.col("played").mean()).sort("season").rows()
        },
        "first17games_rows_per_team_game": {
            str(a): float(b)
            for a, b in first.group_by("season").agg(pl.col("rows").mean()).sort("season").rows()
        },
        "first17games_played_per_team_game": {
            str(a): float(b)
            for a, b in first.group_by("season").agg(pl.col("played").mean()).sort("season").rows()
        },
    }
    sc = con.execute(
        "SELECT s.game_id, s.game_date, s.player_id, s.status, s.in_prod_log, s.p_play_prod, s.y, h.team_id, h.r_reason "
        "FROM forward_pplay_scores s LEFT JOIN (SELECT game_id, player_id, arg_max(team_id, made_at) AS team_id, arg_max(r_reason, made_at) AS r_reason "
        "FROM forward_pplay_shadow WHERE made_at <= tipoff - INTERVAL 60 MINUTE GROUP BY 1, 2) h USING (game_id, player_id) WHERE s.season = 2026"
    ).pl()
    games26 = sc["game_id"].n_unique()
    # is the player on the forecast team's official roster file of the run date? (data/rosters/<date>/<team>.parquet, cached per as-of date)
    marks = []
    for d in sorted(set(sc["game_date"].to_list())):
        fs = sorted((root / "data/rosters" / str(d)).glob("*.parquet"))
        if not fs:
            continue
        ros = pl.concat(
            [pl.read_parquet(f).select("team_id", "player_id") for f in fs]
        ).with_columns(pl.col("team_id").cast(pl.Int64), pl.col("player_id").cast(pl.Int64))
        anyr = sorted(set(ros["player_id"].to_list()))
        s = sc.filter(pl.col("game_date") == d).with_columns(
            pl.col("team_id").cast(pl.Int64), pl.col("player_id").cast(pl.Int64)
        )
        s = s.join(
            ros.with_columns(pl.lit(True).alias("on_team_roster")),
            on=["team_id", "player_id"],
            how="left",
        )
        marks.append(
            s.with_columns(
                pl.col("on_team_roster").fill_null(False),
                pl.col("player_id").is_in(anyr).alias("on_any_roster"),
            )
        )
    sc = pl.concat(marks).with_columns(
        pl.col("y").fill_null(0.0).alias("y0"),
        pl.when(pl.col("r_reason").str.contains("n_played_prior<5").fill_null(False))
        .then(pl.lit("n_prior_lt5"))
        .otherwise(pl.lit("n_prior_ge5"))
        .alias("hist"),
    )
    prod = sc.filter(pl.col("in_prod_log")).with_columns(
        pl.when((pl.col("status") == "not_in_box") & ~pl.col("on_team_roster"))
        .then(pl.lit("A_no_box_row_not_on_team_roster"))
        .when(pl.col("status") == "not_in_box")
        .then(pl.lit("D_no_box_row_on_team_roster"))
        .when(pl.col("hist") == "n_prior_lt5")
        .then(pl.lit("B_scored_n_prior_lt5"))
        .otherwise(pl.lit("C_scored_n_prior_ge5"))
        .alias("pop"),
        (pl.col("p_play_prod") - pl.col("y0")).alias("excess"),
    )
    dec = (
        prod.group_by("pop")
        .agg(
            pl.len().alias("n"),
            pl.col("p_play_prod").sum().alias("expected_played"),
            pl.col("y0").sum().alias("actual_played"),
        )
        .sort("pop")
    )
    gid_list = sorted(prod["game_id"].unique().to_list())
    per_game = (
        prod.group_by(["game_id", "pop"])
        .agg(pl.col("excess").sum())
        .pivot(on="pop", index="game_id", values="excess")
        .fill_null(0.0)
        .sort("game_id")
    )
    pops = [c for c in per_game.columns if c != "game_id"]
    mat = per_game.select(pops).to_numpy()
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(mat), size=(2000, len(mat)))
    boot = mat[idx].mean(axis=1)  # per-game mean excess by population, game-clustered
    tot = boot.sum(axis=1)
    ci = {
        p: [
            float(mat[:, i].mean()),
            float(np.quantile(boot[:, i], 0.025)),
            float(np.quantile(boot[:, i], 0.975)),
        ]
        for i, p in enumerate(pops)
    }
    ci["TOTAL"] = [
        float(mat.sum(axis=1).mean()),
        float(np.quantile(tot, 0.025)),
        float(np.quantile(tot, 0.975)),
    ]
    _ = gid_list
    out["live_2026"] = {
        "games": games26,
        "prod_log_rows": prod.height,
        "expected_played_per_game": float(prod["p_play_prod"].sum() / games26),
        "actual_played_per_game": float(prod["y0"].sum() / games26),
        "decomposition": [dict(zip(dec.columns, row, strict=True)) for row in dec.rows()],
        "excess_players_per_game_mean_lo_hi_game_cluster_boot": ci,
        "note": "pop A/D = status not_in_box (no box row at all; y counted 0 here, the live scorer excludes them); on_team_roster = the forecast team's official roster file of the run date contains the player. Descriptive, 17 games, exploratory, no multiplicity.",
    }
    con.close()
    _log("live regime done", t0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    t0 = time.time()
    C.DB = a.data_root / "nba.duckdb"
    con = C.connect()
    blocks = load_blocks(a.data_root)
    _log(f"stored arm rows {blocks.height}", t0)
    tg = team_game_table(con)
    _log(f"team-games {tg.height}", t0)
    rows = attach_min10(blocks, con)
    rows = mark_edge(rows, tg)
    _log(f"edge rows {int(rows['edge'].sum())}", t0)
    res: dict[str, Any] = {
        "measured_at": time.strftime("%a %b %d %Y %H:%M CDT"),
        "db_mtime": time.ctime((a.data_root / "nba.duckdb").stat().st_mtime),
        "edge_def": {"ranks": EDGE_RANKS, "max_starter10": EDGE_MAX_STARTER10},
        "A_counts": counts(rows),
        "B_depth_moments": depth_moments(tg.filter(pl.col("season").is_in(SEASONS))),
        "C_power": power(rows),
    }
    _log("power done", t0)
    res["D_crps_mc"] = crps_mc(a.data_root, rows)
    res["L_live_regime"] = live_regime(a.data_root, t0)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=float))
    _log(f"wrote {a.out}", t0)


if __name__ == "__main__":
    main()
    _ = math
