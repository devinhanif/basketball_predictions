# ruff: noqa: E501
"""QUESTIONABLE_CASCADE rule and harness: refusals (draft, season > 2024, unfrozen, unsized), the words, the bootstrap, the positive-control pieces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import questionable_cascade_eval as E
from research.eval import questionable_cascade_features as F
from research.eval import questionable_cascade_rule as R

FROZEN_DOC = "Status: **FROZEN by the main session**\nbody\n=== RESULTS BELOW ===\nnot hashed\n"
DRAFT_DOC = "Status: **DRAFT, modeler.**\nbody\n=== RESULTS BELOW ===\n"


def test_draft_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text(DRAFT_DOC)
    with pytest.raises(RuntimeError, match="FROZEN"):
        R.require_not_draft(p)
    p.write_text("no status line at all\n")
    with pytest.raises(RuntimeError, match="FROZEN"):
        R.require_not_draft(p)
    p.write_text(FROZEN_DOC)
    R.require_not_draft(p)


def test_the_committed_doc_is_a_draft_and_run_refuses_it() -> None:
    with pytest.raises(RuntimeError):
        E.run("0" * 64)
    with pytest.raises(RuntimeError):
        R.require_ready("0" * 64)


def test_ready_refuses_unsized_placebo_and_unfrozen(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text(FROZEN_DOC)
    assert R.PLACEBO_TOL is None
    with pytest.raises(RuntimeError, match="PLACEBO_TOL"):
        R.require_ready("a" * 64, doc=p, run=lambda *a: "", ok=lambda *a: True)


def test_frozen_needs_a_matching_commit(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text(FROZEN_DOC)
    sha = R.frozen_sha(p)
    with pytest.raises(RuntimeError, match="not frozen"):
        R.require_frozen(sha, p, run=lambda *a: "")
    with pytest.raises(RuntimeError, match="hashes to"):
        R.require_frozen("b" * 64, p, run=lambda *a: "")
    with pytest.raises(RuntimeError, match="64 hex"):
        R.require_frozen("short", p, run=lambda *a: "")
    subj = f"abc123\tprereg({R.ID}): {sha}"

    def run(*a: str) -> str:
        return subj if a[0] == "log" else FROZEN_DOC

    assert R.require_frozen(sha, p, run=run) == sha
    # text after the marker is not hashed
    p.write_text(FROZEN_DOC + "more results\n")
    assert R.frozen_sha(p) == sha


def test_season_2025_is_refused_everywhere() -> None:
    assert R.require_season(2024) == 2024
    with pytest.raises(RuntimeError, match="holdout"):
        R.require_season(2025)
    with pytest.raises(RuntimeError, match="holdout"):
        R.require_season(2026)


def test_bh() -> None:
    adj = R.bh_adjust(np.array([0.01, 0.04, 0.03, 0.5]))
    assert adj[0] == pytest.approx(0.04) and adj[3] == pytest.approx(0.5)
    assert (adj >= np.array([0.01, 0.04, 0.03, 0.5])).all()


def test_multiboot_brackets_the_mean_and_binding_is_an_envelope() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    gid = np.repeat(np.arange(1000), 4)
    pid = rng.integers(0, 60, n)
    wk = np.repeat(np.arange(25), 160)
    v = rng.normal(-0.01, 0.3, n)
    bt = R.MultiBoot({"game": gid, "player_season": pid, "week": wk}, b=800)
    r = bt.ci(v, np.ones(n, bool))
    assert r["lo"] < r["point"] < r["hi"]
    assert r["lo"] == min(x["lo"] for x in r["per"].values())
    assert r["hi"] == max(x["hi"] for x in r["per"].values())
    # fewer than MIN_WEEKS weeks: week is reported but not decisive
    bt2 = R.MultiBoot({"game": gid, "week": np.repeat(np.arange(10), 400)}, b=400)
    r2 = bt2.ci(v, np.ones(n, bool))
    assert r2["per"]["week"]["decisive"] == 0.0
    assert r2["hi"] == r2["per"]["game"]["hi"]


def ci(point: float, half: float = 0.002, n: int = 5000) -> dict[str, Any]:
    return {
        "point": point,
        "lo": point - half,
        "hi": point + half,
        "p": 0.001 if point + half < 0 else 0.5,
        "n": n,
    }


def season_block(delta: float, **over: Any) -> dict[str, Any]:
    b: dict[str, Any] = {
        "C2_minus_A0_teammate": ci(delta),
        "C2_minus_A0_rest": ci(0.0),
        "C1_minus_A0_own": ci(0.0, half=0.03, n=900),
        "C2_minus_PL_teammate": ci(delta),
        "PL_minus_CTRL_teammate": ci(0.0),
        "coverage_A0_C2": {"0.1": [0.1, 0.1]},
        "slices": {"s": {"point": delta, "lo": delta, "hi": delta, "n": 400}},
    }
    b.update(over)
    return b


def sc_of(delta22: float, delta23: float, **over: Any) -> dict[str, Any]:
    return {
        "pts": {str(R.SELECT): season_block(delta22), str(R.REPORT): season_block(delta23, **over)}
    }


def test_words() -> None:
    assert R.decide(sc_of(-0.006, -0.008))["words"]["pts"] == "PASS"
    assert R.decide(sc_of(-0.006, 0.001))["words"]["pts"] == "CLOSED"
    assert (
        R.decide(sc_of(-0.006, -0.003))["words"]["pts"] == "INCONCLUSIVE"
    )  # above the floor but below the close line
    assert (
        R.decide(sc_of(+0.001, -0.008))["words"]["pts"] == "INCONCLUSIVE"
    )  # select-season sign wrong
    bad_pl = R.decide(sc_of(-0.006, -0.008, **{"C2_minus_PL_teammate": ci(-0.001)}))
    assert (
        bad_pl["words"]["pts"] == "GUARD_FAIL"
        and not bad_pl["detail"]["pts"]["guards"]["placebo_margin"]
    )
    harm = R.decide(sc_of(-0.006, -0.008, **{"C2_minus_A0_rest": ci(0.004, half=0.002)}))
    assert not harm["detail"]["pts"]["guards"]["no_harm_rest"]
    unresolved = R.decide(sc_of(-0.006, -0.008, **{"C2_minus_A0_rest": ci(0.0, half=0.0056)}))
    assert unresolved["detail"]["pts"]["guards"][
        "no_harm_rest"
    ]  # absence of proof of harm is not harm
    assert R.decide(sc_of(-0.006, -0.008))["detail"]["pts"]["own_row_word"] == "OWN_INCONCLUSIVE"


def test_permute_within_group_moves_values_only_inside_a_group() -> None:
    shift = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    group = np.array(["a", "a", "a", "b", "b", "c"])
    mask = np.array([True, True, False, True, True, True])
    out = E.permute_within_group(shift, mask, group, np.random.default_rng(1))
    assert sorted(out[:2]) == [0.1, 0.2] and out[2] == 0.0
    assert sorted(out[3:5]) == [0.4, 0.5] and out[5] == 0.6


def test_permute_within_date_is_joint_and_stays_in_the_date() -> None:
    import datetime as dt

    fr = pl.DataFrame(
        {
            "game_date": [dt.date(2023, 11, 1)] * 3 + [dt.date(2023, 11, 2)] * 3,
            "qc_status": [1.0, 2.0, 3.0, 4.0, 5.0, 3.0],
            "qc_nq": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        }
    )
    out = E.permute_within_date(fr, ("qc_status", "qc_nq"), 7)
    for lo, hi in ((0, 3), (3, 6)):
        pairs = set(zip(fr["qc_status"][lo:hi], fr["qc_nq"][lo:hi], strict=True))
        got = set(zip(out["pl_qc_status"][lo:hi], out["pl_qc_nq"][lo:hi], strict=True))
        assert pairs == got  # rows of the block moved together, never across dates


def test_mid_coverage_charges_ties_half() -> None:
    y = np.array([1.0, 1.0, 1.0, 1.0])
    assert E.mid_coverage(y, np.array([1.0] * 4)) == pytest.approx(0.5)
    assert E.mid_coverage(y, np.array([2.0] * 4)) == pytest.approx(1.0)
    assert E.mid_coverage(y, np.array([0.0] * 4)) == pytest.approx(0.0)


def test_score_arms_path_detects_a_planted_shift_and_not_a_wrong_one() -> None:
    """A small end-to-end scoring check on synthetic integer grids: the oracle passes the contrast, a shuffled copy does not."""
    rng = np.random.default_rng(3)
    n = 6000
    gid = np.repeat(np.arange(n // 5), 5).astype(str)
    pid = rng.integers(0, 300, n)
    season = np.where(np.arange(n) < n // 2, R.SELECT, R.REPORT)
    day = np.repeat(np.arange(n // 5), 5)
    wk = (day // 12).astype(str)
    tm = rng.random(n) < 0.4
    taus = np.linspace(0.005, 0.995, 199)
    base = np.round(10 + 4 * np.array([_ppf(t) for t in taus]))
    q0 = np.tile(base, (n, 1)).astype(np.int16)
    shift = np.where(tm, 3, 0)
    y = np.array([base[rng.integers(0, 199)] for _ in range(n)]) + shift
    q_or = (q0 + shift[:, None]).astype(np.int16)
    q_wr = (q0 + np.where(tm, 0, 3)[:, None]).astype(np.int16)  # the same shift on the OTHER rows
    rc = {
        "tm": tm,
        "own": np.zeros(n, bool),
        "rest": ~tm,
        "qc_nq": np.ones(n),
        "qc_evac_min": np.full(n, 25.0),
    }
    comp = {"row_b2b": np.zeros(n), "row_starter10": np.zeros(n)}
    sc = E.score_arms(
        {"pts": {"A0": q0, "C2": q_or, "PL": q_wr}},
        {"pts": y},
        gid,
        pid,
        season,
        wk,
        rc,
        comp,
        n_boot=400,
    )
    d = sc["pts"][str(R.REPORT)]
    assert d["C2_minus_A0_teammate"]["hi"] < 0 and d["C2_minus_A0_teammate"]["point"] < R.FLOOR
    assert d["C2_minus_PL_teammate"]["hi"] < 0
    assert abs(d["C2_minus_A0_rest"]["point"]) < 1e-9
    sc2 = E.score_arms(
        {"pts": {"A0": q0, "C2": q_wr, "PL": q_or}},
        {"pts": y},
        gid,
        pid,
        season,
        wk,
        rc,
        comp,
        n_boot=400,
    )
    assert sc2["pts"][str(R.REPORT)]["C2_minus_A0_teammate"]["point"] > R.CLOSE_AT


def _ppf(t: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(t))


def test_declared_constants_match_the_doc() -> None:
    text = R.DOC.read_text()
    above = text.split("\n" + R.MARKER + "\n")[0]  # the marker LINE, not an inline mention
    for line in (
        f"FLOOR = {R.FLOOR}",
        f"CLOSE_AT = {R.CLOSE_AT}",
        f"PL_MARGIN = {R.PL_MARGIN}",
        f"NOHARM_POINT = {R.NOHARM_POINT}",
        f"SLICE_TOL = {R.SLICE_TOL}",
        f"MIN_SLICE_N = {R.MIN_SLICE_N}",
        f"COV_TOL = {R.COV_TOL}",
        f"N_BOOT = {R.N_BOOT}",
        f"SEED = {R.SEED}",
        f"SELECT = {R.SELECT}",
        f"REPORT = {R.REPORT}",
        f"BH_Q = {R.BH_Q}",
        f"DESIGN = {R.DESIGN!r}",
    ):
        assert line in above, line


def test_feature_name_lists() -> None:
    assert F.C1_FEATURES == ("qc_status", "qc_reason")
    assert set(F.C1_FEATURES) < set(F.C2_FEATURES)
    assert len(F.C2_FEATURES) == 2 + 3 + 4
