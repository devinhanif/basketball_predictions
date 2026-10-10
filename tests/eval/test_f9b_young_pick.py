"""F9b young-pick study (docs/prereg/F9b_YOUNG_PICK_PRIOR.md): columns, as-of, placebo, gates."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import polars as pl
import pytest
from tests.props.test_context_residual import ELO, _league

from nba.eval import f9b_young_pick as f9b
from nba.eval.context_residual_eval import TEST_SEASONS
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    stat_feature_names,
    young_pick_features,
)


def _static_x(static: pl.DataFrame) -> pl.DataFrame:
    """Player attributes cycling through every draft-status case."""
    ids = static["player_id"].to_list()
    n = len(ids)
    rows: list[tuple[Any, ...]] = []
    for i, pid in enumerate(ids):
        k = i % 6
        if k == 0:  # top-10 pick, FROM_YEAR known
            rows.append((pid, 2022, 3 + i % 6, 2022, "drafted"))
        elif k == 1:  # outside top 10, NULL status (not re-pulled), FROM_YEAR null -> draft_year
            rows.append((pid, 2019, 25, None, None))
        elif k == 2:  # undrafted with FROM_YEAR
            rows.append((pid, None, None, 2022, "undrafted"))
        elif k == 3:  # drafted, no pick recorded
            rows.append((pid, 2018, None, 2018, "drafted"))
        elif k == 4:  # nothing known
            rows.append((pid, None, None, None, None))
        else:  # undrafted, FROM_YEAR missing
            rows.append((pid, None, None, None, "undrafted"))
    assert n > 0
    return pl.DataFrame(
        rows,
        schema={
            "player_id": pl.Int64,
            "draft_year": pl.Int64,
            "draft_pick": pl.Int64,
            "first_season": pl.Int64,
            "draft_status": pl.Utf8,
        },
        orient="row",
    ).with_columns(pl.col("player_id").cast(static.schema["player_id"]))


def _feats(
    gdf: pl.DataFrame, pdf: pl.DataFrame, static: pl.DataFrame, sx: pl.DataFrame
) -> pl.DataFrame:
    base = build_features(gdf, pdf, static, {}, ELO)
    return f9b.add_yp_columns(base, gdf, pdf, sx)


def test_frozen_hash_prefix() -> None:
    assert f9b.frozen_sha256().startswith(f9b.FROZEN_PREFIX)


def test_static_status_pick_ord_and_first_season_fallbacks() -> None:
    gdf, pdf, static = _league(n_days=60)
    sx = _static_x(static)
    first = pdf.join(gdf.select("game_id", "season"), on="game_id").filter(pl.col("minutes") > 0)
    fl = first.group_by("player_id").agg(pl.col("season").min().alias("first_loaded"))
    ps = f9b.player_yp_static(sx, fl).sort("player_id").join(fl, on="player_id", how="left")
    i = {
        k: ps.filter(pl.col("player_id") == static["player_id"][k]).row(0, named=True)
        for k in range(6)
    }
    assert i[0]["yp_status"] == "drafted" and 3 <= i[0]["yp_pick_ord"] <= 8
    assert i[0]["first_season_used"] == 2022 and i[0]["fs_fallback"] == 0
    assert i[1]["yp_pick_ord"] == 25 and i[1]["first_season_used"] == 2019  # draft_year fallback
    assert i[1]["fs_fallback"] == 1 and i[1]["fs_truncated"] == 0
    assert i[2]["yp_status"] == "undrafted" and i[2]["yp_pick_ord"] == 61
    assert i[2]["yp_undrafted"] == 1.0
    assert i[3]["yp_status"] == "drafted" and i[3]["yp_pick_ord"] is None  # unknown pick
    assert i[3]["yp_undrafted"] == 0.0
    assert i[4]["yp_status"] == "unknown" and i[4]["yp_undrafted"] is None
    assert i[4]["fs_truncated"] == 1 and i[4]["first_season_used"] == i[4]["first_loaded"]
    assert i[5]["yp_status"] == "undrafted" and i[5]["fs_truncated"] == 1


def test_weights_decay_and_cutoffs() -> None:
    w = f9b.yp_weight(
        np.array([1, 2, 3, 4, 5, 1, 2, 1]), np.array([3, 10, 1, 7, 2, 11, np.nan, 61.0])
    )
    assert w.tolist() == [1.0, 0.75, 0.5, 0.25, 0.0, 0.0, 0.0, 0.0]


def test_yp_seasons_is_static_and_columns_are_exactly_the_eight() -> None:
    gdf, pdf, static = _league()
    sx = _static_x(static)
    f = _feats(gdf, pdf, static, sx)
    assert all(c in f.columns for c in f9b.YP_NEW)
    ps = f9b.player_yp_static(
        sx,
        pdf.join(gdf.select("game_id", "season"), on="game_id")
        .filter(pl.col("minutes") > 0)
        .group_by("player_id")
        .agg(pl.col("season").min().alias("first_loaded")),
    )
    chk = f.join(ps.select("player_id", "first_season_used"), on="player_id")
    assert (
        chk["yp_seasons"] == chk["season"].cast(pl.Float64) - chk["first_season_used"] + 1.0
    ).all()
    # undrafted never carries pedigree weight; a rookie-year top-10 pick has the full weight
    assert (f.filter(pl.col("yp_pick_ord") == 61)["yp_w"] == 0).all()
    top = f.filter((pl.col("yp_pick_ord") <= 10) & (pl.col("yp_seasons") == 1))
    assert top.height > 0 and (top["yp_w"] == 1.0).all()
    assert np.allclose(f["yp_w_x_role"], f["yp_w"] * f["yp_min_share"], equal_nan=True)


def test_role_columns_match_production_function() -> None:
    """The three role columns equal the (read-only) production helper's, by construction."""
    gdf, pdf, static = _league()
    sx = _static_x(static)
    st = static.join(sx.select("player_id", "draft_pick"), on="player_id")
    base = build_features(gdf, pdf, st, {}, ELO)
    mine = f9b.add_yp_columns(base, gdf, pdf, sx)
    games = gdf.with_columns(pl.col("game_date").cast(pl.Date))
    from nba.props.context_residual import player_states

    played = f9b._played(games, pdf)
    ref = young_pick_features(games, played, st, player_states(played), base)
    j = mine.join(ref, on=["game_id", "player_id"], suffix="_ref")
    for c in ("yp_min_share", "yp_min_trend", "yp_pts_share"):
        assert np.allclose(j[c].to_numpy(), j[f"{c}_ref"].to_numpy(), equal_nan=True), c


