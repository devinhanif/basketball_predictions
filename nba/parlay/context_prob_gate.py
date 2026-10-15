# ruff: noqa: E501
"""Minimum-data gate for PARLAY_CONTEXT_PROB (docs/prereg/PARLAY_CONTEXT_PROB.md), non-negotiable 8.

Question the rule asks: does a context-conditioned copula forecast same-game parlay outcomes more accurately
(joint log loss / calibration) than the base copula? Before any rule is frozen this module answers, on the
real data it would run on and WITHOUT computing the contrast:

* how many settled same-game leg pairs exist per season in each context cell, built from REAL posted market
  legs (DraftKings t60 main lines, over side, half-point lines, players who played, production OOF marginals);
* how many games stand behind them (the clustering unit);
* the power of the primary contrast, analytically: the copula correlation shift of each cell is taken from the
  2020-22 inventory (reports/parlay/context_copula_sizing.json, a different sample from the select/report
  seasons), the leg marginals are the stored 2023-24 forecasts, and the expected paired log-loss difference and
  its variance are computed from forecasts only (no outcome enters).

Read-only: ``nba.duckdb`` / ``nba_history.duckdb`` / ``odds_history.duckdb`` are opened ``read_only=True``;
every query carries ``season <= 2024`` and the loader refuses a later season (2025 is the frozen holdout).
Outputs one JSON under ``--out``. No prices are read for any decision, no stake, no order.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray
from scipy.special import ndtri
from scipy.stats import norm

MAX_SEASON = 2024
SELECT_SEASON = 2023
REPORT_SEASON = 2024
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
REFERENCE_BOOK = "draftkings"
SNAPSHOT = "t60"
VAC_MIN_THRESHOLD = 24.0
COMPETE_TOP = 3
MIN_PAIRS = 300  # inventory floor per side of a cell
MIN_GAMES = 40
ALPHA = 0.05
HERMITE_TERMS = 12
EFFECT_SCALES = (1.0, 0.5)  # 1.0 = the 2020-22 point estimate; 0.5 = a winner's-curse haircut
P_FLOOR = 1e-4


def assert_seasons(seasons: Sequence[int]) -> None:
    if any(int(s) > MAX_SEASON for s in seasons):
        raise ValueError("seasons after 2024 are not loaded by the PARLAY_CONTEXT_PROB gate")


# --------------------------------------------------------------------------- Gaussian-copula pair algebra


def orthant_prob(
    p1: NDArray[np.float64], p2: NDArray[np.float64], rho: NDArray[np.float64] | float
) -> NDArray[np.float64]:
    """P(Y1 yes, Y2 yes) for two yes-events with marginals p1, p2 under a Gaussian copula with latent
    correlation rho, by the Mehler (tetrachoric) series: p1 p2 + phi(a) phi(b) sum_k rho^k He_{k-1}(a) He_{k-1}(b) / k!,
    a = ndtri(1 - p1). Exact as the number of terms grows; 12 terms are tight for |rho| <= 0.6."""
    p1 = np.clip(np.asarray(p1, dtype=float), P_FLOOR, 1 - P_FLOOR)
    p2 = np.clip(np.asarray(p2, dtype=float), P_FLOOR, 1 - P_FLOOR)
    r = np.asarray(rho, dtype=float)
    a, b = ndtri(1 - p1), ndtri(1 - p2)
    ha_prev, ha = np.zeros_like(a), np.ones_like(a)  # He_{-1}=0 (unused), He_0 = 1
    hb_prev, hb = np.zeros_like(b), np.ones_like(b)
    total = np.zeros_like(a)
    fact = 1.0
    rk = np.ones_like(a * r)
    for k in range(1, HERMITE_TERMS + 1):
        fact *= k
        rk = rk * r
        total = total + rk * ha * hb / fact  # He_{k-1}(a) He_{k-1}(b) rho^k / k!
        # recurrence He_{n+1}(x) = x He_n(x) - n He_{n-1}(x), n = k-1
        n = k - 1
        ha, ha_prev = a * ha - n * ha_prev, ha
        hb, hb_prev = b * hb - n * hb_prev, hb
    out = p1 * p2 + norm.pdf(a) * norm.pdf(b) * total
    return np.asarray(np.clip(out, P_FLOOR * P_FLOOR, np.minimum(p1, p2)), dtype=float)


def paired_delta_moments(
    p_base: NDArray[np.float64], p_ctx: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Per unit, when the TRUTH is ``p_ctx``: the mean and variance of d = logloss(ctx) - logloss(base)
    (negative = the context copula is better). d = -(y log(pc/pb) + (1-y) log((1-pc)/(1-pb))), y ~ Bernoulli(pc);
    mean = -KL(pc || pb), variance = pc (1-pc) (log(pc/pb) - log((1-pc)/(1-pb)))^2."""
    pc = np.clip(p_ctx, P_FLOOR, 1 - P_FLOOR)
    pb = np.clip(p_base, P_FLOOR, 1 - P_FLOOR)
    la = np.log(pc / pb)
    lb = np.log((1 - pc) / (1 - pb))
    mean = -(pc * la + (1 - pc) * lb)
    var = pc * (1 - pc) * (la - lb) ** 2
    return mean, var


