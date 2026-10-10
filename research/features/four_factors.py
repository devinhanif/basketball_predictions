"""FOUR_FACTORS features (docs/prereg/FOUR_FACTORS.md, frozen sha256 0d87c2ce).

Nine as-of team-level columns: expected game pace and the offence / allowed-defence profile on
Dean Oliver's four factors (eFG%, TOV%, ORB%, FT rate). Pure function of three frames
(``games``, team box rows, ``possessions``); research-only (``nba/`` is untouched).

Implementation readings of unstated details, fixed before any arm was scored:

* Every game in ``games`` (regular season and playoffs, seasons <= 2024) contributes a team-game.
* ``minutes_played = 48 + 5 * OT``; ``games`` stores no periods, so OT = max(period) of the game's
  possessions minus 4 (0 when fewer).
* Rolling window: the team's prior 20 team-games by ``(game_date, game_id)``, ``shift(1)``, windows
  cross seasons; ``n`` = games in the window (<= 20). The team mean is used when n >= 5, else the
  team is shrunk wholly to the league mean (n = 0). Shrinkage ``(n*m + 10*L) / (n + 10)``.
* League as-of mean ``L`` = mean over team-games with ``game_date`` STRICTLY EARLIER than the
  game's date (same-day games excluded: their tip order is not known), over all data from the
  start of the load. Before any prior date ``L`` is undefined and every ``ff_*`` is 0.0 (centred).
* Fallback for team-games whose offensive possession count is outside [80, 130]: ``pace48`` and
  ``tov_pct`` (the two poss-dependent quantities) of that team-game, and the allowed copy the
  opponent inherits, are replaced by ``L`` at that date and the team-game is excluded from L.
* ``orb_pct = oreb / (oreb + opp_drb)``. The allowed quantity of team T in game g is the
  opponent's offensive quantity in g. ``ff_pace`` = mean of the two teams' shrunk ``pace48``
  minus L.
* NULL box inputs make the raw quantity null; null raws are skipped by the rolling mean and by L.
"""

from __future__ import annotations

import numpy as np
import polars as pl

WINDOW = 20
MIN_PRIOR = 5
PSEUDO = 10.0
POSS_LO, POSS_HI = 80, 130

BASE = ("pace", "efg", "tov", "orb", "ftr")
FF_COLS: tuple[str, ...] = (
    "ff_pace",
    "ff_off_efg",
    "ff_off_tov",
    "ff_off_orb",
    "ff_off_ftr",
    "ff_def_efg",
    "ff_def_tov",
    "ff_def_orb",
    "ff_def_ftr",
)
FF_NO_PACE: tuple[str, ...] = tuple(c for c in FF_COLS if c != "ff_pace")
FF_PACE: tuple[str, ...] = ("ff_pace",)
# raw series per team-game: offence (pace + four factors) and allowed (the opponent's offence)
OFF = [f"o_{b}" for b in BASE]
ALW = [f"a_{b}" for b in BASE[1:]]


