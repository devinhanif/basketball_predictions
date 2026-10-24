"""Freeze hashes, bucket edges in integer cents, and the pinned fee (with negative tests)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nba.parlay.config import DEFAULT_PATH
from research.market_forward import score
from research.market_forward.fee import (
    CENT,
    FeeRefused,
    load_pinned_fee,
)
from research.market_forward.spec import (
    LONGSHOT,
    RULES,
    UNDERS,
    FrozenTextChanged,
    RuleSpec,
    frozen_section_sha256,
    kind_of,
    verify_frozen,
)


@pytest.mark.parametrize("spec", [LONGSHOT, UNDERS])
def test_frozen_sections_hash_to_the_pinned_values(spec) -> None:  # type: ignore[no-untyped-def]
    assert verify_frozen(spec) == spec.frozen_sha256


def test_hash_prefixes_match_the_freeze_records() -> None:
    assert LONGSHOT.frozen_sha256.startswith("0b50d17e")
    assert UNDERS.frozen_sha256.startswith("149be979")


def test_any_edit_inside_the_frozen_section_is_refused(tmp_path: Path) -> None:
    txt = UNDERS.doc_path.read_text()
    edited = tmp_path / "u.md"
    edited.write_text(txt.replace("0.010", "0.011", 1))
    with pytest.raises(FrozenTextChanged):
        verify_frozen(UNDERS, edited)
    # an edit outside the markers does not matter (the freeze record sits outside)
    outside = tmp_path / "o.md"
    outside.write_text(txt.replace("FROZEN 2026-10-23", "FROZEN 2026-10-23 (note)", 1))
    assert verify_frozen(UNDERS, outside) == UNDERS.frozen_sha256


def test_awk_reimplementation_edge_cases() -> None:
    assert frozen_section_sha256("no markers\n") == ""
    t = "a\n<!-- FROZEN-BEGIN -->\nx\ny\n<!-- FROZEN-END -->\nz\n"
    import hashlib

    assert frozen_section_sha256(t) == hashlib.sha256(b"x\ny\n").hexdigest()


def test_bucket_edges_are_integer_cent_sums() -> None:
    def b(spec: RuleSpec, bid: int, ask: int) -> str:
        return score.bucket_of(spec, bid, ask)

    # B1 [0.01, 0.05): mid 0.05 -> B2; B3 [0.10, 0.15] inclusive of 0.15
    assert b(LONGSHOT, 1, 1) == "B1" and b(LONGSHOT, 4, 5) == "B1"
    assert b(LONGSHOT, 5, 5) == "B2" and b(LONGSHOT, 9, 10) == "B2"
    assert b(LONGSHOT, 10, 10) == "B3" and b(LONGSHOT, 15, 15) == "B3"
    assert b(LONGSHOT, 15, 16) == "none"
    # UNDERS: U4 (0.15, 0.30], U5 (0.30, 0.50], U8 (0.85, 1.00)
    assert b(UNDERS, 15, 16) == "U4" and b(UNDERS, 30, 30) == "U4" and b(UNDERS, 30, 31) == "U5"
    assert b(UNDERS, 50, 50) == "U5" and b(UNDERS, 50, 51) == "U6"
    assert b(UNDERS, 70, 70) == "U6" and b(UNDERS, 85, 85) == "U7" and b(UNDERS, 86, 86) == "U8"
    assert b(UNDERS, 99, 100) == "U8"


def test_kinds_and_series_universe() -> None:
    assert kind_of("KXNBAPRA") == "combo" and kind_of("KXNBASPREAD") == "spread"
    assert LONGSHOT.descriptive_series == ("KXNBASPREAD",)
    assert "KXNBAGAME" not in UNDERS.all_series and "KXNBAGAME" in LONGSHOT.decision_series
    assert set(RULES) == {"KALSHI_LONGSHOT_FORWARD", "UNDERS_FORWARD"}


def test_series_match_configs_kalshi_yaml() -> None:
    cfg = yaml.safe_load((Path(__file__).resolve().parents[3] / "configs/kalshi.yaml").read_text())
    assert set(UNDERS.decision_series) == set(cfg["prop_series"])
    assert set(LONGSHOT.all_series) == set(cfg["prop_series"]) | set(cfg["game_series"])


# ---- fee ----


def _config(tmp_path: Path, **changes) -> Path:  # type: ignore[no-untyped-def]
    raw = yaml.safe_load(DEFAULT_PATH.read_text())
    raw["fee_model"].update(changes)
    p = tmp_path / "parlay.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_fee_loads_from_the_verified_config_object() -> None:
    fee = load_pinned_fee(LONGSHOT.all_series)
    assert fee.model.verified and "p_one_minus_p" in fee.describe()
    assert fee.fee_micro("KXNBAPTS", 50) == 17_500  # 0.07 * 0.5 * 0.5 = $0.0175
    assert fee.fee_micro("KXNBAPTS", 1) == 7 * 1 * 99
    assert fee.fee_micro_rounded_up("KXNBAPTS", 50) == 2 * CENT  # S1 rounds up to a whole cent


def test_refuses_unverified_fee(tmp_path: Path) -> None:
    with pytest.raises(FeeRefused, match="verified"):
        load_pinned_fee(UNDERS.all_series, _config(tmp_path, verified=False))


def test_refuses_changed_coefficient(tmp_path: Path) -> None:
    with pytest.raises(FeeRefused, match="coefficient"):
        load_pinned_fee(UNDERS.all_series, _config(tmp_path, coefficient=0.0702))


def test_refuses_changed_formula_default_multiplier_and_series_multiplier(tmp_path: Path) -> None:
    with pytest.raises(FeeRefused, match="formula"):
        load_pinned_fee(UNDERS.all_series, _config(tmp_path, formula="flat"))
    with pytest.raises(FeeRefused, match="default_multiplier"):
        load_pinned_fee(UNDERS.all_series, _config(tmp_path, default_multiplier=0.5))
    with pytest.raises(FeeRefused, match="multiplier for KXNBAGAME"):
        load_pinned_fee(LONGSHOT.all_series, _config(tmp_path, fee_multiplier={"KXNBAGAME": 0.5}))
    # the same multiplier on a series outside the rule's universe is irrelevant to UNDERS
    ok = load_pinned_fee(UNDERS.all_series, _config(tmp_path, fee_multiplier={"KXNBAGAME": 0.5}))
    assert ok.multiplier("KXNBAPTS") == 1


def test_maker_coefficient_is_not_what_is_pinned(tmp_path: Path) -> None:
    ok = load_pinned_fee(UNDERS.all_series, _config(tmp_path, maker_coefficient=0.5))
    assert ok.coefficient * 100 == 7


def test_string_false_verified_is_refused_even_though_the_live_loader_coerces_it(
    tmp_path: Path,
) -> None:
    # nba/parlay/config.py coerces the string "false" to True (noted for Devin; not edited).
    for bad in ("false", "yes", 1):
        with pytest.raises(FeeRefused, match="YAML boolean true"):
            load_pinned_fee(UNDERS.all_series, _config(tmp_path, verified=bad))
