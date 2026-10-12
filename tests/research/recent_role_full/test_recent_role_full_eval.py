# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore"
# ruff: noqa: E501
"""RECENT_ROLE_FULL harness: freeze and holdout refusals, the heads walk equals the WORKLOAD/production construction, a shared-permutation shuffle
that can fail, the walk's as-of property, the decision rule failing each way."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.eval import context_residual_eval as CRE
from nba.props.context_residual import ContextResidualConfig
from research.eval import recent_role_full_eval as E

CFG = ContextResidualConfig(n_estimators=15)


def _doc(tmp_path: Path, body: str = "rule\n") -> tuple[Path, str]:
    p = tmp_path / "d.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\nx\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def test_require_frozen_needs_matching_hash_and_the_prereg_commit(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    good = lambda *a: f"abc\tprereg(RECENT_ROLE_FULL): {sha}\n"  # noqa: E731
    other = lambda *a: f"abc\tprereg(PI_RECENT_ROLE): {sha}\n"  # noqa: E731  (another id's subject must not freeze this one)
    assert E.require_frozen(sha, p, good) == sha
    with pytest.raises(RuntimeError, match="no commit"):
        E.require_frozen(sha, p, other)
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        E.require_frozen("b" * 64, p, good)
    with pytest.raises(RuntimeError, match="required"):
        E.require_frozen(None, p, good)
    p.write_text(p.read_text().replace("rule", "rule edited"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        E.require_frozen(sha, p, good)


def test_holdout_refuses_without_a_committed_confirmed_row(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    log = tmp_path / "log.md"
    frozen = lambda *a: f"abc\tprereg(RECENT_ROLE_FULL): {sha}\n"  # noqa: E731
    log.write_text("| nothing |\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        E.require_holdout_row(sha, log, frozen, p)
    log.write_text(
        f"| 2026-10-12 | RECENT_ROLE_FULL {sha[:8]} PRE-REGISTERED |\n"
    )  # no DEVIN CONFIRMED
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        E.require_holdout_row(sha, log, frozen, p)
    log.write_text(f"| 2026-10-12 | RECENT_ROLE_FULL {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n")

    def dirty(*a: str) -> str:
        return " M log.md\n" if a[0] == "status" else frozen(*a)

    with pytest.raises(RuntimeError, match="uncommitted"):
        E.require_holdout_row(sha, log, dirty, p)

    def uncommitted(*a: str) -> str:
        if a[0] == "status":
            return ""
        if a[0] == "log" and not a[1].startswith("--format=%H%x09"):
            return ""
        return frozen(*a)

    with pytest.raises(RuntimeError, match="not in any commit"):
        E.require_holdout_row(sha, log, uncommitted, p)

    def ok(*a: str) -> str:
        if a[0] == "status":
            return ""
        if a[0] == "log" and not a[1].startswith("--format=%H%x09"):
            return "deadbeef\n"
        return frozen(*a)

    assert "PRE-REGISTERED" in E.require_holdout_row(sha, log, ok, p)


def test_old_holdout_stub_is_gone_and_build_frame_has_no_2025_literal() -> None:
    assert not hasattr(E, "holdout") and not hasattr(E, "load_2025")
    assert 2025 not in E.build_frame.__code__.co_consts
    assert "2025" not in " ".join(E.build_frame.__code__.co_names)


def _frame(seed: int = 0, signal: bool = True) -> pl.DataFrame:
    """Synthetic played-row frame for the heads walk: pts depend on `x` and, when `signal`, on the recent-role column `z`."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    d0 = dt.date(2022, 11, 1)
    pid = 0
    for day in range(0, 720, 3):
        d = d0 + dt.timedelta(days=day)
        season = d.year if d.month >= 8 else d.year - 1
        for _ in range(40):
            pid += 1
            x, z = rng.normal(), rng.normal()
            pts = 10.0 + 1.0 * x + (4.0 * z if signal else 0.0) + rng.normal()
            rows.append(
                {
                    "game_id": f"g{pid // 40:06d}", "player_id": pid, "team_id": 1, "season": season, "game_date": d,
                    "n_prior": 20, "minutes": 25.0, "min10": 25.0, "min40": 25.0, "starter10": 0.5, "starter_act": 0.0,
                    "abs_margin": 3.0, "final_margin": 4.0, "b2b": 0.0, "gap_days": 2.0, "since_flag": 5.0,
                    "pts": pts, "m10_pts": 10.0, "sd10_pts": 2.0, "x": x, "z": z,
                }
            )  # fmt: skip
    return pl.DataFrame(rows)


