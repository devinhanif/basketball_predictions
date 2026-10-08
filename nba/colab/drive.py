"""Locate the Drive-backed ``nba_colab`` folder (Drive for desktop or rclone)."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

ENV_VAR = "NBA_COLAB_DRIVE_DIR"
REMOTE_ENV_VAR = "NBA_COLAB_RCLONE_REMOTE"
DEFAULT_REMOTE = "nbacolab:"  # rclone alias for gdrive:nba_colab
SUBDIR = "nba_colab"
#: Local mirror of the rclone remote (gitignored under data/).
RCLONE_STAGE = Path(__file__).resolve().parents[2] / "data" / "colab" / "rclone_stage" / SUBDIR


class DriveNotFoundError(RuntimeError):
    """No Drive backend: no rclone remote and Drive for desktop absent (or bad override)."""


def rclone_remote() -> str | None:
    """The rclone remote to use, or None when rclone/the remote is unavailable.

    ``NBA_COLAB_RCLONE_REMOTE`` overrides; set it to an empty string to disable rclone.
    """
    if os.environ.get(ENV_VAR):  # explicit local folder (e.g. tests) never touches the remote
        return None
    remote = os.environ.get(REMOTE_ENV_VAR, DEFAULT_REMOTE)
    if not remote or shutil.which("rclone") is None:
        return None
    out = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, check=False)
    return remote if remote in out.stdout.split() else None


def _rclone(*args: str) -> None:
    subprocess.run(["rclone", *args], check=True)


def sync_up(local_dir: Path, rel: str) -> None:
    """Upload ``local_dir`` to ``<remote><rel>`` (no-op without rclone)."""
    remote = rclone_remote()
    if remote:
        _rclone("copy", str(local_dir), f"{remote}{rel}", "--progress")


def sync_down(rel: str) -> None:
    """Mirror ``<remote><rel>`` into the local stage (no-op without rclone)."""
    remote = rclone_remote()
    if remote:
        _rclone("copy", f"{remote}{rel}", str(drive_root() / rel))


def drive_root() -> Path:
    """Return the local ``nba_colab`` folder (created if needed).

    Precedence: ``NBA_COLAB_DRIVE_DIR`` override, then an rclone remote (local mirror under
    data/colab/rclone_stage, synced by push/status/pull), then Drive for desktop.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        base = Path(override).expanduser()
        if not base.is_dir():
            raise DriveNotFoundError(f"{ENV_VAR}={override} is not a directory")
        root = base / SUBDIR
    elif rclone_remote():
        root = RCLONE_STAGE
    else:
        pattern = str(Path.home() / "Library/CloudStorage/GoogleDrive-*/My Drive")
        hits = sorted(glob.glob(pattern))
        if not hits:
            raise DriveNotFoundError(
                "No Drive backend: no rclone remote "
                f"'{DEFAULT_REMOTE}' and Google Drive for desktop not found. Run "
                "`rclone config create nbacolab alias remote=gdrive:nba_colab` after "
                f"configuring gdrive, or set {ENV_VAR}. See docs/COLAB_WORKFLOW.md."
            )
        root = Path(hits[0]) / SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root
