"""Replay scorer: coverage by group, P(play) bins, paired deltas, production-DB refusal."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from nba.props.forward import PROP_STATS, QUANTILE_TAUS
from research.props.roster_replay import main, score_rows, summarize


def _pred(players: list[int], p_play: float, shift: float) -> pl.DataFrame:
    rows = []
    for pid in players:
        for st in PROP_STATS:
            grid = [round(5.0 + shift + 5.0 * t, 4) for t in QUANTILE_TAUS]
            rows.append(
                {"game_id": "G1", "player_id": pid, "stat": st, "p_play": p_play,
                 "proj_minutes": 20.0, "q_grid": json.dumps(grid)}
            )  # fmt: skip
    return pl.DataFrame(rows)


def _actual() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["G1"] * 4,
            "player_id": [1, 2, 3, 4],
            "minutes": [30.0, 20.0, 10.0, 0.0],
            "pts": [10, 6, 2, 0],
            "reb": [4, 3, 1, 0],
            "ast": [2, 1, 0, 0],
            "fg3m": [1, 0, 0, 0],
        }
    )


def test_summarize_coverage_pplay_and_paired_deltas() -> None:
    actual = _actual()
    played = actual.filter(pl.col("minutes") > 0).with_columns(
        team_id=pl.lit(1), date=pl.lit("2024-10-22")
    )
    groups = pl.DataFrame(
        {
            "game_id": ["G1"] * 3,
            "player_id": [1, 2, 3],
            "group": ["same_team", "mover", "debutant"],
        }
    )
    recent = score_rows(_pred([1, 2], 0.7, 0.0).with_columns(date=pl.lit("2024-10-22")), actual)
    official = score_rows(
        _pred([1, 2, 3, 4], 0.7, 0.0).with_columns(date=pl.lit("2024-10-22")), actual
    )
    res = summarize({"recent": recent, "official": official}, played, groups, n_boot=50)
    cov_r = res["arms"]["recent"]["coverage"]
    cov_o = res["arms"]["official"]["coverage"]
    assert cov_r["all"]["share"] == 2 / 3 and cov_o["all"]["share"] == 1.0
    assert cov_r["debutant"]["covered"] == 0 and cov_o["debutant"]["covered"] == 1
    assert cov_o["all"]["minutes_share"] == 1.0
    p = res["arms"]["official"]["p_play"]
    assert p["n"] == 4 and p["obs_rate"] == 0.75 and abs(p["mean_pred"] - 0.7) < 1e-12
    assert res["arms"]["recent"]["by_date"]["2024-10-22"] == {
        "played": 3, "covered": 2, "p_play_mean": 0.7, "obs_rate": 2 / 2, "n_pred": 2,
    }  # fmt: skip
    pair = res["paired_by_group"]["official_vs_recent"]["all"]
    assert pair["n"] == 2 and pair["pts"]["delta_a_minus_b"][0] == 0
    # identical predictions on shared rows: zero paired delta; the extra rows are "only official"
    d = res["paired_vs_recent"]["official"]["pts"]
    assert (
        d["n_both"] == 2 and d["n_only_official"] == 1 and d["delta_official_minus_recent"][0] == 0
    )


def test_main_refuses_the_production_database(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = main(["--db", str(tmp_path / "nba.duckdb"), "--start", "2024-10-22", "--end",
               "2024-10-22", "--out", str(tmp_path / "o.json")])  # fmt: skip
    assert rc == 2 and "refusing" in capsys.readouterr().out
