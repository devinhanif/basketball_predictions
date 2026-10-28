"""The live restatement of the T508 formulas equals the research harness; artifact tamper test."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from nba.markets import halftime_model as hm
from research.eval import in_game_line_eval as ev
from research.eval import in_game_line_gate as gate
from research.eval import in_game_line_rule as rule
from tests.markets.halftime_helpers import tiny_artifact


def random_state(stat: str, n: int = 400) -> hm.Cols:
    rng = np.random.default_rng(3)
    line = rng.choice(hm.GRID[stat], n).astype(float)
    cur = np.minimum(rng.integers(0, 12, n), line - 1).astype(float)
    return {
        "e": rng.choice([6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0], n),
        "minutes": rng.uniform(1, 40, n),
        "starter": rng.integers(0, 2, n).astype(float),
        "fouls": rng.integers(0, 5, n).astype(float),
        "margin": rng.integers(-30, 30, n).astype(float),
        "cur": cur,
        "line": line,
        "pregame_mean": rng.uniform(0.5, 25, n),
        "rec_minutes": rng.uniform(8, 36, n),
    }


@pytest.mark.parametrize("stat", hm.STATS)
def test_b0_b1_match_the_research_harness_value_for_value(stat: str) -> None:
    c = random_state(stat)
    h = np.asarray(
        rule.b0_prob(stat, c["line"], c["cur"], c["minutes"], c["e"], c["starter"], c["margin"])
    )
    assert np.max(np.abs(h - hm.b0_prob(stat, c))) < 1e-12
    b1 = rule.b1_prob(stat, c["line"], c["cur"], c["pregame_mean"])
    assert np.max(np.abs(b1 - hm.b1_prob(stat, c))) < 1e-12
    assert np.max(np.abs(rule.logit(h) - hm.logit(h))) == 0.0


def test_constants_equal_the_rule() -> None:
    for name in (
        "SHARE_PRIOR_STARTER",
        "SHARE_PRIOR_BENCH",
        "SHARE_K",
        "RATE_K",
        "RATE_PRIOR",
        "VMR",
        "MEAN_FLOOR",
        "Q4_FACTOR_BLOWOUT",
        "Q4_FACTOR_NORMAL",
        "BLOWOUT_MARGIN",
    ):
        assert getattr(hm, name) == getattr(rule, name), name
    assert hm.FEATURES_A == ev.FEATURES_A and hm.FEATURES_B == ev.FEATURES_B
    assert {s: tuple(v) for s, v in hm.GRID.items()} == {s: tuple(v) for s, v in rule.LINES.items()}
    assert hm.CHECKPOINT in rule.CHECKPOINTS


@pytest.mark.parametrize("stat", hm.STATS)
def test_derived_columns_match_the_harness(stat: str) -> None:
    import polars as pl

    c = random_state(stat)
    d = hm.derive(c)
    u = pl.DataFrame({k: v for k, v in c.items()})
    chk = ev.add_derived(u)
    for name in ("need", "abs_margin", "q4_rem", "blow_x_q4", "absm_x_q4"):
        assert np.max(np.abs(chk[name].to_numpy() - d[name])) < 1e-12, name
    assert np.max(np.abs(gate.b0_for(u, stat) - hm.b0_prob(stat, d))) < 1e-12


def test_player_aware_pace_uses_the_player_not_the_league() -> None:
    c = random_state("pts", 50)
    lo = dict(c, pregame_mean=np.full(50, 4.0))
    hi = dict(c, pregame_mean=np.full(50, 24.0))
    assert np.all(hm.b0p_prob("pts", hi) >= hm.b0p_prob("pts", lo))
    assert (
        np.max(np.abs(hm.b0_prob("pts", hi) - hm.b0_prob("pts", lo))) == 0.0
    )  # B0 has no player prior


def test_artifact_roundtrip_scores_in_bounds(tmp_path: Path) -> None:
    art = tiny_artifact(tmp_path)
    for stat in hm.STATS:
        c = random_state(stat, 30)
        c["e"] = np.full(30, 24.0)
        p_m, p_g = art.score(stat, c)
        assert p_m.shape == p_g.shape == (30,)
        assert np.all((p_m >= 1e-4) & (p_m <= 1 - 1e-4)) and np.all(
            (p_g >= 1e-4) & (p_g <= 1 - 1e-4)
        )


def test_artifact_digest_detects_tampering_and_is_write_once(tmp_path: Path) -> None:
    art = tiny_artifact(tmp_path)
    path = tmp_path / "artifact.json"
    with pytest.raises(FileExistsError):
        hm.save_artifact(path, {}, art.stage_a, art.stage_b, art.platt, art.glm)
    body = json.loads(path.read_text())
    body["platt"]["pts"] = [1.0, 1.0]
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="digest"):
        hm.load_artifact(path)


def test_artifact_has_no_pickle(tmp_path: Path) -> None:
    tiny_artifact(tmp_path)
    assert json.loads((tmp_path / "artifact.json").read_text())["version"] == hm.ARTIFACT_VERSION
