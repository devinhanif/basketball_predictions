# ruff: noqa: E501
"""ARCHETYPE_MATCHUP harness: hand-computed mix, strictly-before, planted future, shuffled control, refusal, bars in code, no-2025 guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from research.eval import archetype_matchup_eval as mev
from research.eval import archetype_matchup_features as feat
from research.eval import archetype_matchup_rule as rule
from research.eval import role_prior_salary_rule as role_rule

ROOT = Path(__file__).resolve().parents[3]


def _mu(n_days: int = 10, future_scale: float = 1.0) -> pd.DataFrame:
    rows = []
    for d in range(n_days + 1):
        gid = f"002000{d:02d}"
        date = pd.Timestamp("2023-11-01") + pd.Timedelta(days=d)
        s = future_scale if d >= n_days else 1.0
        for dfn, mm in ((10, 30.0), (11, 10.0)):
            rows.append((gid, date, 2023, 1, dfn, 100, 200, mm * s, 20.0 * s, 8.0 * s))
    return pd.DataFrame(
        rows,
        columns=[
            "game_id",
            "game_date",
            "season",
            "off_player_id",
            "def_player_id",
            "off_team",
            "def_team",
            "mm",
            "poss",
            "pts",
        ],
    )


def _tables() -> dict[str, Any]:
    R = np.full((rule.N_OFF, rule.N_DEF), 0.2)
    R[:, 0] = 0.1
    R[:, 1] = 0.3
    return {"lift": np.ones((rule.N_OFF, rule.N_DEF)), "R": R, "lg": 0.2}


def _frame(mu: pd.DataFrame, out: set[int], seed: int | None = None) -> pd.DataFrame:
    gid = "00200010"
    rows = pd.DataFrame(
        {
            "game_id": [gid],
            "player_id": [1],
            "team_id": [100],
            "game_date": [pd.Timestamp("2023-11-11")],
            "min10": [30.0],
            "n_prior": [50],
        }
    )
    games = pd.DataFrame({"game_id": [gid], "home_team": [100], "away_team": [200]})
    off = pd.Series({1: 0})
    dfa = pd.Series({10: 0, 11: 1})
    flagged = {gid: out} if out else {}
    return feat.mix_frame(rows, mu, games, flagged, 2023, _tables(), off, dfa, shuffle_seed=seed)


def test_mix_hand_computed_with_one_defender_out() -> None:
    f = _frame(_mu(), {10})
    # usual mix 0.75 / 0.25, tonight 0 / 1 -> L1 = 1.5; rates 0.1 / 0.3
    assert f.shift_l1.iloc[0] == pytest.approx(1.5)
    assert f.e_usual.iloc[0] == pytest.approx(0.75 * 0.1 + 0.25 * 0.3 - 0.2)
    assert f.delta_rate.iloc[0] == pytest.approx(0.3 - 0.15)
    assert f.retained.iloc[0] == pytest.approx(0.25)
    assert f.n_roster.iloc[0] == 2 and f.n_out.iloc[0] == 1 and not f.no_roster.iloc[0]


def test_no_out_means_no_shift_and_no_report_means_neutral() -> None:
    f = _frame(_mu(), set())
    assert f.shift_l1.iloc[0] == 0 and f.delta_rate.iloc[0] == 0 and f.retained.iloc[0] == 1.0


def test_everyone_out_falls_back_to_usual_not_nan() -> None:
    f = _frame(_mu(), {10, 11})
    assert np.isfinite(f[list(rule.FEATURES)].to_numpy(float)).all()
    assert f.shift_l1.iloc[0] == 0.0 and f.retained.iloc[0] == 0.0


def test_planted_future_does_not_move_features() -> None:
    a = _frame(_mu(), {10})
    b = _frame(
        _mu(future_scale=7.3), {10}
    )  # the game's own matchup rows and nothing earlier are scaled
    for c in rule.FEATURES:
        assert a[c].iloc[0] == pytest.approx(b[c].iloc[0], abs=1e-12)


def test_first_game_of_a_season_has_no_roster_and_neutral_features() -> None:
    mu = _mu()
    rows = pd.DataFrame(
        {
            "game_id": ["00200000"],
            "player_id": [1],
            "team_id": [100],
            "game_date": [pd.Timestamp("2023-11-01")],
            "min10": [30.0],
            "n_prior": [50],
        }
    )
    games = pd.DataFrame({"game_id": ["00200000"], "home_team": [100], "away_team": [200]})
    f = feat.mix_frame(
        rows, mu, games, {}, 2023, _tables(), pd.Series({1: 0}), pd.Series({10: 0, 11: 1})
    )
    assert bool(f.no_roster.iloc[0]) and f.shift_l1.iloc[0] == 0 and f.retained.iloc[0] == 1.0


def test_roster_is_window_limited_and_strictly_before() -> None:
    dg = feat.defender_games(_mu(n_days=40))
    tg = pd.DataFrame(
        {"tgi": [0, 1], "team": [200, 200], "day": [int(dg.day.max()) + 100, int(dg.day.min()) + 2]}
    )
    c = feat.roster_candidates(dg, tg)
    assert 0 not in set(c.tgi)  # nothing within ROSTER_DAYS of a date 100 days later
    assert set(c[c.tgi == 1].j) == {10, 11}
    tg0 = pd.DataFrame({"tgi": [0], "team": [200], "day": [int(dg.day.min())]})
    assert len(feat.roster_candidates(dg, tg0)) == 0  # the first day sees no earlier game


def test_unknown_is_its_own_category() -> None:
    idx = feat.arch_index([1, 2, 3], pd.Series({1: 0, 3: 5}), rule.UNK_DEF)
    assert (
        list(idx) == [0, rule.UNK_DEF, 5]
        and rule.UNK_DEF == rule.K_DEF
        and rule.N_DEF == rule.K_DEF + 1
    )


def test_prior_tables_shares_and_lift() -> None:
    mu = _mu().assign(season=2022)
    t = feat.prior_tables(mu, pd.Series({1: 0}), pd.Series({10: 0, 11: 1}), 2022)
    assert t["A"][0, 0] == pytest.approx(0.75) and t["A"][0, 1] == pytest.approx(0.25)
    assert t["pi"][0] == pytest.approx(0.75)
    assert t["lift"][0, 0] == pytest.approx(1.0)
    assert t["lg"] == pytest.approx(8.0 / 20.0)
    with pytest.raises(ValueError):
        feat.prior_tables(mu, pd.Series({1: 0}), pd.Series({10: 0}), 2025)


def test_permute_within_keeps_each_groups_multiset() -> None:
    g = np.array([0, 0, 0, 1, 1, 2])
    v = np.array([1, 2, 3, 4, 5, 6])
    p = feat.permute_within(g, v, 0)
    for k in (0, 1, 2):
        assert sorted(p[g == k]) == sorted(v[g == k])
    assert (feat.permute_within(g, v, 0) == p).all()


def test_shift_and_t2_mask_and_verdict_words() -> None:
    a = np.array([[0.5, 0.5], [1.0, 0.0]])
    b = np.array([[0.5, 0.5], [0.0, 1.0]])
    assert list(rule.l1_shift(a, b)) == [0.0, 2.0]
    assert list(rule.t2_mask(np.array([0.0, 0.04, 0.05, 1.0]), 0.0)) == [
        False,
        False,
        True,
        True,
    ]  # the floor beats a zero cut
    assert rule.verdict_word(True, True) == "PASS"
    assert rule.verdict_word(True, False) == "T1_ONLY"
    assert rule.verdict_word(False, True) == "FAIL" and rule.verdict_word(False, False) == "FAIL"


def test_season_guard_and_shared_constants() -> None:
    with pytest.raises(ValueError):
        rule.check_season(2025)
    assert rule.check_season(2024) == 2024
    assert (
        rule.N_BOOT == role_rule.N_BOOT
        and rule.SEED == role_rule.SEED
        and rule.ALPHA == role_rule.ALPHA
    )
    assert rule.DE_BAR["pts"] == 0.005 and rule.MIN_ROWS == 300


def test_run_refuses_unfrozen(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        mev.require_frozen(None)
    doc = tmp_path / "d.md"
    doc.write_text("text\n=== RESULTS BELOW ===\n")
    with pytest.raises(SystemExit):
        mev.require_frozen("0" * 64, doc)
    with pytest.raises(SystemExit):
        mev.main(["run"])


def test_modules_do_not_query_2025_or_labels() -> None:
    import re

    for name in ("rule", "features", "gate", "eval"):
        text = (ROOT / f"research/eval/archetype_matchup_{name}.py").read_text()
        assert not re.search(r"season\s*(=|>=|>|between)\s*2025|\b2025-26\b", text), name
        assert "read_only=False" not in text and "duckdb.connect(" not in text.replace(
            "read_only=True", ""
        ), name
    gate = (ROOT / "research/eval/archetype_matchup_gate.py").read_text()
    assert (
        'read_rows("pts", S, ROW_COLS)' in gate
        and '"y"' not in gate.split("ROW_COLS = ")[1].split("\n")[0]
    )


def test_features_do_not_depend_on_matchup_dtype() -> None:
    """Amendment 1: float32 minutes must give the same features as float64 (a float32 groupby sum made the planted-future check fail at ~1e-9)."""
    mu32 = _mu().astype({"mm": "float32", "poss": "float32", "pts": "float32"})
    mu32.loc[mu32.index[::3], "mm"] = np.float32(10.0 / 3.0)
    mu64 = mu32.astype({"mm": "float64", "poss": "float64", "pts": "float64"})
    a, b = _frame(mu32, {10}), _frame(mu64, {10})
    for c in rule.FEATURES:
        assert a[c].iloc[0] == b[c].iloc[0]


def test_regular_season_filter_drops_playoff_and_playin_ids() -> None:
    assert list(rule.is_regular(["0022300001", "0042300101", "0052300001"])) == [True, False, False]
