# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return,call-overload"
# ruff: noqa: E501
"""SEQ_LEARNING_CURVE export (docs/prereg/SEQ_LEARNING_CURVE.md): past-only normalizer, season cap, S1 rules (played rows only, no reports, position zero), flag vectors,
window row sets, norm_c round trip."""

from __future__ import annotations

import dataclasses
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from research.features import player_sequences as P
from research.features import seq_learning_curve_export as X
from tests.research.features.test_player_sequences import N_GAMES, _date, _raw

BASE = dict(k=5, rotation_min=5.0, min_rotation_games=2)


def _tips(raw):
    raw.tips = pl.DataFrame(
        {
            "game_id": list(raw.games["game_id"]),
            "tip_et": [dt.datetime.combine(_date(i), dt.time(19, 30)) for i in range(N_GAMES)],
        },
        schema={"game_id": pl.Utf8, "tip_et": pl.Datetime("us")},
    )
    return raw


def _flags(raw):
    return pl.DataFrame(
        {
            "game_id": list(raw.games["game_id"]),
            "covid_bubble": [False] * N_GAMES,
            "no_fans": [False] * N_GAMES,
            "limited_fans": [False] * N_GAMES,
        }
    )


def _export(raw, seg, norm):
    return X.export_from_raw(raw, seg, norm, _flags(raw), **BASE)


# ------------------------------------------------------------------ normalizer
def _games_pgs(n_days: int = 6, per_game: float = 100.0):
    """One game a day, two teams, three players each; every team-game totals ``per_game`` pts (and 40 reb / 20 ast / 10 fg3m)."""
    games = pd.DataFrame(
        {
            "game_id": [f"X{i}" for i in range(n_days)],
            "game_date": [dt.date(2023, 11, 1) + dt.timedelta(days=i) for i in range(n_days)],
            "season": 2023, "home_team": 1, "away_team": 2, "home_pts": 100, "away_pts": 100,
        }
    )  # fmt: skip
    rows = []
    for i in range(n_days):
        for t in (1, 2):
            for p in range(3):
                rows.append((f"X{i}", t * 10 + p, t, per_game / 3, 40 / 3, 20 / 3, 10 / 3))
    return games, pd.DataFrame(
        rows, columns=["game_id", "player_id", "team_id", "pts", "reb", "ast", "fg3m"]
    )


def test_normalizer_matches_the_written_formula_by_hand() -> None:
    games, pgs = _games_pgs()
    ref = {"pts": 110.0, "reb": 44.0, "ast": 22.0, "fg3m": 11.0}
    lprev = {2023: {"pts": 120.0, "reb": 40.0, "ast": 25.0, "fg3m": 10.0}}
    c = X.norm_factors(games, pgs, lprev=lprev, ref=ref).set_index("game_id")
    assert c.loc["X0", "c_pts"] == pytest.approx(110.0 / 120.0)  # no earlier game: the prior alone
    # game X3: 3 earlier days x 2 team-games = 6 team-games of 100 pts, prior weight 200 at 120
    assert c.loc["X3", "c_pts"] == pytest.approx(110.0 / ((6 * 100.0 + 200 * 120.0) / (6 + 200)))
    assert c.loc["X3", "c_fg3m"] == pytest.approx(11.0 / ((6 * 10.0 + 200 * 10.0) / 206))


def test_normalizer_is_one_for_reference_level_inputs() -> None:
    games, pgs = _games_pgs(per_game=114.330)
    pgs["reb"], pgs["ast"], pgs["fg3m"] = 43.422 / 3, 25.211 / 3, 12.315 / 3
    c = X.norm_factors(games, pgs)  # the frozen 2023 prior is the 2022-23 means
    for k in P.STATS:
        assert np.allclose(c[f"c_{k}"], 1.0, atol=1e-12)


def test_normalizer_is_past_only_planted_future_and_same_day_excluded() -> None:
    games, pgs = _games_pgs()
    base = X.norm_factors(games, pgs).set_index("game_id")
    pgs2 = pgs.copy()
    late = pgs2["game_id"].isin(["X3", "X4", "X5"])
    pgs2.loc[late, ["pts", "reb", "ast", "fg3m"]] *= 3.0  # plant a different future (dates >= X3's)
    alt = X.norm_factors(games, pgs2).set_index("game_id")
    for gid in (
        "X0",
        "X1",
        "X2",
        "X3",
    ):  # X3 itself changed, but c(X3) uses only strictly earlier games
        assert (alt.loc[gid] == base.loc[gid]).all(), gid  # bit-identical
    assert not (alt.loc["X4"] == base.loc["X4"]).all()
    # two games on one date see the same factor and neither sees the other
    g2 = pd.concat(
        [games, games.iloc[[2]].assign(game_id="X2b", home_team=3, away_team=4)], ignore_index=True
    )
    p2 = pd.concat(
        [pgs, pgs[pgs.game_id == "X2"].assign(game_id="X2b", team_id=lambda d: d.team_id + 2)],
        ignore_index=True,
    )
    p2.loc[p2.game_id == "X2b", ["pts"]] *= 9.0
    c2 = X.norm_factors(g2, p2).set_index("game_id")
    assert c2.loc["X2", "c_pts"] == c2.loc["X2b", "c_pts"] == base.loc["X2", "c_pts"]