def _heads(fr: pl.DataFrame, names: list[str], **kw: Any) -> dict[str, Any]:
    return E.collect_heads(fr, "pts", CFG, names, min_train=1500, log=False, **kw)


def test_heads_walk_equals_the_workload_construction_without_shuffle() -> None:
    """Equivalence proof: with shuffle off the walk is bit-identical to workload_eval.collect_components (itself the production construction)."""
    fr = _frame()
    mine = _heads(fr, ["x", "z"])
    ref = E.WE.collect_components(fr, "pts", CFG, ["x", "z"])
    for k in ("row_c", "row_s", "row_y", "row_blk", "cal_z", "cal_c", "cal_s", "cal_blk"):
        assert np.array_equal(mine[k], ref[k]), k
    assert np.array_equal(mine["row_gid"].astype(str), ref["row_gid"].astype(str))
    stored = {k: v for k, v in ref.items()}
    chk = E.heads_vs_stored(mine, stored)
    assert chk["rows_equal"] and chk["max_abs_diff"] == 0.0
    stored["row_c"] = stored["row_c"] + 1e-3
    assert E.heads_vs_stored(mine, stored)["max_abs_diff"] > 5e-4


def test_shuffle_uses_one_shared_permutation_and_real_residuals_do_not_fit() -> None:
    fr = _frame()
    a = _heads(fr, ["x"], shuffle=True)
    b = _heads(fr, ["x", "z"], shuffle=True)
    r = _heads(fr, ["x"], shuffle=False)
    assert list(a["label_hashes"]) == list(b["label_hashes"]) and len(set(a["label_hashes"])) > 1
    assert list(a["label_hashes"]) != list(r["label_hashes"])  # residuals really were permuted
    assert np.array_equal(a["row_y"], r["row_y"])  # scored on the real outcome


def _abs_err_row(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Candidate b minus control a on |y - centre| per row (a stand-in for CRPS on the synthetic frame)."""
    d = np.abs(b["row_y"] - b["row_c"]) - np.abs(a["row_y"] - a["row_c"])
    return {"season": 2023, **E.WE.delta_stats(d, a["row_gid"])}


def test_shuffle_check_passes_when_labels_are_shuffled_and_fails_when_they_are_not() -> None:
    """The harness's own check can fail: with a planted signal in z, unpermuted residuals (the old defect) make FULL beat CTRL and the check FAILS."""
    fr = _frame()
    for permute, want_ok in ((True, True), (False, False)):
        a = _heads(fr, ["x"], shuffle=True, permute=permute)
        b = _heads(fr, ["x", "z"], shuffle=True, permute=permute)
        row = _abs_err_row(a, b)
        assert E.PR.shuffle_check(row)["ok"] == want_ok, (permute, row)


def test_heads_walk_is_as_of_planted_future_and_non_inert_control() -> None:
    fr = _frame()
    cut = dt.date(2024, 2, 15)
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(99.0).otherwise(pl.col("pts")).alias("pts"),
        pl.when(late).then(9.0).otherwise(pl.col("z")).alias("z"),
    )
    a, b = _heads(fr, ["x", "z"]), _heads(edited, ["x", "z"])
    ja = pl.DataFrame({"g": a["row_gid"], "p": a["row_pid"], "ca": a["row_c"]})
    jb = pl.DataFrame({"g": b["row_gid"], "p": b["row_pid"], "cb": b["row_c"]})
    date = fr.select(pl.col("game_id").alias("g"), pl.col("player_id").alias("p"), "game_date")
    j = ja.join(jb, on=["g", "p"]).join(date, on=["g", "p"])
    pre = j.filter(pl.col("game_date") < dt.date(2024, 2, 1))
    nxt = j.filter(pl.col("game_date") >= dt.date(2024, 3, 1))
    assert pre.height > 100 and nxt.height > 100
    assert float(np.abs(pre["ca"].to_numpy() - pre["cb"].to_numpy()).max()) == 0.0
    assert float(np.abs(nxt["ca"].to_numpy() - nxt["cb"].to_numpy()).max()) > 0.0


