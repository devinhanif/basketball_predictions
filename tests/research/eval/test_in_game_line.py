# ruff: noqa: E501
"""IN_GAME_LINE_MODEL: rule constants, state features on a synthetic game, gate verdict, freeze refusal. No real data."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from research.eval import in_game_line_eval as ev
from research.eval import in_game_line_features as feat
from research.eval import in_game_line_gate as gate
from research.eval import in_game_line_rule as rule

H, A = 100, 200  # home / away team ids


def test_holdout_guards() -> None:
    assert rule.check_game_id("0022400001") == "0022400001"
    with pytest.raises(SystemExit):
        rule.check_game_id("0022500001")
    with pytest.raises(SystemExit):
        rule.check_game_id("0042400001")  # playoffs are out of scope
    with pytest.raises(SystemExit):
        rule.check_season(2025)
    with pytest.raises(SystemExit):
        feat.pbp_path("0022500001", feat.data_root())


def test_checkpoints_buckets_lines() -> None:
    assert [rule.bucket_of(e) for e in rule.CHECKPOINTS] == [
        "Q1",
        "Q1",
        "Q2",
        "Q2",
        "Q3",
        "Q3",
        "Q4",
    ]
    assert 48 not in rule.CHECKPOINTS
    assert rule.lines_open("pts", 17) == (20, 25, 30)
    assert rule.lines_open("pts", 10) == (15, 20, 25, 30)  # a line already reached is not a unit
    assert rule.lines_open("fg3m", 4) == ()
    assert rule.is_blowout(-20) and not rule.is_blowout(19)


def test_b0_minutes_rule_is_period_aware() -> None:
    # Through Q3 the margin acts only on the Q4 part of the remaining time; after Q3 it halves the whole remainder.
    base = rule.b0_remaining_minutes(30, 24.0, 1, 5)
    blow = rule.b0_remaining_minutes(30, 24.0, 1, 30)
    share = (24.0 + rule.SHARE_K * rule.SHARE_PRIOR_STARTER) / (30 + rule.SHARE_K)
    assert base - blow == pytest.approx(share * 12 * (1 - rule.Q4_FACTOR_BLOWOUT))
    assert rule.b0_remaining_minutes(36, 29.0, 1, -25) == pytest.approx(
        0.5 * rule.b0_remaining_minutes(36, 29.0, 1, 3)
    )
    # with no Q4 left in the remaining time, nothing is scaled: at e=42 remaining is 6 min and all of it is Q4
    assert rule.b0_remaining_minutes(42, 30.0, 1, 30) == pytest.approx(
        0.5 * rule.b0_remaining_minutes(42, 30.0, 1, 0)
    )


def test_distribution_helpers() -> None:
    k = np.arange(-1, 8)
    sf = rule.nb_sf("pts", 5.0, k)
    assert sf[0] == 1.0 and sf[1] == 1.0 and np.all(np.diff(sf) <= 1e-12)
    assert rule.b1_prob("reb", 4, 6, 7.0) == pytest.approx(
        1.0
    )  # line already below the current count
    p = rule.b1_prob("reb", np.array([4, 6, 8]), np.array([1, 1, 1]), np.array([5.0, 5.0, 5.0]))
    assert np.all((p >= 0) & (p <= 1)) and np.all(np.diff(p) < 0)
    pb0 = rule.b0_prob("pts", 20, 18, 30.0, 36, 1, 3)
    assert 0 < pb0 < 1


def test_bootstrap_clusters_and_top5() -> None:
    rng = np.random.default_rng(0)
    games = np.repeat(np.arange(200), 10)
    players = rng.integers(0, 50, size=len(games))
    delta = rng.normal(-0.01, 0.1, size=len(games)) + np.repeat(rng.normal(0, 0.05, 200), 10)
    codes = {"game": games, "player_season": players, "week": games // 7}
    r = rule.binding_ci(delta, codes, n_boot=300)
    assert r["lo"] < r["point"] < r["hi"]
    assert r["binding"] in rule.CLUSTERINGS and r["half"] == pytest.approx(max(r["by"].values()))
    assert rule.boot_ci(delta, games, 0, 300) == rule.boot_ci(delta, games, 0, 300)  # seeded
    assert rule.top5_share(np.r_[np.full(5, -1.0), np.zeros(95)], np.arange(100)) == pytest.approx(
        1.0
    )
    assert rule.top5_share(np.full(100, -1.0), np.arange(100)) == pytest.approx(0.05)


def test_platt_and_slope() -> None:
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20000)
    y = (rng.random(20000) < rule.sigmoid(1.5 * rule.logit(p) + 0.2)).astype(float)
    a, b = rule.platt_fit(p, y)
    assert b == pytest.approx(1.5, abs=0.1) and a == pytest.approx(0.2, abs=0.1)
    assert rule.calibration_slope(rule.platt_apply(p, (a, b)), y) == pytest.approx(1.0, abs=0.05)
    assert rule.bh_reject([0.001, 0.2, 0.03]) == [True, False, True]


def _synthetic() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    g = "0022200999"
    games = pl.DataFrame(
        {
            "game_id": [g],
            "game_date": [pl.date(2022, 11, 1)]
            if False
            else [__import__("datetime").date(2022, 11, 1)],
            "home_team": [H],
            "away_team": [A],
        }
    )
    rows: list[dict] = []

    def ev_(
        t_left: str,
        period: int,
        team: int,
        pid: int,
        name: str,
        typ: str,
        desc: str,
        sub: str = "",
        sv: int = 0,
        sh: str = "",
        sa: str = "",
    ) -> None:
        rows.append(
            dict(
                game_id=g,
                action_number=len(rows) + 1,
                period=period,
                clock=t_left,
                team_id=team,
                player_id=pid,
                player_name=name,
                action_type=typ,
                sub_type=sub,
                description=desc,
                score_home=sh,
                score_away=sa,
                shot_result="",
                shot_value=sv,
            )
        )

    ev_("PT12M00.00S", 1, 0, 0, "", "period", "Start of 1st Period", "start", sh="0", sa="0")
    ev_(
        "PT11M00.00S",
        1,
        H,
        1,
        "Ann",
        "Made Shot",
        "Ann 25' 3PT Jump Shot (3 PTS) (Bob 1 AST)",
        sv=3,
        sh="3",
        sa="0",
    )
    ev_("PT10M00.00S", 1, A, 11, "Zed", "Made Shot", "Zed Layup (2 PTS)", sv=2, sh="3", sa="2")
    ev_(
        "PT09M00.00S", 1, H, 1, "Ann", "Free Throw", "Ann Free Throw 1 of 2 (4 PTS)", sh="4", sa="2"
    )
    ev_("PT09M00.00S", 1, H, 1, "Ann", "Free Throw", "MISS Ann Free Throw 2 of 2")
    ev_("PT08M00.00S", 1, A, 11, "Zed", "Rebound", "Zed REBOUND (Off:0 Def:1)")
    ev_("PT07M00.00S", 1, H, 2, "Bob", "Foul", "Bob P.FOUL (P1.T1) (X.Ref)", sub="Personal")
    ev_("PT06M00.00S", 1, H, 2, "Bob", "Foul", "Bob T.FOUL (P2.T1) (X.Ref)", sub="Technical")
    ev_("PT00M00.00S", 1, 0, 0, "", "period", "End of 1st Period", "end", sh="4", sa="2")
    # after the first checkpoint (6 min = 360 s elapsed => left 06:00 is at t = 360): this made shot is at t = 420
    ev_("PT05M00.00S", 1, H, 1, "Ann", "Made Shot", "Ann Layup (6 PTS)", sv=2, sh="6", sa="2")
    pbp = (
        pl.DataFrame(rows)
        .with_columns(feat.clock_seconds_expr().alias("left"))
        .with_columns(feat.elapsed_expr().alias("t"))
        .drop("left")
    )
    st = pl.DataFrame(
        {
            "game_id": [g, g],
            "team_id": [H, A],
            "period": [1, 1],
            "start_clock": [720.0, 720.0],
            "end_clock": [0.0, 0.0],
            "players": [[1, 2, 3, 4, 5], [11, 12, 13, 14, 15]],
        },
        schema_overrides={"players": pl.Array(pl.Int32, 5)},
    )
    return games, pbp, st, pbp


def test_state_frame_counts_only_the_past() -> None:
    games, pbp, st, _ = _synthetic()
    s = feat.state_frame(2022, feat.data_root(), False, games, pbp, st)
    ann6 = s.filter((pl.col("player_id") == 1) & (pl.col("e") == 6)).row(0, named=True)
    assert (
        ann6["pts"] == 4.0
        and ann6["fg3m"] == 1.0
        and ann6["minutes"] == pytest.approx(6.0)
        and ann6["starter"] == 1
    )
    assert ann6["margin"] == 2 and ann6["ast"] == 0.0
    bob6 = s.filter((pl.col("player_id") == 2) & (pl.col("e") == 6)).row(0, named=True)
    assert (
        bob6["ast"] == 1.0 and bob6["fouls"] == 1
    )  # technical not counted; assist resolved by surname
    zed6 = s.filter((pl.col("player_id") == 11) & (pl.col("e") == 6)).row(0, named=True)
    assert zed6["reb"] == 1.0 and zed6["margin"] == -2
    ann12 = s.filter((pl.col("player_id") == 1) & (pl.col("e") == 12)).row(0, named=True)
    assert ann12["pts"] == 6.0  # the t = 420 made shot appears only from the second checkpoint on
    u = feat.expand_units(s.filter(pl.col("e") == 6), "pts")
    assert set(u.filter(pl.col("player_id") == 1)["line"].to_list()) == {10, 15, 20, 25, 30}
    assert u.filter(pl.col("player_id") == 1)["cur"].to_list() == [4.0] * 5


def test_planted_future_changes_nothing_before_the_cut() -> None:
    games, pbp, st, _ = _synthetic()
    base = feat.state_frame(2022, feat.data_root(), False, games, pbp, st)
    later = pbp.filter(pl.col("t") > 360)
    planted = pl.concat(
        [
            pbp,
            later.with_columns(
                (pl.col("action_number") + 1000).alias("action_number"),
                pl.lit("99").alias("score_home"),
            ),
        ]
    )
    alt = feat.state_frame(2022, feat.data_root(), False, games, planted, st)
    key = ["game_id", "team_id", "player_id", "e"]
    assert base.filter(pl.col("e") <= 6).sort(key).equals(alt.filter(pl.col("e") <= 6).sort(key))
    assert not base.filter(pl.col("e") > 6).sort(key).equals(alt.filter(pl.col("e") > 6).sort(key))


def test_completeness_flags_a_gap() -> None:
    games, pbp, st, _ = _synthetic()
    c = feat.completeness(games, pbp, st)
    assert (
        c["pbp_ok"][0] is False
    )  # only one period in the synthetic game: not a full reconstruction
    assert c["full"][0] is False


def test_fold_names() -> None:
    d = pl.DataFrame({"player_name": ["Bogdanović", "Green"]})
    assert feat.fold_names(d)["player_name"].to_list() == ["bogdanovic", "green"]


def test_gate_verdict_logic() -> None:
    cell = {"bucket": "Q3", "blow": True, "rows": 5000, "player_games": 300, "games": 120}
    ok_cells = [{**cell, "bucket": b, "blow": bl} for b, bl in rule.REQUIRED_CELLS]
    season = {
        "completeness": {"full": 0.99},
        "stats": {
            "pts": {
                "cells": ok_cells,
                "power": {"de_mean_half_width": 0.001, "positive_control_all_clear": True},
            }
        },
    }
    res = {
        "seasons": {"2023": season, "2024": season},
        "planted_future": {"2023": {"identical_before_cut": True, "later_rows_differ": True}},
    }
    assert gate.verdict(res)["word"] == "PASS"
    bad = {
        **season,
        "stats": {
            "pts": {
                "cells": ok_cells[:-1] + [{**cell, "player_games": 10}],
                "power": season["stats"]["pts"]["power"],
            }
        },
    }
    v = gate.verdict({**res, "seasons": {"2023": bad, "2024": season}})
    assert v["word"] == "FAIL" and "2023_cell_Q4_blow" in v["failed"]


def test_primary_verdict_requires_everything() -> None:
    ci = {"point": -0.006, "hi": -0.002}
    kw = dict(
        select_vs_b0=-0.006,
        select_vs_b1=-0.006,
        report_vs_b0=ci,
        report_vs_b1=ci,
        report_top5_share=0.2,
        report_slope=1.0,
        slice_deltas={"blowout": 0.0},
        shuffled_gain_vs_b0=0.0,
        planted_future_ok=True,
    )
    assert rule.primary_verdict(**kw)["pass"]  # type: ignore[arg-type]
    assert rule.primary_verdict(**{**kw, "report_vs_b1": {"point": -0.006, "hi": 0.001}})[
        "failed"
    ] == ["ci_upper_below_zero_vs_b1"]  # type: ignore[arg-type]
    assert (
        "no_slice_hurt" in rule.primary_verdict(**{**kw, "slice_deltas": {"last6": 0.02}})["failed"]
    )  # type: ignore[arg-type]


def test_freeze_helpers_and_run_refuses() -> None:
    doc = "text\nPIN_X: abc\nLIB_python: 3.12.1\n" + rule.MARKER + "\nPIN_Y: zzz\n"
    assert rule.declared(doc) == {"PIN_X": "abc", "LIB_python": "3.12.1"}
    assert rule.input_mismatches({"PIN_X": "abc"}, {"PIN_X": "abd"}) != []
    assert rule.freeze_commit("0" * 64, run=lambda *a: "") is None
    with pytest.raises(RuntimeError, match="not frozen"):
        rule.require_harness_pinned("0" * 64, run=lambda *a: "")
    with pytest.raises((RuntimeError, SystemExit)):
        ev.run("0" * 64, feat.data_root())
    with pytest.raises(SystemExit):
        ev.verify_inputs({"PIN_A": "x"}, {"PIN_A": "y"})


def test_a0_prob_truncation() -> None:
    v = [[0.9, 0.6, 0.3, 0.1]]
    assert ev.a0_prob(v, np.array([3]), np.array([1]))[0] == pytest.approx(0.3 / 0.9)
    assert ev.a0_prob(v, np.array([2]), np.array([0]))[0] == pytest.approx(0.6)
    assert ev.a0_prob(v, np.array([9]), np.array([0]))[0] == 0.0  # beyond the stored width


def test_arm_probs_runs_and_shuffle_changes_it() -> None:
    rng = np.random.default_rng(0)
    n = 1500

    def frame(m: int) -> pl.DataFrame:
        e = rng.choice(rule.CHECKPOINTS, m)
        minutes = np.minimum(e * rng.uniform(0.4, 0.9, m), e)
        cur = rng.integers(0, 15, m).astype(float)
        line = rng.choice([20, 25, 30], m)
        keep = cur < line
        df = pl.DataFrame(
            {
                "line": line,
                "cur": cur,
                "minutes": minutes,
                "e": e,
                "starter": rng.integers(0, 2, m),
                "margin": rng.integers(-30, 30, m),
                "fouls": rng.integers(0, 5, m),
                "pregame_mean": rng.uniform(5, 25, m),
                "rec_minutes": rng.uniform(10, 35, m),
                "y_rem_min": rng.uniform(0, 20, m),
                "y": rng.integers(0, 2, m),
            }
        )
        return df.filter(pl.Series(keep))

    tr, te = frame(n), frame(400)
    stage_a = ev.fit_minutes_stage(ev.add_derived(tr))
    out = ev.arm_probs(tr, te, "pts", stage_a)
    assert set(out) == {"b0", "b1", "m"} and all(
        len(v) == te.height and np.all((v > 0) & (v < 1)) for v in out.values()
    )
    out2 = ev.arm_probs(tr, te, "pts", stage_a, shuffle_seed=3)
    assert not np.allclose(out["m"], out2["m"])


def test_power_and_positive_control_on_synthetic_units() -> None:
    rng = np.random.default_rng(2)
    n = 6000
    d = __import__("datetime").date
    units = pl.DataFrame(
        {
            "game_id": [f"g{i % 300}" for i in range(n)],
            "player_id": rng.integers(0, 60, n),
            "team_id": rng.integers(0, 30, n),
            "game_date": [d(2023, 11, 1)] * n,
            "e": rng.choice(rule.CHECKPOINTS, n),
            "line": rng.choice([10, 15, 20], n),
            "cur": rng.integers(0, 9, n).astype(float),
            "minutes": rng.uniform(5, 30, n),
            "starter": rng.integers(0, 2, n),
            "margin": rng.integers(-25, 25, n),
        }
    )
    p0 = gate.b0_for(units, "pts")
    r = gate.power(units, "pts", p0, gate.cluster_codes(units))
    assert r["de_mean_half_width"] > 0 and len(r["positive_control"]) == rule.POS_SEEDS
    assert all(p["point"] < 0 for p in r["positive_control"])  # the oracle gains by construction


def test_non_personal_fouls_with_running_number_zero_are_not_counted() -> None:
    """Run 1 of the gate: 'Non-Unsportsmanlike Tech Foul - Flopping (P0.T1)' was counted as personal."""
    games, pbp, _, _ = _synthetic()
    extra = (
        pbp.filter(pl.col("action_type") == "Foul")
        .head(1)
        .with_columns(
            pl.lit("Bob Non-Unsportsmanlike Tech Foul - Flopping (P0.T1) (X.Ref)").alias(
                "description"
            ),
            pl.lit("Flopping").alias("sub_type"),
        )
    )
    n_before = feat.foul_events(pbp).height
    assert feat.foul_events(pl.concat([pbp, extra])).height == n_before


def test_minutes_are_deterministic_under_row_order() -> None:
    games, pbp, st, _ = _synthetic()
    rows = feat.stint_rows(st)
    shuffled = rows.sample(fraction=1.0, shuffle=True, seed=3)
    a = feat.minutes_at(rows, 360.0).sort("game_id", "team_id", "player_id")
    b = feat.minutes_at(shuffled, 360.0).sort("game_id", "team_id", "player_id")
    assert a.equals(b)
    assert a["minutes"].to_list() == [6.0] * 10


def _roster_game() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Home team with two players named Green (ids 21, 22); the assist text prints 'D. Green' and only 21 is on court at that moment."""
    g = "0022200998"
    games = pl.DataFrame(
        {
            "game_id": [g],
            "game_date": [__import__("datetime").date(2022, 11, 2)],
            "home_team": [H],
            "away_team": [A],
        }
    )
    rows: list[dict] = []

    def ev_(clock: str, team: int, pid: int, name: str, typ: str, desc: str, sv: int = 0) -> None:
        rows.append(
            dict(
                game_id=g,
                action_number=len(rows) + 1,
                period=1,
                clock=clock,
                team_id=team,
                player_id=pid,
                player_name=name,
                action_type=typ,
                sub_type="",
                description=desc,
                score_home="",
                score_away="",
                shot_result="",
                shot_value=sv,
            )
        )

    ev_(
        "PT11M00.00S", H, 1, "Ann", "Made Shot", "Ann Layup (2 PTS) (D. Green 1 AST)", 2
    )  # 21 on court (first stint)
    ev_(
        "PT05M00.00S", H, 1, "Ann", "Made Shot", "Ann Layup (4 PTS) (J. Green 1 AST)", 2
    )  # 22 on court (second stint)
    ev_(
        "PT04M00.00S", H, 1, "Ann", "Made Shot", "Ann Layup (6 PTS) (Zzz 1 AST)", 2
    )  # nobody by that name: unresolved
    ev_("PT03M00.00S", H, 21, "Green", "Rebound", "Green REBOUND")
    ev_("PT03M00.00S", H, 22, "Green", "Rebound", "Green REBOUND")
    pbp = (
        pl.DataFrame(rows)
        .with_columns(feat.clock_seconds_expr().alias("left"))
        .with_columns(feat.elapsed_expr().alias("t"))
        .drop("left")
    )
    st = pl.DataFrame(
        {
            "game_id": [g, g],
            "team_id": [H, H],
            "period": [1, 1],
            "start_clock": [720.0, 400.0],
            "end_clock": [400.0, 0.0],
            "players": [[1, 21, 3, 4, 5], [1, 22, 3, 4, 5]],
        },
        schema_overrides={"players": pl.Array(pl.Int32, 5)},
    )
    return games, pbp, st


