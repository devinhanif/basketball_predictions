# ruff: noqa: E501
"""GAME_MARKET_CONTEXT features (docs/prereg/GAME_MARKET_CONTEXT.md; DRAFT until frozen).

Game-level market context known at T-60, attached to every player row of the game:

* ``mkt_margin``      the market's expected margin for the player's team (home spread negated, signed
                      by ``is_home_f``);
* ``mkt_abs_margin``  ``|mkt_margin|``;
* ``mkt_total``       the game total;
* ``mkt_pace``        ``mkt_total`` minus the league's trailing mean of ACTUAL game totals over the
                      last ``PACE_WINDOW`` games strictly before the game's date (as-of; same-day
                      games excluded).

Source: ``data/odds/odds_history.duckdb`` table ``odds_history``, ``snapshot_kind = 't60'`` only,
``season <= 2024`` in the SQL. Book = Pinnacle where it has BOTH a spread and a total for the game;
otherwise the median over draftkings / fanduel / betmgm (at least ``MIN_CONSENSUS`` of them with both).
Known-at: a snapshot is usable only if ``snapshot_ts <= tipoff_utc - LEAD_MIN`` (real tip from
``nba.features.game_tipoff``); of several t60 snapshots of one (game, book) the latest usable one is
taken; a game with no usable snapshot has NULL market columns (never a proxy).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

MKT_COLS: tuple[str, ...] = ("mkt_margin", "mkt_abs_margin", "mkt_total", "mkt_pace")
ODDS_DB = Path("data/odds/odds_history.duckdb")
MAX_SEASON = 2024
LEAD_MIN = 55
PRIMARY_BOOK = "pinnacle"
CONSENSUS_BOOKS: tuple[str, ...] = ("draftkings", "fanduel", "betmgm")
MIN_CONSENSUS = 2
PACE_WINDOW = 300
PACE_MIN_GAMES = 50

ROW_SCHEMA: dict[str, Any] = {
    "game_id": pl.Utf8,
    "season": pl.Int64,
    "snapshot_ts": pl.Datetime("us"),
    "last_update": pl.Datetime("us"),
    "book": pl.Utf8,
    "market": pl.Utf8,
    "side": pl.Utf8,
    "point": pl.Float64,
    "implied_prob_raw": pl.Float64,
}


def load_market_rows(odds_db: str | Path = ODDS_DB, max_season: int = MAX_SEASON) -> pl.DataFrame:
    """T-60 h2h/spreads/totals rows of the four books, season <= 2024, read-only."""
    import duckdb

    if max_season > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    books = ",".join(f"'{b}'" for b in (PRIMARY_BOOK, *CONSENSUS_BOOKS))
    con = duckdb.connect(str(odds_db), read_only=True)
    try:
        df = con.execute(
            "SELECT game_id, season, snapshot_ts, last_update, book, market, side, point, implied_prob_raw "
            "FROM odds_history "
            f"WHERE season <= {int(max_season)} AND snapshot_kind = 't60' "
            "AND market IN ('h2h','spreads','totals') "
            f"AND book IN ({books})"
        ).pl()
    finally:
        con.close()
    assert df.is_empty() or int(df["season"].max()) <= MAX_SEASON, "season 2025 loaded"  # type: ignore[arg-type]
    return df.cast({k: v for k, v in ROW_SCHEMA.items() if k in df.columns}, strict=False)


def _main_line(rows: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, book, market, side): the quote closest to even money (the main line)."""
    return (
        rows.with_columns((pl.col("implied_prob_raw") - 0.5).abs().alias("_d"))
        .sort(["game_id", "book", "market", "side", "_d", "point"])
        .group_by(["game_id", "book", "market", "side"], maintain_order=True)
        .first()
        .drop("_d")
    )


