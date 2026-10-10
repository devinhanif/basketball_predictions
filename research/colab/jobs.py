"""Job definitions: ``research/colab/jobs/<name>/job.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = Path(__file__).resolve().parent / "jobs"


class JobError(ValueError):
    """Invalid or missing job definition."""


@dataclass(frozen=True)
class InputSpec:
    path: str  # repo-relative
    produce: str | None = None  # shell-free command (shlex-split); None = must already exist
    depends_on: tuple[str, ...] = ()  # input is stale if older than any of these


@dataclass(frozen=True)
class Job:
    name: str
    description: str
    notebook: Path  # absolute
    inputs: tuple[InputSpec, ...]
    artifacts: tuple[str, ...]
    model_name: str
    tags: dict[str, Any] = field(default_factory=dict)
    touches_holdout: bool = False
    preregistration_id: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    repo_root: Path = REPO_ROOT


def parse_job(data: dict[str, Any], repo_root: Path = REPO_ROOT) -> Job:
    for key in ("name", "description", "notebook", "inputs", "artifacts", "registry"):
        if key not in data:
            raise JobError(f"job.yaml missing '{key}'")
    reg = data["registry"]
    if not isinstance(reg, dict) or "model_name" not in reg:
        raise JobError("job.yaml registry.model_name is required")
    touches = bool(data.get("touches_holdout", False))
    prereg = data.get("preregistration_id")
    if touches and not prereg:
        raise JobError("touches_holdout=true requires a preregistration_id")
    if not touches and prereg:
        raise JobError("preregistration_id given but touches_holdout is false")
    artifacts = tuple(str(a) for a in data["artifacts"])
    if "metrics.json" not in artifacts:
        raise JobError("artifacts must include metrics.json")
    inputs = tuple(
        InputSpec(
            path=str(i["path"]),
            produce=i.get("produce"),
            depends_on=tuple(i.get("depends_on", ())),
        )
        for i in data["inputs"]
    )
    return Job(
        name=str(data["name"]),
        description=str(data["description"]),
        notebook=(repo_root / str(data["notebook"])),
        inputs=inputs,
        artifacts=artifacts,
        model_name=str(reg["model_name"]),
        tags=dict(reg.get("tags", {})),
        touches_holdout=touches,
        preregistration_id=str(prereg) if prereg else None,
        env={str(k): str(v) for k, v in dict(data.get("env", {})).items()},
        repo_root=repo_root,
    )


def load_job(name: str, jobs_dir: Path = JOBS_DIR, repo_root: Path = REPO_ROOT) -> Job:
    f = jobs_dir / name / "job.yaml"
    if not f.exists():
        raise JobError(f"unknown job {name!r} (no {f})")
    job = parse_job(yaml.safe_load(f.read_text()), repo_root)
    if job.name != name:
        raise JobError(f"job.yaml name {job.name!r} != directory {name!r}")
    return job


def list_jobs(jobs_dir: Path = JOBS_DIR) -> list[str]:
    return sorted(p.parent.name for p in jobs_dir.glob("*/job.yaml"))
