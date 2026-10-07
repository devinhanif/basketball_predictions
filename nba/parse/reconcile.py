"""Possession-count reconciliation.

CLAUDE.md requires validating the parser's possession counts against the
box-score estimate ``FGA + 0.44*FTA + TOV - OREB`` per team-game, failing
the ingest test if the mean absolute error exceeds 1 possession. This
module computes that per-team-game error given the parser's output
(``nba/parse/possessions.py``) and a team-level box score aggregate.
"""

from __future__ import annotations

import polars as pl

#: Columns a `team_box` frame must carry (one row per game_id/team_id).
TEAM_BOX_COLUMNS = ["game_id", "team_id", "fga", "fta", "tov", "oreb"]


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
