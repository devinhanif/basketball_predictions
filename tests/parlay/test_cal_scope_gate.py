"""Scope classifier of the label-free calibration gate (nba/parlay/cal_scope_gate.py)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from nba.parlay import cal_scope_gate as g

BAND = g.Band(r_lo=-1.4, r_hi=0.2, zm_lo=-5.0, zm_hi=5.0, n=100)


def test_classify_order_and_support() -> None:
    # no forecast beats everything; no DK main means the map's zm / r are undefined
    assert g.classify(None, 2.5, 2.5, 0.0, 0.0, BAND) == "no_forecast"
    assert g.classify(0.8, 2.5, None, None, None, BAND) == "no_dk_main"
    # the Kalshi line equals the DK main line: both maps apply (even when raw P < 0.5)
    assert g.classify(0.43, 24.5, 24.5, 0.0, 0.1, BAND) == "at_main"
    # raw P < 0.5 is outside the fit set
    assert g.classify(0.3, 28.5, 24.5, 0.5, 0.1, BAND) == "below_half"
    # P >= 0.5, r inside the fit band
    assert g.classify(0.8, 20.5, 24.5, -1.0, 0.1, BAND) == "in_support"
    # P >= 0.5 but r beyond the fit band's 1st percentile
    assert g.classify(0.97, 12.5, 24.5, -2.0, 0.1, BAND) == "outside_band"


def test_fit_bands_percentiles(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    n = 2000
    stat = np.array(["pts"] * n + ["reb"] * n + ["ast"] * n + ["fg3m"] * n)
    r = np.concatenate([rng.uniform(-2, 0, n) for _ in range(4)])
    path = tmp_path / "arrays.npz"
    np.savez(path, stat=stat, r=r)
    bands = g.fit_bands(path)
    assert set(bands) == {"pts", "reb", "ast", "fg3m"}
    for b in bands.values():
        assert b.n == n
        assert -2.0 <= b.r_lo < -1.9 and -0.1 < b.r_hi <= 0.0
        assert (b.zm_lo, b.zm_hi) == (-5.0, 5.0)


def test_no_order_or_credential_surface() -> None:
    src = Path(g.__file__).read_text()
    for banned in ("orders", "portfolio", "KALSHI_KEY", "api_key", "requests.", "httpx", "urlopen"):
        assert banned not in src
