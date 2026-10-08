"""Per-player raw game-history sequences for the GPU sequence props model.

For every PLAYED target game the export holds the player's last ``K`` (20) prior
team games as a token sequence (oldest -> newest, left-padded), plus a vector of
pre-tip context for the target game. Games the player missed are kept as tokens
with an ``absent`` flag instead of being dropped, so the model sees injury gaps,
DNPs and returns explicitly (see docs/DNP_AUDIT_2026-10-08.md: DNP rows have
``minutes`` NULL and all-zero stats; they are never labels and never "played").

As-of discipline (mirrors ``player_possession_features`` / ``context_residual``):

* A token is a game strictly BEFORE the target ``game_date``. The target game and
  anything later never enters a window (``tests/features/test_player_sequences.py``).
* A player's timeline is built from his own box-score rows (played or DNP) plus
  gap games: games of the team he most recently appeared for (rows strictly
  before the gap game) in which he has no row. A rookie's / new arrival's games
  before his first row for a team are padding, not "absent".
* Team context (rest, pace, defensive rating) is the as-of rolling value BEFORE
  that token's game. Elo is the pre-game MOV-Elo margin.
* Pre-tip report: the latest official snapshot with ``as_of <= game_date +
  tipoff proxy - 60 min`` (``usable_report_rows``); later snapshots are ignored.
  Games with no usable snapshot get ``has_report = 0`` (never imputed).
* Teammate availability for PAST games uses DNP rows of that game (known after it
  was played) and each teammate's as-of minutes / usage state; the target game's
  own box score is never read for context.

Season 2025 is the frozen holdout: ``max_season`` defaults to 2024 and the
builders raise if given anything later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import polars as pl

from nba.sim.usage_redistribution import ReportTriggerConfig, usable_report_rows

K = 20
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
FROZEN_SEASON = 2025
ZONES: tuple[str, ...] = ("rim", "mid", "above3")  # the parser emits no corner3 zone

#: Per-step feature channels (every value is a fixed-constant rescale, no fitting).
STEP_FEATURES: tuple[str, ...] = (
    "present",
    "absent",
    "same_team",
    "days_ago",
    "min",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "fga",
    "fta",
    "tov",
    "stl",
    "blk",
    "starter",
    "home",
    "rest",
    "b2b",
    "usg_poss",
    "zone_rim",
    "zone_mid",
    "zone_ab3",
    "usg_box",
    "opp_drtg",
    "team_pace",
    "n_tm_out",
    "vac_tm",
)
_SI = {n: i for i, n in enumerate(STEP_FEATURES)}
#: Fixed divisors for the box-score channels.
_STEP_SCALE = {
    "min": 36.0,
    "pts": 20.0,
    "reb": 10.0,
    "ast": 8.0,
    "fg3m": 4.0,
    "fga": 18.0,
    "fta": 8.0,
    "tov": 4.0,
    "stl": 2.0,
    "blk": 2.0,
}

CTX_FEATURES: tuple[str, ...] = (
    "home",
    "rest",
    "b2b",
    "elo_margin",
    "opp_drtg",
    "team_pace",
    "opp_pace",
    "team_drtg",
    "has_report",
    "st_available",
    "st_probable",
    "st_questionable",
    "st_doubtful",
    "st_out",
    "n_rep_out",
    "vac_rep",
    "n_return",
    "vac_return",
    "log_n_prior",
    "log_n_season",
    "gap_days",
    "pos_g",
    "pos_f",
    "pos_c",
    "team_game_no",
    "base_min",
    "starter10",
    "base_pts",
    "base_reb",
    "base_ast",
    "base_fg3m",
)
#: Prior league-ish means used to shrink early-career recency averages (constants).
BASE_PRIOR = {"pts": 8.0, "reb": 3.3, "ast": 2.0, "fg3m": 0.8, "min": 18.0}
STAT_SCALE = np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32)
REPORT_STATUSES = ("available", "probable", "questionable", "doubtful", "out")


@dataclass(frozen=True)
class SeqConfig:
    k: int = K
    min_target_season: int = 2022
    max_season: int = 2024
    halflife: float = 10.0
    base_prior_n: float = 2.0
    rotation_min: float = 12.0
    min_rotation_games: int = 5
    team_window: int = 20
    team_prior_n: float = 5.0
    pace_prior: float = 99.0
    drtg_prior: float = 113.0
    report: ReportTriggerConfig = field(default_factory=ReportTriggerConfig)


@dataclass
class RawInputs:
    games: pd.DataFrame  # game_id, game_date, season, home_team, away_team, home_pts, away_pts
    pgs: pd.DataFrame  # box rows incl. DNP rows
    poss_player: pd.DataFrame  # game_id, player_id, att, ft_trips, z_rim, z_mid, z_ab3
    poss_team: pd.DataFrame  # game_id, team_id, n_poss
    avail: pd.DataFrame  # game_id, player_id, status, as_of
    static: pd.DataFrame  # player_id, position
    elo: pd.DataFrame | None = None  # game_id, exp_margin_home


# --------------------------------------------------------------------------- loading


def load_raw(db_path: str, max_season: int = 2024) -> RawInputs:
    """Read-only load of everything the builder needs, aggregated in SQL (memory-light)."""
    if max_season >= FROZEN_SEASON:
        raise ValueError(f"max_season must be < {FROZEN_SEASON} (frozen holdout)")
    con = duckdb.connect(db_path, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {int(max_season)}"
        ).df()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, "
            "s.stl, s.blk, s.tov, s.starter, s.fga, s.fta FROM player_game_stats s "
            f"JOIN games g USING (game_id) WHERE g.season <= {int(max_season)}"
        ).df()
        zsel = ", ".join(
            f"count(*) FILTER (WHERE outcome IN ('FGM2','FGM3','FGA_miss') "
            f"AND shot_zone = '{z}') AS z_{a}"
            for z, a in zip(ZONES, ("rim", "mid", "ab3"), strict=True)
        )
        poss_player = con.execute(
            "SELECT p.game_id, p.shooter_id AS player_id, "
            "count(*) FILTER (WHERE outcome IN ('FGM2','FGM3','FGA_miss')) AS att, "
            f"count(*) FILTER (WHERE outcome = 'FT_trip') AS ft_trips, {zsel} "
            "FROM possessions p JOIN games g USING (game_id) "
            f"WHERE g.season <= {int(max_season)} AND p.shooter_id IS NOT NULL GROUP BY 1, 2"
        ).df()
        poss_team = con.execute(
            "SELECT p.game_id, p.off_team AS team_id, count(*) AS n_poss "
            "FROM possessions p JOIN games g USING (game_id) "
            f"WHERE g.season <= {int(max_season)} GROUP BY 1, 2"
        ).df()
        avail = con.execute(
            "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
            "CAST(a.as_of AS TIMESTAMP) AS as_of FROM player_availability a "
            "JOIN games g USING (game_id) WHERE a.game_id IS NOT NULL "
            "AND a.player_id IS NOT NULL AND a.source = 'nba_official_report' "
            f"AND g.season <= {int(max_season)}"
        ).df()
        static = con.execute("SELECT player_id, position FROM players_static").df()
    finally:
        con.close()
    return RawInputs(games, pgs, poss_player, poss_team, avail, static)


# --------------------------------------------------------------------------- team-game table


def team_game_table(
    games: pd.DataFrame,
    poss_team: pd.DataFrame,
    elo: pd.DataFrame | None,
    cfg: SeqConfig,
) -> pd.DataFrame:
    """One row per (game_id, team_id) with as-of rest / pace / defensive rating.

    ``pace_asof`` / ``drtg_asof`` are shrunk means of the team's previous
    ``team_window`` games (current game excluded) toward fixed constants.
    """
    g = games.copy()
    g["game_date"] = pd.to_datetime(g["game_date"])
    cols = ["game_id", "game_date", "season"]
    home = g[cols].assign(
        team_id=g["home_team"], opp_id=g["away_team"], is_home=1, pts_against=g["away_pts"]
    )
    away = g[cols].assign(
        team_id=g["away_team"], opp_id=g["home_team"], is_home=0, pts_against=g["home_pts"]
    )
    tg = pd.concat([home, away], ignore_index=True)
    pt = poss_team.rename(columns={"n_poss": "n_poss"})
    tg = tg.merge(pt, on=["game_id", "team_id"], how="left")
    opp = pt.rename(columns={"team_id": "opp_id", "n_poss": "opp_poss"})
    tg = tg.merge(opp, on=["game_id", "opp_id"], how="left")
    tg = tg.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)
    tg["pace_g"] = tg["n_poss"].astype(float)
    tg["drtg_g"] = 100.0 * tg["pts_against"].astype(float) / tg["opp_poss"].astype(float)
    grp = tg.groupby("team_id")
    for col, prior in (("pace_g", cfg.pace_prior), ("drtg_g", cfg.drtg_prior)):
        s = grp[col].transform(
            lambda x: x.shift(1).rolling(cfg.team_window, min_periods=1).sum()  # noqa: B023
        )
        c = grp[col].transform(
            lambda x: x.shift(1).rolling(cfg.team_window, min_periods=1).count()  # noqa: B023
        )
        tg[col.replace("_g", "_asof")] = (s.fillna(0.0) + cfg.team_prior_n * prior) / (
            c.fillna(0.0) + cfg.team_prior_n
        )
    rest = grp["game_date"].diff().dt.days
    tg["rest_days"] = rest.fillna(7).clip(0, 7)
    tg["b2b"] = (tg["rest_days"] == 1).astype(int)
    tg["team_game_no"] = tg.groupby(["team_id", "season"]).cumcount()
    tg["next_game_id"] = tg.groupby("team_id")["game_id"].shift(-1)
    oppv = tg[["game_id", "team_id", "pace_asof", "drtg_asof"]].rename(
        columns={"team_id": "opp_id", "pace_asof": "opp_pace_asof", "drtg_asof": "opp_drtg_asof"}
    )
    tg = tg.merge(oppv, on=["game_id", "opp_id"], how="left")
    if elo is not None and len(elo):
        e = elo[["game_id", "exp_margin_home"]]
        tg = tg.merge(e, on="game_id", how="left")
        tg["elo_margin"] = np.where(tg["is_home"] == 1, 1.0, -1.0) * tg["exp_margin_home"]
        tg = tg.drop(columns=["exp_margin_home"])
    else:
        tg["elo_margin"] = 0.0
    tg["elo_margin"] = tg["elo_margin"].fillna(0.0)
    tg = tg.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)
    return tg


# --------------------------------------------------------------------------- player-game table


def player_game_table(raw: RawInputs, cfg: SeqConfig) -> pd.DataFrame:
    """One row per box-score row (played or DNP), sorted by (player, date, game).

    Adds ``played``, per-game usage shares, zone mix, and as-of recency bases
    (``base_*`` use only PRIOR played games; ``n_prior`` counts them).
    """
    g = raw.games[["game_id", "game_date", "season", "home_team"]].copy()
    g["game_date"] = pd.to_datetime(g["game_date"])
    pg = raw.pgs.merge(g, on="game_id", how="inner")
    pg["played"] = (pg["minutes"].fillna(0) > 0).astype(bool)
    for c in ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov", "fga", "fta"):
        pg[c] = pg[c].fillna(0).astype(float)
    pg.loc[~pg["played"], ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov", "fga", "fta"]] = 0.0
    pg["minutes"] = pg["minutes"].where(pg["played"], 0.0).fillna(0.0).astype(float)
    pg["starter"] = pg["starter"].fillna(False).astype(float) * pg["played"]
    pg["is_home"] = (pg["team_id"] == pg["home_team"]).astype(float)
    use = pg["fga"] + 0.44 * pg["fta"] + pg["tov"]
    tot = use.groupby([pg["game_id"], pg["team_id"]]).transform("sum")
    pg["usg_box"] = np.where(tot > 0, use / tot.where(tot > 0, 1.0), 0.0)
    pg = pg.merge(raw.poss_player, on=["game_id", "player_id"], how="left")
    pg = pg.merge(raw.poss_team, on=["game_id", "team_id"], how="left")
    for c in ("att", "ft_trips", "z_rim", "z_mid", "z_ab3"):
        pg[c] = pg[c].fillna(0.0).astype(float)
    pg["usg_poss"] = np.where(
        pg["n_poss"].fillna(0) > 0,
        np.clip((pg["att"] + pg["ft_trips"]) / pg["n_poss"].where(pg["n_poss"] > 0, 1.0), 0, 1),
        0.0,
    )
    att = pg["att"].where(pg["att"] > 0, 1.0)
    for c in ("z_rim", "z_mid", "z_ab3"):
        pg[c.replace("z_", "zone_")] = np.where(pg["att"] > 0, pg[c] / att, 0.0)
    pg = pg.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)
    _add_recency_bases(pg, cfg)
    return pg


def _add_recency_bases(pg: pd.DataFrame, cfg: SeqConfig) -> None:
    """In place: as-of EW means over PRIOR played games, shrunk to constants.

    Same weighting as ``nba.props.forward.recency_weighted_dist`` (weight
    ``0.5 ** (games_ago / halflife)`` over played-only games), plus a pseudo-count
    shrink so a debut / 1-game history does not produce a degenerate base.
    """
    played = pg[pg["played"]]
    d = 0.5 ** (1.0 / cfg.halflife)
    n_prior = played.groupby("player_id").cumcount().astype(float)
    w_eff = (1.0 - d**n_prior) / (1.0 - d)
    cols = {**{s: s for s in STATS}, "min": "minutes", "starter10": "starter"}
    for name, src in cols.items():
        ew = played.groupby("player_id")[src].transform(
            lambda x: x.ewm(halflife=cfg.halflife, adjust=True).mean().shift(1)
        )
        pre = ew.fillna(0.0)
        prior = BASE_PRIOR.get(name, 0.3)
        base = (w_eff * pre + cfg.base_prior_n * prior) / (w_eff + cfg.base_prior_n)
        if name == "starter10":
            base = np.where(n_prior > 0, pre, 0.0)
        pg.loc[played.index, f"base_{name}"] = np.asarray(base, dtype=float)
    pg.loc[played.index, "n_prior"] = n_prior.to_numpy()
    season_n = played.groupby(["player_id", "season"]).cumcount().astype(float)
    pg.loc[played.index, "n_season"] = season_n.to_numpy()
    last_played = played.groupby("player_id")["game_date"].shift(1)
    gap = (played["game_date"] - last_played).dt.days
    pg.loc[played.index, "gap_days"] = gap.fillna(30.0).clip(0, 90).to_numpy()


def _post_state(pg: pd.DataFrame, cfg: SeqConfig) -> pd.DataFrame:
    """Per played row: state AFTER that game (for strictly-earlier as-of lookups)."""
    p = pg[pg["played"]].sort_values(["player_id", "game_date"]).copy()
    p["n_post"] = p.groupby("player_id").cumcount() + 1
    p["min_post"] = p.groupby("player_id")["minutes"].transform(lambda x: x.expanding().mean())
    p["usg_post"] = p.groupby("player_id")["usg_box"].transform(
        lambda x: x.ewm(halflife=cfg.halflife, adjust=True).mean()
    )
    out = p[["player_id", "game_date", "team_id", "n_post", "min_post", "usg_post"]]
    return out.sort_values("game_date").reset_index(drop=True)


# --------------------------------------------------------------------------- reports


def latest_pretip_status(avail: pd.DataFrame, games: pd.DataFrame, cfg: SeqConfig) -> pd.DataFrame:
    """Rows (game_id, player_id, status) of each game's LATEST usable snapshot.

    Usable = ``as_of <= game_date + tipoff proxy - lead`` (the repo-wide report
    rule). A player absent from the latest snapshot has no status. Games with no
    usable snapshot are absent from the result.
    """
    if avail.empty:
        return pd.DataFrame({"game_id": [], "player_id": [], "status": []})
    rows = pl.from_pandas(
        avail.merge(games[["game_id", "game_date"]], on="game_id").assign(
            game_date=lambda d: pd.to_datetime(d["game_date"]).dt.date
        )
    ).with_columns(pl.col("as_of").cast(pl.Datetime("us")), pl.col("game_date").cast(pl.Date))
    usable = usable_report_rows(rows, cfg.report)
    if usable.height == 0:
        return pd.DataFrame({"game_id": [], "player_id": [], "status": []})
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_latest"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_latest"))
    return snap.select(["game_id", "player_id", "status"]).unique().to_pandas()


# --------------------------------------------------------------------------- tokens


def build_token_table(pg: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """Per-player timeline of tokens: real rows plus 'gap' (missed) games.

    Gap games of player ``p`` between his rows ``j`` and ``j+1`` are the games of
    ``team(row j)`` strictly between the two dates in which he has no row. Only
    rows before the gap are consulted (never the target). Columns: player_id,
    game_id, team_id, date (int days), pg_row (-1 for gaps), has_row.
    """
    team_games: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for tid, sub in tg.groupby("team_id"):
        team_games[int(tid)] = (
            sub["game_date"].to_numpy().astype("datetime64[D]").astype(np.int64),
            sub["game_id"].to_numpy(),
        )
    pids = pg["player_id"].to_numpy()
    gids = pg["game_id"].to_numpy()
    tids = pg["team_id"].to_numpy()
    dates = pg["game_date"].to_numpy().astype("datetime64[D]").astype(np.int64)
    n = len(pg)
    bounds = np.flatnonzero(np.r_[True, pids[1:] != pids[:-1], True])
    o_pid: list[np.ndarray] = []
    o_gid: list[np.ndarray] = []
    o_tid: list[np.ndarray] = []
    o_date: list[np.ndarray] = []
    o_row: list[np.ndarray] = []
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        for j in range(a, b):
            o_pid.append(np.array([pids[j]]))
            o_gid.append(np.array([gids[j]], dtype=object))
            o_tid.append(np.array([tids[j]]))
            o_date.append(np.array([dates[j]]))
            o_row.append(np.array([j]))
            if j + 1 < b:
                td, tgid = team_games[int(tids[j])]
                lo = int(np.searchsorted(td, dates[j], side="right"))
                hi = int(np.searchsorted(td, dates[j + 1], side="left"))
                if hi > lo:
                    m = hi - lo
                    o_pid.append(np.full(m, pids[j]))
                    o_gid.append(tgid[lo:hi].astype(object))
                    o_tid.append(np.full(m, tids[j]))
                    o_date.append(td[lo:hi])
                    o_row.append(np.full(m, -1))
    if n == 0:
        return pd.DataFrame(columns=["player_id", "game_id", "team_id", "date", "pg_row"])
    tok = pd.DataFrame(
        {
            "player_id": np.concatenate(o_pid),
            "game_id": np.concatenate(o_gid),
            "team_id": np.concatenate(o_tid),
            "date": np.concatenate(o_date),
            "pg_row": np.concatenate(o_row),
        }
    )
    tok["has_row"] = tok["pg_row"] >= 0
    return tok


def _dnp_rotation(pg: pd.DataFrame, post: pd.DataFrame, cfg: SeqConfig) -> pd.DataFrame:
    """DNP rows with their as-of rotation status. Index = pg row."""
    d = pg.loc[~pg["played"], ["player_id", "game_id", "team_id", "game_date"]].copy()
    m = pd.merge_asof(
        d.sort_values("game_date").reset_index(names="pg_row"),
        post.rename(columns={"team_id": "team_post"}),
        on="game_date",
        by="player_id",
        direction="backward",
        allow_exact_matches=False,
    )
    m["rot"] = (m["n_post"].fillna(0) >= cfg.min_rotation_games) & (
        m["min_post"].fillna(0) >= cfg.rotation_min
    )
    m["usg"] = m["usg_post"].fillna(0.0)
    return m.set_index("pg_row").sort_index()


def build_token_features(
    pg: pd.DataFrame, tg: pd.DataFrame, tok: pd.DataFrame, dnp: pd.DataFrame
) -> np.ndarray:
    """(n_tok, F) float32 step features. ``same_team`` / ``days_ago`` stay 0 here
    (they depend on the target and are filled when windows are gathered)."""
    n = len(tok)
    feat = np.zeros((n, len(STEP_FEATURES)), dtype=np.float32)
    real = tok["has_row"].to_numpy()
    rows = tok["pg_row"].to_numpy()
    played = np.zeros(n, dtype=bool)
    played[real] = pg["played"].to_numpy()[rows[real]]
    feat[:, _SI["present"]] = played
    feat[:, _SI["absent"]] = ~played
    rr = rows[played]
    for name, scale in _STEP_SCALE.items():
        src = "minutes" if name == "min" else name
        feat[played, _SI[name]] = pg[src].to_numpy()[rr] / scale
    feat[played, _SI["starter"]] = pg["starter"].to_numpy()[rr]
    feat[played, _SI["usg_poss"]] = pg["usg_poss"].to_numpy()[rr] * 5.0
    feat[played, _SI["usg_box"]] = pg["usg_box"].to_numpy()[rr] * 5.0
    for src, dst in (
        ("zone_rim", "zone_rim"),
        ("zone_mid", "zone_mid"),
        ("zone_ab3", "zone_ab3"),
    ):
        feat[played, _SI[dst]] = pg[src].to_numpy()[rr]
    # team-game context of the token's game
    key = pd.MultiIndex.from_frame(tok[["game_id", "team_id"]])
    tgi = tg.set_index(["game_id", "team_id"])
    pos = tgi.index.get_indexer(key)
    ok = pos >= 0
    feat[ok, _SI["home"]] = tgi["is_home"].to_numpy()[pos[ok]]
    feat[ok, _SI["rest"]] = tgi["rest_days"].to_numpy()[pos[ok]] / 7.0
    feat[ok, _SI["b2b"]] = tgi["b2b"].to_numpy()[pos[ok]]
    feat[ok, _SI["opp_drtg"]] = (tgi["opp_drtg_asof"].to_numpy()[pos[ok]] - 113.0) / 8.0
    feat[ok, _SI["team_pace"]] = (tgi["pace_asof"].to_numpy()[pos[ok]] - 99.0) / 6.0
    # teammates OUT (rotation DNPs) in that game, excluding the token's own player
    rot = dnp[dnp["rot"]]
    agg = rot.groupby(["game_id", "team_id"]).agg(n_out=("usg", "size"), vac=("usg", "sum"))
    ai = agg.index.get_indexer(key)
    has = ai >= 0
    n_out = np.zeros(n)
    vac = np.zeros(n)
    n_out[has] = agg["n_out"].to_numpy()[ai[has]]
    vac[has] = agg["vac"].to_numpy()[ai[has]]
    own = dnp.reindex(rows[real])
    own_rot = own["rot"].fillna(False).to_numpy(dtype=bool)
    own_usg = own["usg"].fillna(0.0).to_numpy()
    n_out[real] -= own_rot
    vac[real] -= own_usg * own_rot
    feat[:, _SI["n_tm_out"]] = np.clip(n_out, 0, None) / 3.0
    feat[:, _SI["vac_tm"]] = np.clip(vac, 0, None) / 0.5
    return feat


# --------------------------------------------------------------------------- target context


def build_target_context(
    pg: pd.DataFrame, tg: pd.DataFrame, raw: RawInputs, post: pd.DataFrame, cfg: SeqConfig
) -> pd.DataFrame:
    """Pre-tip context for every PLAYED row (index = pg row). Floats, scaled."""
    t = pg[pg["played"]].copy()
    tgi = tg.set_index(["game_id", "team_id"])
    pos = tgi.index.get_indexer(pd.MultiIndex.from_frame(t[["game_id", "team_id"]]))
    c = pd.DataFrame(index=t.index)
    c["home"] = tgi["is_home"].to_numpy()[pos]
    c["rest"] = tgi["rest_days"].to_numpy()[pos] / 7.0
    c["b2b"] = tgi["b2b"].to_numpy()[pos]
    c["elo_margin"] = tgi["elo_margin"].to_numpy()[pos] / 10.0
    c["opp_drtg"] = (tgi["opp_drtg_asof"].to_numpy()[pos] - 113.0) / 8.0
    c["team_pace"] = (tgi["pace_asof"].to_numpy()[pos] - 99.0) / 6.0
    c["opp_pace"] = (tgi["opp_pace_asof"].to_numpy()[pos] - 99.0) / 6.0
    c["team_drtg"] = (tgi["drtg_asof"].to_numpy()[pos] - 113.0) / 8.0
    c["team_game_no"] = tgi["team_game_no"].to_numpy()[pos] / 82.0
    c["opp_id"] = tgi["opp_id"].to_numpy()[pos]

    snap = latest_pretip_status(raw.avail, raw.games, cfg)
    reported = set(snap["game_id"].unique()) if len(snap) else set()
    c["has_report"] = t["game_id"].isin(reported).astype(float)
    own = snap.rename(columns={"status": "own_status"})
    t2 = t[["game_id", "player_id"]].merge(own, on=["game_id", "player_id"], how="left")
    for s in REPORT_STATUSES:
        c[f"st_{s}"] = (t2["own_status"].to_numpy() == s).astype(float)

    # reported-OUT rotation teammates (as-of state; team = latest row before tonight)
    gdate = raw.games[["game_id", "game_date"]].assign(
        game_date=lambda d: pd.to_datetime(d["game_date"])
    )
    out_rows = snap[snap["status"] == "out"].merge(gdate, on="game_id")
    c["n_rep_out"] = 0.0
    c["vac_rep"] = 0.0
    c["n_return"] = 0.0
    c["vac_return"] = 0.0
    if len(out_rows):
        o = pd.merge_asof(
            out_rows.sort_values("game_date"),
            post.rename(columns={"team_id": "team_post"}),
            on="game_date",
            by="player_id",
            direction="backward",
            allow_exact_matches=False,
        )
        o = o[(o["n_post"].fillna(0) >= cfg.min_rotation_games)]
        o = o[o["min_post"].fillna(0) >= cfg.rotation_min]
        oa = o.groupby(["game_id", "team_post"]).agg(n=("usg_post", "size"), v=("usg_post", "sum"))
        ai = oa.index.get_indexer(pd.MultiIndex.from_frame(t[["game_id", "team_id"]]))
        hit = ai >= 0
        c.loc[c.index[hit], "n_rep_out"] = oa["n"].to_numpy()[ai[hit]]
        c.loc[c.index[hit], "vac_rep"] = oa["v"].to_numpy()[ai[hit]]
    # returning: rotation players who sat the team's previous game and are not out tonight
    dnp = _dnp_rotation(pg, post, cfg)
    prev = dnp[dnp["rot"]][["game_id", "team_id", "player_id", "usg"]]
    nxt = tg[["game_id", "team_id", "next_game_id"]].dropna(subset=["next_game_id"])
    prev = prev.merge(nxt, on=["game_id", "team_id"], how="inner")
    prev = prev.rename(columns={"game_id": "prev_game", "next_game_id": "game_id"})
    if len(snap):
        outs = snap[snap["status"] == "out"][["game_id", "player_id"]].assign(_out=1)
        prev = prev.merge(outs, on=["game_id", "player_id"], how="left")
        prev = prev[prev["_out"].isna()]
    ra = prev.groupby(["game_id", "team_id"]).agg(n=("usg", "size"), v=("usg", "sum"))
    ai = ra.index.get_indexer(pd.MultiIndex.from_frame(t[["game_id", "team_id"]]))
    hit = (ai >= 0) & (c["has_report"].to_numpy() > 0)
    c.loc[c.index[hit], "n_return"] = ra["n"].to_numpy()[ai[hit]]
    c.loc[c.index[hit], "vac_return"] = ra["v"].to_numpy()[ai[hit]]
    c["n_rep_out"] = c["n_rep_out"] / 3.0
    c["vac_rep"] = c["vac_rep"] / 0.5
    c["n_return"] = c["n_return"] / 2.0
    c["vac_return"] = c["vac_return"] / 0.3

    c["log_n_prior"] = np.log1p(t["n_prior"].to_numpy()) / 5.0
    c["log_n_season"] = np.log1p(t["n_season"].to_numpy()) / 5.0
    c["gap_days"] = t["gap_days"].to_numpy() / 30.0
    code = raw.static.assign(
        c=lambda d: d["position"].astype(str).str.slice(0, 1).str.upper()
    ).drop_duplicates("player_id")
    code = code.set_index("player_id")["c"]
    pc = t["player_id"].map(code)
    c["pos_g"] = (pc == "G").astype(float)
    c["pos_f"] = (pc == "F").astype(float)
    c["pos_c"] = (pc == "C").astype(float)
    c["base_min"] = t["base_min"].to_numpy() / 36.0
    c["starter10"] = t["base_starter10"].to_numpy()
    for s in STATS:
        c[f"base_{s}"] = t[f"base_{s}"].to_numpy() / float(STAT_SCALE[STATS.index(s)])
    return c


# --------------------------------------------------------------------------- export


@dataclass
class SeqExport:
    """Arrays for the model (``npz``) and the evaluation meta frame."""

    arrays: dict[str, np.ndarray]
    meta: pd.DataFrame
    step_features: tuple[str, ...] = STEP_FEATURES
    ctx_features: tuple[str, ...] = CTX_FEATURES
    window_game_ids: np.ndarray | None = None  # (N, K) object, debug only


def build_export(
    raw: RawInputs,
    cfg: SeqConfig | None = None,
    *,
    max_targets: int | None = None,
    keep_window_ids: bool = False,
    chunk: int = 20000,
) -> SeqExport:
    """Build sequences + context for every played target game in
    ``[min_target_season, max_season]``. ``max_targets`` keeps an even stride
    subsample of targets (histories are still full) for smoke runs."""
    cfg = cfg or SeqConfig()
    if cfg.max_season >= FROZEN_SEASON or int(raw.games["season"].max()) >= FROZEN_SEASON:
        raise ValueError(f"season {FROZEN_SEASON} is the frozen holdout; it is never exported")
    tg = team_game_table(raw.games, raw.poss_team, raw.elo, cfg)
    pg = player_game_table(raw, cfg)
    post = _post_state(pg, cfg)
    dnp = _dnp_rotation(pg, post, cfg)
    tok = build_token_table(pg, tg)
    feat = build_token_features(pg, tg, tok, dnp)
    ctx = build_target_context(pg, tg, raw, post, cfg)

    # team / opponent vocabularies (1-based; 0 = pad)
    teams = np.array(sorted(set(tg["team_id"].tolist())))
    tmap = {int(t): i + 1 for i, t in enumerate(teams)}
    tok_opp_team = tg.set_index(["game_id", "team_id"])["opp_id"]
    tok_opp = tok_opp_team.reindex(pd.MultiIndex.from_frame(tok[["game_id", "team_id"]]))
    tok_opp_idx = tok_opp.map(lambda v: tmap.get(int(v), 0) if pd.notna(v) else 0).to_numpy()

    # targets: played rows in range, as token positions
    sel = pg["played"] & (pg["season"] >= cfg.min_target_season) & (pg["season"] <= cfg.max_season)
    pg_idx = np.flatnonzero(sel.to_numpy())
    row_to_tok = np.full(len(pg), -1, dtype=np.int64)
    real = tok["has_row"].to_numpy()
    row_to_tok[tok["pg_row"].to_numpy()[real]] = np.flatnonzero(real)
    tpos_all = row_to_tok[pg_idx]
    order = np.argsort(pg["game_date"].to_numpy()[pg_idx], kind="stable")
    pg_idx, tpos_all = pg_idx[order], tpos_all[order]
    if max_targets is not None and len(pg_idx) > max_targets:
        keep = np.linspace(0, len(pg_idx) - 1, max_targets).astype(np.int64)
        pg_idx, tpos_all = pg_idx[keep], tpos_all[keep]
    n = len(pg_idx)

    pid_tok = tok["player_id"].to_numpy()
    date_tok = tok["date"].to_numpy()
    team_tok = tok["team_id"].to_numpy()
    gid_tok = tok["game_id"].to_numpy()
    seq = np.zeros((n, cfg.k, len(STEP_FEATURES)), dtype=np.float16)
    mask = np.zeros((n, cfg.k), dtype=np.uint8)
    opp_step = np.zeros((n, cfg.k), dtype=np.int16)
    win_ids = np.full((n, cfg.k), "", dtype=object) if keep_window_ids else None
    offs = np.arange(-cfg.k, 0)
    for a in range(0, n, chunk):
        sl = slice(a, min(a + chunk, n))
        tp = tpos_all[sl]
        w = tp[:, None] + offs[None, :]  # (m, K) token positions (oldest -> newest)
        wc = np.clip(w, 0, len(tok) - 1)
        valid = (w >= 0) & (pid_tok[wc] == pid_tok[tp][:, None])
        # the target's own token and anything later is excluded by construction
        assert (w < tp[:, None]).all()
        x = feat[wc].copy()
        x[..., _SI["same_team"]] = team_tok[wc] == team_tok[tp][:, None]
        days = (date_tok[tp][:, None] - date_tok[wc]).astype(np.float32)
        x[..., _SI["days_ago"]] = np.log1p(np.clip(days, 0, None)) / 6.0
        x[~valid] = 0.0
        seq[sl] = x.astype(np.float16)
        mask[sl] = valid
        opp_step[sl] = np.where(valid, tok_opp_idx[wc], 0)
        if win_ids is not None:
            win_ids[sl] = np.where(valid, gid_tok[wc], "")

    tgt = pg.iloc[pg_idx]
    cx = ctx.loc[tgt.index]
    ctx_arr = cx[list(CTX_FEATURES)].to_numpy(dtype=np.float32)
    ctx_arr = np.nan_to_num(ctx_arr, nan=0.0, posinf=0.0, neginf=0.0)
    y = tgt[list(STATS)].to_numpy(dtype=np.float32)
    base = tgt[[f"base_{s}" for s in STATS]].to_numpy(dtype=np.float32)
    team_idx = np.array([tmap.get(int(t), 0) for t in tgt["team_id"]], dtype=np.int16)
    opp_idx = np.array([tmap.get(int(t), 0) for t in cx["opp_id"]], dtype=np.int16)
    days = (tgt["game_date"].to_numpy().astype("datetime64[D]").astype(np.int64)).astype(np.int32)
    arrays: dict[str, np.ndarray] = {
        "seq": seq,
        "mask": mask,
        "opp_step": opp_step,
        "ctx": ctx_arr,
        "player_id": tgt["player_id"].to_numpy(dtype=np.int64),
        "team_idx": team_idx,
        "opp_idx": opp_idx,
        "date_days": days,
        "season": tgt["season"].to_numpy(dtype=np.int16),
        "game_id": tgt["game_id"].to_numpy(dtype="U12"),
        "y": y,
        "base": base,
        "n_prior": tgt["n_prior"].to_numpy(dtype=np.int32),
        "stat_scale": STAT_SCALE,
    }
    meta = pd.DataFrame(
        {
            "game_id": tgt["game_id"].to_numpy(),
            "player_id": tgt["player_id"].to_numpy(),
            "team_id": tgt["team_id"].to_numpy(),
            "game_date": tgt["game_date"].to_numpy(),
            "season": tgt["season"].to_numpy(),
            "n_prior": tgt["n_prior"].to_numpy(),
            "n_season": tgt["n_season"].to_numpy(),
            "team_game_no": (cx["team_game_no"].to_numpy() * 82.0).round().astype(int),
            "starter10": cx["starter10"].to_numpy(),
            "has_report": cx["has_report"].to_numpy(),
            "n_rep_out": (cx["n_rep_out"].to_numpy() * 3.0).round().astype(int),
            **{f"y_{s}": y[:, i] for i, s in enumerate(STATS)},
            **{f"base_{s}": base[:, i] for i, s in enumerate(STATS)},
        }
    )
    return SeqExport(arrays, meta, window_game_ids=win_ids)


def write_export(exp: SeqExport, out_dir: str | Path) -> tuple[Path, Path]:
    """Write ``seq_props.npz`` (job input) and ``seq_props_meta.parquet`` (eval)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arrays = dict(exp.arrays)
    arrays["step_features"] = np.array(exp.step_features)
    arrays["ctx_features"] = np.array(exp.ctx_features)
    arrays["spec"] = np.array(
        json.dumps({"k": int(arrays["seq"].shape[1]), "stats": list(STATS), "version": 1})
    )
    npz = out / "seq_props.npz"
    np.savez_compressed(npz, **arrays)  # type: ignore[arg-type]
    meta_path = out / "seq_props_meta.parquet"
    exp.meta.to_parquet(meta_path, index=False)
    return npz, meta_path


def export_sequences(
    db_path: str = "nba.duckdb",
    out_dir: str = "data/colab/seq_props",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    max_targets: int | None = None,
) -> dict[str, Any]:
    """Real-data entrypoint (read-only DB). Seasons 2022-2024 only; 2025 never loaded."""
    import yaml

    from nba.props.context_residual import asof_elo_margin

    raw = load_raw(db_path, max_season=2024)
    params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    g = pl.from_pandas(raw.games).with_columns(pl.col("game_date").cast(pl.Date))
    raw.elo = asof_elo_margin(g, params).to_pandas()
    exp = build_export(raw, max_targets=max_targets)
    npz, meta = write_export(exp, out_dir)
    n = len(exp.meta)
    return {
        "npz": str(npz),
        "meta": str(meta),
        "n_targets": n,
        "seasons": sorted(exp.meta["season"].unique().tolist()),
        "npz_mb": round(npz.stat().st_size / 1e6, 1),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--out", default="data/colab/seq_props")
    ap.add_argument("--elo-config", default="configs/mov_elo_tuned.yaml")
    ap.add_argument("--max-targets", type=int, default=None)
    a = ap.parse_args(argv)
    print(json.dumps(export_sequences(a.db, a.out, a.elo_config, a.max_targets), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
