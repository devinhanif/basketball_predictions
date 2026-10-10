"""Copy artifacts back from Drive, validate, optionally register as candidate."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from research.colab.jobs import Job
from research.colab.status import latest_artifact_dir, status

RUNS_SUBDIR = Path("data/colab/runs")
#: Data files kept out of the registry store (CLAUDE.md: never store raw data there).
DATA_PATTERNS = ("*.parquet", "*.csv", "*.duckdb", "*.npz", "*.feather")


@dataclass(frozen=True)
class PullResult:
    dest: Path
    metrics: dict[str, object]
    registered: bool
    summary: str


def _pick_run(drive_root: Path, job: Job, run_id: str | None) -> str:
    rows = status(drive_root, job.name)
    if run_id is not None:
        if run_id not in {r.run_id for r in rows}:
            raise FileNotFoundError(f"no run {run_id} for {job.name} in {drive_root}")
        return run_id
    ready = [r for r in rows if r.artifact_dir is not None]
    if not ready:
        raise FileNotFoundError(f"no run of {job.name} has artifacts yet (Drive sync may lag)")
    return ready[-1].run_id


def pull(
    job: Job,
    drive_root: Path,
    run_id: str | None = None,
    register: bool = False,
    db_path: Path | None = None,
    store_dir: Path | None = None,
    dest_root: Path | None = None,
) -> PullResult:
    run_id = _pick_run(drive_root, job, run_id)
    art = latest_artifact_dir(drive_root / job.name / run_id)
    if art is None:
        raise FileNotFoundError(f"run {run_id} has no artifacts/<ts>/metrics.json yet")
    missing = [a for a in job.artifacts if not (art / a).exists()]
    if missing:
        raise FileNotFoundError(f"artifacts incomplete in {art}: missing {missing}")
    metrics = json.loads((art / "metrics.json").read_text())
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics.json must be a non-empty JSON object")
    bad = _non_finite(metrics)
    if bad:
        raise ValueError(f"metrics.json has NaN/inf at: {bad[:5]}")
    dest = (dest_root or job.repo_root / RUNS_SUBDIR) / job.name / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(art, dest)
    lines = [f"pulled {job.name}/{run_id} -> {dest}"]
    for h, r in (metrics.get("val_heads") or {}).items():
        if isinstance(r, dict) and "skill" in r:
            lines.append(f"  val {h}: skill {r['skill']:+.3%}")
    lines.append("metrics keys: " + ", ".join(sorted(metrics)))
    registered = False
    if register:
        from nba.registry.__main__ import main as registry_main

        argv = ["--db-path", str(db_path)] if db_path else []
        argv += ["--store-dir", str(store_dir)] if store_dir else []
        tags = [f"{k}={json.dumps(v)}" for k, v in job.tags.items()]
        tags += [f"source_run={run_id}"]
        # The registry refuses data files (OOF parquets etc.); register model files only.
        model_dir = dest.parent / f"{run_id}__model"
        if model_dir.exists():
            shutil.rmtree(model_dir)
        shutil.copytree(art, model_dir, ignore=shutil.ignore_patterns(*DATA_PATTERNS))
        rc = registry_main(
            [*argv, "import-artifacts", job.model_name, str(model_dir), "--tags", *tags]
        )
        if rc != 0:
            raise ValueError("registry import-artifacts failed")
        registered = True
        lines.append("registered as candidate (never promoted; promote manually)")
    return PullResult(dest, metrics, registered, "\n".join(lines))


def _non_finite(obj: object, path: str = "") -> list[str]:
    if isinstance(obj, float):
        return [path] if obj != obj or obj in (float("inf"), float("-inf")) else []
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in _non_finite(v, f"{path}/{k}")]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in _non_finite(v, f"{path}[{i}]")]
    return []
