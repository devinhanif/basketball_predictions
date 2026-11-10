# ruff: noqa: E501
"""The pure scenario functions behind "if a teammate sits" (docs/contracts/teammate_sits_v1.md).

A scenario is production's own forecast re-run with one more player on tonight's OUT set, through the
same code that handles a real OUT player (``vacated_features``); no model is refitted and none is new.
For teammate T of sitter P exactly ``n_out_rot`` (+1), ``vac_min`` and ``vac_<stat>`` change.

These are the minimal functions of ``research/teammate_sits/scenario.py`` (the reference), moved here
because nothing under ``nba/`` may import ``research`` (tests/test_layering.py).
``tests/export/test_sits_core.py`` proves each returns exactly what the reference returns. One
difference by design: the forecast step takes the stats to forecast (the reference runs all four).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import polars as pl

from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    QUANTILE_TAUS,
    ROTATION_MIN40,
    ContextResidualModel,
    _sel,
    predict_rows,
    vacated_features,
)

#: A regular starter: as-of decayed (half-life 10 games) starter rate at least this.
REGULAR_STARTER10 = 0.6
#: Columns a sitter changes on his teammates' rows (production feature names).
SCN_COLS: tuple[str, ...] = ("n_out_rot", "vac_min", *(f"vac_{s}" for s in PROP_STATS))
SEP = "|"

GameInfo = dict[str, tuple[object, int, int]]


def game_info_of(games: pl.DataFrame) -> GameInfo:
    return {
        str(g): (d, int(h), int(a))
        for g, d, h, a in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }


def played_frame(games: pl.DataFrame, pgs: pl.DataFrame) -> pl.DataFrame:
    """The played rows exactly as ``build_features`` builds them (same filter, same order)."""
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    return (
        pgs.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )


def sitter_rows(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    out_sets: dict[str, set[int]],
    game_info: GameInfo,
) -> pl.DataFrame:
    """One row per (game_id, player_id) in ``out_sets`` that production's ``vacated_features`` would COUNT
    if OUT (same as-of join on the state after the player's latest game before the game date, same
    rotation filter, same team filter), with the per-player contributions and ``starter10``."""
    post = states["post"]
    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "_pd": played["game_date"],
            "team_id": played["team_id"].cast(pl.Int64),
            "n_after": states["n_prior"] + 1,
            "min40": _sel(post, 40.0, "min"),
            "min10": _sel(post, 10.0, "min"),
            "starter10": _sel(post, 10.0, "starter"),
            **{f"v_{s}": _sel(post, 10.0, s) for s in PROP_STATS},
        }
    ).sort("_pd")
    recs = [
        (gid, pid, game_info[gid][0], game_info[gid][1], game_info[gid][2])
        for gid, pids in out_sets.items()
        if gid in game_info
        for pid in pids
    ]
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "home": pl.Int64,
        "away": pl.Int64,
    }
    left = (
        (pl.DataFrame(recs, schema=schema, orient="row") if recs else pl.DataFrame(schema=schema))
        .with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key"))
        .sort("_key")
    )
    j = left.join_asof(st, left_on="_key", right_on="_pd", by="player_id", strategy="backward")
    j = j.filter(
        (pl.col("n_after") >= MIN_PRIOR_PLAYED)
        & (pl.col("min40") >= ROTATION_MIN40)
        & ((pl.col("team_id") == pl.col("home")) | (pl.col("team_id") == pl.col("away")))
    )
    return j.rename({"_pd": "last_date"}).drop("_key", "home", "away", "n_after")


def rostered_not_out(roster: pl.DataFrame, flagged: dict[str, set[int]]) -> dict[str, set[int]]:
    """Rostered players (``game_id``, ``player_id``) not OUT on a game's report, for games that HAVE one."""
    out_sets: dict[str, set[int]] = {}
    for g, p in roster.select("game_id", "player_id").unique().iter_rows():
        g = str(g)
        if g in flagged and int(p) not in flagged[g]:
            out_sets.setdefault(g, set()).add(int(p))
    return out_sets


def candidates(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    roster: pl.DataFrame,
    flagged: dict[str, set[int]],
    game_info: GameInfo,
    min_starter10: float = REGULAR_STARTER10,
) -> pl.DataFrame:
    """Possible sitters on a slate: rostered players not already OUT on the report who would count as
    vacated if OUT and are regular starters. Games without a report are excluded (production has no
    vacancy there; a scenario would invent a report)."""
    s = sitter_rows(played, states, rostered_not_out(roster, flagged), game_info)
    return s.filter(pl.col("starter10") >= min_starter10).sort(["game_id", "team_id", "player_id"])


