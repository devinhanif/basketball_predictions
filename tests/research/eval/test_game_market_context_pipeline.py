# ruff: noqa: E501
"""GAME_MARKET_CONTEXT: gate audits, per-stat scoring, O/U log loss, rules and report rendering.

Complements ``test_game_market_context_eval.py`` (arm names, freeze refusal, R1/BH logic). Synthetic
arrays and frames only: no database, no network, no season 2025.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.eval.context_residual_eval import TEST_SEASONS, crps_from_quantiles
from research.eval import game_market_context_eval as ev
from research.eval.four_factors_eval import MIN_SLICE_N

TAUS = (np.arange(1, 200) - 0.5) / 199
N_SEASON = 700


def _grid(rng: np.random.Generator, n: int, lam: float, scale: float = 1.0) -> np.ndarray:
    base = np.floor(np.quantile(rng.poisson(lam, 4000), TAUS))
    return np.repeat(base[None, :], n, axis=0).astype(np.int16)


def _meta() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for si, season in enumerate(TEST_SEASONS):
        for k in range(N_SEASON):
            has_mkt = k % 4 != 0
            rows.append(
                {
                    "game_id": f"g{season}_{k // 10}",
                    "player_id": k + 1,
                    "team_id": 1 + k % 5,
                    "game_date": dt.date(2023 + si, 11, 1) + dt.timedelta(days=k // 10),
                    "season": season,
                    "min10": 25.0,
                    "n_prior": 12,
                    "starter10": 1.0 if k % 2 == 0 else 0.0,
                    "team_game_no": k // 10,
                    "n_out_rot": k % 3,
                    "has_report": k % 2,
                    "min_gap": float(rng.normal(0, 4)),
                    "mkt_margin": float(rng.normal(0, 5)) if has_mkt else None,
                    "mkt_abs_margin": float(abs(rng.normal(5, 3))) if has_mkt else None,
                    "mkt_total": float(rng.normal(225, 8)) if has_mkt else None,
                    "mkt_src": ("pinnacle" if k % 3 else "consensus") if has_mkt else None,
                    "train_mkt_games": 800 if (season == 2024 or k % 2) else 100,
                }
            )
    return pl.DataFrame(rows)


def _arms(stat: str = "pts", gain: float = 0.0) -> dict[str, dict[str, Any]]:
    """Six arms over the same rows; ``gain`` makes A1 and A2 condition on a hint of the outcome."""
    rng = np.random.default_rng(1)
    meta = _meta()
    n = meta.height
    y = rng.poisson(12, n).astype(float)
    base = _grid(rng, n, 12)
    out: dict[str, dict[str, Any]] = {}
    from scipy import stats as sps

    hint = np.clip(12 + gain * (y - 12) + rng.normal(0, 1, n), 1, None)
    better = sps.poisson.ppf(TAUS[None, :], hint[:, None]).astype(np.int16)
    for a in ev.ARMS:
        qi = better if (gain and a in ("A1", "A2")) else base
        out[a] = {"qi": qi.copy(), "y": y, "meta": meta}
    return out


# ------------------------------------------------------------------------------ gate audits


def test_check_a0_repro_needs_crps_and_row_counts_to_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arms = {"pts": _arms()["A0"]}
    stored: dict[str, Any] = {"pts": {}}
    for s in TEST_SEASONS:
        m = (arms["pts"]["meta"]["season"] == s).to_numpy()
        crps = float(
            crps_from_quantiles(arms["pts"]["qi"][m].astype(float), arms["pts"]["y"][m]).mean()
        )
        stored["pts"][str(s)] = {"off": {"crps_int": crps, "n": int(m.sum())}}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(stored))
    monkeypatch.setattr(ev, "LOWER_TAIL_JSON", p)
    r = ev.check_a0_repro(arms)
    assert r["ok"] is True and set(r["detail"]) == {"pts_2023", "pts_2024"}
    assert r["detail"]["pts_2023"]["delta"] == pytest.approx(0.0)
    stored["pts"]["2024"]["off"]["crps_int"] += 1e-3
    p.write_text(json.dumps(stored))
    r = ev.check_a0_repro(arms)
    assert (
        r["ok"] is False
        and r["detail"]["pts_2024"]["ok"] is False
        and r["detail"]["pts_2023"]["ok"] is True
    )


def test_collect_cached_returns_a_stored_arm_without_refitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "CACHE_DIR", tmp_path)
    a = _arms()["A0"]
    np.savez(tmp_path / "arm_pts_A0.npz", qi=a["qi"], y=a["y"])
    a["meta"].write_parquet(tmp_path / "arm_pts_A0.parquet")

    def boom(*args: Any, **kw: Any) -> None:
        raise AssertionError("must not refit when the cache exists")

    monkeypatch.setattr(ev, "collect_arm", boom)
    out = ev.collect_cached(pl.DataFrame(), "pts", "A0", None)  # type: ignore[arg-type]
    assert np.array_equal(out["qi"], a["qi"]) and out["meta"].equals(a["meta"])
    # a missing cache entry would trigger the fit and then store its result
    calls: list[tuple[str, bool]] = []

    def fake(
        frame: Any, stat: str, cfg: Any, names: Any, own_history: bool = True
    ) -> dict[str, Any]:
        calls.append((stat, own_history))
        return a

    monkeypatch.setattr(ev, "collect_arm", fake)
    ev.collect_cached(pl.DataFrame(), "pts", "A3", None)  # type: ignore[arg-type]
    assert calls == [("pts", False)]  # A3 drops the own-stat offset
    assert (tmp_path / "arm_pts_A3.npz").exists() and (tmp_path / "arm_pts_A3.parquet").exists()


def _games_and_market() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    rows = []
    for s in (2022, 2023, 2024):
        for i in range(20):
            gid = ("002" if i < 18 else "004") + f"{s}{i:03d}"
            rows.append({"game_id": gid, "season": s, "home_pts": 100})
    games = pl.DataFrame(rows)
    # market covers 17 of 18 regular games in 2023 (94.4%), all of 2024, none of 2022
    gm_ids, src = [], []
    for s in (2023, 2024):
        for i in range(18):
            if s == 2023 and i == 0:
                continue
            gm_ids.append(f"002{s}{i:03d}")
            src.append("pinnacle" if i % 2 else "consensus")
    gm = pl.DataFrame({"game_id": gm_ids, "mkt_src": src})
    base = pl.DataFrame(
        {
            "season": [2023, 2023, 2024, 2024, 2024],
            "n_prior": [10, 3, 10, 10, 10],
            "mkt_total": [220.0, None, 221.0, None, 222.0],
        }
    )
    return games, gm, base


def test_check_2_coverage_reports_regular_season_coverage_and_sources() -> None:
    games, gm, base = _games_and_market()
    tests, out = ev.check_2_coverage(games, gm, base)
    assert [t["ok"] for t in tests] == [True, True]  # 2023: 17/18 = 0.944 >= 0.90; 2024: 1.0
    assert out["2023"]["regular_season_games"] == 18 and out["2023"]["with_market"] == 17
    assert out["2023"]["coverage_regular"] == pytest.approx(17 / 18)
    assert out["2023"]["coverage_all_games"] == pytest.approx(
        17 / 20
    )  # playoffs have no market here
    assert out["2022"]["with_market"] == 0 and out["2022"]["pinnacle_games"] == 0
    assert out["2024"]["pinnacle_games"] + out["2024"]["consensus_games"] == 18
    # scored rows are those with n_prior >= 5; coverage is the share carrying a market total
    assert out["2024"]["scored_rows"] == 3 and out["2024"]["scored_rows_with_market"] == 2
    assert out["2023"]["scored_row_coverage"] == pytest.approx(1.0)
    # drop the 2023 market to 12/18: the audit fails
    gm_low = gm.filter(
        ~(
            (pl.col("game_id").str.contains("2023"))
            & (pl.col("game_id").str.slice(-3).cast(pl.Int64) < 6)
        )
    )
    tests, _ = ev.check_2_coverage(games, gm_low, base)
    assert tests[0]["ok"] is False and tests[1]["ok"] is True


def test_check_3_known_at_counts_late_snapshots_proxies_and_stale_quotes() -> None:
    gm = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "mkt_min_before_tip": [120.0, 90.0, 60.0],
            "mkt_snapshot_ts": [
                dt.datetime(2024, 1, 1, 10),
                dt.datetime(2024, 1, 2, 10),
                dt.datetime(2024, 1, 3, 10),
            ],
        }
    )
    tips = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "tip_et": [dt.datetime(2024, 1, 1, 19)] * 3,
            "tipoff_utc": [
                dt.datetime(2024, 1, 1, 12),
                dt.datetime(2024, 1, 2, 12),
                dt.datetime(2024, 1, 3, 12),
            ],
        }
    )
    rows = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "snapshot_ts": gm["mkt_snapshot_ts"],
            "last_update": [
                dt.datetime(2024, 1, 1, 9),
                dt.datetime(2024, 1, 2, 9),
                dt.datetime(2024, 1, 3, 9),
            ],
        }
    )
    tests, out = ev.check_3_known_at(gm, {"rows": 3}, rows, tips)
    assert [t["ok"] for t in tests] == [True, True, True]
    assert out["min_minutes_before_tip"] == 60.0 and out["median_minutes_before_tip"] == 90.0
    assert out["games"] == 3 and out["used_games_without_real_tip"] == 0
    # a snapshot closer than the lead time, a missing real tip and a post-stamp update each fail their own check
    late = gm.with_columns(
        pl.when(pl.col("game_id") == "a")
        .then(pl.lit(10.0))
        .otherwise(pl.col("mkt_min_before_tip"))
        .alias("mkt_min_before_tip")
    )
    assert [t["ok"] for t in ev.check_3_known_at(late, {}, rows, tips)[0]] == [False, True, True]
    no_tip = tips.filter(pl.col("game_id") != "b")
    assert [t["ok"] for t in ev.check_3_known_at(gm, {}, rows, no_tip)[0]] == [True, False, True]
    stale = rows.with_columns(
        pl.when(pl.col("game_id") == "c")
        .then(pl.col("snapshot_ts") + pl.duration(hours=1))
        .otherwise(pl.col("last_update"))
        .alias("last_update")
    )
    assert [t["ok"] for t in ev.check_3_known_at(gm, {}, stale, tips)[0]] == [True, True, False]


def test_check_4_missingness_gap_across_minutes_buckets() -> None:
    games = pl.DataFrame({"game_id": ["g1", "g2", "g3", "g4"], "season": [2023, 2023, 2024, 2024]})
    # every bucket has the same share of rows in games without a market => zero gap
    pgs = pl.DataFrame(
        {
            "game_id": ["g1", "g2"] * 4,
            "minutes": [0.0, 0.0, 3.0, 3.0, 10.0, 10.0, 30.0, 30.0],
        }
    )
    gm = pl.DataFrame({"game_id": ["g1"]})  # g2 has no market, g1 has
    gap, played_gap, audit = ev.check_4_missingness(pgs, games, gm)
    assert gap == 0.0 and played_gap == 0.0
    assert [a["bucket"] for a in audit] == ["DNP", "<5", "5-20", ">20"] and audit[0]["n"] == 2
    assert all(a["null_market_rate"] == 0.5 for a in audit)
    # NULL markets that concentrate on DNP rows are a gap, visible in the all-bucket figure only
    pgs2 = pl.DataFrame(
        {
            "game_id": ["g2", "g2", "g1", "g1", "g1", "g1"],
            "minutes": [0.0, None, 3.0, 10.0, 20.0, 30.0],
        }
    )
    gap, played_gap, _ = ev.check_4_missingness(pgs2, games, gm)
    assert gap == pytest.approx(100.0) and played_gap == pytest.approx(0.0)


def test_descriptive_corr_and_training_sufficiency() -> None:
    rng = np.random.default_rng(2)
    n = 200
    exp_margin = rng.normal(0, 5, n)
    base = pl.DataFrame(
        {
            "season": [2023] * (n // 2) + [2024] * (n // 2),
            "n_prior": [10] * n,
            "mkt_margin": exp_margin + rng.normal(0, 1, n),
            "exp_margin": exp_margin,
            "r20_pts_for": rng.normal(110, 3, n),
            "opp_r20_pts_for": rng.normal(110, 3, n),
            "mkt_total": 220 + rng.normal(0, 3, n),
            "mkt_abs_margin": np.abs(exp_margin),
            "abs_margin": np.abs(exp_margin) + rng.normal(0, 1, n),
        }
    )
    out = ev.descriptive_corr(base)
    for s in ("2023", "2024"):
        assert out[s]["n"] == n // 2
        assert out[s]["corr_mkt_margin_exp_margin"] > 0.9
        assert abs(out[s]["corr_mkt_total_r20_for_plus_opp_r20_for"]) < 0.4
        assert out[s]["corr_mkt_abs_abs_margin"] > 0.8
    # rows missing any input are not in the correlation
    holes = base.with_columns(
        pl.when(pl.int_range(pl.len()) < 10)
        .then(None)
        .otherwise(pl.col("mkt_margin"))
        .alias("mkt_margin")
    )
    assert ev.descriptive_corr(holes)["2023"]["n"] == n // 2 - 10
    ts = ev.training_sufficiency({"pts": _arms()["A0"]})
    assert ts["mature_games"] == ev.MATURE_GAMES
    assert ts["2024"]["share_rows_in_mature_blocks"] == pytest.approx(1.0)
    assert ts["2023"]["share_rows_in_mature_blocks"] == pytest.approx(0.5)
    assert ts["2023"]["blocks"][0]["ym"] == "2023-11"


def test_gm_equal_compares_only_the_requested_games() -> None:
    a = pl.DataFrame(
        {
            "game_id": ["x", "y"],
            "mkt_home_margin": [1.0, 2.0],
            "mkt_total": [220.0, 221.0],
            "mkt_p_home": [0.5, 0.6],
            "mkt_src": ["pinnacle", "consensus"],
        }
    )
    b = a.with_columns(
        pl.when(pl.col("game_id") == "y")
        .then(pl.lit(999.0))
        .otherwise(pl.col("mkt_total"))
        .alias("mkt_total")
    )
    assert ev._gm_equal(a, b, {"x"}) is True
    assert ev._gm_equal(a, b, {"x", "y"}) is False


def test_load_pairs_concatenates_audit_seasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ev, "PAIRS_DIR", tmp_path)
    for s in ev.AUDIT_SEASONS:
        pl.DataFrame(
            {"game_id": [f"g{s}"], "season": [s], "extra": [1.0] if s == 2023 else [None]}
        ).write_parquet(tmp_path / f"pairs_{s}.parquet")
    d = ev.load_pairs()
    assert d["season"].to_list() == [2023, 2024] and d.height == 2


# ------------------------------------------------------------------------------ scoring


def test_slice_masks_cut_terciles_on_market_rows_only() -> None:
    meta = _meta().filter(pl.col("season") == 2024)
    m = ev.slice_masks(meta)
    n = meta.height
    assert m["all"].sum() == n and m["market_rows"].sum() == meta["mkt_total"].is_not_null().sum()
    assert m["starter"].sum() + m["bench"].sum() == n
    assert m["pinnacle"].sum() + m["consensus"].sum() == m["market_rows"].sum()
    assert m["mature_blocks"].all()  # every 2024 block is mature in this fixture
    for label in ("spread", "total"):
        parts = [m[f"{label}_{t}"] for t in ("low", "mid", "high")]
        assert sum(int(p.sum()) for p in parts) == int(
            m["market_rows"].sum()
        )  # a partition of the market rows
        assert not (parts[0] & parts[1]).any() and not (parts[1] & parts[2]).any()
        assert not any((p & ~m["market_rows"]).any() for p in parts)
    # with too few market rows the tercile slices are simply absent
    few = meta.with_columns(
        pl.when(pl.int_range(pl.len()) < 10)
        .then(pl.col("mkt_total"))
        .otherwise(None)
        .alias("mkt_total")
    )
    assert "total_low" not in ev.slice_masks(few)


def test_score_stat_deltas_match_independent_recomputation() -> None:
    arms = _arms(gain=0.7)
    out = ev.score_stat("pts", arms)
    meta = arms["A0"]["meta"]
    tab = {(r["season"], r["arm"]): r for r in out["rows"]}
    for s in TEST_SEASONS:
        sm = (meta["season"] == s).to_numpy()
        y = arms["A0"]["y"][sm]
        c0 = crps_from_quantiles(arms["A0"]["qi"][sm].astype(float), y)
        c1 = crps_from_quantiles(arms["A1"]["qi"][sm].astype(float), y)
        r1 = tab[(s, "A1")]
        assert r1["dcrps"] == pytest.approx(float((c1 - c0).mean()))
        assert r1["dcrps"] < -0.1 and r1["ci_hi"] < 0 and r1["ci_lo"] <= r1["dcrps"]
        assert tab[(s, "A0")]["dcrps"] is None and tab[(s, "A3")]["dcrps"] == pytest.approx(0.0)
        assert tab[(s, "P1a")]["dcrps"] == pytest.approx(0.0)  # placebo arm = baseline grid here
        mk = meta.filter(pl.col("season") == s)["mkt_total"].is_not_null().to_numpy()
        assert r1["restricted"]["n"] == int(mk.sum())
        assert r1["restricted"]["dcrps"] == pytest.approx(float((c1 - c0)[mk].mean()))
        mat = (meta.filter(pl.col("season") == s)["train_mkt_games"] >= ev.MATURE_GAMES).to_numpy()
        assert r1["mature"]["n"] == int(mat.sum())
        assert r1["mature"]["dcrps"] < 0  # the informed arm also wins on the mature-block rows
    cs = {(c["season"], c["arm"]): c for c in out["contrasts"]}
    assert cs[(2024, "A1")]["vs"] == "P1a" and cs[(2024, "A2")]["vs"] == "P1b"
    assert cs[(2024, "A1")]["dcrps"] < 0 and cs[(2024, "A1")]["restricted_dcrps"] < 0
    slices = [s for s in out["slices"] if s["arm"] == "A1" and s["season"] == 2024]
    assert {"all", "market_rows", "starter", "bench", "first15", "mature_blocks"} <= {
        s["slice"] for s in slices
    }
    big = [s for s in slices if s["n"] >= MIN_SLICE_N]
    assert big and all("dcrps" in s for s in big)
    small = [s for s in slices if s["n"] < MIN_SLICE_N and s["slice"] != "all"]
    assert all("dcrps" not in s for s in small)


def test_score_stat_refuses_arms_scored_on_different_rows() -> None:
    arms = _arms()
    arms["A1"] = {**arms["A1"], "meta": arms["A1"]["meta"].reverse()}
    with pytest.raises(AssertionError, match="identical rows"):
        ev.score_stat("pts", arms)
    arms = _arms()
    arms["A2"] = {**arms["A2"], "y": arms["A2"]["y"] + 1}
    with pytest.raises(AssertionError):
        ev.score_stat("pts", arms)


def _pairs_for(arms: dict[str, dict[str, Any]], stat: str = "pts") -> pl.DataFrame:
    meta = arms["A0"]["meta"]
    rng = np.random.default_rng(4)
    keep = np.arange(meta.height) % 2 == 0
    m = meta.filter(pl.Series(keep))
    return pl.DataFrame(
        {
            "game_id": m["game_id"],
            "player_id": m["player_id"].cast(pl.Int64),
            "stat": [stat] * m.height,
            "book": ["draftkings"] * m.height,
            "snapshot_kind": ["t60"] * m.height,
            "line": [11.5] * m.height,
            "p_model": rng.uniform(0.3, 0.7, m.height),
            "p_over_mult": rng.uniform(0.3, 0.7, m.height),
            "season": m["season"],
        }
    )


def test_ou_inputs_and_scores_use_each_arms_own_grid() -> None:
    arms = _arms(gain=0.7)
    pairs = _pairs_for(arms)
    o = ev.ou_inputs("pts", arms["A1"], pairs)
    assert o["n_pairs"] == pairs.height and len(o["p"]) == pairs.height
    assert np.all((o["p"] >= 0) & (o["p"] <= 1)) and set(np.unique(o["status"])) == {0}
    # at line 11.5 the baseline grid gives the same over probability for every row
    base = ev.ou_inputs("pts", arms["A0"], pairs)
    assert np.allclose(base["p"], base["p"][0])
    # P(over 11.5) is the share of grid points >= 12
    assert base["p"][0] == pytest.approx((arms["A0"]["qi"][0] >= 12).mean())
    rows = ev.ou_scores("pts", arms, pairs)
    tab = {(r["season"], r["arm"]): r for r in rows}
    for s in TEST_SEASONS:
        assert tab[(s, "A0")]["n"] == tab[(s, "A1")]["n"] > 0
        assert (
            "dll" not in tab[(s, "A0")] and tab[(s, "A1")]["dll"] < 0
        )  # the informed arm has lower log loss
        assert tab[(s, "A1")]["ci_lo"] <= tab[(s, "A1")]["dll"] <= tab[(s, "A1")]["ci_hi"]
        assert tab[(s, "A1")]["market_logloss"] == tab[(s, "A0")]["market_logloss"]
    # rows where any arm hits a push are dropped for every arm
    whole = pairs.with_columns(pl.lit(12.0).alias("line"))
    n_whole = ev.ou_scores("pts", arms, whole)
    assert sum(r["n"] for r in n_whole if r["arm"] == "A0") < sum(
        r["n"] for r in rows if r["arm"] == "A0"
    )


def _full_results(gain: float) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {"rows": [], "slices": [], "contrasts": [], "ou": []}
    for stat in ev.STATS:
        arms = _arms(stat, gain)
        t = ev.score_stat(stat, arms)
        for k in ("rows", "slices", "contrasts"):
            tables[k] += t[k]
        tables["ou"] += ev.ou_scores(stat, arms, _pairs_for(arms, stat))
    ev.add_bh(tables["rows"])
    return {**tables, "rules": ev.evaluate_rules(tables, True)}


@pytest.fixture(scope="module")
def results_with_gain() -> dict[str, Any]:
    mp = pytest.MonkeyPatch()
    mp.setattr(ev, "N_BOOT", 150)
    mp.setattr("research.eval.four_factors_eval.N_BOOT", 150)
    try:
        return _full_results(0.7)
    finally:
        mp.undo()


def test_evaluate_rules_passes_an_informed_arm_and_closes_a_flat_one(
    results_with_gain: dict[str, Any],
) -> None:
    rules = results_with_gain["rules"]
    assert set(k for k in rules if k != "close_no_cell_passes_2023") == {
        f"{a}_{s}" for a in ("A1", "A2") for s in ev.STATS
    }
    cell = rules["A1_pts"]
    assert cell["rule1_crps_by_season"] == {"2023": True, "2024": True}
    assert cell["rule2_beats_placebo_by_season"] == {"2023": True, "2024": True}
    assert cell["rule4_ou_noninferior_by_season"] == {"2023": True, "2024": True}
    assert cell["rule3_guards"]["abs_bias<=0.5"] is True
    assert cell["pass_2023_crps"] is True and rules["close_no_cell_passes_2023"] is False
    # tests_ok False (a failed gate check) vetoes a pass whatever the scores say
    res = dict(results_with_gain)
    veto = ev.evaluate_rules(res, False)
    assert (
        veto["A1_pts"]["pass"] is False and veto["A1_pts"]["rule1_crps_by_season"]["2024"] is True
    )
    # a flat result (identical arms) selects nothing and closes the direction
    mp = pytest.MonkeyPatch()
    mp.setattr(ev, "N_BOOT", 100)
    mp.setattr("research.eval.four_factors_eval.N_BOOT", 100)
    try:
        flat = _full_results(0.0)
    finally:
        mp.undo()
    assert flat["rules"]["close_no_cell_passes_2023"] is True
    assert not any(v["pass"] for k, v in flat["rules"].items() if isinstance(v, dict))


def test_report_and_ledgers_render_every_table(
    results_with_gain: dict[str, Any], tmp_path: Path
) -> None:
    res = {**results_with_gain, "sha256": "ab" * 32, "git_sha": "abc1234", "seeds": ev.SEEDS}
    (tmp_path / "results.json").write_text(
        json.dumps(res, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    )
    gate = {
        "sha256": "cd" * 32,
        "tests": [{"ok": True}, {"ok": True}, {"ok": False}],
        "coverage": {"2023": {"coverage_regular": 0.95}, "2024": {"coverage_regular": 0.97}},
        "missingness_gap_pp": {"all": 1.25},
        "a0_repro": {"detail": {"pts_2023": {"delta": 1e-9}, "reb_2024": {"delta": -3e-9}}},
        "known_at": {"snapshot_rows": {"rows_refused_late": 7}, "min_minutes_before_tip": 62.5},
        "training_sufficiency": {"2023": {"share_rows_in_mature_blocks": 0.55}},
    }
    (tmp_path / "gate.json").write_text(json.dumps(gate))
    md = ev.render_report(tmp_path)
    for needle in (
        "# GAME_MARKET_CONTEXT results",
        "`" + "ab" * 32 + "`",
        "## Real vs placebo (A1 - P1a, A2 - P1b)",
        "## Over/under log loss at DraftKings t60 lines (arm - A0)",
        "## Slices (arm - A0)",
        "## Rules",
        "| pts | 2024 | A1 |",
    ):
        assert needle in md
    assert "| pts | 2024 | A3 |" in md  # every arm is in the main table
    assert "| pts | 2024 | A3 | starter" not in md  # but the slice table only lists A1 and A2
    line = ev.gate_ledger_lines(tmp_path)[0]
    assert "2/3 checks pass" in line and "A0 max |delta| 3.0e-09" in line and "7 refused" in line
    assert (
        "0.9500" in line and "0.9700" in line and "1.25 pp" in line and "min lead 62.5 min" in line
    )
    g = ev.write_gate_ledger(tmp_path).read_text().splitlines()
    assert g[0] == "| id | test | result |" and len(g) == 3
    full = ev.write_ledger(tmp_path).read_text().splitlines()
    assert (
        sum(ln.startswith("| T###") for ln in full)
        == 1 + 2 * len(ev.STATS) * 2 + 2 * len(ev.STATS) + 1
    )
    assert any("verdict A1_pts" in ln for ln in full) and "close (no cell passes 2023)" in full[-1]


def test_formatting_helper() -> None:
    assert ev._f(None) == "n/a" and ev._f(0.12345) == "0.1235" and ev._f(-0.5, 2, True) == "-0.50"