def _yp(df: pl.DataFrame) -> pl.DataFrame:
    return df.select("game_id", "player_id", *f9b.YP_ALL).sort(["game_id", "player_id"])


def test_planted_future_game_and_same_game_stats_leave_rows_unchanged() -> None:
    gdf, pdf, static = _league()
    sx = _static_x(static)
    base = _yp(_feats(gdf, pdf, static, sx))
    last = gdf.sort("game_date").tail(1)
    for days, bump in ((1, 0), (400, 1)):  # tonight-ish same season, and a later season
        fid = "9999999"
        fg = last.with_columns(
            pl.lit(fid).alias("game_id"),
            pl.lit(last["game_date"][0] + dt.timedelta(days=days))
            .cast(gdf.schema["game_date"])
            .alias("game_date"),
            (pl.col("season") + bump).cast(gdf.schema["season"]).alias("season"),
            pl.lit(150).cast(pl.Int64).alias("home_pts"),
        )
        fp = pdf.filter(pl.col("game_id") == last["game_id"][0]).with_columns(
            pl.lit(fid).alias("game_id"),
            pl.lit(60).cast(pdf.schema["pts"]).alias("pts"),
            pl.lit(48.0).alias("minutes"),
        )
        newp = fp.head(1).with_columns(
            pl.lit(98765).cast(pdf.schema["player_id"]).alias("player_id")
        )
        g2, p2 = pl.concat([gdf, fg]), pl.concat([pdf, fp, newp])
        got = _yp(_feats(g2, p2, static, sx))
        assert base.equals(got.filter(pl.col("game_id") != fid))
        fake = got.filter(pl.col("game_id") == fid)
        assert fake.height > 0
        # the unseen player has no static row: unknown status, truncated first season
        assert fake.filter(pl.col("player_id") == 98765)["yp_status"][0] == "unknown"
    # tonight's own box score cannot move tonight's rows
    tonight = last["game_id"][0]
    hit = (pl.col("game_id") == tonight) & (pl.col("minutes") > 0)
    mod = pdf.with_columns(
        pl.when(hit).then(99).otherwise(pl.col("pts")).alias("pts"),
        pl.when(hit).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
    )
    g3 = gdf.with_columns(
        pl.when(pl.col("game_id") == tonight)
        .then(200)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    got = _yp(_feats(g3, mod, static, sx))
    a = base.filter(pl.col("game_id") == tonight)
    assert a.height > 0 and a.equals(got.filter(pl.col("game_id") == tonight))


def test_static_fields_do_not_depend_on_loaded_games() -> None:
    """Dropping the first loaded games leaves yp_seasons unchanged when FROM_YEAR is known."""
    gdf, pdf, static = _league()
    sx = _static_x(static)
    full = _feats(gdf, pdf, static, sx)
    cut = gdf.sort("game_date").head(30)["game_id"].to_list()
    f2 = _feats(
        gdf.filter(~pl.col("game_id").is_in(cut)),
        pdf.filter(~pl.col("game_id").is_in(cut)),
        static,
        sx,
    )
    j = full.join(f2, on=["game_id", "player_id"], suffix="_b").join(
        sx.select("player_id", "first_season", "draft_year"), on="player_id"
    )
    known = j.filter(pl.col("first_season").is_not_null() | pl.col("draft_year").is_not_null())
    assert known.height > 0
    assert (known["yp_seasons"] == known["yp_seasons_b"]).all()


def test_placebo_keeps_marginals_role_and_recomputes_weights() -> None:
    gdf, pdf, static = _league()
    sx = _static_x(static)
    f = _feats(gdf, pdf, static, sx)
    p = f9b.placebo_frame(f)
    assert p.height == f.height
    for c in ("yp_seasons", "yp_min_share", "yp_min_trend", "yp_pts_share"):
        assert np.allclose(p[c].to_numpy(), f[c].to_numpy(), equal_nan=True)
    key = ["season", "player_id"]
    a = f.group_by(key).agg(pl.col("yp_pick_ord").first(), pl.col("yp_seasons").first())
    b = p.group_by(key).agg(pl.col("yp_pick_ord").first(), pl.col("yp_seasons").first())
    for season in a["season"].unique().to_list():
        x = a.filter(pl.col("season") == season)
        y = b.filter(pl.col("season") == season)
        xs = sorted(np.nan_to_num(x["yp_pick_ord"].to_numpy(), nan=-1.0))
        ys = sorted(np.nan_to_num(y["yp_pick_ord"].to_numpy(), nan=-1.0))
        assert xs == ys  # permutation across players keeps the season-level multiset
    assert not np.array_equal(
        np.nan_to_num(p["yp_pick_ord"].to_numpy()), np.nan_to_num(f["yp_pick_ord"].to_numpy())
    )
    w = f9b.yp_weight(p["yp_seasons"].to_numpy(), p["yp_pick_ord"].to_numpy())
    assert np.allclose(p["yp_w"].to_numpy(), w)
    assert np.allclose(
        p["yp_w_x_role"].to_numpy(), w * p["yp_min_share"].to_numpy(), equal_nan=True
    )
    assert f9b.placebo_frame(f).equals(p)  # deterministic


def test_arm_names_and_flag_off_default() -> None:
    assert ContextResidualConfig().young_pick == "off"
    base = stat_feature_names("pts")
    assert f9b.arm_names("pts", "A0") == base
    assert f9b.arm_names("pts", "A1") == [*base, *f9b.YP_NEW]
    assert f9b.arm_names("pts", "A2") == f9b.arm_names("pts", "A1")
    a3 = f9b.arm_names("pts", "A3")
    assert a3 == [*base, *f9b.YP_ROLE_ONLY]
    for c in ("yp_pick_ord", "yp_undrafted", "yp_w", "yp_w_x_role"):
        assert c not in a3


def test_refuses_holdout_season_and_smoke_arm_identical_rows() -> None:
    gdf, pdf, static = _league(n_days=300)
    sx = _static_x(static)
    f = _feats(gdf, pdf, static, sx)
    cfg = ContextResidualConfig(n_estimators=20, min_child_samples=30, min_cal_rows=300)
    with pytest.raises(ValueError):
        f9b.collect_arm(f, "pts", cfg, f9b.arm_names("pts", "A0"), test_seasons=(2025,))
    a0 = f9b.collect_arm(f, "pts", cfg, f9b.arm_names("pts", "A0"), test_seasons=(2023,))
    a1 = f9b.collect_arm(f, "pts", cfg, f9b.arm_names("pts", "A1"), test_seasons=(2023,))
    assert (
        a0["meta"].select("game_id", "player_id").equals(a1["meta"].select("game_id", "player_id"))
    )
    assert a0["qi"].shape == a1["qi"].shape and a0["qi"].shape[1] == 199
    # A0 with and without the extra yp columns on the frame is the same model
    blank = f.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in f9b.YP_NEW])
    a0b = f9b.collect_arm(blank, "pts", cfg, f9b.arm_names("pts", "A0"), test_seasons=(2023,))
    assert np.array_equal(a0["qi"], a0b["qi"])