def power_bounds(
    mean_sum: float, var_sum: float, n_units: int, n_games: int, alpha: float
) -> tuple[float, float]:
    """(lower, upper) power of a one-sided test of mean d < 0 at ``alpha`` from the summed per-unit moments.
    Upper: units independent (SE = sd / sqrt(n_units)). Lower: all units of a game perfectly dependent
    (SE = sd / sqrt(n_games)). The truth lies between; clustering can only lower the upper number."""
    if n_units <= 0 or var_sum <= 0:
        return 0.0, 0.0
    m = -mean_sum / n_units  # positive = ctx better
    sd = math.sqrt(var_sum / n_units)
    za = float(norm.ppf(1 - alpha))
    up = float(norm.cdf(math.sqrt(n_units) * m / sd - za))
    lo = float(norm.cdf(math.sqrt(max(n_games, 1)) * m / sd - za))
    return lo, up


# --------------------------------------------------------------------------- labels (as nba/parlay/context_corr.py)


def lab_lead(li: NDArray[np.int8], lj: NDArray[np.int8]) -> NDArray[np.int8]:
    return np.where((li == 1) | (lj == 1), 1, np.where((li == 0) & (lj == 0), 0, -1)).astype(
        np.int8
    )


def lab_vac(ti: NDArray[np.int8], tj: NDArray[np.int8], kind: str) -> NDArray[np.int8]:
    if kind == "opponent":
        return np.where((ti == 1) | (tj == 1), 1, np.where((ti == 0) & (tj == 0), 0, -1)).astype(
            np.int8
        )
    return np.where(ti == tj, ti, -1).astype(np.int8)


def tv_labels(
    vac: NDArray[np.float64], has_report: NDArray[np.bool_]
) -> tuple[NDArray[np.int8], NDArray[np.int8]]:
    """vac24 (1 if vacated minutes >= 24, 0 if below, -1 no report) and vac48 (1 if >= 48, 0 if < 24, -1 between / no report)."""
    t24 = np.where(has_report, np.where(vac >= VAC_MIN_THRESHOLD, 1, 0), -1).astype(np.int8)
    t48 = np.where(
        has_report,
        np.where(vac >= 2 * VAC_MIN_THRESHOLD, 1, np.where(vac < VAC_MIN_THRESHOLD, 0, -1)),
        -1,
    ).astype(np.int8)
    return t24, t48


# --------------------------------------------------------------------------- inventory cells


def load_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """class -> {'all': corr_z estimate pooled, 'cells': {ctx: (rho_ctx, rho_ref, n_pairs_ctx, n_games_ctx)}}."""
    d = json.loads(Path(path).read_text())
    out: dict[str, dict[str, Any]] = {}
    for c in d["classes"]:
        out[c["class"]] = {
            "all": float(c["all"]["corr_z"]["est"]),
            "cells": {
                x["ctx"]: (
                    float(x["corr_z"]["ctx"]),
                    float(x["corr_z"]["ref"]),
                    float(x["corr_z"]["diff"]),
                    bool(x["corr_z"]["excludes_0"]),
                )
                for x in c["cells"]
            },
        }
    return out


# --------------------------------------------------------------------------- data (read-only)


