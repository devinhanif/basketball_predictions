"""F12 data-check helpers: frozen-hash reader and pooled ratio."""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl

from nba.eval.f12_closing_risk import frozen_sha256, q4_ratio


def test_frozen_sha_stops_at_marker(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text("frozen\n=== RESULTS BELOW ===\nlater\n")
    assert frozen_sha256(p) == hashlib.sha256(b"frozen\n").hexdigest()


def test_q4_ratio_baseline() -> None:
    df = pl.DataFrame({"q4": [12.0, 6.0], "q13": [36.0, 18.0]})
    assert abs(q4_ratio(df) - 1 / 3) < 1e-12
