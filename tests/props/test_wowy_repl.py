# ruff: noqa: E501
"""WOWY_STRUCT arm STRUCT_REPL feature tests (rule: docs/prereg/WOWY_STRUCT.md): hand-computed on/off, the replacement
baseline, the shrink weight, as-of logic with a planted future, the NULL rule and the identity placebo. Synthetic league only."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.props import repl_delta as RD
from nba.props import wowy_repl as WR
from tests.props.test_repl_delta import pick_outs, synthetic_box, world

# ---------------------------------------------------------------------------------------- hand-computed micro league


def micro_box() -> pl.DataFrame:
    """One team, players 1 (j), 2 (i), 3. Eight games, player 1 absent in games 3-5 (0-based). P = 50 per game, 100 for i
    when j is absent; i scores 10 / 100 poss with j (games 0,1,2,6) and 15 / 100 poss without (3,4,5)."""
    rows = []
    for g in range(8):
        d = dt.date(2022, 11, 1) + dt.timedelta(days=g)
        for pid in (1, 2, 3):
            if pid == 1 and g in (3, 4, 5):
                continue
            wo = g in (3, 4, 5)
            p = 100.0 if (pid == 2 and wo) else 50.0
            rate = {1: 0.30, 2: 0.15 if wo else 0.10, 3: 0.12}[pid]
            rows.append(
                {
                    "game_id": f"G{g}",
                    "team_id": 7,
                    "player_id": pid,
                    "game_date": d,
                    "P": p,
                    "pts": rate * p,
                    "ast": 0.05 * p,
                }
            )
    return pl.DataFrame(rows)


def test_pair_history_hand_computed() -> None:
    b = micro_box()
    out_pairs = pl.DataFrame({"game_id": ["G7"], "team_id": [7], "j": [1]})
    scored = pl.DataFrame({"game_id": ["G7"], "team_id": [7], "player_id": [2]})
    r = WR.pair_history(b, out_pairs, scored)
    assert r.height == 1
    assert r["n_wo"][0] == 3 and r["n_wi"][0] == 4
    # without j: 3 games x 15 pts / 300 poss = 0.15 ; with j (games 0,1,2,6): 4 x 5 pts / 200 poss = 0.10
    assert r["d_pts"][0] == pytest.approx(0.15 - 0.10)
    assert r["d_ast"][0] == pytest.approx(0.05 - 0.05)


def test_pair_history_reads_only_earlier_games() -> None:
    b = micro_box()
    out_pairs = pl.DataFrame({"game_id": ["G4"], "team_id": [7], "j": [1]})
    scored = pl.DataFrame({"game_id": ["G4"], "team_id": [7], "player_id": [2]})
    r = WR.pair_history(b, out_pairs, scored)
    # before G4: games 0,1,2 (with j) and 3 (without j); G4 and later are never read
    assert r["n_wo"][0] == 1 and r["n_wi"][0] == 3
    later = b.with_columns(
        pl.when(pl.col("game_id").is_in(["G4", "G5", "G6", "G7"]))
        .then(pl.col("pts") * 9 + 5)
        .otherwise(pl.col("pts"))
        .alias("pts")
    )
    r2 = WR.pair_history(later, out_pairs, scored)
    assert r2["d_pts"][0] == r["d_pts"][0]


def test_pair_without_one_side_has_no_history() -> None:
    b = micro_box().filter(pl.col("game_id").is_in(["G0", "G1", "G2", "G3"]))
    # j played games 0-2 and missed G3: asking for G3 sees only games WITH j
    out_pairs = pl.DataFrame({"game_id": ["G3"], "team_id": [7], "j": [1]})
    scored = pl.DataFrame({"game_id": ["G3"], "team_id": [7], "player_id": [2]})
    assert WR.pair_history(b, out_pairs, scored).height == 0


def test_traded_player_has_no_pair_history_with_the_new_team() -> None:
    b = micro_box()
    # player 9 played for another team on G0..G2 and joins team 7 only on G6 and G7
    extra = []
    for g in range(8):
        d = dt.date(2022, 11, 1) + dt.timedelta(days=g)
        if g < 6:
            extra.append(
                {
                    "game_id": f"G{g}",
                    "team_id": 8,
                    "player_id": 9,
                    "game_date": d,
                    "P": 50.0,
                    "pts": 5.0,
                    "ast": 1.0,
                }
            )
        else:
            extra.append(
                {
                    "game_id": f"G{g}",
                    "team_id": 7,
                    "player_id": 9,
                    "game_date": d,
                    "P": 50.0,
                    "pts": 5.0,
                    "ast": 1.0,
                }
            )
    bb = pl.concat([b, pl.DataFrame(extra)], how="vertical_relaxed")
    out_pairs = pl.DataFrame({"game_id": ["G7"], "team_id": [7], "j": [9]})
    scored = pl.DataFrame({"game_id": ["G7"], "team_id": [7], "player_id": [2]})
    # j's current stint with team 7 starts at G6: i = 2 has no game WITHOUT him in [G6, G7) -> no history
    assert WR.pair_history(bb, out_pairs, scored).height == 0


def test_assemble_hand_computed_baseline_excess_and_raw() -> None:
    """rb = r x V_j / den x P_exp ; rx = lam (d - E) P_exp ; rr = lam d P_exp ; lam = n_wo / (n_wo + SHRINK_K)."""
    b = micro_box()
    sc = pl.DataFrame(
        {
            "game_id": ["G7"],
            "team_id": [7],
            "player_id": [2],
            "r_pts": [0.12],
            "r_ast": [0.05],
            "P_exp": [60.0],
            "den": [40.0],
        }
    )
    jf = pl.DataFrame({"game_id": ["G7"], "team_id": [7], "j": [1], "V_j": [10.0]})
    keys = sc.select("game_id", "team_id", "player_id")
    out = WR._assemble("", jf, sc, b, keys, pl.col("game_id").is_in(["G7"]))
    lam = 3.0 / (3.0 + WR.SHRINK_K)
    e_pts = 0.12 * 10.0 / 40.0
    d_pts = 0.15 - 0.10
    assert out["rb_pts"][0] == pytest.approx(e_pts * 60.0)
    assert out["rx_pts"][0] == pytest.approx(lam * (d_pts - e_pts) * 60.0)
    assert out["rr_pts"][0] == pytest.approx(lam * d_pts * 60.0)
    assert out["ro_conf"][0] == pytest.approx(lam)
    # rr = rx + lam E P
    assert out["rr_pts"][0] - out["rx_pts"][0] == pytest.approx(lam * e_pts * 60.0)


def test_pair_without_history_keeps_the_baseline_and_zero_excess() -> None:
    b = micro_box()
    sc = pl.DataFrame(
        {
            "game_id": ["G7"],
            "team_id": [7],
            "player_id": [
                3
            ],  # player 3 has history too; use a j that has none: j = 2 never missed a game
            "r_pts": [0.12],
            "r_ast": [0.05],
            "P_exp": [60.0],
            "den": [40.0],
        }
    )
    jf = pl.DataFrame({"game_id": ["G7"], "team_id": [7], "j": [2], "V_j": [10.0]})
    out = WR._assemble(
        "", jf, sc, b, sc.select("game_id", "team_id", "player_id"), pl.col("game_id").is_in(["G7"])
    )
    assert out["rb_pts"][0] == pytest.approx(0.12 * 10.0 / 40.0 * 60.0)
    assert out["rx_pts"][0] == 0.0 and out["rr_pts"][0] == 0.0 and out["ro_conf"][0] == 0.0


def test_unreported_game_is_null_not_zero() -> None:
    b = micro_box()
    sc = pl.DataFrame(
        {
            "game_id": ["G7"],
            "team_id": [7],
            "player_id": [2],
            "r_pts": [0.12],
            "r_ast": [0.05],
            "P_exp": [60.0],
            "den": [40.0],
        }
    )
    jf = pl.DataFrame(
        schema={"game_id": pl.Utf8, "team_id": pl.Int64, "j": pl.Int64, "V_j": pl.Float64}
    )
    out = WR._assemble(
        "", jf, sc, b, sc.select("game_id", "team_id", "player_id"), pl.col("game_id").is_in([])
    )
    assert all(out[c][0] is None for c in WR.COLUMNS)
    rep = WR._assemble(
        "", jf, sc, b, sc.select("game_id", "team_id", "player_id"), pl.col("game_id").is_in(["G7"])
    )
    assert all(rep[c][0] == 0.0 for c in WR.COLUMNS)  # reported, nobody OUT -> explicit zeros


# ---------------------------------------------------------------------------------------- synthetic league, end to end


def build(w: dict[str, Any]) -> pl.DataFrame:
    return WR.build_wowy_repl(
        w["b"],
        w["st"],
        w["out_rows"],
        w["out_all"],
        w["scored"],
        set(w["flagged"]),
        w["repl"],
        w["k"],
        placebo_seed=0,
    )


@pytest.fixture(scope="module")
def base() -> dict[str, Any]:
    box, games = synthetic_box(0)
    outs, rep = pick_outs(box, games)
    gone = pl.DataFrame(
        [(g, p) for g, ps in outs.items() for p in ps],
        schema={"game_id": pl.Utf8, "player_id": pl.Int64},
        orient="row",
    )
    box = box.join(
        gone, on=["game_id", "player_id"], how="anti"
    )  # an OUT player does not play: pair history exists
    w = world(box, games, outs, rep)
    w["box"], w["games"], w["outs"], w["rep"] = box, games, outs, rep
    w["feat"] = build(w)
    return w


def test_one_feature_row_per_scored_row(base: dict[str, Any]) -> None:
    f = base["feat"]
    assert f.height == base["scored"].height
    assert f.select("game_id", "player_id").unique().height == f.height


def test_null_rule(base: dict[str, Any]) -> None:
    f = base["feat"].join(
        base["scored"].select("game_id", "player_id", "season"), on=["game_id", "player_id"]
    )
    unrep = f.filter(~pl.col("game_id").is_in(sorted(base["rep"])) & (pl.col("season") >= 2022))
    assert unrep.height > 0
    for c in (*WR.COLUMNS, *WR.PLACEBO_COLUMNS):
        assert unrep[c].is_null().all(), c
    nobody = f.filter(
        pl.col("game_id").is_in(sorted(base["rep"]))
        & ~pl.col("game_id").is_in(sorted(base["outs"]))
    )
    assert nobody.height > 0
    z = nobody.filter(pl.col("rb_pts").is_not_null())
    assert z.height > 0
    for c in ("rb_pts", "rx_pts", "rr_pts", "rb_ast", "ro_conf"):
        assert (z[c] == 0).all(), c


def test_raw_equals_excess_plus_lambda_baseline_identity(base: dict[str, Any]) -> None:
    """rr - rx = sum_j lam E P over pairs with history: it is 0 when ro_conf is 0 and never exceeds rb x ro_conf-scale."""
    f = base["feat"].filter(pl.col("rb_pts").is_not_null() & pl.col("rx_pts").is_not_null())
    nohist = f.filter(pl.col("ro_conf") == 0)
    assert nohist.height > 0
    assert np.allclose(nohist["rr_pts"].to_numpy(), nohist["rx_pts"].to_numpy())
    hist = f.filter(pl.col("ro_conf") > 0)
    assert hist.height > 50  # the synthetic league has pair history


def test_planted_future_columns_of_earlier_rows_do_not_move(base: dict[str, Any]) -> None:
    cut = dt.date(2022, 3, 1)
    box, games = base["box"], base["games"]
    rng = np.random.default_rng(5)
    idx = np.flatnonzero((box["game_date"] >= cut).to_numpy())
    perm = rng.permutation(idx)
    scr = box.clone()
    for c in ("minutes", "pts", "reb", "ast", "fg3m", "fga", "fta", "tov", "oreb", "starter"):
        v = scr[c].to_numpy().copy()
        v[idx] = v[perm]
        scr = scr.with_columns(pl.Series(c, v))
    w2 = world(scr, games, base["outs"], base["rep"])
    f1, f2 = base["feat"], build(w2)
    sc = base["scored"].select("game_id", "player_id", "game_date")
    j = f1.join(f2, on=["game_id", "player_id"], suffix="_2").join(sc, on=["game_id", "player_id"])
    early = j.filter(pl.col("game_date") < cut)
    assert early.height > 500
    for c in (*WR.COLUMNS, *WR.PLACEBO_COLUMNS):
        a, b2 = early[c].to_numpy().astype(float), early[c + "_2"].to_numpy().astype(float)
        assert np.array_equal(np.isnan(a), np.isnan(b2)), c
        assert np.nanmax(np.abs(a - b2), initial=0.0) <= 1e-9, c
    late = j.filter(pl.col("game_date") >= cut)
    assert (
        np.nanmax(np.abs(late["rx_pts"].to_numpy() - late["rx_pts_2"].to_numpy())) > 0.0
    )  # not inert


def test_placebo_is_deterministic_differs_and_draws_only_from_the_pool(
    base: dict[str, Any],
) -> None:
    a = build(base).sort(["game_id", "player_id"])
    b2 = build(base).sort(["game_id", "player_id"])
    for c in (
        WR.PLACEBO_COLUMNS
    ):  # sums over j are order-dependent at the ulp level: equal to 1e-12, not bitwise
        x, y = a[c].to_numpy().astype(float), b2[c].to_numpy().astype(float)
        assert np.array_equal(np.isnan(x), np.isnan(y)), c
        assert np.nanmax(np.abs(x - y), initial=0.0) <= 1e-12, c
    f = a.filter(pl.col("rb_pts").is_not_null() & (pl.col("rb_pts") != 0))
    assert f.height > 50
    assert not np.allclose(f["pq_rb_pts"].fill_null(0).to_numpy(), f["rb_pts"].to_numpy())


def test_placebo_identities_are_never_out_tonight(base: dict[str, Any]) -> None:
    b = base["b"]
    cum, pace = RD.player_cum(b), RD.team_pace(b)
    pool = RD.rotation_pool(b).join(
        base["out_all"].with_columns(pl.lit(True).alias("_o")),
        on=["game_id", "player_id"],
        how="left",
    )
    pool = pool.filter(pl.col("_o").is_null()).drop("_o")
    gm = b.select("game_id", "season", "game_date").unique("game_id")
    pk = RD._rates_at(
        pool.join(gm, on="game_id", how="left"), cum, base["st"], pace, base["repl"], base["k"]
    )
    o = RD._rates_at(
        base["out_rows"]
        .drop("min10")
        .join(gm.select("game_id", "season"), on="game_id", how="left"),
        cum,
        base["st"],
        pace,
        base["repl"],
        base["k"],
    )
    pq = WR.placebo_out(o, pk, 0)
    assert pq.height > 20
    out_pairs = {(g, p) for g, p in base["out_all"].iter_rows()}
    assert not any((g, j) in out_pairs for g, j in pq.select("game_id", "j").iter_rows())
    pq2 = WR.placebo_out(o, pk, 1)
    assert not pq.sort(["game_id", "team_id", "j"]).equals(pq2.sort(["game_id", "team_id", "j"]))


def test_baseline_scales_with_vacated_usage_above_replacement(base: dict[str, Any]) -> None:
    """With nothing OUT rb = 0; with OUT players rb has the sign of (u_j - u_repl): positive for the bulk of rotation players."""
    f = base["feat"].join(
        base["scored"].select("game_id", "player_id", "season"), on=["game_id", "player_id"]
    )
    f = f.filter(pl.col("season") >= 2022).drop_nulls("rb_pts")
    z, nz = f.filter(pl.col("ro_conf") >= 0), f.filter(pl.col("rb_pts") != 0)
    assert z.height > nz.height > 100
    assert float(nz["rb_pts"].median()) > 0.0  # type: ignore[arg-type]
