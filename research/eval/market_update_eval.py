# ruff: noqa: E501
"""MARKET_UPDATE gate (rule: docs/prereg/MARKET_UPDATE.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.market_update_eval gate

Gate stage ONLY: it counts what information exists after the T-60 price, per leg. It fits nothing and scores
nothing (no log loss, no Brier, no ROI). Inputs, all read-only: the two pairs parquet frames (seasons 2023 and
2024; season 2025 is never loaded), `nba.duckdb` (`player_availability`, opened ``read_only=True``), the lineup
collector's `lineups.duckdb`, `odds_history.duckdb` and the live `market_asof.duckdb` (all ``read_only=True``).
It never takes or removes `data/ops/heavy.lock`. No credential, no network.

Known-at windows (ET naive clocks, as `player_availability.as_of` is stored):
  prior time     = the T-60 price's own `snapshot_ts` (about tip - 64 min), per game
  decision time  = tip - 30 min
  a report row is "post-T-60 news" iff  prior time < as_of <= decision time.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

PAIRS_DIR = Path("data/ops/model_clv_pairs")
NBA_DB = Path("nba.duckdb")
LINEUPS_DB = Path("data/lineups/lineups.duckdb")
ODDS_DB = Path("data/odds/odds_history.duckdb")
LIVE_DB = Path("data/markets/market_asof.duckdb")
OUT_DIR = Path("reports/prereg_market_update")
SEASONS = (2023, 2024)
MAX_SEASON = 2024
BOOKS = ("draftkings", "pinnacle", "fanduel")
DECISION_LEAD_MIN = 30
TZ = "America/New_York"
OFF_STATES = ("out", "doubtful")
CLEARED_STATES = ("available", "probable")


def to_et_naive(col: str) -> pl.Expr:
    return (
        pl.col(col).dt.replace_time_zone("UTC").dt.convert_time_zone(TZ).dt.replace_time_zone(None)
    )


def load_legs(pairs_dir: Path = PAIRS_DIR) -> pl.DataFrame:
    """T-60 legs with a model row, an outcome and a de-vigged price (the legs every other rule scores)."""
    frames = []
    for s in SEASONS:
        if s > MAX_SEASON:
            raise ValueError("season 2025 is the frozen holdout")
        frames.append(pl.read_parquet(pairs_dir / f"pairs_{s}.parquet"))
    d = pl.concat(frames, how="diagonal_relaxed")
    assert int(d["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    return d.filter(
        (pl.col("snapshot_kind") == "t60")
        & pl.col("book").is_in(list(BOOKS))
        & pl.col("p_model").is_not_null()
        & pl.col("over").is_not_null()
        & pl.col("p_over_mult").is_not_null()
    ).with_columns(
        to_et_naive("snapshot_ts").alias("prior_et"),
        to_et_naive("tipoff_utc").alias("tip_et"),
    )


def window_changes(avail: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Per (game, player): the report status at the prior time (latest as_of <= prior) and the latest status in
    the news window (prior < as_of <= tip - 30). ``games``: game_id, prior_et, tip_et (naive ET).
    ``avail``: game_id, player_id, as_of, status. Returns rows with a window status only; ``s0`` is 'none'
    when the player was unlisted at the prior time. ``changed`` = s0 != s1."""
    a = avail.join(games, on="game_id")
    cut = pl.col("tip_et") - pl.duration(minutes=DECISION_LEAD_MIN)
    pre = (
        a.filter(pl.col("as_of") <= pl.col("prior_et"))
        .sort("as_of")
        .group_by(["game_id", "player_id"])
        .agg(pl.col("status").last().alias("s0"))
    )
    post = (
        a.filter((pl.col("as_of") > pl.col("prior_et")) & (pl.col("as_of") <= cut))
        .sort("as_of")
        .group_by(["game_id", "player_id"])
        .agg(pl.col("status").last().alias("s1"), pl.col("as_of").max().alias("news_as_of"))
    )
    j = post.join(pre, on=["game_id", "player_id"], how="left").with_columns(
        pl.col("s0").fill_null("none")
    )
    return j.with_columns((pl.col("s0") != pl.col("s1")).alias("changed"))


