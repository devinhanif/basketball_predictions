# ruff: noqa: E501
"""ENSEMBLE exports: planted future across a whole later stretch, report channels, positions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.features.player_sequences import CTX_FEATURES, SeqConfig, build_export
from research.features.seq_ensemble_export import (
    REPORT_CTX,
    merge_static,
    report_ctx_index,
    residual_position_share,
)
from tests.research.features.test_player_sequences import CFG, N_GAMES, _date, _raw


def _row(exp, gid: str, pid: int) -> int:
    m = exp.meta
    return int(m.index[(m.game_id == gid) & (m.player_id == pid)][0])


def test_planted_future_whole_stretch_changes_nothing_earlier() -> None:
    """Scramble every box line (and score) of all games from G08 on: inputs of earlier targets are equal."""
    a = build_export(_raw(), CFG, keep_window_ids=True)
    raw = _raw()
    late = raw.pgs["game_id"] >= "G08"
    raw.pgs.loc[late, "pts"] = raw.pgs.loc[late, "pts"] + 17
    raw.pgs.loc[late, "reb"] = 11
    raw.pgs.loc[late & raw.pgs["minutes"].notna(), "minutes"] = 7.0
    raw.pgs.loc[late, "starter"] = ~raw.pgs.loc[late, "starter"].astype(bool)
    raw.games.loc[raw.games["game_id"] >= "G08", "home_pts"] = 150
    b = build_export(raw, CFG, keep_window_ids=True)
    cutoff = _date(8)
    early = [i for i, d in enumerate(a.meta["game_date"]) if pd.Timestamp(d).date() < cutoff]
    assert len(early) > 10
    ka = {(g, p): i for i, (g, p) in enumerate(zip(a.meta.game_id, a.meta.player_id, strict=True))}
    kb = {(g, p): i for i, (g, p) in enumerate(zip(b.meta.game_id, b.meta.player_id, strict=True))}
    for i in early:
        key = (a.meta.game_id[i], a.meta.player_id[i])
        j = kb[key]
        assert np.array_equal(a.arrays["seq"][i], b.arrays["seq"][j])
        assert np.array_equal(a.arrays["ctx"][i], b.arrays["ctx"][j])
        assert np.array_equal(a.arrays["mask"][i], b.arrays["mask"][j])
    assert ka  # the late targets do differ (the scramble is not inert)
    late_rows = [i for i, d in enumerate(a.meta["game_date"]) if pd.Timestamp(d).date() >= _date(9)]
    assert any(
        not np.array_equal(
            a.arrays["seq"][i], b.arrays["seq"][kb[(a.meta.game_id[i], a.meta.player_id[i])]]
        )
        for i in late_rows
    )


def test_history_style_build_has_no_report_tokens() -> None:
    """With the availability table emptied, every report channel is exactly 0 on every row."""
    raw = _raw()
    assert len(raw.avail) > 0
    raw.avail = raw.avail.iloc[0:0]
    exp = build_export(raw, CFG)
    idx = report_ctx_index()
    assert [CTX_FEATURES[i] for i in idx] == list(REPORT_CTX)
    assert np.abs(exp.arrays["ctx"][:, idx]).sum() == 0.0
    with_reports = build_export(_raw(), CFG)
    assert np.abs(with_reports.arrays["ctx"][:, idx]).sum() > 0.0  # the fixture does carry some


def test_merge_static_db_wins_cache_fills_gaps() -> None:
    db = pd.DataFrame({"player_id": [1, 2, 3], "position": ["G", "", None]})
    cache = pd.DataFrame({"player_id": [1, 2, 4], "position": ["C", "F", "G"]})
    m = merge_static(db, cache).set_index("player_id")["position"].to_dict()
    assert m == {1: "G", 2: "F", 4: "G"}


def test_history_cfg_and_position_share() -> None:
    cfg = SeqConfig(
        k=5, min_target_season=2023, max_season=2023, rotation_min=5.0, min_rotation_games=2
    )
    raw = _raw()
    raw.static = pd.DataFrame(
        {"player_id": [11], "position": ["G"]}
    )  # only one player has a position
    exp = build_export(raw, cfg)
    share = residual_position_share(exp)
    assert 0.0 < share["share"] < 1.0 and share["rows"] == len(exp.meta)
    assert N_GAMES == 12
