"""Stage a job into Drive: inputs + run-stamped notebook + RUN.md."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nba.colab.jobs import InputSpec, Job

MARKER = "# --- NBA_COLAB_STAGED"
DRIVE_MOUNT = 'if IN_COLAB:\n    drive.mount("/content/drive")\n'
SAFE_MOUNT = (
    'if IN_COLAB:\n    try:\n        drive.mount("/content/drive")\n'
    "    except Exception as _e:  # staged copy: fall back to the notebook's own folder\n"
    '        print("drive.mount failed, using notebook folder:", _e)\n'
)

CONFIG_CELL = """{marker}: written by `python -m nba.colab push`; do not edit --
import os as _os
from pathlib import Path as _P

_JOB, _RUN = {job!r}, {run!r}
_FORCED_ENV = {env!r}
_CANDS = [
    _P("/content/drive/MyDrive/nba_colab") / _JOB / _RUN,  # Drive mounted
    _P.cwd(),  # opened from the Drive folder (e.g. VS Code), or files uploaded next to it
    _P("/content") / _RUN,
]
RUN_DIR = next((c for c in _CANDS if (c / {marker_file!r}).exists()), None)
if RUN_DIR is None:
    raise FileNotFoundError(
        "run folder not found; looked in " + ", ".join(str(c) for c in _CANDS)
        + ". Mount Drive or upload the staged inputs next to the notebook."
    )
_os.environ["NBA_PARQUET"] = str(RUN_DIR / {parquet!r})
_os.environ["NBA_ARTIFACT_ROOT"] = str(RUN_DIR / "artifacts")
for _k, _v in _FORCED_ENV.items():
    _os.environ[_k] = _v
print("NBA_COLAB run dir:", RUN_DIR, "| forced env:", _FORCED_ENV)
"""


@dataclass(frozen=True)
class PushResult:
    run_id: str
    run_dir: Path
    notebook: Path
    instructions: str


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def ensure_inputs(job: Job, produce: bool = True) -> list[Path]:
    """Return absolute input paths, running producer commands if missing/stale."""
    out: list[Path] = []
    for spec in job.inputs:
        path = job.repo_root / spec.path
        if produce and spec.produce and _needs_produce(job.repo_root, spec, path):
            print(f"producing {spec.path}: {spec.produce}")
            subprocess.run(shlex.split(spec.produce), cwd=job.repo_root, check=True)
        if not path.exists():
            raise FileNotFoundError(
                f"input {spec.path} missing"
                + (f"; produce with: {spec.produce}" if spec.produce else "")
            )
        out.append(path)
    return out


def _needs_produce(root: Path, spec: InputSpec, path: Path) -> bool:
    if not path.exists():
        return True
    m = path.stat().st_mtime
    return any((root / d).exists() and (root / d).stat().st_mtime > m for d in spec.depends_on)


def forced_env(job: Job) -> dict[str, str]:
    """Env the staged notebook sets; holdout scoring is OFF unless pre-registered."""
    env = dict(job.env)
    env["NBA_SCORE_HOLDOUT"] = "True" if job.touches_holdout else "False"
    return env


def stage_notebook(job: Job, run_id: str, parquet_name: str, marker_file: str) -> dict[str, Any]:
    """Return the notebook JSON with the config cell injected before the first code cell."""
    nb: dict[str, Any] = json.loads(job.notebook.read_text())
    cell_src = CONFIG_CELL.format(
        marker=MARKER,
        job=job.name,
        run=run_id,
        env=forced_env(job),
        parquet=parquet_name,
        marker_file=marker_file,
    )
    cell: dict[str, Any] = {
        "cell_type": "code",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": cell_src.splitlines(keepends=True),
    }
    cells = nb["cells"]
    first_code = next(i for i, c in enumerate(cells) if c["cell_type"] == "code")
    for c in cells:
        src = "".join(c["source"])
        if c["cell_type"] == "code" and DRIVE_MOUNT in src:
            c["source"] = src.replace(DRIVE_MOUNT, SAFE_MOUNT).splitlines(keepends=True)
    cells.insert(first_code, cell)
    return nb


def push(job: Job, drive_root: Path, produce: bool = True, run_id: str | None = None) -> PushResult:
    inputs = ensure_inputs(job, produce)
    run_id = run_id or new_run_id()
    run_dir = drive_root / job.name / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for p in inputs:
        shutil.copy2(p, run_dir / p.name)
    nb_name = f"{job.name}__{run_id}.ipynb"
    nb = stage_notebook(job, run_id, inputs[0].name, inputs[0].name)
    (run_dir / nb_name).write_text(json.dumps(nb, indent=1))
    (run_dir / "job_meta.json").write_text(_job_meta(job))
    text = run_md(job, run_id, run_dir, nb_name, [p.name for p in inputs])
    (run_dir / "RUN.md").write_text(text)
    return PushResult(run_id, run_dir, run_dir / nb_name, text)


def _job_meta(job: Job) -> str:
    return json.dumps(
        {
            "job": job.name,
            "model_name": job.model_name,
            "artifacts": list(job.artifacts),
            "forced_env": forced_env(job),
            "touches_holdout": job.touches_holdout,
            "preregistration_id": job.preregistration_id,
        },
        indent=2,
    )


def run_md(job: Job, run_id: str, run_dir: Path, nb_name: str, inputs: list[str]) -> str:
    rel = f"nba_colab/{job.name}/{run_id}/{nb_name}"
    holdout = (
        f"PRE-REGISTERED holdout scoring ON ({job.preregistration_id})"
        if job.touches_holdout
        else "holdout scoring OFF (forced in the staged copy)"
    )
    return f"""# Colab run: {job.name} / {run_id}

{job.description.strip()}

Holdout: {holdout}
Inputs staged: {", ".join(inputs)}
Expected artifacts (in `artifacts/<timestamp>/`): {", ".join(job.artifacts)}

## Browser (recommended)
1. Open https://colab.research.google.com
2. File > Open notebook > **Google Drive** tab > `My Drive/{rel}`
3. Runtime > Change runtime type > **T4 GPU** > Save
4. Runtime > **Run all**; approve the Drive-mount popup
5. Wait for "ARTIFACTS WRITTEN TO". Drive for desktop then syncs it to your Mac.

## VS Code alternative
1. Open `{run_dir / nb_name}` in VS Code
2. Select Kernel > Colab > GPU (T4) > Run All
3. If the extension cannot `drive.mount`, the staged copy falls back to the notebook's
   own folder (cell "NBA_COLAB_STAGED"); upload the inputs next to the kernel's cwd or
   use the browser path instead. Artifacts written on the Colab VM must be downloaded.

## Then, on the Mac
    uv run python -m nba.colab status {job.name}
    uv run python -m nba.colab pull {job.name} {run_id} [--register]
"""