def build_legs(root: Path) -> pl.DataFrame:
    """Real posted legs: DraftKings t60 over main lines on pts/reb/ast/fg3m, seasons 2023-24, joined to the
    stored production OOF quantile grid (marginal P(over)) and to the box score (played, settled). One row per leg."""
    import duckdb

    from nba.parlay.qdist import QuantileGridDist

    con = duckdb.connect(str(root / "data/odds/odds_history.duckdb"), read_only=True)
    try:
        mk = ",".join(f"'{m}'" for m in MARKET_STAT)
        ml = con.execute(
            "SELECT season, game_id, player_id, market, point FROM odds_history "
            f"WHERE season IN ({SELECT_SEASON}, {REPORT_SEASON}) AND season <= {MAX_SEASON} "
            f"AND snapshot_kind = '{SNAPSHOT}' AND book = ? AND market IN ({mk}) "
            "AND side = 'over' AND player_id IS NOT NULL",
            [REFERENCE_BOOK],
        ).pl()
    finally:
        con.close()
    assert_seasons(ml["season"].unique().to_list())
    ml = (
        ml.group_by(["season", "game_id", "player_id", "market"])
        .agg(pl.col("point").first())
        .with_columns(pl.col("market").replace_strict(MARKET_STAT).alias("stat"))
    )
    oof = pl.read_parquet(root / "reports/context_residual/oof_context_residual.parquet").select(
        "game_id", "player_id", "target", "season", "q_grid"
    )
    assert_seasons(oof["season"].unique().to_list())
    db = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    try:
        pgs = db.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season IN ({SELECT_SEASON}, {REPORT_SEASON}) AND g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        db.close()
    legs = (
        ml.join(
            oof,
            left_on=["game_id", "player_id", "stat", "season"],
            right_on=["game_id", "player_id", "target", "season"],
            how="inner",
        )
        .join(pgs, on=["game_id", "player_id"], how="inner")
        .filter(pl.col("minutes") > 0)
    )
    ps = [
        QuantileGridDist.from_grid(r["q_grid"]).p_ge(float(r["point"]) + 0.5)
        for r in legs.select("q_grid", "point").iter_rows(named=True)
    ]
    return legs.with_columns(pl.Series("p", ps)).drop("q_grid")


