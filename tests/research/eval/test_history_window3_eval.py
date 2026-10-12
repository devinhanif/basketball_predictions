# ruff: noqa: E501
"""HISTORY_WINDOW_3 harness: treatment exactness, the guards' power (planted detection), freeze refusals, arms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import history_window3_eval as h3
from research.eval import history_window_eval as hw
from tests.research.eval.test_history_window_eval import CFG, Env


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Env:
    return Env(tmp_path_factory.mktemp("hw3"))


@pytest.fixture(scope="module")
def world(env: Env) -> hw.World:
    return h3.restrict_world(env.world())


@pytest.fixture(scope="module")
def frames(world: hw.World) -> h3.Frames3:
    return h3.build_frames3(world)


# --------------------------------------------------------------------------- window and treatment


def test_history_block_excludes_2021_and_report_is_missing(
    world: hw.World, frames: h3.Frames3
) -> None:
    assert set(world.hist["games"]["season"].unique().to_list()) == {2019, 2020}
    assert set(frames.fh["season"].unique().to_list()) == {2019, 2020}
    ex = h3.treatment_exactness(frames)
    assert ex["pass"], ex
    assert ex["history_has_report_zero"] == ex["history_rows"] > 0
    # the current part of the extended frame is the W0 frame, row for row (evaluation features identical)
    assert frames.fx.filter(pl.col("season") >= 2022).equals(frames.f0)


def test_treatment_exactness_detects_a_report_that_leaks_into_history(frames: h3.Frames3) -> None:
    bad = frames.fh.with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(1)
        .otherwise(pl.col("has_report"))
        .alias("has_report")
    )
    fr = h3.Frames3(frames.f0, bad, frames.fx, frames.fx_w, frames.flags_h, frames.cur_start)
    assert not h3.treatment_exactness(fr)["pass"]


def test_planted_future_on_the_history_build(world: hw.World, frames: h3.Frames3) -> None:
    cutoff = world.hist["games"]["game_date"].sort()[len(world.hist["games"]) // 3]
    res = h3.planted_future(world, frames, cutoff=cutoff)
    assert res["pass"], res
    assert res["rows_compared"] > 100


def test_arm_specs_weights_and_knockout(frames: h3.Frames3) -> None:
    a0, n0 = h3.arm_spec3(frames, "A0", "pts")
    a1, n1 = h3.arm_spec3(frames, "A1", "pts")
    a2, n2 = h3.arm_spec3(frames, "A2", "pts")
    k0, nk0 = h3.arm_spec3(frames, "K0", "pts")
    k1, nk1 = h3.arm_spec3(frames, "K1", "pts")
    assert a0.height < a1.height == a2.height == k1.height and k0.height == a0.height
    assert n0 == n1 == n2 and "sample_w" not in a1.columns and "sample_w" in a2.columns
    assert nk0 == nk1 and not (set(nk0) & set(h3.REPORT_FEATURES))
    assert set(n0) - set(nk0) == {"has_report", "n_out_rot", "vac_min", "vac_pts"}
    w = a2["sample_w"].to_numpy()
    hist = a2["season"].is_in([2019, 2020]).to_numpy()
    assert np.all(w[hist] == h3.HIST_WEIGHT) and np.all(w[~hist] == 1.0)
    with pytest.raises(ValueError):
        h3.arm_spec3(frames, "A9", "pts")


def test_rows_gate_counts_history(frames: h3.Frames3) -> None:
    res = h3.rows_gate(frames)
    assert res["pts"]["history_rows"] > 0 and set(res["pts"]["history_rows_by_season"]) == {
        "2019",
        "2020",
    }


# --------------------------------------------------------------------------- missingness audit power


def _audit_frame(n_games: int = 400, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for s_i, season in enumerate((2019, 2020, 2022, 2023, 2024)):
        for g in range(n_games):
            for _p in range(10):
                rows.append(
                    (
                        f"{season}-{g}",
                        season,
                        "all",
                        float(rng.normal()) if rng.random() > 0.02 else None,
                        float(rng.normal()),
                        s_i,
                    )
                )
    d = pl.DataFrame(rows, schema=["game_id", "season", "stratum", "x", "y", "k"], orient="row")
    return d.drop("k")


def test_null_audit_quiet_on_exchangeable_seasons_and_loud_on_a_plant() -> None:
    d = _audit_frame()
    quiet = h3.null_audit(d, ["x", "y"], n_boot=300)
    assert quiet["flagged_history_cells"] == [] and quiet["flagged_placebo_cells"] == []
    rng = np.random.default_rng(1)
    kill = d["season"].is_in([2019, 2020]).to_numpy() & (rng.random(d.height) < 0.15)
    loud = h3.null_audit(
        d.with_columns(
            pl.when(pl.lit(pl.Series(kill))).then(None).otherwise(pl.col("y")).alias("y")
        ),
        ["x", "y"],
        n_boot=300,
    )
    assert any(c.startswith("y|all|2019") for c in loud["flagged_history_cells"])
    assert not any(c.startswith("x|") for c in loud["flagged_history_cells"])


def test_declared_columns_are_tabulated_not_flagged() -> None:
    d = _audit_frame()
    d = d.with_columns(
        pl.when(pl.col("season") < 2022).then(None).otherwise(pl.col("y")).alias("y")
    )
    res = h3.null_audit(d, ["x", "y"], declared=("y",), n_boot=200)
    assert res["flagged_history_cells"] == []
    assert any(
        r["column"] == "y" and r["declared"] and r["rate"]["2019"] == 1.0 for r in res["cells"]
    )


def test_threshold_is_noise_aware_not_below_placebo() -> None:
    """A history season that differs from the pool by no more than the current seasons differ from each other
    is not flagged (the HISTORY_WINDOW_2 failure mode)."""
    rng = np.random.default_rng(2)
    rows = []
    rates = {2019: 0.05, 2020: 0.05, 2022: 0.04, 2023: 0.06, 2024: 0.05}
    for season, r in rates.items():
        for g in range(300):
            for _ in range(10):
                rows.append((f"{season}-{g}", season, "all", None if rng.random() < r else 1.0))
    d = pl.DataFrame(rows, schema=["game_id", "season", "stratum", "z"], orient="row")
    res = h3.null_audit(d, ["z"], n_boot=300)
    assert res["flagged_history_cells"] == []


# --------------------------------------------------------------------------- era guard power


def test_indicator_auc_is_quiet_on_exchangeable_rows_and_loud_on_a_null_plant() -> None:
    rng = np.random.default_rng(3)

    def part(season: int, tag: str, null_share: float) -> pl.DataFrame:
        n = 6000
        x = rng.normal(size=n)
        x[rng.random(n) < null_share] = np.nan
        return pl.DataFrame(
            {
                "game_id": [f"{tag}{i // 10}" for i in range(n)],
                "season": [season] * n,
                "f1": x,
                "f2": rng.normal(size=n),
            }
        )

    quiet = h3.era_auc(
        part(2019, "a", 0.02), part(2022, "b", 0.02), ["f1", "f2"], indicator=True, n_boot=30
    )
    assert abs(quiet["auc"] - 0.5) < 0.03
    loud = h3.era_auc(
        part(2019, "a", 0.15), part(2022, "b", 0.0), ["f1", "f2"], indicator=True, n_boot=30
    )
    assert loud["lo"] > 0.5 + h3.MATERIAL / 2.0 and loud["auc"] > 0.55
    # values: distinguishable when the distributions differ
    a, b = part(2019, "a", 0.0), part(2022, "b", 0.0)
    b = b.with_columns(pl.col("f2") + 2.0)
    assert h3.era_auc(a, b, ["f2"], n_boot=30)["auc"] > 0.8


def test_static_cache_completes_missing_players(tmp_path: Path) -> None:
    st = pl.DataFrame({"player_id": [1], "position": ["Guard"], "draft_year": [2016]})
    pl.DataFrame(
        {"player_id": [2], "position": ["Center"], "draft_year": [None]},
        schema={"player_id": pl.Int64, "position": pl.Utf8, "draft_year": pl.Int64},
    ).write_parquet(tmp_path / "2.parquet")
    out = h3.static_with_cache(st, [1, 2, 3], tmp_path)
    assert sorted(out["player_id"].to_list()) == [1, 2]  # 3 has no cache file: stays missing
    assert out.filter(pl.col("player_id") == 2)["position"].item() == "Center"


# --------------------------------------------------------------------------- freeze refusals


def test_frozen_ok_refuses_drafts_and_bad_hashes(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"Status: **DRAFT** x\nbody\n{hw.MARKER}\nr\n")
    sha = hw.frozen_sha256(doc)
    with pytest.raises(SystemExit, match="DRAFT"):
        h3.frozen_ok(doc, sha)
    with pytest.raises(SystemExit, match="REFUSED"):
        h3.frozen_ok(doc, "0" * 64)
    doc.write_text(f"Status: FROZEN x\nbody\n{hw.MARKER}\nr\n")
    assert h3.frozen_ok(doc, hw.frozen_sha256(doc)) == hw.frozen_sha256(doc)
    with pytest.raises(SystemExit, match="REFUSED"):
        h3.main(["run", "--doc", str(doc), "--sha", "0" * 64])


def test_run_refuses_without_a_passing_gate(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="gate.json missing"):
        h3.require_gate3(tmp_path)
    (tmp_path / "gate.json").write_text('{"gate_pass": false, "failed_items": ["G5_era"]}')
    with pytest.raises(SystemExit, match="did not pass"):
        h3.require_gate3(tmp_path)
    (tmp_path / "audit.json").write_text("{}")
    (tmp_path / "gate.json").write_text('{"gate_pass": true, "audit_sha256": "' + "0" * 64 + '"}')
    with pytest.raises(SystemExit, match="no longer hashes"):
        h3.require_gate3(tmp_path)


# --------------------------------------------------------------------------- verdict logic


def _pv(sel: bool, rep: bool, harm: bool = False) -> dict[str, dict[str, Any]]:
    return {"2023": {"pass": sel, "harm": harm}, "2024": {"pass": rep, "harm": harm}}


def test_arm_verdict_requires_both_seasons_same_stat_and_the_knockout() -> None:
    ko_good = {"pts": {"2023": -0.004, "2024": -0.003}, "reb": {"2023": 0.0, "2024": 0.0}}
    assert (
        h3.arm_verdict3({"pts": _pv(True, True), "reb": _pv(False, False)}, ko_good)["status"]
        == "PASS"
    )
    assert (
        h3.arm_verdict3({"pts": _pv(True, False), "reb": _pv(False, True)}, ko_good)["status"]
        == "FAIL"
    )
    ko_bad = {"pts": {"2023": -0.004, "2024": -0.001}, "reb": {"2023": 0.0, "2024": 0.0}}
    assert (
        h3.arm_verdict3({"pts": _pv(True, True), "reb": _pv(False, False)}, ko_bad)["status"]
        == "FAIL_KNOCKOUT"
    )
    assert (
        h3.arm_verdict3({"pts": _pv(True, True, harm=True), "reb": _pv(False, False)}, ko_good)[
            "status"
        ]
        == "HARM"
    )


# --------------------------------------------------------------------------- arms end to end (synthetic)


def test_arms_run_on_identical_keys_and_analyze(frames: h3.Frames3, world: hw.World) -> None:
    run = h3.run_arms3(frames, CFG, stats=("pts",), progress=False)
    hashes = {k: v["key_hash"] for k, v in run["key_log"].items()}
    assert len(set(hashes.values())) == 1 and len(hashes) == len(h3.ARMS3)
    res = h3.analyze3(run, frames, world.static_all, stats=("pts",), n_boot=40)
    assert set(res["verdict"]) == set(h3.FAMILY3)
    assert "K1-K0|pts|2023" in res["knockout"]
    assert len(res["family"]["2023"]["tests"]) == 2
    rows = h3.draft_ledger_rows3(res, "a" * 64)
    assert len(rows) == 2 and "HISTORY_WINDOW_3" in rows[0]


def test_weights_change_the_fit_only_in_a2(frames: h3.Frames3) -> None:
    run = h3.run_arms3(frames, CFG, stats=("pts",), arms=("A0", "A1", "A2"), progress=False)
    s1, s2 = run["scores"][("A1", "pts")]["crps_int"], run["scores"][("A2", "pts")]["crps_int"]
    assert not np.allclose(s1, s2)
