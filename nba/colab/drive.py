"""Locate the Google Drive for desktop folder."""

from __future__ import annotations

import glob
import os
from pathlib import Path

ENV_VAR = "NBA_COLAB_DRIVE_DIR"
SUBDIR = "nba_colab"


class DriveNotFoundError(RuntimeError):
    """Google Drive for desktop is not installed/signed in (or override is wrong)."""


def drive_root() -> Path:
    """Return ``<My Drive>/nba_colab`` (created if needed).

    ``NBA_COLAB_DRIVE_DIR`` overrides the detected ``My Drive`` directory.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        base = Path(override).expanduser()
        if not base.is_dir():
            raise DriveNotFoundError(f"{ENV_VAR}={override} is not a directory")
    else:
        pattern = str(Path.home() / "Library/CloudStorage/GoogleDrive-*/My Drive")
        hits = sorted(glob.glob(pattern))
        if not hits:
            raise DriveNotFoundError(
                "Google Drive for desktop not found (looked for "
                "~/Library/CloudStorage/GoogleDrive-*/My Drive). Install it, sign in, "
                f"or set {ENV_VAR} to your 'My Drive' folder. See docs/COLAB_WORKFLOW.md."
            )
        base = Path(hits[0])
    root = base / SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root
