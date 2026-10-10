"""F11 lineup context (docs/prereg/F11_LINEUP_CONTEXT.md): builder, planted tests, gates."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl

from nba.props.context_residual import ContextResidualConfig, stat_feature_names
from research.eval import f11_lineup_context as f11

N_GAMES = 14
T1, T2 = 1, 2
P1 = list(range(101, 109))
P2 = list(range(201, 209))
DNP1, DNP2 = 109, 209
DOC = Path(__file__).resolve().parents[3] / "docs/prereg/F11_LINEUP_CONTEXT.md"


def make_data() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Two teams, 14 days of games, eight rotation players each plus one DNP row each."""
    base = dt.date(2023, 11, 1)
    games, pgs_rows, st_rows, po_rows = [], [], [], []
    for g in range(N_GAMES):
        gid = f"002230{g:04d}"
        games.append(
            {
                "game_id": gid,
                "game_date": base + dt.timedelta(days=g),
                "season": 2023,
                "home_team": T1,
                "away_team": T2,
                "home_pts": 100,
                "away_pts": 99,
            }
        )
        for tid, roster, dnp in ((T1, P1, DNP1), (T2, P2, DNP2)):
            secs = dict.fromkeys(roster, 0.0)
            clock = 720.0
            for i in range(4):
                # rotation: skip three players that change every stint
                skip = {roster[(g + 2 * i + j) % 8] for j in range(3)}
                lineup = [p for p in roster if p not in skip][:5]
                end = clock - 600.0 if i < 3 else clock - 600.0
                st_rows.append(
                    {
                        "game_id": gid,
                        "team_id": tid,
                        "period": 1 + i // 2,
                        "start_clock": clock if i % 2 == 0 else 720.0,
                        "end_clock": end if i % 2 == 0 else 120.0,
                        "players": lineup,
                    }
                )
                dur = 600.0
                for p in lineup:
                    secs[p] += dur
                for _ in range(5):
                    po_rows.append(
                        {
                            "game_id": gid,
                            "off_team": tid,
                            "off_players": lineup,
                            "pts": (i + g) % 3,
                        }
                    )
                clock = clock - 600.0 if i % 2 == 0 else 720.0
            for p in roster:
                pgs_rows.append(
                    {
                        "game_id": gid,
                        "player_id": p,
                        "team_id": tid,
                        "minutes": secs[p] / 60.0 if secs[p] > 0 else None,
                        "pts": p % 7,
                        "ast": p % 3,
                        "fg3m": p % 4,
                        "reb": p % 6,
                        "starter": p == roster[0],
                    }
                )
            pgs_rows.append(
                {
                    "game_id": gid,
                    "player_id": dnp,
                    "team_id": tid,
                    "minutes": None,
                    "pts": 0,
                    "ast": 0,
                    "fg3m": 0,
                    "reb": 0,
                    "starter": False,
                }
            )
    games_df = pl.DataFrame(games).with_columns(pl.col("game_date").cast(pl.Date))
    pgs = pl.DataFrame(
        pgs_rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "team_id": pl.Int64,
            "minutes": pl.Float32,
            "pts": pl.Int64,
            "ast": pl.Int64,
            "fg3m": pl.Int64,
            "reb": pl.Int64,
            "starter": pl.Boolean,
        },
    )
    stints = pl.DataFrame(
        st_rows,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int32,
            "period": pl.Int32,
            "start_clock": pl.Float32,
            "end_clock": pl.Float32,
            "players": pl.Array(pl.Int32, 5),
        },
    )
    poss = pl.DataFrame(
        po_rows,
        schema={
            "game_id": pl.Utf8,
            "off_team": pl.Int32,
            "off_players": pl.Array(pl.Int32, 5),
            "pts": pl.Int64,
        },
    )
    return games_df, pgs, stints, poss


