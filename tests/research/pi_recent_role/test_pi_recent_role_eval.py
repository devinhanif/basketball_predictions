# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore"
# ruff: noqa: E501
"""PI_RECENT_ROLE harness: freeze and holdout refusals, a shared-permutation shuffle that can fail, the walk's as-of property, the decision rule."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import pi_recent_role_eval as E

CFG = ContextResidualConfig()


def _doc(tmp_path: Path, body: str = "rule\n") -> tuple[Path, str]:
    p = tmp_path / "d.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\nx\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def test_require_frozen_needs_matching_hash_and_the_prereg_commit(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    good = lambda *a: f"abc\tprereg(PI_RECENT_ROLE): {sha}\n"  # noqa: E731
    none = lambda *a: "abc\tprereg(RECENT_ROLE): " + sha + "\n"  # noqa: E731  (another id's subject must not freeze this one)
    assert E.require_frozen(sha, p, good) == sha
    with pytest.raises(RuntimeError, match="no commit"):
        E.require_frozen(sha, p, none)
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
    frozen = lambda *a: f"abc\tprereg(PI_RECENT_ROLE): {sha}\n"  # noqa: E731
    log.write_text("| nothing |\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        E.require_holdout_row(sha, log, frozen, p)
    log.write_text(
        f"| 2026-10-12 | PI_RECENT_ROLE {sha[:8]} PRE-REGISTERED |\n"
    )  # no DEVIN CONFIRMED
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        E.require_holdout_row(sha, log, frozen, p)
    log.write_text(f"| 2026-10-12 | PI_RECENT_ROLE {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n")

    def dirty(*a: str) -> str:
        if a[0] == "status":
            return " M log.md\n"
        return frozen(*a)

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


def test_holdout_command_checks_the_row_first_and_loads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(E, "require_holdout_row", lambda sha: called.append(sha) or "row")
    monkeypatch.setattr(E, "OUT_DIR", Path("/nonexistent_dir_for_test"))
    with pytest.raises(
        FileNotFoundError
    ):  # results.json is read only after the row check, and no 2025 loader exists
        E.holdout("x")
    assert called == ["x"]
    assert not hasattr(E, "load_2025") and "2025" not in " ".join(E.build_frame.__code__.co_names)


def _frame(seed: int = 0, signal: bool = True) -> pl.DataFrame:
    """Synthetic played-row frame. Short games are driven by `z` (the 'recent role' column) and `x`."""
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
            p_short = 1 / (1 + np.exp(-(-2.2 + 0.3 * x + (1.6 * z if signal else 0.0))))
            short = rng.random() < p_short
            m10 = 20.0 + rng.normal()
            rows.append(
                {
                    "game_id": f"g{pid // 40:06d}",
                    "player_id": pid,
                    "team_id": 1,
                    "season": season,
                    "game_date": d,
                    "n_prior": 20,
                    "minutes": 5.0 if short else 22.0,
                    "min10": m10,
                    "pts": 5.0,
                    "m10_pts": 5.0,
                    "sd10_pts": 2.0,
                    "x": x,
                    "z": z,
                }
            )
    return pl.DataFrame(rows)


def _walk(fr: pl.DataFrame, cols: list[str], **kw: Any) -> dict[str, Any]:
    return E.walk_pi(fr, cols, CFG, min_train=300, n_estimators=15, log=False, **kw)


def _llrow(a: dict[str, Any], b: dict[str, Any], season: int) -> dict[str, Any]:
    m = (
        a["season"] >= 0
    )  # pooled over seasons: the synthetic seasons are small and the shuffled fit noise is large relative to the tolerance
    y = a["short"][m].astype(float)
    d = E.WE._ll(b["pi"][m], y) - E.WE._ll(a["pi"][m], y)  # candidate b minus control a
    return {"season": season, **E.WE.delta_stats(d, a["gid"][m])}


def test_shuffle_uses_one_shared_permutation_and_real_labels_do_not_fit() -> None:
    fr = _frame()
    a = _walk(fr, ["x"], shuffle=True)
    b = _walk(fr, ["x", "z"], shuffle=True)
    r = _walk(fr, ["x"], shuffle=False)
    assert a["label_hashes"] == b["label_hashes"] and len(set(a["label_hashes"])) > 1
    assert a["label_hashes"] != r["label_hashes"]  # labels really were permuted
    assert np.array_equal(a["short"], r["short"])  # scored on the real flag


def test_shuffle_check_passes_when_labels_are_shuffled_and_fails_when_they_are_not() -> None:
    """The harness's own check can fail: with the planted signal in z, unpermuted labels (the old defect) make CAND beat CTRL and the check FAILS."""
    fr = _frame()
    for permute, want_ok in ((True, True), (False, False)):
        a = _walk(fr, ["x"], shuffle=True, permute=permute)
        b = _walk(fr, ["x", "z"], shuffle=True, permute=permute)
        checks = [E.shuffle_check(_llrow(a, b, 2023))]
        assert checks and all(c["ok"] for c in checks) == want_ok, (permute, checks)


def test_walk_is_as_of_planted_future_and_non_inert_control() -> None:
    fr = _frame()
    cut = dt.date(2024, 2, 15)
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
        pl.when(late).then(9.0).otherwise(pl.col("z")).alias("z"),
    )
    a, b = _walk(fr, ["x", "z"]), _walk(edited, ["x", "z"])
    ja = pl.DataFrame({"game_id": a["gid"], "player_id": a["pid"], "pa": a["pi"]})
    jb = pl.DataFrame({"game_id": b["gid"], "player_id": b["pid"], "pb": b["pi"]})
    j = ja.join(jb, on=["game_id", "player_id"]).join(
        fr.select("game_id", "player_id", "game_date"), on=["game_id", "player_id"]
    )
    pre = j.filter(pl.col("game_date") < cut)
    nxt = j.filter(pl.col("game_date") >= dt.date(2024, 3, 1))
    assert pre.height > 100 and nxt.height > 100
    assert float(np.abs(pre["pa"].to_numpy() - pre["pb"].to_numpy()).max()) == 0.0
    assert float(np.abs(nxt["pa"].to_numpy() - nxt["pb"].to_numpy()).max()) > 0.0


