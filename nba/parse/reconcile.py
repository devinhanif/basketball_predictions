"""Reconciliation of parsed possessions against the box score.

HARD GATE (restated 2026-10-11, DECISIONS "Phase 2 data clean-up", audit F12): the points the
parser attributes to each team's possessions must equal that team's box-score points, per
team-game (``reconcile_points``; ``POINTS_MATCH_MIN`` of team-games must match exactly). On the
real data this is 100% of 10,558 team-games.

DIAGNOSTIC (reported, never a gate): the possession count against the box estimate
``FGA + 0.44*FTA + TOV - OREB`` (``reconcile_possessions``). The estimate is not an identity (team
turnovers and the 0.44 constant are not in the player box), so the real data sits 1.6-2.0 below it
with MAE 2.0-2.25 in every season; the original "MAE <= 1" gate was mis-specified (docs/adr/0001).
``FORMULA_MAE_DIAGNOSTIC`` is the level above which the diagnostic is worth a look, and
``FORMULA_BIAS_BAND`` is the band the signed bias (parsed minus estimate, mean over team-games)
should sit in on real data; outside it is a flag, not a failure.

FIXTURE GATE (restored 2026-10-11): on the clean committed fixture the formula MAE must stay
<= ``FIXTURE_MAE_MAX`` (1.0; the clean fixture sits at 0.34). At the looser real-data level of
2.5 a planted +2 or +3 possessions per team-game still passed, so the real-data level may not
be used for the fixture.
"""

from __future__ import annotations

import polars as pl

#: Columns a `team_box` frame must carry (one row per game_id/team_id).
TEAM_BOX_COLUMNS = ["game_id", "team_id", "fga", "fta", "tov", "oreb"]

#: Hard gate: share of team-games whose possession points equal box points.
POINTS_MATCH_MIN = 0.99
#: Diagnostic only: formula MAE above this is reported as worth a look (real data: 2.0-2.25).
FORMULA_MAE_DIAGNOSTIC = 2.5
#: Fixture gate: formula MAE on the clean committed fixture (measured 0.34). Never loosened.
FIXTURE_MAE_MAX = 1.0
#: Diagnostic band for the signed bias on real data. The stored per-season bias is -1.6 to -2.0
#: (2022: -1.647, 2023: -1.606, 2024: -1.662; audit F12); chosen with about 0.5 margin each side
#: so a drift of +-0.5 in bias is a flag. A planted +2 per team-game moves the bias to about 0.
FORMULA_BIAS_BAND = (-2.5, -1.0)


def team_box_from_player_game_stats(player_game_stats: pl.DataFrame) -> pl.DataFrame:
    """Aggregate player-level box score rows (``player_game_stats`` schema,
    i.e. with fga/fta/tov/oreb columns) up to one row per (game_id, team_id).
    """
    return (
        player_game_stats.group_by(["game_id", "team_id"])
        .agg(
            pl.col("fga").sum().alias("fga"),
            pl.col("fta").sum().alias("fta"),
            pl.col("tov").sum().alias("tov"),
            pl.col("oreb").sum().alias("oreb"),
        )
        .sort(["game_id", "team_id"])
    )


def reconcile_possessions(possessions: pl.DataFrame, team_box: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id): parsed possession count vs. the box-score
    estimate ``FGA + 0.44*FTA + TOV - OREB``.

    Returns a frame with columns: game_id, team_id, n_parsed, estimate,
    error (n_parsed - estimate), abs_error.
    """
    missing = set(TEAM_BOX_COLUMNS) - set(team_box.columns)
    if missing:
        raise ValueError(f"team_box is missing required columns: {sorted(missing)}")

    parsed_counts = (
        possessions.group_by(["game_id", "off_team"])
        .agg(pl.len().alias("n_parsed"))
        .rename({"off_team": "team_id"})
    )

    estimates = team_box.with_columns(
        (pl.col("fga") + 0.44 * pl.col("fta") + pl.col("tov") - pl.col("oreb")).alias("estimate")
    ).select(["game_id", "team_id", "estimate"])

    out = estimates.join(parsed_counts, on=["game_id", "team_id"], how="left").with_columns(
        pl.col("n_parsed").fill_null(0)
    )
    out = out.with_columns(
        (pl.col("n_parsed") - pl.col("estimate")).alias("error"),
    ).with_columns(pl.col("error").abs().alias("abs_error"))
    return out.select(["game_id", "team_id", "n_parsed", "estimate", "error", "abs_error"])


def mean_abs_error(reconciliation: pl.DataFrame) -> float:
    """Mean |parsed - estimate| across all team-games in `reconciliation`."""
    if reconciliation.is_empty():
        return 0.0
    value = reconciliation["abs_error"].mean()
    assert value is not None
    return float(value)  # type: ignore[arg-type]


def signed_bias(reconciliation: pl.DataFrame) -> float:
    """Mean signed error (parsed - estimate) over all team-games in ``reconciliation``."""
    if reconciliation.is_empty():
        return 0.0
    value = reconciliation["error"].mean()
    assert value is not None
    return float(value)  # type: ignore[arg-type]


def bias_outside_band(reconciliation: pl.DataFrame) -> bool:
    """Diagnostic flag for real data: True when the signed formula bias leaves
    ``FORMULA_BIAS_BAND``. Reported, never a gate."""
    lo, hi = FORMULA_BIAS_BAND
    return not lo <= signed_bias(reconciliation) <= hi


def reconcile_points(possessions: pl.DataFrame, player_game_stats: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id): points summed over the parsed possessions vs box-score points.

    ``possessions`` needs ``game_id, off_team, pts``; ``player_game_stats`` needs ``game_id,
    team_id, pts``. Returns game_id, team_id, poss_pts, box_pts, diff (poss - box). A team-game
    with no parsed possessions has poss_pts 0 (it shows up as a miss, not as a missing row).
    """
    for name, frame, cols in (
        ("possessions", possessions, ["game_id", "off_team", "pts"]),
        ("player_game_stats", player_game_stats, ["game_id", "team_id", "pts"]),
    ):
        missing = set(cols) - set(frame.columns)
        if missing:
            raise ValueError(f"{name} is missing required columns: {sorted(missing)}")
    poss = (
        possessions.group_by(["game_id", "off_team"])
        .agg(pl.col("pts").sum().alias("poss_pts"))
        .rename({"off_team": "team_id"})
    )
    box = player_game_stats.group_by(["game_id", "team_id"]).agg(
        pl.col("pts").sum().alias("box_pts")
    )
    out = box.join(poss, on=["game_id", "team_id"], how="left").with_columns(
        pl.col("poss_pts").fill_null(0)
    )
    return (
        out.with_columns((pl.col("poss_pts") - pl.col("box_pts")).alias("diff"))
        .select(["game_id", "team_id", "poss_pts", "box_pts", "diff"])
        .sort(["game_id", "team_id"])
    )


def points_match_rate(reconciliation: pl.DataFrame) -> float:
    """Share of team-games (``reconcile_points`` output) with possession points == box points."""
    if reconciliation.is_empty():
        return 0.0
    return float((reconciliation["diff"] == 0).mean())  # type: ignore[arg-type]
