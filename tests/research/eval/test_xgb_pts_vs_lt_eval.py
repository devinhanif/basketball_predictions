"""XGB_PTS_VS_LT (docs/prereg/XGB_PTS_VS_LT.md): freeze refusal, key accounting, scoring, rule."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.eval import xgb_pts_vs_lt_eval as ev
from research.eval.minutes_hazard_eval import frozen_sha

MARK = "=== RESULTS BELOW ==="


def _doc(tmp: Path, status: str, body: str = "rule text\n") -> Path:
    p = tmp / "D.md"
    p.write_text(f"# t\n\nStatus: {status}.\n\n{body}\n{MARK}\n\nresults are not hashed\n")
    return p


def test_status_and_hash_refusal(tmp_path: Path) -> None:
    draft = _doc(tmp_path, "DRAFT")
    sha = frozen_sha(draft)
    assert ev.require_frozen(draft, None, allow_draft=True) == sha
    with pytest.raises(SystemExit, match="not FROZEN"):
        ev.require_frozen(draft, sha, allow_draft=False)
    frozen = _doc(tmp_path, "FROZEN")
    good = frozen_sha(frozen)
    assert ev.require_frozen(frozen, good, allow_draft=False) == good
    with pytest.raises(SystemExit, match="64-hex"):
        ev.require_frozen(frozen, good[:8], allow_draft=False)
    with pytest.raises(SystemExit, match="!= frozen"):
        ev.require_frozen(frozen, "0" * 64, allow_draft=False)


def test_hash_ignores_results_section(tmp_path: Path) -> None:
    p = _doc(tmp_path, "FROZEN")
    before = frozen_sha(p)
    p.write_text(p.read_text() + "\nmore results\n")
    assert frozen_sha(p) == before


def test_allow_draft_refused_outside_gate() -> None:
    with pytest.raises(SystemExit, match="gate stage only"):
        ev.main(["run", "--allow-draft"])


def test_parse_doc_inputs(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text(f"- input a.npz: {'a' * 64}\n- not an input: x\n- input b.json: {'b' * 64}\n")
    assert ev.parse_doc_inputs(p) == {"a.npz": "a" * 64, "b.json": "b" * 64}


def test_minutes_bucket() -> None:
    m = np.array([np.nan, 0.0, 4.9, 5.0, 20.0, 20.1])
    assert list(ev.minutes_bucket(m)) == ["<5", "<5", "<5", "5-20", "5-20", ">20"]


def test_align_keys_counts_intersection() -> None:
    a0 = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g3"],
            "player_id": [1, 2, 1, 1],
            "season": [2023, 2023, 2024, 2024],
        }
    )
    fr = pl.DataFrame({"game_id": ["g2", "g1"], "player_id": [1, 2], "season": [2024, 2023]})
    k = ev.align_keys(a0, fr)
    assert [k["acct"]["2023"]["a0_only"], k["acct"]["2024"]["a0_only"]] == [1, 1]
    assert k["acct"]["2023"]["frame_only"] == 0 and not k["acct"]["2023"]["identical"]
    assert len(k["i0"]) == 2
    assert [a0["game_id"][int(i)] for i in k["i0"]] == ["g2", "g1"]  # frame order


def test_team_shares_top() -> None:
    delta = np.array([-1.0, -1.0, -2.0, 0.0])
    team = np.array([1, 1, 2, 3])
    s = ev.team_shares(delta, team)
    assert s["top_team"] in (1, 2) and abs(s["top_share"] - 0.5) < 1e-12


def _synthetic(
    season_n: int = 1500, shift: float = 0.0
) -> tuple[dict[int, pl.DataFrame], dict[str, Any], dict[int, Any]]:
    rng = np.random.default_rng(0)
    meta: dict[int, pl.DataFrame] = {}
    qi: dict[str, dict[int, np.ndarray]] = {a: {} for a in ("A0", "A1", "A2")}
    dk: dict[int, Any] = {}
    taus = (np.arange(1, 200) - 0.5) / 199
    for s in ev.SEASONS:
        n = season_n
        y = rng.poisson(12, n).astype(float)
        gid = np.array([f"002{s}{i // 10:04d}" for i in range(n)])
        gid[-40:] = np.array([f"004{s}{i // 10:04d}" for i in range(40)])
        meta[s] = pl.DataFrame(
            {
                "game_id": gid,
                "player_id": np.arange(n),
                "y": y,
                "minutes": rng.uniform(0, 36, n),
                "starter_act": (rng.random(n) > 0.5).astype(float),
                "starter10": (rng.random(n) > 0.5).astype(float),
                "team_game_no": rng.integers(1, 80, n).astype(float),
                "team_id": rng.integers(1, 6, n),
                "tip_hour": rng.choice([17.0, 19.5, 20.0], n),
            }
        )
        base = np.floor(np.quantile(rng.poisson(12, 5000), taus))[None, :].repeat(n, 0)
        qi["A0"][s] = base.astype(np.int16)
        qi["A1"][s] = (base + shift).astype(np.int16)
        qi["A2"][s] = base.astype(np.int16)
        idx = np.arange(0, n, 2)
        dk[s] = (idx, np.full(len(idx), 11.5))
    return meta, qi, dk


def test_score_all_identical_arms_have_zero_delta() -> None:
    meta, qi, dk = _synthetic()
    res = ev.score_all(meta, qi, dk)
    for s in ("2023", "2024"):
        r = res["seasons"][s]
        assert r["primary"]["point"] == 0.0 and r["primary"]["lo"] == 0.0 == r["primary"]["hi"]
        assert r["tll_dk"]["A1-A0"]["point"] == 0.0
        assert r["slices"]["postseason"]["n"] == 40
        assert r["slices"]["minutes>=5"]["n"] + r["slices"]["minutes<5"]["n"] == r["n"]
    assert set(res["bh"]) == {"2023", "2024"}
    rule = ev.evaluate_rule(res, True, True)
    assert rule["pass"] is False and rule["verdict"] == "CLOSED" and rule["closed_no_pass_2023"]


def test_score_all_worse_arm_has_positive_delta_and_fails_rule() -> None:
    meta, qi, dk = _synthetic(shift=3.0)
    res = ev.score_all(meta, qi, dk)
    assert res["seasons"]["2024"]["primary"]["point"] > 0.5
    rule = ev.evaluate_rule(res, True, True)
    assert rule["conditions"]["P1_2023"] is False and rule["pass"] is False


def test_rule_requires_gate_and_refit() -> None:
    meta, qi, dk = _synthetic(shift=0.0)
    res = ev.score_all(meta, qi, dk)
    # force every condition true except the gate / refit switches
    for s in ("2023", "2024"):
        res["seasons"][s]["primary"].update({"point": -0.02, "lo": -0.03, "hi": -0.01})
    res["bh"] = {"2023": 0.001, "2024": 0.001}
    for s in ("2023", "2024"):
        for k in ("minutes>=5", "tip>=19ET(proxy-safe)"):
            res["seasons"][s]["slices"][k]["A1-A0"]["point"] = -0.02
        res["seasons"][s]["tll_dk"]["A1-A0"]["point"] = 0.0
    res["seasons"]["2024"]["team_shares"]["top_share"] = 0.1
    res["seasons"]["2024"]["calibration"]["A1"] = {
        "bias": 0.0,
        "cov80": 0.8,
        "guard": {"ok": True},
    }
    ok = ev.evaluate_rule(res, True, True)
    assert ok["conditions"]["P1_2023"] and ok["conditions"]["P2_2024"]
    assert ok["pass"] is True and ok["verdict"] == "SHADOW_CANDIDATE"
    assert ev.evaluate_rule(res, False, True)["pass"] is False
    assert ev.evaluate_rule(res, True, False)["pass"] is False
    res["seasons"]["2024"]["tll_dk"]["A1-A0"]["point"] = 0.0021
    assert ev.evaluate_rule(res, True, True)["pass"] is False


def test_integer_support_scoring_matches_production_map() -> None:
    q = np.array([[0.2, 0.5, 0.51, 3.49, 3.5, 7.0]])
    qi = ev.to_arm_int(q)
    assert list(qi[0]) == [0.0, 0.0, 1.0, 3.0, 3.0, 7.0]
