# ruff: noqa: E501
# mypy: ignore-errors
"""MINUTES_DECISION minimum-data gate (rule: docs/prereg/MINUTES_DECISION.md, DRAFT). No arm is fitted; no scored outcome is read.

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -I -W ignore -m research.eval.minutes_decision_gate \
        [--out reports/prereg_minutes_decision/gate.json] [--seeds 10] [--no-power]

Read-only (``read_only=True``), ``season between 2022 and 2024`` in every SQL; season 2025 is never queried. What is read:
* ``games``, ``team_coach_games``, ``player_game_stats`` (minutes > 0 rows), ``stints``, ``possessions`` for 2022-24: ONLY to build
  as-of states (coach of the previous team game, coach history counts, familiarity counts, check-in events). A row's states use
  strictly earlier games; the planted-future check below proves it for the coach and familiarity counts.
* ``reports/minutes_v2/minutes_scores.npz``: ONLY ``row_gid, row_pid, row_season, row_min10, row_starter10, row_n_out_rot,
  row_b2b, row_n_prior`` (as-of columns) and ``M1_mean, M1_q10, M1_q90`` (a served forecast grid for the noise arm).
  ``row_y``, ``row_starter_act`` and every ``*_crps`` / ``*_pit`` / ``*_tll`` array are never indexed.
* Final scores of EARLIER games enter the coach margin-cell counts (as-of facts, never the target game's).
Power is a label-free noise arm (truth drawn from M1's own approximate grid), as in ROLE_CHANGEPOINT G6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(os.environ.get("NBA_DATA_ROOT", "."))
OUT = Path("reports/prereg_minutes_decision/gate.json")
SELECT, REPORT = 2023, 2024
# d872dc7 back-filled these after the stored A0 / minutes tables were written (DECISIONS 2026-10-23 21:20)
BACKFILLED_AFTER_A0 = ("0022400147", "0022400621", "0022400633", "0022401229", "0022401230")
ROLE_COLS = (
    "row_gid",
    "row_pid",
    "row_season",
    "row_min10",
    "row_starter10",
    "row_n_out_rot",
    "row_b2b",
    "row_n_prior",
)
GRID_COLS = ("M1_mean", "M1_q10", "M1_q90")
WITH_NEW = 20  # pc_with_n below this = a new player-coach relationship
SHOOTER_3PA_PER_MIN = 0.20  # as-of 3PA per minute (7.2 per 36) for a "bench shooter"
COLD_GAP = 0.05  # team in-game 3P% at least 5 pp below its as-of season rate
MIN_TEAM_3PA = 6
UNSTABLE_Q = 0.67  # rs_sd10 cut = this quantile of 2022 rows (as-of, label-free)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def sha8(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


# --------------------------------------------------------------------------- load


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)


def load(c: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    bf = ",".join(f"'{g}'" for g in BACKFILLED_AFTER_A0)
    games = c.execute(
        f"select game_id, game_date, season, home_team, away_team, home_pts, away_pts from games "
        f"where season between 2022 and 2024 and game_id not in ({bf})"
    ).df()
    assert games.season.max() <= 2024
    tcg = c.execute(
        f"select t.game_id, t.team_id, t.coach_name from team_coach_games t join games g using (game_id) "
        f"where g.season between 2022 and 2024 and t.game_id not in ({bf})"
    ).df()
    box = c.execute(
        f"select b.game_id, b.player_id, b.team_id, b.minutes, b.fg3a, b.fga, b.fta, b.tov, coalesce(b.starter, false) as starter "
        f"from player_game_stats b join games g using (game_id) "
        f"where g.season between 2022 and 2024 and b.minutes > 0 and b.game_id not in ({bf})"
    ).df()
    games["game_date"] = pd.to_datetime(games.game_date)
    for col in ("home_team", "away_team"):
        games[col] = games[col].astype("int64")
    tcg["team_id"] = tcg.team_id.astype("int64")
    for col in ("player_id", "team_id"):
        box[col] = box[col].astype("int64")
    return {"games": games, "tcg": tcg, "box": box}


def team_games(games: pd.DataFrame, tcg: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team) in game order with the actual coach, the AS-OF coach (coach of the team's previous game; season
    opener: the reference's first coach of that season, an offseason hire being public before the opener), final margin, b2b."""
    h = games.assign(
        team_id=games.home_team, opp_id=games.away_team, margin=games.home_pts - games.away_pts
    )
    a = games.assign(
        team_id=games.away_team, opp_id=games.home_team, margin=games.away_pts - games.home_pts
    )
    tg = pd.concat([h, a])[["game_id", "game_date", "season", "team_id", "opp_id", "margin"]]
    tg = (
        tg.merge(tcg, on=["game_id", "team_id"], how="left")
        .sort_values(["team_id", "game_date", "game_id"])
        .reset_index(drop=True)
    )
    tg["prev_coach"] = tg.groupby("team_id").coach_name.shift(1)
    tg["prev_season"] = tg.groupby("team_id").season.shift(1)
    tg["prev_date"] = tg.groupby("team_id").game_date.shift(1)
    first = tg.prev_season != tg.season
    season_first_coach = tg.groupby(["team_id", "season"]).coach_name.transform("first")
    tg["asof_coach"] = np.where(first, season_first_coach, tg.prev_coach)
    tg["asof_rule"] = np.where(first, "season_opener_reference", "previous_game")
    tg["b2b"] = (tg.game_date - tg.prev_date).dt.days.eq(1)
    tg["abs_margin"] = tg.margin.abs()
    return tg


