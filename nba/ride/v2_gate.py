# ruff: noqa: E501
"""Label-free minimum-data gate for INGAME_WINPROB_V2 (docs/prereg/INGAME_WINPROB_V2.md; non-negotiable 8).

Reads, read-only: ``data/pbp/<game>.parquet`` (regular seasons 2022-24 only), the ``stints``, ``possessions`` and
``games`` tables of a copy/backup of ``nba.duckdb`` (game_id prefix 00222/00223/00224 in every query; season 2025
= prefix 00225 is never named), ``data/injury_elo_realtip/oof_predictions.parquet`` (seasons 2022-24 only) and the
fixed v1 coefficient file. It reads NO final score, winner, box-score total or any label: the only scores used are the
running score at an event time (known at that time). Synthetic outcomes (drawn from v1's own probabilities) appear
only in ``power`` and say so.

Usage: python -m nba.ride.v2_gate --db <duckdb copy> --out reports/ingame_v2/gate.json [--max-games N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from nba.features.game_tipoff import build_game_tipoff
from nba.ride.causal_five import causal_five, causal_prefix, prefix_len
from nba.ride.feed_state import Event, update_possession
from nba.ride.winprob import WinProbModel, parse_clock

SEASONS = (2022, 2023, 2024)  # start years; 2025 (the frozen holdout) is not representable here
PREFIX = {2022: "00222", 2023: "00223", 2024: "00224"}
GRID = tuple(range(60, 2821, 60))  # 47 states; 2880 is the final regulation margin (an outcome)
FOUL_TOKEN = re.compile(r"\(P(\d+)(?:\.(T\d+|PN))?\)")
FT_OF = re.compile(r"(\d)\s+of\s+(\d)")
# Declared before any number was computed (thresholds of the gate).
THRESH = {
    "pbp_four_periods": 0.99,
    "timeout_team_resolved": 0.995,
    "foul_token_coverage": 0.99,
    "stint_tile_games": 0.95,
    "possession_next_actor_agreement": 0.95,
    "possession_vs_table_agreement": 0.95,
    "prior_coverage": 0.99,
    "min_states_ge95": 5000,
    "min_games_ge95": 250,
    "min_games_ge98": 100,
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def elapsed(period: int, clock_rem: float) -> float:
    return (
        (period - 1) * 720.0 + (720.0 - clock_rem)
        if period <= 4
        else 2880.0 + (period - 5) * 300.0 + (300.0 - clock_rem)
    )


def to_events(
    d: pd.DataFrame, home: int, away: int
) -> list[tuple[float, int, Event, dict[str, Any]]]:
    """nba.com pbp rows -> (elapsed, row index, Event, extras). extras: team-foul token, player-foul token."""
    out: list[tuple[float, int, Event, dict[str, Any]]] = []
    last_shot_team = ""
    for i, r in enumerate(d.itertuples(index=False)):
        at = str(r.action_type)
        sub = str(r.sub_type or "")
        desc = str(r.description or "")
        team = str(int(r.team_id)) if r.team_id and int(r.team_id) in (home, away) else ""
        per = int(r.period)
        t = elapsed(per, parse_clock(str(r.clock))) if per >= 1 and r.clock else 0.0
        ex: dict[str, Any] = {"period": per, "player": int(r.player_id) if r.player_id else 0}
        ev = Event("other", team)
        if at == "period":
            ev = Event("reset", "")
        elif at == "Made Shot":
            ev = Event("made_fg", team, made=True)
            last_shot_team = team
        elif at == "Missed Shot":
            ev = Event("miss_fg", team)
            last_shot_team = team
        elif at == "Free Throw":
            if "Technical" in sub:
                ev = Event("other", team)
            else:
                m = FT_OF.search(sub)
                ev = Event(
                    "ft",
                    team,
                    made=not desc.startswith("MISS"),
                    last_ft=bool(m) and m is not None and m.group(1) == m.group(2),
                )
        elif at == "Rebound":
            ev = Event("oreb" if team and team == last_shot_team else "dreb", team)
        elif at == "Turnover":
            ev = Event("tov", team)
        elif at == "Foul":
            if sub in ("Offensive", "Offensive Charge"):
                ev = Event("tov", team)
            elif sub in (
                "Shooting",
                "Personal",
                "Loose Ball",
                "Personal Take",
                "Flagrant Type 1",
                "Flagrant Type 2",
                "Transition Take",
                "Away From Play",
                "Clear Path",
            ):
                ev = Event("foul", team)
            m2 = FOUL_TOKEN.search(desc)
            if m2:
                ex["pf"] = int(m2.group(1))
                tok = m2.group(2)
                ex["tf"] = int(tok[1:]) if tok and tok.startswith("T") else None
                ex["pn"] = tok == "PN"
        elif at == "Timeout":
            ex["timeout_team"] = int(r.player_id) if r.player_id else 0
        elif at == "Substitution":
            ex["sub"] = True
        out.append((t, i, ev, ex))
    return out


def v1_probs(model: WinProbModel, margin: np.ndarray, elapsed_s: np.ndarray) -> np.ndarray:
    left = np.where(elapsed_s <= 2880.0, 2880.0 - elapsed_s, 300.0 - ((elapsed_s - 2880.0) % 300.0))
    return model.prob_home(margin, left)


def game_pass(
    root: Path, gid: str, home: int, away: int, poss_rows: pd.DataFrame | None, v1: WinProbModel
) -> dict[str, Any] | None:
    f = root / "data" / "pbp" / f"{gid}.parquet"
    if not f.exists():
        return None
    d = pd.read_parquet(
        f,
        columns=[
            "game_id",
            "action_number",
            "period",
            "clock",
            "team_id",
            "player_id",
            "action_type",
            "sub_type",
            "description",
            "score_home",
            "score_away",
        ],
    )
    d = d.sort_values("action_number", kind="stable").reset_index(drop=True)
    periods = sorted({int(p) for p in d.period.unique() if p >= 1})
    ended = int((d.action_type.eq("period") & d.sub_type.eq("end")).sum())
    res: dict[str, Any] = {
        "gid": gid,
        "has_pbp": True,
        "four_periods": ended >= 4 and set(range(1, 5)) <= set(periods),
        "ot": max(periods, default=0) > 4,
    }
    sh = pd.to_numeric(d.score_home, errors="coerce").ffill().fillna(0).to_numpy()
    sa = pd.to_numeric(d.score_away, errors="coerce").ffill().fillna(0).to_numpy()
    evs = to_events(d, home, away)
    # timeouts
    to = [e[3].get("timeout_team", None) for e in evs if "timeout_team" in e[3]]
    res["timeouts_total"] = len(to)
    res["timeouts_resolved"] = sum(1 for x in to if x in (home, away))
    res["timeouts_max_team"] = max([to.count(home), to.count(away)]) if to else 0
    # fouls
    fouls = [e for e in evs if e[2].kind == "foul" and e[3].get("pf") is not None]
    all_fouls = [e for e in evs if e[2].kind == "foul"]
    res["fouls_total"] = len(all_fouls)
    res["fouls_with_token"] = len(fouls)
    # team-foul token monotone within (period, team)
    mono_ok = mono_n = 0
    seen: dict[tuple[int, str], int] = {}
    for _t, _i, ev, ex in fouls:
        if ex.get("tf") is None:
            continue  # "PN" = the team is already in the penalty; no running count is printed
        key = (ex["period"], ev.team)
        mono_n += 1
        prev = seen.get(key, 0)
        mono_ok += int(ex["tf"] == prev + 1)
        seen[key] = ex["tf"]
    res["teamfoul_steps"] = mono_n
    res["teamfoul_steps_ok"] = mono_ok
    # possession rule
    poss: str | None = None
    known = total = 0
    agree = disagree = 0
    table_ok = table_n = 0
    starts: list[tuple[int, float, str]] = []
    states_t = np.array([e[0] for e in evs])
    holders: list[str | None] = []
    for t_, _i, ev, ex in evs:
        if ev.kind in ("made_fg", "miss_fg", "tov", "ft") and ev.team and poss is not None:
            agree += int(ev.team == poss)
            disagree += int(ev.team != poss)
        newp = update_possession(poss, ev, str(home), str(away))
        expect_known = ev.kind in ("made_fg", "oreb", "dreb", "tov", "foul") or (
            ev.kind == "ft" and (not ev.last_ft or ev.made)
        )
        if expect_known and ev.team:
            total += 1
            known += int(newp is not None)
        if newp is not None and newp != poss and ev.kind in ("made_fg", "dreb", "tov", "ft"):
            starts.append((ex["period"], t_, newp))
        poss = newp
        holders.append(newp)
    res.update(
        poss_events=total, poss_known=known, next_actor_agree=agree, next_actor_disagree=disagree
    )
    if poss_rows is not None and len(poss_rows):
        pr = poss_rows
        for per, t_, team in starts:
            if per > 4:
                continue
            c = 720.0 - (t_ - (per - 1) * 720.0)  # seconds remaining in the period
            m = pr[(pr.period == per) & (pr.clock_start < c)].sort_values(
                "clock_start", ascending=False
            )
            if len(m) == 0:
                continue
            table_n += 1
            table_ok += int(str(int(m.iloc[0].off_team)) == team)
    res.update(table_n=table_n, table_ok=table_ok)
    # grid states
    ts = np.array(GRID, dtype=float)
    idx = np.array(
        [prefix_len(states_t, float(t)) - 1 for t in ts]
    )  # causal prefix, never searchsorted
    ok = idx >= 0
    margin = np.where(ok, sh[np.clip(idx, 0, None)] - sa[np.clip(idx, 0, None)], 0)
    p1 = v1.prob_home(margin.astype(float), 2880.0 - ts)
    lead = np.maximum(p1, 1 - p1)
    hold = np.array([holders[i] if i >= 0 else None for i in idx], dtype=object)
    res["grid_n"] = int(ok.sum())
    res["grid_poss_known"] = int(sum(h is not None for h in hold))
    res["grid_ge95"] = int((lead >= 0.95).sum())
    res["grid_ge98"] = int((lead >= 0.98).sum())
    res["grid_75_95"] = int(((lead >= 0.75) & (lead < 0.95)).sum())
    res["any_ge98"] = bool((lead >= 0.98).any())
    res["any_ge95"] = bool((lead >= 0.95).any())
    res["any_ge75"] = bool((lead >= 0.75).any())
    return res


def stint_tiles(con: duckdb.DuckDBPyConnection, season: int, ids: set[str]) -> dict[str, Any]:
    p = PREFIX[season]
    df = con.execute(
        "select game_id, team_id, period, sum(start_clock - end_clock) as s, count(*) as n, min(list_count(players)) as mn, max(list_count(players)) as mx "
        f"from stints where game_id like '{p}%' and period between 1 and 4 group by 1, 2, 3"
    ).df()
    df = df[df.game_id.isin(ids)]
    g = (
        df.assign(ok=(np.abs(df.s - 720.0) <= 1.0) & (df.mn == 5) & (df.mx == 5))
        .groupby("game_id")
        .agg(n=("ok", "size"), k=("ok", "sum"))
    )
    full = g[(g.n == 8) & (g.k == 8)]
    return {"games_with_stints": int(len(g)), "games_tiling_all_8_team_periods": int(len(full))}


def prior_cov(root: Path, games: pd.DataFrame, season: int) -> dict[str, Any]:
    oof = pd.read_parquet(root / "data" / "injury_elo_realtip" / "oof_predictions.parquet")
    oof = oof[(oof.model == "rung0_injury_elo") & (oof.season == season)]
    m = games.merge(
        oof[["game_id", "p", "made_with_data_through", "game_date"]],
        on="game_id",
        how="left",
        suffixes=("", "_o"),
    )
    has = m.p.notna()
    tips = build_game_tipoff(max_season=2024).to_pandas()[["game_id", "tip_et"]]
    m = m.merge(tips, on="game_id", how="left")
    made = pd.to_datetime(m.made_with_data_through)
    asof = has & m.tip_et.notna() & (made <= pd.to_datetime(m.tip_et) - pd.Timedelta(minutes=60))
    return {
        "games": int(len(m)),
        "with_prior": int(has.sum()),
        "prior_asof_before_game_date": int(asof.sum()),
    }


def star_cov(con: duckdb.DuckDBPyConnection, season: int) -> dict[str, Any]:
    p = PREFIX[season]
    df = con.execute(
        "select count(*) as n, sum(case when k >= 5 then 1 else 0 end) as ge5 from ("
        " select s.player_id, s.game_id, count(*) over (partition by s.player_id order by g.game_date, s.game_id rows between unbounded preceding and 1 preceding) as k"
        " from player_game_stats s join games g using (game_id) where g.season <= 2024 and g.game_id like '002%' and s.minutes > 0"
        f") where game_id like '{p}%'"
    ).fetchone()
    assert df is not None
    return {"player_games_played": int(df[0]), "with_ge5_prior_games_in_window": int(df[1] or 0)}


def five_sample(
    con: duckdb.DuckDBPyConnection, root: Path, games: pd.DataFrame, season: int, n: int
) -> dict[str, Any]:
    """Descriptive: causal five (tracker on the causal prefix, box starters, no box roster) vs the stints table."""
    p = PREFIX[season]
    st = con.execute(
        "select game_id, team_id, player_id from player_game_stats "
        f"where game_id like '{p}%' and starter"
    ).df()
    stints = con.execute(
        f"select game_id, team_id, period, start_clock, end_clock, players from stints where game_id like '{p}%'"
    ).df()
    n_ts = n_diff = n_short = n_games = 0
    for r in games.head(n).itertuples(index=False):
        f = root / "data" / "pbp" / f"{r.game_id}.parquet"
        g = st[st.game_id == r.game_id]
        starters = {int(t): [int(x) for x in v.player_id] for t, v in g.groupby("team_id")}
        if not f.exists() or len(starters) != 2 or any(len(v) != 5 for v in starters.values()):
            continue
        d = pd.read_parquet(f).sort_values("action_number", kind="stable").reset_index(drop=True)
        el = np.array(
            [elapsed(int(a), parse_clock(str(b))) for a, b in zip(d.period, d.clock, strict=True)]
        )
        sg = stints[stints.game_id == r.game_id]
        n_games += 1
        for t in GRID:
            five = causal_five(causal_prefix(d, el, float(t)), starters)
            per = int(np.ceil(t / 720.0))
            c = per * 720.0 - t
            for team, cf in five.items():
                rows = sg[
                    (sg.team_id == team)
                    & (sg.period == per)
                    & (sg.start_clock >= c)
                    & (sg.end_clock < c)
                ]
                if len(rows) == 0:
                    continue
                n_ts += 1
                n_short += int(len(set(cf)) != 5)
                n_diff += int(set(cf) != {int(x) for x in rows.iloc[0].players})
    return {
        "games": n_games,
        "team_states": n_ts,
        "causal_five_ne_stint": n_diff,
        "causal_five_not_five": n_short,
    }


def power(n_games: int, seed: int = 0, n_boot: int = 300) -> dict[str, Any]:
    """Half-width of a game-clustered paired log-loss CI on SYNTHETIC outcomes (truth = a fixed logit path)."""
    rng = np.random.default_rng(seed)
    k = len(GRID)
    z = (
        rng.normal(0.0, 1.6, size=(n_games, 1))
        + np.cumsum(rng.normal(0, 0.35, size=(n_games, k)), axis=1) * 0.4
    )
    p = 1 / (1 + np.exp(-z))
    y = (rng.random((n_games, k)) < p[:, [-1]]).astype(
        float
    )  # one outcome per game (all states share it)

    def ll(q: np.ndarray) -> np.ndarray:
        return np.asarray(-(y * np.log(q) + (1 - y) * np.log(1 - q)))

    arm0 = 1 / (1 + np.exp(-(z + rng.normal(0, 0.05, size=z.shape))))
    arm1 = 1 / (
        1 + np.exp(-(z + rng.normal(0, 0.05, size=z.shape)))
    )  # same noise level: a null contrast
    d = (ll(arm1) - ll(arm0)).mean(axis=1)
    est = float(d.mean())
    boots = [float(d[rng.integers(0, n_games, n_games)].mean()) for _ in range(n_boot)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "null_contrast_point": est,
        "ci": [float(lo), float(hi)],
        "half_width": float((hi - lo) / 2),
        "n_games": n_games,
        "note": "synthetic outcomes, no real label",
    }


def run(db: Path, out: Path, root: Path, max_games: int | None) -> dict[str, Any]:
    v1 = WinProbModel.load()
    con = duckdb.connect(str(db), read_only=True)
    import hashlib

    report: dict[str, Any] = {
        "seasons": {},
        "thresholds": THRESH,
        "db_path": str(db),
        "db_sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
        "grid": [GRID[0], GRID[-1], len(GRID)],
    }
    for s in SEASONS:
        p = PREFIX[s]
        games = con.execute(
            f"select game_id, game_date, home_team, away_team from games where season = {s} and season <= 2024 and game_id like '{p}%' order by game_id"
        ).df()
        if max_games:
            games = games.head(max_games)
        log(f"season {s}: {len(games)} regular-season games")
        poss_all = con.execute(
            f"select game_id, period, clock_start, off_team from possessions where game_id like '{p}%'"
        ).df()
        pg = {k: v for k, v in poss_all.groupby("game_id")}
        rows = []
        for r in games.itertuples(index=False):
            res = game_pass(
                root, r.game_id, int(r.home_team), int(r.away_team), pg.get(r.game_id), v1
            )
            if res is not None:
                rows.append(res)
        df = pd.DataFrame(rows)
        n = int(len(games))
        sm: dict[str, Any] = {
            "games_listed": n,
            "games_with_pbp": int(len(df)),
            "four_periods": int(df.four_periods.sum()),
            "games_with_ot": int(df.ot.sum()),
        }
        sm["timeouts"] = {
            "events": int(df.timeouts_total.sum()),
            "resolved": int(df.timeouts_resolved.sum()),
            "max_team_count_p99": float(np.percentile(df.timeouts_max_team, 99)),
            "share_games_max_gt7": float((df.timeouts_max_team > 7).mean()),
        }
        sm["fouls"] = {
            "events": int(df.fouls_total.sum()),
            "with_token": int(df.fouls_with_token.sum()),
            "teamfoul_steps": int(df.teamfoul_steps.sum()),
            "teamfoul_steps_ok": int(df.teamfoul_steps_ok.sum()),
        }
        sm["possession"] = {
            "events": int(df.poss_events.sum()),
            "known_after_event": int(df.poss_known.sum()),
            "next_actor_agree": int(df.next_actor_agree.sum()),
            "next_actor_disagree": int(df.next_actor_disagree.sum()),
            "vs_table_n": int(df.table_n.sum()),
            "vs_table_ok": int(df.table_ok.sum()),
            "grid_states": int(df.grid_n.sum()),
            "grid_known": int(df.grid_poss_known.sum()),
        }
        sm["grid_v1_cells"] = {
            "states_ge95": int(df.grid_ge95.sum()),
            "games_any_ge95": int(df.any_ge95.sum()),
            "states_ge98": int(df.grid_ge98.sum()),
            "games_any_ge98": int(df.any_ge98.sum()),
            "states_75_95": int(df.grid_75_95.sum()),
            "games_any_ge75": int(df.any_ge75.sum()),
        }
        sm["stints"] = stint_tiles(con, s, set(games.game_id))
        sm["prior"] = prior_cov(root, games, s)
        sm["star"] = star_cov(con, s)
        sm["causal_five_vs_stints_sample"] = five_sample(con, root, games, s, 12)
        report["seasons"][str(s)] = sm
        log(f"season {s} done")
    report["power"] = {
        str(s): power(int(report["seasons"][str(s)]["games_with_pbp"]), seed=s) for s in SEASONS
    }
    sz = report["seasons"]
    checks: list[dict[str, Any]] = []

    def add(name: str, vals: dict[str, float], thr: float) -> None:
        checks.append(
            {
                "check": name,
                "values": vals,
                "threshold": thr,
                "pass": all(v >= thr for v in vals.values()),
            }
        )

    add(
        "pbp_four_periods",
        {k: v["four_periods"] / v["games_listed"] for k, v in sz.items()},
        THRESH["pbp_four_periods"],
    )
    add(
        "timeout_team_resolved",
        {k: v["timeouts"]["resolved"] / max(v["timeouts"]["events"], 1) for k, v in sz.items()},
        THRESH["timeout_team_resolved"],
    )
    add(
        "foul_token_coverage",
        {k: v["fouls"]["with_token"] / max(v["fouls"]["events"], 1) for k, v in sz.items()},
        THRESH["foul_token_coverage"],
    )
    add(
        "stint_tile_games",
        {
            k: v["stints"]["games_tiling_all_8_team_periods"] / v["games_listed"]
            for k, v in sz.items()
        },
        THRESH["stint_tile_games"],
    )
    add(
        "possession_next_actor_agreement",
        {
            k: v["possession"]["next_actor_agree"]
            / max(v["possession"]["next_actor_agree"] + v["possession"]["next_actor_disagree"], 1)
            for k, v in sz.items()
        },
        THRESH["possession_next_actor_agreement"],
    )
    add(
        "possession_vs_table_agreement",
        {
            k: v["possession"]["vs_table_ok"] / max(v["possession"]["vs_table_n"], 1)
            for k, v in sz.items()
        },
        THRESH["possession_vs_table_agreement"],
    )
    add(
        "prior_coverage",
        {k: v["prior"]["prior_asof_before_game_date"] / v["games_listed"] for k, v in sz.items()},
        THRESH["prior_coverage"],
    )
    add(
        "cells_states_ge95",
        {k: float(v["grid_v1_cells"]["states_ge95"]) for k, v in sz.items()},
        float(THRESH["min_states_ge95"]),
    )
    add(
        "cells_games_ge95",
        {k: float(v["grid_v1_cells"]["games_any_ge95"]) for k, v in sz.items()},
        float(THRESH["min_games_ge95"]),
    )
    add(
        "cells_games_ge98",
        {k: float(v["grid_v1_cells"]["games_any_ge98"]) for k, v in sz.items()},
        float(THRESH["min_games_ge98"]),
    )
    report["checks"] = checks
    report["verdict"] = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=float))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--max-games", type=int, default=None)
    a = ap.parse_args()
    rep = run(a.db, a.out, a.root.resolve(), a.max_games)
    for c in rep["checks"]:
        print(
            ("PASS " if c["pass"] else "FAIL ") + c["check"],
            {k: round(v, 4) for k, v in c["values"].items()},
            ">=",
            c["threshold"],
        )
    print("verdict", rep["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
