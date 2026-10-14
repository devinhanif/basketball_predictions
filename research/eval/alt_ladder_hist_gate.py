"""ALT_LADDER_HIST minimum-data gate (rule: docs/prereg/ALT_LADDER_HIST.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.alt_ladder_hist_gate \
        --data-root <checkout holding data/, reports/ and nba.duckdb>

Counts only. No outcome column (``y``) is read, no hit rate, return or EV is computed: the gate
sizes the bet sets and the intervals of the historical player ``*_alternate`` markets before the
rule is frozen (CLAUDE.md non-negotiable 8). It also measures the timing relation between the one
stored alternate capture (``t60``) and the model's T-60 cutoff, and the number of games in which an
official report stamp falls between them.

Inputs (read-only; season <= 2024 asserted before any read):
* ``data/odds/odds_history.duckdb``: table ``odds_history``, the four player ``*_alternate``
  markets, snapshot ``t60`` (the only alternate capture), over and under quotes; plus the main
  markets at ``t60`` and ``t5`` for the price-drift measurement;
* ``reports/odds_history/model_rows_{2023,2024}.parquet``: the stored production forecast
  (``p_ge``), no ``y``;
* ``data/ops/model_clv_pairs/pairs_{2023,2024}.parquet``: only ``game_id`` and ``tipoff_utc``;
* ``nba.duckdb`` table ``player_availability`` joined to ``games`` (seasons 2023 and 2024 only):
  only ``game_id`` and ``as_of`` (the report stamps the model gates on).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval.alt_line_70_gate import (
    BAND,
    CUTOFF_MIN,
    EPS,
    MARKET_STAT,
    MAX_SEASON,
    SEASONS,
    SECONDARY_BINS,
    STATS,
    THRESHOLD,
    cluster_halfwidth,
    decimal_odds,
    dedupe_dropping,
    p_over_at,
    pool_summaries,
    sha256_file,
    summarize_bets,
    survival_matrix,
)

ALT_MARKET_STAT = {
    "player_points_alternate": "pts",
    "player_rebounds_alternate": "reb",
    "player_assists_alternate": "ast",
    "player_threes_alternate": "fg3m",
}
SNAPSHOT_KIND = "t60"  # the only alternate capture the pull stored
#: sensitivity arm: only games whose snapshot is within this many minutes before the real tip
LEAD_CAP_MIN = 66.0
#: ladder integrity (review B1): a (book, game, player, stat) ladder is off when its rung nearest a
#: raw implied over probability of 0.5 (within OFF_NEAR of it) lies more than OFF_POINTS from the
#: same book's main-market over line; prices only, no outcome
OFF_POINTS = 3.0
OFF_NEAR = 0.15
#: contradiction test (review B1b): rungs at least CONTRA_POINTS from the main line whose raw
#: implied probability is on the wrong side of the main line's by more than CONTRA_PROB
CONTRA_POINTS = 1.0
CONTRA_PROB = 0.05
#: known vendor defect (DATA_CHANGELOG 2026-10-14): DraftKings player_points_alternate at the
#: t60 snapshots of these dates carries other stats' ladders; every such rung is excluded outright
VENDOR_DEFECT_BOOK = "draftkings"
VENDOR_DEFECT_STAT = "pts"
VENDOR_DEFECT_FIRST = dt.date(2023, 12, 7)
VENDOR_DEFECT_LAST = dt.date(2023, 12, 19)
ET = "America/New_York"
#: Part 1 gate thresholds (carried from ALT_LINE_70): cell games, cell rungs, conservative
#: hit-rate half-width
P1_MIN_GAMES, P1_MIN_ROWS, P1_MAX_HW = 300, 3_000, 0.03
#: Part 2 gate thresholds (carried): pooled rungs, games, conservative ROI half-width
P2_MIN_RUNGS, P2_MIN_GAMES, P2_MAX_HW = 1_500, 300, 0.05
#: descriptive deep-milestone tail bins (P below the secondary family); counts only
TAIL_BINS: tuple[tuple[str, float, float], ...] = (
    ("05", 0.00, 0.10),
    ("15", 0.10, 0.20),
    ("25", 0.20, 0.30),
    ("35", 0.30, 0.40),
    ("45", 0.40, 0.50),
)
INPUT_FILES = (
    "reports/odds_history/model_rows_2023.parquet",
    "reports/odds_history/model_rows_2024.parquet",
    "data/ops/model_clv_pairs/pairs_2023.parquet",
    "data/ops/model_clv_pairs/pairs_2024.parquet",
    "data/odds/odds_history.duckdb",
)


# ------------------------------------------------------------------------------------ loading


def load_alt_quotes(odds_db: Path, season: int) -> pl.DataFrame:
    """Over and under quotes of the four player ``*_alternate`` markets at ``t60``."""
    import duckdb

    assert season <= MAX_SEASON
    markets = ", ".join(f"'{m}'" for m in ALT_MARKET_STAT)
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        raw: pl.DataFrame = con.execute(
            "select game_id, player_id, market, book, snapshot_ts, last_update, side, point, "
            "price_american from odds_history where season = ? and snapshot_kind = ? "
            f"and market in ({markets}) and player_id is not null and side in ('over','under')",
            [season, SNAPSHOT_KIND],
        ).pl()
    finally:
        con.close()
    return raw.with_columns(pl.col("market").replace_strict(ALT_MARKET_STAT).alias("stat"))


def load_main_lines(odds_db: Path, season: int) -> pl.DataFrame:
    """Per (game, player, stat, book) the book's main-market over line at ``t60``: the over line
    whose raw implied probability is nearest 0.5 (ties to the lower line); a single line for
    DraftKings and FanDuel. Prices only."""
    import duckdb

    assert season <= MAX_SEASON
    markets = ", ".join(f"'{m}'" for m in MARKET_STAT)
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        raw: pl.DataFrame = con.execute(
            "select game_id, player_id, market, book, point, price_american from odds_history "
            "where season = ? and snapshot_kind = ? and side = 'over' and player_id is not null "
            f"and market in ({markets})",
            [season, SNAPSHOT_KIND],
        ).pl()
    finally:
        con.close()
    key = ["game_id", "player_id", "stat", "book"]
    imp = 1.0 / decimal_odds(raw["price_american"].to_numpy())
    raw = raw.with_columns(
        pl.col("market").replace_strict(MARKET_STAT).alias("stat"),
        pl.Series("imp", imp),
        pl.Series("d5", np.abs(imp - 0.5)),
    )
    return (
        raw.sort([*key, "d5", "point"])
        .unique(key, keep="first", maintain_order=True)
        .select(*key, pl.col("point").alias("main_point"), pl.col("imp").alias("main_imp"))
    )


def off_ladders(over: pl.DataFrame, main: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Ladder integrity (rule, review B1 and B1b). ``over``: over rungs with ``po``. A ladder is off
    when (test 1) its rung nearest a raw implied over probability of 0.5, within OFF_NEAR of it, is
    more than OFF_POINTS from the book's main line, OR (test 2) any rung at least CONTRA_POINTS
    below the main line has raw implied probability more than CONTRA_PROB below the main line's, or
    any rung at least CONTRA_POINTS above has one more than CONTRA_PROB above (the ladder
    contradicts the book's own main line; needs no rung near 0.5). Returns the off ladder keys and,
    per book: ladders, tested (test 1 applicable), off1, off2_only, off, no_main_line, untested."""
    key = ["game_id", "player_id", "stat", "book"]
    imp = 1.0 / decimal_odds(over["po"].to_numpy())
    r = over.with_columns(pl.Series("imp", imp), pl.Series("d5", np.abs(imp - 0.5)))
    near = (
        r.sort([*key, "d5", "point"])
        .unique(key, keep="first", maintain_order=True)
        .select(*key, pl.col("point").alias("alt50"), "d5")
    )
    t1 = (
        near.filter(pl.col("d5") < OFF_NEAR)
        .join(main, on=key, how="inner")
        .with_columns(((pl.col("alt50") - pl.col("main_point")).abs() > OFF_POINTS).alias("off1"))
    )
    rm = r.join(main, on=key, how="inner").with_columns(
        (
            (
                (pl.col("point") <= pl.col("main_point") - CONTRA_POINTS)
                & (pl.col("imp") < pl.col("main_imp") - CONTRA_PROB)
            )
            | (
                (pl.col("point") >= pl.col("main_point") + CONTRA_POINTS)
                & (pl.col("imp") > pl.col("main_imp") + CONTRA_PROB)
            )
        ).alias("contra")
    )
    t2 = rm.group_by(key).agg(pl.col("contra").any().alias("off2"))
    allv = (
        near.select(*key)
        .join(main.select(*key, pl.lit(True).alias("has_main")), on=key, how="left")
        .join(t1.select(*key, "off1"), on=key, how="left")
        .join(t2, on=key, how="left")
        .with_columns(
            pl.col("has_main").fill_null(False),
            pl.col("off1").fill_null(False),
            pl.col("off2").fill_null(False),
        )
        .with_columns(
            (pl.col("off1") | pl.col("off2")).alias("off"),
            (pl.col("off2") & ~pl.col("off1")).alias("off2_only"),
        )
    )
    summary = (
        allv.group_by("book")
        .agg(
            pl.len().alias("ladders"),
            pl.col("has_main").sum().alias("with_main_line"),
            pl.col("off1").sum().alias("off1"),
            pl.col("off2_only").sum().alias("off2_only"),
            pl.col("off").sum().alias("off"),
        )
        .with_columns((pl.col("ladders") - pl.col("with_main_line")).alias("no_main_line"))
        .sort("book")
    )
    return allv.filter(pl.col("off")).select(key), summary


