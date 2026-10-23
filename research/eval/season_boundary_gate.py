# ruff: noqa: E501
# mypy: ignore-errors
"""SEASON_BOUNDARY minimum-data gate (rule: docs/prereg/SEASON_BOUNDARY.md, DRAFT). No arm is fitted; no scored outcome is read.

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore -m research.eval.season_boundary_gate [--out reports/prereg_season_boundary/gate.json] [--seeds 6] [--no-power]

Read-only; ``season <= 2024`` in every SQL; 2025 never queried. Stored A0 files are read for ids and the served forecast (``c, s, p_ge``), never
``y`` or realised ``minutes``; ``minutes_scores.npz`` is read for ``row_gid, row_pid, row_season`` and ``M1_mean, M1_q10, M1_q90`` only.
Box-score outcomes are read for (i) as-of states of PRIOR games, (ii) seasons <= 2022 (history 2013-2021 and the training season 2022) for the aging
table and the league-shift premise; the league rate of the select/report seasons 2023-24 is never summarised as a value (counts and identities only).
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

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_gate as amg
from research.eval import archetype_matchup_all_rule as amr
from research.eval import archetype_matchup_gate as g6
from research.eval import role_prior_salary_eval as ev
from research.eval import season_boundary_rule as R

ROOT = Path(os.environ.get("NBA_DATA_ROOT", "."))
OUT = Path("reports/prereg_season_boundary/gate.json")
ODDS = ROOT / "reports/odds_history"
SC5 = ["pts", "reb", "ast", "fg3m", "minutes"]
LEAGUE_COLS = ["pts", "reb", "ast", "fg3m", "fg3a", "fta"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def sha8(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


Q = (
    "select b.game_id, b.player_id, b.team_id, b.minutes, b.pts, b.reb, b.ast, b.fg3m, b.fg3a, b.fta, b.fga, b.oreb, b.tov, "
    "coalesce(b.starter, false) as starter, g.game_date, g.season from player_game_stats_played b join games g using (game_id) "
    "where g.season {w} and b.minutes > 0"
)


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    c = duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)
    h = duckdb.connect(str(ROOT / "data/history/nba_history.duckdb"), read_only=True)
    db = c.execute(Q.format(w="between 2022 and 2024")).df()
    hist = h.execute(Q.format(w="<= 2021")).df()
    bd = pd.concat(
        [x.execute("select player_id, birth_date from players_static").df() for x in (c, h)]
    ).drop_duplicates("player_id")
    bd["birth_date"] = pd.to_datetime(bd.birth_date)
    assert db.season.max() <= 2024
    return hist, db, bd.set_index("player_id").birth_date


def states(box: pd.DataFrame) -> pd.DataFrame:
    b = box.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)
    st = R.decayed_states(b.player_id.to_numpy(), b.season.to_numpy(), b[SC5].to_numpy(float))
    for j, s in enumerate(SC5):
        for key in ("m_this", "m10", "L0"):
            b[f"{key}_{s}"] = st[key][:, j]
    b["g"] = st["g"]
    b["n_prior_games"] = st["n_prior"]
    return b


def reg(box: pd.DataFrame) -> pd.DataFrame:
    return box[box.game_id.str.startswith("002")]


def aging_table(hist: pd.DataFrame, bd: pd.Series) -> dict[str, Any]:
    """Frozen-table candidate from seasons <= 2021 ONLY: league-neutral log ratio of per-game stat, season s vs s-1, by age at Oct 20 of s."""
    r = reg(hist)
    agg = (
        r.groupby(["player_id", "season"])
        .agg(n=("game_id", "size"), **{s: (s, "sum") for s in SC5})
        .reset_index()
    )
    cur = agg.merge(
        agg.assign(season=agg.season + 1), on=["player_id", "season"], suffixes=("", "_p")
    )
    cur = cur[(cur.n >= 20) & (cur.n_p >= 20)].copy()
    cur["age"] = (
        pd.to_datetime(cur.season.astype(str) + "-10-20") - cur.player_id.map(bd)
    ).dt.days / 365.25
    cur = cur[cur.age.notna()]
    cur["bin"] = np.clip(np.floor(cur.age), R.AGE_LO, R.AGE_HI).astype(int)
    out: dict[str, Any] = {"n_transitions_players": int(len(cur)), "bins": {}}
    for s in SC5:
        num = []
        for seas, d in cur.groupby("season"):
            tot = (d[s].sum() / d.n.sum()) / (d[f"{s}_p"].sum() / d.n_p.sum())
            for bn, e in d.groupby("bin"):
                num.append(
                    (
                        bn,
                        seas,
                        (e[s].sum() / e.n.sum()) / (e[f"{s}_p"].sum() / e.n_p.sum()) / tot,
                        len(e),
                    )
                )
        t = pd.DataFrame(num, columns=["bin", "season", "ratio", "n"])
        t["lr"] = np.log(t.ratio.clip(1e-3)) * t.n
        t = t.groupby("bin").agg(lr=("lr", "sum"), n=("n", "sum"))
        t["logdelta"] = t.lr / t.n
        out["bins"][s] = {
            int(i): {"n": int(r_.n), "logdelta": round(float(r_.logdelta), 4)}
            for i, r_ in t.iterrows()
        }
    return out


def premise(hist: pd.DataFrame, db: pd.DataFrame) -> dict[str, Any]:
    """League-shift premise on NON-scored seasons only (2014-2022): does the shrunk as-of league ratio at league game 20 predict the
    league rate over league games 21-80 better than 'no shift' (ratio 1)?"""
    allb = pd.concat([hist, db[db.season <= 2022]])
    r = reg(allb)
    rows = []
    for S in range(2014, 2023):
        cur, prev = r[r.season == S], r[r.season == S - 1]
        prate = {k: 36.0 * prev[k].sum() / prev.minutes.sum() for k in LEAGUE_COLS}
        gd = cur.sort_values("game_date")
        gids = gd.drop_duplicates("game_id").game_id.tolist()
        first20 = set(gids[:20])
        nxt = set(gids[20:80])
        a = gd[gd.game_id.isin(first20)]
        b = gd[gd.game_id.isin(nxt)]
        row = {"season": S}
        for k in LEAGUE_COLS:
            obs = 36.0 * a[k].sum() / a.minutes.sum()
            shr = (20 * obs + R.LEAGUE_C * prate[k]) / (20 + R.LEAGUE_C)
            tgt = 36.0 * b[k].sum() / b.minutes.sum()
            row[k] = {
                "open_vs_prev_logratio": round(
                    float(
                        np.log(
                            36.0
                            * gd[gd.game_id.isin(set(gids[:60]))][k].sum()
                            / gd[gd.game_id.isin(set(gids[:60]))].minutes.sum()
                            / prate[k]
                        )
                    ),
                    4,
                ),
                "err_update": round(float(abs(np.log(shr / tgt))), 4),
                "err_noshift": round(float(abs(np.log(prate[k] / tgt))), 4),
            }
        for nm, flag in (("min_starter", True), ("min_bench", False)):
            pv = prev[prev.starter == flag].minutes.mean()
            fa, fb = a[a.starter == flag], b[b.starter == flag]
            obs = fa.minutes.mean()
            shr = (20 * obs + R.LEAGUE_C * pv) / (20 + R.LEAGUE_C)
            tgt = fb.minutes.mean()
            m60 = gd[gd.game_id.isin(set(gids[:60])) & (gd.starter == flag)].minutes.mean()
            row[nm] = {
                "open_vs_prev_logratio": round(float(np.log(m60 / pv)), 4),
                "err_update": round(float(abs(np.log(shr / tgt))), 4),
                "err_noshift": round(float(abs(np.log(pv / tgt))), 4),
            }
        rows.append(row)
    summ = {}
    for k in [*LEAGUE_COLS, "min_starter", "min_bench"]:
        wins = sum(r_[k]["err_update"] < r_[k]["err_noshift"] for r_ in rows)
        summ[k] = {
            "update_beats_noshift": f"{wins}/{len(rows)}",
            "median_abs_open_vs_prev_logratio": round(
                float(np.median([abs(r_[k]["open_vs_prev_logratio"]) for r_ in rows])), 4
            ),
            "mean_err_update": round(float(np.mean([r_[k]["err_update"] for r_ in rows])), 4),
            "mean_err_noshift": round(float(np.mean([r_[k]["err_noshift"] for r_ in rows])), 4),
        }
    return {
        "per_season": rows,
        "summary": summ,
        "seasons_read": "2014-2022 (none is select or report)",
    }


def key_frames() -> dict[int, pd.DataFrame]:
    out = {}
    for S in (R.SELECT, R.REPORT):
        sets = []
        for st in R.STATS:
            o = arch.read_rows(st, S, ["game_id", "player_id"])
            o = o[o.game_id.str.startswith("002")]
            sets.append(set(zip(o.game_id, o.player_id, strict=True)))
        out[S] = pd.DataFrame(sorted(set.intersection(*sets)), columns=["game_id", "player_id"])
    return out


def slice_counts(
    keys: dict[int, pd.DataFrame], dbs: pd.DataFrame, bd: pd.Series, ms: dict[str, np.ndarray]
) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for S, k in keys.items():
        m = k.merge(
            dbs[["game_id", "player_id", "game_date", "season", "g", "L0_pts", "n_prior_games"]],
            on=["game_id", "player_id"],
            how="left",
        )
        assert m.g.notna().all()
        first20 = m[m.g < R.SLICE_G]
        ret = first20[first20.L0_pts.notna()]
        age_ok = ret.player_id.map(bd).notna().mean()
        wk = g6.weeks(ret, S)
        mk = set(
            zip(
                ms["row_gid"][ms["row_season"] == S],
                ms["row_pid"][ms["row_season"] == S],
                strict=True,
            )
        )
        mrow = ret[[(a, b) in mk for a, b in zip(ret.game_id, ret.player_id, strict=True)]]
        res[str(S)] = {
            "all_regular_rows_all_four_stats": int(len(k)),
            "first20_rows": int(len(first20)),
            "first20_returner_rows": int(len(ret)),
            "first20_rookie_or_new_rows": int(len(first20) - len(ret)),
            "returner_player_seasons": int(ret.player_id.nunique()),
            "returner_games": int(ret.game_id.nunique()),
            "returner_iso_weeks": int(len(np.unique(wk))),
            "returner_share_of_first20": round(len(ret) / max(len(first20), 1), 4),
            "birthdate_cover": round(float(age_ok), 4),
            "minutes_rows_in_returner_slice": int(len(mrow)),
            "minutes_rows_missing_vs_props_rows": int(len(ret) - len(mrow)),
            "by_g_bucket_rows": {
                b: int(((ret.g >= lo) & (ret.g < hi)).sum())
                for b, (lo, hi) in {"0-4": (0, 5), "5-9": (5, 10), "10-19": (10, 20)}.items()
            },
        }
    return res


def continuity_slice(
    keys: dict[int, pd.DataFrame], sdb: pd.DataFrame, db: pd.DataFrame
) -> dict[str, Any]:
    """REPORTED slice (not a decision cell): terciles of opening-day team roster continuity, split by player moved teams. Continuity of
    team T in season S = share of T's season S-1 regular-season minutes (for T) played by players who played for T in the S preseason log
    (known before game 1). Terciles of the 30 team values within each season (ties by team id). Counts only; no outcome."""
    c = duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)
    out: dict[str, Any] = {}
    for S in (R.SELECT, R.REPORT):
        pre = c.execute(
            "select distinct p.team_id, p.player_id from preseason_player_game_stats p join preseason_games g using (game_id) "
            "where g.season = ? and p.minutes > 0",
            [S],
        ).df()
        prev = (
            reg(db[db.season == S - 1])
            .groupby(["team_id", "player_id"])
            .minutes.sum()
            .reset_index()
        )
        tot = prev.groupby("team_id").minutes.sum()
        kept = prev.merge(pre, on=["team_id", "player_id"]).groupby("team_id").minutes.sum()
        cont = (kept.reindex(tot.index).fillna(0.0) / tot).sort_values(kind="stable")
        terc = (
            pd.Series(np.repeat([1, 2, 3], [10, 10, 10]), index=cont.index[:30])
            if len(cont) == 30
            else None
        )
        last_team = (
            reg(db[db.season == S - 1])
            .sort_values(["game_date", "game_id"])
            .groupby("player_id")
            .team_id.last()
        )
        m = keys[S].merge(
            sdb[["game_id", "player_id", "team_id", "g", "L0_pts"]], on=["game_id", "player_id"]
        )
        m = m[(m.g < R.SLICE_G) & m.L0_pts.notna()].copy()
        m["terc"] = m.team_id.map(terc) if terc is not None else np.nan
        m["moved"] = m.player_id.map(last_team) != m.team_id
        tab = {
            f"T{t}": {
                "stayers": int(((m.terc == t) & ~m.moved).sum()),
                "movers": int(((m.terc == t) & m.moved).sum()),
                "all": int((m.terc == t).sum()),
                "player_seasons": int(m[m.terc == t].player_id.nunique()),
            }
            for t in (1, 2, 3)
        }
        out[str(S)] = {
            "teams_with_continuity": int(len(cont)),
            "continuity_min_median_max": [
                round(float(cont.min()), 3),
                round(float(cont.median()), 3),
                round(float(cont.max()), 3),
            ],
            "tercile_cut_values": [round(float(cont.iloc[9]), 3), round(float(cont.iloc[19]), 3)],
            "rows_by_tercile": tab,
            "movers_total": int(m.moved.sum()),
            "rows": int(len(m)),
        }
    return out


def planted_future(box_db: pd.DataFrame, prev_rate: dict[int, dict[str, float]]) -> dict[str, Any]:
    """Edit every box row dated on or after a cut (x50 and extra rows); player states and the as-of league table for dates before the cut must be
    byte-identical; and the table at the cut itself must not move (it uses dates < cut)."""
    out = {}
    for S, cut in ((2023, "2023-11-15"), (2024, "2024-11-15")):
        cutd = pd.Timestamp(cut).date()
        a = box_db[box_db.season <= S]
        gd = pd.to_datetime(a.game_date).dt.date
        b = a.copy()
        fut = gd >= cutd
        for s in SC5 + ["fg3a", "fta"]:
            b.loc[fut, s] = b.loc[fut, s] * 50 + 7
        sa, sb = states(a), states(b)
        # at the cut date the state of a row ON the cut date uses only earlier games
        upto = pd.to_datetime(sa.game_date).dt.date <= cutd
        cols = [c for c in sa.columns if c.startswith(("m_this_", "m10_", "L0_"))] + ["g"]
        same_states = bool(
            np.allclose(
                sa.loc[upto, cols].to_numpy(float),
                sb.loc[upto, cols].to_numpy(float),
                equal_nan=True,
                rtol=0,
                atol=0,
            )
        )
        la = R.league_asof(reg(a[a.season == S]), LEAGUE_COLS, prev_rate[S])
        lb = R.league_asof(reg(b[b.season == S]), LEAGUE_COLS, prev_rate[S])
        idx = [d for d in la.index if pd.Timestamp(d).date() <= cutd]
        same_league = bool(np.array_equal(la.loc[idx].to_numpy(), lb.loc[idx].to_numpy()))
        out[str(S)] = {
            "cut": cut,
            "rows_edited": int(fut.sum()),
            "states_identical_through_cut_date": same_states,
            "league_table_identical_through_cut_date": same_league,
            "rows_compared": int(upto.sum()),
        }
    return out


def sigma_eff(
    sdf: pd.DataFrame, bd: pd.Series, age: dict[str, Any], S: int, k: float, lg: pd.DataFrame | None
) -> dict[str, Any]:
    r = reg(sdf[sdf.season == S])
    r = r[(r.g < R.SLICE_G) & r.L0_pts.notna()].copy()
    age_y = (pd.to_datetime(r.season.astype(str) + "-10-20") - r.player_id.map(bd)).dt.days / 365.25
    bins = np.clip(np.floor(age_y.fillna(27)), R.AGE_LO, R.AGE_HI).astype(int).to_numpy()
    out = {"n": int(len(r)), "k": k}
    for s in SC5:
        tab = age["bins"][s]
        ad = np.array(
            [
                tab.get(int(b), {"logdelta": 0.0})["logdelta"]
                if tab.get(int(b), {"n": 0})["n"] >= R.AGE_MIN_CELL
                else 0.0
                for b in bins
            ]
        )
        st = {
            "g": r.g.to_numpy(),
            "L0": r[f"L0_{s}"].to_numpy()[:, None],
            "m_this": r[f"m_this_{s}"].to_numpy()[:, None],
            "m10": r[f"m10_{s}"].to_numpy()[:, None],
        }
        m1 = R.a1_base(st, k, ad[:, None])[:, 0]
        m10 = r[f"m10_{s}"].to_numpy()
        lr = np.log((m1 + R.STAB) / (m10 + R.STAB))
        out[s] = {
            "sd_log_m1_over_m10": round(float(np.nanstd(lr)), 4),
            "mean_abs": round(float(np.nanmean(np.abs(lr))), 4),
            "mean_log": round(float(np.nanmean(lr)), 4),
        }
        if lg is not None and s in ("pts", "reb", "ast", "fg3m"):
            f = r.game_date.map(lambda d, col=f"f_{s}": lg[col].get(d, 1.0)).to_numpy(float)
            m2 = R.a1_base(st, k, ad[:, None], f[:, None])[:, 0]
            out[s]["sd_log_m2_over_m1"] = round(
                float(np.nanstd(np.log((m2 + R.STAB) / (m1 + R.STAB)))), 4
            )
            out[s]["league_f_range"] = [round(float(f.min()), 4), round(float(f.max()), 4)]
    return out


def crps_cont(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    tau = (np.arange(1, 200) - 0.5) / 199
    return 2.0 * np.mean(((y[:, None] < q) - tau[None, :]) * (q - y[:, None]), axis=1)


def minutes_power(
    S: int,
    ms: dict[str, np.ndarray],
    keys: pd.DataFrame,
    games: pd.DataFrame,
    seeds: int,
    sigma: float,
) -> dict[str, Any]:
    """Approximate stored grid: Normal(M1_mean, sd = (q90 - q10) / 2.563) clipped [0, 48] (the full minutes grid is not stored; stated)."""
    from scipy.stats import norm

    sel = ms["row_season"] == S
    df = pd.DataFrame(
        {
            "game_id": ms["row_gid"][sel],
            "player_id": ms["row_pid"][sel],
            "mu": ms["M1_mean"][sel],
            "q10": ms["M1_q10"][sel],
            "q90": ms["M1_q90"][sel],
        }
    )
    df = keys.merge(df, on=["game_id", "player_id"], how="inner").merge(
        games[["game_id", "game_date"]], on="game_id"
    )
    sd = np.maximum((df.q90 - df.q10).to_numpy() / 2.563, 0.5)
    tau = (np.arange(1, 200) - 0.5) / 199
    q0 = np.clip(df.mu.to_numpy()[:, None] + sd[:, None] * norm.ppf(tau)[None, :], 0, 48)
    ps = np.array([f"{S}-{p}" for p in df.player_id])
    gid = df.game_id.to_numpy()
    wk = g6.weeks(df, S)
    hws, pos = [], []
    for k in range(seeds):
        rng = np.random.default_rng(900 + k)
        ids, inv = np.unique(ps, return_inverse=True)
        z = rng.standard_normal(len(ids))[inv]
        u = rng.integers(0, 199, size=len(q0))
        y = q0[np.arange(len(q0)), u]
        d = crps_cont(np.clip(q0 * np.exp(sigma * z)[:, None], 0, 48), y) - crps_cont(q0, y)
        hws.append(ev.three_way(d, gid, ps, wk)["binding"]["half_width"])
    for k in range(3):
        rng = np.random.default_rng(950 + k)
        ids, inv = np.unique(ps, return_inverse=True)
        z = rng.standard_normal(len(ids))[inv]
        u = rng.integers(0, 199, size=len(q0))
        y = np.clip(q0[np.arange(len(q0)), u] * np.exp(0.20 * z), 0, 48)
        d = crps_cont(q0 * np.exp(0.20 * z)[:, None], y) - crps_cont(q0, y)
        c = ev.three_way(d, gid, ps, wk)["binding"]
        pos.append(
            {
                "point": c["point"],
                "hi": c["hi"],
                "clears": bool(c["hi"] < 0 and c["point"] <= -R.MIN_FLOOR),
            }
        )
    return {
        "n_rows": int(len(df)),
        "sigma": sigma,
        "hw_mean": float(np.mean(hws)),
        "hw_seeds": [float(x) for x in hws],
        "floor_min": R.MIN_FLOOR,
        "positive_control": pos,
    }


def props_power(
    S: int, keys: pd.DataFrame, seeds: int, sig_p: float, sig_w: float, sig_f: float
) -> dict[str, Any]:
    q0: dict[str, np.ndarray] = {}
    sub = None
    for st in R.STATS:
        q0[st], sub = amg.stat_inputs(st, S, keys)
    ps = np.array([f"{S}-{p}" for p in sub.player_id])
    game = sub.game_id.to_numpy()
    wk = g6.weeks(sub, S)
    out: dict[str, Any] = {"n_rows": int(len(keys)), "clusters": g6.cluster_counts(sub, S)}
    for name, grp, sg in (
        ("A1_player_season_shock", ps, sig_p),
        ("A2_minus_A1_week_shock", wk, sig_w),
        ("fed_by_minutes_player_shock", ps, sig_f),
    ):
        hw = []
        per_floor: dict[str, list[float]] = {s: [] for s in R.STATS}
        for k in range(seeds):
            d = {st: amg.noise_deltas(q0[st], grp, sg, amr.noise_seed(st, k)) for st in R.STATS}
            jc = amg.joint(d, game, ps, wk)
            hw.append(jc["binding"]["half_width"])
            for st in R.STATS:
                per_floor[st].append(
                    ev.three_way(d[st], game, ps, wk)["binding"]["half_width"] / R.FLOORS[st]
                )
            log(f"props {S} {name} seed {k + 1}/{seeds}")
        pos = []
        psg = 0.20 if "week" not in name else 0.10
        for k in range(3):
            d = {st: amg.sim_deltas(q0[st], grp, psg, 1.0, amr.pos_seed(st, k)) for st in R.STATS}
            jc = amg.joint(d, game, ps, wk)
            pos.append(
                {
                    "point": jc["binding"]["point"],
                    "hi": jc["binding"]["hi"],
                    "clears": bool(jc["binding"]["hi"] < 0 and jc["binding"]["point"] <= -1.0),
                }
            )
        out[name] = {
            "sigma": sg,
            "cm_hw_floor_units_mean": float(np.mean(hw)),
            "cm_hw_seeds": [round(float(x), 4) for x in hw],
            "per_stat_hw_floor_units_mean": {
                s: round(float(np.mean(v)), 3) for s, v in per_floor.items()
            },
            "positive_control": pos,
        }
    return out


def run(out: Path, power: bool, seeds: int) -> dict[str, Any]:
    t0 = time.time()
    hist, db, bd = load()
    log(f"loaded hist {len(hist)} db {len(db)} birth_dates {len(bd)}")
    res: dict[str, Any] = {
        "rule": R.ID,
        "status": "DRAFT gate, label-free",
        "data_version": {},
        "checks": [],
    }
    for p in sorted(ODDS.glob("model_rows_202[34]_*.parquet")):
        res["data_version"][p.name] = sha8(p)
    res["data_version"]["minutes_scores.npz"] = sha8(ROOT / "reports/minutes_v2/minutes_scores.npz")
    res["data_version"]["nba.duckdb_mtime"] = time.strftime(
        "%Y-%m-%d %H:%M", time.localtime((ROOT / "nba.duckdb").stat().st_mtime)
    )
    # seasons held
    res["audit_seasons_held"] = {
        "history_box": sorted(int(x) for x in hist.season.unique()),
        "db_box": sorted(int(x) for x in db.season.unique()),
        "history_rows": int(len(hist)),
        "db_rows": int(len(db)),
    }
    sdb = states(db)  # production data only (2022-24)
    log("states db done")
    sall = states(
        pd.concat([hist[hist.season >= 2018], db[db.season <= 2022]])
    )  # hist-chained, for the 2022 sigma only
    log("states chain done")
    age = aging_table(hist, bd)
    res["aging_table_from_seasons_le_2021"] = age
    res["premise_league_shift_nonscored_seasons"] = premise(hist, db)
    z = np.load(ROOT / "reports/minutes_v2/minutes_scores.npz", allow_pickle=True)
    ms = {k: z[k] for k in ("row_gid", "row_pid", "row_season", "M1_mean", "M1_q10", "M1_q90")}
    keys = key_frames()
    sc = slice_counts(keys, sdb, bd, ms)
    res["slice_counts"] = sc
    res["continuity_slice_reported"] = continuity_slice(keys, sdb, db)
    # league as-of coverage on slice dates (counts only)
    prev = {}
    for S in (2022, 2023, 2024):
        p = reg(db[db.season == S - 1]) if S > 2022 else reg(hist[hist.season == 2021])
        prev[S] = {k: 36.0 * p[k].sum() / p.minutes.sum() for k in LEAGUE_COLS}
    cov = {}
    for S in (2023, 2024):
        lg = R.league_asof(reg(db[db.season == S]), LEAGUE_COLS, prev[S])
        cov[str(S)] = {
            "dates": int(len(lg)),
            "nan_cells": int(lg.isna().sum().sum()),
            "first_date_f_is_one": bool(np.allclose(lg.iloc[0].to_numpy(), 1.0)),
        }
    res["league_asof_coverage"] = cov
    res["planted_future"] = planted_future(db, prev)
    # sigma on the non-scored 2022 season (hist-chained) and on 2023/24 as a sensitivity (A1 part uses own prior games only)
    lg22 = R.league_asof(reg(db[db.season == 2022]), LEAGUE_COLS, prev[2022])
    res["sigma_eff"] = {
        "2022_nonscored": sigma_eff(sall, bd, age, 2022, 10, lg22),
        "2023_A1_part_only": sigma_eff(sdb, bd, age, 2023, 10, None),
        "2024_A1_part_only": sigma_eff(sdb, bd, age, 2024, 10, None),
    }
    ids_hist21 = set(hist[hist.season == 2021].player_id)
    r22 = reg(db[db.season == 2022]).drop_duplicates("player_id")
    res["id_overlap_2022_players_in_hist2021"] = round(
        float(r22.player_id.isin(ids_hist21).mean()), 4
    )
    res["weights_a0_vs_a1"] = {
        str(g): {
            "A0_mass_on_this_season": round(float(R.a0_equivalent_weight(g)), 3),
            **{f"A1_k{k}": round(float(R.w_this(g, k)), 3) for k in R.K_GRID},
        }
        for g in (0, 1, 5, 10, 20, 40)
    }
    # checks
    for S in ("2023", "2024"):
        c = sc[S]
        res["checks"] += [
            chk(
                f"G1 returner first-20 rows per stat {S}",
                c["first20_returner_rows"],
                ">= 3000",
                c["first20_returner_rows"] >= 3000,
            ),
            chk(
                f"G1 player-seasons {S}",
                c["returner_player_seasons"],
                ">= 250",
                c["returner_player_seasons"] >= 250,
            ),
            chk(f"G1 ISO weeks {S}", c["returner_iso_weeks"], ">= 6", c["returner_iso_weeks"] >= 6),
            chk(
                f"G1m minutes rows match props rows {S}",
                c["minutes_rows_missing_vs_props_rows"],
                "<= 2% of rows",
                c["minutes_rows_missing_vs_props_rows"] <= 0.02 * c["first20_returner_rows"],
            ),
            chk(
                f"G3 birth_date coverage {S}",
                c["birthdate_cover"],
                ">= 0.98",
                c["birthdate_cover"] >= 0.98,
            ),
            chk(
                f"G2 league as-of NaN cells {S}", cov[S]["nan_cells"], "0", cov[S]["nan_cells"] == 0
            ),
            chk(
                f"G2 planted future identical {S}",
                res["planted_future"][S],
                "both identities True",
                res["planted_future"][S]["states_identical_through_cut_date"]
                and res["planted_future"][S]["league_table_identical_through_cut_date"],
            ),
        ]
    res["checks"].append(
        chk(
            "G2 id overlap hist2021 -> db2022",
            res["id_overlap_2022_players_in_hist2021"],
            ">= 0.95 of 2022 players (only used for the 2022 sigma)",
            res["id_overlap_2022_players_in_hist2021"] >= 0.95,
        )
    )
    pm = res["premise_league_shift_nonscored_seasons"]["summary"]
    wins = lambda k: int(pm[k]["update_beats_noshift"].split("/")[0])  # noqa: E731
    res["checks"].append(
        chk(
            "G4 (as first written) premise: update beats no-shift for fg3m itself, 2014-2022",
            pm["fg3m"]["update_beats_noshift"],
            ">= 6/9",
            wins("fg3m") >= 6,
        )
    )
    for stat, drv in {
        "pts": "pts",
        "reb": "reb",
        "ast": "ast",
        "fg3m": "fg3a",
        "minutes_starter": "min_starter",
        "minutes_bench": "min_bench",
    }.items():
        res["checks"].append(
            chk(
                f"G4' (amended driver map, post-hoc from non-scored seasons) {stat} <- {drv}",
                pm[drv]["update_beats_noshift"],
                ">= 6/9",
                wins(drv) >= 6,
            )
        )
    s22 = res["sigma_eff"]["2022_nonscored"]
    res["checks"].append(
        chk(
            "G5 arm not inert: sd log(m1/m10) pts, 2022 returners",
            s22["pts"]["sd_log_m1_over_m10"],
            ">= 0.02",
            s22["pts"]["sd_log_m1_over_m10"] >= 0.02,
        )
    )
    log(f"descriptive done {time.time() - t0:.0f}s")
    if power:
        res["power"] = {}
        for S in (2023, 2024):
            ret = sdb[(sdb.g < R.SLICE_G) & sdb.L0_pts.notna()][["game_id", "player_id"]]
            k = (
                keys[S]
                .merge(ret, on=["game_id", "player_id"])
                .sort_values(["game_id", "player_id"])
                .reset_index(drop=True)
            )
            games = db[["game_id", "game_date"]].drop_duplicates("game_id")
            res["power"][str(S)] = {
                "props": props_power(S, k, seeds, 0.05, 0.03, 0.02),
                "minutes": minutes_power(S, ms, k, games, seeds, 0.05),
            }
            pw = res["power"][str(S)]
            for nm in (
                "A1_player_season_shock",
                "A2_minus_A1_week_shock",
                "fed_by_minutes_player_shock",
            ):
                v = pw["props"][nm]
                res["checks"].append(
                    chk(
                        f"G6 {nm} composite half-width {S}",
                        round(v["cm_hw_floor_units_mean"], 3),
                        "<= 1.0 floor unit",
                        v["cm_hw_floor_units_mean"] <= 1.0,
                    )
                )
                res["checks"].append(
                    chk(
                        f"G6 {nm} positive control {S}",
                        [p["clears"] for p in v["positive_control"]],
                        "all clear",
                        all(p["clears"] for p in v["positive_control"]),
                    )
                )
            mv = pw["minutes"]
            res["checks"].append(
                chk(
                    f"G6 minutes half-width {S}",
                    round(mv["hw_mean"], 4),
                    f"<= {R.MIN_FLOOR} min",
                    mv["hw_mean"] <= R.MIN_FLOOR,
                )
            )
            res["checks"].append(
                chk(
                    f"G6 minutes positive control {S}",
                    [p["clears"] for p in mv["positive_control"]],
                    "all clear",
                    all(p["clears"] for p in mv["positive_control"]),
                )
            )
    res["all_pass"] = all(c["pass"] for c in res["checks"])
    res["wall_seconds"] = round(time.time() - t0)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    log(f"wrote {out}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--no-power", action="store_true")
    a = ap.parse_args(argv)
    run(Path(a.out), not a.no_power, a.seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