def _fake_tables(
    s_dc: float = -0.05, a2: float = 0.0, a3: float = 0.0, n_s: int = 800
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, atk = [], []
    for stat in PROP_STATS:
        for season in TEST_SEASONS:
            for arm in f9b.ARMS:
                for sl in f9b.REPORT_SLICES:
                    n = n_s if sl == "S" else 1000
                    r: dict[str, Any] = {
                        "stat": stat,
                        "season": season,
                        "arm": arm,
                        "slice": sl,
                        "n_rows": n,
                        "n_games": 400,
                        "crps_int": 3.0,
                        "bias": -0.6 if arm == "A0" else -0.2,
                        "cov80": 0.77 if arm == "A0" else 0.80,
                        "cov_le80": 0.8,
                        "pit_q10": 0.1,
                        "tll_pts10_15": 0.3,
                    }
                    if arm == "A0":
                        r.update(dcrps=None, ci_lo=None, ci_hi=None, p=None, p_bh=None, mde=None)
                    else:
                        d = s_dc if (sl == "S" and arm == "A1") else 0.0
                        if sl == "S" and arm == "A3":
                            d = a3
                        r.update(
                            dcrps=d, ci_lo=d - 0.01, ci_hi=d + 0.01, p=0.001, p_bh=None, mde=0.01
                        )
                        if sl == "all":
                            r.update(ci_lo=-0.001, ci_hi=0.001)
                    rows.append(r)
            atk.append(
                {
                    "stat": stat,
                    "season": season,
                    "A1_minus_A2": {"point": a2 - 0.03, "lo": -0.05, "hi": 0.0},
                    "A1_minus_A3": {"point": -0.03, "lo": -0.05, "hi": 0.0},
                }
            )
    f9b.add_bh(rows)
    return rows, atk


def test_gates_pass_fail_and_underpowered() -> None:
    rows, atk = _fake_tables()
    g = f9b.evaluate_gates(rows, atk, "pts", 2023)
    assert g["gate1"] and g["gate2"] and g["gate3"] and g["gate4"] and g["gate5"] and g["all"]
    # effect too small
    rows2, atk2 = _fake_tables(s_dc=-0.01)
    assert not f9b.evaluate_gates(rows2, atk2, "pts", 2023)["gate1"]
    # underpowered slice cannot pass
    rows3, atk3 = _fake_tables(n_s=500)
    assert not f9b.evaluate_gates(rows3, atk3, "pts", 2023)["all"]
    # overall harm trips gate 4 only
    rows4, atk4 = _fake_tables()
    for r in rows4:
        if r["arm"] == "A1" and r["slice"] == "all":
            r["dcrps"] = 0.002
    g4 = f9b.evaluate_gates(rows4, atk4, "pts", 2023)
    assert g4["gate1"] and not g4["gate4"] and not g4["all"]
    # placebo equally good -> attack gate fails
    rows5, atk5 = _fake_tables(a2=0.03)
    assert not f9b.evaluate_gates(rows5, atk5, "pts", 2023)["gate5"]
    # role-only matches candidate -> role effect, not confirmed
    rows6, atk6 = _fake_tables(a3=-0.05)
    for a in atk6:
        a["A1_minus_A3"]["point"] = 0.0
    g6 = f9b.evaluate_gates(rows6, atk6, "pts", 2023)
    assert g6["role_effect"] and not g6["all"]


def test_table_rows_bootstrap_and_bh_smoke() -> None:
    rng = np.random.default_rng(0)
    n = 400
    gid = np.repeat(np.arange(n // 4), 4)
    base = rng.gamma(3.0, 1.0, n)
    scores: dict[str, dict[str, Any]] = {}
    for k, shift in zip(f9b.ARMS, (0.0, -0.1, 0.0, 0.0), strict=True):
        scores[k] = {
            "crps": base + shift,
            "resid": rng.normal(0, 1, n),
            "pit": rng.random(n),
            "tll": rng.random(n),
        }
    masks = {k: np.ones(n, dtype=bool) for k in f9b.REPORT_SLICES}
    masks["veterans"] = np.zeros(n, dtype=bool)
    rows = f9b.table_rows("pts", 2023, scores, gid, masks, n_boot=200)
    f9b.add_bh(rows)
    s = next(r for r in rows if r["arm"] == "A1" and r["slice"] == "S")
    assert s["dcrps"] == pytest.approx(-0.1) and s["ci_hi"] < 0 and s["p_bh"] is not None
    assert s["mde"] == pytest.approx((s["ci_hi"] - s["ci_lo"]) / 2)
    vet = next(r for r in rows if r["arm"] == "A1" and r["slice"] == "veterans")
    assert vet["n_rows"] == 0 and "dcrps" not in vet


def test_end_to_end_synthetic_and_report(tmp_path: Any) -> None:
    gdf, pdf, static = _league(n_days=300)
    sx = _static_x(static)
    f = _feats(gdf, pdf, static, sx)
    cfg = ContextResidualConfig(n_estimators=20, min_child_samples=30, min_cal_rows=300)
    frames = {"A0": f, "A1": f, "A2": f9b.placebo_frame(f), "A3": f}
    arms = {
        a: f9b.collect_arm(frames[a], "pts", cfg, f9b.arm_names("pts", a), test_seasons=(2023,))
        for a in f9b.ARMS
    }
    rows, atk = f9b.score_stat("pts", arms, seasons=(2023,), n_boot=50)
    f9b.add_bh(rows)
    assert {r["arm"] for r in rows} == set(f9b.ARMS)
    s = next(r for r in rows if r["arm"] == "A1" and r["slice"] == "S")
    assert s["n_rows"] > 0 and s["p_bh"] is not None
    g = f9b.evaluate_gates(rows, atk, "pts", 2023)
    assert set(g) >= {"gate1", "gate2", "gate3", "gate4", "gate5", "role_effect", "all"}
    # the report renders from a results file
    rows2, atk2 = _fake_tables()
    gates = [f9b.evaluate_gates(rows2, atk2, st, se) for st in PROP_STATS for se in TEST_SEASONS]
    res = {
        "frozen_sha256": "884afcdb" + "0" * 56,
        "git_sha": "x",
        "seeds": f9b.SEEDS,
        "max_season_loaded": 2024,
        "checks": [{"check": "c", "value": 1.0, "threshold": ">= 1", "ok": True}],
        "check_1_2": {
            "n_players_ge20": 1,
            "status_counts": {},
            "n_fs_from_first_season": 1,
            "fs_fallback_share": 0.0,
            "fs_truncated_share": 0.0,
            "unknown_rate_by_minutes": {},
            "nan_yp_pick_ord_rate_by_minutes": {},
            "undrafted_share_by_minutes_bucket": {},
        },
        "check_3_counts": {},
        "arms": {
            "table": rows2,
            "attack": atk2,
            "gates": gates,
            "verdicts": f9b.verdicts(gates),
        },
    }
    f9b._write_json(tmp_path / "results.json", res)
    md = f9b.render_report(tmp_path)
    assert "Proposed ledger rows" in md and "| pts | 2023 | A1 | S |" in md
    assert f9b.verdicts(gates)["pts"]["confirmed_2024"]
