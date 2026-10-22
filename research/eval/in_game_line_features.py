# ruff: noqa: E501
"""IN_GAME_LINE_MODEL state features and loaders (rule: docs/prereg/IN_GAME_LINE_MODEL.md, DRAFT).

State at a checkpoint is built from play-by-play events and lineup stints with ``t <= T`` only (``t`` = game seconds elapsed). Reads
``nba.duckdb`` (seasons 2022-2024) and ``data/history/nba_history.duckdb`` (2019-2021) with ``read_only=True`` and ``season <= 2024`` in every
SQL; every pbp file path goes through ``rule.check_game_id`` first. The final box score is NOT read here. ``with_future=True`` adds the regulation
minutes after the checkpoint (stint-derived, a minutes target, never a feature) and is requested only by the descriptive shape item and by the
training of arm M's minutes stage.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import in_game_line_rule as rule

CK_SECONDS = tuple(e * 60 for e in rule.CHECKPOINTS)
PBP_COLS = [
    "game_id", "action_number", "period", "clock", "team_id", "player_id", "player_name",
    "action_type", "sub_type", "description", "score_home", "score_away", "shot_result", "shot_value",
]  # fmt: skip


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def data_root() -> Path:
    import os

    return Path(os.environ.get("NBA_DATA_ROOT", Path(__file__).resolve().parents[2]))


def pbp_path(game_id: str, root: Path) -> Path:
    rule.check_game_id(game_id)
    season = 2000 + int(game_id[3:5])
    return root / "data" / ("pbp" if season >= 2022 else "history/pbp") / f"{game_id}.parquet"


def db_path(season: int, root: Path) -> Path:
    rule.check_season(season)
    return root / ("nba.duckdb" if season >= 2022 else "data/history/nba_history.duckdb")


def read_games(season: int, root: Path) -> pl.DataFrame:
    """Regular-season games of one season: game_id, game_date, home_team, away_team (season <= 2024 in the SQL)."""
    rule.check_season(season)
    c = duckdb.connect(str(db_path(season, root)), read_only=True)
    try:
        df = c.execute(
            "select game_id, game_date, home_team, away_team from games where season = ? and season <= 2024 and game_id like '002%' order by game_id",
            [season],
        ).pl()
    finally:
        c.close()
    return df


def read_stints(season: int, root: Path) -> pl.DataFrame:
    rule.check_season(season)
    c = duckdb.connect(str(db_path(season, root)), read_only=True)
    try:
        df = c.execute(
            "select s.game_id, s.team_id, s.period, s.start_clock, s.end_clock, s.players from stints s join games g on g.game_id = s.game_id "
            "where g.season = ? and g.season <= 2024 and s.game_id like '002%' order by s.game_id, s.team_id, s.period, s.stint_idx",
            [season],
        ).pl()
    finally:
        c.close()
    return df


def clock_seconds_expr() -> pl.Expr:
    return pl.col("clock").str.extract(r"PT(\d+)M", 1).cast(pl.Float64) * 60.0 + pl.col(
        "clock"
    ).str.extract(r"M([\d.]+)S", 1).cast(pl.Float64)


def elapsed_expr(period: str = "period", left: str = "left") -> pl.Expr:
    """Game seconds elapsed at (period, seconds left in the period): regulation periods are 720 s, overtime periods 300 s."""
    p = pl.col(period)
    return (
        pl.when(p <= 4)
        .then((p - 1) * 720.0 + 720.0 - pl.col(left))
        .otherwise(2880.0 + (p - 5) * 300.0 + 300.0 - pl.col(left))
    )


def read_pbp(game_ids: list[str], root: Path) -> pl.DataFrame:
    """Events of the given regular-season games with ``t`` (game seconds elapsed); missing files are simply absent (counted by completeness)."""
    paths = [p for p in (pbp_path(g, root) for g in game_ids) if p.exists()]
    if not paths:
        return pl.DataFrame(schema={c: pl.Utf8 for c in PBP_COLS} | {"t": pl.Float64})
    df = pl.concat([pl.read_parquet(p, columns=PBP_COLS) for p in paths], how="vertical_relaxed")
    return (
        df.with_columns(clock_seconds_expr().alias("left"))
        .with_columns(elapsed_expr().alias("t"))
        .drop("left")
    )


def _fold(name: str | None) -> str | None:
    if name is None:
        return None
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)
    ).casefold()


def fold_names(df: pl.DataFrame, col: str = "player_name") -> pl.DataFrame:
    """Accent- and case-folded copy of a name column (pbp spells Bogdanovic with and without the accent in different fields)."""
    uniq = df[col].unique().to_list()
    return df.with_columns(
        pl.col(col).replace_strict({u: _fold(u) for u in uniq}, default=None).alias(col)
    )


def assist_events(
    pbp: pl.DataFrame,
    made: pl.DataFrame,
    st: pl.DataFrame | None,
    dates: pl.DataFrame | None = None,
    names_pbp: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Assist events (see ``stat_events``). The name dictionary is CAUSAL (Amendment 3, M1): an assist at time t in game G is matched to a teammate
    whose folded surname equals the assister text (a leading first-name prefix ``[a-z]{1,3}. `` removed) AND whose name is known by then, i.e.
    (a) he has an event of his own in G at a time <= t, or (b, Amendment 3b) he appears in a game on a STRICTLY EARLIER DATE (``dates`` gives
    each game's date, ``names_pbp`` the play-by-play those earlier games are read from, default ``pbp``). Without ``st``: a unique candidate
    seen by t resolves. With ``st``: the teammates on court at t (stint a < t <= b, or t = 0) whose name is known and matches; exactly one
    resolves; none on court: the unique seen-by-t candidate if there is one; several: unresolved."""
    ev = (
        fold_names(
            made.with_columns(
                pl.col("description").str.extract(r"\(([^()]+?) \d+ AST\)", 1).alias("assister")
            ),
            "assister",
        )
        .filter(pl.col("assister").is_not_null())
        .with_columns(pl.col("assister").str.replace(r"^[a-z]{1,3}\. ", "").alias("surname"))
        .select("game_id", "team_id", "t", "surname")
        .with_row_index("eid")
    )
    seen = (
        fold_names(
            pbp.filter(pl.col("player_id") > 0).select(
                "game_id", "team_id", "player_id", "player_name", "t"
            )
        )
        .group_by("game_id", "team_id", "player_id", "player_name")
        .agg(pl.col("t").min().alias("first_t"))
        .rename({"player_name": "surname", "player_id": "cand"})
    )
    cands = ev.join(seen, on=["game_id", "team_id", "surname"], how="inner").filter(
        pl.col("first_t") <= pl.col("t")
    )
    n = cands.group_by("eid").agg(pl.len().alias("n"))
    one = (
        cands.join(n, on="eid")
        .filter(pl.col("n") == 1)
        .select("eid", pl.col("cand").alias("player_id"))
    )
    if st is None:
        res = one
    else:
        oncourt = ev.join(
            st.rename({"player_id": "cand"}).with_columns(pl.col("cand").cast(pl.Int64)),
            on=["game_id", "team_id"],
            how="inner",
        ).filter(
            ((pl.col("a") < pl.col("t")) & (pl.col("t") <= pl.col("b")))
            | ((pl.col("a") == 0.0) & (pl.col("t") == 0.0))
        )
        m = (
            oncourt.join(
                seen.select("game_id", "team_id", "cand", "surname", "first_t"),
                on=["game_id", "team_id", "cand", "surname"],
                how="inner",
            )
            .filter(pl.col("first_t") <= pl.col("t"))
            .select("eid", "cand")
        )
        if dates is not None:
            src = names_pbp if names_pbp is not None else pbp
            prior = (
                fold_names(
                    src.filter(pl.col("player_id") > 0)
                    .select("game_id", "player_id", "player_name")
                    .unique()
                )
                .join(dates.select("game_id", "game_date"), on="game_id")
                .group_by("player_id", "player_name")
                .agg(pl.col("game_date").min().alias("first_date"))
                .rename({"player_name": "surname", "player_id": "cand"})
            )
            m2 = (
                oncourt.join(dates.select("game_id", "game_date"), on="game_id")
                .join(prior, on=["cand", "surname"], how="inner")
                .filter(pl.col("first_date") < pl.col("game_date"))
                .select("eid", "cand")
            )
            m = pl.concat([m, m2])
        k = (
            m.unique()
            .group_by("eid")
            .agg(pl.col("cand").n_unique().alias("k"), pl.col("cand").first())
            .filter(pl.col("k") == 1)
        )
        matched = m["eid"].unique()
        fallback = one.filter(~pl.col("eid").is_in(matched))
        res = pl.concat([k.select("eid", pl.col("cand").alias("player_id")), fallback])
    out = ev.join(res, on="eid", how="inner")
    return out.select(
        "game_id", "team_id", "player_id", "t", pl.lit(1.0).alias("val")
    ).with_columns(pl.lit("ast").alias("stat"))