def team_game_raw(games: pl.DataFrame, pgs: pl.DataFrame, poss: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, team): poss, minutes_played, pts reconciliation and the raw series."""
    box = (
        pgs.group_by(["game_id", "team_id"])
        .agg(
            *[
                pl.col(c).sum().alias(f"b_{c}")
                for c in ("fga", "fgm", "fg3m", "fta", "tov", "oreb", "dreb", "pts")
            ]
        )
        .with_columns(pl.col("team_id").cast(pl.Int64))
    )
    pc = (
        poss.group_by(["game_id", "off_team"])
        .agg(pl.len().alias("poss"), pl.col("pts").fill_null(0).sum().alias("poss_pts"))
        .rename({"off_team": "team_id"})
        .with_columns(pl.col("team_id").cast(pl.Int64))
    )
    ot = poss.group_by("game_id").agg(
        (pl.col("period").max() - 4).clip(lower_bound=0).alias("n_ot")
    )
    g = games.select("game_id", "game_date", "season", "home_team", "away_team")
    tg = pl.concat(
        [
            g.select(
                "game_id",
                "game_date",
                "season",
                pl.col("home_team").cast(pl.Int64).alias("team_id"),
                pl.col("away_team").cast(pl.Int64).alias("opp_id"),
            ),
            g.select(
                "game_id",
                "game_date",
                "season",
                pl.col("away_team").cast(pl.Int64).alias("team_id"),
                pl.col("home_team").cast(pl.Int64).alias("opp_id"),
            ),
        ]
    )
    tg = (
        tg.join(box, on=["game_id", "team_id"], how="left")
        .join(pc, on=["game_id", "team_id"], how="left")
        .join(ot, on="game_id", how="left")
    )
    opp = box.select(
        "game_id", pl.col("team_id").alias("opp_id"), pl.col("b_dreb").alias("opp_drb")
    )
    tg = tg.join(opp, on=["game_id", "opp_id"], how="left")
    tg = tg.with_columns(
        pl.col("poss").fill_null(0),
        (48.0 + 5.0 * pl.col("n_ot").fill_null(0)).alias("minutes_played"),
    ).with_columns(
        (pl.col("poss") * 48.0 / pl.col("minutes_played")).alias("o_pace"),
        ((pl.col("b_fgm") + 0.5 * pl.col("b_fg3m")) / pl.col("b_fga")).alias("o_efg"),
        (pl.col("b_tov") / pl.col("poss")).alias("o_tov"),
        (pl.col("b_oreb") / (pl.col("b_oreb") + pl.col("opp_drb"))).alias("o_orb"),
        (pl.col("b_fta") / pl.col("b_fga")).alias("o_ftr"),
        ((pl.col("poss") < POSS_LO) | (pl.col("poss") > POSS_HI)).alias("bad_poss"),
    )
    # allowed = the opponent's offence in the same game
    mirror = tg.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        *[pl.col(f"o_{b}").alias(f"a_{b}") for b in BASE[1:]],
        pl.col("bad_poss").alias("opp_bad"),
    )
    tg = tg.join(mirror, on=["game_id", "opp_id"], how="left")
    return tg.with_columns(pl.col("opp_bad").fill_null(False)).sort(
        ["team_id", "game_date", "game_id"]
    )


def _league_asof(tg: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    """Per game_date: mean of each column over non-bad team-games on STRICTLY earlier dates."""
    ok = tg.filter(~pl.col("bad_poss"))
    daily = ok.group_by("game_date").agg(
        *[pl.col(c).filter(pl.col(c).is_not_null()).sum().alias(f"s_{c}") for c in cols],
        *[pl.col(c).is_not_null().sum().alias(f"n_{c}") for c in cols],
    )
    # every date of the data gets a row (dates whose games are all out of range contribute 0)
    dates = tg.select("game_date").unique().sort("game_date")
    daily = dates.join(daily, on="game_date", how="left").with_columns(
        *[pl.col(f"s_{c}").fill_null(0.0) for c in cols],
        *[pl.col(f"n_{c}").fill_null(0) for c in cols],
    )
    daily = daily.with_columns(
        *[pl.col(f"s_{c}").cum_sum().shift(1).alias(f"cs_{c}") for c in cols],
        *[pl.col(f"n_{c}").cum_sum().shift(1).alias(f"cn_{c}") for c in cols],
    ).with_columns(
        *[
            pl.when(pl.col(f"cn_{c}") > 0)
            .then(pl.col(f"cs_{c}") / pl.col(f"cn_{c}"))
            .otherwise(None)
            .alias(f"L_{c}")
            for c in cols
        ]
    )
    return daily.select("game_date", *[f"L_{c}" for c in cols])


def build_four_factors(
    games: pl.DataFrame, pgs: pl.DataFrame, poss: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(ff, tg): ``ff`` has game_id, team_id and the nine ``ff_*`` columns for every team-game;
    ``tg`` is the raw team-game frame (for the gate)."""
    tg = team_game_raw(games, pgs, poss)
    cols = [*OFF, *ALW]
    lg = _league_asof(tg, cols)
    tg = tg.join(lg, on="game_date", how="left")
    # declared fallback: poss-dependent quantities of out-of-range team-games take L
    tg = tg.with_columns(
        pl.when(pl.col("bad_poss"))
        .then(pl.col("L_o_pace"))
        .otherwise(pl.col("o_pace"))
        .alias("o_pace"),
        pl.when(pl.col("bad_poss"))
        .then(pl.col("L_o_tov"))
        .otherwise(pl.col("o_tov"))
        .alias("o_tov"),
        pl.when(pl.col("opp_bad"))
        .then(pl.col("L_a_tov"))
        .otherwise(pl.col("a_tov"))
        .alias("a_tov"),
    )
    tg = tg.sort(["team_id", "game_date", "game_id"])
    roll: list[pl.Expr] = []
    for c in cols:
        roll.append(
            pl.col(c).shift(1).rolling_mean(WINDOW, min_samples=1).over("team_id").alias(f"m_{c}")
        )
    tg = tg.with_columns(
        pl.col("game_id").cum_count().over("team_id").alias("_k"),
        *roll,
    )
    # window length n = min(20, prior games); null raws are not counted by rolling_mean
    tg = tg.with_columns((pl.col("_k") - 1).clip(upper_bound=WINDOW).cast(pl.Float64).alias("_n"))
    shr: list[pl.Expr] = []
    for c in cols:
        use = pl.col("_n") >= MIN_PRIOR
        shr.append(
            pl.when(use & pl.col(f"m_{c}").is_not_null())
            .then(
                (pl.col("_n") * pl.col(f"m_{c}") + PSEUDO * pl.col(f"L_{c}"))
                / (pl.col("_n") + PSEUDO)
            )
            .otherwise(pl.col(f"L_{c}"))
            .alias(f"s_{c}")
        )
    tg = tg.with_columns(shr)
    cen = tg.with_columns(*[(pl.col(f"s_{c}") - pl.col(f"L_{c}")).alias(f"c_{c}") for c in cols])
    own = cen.select(
        "game_id",
        "team_id",
        "opp_id",
        pl.col("c_o_pace").alias("pace_own"),
        *[pl.col(f"c_o_{b}").alias(f"ff_off_{b}") for b in BASE[1:]],
        *[pl.col(f"c_a_{b}").alias(f"alw_{b}") for b in BASE[1:]],
    )
    opp = own.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        pl.col("pace_own").alias("pace_opp"),
        *[pl.col(f"alw_{b}").alias(f"ff_def_{b}") for b in BASE[1:]],
    )
    ff = own.join(opp, on=["game_id", "opp_id"], how="left").with_columns(
        ((pl.col("pace_own") + pl.col("pace_opp")) / 2.0).alias("ff_pace")
    )
    ff = ff.select("game_id", "team_id", *FF_COLS).with_columns(
        *[pl.col(c).fill_null(0.0).fill_nan(0.0) for c in FF_COLS]
    )
    return ff, tg.select(
        "game_id",
        "team_id",
        "opp_id",
        "game_date",
        "season",
        "poss",
        "poss_pts",
        "b_pts",
        "bad_poss",
        "n_ot",
        "minutes_played",
        *[f"b_{c}" for c in ("fga", "fgm", "fg3m", "fta", "tov", "oreb")],
    )


def placebo_ff(feats: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """A3: the nine columns permuted JOINTLY across rows within team-season."""
    d = feats.with_row_index("_ord")
    groups = d.group_by(["team_id", "season"], maintain_order=True).agg(pl.col("_ord"))
    rng = np.random.default_rng(seed)
    src = np.arange(d.height)
    for ords in groups["_ord"].to_list():
        o = np.asarray(ords)
        src[o] = o[rng.permutation(len(o))]
    return d.with_columns([pl.Series(c, d[c].to_numpy()[src]) for c in FF_COLS]).drop("_ord")
