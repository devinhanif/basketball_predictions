"""Usage redistribution when a high-usage teammate is projected absent.

``docs/NEXT_OPTIONS.md`` §2: the shipped ``shot_share_prior`` proxy
(``nba.features.player_possession_features.build_player_shot_rates``) never
reallocates usage when a top-usage teammate sits -- it is a whole-game,
whole-season proxy, blind to who else was on the floor for any single game.
A previous attempt to fix this a different way (``use_oncourt_usage=True``,
a true on-court shot-share *denominator*) REGRESSED points CRPS by +0.398
(CI excludes 0) by over-concentrating the shot distribution once
renormalized across the on-court five -- see that module's docstring. This
module deliberately does **not** touch the denominator; it keeps the
shipped whole-game proxy exactly as-is and only perturbs the attribution
*weight vector* that feeds ``nba.sim.player_attribution._usage_weight``,
for the narrow slice of games where a projected-top-N-usage teammate is
flagged absent.

The absence/availability signal is always an **injected argument**
(``absent_player_ids``) -- this module never computes minutes or lineups
itself (that is ``nba.props.minutes``'s job; this module has no DuckDB
write access and is not allowed to touch ``nba/props/*`` per this task's
scope). Composition with the existing sim is entirely from the outside:
build :class:`nba.sim.player_attribution.PlayerSimProfile` rows as today via
``profiles_from_features``, then call :func:`apply_usage_redistribution` on
the resulting list *before* passing it to
``nba.sim.player_attribution.simulate_game_with_players`` -- no changes to
``nba.sim.player_attribution`` itself are required or made.

## Two head-to-head options (both behind the same ``enabled`` flag, default off)

- **Option A** (``method="player"``): a player-level as-of reference --
  "how much did teammates' *realized* shot-share historically exceed the
  simple proportional-renormalization baseline, in as-of-earlier games
  where a top-N-usage teammate had 0 minutes" --
  :func:`historical_player_boost_reference`.
- **Option B** (``method="team"``): the same question, answered at the
  team-season level via an as-of Gini-concentration delta (coarser, fewer,
  lower-variance observations per cell, per the design brief's rationale
  for avoiding a per-(star, replacement) pair fit on thin data) --
  :func:`historical_team_boost_reference`.

Both reference functions return ``(boost_fraction, n_qualifying)`` and feed
the exact SAME application function, :func:`apply_usage_redistribution`, so
A and B are comparable head-to-head on identical mechanics -- the only
difference between the two options is which function produced the scalar.

## Shrinkage, not a hard switch

Both reference functions shrink their raw observed excess toward 0.0 (a
pure no-op, identical to today's shipped flat-``shot_share_prior``
behavior) via ``nba.coldstart.shrinkage.shrink_rate``, with the qualifying
historical sample size as ``n`` and ``config.boost_pseudo_count`` as ``k`` --
the same "ship first, shrink to the prior when data is thin" discipline
CLAUDE.md requires for every cold-start-adjacent rate in this project. On
the tiny committed test fixtures the qualifying sample is typically empty
or tiny, so both reference functions correctly return ``(0.0, 0)`` or a
heavily-shrunk value close to 0 -- documented, not a bug (see this module's
test file for the shrinkage-convergence proof: observed excess as
``n -> large``, 0.0 as ``n -> 0``).

## The capped, richer-get-richer redistribution itself

:func:`adjust_profiles_for_absence` is the one place that actually changes
numbers. For each absent top-N-usage player, their typical
``shot_share_prior`` (not scaled by their own now-near-zero availability --
it represents the share of the team's shots they would have taken) is
"freed." A **capped** fraction of that freed share (``min(raw_boost,
config.max_boost_fraction)`` -- the shrinkage-style cap this task's brief
calls "essential to avoid the over-concentration that killed on-court
usage") is added to each *present* teammate's ``shot_share``, weighted by
the *square* of their own existing ``shot_share`` (richer-get-richer: a
30%-usage teammate absorbs disproportionately more of the freed share than
a 10%-usage teammate, not an equal split). The remaining, un-boosted
fraction of the freed share is implicitly redistributed exactly
proportionally, for free, by the normalization
``nba.sim.player_attribution._normalized_weights`` already performs when an
absent player's near-zero-availability weight collapses toward 0 -- this
module only adds the *extra* tilt beyond that automatic proportional
baseline, never replaces it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.shrinkage import shrink_rate
from nba.sim.player_attribution import PlayerSimProfile

#: Floor below which a redistribution weight is treated as 0 (same
#: convention as ``nba.sim.player_attribution._WEIGHT_EPS``, duplicated
#: rather than imported -- see that module's own docstring on keeping
#: private helpers un-imported across modules).
_WEIGHT_EPS = 1e-9

_DUCKDB_TO_POLARS: dict[str, type[pl.DataType]] = {
    "BIGINT": pl.Int64,
    "INTEGER": pl.Int64,
    "SMALLINT": pl.Int64,
    "HUGEINT": pl.Int64,
    "DOUBLE": pl.Float64,
    "FLOAT": pl.Float64,
    "DATE": pl.Date,
    "BOOLEAN": pl.Boolean,
    "VARCHAR": pl.Utf8,
}


def _query_to_polars(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None
) -> pl.DataFrame:
    """Run ``sql`` and materialize the result as a polars DataFrame.

    Same "plain ``fetchall()`` + explicit schema from ``cursor.description``"
    pattern as ``nba.features.team_features._query_to_polars`` (duplicated,
    not imported, per that module's convention of keeping private helpers
    un-shared across modules) -- avoids pulling in pyarrow via ``.pl()``.
    """
    con.execute(sql, params or [])
    columns = [d[0] for d in con.description]
    duckdb_types = [str(d[1]) for d in con.description]
    schema = {
        name: _DUCKDB_TO_POLARS.get(dt, pl.Float64)
        for name, dt in zip(columns, duckdb_types, strict=True)
    }
    rows = con.fetchall()
    if not rows:
        return pl.DataFrame(schema={c: schema[c] for c in columns})
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


@dataclass(frozen=True)
class UsageRedistributionConfig:
    """Flag-gated config for both Option A and Option B (see module docstring).

    Defaults to fully off (``enabled=False``) -- the shipped behavior is
    completely unchanged unless a caller explicitly opts in.
    """

    enabled: bool = False
    method: str = "player"  # "player" (Option A) | "team" (Option B)
    top_n_usage: int = 2
    #: Shrinkage-style cap on the fraction of an absent top-usage player's
    #: freed ``shot_share`` that gets the richer-get-richer tilt (the rest
    #: is redistributed proportionally for free -- see module docstring).
    #: Bounded well below 1.0 specifically to avoid the winner-take-all
    #: over-concentration that regressed ``use_oncourt_usage``.
    max_boost_fraction: float = 0.3
    #: Pseudo-count ``k`` (in "qualifying historical games" units) for the
    #: empirical-Bayes shrinkage in both reference functions.
    boost_pseudo_count: float = 20.0


def effective_boost_fraction(raw_boost: float, config: UsageRedistributionConfig) -> float:
    """Clip a raw reference-rate output into ``[0, config.max_boost_fraction]``.

    Negative raw values (teammates' shares historically fell, not rose, when
    a star sat -- plausible, e.g. a different ball-handler absorbs
    possessions without taking more shots) are floored at 0: this module
    only ever adds weight, never removes it, since removing weight from an
    already-present player who didn't sit is a different, unvalidated claim.
    """
    return float(np.clip(raw_boost, 0.0, config.max_boost_fraction))


def adjust_profiles_for_absence(
    profiles: list[PlayerSimProfile],
    absent_player_ids: set[int],
    boost_fraction: float,
    top_n_usage: int = 2,
) -> list[PlayerSimProfile]:
    """Return a NEW profile list with present teammates' ``shot_share``
    boosted when an absent player was top-N usage; a no-op copy otherwise.

    No-op conditions (returns ``profiles`` completely unchanged, same
    objects, not merely numerically close -- the full-sample regression
    guard this task requires): ``boost_fraction <= 0``, no profiles, no
    overlap between ``absent_player_ids`` and the roster's own top-N-usage
    players (an absent bench player with no usage to redistribute is
    correctly treated as nothing happening), or no present players left to
    receive the boost.
    """
    if not profiles or boost_fraction <= 0 or not absent_player_ids:
        return profiles
    ranked = sorted(profiles, key=lambda p: p.shot_share, reverse=True)
    top_ids = {p.player_id for p in ranked[:top_n_usage]}
    absent_top = [
        p for p in profiles if p.player_id in absent_player_ids and p.player_id in top_ids
    ]
    if not absent_top:
        return profiles
    present = [p for p in profiles if p.player_id not in absent_player_ids]
    if not present:
        return profiles

    freed_usage = float(sum(p.shot_share for p in absent_top))
    sq = np.array([max(p.shot_share, 0.0) ** 2 for p in present], dtype=float)
    sq_sum = sq.sum()
    if sq_sum <= _WEIGHT_EPS:
        richer_share = np.full(len(present), 1.0 / len(present))
    else:
        richer_share = sq / sq_sum
    boost_total = boost_fraction * freed_usage

    present_extra = {p.player_id: boost_total * richer_share[i] for i, p in enumerate(present)}
    adjusted: list[PlayerSimProfile] = []
    for p in profiles:
        extra = present_extra.get(p.player_id)
        if extra is None or extra <= 0:
            adjusted.append(p)
        else:
            adjusted.append(replace(p, shot_share=float(p.shot_share + extra)))
    return adjusted


def apply_usage_redistribution(
    profiles: list[PlayerSimProfile],
    absent_player_ids: set[int],
    config: UsageRedistributionConfig,
    raw_boost: float,
) -> list[PlayerSimProfile]:
    """Flag-gated entry point composing :func:`effective_boost_fraction` and
    :func:`adjust_profiles_for_absence` -- the one function callers need.

    Returns ``profiles`` unchanged (no copy) when ``config.enabled`` is
    False or ``absent_player_ids`` is empty, so a disabled flag is
    byte-for-byte identical to never having called this module at all.
    """
    if not config.enabled or not absent_player_ids:
        return profiles
    boost_fraction = effective_boost_fraction(raw_boost, config)
    return adjust_profiles_for_absence(
        profiles, absent_player_ids, boost_fraction, config.top_n_usage
    )


def _gini(shares: np.ndarray) -> float:
    """Standard Gini coefficient of a non-negative share vector, 0 for
    ``n <= 1`` or an all-zero vector (perfectly "equal" degenerate cases)."""
    x = np.sort(np.clip(np.asarray(shares, dtype=float), 0.0, None))
    n = len(x)
    total = x.sum()
    if n <= 1 or total <= 0:
        return 0.0
    idx = np.arange(1, n + 1, dtype=float)
    return float((2.0 * np.sum(idx * x)) / (n * total) - (n + 1) / n)


_ACTUAL_SHOT_SHARE_SQL = """
WITH shots AS (
    SELECT p.game_id, p.off_team AS team_id, p.shooter_id AS player_id
    FROM possessions p
    JOIN games g ON g.game_id = p.game_id
    WHERE p.shooter_id IS NOT NULL AND g.game_date < ?
),
pfga AS (
    SELECT game_id, team_id, player_id, COUNT(*) AS n_fga
    FROM shots
    GROUP BY 1, 2, 3
),
tfga AS (
    SELECT game_id, team_id, SUM(n_fga) AS team_n_fga
    FROM pfga
    GROUP BY 1, 2
)
SELECT p.game_id, p.team_id, p.player_id, p.n_fga, t.team_n_fga
FROM pfga p
JOIN tfga t USING (game_id, team_id)
"""

#: Roster membership for every historical (as-of ``as_of_date``) team-game
#: -- ``player_game_stats`` rather than ``possessions.shooter_id`` on
#: purpose: a player who is on the roster but attempted 0 shots (e.g. truly
#: absent, ``minutes == 0``) has NO row in ``possessions`` as a shooter for
#: that game at all, so starting from the shot table would silently drop
#: exactly the "absent top-usage player" rows this module needs to detect.
_ROSTER_SQL = """
SELECT s.game_id, s.team_id, s.player_id, g.game_date, COALESCE(s.minutes, 0.0) AS minutes
FROM player_game_stats s
JOIN games g ON g.game_id = s.game_id
WHERE g.game_date < ?
"""


def _qualifying_rows(
    con: duckdb.DuckDBPyConnection,
    as_of_date: dt.date,
    top_n_usage: int,
    shot_rates: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per (game_id, team_id, player_id) for every historical (as-of
    ``as_of_date``) team-game, carrying: ``shot_share_prior`` (that
    player's most recent known as-of usage level heading into this game --
    an as-of merge against ``build_player_shot_rates``, NOT simply that
    game's own shot-table row, since an absent player has no shot-table row
    for the game they sat out), ``actual_shot_share`` (this historical
    game's own realized share, ground truth for the reference fit only --
    this function is never called on the game being predicted), ``is_top``
    (top-N usage by that trailing ``shot_share_prior``), and ``absent``
    (``minutes == 0`` that game). Deliberately imports
    ``build_player_shot_rates`` lazily to avoid a module-level circular
    import with ``nba.features.player_possession_features`` (which does not
    import this module, but keeping the import local documents the
    one-directional dependency).
    """
    from nba.features.player_possession_features import build_player_shot_rates

    roster = _query_to_polars(con, _ROSTER_SQL, [as_of_date])
    if roster.height == 0:
        return roster.with_columns(
            [
                pl.lit(0.0).alias("shot_share_prior"),
                pl.lit(0.0).alias("actual_shot_share"),
                pl.lit(False).alias("is_top"),
                pl.lit(False).alias("absent"),
            ]
        )

    if shot_rates is None:
        shot_rates = build_player_shot_rates(con)
    shot_hist = (
        shot_rates.filter(pl.col("game_date") < as_of_date)
        .select(["player_id", "game_date", "shot_share_prior"])
        .sort(["player_id", "game_date"])
        if shot_rates.height
        else pl.DataFrame(
            schema={"player_id": pl.Int64, "game_date": pl.Date, "shot_share_prior": pl.Float64}
        )
    )

    roster_sorted = roster.sort(["player_id", "game_date"])
    if shot_hist.height == 0:
        usage = roster_sorted.with_columns(pl.lit(0.0).alias("shot_share_prior"))
    else:
        usage = roster_sorted.join_asof(
            shot_hist, on="game_date", by="player_id", strategy="backward"
        )
        usage = usage.with_columns(pl.col("shot_share_prior").fill_null(0.0))

    actual = _query_to_polars(con, _ACTUAL_SHOT_SHARE_SQL, [as_of_date])
    usage = usage.join(
        actual.select(["game_id", "team_id", "player_id", "n_fga", "team_n_fga"]),
        on=["game_id", "team_id", "player_id"],
        how="left",
    )
    usage = usage.with_columns(
        pl.when(pl.col("team_n_fga").is_not_null() & (pl.col("team_n_fga") > 0))
        .then(pl.col("n_fga").fill_null(0) / pl.col("team_n_fga"))
        .otherwise(0.0)
        .alias("actual_shot_share")
    )
    usage = usage.with_columns(
        pl.col("shot_share_prior")
        .rank(method="ordinal", descending=True)
        .over(["game_id", "team_id"])
        .alias("_usage_rank")
    )
    usage = usage.with_columns(
        [
            (pl.col("_usage_rank") <= top_n_usage).alias("is_top"),
            (pl.col("minutes") <= 0.0).alias("absent"),
        ]
    )
    return usage.drop("_usage_rank")


