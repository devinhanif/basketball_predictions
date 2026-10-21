# ruff: noqa: E501
"""MATCHUP_AVAILABILITY as-of features, archetype layer and loaders (rule: docs/prereg/MATCHUP_AVAILABILITY.md, DRAFT).

Read-only: every DuckDB is opened ``read_only=True`` (through ``preseason_role_prior.connect``), every SQL carries ``season <= 2024``,
season 2025 is never queried. ``player_game_matchups`` is POST-GAME data for the game it belongs to: it enters a feature of game g only
through games strictly before g (``decayed_asof`` masks every event with day >= the query day; tested with a planted future).
The OUT flags are the usable pre-tip official-report OUT set the production model sees (``flagged_from_availability`` with
``ReportTriggerConfig(tip_source="real")``, the default), never a box-score absence.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from nba.features.injury_report import ReportTriggerConfig
from nba.props.context_residual import flagged_from_availability
from research.coldstart.archetypes import encode_position_fractions, fit_archetypes
from research.eval import matchup_availability_rule as rule
from research.eval.matchup_availability_rule import pick_primary, slice_labels

_EPOCH = np.datetime64("1970-01-01")


def to_day(s: pd.Series) -> np.ndarray:
    d = pd.to_datetime(s).to_numpy().astype("datetime64[D]")
    return np.asarray((d - _EPOCH) / np.timedelta64(1, "D"), dtype="int64")


# --------------------------------------------------------------------------- loaders (read-only, season <= 2024)


def read_matchups(c: Any) -> pd.DataFrame:
    """Regular-season matchup rows 2022..2024 with the game date and season."""
    return c.execute(
        "select m.game_id, g.game_date, g.season, m.off_player_id, m.def_player_id, m.team_id off_team, m.def_team_id def_team, "
        "m.matchup_minutes mm, m.partial_possessions poss, m.player_points pts "
        "from player_game_matchups m join games g using(game_id) "
        "where substr(m.game_id,1,3)='002' and g.season between ? and ? and g.season <= ?",
        [rule.FIRST_MATCHUP_SEASON, rule.MAX_SEASON, rule.MAX_SEASON],
    ).df()


def read_games(c: Any) -> pd.DataFrame:
    return c.execute(
        "select game_id, game_date, season, home_team, away_team from games where season <= ? and substr(game_id,1,3)='002'",
        [rule.MAX_SEASON],
    ).df()


def read_flagged(c: Any, games: pd.DataFrame) -> dict[str, set[int]]:
    """Latest usable pre-tip official-report OUT set per game (same function, same config, as the production model)."""
    g = pl.from_pandas(games.assign(game_date=pd.to_datetime(games.game_date).dt.date))
    c.register("_allowed_games", games[["game_id"]])
    avail = c.execute(
        "select game_id, player_id, status, as_of, source from player_availability where game_id in (select game_id from _allowed_games)"
    ).pl()
    flagged, _info = flagged_from_availability(
        avail, g, ReportTriggerConfig(tip_source="real"), max_season=rule.MAX_SEASON
    )
    return flagged


def read_player_season_stats(c: Any, season: int) -> pd.DataFrame:
    rule.check_season(season)
    return c.execute(
        "select s.player_id, sum(s.minutes) mins, sum(s.pts) pts, sum(s.reb) reb, sum(s.ast) ast, sum(s.fg3m) fg3m, sum(s.fg3a) fg3a, "
        "sum(s.fga) fga, sum(s.fta) fta, sum(s.tov) tov, sum(s.stl) stl, sum(s.blk) blk, sum(s.pf) pf, sum(s.oreb) oreb, sum(s.dreb) dreb "
        "from player_game_stats s join games g using(game_id) "
        "where substr(s.game_id,1,3)='002' and g.season = ? and g.season <= ? and s.minutes > 0 group by 1",
        [season, rule.MAX_SEASON],
    ).df()


def read_static(c: Any) -> pd.DataFrame:
    return c.execute("select player_id, position, height_in, weight_lb from players_static").df()


# --------------------------------------------------------------------------- as-of decayed sums


def decayed_asof(
    events: pd.DataFrame, queries: pd.DataFrame, val_cols: list[str], decay: float = rule.DECAY
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For each query (ent, day, season) the decayed sum over the entity's events STRICTLY before ``day``, in the query's season or the
    previous one, weighted ``decay ** (number of the entity's games between the event and the query)``.

    events: ent, oth, day, season, *val_cols (one row per ent-oth-game). queries: ent, day, season (index = qidx).
    Returns (long: qidx, oth, *val_cols with nonzero weight, hist: qidx, nhist = games in the window before the query).
    """
    groups = {k: v for k, v in events.groupby("ent", sort=False)}
    outs: list[pd.DataFrame] = []
    hist = np.zeros(len(queries), dtype=int)
    q = queries.reset_index(drop=True)
    for e, qg in q.groupby("ent", sort=False):
        ev = groups.get(e)
        if ev is None:
            continue
        days_u, dinv = np.unique(ev.day.to_numpy(), return_inverse=True)
        season_u = np.zeros(len(days_u), dtype=int)
        season_u[dinv] = ev.season.to_numpy()
        oth_u, oinv = np.unique(ev.oth.to_numpy(), return_inverse=True)
        X = {v: np.zeros((len(days_u), len(oth_u))) for v in val_cols}
        for v in val_cols:
            np.add.at(X[v], (dinv, oinv), np.nan_to_num(ev[v].to_numpy(float)))
        qd = qg.day.to_numpy()
        qs = qg.season.to_numpy()
        pos = np.searchsorted(days_u, qd, side="left")
        idx = np.arange(len(days_u))[None, :]
        mask = (idx < pos[:, None]) & (season_u[None, :] >= qs[:, None] - 1)
        W = np.where(mask, decay ** np.clip(pos[:, None] - 1 - idx, 0, None), 0.0)
        hist[qg.index.to_numpy()] = mask.sum(1)
        R = {v: W @ X[v] for v in val_cols}
        anyv = np.zeros_like(R[val_cols[0]], dtype=bool)
        for v in val_cols:
            anyv |= R[v] != 0
        qi, oi = np.nonzero(anyv)
        if len(qi) == 0:
            continue
        d = {"qidx": qg.index.to_numpy()[qi], "oth": oth_u[oi]}
        for v in val_cols:
            d[v] = R[v][qi, oi]
        outs.append(pd.DataFrame(d))
    long = (
        pd.concat(outs, ignore_index=True)
        if outs
        else pd.DataFrame({"qidx": [], "oth": [], **{v: [] for v in val_cols}})
    )
    return long, pd.DataFrame({"qidx": np.arange(len(q)), "nhist": hist})