def test_normalize_then_divide_round_trips_to_1e6_and_scales_tokens_base_and_targets() -> None:
    raw = _tips(_raw(season=2023))
    exp_raw, _ = _export(raw, "S3", False)
    exp_n, _ = _export(raw, "S3", True)
    assert (exp_raw.arrays["norm_c"] == 1.0).all()
    c = exp_n.arrays["norm_c"]
    assert (c != 1.0).any() and np.array_equal(
        exp_n.meta["game_id"].to_numpy(), exp_raw.meta["game_id"].to_numpy()
    )
    back = exp_n.arrays["y"] / c.astype(np.float64)
    assert np.allclose(back, exp_raw.arrays["y"], atol=1e-6)  # normalize then divide returns raw
    # every recency base is a mean of normalized series: dividing by one common c is NOT exact, but base moves with c
    assert not np.allclose(exp_n.arrays["base"], exp_raw.arrays["base"])
    # token pts channel is normalized by the factor of the TOKEN's game (not the target's)
    si = P._SI["pts"]
    r = int(np.flatnonzero(exp_n.arrays["mask"].sum(axis=1) == BASE["k"])[0])
    assert not np.allclose(exp_n.arrays["seq"][r, :, si], exp_raw.arrays["seq"][r, :, si])
    # the source frame was not mutated
    assert raw.pgs["pts"].max() == _raw().pgs["pts"].max()
    # non-stat structure is identical
    assert X.norm_vs_raw_structure(exp_n, exp_raw)["same_rows"]
    assert all(v for v in X.norm_vs_raw_structure(exp_n, exp_raw).values())


# ------------------------------------------------------------------ cap
def test_cap_refuses_season_2024_in_code_and_in_sql(tmp_path) -> None:
    raw = _tips(_raw(season=2024))
    with pytest.raises(RuntimeError, match="capped"):
        X.export_from_raw(raw, "S3", False, _flags(raw), **BASE)
    with pytest.raises(RuntimeError):
        X.assert_cap(2024)
    db = tmp_path / "m.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT, home_pts INT, away_pts INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, minutes DOUBLE, pts INT, reb INT, ast INT, fg3m INT, stl INT, blk INT, tov INT, starter BOOLEAN, fga INT, fta INT)"
    )
    con.execute(
        "CREATE TABLE possessions (game_id VARCHAR, shooter_id INT, off_team INT, outcome VARCHAR, shot_zone VARCHAR)"
    )
    con.execute(
        "CREATE TABLE player_availability (game_id VARCHAR, player_id INT, status VARCHAR, as_of TIMESTAMP, source VARCHAR)"
    )
    con.execute("CREATE TABLE players_static (player_id INT, position VARCHAR)")
    for i, s in enumerate((2022, 2023, 2024)):
        con.execute(f"INSERT INTO games VALUES ('G{i}', DATE '{s}-11-01', {s}, 1, 2, 100, 90)")
        con.execute(
            f"INSERT INTO player_game_stats VALUES ('G{i}', 11, 1, 30.0, 10, 3, 2, 1, 0, 0, 1, true, 8, 2)"
        )
    con.close()
    raw3 = X.load_segment_raw("S3", db)
    assert sorted(raw3.games["season"].unique()) == [
        2022,
        2023,
    ]  # the 2024 game never left the database
    assert set(raw3.pgs["game_id"]) == {"G0", "G1"}


# ------------------------------------------------------------------ S1 rules
def test_s1_played_rows_only_no_reports_position_zero_and_gap_tokens() -> None:
    raw = _raw(season=2015)
    assert (
        (raw.pgs["minutes"].isna()).any() and len(raw.static) == 3 and len(raw.avail) == 4
    )  # DNP row, positions and report rows exist upstream
    n0 = len(raw.pgs)
    info = X.s1_rules(raw)
    assert (
        info["s1_dropped_non_playing_rows"] == 1
        and len(raw.pgs) == n0 - 1
        and (raw.pgs["minutes"] > 0).all()
    )
    assert raw.avail.empty and raw.static.empty and raw.tips is None
    exp, _ = X.export_from_raw(raw, "S1", True, _flags(raw), **BASE)
    ctx = exp.arrays["ctx"]
    names = exp.ctx_features
    for c in ("has_report", "st_available", "st_out", "n_rep_out", "pos_g", "pos_f", "pos_c"):
        assert (ctx[:, names.index(c)] == 0).all(), c
    assert exp.meta["has_report"].eq(0).all()
    assert (ctx[:, names.index("era_s1_derived_noreport")] == 1).all()
    # the player's missed games are still absent tokens (gap games), built from played rows only; S1 has no DNP rows
    r = exp.meta.index[(exp.meta.game_id == "G07") & (exp.meta.player_id == 12)][0]
    assert (
        exp.arrays["seq"][r].astype(np.float32)[:, P._SI["absent"]] == 1
    ).sum() >= 2  # G03 (was a DNP row) and G04 are both gap tokens now
    assert names == X.NORM_CTX_FEATURES