def test_assist_repair_resolves_initials_by_who_is_on_court() -> None:
    games, pbp, st = _roster_game()
    rows = feat.stint_rows(st)
    before = feat.stat_events(pbp).filter(pl.col("stat") == "ast")
    after = feat.stat_events(pbp, rows).filter(pl.col("stat") == "ast").sort("t")
    assert (
        before.height == 0
    )  # 'D. Green' / 'J. Green' never matched the surname-only names, and 'Green' is ambiguous
    assert after["player_id"].to_list() == [21, 22]  # the third assist (no such player) is dropped
    assert feat.ast_unresolved(pbp, after) == (3, 2)


def test_binding_p_and_boot_draws() -> None:
    rng = np.random.default_rng(0)
    games = np.repeat(np.arange(100), 5)
    codes = {"game": games, "player_season": rng.integers(0, 20, len(games)), "week": games // 7}
    gain = rng.normal(-0.5, 0.2, len(games))
    assert rule.binding_p(gain, codes, n_boot=200) < 0.05
    assert rule.binding_p(-gain, codes, n_boot=200) > 0.95
    point, draws = rule.boot_draws(gain, games, 0, 200)
    assert len(draws) == 200 and point == pytest.approx(float(gain.mean()))


def test_synthetic_finals_are_coherent_and_dryrun_guards_the_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    games, pbp, st, _ = _synthetic()
    state = feat.state_frame(2022, feat.data_root(), True, games, pbp, st)
    fin = ev.synthetic_finals(feat.data_root(), (2022,), {2022: state})
    last = state.sort("e").group_by("game_id", "player_id", maintain_order=True).last()
    j = fin.join(last, on=["game_id", "player_id"], suffix="_cur")
    for stat in rule.STATS:
        assert (j[stat] >= j[f"{stat}_cur"]).all()
    assert fin.equals(ev.synthetic_finals(feat.data_root(), (2022,), {2022: state}))  # seeded

    real = ev.read_finals

    def stub(root, finals_fn, sha, **kw):  # type: ignore[no-untyped-def]
        ev.read_finals(root, (2023,))  # any real-outcome read inside a dryrun must raise
        return {}

    monkeypatch.setattr(ev, "run_pipeline", stub)
    with pytest.raises(RuntimeError, match="outcome reader"):
        ev.dryrun(feat.data_root(), tmp_path / "d.json")
    assert ev.read_finals is real  # restored even on failure


def test_recency_and_evaluate_wiring() -> None:
    d = __import__("datetime").date
    finals = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(8)],
            "player_id": [1] * 8,
            "team_id": [1] * 8,
            "game_date": [d(2023, 1, 1 + i) for i in range(8)],
            "minutes": [30.0] * 8,
            "pts": [10, 20, 30, 10, 20, 30, 10, 20],
            "reb": [1] * 8,
            "ast": [1] * 8,
            "fg3m": [1] * 8,
        }
    )
    r = ev.recency(finals).sort("game_id")
    assert r["n_prior"].to_list() == list(range(8))
    assert (
        r["m_pts"][3] == pytest.approx(20.0) and r["m_pts"][0] is None
    )  # strictly earlier games only
    rng = np.random.default_rng(0)
    n = 4000
    test = pl.DataFrame(
        {
            "game_id": [f"g{i % 400}" for i in range(n)],
            "player_id": rng.integers(0, 80, n),
            "game_date": [d(2024, 1, 1 + (i % 400) % 28) for i in range(n)],
            "e": rng.choice(rule.CHECKPOINTS, n),
            "margin": rng.integers(-30, 30, n),
            "y": rng.integers(0, 2, n).astype(float),
            "b1_a0": rng.uniform(0.05, 0.95, n),
        }
    )
    probs = {k: rng.uniform(0.05, 0.95, n) for k in ("b0", "b1", "m")}
    res = ev.evaluate(test, probs, "pts")
    assert res["better_b1"] in ("b1", "b1_a0") and set(res["slices"]) == {"blowout_q3q4", "last6"}
    assert res["vs_b0"]["n_rows"] == n and 0 <= res["p_vs_b0"] <= 1 and res["cells"]
