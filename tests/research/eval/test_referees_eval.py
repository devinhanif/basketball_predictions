# ruff: noqa: E501
"""docs/REFEREES.md: the tests the frozen text requires (before data) plus pipeline guards.

Required: planted-future crew rows, same-day exclusion, official_id NULL handling, flag-off
byte-identity of production features and predictions, K -> infinity gives the league mean, newness
gives the league mean, permutation control keeps column marginals.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.eval.lower_tail_eval import arm_scores, attach_realised, collect_stat, production_grid
from nba.features.injury_report import ReportTriggerConfig
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    stat_feature_names,
)
from research.eval import referees_eval as rf
from tests.props.test_context_residual import ELO, _league

# --------------------------------------------------------------------------- helpers


def _gq(rows: list[tuple[str, dt.date, float, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema=["game_id", "game_date", "pf100_g", "fta100_g", "pace_g", "home_margin"],
        orient="row",
    ).with_columns(pl.lit(2022).alias("season"))


def _off(rows: list[tuple[str, int | None]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema={"game_id": pl.Utf8, "official_id": pl.Int64}, orient="row")


D = dt.date


def _three_game_fixture() -> tuple[pl.DataFrame, pl.DataFrame]:
    gq = _gq(
        [
            ("g1", D(2022, 10, 18), 40.0, 50.0, 100.0, 5.0),
            ("g2", D(2022, 10, 19), 44.0, 54.0, 104.0, -3.0),
            ("g3", D(2022, 10, 20), 48.0, 58.0, 108.0, 9.0),
        ]
    )
    off = _off(
        [
            ("g1", 1),
            ("g1", 2),
            ("g1", 3),
            ("g2", 1),
            ("g2", 2),
            ("g2", 4),
            ("g3", 1),
            ("g3", 4),
            ("g3", 5),
        ]
    )
    return gq, off


# --------------------------------------------------------------------------- frozen text


def test_frozen_hash_matches_constant_and_ledger() -> None:
    assert rf.frozen_sha256() == rf.FROZEN_SHA
    assert rf.ledger_recorded_sha() == rf.FROZEN_SHA


def test_frozen_sha_ignores_results_section(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text("rule\n" + rf.MARKER + "\nresults A\n")
    h = rf.frozen_sha256(p)
    p.write_text("rule\n" + rf.MARKER + "\nresults completely different\n")
    assert rf.frozen_sha256(p) == h
    p.write_text("edited rule\n" + rf.MARKER + "\nresults A\n")
    assert rf.frozen_sha256(p) != h


def test_frozen_constants_match_the_document() -> None:
    txt = rf.DOC.read_text()
    assert "K = 30 games" in txt and rf.K_SHRINK == 30.0
    for c in rf.CREW_COLS:
        assert c in txt
    assert rf.REPRO_PTS == {2023: 3.1528, 2024: 3.1884}
    assert rf.PROP_FLOOR == -0.005 and rf.TOTAL_FLOOR == -0.05


# --------------------------------------------------------------------------- crew features


def test_first_game_is_warmup_nan_and_second_uses_only_the_first() -> None:
    gq, off = _three_game_fixture()
    c = rf.crew_features(gq, off).sort("game_id")
    assert c["crew_pf100"][0] is None  # opening day: league mean undefined
    # g2: league mean = g1 values; officials 1,2 worked g1 (n=1), official 4 is new -> league mean
    k = rf.K_SHRINK
    lg = 40.0
    x12 = (1 * 40.0 + k * lg) / (1 + k)  # == 40.0 (mean_o == league here)
    assert c["crew_pf100"][1] == pytest.approx((x12 + x12 + lg) / 3)
    assert c["crew_pace"][1] == pytest.approx(100.0)


def test_shrinkage_formula_by_hand() -> None:
    gq, off = _three_game_fixture()
    c = rf.crew_features(gq, off).sort("game_id")
    k = rf.K_SHRINK
    # g3 (Oct 20): games before = g1, g2. league pf100 mean = 42. Official 1 worked both (mean 42),
    # official 4 worked g2 only (44), official 5 is new.
    lg = 42.0
    x1 = (2 * 42.0 + k * lg) / (2 + k)
    x4 = (1 * 44.0 + k * lg) / (1 + k)
    assert c["crew_pf100"][2] == pytest.approx((x1 + x4 + lg) / 3)


def test_planted_future_does_not_move_earlier_or_own_features() -> None:
    gq, off = _three_game_fixture()
    base = rf.crew_features(gq, off).sort("game_id")
    mod = gq.with_columns(
        pl.when(pl.col("game_id") == "g3").then(1e6).otherwise(pl.col("pf100_g")).alias("pf100_g"),
        pl.when(pl.col("game_id") == "g3").then(1e6).otherwise(pl.col("pace_g")).alias("pace_g"),
    )
    alt = rf.crew_features(mod, off).sort("game_id")
    assert base.equals(alt)  # a game's own (and any later) values cannot enter its features
    # the A3 tripwire construction DOES see the game itself
    inc_b = rf.crew_features(gq, off, inclusive=True).sort("game_id")
    inc_a = rf.crew_features(mod, off, inclusive=True).sort("game_id")
    assert inc_a["crew_pf100"][2] != inc_b["crew_pf100"][2]
    assert (
        inc_a["crew_pf100"][0] == inc_b["crew_pf100"][0]
    )  # earlier games unaffected by later ones


def test_same_day_games_are_excluded() -> None:
    gq = _gq(
        [
            ("g1", D(2022, 10, 18), 40.0, 50.0, 100.0, 1.0),
            ("g2", D(2022, 10, 19), 44.0, 54.0, 104.0, 2.0),
            ("g3", D(2022, 10, 19), 48.0, 58.0, 108.0, 3.0),
        ]
    )
    off = _off([("g1", 1), ("g1", 2), ("g2", 1), ("g2", 2), ("g3", 1), ("g3", 2)])
    base = rf.crew_features(gq, off).sort("game_id")
    alt = rf.crew_features(
        gq.with_columns(
            pl.when(pl.col("game_id") == "g3")
            .then(900.0)
            .otherwise(pl.col("pf100_g"))
            .alias("pf100_g")
        ),
        off,
    ).sort("game_id")
    assert base["crew_pf100"][1] == alt["crew_pf100"][1]  # g2 is the same-day twin of g3
    assert base["crew_pf100"][2] == alt["crew_pf100"][2]
    assert base["crew_pf100"][1] == pytest.approx(base["crew_pf100"][2])


def test_null_official_ids_are_ignored_and_fewer_than_two_gives_nan() -> None:
    gq, off = _three_game_fixture()
    off2 = _off(
        [
            ("g1", 1),
            ("g1", 2),
            ("g1", 3),
            ("g2", 1),
            ("g2", None),
            ("g2", None),
            ("g3", 1),
            ("g3", 4),
            ("g3", None),
        ]
    )
    c = rf.crew_features(gq, off2).sort("game_id")
    assert c["crew_pf100"][1] is None and c["crew_n"][1] == 1  # one usable id -> NaN
    assert c["crew_pf100"][2] is not None and c["crew_n"][2] == 2  # two usable ids -> computed
    assert c["crew_n"].to_list() == [3, 1, 2]


def test_k_to_infinity_gives_league_mean_and_newness_gives_league_mean() -> None:
    gq, off = _three_game_fixture()
    lg_pf = [None, 40.0, 42.0]
    for k in (np.inf, 1e12):
        c = rf.crew_features(gq, off, k=k).sort("game_id")
        for i in (1, 2):
            assert c["crew_pf100"][i] == pytest.approx(lg_pf[i], rel=1e-9)
    # a crew of officials nobody has seen before gets the league mean at default K
    off_new = _off([("g1", 1), ("g1", 2), ("g2", 8), ("g2", 9), ("g3", 10), ("g3", 11)])
    c2 = rf.crew_features(gq, off_new).sort("game_id")
    assert c2["crew_pf100"][1] == pytest.approx(40.0)
    assert c2["crew_pace"][2] == pytest.approx(102.0)  # league pace of g1, g2


def test_permutation_keeps_column_marginals_within_season_and_moves_rows_jointly() -> None:
    rng = np.random.default_rng(1)
    n = 60
    gid = [f"g{i:03d}" for i in range(n)]
    crew = pl.DataFrame(
        {
            "game_id": gid,
            "crew_pf100": rng.normal(size=n),
            "crew_fta100": rng.normal(size=n),
            "crew_pace": rng.normal(size=n),
            "crew_home_margin": rng.normal(size=n),
            "league_home_margin": rng.normal(size=n),
            "crew_n": [3] * n,
        }
    )
    games = pl.DataFrame({"game_id": gid, "season": [2023] * 30 + [2024] * 30})
    perm = rf.permute_crew(crew, games, seed=0).sort("game_id")
    base = crew.sort("game_id")
    for c in (*rf.CREW_COLS, "crew_home_margin"):
        for lo, hi in ((0, 30), (30, 60)):
            assert sorted(perm[c][lo:hi].to_list()) == sorted(base[c][lo:hi].to_list())
    assert perm["crew_pf100"].to_list() != base["crew_pf100"].to_list()
    # joint: the (pf100, fta100) pairs are intact, only re-assigned to other games
    pairs = set(zip(base["crew_pf100"].to_list(), base["crew_fta100"].to_list(), strict=True))
    assert (
        set(zip(perm["crew_pf100"].to_list(), perm["crew_fta100"].to_list(), strict=True)) == pairs
    )
    assert perm["league_home_margin"].to_list() == base["league_home_margin"].to_list()


# --------------------------------------------------------------------------- game quantities


def test_game_quantities_formula_and_dnp_exclusion() -> None:
    games = pl.DataFrame(
        {
            "game_id": ["g1"],
            "game_date": [D(2022, 10, 18)],
            "season": [2022],
            "home_team": [1],
            "away_team": [2],
            "home_pts": [100],
            "away_pts": [90],
        }
    )
    pgs = pl.DataFrame(
        {
            "game_id": ["g1"] * 3,
            "team_id": [1, 2, 2],
            "minutes": [30.0, 20.0, None],
            "fga": [80, 70, 9],
            "fta": [20, 10, 9],
            "tov": [10, 12, 9],
            "oreb": [8, 9, 9],
            "pf": [18, 22, 9],
        }
    )
    q = rf.game_quantities(games, pgs)
    poss = 0.5 * ((80 + 0.44 * 20 + 10 - 8) + (70 + 0.44 * 10 + 12 - 9))
    assert q["poss_g"][0] == pytest.approx(poss)
    assert q["pf100_g"][0] == pytest.approx(100 * 40 / (2 * poss))
    assert q["fta100_g"][0] == pytest.approx(100 * 30 / (2 * poss))
    assert q["pace_g"][0] == pytest.approx(poss)
    assert q["home_margin"][0] == 10 and q["total"][0] == 190


# --------------------------------------------------------------------------- G0 and the missingness audit


def _g0_inputs(dup: bool, short: bool = False) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    games = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "game_date": [D(2022, 10, 18), D(2022, 10, 19), D(2022, 11, 1)],
            "season": [2022] * 3,
            "home_team": [1, 2, 3],
            "away_team": [2, 3, 1],
            "home_pts": [100, 110, 90],
            "away_pts": [90, 100, 95],
        }
    )
    pgs = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "team_id": [1, 2, 3],
            "minutes": [30.0, 20.0, 25.0],
            "fga": [80, 70, 75],
            "fta": [20, 10, 15],
            "tov": [10, 12, 11],
            "oreb": [8, 9, 7],
            "pf": [18, 22, 20],
        }
    )
    rows = []
    for g in ("g1", "g2", "g3"):
        for i, nm in enumerate(("A", "B", "C")):
            rows.append((g, i + 1, nm, str(10 + i)))
    if dup:
        rows.append(("g1", 99, "A", "10"))  # same name, second id, same jersey
    if short:
        rows = [r for r in rows if not (r[0] == "g3" and r[2] != "A")]
    off = pl.DataFrame(rows, schema=["game_id", "official_id", "name", "jersey"], orient="row")
    return rf.game_quantities(games, pgs), pgs, off


def test_g0_passes_on_clean_data_and_fails_on_duplicate_name_ids() -> None:
    gq, pgs, off = _g0_inputs(dup=False)
    ok = rf.g0_checks(gq, pgs, off)
    assert ok["2_name_id_1to1"]["pass"] and ok["3_pf_fta_fields"]["pass"]
    assert ok["1_officials_coverage"]["share_ge3_officials"] == 1.0
    gq, pgs, off = _g0_inputs(dup=True)
    bad = rf.g0_checks(gq, pgs, off)
    assert not bad["2_name_id_1to1"]["pass"]
    assert bad["2_name_id_1to1"]["n_names_unresolved_by_jersey"] == 1
    assert bad["2_name_id_1to1"]["names_with_multiple_ids"][0]["name"] == "A"


def test_g0_distinct_jerseys_resolve_a_same_name_pair() -> None:
    gq, pgs, off = _g0_inputs(dup=True)
    off = off.with_columns(
        pl.when(pl.col("official_id") == 99)
        .then(pl.lit("77"))
        .otherwise(pl.col("jersey"))
        .alias("jersey")
    )
    chk = rf.g0_checks(gq, pgs, off)["2_name_id_1to1"]
    assert chk["names_with_multiple_ids"][0]["resolved_by_jersey"] and chk["pass"]


def test_g0_fails_on_thin_officials_coverage() -> None:
    gq, pgs, off = _g0_inputs(dup=False, short=True)
    c1 = rf.g0_checks(gq, pgs, off)["1_officials_coverage"]
    assert not c1["pass"] and c1["share_1_or_2_officials"] > rf.G0_FEW


def test_missingness_audit_vacuous_with_full_coverage_and_flags_missing_games() -> None:
    gq, pgs, off = _g0_inputs(dup=False)
    pgs2 = pgs.with_columns(pl.lit(1).alias("player_id"), pl.lit(True).alias("starter"))
    strict = rf.crew_features(gq, off)
    a = rf.missingness_audit(gq, pgs2, off, strict, n_boot=20)
    assert a["n_missing_games"] == 0 and not a["stop"] and "not estimable" in a["tests_status"]
    gq, pgs, off = _g0_inputs(dup=False, short=True)
    pgs2 = pgs.with_columns(pl.lit(1).alias("player_id"), pl.lit(True).alias("starter"))
    b = rf.missingness_audit(gq, pgs2, off, rf.crew_features(gq, off), n_boot=20)
    assert b["n_missing_games"] == 1


def test_require_g0_refuses_without_pass_or_with_tampered_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        rf.require_g0(tmp_path)  # nothing written yet
    g0 = {"g0_pass": False, "failed_items": ["2_name_id_1to1"]}
    (tmp_path / "g0.json").write_text(rf.canonical_json(g0))
    (tmp_path / "g0.sha256").write_text(f"{rf.file_sha256(tmp_path / 'g0.json')}  g0.json\n")
    with pytest.raises(SystemExit, match="failed"):
        rf.require_g0(tmp_path)
    g0["g0_pass"] = True
    (tmp_path / "g0.json").write_text(rf.canonical_json(g0))  # edited after its sha was logged
    with pytest.raises(SystemExit, match="no longer matches"):
        rf.require_g0(tmp_path)
    (tmp_path / "g0.sha256").write_text(f"{rf.file_sha256(tmp_path / 'g0.json')}  g0.json\n")
    assert rf.require_g0(tmp_path)["g0_pass"] is True


# --------------------------------------------------------------------------- pass rules


def _contrast(
    point: float, se: float = 0.001, bias: float = 0.0, cov: float = 0.8
) -> dict[str, object]:
    return {
        "d_crps_int": {"point": point, "lo": point - 1.96 * se, "hi": point + 1.96 * se, "se": se},
        "bias_arm": bias,
        "cov80_arm": cov,
        "slices": {},
    }


def test_pass_checks_floor_ci_noise_guard() -> None:
    good = rf.pass_checks("pts", _contrast(-0.010), _contrast(-0.002), 0.01)
    assert good["pass"] and good["powered"]
    assert not rf.pass_checks("pts", _contrast(-0.004), _contrast(0.0), 0.01)["pass"]  # floor
    assert not rf.pass_checks("pts", _contrast(-0.010, se=0.01), _contrast(0.0), 0.01)["pass"]  # CI
    assert not rf.pass_checks("pts", _contrast(-0.010), _contrast(-0.008), 0.01)[
        "pass"
    ]  # noise arm
    assert not rf.pass_checks("pts", _contrast(-0.010), _contrast(-0.002), 0.2)["pass"]  # BH p
    assert not rf.pass_checks("pts", _contrast(-0.010, bias=0.7), _contrast(0.0), 0.01)["pass"]
    assert not rf.pass_checks("pts", _contrast(-0.010, cov=0.7), _contrast(0.0), 0.01)["pass"]
    sl = _contrast(-0.010)
    sl["slices"] = {"bench": {"n_rows": 400, "d_crps_int": 0.02}}
    r = rf.pass_checks("pts", sl, _contrast(0.0), 0.01)
    assert not r["pass"] and r["bad_slices"] == ["bench"]
    assert rf.pass_checks("total", _contrast(-0.06, se=0.01), _contrast(0.0), 0.01)["pass"]
    assert not rf.pass_checks("total", _contrast(-0.04, se=0.01), _contrast(0.0), 0.01)["pass"]
    assert not rf.pass_checks("total", _contrast(-0.06, se=0.01, bias=1.2), _contrast(0.0), 0.01)[
        "pass"
    ]
    assert not rf.pass_checks("total", _contrast(-0.06, se=0.05), _contrast(0.0), 0.01)["powered"]


def test_verdict_needs_same_test_on_2023_and_2024_and_labels_underpowered() -> None:
    def t(passed: bool, powered: bool = True) -> dict[str, object]:
        return {"pass": passed, "powered": powered}

    tests = {
        "pts": {"2023": t(True), "2024": t(True)},
        "fg3m": {"2023": t(False), "2024": t(False)},
        "total": {"2023": t(False, False), "2024": t(False, False)},
    }
    v = rf.verdict(tests)
    assert v["overall"] == "PASS" and v["per_test"]["total"]["status"] == "UNDERPOWERED"
    tests["pts"] = {"2023": t(True), "2024": t(False)}
    v = rf.verdict(tests)
    assert v["overall"] == "FAIL" and v["per_test"]["pts"]["status"] == "FAIL"
    tests["fg3m"] = {"2023": t(False, False), "2024": t(False)}
    tests["pts"] = {"2023": t(False, False), "2024": t(False, False)}
    assert rf.verdict(tests)["overall"] == "UNDERPOWERED"


# --------------------------------------------------------------------------- production identity and pipeline smoke

CFG = ContextResidualConfig(
    n_estimators=25,
    min_child_samples=30,
    min_cal_rows=300,
    report=ReportTriggerConfig(tip_source="proxy19"),
)


def _league_with_box(
    seed: int = 0, n_days: int = 340
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    gdf, pdf, static = _league(seed=seed, n_days=n_days)
    rng = np.random.default_rng(seed + 7)
    n = pdf.height
    pdf = pdf.with_columns(
        pl.Series("fga", rng.integers(0, 18, n)),
        pl.Series("fta", rng.integers(0, 8, n)),
        pl.Series("oreb", rng.integers(0, 4, n)),
        pl.Series("tov", rng.integers(0, 5, n)),
        pl.Series("pf", rng.integers(0, 6, n)),
    )
    rows = []
    for gid in gdf["game_id"].to_list():
        for o in rng.choice(8, size=3, replace=False):
            rows.append((gid, 1000 + int(o), f"O{o}", str(10 + int(o))))
    off = pl.DataFrame(rows, schema=["game_id", "official_id", "name", "jersey"], orient="row")
    return gdf, pdf, static, off


@pytest.fixture(scope="module")
def league() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    return _league_with_box()


def test_flag_off_production_features_and_predictions_are_byte_identical(
    league: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    gdf, pdf, static, off = league
    prod_cols = [
        "game_id",
        "player_id",
        "team_id",
        "minutes",
        "pts",
        "reb",
        "ast",
        "fg3m",
        "starter",
    ]
    feats = build_features(gdf, pdf.select(prod_cols), static, {}, ELO)
    # extra box columns in pgs do not change production features
    wide = build_features(gdf, pdf, static, {}, ELO)
    assert feats.equals(wide)
    gq = rf.game_quantities(gdf, pdf)
    crew = rf.crew_features(gq, off)
    with_crew = rf.attach_crew(feats, crew)
    assert with_crew.select(feats.columns).equals(feats)  # attaching crew changes nothing else
    assert stat_feature_names("pts") == stat_feature_names("pts", extra=())
    assert stat_feature_names("pts", extra=rf.CREW_COLS)[
        : len(stat_feature_names("pts"))
    ] == stat_feature_names("pts")
    # A0 through this module == the production walk-forward + scoring of lower_tail_eval
    cov = feats.with_columns(pl.lit(1.0).alias("cov_covered"), pl.lit(1.0).alias("cov_pace"))
    mine = rf.collect_arm(cov, "pts", CFG, stat_feature_names("pts"))
    comp = collect_stat(attach_realised(feats, gdf, pdf), "pts", CFG)
    ref = arm_scores(production_grid(comp), comp["row_y"], "pts")
    assert np.array_equal(mine.gid, comp["row_gid"] if "row_gid" in comp else comp["row_gid"])
    assert np.allclose(mine.crps_int, ref["crps_int"], rtol=0, atol=1e-12)
    assert np.allclose(mine.pit_int, ref["pit_int"], rtol=0, atol=1e-12)
    assert set(np.unique(mine.season)) <= {2023, 2024}


def test_holdout_season_is_refused() -> None:
    bad = pl.DataFrame({"season": [2024, 2025]})
    with pytest.raises(ValueError, match="holdout"):
        rf.assert_no_holdout(bad)
    feats = pl.DataFrame({"x": [1]})
    with pytest.raises(ValueError, match="holdout"):
        rf.collect_arm(feats, "pts", CFG, [], test_seasons=(2025,))


def test_total_features_are_asof_and_integer_support(
    league: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    gdf, pdf, static, _ = league
    prod_cols = [
        "game_id",
        "player_id",
        "team_id",
        "minutes",
        "pts",
        "reb",
        "ast",
        "fg3m",
        "starter",
    ]
    feats = build_features(gdf, pdf.select(prod_cols), static, {}, ELO)
    a = rf.build_total_frame(gdf, feats, ELO)
    last = gdf.sort(["game_date", "game_id"]).row(-1, named=True)
    mod = gdf.with_columns(
        pl.when(pl.col("game_id") == last["game_id"])
        .then(300)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    b = rf.build_total_frame(mod, feats, ELO)
    feat_cols = [c for c in rf.TOTAL_BASE_FEATURES if c != "n_out_h" and c != "n_out_a"]
    ra, rb = (
        a.filter(pl.col("game_id") == last["game_id"]),
        b.filter(pl.col("game_id") == last["game_id"]),
    )
    for c in feat_cols:
        assert ra[c][0] == pytest.approx(rb[c][0]), (
            c
        )  # the game's own score never enters its features
    # eligibility: both teams need >= 10 prior games
    assert not a.filter(pl.col("game_date") == a["game_date"].min())["eligible"].any()
    el = a.filter(pl.col("eligible"))
    assert el.height > 100 and el["m"].is_not_nan().all()
    head = rf.TotalHead(CFG, list(rf.TOTAL_BASE_FEATURES)).fit(el.head(int(el.height * 0.7)))
    mean, q = head.predict(el.tail(50))
    assert np.array_equal(q, np.round(q)) and (np.diff(q, axis=1) >= 0).all()
    assert np.isfinite(mean).all()


def test_pipeline_smoke_end_to_end_on_a_synthetic_league(
    league: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame], tmp_path: Path
) -> None:
    gdf, pdf, static, off = league
    avail = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "source": pl.Utf8,
        }
    )
    win = (
        gdf.sort(["game_date", "game_id"])
        .with_columns(
            (pl.col("home_pts") > pl.col("away_pts")).cast(pl.Int64).alias("y"),
            pl.lit(0.1).alias("elo_logit"),
            pl.lit(0.0).alias("d_out"),
            pl.lit(0.0).alias("d_doubt"),
        )
        .select("game_id", "game_date", "season", "y", "elo_logit", "d_out", "d_doubt")
    )
    res = rf.run_experiment(
        gdf, pdf, static, avail, off, ELO, CFG, tmp_path, n_boot=40, enforce_repro=False,
        win_base=win, win_cfg=(5.0, 150), stats_arms=("pts", "fg3m"),
    )  # fmt: skip
    assert res["repro"]["pass"] is False  # synthetic data is not production
    for stat in ("pts", "fg3m"):
        for s in ("2023", "2024"):
            c = res["props"][stat][s]["A1-A0"]
            assert c["n_rows"] > 200 and np.isfinite(c["d_crps_int"]["point"])
            assert c["n_games"] <= c["n_rows"]
    for s in ("2023", "2024"):
        t = res["total"][s]["T1-T0"]
        assert t["n_rows"] == t["n_games"] > 20
        assert np.isfinite(t["d_crps_int"]["point"]) and np.isfinite(t["sd_paired_diff"])
        assert res["tests"]["total"][s]["floor"] == rf.TOTAL_FLOOR
    assert res["verdict"]["overall"] in {"PASS", "FAIL", "UNDERPOWERED"}
    assert res["win"]["2023"]["n_games"] > 100
    assert (tmp_path / "arms" / "A0_pts.npz").exists()
    # same rows in every arm; A0 is the exact control for A1/A2/A3
    a0 = rf.ArmScores.load(tmp_path / "arms" / "A0_pts.npz")
    a1 = rf.ArmScores.load(tmp_path / "arms" / "A1_pts.npz")
    assert np.array_equal(a0.gid, a1.gid) and np.array_equal(a0.pid, a1.pid)
    # the report renders from the stored pieces (g0-fail path and full-results path)
    out = tmp_path
    gq, pgs, off_g = _g0_inputs(dup=True)
    checks = rf.g0_checks(gq, pgs, off_g)
    g0 = {
        "hash_check": rf.hash_check(),
        "checks": checks,
        "g0_pass": False,
        "failed_items": ["2_name_id_1to1"],
        "missingness_audit_sha256": "x",
        "missingness_stop": False,
    }
    (out / "g0.json").write_text(rf.canonical_json(g0))
    (out / "g0.sha256").write_text("abc  g0.json\n")
    (out / "missingness.sha256").write_text("def  missingness.json\n")
    strict = rf.crew_features(gq, off_g)
    (out / "missingness.json").write_text(
        rf.canonical_json(
            rf.missingness_audit(
                gq,
                pgs.with_columns(pl.lit(1).alias("player_id"), pl.lit(True).alias("starter")),
                off_g,
                strict,
                20,
            )
        )
    )
    md = rf.render_report(out)
    assert "G0 FAILED" in md and "Not run" in md and "Names carrying more than one" in md
    (out / "results.json").write_text(rf.canonical_json(res))
    md2 = rf.render_report(out)
    assert "Pass rules" in md2 and "Verdict" in md2
    json.loads((out / "results.json").read_text())


def test_stats_family_is_the_confirmatory_three() -> None:
    assert rf.CONFIRMATORY == ("pts", "fg3m", "total") and set(rf.CONFIRMATORY[:2]) <= set(
        PROP_STATS
    )
