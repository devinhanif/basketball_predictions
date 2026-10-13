# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_LEARNING_CURVE Colab job: builder refusal, the run script refuses without the frozen document and the loader-review attestation, nothing after 2023 loads, window row
sets (the job's copy equals the exporter's), pools hold only earlier rows and only the window's seasons, cold-fit epoch records, restart skips finished arms, the ceiling reports
unfinished arms. Runs in clean interpreters (torch after lightgbm segfaults on macOS)."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from research.colab.jobs import load_job
from research.features import seq_learning_curve_export as X
from research.features.player_sequences import STEP_FEATURES

ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = ROOT / "research/colab/jobs/seq_learning_curve"
MARK = "=== RESULTS BELOW ===\n"
SEGS = {"S1": (2015, 2016, 2017, 2018), "S2": (2019, 2020, 2021), "S3": (2022, 2023)}


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("slc_build_t", JOB_DIR / "build_notebook.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc(tmp: Path, status: str = "FROZEN") -> tuple[Path, str]:
    p = tmp / "SEQ_LEARNING_CURVE.md"
    body = f"# t\nStatus: **{status} x**\nrule\n"
    p.write_text(f"{body}{MARK}later\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def _npz(path: Path, seasons: tuple[int, ...], n_per: int, seed: int, nc: int) -> None:
    """Row identity (season, date, game, player, n_prior) depends on ``seed`` only, so the raw and norm variants of a segment hold the same rows."""
    rid = np.random.default_rng(seed)
    season = np.concatenate([np.full(n_per, s, dtype=np.int16) for s in seasons])
    days = (
        np.concatenate([np.datetime64(f"{s}-10-25") + np.sort(rid.integers(0, 230, n_per)).astype("timedelta64[D]") for s in seasons])
        .astype("datetime64[D]").astype(np.int64).astype(np.int32)
    )  # fmt: skip
    n = len(season)
    pid, nprior = rid.integers(1, 40, n).astype(np.int64), rid.integers(3, 60, n).astype(np.int32)
    rng = np.random.default_rng(1000 + nc + seed)
    base = rng.gamma(3.0, 2.0, (n, 4)).astype(np.float32)
    np.savez_compressed(
        path, seq=rng.normal(size=(n, 20, len(STEP_FEATURES))).astype(np.float16), mask=(rng.random((n, 20)) < 0.9).astype(np.uint8),
        opp_step=rng.integers(0, 30, (n, 20)).astype(np.int16), ctx=rng.normal(size=(n, nc)).astype(np.float32), player_id=pid,
        team_idx=rng.integers(1, 31, n).astype(np.int16), opp_idx=rng.integers(1, 31, n).astype(np.int16), date_days=days, season=season,
        game_id=np.array([f"{seed}{s}{i:08d}"[:12] for i, s in enumerate(season)], dtype="U12"),
        y=(base + rng.normal(size=(n, 4))).clip(0).astype(np.float32), base=base, n_prior=nprior,
        norm_c=np.ones((n, 4)), stat_scale=np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32), step_features=np.array(STEP_FEATURES),
        ctx_features=np.array(X.NORM_CTX_FEATURES if nc == 41 else X.RAW_CTX_FEATURES),
    )  # fmt: skip


def _stage(
    tmp: Path,
    sha: str,
    *,
    descends: bool = True,
    rule_sha: str | None = None,
    n_per: int = 60,
    seasons_s3: tuple[int, ...] = (2022, 2023),
) -> None:
    exports = {}
    for v, nc in (("norm", 41), ("raw", 34)):
        for i, (s, labs) in enumerate(SEGS.items()):
            f = tmp / f"seq_learning_curve_{v}_{s}.npz"
            _npz(f, seasons_s3 if s == "S3" else labs, n_per, 10 * i, nc)
            exports[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    (tmp / "loader_review.md").write_text("# review\n")
    att = {
        "rule_sha256": rule_sha or sha, "freeze_commit": "f" * 40, "review_path": "docs/reviews/seq_learning_curve_loader_2026-10-13.md", "review_commit": "a" * 40,
        "review_sha256": hashlib.sha256(b"# review\n").hexdigest(), "review_file": "loader_review.md", "review_descends_from_freeze": descends, "exports": exports,
        "exports_manifest_sha256": X.manifest_sha(exports),
    }  # fmt: skip
    (tmp / "loader_review_attestation.json").write_text(json.dumps(att))


def _run(train: Path, cwd: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    code = (
        "import json, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(train)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "infos = m.main()\n"
        "print('INFOS ' + json.dumps({k: {a: b for a, b in v.items()} for k, v in infos.items()}))\n"
    )
    env = {
        **os.environ,
        "NBA_SMOKE": "100000",
        "NBA_ARTIFACT_ROOT": str(cwd / "art"),
        "NBA_RUN_STAMP": "t",
        "NBA_PARQUET": str(cwd / "seq_learning_curve_norm_S3.npz"),
        **env_extra,
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _infos(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, r.stderr[-2500:]
    return json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("INFOS "))[6:])


def _train(
    tmp: Path, status: str = "FROZEN", patch: tuple[str, str] | None = None
) -> tuple[Path, str]:
    doc, sha = _doc(tmp, status)
    code, _ = _builder().build(doc)
    if patch:
        assert patch[0] in code
        code = code.replace(*patch)
    t = tmp / "train.py"
    t.write_text(code)
    return t, sha


def test_job_definition_and_builder_refuses_an_unfrozen_rule(tmp_path: Path) -> None:
    job = load_job("seq_learning_curve")
    assert (
        not job.touches_holdout
        and job.notebook.name == "seq_learning_curve.ipynb"
        and job.notebook.exists()
    )
    assert job.inputs[0].path.endswith("seq_learning_curve_norm_S3.npz")
    assert {"loader_review.md", "loader_review_attestation.json", "SEQ_LEARNING_CURVE.md"} <= {
        Path(i.path).name for i in job.inputs
    }
    b = _builder()
    draft, _ = _doc(tmp_path, "DRAFT")
    with pytest.raises(SystemExit):
        b.build(draft)
    frozen, sha = _doc(tmp_path, "FROZEN")
    code, nb = b.build(frozen)
    ast.parse(code)
    assert (
        f'RULE_SHA = "{sha}"' in code
        and "__RULE_SHA__" not in code
        and [c["cell_type"] for c in nb["cells"]] == ["markdown", "code", "code", "code"]
    )
    committed = json.loads((JOB_DIR / "seq_learning_curve.ipynb").read_text())
    assert any(
        f'RULE_SHA = "{b.rule_sha()}"' in "".join(c["source"]) for c in committed["cells"]
    )  # the committed notebook is the frozen rule's


def test_run_script_refuses_without_the_review_attestation_and_on_every_mismatch(
    tmp_path: Path,
) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha)
    ok_files = {p.name: p.read_bytes() for p in tmp_path.glob("loader_review*")}
    (tmp_path / "loader_review_attestation.json").unlink()
    r = _run(train, tmp_path, {"NBA_ARMS": "RAW_1"})
    assert (
        r.returncode != 0
        and "loader_review_attestation.json not staged" in r.stderr
        and "hard stop" in r.stderr
    )
    for name, data in ok_files.items():
        (tmp_path / name).write_bytes(data)
    att = json.loads((tmp_path / "loader_review_attestation.json").read_text())
    for edit, needle in (
        ({"review_descends_from_freeze": False}, "does not descend"),
        ({"rule_sha256": "0" * 64}, "different rule sha"),
        ({"review_sha256": "0" * 64}, "differs from the attested"),
        (
            {"exports": {**att["exports"], "seq_learning_curve_raw_S1.npz": "0" * 64}},
            "differs from the attested sha",
        ),
    ):
        (tmp_path / "loader_review_attestation.json").write_text(json.dumps({**att, **edit}))
        r = _run(train, tmp_path, {"NBA_ARMS": "RAW_1"})
        assert r.returncode != 0 and needle in r.stderr, (edit, r.stderr[-400:])
    (tmp_path / "loader_review_attestation.json").write_text(
        json.dumps({k: v for k, v in att.items() if k != "review_commit"})
    )
    assert _run(train, tmp_path, {"NBA_ARMS": "RAW_1"}).returncode != 0
    (tmp_path / "loader_review_attestation.json").write_text(json.dumps(att))
    (tmp_path / "SEQ_LEARNING_CURVE.md").write_text(
        (tmp_path / "SEQ_LEARNING_CURVE.md").read_text().replace("rule", "rule edited", 1)
    )
    r = _run(train, tmp_path, {"NBA_ARMS": "RAW_1"})
    assert r.returncode != 0 and "rule sha mismatch" in r.stderr
    (tmp_path / "SEQ_LEARNING_CURVE.md").unlink()
    assert "not staged" in _run(train, tmp_path, {"NBA_ARMS": "RAW_1"}).stderr


def test_nothing_after_2023_can_be_loaded(tmp_path: Path) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha, seasons_s3=(2022, 2023, 2024))
    r = _run(train, tmp_path, {"NBA_ARMS": "RAW_1"})
    assert r.returncode != 0 and "after 2023" in r.stderr


def test_smoke_run_wires_windows_pools_cold_records_restart_and_unfinished(tmp_path: Path) -> None:
    train, sha = _train(tmp_path)
    _stage(tmp_path, sha)
    arms = "RAW_1,NORM_1,NORM_3,NORM_5,NREP_4"
    infos = _infos(_run(train, tmp_path, {"NBA_ARMS": arms}))
    assert set(infos) == set(arms.split(","))
    out = tmp_path / "art" / "t"
    segs = {
        (v, s): dict(np.load(tmp_path / f"seq_learning_curve_{v}_{s}.npz"))
        for v in ("norm", "raw")
        for s in SEGS
    }
    test_keys = None
    for name, info in infos.items():
        z = np.load(out / f"oof_{name}.npz")
        assert set(np.unique(z["season"]).tolist()) == {2023}  # scored on 2023-24 only
        variant = "raw" if name.startswith("RAW") else "norm"
        n = info["window_n"]
        labs = X.window_labels(n)
        assert (
            info["window_labels"] == list(labs)
            and info["variant"] == variant
            and info["seeds"] == [3 if name == "NREP_4" else 0]
        )
        # row sets: the job's counts equal the exporter's definition applied to the same arrays
        parts = [d for (v, s), d in segs.items() if v == variant]
        season = np.concatenate([d["season"] for d in parts])
        date = np.concatenate([d["date_days"] for d in parts])
        npr = np.concatenate([d["n_prior"] for d in parts])
        keep = np.isin(season, (*labs, 2023))
        m = X.window_masks(season[keep], date[keep], npr[keep], n)
        assert (info["n_fit"], info["n_val"], info["n_test"]) == (
            int(m["fit"].sum()),
            int(m["val"].sum()),
            int(m["test"].sum()),
        )
        pools = {int(s) for b_ in info["blocks_detail"] for s in b_["pool_by_season"]}
        assert pools <= {*labs, 2023} and set(labs) - {2022} <= pools | {2022}
        assert all(b_["max_pool_date"] < b_["min_block_date"] for b_ in info["blocks_detail"])
        assert info["blocks_detail"][0]["pool_by_season"].keys() >= {
            str(s) for s in labs
        }  # the first warm pool already holds every window season
        assert len(info["cold"]) == 1 and {
            "epochs_run",
            "best_epoch",
            "best_val",
            "reached_cap",
        } <= set(info["cold"][0])
        test_keys = test_keys or info["test_keys_sha256"]
        assert (
            info["test_keys_sha256"] == test_keys
        )  # identical 2023-24 row set for every window and variant
    assert (
        infos["NORM_1"]["test_inputs_sha256"]
        == infos["NORM_5"]["test_inputs_sha256"]
        == infos["NREP_4"]["test_inputs_sha256"]
    )
    assert infos["NORM_5"]["n_fit"] > infos["NORM_3"]["n_fit"] > infos["NORM_1"]["n_fit"]
    assert (
        infos["NORM_1"]["n_val"] == infos["NORM_5"]["n_val"]
    )  # the same early-stopping validation set
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["arms_unfinished"] == [] and cfg["test_label"] == 2023 and cfg["cap_season"] == 2023
    diag = json.loads((out / "cap_diagnostic.json").read_text())
    assert (
        diag["cold_fits"] == 5 and diag["reached_cap"] == 5 and diag["budget_ok"] is False
    )  # smoke runs 1 epoch: every fit "reaches" the cap by construction
    # restart: finished arms are skipped
    r = _run(train, tmp_path, {"NBA_ARMS": arms})
    assert _infos(r) == {} and r.stdout.count("done, skipping") == 5
    # the ceiling: an exhausted budget reports the arms unfinished and writes no partial OOF
    (tmp_path / "c").mkdir()
    _train(tmp_path / "c", patch=("CEILING_MIN = 240.0", "CEILING_MIN = -1.0"))
    _stage(tmp_path / "c", sha)
    r = _run(tmp_path / "c" / "train.py", tmp_path / "c", {"NBA_ARMS": "RAW_1,NORM_1"})
    assert _infos(r) == {} and not list((tmp_path / "c" / "art" / "t").glob("oof_*.npz"))
    cfg = json.loads((tmp_path / "c" / "art" / "t" / "config.json").read_text())
    assert cfg["arms_unfinished"] == ["RAW_1", "NORM_1"]


def test_tail_window_masks_equal_the_exporters(tmp_path: Path) -> None:
    train, _ = _train(tmp_path)
    rng = np.random.default_rng(3)
    labs = np.repeat(np.arange(2015, 2024), 50)
    date = (
        (
            np.array([np.datetime64(f"{s}-10-25") for s in labs])
            + rng.integers(0, 230, len(labs)).astype("timedelta64[D]")
        )
        .astype("datetime64[D]")
        .astype(np.int64)
        .astype(np.int32)
    )
    npr = rng.integers(0, 8, len(labs)).astype(np.int32)
    np.savez(tmp_path / "w.npz", season=labs, date=date, npr=npr)
    script = (
        "import importlib.util, json, numpy as np, hashlib\n"
        f"spec = importlib.util.spec_from_file_location('t', {str(train)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"z = np.load({str(tmp_path / 'w.npz')!r})\n"
        "out = {n: {k: hashlib.sha256(v.tobytes()).hexdigest() for k, v in m.window_masks(z['season'], z['date'], z['npr'], n).items()} for n in range(1, 9)}\n"
        "print('J' + json.dumps(out))\n"
    )
    r = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    got = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("J"))[1:])
    for n in range(1, 9):
        want = X.window_masks(labs, date, npr, n)
        assert got[str(n)] == {k: hashlib.sha256(v.tobytes()).hexdigest() for k, v in want.items()}