def units_for_class(
    legs: pl.DataFrame, ctx_tg: pl.DataFrame, ctx_pl: pl.DataFrame, name: str
) -> pl.DataFrame:
    """Leg-pair units of one class with context labels (1 / 0 / -1 per context, as context_corr.pair_table).
    Columns: season, game_id, p_a, p_b, and one int8 column per context."""
    kind, spec = name.split(":")
    tg = ctx_tg
    base = legs.join(ctx_pl, on=["game_id", "player_id"], how="left").join(
        tg, on=["game_id", "team_id"], how="left"
    )
    # rank of the player among the team's scored players in the stat (compete = top 3 for both legs)
    base = base.with_columns(
        pl.struct(["stat", "m10_pts", "m10_reb", "m10_ast", "m10_fg3m"])
        .map_elements(lambda r: r["m10_" + r["stat"]], return_dtype=pl.Float64)
        .alias("m10s")
    ).with_columns(
        pl.col("m10s")
        .rank("ordinal", descending=True)
        .over(["game_id", "team_id", "stat"])
        .alias("rk")
    )
    cols = [
        "season", "game_id", "player_id", "team_id", "stat", "p", "rk", "t24", "t48", "blow",
        "fav", *[f"lead_{s}" for s in STATS],
    ]  # fmt: skip
    base = base.select(cols)
    if kind == "same_player":
        a, b = spec.split("-")
        j = base.filter(pl.col("stat") == a).join(
            base.filter(pl.col("stat") == b),
            on=["season", "game_id", "player_id", "team_id"],
            suffix="_b",
        )
    elif kind in ("teammate", "teammate_cross"):
        if kind == "teammate":
            a = b = spec.split("-")[0]
        else:
            a, b = (x.split("_")[0] for x in spec.split("-"))
        j = (
            base.filter(pl.col("stat") == a)
            .join(
                base.filter(pl.col("stat") == b), on=["season", "game_id", "team_id"], suffix="_b"
            )
            .filter(pl.col("player_id") != pl.col("player_id_b"))
        )
        if kind == "teammate":
            j = j.filter(pl.col("player_id") < pl.col("player_id_b"))
    elif kind == "opponent":
        a = b = spec.split("-")[0]
        j = (
            base.filter(pl.col("stat") == a)
            .join(base.filter(pl.col("stat") == b), on=["season", "game_id"], suffix="_b")
            .filter(
                (pl.col("team_id") != pl.col("team_id_b"))
                & (pl.col("player_id") < pl.col("player_id_b"))
            )
        )
    elif kind in ("own_margin", "total"):
        a = spec
        j = base.filter(pl.col("stat") == a).with_columns(
            pl.lit(0.5).alias("p_b"), pl.col("t24").alias("t24_b"), pl.col("t48").alias("t48_b")
        )
        j = j.with_columns(
            *[pl.col(f"lead_{s}").alias(f"lead_{s}_b") for s in STATS],
            pl.col("stat").alias("stat_b"),
        )
    else:
        raise ValueError(name)
    ta, tb = j["t24"].to_numpy().astype(np.int8), j["t24_b"].to_numpy().astype(np.int8)
    ua, ub = j["t48"].to_numpy().astype(np.int8), j["t48_b"].to_numpy().astype(np.int8)
    la = np.array(
        [
            r[f"lead_{s}"]
            for r, s in zip(
                j.select([f"lead_{x}" for x in STATS]).iter_rows(named=True), j["stat"], strict=True
            )
        ],
        dtype=float,
    )
    lb = np.array(
        [
            r[f"lead_{s}_b"]
            for r, s in zip(
                j.select([f"lead_{x}_b" for x in STATS]).iter_rows(named=True),
                j["stat_b"],
                strict=True,
            )
        ],
        dtype=float,
    )
    la = np.where(np.isnan(la), -1, la).astype(np.int8)
    lb = np.where(np.isnan(lb), -1, lb).astype(np.int8)
    if kind == "own_margin" or kind == "total":
        lead = np.where(la >= 0, la, -1).astype(np.int8)
    else:
        lead = lab_lead(la, lb)
    if kind in ("teammate", "teammate_cross"):
        comp = np.where(
            (j["rk"].to_numpy() <= COMPETE_TOP) & (j["rk_b"].to_numpy() <= COMPETE_TOP), 1, 0
        ).astype(np.int8)
    else:
        comp = np.full(j.height, -1, dtype=np.int8)
    blow = j["blow"].to_numpy().astype(np.int8)
    fav = (
        j["fav"].to_numpy().astype(np.int8)
        if kind in ("own_margin", "total")
        else np.full(j.height, -1, dtype=np.int8)
    )
    lxc = np.where((lead == 1) & (comp == 1), 1, np.where((lead == 0) & (comp == 0), 0, -1)).astype(
        np.int8
    )
    return pl.DataFrame(
        {
            "season": j["season"],
            "game_id": j["game_id"],
            "p_a": j["p"],
            "p_b": j["p_b"] if "p_b" in j.columns else pl.Series([0.5] * j.height),
            "vac24": lab_vac(ta, tb, kind),
            "vac48": lab_vac(ua, ub, kind),
            "lead_out": lead,
            "compete": comp,
            "lead_x_compete": lxc,
            "blowout": blow,
            "fav": fav,
        }
    )


