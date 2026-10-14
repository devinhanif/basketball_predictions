# ruff: noqa: E501
"""The Colab rebuild job end to end on SYNTHETIC arrays (no real data, no GPU): the notebook script runs in a child process, refuses without
the frozen rule and the holdout row, saves weights with sha256, and the Mac assembles a verified bundle that the live inference loads."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from nba.daily.avg_shadow import RULE_SHA256
from nba.daily.avg_shadow import bundle as B

# no lightgbm-importing nba module here: this file shares a pytest process with the torch job tests
ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "research/colab/jobs/avg_shadow_rebuild"
N_STEP, N_CTX = 27, 34


def load_job_module(name: str):  # noqa: ANN201
    """Import a script of the job directory under a UNIQUE module name (a bare ``import build_notebook`` would shadow other jobs' modules of the same name in sys.modules)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"avg_shadow_rebuild_job_{name}", JOB / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synth(path: Path, seasons: dict[int, tuple[str, str]], n_per_month: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    days, season = [], []
    for s, (a, b) in seasons.items():
        for m in np.arange(np.datetime64(a, "M"), np.datetime64(b, "M") + np.timedelta64(1, "M")):
            d0 = m.astype("datetime64[D]").astype(np.int64)
            days += list(d0 + np.sort(rng.integers(1, 26, n_per_month)))
            season += [s] * n_per_month
    n = len(days)
    order = np.argsort(days, kind="stable")
    days, season = np.array(days)[order], np.array(season)[order]
    pid = rng.integers(1, 9, n)
    np.savez(
        path,
        seq=rng.normal(size=(n, 20, N_STEP)).astype(np.float16), mask=(rng.random((n, 20)) > 0.2).astype(np.uint8),
        opp_step=rng.integers(0, 31, (n, 20)).astype(np.int16), ctx=rng.normal(size=(n, N_CTX)).astype(np.float32),
        player_id=pid.astype(np.int64), team_idx=rng.integers(1, 31, n).astype(np.int16), opp_idx=rng.integers(1, 31, n).astype(np.int16),
        date_days=days.astype(np.int32), season=season.astype(np.int16), game_id=np.array([f"00{i:010d}" for i in range(n)], dtype="U12"),
        y=rng.poisson(8, (n, 4)).astype(np.float32), base=(rng.random((n, 4)) * 8 + 1).astype(np.float32),
        n_prior=rng.integers(3, 80, n).astype(np.int32), stat_scale=np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32),
        step_features=np.array([f"s{i}" for i in range(N_STEP)]), ctx_features=np.array([f"c{i}" for i in range(N_CTX)]),
    )  # fmt: skip


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("rebuild")
    stage = work / "stage"
    stage.mkdir()
    synth(
        stage / "seq_ensemble_rt_cur25.npz",
        {
            2022: ("2022-10", "2023-06"),
            2023: ("2023-10", "2024-06"),
            2024: ("2024-10", "2025-06"),
            2025: ("2025-10", "2026-06"),
        },
        14,
        0,
    )
    synth(
        stage / "seq_ensemble_rt_hist.npz",
        {2019: ("2019-10", "2020-03"), 2020: ("2020-12", "2021-06"), 2021: ("2021-10", "2022-06")},
        14,
        1,
    )
    return stage