def coverage(legs: pl.DataFrame, ch: pl.DataFrame) -> dict[str, Any]:
    """Shares of legs with (a) any post-T-60 report row in their game, (a2) a status change in their game, (a3) a
    change for their own player. Per book and season. Pure counting."""
    g_any = set(ch["game_id"].unique().to_list())
    g_chg = set(ch.filter(pl.col("changed"))["game_id"].unique().to_list())
    own = (
        ch.filter(pl.col("changed"))
        .select("game_id", "player_id")
        .with_columns(pl.lit(True).alias("own"))
    )
    t = legs.join(own, on=["game_id", "player_id"], how="left").with_columns(
        pl.col("game_id").is_in(list(g_any)).alias("game_has_post_report"),
        pl.col("game_id").is_in(list(g_chg)).alias("game_has_change"),
        pl.col("own").fill_null(False).alias("own_changed"),
    )
    out: dict[str, Any] = {}
    for key, sub in [
        ("all", t),
        *[(f"book={b}", t.filter(pl.col("book") == b)) for b in BOOKS],
        *[(f"season={s}", t.filter(pl.col("season") == s)) for s in SEASONS],
        *[
            (
                f"draftkings,season={s}",
                t.filter((pl.col("book") == "draftkings") & (pl.col("season") == s)),
            )
            for s in SEASONS
        ],
    ]:
        n = sub.height
        out[key] = {
            "legs": n,
            "games": int(sub["game_id"].n_unique()),
            "legs_game_has_post_report": int(sub["game_has_post_report"].sum()),
            "legs_game_has_change": int(sub["game_has_change"].sum()),
            "legs_own_changed": int(sub["own_changed"].sum()),
            "share_game_has_post_report": int(sub["game_has_post_report"].sum()) / n if n else None,
            "share_game_has_change": int(sub["game_has_change"].sum()) / n if n else None,
            "share_own_changed": int(sub["own_changed"].sum()) / n if n else None,
        }
    return out


def weeks_to_n(
    n_min: int,
    news_per_game: float,
    games_per_week: float,
    capture: float,
    open_date: str = "2026-10-20",
) -> dict[str, Any]:
    """Season week (1 = the week of opening night) in which cumulative news legs reach ``n_min``.
    Pure arithmetic from stated capacity; nothing measured about outcomes."""
    per_week = news_per_game * games_per_week * capture
    wk = float(np.ceil(n_min / per_week)) if per_week > 0 else None
    end = None
    if wk is not None:
        from datetime import date

        y, m, d = (int(x) for x in open_date.split("-"))
        end = (date(y, m, d) + timedelta(days=7 * int(wk) - 1)).isoformat()
    return {"news_legs_per_week": per_week, "week": wk, "week_ends": end}


def _ro(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


def gate(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    legs = load_legs()
    games = legs.group_by("game_id").agg(
        pl.col("prior_et").min(), pl.col("tip_et").first(), pl.col("season").first()
    )
    # (a) post-T-60 report changes
    con = _ro(NBA_DB)
    gdf = games.select("game_id", "prior_et", "tip_et").to_pandas()
    con.register("g", gdf)
    avail = con.sql(
        "SELECT a.game_id, a.player_id, a.as_of, a.status FROM player_availability a "
        "JOIN g USING (game_id) WHERE a.source = 'nba_official_report'"
    ).pl()
    con.close()
    ch = window_changes(avail, games.select("game_id", "prior_et", "tip_et"))
    cov = coverage(legs, ch)
    kinds = (
        ch.filter(pl.col("changed"))
        .group_by(["s0", "s1"])
        .len()
        .sort("len", descending=True)
        .head(12)
    )
    # (b) T-30 lineups, history: the only per-game starter data is the box-score flag (post-hoc)
    lcon = _ro(LINEUPS_DB)
    lin = lcon.sql(
        "SELECT count(DISTINCT game_id), min(game_date), max(game_date) FROM lineup_snapshots"
    ).fetchone()
    lcon.close()
    # (c) intermediate prices: which snapshot kinds exist for 2023/2024 and live
    ocon = _ro(ODDS_DB)
    kinds_hist = ocon.sql(
        "SELECT season, snapshot_kind, count(DISTINCT game_id) FROM odds_history WHERE season <= 2024 GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    ocon.close()
    mcon = _ro(LIVE_DB)
    live_kinds = mcon.sql(
        "SELECT snapshot_kind, status, count(*) FROM live_capture_log GROUP BY 1, 2"
    ).fetchall()
    live_rows = mcon.sql("SELECT count(*) FROM live_book_odds").fetchone()
    mcon.close()
    dk = cov["book=draftkings"]
    res = {
        "legs_total": legs.height,
        "games": games.height,
        "a_post_t60_reports": cov,
        "a_status_change_kinds": [{"s0": r[0], "s1": r[1], "n": r[2]} for r in kinds.iter_rows()],
        "b_t30_lineups_history": {
            "announced_lineup_snapshots_games": int(lin[0]) if lin else 0,
            "first_date": str(lin[1]) if lin else None,
            "last_date": str(lin[2]) if lin else None,
            "note": "collector began 2026-10-09; 2023/2024 have only the post-hoc box-score starter flag (known_at violated by construction)",
        },
        "c_intermediate_price": {
            "history_snapshot_kinds_by_season": [list(map(str, r)) for r in kinds_hist],
            "live_capture_log": [list(map(str, r)) for r in live_kinds],
            "live_book_odds_rows": int(live_rows[0]) if live_rows else 0,
            "t30_price_exists": any(r[1] not in ("t60", "t5") for r in kinds_hist),
        },
        "dk_legs_per_game": dk["legs"] / dk["games"],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["gate"])
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    a = ap.parse_args()
    print(json.dumps(gate(a.out_dir), indent=1, default=str))


if __name__ == "__main__":
    main()
