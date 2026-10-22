# ruff: noqa: E501
# mypy: ignore-errors
"""TEAM_USAGE_CAP minimum-data gate (rule: docs/prereg/TEAM_USAGE_CAP.md, DRAFT). No arm is fitted, no outcome is read.

    cd /Users/devin/Downloads/nba-prediction   # (or a worktree whose data/ symlinks to it)
    .venv/bin/python -W ignore -m research.eval.team_usage_cap_gate [--out reports/prereg_team_usage_cap/gate.json] [--no-power]

Read-only: ``nba.duckdb`` opened ``read_only=True``, ``season <= 2024`` in every SQL; season 2025 is never queried.
Label columns of ``model_rows`` (``y``, realised ``minutes``) are never selected. Realised ``minutes`` of the box score are read ONLY
for PRIOR games (strictly earlier dates) to build as-of rolling minutes; the arrival flag, the as-of roster and the slice score of a
team-game use nothing dated on or after that game. One disclosed membership read: the identifiability audit asks, for rows that played,
whether the arrival could have been known from the preseason log (it uses who appeared at the game ONLY to define the denominator of
that audit, never the slice).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import role_prior_salary_eval as ev

SEASONS = (2023, 2024)
STATS = ("pts", "reb", "ast", "fg3m")
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
K_ARRIVAL = 10  # an arrival stays an arrival for his first K regular-season games with the team
ROT = 10  # rotation = the 10 players with the highest as-of mean minutes
XS = (0.10, 0.15, 0.20, 0.25, 0.30)
X_DEFAULT = 0.20
OUT = Path("reports/prereg_team_usage_cap/gate.json")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    games = con.execute(
        "select game_id, game_date, season, home_team, away_team from games "
        "where season <= 2024 and game_id like '002%' and home_pts is not null"
    ).df()
    box = con.execute(
        "select b.game_id, b.player_id, b.team_id, b.minutes, g.game_date, g.season "
        "from player_game_stats_played b join games g using (game_id) "
        "where g.season <= 2024 and g.game_id like '002%' and b.minutes > 0"
    ).df()
    pre = con.execute(
        "select p.player_id, p.team_id, g.game_date, g.season "
        "from preseason_player_game_stats p join preseason_games g using (game_id) "
        "where g.season <= 2024 and p.minutes > 0"
    ).df()
    return games, box, pre


def team_games(games: pd.DataFrame) -> pd.DataFrame:
    h = games.rename(columns={"home_team": "team_id"})[
        ["game_id", "game_date", "season", "team_id"]
    ]
    a = games.rename(columns={"away_team": "team_id"})[
        ["game_id", "game_date", "season", "team_id"]
    ]
    tg = pd.concat([h, a]).sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)
    tg["k"] = tg.groupby(["team_id", "season"]).cumcount()
    return tg


def asof_tables(box: pd.DataFrame) -> pd.DataFrame:
    """Per appearance (player, game): as-of mean minutes of his previous 10 appearances (any team), and
    ``since_change_before`` = appearances for the current team since his last team change, BEFORE this game
    (0 if the previous appearance was for another team; NaN if he has no earlier appearance)."""
    b = box.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)
    g = b.groupby("player_id")
    b["prev_team"] = g["team_id"].shift(1)
    b["m10_before"] = g["minutes"].transform(lambda s: s.shift(1).rolling(10, min_periods=3).mean())
    change = (b["team_id"] != b["prev_team"]).astype(int)
    run_id = change.groupby(b["player_id"]).cumsum()
    pos = b.groupby(
        ["player_id", run_id]
    ).cumcount()  # appearances for the team since the change, BEFORE this one
    b["since_change_before"] = np.where(b["prev_team"].isna(), np.nan, pos.where(change == 0, 0))
    return b


def roster_scores(tg: pd.DataFrame, app: pd.DataFrame, pre: pd.DataFrame) -> pd.DataFrame:
    """One row per regular-season team-game of the select/report seasons: as-of rotation, arrivals, NS."""
    # per team, the k-indexed list of games; roster of team-game k = appearers in its previous 10 team games
    by_team = {t: d.reset_index(drop=True) for t, d in tg.groupby("team_id")}
    # last known state of each player BEFORE a date: team, m10, since_change (for arrivals not yet appeared)
    app_sorted = app.sort_values(["player_id", "game_date", "game_id"])
    out = []
    pre_by = pre.groupby(["season", "team_id"])["player_id"].apply(set).to_dict()
    for S in SEASONS:
        tgS = tg[tg.season == S]
        for _, r in tgS.iterrows():
            t = int(r.team_id)
            tdf = by_team[t]
            k_all = int(tdf.index[tdf.game_id == r.game_id][0])
            prev_ids = tdf.game_id.iloc[max(0, k_all - 10) : k_all].tolist()
            inc = app[(app.team_id == t) & (app.game_id.isin(prev_ids))]
            out.append((r.game_id, t, S, r.game_date, inc.player_id.unique().tolist(), k_all))
    # (the loop above is O(team-games x games); 5,300 team-games, fine)
    rows = []
    # as-of state per player: last appearance strictly before date
    last = app_sorted.groupby("player_id")
    hist = {p: d for p, d in last}
    for gid, t, S, d0, inc_ids, _ in out:
        pre_ids = pre_by.get((S, t), set())
        cand = set(inc_ids) | set(pre_ids)
        recs = []
        for p in cand:
            h = hist.get(p)
            if h is None:
                continue
            before = h[h.game_date < d0]
            if before.empty:
                continue
            lastrow = before.iloc[-1]
            m10 = float(before.minutes.tail(10).mean()) if len(before) >= 3 else np.nan
            if int(lastrow.team_id) == t:
                # incumbent or arrival that has already played for t: since_change from the appearance AFTER the last one
                sc = float(lastrow.since_change_before) + 1.0
                had_change = (
                    lastrow.since_change_before == lastrow.since_change_before
                )  # not NaN (has a prior team)
                # arrival iff his most recent team change was to t and run < K, and a change exists in his history
                arr = bool(had_change and _run_started_with_change(before) and sc < K_ARRIVAL)
            else:
                # last appearance was for another team: counts as an arrival only if the preseason log puts him with t
                sc = 0.0
                arr = bool(p in pre_ids)
                if not arr:
                    continue
            recs.append((p, m10, arr))
        rows.append((gid, t, S, d0, pd.DataFrame(recs, columns=["player_id", "m10", "arrival"])))
    res = []
    for gid, t, S, d0, df in rows:
        df = df.dropna(subset=["m10"]).sort_values("m10", ascending=False).head(ROT)
        tot = float(df.m10.sum())
        ns = float(df.loc[df.arrival, "m10"].sum() / tot) if tot > 0 else np.nan
        res.append(
            {
                "game_id": gid,
                "team_id": t,
                "season": S,
                "game_date": d0,
                "n_rot": len(df),
                "n_arrivals_rot": int(df.arrival.sum()),
                "rot_minutes": tot,
                "NS": ns,
            }
        )
    return pd.DataFrame(res)


def _run_started_with_change(before: pd.DataFrame) -> bool:
    """True if the player's current same-team run began with a change from another team (so he is a mover)."""
    t = before.team_id.to_numpy()
    if len(t) < 2:
        return False
    i = len(t) - 1
    while i > 0 and t[i - 1] == t[-1]:
        i -= 1
    return i > 0  # there is an earlier appearance for another team right before the run