def historical_player_boost_reference(
    con: duckdb.DuckDBPyConnection,
    as_of_date: dt.date,
    top_n_usage: int = 2,
    pseudo_count: float = 20.0,
    rows: pl.DataFrame | None = None,
    shot_rates: pl.DataFrame | None = None,
) -> tuple[float, int]:
    """Option A reference scalar: as-of (strictly before ``as_of_date``)
    average excess shot-share present teammates absorbed, beyond the
    proportional-renormalization baseline, in historical team-games where a
    top-N-usage teammate had 0 minutes. Shrunk toward 0.0 via
    :func:`nba.coldstart.shrinkage.shrink_rate`. Returns
    ``(boost_fraction, n_qualifying_player_game_rows)`` so callers can
    report the sample size honestly (CLAUDE.md "state sample sizes in every
    comparison"). ``rows`` / ``shot_rates`` let a caller that evaluates many
    as-of dates reuse one precomputed frame (see
    :func:`qualifying_rows_for_reference`) instead of rebuilding the
    full-history SQL for every call; results are identical.
    """
    df = rows if rows is not None else _qualifying_rows(con, as_of_date, top_n_usage, shot_rates)
    return player_boost_from_rows(df, pseudo_count)


def qualifying_rows_for_reference(
    con: duckdb.DuckDBPyConnection,
    as_of_date: dt.date,
    top_n_usage: int = 2,
    shot_rates: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Public handle on the historical roster frame (all games strictly
    before ``as_of_date``). Filtering it to ``game_date < d`` for any
    ``d <= as_of_date`` is identical to recomputing at ``d``, because every
    column is itself as-of per row."""
    return _qualifying_rows(con, as_of_date, top_n_usage, shot_rates)


def player_boost_from_rows(df: pl.DataFrame, pseudo_count: float = 20.0) -> tuple[float, int]:
    """Option A scalar from a :func:`qualifying_rows_for_reference` frame."""
    if df.height == 0:
        return 0.0, 0

    # Team-games with >= 1 absent top-usage player.
    flagged = df.filter(pl.col("is_top") & pl.col("absent")).select(["game_id", "team_id"]).unique()
    if flagged.height == 0:
        return 0.0, 0

    qualifying = df.join(flagged, on=["game_id", "team_id"], how="inner").filter(
        ~(pl.col("is_top") & pl.col("absent"))
    )
    if qualifying.height == 0:
        return 0.0, 0

    # Proportional-renormalization baseline: each present player's
    # shot_share_prior re-normalized over the OTHER present players in that
    # same team-game (excludes the absent top player(s) from the
    # denominator, exactly what the existing sim's own normalization does
    # automatically).
    denom = qualifying.group_by(["game_id", "team_id"]).agg(
        pl.col("shot_share_prior").sum().alias("_denom")
    )
    qualifying = qualifying.join(denom, on=["game_id", "team_id"], how="left")
    qualifying = qualifying.with_columns(
        pl.when(pl.col("_denom") > 0)
        .then(pl.col("shot_share_prior") / pl.col("_denom"))
        .otherwise(0.0)
        .alias("_predicted_proportional_share")
    )
    qualifying = qualifying.with_columns(
        (pl.col("actual_shot_share") - pl.col("_predicted_proportional_share")).alias("_excess")
    )

    n = qualifying.height
    # Weighted (not plain) mean excess, weighted by each present player's own
    # shot_share_prior: with exactly 2 present teammates the UNWEIGHTED
    # excess is a zero-sum pair (one gains exactly what the other loses,
    # since both shares and both baselines sum to 1), which would average to
    # identically 0 regardless of signal. Weighting by shot_share_prior
    # measures precisely the richer-get-richer question this reference
    # answers: does usage flow disproportionately to the teammate who was
    # ALREADY higher-usage, not an undirected average excess.
    weights = qualifying.get_column("shot_share_prior").to_numpy()
    excess = qualifying.get_column("_excess").to_numpy()
    weight_sum = float(weights.sum())
    r_obs = (
        float((excess * weights).sum() / weight_sum)
        if weight_sum > _WEIGHT_EPS
        else float(excess.mean())
    )
    boost = float(shrink_rate(r_obs, n, 0.0, pseudo_count))
    return boost, n


def historical_team_boost_reference(
    con: duckdb.DuckDBPyConnection,
    as_of_date: dt.date,
    top_n_usage: int = 2,
    pseudo_count: float = 20.0,
    rows: pl.DataFrame | None = None,
    shot_rates: pl.DataFrame | None = None,
) -> tuple[float, int]:
    """Option B reference scalar: the same question as
    :func:`historical_player_boost_reference`, answered at the team-game
    level via a Gini-concentration delta instead of a per-player excess --
    coarser, fewer observations per cell, per ``docs/NEXT_OPTIONS.md`` §2
    Option B's stated rationale. Returns ``(boost_fraction,
    n_absent_team_games)``. ``rows``/``shot_rates``: see Option A.
    """
    df = rows if rows is not None else _qualifying_rows(con, as_of_date, top_n_usage, shot_rates)
    return team_boost_from_rows(df, pseudo_count)


def team_boost_from_rows(df: pl.DataFrame, pseudo_count: float = 20.0) -> tuple[float, int]:
    """Option B scalar from a :func:`qualifying_rows_for_reference` frame."""
    if df.height == 0:
        return 0.0, 0

    team_games = df.group_by(["game_id", "team_id"]).agg(
        [
            pl.col("actual_shot_share").alias("_shares"),
            (pl.col("is_top") & pl.col("absent")).any().alias("_any_top_absent"),
        ]
    )
    if team_games.height == 0:
        return 0.0, 0

    ginis = [_gini(np.array(s, dtype=float)) for s in team_games.get_column("_shares").to_list()]
    team_games = team_games.with_columns(pl.Series("_gini", ginis))

    absent_games = team_games.filter(pl.col("_any_top_absent"))
    present_games = team_games.filter(~pl.col("_any_top_absent"))
    n_absent = absent_games.height
    if n_absent == 0 or present_games.height == 0:
        return 0.0, n_absent

    mean_gini_absent = float(np.mean(absent_games.get_column("_gini").to_numpy()))
    mean_gini_present = float(np.mean(present_games.get_column("_gini").to_numpy()))
    r_obs = mean_gini_absent - mean_gini_present
    boost = float(shrink_rate(r_obs, n_absent, 0.0, pseudo_count))
    return boost, n_absent


# ---------------------------------------------------------------------------
# Availability trigger: official pre-game injury report (as-of, pre-tip only)
# ---------------------------------------------------------------------------

#: ``player_availability.source`` of every official-report row (the live puller
#: and the historical backfill both write this value; the backfill's own
#: ``ingest_log`` key is a different string and never lands in this column).
OFFICIAL_REPORT_SOURCE = "nba_official_report"


@dataclass(frozen=True)
class ReportTriggerConfig:
    """Which official-report rows may trigger 2A/2B, and by when.

    ``games`` carries a DATE only (no tip-off time), so tip-off is a PROXY:
    ``game_date`` at ``tipoff_hour_et`` (naive, same clock as the report
    ``as_of``). A report row is usable for a game iff
    ``as_of <= proxy_tip - lead_minutes`` (hence strictly before tip). Rows
    stamped later are ignored, never used. Default 19:00 ET puts the cutoff
    at 18:00, which admits the 17:45 backfill anchor and excludes anything
    after; matinee tips are a documented limitation (lower ``tipoff_hour_et``
    for a conservative sensitivity run).
    """

    statuses: tuple[str, ...] = ("out",)
    tipoff_hour_et: float = 19.0
    lead_minutes: int = 60
    sources: tuple[str, ...] = (OFFICIAL_REPORT_SOURCE,)
    table: str = "player_availability"
    #: "proxy19" (default, production): tip = game_date at ``tipoff_hour_et``.
    #: "real": tip = scheduled tip-off (``tip_et`` column on the rows, attached
    #: via ``nba.features.game_tipoff.attach_real_tips``); games whose real tip
    #: is missing fall back to the proxy.
    tip_source: str = "proxy19"

    @property
    def cutoff_minutes_after_midnight(self) -> float:
        return self.tipoff_hour_et * 60.0 - float(self.lead_minutes)


def load_report_rows(
    con: duckdb.DuckDBPyConnection,
    config: ReportTriggerConfig,
    game_ids: list[str] | None = None,
) -> pl.DataFrame:
    """All official-report rows attached to a ``games`` row, with its date.

    Columns: ``game_id, player_id, status (lower-cased), as_of, game_date``.
    Rows with NULL ``game_id`` (unresolved matchup) are excluded here and
    counted by the caller's coverage diagnostics. Applies NO time filter;
    that is :func:`latest_pretip_flagged`'s job so the leakage rule lives in
    exactly one place.
    """
    placeholders = ", ".join("?" for _ in config.sources)
    sql = (
        "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
        "CAST(a.as_of AS TIMESTAMP) AS as_of, g.game_date "
        f"FROM {config.table} a JOIN games g ON g.game_id = a.game_id "
        f"WHERE a.source IN ({placeholders}) AND a.player_id IS NOT NULL"
    )
    df = _query_to_polars(con, sql, list(config.sources))
    df = df.with_columns(pl.col("as_of").cast(pl.Datetime("us")))
    if game_ids is not None:
        df = df.filter(pl.col("game_id").is_in(game_ids))
    return df


def usable_report_rows(rows: pl.DataFrame, config: ReportTriggerConfig) -> pl.DataFrame:
    """Rows with ``as_of <= tip - lead`` (the leakage rule).

    ``tip`` is the proxy (``game_date + tipoff_hour_et``) unless
    ``config.tip_source == "real"``, in which case it is the row's ``tip_et``
    (proxy where null)."""
    if config.tip_source not in ("proxy19", "real"):
        raise ValueError(f"unknown tip_source {config.tip_source!r}")
    if rows.height == 0:
        return rows
    if config.tip_source == "real":
        if "tip_et" not in rows.columns:
            raise ValueError("tip_source='real' needs a tip_et column (attach_real_tips)")
        proxy = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(
            minutes=int(config.tipoff_hour_et * 60.0)
        )
        cut = pl.coalesce(pl.col("tip_et"), proxy) - pl.duration(minutes=int(config.lead_minutes))
        return rows.filter(pl.col("as_of") <= cut)
    cutoff = pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(
        minutes=int(config.cutoff_minutes_after_midnight)
    )
    return rows.filter(pl.col("as_of") <= cutoff)


def latest_pretip_flagged(
    rows: pl.DataFrame, config: ReportTriggerConfig
) -> tuple[dict[str, set[int]], dict[str, dt.datetime]]:
    """Per game: players whose status in the LATEST usable report is flagged.

    "Latest usable report" = the greatest ``as_of`` among that game's rows that
    pass :func:`usable_report_rows`; only rows of THAT snapshot count, so a
    player flagged OUT in an earlier report but absent/upgraded in the latest
    one is not out. Games with no usable row are absent from both returned
    dicts (no report, no trigger; never imputed). The second dict maps game
    to the snapshot ``as_of`` actually used.
    """
    usable = usable_report_rows(rows, config)
    if usable.height == 0:
        return {}, {}
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_latest"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_latest"))
    flagged: dict[str, set[int]] = {gid: set() for gid in latest["game_id"].to_list()}
    wanted = {s.lower() for s in config.statuses}
    for gid, pid, status in snap.select(["game_id", "player_id", "status"]).iter_rows():
        if status in wanted:
            flagged[str(gid)].add(int(pid))
    used = {str(g): t for g, t in latest.select(["game_id", "_latest"]).iter_rows()}
    return flagged, used


def serve_pretip_flagged(
    rows: pl.DataFrame,
    tips_et: dict[str, dt.datetime],
    now_et: dt.datetime,
    config: ReportTriggerConfig,
    max_age_hours: float | None = None,
) -> tuple[dict[str, set[int]], dict[str, dt.datetime]]:
    """Serving-time twin of the training rule, built ON :func:`latest_pretip_flagged`.

    Per game: the latest snapshot among THAT game's own rows with
    ``as_of <= min(now, real tip - lead)`` (all naive US-Eastern), optionally
    ``<= max_age_hours`` older than that cutoff. ``tips_et`` maps game_id to
    its real tip (naive ET); a game without a tip is absent (never proxied).
    A game with no qualifying snapshot is absent from both dicts (has_report=0,
    never "nobody out"). Training uses the same function with ``tip_et``
    attached by ``attach_real_tips``, ``now`` unbounded and no age cap."""
    if rows.height == 0:
        return {}, {}
    cfg = replace(config, tip_source="real")
    keyed = rows.filter(
        pl.col("game_id").is_in(list(tips_et)) & (pl.col("as_of") <= pl.lit(now_et))
    ).with_columns(
        pl.col("game_id")
        .replace_strict(tips_et, return_dtype=pl.Datetime("us"), default=None)
        .alias("tip_et")
    )
    flagged, used = latest_pretip_flagged(keyed, cfg)
    if max_age_hours is not None:
        lead = dt.timedelta(minutes=int(cfg.lead_minutes))
        age = dt.timedelta(hours=max_age_hours)
        for gid in [g for g, t in used.items() if t < min(now_et, tips_et[g] - lead) - age]:
            used.pop(gid)
            flagged.pop(gid)
    return flagged, used


def prior_minutes_state(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Per (player, game played): cumulative mean minutes and games INCLUDING
    that game, plus the team, for as-of lookups (query with a date strictly
    after the game). Only games with ``minutes > 0`` count as played."""
    df = _query_to_polars(
        con,
        "SELECT s.player_id, s.team_id, g.game_date, s.minutes FROM player_game_stats s "
        "JOIN games g ON g.game_id = s.game_id WHERE COALESCE(s.minutes, 0) > 0",
    )
    df = df.sort(["player_id", "game_date"]).with_columns(
        (
            pl.col("minutes").cum_sum().over("player_id")
            / pl.col("minutes").cum_count().over("player_id")
        ).alias("avg_min_after"),
        pl.col("minutes").cum_count().over("player_id").alias("n_after"),
    )
    return df.select(["player_id", "team_id", "game_date", "avg_min_after", "n_after"])


