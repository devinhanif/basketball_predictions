# ruff: noqa: E501
"""QUESTIONABLE_CASCADE features (rule: docs/prereg/QUESTIONABLE_CASCADE.md; DRAFT until frozen).

As-of report-status columns for every A0 row (a PLAYED player-game with ``n_prior >= 5``). Status = the status on the latest usable
official-report snapshot of the game, stamped ``<= tip - 60`` minutes on the REAL tip (the same snapshot rule as ``snapshot_status`` of
RETURNING_TEAMMATE_2, which carries the reason text too). A player absent from that snapshot is "unlisted" (code 0); a game with no usable
snapshot gets NaN in every column (the same NULL pattern production stores as ``has_report = 0``; never imputed as "nobody listed").

Own columns (C1, C2): ``qc_status`` (0 unlisted, 1 available, 2 probable, 3 questionable, 4 doubtful, 5 out) and ``qc_reason`` (0 unlisted,
1 injury/illness, 2 G League, 3 rest, 4 personal, 5 other). The snapshot LEAD (minutes before tip) is never a feature: history is a
mixture of cadences (about 180 minutes in 2022-24) and the live pull is 60-100, so a lead feature would be an era flag.

Cascade columns (C2 only), for the row of player ``i`` of team ``T`` in game ``g``, over ROTATION teammates ``j != i`` who are questionable or doubtful
on the snapshot (rotation = production's cut: at least ``MIN_PRIOR_PLAYED`` games and long-run minutes ``min40 >= ROTATION_MIN40``, taken from the state
after j's latest game strictly before g, exactly as ``vacated_features`` does for OUT teammates):
``qc_nq`` / ``qc_nd`` (counts), ``qc_evac_min`` = sum_j (1 - r_status(j)) * min10_j and ``qc_evac_{pts,reb,ast,fg3m}`` = sum_j (1 - r_status(j)) * v_stat_j,
where r_status is the play rate of that status measured on the SELECT season only (``play_rates``) and (min10, v_stat) are the decayed recent
averages production already uses. OUT teammates stay in production's own ``n_out_rot`` / ``vac_*`` columns; nothing here double counts them.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Any

import numpy as np
import polars as pl

from nba.props.context_residual import (
    MIN_PRIOR_PLAYED,
    PROP_STATS,
    ROTATION_MIN40,
    _sel,
    player_states,
)

STATUS_CODE = {"available": 1, "probable": 2, "questionable": 3, "doubtful": 4, "out": 5}
REASON_CODE = {"injury": 1, "gleague": 2, "rest": 3, "personal": 4, "other": 5}
CASCADE_STATUSES = ("questionable", "doubtful")
OWN_FEATURES = ("qc_status", "qc_reason")
CASCADE_FEATURES = (
    "qc_nq",
    "qc_nd",
    "qc_evac_min",
    *[f"qc_evac_{s}" for s in PROP_STATS],
)
C1_FEATURES = OWN_FEATURES
C2_FEATURES = (*OWN_FEATURES, *CASCADE_FEATURES)
SNAPSHOT_ID = "latest usable snapshot per game: as_of <= coalesce(tip_et, game_date+19h) - 60 min, source nba_official_report"
#: Rates used only if a status has no select-season rows (declared before any outcome).
FALLBACK_RATES = {"questionable": 0.5, "doubtful": 0.025}


def reason_class(reason: str | None) -> str:
    """Declared reason classes from the report's free text (case, spaces and hyphens ignored)."""
    if not reason:
        return "other"
    s = "".join(ch for ch in reason.lower() if ch.isalnum())
    if s.startswith("injury") or "illness" in s:
        return "injury"
    if s.startswith("gleague") or "twoway" in s or "onassignment" in s:
        return "gleague"
    if s.startswith("rest"):
        return "rest"
    if s.startswith("personal"):
        return "personal"
    return "other"


def snapshot_with_reason(report: pl.DataFrame, games: pl.DataFrame, lead: int = 60) -> pl.DataFrame:
    """Per (game, player): status, reason class and snapshot lead on the LATEST snapshot stamped ``<= tip - lead``.

    ``report``: game_id, player_id, status, reason, as_of; ``games``: game_id, tip. Rows of later snapshots are ignored, never used. A player
    absent from the latest usable snapshot has no row. Games without a usable snapshot have no rows."""
    r = report.join(games.select("game_id", "tip"), on="game_id", how="inner")
    r = r.filter(pl.col("as_of") <= pl.col("tip") - pl.duration(minutes=lead))
    latest = r.group_by("game_id").agg(pl.col("as_of").max().alias("snap_as_of"))
    snap = r.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("snap_as_of"))
    snap = snap.with_columns(
        pl.col("status").str.to_lowercase(),
        pl.col("reason").map_elements(reason_class, return_dtype=pl.Utf8).alias("reason_cls"),
        ((pl.col("tip") - pl.col("snap_as_of")).dt.total_seconds() / 60.0).alias("lead_min"),
    )
    return snap.select(
        "game_id", "player_id", "status", "reason_cls", "snap_as_of", "lead_min"
    ).unique(["game_id", "player_id"], keep="last")


