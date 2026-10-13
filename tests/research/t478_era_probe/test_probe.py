# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return,attr-defined,unused-ignore,union-attr"
# ruff: noqa: E501
"""T478 era probe: the job reads 2019-2024 and cannot read 2025, wraps `fit` without changing a single OOF value, and the Mac-side AUC reader behaves.
Torch runs in clean interpreters (torch after lightgbm segfaults on macOS)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from research.colab.jobs import load_job
from research.eval import t478_era_probe as P
from tests.research.seq_full_mix.test_rt_job import _npz

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / "research/colab/jobs/t478_era_probe"
MARK = "=== RESULTS BELOW ===\n"


def _builder():
    import importlib.util

    spec = importlib.util.spec_from_file_location("t478_build", JOB / "build_notebook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plan(tmp: Path, status: str) -> Path:
    p = tmp / "PLAN.md"
    p.write_text(f"# t\nStatus: {status}\nplan\n{MARK}later\n")
    return p


def _py(code: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        env={**os.environ, **env},
    )


def _load(train: Path) -> str:
    return f"import importlib.util, json, numpy as np\nspec = importlib.util.spec_from_file_location('t', {str(train)!r})\nm = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"


def test_job_is_defined_and_not_a_holdout_touch() -> None:
    job = load_job("t478_era_probe")
    assert not job.touches_holdout and job.preregistration_id is None
    assert (
        job.inputs[0].path.endswith("seq_ensemble_rt_cur.npz") and "rt_hist" in job.inputs[1].path
    )
    assert (
        "states_WALL_E2.npz" in job.artifacts
        and (ROOT / job.notebook).exists()
        or job.notebook.exists()
    )


def test_builder_refuses_a_draft_plan_unless_previewing(tmp_path: Path) -> None:
    b = _builder()
    with pytest.raises(SystemExit):
        b.build(False, _plan(tmp_path, "DRAFT"))
    code, nb = b.build(True, _plan(tmp_path, "DRAFT"))
    ast.parse(code)
    assert (
        "__PLAN_SHA__" not in code
        and "__MODE__" not in code
        and [c["cell_type"] for c in nb["cells"]] == ["markdown", "code", "code", "code"]
    )
    code2, _ = b.build(False, _plan(tmp_path, "COMMITTED 2026-10-12"))
    assert 'MODE = "sel"' in code2 and "main_probe" in code2


def test_probe_reads_to_2024_cannot_read_2025_and_wrapper_is_equivalent(tmp_path: Path) -> None:
    b = _builder()
    code, _ = b.build(True, _plan(tmp_path, "DRAFT"))
    train = tmp_path / "train.py"
    train.write_text(code)
    cur, hist = tmp_path / "seq_ensemble_rt_cur.npz", tmp_path / "seq_ensemble_rt_hist.npz"
    _npz(cur, (2022, 2023, 2024), 150, 1)
    _npz(hist, (2019, 2020, 2021), 150, 2)
    env = {
        "NBA_SMOKE": "100000",
        "NBA_PARQUET": str(cur),
        "NBA_ARTIFACT_ROOT": str(tmp_path / "art"),
        "NBA_RUN_STAMP": "t",
    }
    r = _py(_load(train) + "infos = m.main_probe(); print('OK')\n", tmp_path, env)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2500:]
    out = tmp_path / "art" / "t"
    st = np.load(out / "states_WALL_E2.npz")
    oof = np.load(out / "oof_WALL_E2.npz")
    assert set(np.unique(st["season"]).tolist()) <= {2019, 2020, 2021, 2022, 2023, 2024} and set(
        np.unique(oof["season"]).tolist()
    ) == {2023, 2024}
    assert (
        st["z"].shape[1] == 32
        and st["z"].shape[0] == len(st["season"])
        and st["era_zero"].dtype == bool
    )
    info = json.loads((out / "info_WALL_E2.json").read_text())
    assert (
        info["seeds"] == [0]
        and info["season_2025_used"] is False
        and max(int(s) for s in info["states_by_season"]) <= 2024
    )
    # seeded equivalence: the same walk called directly (no wrapper, same pools) gives identical OOF values
    direct = (
        _load(train)
        + "import torch\nfrom pathlib import Path\n"
        + f"cur = m.drop_after(m.load_npz_25({str(cur)!r}), 2024); hist = m.load_npz_25({str(hist)!r})\n"
        + "cur = m.subsample(cur, 100000); hist = m.subsample(hist, 100000)\n"
        + "d = m.prepare('E2', cur, hist, 'WALL'); m.fit = m._orig_fit\n"
        + f"out = Path({str(tmp_path / 'direct')!r}); out.mkdir()\n"
        + "m.run_walk_forward('WALL_E2', d, torch.device('cpu'), 16, out, True, 'WALL'); print('OK')\n"
    )
    r2 = _py(direct, tmp_path, env)
    assert r2.returncode == 0 and "OK" in r2.stdout, r2.stderr[-2500:]
    a, c = np.load(out / "oof_WALL_E2.npz"), np.load(tmp_path / "direct" / "oof_WALL_E2.npz")
    assert (
        np.array_equal(a["game_id"], c["game_id"])
        and np.allclose(a["q"], c["q"], atol=1e-6)
        and np.allclose(a["mean"], c["mean"], atol=1e-6)
    )
    # a 2025 row anywhere is refused
    _npz(cur, (2023, 2024, 2025), 60, 1)
    r3 = _py(_load(train) + "m.main_probe()\n", tmp_path, {**env, "NBA_RUN_STAMP": "t3"})
    assert r3.returncode != 0
    _npz(cur, (2022, 2023, 2024), 60, 1)
    _npz(hist, (2019, 2020, 2021, 2025), 40, 2)
    assert (
        _py(_load(train) + "m.main_probe()\n", tmp_path, {**env, "NBA_RUN_STAMP": "t4"}).returncode
        != 0
    )


def _states(
    shift: dict[int, float], n: int = 600, d: int = 8, seed: int = 0
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    season = np.repeat(np.array(sorted(shift), dtype=np.int16), n)
    z = rng.normal(size=(len(season), d)).astype(np.float16)
    z[:, 0] += np.array([shift[int(s)] for s in season], dtype=np.float16)
    gid = np.array([f"{int(s) % 100:02d}{i // 5:08d}" for i, s in enumerate(season)], dtype="U12")
    return {"z": z, "season": season, "game_id": gid, "era_zero": np.ones(len(season), dtype=bool)}


def test_reader_separates_a_planted_shift_and_the_null_pair_reads_a_coin_flip() -> None:
    res = P.run_probe(
        _states({2019: 4.0, 2020: 0.0, 2021: 0.0, 2022: 0.0, 2023: 0.0, 2024: 0.0}), boot=30
    )
    pr = res["pairs"]
    assert pr["2019v2022"]["auc"] > 0.95 and pr["2019v2022"]["flagged"]
    assert abs(pr["2022a_v_2022b"]["auc"] - 0.5) < 0.1 and not pr["2022v2023"]["flagged"]
    assert res["limit_from"] == "2022v2024" and res["limit"] is not None
    assert "2019v2022" in P.to_markdown(res)
    half = P.run_probe(
        _states({2019: 4.0, 2020: 0.0, 2021: 0.0, 2022: 0.0, 2023: 0.0, 2024: 0.0}),
        boot=10,
        cols=slice(0, 4),
    )
    assert half["pairs"]["2019v2022"]["auc"] > 0.9


def test_reader_refuses_season_2025() -> None:
    with pytest.raises(ValueError):
        P.run_probe(_states({2022: 0.0, 2025: 0.0}), boot=5)