def _rows(
    d23: float,
    d24: float,
    shuf: float = 0.0,
    plc: float = -0.01,
    p: float = 0.001,
    cov_worse: float = 0.0,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for st in E.STATS:
        for s, d in ((2023, d23), (2024, d24)):
            base = {"stat": st, "season": s, "n_rows": 100, "n_games": 10}
            for arm, dd, pp in (
                ("FULL_minus_A0", d, p),
                ("FULL_minus_PL_FULL", plc, 0.1),
                ("SHUF_FULL_minus_SHUF_CTRL", shuf, 0.5),
            ):
                rows.append({**base, "arm": arm, "delta": dd, "ci_lo": dd - 0.002, "ci_hi": dd + 0.002, "p": pp, "mde": 0.002})  # fmt: skip
            slices.append({"stat": st, "season": s, "slice": "min<5", "n": 500, "delta": -0.05})
        a0 = {str(s): {f"{t:.2f}": t for t in E.COV_TAUS} for s in E.SEASONS}
        full = {str(s): {f"{t:.2f}": t + cov_worse for t in E.COV_TAUS} for s in E.SEASONS}
        coverage[st] = {"A0": a0, "FULL": full}
    E.add_bh(rows)
    pi_rows = [{"season": s, "arm": "FULL_minus_A0", "ece_delta": 0.0} for s in E.SEASONS]
    return {"rows": rows, "slices": slices, "pi_rows": pi_rows, "coverage": coverage}


def test_decide_pass_and_each_way_to_fail() -> None:
    assert E.decide(_rows(-0.02, -0.02), True)["any_pass"]
    assert not E.decide(_rows(-0.02, -0.02), False)["any_pass"]  # gate
    assert not E.decide(_rows(-0.02, -0.003), True)["any_pass"]  # report season below the floor
    assert not E.decide(_rows(-0.003, -0.02), True)["any_pass"]  # select season below the floor
    assert not E.decide(_rows(-0.02, -0.02, plc=-0.001), True)["any_pass"]  # placebo
    assert not E.decide(_rows(-0.02, -0.02, shuf=-0.004), True)[
        "any_pass"
    ]  # shuffle manufactures a gain
    assert not E.decide(_rows(-0.02, -0.02, p=0.9), True)["any_pass"]  # BH
    assert not E.decide(_rows(-0.02, -0.02, cov_worse=0.02), True)[
        "any_pass"
    ]  # calibration of the forecast
    assert E.decide(_rows(-0.02, -0.02, cov_worse=0.005), True)[
        "any_pass"
    ]  # inside the coverage tolerance
    r = _rows(-0.02, -0.02)
    r["pi_rows"][0]["ece_delta"] = 0.01
    assert not E.decide(r, True)["any_pass"]  # pi calibration
    r = _rows(-0.02, -0.02)
    r["slices"][0]["delta"] = 0.02
    v = E.decide(r, True)
    assert (
        not v["per_stat"][E.STATS[0]]["pass"] and v["per_stat"][E.STATS[1]]["pass"]
    )  # slice guard is per stat
    r = _rows(-0.02, -0.02)
    r["slices"][0]["n"] = 100  # a slice under 300 cannot fail the guard
    r["slices"][0]["delta"] = 0.5
    assert E.decide(r, True)["any_pass"]


def test_floor_is_the_house_floor_and_arms_are_declared() -> None:
    assert E.FLOOR == -0.005 and E.PLACEBO_MARGIN == -0.003 and E.SLICE_TOL == 0.01
    assert (
        ("FULL", "A0") in E.CONTRASTS
        and ("HEADS", "A0") in E.CONTRASTS
        and ("PI", "A0") in E.CONTRASTS
    )
    assert set(E.ARMS_HEADS) == {"R", "PL", "SHUF_CTRL", "SHUF_R"} and set(E.ARMS_PI) == set(
        E.ARMS_HEADS
    )
    assert E.R_COLS == ("wl_min_last", "wl_mean_min_l3", "wl_min_3d", "wl_min_7d", "wl_mean_min_l5")
    assert (
        E.heads_names("pts", "R")[-5:] == list(E.R_COLS)
        and E.heads_names("pts", "PL")[-1] == "pl_wl_mean_min_l5"
    )


def test_checkpoint_older_than_freeze_is_refused(tmp_path: Path) -> None:
    import os

    import pytest

    from research.eval.recent_role_full_eval import check_checkpoint

    p = tmp_path / "arm.npz"
    assert check_checkpoint(p, 1000.0) is False
    p.write_bytes(b"x")
    os.utime(p, (500.0, 500.0))
    with pytest.raises(RuntimeError, match="predates the freeze"):
        check_checkpoint(p, 1000.0)
    os.utime(p, (2000.0, 2000.0))
    assert check_checkpoint(p, 1000.0) is True
    assert check_checkpoint(p, None) is True


# --------------------------------------------------------------------------- Stage 2 (fakes only; no 2025 data, no DB)
SHA = "73f3f4b04754ec9a8674b050d0b492116376dd03c513b9338ddabb7146355235"


def _ok_git(*a: str) -> str:
    if a[0] == "status":
        return ""
    if a[0] == "log" and not a[1].startswith("--format=%H%x09"):
        return "deadbeef\n"
    return f"abc\tprereg(RECENT_ROLE_FULL): {SHA}\n"


def _good_doc_and_log(tmp_path: Path) -> tuple[Path, Path, str]:
    doc, sha = _doc(tmp_path)
    log = tmp_path / "log.md"
    log.write_text(f"| 2026-10-12 | RECENT_ROLE_FULL {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n")

    def git(*a: str) -> str:
        if a[0] == "status":
            return ""
        if a[0] == "log" and not a[1].startswith("--format=%H%x09"):
            return "deadbeef\n"
        return f"abc\tprereg(RECENT_ROLE_FULL): {sha}\n"

    return doc, log, sha


class _Spy:
    """Records every build / pipeline call with the value of the season cap at that moment."""

    def __init__(self) -> None:
        self.builds: list[int] = []
        self.pipes: list[tuple[int, tuple[str, ...], int]] = []


def _fake_res(
    season: int, pi_shift: float = 0.0, wq_shift: float = 0.0, delta: float = -0.008314
) -> dict[str, Any]:
    n = 6
    heads = {
        "row_gid": np.array([f"g{i // 2}" for i in range(n)]), "row_pid": np.arange(n), "row_y": np.arange(n, dtype=float),
        "row_c": np.linspace(1, 2, n), "row_s": np.ones(n), "cal_z": np.zeros(4), "cal_c": np.ones(4), "cal_s": np.ones(4),
    }  # fmt: skip
    pic = {
        "row_gid": heads["row_gid"].copy(), "row_pid": heads["row_pid"].copy(), "row_pi": np.full(n, 0.2) + pi_shift,
        "wq_pts": np.full((2, 3), 0.5) + wq_shift,
    }  # fmt: skip
    base = np.full(n, 3.0)
    return {"season": season, "heads": {"A0": heads}, "pic": pic, "pis": {"A0": pic["row_pi"]},
            "scores": {"A0": base, "FULL": base + delta, "PL_FULL": base + delta + 0.0086}}  # fmt: skip


def _fake_ref() -> dict[str, Any]:
    r = _fake_res(2024)
    return {
        "comp": r["heads"]["A0"],
        "pic": r["pic"],
        "full_minus_a0": -0.008314476,
        "full_minus_pl": -0.0086,
        "a0_crps": 3.0,
    }


def _fake_sc(delta: float = -0.007, hi: float = -0.003, **over: Any) -> dict[str, Any]:
    base = {"n_rows": 27000, "n_games": 1300}
    cov = {t: float(t) for t in ("0.10", "0.20", "0.80", "0.90")}
    sc: dict[str, Any] = {
        "season": 2025,
        "rows": [
            {"arm": "A0", **base, "crps_int": 3.1}, {"arm": "FULL", **base, "crps_int": 3.09},
            {"arm": "FULL_minus_A0", **base, "delta": delta, "ci_lo": delta - 0.004, "ci_hi": hi, "p": 0.001, "p_bh": 0.001, "mde": 0.004},
            {"arm": "FULL_minus_PL_FULL", **base, "delta": -0.007, "ci_lo": -0.01, "ci_hi": -0.004, "p": 0.001, "mde": 0.003},
            {"arm": "SHUF_FULL_minus_SHUF_CTRL", **base, "delta": 0.0, "ci_lo": -0.002, "ci_hi": 0.002, "p": 0.9, "mde": 0.002},
        ],
        "slices": [{"slice": "all", "n": 27000, "delta": delta, "ci_lo": -0.01, "ci_hi": -0.002}, {"slice": "bench", "n": 15000, "delta": -0.005, "ci_lo": -0.01, "ci_hi": 0.0}],
        "coverage": {"A0": {"2025": dict(cov)}, "FULL": {"2025": dict(cov)}},
        "pi": {"A0": {"ece": 0.01}, "FULL": {"ece": 0.01}, "ece_delta": 0.0},
        "observed_short": 0.1,
    }  # fmt: skip
    sc.update(over)
    return sc


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    spy: _Spy,
    mismatch_season: tuple[int, str] | None = None,
    data_ok: bool = True,
) -> None:
    """Fake builder / pipeline / reference / scorer; the REAL season_cap, reproduction_checks, decide and body are used."""
    monkeypatch.setattr(E, "STAGE2_DIR", tmp_path / "stage2")
    monkeypatch.setattr(E, "STAGE2_REPORT", tmp_path / "stage2.md")
    monkeypatch.setattr(E, "stored_reference", _fake_ref)
    monkeypatch.setattr(E, "arm_scores", lambda res: res["scores"])

    def build() -> tuple[Any, Any, dict[str, Any]]:
        spy.builds.append(E.MAX_SEASON)
        return "frame", "cfg", {}

    def pipe(
        fr: Any, cfg: Any, season: int, guards: tuple[str, ...] = ("PL", "SHUF"), log: bool = True
    ) -> dict[str, Any]:
        spy.pipes.append((season, guards, E.MAX_SEASON))
        bad = mismatch_season is not None and mismatch_season[0] == len(spy.pipes)
        return _fake_res(season, pi_shift=1e-12 if bad else 0.0)

    monkeypatch.setattr(E, "build_frame", build)
    monkeypatch.setattr(E, "stage2_pipeline", pipe)
    monkeypatch.setattr(
        E, "data_gate_2025", lambda fr, season: [E.WE._check("data", {}, "x", data_ok)]
    )
    monkeypatch.setattr(E, "stage2_scores", lambda res: _fake_sc())
    monkeypatch.setattr("research.eval.recent_role_holdout.preimport", lambda: None)