def snapshot_source_sha() -> str:
    """sha256 of the snapshot rule: its description plus ``snapshot_with_reason``'s source with all whitespace collapsed (pinned by the harness)."""
    src = " ".join(inspect.getsource(snapshot_with_reason).split())
    return hashlib.sha256((SNAPSHOT_ID + src).encode()).hexdigest()


def played_states(
    games: pl.DataFrame, pgs: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """``played`` (as ``build_features`` builds it) and the sequential player states."""
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    played = (
        pgs.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    return played, player_states(played)


def tagged_table(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    snap: pl.DataFrame,
    games: pl.DataFrame,
    statuses: tuple[str, ...] | None = None,
) -> pl.DataFrame:
    """ROTATION listed players per (game, team): as-of min10 / v_stat from the state after their latest game strictly before the game date.

    The same join as ``nba.props.context_residual.vacated_features`` (asof by player, one day back, ``n_after >= MIN_PRIOR_PLAYED`` and ``min40 >=
    ROTATION_MIN40``, the player's team must be one of the game's teams) but it keeps the player rows, so a row's own contribution can be removed.
    ``statuses`` None = every listed status. Columns: game_id, team_id, player_id, status, reason_cls, min10, v_pts..v_fg3m."""
    post = states["post"]
    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"],
            "n_after": states["n_prior"] + 1,
            "min40": _sel(post, 40.0, "min"),
            "min10": _sel(post, 10.0, "min"),
            **{f"v_{s}": _sel(post, 10.0, s) for s in PROP_STATS},
        }
    ).sort("game_date")
    s = snap if statuses is None else snap.filter(pl.col("status").is_in(list(statuses)))
    g = games.with_columns(pl.col("game_date").cast(pl.Date)).select(
        "game_id", "game_date", "home_team", "away_team"
    )
    left = (
        s.join(g, on="game_id")
        .select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            "status",
            "reason_cls",
            pl.col("game_date"),
            pl.col("home_team").cast(pl.Int64).alias("home"),
            pl.col("away_team").cast(pl.Int64).alias("away"),
        )
        .with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key"))
        .sort("_key")
    )
    j = left.join_asof(
        st.rename({"game_date": "_pd"}),
        left_on="_key",
        right_on="_pd",
        by="player_id",
        strategy="backward",
    ).filter((pl.col("n_after") >= MIN_PRIOR_PLAYED) & (pl.col("min40") >= ROTATION_MIN40))
    j = j.filter((pl.col("team_id") == pl.col("home")) | (pl.col("team_id") == pl.col("away")))
    return j.select(
        "game_id",
        "team_id",
        "player_id",
        "status",
        "reason_cls",
        "min10",
        *[f"v_{s}" for s in PROP_STATS],
    )


def play_rates(
    tagged: pl.DataFrame, box: pl.DataFrame, game_season: dict[str, int], season: int
) -> dict[str, dict[str, float]]:
    """Share of rotation tagged players with a box row (minutes > 0) tonight, by status, on ONE season (the select season). Returns {status: {rate, n}}.

    Uses the realised play flag (the returner's-own-play-flag precedent of RETURNING_TEAMMATE_2's gate); never an outcome of any prop stat."""
    in_season = tagged.filter(
        pl.col("game_id").map_elements(
            lambda g: game_season.get(g) == season, return_dtype=pl.Boolean
        )
    )
    played = box.filter(pl.col("minutes") > 0).select("game_id", "player_id").unique()
    played = played.with_columns(pl.col("player_id").cast(pl.Int64), pl.lit(1).alias("_p"))
    j = in_season.join(played, on=["game_id", "player_id"], how="left").with_columns(
        pl.col("_p").fill_null(0)
    )
    out: dict[str, dict[str, float]] = {}
    for status, rate, n in (
        j.group_by("status").agg(pl.col("_p").mean(), pl.len()).sort("status").rows()
    ):
        out[str(status)] = {"rate": float(rate), "n": float(n)}
    return out


def rates_for_cascade(rates: dict[str, dict[str, float]]) -> dict[str, float]:
    """r_status for the cascade statuses; the declared fallback when a status has no select-season rows."""
    return {
        s: float(rates[s]["rate"]) if s in rates and rates[s]["n"] > 0 else FALLBACK_RATES[s]
        for s in CASCADE_STATUSES
    }