def build(
    flagged: dict[str, set[int]] | None = None,
    data: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    games, pgs, stints, poss = data if data is not None else make_data()
    return f11.build_lineup_context(games, pgs, stints, poss, flagged or {})


def gid(g: int) -> str:
    return f"002230{g:04d}"


# ----------------------------------------------------------------------------- pre-registration


def test_frozen_hash_prefix_and_marker() -> None:
    sha = f11.frozen_sha256(DOC)
    assert sha.startswith(f11.FROZEN_PREFIX)


# ----------------------------------------------------------------------------- aggregation


def test_stint_frames_pair_seconds_symmetric_and_solo_totals() -> None:
    _, _, stints, _ = make_data()
    solo, pair = f11.stint_frames(stints)
    g0 = solo.filter((pl.col("game_id") == gid(0)) & (pl.col("team_id") == T1))
    # four stints of 600 s each (alternating clock reset): total floor seconds = 5 x 2400
    assert abs(float(g0["sec"].sum()) - 5 * 2400.0) < 1e-6
    ab = pair.filter((pl.col("game_id") == gid(0)) & (pl.col("team_id") == T1))
    j = ab.join(ab.rename({"p": "q", "q": "p", "sec": "sec2"}), on=["game_id", "team_id", "p", "q"])
    assert np.allclose(j["sec"].to_numpy(), j["sec2"].to_numpy())
    assert ab.filter(pl.col("p") == pl.col("q")).height == 0
    # each player's pair seconds with the other four sum to 4 x his floor seconds
    tot = (
        ab.group_by("p")
        .agg(pl.col("sec").sum().alias("s"))
        .join(g0.rename({"p": "p", "sec": "solo"}), on="p")
    )
    assert np.allclose(tot["s"].to_numpy(), 4.0 * tot["solo"].to_numpy())


def test_aggregate_possessions_counts() -> None:
    _, _, _, poss = make_data()
    on, tot = f11.aggregate_possessions(poss)
    row = tot.filter((pl.col("game_id") == gid(0)) & (pl.col("team_id") == T1))
    assert row["n_tot"][0] == 20
    # every on-court count is at most the team total
    j = on.join(tot, on="game_id")
    assert (j["n_on"] <= j["n_tot"]).all()


# ----------------------------------------------------------------------------- traits


def test_shrunk_traits_formula_and_guard() -> None:
    z = np.zeros(1)
    thr3, lift, rpm = f11.shrunk_traits(
        smin=np.array([600.0]),
        s3=np.array([30.0]),
        sreb=np.array([120.0]),
        son=np.array([3000.0]),
        spon=np.array([3300.0]),
        soff=np.array([1000.0]),
        spoff=np.array([1000.0]),
        lg3=np.array([0.04]),
        lgr=np.array([0.2]),
    )
    assert abs(thr3[0] - 36.0 * (30.0 + 0.04 * 300.0) / 900.0) < 1e-12
    assert abs(rpm[0] - (120.0 + 0.2 * 300.0) / 900.0) < 1e-12
    assert abs(lift[0] - (1.1 - 1.0) * 3000.0 / 4500.0) < 1e-12
    # no history -> league rate and zero lift; too few off-court possessions -> zero lift
    t0, l0, r0 = f11.shrunk_traits(z, z, z, z, z, z, z, np.array([0.05]), np.array([0.25]))
    assert abs(t0[0] - 36 * 0.05) < 1e-12 and abs(r0[0] - 0.25) < 1e-12 and l0[0] == 0.0
    _, l1, _ = f11.shrunk_traits(
        z, z, z, np.array([500.0]), np.array([600.0]), np.array([10.0]), np.array([30.0]), z, z
    )
    assert l1[0] == 0.0


def test_trait_history_is_strictly_asof() -> None:
    games, pgs, _, poss = make_data()
    hist, league = f11.trait_history(games, pgs, poss)
    first = hist.filter(pl.col("player_id") == 101).sort("game_date")
    assert first.height == N_GAMES
    # post-state after game 0 holds only game 0: minutes = the player's game-0 minutes
    m0 = pgs.filter((pl.col("player_id") == 101) & (pl.col("game_id") == gid(0)))["minutes"][0]
    assert abs(first["smin"][0] - m0) < 1e-6
    assert league["cum_min"].is_sorted()


# ----------------------------------------------------------------------------- builder


def test_builder_rows_cover_roster_including_dnp_and_sum_c() -> None:
    a1, orc = build()
    games, pgs, _, _ = make_data()
    assert a1.height == pgs.height == orc.height
    late = a1.filter(pl.col("game_id") == gid(N_GAMES - 1))
    assert late.height == 18
    ok = late.filter(pl.col("lc_navail") >= 4)
    assert ok.height > 0
    assert (ok["lc_csum"] - 4.0).abs().max() < 1e-9  # type: ignore[operator]
    dnp = late.filter(pl.col("player_id") == DNP1)
    assert dnp["lc_cover"][0] == 0.0 and dnp["lc_csum"][0] > 0  # fallback min10 weights
    reg = late.filter(pl.col("player_id") == 101)
    assert reg["lc_cover"][0] == 1.0
    # nothing to know on the very first day
    first = a1.filter(pl.col("game_id") == gid(0))
    assert first["lc_thr3"].is_nan().all()


def test_out_teammate_gets_zero_weight_and_mass_is_redistributed() -> None:
    a1, _ = build()
    target = gid(N_GAMES - 1)
    out, _ = build({target: {102}})
    r0 = a1.filter((pl.col("game_id") == target) & (pl.col("player_id") == 101))
    r1 = out.filter((pl.col("game_id") == target) & (pl.col("player_id") == 101))
    assert abs(r1["lc_csum"][0] - 4.0) < 1e-9
    assert r1["lc_navail"][0] == r0["lc_navail"][0] - 1
    assert r1["lc_thr3"][0] != r0["lc_thr3"][0]
    # other games are untouched
    assert f11.lc_equal(a1, out, {gid(g) for g in range(N_GAMES - 1)})


def test_top3_zero_when_self_or_out() -> None:
    a1, _ = build()
    target = gid(N_GAMES - 1)
    games, pgs, stints, poss = make_data()
    hist, _ = f11.trait_history(games, pgs, poss)
    last = hist.filter(pl.col("game_date") < dt.date(2023, 11, 1) + dt.timedelta(days=N_GAMES - 1))
    best = (
        last.sort("game_date")
        .group_by("player_id")
        .last()
        .filter(pl.col("player_id").is_in(P1))
        .sort(["fg3pg", "player_id"], descending=[True, False])
    )
    qstar = int(best["player_id"][0])
    row = a1.filter((pl.col("game_id") == target) & (pl.col("player_id") == qstar))
    assert row["lc_top3"][0] == 0.0
    out, _ = build({target: {qstar}})
    other = next(p for p in P1 if p != qstar)
    assert (
        out.filter((pl.col("game_id") == target) & (pl.col("player_id") == other))["lc_top3"][0]
        == 0.0
    )
    inn = a1.filter((pl.col("game_id") == target) & (pl.col("player_id") == other))
    assert inn["lc_top3"][0] > 0.0


def test_planted_same_game_leaves_game_and_earlier_unchanged() -> None:
    data = make_data()
    games, pgs, stints, poss = data
    a1, _ = build(data=data)
    day = dt.date(2023, 11, 1) + dt.timedelta(days=9)
    target = {gid(9)}
    pg2, st2, po2 = f11.plant_same_game(pgs, stints, poss, target)
    # the plant really changed the inputs
    assert not pg2["minutes"].equals(pgs["minutes"])
    assert st2.height > stints.height
    p3, _ = f11.build_lineup_context(games, pg2, st2, po2, {})
    upto = set(games.filter(pl.col("game_date") <= day)["game_id"].to_list())
    assert f11.lc_equal(a1, p3, upto)
    later = set(games["game_id"].to_list()) - upto
    assert not f11.lc_equal(a1, p3, later)  # the plant is not inert: it moves later windows


def test_planted_future_ignored() -> None:
    data = make_data()
    games, pgs, stints, poss = data
    a1, _ = build(data=data)
    cutoff = dt.date(2023, 11, 1) + dt.timedelta(days=7)
    pg4, st4, po4 = f11.plant_future(games, pgs, stints, poss, cutoff)
    p4, _ = f11.build_lineup_context(games, pg4, st4, po4, {})
    before = set(games.filter(pl.col("game_date") <= cutoff)["game_id"].to_list())
    assert f11.lc_equal(a1, p4, before)
    assert not f11.lc_equal(a1, p4, set(games["game_id"].to_list()) - before)


def test_oracle_uses_tonights_stints_and_is_not_a1() -> None:
    a1, orc = build()
    games, pgs, stints, poss = make_data()
    g = N_GAMES - 1
    solo, pair = f11.stint_frames(stints.filter(pl.col("game_id") == gid(g)))
    row = pair.filter((pl.col("team_id") == T1) & (pl.col("p") == 101))
    # a player who did not play tonight has no oracle value
    o_dnp = orc.filter((pl.col("game_id") == gid(g)) & (pl.col("player_id") == DNP1))
    assert o_dnp["lc_thr3"].is_nan().all()
    o = orc.filter((pl.col("game_id") == gid(g)) & (pl.col("player_id") == 101))
    assert row.height > 0 and not np.isnan(o["lc_thr3"][0])
    assert o["lc_cover"][0] == 1.0
    a = a1.filter((pl.col("game_id") == gid(g)) & (pl.col("player_id") == 101))
    assert a["lc_thr3"][0] != o["lc_thr3"][0]


def test_post_tip_report_rows_do_not_flag_a_player() -> None:
    from nba.features.injury_report import ReportTriggerConfig, latest_pretip_flagged

    tip = dt.datetime(2023, 11, 5, 19, 30)
    rows = pl.DataFrame(
        {
            "game_id": ["g", "g", "g"],
            "player_id": [1, 2, 3],
            "status": ["out", "out", "out"],
            "as_of": [
                dt.datetime(2023, 11, 5, 10, 0),
                tip + dt.timedelta(hours=2),
                tip - dt.timedelta(hours=3),
            ],
            "game_date": [dt.date(2023, 11, 5)] * 3,
            "tip_et": [tip] * 3,
        }
    )
    cfg = ReportTriggerConfig(tip_source="real")
    flagged, used = latest_pretip_flagged(rows, cfg)
    # only the latest usable snapshot counts (player 3, 3h before tip); player 2 is post-tip
    assert flagged["g"] == {3}
    assert used["g"] == tip - dt.timedelta(hours=3)


# ----------------------------------------------------------------------------- arms plumbing


def test_arm_names_are_explicit_lists() -> None:
    base = stat_feature_names("pts")
    assert f11.arm_names("pts", "A0") == base
    assert f11.arm_names("pts", "A1") == [*base, *f11.LC_COLS]
    assert f11.arm_names("pts", "A4") == [*base, *f11.LC_NO_LIFT]
    assert "lc_lift" not in f11.arm_names("reb", "A4")
    assert f11.arm_names("reb", "A1", drop_lift=True) == [
        *stat_feature_names("reb"),
        *f11.LC_NO_LIFT,
    ]


def test_placebo_permutes_within_team_season_and_keeps_marginals() -> None:
    a1, _ = build()
    feats = a1.filter(pl.col("lc_navail") >= 4).with_columns(pl.lit(2023).alias("season"))
    pl_f = f11.placebo_frame(feats)
    assert pl_f.height == feats.height
    for t in (T1, T2):
        x = feats.filter(pl.col("team_id") == t).sort(["game_id", "player_id"])
        y = pl_f.filter(pl.col("team_id") == t).sort(["game_id", "player_id"])
        for c in f11.LC_COLS:
            assert np.allclose(np.sort(x[c].to_numpy()), np.sort(y[c].to_numpy()))
    changed = (feats["lc_thr3"].to_numpy() != pl_f["lc_thr3"].to_numpy()).mean()
    assert changed > 0.5
    # the five columns move together (vector permutation)
    key_in = {tuple(np.round(r, 9)) for r in feats.select(f11.LC_COLS).to_numpy()}
    key_out = {tuple(np.round(r, 9)) for r in pl_f.select(f11.LC_COLS).to_numpy()}
    assert key_out <= key_in
    # deterministic
    assert f11.placebo_frame(feats).equals(pl_f)


def test_lc_equal_treats_nan_as_equal_and_detects_change() -> None:
    a1, _ = build()
    assert f11.lc_equal(a1, a1)
    b = a1.with_columns(
        pl.when(pl.col("game_id") == gid(5))
        .then(pl.col("lc_thr3") + 1e-9)
        .otherwise(pl.col("lc_thr3"))
        .alias("lc_thr3")
    )
    assert not f11.lc_equal(a1, b)
    assert f11.lc_equal(a1, b, {gid(4)})


def test_check_6_and_check_2_helpers() -> None:
    a1, _ = build()
    games, pgs, _, _ = make_data()
    c6 = f11.check_6_sum_c(a1)
    assert c6["n_rows_ge4"] > 0 and c6["max_abs_dev"] < 1e-9
    c2 = f11.check_2_missingness(a1, pgs, games)
    assert "lc_thr3|minutes" in c2["gaps_pp"]
    assert {a["bucket"] for a in c2["audit"]} >= {"DNP", ">20"}


def test_check_1_coverage() -> None:
    games, _, stints, poss = make_data()
    c1 = f11.check_1_coverage(games, stints, poss)
    assert c1["stints_cov"] == 1.0 and c1["poss_cov"] == 1.0
    half = stints.filter(pl.col("game_id") != gid(0)).filter(
        ~((pl.col("game_id") == gid(1)) & (pl.col("team_id") == T2))
    )
    c1b = f11.check_1_coverage(games, half, poss)
    assert abs(c1b["stints_cov"] - (N_GAMES - 2) / N_GAMES) < 1e-12


# ----------------------------------------------------------------------------- gates


def _row(stat: str, season: int, arm: str, d: float | None, **kw: float) -> dict[str, object]:
    base: dict[str, object] = {
        "stat": stat,
        "season": season,
        "arm": arm,
        "n_rows": 1000,
        "n_games": 100,
        "bias": 0.0,
        "cov80": 0.80,
        "pit_q10": 0.10,
        "dcrps": d,
        "ci_lo": None if d is None else d - 0.004,
        "ci_hi": None if d is None else d + 0.004,
        "p_bh": 0.01,
    }
    base.update(kw)
    return base


def _tables(a1: float, a2: float, a3: float, a4: float, slice_d: float = 0.0) -> tuple:  # type: ignore[type-arg]
    rows = [
        _row("pts", 2023, "A0", None),
        _row("pts", 2023, "A1", a1),
        _row("pts", 2023, "A2", a2),
        _row("pts", 2023, "A3", a3),
        _row("pts", 2023, "A4", a4),
    ]
    slices = [{"stat": "pts", "season": 2023, "slice": "bench", "n": 500, "dcrps": slice_d}]
    attacks = [
        {
            "stat": "pts",
            "season": 2023,
            "A1_minus_A2": {"point": a1 - a2, "lo": 0, "hi": 0},
            "A1_minus_A3": {"point": a1 - a3, "lo": 0, "hi": 0},
            "A1_minus_A4": {"point": a1 - a4, "lo": 0, "hi": 0},
        }
    ]
    return rows, slices, attacks


def test_gates_pass_and_each_failure_mode() -> None:
    rows, slices, attacks = _tables(-0.010, -0.001, -0.015, -0.008)
    g = f11.evaluate_gates(rows, slices, attacks, "pts", 2023, True)
    assert g["all"] and not g["audit_leak_flag_A1_not_worse_than_A3"]
    # the placebo carries the gain -> attack fails
    rows, slices, attacks = _tables(-0.010, -0.009, -0.015, -0.008)
    assert not f11.evaluate_gates(rows, slices, attacks, "pts", 2023, True)["gate3"]
    # ablation loses more than half the gain
    rows, slices, attacks = _tables(-0.010, -0.001, -0.015, -0.003)
    assert not f11.evaluate_gates(rows, slices, attacks, "pts", 2023, True)["gate3"]
    # A1 at least as good as the oracle -> presumed leak flag, gate fails
    rows, slices, attacks = _tables(-0.010, -0.001, -0.008, -0.008)
    g = f11.evaluate_gates(rows, slices, attacks, "pts", 2023, True)
    assert g["audit_leak_flag_A1_not_worse_than_A3"] and not g["all"]
    # below the practical floor
    rows, slices, attacks = _tables(-0.004, 0.0, -0.015, -0.003)
    assert not f11.evaluate_gates(rows, slices, attacks, "pts", 2023, True)["gate1"]
    # a big slice that is worse by more than +0.01
    rows, slices, attacks = _tables(-0.010, -0.001, -0.015, -0.008, slice_d=0.02)
    g = f11.evaluate_gates(rows, slices, attacks, "pts", 2023, True)
    assert not g["gate2"] and "bench" in g["slices_worse"]
    # failed planted tests
    rows, slices, attacks = _tables(-0.010, -0.001, -0.015, -0.008)
    assert not f11.evaluate_gates(rows, slices, attacks, "pts", 2023, False)["gate3"]


def test_tercile_masks_partition_rows_per_season() -> None:
    d = pl.DataFrame({"season": [2023] * 9 + [2024] * 9, "v": list(map(float, range(9))) * 2})
    m = f11.tercile_masks(d, "v")
    total = m["T1"].astype(int) + m["T2"].astype(int) + m["T3"].astype(int)
    assert (total == 1).all()
    assert m["T1"].sum() == 6 and m["T3"].sum() == 6


def test_season_2025_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError):
        f11.collect_arm(pl.DataFrame(), "pts", ContextResidualConfig(), [], (2025,))
