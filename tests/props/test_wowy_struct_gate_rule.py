"""WOWY_STRUCT cell gate (a)-(c) computed from stored numbers (E3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from research.eval import wowy_struct_gate as SG

ROOT = Path(__file__).resolve().parents[2]


def _fixture(
    power: float, beyond: list[float], d1_lo: float, perm: float, struct: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    sizing: dict[str, Any] = {}
    gbm: dict[str, Any] = {}
    for stat in SG.STATS_USED:
        sizing[stat] = {
            str(s): {sn: {"plain_2020_21(reference)": {"power_b": power}} for sn in SG.SLICES_USED}
            for s in (2023, 2024)
        }
        gbm[stat] = {
            d: {
                sn: {
                    "struct_beyond_role(struct_role_vs_role)": {
                        "relative_gain_pct": beyond[i],
                        "ci_pct": [d1_lo, 2.0],
                    },
                    "perm_ps_vs_base": {"relative_gain_pct": perm},
                    "struct_vs_base": {"relative_gain_pct": struct},
                }
                for sn in SG.SLICES_USED
            }
            for i, d in enumerate(SG.DESIGNS_GATE)
        }
    return sizing, gbm


def test_cell_gate_rule_requires_all_three_criteria() -> None:
    ok = SG.cell_gate_rule(*_fixture(0.6, [1.0, 0.5, 0.7], 0.3, 0.1, 1.0))
    assert all(v["pass_gate"] for v in ok.values())
    assert not any(
        v["pass_gate"]
        for v in SG.cell_gate_rule(*_fixture(0.4, [1.0, 0.5, 0.7], 0.3, 0.1, 1.0)).values()
    )  # (a)
    assert not any(
        v["pass_gate"]
        for v in SG.cell_gate_rule(*_fixture(0.6, [1.0, -0.1, 0.7], 0.3, 0.1, 1.0)).values()
    )  # (b) sign
    assert not any(
        v["pass_gate"]
        for v in SG.cell_gate_rule(*_fixture(0.6, [1.0, 0.5, 0.7], -0.1, 0.1, 1.0)).values()
    )  # (b) D1 CI
    assert not any(
        v["pass_gate"]
        for v in SG.cell_gate_rule(*_fixture(0.6, [1.0, 0.5, 0.7], 0.3, 1.5, 1.0)).values()
    )  # (c)


def test_stored_gate_json_reproduces_the_draft_table() -> None:
    path = ROOT / "reports/prereg_wowy_redistribution/gate.json"
    if not path.exists():
        pytest.skip("gate.json not present")
    sw = json.loads(path.read_text())["struct_warm"]
    got = SG.cell_gate_rule(sw["sizing"], sw["gbm_warm"])
    assert sorted(k for k, v in got.items() if v["pass_gate"]) == [
        "ast|leader_out",
        "pts|leader_out",
        "pts|top25_fixed",
    ]
    assert sw["cell_gate"] == json.loads(json.dumps(got))
    assert all(v["pass_gate"] for v in sw["cell_gate_beyond_role_superseded"].values())
