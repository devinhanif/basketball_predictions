# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return,call-overload"
# ruff: noqa: E501
"""SEQ_LEARNING_CURVE_2 exporter (docs/prereg/SEQ_LEARNING_CURVE_2.md): the rule's constants, S1 = 2013-2018 with REAL position, the run-time D4 guard, the season cap, the
planted-future test (adding later games changes no earlier row), identity with the parent's NORM export where the rule changes nothing, windows 9 and 10."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from research.features import player_sequences as P
from research.features import seq_learning_curve_2_export as X
from research.features import seq_learning_curve_export as X1
from tests.research.features.test_player_sequences import N_GAMES, _raw
from tests.research.seq_learning_curve.test_export import BASE, _tips

ROOT = Path(__file__).resolve().parents[3]
ALL_PLAYERS = [11, 12, 13, 14, 21, 22, 23, 24]


def _flags(raw):
    ids = list(raw.games["game_id"])
    return pl.DataFrame(
        {
            "game_id": ids,
            "covid_bubble": [False] * len(ids),
            "no_fans": [False] * len(ids),
            "limited_fans": [False] * len(ids),
        }
    )


def _s1_raw(season: int = 2013):
    raw = _raw(season=season)
    raw.static = pd.DataFrame(
        {"player_id": ALL_PLAYERS, "position": ["Guard", "Forward", "Center", "Guard"] * 2}
    )
    X.s1_rules(raw)
    return raw


def _export(raw, seg):
    return X.export_from_raw(raw, seg, _flags(raw), **BASE)


# ------------------------------------------------------------------ the rule's constants
def test_constants_are_the_rule_table_and_the_gate_measurement() -> None:
    assert X.SEASON_MEAN[2013] == (100.910, 42.639, 21.856, 7.763)
    assert X.SEASON_MEAN[2014] == (100.047, 43.401, 22.016, 7.904)
    assert (
        X.SEASON_MEAN[2015][0] == 102.486 and X.SEASON_MEAN[2016][0] == 105.618
    )  # re-measured after the free-throw fix
    assert (
        X.SEASON_MEAN[2015][1:] == X1.SEASON_MEAN[2015][1:]
        and X.SEASON_MEAN[2016][1:] == X1.SEASON_MEAN[2016][1:]
    )
    ref = tuple(X.REF.values())
    L = {s: tuple(v.values()) for s, v in X.LPREV.items()}
    assert L[2013] == ref  # no prior season in the data: L_ref
    assert L[2014] == X.SEASON_MEAN[2013]
    assert L[2015] == X.SEASON_MEAN[2014]  # the parent used L_ref here (disclosed change)
    assert L[2016] == X.SEASON_MEAN[2015]
    for s in range(
        2018, 2024
    ):  # from 2018-19 on the parent's Lprev is unchanged: S2 and S3 are the parent's normalizer
        assert X.LPREV[s] == X1.LPREV[s], s
    assert X1.LPREV[2015] == dict(
        zip(P.STATS, ref, strict=True)
    )  # the parent's L_ref seed that this rule replaces
    gate = json.loads((ROOT / "reports/prereg_seq_learning_curve_2/gate_check_2.json").read_text())[
        "D7"
    ]["season_mean_pts_reb_ast_fg3m"]
    for s in ("2013", "2014", "2015", "2016", "2017", "2018", "2019", "2020", "2021", "2022"):
        assert tuple(gate[s]) == pytest.approx(X.SEASON_MEAN[int(s)], abs=1e-3), s


def test_segments_cap_and_stems() -> None:
    assert X.SEGMENTS == {"S1": (2013, 2018), "S2": (2019, 2021), "S3": (2022, 2023)}
    assert [X.stem(s) for s in ("S1", "S2", "S3")] == [n[:-4] for n in X.RULE.EXPORT_NAMES]
    with pytest.raises(RuntimeError, match="capped"):
        X.assert_cap(2024)
    raw = _tips(_raw(season=2024))
    with pytest.raises(RuntimeError, match="capped"):
        _export(raw, "S3")


def test_load_segment_refuses_2024_in_sql_and_missing_seasons(tmp_path) -> None:
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
    assert sorted(raw3.games["season"].unique()) == [2022, 2023] and set(raw3.pgs["game_id"]) == {
        "G0",
        "G1",
    }
    with pytest.raises(ValueError, match="database seasons"):
        X.load_segment_raw("S1", db)  # S1 needs 2013..2018; the database holds none of them


# ------------------------------------------------------------------ S1: 2013-2018, played rows only, no reports, REAL position
def test_s1_keeps_real_position_and_drops_only_non_playing_rows() -> None:
    raw = _raw(season=2013)
    raw.static = pd.DataFrame(
        {"player_id": ALL_PLAYERS, "position": ["Guard", "Forward", "Center", "Guard"] * 2}
    )
    n0 = len(raw.pgs)
    info = X.s1_rules(raw)
    assert (
        info["s1_dropped_non_playing_rows"] == 1
        and len(raw.pgs) == n0 - 1
        and (raw.pgs["minutes"] > 0).all()
    )
    assert raw.avail.empty and raw.tips is None
    assert len(raw.static) == 8  # position is NOT zeroed (the parent emptied it)
    exp, info2 = _export(raw, "S1")
    ctx, names = exp.arrays["ctx"], exp.ctx_features
    for c in ("has_report", "st_available", "st_out", "n_rep_out"):
        assert (ctx[:, names.index(c)] == 0).all(), c
    assert (
        ctx[:, [names.index(c) for c in ("pos_g", "pos_f", "pos_c")]].sum(axis=1).min() == 1.0
    )  # every row has exactly one position
    assert info2["position_by_season"]["ok"] and info2["position_by_season"]["2013"]["share"] == 0.0
    assert (ctx[:, names.index("era_s1_derived_noreport")] == 1).all()
    assert all(
        (ctx[:, names.index(c)] == 0).all()
        for c in X1.RULE_FLAG_FROM
        if X1.RULE_FLAG_FROM[c] > 2013
    )  # no rule step before 2016-17: all zeros on 2013 rows
    assert names == X.NORM_CTX_FEATURES and ctx.shape[1] == 41
    # 2013 rows use Lprev = L_ref: c = 1 on the first game (no earlier game), up to the prior alone
    c0 = exp.arrays["norm_c"][exp.meta.game_id == "G00"][0]
    assert np.allclose(c0, [X.REF[k] / X.LPREV[2013][k] for k in P.STATS])


def test_extra_channels_s1_flag_covers_2013_2018_and_flags_are_cumulative() -> None:
    s = np.arange(2013, 2024)
    e = X.extra_channels(s)
    assert (
        e.shape == (11, 7)
        and e[:, 5].tolist() == [1] * 6 + [0] * 5
        and e[:, 6].tolist() == [0] * 8 + [1, 0, 0]
    )
    assert (
        e[:3, :5] == 0
    ).all()  # 2013-15 rows carry zeros on every rule step (disclosed, no new channel)
    assert (np.diff(e[:, :5], axis=0) >= 0).all()
    assert (e[-1] == e[-2]).all() and (
        e[-1] == np.array([1, 1, 1, 1, 1, 0, 0])
    ).all()  # 2023-24 shares the 2022-23 vector


# ------------------------------------------------------------------ gate D4 enforced at export time
def test_position_guard_refuses_unknown_share_above_one_percent_before_2022_only() -> None:
    raw = _raw(season=2013)  # static covers 3 of 8 players
    X.s1_rules(raw)
    with pytest.raises(RuntimeError, match="gate D4"):
        _export(raw, "S1")
    raw2 = _tips(
        _raw(season=2023)
    )  # label 2023: not guarded (the S3 segment's position is the parent's)
    _, info = _export(raw2, "S3")
    assert info["position_by_season"]["ok"]


def test_fill_position_db_wins_then_cache_then_sidecar() -> None:
    raw = _raw(season=2013)
    raw.static = pd.DataFrame({"player_id": [11, 12], "position": ["Guard", None]})
    cache = pd.DataFrame({"player_id": [11, 12, 13], "position": ["Forward", "Forward", "Center"]})
    side = pd.DataFrame({"player_id": [11, 14, 21], "position": ["Center", "Guard", "Guard"]})
    info = X.fill_position(raw, cache, side)
    got = dict(zip(raw.static["player_id"], raw.static["position"], strict=True))
    assert got == {11: "Guard", 12: "Forward", 13: "Center", 14: "Guard", 21: "Guard"}
    assert info["db_known"] == 1 and info["known_after"] == 5


def test_sidecar_positions_reads_the_table_or_returns_empty(tmp_path) -> None:
    db = tmp_path / "h.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE players_static (player_id INT, position VARCHAR)")
    con.close()
    assert X.sidecar_positions(db).empty
    con = duckdb.connect(str(db))
    con.execute(f"CREATE TABLE {X.SIDECAR} (player_id INT, position VARCHAR)")
    con.execute(f"INSERT INTO {X.SIDECAR} VALUES (201946, 'Forward')")
    con.close()
    s = X.sidecar_positions(db)
    assert s["player_id"].tolist() == [201946] and s["position"].tolist() == ["Forward"]


# ------------------------------------------------------------------ planted future
def _truncate(raw, keep_games: int):
    ids = set(raw.games["game_id"].iloc[:keep_games])
    return dataclasses.replace(
        raw,
        games=raw.games[raw.games["game_id"].isin(ids)].reset_index(drop=True),
        pgs=raw.pgs[raw.pgs["game_id"].isin(ids)].reset_index(drop=True),
        poss_player=raw.poss_player[raw.poss_player["game_id"].isin(ids)].reset_index(drop=True),
        poss_team=raw.poss_team[raw.poss_team["game_id"].isin(ids)].reset_index(drop=True),
        avail=raw.avail[raw.avail["game_id"].isin(ids)].reset_index(drop=True),
    )


@pytest.mark.parametrize("season,seg", [(2013, "S1"), (2019, "S2"), (2023, "S3")])
def test_planted_future_later_games_change_no_earlier_row(season: int, seg: str) -> None:
    raw = _tips(_raw(season=season))
    raw.static = pd.DataFrame(
        {"player_id": ALL_PLAYERS, "position": ["Guard", "Forward", "Center", "Guard"] * 2}
    )
    if seg == "S1":
        X.s1_rules(raw)
    k = N_GAMES - 4
    short = _truncate(raw, k)
    short_flags = _flags(short)
    long_exp, _ = _export(raw, seg)
    short_exp, _ = X.export_from_raw(short, seg, short_flags, **BASE)
    early = set(short.games["game_id"])
    sel = long_exp.meta["game_id"].isin(early).to_numpy()
    assert sel.sum() == len(short_exp.meta) > 0
    assert np.array_equal(
        long_exp.meta["game_id"].to_numpy()[sel], short_exp.meta["game_id"].to_numpy()
    )
    assert np.array_equal(
        long_exp.meta["player_id"].to_numpy()[sel], short_exp.meta["player_id"].to_numpy()
    )
    for key, arr in short_exp.arrays.items():
        if key in ("stat_scale",):
            continue
        assert np.array_equal(long_exp.arrays[key][sel], arr), (
            key
        )  # bit-identical, including the normalizer factor and every token
    # and the planted values do change the LATER rows (the test can fail)
    raw2 = dataclasses.replace(raw, pgs=raw.pgs.copy())
    late = raw2.pgs["game_id"].isin(list(raw.games["game_id"].iloc[k:]))
    raw2.pgs.loc[late, ["pts", "reb", "ast", "fg3m"]] *= 3.0
    alt, _ = _export(raw2, seg)
    assert np.array_equal(alt.arrays["norm_c"][sel], long_exp.arrays["norm_c"][sel])
    assert not np.array_equal(alt.arrays["y"][~sel], long_exp.arrays["y"][~sel])


# ------------------------------------------------------------------ identity with the parent where the rule changes nothing
def test_s3_equals_the_parents_norm_export_on_the_same_rows() -> None:
    raw = _tips(_raw(season=2023))
    new, _ = _export(raw, "S3")
    old, _ = X1.export_from_raw(raw, "S3", True, _flags(raw), **BASE)
    assert new.ctx_features == old.ctx_features
    for key in old.arrays:
        assert np.array_equal(new.arrays[key], old.arrays[key]), key
    # normalize then divide returns the raw target
    raw_exp, _ = X1.export_from_raw(raw, "S3", False, _flags(raw), **BASE)
    assert np.allclose(new.arrays["y"] / new.arrays["norm_c"], raw_exp.arrays["y"], atol=1e-6)


def test_source_frame_is_not_mutated() -> None:
    raw = _tips(_raw(season=2023))
    before = raw.pgs["pts"].copy()
    _export(raw, "S3")
    assert raw.pgs["pts"].equals(before)


# ------------------------------------------------------------------ windows 9 and 10
def test_windows_extend_the_parents_to_nine_and_ten() -> None:
    assert (
        X.window_labels(9)[0] == 2014
        and X.window_labels(10)[0] == 2013
        and X.window_labels(10)[-1] == 2022
    )
    for n in range(1, 9):
        assert X.window_labels(n) == X1.window_labels(n)
    with pytest.raises(ValueError):
        X.window_labels(11)
    rng = np.random.default_rng(0)
    labs = np.repeat(np.arange(2013, 2024), 50)
    date = np.array(
        [
            np.datetime64(f"{s}-10-25") + np.timedelta64(int(d), "D")
            for s, d in zip(labs, rng.integers(0, 230, len(labs)), strict=True)
        ]
    )
    meta = pd.DataFrame(
        {"season": labs, "game_date": date, "n_prior": rng.integers(0, 10, len(labs))}
    )
    for n in range(1, 9):
        assert X.window_counts(meta, n) == X1.window_counts(meta, n), n
    c = [X.window_counts(meta, n) for n in (8, 9, 10)]
    assert (
        c[0]["fit"] < c[1]["fit"] < c[2]["fit"]
        and c[0]["val"] == c[1]["val"] == c[2]["val"]
        and c[0]["test"] == c[2]["test"]
    )


def test_era_runtime_gate_is_the_parents() -> None:
    names = X.NORM_CTX_FEATURES
    ctx = np.zeros((20, len(names)), dtype=np.float32)
    season = np.array([2022] * 10 + [2023] * 10)
    ctx[:, [names.index(c) for c in X1.RULE_FLAG_FROM]] = X.extra_channels(season)[:, :5]
    assert X.era_runtime_gate(ctx, season, names)["ok"]
    ctx[15, names.index("era_limited")] = 1.0
    assert not X.era_runtime_gate(ctx, season, names)["ok"]


# ------------------------------------------------------------------ the data_version pin
def test_export_refuses_a_history_data_version_other_than_the_rules(monkeypatch, tmp_path) -> None:
    assert X.HISTORY_DATA_VERSION == "c90139d87ded"
    X.require_data_version("c90139d87ded")
    with pytest.raises(RuntimeError, match="pinned"):
        X.require_data_version("507d4a01a024")
    called = []
    monkeypatch.setattr(X, "db_data_version", lambda db: "507d4a01a024")
    monkeypatch.setattr(X, "load_segment_raw", lambda seg: called.append(seg))
    with pytest.raises(RuntimeError, match="pinned"):
        X.build(tmp_path, ("S1",))
    assert called == []  # refused before anything was loaded