def test_stage2_refuses_without_the_committed_confirmed_row_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, _, sha = _good_doc_and_log(tmp_path)
    spy = _Spy()
    _wire(monkeypatch, tmp_path, spy)
    log = tmp_path / "empty.md"
    log.write_text("| nothing |\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        E.stage2(sha, _ok_git_for(sha), doc, log)
    assert spy.builds == [] and spy.pipes == [] and E.MAX_SEASON == 2024


def _ok_git_for(sha: str) -> Any:
    def git(*a: str) -> str:
        if a[0] == "status":
            return ""
        if a[0] == "log" and not a[1].startswith("--format=%H%x09"):
            return "deadbeef\n"
        return f"abc\tprereg(RECENT_ROLE_FULL): {sha}\n"

    return git


def test_stage2_refuses_when_a_scored_results_file_or_started_touch_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, log, sha = _good_doc_and_log(tmp_path)
    spy = _Spy()
    _wire(monkeypatch, tmp_path, spy)
    out = tmp_path / "stage2"
    out.mkdir()
    for name in ("results.json", "touch_started.json"):
        (out / name).write_text("{}")
        with pytest.raises(RuntimeError, match="touch is spent"):
            E.stage2(sha, _ok_git_for(sha), doc, log)
        (out / name).unlink()
    (out / "gate_failed.json").write_text(
        "{}"
    )  # a gate stop scored nothing: it must NOT block a rerun
    r = E.stage2(sha, _ok_git_for(sha), doc, log)
    assert r["gate_ok"] and spy.pipes[-1][0] == 2025


def test_stage2_cap_is_raised_only_after_both_refusals_and_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, log, sha = _good_doc_and_log(tmp_path)
    spy = _Spy()
    _wire(monkeypatch, tmp_path, spy)
    assert E.MAX_SEASON == 2024
    r = E.stage2(sha, _ok_git_for(sha), doc, log)
    assert E.MAX_SEASON == 2024 and CRE.MAX_SEASON == 2024  # restored
    # build 1 (gate A) under the repo cap, build 2 (gate B + 2025) under 2025
    assert spy.builds == [2024, 2025]
    # pipelines: gate A 2024 (cap 2024), gate B 2024 (cap 2025), the one 2025 walk (cap 2025)
    assert [(s, c) for s, _, c in spy.pipes] == [(2024, 2024), (2024, 2025), (2025, 2025)]
    assert (
        spy.pipes[2][1] == ("PL", "SHUF") and spy.pipes[0][1] == ("PL",) and spy.pipes[1][1] == ()
    )
    assert r["gate_ok"] and (tmp_path / "stage2" / "results.json").exists()
    exp = tmp_path / "stage2" / "expected.json"
    assert exp.exists() and '"expected_delta_pts_2025": -0.007' in exp.read_text()
    assert r["expected"]["pass_probability"] == [0.75, 0.82]
    # a second call is refused: one run, no reruns
    with pytest.raises(RuntimeError, match="touch is spent"):
        E.stage2(sha, _ok_git_for(sha), doc, log)


def test_stage2_cap_restored_when_the_2025_walk_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, log, sha = _good_doc_and_log(tmp_path)
    spy = _Spy()
    _wire(monkeypatch, tmp_path, spy)
    inner = E.stage2_pipeline

    def boom(
        fr: Any, cfg: Any, season: int, guards: tuple[str, ...] = (), log: bool = True
    ) -> dict[str, Any]:
        if season == 2025:
            assert E.MAX_SEASON == 2025
            raise MemoryError("planted")
        return inner(fr, cfg, season, guards, log)

    monkeypatch.setattr(E, "stage2_pipeline", boom)
    with pytest.raises(MemoryError):
        E.stage2(sha, _ok_git_for(sha), doc, log)
    assert E.MAX_SEASON == 2024 and CRE.MAX_SEASON == 2024
    assert (
        tmp_path / "stage2" / "touch_started.json"
    ).exists()  # it had started: a rerun is refused until Devin decides
    with pytest.raises(RuntimeError, match="touch is spent"):
        E.stage2(sha, _ok_git_for(sha), doc, log)


@pytest.mark.parametrize("which", [1, 2])
def test_planted_reproduction_mismatch_stops_before_any_2025_row_is_fitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: int
) -> None:
    """A 1e-12 drift in the A0 pi at gate A (pipeline call 1) or gate B (call 2) stops the run: no 2025 pipeline, no touch marker, no results."""
    doc, log, sha = _good_doc_and_log(tmp_path)
    spy = _Spy()
    _wire(monkeypatch, tmp_path, spy, mismatch_season=(which, "pi"))
    r = E.stage2(sha, _ok_git_for(sha), doc, log)
    assert r["gate_ok"] is False and r["touch_consumed"] is False
    assert all(s == 2024 for s, _, _ in spy.pipes) and len(spy.pipes) == which
    out = tmp_path / "stage2"
    assert (out / "gate_failed.json").exists()
    assert (
        not (out / "results.json").exists()
        and not (out / "touch_started.json").exists()
        and not (out / "expected.json").exists()
    )
    assert E.MAX_SEASON == 2024
    if which == 1:
        assert spy.builds == [2024]  # the cap was never raised at all