def test_s2_s3_keep_the_rt_behaviour_position_known_and_no_s1_flag() -> None:
    raw = _tips(_raw(season=2023))
    exp, info = _export(raw, "S3", False)
    assert (
        exp.ctx_features == X.RAW_CTX_FEATURES
        and exp.arrays["ctx"].shape[1] == len(P.CTX_FEATURES) + 3
    )
    assert exp.arrays["ctx"][:, exp.ctx_features.index("pos_g")].max() == 1.0
    assert info["era"]["rows_without_flags"] == 0
    expn, _ = _export(raw, "S3", True)
    assert expn.arrays["ctx"].shape[1] == len(P.CTX_FEATURES) + 3 + 7 == 41


# ------------------------------------------------------------------ flags
def test_flag_vectors_on_2023_24_equal_2022_23_and_cumulative_steps() -> None:
    s = np.array([2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023])
    e = X.extra_channels(s)
    assert e.shape == (9, 7)
    assert (e[-1] == e[-2]).all()  # 2023-24 shares the vector of 2022-23
    assert (e[-1] == np.array([1, 1, 1, 1, 1, 0, 0])).all()
    assert (np.diff(e[:, :5], axis=0) >= 0).all()  # cumulative step indicators never turn off
    assert e[:, 5].tolist() == [1, 1, 1, 1, 0, 0, 0, 0, 0] and e[:, 6].tolist() == [
        0,
        0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
    ]
    assert e[0].tolist() == [0, 0, 0, 0, 0, 1, 0]
    # no channel is 1 only on test rows: every channel that is 1 on 2023 is also 1 on 2022
    assert ((e[-1] == 1) <= (e[-2] == 1)).all()


def test_era_runtime_gate_passes_on_the_2022_vector_and_voids_on_a_mismatch() -> None:
    names = X.NORM_CTX_FEATURES
    n = 20
    ctx = np.zeros((n, len(names)), dtype=np.float32)
    season = np.array([2022] * 10 + [2023] * 10)
    ctx[:, [names.index(c) for c in list(X.RULE_FLAG_FROM)]] = X.extra_channels(season)[:, :5]
    assert X.era_runtime_gate(ctx, season, names)["ok"]
    ctx[15, names.index("era_limited")] = 1.0
    assert not X.era_runtime_gate(ctx, season, names)["ok"]


# ------------------------------------------------------------------ windows
def test_window_row_sets() -> None:
    rng = np.random.default_rng(0)
    labs = np.repeat(np.arange(2015, 2024), 40)
    start = {s: np.datetime64(f"{s}-10-25") for s in labs}
    date = (
        np.array(
            [
                (start[s] + np.timedelta64(int(d), "D"))
                for s, d in zip(labs, rng.integers(0, 230, len(labs)), strict=True)
            ]
        )
        .astype("datetime64[D]")
        .astype(np.int64)
    )
    npr = rng.integers(0, 10, len(labs))
    cold_end = np.datetime64(X.COLD_END)
    d64 = date.astype("datetime64[D]")
    test_sets = []
    for n in range(1, 9):
        m = X.window_masks(labs, date, npr, n)
        assert (
            set(np.unique(labs[m["fit"]])) <= set(X.window_labels(n)) and (npr[m["fit"]] >= 3).all()
        )
        assert not (m["fit"] & m["val"]).any() and not (m["fit"] & m["test"]).any()
        assert (labs[m["val"]] == 2022).all() and (
            d64[m["val"]] >= cold_end
        ).all()  # the same validation set for every N
        assert (d64[m["fit"] & (labs == 2022)] < cold_end).all()
        assert X.window_labels(n)[-1] == 2022 and len(X.window_labels(n)) == n
        assert (
            m["pool_eligible"][m["test"] & (npr >= 3)].all()
            and not m["pool_eligible"][(labs < min(X.window_labels(n)))].any()
        )
        test_sets.append(m["test"].copy())
    assert all((t == test_sets[0]).all() for t in test_sets)  # identical test rows for every N
    w1, w4, w8 = (X.window_masks(labs, date, npr, n) for n in (1, 4, 8))
    assert (w1["val"] == w8["val"]).all()
    assert (w1["fit"] <= w4["fit"]).all() and (w4["fit"] <= w8["fit"]).all()  # nested windows
    assert set(np.unique(labs[w4["fit"]])) == {2019, 2020, 2021, 2022} and set(
        np.unique(labs[w8["fit"]])
    ) == set(range(2015, 2023))
    with pytest.raises(ValueError):
        X.window_labels(9)


def test_manifest_sha_is_order_free() -> None:
    assert (
        X.manifest_sha({"a": "1", "b": "2"})
        == X.manifest_sha({"b": "2", "a": "1"})
        != X.manifest_sha({"a": "1", "b": "3"})
    )


def test_dataclass_replace_does_not_leak_between_variants() -> None:
    raw = _tips(_raw(season=2023))
    before = raw.pgs["pts"].copy()
    _export(raw, "S3", True)
    assert raw.pgs["pts"].equals(before) and dataclasses.is_dataclass(raw)
