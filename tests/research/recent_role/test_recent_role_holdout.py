# ruff: noqa: E501
"""RECENT_ROLE_HOLDOUT harness: both refusals (fake git, temp files), one-run guard, season cap, scoring, decision, shuffle can fail."""

from __future__ import annotations

import datetime as dt
import hashlib
import sys
import types
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import recent_role_eval as RR
from research.eval import recent_role_holdout as H


def _doc(tmp_path: Path) -> tuple[Path, str]:
    p = tmp_path / "d.md"
    body = "rule\n"
    p.write_text(f"{body}=== RESULTS BELOW ===\nx\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def _git_factory(sha: str, frozen: bool = True, dirty: bool = False, row_in_commit: bool = True):
    def git(*a: str) -> str:
        if a[0] == "log" and a[1].startswith("--format=%H%x09"):
            return f"abc\tprereg({H.ID}): {sha}\n" if frozen else "abc\tother\n"
        if a[0] == "status":
            return " M log\n" if dirty else ""
        if a[0] == "log":
            return "deadbeef\n" if row_in_commit else ""
        return ""

    return git


def _log(tmp_path: Path, sha: str, text: str | None = None) -> Path:
    p = tmp_path / "log.md"
    p.write_text(
        text
        if text is not None
        else f"| 2026-10-12 | {H.ID} {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n"
    )
    return p


def test_refuses_without_the_frozen_commit_or_with_a_changed_doc(tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    assert H.require_frozen(sha, doc, _git_factory(sha)) == sha
    with pytest.raises(RuntimeError, match="not frozen"):
        H.require_frozen(sha, doc, _git_factory(sha, frozen=False))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        H.require_frozen("b" * 64, doc, _git_factory("b" * 64))
    with pytest.raises(RuntimeError, match="required"):
        H.require_frozen(None, doc, _git_factory(sha))
    doc.write_text(doc.read_text().replace("rule", "rule edited"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        H.require_frozen(sha, doc, _git_factory(sha))


def test_refuses_without_a_committed_confirmed_holdout_row(tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    git = _git_factory(sha)
    with pytest.raises(RuntimeError, match="no holdout row"):
        H.require_holdout_row(sha, _log(tmp_path, sha, "| nothing |\n"), git)
    for missing in ("PRE-REGISTERED", "DEVIN CONFIRMED", sha[:8]):
        text = f"| {H.ID} {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n".replace(missing, "x")
        with pytest.raises(RuntimeError, match="no holdout row"):
            H.require_holdout_row(sha, _log(tmp_path, sha, text), git)
    # the earlier RECENT_ROLE row text (a different id) does not satisfy it
    with pytest.raises(RuntimeError, match="no holdout row"):
        H.require_holdout_row(
            sha,
            _log(tmp_path, sha, f"| RECENT_ROLE {sha[:8]} PRE-REGISTERED DEVIN CONFIRMED |\n"),
            git,
        )
    good = _log(tmp_path, sha)
    with pytest.raises(RuntimeError, match="uncommitted"):
        H.require_holdout_row(sha, good, _git_factory(sha, dirty=True))
    with pytest.raises(RuntimeError, match="no commit"):
        H.require_holdout_row(sha, good, _git_factory(sha, row_in_commit=False))
    assert "PRE-REGISTERED" in H.require_holdout_row(sha, good, git)
    assert H.require_both(sha, doc, good, git) == sha
    with pytest.raises(RuntimeError, match="not frozen"):
        H.require_both(sha, doc, good, _git_factory(sha, frozen=False))


def test_run_touches_nothing_when_a_refusal_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("data path reached before the refusals passed")

    monkeypatch.setattr(RR, "build_frame", boom)
    monkeypatch.setattr(H, "season_cap", boom)
    with pytest.raises(RuntimeError):
        H.run("0" * 64)  # the real doc/git do not hold this sha
    with pytest.raises(RuntimeError, match="required"):
        H.run(None)


def test_second_run_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(H, "require_both", lambda sha: sha)
    monkeypatch.setattr(H, "OUT_DIR", tmp_path)
    (tmp_path / "results.json").write_text('{"gate_ok": true}')
    with pytest.raises(RuntimeError, match="no reruns"):
        H.run("a" * 64)
    # a run that stopped at the gate scored nothing: it is not blocked by the one-run guard (it reaches the data path)
    (tmp_path / "results.json").write_text('{"gate_ok": false}')
    monkeypatch.setattr(H, "preimport", lambda: (_ for _ in ()).throw(AssertionError("reached")))
    with pytest.raises(AssertionError, match="reached"):
        H.run("a" * 64)


def test_season_cap_raises_and_restores_and_is_off_by_default() -> None:
    fake = types.ModuleType("research.fake_cap_probe")
    fake.MAX_SEASON = 2024  # type: ignore[attr-defined]
    other = types.ModuleType("research.fake_other")
    other.MAX_SEASON = 2023  # type: ignore[attr-defined]
    sys.modules["research.fake_cap_probe"], sys.modules["research.fake_other"] = fake, other
    from nba.eval import context_residual_eval as CRE

    before = CRE.load_inputs.__defaults__
    try:
        with H.season_cap(2025):
            assert fake.MAX_SEASON == 2025 and CRE.MAX_SEASON == 2025  # type: ignore[attr-defined]
            assert other.MAX_SEASON == 2023  # type: ignore[attr-defined]
            assert CRE.load_inputs.__defaults__ == (2025,)
        assert fake.MAX_SEASON == 2024 and CRE.MAX_SEASON == 2024  # type: ignore[attr-defined]
        assert CRE.load_inputs.__defaults__ == before
        with pytest.raises(ValueError, match="holdout"):
            RR.WE.assert_seasons(pl.DataFrame({"season": [2025]}))
    finally:
        sys.modules.pop("research.fake_cap_probe", None)
        sys.modules.pop("research.fake_other", None)


def _res(
    delta: float, n: int = 4000, seed: int = 0
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], pl.DataFrame]:
    rng = np.random.default_rng(seed)
    gid = np.array([f"004{i // 10:05d}" if i % 7 == 0 else f"002{i // 10:05d}" for i in range(n)])
    q = np.clip(rng.beta(8, 2, n), 0.05, 0.95)
    y = (rng.random(n) < q).astype(float)
    base = np.clip(0.8 + 0.2 * (q - 0.8), 0.05, 0.95)
    r = q if delta > 0 else base
    cp = 0.5 * (r + base)
    res = {
        "gid": gid,
        "pid": np.arange(n),
        "season": np.full(n, 2025),
        "y": y,
        "p_base": base,
        "p_Cp": cp,
        "p_R": r,
        "p_PL": cp,
    }
    shuf = {"gid": gid, "season": res["season"], "y": y, "p_base": base, "p_R": base}
    meta = pl.DataFrame(
        {
            "game_id": gid,
            "starter10": rng.random(n),
            "n_played_prior": rng.integers(5, 50, n).astype(float),
            "is_b2b_second": (rng.random(n) < 0.15).astype(float),
            "wl_min_last": rng.uniform(0, 40, n),
        }
    )
    return res, shuf, meta


def test_decide_pass_and_each_failure_mode() -> None:
    res, shuf, meta = _res(1.0)
    v = H.decide_holdout(H.score_holdout(res, shuf, meta, 2025), True)
    assert v["pass"] and v["primary"] and v["placebo"] and v["shuffle"]["ok"]
    assert not H.decide_holdout(H.score_holdout(res, shuf, meta, 2025), False)["pass"]
    res0, shuf0, meta0 = _res(0.0)
    v0 = H.decide_holdout(H.score_holdout(res0, shuf0, meta0, 2025), True)
    assert not v0["pass"] and not v0["primary"]
    # a gain smaller than the floor fails even with CI below zero
    res1, shuf1, meta1 = _res(1.0)
    res1["p_R"] = 0.97 * res1["p_R"] + 0.03 * res1["p_base"]
    sc = H.score_holdout(res1, shuf1, meta1, 2025)
    d = {x["arm"]: x for x in sc["rows"]}["R_minus_base"]["delta"]
    assert (H.decide_holdout(sc, True)["primary"]) == (d <= H.FLOOR)
    with pytest.raises(ValueError, match="only the held-out season"):
        bad = dict(res)
        bad["season"] = np.where(np.arange(len(res["y"])) % 2 == 0, 2024, 2025)
        H.score_holdout(bad, shuf, meta, 2025)


def test_floor_and_tolerances_are_the_declared_ones() -> None:
    assert (
        H.FLOOR == -0.010
        and H.PLACEBO_MARGIN == -0.003
        and H.ECE_TOL == 0.005
        and H.SLICE_TOL == 0.01
        and H.SHUF_TOL == 0.003
    )


def _planted() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    d0 = dt.date(2022, 11, 1)
    gid = 0
    for day in range(0, 900, 3):
        d = d0 + dt.timedelta(days=day)
        season = d.year if d.month >= 8 else d.year - 1
        for _ in range(12):
            gid += 1
            x, z = rng.normal(), rng.normal()
            played = float(rng.random() < 1 / (1 + np.exp(-(0.8 * x + 1.2 * z))))
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


def test_holdout_shuffle_check_passes_with_the_permutation_and_fails_without_it() -> None:
    fr = _planted()
    arms = {"base": ["x"], "R": ["x", "z"]}
    for permute, want_ok in ((True, True), (False, False)):
        res, _ = RR.walk(
            fr,
            arms,
            ContextResidualConfig(),
            shuffle=True,
            permute=permute,
            min_train=300,
            n_estimators=20,
        )
        sm = res["season"] == 2023
        d = RR._ll(res["p_R"][sm], res["y"][sm]) - RR._ll(res["p_base"][sm], res["y"][sm])
        st = RR.WE.delta_stats(d, res["gid"][sm])
        assert H.shuffle_check({"season": 2023, **st})["ok"] == want_ok
