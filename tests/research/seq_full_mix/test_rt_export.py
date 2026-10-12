# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_FULL_MIX_RT exports: real-tip report gating (planted future), proxy19 vs real differ exactly on the leaky games, no-tip -> no token, era channels,
history 2021-22 report tokens dropped, the selection export refuses seasons after 2024."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import polars as pl
import pytest

from nba.features.injury_report import ReportTriggerConfig
from research.features import player_sequences as P
from research.features import seq_full_mix_rt_export as X
from tests.research.features.test_player_sequences import N_GAMES, _date, _raw

BASE = dict(k=5, min_target_season=2023, rotation_min=5.0, min_rotation_games=2)


def _raw_with_reports():
    """G08: matinee (13:00 ET) with a 10:00 snapshot (usable) and a 14:00 snapshot (after tip-60; proxy19 admits it, 18:00 cutoff).
    G10: matinee with ONLY a 14:00 snapshot. G11: no real tip, an 08:00 snapshot. Every other game tips at 19:30 ET."""
    raw = _raw()
    add = [
        ("G08", 11, "out", f"{_date(8)} 14:00"),
        ("G10", 11, "out", f"{_date(10)} 14:00"),
        ("G10", 13, "out", f"{_date(10)} 14:00"),
        ("G11", 13, "out", f"{_date(11)} 08:00"),
    ]
    raw.avail = pd.concat(
        [
            raw.avail,
            pd.DataFrame(
                {
                    "game_id": [a[0] for a in add],
                    "player_id": [a[1] for a in add],
                    "status": [a[2] for a in add],
                    "as_of": pd.to_datetime([a[3] for a in add]),
                }
            ),
        ],
        ignore_index=True,
    )
    rows = []
    for i in range(N_GAMES):
        if i == 11:
            continue  # no real tip
        h = 13 if i in (8, 10) else 19
        rows.append((f"G{i:02d}", dt.datetime.combine(_date(i), dt.time(h, 0 if h == 13 else 30))))
    raw.tips = pl.DataFrame(
        {"game_id": [r[0] for r in rows], "tip_et": [r[1] for r in rows]},
        schema={"game_id": pl.Utf8, "tip_et": pl.Datetime("us")},
    )
    return raw


def _exports():
    raw = _raw_with_reports()
    real = P.build_export(raw, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE))
    prox = P.build_export(
        _raw_with_reports(), P.SeqConfig(report=ReportTriggerConfig(tip_source="proxy19"), **BASE)
    )
    return real, prox


def test_real_tip_admits_no_snapshot_after_tip_minus_60_and_proxy_differs_exactly_on_the_leaky_games() -> (
    None
):
    real, prox = _exports()
    assert list(real.meta.game_id) == list(prox.meta.game_id)
    hr, hp = P.CTX_FEATURES.index("has_report"), P.CTX_FEATURES.index("st_out")
    g = real.meta.game_id.to_numpy()
    # G10 has only a post-tip-60 snapshot: no report under real tips, a report under the proxy
    assert (real.arrays["ctx"][g == "G10", hr] == 0).all() and (
        prox.arrays["ctx"][g == "G10", hr] == 1
    ).all()
    # G11 has no real tip: NO report token (never the proxy)
    assert (real.arrays["ctx"][g == "G11", hr] == 0).all() and (
        prox.arrays["ctx"][g == "G11", hr] == 1
    ).all()
    # G08: real uses the 10:00 snapshot (player 11 not out), proxy uses the 14:00 one (player 11 out)
    r11 = (real.meta.game_id == "G08") & (real.meta.player_id == 11)
    assert (
        real.arrays["ctx"][r11.to_numpy(), hp][0] == 0
        and prox.arrays["ctx"][r11.to_numpy(), hp][0] == 1
    )
    differ = (real.arrays["ctx"] != prox.arrays["ctx"]).any(axis=1)
    assert set(g[differ]) == {
        "G08",
        "G10",
        "G11",
    }  # exactly the leaky / tip-less games; nothing else moves
    for k in (
        "seq",
        "mask",
        "opp_step",
        "y",
        "base",
    ):  # the sequence inputs and labels do not depend on the report rule
        assert np.array_equal(real.arrays[k], prox.arrays[k])


