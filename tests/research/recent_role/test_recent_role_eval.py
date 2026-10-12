# ruff: noqa: E501
"""RECENT_ROLE harness: freeze and holdout refusal, shared-permutation shuffle that can fail, scoring, decision, Stage 2 pieces."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import recent_role_eval as E
from research.eval import recent_role_props as RP

SHA = "a" * 64


def _doc(tmp_path: Path, body: str = "rule\n") -> tuple[Path, str]:
    p = tmp_path / "d.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\nx\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def test_require_frozen_needs_matching_hash_and_the_prereg_commit(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    good = lambda *a: f"abc\tprereg(RECENT_ROLE): {sha}\n"  # noqa: E731
    none = lambda *a: "abc\tsomething else\n"  # noqa: E731
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


def test_stage3_refuses_without_a_committed_confirmed_row(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)
    log = tmp_path / "log.md"
    frozen = lambda *a: f"abc\tprereg(RECENT_ROLE): {sha}\n"  # noqa: E731
    log.write_text("| nothing |\n")
    with pytest.raises(RuntimeError, match="no committed holdout row"):
        E.require_holdout_row(sha, log, frozen, p)
    row = f"| 2026-10-12 | RECENT_ROLE {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n"
    log.write_text(row)

    def git(*a: str) -> str:
        if a[0] == "log" and a[1].startswith("--format=%H%x09"):
            return f"abc\tprereg(RECENT_ROLE): {sha}\n"
        if a[0] == "status":
            return " M log.md\n"
        return ""

    with pytest.raises(RuntimeError, match="uncommitted"):
        E.require_holdout_row(sha, log, git, p)

    def git2(*a: str) -> str:
        return (
            "" if a[0] in ("status", "log") and not a[1].startswith("--format=%H%x09") else git(*a)
        )

    with pytest.raises(RuntimeError, match="not in any commit"):
        E.require_holdout_row(sha, log, git2, p)

    def git3(*a: str) -> str:
        if a[0] == "status":
            return ""
        if a[0] == "log" and not a[1].startswith("--format=%H%x09"):
            return "deadbeef\n"
        return git(*a)

    assert "PRE-REGISTERED" in E.require_holdout_row(sha, log, git3, p)


def test_stage3_entry_checks_the_row_first(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(E, "require_holdout_row", lambda sha: called.append(sha) or "row")
    with pytest.raises(NotImplementedError):
        E.stage3("x")
    assert called == ["x"]


def _frame(n_per_month: int = 700, signal: bool = True, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    d0 = dt.date(2022, 11, 1)
    gid = 0
    for day in range(0, 900, 3):
        d = d0 + dt.timedelta(days=day)
        season = d.year if d.month >= 8 else d.year - 1
        for _ in range(12):
            gid += 1
            x = rng.normal()
            z = rng.normal()
            played = float(
                rng.random() < 1 / (1 + np.exp(-(0.8 * x + (1.2 * z if signal else 0.0))))
            )
            rows.append(
                {
                    "game_id": f"g{gid // 12:06d}",
                    "player_id": gid,
                    "season": season,
                    "game_date": d,
                    "played": played,
                    "x": x,
                    "z": z,
                }
            )
    return pl.DataFrame(rows)


ARMS = {"Cp": ["x"], "R": ["x", "z"]}
CFG = ContextResidualConfig()


def test_shuffle_uses_one_shared_permutation_and_real_labels_are_scored() -> None:
    fr = _frame()
    res, audit = E.walk(fr, ARMS, CFG, shuffle=True, min_train=300, n_estimators=10)
    assert (
        audit["label_hashes"]["Cp"] == audit["label_hashes"]["R"]
        and len(set(audit["label_hashes"]["Cp"])) > 1
    )
    real, a2 = E.walk(fr, ARMS, CFG, shuffle=False, min_train=300, n_estimators=10)
    assert audit["label_hashes"]["Cp"] != a2["label_hashes"]["Cp"]  # labels really were permuted
    assert np.array_equal(res["y"], real["y"])  # scored on the real labels
    assert E._ll(res["p_R"], res["y"]).mean() > E._ll(real["p_R"], real["y"]).mean()


def test_shuffle_check_passes_when_labels_are_shuffled_and_fails_when_they_are_not() -> None:
    """The old defect (labels not permuted) must be caught: with the planted signal in z the check fails; with the permutation it passes."""
    fr = _frame()
    for permute, want_ok in ((True, True), (False, False)):
        res, _ = E.walk(
            fr, ARMS, CFG, shuffle=True, permute=permute, min_train=300, n_estimators=20
        )
        out = {"rows": []}
        for season in E.SEASONS:
            sm = res["season"] == season
            d = E._ll(res["p_R"][sm], res["y"][sm]) - E._ll(res["p_Cp"][sm], res["y"][sm])
            out["rows"].append({"season": season, **E.WE.delta_stats(d, res["gid"][sm])})
        checks = [E.shuffle_check(r) for r in out["rows"]]
        assert all(c["ok"] for c in checks) == want_ok, (permute, checks)


def test_walk_checkpoint_resume_repeats_the_shuffle(tmp_path: Path) -> None:
    fr = _frame()
    a, h1 = E.walk(fr, ARMS, CFG, shuffle=True, ckpt=tmp_path, min_train=300, n_estimators=10)
    b, h2 = E.walk(fr, ARMS, CFG, shuffle=True, ckpt=tmp_path, min_train=300, n_estimators=10)
    assert np.array_equal(a["p_R"], b["p_R"]) and h1["label_hashes"] == h2["label_hashes"]


def _res(delta: float, seed: int = 0, n: int = 4000) -> tuple[dict[str, np.ndarray], pl.DataFrame]:
    rng = np.random.default_rng(seed)
    season = np.repeat([2023, 2024], n // 2)
    gid = np.array([f"g{i // 10:05d}" for i in range(n)])
    q = np.clip(rng.beta(8, 2, n), 0.05, 0.95)
    y = (rng.random(n) < q).astype(float)
    p_cp = np.clip(0.8 + 0.2 * (q - 0.8), 0.05, 0.95)
    p_r = q if delta > 0 else p_cp
    res = {
        "gid": gid,
        "pid": np.arange(n),
        "season": season,
        "y": y,
        "p_base": p_cp,
        "p_Cp": p_cp,
        "p_R": p_r,
        "p_R1": p_r,
        "p_KO1": p_cp,
        "p_PL": p_cp,
    }
    meta = pl.DataFrame(
        {
            "game_id": gid,
            "starter10": rng.random(n),
            "n_played_prior": rng.integers(5, 50, n).astype(float),
            "is_b2b_second": (rng.random(n) < 0.15).astype(float),
            "wl_min_last": rng.uniform(0, 40, n),
        }
    )
    return res, meta


def test_score_and_decide_pass_and_fail() -> None:
    res, meta = _res(0.06)
    shuf = {
        "gid": res["gid"],
        "season": res["season"],
        "y": res["y"],
        "p_Cp": res["p_Cp"],
        "p_R": res["p_Cp"],
    }
    sc = E.score_stage1(res, meta, shuf)
    v = E.decide_stage1(sc, gate_ok=True)
    assert (
        v["rule1_2023"]
        and v["rule2_2024"]
        and v["attack"]["shuffle"]
        and v["attack"]["placebo"] is True
    )
    assert v["pass"] and v["named_claim"] == "min_last" or v["pass"]
    res0, meta0 = _res(0.0)
    v0 = E.decide_stage1(E.score_stage1(res0, meta0, shuf), gate_ok=True)
    assert not v0["pass"] and not v0["rule1_2023"]
    assert not E.decide_stage1(sc, gate_ok=False)["pass"]
    sl = {(s["season"], s["slice"]) for s in sc["slices"]}
    assert (2024, "bench") in sl and (2023, "playoffs") in sl


def test_weighted_crps_matches_brute_force_and_degenerate_cases() -> None:
    rng = np.random.default_rng(1)
    x = rng.integers(0, 12, (5, 7)).astype(float)
    w = rng.random((5, 7))
    w /= w.sum(axis=1, keepdims=True)
    y = rng.integers(0, 12, 5).astype(float)
    got = RP.weighted_crps(x, w, y)
    for i in range(5):
        e1 = (w[i] * np.abs(x[i] - y[i])).sum()
        e2 = sum(w[i, a] * w[i, b] * abs(x[i, a] - x[i, b]) for a in range(7) for b in range(7)) / 2
        assert abs(got[i] - (e1 - e2)) < 1e-12
    qi = np.tile(np.arange(10), (3, 1))
    assert np.allclose(
        RP.uncond_crps(qi, np.ones(3), np.array([0.0, 4.0, 9.0])),
        RP.weighted_crps(qi.astype(float), np.full((3, 10), 0.1), np.array([0.0, 4.0, 9.0])),
        atol=1e-9,
    )
    # p = 0: the forecast is the point mass at 0, so the CRPS is |y|
    assert np.allclose(RP.uncond_crps(qi, np.zeros(3), np.array([0.0, 4.0, 9.0])), [0.0, 4.0, 9.0])
    # a calibrated lower p for a DNP row scores better than a higher one
    assert (
        RP.uncond_crps(qi, np.array([0.2]), np.array([0.0]))[0]
        < RP.uncond_crps(qi, np.array([0.9]), np.array([0.0]))[0]
    )


def test_decide_2a_requires_the_live_gate_and_bh() -> None:
    rows = []
    for s in RP.PROP_STATS:
        for season, d in ((2023, -0.01), (2024, -0.01)):
            rows.append(
                {
                    "stat": s,
                    "season": season,
                    "arm": "R_minus_Cp",
                    "delta": d,
                    "ci_hi": -0.002,
                    "p": 0.001,
                    "p_bh": 0.004,
                    "mde": 0.002,
                }
            )
    out = {"rows": rows, "live_vs_stored": [{"ok": True}]}
    assert RP.decide_2a(out)["any_pass"]
    out["live_vs_stored"] = [{"ok": False}]
    assert not RP.decide_2a(out)["any_pass"]


def test_injury_elo_has_no_availability_probability_input() -> None:
    import inspect

    from nba.models import injury_elo as IE

    src = inspect.getsource(IE._side_sums)
    assert "doubtful_weight" in src and "p_play" not in src and "prob" not in src