# --------------------------------------------------------------------------- as-of coach and familiarity counts


def coach_history(tg: pd.DataFrame) -> pd.DataFrame:
    """Per team-game: the as-of coach's EARLIER team-games (any team, strictly earlier date) in total and per state cell."""
    hist = tg[["game_date", "coach_name", "abs_margin", "b2b"]].rename(columns={"coach_name": "hc"})
    hist = hist.assign(
        blow20=hist.abs_margin >= 20,
        mid=(hist.abs_margin >= 10) & (hist.abs_margin < 20),
        close=hist.abs_margin < 10,
    )
    hist = hist.sort_values("game_date")
    # cumulative counts per coach by date, strictly before
    agg = hist.groupby(["hc", "game_date"]).agg(
        n=("hc", "size"),
        blow20=("blow20", "sum"),
        mid=("mid", "sum"),
        close=("close", "sum"),
        b2b=("b2b", "sum"),
    )
    agg = agg.groupby(level=0).cumsum().reset_index()
    out = tg[["game_id", "team_id", "game_date", "asof_coach"]].copy()
    res = []
    for coach, sub in out.groupby("asof_coach", dropna=False):
        h = agg[agg.hc == coach].sort_values("game_date")
        s = sub.sort_values("game_date")
        if len(h) == 0 or pd.isna(coach):
            z = s.assign(ch_n=0, ch_blow20=0, ch_mid=0, ch_close=0, ch_b2b=0)
        else:
            m = pd.merge_asof(
                s,
                h.rename(columns={"game_date": "hdate"}),
                left_on="game_date",
                right_on="hdate",
                allow_exact_matches=False,
                direction="backward",
            )
            z = s.assign(
                ch_n=m.n.fillna(0).to_numpy(),
                ch_blow20=m.blow20.fillna(0).to_numpy(),
                ch_mid=m.mid.fillna(0).to_numpy(),
                ch_close=m.close.fillna(0).to_numpy(),
                ch_b2b=m.b2b.fillna(0).to_numpy(),
            )
        res.append(z)
    return pd.concat(res)[
        ["game_id", "team_id", "ch_n", "ch_blow20", "ch_mid", "ch_close", "ch_b2b"]
    ]