def test_a_snapshot_planted_after_tip_minus_60_changes_nothing_under_real_tips() -> None:
    base_raw = _raw_with_reports()
    a = P.build_export(base_raw, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE))
    raw = _raw_with_reports()
    plant = pd.DataFrame(
        {
            "game_id": ["G09", "G09", "G05"],
            "player_id": [11, 12, 13],
            "status": ["out", "out", "out"],
            "as_of": pd.to_datetime(
                [f"{_date(9)} 19:29", f"{_date(9)} 19:00", f"{_date(5)} 19:31"]
            ),
        }
    )
    raw.avail = pd.concat(
        [raw.avail, plant], ignore_index=True
    )  # G09/G05 tip at 19:30: cutoff 18:30, all three are later
    b = P.build_export(raw, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE))
    assert np.array_equal(a.arrays["ctx"], b.arrays["ctx"])
    raw2 = _raw_with_reports()  # control: a snapshot BEFORE the cutoff does move the context
    raw2.avail = pd.concat(
        [
            raw2.avail,
            pd.DataFrame(
                {
                    "game_id": ["G09"],
                    "player_id": [12],
                    "status": ["out"],
                    "as_of": pd.to_datetime([f"{_date(9)} 18:29"]),
                }
            ),
        ],
        ignore_index=True,
    )
    c = P.build_export(raw2, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE))
    assert not np.array_equal(a.arrays["ctx"], c.arrays["ctx"])


def test_real_tip_config_without_tips_refuses() -> None:
    raw = _raw_with_reports()
    raw.tips = None
    with pytest.raises(ValueError, match="raw.tips"):
        P.build_export(raw, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE))


def test_season_with_dropped_report_tokens_keeps_box_rows_and_has_no_report() -> None:
    raw = _raw_with_reports()
    kept = P.build_export(raw, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE))
    raw2 = _raw_with_reports()
    raw2.no_report_seasons = (int(raw2.games["season"].iloc[0]),)
    dropped = P.build_export(
        raw2, P.SeqConfig(report=ReportTriggerConfig(tip_source="real"), **BASE)
    )
    hr = P.CTX_FEATURES.index("has_report")
    assert kept.arrays["ctx"][:, hr].max() == 1 and dropped.arrays["ctx"][:, hr].max() == 0
    assert list(kept.meta.game_id) == list(dropped.meta.game_id) and np.array_equal(
        kept.arrays["seq"], dropped.arrays["seq"]
    )  # same rows


def test_era_channels_are_appended_flagged_and_never_imputed() -> None:
    real, _ = _exports()
    n0 = real.arrays["ctx"].shape[1]
    flags = pl.DataFrame(
        {
            "game_id": ["G03", "G04"],
            "covid_bubble": [True, False],
            "no_fans": [True, True],
            "limited_fans": [False, None],
        }
    )
    info = X.add_era(real, flags)
    assert (
        real.arrays["ctx"].shape[1] == n0 + 3
        and real.ctx_features[-3:] == X.ERA_CTX
        and len(real.ctx_features) == real.arrays["ctx"].shape[1]
    )
    g = real.meta.game_id.to_numpy()
    era = real.arrays["ctx"][:, n0:]
    assert (era[g == "G03"] == [1, 1, 0]).all() and (
        era[g == "G04"] == [0, 1, 0]
    ).all()  # None in a flag -> 0, flagged games keep their flags
    assert (era[~np.isin(g, ["G03", "G04"])] == 0).all() and info["rows_without_flags"] == int(
        (~np.isin(g, ["G03", "G04"])).sum()
    )  # missing rows counted


def test_selection_export_refuses_any_season_after_2024() -> None:
    real, _ = _exports()
    X.assert_selection_seasons(real)
    real.meta["season"] = real.meta["season"].replace({2023: 2025})
    with pytest.raises(RuntimeError, match="> 2024"):
        X.assert_selection_seasons(real)
    with pytest.raises(
        ValueError, match="frozen holdout"
    ):  # and the loader itself refuses 2025 without the lifted bound
        X.build_cur_rt(2025, db=":memory:")


def test_export25_refuses_before_the_freeze(tmp_path) -> None:
    with pytest.raises(RuntimeError):
        X.export25("a" * 64, run=lambda *a: "", out=tmp_path)


def test_history_and_current_exports_must_share_vocabulary_scale_and_feature_names() -> None:
    real, _ = _exports()
    other, _ = _exports()
    X.assert_same_vocabulary(real, other)
    assert len(X.team_map(real)) == 2  # the fixture's two teams
    other.arrays["team_idx"] = other.arrays["team_idx"] + 1  # a shifted team index mapping
    with pytest.raises(RuntimeError, match="team index mapping"):
        X.assert_same_vocabulary(real, other)
    third, _ = _exports()
    third.arrays["stat_scale"] = third.arrays["stat_scale"] * 2
    with pytest.raises(RuntimeError, match="stat_scale"):
        X.assert_same_vocabulary(real, third)
    fourth, _ = _exports()
    fourth.ctx_features = (*fourth.ctx_features, "extra")
    with pytest.raises(RuntimeError, match="feature names"):
        X.assert_same_vocabulary(real, fourth)
    dup, _ = _exports()
    dup.arrays["team_idx"] = dup.arrays["team_idx"].copy()
    dup.arrays["team_idx"][0] = 99
    with pytest.raises(RuntimeError, match="two indices"):
        X.team_map(dup)