def load_report_stamps(nba_db: Path, seasons: tuple[int, ...] = SEASONS) -> pl.DataFrame:
    """Distinct (game_id, as_of) of the official-report rows the model gates on. ``as_of`` is a
    naive Eastern clock time (11:00 and 17:00 in the stored seasons). Seasons 2023 and 2024 only."""
    import duckdb

    assert max(seasons) <= MAX_SEASON
    con = duckdb.connect(str(nba_db), read_only=True)
    try:
        ids = ", ".join(str(int(s)) for s in seasons)
        stamps: pl.DataFrame = con.execute(
            "select distinct a.game_id, a.as_of from player_availability a "
            f"join games g on g.game_id = a.game_id where g.season in ({ids}) "
            "and a.as_of is not null"
        ).pl()
    finally:
        con.close()
    return stamps


def window_games(stamps: pl.DataFrame, snap: pl.DataFrame) -> pl.DataFrame:
    """Games where a report stamp falls in ``(snapshot_ts, tipoff - 60 min]``: the model's
    information set includes it, the stored price (taken at ``snapshot_ts``) does not.
    ``snap``: one row per game with ``tipoff_utc`` and ``snapshot_ts`` (naive UTC); ``stamps``:
    naive Eastern. Returns the distinct ``game_id`` list."""

    def to_et(col: pl.Expr) -> pl.Expr:
        return col.dt.replace_time_zone("UTC").dt.convert_time_zone(ET).dt.replace_time_zone(None)

    g = snap.with_columns(
        to_et(pl.col("snapshot_ts")).alias("snap_et"),
        to_et(pl.col("tipoff_utc") - pl.duration(minutes=CUTOFF_MIN)).alias("cut_et"),
    )
    j = g.join(stamps, on="game_id", how="inner").filter(
        (pl.col("as_of") > pl.col("snap_et")) & (pl.col("as_of") <= pl.col("cut_et"))
    )
    return j.select("game_id").unique()


