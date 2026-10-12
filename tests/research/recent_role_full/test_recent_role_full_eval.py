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


def test_holdout_command_checks_the_row_first_and_has_no_2025_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(E, "require_holdout_row", lambda sha: called.append(sha) or "row")
    monkeypatch.setattr(E, "OUT_DIR", Path("/nonexistent_dir_for_test"))
    with pytest.raises(FileNotFoundError):  # results.json is read only after the row check
        E.holdout("x")
    assert called == ["x"]
    assert not hasattr(E, "load_2025")
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