def team_asof(matchups: pd.DataFrame) -> pd.DataFrame:
    """(player, day, team) from the matchup table, both sides: used to check a flagged player is on tonight's opposing team."""
    a = matchups[["off_player_id", "game_date", "off_team"]].rename(
        columns={"off_player_id": "player", "off_team": "team"}
    )
    b = matchups[["def_player_id", "game_date", "def_team"]].rename(
        columns={"def_player_id": "player", "def_team": "team"}
    )
    t = pd.concat([a, b]).drop_duplicates(["player", "game_date"])
    t = t.assign(
        day=to_day(t.game_date).astype("int64"),
        player=t.player.astype("int64"),
        team=t.team.astype("int64"),
    )
    return t[["player", "day", "team"]].sort_values("day").reset_index(drop=True)


_KEY = 100_000  # day < 100000, so player * _KEY + day is a unique sortable key


def team_keys(ta: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    k = ta.player.to_numpy("int64") * _KEY + ta.day.to_numpy("int64")
    order = np.argsort(k, kind="stable")
    return k[order], ta.team.to_numpy("int64")[order]


def team_at(
    keys: tuple[np.ndarray, np.ndarray], players: np.ndarray, days: np.ndarray
) -> np.ndarray:
    """Last known team of each player STRICTLY before ``days`` (from the matchup table); -1 when none."""
    k, t = keys
    p = np.asarray(players, "int64")
    q = p * _KEY + np.asarray(days, "int64")
    pos = np.searchsorted(k, q, side="left") - 1
    ok = pos >= 0
    ok[ok] = (k[pos[ok]] // _KEY) == p[ok]
    out = np.full(len(q), -1, dtype="int64")
    out[ok] = t[pos[ok]]
    return out


def out_opposing(pairs: pd.DataFrame, flagged: dict[str, set[int]], ta: pd.DataFrame) -> pd.Series:
    """pairs: game_id, oth, day, opp_team. True iff ``oth`` is in tonight's usable OUT set AND his last known team before tonight is the
    opposing team. Returns a boolean Series on pairs.index. Unverifiable team (no earlier game) -> False."""
    fl = pd.DataFrame([(g, p) for g, ps in flagged.items() for p in ps], columns=["game_id", "oth"])
    fl["is_out"] = True
    p = pairs.reset_index().merge(fl, on=["game_id", "oth"], how="left")
    p["is_out"] = p.is_out.fillna(False).astype(bool)
    sub = p[p.is_out]
    res = pd.Series(False, index=pairs.index)
    if len(sub):
        t = team_at(team_keys(ta), sub.oth.to_numpy(), sub.day.to_numpy())
        ok = t == sub.opp_team.to_numpy("int64")
        res.loc[sub["index"].to_numpy()[ok]] = True
    return res


# --------------------------------------------------------------------------- archetype layer (fixed k-means, as-of the PRIOR season)

OFF_FEATURES = ["pts36", "ast36", "fg3a36", "fta36", "tov36", "oreb36", "height_in"]
DEF_FEATURES = ["reb36", "dreb36", "blk36", "stl36", "pf36", "height_in", "weight_lb"]


def archetype_features(stats: pd.DataFrame, static: pd.DataFrame) -> pd.DataFrame:
    s = stats[stats.mins >= rule.ARCH_MIN_MINUTES].merge(static, on="player_id", how="inner")
    s = s.dropna(subset=["height_in", "weight_lb"])
    for c in ("pts", "ast", "fg3a", "fta", "tov", "oreb", "reb", "dreb", "blk", "stl", "pf"):
        s[f"{c}36"] = 36.0 * s[c] / s.mins
    pos = np.array([encode_position_fractions(p) for p in s.position], dtype=float).reshape(-1, 3)
    for k, nm in enumerate(("is_guard", "is_forward", "is_center")):
        s[nm] = pos[:, k]
    return s.reset_index(drop=True)


def assign_archetypes(feat: pd.DataFrame, cols: list[str], k: int) -> pd.Series:
    """Fixed-k k-means on standardized ``cols`` + position fractions via the project's ``fit_archetypes`` (seed fixed, no tuning).
    Returns player_id -> label (0..k-1), ordered by cluster mean of the first column so labels are stable; absent players are -1 by reindex."""
    use = cols + ["is_guard", "is_forward", "is_center"]
    X = feat[use].to_numpy(float)
    model = fit_archetypes(X, feat[cols[0]].to_numpy(float), k, seed=rule.ARCH_SEED)
    lab = model.kmeans.predict(model.scaler.transform(X))
    order = (
        pd.Series(feat[cols[0]].to_numpy(float)).groupby(lab).mean().sort_values().index.tolist()
    )
    remap = {old: new for new, old in enumerate(order)}
    return pd.Series([remap[v] for v in lab], index=feat.player_id.to_numpy(), name="arch")


def build_archetypes(c: Any, season: int) -> tuple[pd.Series, pd.Series]:
    """(offensive, defensive) archetype of each player, clustered on season ``season - 1`` only (strictly before the target season)."""
    rule.check_season(season)
    feat = archetype_features(read_player_season_stats(c, season - 1), read_static(c))
    return assign_archetypes(feat, OFF_FEATURES, rule.K_OFF), assign_archetypes(
        feat, DEF_FEATURES, rule.K_DEF
    )


def pair_rates(
    mu: pd.DataFrame, off_arch: pd.Series, def_arch: pd.Series, prior_season: int
) -> tuple[pd.DataFrame, float]:
    """Archetype-vs-archetype allowed points per partial possession from the PRIOR season's matchups; thin cells fall back to the league rate."""
    m = mu[mu.season == prior_season]
    lg = float(m.pts.sum() / m.poss.sum())
    a = m.off_player_id.map(off_arch).fillna(-1).astype(int)
    b = m.def_player_id.map(def_arch).fillna(-1).astype(int)
    t = (
        m.assign(a=a, b=b)
        .groupby(["a", "b"])
        .agg(pts=("pts", "sum"), poss=("poss", "sum"))
        .reset_index()
    )
    t["rate"] = np.where(t.poss >= rule.MIN_ARCH_PAIR_POSS, t.pts / t.poss, lg)
    return t[["a", "b", "poss", "rate"]], lg


def shrunk_excess(pts: float, exp_pts: float, poss: float, prior: float = rule.PRIOR_POSS) -> float:
    """A defender's allowed points per possession ABOVE what his archetype pair rates predict for the scorers he faced, shrunk to zero
    (to the archetype) with a fixed prior weight of ``prior`` partial possessions."""
    return (pts - exp_pts) / (poss + prior)


# --------------------------------------------------------------------------- the slice frame (gate and, later, the arms)


def slice_frame(
    rows: pd.DataFrame,
    mu: pd.DataFrame,
    games: pd.DataFrame,
    flagged: dict[str, set[int]],
    season: int,
) -> pd.DataFrame:
    """One row per A0 row of ``season``: as-of primary defender faced and primary assignment, whether each is OUT on the pre-tip report,
    the freed share of his defensive time, history counts. Nothing here reads tonight's outcome or tonight's minutes."""
    rule.check_season(season)
    r = rows.reset_index(drop=True).copy()
    r = r.merge(games[["game_id", "home_team", "away_team"]], on="game_id", how="left")
    r["opp_team"] = np.where(r.team_id == r.home_team, r.away_team, r.home_team).astype("int64")
    r["day"] = to_day(r.game_date)
    q = pd.DataFrame({"ent": r.player_id.astype("int64"), "day": r.day, "season": season})
    ev_o = mu.assign(
        ent=mu.off_player_id.astype("int64"),
        oth=mu.def_player_id.astype("int64"),
        day=to_day(mu.game_date),
    )
    ev_d = mu.assign(
        ent=mu.def_player_id.astype("int64"),
        oth=mu.off_player_id.astype("int64"),
        day=to_day(mu.game_date),
    )
    ta = team_asof(mu)
    tkeys = team_keys(ta)
    out: dict[str, Any] = {}
    for side, ev in (("D", ev_o), ("O", ev_d)):
        long, hist = decayed_asof(ev[["ent", "oth", "day", "season", "mm"]], q, ["mm"])
        long = long.rename(columns={"mm": "w"})
        qd = r.day.to_numpy()[long.qidx.to_numpy()]
        qo = r.opp_team.to_numpy()[long.qidx.to_numpy()]
        long = long[team_at(tkeys, long.oth.to_numpy(), qd) == qo].reset_index(drop=True)
        top = pick_primary(long)
        r[f"nhist_{side}"] = hist.nhist.to_numpy()
        top = top.set_index("qidx").reindex(range(len(r)))
        ok = (r[f"nhist_{side}"] >= rule.MIN_HIST_GAMES).to_numpy() & (
            top.primary_share >= rule.PRIMARY_SHARE_MIN
        ).to_numpy()
        r[f"primary_{side}"] = top.primary.to_numpy()
        r[f"primary_share_{side}"] = top.primary_share.to_numpy()
        r[f"has_primary_{side}"] = ok
        pr = pd.DataFrame(
            {
                "game_id": r.game_id,
                "oth": r[f"primary_{side}"].fillna(-1).astype("int64"),
                "day": r.day,
                "opp_team": r.opp_team,
            }
        )
        o = out_opposing(pr, flagged, ta).to_numpy()
        r[f"primary_out_{side}"] = ok & o
        # freed share: share of the as-of mix whose members are OUT tonight (and on the opposing team)
        tot = long.groupby("qidx").w.transform("sum")
        lp = long.assign(
            share=long.w / tot,
            game_id=r.game_id.to_numpy()[long.qidx.to_numpy()],
            day=r.day.to_numpy()[long.qidx.to_numpy()],
            opp_team=r.opp_team.to_numpy()[long.qidx.to_numpy()],
        )
        lo = out_opposing(lp[["game_id", "oth", "day", "opp_team"]], flagged, ta)
        freed = lp.assign(f=np.where(lo, lp.share, 0.0)).groupby("qidx").f.sum()
        r[f"freed_share_{side}"] = freed.reindex(range(len(r))).fillna(0.0).to_numpy()
        out[side] = long
    r["has_report"] = r.game_id.isin(list(flagged.keys())).astype(bool)
    r["slice"] = slice_labels(r.primary_out_D.to_numpy(), r.primary_out_O.to_numpy())
    return r
