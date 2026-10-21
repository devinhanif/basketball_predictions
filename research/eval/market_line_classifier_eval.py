"""MARKET_LINE_CLASSIFIER harness (rule: docs/prereg/MARKET_LINE_CLASSIFIER.md; DRAFT until frozen).

    python -W ignore -m research.eval.market_line_classifier_eval gate --allow-draft \
        --doc docs/prereg/MARKET_LINE_CLASSIFIER.md      # minimum-data checks 1-4 (draft allowed)
    python -W ignore -m research.eval.market_line_classifier_eval smoke --doc ... --sha <64 hex>
    python -W ignore -m research.eval.market_line_classifier_eval run   --doc ... --sha <64 hex>
    python -W ignore -m research.eval.market_line_classifier_eval report --doc ... --sha <64 hex>

``gate`` measures the four minimum-data checks on the real data and writes
``reports/prereg_market_line/gate.json``; no arm is fitted (M0 is the book's own probability and is
only compared with ODDS_HISTORY's stored log loss). ``smoke`` fits one month block. ``run`` is the
full walk-forward (a checkpoint per month block under ``data/market_line_classifier/``, progress per
block). ``report`` scores the stored per-row predictions (paired, game-clustered) and writes
``results.json`` and ``reports/prereg_market_line.md``. ``smoke``/``run``/``report`` need ``--sha``
equal to the sha256 of everything above the marker line and re-run the gate; ``--allow-draft``
skips the hash check and is for the gate stage only.

Pairing, de-vig and the leak guard are ODDS_HISTORY's (``research.eval.odds_history_eval``) called,
not copied: stored ``reports/odds_history/{model,market}_rows_<season>.parquet`` are joined by
``pairs_for_season``. ``nba.duckdb`` and the odds DB are opened read-only; ``season <= 2024`` is
asserted; season 2025 is never loaded. Heavy stages take ``data/ops/heavy.lock``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import odds_history_eval as oh
from research.eval.minutes_hazard_eval import HeavyLock, frozen_sha

DEFAULT_DOC = Path("docs/prereg/MARKET_LINE_CLASSIFIER.md")
PREREG = DEFAULT_DOC
OUT_DIR = Path("reports/prereg_market_line")
REPORT_PATH = Path("reports/prereg_market_line.md")
CACHE = Path("data/market_line_classifier")
ODDS_OUT = oh.OUT_DIR
STATS = oh.STATS
SEASONS = (2023, 2024)
SELECT_SEASON, REPORT_SEASON = SEASONS
MAX_SEASON = 2024
N_BOOT = 2000
BOOT_SEED = 0
MIN_PAIRS = 5000
COVERAGE_MIN = 0.95
GAP_PP = 2.0
TOL_M0 = 1e-6
MIN_TRAIN_ROWS = (
    5000  # rows before the first scored block (draft: 2022 has no pairs, so block 1 is unscored)
)
MIN_CELL = 300  # missingness cells below this n are listed but not judged
LEAD_MIN = oh.LEAD_MIN
BOOK = "draftkings"
TIER_EDGES = (10.0, 17.0, 24.0)  # prior-10 pts: <10, 10-17, 17-24, 24+
EDGE_MIN = oh.EDGE_MIN
FLOOR, FLOOR_M3, FLOOR_M4, ECE_TOL = -0.002, -0.001, -0.001, 0.005
STAT_CODE = {s: float(i) for i, s in enumerate(STATS)}

# feature lists (draft "Fixed design"; final at the freeze)
M2_FEATURES = [
    "p_book_over",
    "p_cons_over",
    "line",
    "z",
    "p_grid_over",
    "stat_code",
    "tier",
    "starter10",
    "is_b2b",
    "n_out_rot",
    "tag",
    "game_total",
    "spread",
    "total_missing",
    "spread_missing",
    "n_books",
]
M3_FEATURES = [
    "p_book_over",
    "line",
    "stat_code",
    "tier",
    "game_total",
    "spread",
    "total_missing",
    "spread_missing",
    "n_books",
]
FORECAST_INPUTS = ("p_grid_over", "z")  # what the placebo permutes and the control excludes
PERM_SUFFIX = "_perm"
ARMS = ("M0", "M1", "M2", "M3", "M4")

UNSTATED_DETAILS = (
    "pairs = DK t60 main-line pairs from ODDS_HISTORY's pairs_for_season on the stored "
    "model_rows/market_rows parquets (pushes dropped, status 0); consensus p is the median "
    "multiplicative de-vig at the modal main line, which can differ from DK's line (p_cons_over is "
    "NaN when no consensus row exists; LightGBM handles NaN)",
    "game_total = median over books of each book's main total (most balanced prices, ties lower "
    "point); spread = median home spread over books, signed to the player's team (home: as is, "
    "away: negated); both restricted to book rows with snapshot_ts <= real tip - 60 min; the "
    "h2h consensus P(home) is measured in the gate but is not a feature (the draft lists none)",
    "missing game_total -> median of totals of games on EARLIER dates (as-of; null if none), "
    "missing spread -> 0.0 (the median of a team-signed spread); both with a *_missing flag",
    "tier = prior-10 points per game over the player's last 10 played games (minutes > 0, strictly "
    "before the game, min 1 game): 0 <10, 1 10-17, 2 17-24, 3 24+, -1 no prior game",
    "tag = latest usable T-60 official-report status (production real-tip gate): 0 unlisted, "
    "1 available, 2 probable, 3 questionable, 4 doubtful, 5 out; null when the game has no "
    "usable snapshot (never 'nobody listed')",
    "is_b2b = production team_game_context b2b (previous team game yesterday); n_out_rot and "
    "starter10 are the stored production feature-frame values on the model rows",
    "stat enters M2/M3 as a numeric code (pts 0, reb 1, ast 2, fg3m 3); one pooled model over the "
    "four stats per block",
    "month-block walk-forward: train = every pair row with game_date before the block; odds exist "
    "from 2023 only, so the first blocks with fewer than 5,000 training rows are unscored and "
    "reported (all arms share the scored rows)",
    "M1 uses the stored ODDS_HISTORY weights w (fit on 2023 DK t60, results_2024.json h2); on 2023 "
    "that is in-sample by construction and is reported as such",
    "M4 permutes p_grid_over and z JOINTLY (one permutation per (stat, season)) so their relation "
    "to each other survives and only their link to the row's side is broken (seed 0)",
    "pass rule read as: 2023 is the select season (conditions 1-4 on the point estimates and the "
    "CI, no Holm), 2024 confirms (all conditions incl. Holm over 4 stats); both must hold",
    "gate check 4 gap = max - min null rate across tiers within a stat and across stats within a "
    "tier (cells under 300 rows listed, not judged); also reported pooled",
    "side accuracy: prediction > 0.5 means over; acc_book is the book's own p > 0.5 (same rows)",
)

LGB_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "n_estimators": 200,
    "learning_rate": 0.04,
    "num_leaves": 15,
    "min_child_samples": 100,
    "colsample_bytree": 0.8,
    "subsample": 0.8,
    "subsample_freq": 1,
    "random_state": 0,
    "n_jobs": 4,
    "verbose": -1,
    "deterministic": True,
    "force_row_wise": True,
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze


def configure(doc: Path) -> None:
    global PREREG
    PREREG = doc


def require_frozen(expected: str | None, allow_draft: bool) -> str:
    sha = frozen_sha(PREREG)
    if allow_draft:
        return sha
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SystemExit("STOP: --sha must be the full 64-hex freeze hash")
    if sha != expected:
        raise SystemExit(f"STOP: prereg sha256 {sha} != frozen {expected}")
    return sha


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def assert_seasons(df: pl.DataFrame, col: str = "season") -> None:
    if col in df.columns and df.height and int(df[col].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")


# --------------------------------------------------------------------------- game lines


def _main_by_balance(t: pl.DataFrame, key: list[str], pt: str) -> pl.DataFrame:
    """Per book's main line: most balanced prices, ties -> lower point."""
    return t.sort([*key, "bal", pt]).unique(subset=key, keep="first", maintain_order=True)