def scenario_vacancy(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    flagged: dict[str, set[int]],
    game_info: GameInfo,
    sitters: Iterable[tuple[str, int]],
) -> pl.DataFrame:
    """For each (game_id, sitter_id): production ``vacated_features`` for that game with the sitter ADDED to
    the report's OUT set (exact recompute, one batched call keyed ``"<game_id>|<sitter_id>"``). Returns
    game_id, sitter_id, team_id, ``SCN_COLS`` for every team with any vacancy in the edited game (a team
    absent here has zero vacancy)."""
    keyed: dict[str, set[int]] = {}
    info: GameInfo = {}
    for g, p in sitters:
        k = f"{g}{SEP}{int(p)}"
        keyed[k] = set(flagged.get(str(g), set())) | {int(p)}
        info[k] = game_info[str(g)]
    vac = vacated_features(played, states, keyed, info)
    parts = vac["game_id"].str.split_exact(SEP, 1)
    return vac.with_columns(
        parts.struct.field("field_0").alias("game_id"),
        parts.struct.field("field_1").cast(pl.Int64).alias("sitter_id"),
    ).select("game_id", "sitter_id", "team_id", *SCN_COLS)


def scenario_features(
    base: pl.DataFrame, scn_vac: pl.DataFrame, sitters: pl.DataFrame
) -> pl.DataFrame:
    """Teammate rows of each sitter (``sitters``: game_id, sitter_id, team_id) with the vacancy columns
    replaced by the scenario's. ``base`` is ``build_features`` output for the slate. The sitter's own row
    is dropped; rows of a game without a report never get a scenario."""
    tm = sitters.join(base, on=["game_id", "team_id"], how="inner").filter(
        (pl.col("player_id") != pl.col("sitter_id")) & (pl.col("has_report") == 1)
    )
    tm = tm.drop(*SCN_COLS).join(
        scn_vac.with_columns([pl.col(c).cast(base.schema[c]) for c in SCN_COLS]),
        on=["game_id", "sitter_id", "team_id"],
        how="left",
    )
    # a reported team-game with nobody rotation-OUT carries explicit zeros (production semantics)
    tm = tm.with_columns([pl.col(c).fill_null(0) for c in SCN_COLS])
    return tm.select(base.columns + ["sitter_id"])


def _forecast(
    models: dict[str, ContextResidualModel],
    feats: pl.DataFrame,
    keys: list[str],
    stats: Sequence[str],
) -> pl.DataFrame:
    taus = np.array(QUANTILE_TAUS)
    out = []
    for stat in stats:
        rows, mean, q = predict_rows(models, feats, stat, taus)
        if rows.is_empty():
            continue
        out.append(
            rows.select(keys).with_columns(
                pl.lit(stat).alias("stat"),
                pl.Series("mean", mean),
                pl.Series("q10", q[:, 1]),
                pl.Series("q50", q[:, 9]),
                pl.Series("q90", q[:, 17]),
            )
        )
    return pl.concat(out) if out else pl.DataFrame()


def scenario_forecasts(
    models: dict[str, ContextResidualModel],
    base: pl.DataFrame,
    scn: pl.DataFrame,
    stats: Sequence[str] = PROP_STATS,
) -> pl.DataFrame:
    """Long table: one row per (game_id, sitter_id, player_id, stat) with baseline and scenario mean and
    q10/q50/q90 and ``delta_mean``. Same fitted models, same rows; only the vacancy inputs differ."""
    keys = ["game_id", "team_id", "player_id"]
    b = _forecast(models, base, keys, stats)
    s = _forecast(models, scn, [*keys, "sitter_id"], stats)
    if s.is_empty() or b.is_empty():
        return pl.DataFrame()
    j = s.join(b, on=[*keys, "stat"], how="inner", suffix="_base")
    return j.select(
        "game_id",
        "team_id",
        "sitter_id",
        "player_id",
        "stat",
        pl.col("mean_base").alias("base_mean"),
        pl.col("mean").alias("scn_mean"),
        (pl.col("mean") - pl.col("mean_base")).alias("delta_mean"),
        pl.col("q10_base").alias("base_q10"),
        pl.col("q50_base").alias("base_q50"),
        pl.col("q90_base").alias("base_q90"),
        pl.col("q10").alias("scn_q10"),
        pl.col("q50").alias("scn_q50"),
        pl.col("q90").alias("scn_q90"),
    ).sort(["game_id", "sitter_id", "player_id", "stat"])