def team_game_context(ctx: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Per team-game labels and per player-game m10 from a context frame (columns of the production feature frame
    plus lead_out_<stat>). Blowout label: top / bottom tercile of |Elo margin| WITHIN each season (-1 between)."""
    tg = ctx.group_by(["game_id", "team_id"]).agg(
        pl.col("season").first(),
        pl.col("vac_min").first(),
        pl.col("has_report").first(),
        pl.col("abs_margin").first(),
        pl.col("exp_margin").first(),
        *[pl.col(f"lead_out_{s}").first().alias(f"lead_{s}") for s in STATS],
    )
    q = tg.group_by("season").agg(
        pl.col("abs_margin").quantile(1 / 3).alias("q1"),
        pl.col("abs_margin").quantile(2 / 3).alias("q2"),
    )
    tg = tg.join(q, on="season")
    t24, t48 = tv_labels(tg["vac_min"].to_numpy().astype(float), (tg["has_report"].to_numpy() == 1))
    blow = np.where(
        tg["abs_margin"].to_numpy() >= tg["q2"].to_numpy(),
        1,
        np.where(tg["abs_margin"].to_numpy() <= tg["q1"].to_numpy(), 0, -1),
    ).astype(np.int8)
    tg = tg.with_columns(
        pl.Series("t24", t24),
        pl.Series("t48", t48),
        pl.Series("blow", blow),
        (pl.col("exp_margin") > 0).cast(pl.Int8).alias("fav"),
    ).drop("season", "q1", "q2")
    pl_ctx = ctx.select("game_id", "player_id", "m10_pts", "m10_reb", "m10_ast", "m10_fg3m")
    return tg, pl_ctx


# --------------------------------------------------------------------------- cell counts and power


def cell_row(
    name: str,
    ctxname: str,
    units: pl.DataFrame,
    inv: dict[str, Any],
    seasons: Sequence[int],
) -> dict[str, Any]:
    """Counts and power for one (class, context) cell over ``seasons`` of units."""
    u = units.filter(pl.col("season").is_in(list(seasons)))
    lab = u[ctxname].to_numpy()
    rho_all = inv["all"]
    rho_ctx, rho_ref, diff, excl = inv["cells"][ctxname]
    n_ctx, n_ref = int((lab == 1).sum()), int((lab == 0).sum())
    g_ctx = int(u.filter(pl.col(ctxname) == 1)["game_id"].n_unique())
    g_ref = int(u.filter(pl.col(ctxname) == 0)["game_id"].n_unique())
    row: dict[str, Any] = {
        "class": name,
        "ctx": ctxname,
        "n_ctx": n_ctx,
        "n_ref": n_ref,
        "games_ctx": g_ctx,
        "games_ref": g_ref,
        "inventory_diff_corr_z": diff,
        "inventory_z_ci_excludes_0": excl,
        "meets_inventory_floor": bool(
            min(n_ctx, n_ref) >= MIN_PAIRS and min(g_ctx, g_ref) >= MIN_GAMES
        ),
    }
    pa, pb = u["p_a"].to_numpy(), u["p_b"].to_numpy()
    base = orthant_prob(pa, pb, rho_all)
    games_all = int(u["game_id"].n_unique())
    for scale in EFFECT_SCALES:
        rho_t = np.where(
            lab == 1, rho_all + scale * (rho_ctx - rho_all), rho_all + scale * (rho_ref - rho_all)
        )
        rho_t = np.where(lab == -1, rho_all, rho_t)
        m, v = paired_delta_moments(base, orthant_prob(pa, pb, rho_t))
        ms, vs = float(m.sum()), float(v.sum())
        key = f"scale{scale:g}"
        row[f"mean_d_{key}"] = ms / max(len(m), 1)  # whole-class mean (diluted by unaffected units)
        in_ctx = lab == 1
        if in_ctx.any():
            row[f"mean_d_ctx_units_{key}"] = float(m[in_ctx].mean())
            row[f"median_abs_dp_ctx_units_{key}"] = float(
                np.median(np.abs(orthant_prob(pa, pb, rho_t)[in_ctx] - base[in_ctx]))
            )
        for alpha_name, alpha in (("a05", ALPHA), ("a05_over_114", ALPHA / 114.0)):
            lo, up = power_bounds(ms, vs, len(m), games_all, alpha)
            row[f"power_lo_{key}_{alpha_name}"] = lo
            row[f"power_up_{key}_{alpha_name}"] = up
        # minimum detectable mean d per unit at 80% power (positive = log-loss improvement), same two bounds
        if len(m) and vs > 0:
            sd = math.sqrt(vs / len(m))
            zz = float(norm.ppf(1 - ALPHA) + norm.ppf(0.8))
            row[f"mde_up_{key}"] = zz * sd / math.sqrt(len(m))
            row[f"mde_lo_{key}"] = zz * sd / math.sqrt(max(games_all, 1))
            row[f"effect_multiple_for_80_up_{key}"] = (
                zz * sd / math.sqrt(len(m)) / max(-ms / len(m), 1e-12)
            )
    row["n_units"] = int(u.height)
    row["games_all"] = games_all
    return row


def run_gate(root: Path, ctx_path: Path, inventory_path: Path, out: Path) -> dict[str, Any]:
    ctx = pl.read_parquet(ctx_path)
    assert_seasons(ctx["season"].unique().to_list())
    tg, pl_ctx = team_game_context(ctx)
    legs = build_legs(root)
    inv = load_inventory(inventory_path)
    rows: list[dict[str, Any]] = []
    for name, spec in inv.items():
        units = units_for_class(legs, tg, pl_ctx, name)
        for cx in spec["cells"]:
            for label, seas in (
                ("report_2024", (REPORT_SEASON,)),
                ("select_2023", (SELECT_SEASON,)),
                ("pooled_2023_24", (SELECT_SEASON, REPORT_SEASON)),
            ):
                r = cell_row(name, cx, units, spec, seas)
                r["season_set"] = label
                rows.append(r)
    res = {
        "legs_per_season": {
            str(s): int(legs.filter(pl.col("season") == s).height)
            for s in (SELECT_SEASON, REPORT_SEASON)
        },
        "games_per_season": {
            str(s): int(legs.filter(pl.col("season") == s)["game_id"].n_unique())
            for s in (SELECT_SEASON, REPORT_SEASON)
        },
        "cells": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    return res


# --------------------------------------------------------------------------- context frame (as-of labels)


def build_context(root: Path, config_dir: Path, out: Path) -> int:
    """Per player-game context frame for seasons 2022-24 (the production feature frame of the real-tip red-team
    build plus the leader-OUT flags from the official pre-tip report gated on real tips). Seasons 2019-21 from the
    history store feed only the as-of rolling states. Read-only; one parquet under ``out``."""
    import yaml

    from nba.db.connect import connect_with_retry
    from nba.features.game_tipoff import attach_real_tips, build_game_tipoff
    from nba.features.injury_report import latest_pretip_flagged
    from nba.parlay import context_data as C
    from nba.props.context_residual import ContextResidualConfig, player_states

    def side(
        path: Path, seasons: tuple[int, ...]
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        ss = ",".join(map(str, seasons))
        con = connect_with_retry(path, read_only=True)
        try:
            g = con.execute(
                "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games "
                f"WHERE season IN ({ss}) AND season <= {MAX_SEASON}"
            ).pl()
            p = con.execute(
                f"SELECT {C.PGS_COLS} FROM player_game_stats s JOIN games g USING (game_id) "
                f"WHERE g.season IN ({ss}) AND g.season <= {MAX_SEASON}"
            ).pl()
            a = con.execute(
                "SELECT s.game_id, s.player_id, s.status, s.as_of, s.source FROM player_availability s "
                f"JOIN games g USING (game_id) WHERE g.season IN ({ss}) AND g.season <= {MAX_SEASON}"
            ).pl()
        finally:
            con.close()
        return g, p, a

    cur = side(root / "nba.duckdb", (2022, 2023, 2024))
    hist = side(root / "data/history/nba_history.duckdb", (2019, 2020, 2021))
    games = pl.concat([cur[0], hist[0]], how="vertical_relaxed")
    pgs = pl.concat([cur[1], hist[1]], how="vertical_relaxed")
    avail = pl.concat([cur[2], hist[2]], how="vertical_relaxed")
    assert_seasons(games["season"].unique().to_list())
    cfg = ContextResidualConfig(
        **yaml.safe_load((config_dir / "context_residual.yaml").read_text()).get("model", {})
    )
    tips = pl.concat(
        [
            build_game_tipoff(root / "data/schedule"),
            build_game_tipoff(root / "data/history/schedule"),
        ],
        how="vertical_relaxed",
    ).unique("game_id", keep="first", maintain_order=True)
    rows = (
        avail.filter(pl.col("source").is_in(list(cfg.report.sources)) & pl.col("player_id").is_not_null())
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id", "player_id", pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")), "game_date",
        )
    )  # fmt: skip
    rows = attach_real_tips(rows, tips)
    hist_ids = sorted(set(hist[0]["game_id"].to_list()))
    rows = rows.filter(~(pl.col("tip_et").is_null() & pl.col("game_id").is_in(hist_ids)))
    flagged, _ = latest_pretip_flagged(rows, cfg.report)
    info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    g2 = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = (
        pgs.select(list(C.PROD_PGS))
        .join(g2.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    out_rows = C.out_rotation_rows(played, player_states(played), flagged, info)
    feats = pl.read_parquet(
        root / "data/reviews/redteam_production_2026-10-09/feats_realtip.parquet"
    )
    assert_seasons(feats["season"].unique().to_list())
    lead = C.leader_out_flags(feats, out_rows)
    ctx = feats.join(lead, on=["game_id", "team_id"], how="left").with_columns(
        [
            pl.when(pl.col("has_report") == 1)
            .then(pl.col(f"lead_out_{s}").fill_null(0))
            .otherwise(None)
            .alias(f"lead_out_{s}")
            for s in STATS
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    ctx.write_parquet(out)
    return ctx.height


def render_md(res: dict[str, Any]) -> str:
    """Short human summary of a gate result (counts and power bounds only; no contrast exists)."""
    df = pl.DataFrame(res["cells"])
    lines = [
        "# PARLAY_CONTEXT_PROB minimum-data gate (counts and power; no contrast computed)",
        "",
        f"Real legs scored per season (DraftKings t60 over main lines, played, OOF marginal): {res['legs_per_season']}; "
        f"games: {res['games_per_season']}.",
        "",
        "| season set | cells | meet inventory floor (>=300 pairs, >=40 games per side) | inventory z-CI excludes 0 | "
        "best upper-bound power (point estimate) | best upper-bound power (half effect) | best lower-bound power (point) | "
        "cells with upper-bound power >= 0.8 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ss in ("select_2023", "report_2024", "pooled_2023_24"):
        sub = df.filter(pl.col("season_set") == ss)
        lines.append(
            f"| {ss} | {sub.height} | {int(sub['meets_inventory_floor'].sum())} | {int(sub['inventory_z_ci_excludes_0'].sum())} | "
            f"{sub['power_up_scale1_a05'].to_numpy().max():.3f} | {sub['power_up_scale0.5_a05'].to_numpy().max():.3f} | "
            f"{sub['power_lo_scale1_a05'].to_numpy().max():.3f} | {int((sub['power_up_scale1_a05'] >= 0.8).sum())} |"
        )
    lines += [
        "",
        "Power = one-sided alpha 0.05, paired log loss, truth = the 2020-22 correlation shift (scale 1) or half of it.",
        "Upper bound treats units as independent; lower bound treats all units of a game as one (perfectly dependent).",
        "",
        "## Top 12 cells by upper-bound power, report season 2024",
        "",
        "| class | context | pairs ctx | games ctx | expected mean d per treated unit | median abs dp | power up | power lo | MDE up | MDE lo |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    top = (
        df.filter(pl.col("season_set") == "report_2024")
        .sort("power_up_scale1_a05", descending=True)
        .head(12)
    )
    for tr in top.iter_rows(named=True):
        lines.append(
            f"| {tr['class']} | {tr['ctx']} | {tr['n_ctx']} | {tr['games_ctx']} | {tr['mean_d_ctx_units_scale1']:.2e} | "
            f"{tr['median_abs_dp_ctx_units_scale1']:.4f} | {tr['power_up_scale1_a05']:.3f} | {tr['power_lo_scale1_a05']:.3f} | "
            f"{tr['mde_up_scale1']:.1e} | {tr['mde_lo_scale1']:.1e} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="PARLAY_CONTEXT_PROB minimum-data gate (read-only; no contrast)."
    )
    ap.add_argument(
        "--root", required=True, help="checkout holding nba.duckdb and data/ (read-only)"
    )
    ap.add_argument("--configs", default="configs")
    ap.add_argument("--ctx", required=True, help="context parquet (built here if missing)")
    ap.add_argument("--inventory", default="reports/parlay/context_copula_sizing.json")
    ap.add_argument("--out", required=True, help="gate JSON to write")
    a = ap.parse_args(argv)
    ctx_path = Path(a.ctx)
    if not ctx_path.exists():
        build_context(Path(a.root), Path(a.configs), ctx_path)
    res = run_gate(Path(a.root), ctx_path, Path(a.inventory), Path(a.out))
    Path(a.out).with_suffix(".md").write_text(render_md(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