def alt_over_rungs(
    raw: pl.DataFrame,
    tips: pl.DataFrame,
    model: pl.DataFrame,
    main: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Over rungs (half-point) of every book joined to the stored model P(over) (played rows only),
    the under price at the same key when the book posted one, the lead of the snapshot before the
    real tip, and the age of the quote (``snapshot_ts - last_update``). A key quoted more than once
    on a side is DROPPED and counted (the store keeps the last-listed price per key, so a surviving
    key is already 'the last price before the snapshot'; the parser dropped repeats)."""
    key = ["game_id", "player_id", "stat", "book", "point"]
    over = raw.filter(pl.col("side") == "over").rename({"price_american": "po"})
    over = over.select(*key, "po", "snapshot_ts", "last_update")
    under = raw.filter(pl.col("side") == "under").select(*key, pl.col("price_american").alias("pu"))
    over, dup_over = dedupe_dropping(over, key)
    under, dup_under = dedupe_dropping(under, key)
    over = over.join(under, on=key, how="left")
    n_all = over.height
    half = ((pl.col("point") * 2).round(6) % 2) == 1
    over = over.filter(half)
    n_half = over.height
    off_keys = over.head(0).select("game_id", "player_id", "stat", "book")
    integrity: list[dict[str, Any]] = []
    if main is not None:
        off_keys, summary = off_ladders(over, main)
        integrity = summary.to_dicts()
    over = over.join(tips, on="game_id", how="left")
    parts: list[pl.DataFrame] = []
    for stat in STATS:
        d = model.filter(pl.col("stat") == stat)
        surv = survival_matrix(d["p_ge"].to_list())
        idx = d.select("game_id", "player_id", "game_date").with_row_index("mi")
        t = over.filter(pl.col("stat") == stat).join(idx, on=["game_id", "player_id"], how="inner")
        pm = p_over_at(surv, t["mi"].to_numpy(), t["point"].to_numpy())
        parts.append(t.with_columns(pl.Series("pm", pm)))
    t = pl.concat(parts)
    dec_o = decimal_odds(t["po"].to_numpy())
    pu = pl.col("pu").cast(pl.Float64)
    dec_u = pl.when(pu > 0).then(1.0 + pu / 100.0).otherwise(1.0 + 100.0 / pu.abs())
    t = t.with_columns(pl.Series("dec", dec_o)).with_columns(
        pl.when(pu.is_not_null())
        .then(1.0 / pl.col("dec") + 1.0 / dec_u - 1.0)
        .otherwise(None)
        .alias("ovr"),
        pl.col("game_date").dt.truncate("1w").alias("wk"),
        ((pl.col("tipoff_utc") - pl.col("snapshot_ts")).dt.total_seconds() / 60.0).alias(
            "lead_min"
        ),
        ((pl.col("snapshot_ts") - pl.col("last_update")).dt.total_seconds() / 60.0).alias(
            "quote_age_min"
        ),
    )
    lk = ["game_id", "player_id", "stat", "book"]
    t = t.join(
        off_keys.with_columns(pl.lit(True).alias("ladder_off_test")), on=lk, how="left"
    ).with_columns(pl.col("ladder_off_test").fill_null(False))
    if main is not None:
        t = t.join(main.select(*lk, pl.lit(True).alias("has_main")), on=lk, how="left")
    else:
        t = t.with_columns(pl.lit(False).alias("has_main"))
    t = t.with_columns(pl.col("has_main").fill_null(False))
    snap_date = pl.col("snapshot_ts").dt.date()
    t = t.with_columns(
        (
            (pl.col("book") == VENDOR_DEFECT_BOOK)
            & (pl.col("stat") == VENDOR_DEFECT_STAT)
            & (snap_date >= VENDOR_DEFECT_FIRST)
            & (snap_date <= VENDOR_DEFECT_LAST)
        ).alias("vendor_window")
    ).with_columns((pl.col("ladder_off_test") | pl.col("vendor_window")).alias("ladder_off"))
    lead = t["lead_min"].drop_nulls().to_numpy()
    meta: dict[str, Any] = {
        "ladder_integrity_by_book": integrity,
        "over_rungs_after_dedupe": n_all,
        "half_point_rungs": n_half,
        "joined_to_model": t.height,
        "void_or_unmatched": n_half - t.height,
        "duplicate_key_rows_dropped": {"over": dup_over, "under": dup_under},
        "lead_minutes_min_med_max": (
            [round(float(x), 2) for x in (lead.min(), np.median(lead), lead.max())]
            if lead.size
            else None
        ),
        "rows_with_lead_below_60": int((lead < CUTOFF_MIN).sum()),
        "rows_with_null_tip": int(t["tipoff_utc"].is_null().sum()),
    }
    return t, meta


# ------------------------------------------------------------------------------------ cells


def p1_cell(e: pl.DataFrame) -> dict[str, Any]:
    """Part 1 cell: rungs, player-games, games, mean model P, conservative hit-rate half-width."""
    if e.is_empty():
        return {"rungs": 0}
    per_game = e.group_by("game_id").len()["len"].to_numpy().astype(float)
    p = float(e["pm"].mean())  # type: ignore[arg-type]
    return {
        "rungs": e.height,
        "player_games": e.select("game_id", "player_id", "stat").unique().height,
        "games": int(per_game.size),
        "mean_p": round(p, 4),
        "halfwidth_cons": round(cluster_halfwidth(p, per_game), 4),
        "halfwidth_independent": round(float(1.96 * np.sqrt(p * (1 - p) / e.height)), 4),
    }


def pool_p1(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-season Part 1 cells: cluster sums add, ``S = (hw N / 1.96)^2``."""
    parts = [x for x in parts if x.get("rungs", 0) > 0]
    n = sum(int(x["rungs"]) for x in parts)
    if n == 0:
        return {"rungs": 0}
    s = sum((x["halfwidth_cons"] * x["rungs"] / 1.96) ** 2 for x in parts)
    si = sum((x["halfwidth_independent"] * x["rungs"] / 1.96) ** 2 for x in parts)
    return {
        "rungs": n,
        "games": sum(int(x["games"]) for x in parts),
        "halfwidth_cons": round(float(1.96 * np.sqrt(s) / n), 4),
        "halfwidth_independent": round(float(1.96 * np.sqrt(si) / n), 4),
    }


def summarize(e: pl.DataFrame) -> dict[str, Any]:
    """``summarize_bets`` for rungs that may be one-sided: the overround is reported only over the
    rungs whose under was posted at the same key (``ovr`` is null otherwise)."""
    if e.is_empty():
        return {"rungs": 0}
    out = summarize_bets(e.with_columns(pl.col("ovr").fill_null(0.0).alias("ovr")))
    two = e.filter(pl.col("ovr").is_not_null())
    out["two_sided_share"] = round(two.height / e.height, 3)
    out["mean_overround_two_sided"] = (
        round(float(two["ovr"].mean()), 4) if two.height else None  # type: ignore[arg-type]
    )
    out.pop("mean_overround", None)
    return out


def band_filter(e: pl.DataFrame, lo: float, hi: float) -> pl.DataFrame:
    return e.filter((pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi - EPS))


def book_arm(e: pl.DataFrame) -> dict[str, Any]:
    """Everything the gate needs for one (book, season, arm): the primary set P >= 0.70 (Part 2),
    its headline band, the four secondary bins (Part 1S and Part 2S) and the deep tail (counts)."""
    if e.is_empty():
        return {"rungs": 0}
    ge70 = e.filter(pl.col("pm") >= THRESHOLD - EPS)
    band = band_filter(e, BAND[0], BAND[1])
    out: dict[str, Any] = {
        "rungs": e.height,
        "games": int(e["game_id"].n_unique()),
        "ge70": summarize(ge70),
        "p1_band_70_90": {s: p1_cell(band.filter(pl.col("stat") == s)) for s in STATS},
        "p1_ge70_by_stat": {s: p1_cell(ge70.filter(pl.col("stat") == s)) for s in STATS},
        "bins": {},
        "p1_bins": {},
        "tail_rungs": {},
    }
    for label, lo, hi in SECONDARY_BINS:
        b = band_filter(e, lo, hi)
        out["bins"][label] = summarize(b)
        out["p1_bins"][label] = {s: p1_cell(b.filter(pl.col("stat") == s)) for s in STATS}
    for label, lo, hi in TAIL_BINS:
        out["tail_rungs"][label] = band_filter(e, lo, hi).height
    return out


def pool_arm(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool the per-season ``book_arm`` results (the gate decides on the pooled estimate with the
    per-season counts shown beside it)."""
    live = [p for p in parts if p.get("rungs", 0) > 0]
    out: dict[str, Any] = {
        "ge70": pool_summaries([p["ge70"] for p in live]) if live else {"rungs": 0},
        "p1_band_70_90": {s: pool_p1([p["p1_band_70_90"][s] for p in live]) for s in STATS},
        "bins": {},
        "p1_bins": {},
    }
    for label, _, _ in SECONDARY_BINS:
        out["bins"][label] = pool_summaries([p["bins"][label] for p in live]) if live else {}
        out["p1_bins"][label] = {s: pool_p1([p["p1_bins"][label][s] for p in live]) for s in STATS}
    return out


def gate_flags(arm: dict[str, Any], per_season: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass/fail by the carried thresholds. Part 2: pooled rungs, games and conservative ROI
    half-width, for the primary set and for each secondary bin (and per season for information).
    Part 1: the headline band and each bin, per stat, per season (every cell must pass)."""

    def p2(x: dict[str, Any]) -> bool:
        return bool(
            x.get("rungs", 0) >= P2_MIN_RUNGS
            and x.get("games", 0) >= P2_MIN_GAMES
            and x.get("roi_halfwidth_cons", 9.0) <= P2_MAX_HW
        )

    def p1(x: dict[str, Any]) -> bool:
        return bool(
            x.get("rungs", 0) >= P1_MIN_ROWS
            and x.get("games", 0) >= P1_MIN_GAMES
            and x.get("halfwidth_cons", 9.0) <= P1_MAX_HW
        )

    flags: dict[str, Any] = {
        "part2_ge70_pooled": p2(arm["ge70"]),
        "part2_bins_pooled": {k: p2(v) for k, v in arm["bins"].items()},
        "part2_ge70_by_season": [p2(p["ge70"]) if p.get("rungs", 0) else False for p in per_season],
        "part2_bins_by_season": [
            {k: p2(v) if p.get("rungs", 0) else False for k, v in p.get("bins", {}).items()}
            for p in per_season
        ],
        "part1_band_cells_by_season": [
            {s: p1(p["p1_band_70_90"][s]) if p.get("rungs", 0) else False for s in STATS}
            for p in per_season
        ],
        "part1_band_pooled": {s: p1(arm["p1_band_70_90"][s]) for s in STATS},
        "part1_bins_pooled": {k: {s: p1(v[s]) for s in STATS} for k, v in arm["p1_bins"].items()},
    }
    return flags


# ------------------------------------------------------------------------------------ drift


def price_drift(
    odds_db: Path, season: int, tips: pl.DataFrame, model: pl.DataFrame, books: list[str]
) -> dict[str, Any]:
    """Staleness of a ``t60`` price against the ``t5`` price of the SAME book, line and side on the
    MAIN markets (the alternates have no ``t5`` capture). Delta = implied(t5) - implied(t60) of the
    over (raw, vigged), so a positive delta means the over got shorter after t60. Reported over all
    shared rungs and over rungs with model P >= 0.70 (a model-selected over whose price moved up
    after t60 was cheaper at t60: that is the direction in which a stale price flatters the model).
    No outcome is read."""
    import duckdb

    assert season <= MAX_SEASON
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        raw: pl.DataFrame = con.execute(
            "select game_id, player_id, market, book, snapshot_kind, point, price_american "
            "from odds_history where season = ? and snapshot_kind in ('t60','t5') "
            "and market in ('player_points','player_rebounds','player_assists','player_threes') "
            "and player_id is not null and side = 'over'",
            [season],
        ).pl()
    finally:
        con.close()
    raw = raw.filter(pl.col("book").is_in(books)).with_columns(
        pl.col("market").replace_strict(MARKET_STAT).alias("stat")
    )
    key = ["game_id", "player_id", "stat", "book", "point"]
    a = raw.filter(pl.col("snapshot_kind") == "t60").select(
        *key, pl.col("price_american").alias("p60")
    )
    b = raw.filter(pl.col("snapshot_kind") == "t5").select(
        *key, pl.col("price_american").alias("p5")
    )
    a, _ = dedupe_dropping(a, key)
    b, _ = dedupe_dropping(b, key)
    j = a.join(b, on=key, how="inner")
    j = j.filter(((pl.col("point") * 2).round(6) % 2) == 1)
    parts: list[pl.DataFrame] = []
    for stat in STATS:
        d = model.filter(pl.col("stat") == stat)
        surv = survival_matrix(d["p_ge"].to_list())
        idx = d.select("game_id", "player_id").with_row_index("mi")
        t = j.filter(pl.col("stat") == stat).join(idx, on=["game_id", "player_id"], how="inner")
        pm = p_over_at(surv, t["mi"].to_numpy(), t["point"].to_numpy())
        parts.append(t.with_columns(pl.Series("pm", pm)))
    t = pl.concat(parts)
    delta = 1.0 / decimal_odds(t["p5"].to_numpy()) - 1.0 / decimal_odds(t["p60"].to_numpy())
    t = t.with_columns(pl.Series("delta", delta))
    out: dict[str, Any] = {}
    for book in books:
        tb = t.filter(pl.col("book") == book)
        sel = tb.filter(pl.col("pm") >= THRESHOLD - EPS)

        def stats(x: pl.DataFrame) -> dict[str, Any]:
            if x.is_empty():
                return {"n": 0}
            d = x["delta"].to_numpy()
            return {
                "n": int(d.size),
                "mean_delta": round(float(d.mean()), 4),
                "mean_abs_delta": round(float(np.abs(d).mean()), 4),
                "share_abs_ge_0.02": round(float((np.abs(d) >= 0.02).mean()), 3),
                "share_unchanged": round(float((d == 0).mean()), 3),
            }

        out[book] = {"all_shared": stats(tb), "model_p_ge_70": stats(sel)}
    return out


# ------------------------------------------------------------------------------------ entrypoint


def run_gate(
    data_root: Path, nba_db: Path | None = None, hash_inputs: bool = True
) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON  # before any read
    odds_db = data_root / "data/odds/odds_history.duckdb"
    nba_db = nba_db or (data_root / "nba.duckdb")
    result: dict[str, Any] = {
        "rule": "docs/prereg/ALT_LADDER_HIST.md (DRAFT)",
        "snapshot_kind": SNAPSHOT_KIND,
        "seasons": list(SEASONS),
        "thresholds": {
            "part1": {"games": P1_MIN_GAMES, "rungs": P1_MIN_ROWS, "halfwidth": P1_MAX_HW},
            "part2": {"rungs": P2_MIN_RUNGS, "games": P2_MIN_GAMES, "halfwidth": P2_MAX_HW},
        },
    }
    if hash_inputs:
        result["input_sha256"] = {f: sha256_file(data_root / f) for f in INPUT_FILES}
    tips = pl.concat(
        [
            pl.read_parquet(
                data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "tipoff_utc"],
            )
            for s in SEASONS
        ]
    ).unique("game_id")
    stamps = load_report_stamps(nba_db)
    per_season: dict[str, dict[str, Any]] = {}
    arms: dict[str, dict[str, list[dict[str, Any]]]] = {
        "all": {},
        "excl_window": {},
        "excl_window_lead66": {},
        "primary": {},
    }
    books_seen: set[str] = set()
    for season in SEASONS:
        m = pl.read_parquet(
            data_root / f"reports/odds_history/model_rows_{season}.parquet",
            columns=["game_id", "player_id", "stat", "game_date", "p_ge"],
        )
        assert "y" not in m.columns
        raw = load_alt_quotes(odds_db, season)
        t, meta = alt_over_rungs(raw, tips, m, load_main_lines(odds_db, season))
        snap = (
            raw.group_by("game_id")
            .agg(
                pl.col("snapshot_ts").max().alias("snapshot_ts"),
                pl.col("snapshot_ts").n_unique().alias("n_ts"),
            )
            .join(tips, on="game_id", how="inner")
        )
        wg = window_games(stamps, snap.select("game_id", "tipoff_utc", "snapshot_ts"))
        game_lead = (snap["tipoff_utc"] - snap["snapshot_ts"]).dt.total_seconds().to_numpy() / 60.0
        meta["games_with_alt_quotes"] = int(snap.height)
        meta["game_lead_min_p01_p10_p50_p90_p99"] = [
            round(float(x), 2) for x in np.quantile(game_lead, [0.01, 0.1, 0.5, 0.9, 0.99])
        ]
        meta["games_with_lead_gt_66"] = int((game_lead > 66.0).sum())
        meta["games_snapshot_ts_not_unique"] = int((snap["n_ts"] > 1).sum())
        meta["games_with_report_stamp_in_window"] = wg.height
        w_set = set(wg["game_id"].to_list())
        t = t.with_columns(pl.col("game_id").is_in(list(w_set)).alias("in_window"))
        books = sorted(t["book"].unique().to_list())
        books_seen.update(books)
        qa = t["quote_age_min"].drop_nulls().to_numpy()
        meta["quote_age_min_p10_med_p90"] = (
            [round(float(x), 1) for x in np.quantile(qa, [0.1, 0.5, 0.9])] if qa.size else None
        )
        meta["rungs_by_book"] = {b: int((t["book"] == b).sum()) for b in books}
        meta["rungs_in_window_games"] = int(t["in_window"].sum())
        cap = t.filter(~pl.col("in_window") & (pl.col("lead_min") <= LEAD_CAP_MIN))
        meta["off_ladder_rungs_by_book"] = {
            b: {
                "all_rungs": int(t.filter((pl.col("book") == b) & pl.col("ladder_off")).height),
                "by_vendor_window_all_rungs": int(
                    t.filter((pl.col("book") == b) & pl.col("vendor_window")).height
                ),
                "by_vendor_window_p_ge_70_window_arm": int(
                    cap.filter(
                        (pl.col("book") == b)
                        & pl.col("vendor_window")
                        & (pl.col("pm") >= THRESHOLD - EPS)
                    ).height
                ),
                "by_test_only_p_ge_70_window_arm": int(
                    cap.filter(
                        (pl.col("book") == b)
                        & pl.col("ladder_off_test")
                        & ~pl.col("vendor_window")
                        & (pl.col("pm") >= THRESHOLD - EPS)
                    ).height
                ),
                "no_main_line_p_ge_70_primary_arm": int(
                    cap.filter(
                        (pl.col("book") == b)
                        & ~pl.col("ladder_off")
                        & ~pl.col("has_main")
                        & (pl.col("pm") >= THRESHOLD - EPS)
                    ).height
                ),
                "residual_vendor_window_rungs_in_primary_arm": int(
                    cap.filter(
                        (pl.col("book") == b) & pl.col("vendor_window") & ~pl.col("ladder_off")
                    ).height
                ),
                "dec_ge_2_p_ge_70_december_primary_arm": int(
                    cap.filter(
                        (pl.col("book") == b)
                        & ~pl.col("ladder_off")
                        & (pl.col("pm") >= THRESHOLD - EPS)
                        & (pl.col("dec") >= 2.0)
                        & (pl.col("snapshot_ts").dt.month() == 12)
                    ).height
                ),
                "p_ge_70_rungs_window_arm": int(
                    cap.filter(
                        (pl.col("book") == b)
                        & pl.col("ladder_off")
                        & (pl.col("pm") >= THRESHOLD - EPS)
                    ).height
                ),
            }
            for b in books
        }
        res: dict[str, Any] = {"meta": meta, "books": {}}
        for book in books:
            tb = t.filter(pl.col("book") == book)
            fresh = tb.filter(~pl.col("in_window"))
            capped = fresh.filter(pl.col("lead_min") <= LEAD_CAP_MIN)
            for arm, frame in (
                ("all", tb),
                ("excl_window", fresh),
                ("excl_window_lead66", capped),  # S3: off ladders added back
                ("primary", capped.filter(~pl.col("ladder_off"))),
            ):
                r = book_arm(frame)
                res["books"].setdefault(book, {})[arm] = r
                arms[arm].setdefault(book, []).append(r)
        res["price_drift_main_markets"] = price_drift(odds_db, season, tips, m, books)
        per_season[str(season)] = res
    result["per_season"] = per_season
    pooled: dict[str, Any] = {}
    for arm in arms:
        pooled[arm] = {}
        for book in sorted(books_seen):
            parts = arms[arm].get(book, [])
            if len(parts) != len(SEASONS):
                parts = parts + [{"rungs": 0}] * (len(SEASONS) - len(parts))
            pa = pool_arm(parts)
            pa["gate"] = gate_flags(pa, parts)
            pooled[arm][book] = pa
    result["pooled"] = pooled
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--nba-db", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("reports/prereg_alt_ladder_hist/gate.json"))
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args(argv)
    res = run_gate(args.data_root, args.nba_db, hash_inputs=not args.no_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