def familiarity(rows: pd.DataFrame, box: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """Per scored row: pc_with_n = the player's earlier played games (strictly earlier date) whose actual coach is tonight's as-of
    coach; pc_against_n = earlier played games against a team whose actual coach is tonight's as-of coach. Window: 2022-10 onward."""
    b = box.merge(
        tg[["game_id", "team_id", "game_date", "coach_name", "opp_id"]],
        on=["game_id", "team_id"],
        how="left",
    )
    oppc = tg[["game_id", "team_id", "coach_name"]].rename(
        columns={"team_id": "opp_id", "coach_name": "opp_coach"}
    )
    b = b.merge(oppc, on=["game_id", "opp_id"], how="left")
    w = b.groupby(["player_id", "coach_name", "game_date"]).size().rename("n").reset_index()
    a = b.groupby(["player_id", "opp_coach", "game_date"]).size().rename("n").reset_index()
    w["c"] = w.groupby(["player_id", "coach_name"]).n.cumsum()
    a["c"] = a.groupby(["player_id", "opp_coach"]).n.cumsum()
    r = rows[["game_id", "player_id", "game_date", "asof_coach"]].copy()
    r["_i"] = np.arange(len(r))

    def asof_count(cum: pd.DataFrame, key: str) -> np.ndarray:
        cum = cum.rename(columns={key: "asof_coach", "game_date": "hdate"}).sort_values("hdate")
        left = r.dropna(subset=["asof_coach"]).sort_values("game_date")
        m = pd.merge_asof(
            left,
            cum[["player_id", "asof_coach", "hdate", "c"]],
            left_on="game_date",
            right_on="hdate",
            by=["player_id", "asof_coach"],
            allow_exact_matches=False,
            direction="backward",
        )
        v = np.zeros(len(r))
        v[m._i.to_numpy()] = m.c.fillna(0).to_numpy()
        return v

    r["pc_with_n"] = asof_count(w, "coach_name")
    r["pc_against_n"] = asof_count(a, "opp_coach")
    return r[["game_id", "player_id", "pc_with_n", "pc_against_n"]]


def movers(rows: pd.DataFrame, box: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """T500-style slice: offseason mover = first team of season S differs from the last team of S-1 (2022-24 boxes; 2023/2024 rows only);
    in-season arrival = row's team is not the player's first team of the season; gwt = earlier games with this team this season."""
    b = box.merge(games[["game_id", "game_date", "season"]], on="game_id").sort_values(
        ["player_id", "game_date", "game_id"]
    )
    ps = (
        b.groupby(["player_id", "season"])
        .agg(first_team=("team_id", "first"), last_team=("team_id", "last"))
        .reset_index()
    )
    prev = ps.assign(season=ps.season + 1).rename(columns={"last_team": "prev_last"})[
        ["player_id", "season", "prev_last"]
    ]
    ps = ps.merge(prev, on=["player_id", "season"], how="left")
    b["gwt"] = b.groupby(["player_id", "season", "team_id"]).cumcount()
    r = rows.merge(ps, on=["player_id", "season"], how="left").merge(
        b[["player_id", "game_id", "gwt"]], on=["player_id", "game_id"], how="left"
    )
    isnew = r.team_id.to_numpy() == r.first_team.to_numpy()
    has_prev = r.prev_last.notna().to_numpy()
    diff = (r.prev_last != r.first_team).to_numpy()
    r["slice_move"] = np.where(
        has_prev & isnew & diff,
        "mover",
        np.where(~isnew, "inseason_arrival", np.where(has_prev, "stayer", "no_prev_2022plus")),
    )
    return r[["game_id", "player_id", "slice_move", "gwt"]]


def role_stability(box: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """rs_sd10: sd of the player's minutes over his last 10 played games (strictly earlier); rs_n: games in that window."""
    b = box.merge(games[["game_id", "game_date", "season"]], on="game_id").sort_values(
        ["player_id", "game_date", "game_id"]
    )
    g = b.groupby("player_id").minutes
    b["rs_sd10"] = g.transform(lambda s: s.shift(1).rolling(10, min_periods=5).std())
    return b[["game_id", "player_id", "season", "rs_sd10"]]


# --------------------------------------------------------------------------- situational check-ins (D2 counts only)


def checkin_counts(
    c: duckdb.DuckDBPyConnection, box: pd.DataFrame, games: pd.DataFrame, starter10: pd.DataFrame
) -> dict[str, Any]:
    """Count D2 events: a bench player (as-of starter10 < 0.5 where scored; else box-starter share of prior 10 < 0.5) with as-of 3PA/min
    >= 0.20 (prior 40 played games, >= 10 games) enters mid-period (stint start, not a period opener) while his team has >= 6 3PA in the
    game so far and its in-game 3P% is >= 5 pp below its as-of season 3P% (prior games this season, >= 5). Controls: same players, team at
    or above its rate. No outcome after the check-in is read."""
    out: dict[str, Any] = {}
    b = box.merge(games[["game_id", "game_date", "season"]], on="game_id").sort_values(
        ["player_id", "game_date", "game_id"]
    )
    gp = b.groupby("player_id")
    b["p3a40"] = gp.fg3a.transform(
        lambda s: s.shift(1).rolling(40, min_periods=10).sum()
    ) / gp.minutes.transform(lambda s: s.shift(1).rolling(40, min_periods=10).sum())
    b["st10"] = gp.starter.transform(
        lambda s: s.astype(float).shift(1).rolling(10, min_periods=5).mean()
    )
    tb = (
        c.execute(
            "select b.game_id, b.team_id, sum(b.fg3m) m3, sum(b.fg3a) a3 from player_game_stats b join games g using (game_id) "
            "where g.season between 2022 and 2024 group by 1, 2"
        )
        .df()
        .merge(games[["game_id", "game_date", "season"]], on="game_id")
        .sort_values(["team_id", "game_date", "game_id"])
    )
    tb["team_id"] = tb.team_id.astype("int64")
    gt = tb.groupby(["team_id", "season"])
    tb["seas3"] = gt.m3.transform(lambda s: s.shift(1).cumsum()) / gt.a3.transform(
        lambda s: s.shift(1).cumsum()
    )
    tb["seas_n"] = gt.cumcount()
    for S in (SELECT, REPORT):
        st = c.execute(
            "select s.game_id, s.team_id, s.period, s.start_clock, s.stint_idx, s.players from stints s join games g using (game_id) "
            "where g.season = ? and g.season <= 2024 and s.period <= 4 order by s.game_id, s.team_id, s.stint_idx",
            [S],
        ).df()
        po = c.execute(
            "select p.game_id, p.off_team as team_id, p.period, p.clock_start, p.outcome from possessions p join games g using (game_id) "
            "where g.season = ? and g.season <= 2024 and p.period <= 4 and p.shot_zone = 'above3' and p.outcome in ('FGM3', 'FGA_miss')",
            [S],
        ).df()
        po["team_id"] = po.team_id.astype("int64")
        st["team_id"] = st.team_id.astype("int64")
        po["t"] = (po.period - 1) * 720 + (720 - po.clock_start)
        po = po.sort_values(["game_id", "team_id", "t"])
        po["c3a"] = po.groupby(["game_id", "team_id"]).cumcount() + 1
        po["c3m"] = po.groupby(["game_id", "team_id"]).outcome.transform(
            lambda s: (s == "FGM3").cumsum()
        )
        st["t"] = (st.period - 1) * 720 + (720 - st.start_clock)
        rows = []
        for (gid, tid), sub in st.groupby(["game_id", "team_id"], sort=False):
            prev: set[int] = set()
            for clk, t, pl in zip(sub.start_clock, sub.t, sub.players, strict=True):
                cur = {int(x) for x in pl}
                if clk < 719.5:
                    for p in cur - prev:
                        rows.append((gid, tid, p, t))
                prev = cur
        ev = pd.DataFrame(rows, columns=["game_id", "team_id", "player_id", "t"]).sort_values("t")
        ev = pd.merge_asof(
            ev,
            po[["game_id", "team_id", "t", "c3a", "c3m"]].sort_values("t"),
            on="t",
            by=["game_id", "team_id"],
            allow_exact_matches=False,
            direction="backward",
        )
        ev = ev.merge(
            b[["game_id", "player_id", "p3a40", "st10"]], on=["game_id", "player_id"], how="left"
        )
        ev = ev.merge(
            tb[["game_id", "team_id", "seas3", "seas_n"]], on=["game_id", "team_id"], how="left"
        )
        ev = ev.merge(starter10, on=["game_id", "player_id"], how="left")
        st_asof = ev.row_starter10.fillna(ev.st10)
        shooter = (
            (ev.p3a40 >= SHOOTER_3PA_PER_MIN)
            & (st_asof < 0.5)
            & (ev.seas_n >= 5)
            & (ev.c3a >= MIN_TEAM_3PA)
        )
        rate = ev.c3m / ev.c3a
        cold = shooter & (rate <= ev.seas3 - COLD_GAP)
        ctrl = shooter & (rate >= ev.seas3)
        out[str(S)] = {
            "checkins_mid_period": int(len(ev)),
            "bench_shooter_checkins_eligible": int(shooter.sum()),
            "cold_events": int(cold.sum()),
            "cold_events_players": int(ev[cold].player_id.nunique()),
            "cold_events_games": int(ev[cold].game_id.nunique()),
            "control_events": int(ctrl.sum()),
            "top5_player_share_cold": round(
                float(ev[cold].player_id.value_counts().head(5).sum() / max(cold.sum(), 1)), 3
            ),
        }
        log(f"D2 counts {S}: {out[str(S)]}")
    return out


# --------------------------------------------------------------------------- power (label-free noise arm)


def crps_cont(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    tau = (np.arange(1, 200) - 0.5) / 199
    return 2.0 * np.mean(((y[:, None] < q) - tau[None, :]) * (q - y[:, None]), axis=1)


def noise_deltas(q0: np.ndarray, ps: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(ps, return_inverse=True)
    z = rng.standard_normal(len(ids))[inv]
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = q0[np.arange(len(q0)), u]
    return crps_cont(np.clip(q0 * np.exp(sigma * z)[:, None], 0, 48), y) - crps_cont(q0, y)


def weeks(dates: pd.Series, season: int) -> np.ndarray:
    return np.array([f"{season}-{w}" for w in pd.to_datetime(dates).dt.strftime("%G-%V")])


def power(rows: pd.DataFrame, slices: dict[str, np.ndarray], S: int, seeds: int) -> dict[str, Any]:
    from research.eval import role_prior_salary_eval as ev

    tau = (np.arange(1, 200) - 0.5) / 199
    sd = np.maximum((rows.M1_q90 - rows.M1_q10).to_numpy() / 2.563, 0.5)
    q_all = np.clip(rows.M1_mean.to_numpy()[:, None] + sd[:, None] * norm.ppf(tau)[None, :], 0, 48)
    ps_all = np.array([f"{S}-{p}" for p in rows.player_id])
    wk_all = weeks(rows.game_date, S)
    out: dict[str, Any] = {}
    for name, m in slices.items():
        if m.sum() < 50:
            out[name] = {"rows": int(m.sum()), "half_width_min": None}
            continue
        hw, binds = [], []
        for s in range(seeds):
            d = noise_deltas(q_all[m], ps_all[m], 0.05, 900 + s)
            r = ev.three_way(d, rows.game_id.to_numpy()[m], ps_all[m], wk_all[m])["binding"]
            hw.append(r["half_width"])
            binds.append(r["clustering"])
        out[name] = {
            "rows": int(m.sum()),
            "player_seasons": int(len(np.unique(ps_all[m]))),
            "games": int(rows.game_id[m].nunique()),
            "half_width_min_mean": round(float(np.mean(hw)), 4),
            "half_width_min_sd": round(float(np.std(hw)), 4),
            "binding_mode": max(set(binds), key=binds.count),
        }
        log(f"power {S} {name}: {out[name]}")
    return out


# --------------------------------------------------------------------------- planted future


def planted_future(
    data: dict[str, pd.DataFrame], rows: pd.DataFrame, cut: str = "2023-12-01"
) -> dict[str, Any]:
    """Change every game dated on/after ``cut``: coaches renamed, margins flipped, box minutes x 50, players reassigned to another team.
    Coach-history and familiarity counts of rows before the cut must be identical; a control (rows on/after the cut) must move."""
    g2 = data["games"].copy()
    late = g2.game_date >= pd.Timestamp(cut)
    g2.loc[late, "home_pts"] = g2.loc[late, "home_pts"] + 40
    t2 = data["tcg"].merge(g2[["game_id", "game_date"]], on="game_id")
    t2.loc[t2.game_date >= pd.Timestamp(cut), "coach_name"] = "PLANTED COACH"
    t2 = t2.drop(columns="game_date")
    b2 = data["box"].merge(g2[["game_id", "game_date"]], on="game_id")
    lb = b2.game_date >= pd.Timestamp(cut)
    b2.loc[lb, "minutes"] = b2.loc[lb, "minutes"] * 50
    b2 = b2.drop(columns="game_date")

    def feats(g, t, b):
        tg = team_games(g, t)
        ch = coach_history(tg)
        r = rows[["game_id", "player_id", "team_id", "game_date", "season"]].merge(
            tg[["game_id", "team_id", "asof_coach"]], on=["game_id", "team_id"], how="left"
        )
        fam = familiarity(r, b, tg)
        return (
            r.merge(ch, on=["game_id", "team_id"], how="left")
            .merge(fam, on=["game_id", "player_id"], how="left")
            .sort_values(["game_id", "player_id"])
            .reset_index(drop=True)
        )

    a = feats(data["games"], data["tcg"], data["box"])
    p = feats(g2, t2, b2)
    cols = [
        "asof_coach",
        "ch_n",
        "ch_blow20",
        "ch_mid",
        "ch_close",
        "ch_b2b",
        "pc_with_n",
        "pc_against_n",
    ]
    before = a.game_date < pd.Timestamp(cut)
    same = all(
        a.loc[before, k].fillna(-1).astype(str).equals(p.loc[before, k].fillna(-1).astype(str))
        for k in cols
    )
    after = ~before
    moved = float(
        np.mean(
            np.any(
                np.stack(
                    [
                        a.loc[after, k].astype(str).to_numpy()
                        != p.loc[after, k].astype(str).to_numpy()
                        for k in cols
                    ]
                ),
                axis=0,
            )
        )
    )
    return {
        "rows_before_cut": int(before.sum()),
        "identical_before_cut": bool(same),
        "control_rows_after_cut_changed_share": round(moved, 3),
    }


# --------------------------------------------------------------------------- main


def build_rows(data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    ms = np.load(
        ROOT / "reports/minutes_v2/minutes_scores.npz", allow_pickle=True
    )  # own artifact; only the listed keys are indexed
    r = pd.DataFrame({k: ms[k] for k in ROLE_COLS + GRID_COLS})
    r = r.rename(columns={"row_gid": "game_id", "row_pid": "player_id", "row_season": "season"})
    r["player_id"] = r.player_id.astype(int)
    r["season"] = r.season.astype(int)
    assert r.season.max() <= 2024
    r = r[~r.game_id.isin(BACKFILLED_AFTER_A0)]
    box = data["box"]
    r = r.merge(box[["game_id", "player_id", "team_id"]], on=["game_id", "player_id"], how="left")
    r = r.merge(data["games"][["game_id", "game_date"]], on="game_id", how="left")
    tg = team_games(data["games"], data["tcg"])
    r = r.merge(
        tg[["game_id", "team_id", "asof_coach", "asof_rule", "coach_name", "abs_margin"]],
        on=["game_id", "team_id"],
        how="left",
    )
    return r.reset_index(drop=True), tg


def run(out: Path, do_power: bool, seeds: int) -> dict[str, Any]:
    t0 = time.time()
    c = connect()
    data = load(c)
    log(
        f"loaded: games {len(data['games'])}, coach rows {len(data['tcg'])}, box {len(data['box'])}"
    )
    rows, tg = build_rows(data)
    log(f"scored minutes rows {len(rows)} (team_id missing {int(rows.team_id.isna().sum())})")
    ch = coach_history(tg)
    rows = rows.merge(ch, on=["game_id", "team_id"], how="left")
    rows = rows.merge(familiarity(rows, data["box"], tg), on=["game_id", "player_id"], how="left")
    rows = rows.merge(
        movers(rows[["game_id", "player_id", "team_id", "season"]], data["box"], data["games"]),
        on=["game_id", "player_id"],
        how="left",
    )
    rs = role_stability(data["box"], data["games"])
    rows = rows.merge(
        rs[["game_id", "player_id", "rs_sd10"]], on=["game_id", "player_id"], how="left"
    )
    cut_unstable = float(rs[rs.season == 2022].rs_sd10.quantile(UNSTABLE_Q))
    log("as-of states built")

    res: dict[str, Any] = {
        "rule": "MINUTES_DECISION (DRAFT)",
        "data": {
            "nba_duckdb_mtime": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime((ROOT / "nba.duckdb").stat().st_mtime)
            ),
            "minutes_scores_sha8": sha8(ROOT / "reports/minutes_v2/minutes_scores.npz"),
            "excluded_backfilled_games": list(BACKFILLED_AFTER_A0),
            "seasons_loaded": "2022-2024 (season <= 2024 asserted)",
        },
        "checks": [],
    }
    # ---- G0 history coach coverage (data audit, non-negotiable 8)
    h = duckdb.connect(str(ROOT / "data/history/nba_history.duckdb"), read_only=True)
    hist_tcg = h.execute("select count(*) from team_coach_games").fetchone()[0]
    hist_tc = h.execute("select count(*) from team_coaches").fetchone()[0]
    hist_box = h.execute(
        "select g.season, count(*) from player_game_stats p join games g using (game_id) group by 1 order by 1"
    ).fetchall()
    hist_st = h.execute(
        "select g.season, count(distinct s.game_id) from stints s join games g using (game_id) group by 1 order by 1"
    ).fetchall()
    h.close()
    res["history_audit"] = {
        "team_coach_games_rows_2013_21": int(hist_tcg),
        "team_coaches_rows_2013_21": int(hist_tc),
        "box_rows_by_season": {int(s): int(n) for s, n in hist_box},
        "stint_games_by_season": {int(s): int(n) for s, n in hist_st},
    }
    # ---- coach coverage per season
    cov = {}
    for S in (2022, SELECT, REPORT):
        t = tg[tg.season == S]
        cov[str(S)] = {
            "team_games": int(len(t)),
            "with_actual_coach": int(t.coach_name.notna().sum()),
            "with_asof_coach": int(t.asof_coach.notna().sum()),
            "asof_equals_actual_share": round(float((t.asof_coach == t.coach_name).mean()), 4),
            "asof_from_season_opener_reference": int(
                (t.asof_rule == "season_opener_reference").sum()
            ),
            "distinct_coaches": int(t.coach_name.nunique()),
        }
    res["coach_coverage"] = cov
    res["checks"].append(
        chk(
            "G1 every 2023/2024 team-game has an as-of coach",
            {k: cov[k]["with_asof_coach"] == cov[k]["team_games"] for k in ("2023", "2024")},
            "all",
            all(cov[k]["with_asof_coach"] == cov[k]["team_games"] for k in ("2023", "2024")),
        )
    )

    per: dict[str, Any] = {}
    for S in (SELECT, REPORT):
        r = rows[rows.season == S].reset_index(drop=True)
        d: dict[str, Any] = {
            "rows": int(len(r)),
            "games": int(r.game_id.nunique()),
            "player_seasons": int(r.player_id.nunique()),
        }
        d["rows_missing_team"] = int(r.team_id.isna().sum())
        d["asof_coach_missing_rows"] = int(r.asof_coach.isna().sum())
        # coach history depth at the row
        d["coach_prior_games_quantiles"] = {
            q: float(r.ch_n.quantile(q)) for q in (0.05, 0.25, 0.5, 0.75)
        }
        d["rows_coach_prior_lt20_share"] = round(float((r.ch_n < 20).mean()), 4)
        d["rows_coach_prior_0_share"] = round(float((r.ch_n == 0).mean()), 4)
        # coach-state cells (prior team-games of the as-of coach in each state)
        for k in ("ch_blow20", "ch_mid", "ch_close", "ch_b2b"):
            d[f"{k}_median"] = float(r[k].median())
            d[f"{k}_ge10_share"] = round(float((r[k] >= 10).mean()), 4)
        d["coach_season_cells"] = int(tg[tg.season == S].groupby(["asof_coach"]).ngroups)
        # familiarity
        d["pc_with_n_quantiles"] = {
            q: float(r.pc_with_n.quantile(q)) for q in (0.05, 0.25, 0.5, 0.75)
        }
        d["new_relationship_rows"] = int((r.pc_with_n < WITH_NEW).sum())
        d["new_relationship_player_seasons"] = int(r[r.pc_with_n < WITH_NEW].player_id.nunique())
        mv = r[(r.slice_move == "mover") & (r.gwt < 10)]
        ia = r[(r.slice_move == "inseason_arrival") & (r.gwt < 10)]
        d["movers_first10"] = {
            "rows": int(len(mv)),
            "players": int(mv.player_id.nunique()),
            "with_ge1_rows": int((mv.pc_with_n >= 1).sum()),
            "with_ge1_players": int(mv[mv.pc_with_n >= 1].player_id.nunique()),
            "with_ge20_players": int(mv[mv.pc_with_n >= 20].player_id.nunique()),
            "against_ge2_rows": int((mv.pc_against_n >= 2).sum()),
            "against_ge2_players": int(mv[mv.pc_against_n >= 2].player_id.nunique()),
            "against_median": float(mv.pc_against_n.median()) if len(mv) else None,
        }
        # pre-move familiarity = counts at the first game with the new team (gwt == 0): games WITH / AGAINST the new coach before the move
        m0 = mv[mv.gwt == 0]
        d["movers_premove_familiarity"] = {
            "players_with_first_game_row": int(m0.player_id.nunique()),
            "with_ge1": int((m0.pc_with_n >= 1).sum()),
            "with_ge20": int((m0.pc_with_n >= 20).sum()),
            "against_ge1": int((m0.pc_against_n >= 1).sum()),
            "against_ge2": int((m0.pc_against_n >= 2).sum()),
            "against_ge4": int((m0.pc_against_n >= 4).sum()),
            "against_quantiles": {q: float(m0.pc_against_n.quantile(q)) for q in (0.25, 0.5, 0.75)}
            if len(m0)
            else None,
            "with_quantiles": {q: float(m0.pc_with_n.quantile(q)) for q in (0.5, 0.9)}
            if len(m0)
            else None,
        }
        a0 = ia[ia.gwt == 0]
        d["inseason_premove_familiarity"] = {
            "players": int(a0.player_id.nunique()),
            "with_ge1": int((a0.pc_with_n >= 1).sum()),
            "against_ge2": int((a0.pc_against_n >= 2).sum()),
        }
        d["inseason_arrivals_first10"] = {
            "rows": int(len(ia)),
            "players": int(ia.player_id.nunique()),
            "with_ge1_players": int(ia[ia.pc_with_n >= 1].player_id.nunique()),
        }
        # coach change: team-seasons whose opener coach differs from the previous season's last coach, or with a mid-season change
        t = tg[tg.season == S]
        prev_last = tg[tg.season == S - 1].groupby("team_id").coach_name.last()
        opener = t.groupby("team_id").coach_name.first()
        d["team_seasons_new_coach_at_opener"] = int(
            (opener != prev_last.reindex(opener.index)).sum()
        )
        d["team_seasons_midseason_change"] = int(
            (t.groupby("team_id").coach_name.nunique() > 1).sum()
        )
        # role stability
        d["unstable_cut_rs_sd10_from_2022"] = round(cut_unstable, 3)
        d["unstable_rows"] = int((r.rs_sd10 >= cut_unstable).sum())
        d["stable_rows"] = int((r.rs_sd10 < cut_unstable).sum())
        d["rs_sd10_missing_rows"] = int(r.rs_sd10.isna().sum())
        # label-free missingness: coach-history-zero and familiarity-zero shares by as-of strata
        r["_min10b"] = pd.cut(
            r.row_min10.astype(float),
            [-1, 10, 20, 28, 99],
            labels=["<10", "10-20", "20-28", ">=28"],
        )
        r["_role"] = np.where(r.row_starter10.astype(float) >= 0.5, "starter", "bench")
        d["null_by_asof_strata"] = {
            f"{a}|{b}": {
                "n": int(len(g)),
                "coach_hist0": round(float((g.ch_n == 0).mean()), 4),
                "with0": round(float((g.pc_with_n == 0).mean()), 4),
            }
            for (a, b), g in r.groupby(["_min10b", "_role"], observed=True)
        }
        # slices (rows) for the rule
        sl = {
            "all": np.ones(len(r), bool),
            "starters": (r.row_starter10.astype(float) >= 0.5).to_numpy(),
            "bench": (r.row_starter10.astype(float) < 0.5).to_numpy(),
            "teammate_out": (r.row_n_out_rot.astype(float) >= 1).to_numpy(),
            "movers_first10": ((r.slice_move == "mover") & (r.gwt < 10)).to_numpy(),
            "new_relationship": (r.pc_with_n < WITH_NEW).to_numpy(),
            "unstable": (r.rs_sd10 >= cut_unstable).to_numpy(),
            "stable": (r.rs_sd10 < cut_unstable).to_numpy(),
            "realised_blowout20": (r.abs_margin >= 20).to_numpy(),
            "b2b": (r.row_b2b.astype(float) >= 1).to_numpy(),
        }
        d["slice_rows"] = {k: int(v.sum()) for k, v in sl.items()}
        if do_power:
            d["power_noise_sigma0.05"] = power(r, sl, S, seeds)
        per[str(S)] = d
        log(f"season {S} done")
    res["per_season"] = per
    res["checks"].append(
        chk(
            "G9 planted future (coach and familiarity counts)",
            planted_future(data, rows[rows.season == SELECT].reset_index(drop=True)),
            "identical before cut; control >= 0.5",
            True,
        )
    )
    pf = res["checks"][-1]["value"]
    res["checks"][-1]["pass"] = bool(
        pf["identical_before_cut"] and pf["control_rows_after_cut_changed_share"] >= 0.5
    )
    sr = rows[["game_id", "player_id", "row_starter10"]]
    res["d2_situational_checkin_counts"] = checkin_counts(c, data["box"], data["games"], sr)
    c.close()
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    log(f"wrote {out} in {res['wall_seconds']} s")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--no-power", action="store_true")
    a = ap.parse_args(argv)
    run(a.out, not a.no_power, a.seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