def stat_events(
    pbp: pl.DataFrame,
    st: pl.DataFrame | None = None,
    dates: pl.DataFrame | None = None,
    names_pbp: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per stat event: game_id, team_id, player_id, t, stat, val. pts = made field goals (shot_value) + made free throws (1);
    fg3m = made threes; reb = rebounds credited to a player; ast = the assister named in a made shot's description, resolved to a player id of
    the SAME team by surname when that surname is unique among the team's player names in the game. Amendment 2 (assist repair): when ``st`` (the
    ``stint_rows`` of the games) is given, a description printed with a first-name prefix (``D. Green``, ``Jal. Williams``) or a surname shared by
    two teammates is resolved by who of the matching teammates is ON COURT at the event time (exactly one: resolved; zero or several: dropped and
    counted by ``ast_unresolved``). Without ``st`` only the causal unique-surname rule applies (the 'before' column of the repair table)."""
    made = pbp.filter(pl.col("action_type") == "Made Shot")
    ft = pbp.filter(
        (pl.col("action_type") == "Free Throw")
        & ~pl.col("description").str.starts_with("MISS")
        & (pl.col("player_id") > 0)
    )
    pts = pl.concat(
        [
            made.select(
                "game_id",
                "team_id",
                "player_id",
                "t",
                pl.col("shot_value").cast(pl.Float64).alias("val"),
            ),
            ft.select("game_id", "team_id", "player_id", "t", pl.lit(1.0).alias("val")),
        ]
    ).with_columns(pl.lit("pts").alias("stat"))
    fg3 = (
        made.filter(pl.col("shot_value") == 3)
        .select("game_id", "team_id", "player_id", "t", pl.lit(1.0).alias("val"))
        .with_columns(pl.lit("fg3m").alias("stat"))
    )
    reb = (
        pbp.filter((pl.col("action_type") == "Rebound") & (pl.col("player_id") > 0))
        .select("game_id", "team_id", "player_id", "t", pl.lit(1.0).alias("val"))
        .with_columns(pl.lit("reb").alias("stat"))
    )
    ast = assist_events(pbp, made, st, dates, names_pbp)
    return pl.concat([pts, fg3, reb, ast]).select(
        "game_id", "team_id", "player_id", "t", "stat", "val"
    )


def ast_unresolved(pbp: pl.DataFrame, ev: pl.DataFrame) -> tuple[int, int]:
    """(assists named in descriptions, assists resolved to an id): the share resolved is part of the completeness report."""
    named = int(
        pbp.filter(
            pl.col("description").str.contains(r"\d+ AST\)")
            & (pl.col("action_type") == "Made Shot")
        ).height
    )
    return named, int(ev.filter(pl.col("stat") == "ast").height)


def foul_events(pbp: pl.DataFrame) -> pl.DataFrame:
    """Personal fouls (technicals excluded) with the running personal-foul number parsed from the description ``(P<n>...)``."""
    f = pbp.filter(
        (pl.col("action_type") == "Foul")
        & (pl.col("player_id") > 0)
        & ~pl.col("sub_type").str.contains("Technical")
    )
    return (
        f.with_columns(pl.col("description").str.extract(r"\(P(\d+)", 1).cast(pl.Int64).alias("pn"))
        .drop_nulls("pn")
        .filter(pl.col("pn") > 0)
        .select("game_id", "team_id", "player_id", "t", "pn")
    )


def scoring_timeline(pbp: pl.DataFrame) -> pl.DataFrame:
    """(game_id, t, home, away) at every row that carries a score, sorted. Period rows included (the end of a period carries the score)."""
    s = pbp.filter((pl.col("score_home") != "") & (pl.col("score_away") != "")).select(
        "game_id",
        "t",
        pl.col("score_home").cast(pl.Int64, strict=False).alias("home"),
        pl.col("score_away").cast(pl.Int64, strict=False).alias("away"),
        "action_number",
    )
    return s.drop_nulls().sort("game_id", "t", "action_number").drop("action_number")


def stint_rows(stints: pl.DataFrame) -> pl.DataFrame:
    """One row per (game, team, player, stint) with elapsed start ``a`` and end ``b`` (game seconds)."""
    s = stints.filter(pl.col("period") <= 4).with_columns(
        elapsed_expr("period", "start_clock").alias("a"),
        elapsed_expr("period", "end_clock").alias("b"),
    )
    return (
        s.explode("players")
        .rename({"players": "player_id"})
        .select("game_id", "team_id", "player_id", "a", "b")
    )


def minutes_at(st: pl.DataFrame, T: float) -> pl.DataFrame:
    """Minutes on court by T per (game, team, player), from stints only (no box score). Seconds are summed and rounded to 0.01 s before the
    division (a parallel group-by sums floats in a varying order; Run 1 of the gate caught a 1e-15 difference as a planted-future failure), then
    minutes are rounded to 1e-6 so the state and its digest are deterministic."""
    sec = (pl.min_horizontal(pl.col("b"), pl.lit(T)) - pl.col("a")).clip(lower_bound=0.0)
    return (
        st.with_columns(sec.alias("sec"))
        .group_by("game_id", "team_id", "player_id")
        .agg(pl.col("sec").sum().round(2).alias("sec"), (pl.col("a") == 0.0).any().alias("starter"))
        .with_columns((pl.col("sec") / 60.0).round(6).alias("minutes"))
        .drop("sec")
    )


def margin_at(
    score: pl.DataFrame, games: pl.DataFrame, T: float, players: pl.DataFrame
) -> pl.Series:
    """Team-view margin at T for each row of ``players`` (columns game_id, team_id): last score row with t <= T (0-0 before any score)."""
    sc = (
        score.filter(pl.col("t") <= T)
        .group_by("game_id")
        .agg(pl.col("home").last(), pl.col("away").last())
    )
    j = players.join(games.select("game_id", "home_team"), on="game_id", how="left").join(
        sc, on="game_id", how="left"
    )
    mar = (
        pl.when(pl.col("team_id") == pl.col("home_team"))
        .then(pl.col("home") - pl.col("away"))
        .otherwise(pl.col("away") - pl.col("home"))
    )
    return j.select(mar.fill_null(0).alias("margin")).to_series()


def state_frame(
    season: int,
    root: Path,
    with_future: bool = False,
    games: pl.DataFrame | None = None,
    pbp: pl.DataFrame | None = None,
    stints: pl.DataFrame | None = None,
    names_pbp: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per (game, team, player, checkpoint e) for every player who has been on court by the checkpoint, with minutes, starter flag, fouls,
    the four current stats and the team-view margin, all as of T = e x 60 seconds (events with t <= T). With ``with_future`` also
    ``y_rem_min`` = regulation minutes played after T (stint-derived) -- a minutes TARGET, never a feature."""
    games = games if games is not None else read_games(season, root)
    ids = games["game_id"].to_list()
    pbp = pbp if pbp is not None else read_pbp(ids, root)
    st = stint_rows(stints if stints is not None else read_stints(season, root))
    ev = stat_events(pbp, st, games, names_pbp)
    fo = foul_events(pbp)
    score = scoring_timeline(pbp)
    m48 = (
        minutes_at(st, float(rule.REGULATION_SECONDS)).select(
            "game_id", "team_id", "player_id", pl.col("minutes").alias("m48")
        )
        if with_future
        else None
    )
    parts = []
    for e, T in zip(rule.CHECKPOINTS, CK_SECONDS, strict=True):
        base = minutes_at(st, float(T)).filter(pl.col("minutes") > 0)
        cur = (
            ev.filter(pl.col("t") <= T)
            .group_by("game_id", "player_id", "stat")
            .agg(pl.col("val").sum())
            .pivot(on="stat", index=["game_id", "player_id"], values="val")
        )
        for s in rule.STATS:
            if s not in cur.columns:
                cur = cur.with_columns(pl.lit(0.0).alias(s))
        nf = (
            fo.filter(pl.col("t") <= T)
            .group_by("game_id", "player_id")
            .agg(pl.len().alias("fouls"))
        )
        df = base.join(cur, on=["game_id", "player_id"], how="left").join(
            nf, on=["game_id", "player_id"], how="left"
        )
        df = df.with_columns(
            [pl.col(s).fill_null(0.0) for s in rule.STATS]
            + [
                pl.col("fouls").fill_null(0).cast(pl.Int64),
                pl.lit(e).alias("e"),
                pl.col("starter").cast(pl.Int64),
            ]
        )
        df = df.with_columns(
            margin_at(score, games, float(T), df.select("game_id", "team_id")).alias("margin")
        )
        if m48 is not None:
            df = (
                df.join(m48, on=["game_id", "team_id", "player_id"], how="left")
                .with_columns(
                    (pl.col("m48").fill_null(0.0) - pl.col("minutes"))
                    .clip(lower_bound=0.0)
                    .alias("y_rem_min")
                )
                .drop("m48")
            )
        parts.append(
            df.select(
                "game_id",
                "team_id",
                "player_id",
                "e",
                "minutes",
                "starter",
                "fouls",
                *rule.STATS,
                "margin",
                *(["y_rem_min"] if with_future else []),
            )
        )
    out = pl.concat(parts).join(games.select("game_id", "game_date"), on="game_id", how="left")
    return out.sort("game_id", "team_id", "player_id", "e")