def cascade_table(tagged: pl.DataFrame, r: dict[str, float]) -> pl.DataFrame:
    """Per (game_id, team_id, player_id) of a Q/D rotation player: his contribution w * (min10, v_stat), w = 1 - r_status (the expected vacated share)."""
    t = tagged.filter(pl.col("status").is_in(list(CASCADE_STATUSES)))
    w = pl.col("status").replace_strict({s: 1.0 - r[s] for s in CASCADE_STATUSES}, default=0.0)
    return t.with_columns(
        w.alias("_w"),
        (pl.col("status") == "questionable").cast(pl.Float64).alias("_q"),
        (pl.col("status") == "doubtful").cast(pl.Float64).alias("_d"),
    ).with_columns(
        (pl.col("_w") * pl.col("min10")).alias("_e_min"),
        *[(pl.col("_w") * pl.col(f"v_{s}")).alias(f"_e_{s}") for s in PROP_STATS],
    )


def build_columns(
    rows: pl.DataFrame,
    snap: pl.DataFrame,
    tagged: pl.DataFrame,
    r: dict[str, float],
    reported_games: set[str],
) -> pl.DataFrame:
    """The C2 columns for ``rows`` (game_id, team_id, player_id): own status/reason and the teammate cascade with self removed.

    Games outside ``reported_games`` get NaN in every column; reported games with no tagged teammate get explicit zeros."""
    ct = cascade_table(tagged, r)
    agg_cols = [
        pl.col("_q").sum().alias("_sq"),
        pl.col("_d").sum().alias("_sd"),
        pl.col("_e_min").sum().alias("_smin"),
        *[pl.col(f"_e_{s}").sum().alias(f"_s{s}") for s in PROP_STATS],
    ]
    team = ct.group_by(["game_id", "team_id"]).agg(agg_cols)
    own = ct.select(
        "game_id",
        "player_id",
        pl.col("_q").alias("_oq"),
        pl.col("_d").alias("_od"),
        pl.col("_e_min").alias("_omin"),
        *[pl.col(f"_e_{s}").alias(f"_o{s}") for s in PROP_STATS],
    )
    x = (
        rows.select("game_id", "team_id", pl.col("player_id").cast(pl.Int64))
        .join(team, on=["game_id", "team_id"], how="left")
        .join(own, on=["game_id", "player_id"], how="left")
        .join(
            snap.select(
                "game_id",
                pl.col("player_id").cast(pl.Int64),
                pl.col("status").replace_strict(STATUS_CODE, default=0).alias("_sc"),
                pl.col("reason_cls").replace_strict(REASON_CODE, default=5).alias("_rc"),
            ),
            on=["game_id", "player_id"],
            how="left",
        )
    )

    def z(c: str) -> pl.Expr:
        return pl.col(c).fill_null(0.0)

    out = x.with_columns(
        (z("_sq") - z("_oq")).alias("qc_nq"),
        (z("_sd") - z("_od")).alias("qc_nd"),
        (z("_smin") - z("_omin")).alias("qc_evac_min"),
        *[(z(f"_s{s}") - z(f"_o{s}")).alias(f"qc_evac_{s}") for s in PROP_STATS],
        pl.col("_sc").fill_null(0).cast(pl.Float64).alias("qc_status"),
        pl.col("_rc").fill_null(0).cast(pl.Float64).alias("qc_reason"),
    ).select("game_id", "player_id", *C2_FEATURES)
    nan = pl.col("game_id").is_in(sorted(reported_games)).not_()
    return out.with_columns(
        [pl.when(nan).then(None).otherwise(pl.col(c)).alias(c) for c in C2_FEATURES]
    )


def summarize_counts(tagged: pl.DataFrame, games: pl.DataFrame) -> dict[str, Any]:
    """Rotation Q/D player-games and affected team-games per season."""
    season = dict(games.select("game_id", "season").rows())
    t = tagged.filter(pl.col("status").is_in(list(CASCADE_STATUSES))).with_columns(
        pl.col("game_id").replace_strict(season, default=None).alias("season")
    )
    out: dict[str, Any] = {}
    for s in sorted(t["season"].drop_nulls().unique().to_list()):
        x = t.filter(pl.col("season") == s)
        out[str(s)] = {
            "rotation_q": int(x.filter(pl.col("status") == "questionable").height),
            "rotation_d": int(x.filter(pl.col("status") == "doubtful").height),
            "affected_team_games": int(x.select("game_id", "team_id").unique().height),
        }
    return out
