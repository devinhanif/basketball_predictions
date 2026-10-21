# ruff: noqa: E501
"""P1 reference of COMBO_STOCKS_SHADOW: the committed 200-row reference (inputs and expected grids built with the CONFIG R, 4 dp)
must be reproduced bit for bit by ``combo_copula.joint_grids`` in any chunking; the config's sha is what the rule text freezes."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import yaml

from nba.daily.combo_shadow.config import load_config
from nba.props import combo_copula as C

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs/combo_stocks_shadow.yaml"
REF = ROOT / "reports/prereg_combo_stocks_shadow/p1_reference.npz"


def test_p1_reference_is_reproduced_exactly() -> None:
    cfg = yaml.safe_load(CFG.read_text())
    ref = np.load(REF)
    r = np.array(cfg["copula"]["R"], dtype=float)
    assert list(cfg["stat_order"]) == list(C.STAT_ORDER)
    assert np.array_equal(
        ref["R"], r
    )  # the reference was built with the config R, not the full-precision one
    assert np.linalg.eigvalsh(r).min() > 0
    base = (
        load_config().base
    )  # the committed file, verified against the frozen sha (not regenerated: BLAS-dependent)
    assert (
        hashlib.sha256(base.tobytes()).hexdigest()
        == cfg["copula"]["base_sha256"]
        == str(ref["base_sha256"])
    )
    cdfs = {s: ref[f"cdf_{s}"] for s in C.STAT_ORDER}
    for chunk in (200, 17):
        got = C.joint_grids(cdfs, r, base, chunk=chunk)
        ind = C.joint_grids(cdfs, np.eye(len(C.STAT_ORDER)), base, chunk=chunk)
        for c in C.COMBOS:
            assert np.array_equal(got[c], ref[f"expected_{c}"]), (c, chunk)
            assert np.array_equal(ind[c], ref[f"expected_indep_{c}"]), (c, chunk)


def test_config_is_the_select_artifact() -> None:
    """Floors and R in the config equal select.json (R to the 4 dp the config states)."""
    import json

    cfg = yaml.safe_load(CFG.read_text())
    sel = json.loads((ROOT / cfg["select_json"]).read_text())
    assert (
        np.abs(np.array(cfg["copula"]["R"]) - np.array(sel["matrices_2023"]["pooled_latent"])).max()
        < 6e-5
    )
    for t, v in sel["floors_and_n_required"].items():
        assert abs(cfg["floors"][t] - v["floor"]) < 6e-5