def test_2025_data_gate_failure_stops_without_fitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, log, sha = _good_doc_and_log(tmp_path)
    spy = _Spy()
    _wire(monkeypatch, tmp_path, spy, data_ok=False)
    r = E.stage2(sha, _ok_git_for(sha), doc, log)
    assert r["gate_ok"] is False and all(s == 2024 for s, _, _ in spy.pipes)
    assert not (tmp_path / "stage2" / "touch_started.json").exists() and E.MAX_SEASON == 2024


def test_held_out_body_needs_a_sha() -> None:
    with pytest.raises(RuntimeError, match="needs the frozen --sha"):
        E._stage2_body(None, 2025, lambda: None, Path("/nonexistent"), lambda: None)  # type: ignore[arg-type,return-value]


def test_reproduction_checks_each_one_fails_on_its_planted_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(E, "arm_scores", lambda res: res["scores"])
    ref = _fake_ref()

    def failed(res: dict[str, Any], with_pl: bool = True) -> list[str]:
        return [t["test"] for t in E.reproduction_checks(res, ref, with_pl, "x") if not t["ok"]]

    assert failed(_fake_res(2024)) == []
    bad = _fake_res(2024)
    bad["heads"]["A0"]["row_c"] = bad["heads"]["A0"]["row_c"] + 1e-12
    assert any("A0 heads" in t for t in failed(bad))
    assert any("A0 pi" in t for t in failed(_fake_res(2024, pi_shift=1e-12)))
    assert any("w_q" in t for t in failed(_fake_res(2024, wq_shift=1e-5)))
    assert failed(_fake_res(2024, wq_shift=1e-7)) == []  # inside the 1e-6 tolerance
    assert any("T467" in t for t in failed(_fake_res(2024, delta=-0.0085)))
    assert failed(_fake_res(2024, delta=-0.008314 + 9e-7)) == []
    plc = _fake_res(2024)
    plc["scores"]["PL_FULL"] = plc["scores"]["PL_FULL"] + 1e-3
    assert any("placebo" in t for t in failed(plc)) and not failed(plc, with_pl=False)


