"""Static player pedigree features (draft slot, college group, age) for cold-start priors.

CLAUDE.md cold-start method 3 ("rookie priors from draft and college"). This
module only builds **features**; the screen that asks whether they carry
signal lives in ``nba.eval.context_screen_players``.

As-of discipline: every column is a pure function of ``players_static`` (draft
year/pick, birth date, college, height/weight/position -- facts fixed before
any game is played) and the *game date* it is evaluated at (age, years since
draft). No box-score, minutes or result of the current or any later game is
read, so nothing here can leak the future. ``tests/features/
test_player_pedigree.py`` proves it with a planted-future-game check.

Missingness (documented, not silently filled):

* ``draft_year`` and ``draft_pick`` both NULL (~33% of ``players_static``) is
  treated as **undrafted** (``undrafted = 1``). Those rows hold real veterans
  (Seth Curry-type, 48 with >2000 NBA minutes in the data), consistent with
  undrafted free agents, and international players never drafted. This cannot
  be proven from the table alone: a handful may be draft-data gaps.
* ``draft_year`` set but ``draft_pick`` NULL (6 rows) is **unknown pick**:
  ``undrafted = 0``, ``draft_pick`` stays NULL, ``log_pick`` NULL.
* ``college_stats`` is entirely NULL in the real DB and is not used.
* ``college`` is mapped to a coarse group by the HAND-BUILT table in
  ``configs/college_conferences.yaml`` (judgement, not an external source).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import yaml

DEFAULT_COLLEGE_CONFIG = (
    Path(__file__).resolve().parents[2] / "configs" / "college_conferences.yaml"
)
COLLEGE_GROUPS = (
    "power_conf",
    "mid_major",
    "international_pro",
    "pathway",
    "high_school",
    "unknown",
)
#: Nominal draft night (late June); used for age-at-draft and years-since-draft.
DRAFT_MONTH_DAY = (6, 25)
_DAYS_PER_YEAR = 365.25


def load_college_map(path: Path | str = DEFAULT_COLLEGE_CONFIG) -> dict[str, str]:
    """college string -> group, from the hand-built yaml. Fails loudly on a
    name listed under two groups or on an unknown group key."""
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    out: dict[str, str] = {}
    for group, names in raw.items():
        if group not in COLLEGE_GROUPS:
            raise ValueError(f"unknown college group {group!r} in {path}")
        for name in names or []:
            key = str(name).strip()
            if key in out and out[key] != group:
                raise ValueError(f"college {key!r} listed under {out[key]!r} and {group!r}")
            out[key] = group
    return out


def college_group(college: str | None, mapping: dict[str, str]) -> str:
    """Group for one college string. NULL/blank -> ``unknown``; a non-blank
    name not in any explicit list defaults to ``mid_major`` (documented rule)."""
    if college is None or not str(college).strip():
        return "unknown"
    return mapping.get(str(college).strip(), "mid_major")


def _primary_position(pos: str | None) -> str:
    if not pos:
        return "Unknown"
    return pos.split("-")[0]


def static_pedigree(players: pl.DataFrame, mapping: dict[str, str]) -> pl.DataFrame:
    """Time-invariant pedigree columns, one row per ``player_id``.

    Input: ``players_static`` columns. Output columns: player_id, draft_year,
    draft_pick (NULL if undrafted/unknown), undrafted (0/1), draft_round
    (1/2, 0 = undrafted, NULL = unknown pick), log_pick (ln pick; undrafted
    gets ln(61)), college_group, position_primary, height_in, weight_lb,
    birth_date, age_at_draft (years; NULL if undrafted/no birth date),
    young_high_pick (pick <= 14 and age_at_draft < 20.5; 0 otherwise)."""
    if players["player_id"].n_unique() != players.height:
        raise ValueError("players_static player_id must be unique")
    groups = [college_group(c, mapping) for c in players["college"].to_list()]
    positions = [_primary_position(p) for p in players["position"].to_list()]
    df = players.with_columns(
        pl.Series("college_group", groups, dtype=pl.Utf8),
        pl.Series("position_primary", positions, dtype=pl.Utf8),
    )
    undrafted = pl.col("draft_year").is_null() & pl.col("draft_pick").is_null()
    draft_date = pl.date(pl.col("draft_year"), DRAFT_MONTH_DAY[0], DRAFT_MONTH_DAY[1])
    df = df.with_columns(
        undrafted.cast(pl.Int8).alias("undrafted"),
        pl.when(undrafted)
        .then(0)
        .when(pl.col("draft_pick").is_null())
        .then(None)
        .when(pl.col("draft_pick") <= 30)
        .then(1)
        .otherwise(2)
        .alias("draft_round"),
        pl.when(undrafted)
        .then(pl.lit(61.0).log())
        .otherwise(pl.col("draft_pick").cast(pl.Float64).log())
        .alias("log_pick"),
        ((draft_date - pl.col("birth_date")).dt.total_days() / _DAYS_PER_YEAR).alias(
            "age_at_draft"
        ),
    )
    df = df.with_columns(
        ((pl.col("draft_pick") <= 14).fill_null(False) & (pl.col("age_at_draft") < 20.5))
        .cast(pl.Int8)
        .alias("young_high_pick")
    )
    return df.select(
        "player_id",
        "draft_year",
        "draft_pick",
        "undrafted",
        "draft_round",
        "log_pick",
        "college_group",
        "position_primary",
        "height_in",
        "weight_lb",
        "birth_date",
        "age_at_draft",
        "young_high_pick",
    )


def pedigree_as_of(static: pl.DataFrame, rows: pl.DataFrame) -> pl.DataFrame:
    """Attach the pedigree to ``rows`` (player_id, game_date) with the two
    date-dependent columns: ``age`` (years at game_date) and
    ``years_since_draft`` (NULL for undrafted). Left join: row count and order
    of ``rows`` are preserved; a player missing from ``static`` fails loudly."""
    missing = set(rows["player_id"].unique().to_list()) - set(static["player_id"].to_list())
    if missing:
        raise ValueError(f"{len(missing)} player_id(s) absent from players_static")
    out = rows.join(static, on="player_id", how="left", maintain_order="left")
    draft_date = pl.date(pl.col("draft_year"), DRAFT_MONTH_DAY[0], DRAFT_MONTH_DAY[1])
    return out.with_columns(
        ((pl.col("game_date") - pl.col("birth_date")).dt.total_days() / _DAYS_PER_YEAR).alias(
            "age"
        ),
        ((pl.col("game_date") - draft_date).dt.total_days() / _DAYS_PER_YEAR).alias(
            "years_since_draft"
        ),
    )


def age_on(birth: date, on: date) -> float:
    """Age in years of someone born ``birth`` on date ``on`` (scalar helper)."""
    return (on - birth).days / _DAYS_PER_YEAR
