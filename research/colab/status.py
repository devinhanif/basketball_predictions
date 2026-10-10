"""List Drive runs and whether artifacts have landed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RunStatus:
    job: str
    run_id: str
    artifact_dir: Path | None  # newest artifacts/<ts>/ containing metrics.json
    files: tuple[tuple[str, int], ...]  # (name, bytes)
    mtime: float | None


def latest_artifact_dir(run_dir: Path) -> Path | None:
    root = run_dir / "artifacts"
    if not root.is_dir():
        return None
    done = sorted(d for d in root.iterdir() if d.is_dir() and (d / "metrics.json").exists())
    return done[-1] if done else None


def status(drive_root: Path, job: str) -> list[RunStatus]:
    jdir = drive_root / job
    out: list[RunStatus] = []
    if not jdir.is_dir():
        return out
    for run in sorted(p for p in jdir.iterdir() if p.is_dir()):
        art = latest_artifact_dir(run)
        files = (
            tuple(sorted((f.name, f.stat().st_size) for f in art.iterdir() if f.is_file()))
            if art
            else ()
        )
        mt = (
            max((f.stat().st_mtime for f in art.iterdir() if f.is_file()), default=None)
            if art
            else None
        )
        out.append(RunStatus(job, run.name, art, files, mt))
    return out


def format_status(rows: list[RunStatus]) -> str:
    if not rows:
        return "no runs found (push a job first; Drive sync may lag)"
    lines = []
    for r in rows:
        if r.artifact_dir is None:
            lines.append(f"{r.job}  {r.run_id}  waiting (no artifacts yet; Drive sync may lag)")
            continue
        when = datetime.fromtimestamp(r.mtime).isoformat(timespec="seconds") if r.mtime else "?"
        sizes = ", ".join(f"{n} {s / 1e6:.2f}MB" for n, s in r.files)
        lines.append(f"{r.job}  {r.run_id}  ARTIFACTS {r.artifact_dir.name} @ {when}: {sizes}")
    return "\n".join(lines)