def test_restrict_stored_picks_the_season_blocks_and_their_wq_rows() -> None:
    comp = {
        "row_season": np.array([2023, 2023, 2024, 2024, 2024]), "row_blk": np.array([0, 1, 2, 2, 3]), "row_gid": np.array(list("abcde")),
        "cal_blk": np.array([0, 1, 1, 2, 3, 3]), "cal_z": np.arange(6.0),
    }  # fmt: skip
    pic = {
        "row_gid": np.array(list("abcde")),
        "row_pid": np.arange(5),
        "row_pi": np.arange(5.0),
        "wq_pts": np.arange(4.0)[:, None] * np.ones((1, 2)),
    }
    c, p = E.restrict_stored(comp, pic, 2024)
    assert list(c["row_gid"]) == ["c", "d", "e"] and list(c["cal_z"]) == [3.0, 4.0, 5.0]
    assert list(p["wq_pts"][:, 0]) == [2.0, 3.0] and list(p["row_pi"]) == [2.0, 3.0, 4.0]
    with pytest.raises(RuntimeError, match="w_q rows"):
        E.restrict_stored(comp, {**pic, "wq_pts": np.zeros((3, 2))}, 2024)


def test_stage2_pipeline_never_reads_a_stored_file() -> None:
    """w_q and the A0 pi come from production's collect_pi on the live walk; the pipeline has no path to a stored component or npz."""
    names = set(E.stage2_pipeline.__code__.co_names)
    assert "collect_pi" in names and not names & {
        "load_components",
        "load",
        "LT_DIR",
        "stored_reference",
    }