def two_sided_lines(q: pl.DataFrame, market: str) -> pl.DataFrame:
    """(game_id, book, point, p_over|p_home, snapshot_ts) two-sided, de-vigged (multiplicative).

    totals: over/under at the same point. spreads: home at ``point`` and away at ``-point``; the
    point is the HOME spread."""
    d = q.filter(pl.col("market") == market)
    if d.is_empty():
        return pl.DataFrame()
    a, b = ("over", "under") if market == "totals" else ("home", "away")
    if market == "spreads":
        d = d.with_columns(
            pl.when(pl.col("side") == "home")
            .then(pl.col("point"))
            .otherwise(-pl.col("point"))
            .alias("point")
        )
    g = d.group_by(["game_id", "book", "point"]).agg(
        (pl.col("side") == a).sum().alias("na"),
        (pl.col("side") == b).sum().alias("nb"),
        pl.col("price_american").filter(pl.col("side") == a).first().alias("pa"),
        pl.col("price_american").filter(pl.col("side") == b).first().alias("pb"),
        pl.col("snapshot_ts").first().alias("snapshot_ts"),
    )
    g = g.filter((pl.col("na") == 1) & (pl.col("nb") == 1))
    ra, rb = oh.american_prob(g["pa"].to_numpy()), oh.american_prob(g["pb"].to_numpy())
    return g.with_columns(
        pl.Series("p_a", oh.devig_mult(ra, rb)),
        pl.Series("bal", np.round(np.abs(ra - rb), oh.TIE_DECIMALS)),
    )


