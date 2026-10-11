"""GAME_MARKET_CONTEXT features: cutoff, source choice, signs, as-of pace, placebo."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from research.features.game_market_context import (
    attach_market,
    book_lines,
    build_game_market,
    league_total_mean,
    placebo_fixed_points,
    placebo_market,
    usable_snapshots,
)

TIP = dt.datetime(2024, 1, 10, 0, 0)


def _tips(*gids: str) -> pl.DataFrame:
    return pl.DataFrame({"game_id": list(gids), "tipoff_utc": [TIP] * len(gids)}).with_columns(
        pl.col("tipoff_utc").alias("tip_et")
    )


def _q(
    g: str, book: str, mins: float, home_pt: float, total: float, p_home: float = 0.6
) -> list[dict[str, object]]:
    ts = TIP - dt.timedelta(minutes=mins)

    def r(market: str, side: str, point: float | None, p: float) -> dict[str, object]:
        return {
            "game_id": g,
            "season": 2023,
            "snapshot_ts": ts,
            "last_update": ts,
            "book": book,
            "market": market,
            "side": side,
            "point": point,
            "implied_prob_raw": p,
        }

    return [
        r("spreads", "home", home_pt, 0.51),
        r("spreads", "away", -home_pt, 0.51),
        r("totals", "over", total, 0.51),
        r("totals", "under", total, 0.51),
        r("h2h", "home", None, p_home),
        r("h2h", "away", None, 1.0 - p_home + 0.04),
    ]


def _rows(items: list[list[dict[str, object]]]) -> pl.DataFrame:
    flat = [x for i in items for x in i]
    return pl.DataFrame(
        flat,
        schema={
            "game_id": pl.Utf8,
            "season": pl.Int64,
            "snapshot_ts": pl.Datetime("us"),
            "last_update": pl.Datetime("us"),
            "book": pl.Utf8,
            "market": pl.Utf8,
            "side": pl.Utf8,
            "point": pl.Float64,
            "implied_prob_raw": pl.Float64,
        },
    )


def test_cutoff_boundary_and_latest_usable() -> None:
    rows = _rows(
        [
            _q("g1", "pinnacle", 90, -3.0, 220.0),
            _q("g1", "pinnacle", 55, -4.0, 222.0),  # exactly tip - 55: usable and the latest
            _q("g1", "pinnacle", 54, -9.0, 240.0),  # after the cutoff: refused
            _q("g1", "pinnacle", 30, -9.0, 240.0),
        ]
    )
    used, counts = usable_snapshots(rows, _tips("g1"))
    assert counts["rows_refused_late"] == 12 and counts["rows_used"] == 6
    gm, _ = build_game_market(rows, _tips("g1"))
    assert gm["mkt_home_margin"].to_list() == [4.0]  # margin = - home spread
    assert gm["mkt_total"].to_list() == [222.0]
    assert gm["mkt_min_before_tip"].to_list()[0] >= 55.0


def test_no_real_tip_means_no_market() -> None:
    rows = _rows([_q("g9", "pinnacle", 90, -3.0, 220.0)])
    gm, counts = build_game_market(rows, _tips("g1"))
    assert gm.is_empty() and counts["rows_no_real_tip"] == 6


def test_pinnacle_preferred_else_median_consensus() -> None:
    rows = _rows(
        [
            _q("g1", "pinnacle", 62, -3.0, 220.0),
            _q("g1", "draftkings", 62, -9.0, 230.0),
            _q("g2", "draftkings", 62, -2.0, 210.0),
            _q("g2", "fanduel", 62, -4.0, 214.0),
            _q("g2", "betmgm", 62, -6.0, 230.0),
            _q("g3", "draftkings", 62, -2.0, 210.0),  # one consensus book only: below MIN_CONSENSUS
        ]
    )
    gm, _ = build_game_market(rows, _tips("g1", "g2", "g3"))
    d = {r["game_id"]: r for r in gm.iter_rows(named=True)}
    assert d["g1"]["mkt_src"] == "pinnacle" and d["g1"]["mkt_home_margin"] == 3.0
    assert d["g2"]["mkt_src"] == "consensus" and d["g2"]["mkt_home_margin"] == 4.0
    assert d["g2"]["mkt_total"] == 214.0
    assert "g3" not in d


def test_non_mirrored_spread_and_mismatched_total_are_null() -> None:
    q = _q("g1", "pinnacle", 62, -3.0, 220.0)
    q[1]["point"] = 7.0  # away side does not mirror
    q[3]["point"] = 225.0  # under != over
    bl = book_lines(_rows([q]))
    assert bl["home_margin"][0] is None and bl["total"][0] is None


def test_devig_home_probability_sums_to_one() -> None:
    bl = book_lines(_rows([_q("g1", "pinnacle", 62, -3.0, 220.0, p_home=0.6)]))
    assert abs(bl["p_home"][0] - 0.6 / (0.6 + 0.44)) < 1e-12


def _games(n_days: int = 80, per_day: int = 3) -> pl.DataFrame:
    rows = []
    for d in range(n_days):
        for k in range(per_day):
            rows.append(
                {
                    "game_id": f"g{d:03d}{k}",
                    "game_date": dt.date(2024, 1, 1) + dt.timedelta(days=d),
                    "home_pts": 110 + (d % 5),
                    "away_pts": 105,
                }
            )
    return pl.DataFrame(rows)


def test_pace_baseline_is_strictly_earlier_and_planted_future_is_inert() -> None:
    g = _games()
    lg = league_total_mean(g)
    day = dt.date(2024, 2, 20)
    ids_day = g.filter(pl.col("game_date") == day)["game_id"].to_list()
    alt = g.with_columns(
        pl.when(pl.col("game_date") >= day)
        .then(pl.col("home_pts") + 50)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    lg2 = league_total_mean(alt)
    early = g.filter(pl.col("game_date") <= day)["game_id"].to_list()  # same day included
    a = lg.filter(pl.col("game_id").is_in(early)).sort("game_id")
    b = lg2.filter(pl.col("game_id").is_in(early)).sort("game_id")
    assert a.equals(b)  # editing the day's and later scores never moves the day's baseline
    assert ids_day
    assert not lg.sort("game_id").equals(lg2.sort("game_id"))  # not inert for later days
    first = lg.sort("game_id")["lg_total_mean"].to_list()
    assert first[0] is None  # fewer than 50 prior games


def _feats() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g0", "g0", "g1", "g2"],
            "player_id": [1, 2, 3, 4],
            "is_home_f": [1, 0, 1, 1],
            "game_date": [dt.date(2024, 1, 3)] * 4,
        }
    )


def test_attach_market_signs_null_game_and_row_count() -> None:
    gm = pl.DataFrame(
        {
            "game_id": ["g0", "g1"],
            "mkt_home_margin": [4.0, -2.0],
            "mkt_total": [220.0, 210.0],
            "mkt_src": ["pinnacle", "pinnacle"],
        }
    )
    lg = pl.DataFrame({"game_id": ["g0", "g1", "g2"], "lg_total_mean": [215.0, 215.0, 215.0]})
    out = attach_market(_feats(), gm, lg)
    assert out.height == 4
    assert out["mkt_margin"].to_list() == [4.0, -4.0, -2.0, None]  # home +, away -, no market NULL
    assert out["mkt_abs_margin"].to_list() == [4.0, 4.0, 2.0, None]
    assert out["mkt_pace"].to_list() == [5.0, 5.0, -5.0, None]
    assert out["player_id"].to_list() == [1, 2, 3, 4]


def _big() -> pl.DataFrame:
    rows = []
    for d in range(12):
        for k in range(3 if d % 4 else 1):  # some single-game dates
            for p in range(2):
                rows.append(
                    {
                        "game_id": f"g{d}{k}",
                        "player_id": d * 100 + k * 10 + p,
                        "season": 2024,
                        "game_date": dt.date(2024, 1, 1) + dt.timedelta(days=d),
                        "is_home_f": p % 2,
                        "_hm": float(d * 3 + k),
                        "_tot": 200.0 + d * 2 + k,
                        "_lg": 210.0,
                    }
                )
    return pl.DataFrame(rows)


def test_placebo_is_a_derangement_within_date_with_same_marginals() -> None:
    f = attach_market_like = _big()
    from research.features.game_market_context import derive_columns

    f = derive_columns(attach_market_like)
    p = placebo_market(f, 0)
    assert p.height == f.height and p["player_id"].to_list() == f["player_id"].to_list()
    r = f.unique("game_id").sort("game_id")
    q = p.unique("game_id").sort("game_id")
    assert np.allclose(np.sort(r["_hm"].to_numpy()), np.sort(q["_hm"].to_numpy()))
    assert np.allclose(np.sort(r["_tot"].to_numpy()), np.sort(q["_tot"].to_numpy()))
    fp = placebo_fixed_points(f, p)
    assert fp["unchanged_share"] == 0.0  # no game keeps its own numbers (pools of >= 2 here)
    assert p.equals(placebo_market(f, 0))  # seeded
    assert not p.equals(placebo_market(f, 1))


def test_placebo_nulls_stay_null() -> None:
    from research.features.game_market_context import derive_columns

    f = derive_columns(_big().with_columns(pl.lit(None, dtype=pl.Float64).alias("_hm")))
    p = placebo_market(f, 0)
    assert p["mkt_margin"].null_count() == p.height