def test_decide_stage2_pass_and_each_way_to_fail() -> None:
    assert E.decide_stage2(_fake_sc(), True)["pass"]
    assert not E.decide_stage2(_fake_sc(), False)["pass"]
    assert not E.decide_stage2(_fake_sc(delta=-0.0049, hi=-0.001), True)["pass"]  # above the floor
    assert not E.decide_stage2(_fake_sc(delta=-0.007, hi=0.001), True)["pass"]  # CI upper >= 0
    sc = _fake_sc()
    sc["rows"][2]["p_bh"] = 0.2
    assert not E.decide_stage2(sc, True)["pass"]
    sc = _fake_sc()
    sc["rows"][3]["delta"] = -0.002  # placebo margin missed
    assert not E.decide_stage2(sc, True)["pass"]
    sc = _fake_sc()
    sc["rows"][4].update(delta=-0.002, ci_hi=-0.0005)  # shuffled labels still win
    d = E.decide_stage2(sc, True)
    assert not d["pass"] and not d["shuffle_ok"]
    sc = _fake_sc()
    sc["slices"][1]["delta"] = 0.02
    assert not E.decide_stage2(sc, True)["pass"]
    sc = _fake_sc()
    sc["slices"][1].update(n=100, delta=0.5)  # under 300 rows cannot fail
    assert E.decide_stage2(sc, True)["pass"]
    sc = _fake_sc()
    sc["coverage"]["FULL"]["2025"]["0.10"] = 0.13
    assert not E.decide_stage2(sc, True)["pass"]
    sc = _fake_sc()
    sc["pi"]["ece_delta"] = 0.006
    assert not E.decide_stage2(sc, True)["pass"]
    sc = _fake_sc()
    sc["rows"] = [r for r in sc["rows"] if r["arm"] != "FULL_minus_PL_FULL"]
    assert not E.decide_stage2(sc, True)["pass"]  # a missing guard arm is not a pass