def rotation_flagged_by_team(
    flagged: dict[str, set[int]],
    game_info: dict[str, tuple[dt.date, int, int]],
    state: pl.DataFrame,
    rotation_min_avg: float = 20.0,
    rotation_min_games: int = 5,
) -> dict[tuple[str, int], set[int]]:
    """Keep flagged players who are rotation players AS OF the game, keyed
    by ``(game_id, team_id)``.

    Rotation = mean minutes over games played STRICTLY BEFORE the game date
    >= ``rotation_min_avg`` with >= ``rotation_min_games`` such games (so a
    return-from-injury player keeps his pre-injury average). The player's
    team is his team in that last prior game; players whose team is not one
    of the game's two teams are dropped.
    """
    recs = [
        (gid, pid, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for pid in pids
    ]
    if not recs or state.height == 0:
        return {}
    left = pl.DataFrame(
        recs, schema={"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date}, orient="row"
    ).with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key"))
    right = state.rename({"game_date": "_prior_date"}).sort("_prior_date")
    joined = left.sort("_key").join_asof(
        right, left_on="_key", right_on="_prior_date", by="player_id", strategy="backward"
    )
    joined = joined.filter(
        (pl.col("avg_min_after") >= rotation_min_avg) & (pl.col("n_after") >= rotation_min_games)
    )
    out: dict[tuple[str, int], set[int]] = {}
    for gid, pid, team in joined.select(["game_id", "player_id", "team_id"]).iter_rows():
        _d, home, away = game_info[str(gid)]
        if int(team) in (home, away):
            out.setdefault((str(gid), int(team)), set()).add(int(pid))
    return out


__all__ = [
    "OFFICIAL_REPORT_SOURCE",
    "ReportTriggerConfig",
    "latest_pretip_flagged",
    "load_report_rows",
    "player_boost_from_rows",
    "prior_minutes_state",
    "qualifying_rows_for_reference",
    "rotation_flagged_by_team",
    "team_boost_from_rows",
    "usable_report_rows",
    "UsageRedistributionConfig",
    "adjust_profiles_for_absence",
    "apply_usage_redistribution",
    "effective_boost_fraction",
    "historical_player_boost_reference",
    "historical_team_boost_reference",
]