def identifiability(app: pd.DataFrame, pre: pd.DataFrame, tg: pd.DataFrame) -> dict[str, Any]:
    """Among appearances flagged as arrivals by the box (first K games after a change), how many were known from the preseason log?"""
    a = app[
        (app.season.isin(SEASONS))
        & (app.since_change_before.notna())
        & (app.since_change_before < K_ARRIVAL)
    ].copy()
    a = a[a.prev_team.notna()]
    # box arrival = since_change_before < K and run began with a change: recompute run start via prev_team of first run row
    # (since_change_before == 0 and prev_team != team) marks the change game; later games of the run have since_change_before >= 1
    pre_ids = pre.groupby(["season", "team_id"])["player_id"].apply(set).to_dict()
    a["pre_known"] = [
        p in pre_ids.get((s, t), set())
        for p, s, t in zip(a.player_id, a.season, a.team_id, strict=True)
    ]
    first = a[(a.since_change_before == 0)].merge(
        tg[["game_id", "team_id", "k"]], on=["game_id", "team_id"]
    )
    out: dict[str, Any] = {}
    for S in SEASONS:
        f = first[first.season == S]
        e = f[
            f.k < 5
        ]  # season-start movers: change appearance within the team's first 5 games (k is 0-based)
        o = f[f.k >= 5]
        out[str(S)] = {
            "change_games": int(len(f)),
            "known_from_preseason": int(f.pre_known.sum()),
            "share_known": float(f.pre_known.mean()) if len(f) else None,
            "first5_games": {
                "n": int(len(e)),
                "known": int(e.pre_known.sum()),
                "share": float(e.pre_known.mean()) if len(e) else None,
            },
            "later": {
                "n": int(len(o)),
                "known": int(o.pre_known.sum()),
                "share": float(o.pre_known.mean()) if len(o) else None,
            },
        }
    return out


