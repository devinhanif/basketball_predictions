"""Colab hand-off tests; a temp dir stands in for Drive. Never touches nba.duckdb."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nba.colab.__main__ import main
from nba.colab.drive import DriveNotFoundError, drive_root
from nba.colab.jobs import JobError, load_job, parse_job
from nba.colab.pull import pull
from nba.colab.push import push, stage_notebook
from nba.colab.status import format_status, status


def _make_repo(tmp_path: Path, touches: bool = False) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    jobs = repo / "jobs"
    (jobs / "demo").mkdir(parents=True)
    (repo / "nb.ipynb").write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "metadata": {}, "source": ["# t"]},
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "outputs": [],
                        "execution_count": None,
                        "source": [
                            "if IN_COLAB:\n",
                            '    drive.mount("/content/drive")\n',
                            "    X = 1\n",
                        ],
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        )
    )
    spec = {
        "name": "demo",
        "description": "d",
        "notebook": "nb.ipynb",
        "inputs": [
            {
                "path": "in.parquet",
                "produce": "touch in.parquet",  # non-Python (avoids a stray pytest-cov child)
            }
        ],
        "artifacts": ["weights.pt", "config.json", "metrics.json"],
        "registry": {"model_name": "demo_model", "tags": {"rung": 4}},
        "touches_holdout": touches,
        "preregistration_id": "PR-1" if touches else None,
    }
    (jobs / "demo" / "job.yaml").write_text(yaml.safe_dump(spec))
    return repo, jobs


def _fake_artifacts(run_dir: Path, ts: str = "20260101_000000", metrics: object = None) -> Path:
    art = run_dir / "artifacts" / ts
    art.mkdir(parents=True)
    (art / "weights.pt").write_bytes(b"w")
    (art / "config.json").write_text("{}")
    (art / "metrics.json").write_text(
        json.dumps(metrics or {"val_heads": {"make": {"skill": 0.01}}})
    )
    return art


def test_drive_detection_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_COLAB_DRIVE_DIR", str(tmp_path))
    assert drive_root() == tmp_path / "nba_colab"
    monkeypatch.setenv("NBA_COLAB_DRIVE_DIR", str(tmp_path / "nope"))
    with pytest.raises(DriveNotFoundError):
        drive_root()
    monkeypatch.delenv("NBA_COLAB_DRIVE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "emptyhome"))
    with pytest.raises(DriveNotFoundError, match="NBA_COLAB_DRIVE_DIR"):
        drive_root()
    g = tmp_path / "home2/Library/CloudStorage/GoogleDrive-a@b.c/My Drive"
    g.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home2"))
    assert drive_root() == g / "nba_colab"


def test_holdout_requires_prereg():
    base = {
        "name": "x", "description": "d", "notebook": "n", "inputs": [],
        "artifacts": ["metrics.json"], "registry": {"model_name": "m"},
    }  # fmt: skip
    with pytest.raises(JobError):
        parse_job({**base, "touches_holdout": True})
    assert parse_job({**base, "touches_holdout": True, "preregistration_id": "P"}).touches_holdout


def test_real_job_definition_holdout_off():
    job = load_job("rung4_stepheads")
    assert job.touches_holdout is False and job.notebook.exists()
    nb = stage_notebook(job, "r1", "possession_steps.parquet", "possession_steps.parquet")
    src = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "'NBA_SCORE_HOLDOUT': 'False'" in src
    assert "except Exception" in src  # mount made non-fatal
    # original notebook untouched
    orig = json.loads(job.notebook.read_text())
    assert "NBA_COLAB_STAGED" not in json.dumps(orig)


def test_push_status_pull_roundtrip(tmp_path, monkeypatch):
    repo, jobs = _make_repo(tmp_path)
    job = load_job("demo", jobs, repo)
    drive = tmp_path / "drive" / "nba_colab"
    res = push(job, drive, run_id="r1")
    assert (repo / "in.parquet").exists()  # producer ran
    assert (res.run_dir / "in.parquet").exists() and res.notebook.exists()
    assert "T4 GPU" in res.instructions and "Run all" in (res.run_dir / "RUN.md").read_text()
    nb = json.loads(res.notebook.read_text())
    assert nb["cells"][1]["cell_type"] == "code"  # config cell precedes original code
    assert "NBA_SCORE_HOLDOUT" in "".join(nb["cells"][1]["source"])

    assert "waiting" in format_status(status(drive, "demo"))
    with pytest.raises(FileNotFoundError):
        pull(job, drive, dest_root=tmp_path / "out")
    _fake_artifacts(res.run_dir)
    assert "ARTIFACTS" in format_status(status(drive, "demo"))
    out = pull(job, drive, dest_root=tmp_path / "out")
    assert (out.dest / "metrics.json").exists() and out.dest == tmp_path / "out/demo/r1"
    assert not out.registered and "make" in out.summary


def test_pull_rejects_incomplete_and_nan(tmp_path):
    repo, jobs = _make_repo(tmp_path)
    job = load_job("demo", jobs, repo)
    drive = tmp_path / "d"
    r = push(job, drive, run_id="r1")
    art = _fake_artifacts(r.run_dir)
    (art / "weights.pt").unlink()
    with pytest.raises(FileNotFoundError, match="incomplete"):
        pull(job, drive, "r1", dest_root=tmp_path / "o")
    (art / "weights.pt").write_bytes(b"w")
    (art / "metrics.json").write_text('{"a": NaN}')
    with pytest.raises(ValueError, match="NaN"):
        pull(job, drive, "r1", dest_root=tmp_path / "o")


def test_register_is_candidate_only(tmp_path):
    repo, jobs = _make_repo(tmp_path)
    job = load_job("demo", jobs, repo)
    drive = tmp_path / "d"
    r = push(job, drive, run_id="r1")
    _fake_artifacts(r.run_dir)
    db = tmp_path / "reg.duckdb"
    import duckdb

    from nba.db.connect import apply_schema

    con = duckdb.connect(str(db))
    apply_schema(con)
    con.close()
    out = pull(
        job,
        drive,
        "r1",
        register=True,
        db_path=db,
        store_dir=tmp_path / "store",
        dest_root=tmp_path / "o",
    )
    assert out.registered
    con = duckdb.connect(str(db), read_only=True)
    stages = con.execute("select stage from experiments where model_name='demo_model'").fetchall()
    con.close()
    assert stages == [("candidate",)]


def test_cli_missing_drive(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NBA_COLAB_DRIVE_DIR", raising=False)
    assert main(["status"]) == 1
    assert "Drive" in capsys.readouterr().err
