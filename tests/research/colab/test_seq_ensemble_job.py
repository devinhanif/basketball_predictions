# ruff: noqa: E501
"""seq_ensemble job: sha refusal, input transforms (knockout, shuffle), a tiny end-to-end run."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from research.colab.jobs import load_job
from research.features.player_sequences import CTX_FEATURES, STEP_FEATURES

ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = ROOT / "research/colab/jobs/seq_ensemble"
MARK = "=== RESULTS BELOW ===\n"


def _builder() -> ModuleType:
    sys.path.insert(0, str(JOB_DIR))
    try:
        import build_notebook

        return build_notebook
    finally:
        sys.path.pop(0)


def _doc(tmp: Path, status: str = "FROZEN") -> tuple[Path, str]:
    p = tmp / "ENSEMBLE.md"
    p.write_text(f"# t\nStatus: {status} x\nrule\n{MARK}later\n")
    return p, hashlib.sha256(p.read_text().split(MARK)[0].encode()).hexdigest()


def _module(tmp: Path, sha: str) -> ModuleType:
    b = _builder()
    path = tmp / "train.py"
    path.write_text(b.script(sha))
    spec = importlib.util.spec_from_file_location("seq_ensemble_train_t", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _npz(path: Path, seasons: tuple[int, ...], n_per: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    parts = []
    for s in seasons:
        start = {
            2019: "2019-11-01",
            2020: "2020-12-25",
            2022: "2022-10-20",
            2023: "2023-10-20",
            2024: "2024-10-20",
        }[s]
        day = np.datetime64(start) + np.sort(rng.integers(0, 230, n_per)).astype("timedelta64[D]")
        parts.append((s, day))
    season = np.concatenate([np.full(n_per, s, dtype=np.int16) for s, _ in parts])
    days = (
        np.concatenate([d for _, d in parts])
        .astype("datetime64[D]")
        .astype(np.int64)
        .astype(np.int32)
    )
    n = len(season)
    k, ns, nc = 20, len(STEP_FEATURES), len(CTX_FEATURES)
    pid = rng.integers(1, 40, n)
    base = rng.gamma(3.0, 2.0, (n, 4)).astype(np.float32)
    np.savez_compressed(
        path,
        seq=rng.normal(size=(n, k, ns)).astype(np.float16),
        mask=(rng.random((n, k)) < 0.9).astype(np.uint8),
        opp_step=rng.integers(0, 30, (n, k)).astype(np.int16),
        ctx=rng.normal(size=(n, nc)).astype(np.float32),
        player_id=pid.astype(np.int64),
        team_idx=rng.integers(1, 31, n).astype(np.int16),
        opp_idx=rng.integers(1, 31, n).astype(np.int16),
        date_days=days,
        season=season,
        game_id=np.array([f"00{s}{i:06d}"[:12] for i, s in enumerate(season)], dtype="U12"),
        y=(base + rng.normal(size=(n, 4))).clip(0).astype(np.float32),
        base=base,
        n_prior=rng.integers(3, 60, n).astype(np.int32),
        stat_scale=np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32),
        step_features=np.array(STEP_FEATURES),
        ctx_features=np.array(CTX_FEATURES),
    )


def test_job_definition_is_valid_and_inputs_travel_with_the_rule() -> None:
    job = load_job("seq_ensemble")
    assert not job.touches_holdout and job.preregistration_id is None
    paths = [i.path for i in job.inputs]
    assert paths[0].endswith("seq_ensemble_cur.npz") and "docs/prereg/ENSEMBLE.md" in paths
    assert "metrics.json" in job.artifacts and "oof_E3.npz" in job.artifacts


def test_builder_refuses_unfrozen_document(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = _builder()
    monkeypatch.setattr(b, "DOC", _doc(tmp_path, "DRAFT")[0])
    with pytest.raises(SystemExit):
        b.rule_sha()
    monkeypatch.setattr(b, "DOC", _doc(tmp_path, "FROZEN")[0])
    assert len(b.rule_sha()) == 64


def test_check_rule_refuses_wrong_sha_and_draft(tmp_path: Path) -> None:
    doc, sha = _doc(tmp_path)
    mod = _module(tmp_path, sha)
    assert mod.check_rule(tmp_path) == sha
    doc.write_text(doc.read_text().replace("rule\n", "rule edited\n", 1))
    with pytest.raises(RuntimeError, match="sha mismatch"):
        mod.check_rule(tmp_path)
    doc.write_text(f"# t\nStatus: DRAFT x\nrule\n{MARK}")
    with pytest.raises(RuntimeError, match="not FROZEN"):
        mod.check_rule(tmp_path)
    (tmp_path / "ENSEMBLE.md").unlink()
    with pytest.raises(FileNotFoundError):
        mod.check_rule(tmp_path)


def test_knockout_and_shuffle_transforms(tmp_path: Path) -> None:
    _, sha = _doc(tmp_path)
    mod = _module(tmp_path, sha)
    _npz(tmp_path / "c.npz", (2022, 2023), 120, 0)
    d = mod.load_npz(tmp_path / "c.npz")
    k = mod.zero_report(dict(d))
    names = [str(x) for x in d["ctx_features"]]
    rep = [names.index(c) for c in mod.REPORT_CTX]
    oth = [i for i in range(len(names)) if i not in rep]
    assert np.abs(k["ctx"][:, rep]).sum() == 0 and np.array_equal(
        k["ctx"][:, oth], d["ctx"][:, oth]
    )
    s = mod.shuffle_within_date(
        {kk: (v.copy() if hasattr(v, "copy") else v) for kk, v in d.items()}
    )
    assert np.array_equal(s["y"], d["y"]) and np.array_equal(s["base"], d["base"])
    assert np.array_equal(s["date_days"], d["date_days"]) and not np.array_equal(s["ctx"], d["ctx"])
    for day in np.unique(d["date_days"]):  # permutation stays inside the date
        w = d["date_days"] == day
        assert sorted(map(tuple, s["ctx"][w].round(5))) == sorted(map(tuple, d["ctx"][w].round(5)))


def test_end_to_end_tiny_run_writes_artifacts_and_no_future_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc, sha = _doc(tmp_path)
    mod = _module(tmp_path, sha)
    _npz(tmp_path / "seq_ensemble_cur.npz", (2022, 2023, 2024), 150, 1)
    _npz(tmp_path / "seq_ensemble_hist.npz", (2019, 2020), 150, 2)
    monkeypatch.setenv("NBA_SMOKE", "100000")
    monkeypatch.setenv("NBA_PARQUET", str(tmp_path / "seq_ensemble_cur.npz"))
    monkeypatch.setenv("NBA_ARTIFACT_ROOT", str(tmp_path / "art"))
    monkeypatch.setenv("NBA_RUN_STAMP", "t")
    monkeypatch.setenv("NBA_PROGRAMS", "E2,E3K,E2o")
    infos = mod.main()
    out = tmp_path / "art" / "t"
    assert set(infos) == {"E2", "E3K", "E2o"}
    for p in ("E2", "E3K", "E2o"):
        z = np.load(out / f"oof_{p}.npz")
        assert z["q"].shape[1:] == (4, 19) and np.isfinite(z["q"]).all()
        assert (np.diff(z["q"], axis=2) >= -1e-6).all() and int(z["season"].max()) <= 2024
    z2 = np.load(out / "oof_E2.npz")
    assert set(np.unique(z2["season"]).tolist()) <= {2023, 2024}
    assert json.loads((out / "metrics.json").read_text())["rule_sha256"] == sha
    # a resumed run skips finished programs
    mod.main()


def test_refuses_season_2025_input(tmp_path: Path) -> None:
    _, sha = _doc(tmp_path)
    mod = _module(tmp_path, sha)
    _npz(tmp_path / "c.npz", (2024,), 50, 0)
    d = dict(np.load(tmp_path / "c.npz"))
    d["season"][:] = 2025
    np.savez(tmp_path / "bad.npz", **d)
    with pytest.raises(ValueError, match="frozen holdout"):
        mod.load_npz(tmp_path / "bad.npz")