def test_control_walk_equals_production_collect_pi_construction() -> None:
    """On a frame with the stat columns, the walk's pi equals lower_tail_eval.collect_pi's for the same feature list (same classifier, same blocks)."""
    from nba.eval import lower_tail_eval as LT
    from nba.props import context_residual as CR

    fr = _frame()
    for st in CR.PROP_STATS:
        fr = (
            fr.with_columns(
                pl.col("pts").alias(st),
                pl.col("m10_pts").alias(f"m10_{st}"),
                pl.col("sd10_pts").alias(f"sd10_{st}"),
            )
            if st != "pts"
            else fr
        )
    old = CR.COMMON_FEATURES
    try:
        LT.COMMON_FEATURES = ("x",)  # type: ignore[assignment]
        cfg = ContextResidualConfig(n_estimators=15)
        ref = LT.collect_pi(fr, cfg, stats=("pts",))
    finally:
        LT.COMMON_FEATURES = old
    mine = E.walk_pi(fr, ["x"], cfg, min_train=1500, log=False)
    assert np.array_equal(ref["row_gid"], mine["gid"]) and np.array_equal(
        ref["row_pid"], mine["pid"]
    )
    assert np.abs(ref["row_pi"] - mine["pi"]).max() == 0.0


def test_align_and_candidate_pic_replace_only_pi() -> None:
    res = {"gid": np.array(["a", "b"]), "pid": np.array([2, 1]), "pi": np.array([0.2, 0.1])}
    out = E.align_to_stored(res, np.array(["b", "a"]), np.array([1, 2]))
    assert out.tolist() == [0.1, 0.2]
    with pytest.raises(RuntimeError):
        E.align_to_stored(res, np.array(["b", "z"]), np.array([1, 2]))
    pic = {"row_pi": np.zeros(2), "wq_pts": np.ones((1, 3)), "row_gid": np.array(["b", "a"])}
    c = E.candidate_pic(pic, out)
    assert (
        c["row_pi"].tolist() == [0.1, 0.2]
        and c["wq_pts"] is pic["wq_pts"]
        and pic["row_pi"].tolist() == [0.0, 0.0]
    )


def test_auc_and_ece() -> None:
    p = np.array([0.1, 0.4, 0.35, 0.8])
    y = np.array([0, 0, 1, 1])
    assert abs(E.auc(p, y) - 0.75) < 1e-12
    assert E.auc(np.zeros(4), y) == 0.5
    assert E.ece_bins(np.array([0.05, 0.05]), np.array([0, 0])) == pytest.approx(0.05)


def _rows(
    delta23: float, delta24: float, shuf: float = 0.0, plc: float = -0.01, p: float = 0.001
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for st in E.STATS:
        for s, d in ((2023, delta23), (2024, delta24)):
            base = {"stat": st, "season": s, "n_rows": 100, "n_games": 10}
            rows.append(
                {
                    **base,
                    "arm": "CAND_minus_A0",
                    "delta": d,
                    "ci_lo": d - 0.002,
                    "ci_hi": d + 0.002,
                    "p": p,
                    "mde": 0.002,
                }
            )
            rows.append(
                {
                    **base,
                    "arm": "CAND_minus_PL",
                    "delta": plc,
                    "ci_lo": plc - 0.002,
                    "ci_hi": plc + 0.002,
                    "p": 0.1,
                    "mde": 0.002,
                }
            )
            rows.append(
                {
                    **base,
                    "arm": "SHUF_CAND_minus_SHUF_CTRL",
                    "delta": shuf,
                    "ci_lo": shuf - 0.001,
                    "ci_hi": shuf + 0.001,
                    "p": 0.5,
                    "mde": 0.001,
                }
            )
            slices.append({"stat": st, "season": s, "slice": "min<5", "n": 500, "delta": -0.05})
    E.add_bh(rows)
    pi_rows = [{"season": s, "arm": "CAND_minus_A0", "ece_delta": 0.0} for s in E.SEASONS]
    return {"rows": rows, "slices": slices, "pi_rows": pi_rows}


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
    r = _rows(-0.02, -0.02)
    r["pi_rows"][0]["ece_delta"] = 0.01
    assert not E.decide(r, True)["any_pass"]  # calibration
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


def test_slice_masks_are_asof_where_declared() -> None:
    comp = {
        "row_minutes": np.array([2.0, 30.0, 12.0]),
        "row_min10": np.array([20.0, 30.0, 12.0]),
        "row_gid": np.array(["0042300001", "0022300002", "0022300003"]),
        "row_starter10": np.array([0.1, 0.9, 0.2]),
        "row_n_prior": np.array([6.0, 30.0, 9.0]),
    }
    m = E.slice_masks(comp)
    assert m["min<5"].tolist() == [True, False, False] and m["bench"].tolist() == [
        True,
        False,
        True,
    ]
    assert m["playoffs"].tolist() == [True, False, False] and m["cold_start"].tolist() == [
        True,
        False,
        True,
    ]
    assert m["short(<0.5*proj)"].tolist() == [True, False, False]
