# ruff: noqa: E501
"""ADVANCED_RATES assembly (docs/prereg/ADVANCED_RATES.md, DRAFT): the seven families' columns.

``build_advanced(inputs, rows)`` is a pure function of eight frames (see :data:`INPUT_KEYS`) and the
target rows ``(game_id, player_id, min10)``: no database access, so the planted-future and
same-game tests can edit the inputs and compare. ``load_inputs_db`` reads the frames from
``nba.duckdb`` (read-only, ``season <= 2024`` in SQL) and ``data/pbp``.

Team-level series (own team and tonight's opponent) use the same time-series builder with the
team as the entity and a single league group; their prior is the league as-of ratio.
League references (``lg_*``) use games dated STRICTLY before the row's date.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from research.features import advanced_oncourt as ao
from research.features import advanced_rates as ar
from research.features import advanced_shots as sh

MAX_SEASON = 2024
INPUT_KEYS: tuple[str, ...] = (
    "games", "box", "poss_tg", "oc", "ptype", "pshots", "tshots", "tfast", "track", "hustle",
)  # fmt: skip
BK = sh.BUCKETS
SUB = sh.SHOT_PROFILE_SUBTYPES

# player stats beyond ar.STAT_DEFS (SHOT_PROFILE); ppws is dropped by the pre-declared redundancy rule
_ATT = pl.sum_horizontal([pl.col(f"att_{b}") for b in BK])
_EXP = pl.sum_horizontal([pl.col(f"att_{b}") * pl.col(f"lg_fg_{b}") for b in BK])
SHOT_DEFS: dict[str, tuple[pl.Expr, pl.Expr]] = {
    **{f"sh_{b}": (pl.col(f"att_{b}"), _ATT) for b in BK},
    **{f"fg_{b}": (pl.col(f"made_{b}"), pl.col(f"att_{b}")) for b in BK},
    **{f"sub_{s}": (pl.col(f"att_{s}"), _ATT) for s in SUB},
    "sh_three": (pl.col("att_corner3") + pl.col("att_atb3"), _ATT),
    "exp_fg": (_EXP, _ATT),
    "shot_making": (pl.sum_horizontal([pl.col(f"made_{b}") for b in BK]) - _EXP, _ATT),
}
EXCLUDED_STATS = ("ppws",)

TEAM_STATS: tuple[str, ...] = (
    "pace48", "orb", "drb", "alw_orb", "alw_drb", "alw_fast",
    *[f"alw_sh_{b}" for b in BK], *[f"alw_fg_{b}" for b in BK],
    "def_rim_fg", "alw_contest", "hus_contest",
)  # fmt: skip


def _cols(stats: list[str] | tuple[str, ...], suffixes: tuple[str, ...]) -> list[str]:
    return [f"{s}_{x}" for s in stats for x in suffixes]


R6 = ar.REPR_SUFFIXES
FAMILY_COLUMNS: dict[str, list[str]] = {
    "USAGE_PACE": [
        *_cols(["usg", "pts100", "reb100", "ast100", "fg3m100", "pace48"], R6), "adv_stint_n",
        "team_pace", "opp_pace", "proj_poss", "exp_plays", "exp_pts", "exp_reb", "exp_ast", "exp_fg3m",
    ],
    "EFFICIENCY": [*_cols(["gmsc", "ts", "par3", "ftr"], R6), "adv_stint_n"],
    "PLAYMAKING": [*_cols(["ppr", "ast_ratio", "ast_pct", "tov_pct"], R6), "adv_stint_n"],
    "REBOUNDING": [
        *_cols(["orb", "drb"], R6), "adv_stint_n", "team_orb", "team_drb", "opp_alw_orb", "opp_alw_drb",
    ],
    "SHOT_PROFILE": [
        *[f"sh_{b}_{x}" for b in BK for x in ("ew20", "ew100", "slope20")],
        *[f"fg_{b}_ew100" for b in BK],
        *[f"sub_{s}_ew20" for s in SUB],
        "exp_fg_ew20", "shot_making_ew100", "sh_rim_stint20", "sh_three_stint20", "adv_stint_n",
    ],
    "DEFENSE_CONTEXT": [
        *[f"opp_alw_sh_{b}" for b in BK], *[f"opp_alw_fg_{b}" for b in BK],
        "opp_def_rim_fg", "opp_alw_contest", "opp_hus_contest",
        "exp_fg_vs_opp", "rim_x_opp", "three_x_opp",
    ],
    "PLAY_TYPE": [
        *_cols(["tr_share", "tr_ppp", "ast_share", "putback_share"], R6), "adv_stint_n",
        "opp_alw_fast", "tr_share_x_opp", "tr_ppp_x_opp",
    ],
}  # fmt: skip
#: cell = (family, stat); m = 15
CELLS: tuple[tuple[str, str], ...] = tuple(
    (f, s)
    for f, ss in (
        ("USAGE_PACE", ("pts", "reb", "ast", "fg3m")),
        ("EFFICIENCY", ("pts", "fg3m")),
        ("PLAYMAKING", ("ast",)),
        ("REBOUNDING", ("reb",)),
        ("SHOT_PROFILE", ("pts", "fg3m")),
        ("DEFENSE_CONTEXT", ("pts", "fg3m")),
        ("PLAY_TYPE", ("pts", "reb", "fg3m")),
    )
    for s in ss
)


# --------------------------------------------------------------------------- DB loading
def load_inputs_db(
    db_path: str = "nba.duckdb", pbp_glob: str = "data/pbp/*.parquet"
) -> dict[str, pl.DataFrame]:
    """Read every input frame; ``season <= 2024`` is in the SQL and asserted."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute(
            f"CREATE TEMP VIEW pbp AS SELECT p.* FROM read_parquet('{pbp_glob}') p JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        )
        games = con.execute(
            f"SELECT game_id, game_date, season, home_team, away_team FROM games WHERE season <= {MAX_SEASON}"
        ).pl()
        box = con.execute(
            "SELECT s.*, COALESCE(LEFT(ps.position, 1), 'U') AS pos FROM player_game_stats s "
            f"JOIN games g USING (game_id) LEFT JOIN players_static ps USING (player_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        poss_tg = con.execute(
            "SELECT p.game_id, p.off_team AS team_id, COUNT(*)::INTEGER AS poss, MAX(p.period) OVER (PARTITION BY p.game_id) AS max_period "
            f"FROM possessions p JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON} GROUP BY p.game_id, p.off_team, p.period"
        ).pl()
        poss_tg = poss_tg.group_by(["game_id", "team_id"]).agg(
            pl.col("poss").sum(), pl.col("max_period").max()
        )
        oc = ao.build_oncourt(con)
        ptype = sh.play_type_counts(con)
        pshots = sh.player_shot_counts(con)
        tshots = sh.team_allowed_counts(con)
        tfast = sh.team_allowed_fast(con).join(games.select("game_id"), on="game_id")
        track = con.execute(
            "SELECT t.game_id, t.player_id, t.team_id, t.contested_field_goals_attempted AS contested_fga, "
            "t.uncontested_field_goals_attempted AS uncontested_fga, t.defended_at_rim_field_goals_made AS rim_fgm, "
            f"t.defended_at_rim_field_goals_attempted AS rim_fga FROM player_game_tracking t JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        hustle = con.execute(
            f"SELECT h.game_id, h.player_id, h.contested_shots FROM player_game_hustle h JOIN games g USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()
    inputs = {"games": games, "box": box, "poss_tg": poss_tg, "oc": oc, "ptype": ptype, "pshots": pshots,
              "tshots": tshots, "tfast": tfast, "track": track, "hustle": hustle}  # fmt: skip
    assert int(games["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    return inputs


# --------------------------------------------------------------------------- league references
def league_asof(
    daily: pl.DataFrame, pairs: dict[str, tuple[str, str]], dates: pl.Series
) -> pl.DataFrame:
    """For each date in ``dates``: cumulative ``sum(num)/sum(den)`` over ``daily`` rows dated
    STRICTLY earlier (``daily``: ``game_date`` plus the num / den columns, one or many rows per date)."""
    cols = sorted({c for p in pairs.values() for c in p})
    d = (
        daily.group_by("game_date")
        .agg([pl.col(c).cast(pl.Float64).fill_null(0.0).sum() for c in cols])
        .sort("game_date")
        .with_columns([pl.col(c).cum_sum() for c in cols])
    )
    left = pl.DataFrame({"game_date": dates.unique().sort()}).with_columns(
        (pl.col("game_date") - dt.timedelta(days=1)).alias("_d")
    )
    j = left.join_asof(
        d.rename({"game_date": "_gd"}), left_on="_d", right_on="_gd", strategy="backward"
    )
    return j.select(
        ["game_date", *[(pl.col(num) / pl.col(den)).alias(k) for k, (num, den) in pairs.items()]]
    )


# --------------------------------------------------------------------------- team table
def team_games(inp: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """One row per (game, team): own and opponent box sums, possessions, allowed shot / fast / tracking / hustle sums."""
    g = inp["games"].select("game_id", "game_date", "season", "home_team", "away_team")
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
    box = inp["box"].with_columns(pl.col("team_id").cast(pl.Int64))
    tb = box.group_by(["game_id", "team_id"]).agg(
        pl.col("oreb").sum().alias("oreb"), pl.col("dreb").sum().alias("dreb"), pl.col("fga").sum().alias("fga"),
        pl.col("minutes").fill_null(0).sum().alias("mp"),
    )  # fmt: skip
    pt = inp["poss_tg"].with_columns(pl.col("team_id").cast(pl.Int64))
    tg = tg.join(tb, on=["game_id", "team_id"], how="left").join(
        pt, on=["game_id", "team_id"], how="left"
    )
    opp = tb.select(
        "game_id",
        pl.col("team_id").alias("opp_id"),
        pl.col("oreb").alias("opp_oreb"),
        pl.col("dreb").alias("opp_dreb"),
        pl.col("fga").alias("opp_fga"),
    )
    tg = tg.join(opp, on=["game_id", "opp_id"], how="left")
    tg = tg.with_columns(
        (48.0 + 5.0 * (pl.col("max_period").fill_null(4) - 4).clip(lower_bound=0)).alias(
            "minutes_played"
        )
    )
    ts = (
        inp["tshots"]
        .with_columns(pl.col("def_team_id").cast(pl.Int64).alias("team_id"))
        .drop("def_team_id")
    )
    ts = ts.rename({c: f"alw_{c}" for c in ts.columns if c.startswith(("att_", "made_"))})
    tf = (
        inp["tfast"]
        .with_columns(pl.col("def_team_id").cast(pl.Int64).alias("team_id"))
        .drop("def_team_id")
    )
    tg = tg.join(ts, on=["game_id", "team_id"], how="left").join(
        tf, on=["game_id", "team_id"], how="left"
    )
    # tracking: the team's defenders at the rim; the opponent's contested share
    tr = inp["track"].with_columns(pl.col("team_id").cast(pl.Int64))
    trt = tr.group_by(["game_id", "team_id"]).agg(
        pl.col("rim_fgm").sum().alias("rim_fgm"), pl.col("rim_fga").sum().alias("rim_fga"),
        pl.col("contested_fga").sum().alias("c_fga"), pl.col("uncontested_fga").sum().alias("u_fga"),
    )  # fmt: skip
    tg = tg.join(
        trt.select("game_id", "team_id", "rim_fgm", "rim_fga"),
        on=["game_id", "team_id"],
        how="left",
    )
    tg = tg.join(
        trt.select("game_id", pl.col("team_id").alias("opp_id"), pl.col("c_fga").alias("opp_c_fga"), pl.col("u_fga").alias("opp_u_fga")),
        on=["game_id", "opp_id"], how="left",
    )  # fmt: skip
    # hustle: presence flag through join_side_table; coverage = minutes of present rows / team minutes
    from nba.data.side_tables import join_side_table

    hb = join_side_table(
        box.select("game_id", "player_id", "team_id", pl.col("minutes").fill_null(0.0)),
        inp["hustle"].select("game_id", "player_id", "contested_shots"),
        on=["game_id", "player_id"], flag="hus_present",
    )  # fmt: skip
    ht = hb.group_by(["game_id", "team_id"]).agg(
        pl.col("contested_shots").sum().alias("hus_contested"),
        (pl.col("minutes") * pl.col("hus_present").cast(pl.Float64)).sum().alias("hus_min"),
        pl.col("minutes").sum().alias("tot_min"),
    )  # fmt: skip
    tg = tg.join(ht, on=["game_id", "team_id"], how="left")
    return tg.sort(["team_id", "game_date", "game_id"])


def team_stat_inputs(tg: pl.DataFrame) -> pl.DataFrame:
    """num / den columns of :data:`TEAM_STATS` on the team-game table (null where undefined)."""
    n = {}
    n["pace48"] = (pl.col("poss"), pl.col("minutes_played") / 48.0)
    n["orb"] = (pl.col("oreb"), pl.col("oreb") + pl.col("opp_dreb"))
    n["drb"] = (pl.col("dreb"), pl.col("dreb") + pl.col("opp_oreb"))
    n["alw_orb"] = (pl.col("opp_oreb"), pl.col("opp_oreb") + pl.col("dreb"))
    n["alw_drb"] = (pl.col("opp_dreb"), pl.col("opp_dreb") + pl.col("oreb"))
    n["alw_fast"] = (pl.col("faced_fast"), pl.col("faced"))
    tot = pl.sum_horizontal([pl.col(f"alw_att_{b}") for b in BK])
    for b in BK:
        n[f"alw_sh_{b}"] = (pl.col(f"alw_att_{b}"), tot)
        n[f"alw_fg_{b}"] = (pl.col(f"alw_made_{b}"), pl.col(f"alw_att_{b}"))
    n["def_rim_fg"] = (pl.col("rim_fgm"), pl.col("rim_fga"))
    n["alw_contest"] = (pl.col("opp_c_fga"), pl.col("opp_c_fga") + pl.col("opp_u_fga"))
    cov = pl.col("hus_min") / pl.col("tot_min")
    n["hus_contest"] = (pl.col("hus_contested"), pl.col("opp_fga") * cov)
    out = tg.select(
        "game_id", "game_date", "team_id", "opp_id",
        *[e.alias(f"{k}__num") for k, (e, _) in n.items()],
        *[d.alias(f"{k}__den") for k, (_, d) in n.items()],
    )  # fmt: skip
    return out.with_columns(
        [
            pl.when(pl.col(f"{k}__den") > 0)
            .then(pl.col(f"{k}__den"))
            .otherwise(None)
            .alias(f"{k}__den")
            for k in n
        ]
    )


TEAM_INPUT = ar.SeriesInput(entity="tid", team="tid_team", pos="tpos", played="tplayed")


def team_series(tg: pl.DataFrame) -> pl.DataFrame:
    """``ew20`` per team stat as of each team-game (strictly earlier games) keyed ``(game_id, team_id)``."""
    ti = team_stat_inputs(tg).with_columns(
        pl.col("team_id").alias("tid"),
        pl.col("team_id").alias("tid_team"),
        pl.lit("T").alias("tpos"),
        pl.lit(True).alias("tplayed"),
    )
    out = ar.asof_series(ti, list(TEAM_STATS), TEAM_INPUT)
    keep = ["tid", "game_id", *[f"{s}_ew20" for s in TEAM_STATS]]
    return out.select(keep).rename({"tid": "team_id", **{f"{s}_ew20": s for s in TEAM_STATS}})


def pace_factor_table(tg: pl.DataFrame) -> pl.DataFrame:
    """Per team-game: league possessions per 48 over games dated strictly earlier / the team's own (1.0 before any history)."""
    d = tg.with_columns(
        (pl.col("poss") * 48.0 / pl.col("minutes_played")).alias("p48"), pl.lit(1.0).alias("one")
    )
    lg = league_asof(
        d.select("game_date", "p48", "one"), {"lg_p48": ("p48", "one")}, d["game_date"]
    )
    out = d.join(lg, on="game_date", how="left")
    return out.select(
        "game_id", "team_id", pl.col("lg_p48").alias("lg_pace"),
        pl.when(pl.col("p48") > 0).then(pl.col("lg_p48").fill_null(pl.col("p48")) / pl.col("p48")).otherwise(None).alias("pace_factor"),
    )  # fmt: skip


# --------------------------------------------------------------------------- assembly
def player_rows(inp: dict[str, pl.DataFrame], tg: pl.DataFrame) -> pl.DataFrame:
    """One row per box row with every num / den (box + on-court + play type + shots) for the player series."""
    games = inp["games"].select("game_id", "game_date", "season")
    df = inp["box"].join(games, on="game_id", how="left")
    df = df.join(inp["oc"], on=["game_id", "player_id"], how="left").join(
        inp["ptype"], on=["game_id", "player_id"], how="left"
    )
    df = df.join(inp["pshots"], on=["game_id", "player_id"], how="left")
    df = df.with_columns(pl.col("team_id").cast(pl.Int64))
    pf = pace_factor_table(tg).select("game_id", "team_id", "pace_factor")
    df = df.join(pf, on=["game_id", "team_id"], how="left")
    played = pl.col("minutes").fill_null(0) > 0
    zero = (
        ["fsp", "tr_fsp", "tr_pts", "mk", "ast_mk", "pb_att", "pb_made"]
        + [f"att_{b}" for b in BK]
        + [f"made_{b}" for b in BK]
        + [f"att_{s}" for s in sh.SUBTYPES]
    )
    df = df.with_columns(
        [
            pl.when(played).then(pl.col(c).fill_null(0)).otherwise(pl.col(c)).alias(c)
            for c in zero
            if c in df.columns
        ]
    )
    # league bucket FG% strictly before the date (for exp_fg / shot_making)
    daily = df.select("game_date", *[f"att_{b}" for b in BK], *[f"made_{b}" for b in BK])
    lg = league_asof(daily, {f"lg_fg_{b}": (f"made_{b}", f"att_{b}") for b in BK}, df["game_date"])
    df = df.join(lg, on="game_date", how="left").with_columns(played.alias("played"))
    stats = [s for s in ar.STAT_DEFS if s not in EXCLUDED_STATS]
    exprs: list[pl.Expr] = []
    for s in stats:
        num, den, _ = ar.STAT_DEFS[s]
        exprs += [num.alias(f"{s}__num"), den.alias(f"{s}__den")]
    for s, (num, den) in SHOT_DEFS.items():
        exprs += [num.alias(f"{s}__num"), den.alias(f"{s}__den")]
    keys = ["player_id", "game_id", "game_date", "team_id", "pos", "played"]
    out = df.with_columns(exprs).select(keys + [e.meta.output_name() for e in exprs])
    allstats = stats + list(SHOT_DEFS)
    return out.with_columns(
        [
            pl.when(pl.col(f"{s}__den") > 0)
            .then(pl.col(f"{s}__den"))
            .otherwise(None)
            .alias(f"{s}__den")
            for s in allstats
        ]
    )


def build_advanced(inp: dict[str, pl.DataFrame], rows: pl.DataFrame) -> pl.DataFrame:
    """All family columns for the target ``rows`` (``game_id, player_id, min10``): one row per target row.

    Player series are computed over every box row (DNP rows included; they never advance a state).
    """
    missing = [k for k in INPUT_KEYS if k not in inp]
    if missing:
        raise KeyError(missing)
    tg = team_games(inp)
    pr = player_rows(inp, tg)
    stats = [s for s in ar.STAT_DEFS if s not in EXCLUDED_STATS] + list(SHOT_DEFS)
    ser = ar.asof_series(pr, stats)
    team = team_series(tg)
    games = inp["games"].select("game_id", "game_date", "home_team", "away_team")
    base = (
        rows.join(
            inp["box"].select("game_id", "player_id", pl.col("team_id").cast(pl.Int64)),
            on=["game_id", "player_id"],
            how="left",
        )
        .join(games, on="game_id", how="left")
        .with_columns(
            pl.when(pl.col("team_id") == pl.col("home_team"))
            .then(pl.col("away_team"))
            .otherwise(pl.col("home_team"))
            .cast(pl.Int64)
            .alias("opp_id")
        )
        .join(ser, on=["player_id", "game_id"], how="left")
    )
    own = team.rename({c: f"t_{c}" for c in TEAM_STATS})
    opp = team.rename({"team_id": "opp_id", **{c: f"o_{c}" for c in TEAM_STATS}})
    base = base.join(own, on=["game_id", "team_id"], how="left").join(
        opp, on=["game_id", "opp_id"], how="left"
    )
    # league references for the interactions (strictly earlier dates)
    lg_in = tg.select(
        "game_date",
        *[f"alw_att_{b}" for b in BK],
        *[f"alw_made_{b}" for b in BK],
        "faced",
        "faced_fast",
    )
    lg = league_asof(
        lg_in,
        {
            **{f"lgx_fg_{b}": (f"alw_made_{b}", f"alw_att_{b}") for b in BK},
            "lgx_fast": ("faced_fast", "faced"),
        },
        base["game_date"],
    )
    base = base.join(lg, on="game_date", how="left")
    c: list[pl.Expr] = [
        pl.col("t_pace48").alias("team_pace"),
        pl.col("o_pace48").alias("opp_pace"),
        ((pl.col("t_pace48") + pl.col("o_pace48")) / 2.0).alias("proj_poss"),
        pl.col("t_orb").alias("team_orb"),
        pl.col("t_drb").alias("team_drb"),
        pl.col("o_alw_orb").alias("opp_alw_orb"),
        pl.col("o_alw_drb").alias("opp_alw_drb"),
        *[pl.col(f"o_alw_sh_{b}").alias(f"opp_alw_sh_{b}") for b in BK],
        *[pl.col(f"o_alw_fg_{b}").alias(f"opp_alw_fg_{b}") for b in BK],
        pl.col("o_def_rim_fg").alias("opp_def_rim_fg"),
        pl.col("o_alw_contest").alias("opp_alw_contest"),
        pl.col("o_hus_contest").alias("opp_hus_contest"),
        pl.col("o_alw_fast").alias("opp_alw_fast"),
    ]
    base = base.with_columns(c).with_columns(
        (pl.col("usg_ew20") * pl.col("proj_poss") * pl.col("min10") / 48.0).alias("exp_plays"),
        *[
            (pl.col(f"{s}100_ew20") * pl.col("proj_poss") * pl.col("min10") / 48.0 / 100.0).alias(
                f"exp_{s}"
            )
            for s in ("pts", "reb", "ast", "fg3m")
        ],
        pl.sum_horizontal(
            [pl.col(f"sh_{b}_ew20") * (pl.col(f"o_alw_fg_{b}") - pl.col(f"lgx_fg_{b}")) for b in BK]
        ).alias("exp_fg_vs_opp"),
        (pl.col("sh_rim_ew20") * (pl.col("o_alw_fg_rim") - pl.col("lgx_fg_rim"))).alias(
            "rim_x_opp"
        ),
        pl.sum_horizontal(
            [
                pl.col(f"sh_{b}_ew20") * (pl.col(f"o_alw_fg_{b}") - pl.col(f"lgx_fg_{b}"))
                for b in ("corner3", "atb3")
            ]
        ).alias("three_x_opp"),
        (pl.col("tr_share_ew20") * (pl.col("o_alw_fast") - pl.col("lgx_fast"))).alias(
            "tr_share_x_opp"
        ),
        (pl.col("tr_ppp_ew20") * (pl.col("o_alw_fast") - pl.col("lgx_fast"))).alias("tr_ppp_x_opp"),
    )
    cols = sorted({c for v in FAMILY_COLUMNS.values() for c in v})
    absent = [c for c in cols if c not in base.columns]
    if absent:
        raise KeyError(f"assembly did not produce {absent}")
    return base.select(["game_id", "player_id", *cols])


def save_inputs_hint() -> Path:  # pragma: no cover - documentation anchor
    return Path("docs/prereg/ADVANCED_RATES.md")


def numeric_perturb(
    inp: dict[str, pl.DataFrame], game_ids: set[str], skip: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Planted-test helper: inflate every numeric non-key column of the given games in every input frame."""
    keep = {
        "game_id",
        "player_id",
        "team_id",
        "def_team_id",
        "season",
        "period",
        "max_period",
        "home_team",
        "away_team",
        *skip,
    }
    out: dict[str, Any] = {}
    ids = list(game_ids)
    for k, df in inp.items():
        if "game_id" not in df.columns:
            out[k] = df
            continue
        exprs = [
            pl.when(pl.col("game_id").is_in(ids))
            .then(pl.col(c) * 3 + 7)
            .otherwise(pl.col(c))
            .alias(c)
            for c, t in df.schema.items()
            if c not in keep and t.is_numeric()
        ]
        out[k] = df.with_columns(exprs)
    return out
