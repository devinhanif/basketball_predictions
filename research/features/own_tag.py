# ruff: noqa: E501
"""OWN_TAG features (docs/prereg/OWN_TAG.md; DRAFT until frozen).

Two columns per (game_id, player_id) from the player's OWN line on the latest real-tip-gated
official-report snapshot at or before tip - 60 min (``nba.features.injury_report.usable_report_rows``
is the single place the leakage rule lives; ``tip_source="real"``, proxy only where the real tip is
missing, exactly as production):

* ``own_tag``: 0 none (on the roster, absent from that snapshot, or the game has no usable snapshot),
  1 available, 2 probable, 3 questionable, 4 doubtful. An OUT line is coded 5 so the ordinal is kept
  and so the gate can count it (OUT players are not in the played universe unless they played).
* ``own_tag_hours``: hours from the snapshot to the real tip, capped at 48 (48 where the game has no
  usable snapshot; ``has_report`` already carries that fact).

Built for ALL active-roster rows (every ``player_game_stats`` row, played or not), then filtered by
the caller, so a join cannot hide a missingness pattern. ``_has_snap`` is diagnostic only.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.features.game_tipoff import attach_real_tips
from nba.features.injury_report import ReportTriggerConfig, usable_report_rows

TAG_CODES: dict[str, int] = {
    "available": 1,
    "probable": 2,
    "questionable": 3,
    "doubtful": 4,
    "out": 5,
}
OWN_TAG_COLS: tuple[str, ...] = ("own_tag", "own_tag_hours")
HOURS_CAP = 48.0
KEYS = ["game_id", "player_id"]


def build_own_tag(
    roster: pl.DataFrame,
    games: pl.DataFrame,
    avail: pl.DataFrame,
    cfg: ReportTriggerConfig,
    tips: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """``roster`` (game_id, player_id, ...) -> keys + ``own_tag``, ``own_tag_hours``, ``_has_snap``.

    ``avail`` has game_id, player_id, status, as_of, source (raw ``player_availability``).
    """
    if cfg.tip_source != "real":
        raise ValueError("OWN_TAG uses real tips only")
    rows = (
        avail.filter(pl.col("source").is_in(list(cfg.sources)) & pl.col("player_id").is_not_null())
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            "player_id",
            pl.col("status").str.to_lowercase().alias("status"),
            pl.col("as_of").cast(pl.Datetime("us")),
            "game_date",
        )
    )
    rows = attach_real_tips(rows, tips)
    proxy = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(
        minutes=int(cfg.tipoff_hour_et * 60.0)
    )
    usable = usable_report_rows(rows, cfg)
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_snap"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_snap"))
    code = pl.col("status").replace_strict(TAG_CODES, default=0, return_dtype=pl.Int64)
    tags = snap.with_columns(code.alias("_code")).group_by(KEYS).agg(pl.col("_code").max())
    hours = (
        snap.group_by("game_id")
        .agg(
            pl.col("_snap").first(),
            pl.coalesce(pl.col("tip_et").first(), proxy.first()).alias("_tip"),
        )
        .select(
            "game_id",
            ((pl.col("_tip") - pl.col("_snap")).dt.total_seconds() / 3600.0)
            .clip(0.0, HOURS_CAP)
            .alias("_hours"),
        )
    )
    out = (
        roster.select(KEYS)
        .join(tags, on=KEYS, how="left")
        .join(hours, on="game_id", how="left")
        .with_columns(
            pl.col("_hours").is_not_null().cast(pl.Int64).alias("_has_snap"),
            pl.col("_code").fill_null(0).cast(pl.Float64).alias("own_tag"),
            pl.col("_hours").fill_null(HOURS_CAP).cast(pl.Float64).alias("own_tag_hours"),
        )
        .select([*KEYS, "own_tag", "own_tag_hours", "_has_snap"])
    )
    assert out.height == roster.height, "roster must be unique on (game_id, player_id)"
    return out


def placebo_own_tag(frame: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """``own_tag`` permuted within (team_id, game_date); the keys and every other column are kept."""
    rng = np.random.default_rng(seed)
    d = frame.with_row_index("_row").with_columns(pl.Series("_r", rng.random(frame.height)))
    base = d.sort(["team_id", "game_date", "_row"])
    shuf = d.sort(["team_id", "game_date", "_r", "_row"])
    perm = base.select("_row").with_columns(shuf["own_tag"].alias("_tag")).sort("_row")
    return frame.with_columns(perm["_tag"].alias("own_tag"))
