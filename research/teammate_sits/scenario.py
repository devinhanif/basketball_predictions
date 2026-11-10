# ruff: noqa: E501
"""'If a teammate sits' scenarios from the production context-residual inputs (reference implementation, research only).

Design (docs/prereg/TEAMMATE_SITS.md, DRAFT): a scenario is the production forecast re-run with one player added to
tonight's OUT set, through the SAME code that handles a real OUT player (``vacated_features`` on the official-report
OUT set). Nothing is refitted and no new model exists. For teammate T of sitter P on P's team, exactly these inputs
change: ``n_out_rot`` (+1), ``vac_min`` (+P's as-of min10) and ``vac_<stat>`` (+P's as-of 10-game mean), with
``has_report = 1``. Everything else on T's row (own recency, starter history, Elo margin, opponent context) and every
row of the opponent are unchanged, so the opponent's scenario equals its baseline by construction.

Batching trick: one ``vacated_features`` call covers every (game, sitter) by keying the OUT dict on
``"<game_id>|<sitter_id>"`` with the game's info copied; each key is an exact recompute (not an increment) of the
production vacancy for that game with the sitter added. ``tests/research/teammate_sits`` proves the result equals a
full ``build_features`` rebuild with the sitter on the report.

Entry point for one historical slate (maintainer; fits four LightGBM heads for the date, reads ``season <= 2024`` only):

    mkdir data/ops/heavy.lock && (python -W ignore -m research.teammate_sits.scenario \\
        --db nba.duckdb --date 2024-01-15 --out scratch/teammate_sits_2024-01-15.parquet; rmdir data/ops/heavy.lock)

The historical slate uses played rows as tonight's roster (box-score membership); fine for a demonstration and for the
paired test (both arms share the rows), never as an availability forecast.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    QUANTILE_TAUS,
    ROTATION_MIN40,
    ContextResidualModel,
    _sel,
    player_states,
    predict_rows,
    vacated_features,
)

#: A regular starter: as-of decayed (halflife 10 games) starter rate at least this.
REGULAR_STARTER10 = 0.6
#: Columns a sitter changes on his teammates' rows (production feature names).
SCN_COLS: tuple[str, ...] = ("n_out_rot", "vac_min", *(f"vac_{s}" for s in PROP_STATS))
SEP = "|"
MAX_SEASON = 2024

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
    """One row per (game_id, player_id) in ``out_sets`` that production's ``vacated_features`` would COUNT if OUT
    (same as-of join on the state after the player's latest game before the game date, same rotation filter, same
    team filter), with the per-player contributions and ``starter10`` / ``last_date`` for event definitions.
    ``tests`` tie its group sums to ``vacated_features`` exactly."""
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


def candidates(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    roster: pl.DataFrame,
    flagged: dict[str, set[int]],
    game_info: GameInfo,
    min_starter10: float = REGULAR_STARTER10,
) -> pl.DataFrame:
    """Possible sitters on a slate: rostered players (``game_id``, ``player_id``) not already OUT on the report
    who would count as vacated if OUT and are regular starters. Games without a report are excluded (production
    has no vacancy there; a scenario would invent a report)."""
    out_sets: dict[str, set[int]] = {}
    for g, p in roster.select("game_id", "player_id").unique().iter_rows():
        g = str(g)
        if g in flagged and int(p) not in flagged[g]:
            out_sets.setdefault(g, set()).add(int(p))
    s = sitter_rows(played, states, out_sets, game_info)
    return s.filter(pl.col("starter10") >= min_starter10).sort(["game_id", "team_id", "player_id"])


def scenario_vacancy(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    flagged: dict[str, set[int]],
    game_info: GameInfo,
    sitters: Iterable[tuple[str, int]],
    remove: bool = False,
) -> pl.DataFrame:
    """For each (game_id, sitter_id): production ``vacated_features`` for that game with the sitter ADDED to the
    report's OUT set (exact recompute, one batched call). ``remove=True`` is the counterfactual of the pre-registered
    check: the sitter taken OFF a real report ("the forecast before the news"). Returns game_id, sitter_id, team_id,
    ``SCN_COLS`` for every team with any vacancy in the edited game (a team absent here has zero vacancy)."""
    keyed: dict[str, set[int]] = {}
    info: GameInfo = {}
    for g, p in sitters:
        k = f"{g}{SEP}{int(p)}"
        cur = set(flagged.get(str(g), set()))
        keyed[k] = cur - {int(p)} if remove else cur | {int(p)}
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
    """Teammate rows of each sitter (``sitters``: game_id, sitter_id, team_id) with the vacancy columns replaced by
    the scenario's. ``base`` is ``build_features`` output for the slate. The sitter's own row is dropped. Rows of a
    game without a report never get a scenario (``candidates`` already excludes them)."""
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
    models: dict[str, ContextResidualModel], feats: pl.DataFrame, keys: list[str]
) -> pl.DataFrame:
    taus = np.array(QUANTILE_TAUS)
    out = []
    for stat in PROP_STATS:
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
) -> pl.DataFrame:
    """Long table: one row per (game_id, sitter_id, player_id, stat) with the baseline and scenario mean and
    q10/q50/q90 and ``delta_mean``. Same fitted models, same rows; only the vacancy inputs differ."""
    keys = ["game_id", "team_id", "player_id"]
    b = _forecast(models, base, keys)
    s = _forecast(models, scn, [*keys, "sitter_id"])
    if s.is_empty():
        return s
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


def slate_scenarios(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    feats: pl.DataFrame,
    flagged: dict[str, set[int]],
    models: dict[str, ContextResidualModel],
    slate_ids: list[str],
    roster: pl.DataFrame,
) -> pl.DataFrame:
    """Every scenario for one slate: candidates on ``roster`` (game_id, player_id), their vacancy recompute, and the
    paired forecasts. ``feats`` is the production feature frame covering the slate rows."""
    played = played_frame(games, pgs)
    states = player_states(played)
    info = game_info_of(games)
    cand = candidates(
        played, states, roster.filter(pl.col("game_id").is_in(slate_ids)), flagged, info
    )
    if cand.is_empty():
        return pl.DataFrame()
    sitters = cand.select("game_id", pl.col("player_id").alias("sitter_id"), "team_id")
    vac = scenario_vacancy(
        played, states, flagged, info, sitters.select("game_id", "sitter_id").rows()
    )
    base = feats.filter(pl.col("game_id").is_in(slate_ids))
    scn = scenario_features(base, vac, sitters)
    return scenario_forecasts(models, base, scn)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - maintainer entry point
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.props.context_residual import (
        ContextResidualConfig,
        build_features,
        fit_for_date,
        flagged_from_availability,
    )

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--date", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()

    def log(m: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {m}", flush=True)

    games, pgs, static, avail = load_inputs(a.db, None, MAX_SEASON)
    if float(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise RuntimeError("season 2025 is the frozen holdout")
    games = games.filter(pl.col("game_date").cast(pl.Date) <= a.date)
    pgs = pgs.join(games.select("game_id"), on="game_id")
    elo = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)
    log(f"inputs: {games.height} games, {pgs.height} player-games")
    feats = build_features(games, pgs, static, flagged, elo)
    log(f"features: {feats.height} rows")
    models = fit_for_date(feats, a.date, cfg)
    log("fitted four heads")
    slate = games.filter(pl.col("game_date").cast(pl.Date) == a.date)
    ids = [str(g) for g in slate["game_id"].to_list()]
    roster = feats.filter(pl.col("game_id").is_in(ids)).select("game_id", "player_id")
    out = slate_scenarios(games, pgs, feats, flagged, models, ids, roster)
    out.write_parquet(a.out)
    n_s = out.select("game_id", "sitter_id").unique().height if out.height else 0
    log(f"{out.height} rows, {n_s} scenarios, {len(ids)} games -> {a.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__: list[Any] = [
    "REGULAR_STARTER10",
    "SCN_COLS",
    "candidates",
    "scenario_features",
    "scenario_forecasts",
    "scenario_vacancy",
    "sitter_rows",
    "slate_scenarios",
]
