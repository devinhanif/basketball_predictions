# ruff: noqa: E501
"""The MONTHLY refit job on SYNTHETIC arrays (no real data, no GPU): refuses without the frozen rule and a monthly-refit log row for the month, starts from the previous
bundle's weights, saves three new state dicts with sha256, and the Mac assembles a bundle that verifies and that the live selection rule accepts for the new month."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from nba.daily.avg_shadow import RULE_SHA256
from nba.daily.avg_shadow import bundle as B

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "research/colab/jobs/avg_shadow_monthly"
REB = ROOT / "research/colab/jobs/avg_shadow_rebuild"
MONTH = "2026-11"


def _load(path: Path, name: str):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load(ROOT / "tests/daily/test_avg_shadow_rebuild.py", "avg_shadow_rebuild_tests_for_monthly")


@pytest.fixture(scope="module")
def stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("monthly")
    R.synth(
        work / "seq_ensemble_rt_cur25.npz",
        {
            2022: ("2022-10", "2023-06"),
            2023: ("2023-10", "2024-06"),
            2024: ("2024-10", "2025-06"),
            2025: ("2025-10", "2026-06"),
        },
        14,
        0,
    )
    R.synth(
        work / "seq_ensemble_rt_hist.npz",
        {2019: ("2019-10", "2020-03"), 2020: ("2020-12", "2021-06"), 2021: ("2021-10", "2022-06")},
        14,
        1,
    )
    R.synth(work / "seq_ensemble_rt_live.npz", {2026: ("2026-10", "2026-10")}, 30, 2)
    import hashlib

    info = {
        "month_block": MONTH,
        "before_date": f"{MONTH}-01",
        "npz_sha256": hashlib.sha256((work / "seq_ensemble_rt_live.npz").read_bytes()).hexdigest(),
    }
    (work / "export_info_live.json").write_text(json.dumps(info))
    B.write_fixture_bundle(
        work / "prev_bundle",
        month_block="2026-10",
        fit_through="2026-06-13",
        installed_at=datetime(2026, 10, 14, 21, 0),
    )
    return work


def _run(
    stage: Path, out: Path, rule_doc: bool = True, log_row: bool = True
) -> subprocess.CompletedProcess[str]:
    p = stage / "AVG_SHADOW_LIVE_2.md"
    if rule_doc:
        p.write_text((ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md").read_text())
    elif p.exists():
        p.unlink()
    row = f"| 2026-11-01 | {'AVG_SHADOW_LIVE_2 monthly refit' if log_row else 'x'} {MONTH} | training only |\n"
    (stage / "HOLDOUT_ACCESS_LOG.md").write_text("header\n" + row)
    env = {
        **os.environ,
        "NBA_SMOKE": "200",
        "NBA_PARQUET": str(stage / "seq_ensemble_rt_cur25.npz"),
        "NBA_ARTIFACT_ROOT": str(out),
        "NBA_RUN_STAMP": "t",
    }
    return subprocess.run(
        [sys.executable, str(JOB / "avg_shadow_monthly_train.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=stage,
        timeout=600,
    )


def test_notebook_is_built_for_the_pinned_rule_and_matches_its_script() -> None:
    BN = _load(JOB / "build_notebook.py", "avg_shadow_monthly_build_notebook")
    assert BN.RULE_SHA_PIN == "9afbfeb6" == RULE_SHA256[:8]
    code, nb = BN.build()
    assert RULE_SHA256 in code
    assert "".join(nb["cells"][1]["source"]).rstrip("\n") == code.replace(
        'if __name__ == "__main__":\n    main()\n', ""
    ).rstrip("\n")
    assert (JOB / "avg_shadow_monthly_train.py").read_text() == code, (
        "the committed script is stale: rerun build_notebook.py"
    )
    committed = json.loads(
        (JOB / "avg_shadow_monthly.ipynb").read_text()
    )  # ruff format adds cell ids; compare the sources
    assert [c["source"] for c in committed["cells"]] == [c["source"] for c in nb["cells"]], (
        "the committed notebook is stale: rerun build_notebook.py"
    )
    assert (
        "SeqNet" in code
        and "load_state_dict" in code
        and "seed_everything(1000 + k_)" in code
        and "WARM = dict(epochs=3, lr=3e-4, batch=1024)" in code
    )


def test_refuses_without_the_rule_or_the_monthly_log_row(stage: Path, tmp_path: Path) -> None:
    r = _run(stage, tmp_path, rule_doc=False)
    assert r.returncode != 0 and "not staged" in r.stderr + r.stdout
    r = _run(stage, tmp_path, log_row=False)
    assert r.returncode != 0 and "no holdout-log row" in r.stderr + r.stdout


def test_smoke_run_starts_from_previous_weights_and_the_mac_assembles_the_next_month(
    stage: Path, tmp_path: Path
) -> None:
    r = _run(stage, tmp_path)
    assert r.returncode == 0, r.stderr[-1500:]
    out = tmp_path / "t"
    parts = json.loads((out / "bundle_parts.json").read_text())
    assert (
        parts["rule_sha256"] == RULE_SHA256
        and parts["month_block"] == MONTH
        and parts["kind"] == "E2_W_ALL_MONTHLY"
    )
    assert (
        parts["fit_through"] < f"{MONTH}-01" and parts["fit_through"] >= "2026-10-01"
    )  # the live October rows are in the pool
    assert parts["prev_model_id"] == B.verify(stage / "prev_bundle").model_id
    for n in ("seed0.pt", "seed1.pt", "seed2.pt", "vocab.json"):
        assert B.file_sha256(out / n) == parts["weights_sha256"][n]
    prev = {
        n: B.file_sha256(stage / "prev_bundle" / n) for n in ("seed0.pt", "seed1.pt", "seed2.pt")
    }
    assert all(B.file_sha256(out / n) != prev[n] for n in prev), (
        "the warm block must move the weights"
    )
    # the Mac side: the smoke flag blocks assembly, a real-looking run (smoke False) assembles with the previous gate and the next month's stamps
    AB = _load(REB / "assemble_bundle.py", "avg_shadow_rebuild_assemble_for_monthly")
    with pytest.raises(ValueError, match="SMOKE"):
        AB.assemble(out, stage / "prev_bundle" / "gate.json", "2026-06-13", tmp_path / "bundle")
    parts["smoke"] = False
    (out / "bundle_parts.json").write_text(json.dumps(parts))
    b = AB.assemble(out, stage / "prev_bundle" / "gate.json", "2026-06-13", tmp_path / "bundle")
    assert (
        b.month_block == MONTH
        and b.fit_through < date(2026, 11, 1)
        and b.manifest["kind"] == "E2_W_ALL_MONTHLY"
    )
    root = tmp_path / "bundles"
    B.install(stage / "prev_bundle", root, datetime(2026, 10, 14, 21, 0))
    B.install(tmp_path / "bundle", root, datetime(2026, 11, 1, 15, 0))
    sel, stale = B.select_bundle(root, date(2026, 11, 2), datetime(2026, 11, 2, 20, 0))
    assert sel is not None and sel.month_block == MONTH and not stale
    # the same engine as the October bundle's: the live inference loads the new weights
    import torch

    from nba.daily.avg_shadow.infer import build_net

    sd = torch.load(root / MONTH / "seed0.pt", map_location="cpu")
    build_net(
        int(parts["n_step"]),
        int(parts["n_ctx"]),
        len(json.loads((root / MONTH / "vocab.json").read_text())),
        np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32),
    ).load_state_dict(sd)


def test_refuses_a_live_row_on_or_after_the_month_start(stage: Path, tmp_path: Path) -> None:
    bad = tmp_path / "stage"
    bad.mkdir()
    for f in stage.iterdir():
        if f.is_file():
            (bad / f.name).write_bytes(f.read_bytes())
    import shutil

    shutil.copytree(stage / "prev_bundle", bad / "prev_bundle")
    R.synth(
        bad / "seq_ensemble_rt_live.npz", {2026: ("2026-10", "2026-11")}, 30, 2
    )  # rows into November
    info = json.loads((bad / "export_info_live.json").read_text())
    import hashlib

    info["npz_sha256"] = hashlib.sha256((bad / "seq_ensemble_rt_live.npz").read_bytes()).hexdigest()
    (bad / "export_info_live.json").write_text(json.dumps(info))
    r = _run(bad, tmp_path / "o")
    assert r.returncode != 0 and "is not before" in r.stderr + r.stdout


def _stage_inputs(
    tmp: Path, stage_dir: Path, log_row: bool = True, installed: bool = True
) -> dict[str, Path]:
    import hashlib
    import shutil

    out, rebuild, bundles = tmp / "out", tmp / "rebuild", tmp / "bundles"
    (out / MONTH).mkdir(parents=True)
    rebuild.mkdir()
    for n in ("seq_ensemble_rt_cur25.npz", "seq_ensemble_rt_hist.npz"):
        shutil.copy2(stage_dir / n, rebuild / n)
    live = out / MONTH / "seq_ensemble_rt_live.npz"
    shutil.copy2(stage_dir / "seq_ensemble_rt_live.npz", live)
    info = {
        "month_block": MONTH,
        "before_date": f"{MONTH}-01",
        "targets": 1,
        "max_date": "2026-10-31",
        "npz_sha256": hashlib.sha256(live.read_bytes()).hexdigest(),
    }
    (out / MONTH / "export_info_live.json").write_text(json.dumps(info))
    B.write_fixture_bundle(
        bundles / "2026-10",
        month_block="2026-10",
        fit_through="2026-06-13",
        installed_at=datetime(2026, 10, 14, 21, 0) if installed else None,
    )
    log = tmp / "log.md"
    log.write_text(f"| {'AVG_SHADOW_LIVE_2 monthly refit' if log_row else 'x'} {MONTH} |\n")
    return {
        "bundles": bundles,
        "rebuild": rebuild,
        "out": out,
        "log": log,
        "rule": ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md",
    }


def test_stage_monthly_builds_the_run_folder_and_refuses_what_it_should(
    stage: Path, tmp_path: Path
) -> None:
    SM = _load(JOB / "stage_monthly.py", "avg_shadow_monthly_stage")
    ok = _stage_inputs(tmp_path / "a", stage)
    run = SM.stage(MONTH, tmp_path / "drive", **ok)
    assert (
        (run / "prev_bundle" / "manifest.json").exists()
        and (run / "export_info_live.json").exists()
        and (run / "AVG_SHADOW_LIVE_2.md").exists()
    )
    assert (tmp_path / "drive" / MONTH / "seq_ensemble_rt_live.npz").exists()
    with pytest.raises(SystemExit, match="already exists"):
        SM.stage(MONTH, tmp_path / "drive", **ok)
    with pytest.raises(SystemExit, match="monthly refit"):
        SM.stage(MONTH, None, **_stage_inputs(tmp_path / "b", stage, log_row=False))
    with pytest.raises(SystemExit, match="no installed bundle"):
        SM.stage(MONTH, None, **_stage_inputs(tmp_path / "c", stage, installed=False))


def test_setup_cell_finds_the_staged_folder(stage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nb = json.loads((JOB / "avg_shadow_monthly.ipynb").read_text())
    src = "".join(nb["cells"][2]["source"])
    monkeypatch.delenv("NBA_PARQUET", raising=False)
    monkeypatch.delenv("NBA_ARTIFACT_ROOT", raising=False)
    monkeypatch.chdir(stage)
    ns: dict[str, object] = {"os": os, "Path": Path, "RULE_SHA": "x"}
    exec(src, ns)  # noqa: S102
    assert (
        Path(os.environ["NBA_PARQUET"]).resolve() == (stage / "seq_ensemble_rt_cur25.npz").resolve()
    )
    assert Path(os.environ["NBA_ARTIFACT_ROOT"]).resolve() == (stage / "artifacts").resolve()
    monkeypatch.delenv("NBA_PARQUET")
    monkeypatch.delenv("NBA_ARTIFACT_ROOT")
    monkeypatch.chdir(stage / "prev_bundle")
    with pytest.raises(FileNotFoundError):
        exec(src, ns)  # noqa: S102
