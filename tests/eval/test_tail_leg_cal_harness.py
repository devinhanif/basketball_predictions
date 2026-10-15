"""TAIL_LEG_CAL harness: map properties, planted future, rule refusals (no real data)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from research.eval import parlay_joint_cal_eval as pj
from research.eval import tail_leg_cal_eval as ev
from research.eval import tail_leg_cal_rule as rule


def _legs(n: int = 6000, seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    start = np.datetime64("2023-12-01")
    date = np.sort(start + rng.integers(0, 240, n).astype("timedelta64[D]"))
    q = np.clip(0.5 + np.abs(rng.normal(0, 0.1, n)), 0.5, 0.95)
    return {"date": date, "q": q}


def test_hinge_is_identity_below_knee_continuous_and_monotone() -> None:
    q = np.linspace(0.5, 0.95, 400)
    for beta in (-0.9, -0.3, 0.0, 0.4):
        out = ev.apply_hinge(q, beta)
        assert np.array_equal(out[q < ev.KNEE], q[q < ev.KNEE])
        assert np.all(np.diff(out) >= -1e-12)
        at = ev.apply_hinge(np.array([ev.KNEE]), beta)[0]
        assert abs(at - ev.KNEE) < 1e-9  # no jump at the knee


def test_hinge_recovers_a_planted_slope() -> None:
    rng = np.random.default_rng(1)
    q = np.clip(0.5 + np.abs(rng.normal(0, 0.12, 60000)), 0.6, 0.95)
    truth = ev.sigmoid(ev.logit(q) - 0.5 * ev.hinge_u(q))
    hit = (rng.random(len(q)) < truth).astype(float)
    beta = ev.fit_hinge(q, hit, np.ones(len(q)))
    assert abs(beta + 0.5) < 0.08


def test_iso_is_nondecreasing_floored_and_identity_below_knee() -> None:
    rng = np.random.default_rng(2)
    q = rng.uniform(0.6, 0.9, 3000)
    hit = (rng.random(3000) < q - 0.05).astype(float)
    kx, ky = ev.fit_iso(q, hit, np.ones(3000))
    assert np.all(np.diff(ky) > 0)
    grid = np.linspace(0.5, 0.95, 300)
    out = ev.apply_iso(grid, kx, ky)
    assert np.array_equal(out[grid < ev.KNEE], grid[grid < ev.KNEE])
    assert np.all(out[grid >= ev.KNEE] >= ev.KNEE - 1e-12)
    assert np.all(np.diff(out) >= -1e-12)


def test_pav_matches_a_known_case() -> None:
    out = ev.pav(np.array([1.0, 3.0, 2.0, 4.0]), np.ones(4))
    assert np.allclose(out, [1.0, 2.5, 2.5, 4.0])


def test_planted_future_changes_nothing_before_its_block() -> None:
    d = _legs()
    rng = np.random.default_rng(3)
    hit = (rng.random(len(d["q"])) < d["q"] - 0.03).astype(float)
    base = ev.walk_forward_tail(d["date"], d["q"], hit)
    cut = np.datetime64("2024-04-01")
    h2 = hit.copy()
    h2[d["date"] >= cut] = 1.0 - h2[d["date"] >= cut]  # plant a flipped future
    alt = ev.walk_forward_tail(d["date"], d["q"], h2)
    before = d["date"] < cut
    for a in ("TAIL_H", "TAIL_I", "SHIFT", "CONST"):
        assert np.array_equal(base["arms"][a][before], alt["arms"][a][before])
    # and the future does move once it can be fit on (positive control)
    after = d["date"] >= np.datetime64("2024-05-01")
    assert not np.array_equal(base["arms"]["TAIL_H"][after], alt["arms"]["TAIL_H"][after])


def test_identity_below_min_tail_and_only_tail_rows_are_scored() -> None:
    d = _legs(n=1200)
    hit = np.zeros(len(d["q"]))
    wf = ev.walk_forward_tail(d["date"], d["q"], hit)
    assert wf["scored"].sum() == 0 or all(
        not r["identity"] for r in wf["blocks"] if r["n_score"] and r["n_fit"] >= ev.MIN_TAIL
    )
    assert not np.any(wf["scored"] & (d["q"] < ev.KNEE))


def test_construction_checks_hold_on_walk_forward_output() -> None:
    d = _legs()
    rng = np.random.default_rng(4)
    hit = (rng.random(len(d["q"])) < d["q"] - 0.04).astype(float)
    wf = ev.walk_forward_tail(d["date"], d["q"], hit)
    dd = {"q": d["q"], "date": d["date"]}
    out = ev.construction_checks(dd, wf)
    assert all(out.values())


def test_calibrated_world_leaves_the_tail_nearly_unchanged() -> None:
    d = _legs(n=20000, seed=5)
    rng = np.random.default_rng(6)
    hit = (rng.random(len(d["q"])) < d["q"]).astype(float)
    wf = ev.walk_forward_tail(d["date"], d["q"], hit)
    m = wf["scored"]
    assert abs(float(np.mean(wf["arms"]["TAIL_H"][m] - d["q"][m]))) < 0.01


def test_truth_prob_forms() -> None:
    q = np.array([0.55, 0.62, 0.69])
    assert np.allclose(ev.truth_prob(q, "smooth"), [0.55, 0.61, 0.645])
    assert np.allclose(ev.truth_prob(q, "calibrated"), q)
    assert np.allclose(ev.truth_prob(q, "step"), [0.55, 0.613, 0.65])
    with pytest.raises(RuntimeError):
        ev.truth_prob(q, "nope")


def test_guards_raise_on_2025_and_late_fit_rows() -> None:
    with pytest.raises(RuntimeError, match="holdout"):
        ev.guard_season((2023, 2025))
    with pytest.raises(RuntimeError, match="fit row"):
        ev.guard_fit_before(
            np.array(["2024-02-01"], dtype="datetime64[D]"), np.datetime64("2024-02-01"), "x"
        )


def test_bh_equals_the_nba_implementation() -> None:
    from nba.eval.context_residual_eval import bh_adjust

    p = [0.01, 0.2, 0.03, 0.5]
    assert np.allclose(ev.bh_adjust(p), bh_adjust(np.array(p)))


def test_clusters_summary_and_slope_on_toy_data() -> None:
    rng = np.random.default_rng(7)
    n = 4000
    season = np.where(np.arange(n) < n // 2, 2023, 2024)
    game = np.array([f"g{i // 4}" for i in range(n)])
    date = np.array(
        [
            f"{2023 if s == 2023 else 2024}-{(i // 40) % 12 + 1:02d}-01"
            for i, s in enumerate(season)
        ],
        dtype="datetime64[D]",
    )
    cl = ev.Clusters(game, date, season, b=300)
    x = rng.uniform(0.6, 0.8, n)
    y = (rng.random(n) < x).astype(float)
    m = np.ones(n, dtype=bool)
    g = cl.summary(m, y - x)
    assert g["lo"] < g["point"] < g["hi"] and abs(g["point"]) < 0.03
    sl = cl.slope(m, x, y)
    assert sl["lo"] < sl["point"] < sl["hi"]


def test_text_sha_stops_at_marker_and_declared_parses_hash_lines(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"x\nNAME_A: {'1' * 64}\n{rule.MARKER}\nNAME_B: {'2' * 64}\n")
    assert rule.declared(doc) == {"NAME_A": "1" * 64}
    assert rule.declared(tmp_path / "missing.md") == {}


def test_freeze_and_pin_refusals_with_a_fake_git(tmp_path: Path) -> None:
    doc_text = "rule text\n" + rule.MARKER + "\nresults\n"
    sha = rule.text_sha(doc_text)
    files = {rel: f"content of {rel}\n" for rel in rule.HARNESS_FILES}
    for rel, txt in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(txt)
    blobs = rule.disk_blobs(tmp_path)
    pin = rule.pin_of(blobs)

    def fake(*args: str) -> str:
        if args[0] == "log":
            return f"fc\tprereg(TAIL_LEG_CAL): {sha}\nph\tharness(TAIL_LEG_CAL): {pin}\n"
        if args[0] == "show":
            _, _, rel = args[1].partition(":")
            return doc_text if rel == rule.DOC_REL else files[rel]
        return ""

    assert rule.freeze_commit(sha, fake) == "fc"
    assert rule.require_harness_pinned(sha, fake, tmp_path, lambda *a: True) == blobs
    with pytest.raises(RuntimeError, match="descend"):
        rule.require_harness_pinned(sha, fake, tmp_path, lambda *a: False)
    (tmp_path / rule.HARNESS_FILES[0]).write_text("edited\n")
    with pytest.raises(RuntimeError):
        rule.require_harness_pinned(sha, fake, tmp_path, lambda *a: True)
    other = tmp_path / "other.md"
    other.write_text("a different rule\n")
    with pytest.raises(RuntimeError):
        rule.require_frozen(sha, other, fake)

    def nopin(*args: str) -> str:
        return (
            f"fc\tprereg(TAIL_LEG_CAL): {sha}\n"
            if args[0] == "log"
            else (doc_text if args[0] == "show" and args[1].endswith(rule.DOC_REL) else "")
        )

    with pytest.raises(RuntimeError, match="not pinned"):
        rule.require_harness_pinned(sha, nopin, tmp_path, lambda *a: True)


def test_run_refuses_before_reading_anything_when_not_frozen(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        ev.main(["run", "--data-root", str(tmp_path / "nowhere"), "--sha", "0" * 64])


def test_attach_labels_real_needs_the_hits_and_synthetic_never_reads_them() -> None:
    d = {"q": np.array([0.6, 0.7]), "date": np.array(["2024-01-01"] * 2, dtype="datetime64[D]")}
    legs = {"draftkings": d, "pinnacle": d}
    syn = ev.attach_labels(
        legs,
        {"draftkings": np.array([9.0, 9.0]), "pinnacle": np.array([9.0, 9.0])},
        "synthetic",
        "calibrated",
    )
    assert set(np.unique(syn["draftkings"])) <= {0.0, 1.0}
    real = ev.attach_labels(
        legs, {"draftkings": np.array([1.0, 0.0]), "pinnacle": np.array([0.0, 0.0])}, "real"
    )
    assert real["draftkings"].tolist() == [1.0, 0.0]


def _s(
    point: float,
    lo: float | None = None,
    hi: float | None = None,
    p_neg: float = 0.5,
    p_pos_min: float = 0.5,
) -> dict[str, object]:
    lo = point - 0.01 if lo is None else lo
    hi = point + 0.01 if hi is None else hi
    return {
        "n": 1000,
        "point": point,
        "lo": lo,
        "hi": hi,
        "game": [lo, hi],
        "date": [lo, hi],
        "p_neg": p_neg,
        "p_pos": 0.5,
        "p_pos_min": p_pos_min,
    }


def _books(
    cal65_hi: float = -0.01, g65: float = 0.0, l60: float = -0.001, p_neg: float = 0.01,
    beta: float = -0.5, bad_stat: bool = False, season_bad: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:  # fmt: skip
    arm = {
        "gap": _s(0.0, -0.01, 0.01),
        "ll_minus_cal": _s(l60, l60 - 0.0005, min(l60 + 0.0005, 0.0002), p_neg=p_neg),
    }
    arm65 = {"gap": _s(g65, g65 - 0.02, g65 + 0.02), "ll_minus_cal": _s(-0.002, p_neg=0.5)}
    sl: dict[str, Any] = {}
    for s in (2023, 2024):
        ll_s = 0.001 if (season_bad and s == 2023) else -0.0005
        sl[f"season_{s}_r60"] = {"TAIL_H": {"ll_minus_cal": ll_s, "gap": 0.0}}
        sl[f"season_{s}_r65"] = {"TAIL_H": {"gap": g65}}
    for st in ("fg3m", "reb"):
        sl[f"stat_{st}"] = {
            "TAIL_H_contrast": _s(0.001, p_neg=0.5, p_pos_min=0.001 if bad_stat else 0.6)
        }
    for v in ("over", "under"):
        sl[f"side_{v}"] = {"TAIL_H": {"ll_minus_cal": -0.0002}}
    prim = {
        "sets": {
            "r60": {"TAIL_H": arm},
            "r65": {"TAIL_H": arm65, "CAL": {"gap": _s(-0.04, -0.06, cal65_hi)}},
        },
        "slices": sl,
        "blocks": [{"identity": False, "beta": beta}] * 5 + [{"identity": True}],
    }
    other = {"sets": {"r60": {"TAIL_H": {"ll_minus_cal": _s(-0.0003), "gap": _s(0.0)}}}}
    return prim, other


def test_evaluate_issues_each_word_and_p0_wins() -> None:
    assert ev.evaluate(*_books())["word"] == "TAIL_FIXES"
    assert ev.evaluate(*_books(l60=-0.0001, p_neg=0.2))["word"] == "TAIL_GAP_FIXED_LL_NOT_SHOWN"
    assert ev.evaluate(*_books(beta=-0.95))["word"] == "TAIL_FLATTENED"
    assert ev.evaluate(*_books(g65=-0.05))["word"] == "NOT_FIXED"
    assert ev.evaluate(*_books(season_bad=True))["word"] == "NOT_FIXED"
    assert ev.evaluate(*_books(bad_stat=True))["word"] == "NOT_FIXED"
    # P0 failing wins over a flat tail and over everything else
    assert ev.evaluate(*_books(cal65_hi=0.01, beta=-0.95))["word"] == "NO_TAIL_PROBLEM"
    assert ev.evaluate(*_books(cal65_hi=0.01, g65=-0.05))["word"] == "NO_TAIL_PROBLEM"


def test_p3_needs_both_intervals_to_contain_zero() -> None:
    prim, other = _books()
    prim["sets"]["r65"]["TAIL_H"]["gap"]["date"] = [0.005, 0.03]
    assert ev.evaluate(prim, other)["checks"]["P3_gap65"] is False


def test_preflight_refuses_on_a_version_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pj, "versions", lambda: {"numpy": "0", "polars": "0", "scipy": "0"})
    with pytest.raises(RuntimeError, match="versions"):
        ev.preflight(tmp_path)