def expand_units(state: pl.DataFrame, stat: str) -> pl.DataFrame:
    """Units of one stat: (player-game, checkpoint, line) where the line is not yet reached. Adds ``line`` and ``cur`` (the current stat)."""
    lines = pl.DataFrame({"line": list(rule.LINES[stat])})
    u = state.join(lines, how="cross").filter(pl.col(stat) < pl.col("line"))
    return u.with_columns(pl.col(stat).alias("cur")).drop(
        *[s for s in rule.STATS if s in u.columns]
    )


def sha_frame(df: pl.DataFrame) -> str:
    """Order-independent sha256 of a frame (sorted by all columns, then hashed through its parquet-free row text)."""
    cols = sorted(df.columns)
    d = df.select(cols).sort(cols)
    h = hashlib.sha256()
    h.update(",".join(cols).encode())
    for chunk in d.iter_slices(50_000):
        h.update(chunk.write_csv().encode())
    return h.hexdigest()


def completeness(
    games: pl.DataFrame,
    pbp: pl.DataFrame,
    stints: pl.DataFrame,
    state_stats: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Per game, structural completeness (label-free; nothing about a box score): ``pbp_ok`` (file present, four regulation periods ended),
    ``minutes_ok`` (both teams, periods 1-4: every stint has five players and stints tile exactly 720 s within one second),
    ``pts_ok`` (the pbp-reconstructed team points through regulation equal the pbp's own score at the end of regulation) and ``fouls_ok``
    (the counted personal fouls of at least FOULS_CONSISTENT_MIN of fouling players equal their highest running foul number)."""
    ids = games.select("game_id")
    have = pbp.group_by("game_id").agg(
        (
            (pl.col("action_type") == "period")
            & (pl.col("sub_type") == "end")
            & (pl.col("period") <= 4)
        )
        .sum()
        .alias("nend")
    )
    pbp_ok = (
        ids.join(have, on="game_id", how="left")
        .with_columns((pl.col("nend").fill_null(0) >= 4).alias("pbp_ok"))
        .select("game_id", "pbp_ok")
    )
    s = stints.filter(pl.col("period") <= 4).with_columns(
        (pl.col("start_clock") - pl.col("end_clock")).alias("dur")
    )  # players is a fixed Array(Int32, 5) column: five per stint by type
    agg = s.group_by("game_id", "team_id", "period").agg(
        pl.col("dur").sum().alias("dur"), pl.col("players").is_null().any().alias("bad")
    )
    per_team = agg.group_by("game_id").agg(
        (((pl.col("dur") - 720.0).abs() <= 1.0) & ~pl.col("bad")).all().alias("tiles"),
        pl.len().alias("n_team_periods"),
    )
    minutes_ok = (
        ids.join(per_team, on="game_id", how="left")
        .with_columns(
            ((pl.col("n_team_periods").fill_null(0) == 8) & pl.col("tiles").fill_null(False)).alias(
                "minutes_ok"
            )
        )
        .select("game_id", "minutes_ok")
    )
    ev = stat_events(pbp).filter(
        (pl.col("stat") == "pts") & (pl.col("t") <= rule.REGULATION_SECONDS)
    )
    sc = (
        scoring_timeline(pbp)
        .filter(pl.col("t") <= rule.REGULATION_SECONDS)
        .group_by("game_id")
        .agg(pl.col("home").last(), pl.col("away").last())
    )
    rec = (
        ev.join(games.select("game_id", "home_team"), on="game_id")
        .group_by("game_id")
        .agg(
            pl.col("val").filter(pl.col("team_id") == pl.col("home_team")).sum().alias("rh"),
            pl.col("val").filter(pl.col("team_id") != pl.col("home_team")).sum().alias("ra"),
        )
    )
    pts_ok = (
        ids.join(sc, on="game_id", how="left")
        .join(rec, on="game_id", how="left")
        .with_columns(
            ((pl.col("home") == pl.col("rh")) & (pl.col("away") == pl.col("ra")))
            .fill_null(False)
            .alias("pts_ok")
        )
        .select("game_id", "pts_ok")
    )
    fo = (
        foul_events(pbp)
        .group_by("game_id", "player_id")
        .agg(pl.len().alias("n"), pl.col("pn").max().alias("mx"))
    )
    fouls_ok = (
        fo.group_by("game_id")
        .agg((pl.col("n") == pl.col("mx")).mean().alias("share"))
        .with_columns((pl.col("share") >= rule.FOULS_CONSISTENT_MIN).alias("fouls_ok"))
        .select("game_id", "fouls_ok")
    )
    out = (
        ids.join(pbp_ok, on="game_id")
        .join(minutes_ok, on="game_id")
        .join(pts_ok, on="game_id")
        .join(fouls_ok, on="game_id", how="left")
    )
    out = out.with_columns(pl.col("fouls_ok").fill_null(False))
    return out.with_columns(
        (pl.col("pbp_ok") & pl.col("minutes_ok") & pl.col("pts_ok") & pl.col("fouls_ok")).alias(
            "full"
        )
    )


def np_codes(values: Any) -> np.ndarray:
    return np.unique(np.asarray(values), return_inverse=True)[1].astype(np.int64)


def planted_future(season: int, root: Path, cut_min: int = 24, n_games: int = 30) -> dict[str, Any]:
    """Planted-future identity: the state rows at checkpoints e <= cut_min must be byte-identical when every event and stint AFTER the cut is
    rewritten (events doubled with inflated scores, post-cut stints given other players), and the rows after the cut must differ (positive
    control that the plant is visible). Label-free; ``n_games`` first games of the season."""
    games = read_games(season, root).head(n_games)
    ids = games["game_id"].to_list()
    pbp = read_pbp(ids, root)
    stints = read_stints(season, root).filter(pl.col("game_id").is_in(ids))
    cut = float(cut_min * 60)
    later = pbp.filter(pl.col("t") > cut)
    planted = pl.concat(
        [
            pbp,
            later.with_columns(
                (pl.col("action_number") + 100000).alias("action_number"),
                pl.when(pl.col("score_home") != "")
                .then((pl.col("score_home").cast(pl.Int64, strict=False) + 50).cast(pl.Utf8))
                .otherwise(pl.col("score_home"))
                .alias("score_home"),
            ),
        ]
    )
    elapsed = elapsed_expr("period", "start_clock")
    st2 = stints.with_columns(
        pl.when((elapsed > cut) & (pl.col("period") <= 4))
        .then(pl.col("players").arr.eval(pl.element() + 7))
        .otherwise(pl.col("players"))
        .alias("players")
    )
    a = state_frame(season, root, False, games, pbp, stints)
    b = state_frame(season, root, False, games, planted, st2)
    keys = ["game_id", "team_id", "player_id", "e"]
    early_a, early_b = (
        a.filter(pl.col("e") <= cut_min).sort(keys),
        b.filter(pl.col("e") <= cut_min).sort(keys),
    )
    late_a, late_b = (
        a.filter(pl.col("e") > cut_min).sort(keys),
        b.filter(pl.col("e") > cut_min).sort(keys),
    )
    return {
        "season": season,
        "games": len(ids),
        "rows_early": early_a.height,
        "identical_before_cut": bool(early_a.equals(early_b)),
        "later_rows_differ": not late_a.equals(late_b),
    }


def clip_stints(stints: pl.DataFrame, T: float) -> pl.DataFrame:
    """Stints as known at game second T: stints starting at or after T are dropped and a stint spanning T ends at T (regulation periods; T is
    never in overtime)."""
    a = elapsed_expr("period", "start_clock")
    b = elapsed_expr("period", "end_clock")
    st = stints.with_columns(a.alias("_a"), b.alias("_b")).filter(pl.col("_a") < T)
    clipped = (pl.col("period").cast(pl.Float64) * 720.0 - T).cast(stints.schema["end_clock"])
    return st.with_columns(
        pl.when(pl.col("_b") > T).then(clipped).otherwise(pl.col("end_clock")).alias("end_clock")
    ).drop("_a", "_b")


def truncation_identity(
    season: int,
    root: Path,
    n_games: int = 200,
    state_fn: Any = None,
    games: pl.DataFrame | None = None,
    pbp: pl.DataFrame | None = None,
    stints: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Truncation identity (adversary review M2): for every checkpoint e, the state rebuilt from the play-by-play cut at T = 60 e and stints clipped
    at T must equal the state at e built from the whole game, on EVERY state column (including ast), with no row missing or extra. Catches any
    whole-game aggregate (name sets, candidate sets, stint-derived flags). Label-free. ``n_games`` evenly spaced games. ``state_fn`` is injectable
    for the test that proves the check can fail."""
    fn = state_fn if state_fn is not None else state_frame
    games = games if games is not None else read_games(season, root)
    if games.height > n_games:
        idx = np.linspace(0, games.height - 1, n_games).round().astype(int)
        games = games[sorted(set(idx.tolist()))]
    ids = games["game_id"].to_list()
    pbp = pbp if pbp is not None else read_pbp(ids, root)
    stints = (
        stints
        if stints is not None
        else read_stints(season, root).filter(pl.col("game_id").is_in(ids))
    )
    full = fn(season, root, False, games, pbp, stints, pbp)
    keys = ["game_id", "team_id", "player_id", "e"]
    cols = [c for c in full.columns if c not in keys]
    differing = dict.fromkeys(cols, 0)
    compared = missing = extra = 0
    for e in rule.CHECKPOINTS:
        T = e * 60.0
        tr = fn(
            season, root, False, games, pbp.filter(pl.col("t") <= T), clip_stints(stints, T), pbp
        )
        fu = full.filter(pl.col("e") == e)
        tr = tr.filter(pl.col("e") == e)
        j = fu.join(tr, on=keys, how="full", suffix="_tr", coalesce=True)
        both = j.filter(pl.col("minutes").is_not_null() & pl.col("minutes_tr").is_not_null())
        missing += int(j.filter(pl.col("minutes_tr").is_null()).height)
        extra += int(j.filter(pl.col("minutes").is_null()).height)
        compared += both.height
        for c in cols:
            differing[c] += int(both.filter(pl.col(c) != pl.col(f"{c}_tr")).height)
    return {
        "season": season,
        "games": len(ids),
        "rows_compared": compared,
        "rows_missing_in_truncated": missing,
        "rows_extra_in_truncated": extra,
        "differing_rows_by_column": differing,
        "identical": bool(missing == 0 and extra == 0 and not any(differing.values())),
    }