def game_line_table(q: pl.DataFrame, tips: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """One row per game: consensus total / home spread / P(home) (median over books) at T-60.

    Rows stamped after real tip - 60 min are excluded BEFORE aggregation (the guard) and counted."""
    t = tips.select("game_id", "tipoff_utc").unique("game_id")
    qq = q.join(t, on="game_id", how="left")
    late = (pl.col("tipoff_utc").is_null()) | (
        pl.col("snapshot_ts") > pl.col("tipoff_utc") - pl.duration(minutes=LEAD_MIN)
    )
    guard = {
        "rows": int(qq.height),
        "late_or_no_tip_rows_excluded": int(qq.filter(late).height),
    }
    qq = qq.filter(~late).drop("tipoff_utc")
    outs: list[pl.DataFrame] = []
    for market, col, name in (
        ("totals", "point", "game_total"),
        ("spreads", "point", "spread_home"),
    ):
        tl = two_sided_lines(qq, market)
        if tl.is_empty():
            continue
        main = _main_by_balance(tl, ["game_id", "book"], col)
        outs.append(
            main.group_by("game_id").agg(
                pl.col(col).median().alias(name),
                pl.len().alias(f"n_books_{name}"),
                pl.col("snapshot_ts").max().alias(f"ts_{name}"),
            )
        )
    h = qq.filter(pl.col("market") == "h2h")
    if h.height:
        hh = oh.h2h_table(h.filter(pl.col("side").is_in(["home", "away"]))).filter(
            pl.col("source") == "consensus"
        )
        outs.append(hh.select("game_id", pl.col("p_home_mult").alias("p_home_h2h")))
    gl = tips.select("game_id").unique("game_id")
    for o in outs:
        gl = gl.join(o, on="game_id", how="left")
    return gl, guard


def load_game_quotes(season: int, odds_db: Path = oh.ODDS_DB) -> pl.DataFrame:
    assert season in SEASONS, season
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        return con.execute(
            "SELECT game_id, snapshot_kind, snapshot_ts, book, market, side, point, price_american "
            f"FROM odds_history WHERE season = {int(season)} AND snapshot_kind = 't60' "
            "AND market IN ('totals','spreads','h2h') AND side IN ('over','under','home','away')"
        ).pl()
    finally:
        con.close()


def fill_total_asof(df: pl.DataFrame) -> pl.DataFrame:
    """Missing ``game_total`` -> median of totals on EARLIER game dates (as-of; null if none)."""
    known = (
        df.filter(pl.col("game_total").is_not_null())
        .unique("game_id")
        .select("game_date", "game_total")
        .sort("game_date")
    )
    kd = known["game_date"].to_numpy().astype("datetime64[D]")
    vals = known["game_total"].to_numpy()
    need = df.filter(pl.col("game_total").is_null())["game_date"].unique().to_list()
    rows = []
    for d in need:
        k = int(np.searchsorted(kd, np.datetime64(d)))
        rows.append((d, float(np.median(vals[:k])) if k > 0 else None))
    fill = pl.DataFrame(rows, schema={"game_date": pl.Date, "_fill": pl.Float64}, orient="row")
    return (
        df.join(fill, on="game_date", how="left")
        .with_columns(
            pl.col("game_total").is_null().cast(pl.Float64).alias("total_missing"),
            pl.col("game_total").fill_null(pl.col("_fill")),
        )
        .drop("_fill")
    )


# --------------------------------------------------------------------------- features


def team_b2b(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    """Production ``team_game_context`` b2b per (game_id, team_id) (Elo not needed -> stub)."""
    from nba.props.context_residual import team_game_context

    elo = pl.DataFrame(
        {"game_id": [], "exp_margin_home": []},
        schema={"game_id": pl.Utf8, "exp_margin_home": pl.Float64},
    )
    ctx = team_game_context(games, pgs, elo)
    return ctx.select("game_id", pl.col("team_id").cast(pl.Int64), "b2b")


def prior10_tier(pgs: pl.DataFrame, games: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """(game_id, player_id, p10, tier) for ``keys``: the mean points of the player's last 10 played
    games (minutes > 0) dated STRICTLY before the key's game date (as-of join, so neither the
    game's own box score nor whether the player played it enters the row)."""
    gd = games.select("game_id", pl.col("game_date").cast(pl.Date))
    p = (
        pgs.select(
            pl.col("game_id").cast(pl.Utf8),
            pl.col("player_id").cast(pl.Int64),
            pl.col("minutes").cast(pl.Float64),
            pl.col("pts").cast(pl.Float64),
        )
        .filter(pl.col("minutes") > 0)
        .join(gd, on="game_id", how="inner")
        .sort(["player_id", "game_date", "game_id"])
    )
    p = (
        p.with_columns(pl.col("pts").rolling_mean(10, min_samples=1).over("player_id").alias("p10"))
        .unique(["player_id", "game_date"], keep="last", maintain_order=True)
        .select("player_id", "game_date", "p10")
        .sort("game_date")
    )
    tgt = (
        keys.select(pl.col("game_id").cast(pl.Utf8), pl.col("player_id").cast(pl.Int64))
        .unique()
        .join(gd, on="game_id", how="inner")
        .sort("game_date")
    )
    out = tgt.join_asof(
        p,
        on="game_date",
        by="player_id",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    e0, e1, e2 = TIER_EDGES
    tier = (
        pl.when(pl.col("p10").is_null())
        .then(-1.0)
        .when(pl.col("p10") < e0)
        .then(0.0)
        .when(pl.col("p10") < e1)
        .then(1.0)
        .when(pl.col("p10") < e2)
        .then(2.0)
        .otherwise(3.0)
    )
    return out.select("game_id", "player_id", "p10", tier.alias("tier"))


def tag_view(snaps: pl.DataFrame) -> pl.DataFrame:
    from research.eval.pplay_context_eval import snapshot_views

    return snapshot_views(snaps)["t60"]


def attach_tag(rows: pl.DataFrame, t60: pl.DataFrame) -> pl.DataFrame:
    from research.eval.pplay_context_eval import _attach_snapshot

    keys = rows.select("game_id", pl.col("player_id").cast(pl.Int64)).unique()
    tag = _attach_snapshot(keys, t60, "tag", "_q", "_d", "_has").select(
        "game_id", "player_id", "tag"
    )
    return rows.join(tag, on=["game_id", "player_id"], how="left")


def dk_pairs(
    model: pl.DataFrame, market: pl.DataFrame, played: pl.DataFrame, tips: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """DK t60 main-line pairs (ODDS_HISTORY's pairing) with team/date/season from the model rows
    and the consensus p as a feature. Returns (pairs, leak-guard result)."""
    m = market.filter(pl.col("snapshot_kind") == "t60")
    keep = m.filter(pl.col("book").is_in([BOOK, "consensus"]))
    pairs, _ = oh.pairs_for_season(model, keep, played, tips)
    guard = oh.leak_guard(pairs)
    pairs = pairs.filter(pl.col("book") == BOOK)
    cons = keep.filter(pl.col("book") == "consensus").select(
        "game_id",
        pl.col("player_id").cast(pl.Int64),
        "stat",
        pl.col("p_over_mult").alias("p_cons_over"),
        pl.col("line").alias("cons_line"),
        pl.col("n_books").cast(pl.Float64),
        pl.col("snapshot_ts").alias("cons_ts"),
    )
    pairs = pairs.join(cons, on=["game_id", "player_id", "stat"], how="left")
    meta = model.select(
        "game_id",
        pl.col("player_id").cast(pl.Int64),
        "stat",
        pl.col("team_id").cast(pl.Int64),
        pl.col("game_date").cast(pl.Date),
        pl.col("season").cast(pl.Int64),
    )
    pairs = pairs.join(meta, on=["game_id", "player_id", "stat"], how="left")
    return pairs, guard


def build_frame(
    pairs: pl.DataFrame,
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    t60: pl.DataFrame,
    glines: pl.DataFrame,
) -> pl.DataFrame:
    """The classifier frame (one row per DK pair); every feature is as-of tip - 60 min."""
    f = pairs.with_columns(pl.col("player_id").cast(pl.Int64))
    f = f.join(team_b2b(games, pgs), on=["game_id", "team_id"], how="left")
    f = f.join(
        prior10_tier(pgs, games, f).select("game_id", "player_id", "tier"),
        on=["game_id", "player_id"],
        how="left",
    )
    f = attach_tag(f, t60)
    for c in ("game_total", "spread_home", "p_home_h2h"):
        if c not in glines.columns:
            glines = glines.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))
    f = f.join(
        glines.select("game_id", "game_total", "spread_home", "p_home_h2h"),
        on="game_id",
        how="left",
    )
    f = f.with_columns(
        pl.when(pl.col("is_home_f") == 1)
        .then(pl.col("spread_home"))
        .otherwise(-pl.col("spread_home"))
        .alias("spread_raw"),
        pl.col("game_total").alias("total_raw"),
    )
    f = f.with_columns(
        pl.col("spread_raw").is_null().cast(pl.Float64).alias("spread_missing"),
        pl.col("spread_raw").fill_null(0.0).alias("spread"),
    )
    f = fill_total_asof(f)
    return f.select(
        "game_id",
        "player_id",
        "team_id",
        "game_date",
        "season",
        "stat",
        pl.col("stat").replace_strict(STAT_CODE, default=None).cast(pl.Float64).alias("stat_code"),
        "line",
        pl.col("over").alias("y"),
        pl.col("p_over_mult").alias("p_book_over"),
        "p_cons_over",
        "cons_line",
        pl.col("n_books").fill_null(0.0).alias("n_books"),
        pl.col("p_model").alias("p_grid_over"),
        (
            (pl.col("line") - pl.col("mean_int"))
            / pl.max_horizontal(pl.col("std_int"), pl.lit(1e-6))
        ).alias("z"),
        pl.col("tier").fill_null(-1.0),
        "starter10",
        pl.col("b2b").cast(pl.Float64).alias("is_b2b"),
        "n_out_rot",
        "tag",
        "game_total",
        "total_raw",
        "total_missing",
        "spread",
        "spread_raw",
        "spread_missing",
        "p_home_h2h",
        "price_over",
        "price_under",
        "snapshot_ts",
        "cons_ts",
        "tipoff_utc",
    ).sort(["game_date", "game_id", "player_id", "stat"])


def permute_forecast_inputs(frame: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """M4 placebo: ``p_grid_over`` and ``z`` permuted jointly within (stat, season)."""
    rng = np.random.default_rng(seed)
    stat = frame["stat"].to_numpy()
    season = frame["season"].to_numpy()
    idx = np.arange(frame.height)
    perm = idx.copy()
    for s in np.unique(stat):
        for y in np.unique(season):
            sel = np.where((stat == s) & (season == y))[0]
            perm[sel] = sel[rng.permutation(len(sel))]
    return frame.with_columns(
        *[pl.Series(c + PERM_SUFFIX, frame[c].to_numpy()[perm]) for c in FORECAST_INPUTS]
    )


def arm_features() -> dict[str, list[str]]:
    m4 = [c + PERM_SUFFIX if c in FORECAST_INPUTS else c for c in M2_FEATURES]
    return {"M2": list(M2_FEATURES), "M3": list(M3_FEATURES), "M4": m4}


# --------------------------------------------------------------------------- data (real)


def load_world(db_path: str = "nba.duckdb") -> dict[str, pl.DataFrame]:
    """games / player stats (season <= 2024) and the official-report snapshots with real tips."""
    from nba.features.game_tipoff import attach_real_tips

    con = duckdb.connect(db_path, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {MAX_SEASON}"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m "
            f"FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        snaps = con.execute(
            "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
            "CAST(a.as_of AS TIMESTAMP) AS as_of, g.game_date "
            "FROM player_availability a JOIN games g ON g.game_id = a.game_id "
            "WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL "
            f"AND g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    assert_seasons(games)
    snaps = snaps.with_columns(
        pl.col("as_of").cast(pl.Datetime("us")),
        pl.col("player_id").cast(pl.Int64),
        pl.col("game_date").cast(pl.Date),
    )
    return {
        "games": games,
        "pgs": pgs,
        "snaps": attach_real_tips(snaps, None, max_season=MAX_SEASON),
    }


def prepare(db_path: str = "nba.duckdb") -> dict[str, Any]:
    """Everything the gate and the fits need, from stored ODDS_HISTORY rows and read-only DBs."""
    world = load_world(db_path)
    t60 = tag_view(world["snaps"])
    guards: dict[str, Any] = {}
    pair_frames: list[pl.DataFrame] = []
    glines_all: list[pl.DataFrame] = []
    glines_guard: dict[str, Any] = {}
    for season in SEASONS:
        model = oh.load_model_rows(season, ODDS_OUT)
        market = pl.read_parquet(ODDS_OUT / f"market_rows_{season}.parquet")
        played = oh.load_played(season, db_path)
        tips = oh.load_tips(season, db_path)
        pairs, guard = dk_pairs(model, market, played, tips)
        guards[str(season)] = guard
        quotes = load_game_quotes(season)
        gl, gg = game_line_table(quotes, tips)
        glines_guard[str(season)] = gg
        glines_all.append(gl)
        pair_frames.append(pairs)
        _log(f"season {season}: {pairs.height} DK t60 pairs, {gl.height} games with lines")
    pairs_all = pl.concat(pair_frames, how="diagonal_relaxed")
    glines = pl.concat(glines_all, how="diagonal_relaxed")
    frame = build_frame(pairs_all, world["games"], world["pgs"], t60, glines)
    assert_seasons(frame)
    return {
        "frame": frame,
        "pairs": pairs_all,
        "games": world["games"],
        "pgs": world["pgs"],
        "snaps": world["snaps"],
        "t60": t60,
        "glines": glines,
        "guards": guards,
        "glines_guard": glines_guard,
    }


# --------------------------------------------------------------------------- walk-forward


def month_blocks(df: pl.DataFrame) -> list[tuple[dt.date, dt.date]]:
    months = sorted({(d.year, d.month) for d in df["game_date"].to_list()})
    return [
        (dt.date(y, m, 1), dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)) for y, m in months
    ]


def block_splits(
    frame: pl.DataFrame, min_train: int = MIN_TRAIN_ROWS
) -> list[tuple[dt.date, dt.date, np.ndarray, np.ndarray, bool]]:
    """(start, end, train_idx, test_idx, scored) per month block; train = strictly before start."""
    gd = frame["game_date"].to_numpy().astype("datetime64[D]")
    out = []
    for start, end in month_blocks(frame):
        s, e = np.datetime64(start), np.datetime64(end)
        tr = np.where(gd < s)[0]
        te = np.where((gd >= s) & (gd < e))[0]
        out.append((start, end, tr, te, bool(len(tr) >= min_train and len(te) > 0)))
    return out


def _mat(frame: pl.DataFrame, names: list[str]) -> np.ndarray:
    return frame.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()


def m1_weights(path: Path | None = None) -> dict[str, float]:
    """ODDS_HISTORY's frozen blend weights (DK, t60), fit on 2023: results_2024.json h2."""
    p = path or (ODDS_OUT / "results_2024.json")
    h2 = json.loads(p.read_text())["h2"]
    return {s: float(h2[f"t60/{s}/{BOOK}"]["w"]) for s in STATS}


def walk_forward(
    frame: pl.DataFrame,
    weights: dict[str, float],
    ckpt: Path | None = None,
    only_start: dt.date | None = None,
    n_estimators: int | None = None,
    min_train: int = MIN_TRAIN_ROWS,
) -> dict[str, np.ndarray]:
    """Per-row predictions of M0..M4 on the scored blocks; M2/M3/M4 are fit strictly before each
    block, all arms share the rows, a checkpoint per block, progress per block."""
    import lightgbm as lgb

    assert_seasons(frame)
    fr = permute_forecast_inputs(frame)
    feats = arm_features()
    splits = block_splits(fr, min_train)
    parts: list[dict[str, np.ndarray]] = []
    for bi, (start, _end, tr, te, scored) in enumerate(splits):
        if only_start is not None and start != only_start:
            continue
        if not scored:
            _log(f"block {bi + 1}/{len(splits)} {start} UNSCORED train={len(tr)} test={len(te)}")
            continue
        path = None if ckpt is None else ckpt / f"block_{start}.npz"
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=True)
            parts.append({k: z[k] for k in z.files})
            _log(f"block {bi + 1}/{len(splits)} {start} loaded from checkpoint")
            continue
        train, block = fr[tr], fr[te]
        ytr = train["y"].to_numpy().astype(int)
        pk = block["p_book_over"].to_numpy()
        pg = block["p_grid_over"].to_numpy()
        w = np.array([weights[s] for s in block["stat"].to_list()])
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy(),
            "pid": block["player_id"].to_numpy(),
            "stat": block["stat"].to_numpy(),
            "season": block["season"].to_numpy(),
            "date": block["game_date"].to_numpy().astype("datetime64[D]").astype(str),
            "y": block["y"].to_numpy(),
            "price_over": block["price_over"].to_numpy(),
            "price_under": block["price_under"].to_numpy(),
            "M0": pk,
            "M1": _blend_vec(pk, pg, w),
        }
        for arm, names in feats.items():
            params = dict(LGB_PARAMS)
            if n_estimators is not None:
                params["n_estimators"] = n_estimators
            clf = lgb.LGBMClassifier(**params).fit(_mat(train, names), ytr)
            part[arm] = np.asarray(clf.predict_proba(_mat(block, names)))[:, 1]
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, **part)  # type: ignore[arg-type]
        parts.append(part)
        _log(f"block {bi + 1}/{len(splits)} {start} n_test={len(te)} train={len(tr)}")
    if not parts:
        raise ValueError("no month block had enough training rows")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def _blend_vec(pk: np.ndarray, pm: np.ndarray, w: np.ndarray) -> np.ndarray:
    pmc = np.clip(pm, oh.MODEL_EPS, 1 - oh.MODEL_EPS)
    pkc = np.clip(pk, oh.MARKET_EPS, 1 - oh.MARKET_EPS)
    z = oh.logit(pkc) + w * (oh.logit(pmc) - oh.logit(pkc))
    return np.asarray(1.0 / (1.0 + np.exp(-z)))


# --------------------------------------------------------------------------- analysis


def _clip(p: np.ndarray) -> np.ndarray:
    return np.asarray(np.clip(p, 1e-6, 1 - 1e-6))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(tot)


def analyse(preds: dict[str, np.ndarray], n_boot: int = N_BOOT) -> dict[str, Any]:
    """Rows ``[stat, season, arm, n, logloss, brier, dlogloss, ci_lo, ci_hi, p, p_holm, ece, acc,
    acc_book]``, reliability bins, pairwise deltas and the pass rule per stat."""
    rows: list[dict[str, Any]] = []
    rel: list[dict[str, Any]] = []
    pair: dict[str, Any] = {}
    stat, season, y, gid = preds["stat"], preds["season"], preds["y"], preds["gid"]
    for s in STATS:
        for yr in SEASONS:
            m = (stat == s) & (season == yr)
            if not m.any():
                continue
            yy, gg = y[m], gid[m]
            p0 = _clip(preds["M0"][m])
            ll0 = oh.ll_vec(p0, yy, oh.MARKET_EPS)
            acc_book = float(((p0 > 0.5) == (yy > 0.5)).mean())
            for arm in ARMS:
                p = _clip(preds[arm][m])
                ll = oh.ll_vec(p, yy, oh.MARKET_EPS)
                ci = oh.boot_ci(ll - ll0, gg, n_boot, BOOT_SEED)
                rows.append(
                    {
                        "stat": s,
                        "season": int(yr),
                        "arm": arm,
                        "n": int(m.sum()),
                        "logloss": float(ll.mean()),
                        "brier": float(oh.brier_vec(p, yy).mean()),
                        "dlogloss": ci["point"],
                        "ci_lo": ci["lo"],
                        "ci_hi": ci["hi"],
                        "p": ci["p"],
                        "p_holm": None,
                        "ece": ece(p, yy),
                        "acc": float(((p > 0.5) == (yy > 0.5)).mean()),
                        "acc_book": acc_book,
                    }
                )
                if arm in ("M0", "M2"):
                    for b, (lo, hi) in enumerate(
                        zip(np.arange(10) / 10, np.arange(1, 11) / 10, strict=True)
                    ):
                        sel = (p >= lo) & ((p < hi) if b < 9 else (p <= hi))
                        if sel.any():
                            rel.append(
                                {
                                    "stat": s,
                                    "season": int(yr),
                                    "arm": arm,
                                    "bin": b,
                                    "n": int(sel.sum()),
                                    "mean_pred": float(p[sel].mean()),
                                    "mean_obs": float(yy[sel].mean()),
                                }
                            )
            p2 = _clip(preds["M2"][m])
            ll2 = oh.ll_vec(p2, yy, oh.MARKET_EPS)
            for other in ("M3", "M4"):
                llo = oh.ll_vec(_clip(preds[other][m]), yy, oh.MARKET_EPS)
                c = oh.boot_ci(ll2 - llo, gg, n_boot, BOOT_SEED)
                pair[f"{s}/{yr}/M2-{other}"] = {k: c[k] for k in ("point", "lo", "hi", "p", "n")}
    holm_family(rows)
    return {"rows": rows, "reliability": rel, "pairwise": pair, "pass": pass_rule(rows, pair)}


def holm_family(rows: list[dict[str, Any]]) -> None:
    """Holm over {4 stats x M2}, per season (the report season is the tested one)."""
    for yr in SEASONS:
        fam = [r for r in rows if r["arm"] == "M2" and r["season"] == yr]
        if not fam:
            continue
        adj = oh.holm_adjust([r["p"] for r in fam] + [1.0] * (len(STATS) - len(fam)))
        for r, a in zip(fam, adj[: len(fam)], strict=True):
            r["p_holm"] = float(a)


def pass_rule(rows: list[dict[str, Any]], pair: dict[str, Any]) -> dict[str, Any]:
    """Draft 'Pass rule' per stat: 2023 selects (no Holm), 2024 confirms (all conditions)."""
    out: dict[str, Any] = {}
    for s in STATS:
        res: dict[str, Any] = {}
        for yr in SEASONS:
            m2 = next(
                (r for r in rows if (r["stat"], r["season"], r["arm"]) == (s, yr, "M2")), None
            )
            if m2 is None:
                continue
            c1 = m2["dlogloss"] <= FLOOR and m2["ci_hi"] < 0
            if yr == REPORT_SEASON:
                c1 = c1 and m2["p_holm"] is not None and m2["p_holm"] < 0.05
            c2 = pair[f"{s}/{yr}/M2-M3"]["point"] <= FLOOR_M3 and pair[f"{s}/{yr}/M2-M3"]["hi"] < 0
            c3 = pair[f"{s}/{yr}/M2-M4"]["point"] <= FLOOR_M4
            m0 = next(r for r in rows if (r["stat"], r["season"], r["arm"]) == (s, yr, "M0"))
            c4 = m2["ece"] <= m0["ece"] + ECE_TOL and m2["acc"] >= m0["acc"]
            res[str(yr)] = {
                "c1": c1,
                "c2": c2,
                "c3": c3,
                "c4": c4,
                "ok": bool(c1 and c2 and c3 and c4),
            }
        res["pass"] = bool(all(res.get(str(y), {}).get("ok") for y in SEASONS))
        out[s] = res
    return out


def ev_descriptive(preds: dict[str, np.ndarray], stat: str, n_boot: int = N_BOOT) -> dict[str, Any]:
    """Realised return per $1 at DK's price for legs with classifier edge >= 0.03 (only for a
    stat that passed). Report season only."""
    m = (preds["stat"] == stat) & (preds["season"] == REPORT_SEASON)
    c = pl.DataFrame(
        {
            "game_id": preds["gid"][m],
            "price_over": preds["price_over"][m],
            "price_under": preds["price_under"][m],
            "p_over_mult": preds["M0"][m],
            "over": preds["y"][m],
        }
    )
    return oh.ev_cell(c, preds["M2"][m], EDGE_MIN, n_boot)


# --------------------------------------------------------------------------- gate


def check_pairs(frame: pl.DataFrame, min_train: int = MIN_TRAIN_ROWS) -> dict[str, Any]:
    """Check 1: pairs per stat per season, game total/spread coverage, scored rows after warm-up."""
    detail: dict[str, Any] = {}
    ok = True
    scored = np.zeros(frame.height, dtype=bool)
    for _s, _e, _tr, te, sc in block_splits(frame, min_train):
        scored[te] = sc
    fr = frame.with_columns(pl.Series("_scored", scored))
    for s in STATS:
        for yr in SEASONS:
            sub = fr.filter((pl.col("stat") == s) & (pl.col("season") == yr))
            n = sub.height
            cov_t = float(sub["total_raw"].is_not_null().mean()) if n else 0.0  # type: ignore[arg-type]
            cov_s = float(sub["spread_raw"].is_not_null().mean()) if n else 0.0  # type: ignore[arg-type]
            both = (
                float((sub["total_raw"].is_not_null() & sub["spread_raw"].is_not_null()).mean())  # type: ignore[arg-type]
                if n
                else 0.0
            )
            cell_ok = n >= MIN_PAIRS and min(cov_t, cov_s) >= COVERAGE_MIN
            ok &= cell_ok
            detail[f"{s}/{yr}"] = {
                "pairs": n,
                "scored_after_warmup": int(sub["_scored"].sum()),
                "games": int(sub["game_id"].n_unique()),
                "total_coverage": cov_t,
                "spread_coverage": cov_s,
                "both_coverage": both,
                "consensus_p_coverage": float(sub["p_cons_over"].is_not_null().mean())  # type: ignore[arg-type]
                if n
                else 0.0,
                "ok": bool(cell_ok),
            }
    unscored = [
        {"block": str(a), "train": len(tr), "test": len(te)}
        for a, _b, tr, te, sc in block_splits(frame, min_train)
        if not sc
    ]
    return {"ok": bool(ok), "detail": detail, "unscored_blocks": unscored}


def check_leak(prep: dict[str, Any]) -> dict[str, Any]:
    """Check 2: every feature source's snapshot_ts <= tip - 60 min (after the guard exclusions)."""
    frame = prep["frame"]
    gap = pl.duration(minutes=LEAD_MIN)
    asof = pl.col("tipoff_utc") - gap
    dk = frame.filter((pl.col("snapshot_ts") > asof) | pl.col("tipoff_utc").is_null())
    cons = frame.filter(pl.col("cons_ts").is_not_null() & (pl.col("cons_ts") > asof))
    tips = prep["snaps"].select("game_id", "tip_et").unique("game_id")
    used = prep["t60"].join(tips, on="game_id", how="left")
    real = used.filter(pl.col("tip_et").is_not_null())
    late_tag = real.filter(pl.col("snap_ts") > pl.col("tip_et") - gap)
    # game-line sources are the table's ts columns, compared with the real tip
    tps = frame.select("game_id", "tipoff_utc").unique("game_id")
    gl = prep["glines"].join(tps, on="game_id", how="inner")
    gl_late = 0
    for c in ("ts_game_total", "ts_spread_home"):
        if c in gl.columns:
            gl_late += int(
                gl.filter(pl.col(c).is_not_null() & (pl.col(c) > pl.col("tipoff_utc") - gap)).height
            )
    excl = {k: v["late_or_no_tip_rows_excluded"] for k, v in prep["glines_guard"].items()}
    detail = {
        "dk_pairs_after_asof": int(dk.height),
        "dk_leak_guard_t60_pairs": prep["guards"],
        "consensus_after_asof": int(cons.height),
        "tag_snapshot_rows": int(used.height),
        "tag_snapshots_after_asof": int(late_tag.height),
        "tag_games_on_proxy_tip": int(
            used.filter(pl.col("tip_et").is_null())["game_id"].n_unique()
        ),
        "game_line_features_after_asof": gl_late,
        "game_line_quote_rows_excluded_before_aggregation": excl,
        "b2b_tier_n_out_starter": "structural: strictly earlier games / stored production rows",
    }
    ok = (
        detail["dk_pairs_after_asof"] == 0
        and detail["consensus_after_asof"] == 0
        and detail["tag_snapshots_after_asof"] == 0
        and gl_late == 0
    )
    return {"ok": bool(ok), "detail": detail}


def check_m0(prep: dict[str, Any], ref_dir: Path = ODDS_OUT) -> dict[str, Any]:
    """Check 3: M0 (the book's de-vigged p, DK t60) reproduces ODDS_HISTORY's stored market log
    loss per stat/season to 1e-6 on the same pairs; plus the de-vig recomputed from raw prices."""
    frame = prep["frame"]
    detail: dict[str, Any] = {}
    ok = True
    for yr in SEASONS:
        ref = json.loads((ref_dir / f"results_{yr}.json").read_text())["h1"]
        for s in STATS:
            sub = frame.filter((pl.col("stat") == s) & (pl.col("season") == yr))
            ll = float(
                oh.ll_vec(sub["p_book_over"].to_numpy(), sub["y"].to_numpy(), oh.MARKET_EPS).mean()
            )
            r = ref[f"t60/{s}/{BOOK}"]
            dp = abs(ll - float(r["ll_market"]))
            same_n = sub.height == int(r["n"])
            ent = {
                "n_mine": sub.height,
                "n_stored": int(r["n"]),
                "ll_mine": ll,
                "ll_stored": float(r["ll_market"]),
                "abs_diff": dp,
                "same_n": same_n,
                "ok": bool(dp <= TOL_M0 and same_n),
            }
            ok &= ent["ok"]
            detail[f"{s}/{yr}"] = ent
    raw = frame.filter(pl.col("price_over").is_not_null() & pl.col("price_under").is_not_null())
    ro = oh.american_prob(raw["price_over"].to_numpy())
    ru = oh.american_prob(raw["price_under"].to_numpy())
    pd = oh.devig_mult(ro, ru)
    devig = float(np.abs(pd - raw["p_book_over"].to_numpy()).max()) if raw.height else float("nan")
    return {"ok": bool(ok), "detail": detail, "devig_recompute_max_abs_diff": devig}


def missingness(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """Null rate of total_raw / spread_raw / tag by stat and tier (2023 and 2024 pairs)."""
    out: list[dict[str, Any]] = []
    cols = {"game_total": "total_raw", "spread": "spread_raw", "tag": "tag"}
    for s in (*STATS, "ALL"):
        for t in (-1.0, 0.0, 1.0, 2.0, 3.0, None):
            sub = frame if s == "ALL" else frame.filter(pl.col("stat") == s)
            if t is not None:
                sub = sub.filter(pl.col("tier") == t)
            for lab, col in cols.items():
                out.append(
                    {
                        "col": lab,
                        "stat": s,
                        "tier": "ALL" if t is None else int(t),
                        "n": sub.height,
                        "null_rate": float(sub[col].is_null().mean())  # type: ignore[arg-type]
                        if sub.height
                        else float("nan"),
                    }
                )
    return out


def max_gap_pp(audit: list[dict[str, Any]]) -> dict[str, Any]:
    """Per column: max-min null rate (pp) across tiers within each stat, and across stats within
    each tier; cells under MIN_CELL rows are ignored. Pooled gap reported alongside."""
    res: dict[str, Any] = {}
    for col in sorted({r["col"] for r in audit}):
        a = [r for r in audit if r["col"] == col and r["n"] >= MIN_CELL]

        def spread(vals: list[float]) -> float:
            return 100.0 * (max(vals) - min(vals)) if len(vals) > 1 else 0.0

        by_tier = {
            s: spread([r["null_rate"] for r in a if r["stat"] == s and r["tier"] != "ALL"])
            for s in (*STATS, "ALL")
        }
        by_stat = {
            str(t): spread([r["null_rate"] for r in a if r["tier"] == t and r["stat"] != "ALL"])
            for t in (-1, 0, 1, 2, 3, "ALL")
        }
        res[col] = {
            "across_tiers_within_stat_pp": by_tier,
            "across_stats_within_tier_pp": by_stat,
            "max_pp": max([*by_tier.values(), *by_stat.values()]),
            "pooled_across_tiers_pp": by_tier["ALL"],
            "pooled_across_stats_pp": by_stat["ALL"],
            "cells_below_min_n": sum(1 for r in audit if r["col"] == col and r["n"] < MIN_CELL),
        }
    return res


def _rebuild(prep: dict[str, Any], **over: pl.DataFrame) -> pl.DataFrame:
    return build_frame(
        over.get("pairs", prep["pairs"]),
        prep["games"],
        over.get("pgs", prep["pgs"]),
        over.get("t60", prep["t60"]),
        over.get("glines", prep["glines"]),
    )


FEATURE_COLS = [
    "tier",
    "is_b2b",
    "tag",
    "game_total",
    "spread",
    "total_missing",
    "spread_missing",
    "p_cons_over",
    "z",
    "p_grid_over",
    "p_book_over",
    "starter10",
    "n_out_rot",
    "n_books",
    "line",
]


def _same(a: pl.DataFrame, b: pl.DataFrame, rows: pl.Expr | None = None) -> bool:
    if rows is not None:
        a, b = a.filter(rows), b.filter(rows)
    key = ["game_id", "player_id", "stat"]
    return bool(
        a.select([*key, *FEATURE_COLS]).sort(key).equals(b.select([*key, *FEATURE_COLS]).sort(key))
    )


def planted_tests(prep: dict[str, Any], seed: int = 7) -> dict[str, bool]:
    """Same-game, future, post-tip and label plants leave the as-of features byte-identical and
    three positive controls change them; then the walk-forward split plants."""
    rng = np.random.default_rng(seed)
    base = prep["frame"]
    pgs, glines, pairs = prep["pgs"], prep["glines"], prep["pairs"]
    dates = base["game_date"].sort()
    cut = dates[len(dates) // 2]
    gdate = prep["games"].select("game_id", pl.col("game_date").cast(pl.Date))
    pg_d = pgs.join(gdate, on="game_id", how="left")

    def box_edit(mask: pl.Series) -> pl.DataFrame:
        n = pg_d.height
        mins = np.where(rng.random(n) < 0.3, 0.0, rng.uniform(1, 48, n))
        pts = rng.integers(0, 60, n).astype(float)
        edited: pl.DataFrame = pg_d.with_columns(
            pl.when(mask)
            .then(pl.Series(mins))
            .otherwise(pl.col("minutes").cast(pl.Float64))
            .alias("minutes"),
            pl.when(mask)
            .then(pl.Series(pts))
            .otherwise(pl.col("pts").cast(pl.Float64))
            .alias("pts"),
        ).drop("game_date")
        return edited

    out: dict[str, bool] = {}
    early = pl.col("game_date") < cut
    out["future_box_scores_and_game_lines_and_reports"] = _same(
        base,
        _rebuild(
            prep,
            pgs=box_edit(pg_d["game_date"] >= cut),
            glines=glines.join(gdate, on="game_id", how="left")
            .with_columns(
                pl.when(pl.col("game_date") >= cut)
                .then(pl.col("game_total") + 17.0)
                .otherwise(pl.col("game_total"))
                .alias("game_total"),
                pl.when(pl.col("game_date") >= cut)
                .then(pl.col("spread_home") + 9.0)
                .otherwise(pl.col("spread_home"))
                .alias("spread_home"),
            )
            .drop("game_date"),
            t60=prep["t60"].with_columns(
                pl.when(pl.col("game_id").is_in(base.filter(~early)["game_id"].unique().to_list()))
                .then(pl.lit("questionable"))
                .otherwise(pl.col("status"))
                .alias("status")
            ),
        ),
        early,
    )
    picks = rng.choice(base["game_id"].unique().sort().to_numpy(), size=4, replace=False)
    ok_same = True
    for g in picks:
        ok_same &= _same(
            base, _rebuild(prep, pgs=box_edit(pg_d["game_id"] == g)), pl.col("game_id") == g
        )
    out["same_game_box_score"] = bool(ok_same)
    # outcome labels carry no feature: flip every label, features unchanged
    flipped = pairs.with_columns((1.0 - pl.col("over")).alias("over"))
    out["labels_not_in_features"] = _same(base, _rebuild(prep, pairs=flipped))
    # post-tip tag rows (after tip - 60) never reach the T-60 view
    tips = prep["snaps"].select("game_id", "tip_et").unique("game_id")
    sample = base["game_id"].unique().sort().to_numpy()
    sample = rng.choice(sample, size=min(40, len(sample)), replace=False)
    plant = (
        prep["snaps"]
        .filter(pl.col("game_id").is_in(list(sample)))
        .unique(["game_id", "player_id"])
        .join(tips.rename({"tip_et": "tip_x"}), on="game_id", how="left")
        .filter(pl.col("tip_x").is_not_null())
        .with_columns(
            pl.lit("out").alias("status"),
            (pl.col("tip_x") - pl.duration(minutes=20)).alias("as_of"),
        )
        .drop("tip_x")
    )
    late = pl.concat([prep["snaps"], plant.select(prep["snaps"].columns)])
    out["post_tip_report_rows"] = _same(base, _rebuild(prep, t60=tag_view(late)))
    # positive controls
    past = box_edit(pg_d["game_date"] < cut)
    out["control_past_box_changes_later_tier"] = not _same(
        base, _rebuild(prep, pgs=past), pl.col("game_date") >= cut
    )
    out["control_report_edit_changes_tag"] = not _same(
        base,
        _rebuild(prep, t60=prep["t60"].with_columns(pl.lit("out").alias("status"))),
    )
    out["control_game_line_edit_changes_features"] = not _same(
        base,
        _rebuild(
            prep, glines=glines.with_columns((pl.col("game_total") + 5.0).alias("game_total"))
        ),
    )
    out.update(walk_forward_plants(base, seed=seed))
    return out


def walk_forward_plants(frame: pl.DataFrame, seed: int = 7) -> dict[str, bool]:
    """The walk-forward never trains on its own block or later: every block's train rows are dated
    before it; rewriting every row on/after a cut leaves earlier blocks' training sets identical;
    the control (a block after the cut) does change."""
    rng = np.random.default_rng(seed)
    splits = block_splits(frame)
    strict = all(
        frame["game_date"].to_numpy()[tr].astype("datetime64[D]").max() < np.datetime64(a)
        for a, _b, tr, _te, sc in splits
        if sc and len(tr)
    )
    dates = frame["game_date"].sort()
    cut = dates[len(dates) // 2]
    msk = frame["game_date"] >= cut
    n = frame.height
    edited = frame.with_columns(
        pl.when(msk)
        .then(pl.Series(rng.integers(0, 2, n).astype(float)))
        .otherwise(pl.col("y"))
        .alias("y"),
        pl.when(msk)
        .then(pl.Series(rng.random(n)))
        .otherwise(pl.col("p_book_over"))
        .alias("p_book_over"),
        pl.when(msk).then(pl.Series(rng.normal(size=n))).otherwise(pl.col("z")).alias("z"),
    )
    cols = [
        "y",
        "p_book_over",
        "z",
        "p_grid_over",
        "line",
        "tier",
        "is_b2b",
        "tag",
        "game_total",
        "spread",
    ]
    sp2 = block_splits(edited)
    before_same, after_diff = True, False
    for (a, _b, tr, _te, sc), (_a2, _b2, tr2, _te2, _sc2) in zip(splits, sp2, strict=True):
        if not sc:
            continue
        same = np.array_equal(tr, tr2) and frame[tr].select(cols).equals(edited[tr2].select(cols))
        if a <= cut:
            before_same &= bool(same)
        else:
            after_diff |= not same
    return {
        "walk_forward_train_strictly_before_block": bool(strict),
        "walk_forward_future_rewrite_leaves_earlier_blocks_identical": bool(before_same),
        "control_walk_forward_later_block_changes": bool(after_diff),
    }


def do_gate(prep: dict[str, Any]) -> dict[str, Any]:
    gate: dict[str, Any] = {}
    gate["1_pairs_and_coverage"] = check_pairs(prep["frame"])
    gate["2_leak_guard"] = check_leak(prep)
    gate["3_m0_reproduces"] = check_m0(prep)
    audit = missingness(prep["frame"])
    gaps = max_gap_pp(audit)
    gate["4a_missingness_gap"] = {
        "ok": bool(gaps and max(v["max_pp"] for v in gaps.values()) <= GAP_PP),
        "detail": gaps,
        "audit": audit,
    }
    plants = planted_tests(prep)
    gate["4b_planted"] = {"ok": bool(all(plants.values())), "detail": plants}
    fr = prep["frame"]
    gate["info_fills"] = {
        "total_filled_rows": int(fr["total_missing"].sum()),
        "spread_filled_rows": int(fr["spread_missing"].sum()),
        "total_still_null": int(fr["game_total"].is_null().sum()),
        "h2h_p_home_coverage": float(fr["p_home_h2h"].is_not_null().mean()),
        "tag_null_rate": float(fr["tag"].is_null().mean()),
        "tier_unknown_rate": float((fr["tier"] == -1.0).mean()),
        "cons_line_differs_from_dk": float(
            (
                fr.filter(pl.col("cons_line").is_not_null())["cons_line"]
                != fr.filter(pl.col("cons_line").is_not_null())["line"]
            ).mean()
        ),
    }
    gate["info_m1_weights"] = m1_weights()
    gate["all_ok"] = bool(
        all(v["ok"] for k, v in gate.items() if isinstance(v, dict) and "ok" in v)
    )
    return gate


def write_gate(gate: dict[str, Any], sha: str, allow_draft: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "gate.json"
    res = {
        "status": "GATE_PASSED" if gate["all_ok"] else "STOPPED_AT_GATE",
        "draft": allow_draft,
        "sha256": sha,
        "git_sha": git_sha(),
        "seasons": list(SEASONS),
        "unstated_details": list(UNSTATED_DETAILS),
        "tests": [
            {"test": k, "ok": v["ok"]} for k, v in gate.items() if isinstance(v, dict) and "ok" in v
        ],
        "gate": gate,
    }
    path.write_text(json.dumps(res, indent=1, default=float))
    return path


# --------------------------------------------------------------------------- report


def _conds(v: dict[str, Any]) -> dict[str, Any]:
    return {k: x for k, x in v.items() if k != "pass"}


def render_report(res: dict[str, Any], sha: str, gate_ok: bool) -> str:
    lines = [
        "# MARKET_LINE_CLASSIFIER results",
        "",
        f"sha256 `{sha}`; git `{git_sha()}`; gate passed: {gate_ok}; seeds: boot {BOOT_SEED}, "
        "lightgbm 0, placebo 0",
        "",
        "| stat | season | arm | n | logloss | brier | dlogloss | ci_lo | ci_hi | p | p_holm | ece "
        "| acc | acc_book |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res["rows"]:
        ph = "" if r["p_holm"] is None else f"{r['p_holm']:.3f}"
        lines.append(
            f"| {r['stat']} | {r['season']} | {r['arm']} | {r['n']} | {r['logloss']:.5f} | "
            f"{r['brier']:.5f} | {r['dlogloss']:+.5f} | {r['ci_lo']:+.5f} | {r['ci_hi']:+.5f} | "
            f"{r['p']:.3f} | {ph} | {r['ece']:.4f} | {r['acc']:.4f} | {r['acc_book']:.4f} |"
        )
    lines += ["", "## Pairwise (M2 minus control)", ""]
    for k, v in res["pairwise"].items():
        lines.append(f"- {k}: {v['point']:+.5f} [{v['lo']:+.5f}, {v['hi']:+.5f}] n={v['n']}")
    lines += ["", "## Pass rule", ""]
    for s, v in res["pass"].items():
        lines.append(f"- {s}: {'PASS' if v['pass'] else 'no pass'} {json.dumps(_conds(v))}")
    return "\n".join(lines) + "\n"


def do_report(preds: dict[str, np.ndarray], sha: str, gate_ok: bool) -> dict[str, Any]:
    res = analyse(preds)
    ev = {s: ev_descriptive(preds, s) for s, v in res["pass"].items() if v["pass"]}
    out = {
        "rows": res["rows"],
        "reliability": res["reliability"],
        "pairwise": res["pairwise"],
        "pass": res["pass"],
        "ev_descriptive": ev,
        "tests": [{"test": f"gate_{i}", "ok": gate_ok} for i in (1, 2, 3, 4)],
        "seeds": {"bootstrap": BOOT_SEED, "lightgbm": 0, "placebo": 0},
        "sha256": sha,
        "git_sha": git_sha(),
        "unstated_details": list(UNSTATED_DETAILS),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(out, indent=1, default=float))
    REPORT_PATH.write_text(render_report(res, sha, gate_ok))
    return out


# --------------------------------------------------------------------------- CLI


def _gate_or_stop(prep: dict[str, Any], sha: str) -> dict[str, Any]:
    gate = do_gate(prep)
    write_gate(gate, sha, False)
    if not gate["all_ok"]:
        raise SystemExit("STOPPED_AT_GATE: see reports/prereg_market_line/gate.json")
    return gate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "smoke", "run", "report"])
    ap.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--db", default="nba.duckdb")
    a = ap.parse_args(argv)
    configure(a.doc)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    sha = require_frozen(a.sha, a.allow_draft)
    if a.stage == "gate":
        _log("building frame (read-only; season <= 2024)")
        prep = prepare(a.db)
        gate = do_gate(prep)
        path = write_gate(gate, sha, a.allow_draft)
        _log(f"gate {'PASSED' if gate['all_ok'] else 'STOPPED'} -> {path}")
        return 0 if gate["all_ok"] else 1
    with HeavyLock():
        prep = prepare(a.db)
        _gate_or_stop(prep, sha)
        weights = m1_weights()
        if a.stage == "smoke":
            first = [s for s in block_splits(prep["frame"]) if s[4]][0][0]
            preds = walk_forward(prep["frame"], weights, only_start=first, n_estimators=50)
            _log(f"smoke ok: {len(preds['y'])} rows, block {first}")
            return 0
        if a.stage == "run":
            preds = walk_forward(prep["frame"], weights, ckpt=CACHE)
            np.savez_compressed(CACHE / "preds.npz", **preds)  # type: ignore[arg-type]
            _log(f"run done: {len(preds['y'])} rows")
            return 0
        z = np.load(CACHE / "preds.npz", allow_pickle=True)
        do_report({k: z[k] for k in z.files}, sha, True)
        _log(f"report written -> {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
