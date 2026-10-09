"""Cold-start helpers for players the official roster adds to the forward projection.

Three cases, all history-only (every query runs on the strictly-before-``as_of`` scratch copy):

1. **New to the team** (traded / signed since the player's last game). The minutes model's
   history is partitioned by player, so a mover would silently keep the OLD team's play rate
   and minutes. We do not borrow them blindly: the empirical-Bayes pseudo-counts of the
   minutes model are multiplied by ``role_change.k_multiplier`` (the existing role-change
   convention: 1 + 3 right after the change, decaying linearly to 1 over 8 team-games with
   the new team). The shrinkage target is the league default (24 min, P(play) 0.82), the same
   one rookies get. This is mild for veterans (k=24 vs ~100 prior games) and strong for
   short histories. It is NOT tuned. MEASURED WORSE than keeping the player's own history on
   two opening-week replays (movers' minutes MAE +0.24 and +0.71 min), so
   ``ForwardConfig.new_team_shrinkage`` defaults to False; see docs/OPENING_WEEK_ROSTERS.md.
2. **No NBA history** (rookies, returning-from-abroad). Minutes: the draft-slot ridge prior of
   ``rookie_minutes`` replaces the league default (``apply_prior``). Stats: the distribution of
   the stat among league played games at the same minutes (4-minute bins, last two seasons of
   history), conditional on playing like every other forward distribution.
3. A player missing from ``players_static`` (no pedigree) keeps the league default.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.features.player_pedigree import load_college_map, static_pedigree
from nba.props.config import MinutesModelConfig
from nba.props.distributions import MinutesHurdleDist, NormalDist
from nba.props.role_change import RoleChangeConfig, k_multiplier
from nba.props.rookie_minutes import (
    ROOKIE_MAX_PLAYED,
    RookieMinutesPrior,
    apply_prior,
    class_member_expr,
    first_seasons,
    rookie_training_table,
    with_class_flag,
)

#: (bin_lo, bin_hi, mean, std) of a stat at a minutes level.
BinPrior = tuple[float, float, float, float]
MIN_BIN_ROWS = 200
BIN_WIDTH = 4.0


def new_team_k_multipliers(
    sc: duckdb.DuckDBPyConnection,
    rows: pl.DataFrame,
    season: int,
    rc: RoleChangeConfig | None = None,
) -> np.ndarray:
    """One pseudo-count multiplier per row of ``rows`` (columns player_id, team_id,
    games_played_prior).

    A row is a "team change" when the player has history and the roster team differs from the
    team of his latest played game. ``games_since_change`` = the player's played games for
    the roster team in the current ``season`` since that change (0 until he plays one). After
    his first game for the new team the latest-team test no longer fires, so the run length
    is taken from his team sequence: a trailing run on the roster team that is preceded by a
    different team counts as a change that is ``run`` games old. 1.0 for everyone else."""
    ids = sorted({int(p) for p in rows["player_id"].to_list()})
    if not ids:
        return np.ones(rows.height)
    seq = {
        int(pid): [int(t) for t in teams]
        for pid, teams in sc.execute(
            f"""
            SELECT s.player_id, list(s.team_id ORDER BY g.game_date, g.game_id)
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE s.minutes > 0 AND s.player_id IN ({",".join(str(i) for i in ids)})
            GROUP BY s.player_id
            """
        ).fetchall()
    }
    out = np.ones(rows.height)
    for i, r in enumerate(rows.iter_rows(named=True)):
        pid, team = int(r["player_id"]), int(r["team_id"])
        teams = seq.get(pid, [])
        if int(r["games_played_prior"] or 0) <= 0 or not teams:
            continue
        if teams[-1] != team:
            out[i] = k_multiplier(0.0, rc)
            continue
        run = 0
        for t in reversed(teams):
            if t != team:
                break
            run += 1
        if run < len(teams):  # a trailing run on this team, preceded by another team
            out[i] = k_multiplier(float(run), rc)
    return out


def minute_bin_priors(sc: duckdb.DuckDBPyConnection, season: int) -> dict[str, list[BinPrior]]:
    """Per stat: ``(bin_lo, bin_hi, mean, std)`` of the stat among league played games in the
    last two seasons of history, by 4-minute minutes bin (history only)."""
    df = sc.execute(
        f"""
        SELECT s.minutes, s.pts, s.reb, s.ast, s.fg3m
        FROM player_game_stats s JOIN games g USING (game_id)
        WHERE s.minutes > 0 AND g.season >= {int(season) - 1}
        """
    ).pl()
    out: dict[str, list[BinPrior]] = {}
    if df.is_empty():
        return out
    mins = df["minutes"].cast(pl.Float64).to_numpy()
    edges = np.arange(0.0, 60.0 + BIN_WIDTH, BIN_WIDTH)
    which = np.digitize(mins, edges) - 1
    for stat in ("pts", "reb", "ast", "fg3m"):
        y = df[stat].cast(pl.Float64).fill_null(0.0).to_numpy()
        rows: list[BinPrior] = []
        for b in range(len(edges) - 1):
            m = which == b
            if int(m.sum()) >= MIN_BIN_ROWS:
                rows.append(
                    (
                        float(edges[b]),
                        float(edges[b + 1]),
                        float(y[m].mean()),
                        float(y[m].std(ddof=1)),
                    )
                )
        out[stat] = rows
    return out


def cold_stat_dist(
    priors: dict[str, list[BinPrior]], stat: str, minutes: float
) -> NormalDist | None:
    """League Normal(mean, std) of ``stat`` for played games at ~``minutes`` (the nearest
    populated bin); None when no bin exists (the caller keeps its existing default)."""
    bins = priors.get(stat, [])
    if not bins:
        return None
    best = min(
        bins,
        key=lambda b: (
            0.0 if b[0] <= minutes < b[1] else min(abs(minutes - b[0]), abs(minutes - b[1]))
        ),
    )
    return NormalDist(best[2], max(best[3], 0.3))


def rookie_minutes_mu(
    sc: duckdb.DuckDBPyConnection,
    rows: pl.DataFrame,
    season: int,
    static_raw: pl.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    """``(prior_mu, is_rookie)`` aligned to ``rows`` (player_id, n_played_prior).

    The ridge is fit on rookie classes strictly before ``season`` using the history scratch.
    ``is_rookie``: n_played_prior < 40 and a member of the rookie class of ``season`` (first
    played season == ``season``, or no history at all -> a debut now). Without >= 20 training
    rookies the prior is skipped (all-False)."""
    n = rows.height
    empty = (np.full(n, np.nan), np.zeros(n, dtype=bool))
    played = sc.execute(
        "SELECT s.player_id, g.season, s.minutes FROM player_game_stats s "
        "JOIN games g USING (game_id) WHERE s.minutes > 0"
    ).pl()
    static = static_pedigree(static_raw, load_college_map())
    tr_parts = [rookie_training_table(played, static, c) for c in range(2022, season)]
    tr = pl.concat(tr_parts, how="diagonal_relaxed") if tr_parts else pl.DataFrame()
    if tr.height < 20:
        return empty
    prior = RookieMinutesPrior().fit(tr, tr["mpg"].to_numpy())
    firsts = first_seasons(played)
    ids = rows.select("player_id").with_columns(pl.col("player_id").cast(pl.Int64))
    flagged = with_class_flag(ids, static, firsts)
    npl = rows["n_played_prior"].fill_null(0.0).to_numpy()
    # with_class_flag leaves first_season NULL for debutants; they debut in ``season``.
    flagged = flagged.with_columns(pl.col("first_season").fill_null(season))
    fs = flagged["first_season"].to_numpy()
    member = flagged.with_columns(class_member_expr().fill_null(False).alias("m"))["m"].to_numpy()
    is_rookie = member & (fs == season) & (npl < ROOKIE_MAX_PLAYED)
    ped = ids.join(
        static.select(
            "player_id",
            "log_pick",
            "undrafted",
            "height_in",
            "weight_lb",
            "birth_date",
            "position_primary",
        ),  # fmt: skip
        on="player_id",
        how="left",
        maintain_order="left",
    ).with_columns(pl.lit(season).alias("season"))
    mu = prior.predict(ped)
    return mu, is_rookie


def apply_rookie_prior(
    dists: list[MinutesHurdleDist],
    rows: pl.DataFrame,
    prior_mu: np.ndarray,
    is_rookie: np.ndarray,
    cfg: MinutesModelConfig | None = None,
) -> list[MinutesHurdleDist]:
    cfg = cfg or MinutesModelConfig()
    n_played = rows["n_played_prior"].fill_null(0.0).to_numpy()
    safe = np.where(np.isnan(prior_mu), cfg.default_mu, prior_mu)
    return apply_prior(dists, n_played, safe, is_rookie, cfg)