def usable_snapshots(
    rows: pl.DataFrame, tips: pl.DataFrame, lead_min: int = LEAD_MIN
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Keep rows whose snapshot is at or before ``tipoff_utc - lead_min`` (real tip), then, per
    (game, book), only the latest such snapshot. Returns (rows, counts incl. refused rows)."""
    j = rows.join(tips.select(["game_id", "tipoff_utc"]), on="game_id", how="left")
    ok = pl.col("tipoff_utc").is_not_null() & (
        pl.col("snapshot_ts") <= pl.col("tipoff_utc") - pl.duration(minutes=lead_min)
    )
    n_all = j.height
    good = j.filter(ok)
    latest = good.group_by(["game_id", "book"]).agg(pl.col("snapshot_ts").max().alias("_latest"))
    good = good.join(latest, on=["game_id", "book"]).filter(
        pl.col("snapshot_ts") == pl.col("_latest")
    )
    counts = {
        "rows_in": n_all,
        "rows_no_real_tip": int(j["tipoff_utc"].is_null().sum()),
        "rows_refused_late": int(j.filter(~ok).height),
        "rows_used": good.height,
    }
    return good.drop("_latest"), counts


def book_lines(rows: pl.DataFrame) -> pl.DataFrame:
    """Per (game, book): home margin (= - home spread), total, de-vigged home win probability.
    A book whose two spread sides do not mirror (away != -home) gets NULL margin."""
    m = _main_line(rows)
    sp = m.filter(pl.col("market") == "spreads")
    home = sp.filter(pl.col("side") == "home").select(
        "game_id", "book", pl.col("point").alias("_hp")
    )
    away = sp.filter(pl.col("side") == "away").select(
        "game_id", "book", pl.col("point").alias("_ap")
    )
    spread = (
        home.join(away, on=["game_id", "book"], how="full", coalesce=True)
        .with_columns(
            pl.when(pl.col("_hp").is_not_null() & ((pl.col("_hp") + pl.col("_ap")).abs() < 1e-9))
            .then(-pl.col("_hp"))
            .otherwise(None)
            .alias("home_margin")
        )
        .select("game_id", "book", "home_margin")
    )
    to = m.filter(pl.col("market") == "totals")
    over = to.filter(pl.col("side") == "over").select(
        "game_id", "book", pl.col("point").alias("_op")
    )
    under = to.filter(pl.col("side") == "under").select(
        "game_id", "book", pl.col("point").alias("_up")
    )
    total = (
        over.join(under, on=["game_id", "book"], how="full", coalesce=True)
        .with_columns(
            pl.when(pl.col("_op").is_not_null() & ((pl.col("_op") - pl.col("_up")).abs() < 1e-9))
            .then(pl.col("_op"))
            .otherwise(None)
            .alias("total")
        )
        .select("game_id", "book", "total")
    )
    h = m.filter(pl.col("market") == "h2h")
    ph = h.filter(pl.col("side") == "home").select(
        "game_id", "book", pl.col("implied_prob_raw").alias("_ph")
    )
    pa = h.filter(pl.col("side") == "away").select(
        "game_id", "book", pl.col("implied_prob_raw").alias("_pa")
    )
    win = (
        ph.join(pa, on=["game_id", "book"], how="full", coalesce=True)
        .with_columns((pl.col("_ph") / (pl.col("_ph") + pl.col("_pa"))).alias("p_home"))
        .select("game_id", "book", "p_home")
    )
    snap = m.group_by(["game_id", "book"]).agg(pl.col("snapshot_ts").max())
    return (
        snap.join(spread, on=["game_id", "book"], how="left")
        .join(total, on=["game_id", "book"], how="left")
        .join(win, on=["game_id", "book"], how="left")
    )


def game_market(bl: pl.DataFrame) -> pl.DataFrame:
    """One row per game: Pinnacle where complete, else the median of >= MIN_CONSENSUS complete
    consensus books. ``mkt_src`` is 'pinnacle' or 'consensus'."""
    complete = bl.filter(pl.col("home_margin").is_not_null() & pl.col("total").is_not_null())
    pin = complete.filter(pl.col("book") == PRIMARY_BOOK).select(
        "game_id",
        pl.col("home_margin").alias("mkt_home_margin"),
        pl.col("total").alias("mkt_total"),
        pl.col("p_home").alias("mkt_p_home"),
        pl.col("snapshot_ts").alias("mkt_snapshot_ts"),
        pl.lit("pinnacle").alias("mkt_src"),
    )
    con = (
        complete.filter(pl.col("book").is_in(list(CONSENSUS_BOOKS)))
        .filter(~pl.col("game_id").is_in(pin["game_id"].to_list()))
        .group_by("game_id")
        .agg(
            pl.len().alias("_n"),
            pl.col("home_margin").median().alias("mkt_home_margin"),
            pl.col("total").median().alias("mkt_total"),
            pl.col("p_home").median().alias("mkt_p_home"),
            pl.col("snapshot_ts").max().alias("mkt_snapshot_ts"),
        )
        .filter(pl.col("_n") >= MIN_CONSENSUS)
        .drop("_n")
        .with_columns(pl.lit("consensus").alias("mkt_src"))
        .select(pin.columns)
    )
    return pl.concat([pin, con]).sort("game_id")


def build_game_market(
    rows: pl.DataFrame, tips: pl.DataFrame, lead_min: int = LEAD_MIN
) -> tuple[pl.DataFrame, dict[str, int]]:
    used, counts = usable_snapshots(rows, tips, lead_min)
    gm = game_market(book_lines(used)).join(
        tips.select(["game_id", "tipoff_utc"]), on="game_id", how="left"
    )
    gm = gm.with_columns(
        ((pl.col("tipoff_utc") - pl.col("mkt_snapshot_ts")).dt.total_seconds() / 60.0).alias(
            "mkt_min_before_tip"
        )
    )
    assert gm.is_empty() or float(gm["mkt_min_before_tip"].min()) >= lead_min - 1e-9, (  # type: ignore[arg-type]
        "a used snapshot is later than tip - 55 min"
    )
    return gm, counts


def league_total_mean(games: pl.DataFrame) -> pl.DataFrame:
    """(game_id, lg_total_mean): mean ACTUAL total of the last PACE_WINDOW games strictly before
    the game's date (same-day games excluded); NULL below PACE_MIN_GAMES prior games."""
    g = (
        games.filter(pl.col("home_pts").is_not_null() & pl.col("away_pts").is_not_null())
        .select("game_id", "game_date", (pl.col("home_pts") + pl.col("away_pts")).alias("_t"))
        .sort(["game_date", "game_id"])
    )
    dates = g["game_date"].to_list()
    tot = g["_t"].to_numpy().astype(float)
    allg = games.select("game_id", "game_date")
    idx_dates = np.array(dates, dtype="datetime64[D]")
    per_date: dict[dt.date, float | None] = {}
    for d in sorted(set(allg["game_date"].to_list())):
        k = int(
            np.searchsorted(idx_dates, np.datetime64(d), side="left")
        )  # completed games before d
        per_date[d] = (
            float(tot[max(0, k - PACE_WINDOW) : k].mean()) if k >= PACE_MIN_GAMES else None
        )
    return allg.with_columns(
        pl.col("game_date")
        .replace_strict(per_date, default=None, return_dtype=pl.Float64)
        .alias("lg_total_mean")
    ).select("game_id", "lg_total_mean")


def derive_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """Row-level market columns from game-level ``_hm`` (home margin), ``_tot`` and ``_lg``."""
    sign = pl.when(pl.col("is_home_f") == 1).then(1.0).otherwise(-1.0)
    return frame.with_columns(
        (sign * pl.col("_hm")).alias("mkt_margin"),
        pl.col("_hm").abs().alias("mkt_abs_margin"),
        pl.col("_tot").alias("mkt_total"),
        (pl.col("_tot") - pl.col("_lg")).alias("mkt_pace"),
    )


def attach_market(feats: pl.DataFrame, gm: pl.DataFrame, lg: pl.DataFrame) -> pl.DataFrame:
    """Left-join the game market onto player rows (row count and order unchanged). Rows of games
    without a usable market snapshot get NULL in all four columns. ``_hm``/``_tot``/``_lg`` are
    kept (placebo input) and ``mkt_src`` for audits."""
    out = (
        feats.join(
            gm.select(
                "game_id",
                pl.col("mkt_home_margin").alias("_hm"),
                pl.col("mkt_total").alias("_tot"),
                "mkt_src",
            ),
            on="game_id",
            how="left",
        )
        .join(lg, on="game_id", how="left")
        .rename({"lg_total_mean": "_lg"})
    )
    assert out.height == feats.height, "market join changed the row count"
    out = derive_columns(out)
    # a game with a market but no pace baseline (first games of 2022) has no mkt_pace; the market
    # exists only from 2023 so this is empty in practice and is counted by the gate
    return out


def placebo_market(frame: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """Permute the game-level (home margin, total) tuples across games WITHIN a date (a derangement:
    no game keeps its own numbers), then re-derive the four columns with each row's own side and
    pace baseline. Dates with a single market game are pooled by (season, month). Same marginal
    distribution, no game information. Games with a NULL market stay NULL; a pool of one game is
    left unchanged and counted by :func:`placebo_fixed_points`."""
    rng = np.random.default_rng(seed)
    g = (
        frame.filter(pl.col("_hm").is_not_null())
        .select("game_id", "game_date", "season", "_hm", "_tot")
        .unique("game_id")
        .sort(["game_date", "game_id"])
    )
    n_by_date = g.group_by("game_date").len().rename({"len": "_nd"})
    g = g.join(n_by_date, on="game_date").with_columns(
        pl.when(pl.col("_nd") >= 2)
        .then(pl.col("game_date").cast(pl.Utf8))
        .otherwise(
            pl.lit("pool-")
            + pl.col("season").cast(pl.Utf8)
            + pl.col("game_date").dt.strftime("-%m")
        )
        .alias("_grp")
    )
    gid = g["game_id"].to_list()
    hm = g["_hm"].to_numpy().copy()
    tot = g["_tot"].to_numpy().copy()
    new_hm = hm.copy()
    new_tot = tot.copy()
    grp = np.array(g["_grp"].to_list())
    for key in sorted(set(grp)):
        ix = np.flatnonzero(grp == key)
        if len(ix) < 2:
            continue
        order = ix[rng.permutation(len(ix))]
        new_hm[order] = hm[np.roll(order, 1)]
        new_tot[order] = tot[np.roll(order, 1)]
    swap = pl.DataFrame(
        {"game_id": gid, "_hm2": new_hm, "_tot2": new_tot},
        schema={"game_id": pl.Utf8, "_hm2": pl.Float64, "_tot2": pl.Float64},
    )
    out = (
        frame.join(swap, on="game_id", how="left")
        .with_columns(
            pl.coalesce(pl.col("_hm2"), pl.col("_hm")).alias("_hm"),
            pl.coalesce(pl.col("_tot2"), pl.col("_tot")).alias("_tot"),
        )
        .drop("_hm2", "_tot2")
    )
    out = derive_columns(out)
    assert out.height == frame.height
    return out


def placebo_fixed_points(real: pl.DataFrame, plc: pl.DataFrame) -> dict[str, float]:
    """Share of market-bearing rows whose placebo (home margin, total) equals their own game's."""
    m = real["_hm"].is_not_null()
    same = (real["_hm"] == plc["_hm"]) & (real["_tot"] == plc["_tot"])
    return {
        "rows": int(m.sum()),
        "unchanged_rows": int((same & m).sum()),
        "unchanged_share": float((same & m).sum() / max(int(m.sum()), 1)),
    }