def _run(
    stage: Path, out: Path, rule_doc: bool = True, log_row: bool = True
) -> subprocess.CompletedProcess[str]:
    if rule_doc:
        (stage / "AVG_SHADOW_LIVE_2.md").write_text(
            (ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md").read_text()
        )
    elif (stage / "AVG_SHADOW_LIVE_2.md").exists():
        (stage / "AVG_SHADOW_LIVE_2.md").unlink()
    log = (ROOT / "docs/HOLDOUT_ACCESS_LOG.md").read_text()
    (stage / "HOLDOUT_ACCESS_LOG.md").write_text(
        log if log_row else log.replace("Colab chain rebuild for AVG_SHADOW_LIVE", "x")
    )
    env = {
        **os.environ,
        "NBA_SMOKE": "200",
        "NBA_PARQUET": str(stage / "seq_ensemble_rt_cur25.npz"),
        "NBA_ARTIFACT_ROOT": str(out),
        "NBA_RUN_STAMP": "t",
    }
    return subprocess.run(
        [sys.executable, str(JOB / "avg_shadow_rebuild_train.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=stage,
        timeout=900,
    )


def test_notebook_is_the_t478_code_plus_one_marked_block() -> None:
    BN = load_job_module("build_notebook")
    code, nb = BN.build()
    assert BN.rule_sha() == RULE_SHA256 and RULE_SHA256 in code
    body = "".join(nb["cells"][1]["source"])
    assert body.rstrip("\n") == code.replace('if __name__ == "__main__":\n    main()\n', "").rstrip(
        "\n"
    )
    assert (
        JOB / "avg_shadow_rebuild_train.py"
    ).read_text() == code  # the committed script is the built one
    assert (
        json.loads((JOB / "avg_shadow_rebuild.ipynb").read_text()) == nb
    )  # and so is the committed notebook
    assert code.count("# ---- AVG_SHADOW_LIVE install fit") == 1
    # every T478 function is present unchanged except run_walk_forward, which is the original plus the install block
    import ast

    rt = (ROOT / "research/colab/jobs/seq_full_mix_rt/seq_full_mix_rt_tail.py").read_text()
    src_rt = {
        n.name: ast.get_source_segment(rt, n)
        for n in ast.parse(rt).body
        if isinstance(n, ast.FunctionDef)
    }
    mine = ast.parse(code)
    seg = {n.name: ast.dump(n) for n in mine.body if isinstance(n, ast.FunctionDef)}
    for name in ("load_npz_25", "concat_rows", "month_blocks", "to_dev", "oof_dict"):
        assert seg[name] == ast.dump(ast.parse(src_rt[name]).body[0]), name
    walk_orig = [ln.strip() for ln in src_rt["run_walk_forward"].splitlines() if ln.strip()]
    walk_mine = {
        ln.strip()
        for ln in code[code.index("def run_walk_forward") :].split("\ndef prepare")[0].splitlines()
    }
    # formatting aside (ruff reflows long lines), every statement of T478's loop is present: the cold fit, the warm refit and the pool rule
    for needle in (
        "seed_everything(1000 + k_)",
        "pool = np.flatnonzero((date < start) & (nprior >= 3))",
        'assert date[pool].max() < date[blk].min(), "a pool row is not strictly before the block"',
    ):
        assert any(needle in ln for ln in walk_orig) and needle in walk_mine


def test_refuses_without_the_frozen_rule_or_the_holdout_row(run_dir: Path, tmp_path: Path) -> None:
    r = _run(run_dir, tmp_path, rule_doc=False)
    assert r.returncode != 0 and "not staged" in (r.stderr + r.stdout)
    r = _run(run_dir, tmp_path, log_row=False)
    assert r.returncode != 0 and "no holdout-log row" in (r.stderr + r.stdout)
    (run_dir / "AVG_SHADOW_LIVE_2.md").write_text(
        (ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md")
        .read_text()
        .replace("Status: **FROZEN", "Status: **DRAFT", 1)
    )
    log = (ROOT / "docs/HOLDOUT_ACCESS_LOG.md").read_text()
    (run_dir / "HOLDOUT_ACCESS_LOG.md").write_text(log)
    env = {
        **os.environ,
        "NBA_SMOKE": "200",
        "NBA_PARQUET": str(run_dir / "seq_ensemble_rt_cur25.npz"),
        "NBA_ARTIFACT_ROOT": str(tmp_path),
        "NBA_RUN_STAMP": "t",
    }
    r = subprocess.run(
        [sys.executable, str(JOB / "avg_shadow_rebuild_train.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=run_dir,
        timeout=300,
    )
    assert r.returncode != 0 and ("not FROZEN" in r.stderr or "mismatch" in r.stderr)


def test_notebook_and_builder_refuse_any_rule_text_but_avg_shadow_live_2_9afbfeb6(
    run_dir: Path, tmp_path: Path
) -> None:
    BN = load_job_module("build_notebook")
    assert BN.RULE_SHA_PIN == "9afbfeb6" == RULE_SHA256[:8]
    other = tmp_path / "other.md"
    other.write_text(
        (ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md")
        .read_text()
        .replace("Season labels", "Season label", 1)
    )  # still FROZEN, one word moved above the results marker
    with pytest.raises(SystemExit, match="9afbfeb6"):
        BN.rule_sha(other)
    # the old rule (sha 6e57fc11) is refused by the builder and by the notebook on the VM
    old = tmp_path / "old.md"
    old.write_text(
        "Status: **FROZEN Tue Oct 13 2026**\nold rule text\n=== RESULTS BELOW ===\nresults\n"
    )
    with pytest.raises(SystemExit, match="9afbfeb6"):
        BN.rule_sha(old)
    (run_dir / "AVG_SHADOW_LIVE_2.md").write_text(other.read_text())
    (run_dir / "HOLDOUT_ACCESS_LOG.md").write_text(
        (ROOT / "docs/HOLDOUT_ACCESS_LOG.md").read_text()
    )
    env = {
        **os.environ,
        "NBA_SMOKE": "200",
        "NBA_PARQUET": str(run_dir / "seq_ensemble_rt_cur25.npz"),
        "NBA_ARTIFACT_ROOT": str(tmp_path),
        "NBA_RUN_STAMP": "t",
    }
    r = subprocess.run(
        [sys.executable, str(JOB / "avg_shadow_rebuild_train.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=run_dir,
        timeout=300,
    )
    assert r.returncode != 0 and "is not AVG_SHADOW_LIVE_2 9afbfeb6" in r.stderr


def test_smoke_run_saves_weights_and_the_mac_assembles_a_loadable_bundle(
    run_dir: Path, tmp_path: Path
) -> None:
    r = _run(run_dir, tmp_path)
    assert r.returncode == 0, r.stderr[-1500:]
    out = tmp_path / "t"
    parts = json.loads((out / "bundle_parts.json").read_text())
    assert (
        parts["rule_sha256"] == RULE_SHA256
        and parts["month_block"] == "2026-10"
        and parts["smoke"] is True
    )
    assert (
        parts["fit_through"] < "2026-10-01"
        and parts["n_step"] == N_STEP
        and parts["n_ctx"] == N_CTX
    )
    for n in ("seed0.pt", "vocab.json"):
        assert B.file_sha256(out / n) == parts["weights_sha256"][n]
    info = json.loads((out / "info_E2.json").read_text())
    assert (
        info["install_fit"]["pool_rows"] > 0
        and info["blocks"] >= 20
        and info["oof_by_season"].keys() >= {"2023", "2024", "2025"}
    )
    # a SMOKE run is never assembled into a bundle
    AB = load_job_module("assemble_bundle")
    gate = {
        st: {
            "columns": ["log_n_prior", "starter10", "n_rep_out", "has_report", "team_game_no"],
            "med": [0.0] * 5,
            "mu": [0.0] * 5,
            "sd": [1.0] * 5,
            "coef": [[0.0, 0.0]] * 6,
        }
        for st in ("pts", "reb", "ast", "fg3m")
    }
    (tmp_path / "gate.json").write_text(json.dumps(gate))
    with pytest.raises(ValueError, match="SMOKE"):
        AB.assemble(out, tmp_path / "gate.json", "2026-06-13", tmp_path / "bundle")
    # the same artifacts without the smoke flag assemble, verify and load in the live inference (3 seeds recorded, so give it 3 state dicts)
    parts["smoke"], parts["seeds"] = False, [0, 1, 2]
    for s in (1, 2):
        (out / f"seed{s}.pt").write_bytes((out / "seed0.pt").read_bytes())
        parts["weights_sha256"][f"seed{s}.pt"] = B.file_sha256(out / f"seed{s}.pt")
    (out / "bundle_parts.json").write_text(json.dumps(parts))
    b = AB.assemble(out, tmp_path / "gate.json", "2026-06-13", tmp_path / "bundle")
    assert (
        b.month_block == "2026-10"
        and b.fit_through.isoformat() < "2026-10-01"
        and b.gate_fit_through.isoformat() == "2026-06-13"
    )
    rng = np.random.default_rng(0)
    n = 6
    np.savez(
        tmp_path / "in.npz", seq=rng.normal(size=(n, 20, N_STEP)).astype(np.float16), mask=np.ones((n, 20), np.uint8), opp_step=np.zeros((n, 20), np.int16),
        ctx=rng.normal(size=(n, N_CTX)).astype(np.float32), player_id=np.arange(1, n + 1), team_idx=np.ones(n, np.int16), opp_idx=np.ones(n, np.int16),
        base=np.ones((n, 4), np.float32) * 5,
    )  # fmt: skip
    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "nba.daily.avg_shadow.infer",
            "--bundle",
            str(b.path),
            "--in",
            str(tmp_path / "in.npz"),
            "--out",
            str(tmp_path / "out.npz"),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )
    assert cp.returncode == 0, cp.stderr[-800:]
    q = np.load(tmp_path / "out.npz")["q"]
    assert q.shape == (n, 4, 19) and (np.diff(q, axis=2) >= 0).all() and (q >= 0).all()
