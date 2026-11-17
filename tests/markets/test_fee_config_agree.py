"""Guard: the Polymarket US fee object lives in two YAMLs and they must not silently diverge.

``configs/jarvis.yaml`` ``fees.polymarket`` (read by ``nba.jarvis.config.load_config``) and
``configs/fastarb.yaml`` ``polymarket_us.fee`` (read by ``nba.markets.fastarb.config.load_config``)
describe the same venue fee. Neither key is moved here (scorers pin ``fee_pin``; config values are
frozen); consolidation is a proposal in docs/handoff_2026-11-17/lane_live_markets.md.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from nba.jarvis.config import load_config as load_jarvis
from nba.markets.fastarb.config import load_config as load_fastarb

ROOT = Path(__file__).resolve().parents[2]
SHARED = ("name", "formula", "coefficient", "rounding", "verified")


def test_polymarket_fee_objects_agree_in_both_yamls() -> None:
    jv = yaml.safe_load((ROOT / "configs" / "jarvis.yaml").read_text())["fees"]["polymarket"]
    fa = yaml.safe_load((ROOT / "configs" / "fastarb.yaml").read_text())["polymarket_us"]["fee"]
    for key in SHARED:
        assert key in jv and key in fa, key
        assert jv[key] == fa[key], (key, jv[key], fa[key])
        assert type(jv[key]) is type(fa[key]), key


def test_loaded_polymarket_fee_objects_agree() -> None:
    jv = load_jarvis()
    fa = load_fastarb()
    pm = jv.fee("polymarket")
    assert pm.coefficient == fa.us_fee.coefficient
    assert float.hex(pm.coefficient) == float.hex(fa.us_fee.coefficient)
    assert pm.name == fa.us_fee.name
    assert pm.verified is fa.us_fee.verified is True
    assert jv.fee_rounding["polymarket"] == "bankers_cent"
    assert pm.multiplier("") == 1.0  # JARVIS's scaled form reduces to the fast-arb form at M = 1


def test_kalshi_fee_is_one_object_for_every_reader() -> None:
    """Kalshi has one source (configs/parlay.yaml fee_model); JARVIS and fast-arb both read it."""
    jv = load_jarvis()
    fa = load_fastarb()
    assert jv.fee("kalshi") == fa.kalshi_fee
    raw = yaml.safe_load((ROOT / "configs" / "jarvis.yaml").read_text())
    pin = raw["halftime_overs"]["fee_pin"]
    k = jv.fee("kalshi")
    assert (k.formula, k.coefficient, k.multiplier("")) == (
        pin["formula"],
        float(pin["coefficient"]),
        float(pin["multiplier"]),
    )