def slice_counts(scores: pd.DataFrame, rows: dict[int, pd.DataFrame]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for S in SEASONS:
        sc = scores[scores.season == S]
        r = rows[S]
        cell: dict[str, Any] = {"team_games_total": int(len(sc)), "A0_rows_total": int(len(r))}
        for X in XS:
            keys = set(zip(sc.game_id[sc.NS >= X], sc.team_id[sc.NS >= X], strict=True))
            m = np.array([(g, t) in keys for g, t in zip(r.game_id, r.team_id, strict=True)])
            rr = r[m]
            wk = pd.to_datetime(rr.game_date).dt.strftime("%G-%V")
            cell[f"X={X:.2f}"] = {
                "team_games": int((sc.NS >= X).sum()),
                "teams": int(sc.team_id[sc.NS >= X].nunique()),
                "rows": int(len(rr)),
                "games": int(rr.game_id.nunique()),
                "player_seasons": int(rr.player_id.nunique()),
                "weeks": int(wk.nunique()),
            }
        cell["NS_quantiles"] = {q: float(sc.NS.quantile(q)) for q in (0.5, 0.75, 0.9, 0.95)}
        cell["NS_missing"] = int(sc.NS.isna().sum())
        out[str(S)] = cell
    return out


def premise_block(scores: pd.DataFrame, S: int) -> dict[str, Any]:
    """Label-free premise check: the stored forecast means summed over a team-game's A0 rows (played rows; membership caveat), slice vs rest."""
    r = arch.read_rows("pts", S, ["game_id", "team_id", "mean_prod", "min10"])
    s = (
        r.groupby(["game_id", "team_id"])
        .agg(sum_pts=("mean_prod", "sum"), sum_min10=("min10", "sum"), n=("min10", "size"))
        .reset_index()
    )
    j = s.merge(scores[scores.season == S][["game_id", "team_id", "NS"]], on=["game_id", "team_id"])
    hi, lo = j[j.NS >= X_DEFAULT], j[j.NS < X_DEFAULT]
    return {
        "slice": {
            "team_games": int(len(hi)),
            "sum_mean_pts": float(hi.sum_pts.mean()),
            "sum_min10": float(hi.sum_min10.mean()),
            "rows_per_team_game": float(hi.n.mean()),
        },
        "rest": {
            "team_games": int(len(lo)),
            "sum_mean_pts": float(lo.sum_pts.mean()),
            "sum_min10": float(lo.sum_min10.mean()),
            "rows_per_team_game": float(lo.n.mean()),
        },
        "note": "sum over rows that PLAYED (membership caveat); conditional-on-playing means; no outcome read",
    }


def power_block(
    scores: pd.DataFrame, X: float = X_DEFAULT, sigma_list: tuple[float, ...] = (0.05,)
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    prep: dict[tuple[int, str], tuple[np.ndarray, ...]] = {}
    for S in SEASONS:
        sc = scores[scores.season == S]
        keys = set(zip(sc.game_id[sc.NS >= X], sc.team_id[sc.NS >= X], strict=True))
        for st in STATS:
            df = arch.read_rows(
                st, S, ["game_id", "player_id", "team_id", "game_date", "c", "s", "p_ge"]
            )
            sel = np.flatnonzero(
                [(g, t) in keys for g, t in zip(df.game_id, df.team_id, strict=True)]
            )
            blocks = arch.month_blocks(df)
            q0 = arch.quantile_grid(df, sel, blocks)
            gid = df.game_id.to_numpy()[sel]
            ts = np.array([f"{S}-{t}" for t in df.team_id.to_numpy()[sel]])
            tgm = np.array(
                [f"{g}-{t}" for g, t in zip(gid, df.team_id.to_numpy()[sel], strict=True)]
            )
            ps = np.array([f"{S}-{p}" for p in df.player_id.to_numpy()[sel]])
            wk = np.array(
                [f"{S}-{w}" for w in pd.to_datetime(df.game_date.iloc[sel]).dt.strftime("%G-%V")]
            )
            prep[(S, st)] = (q0, ps, ts, gid, wk, tgm)
        log(f"power prep {S}")
    for S in SEASONS:
        for st in STATS:
            q0, ps, ts, gid, wk, tgm = prep[(S, st)]
            cell: dict[str, Any] = {
                "n_rows": int(len(q0)),
                "n_games": int(len(np.unique(gid))),
                "n_team_seasons": int(len(np.unique(ts))),
                "n_player_seasons": int(len(np.unique(ps))),
                "n_weeks": int(len(np.unique(wk))),
                "floor": FLOORS[st],
            }
            for sg in sigma_list:
                # shock shared within player-season (cap factor of one player) and within team-season (a team-wide cap factor)
                a = ev.noise_arm(q0, ps, gid, wk, sg, 1, arch.crps_int_q)
                b = ev.noise_arm(q0, ts, gid, wk, sg, 1, arch.crps_int_q)
                c = ev.noise_arm(q0, tgm, gid, wk, sg, 1, arch.crps_int_q)
                cell[f"noise_sigma{sg:.2f}"] = {
                    "teamgame_shock": {
                        "binding": c["binding"]["clustering"],
                        "half_width": c["binding"]["half_width"],
                    },
                    "player_shock": {
                        "binding": a["binding"]["clustering"],
                        "half_width": a["binding"]["half_width"],
                    },
                    "team_shock": {
                        "binding": b["binding"]["clustering"],
                        "half_width": b["binding"]["half_width"],
                    },
                }
            out[f"{S}|{st}"] = cell
            log(f"power {S} {st} done")
    return out


def run(out: Path, power: bool) -> dict[str, Any]:
    con = duckdb.connect(str(arch.DATA / "nba.duckdb"), read_only=True)
    games, box, pre = load(con)
    assert int(games.season.max()) <= 2024 and int(box.season.max()) <= 2024
    log(f"loaded games {len(games)} box {len(box)} preseason {len(pre)}")
    tg = team_games(games)
    app = asof_tables(box)
    scores = roster_scores(tg, app, pre)
    log(f"scores {len(scores)} team-games")
    rows = {
        S: arch.read_rows("pts", S, ["game_id", "player_id", "team_id", "game_date"])
        for S in SEASONS
    }
    rows = {S: r[r.game_id.str.startswith("002")] for S, r in rows.items()}
    res: dict[str, Any] = {
        "seasons_held": {
            "duckdb": sorted(int(x) for x in games.season.unique()),
            "history": "data/history 2013-2021 box scores (not read here)",
        },
        "preseason_coverage": {
            str(S): {
                "teams": int(pre[pre.season == S].team_id.nunique()),
                "player_rows": int((pre.season == S).sum()),
            }
            for S in sorted(pre.season.unique())
        },
        "slice_counts": slice_counts(scores, rows),
        "identifiability": identifiability(app, pre, tg),
        "premise": {str(S): premise_block(scores, S) for S in SEASONS},
        "season_2025_loaded": False,
    }
    if power:
        res["power"] = {f"X={X:.2f}": power_block(scores, X) for X in (0.10, 0.15, 0.20)}
    out.parent.mkdir(parents=True, exist_ok=True)
    scores.to_parquet(out.with_suffix(".scores.parquet"))
    out.write_text(json.dumps(res, indent=2, default=float))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-power", action="store_true")
    a = ap.parse_args(argv)
    run(Path(a.out), not a.no_power)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
