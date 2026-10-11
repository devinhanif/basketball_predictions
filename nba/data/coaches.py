"""Game-level head coach assignment (``team_coach_games``).

Why this exists: ``team_coaches`` (CommonTeamRoster) holds ONE head coach per team-season, the
last occupant of the job including summer hires. It is wrong in 14 of 87 reference team-seasons
(11 name the NEXT season's coach) and cannot hold a mid-season change. See
docs/reviews/data_audit_2026-10-11.md, section 7b.

Definition. One row per (game_id, team_id) for every game in ``games``:

* ``coach_name`` / ``coach_id``: the head coach for that game. Regular season: by the team's
  regular-season game number (date, then game_id order) against the reviewed reference
  ``head_coach_reference.csv`` (season, team, coach, number of games, in coaching order). Playoffs
  and play-in: the coach of the team's last regular-season game.
* ``known_at``: the real scheduled tip (naive UTC) of that game. A coach for a game is public by its
  tip; a change takes effect on the first game he coaches, so this is a conservative bound.
* ``verified``: True for reference rows. Seasons the reference does not cover (2025) fall back to
  ``team_coaches`` with ``verified`` False and ``known_at`` NULL: no as-of consumer can read them.

Imputation: none. A team-season with neither a reference nor a ``team_coaches`` row has no rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

REFERENCE_PATH = Path(__file__).with_name("head_coach_reference.csv")

_SCHEMA_DICT: dict[str, Any] = {
    "game_id": pl.Utf8,
    "team_id": pl.Int64,
    "season": pl.Int64,
    "coach_name": pl.Utf8,
    "coach_id": pl.Int64,
    "source": pl.Utf8,
    "verified": pl.Boolean,
    "known_at": pl.Datetime("us"),
}
SCHEMA = pl.Schema(_SCHEMA_DICT)


def load_reference(path: Path = REFERENCE_PATH) -> pl.DataFrame:
    return pl.read_csv(path, schema_overrides={"team_id": pl.Int64, "season": pl.Int64})


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalpha())


def build_team_coach_games(
    games: pl.DataFrame,
    reference: pl.DataFrame,
    tips: pl.DataFrame,
    team_coaches: pl.DataFrame,
) -> pl.DataFrame:
    """``games``: game_id, game_date, season, home_team, away_team. ``tips``: game_id, tipoff_utc.
    ``team_coaches``: season, team_id, coach_id, name, coach_type."""
    tg = pl.concat(
        [
            games.select("game_id", "game_date", "season", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", "season", pl.col("away_team").alias("team_id")),
        ]
    )
    reg = (
        tg.filter(pl.col("game_id").str.starts_with("002"))
        .sort("season", "team_id", "game_date", "game_id")
        .with_columns((pl.int_range(pl.len()).over(["season", "team_id"]) + 1).alias("reg_no"))
    )
    ref = reference.sort("season", "team_id", "seq").with_columns(
        pl.col("games").cum_sum().over(["season", "team_id"]).alias("upto")
    )
    names = {
        _norm(r["name"]): int(r["coach_id"])
        for r in team_coaches.iter_rows(named=True)
        if r["coach_id"] is not None
    }
    out: list[dict[str, object]] = []
    ref_by = {
        k: g.sort("seq").select("coach_name", "upto").rows()
        for k, g in ref.group_by(["season", "team_id"], maintain_order=True)
    }
    last_coach: dict[tuple[int, int], tuple[str, bool]] = {}
    for r in reg.iter_rows(named=True):
        key = (r["season"], r["team_id"])
        steps = ref_by.get(key)
        if steps is None:
            continue
        nm = next((n for n, upto in steps if r["reg_no"] <= upto), steps[-1][0])
        out.append({**r, "coach_name": nm, "verified": True, "source": "head_coach_reference.csv"})
        last_coach[key] = (nm, True)
    post = tg.filter(~pl.col("game_id").str.starts_with("002"))
    for r in post.iter_rows(named=True):
        key = (r["season"], r["team_id"])
        if key in last_coach:
            nm, _ = last_coach[key]
            out.append(
                {**r, "coach_name": nm, "verified": True,
                 "source": "head_coach_reference.csv (post-season: last regular-season coach)"}
            )  # fmt: skip
    # seasons without a reference: fall back to team_coaches, unverified, not knowable as-of
    hc = team_coaches.filter(pl.col("coach_type") == "Head Coach")
    covered = {k[0] for k in ref_by}
    for r in tg.filter(~pl.col("season").is_in(sorted(covered))).iter_rows(named=True):
        m = hc.filter((pl.col("season") == r["season"]) & (pl.col("team_id") == r["team_id"]))
        if m.height:
            out.append(
                {**r, "coach_name": m["name"][0], "verified": False,
                 "source": "team_coaches (unverified: last occupant, may include later hires)"}
            )  # fmt: skip
    df = pl.DataFrame(out) if out else pl.DataFrame(schema=SCHEMA)
    df = df.with_columns(
        pl.col("coach_name")
        .map_elements(lambda n: names.get(_norm(n)), return_dtype=pl.Int64)
        .alias("coach_id")
    ).join(tips.select("game_id", "tipoff_utc"), on="game_id", how="left")
    df = df.with_columns(
        pl.when(pl.col("verified")).then(pl.col("tipoff_utc")).otherwise(None).alias("known_at")
    )
    return df.select(list(SCHEMA)).cast(SCHEMA).sort("game_id", "team_id")
