# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""QUESTIONABLE_CASCADE minimum-data gate and data-capture audit (docs/prereg/QUESTIONABLE_CASCADE.md, DRAFT).

    cd <worktree> && .venv/bin/python -W ignore -m research.eval.questionable_cascade_gate \
        --data-root <main checkout> --out reports/prereg_questionable_cascade/gate.json [--history]

Read-only on ``nba.duckdb`` (every query ``season <= 2024``; 2025 is never loaded). Nothing is fitted. Label-free except three disclosed
uses: (1) the play flag (minutes > 0) of Q/D players on the SELECT season to measure r_status; (2) realised minutes to bucket the missingness
audit; (3) the stored FULL-minus-A0 per-row CRPS deltas (already frozen and scored, T467) for sd and design effect only.

Parts: G1 capture and snapshot lead; G2 counts (rotation Q/D player-games, affected team-games, affected rows per stat-season); G3 clusters;
G4 sizing (half-width at the floor, rows needed, power of the pass line, three clusterings); G5 A0 reproduction to 1e-9 and the production
OUT-set tie; G6 feature-frame alignment; G7 missingness vs outcome; G8 staleness of the stored snapshot; G9 history audit (2019-21, never the gate).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C
from research.eval import questionable_cascade_features as F

FLOOR = -0.005
HW_BAR = 0.005
POWER_BAR = 0.5
SELECT, REPORT = 2023, 2024
SEASONS = (SELECT, REPORT)
STATS = ("pts", "reb", "ast", "fg3m")
DESC_EFFECT = 0.81  # T476 magnitude (pts per 33 mpg of a rotation teammate sitting), sign: teammates gain when he sits
DESC_MPG = 33.0
SCENARIO_F = (1.0, 0.5, 0.25)
REPRO_TOL = 1e-9
MIN_WEEKS = 20
STORED_COLS = {
    "min10": "row_min10",
    "m": "row_m",
    "n_prior": "row_n_prior",
    "since_flag": "row_since_flag",
    "gap_days": "row_gap_days",
    "abs_margin": "row_abs_margin",
    "starter10": "row_starter10",
}


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def load_report_reason(con: Any) -> pl.DataFrame:
    return C.query(
        con,
        "SELECT a.game_id, a.player_id, lower(a.status) AS status, a.reason, CAST(a.as_of AS TIMESTAMP) AS as_of "
        "FROM player_availability a JOIN games g ON g.game_id = a.game_id "
        f"WHERE a.source = '{C.REPORT_SOURCE}' AND a.player_id IS NOT NULL AND g.season <= 2024",
    )


def build_world(root: Path, t0: float) -> dict[str, Any]:
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.eval.lower_tail_eval import attach_realised
    from nba.props.context_residual import (
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
    )

    C.DB = root / "nba.duckdb"
    elo = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    games_f, pgs, static, avail = load_inputs(str(C.DB), None)
    assert int(games_f["season"].max()) <= C.MAX_SEASON
    flagged, _ = flagged_from_availability(avail, games_f, cfg.report, max_season=C.MAX_SEASON)
    feats = attach_realised(build_features(games_f, pgs, static, flagged, elo), games_f, pgs)
    assert int(feats["season"].max()) <= C.MAX_SEASON
    _log(f"feature frame {feats.height} rows", t0)
    con = C.connect()
    games = C.load_games(con)
    report = load_report_reason(con)
    con.close()
    snap = F.snapshot_with_reason(report, games)
    played, states = F.played_states(games_f, pgs)
    tagged = F.tagged_table(played, states, snap, games_f)
    _log(f"snapshot {snap.height} rows, rotation-listed {tagged.height}", t0)
    return {
        "games": games,
        "games_f": games_f,
        "pgs": pgs,
        "avail": avail,
        "flagged": flagged,
        "feats": feats,
        "report": report,
        "snap": snap,
        "played": played,
        "states": states,
        "tagged": tagged,
    }


def production_tie(w: dict[str, Any]) -> dict[str, Any]:
    """My status == 'out' rotation set must reproduce production's n_out_rot and vac_min for every (game, team) with a report."""
    from nba.props.context_residual import vacated_features

    tout = F.tagged_table(w["played"], w["states"], w["snap"], w["games_f"], ("out",))
    mine = tout.group_by(["game_id", "team_id"]).agg(
        pl.len().alias("n_mine"), pl.col("min10").sum().alias("vac_mine")
    )
    game_info = {
        str(g): (d, int(h), int(a))
        for g, d, h, a in w["games_f"]
        .select(["game_id", "game_date", "home_team", "away_team"])
        .iter_rows()
    }
    prod = vacated_features(w["played"], w["states"], w["flagged"], game_info)
    j = prod.join(mine, on=["game_id", "team_id"], how="full", coalesce=True).with_columns(
        pl.col("n_out_rot").fill_null(0),
        pl.col("vac_min").fill_null(0.0),
        pl.col("n_mine").fill_null(0),
        pl.col("vac_mine").fill_null(0.0),
    )
    # production only reports games with a usable report; keep those games
    rep = set(w["flagged"].keys())
    j = j.filter(pl.col("game_id").is_in(sorted(rep)))
    bad_n = int((j["n_out_rot"].cast(pl.Int64) != j["n_mine"].cast(pl.Int64)).sum())
    bad_v = int(((j["vac_min"] - j["vac_mine"]).abs() > REPRO_TOL).sum())
    return {
        "team_games_compared": j.height,
        "n_out_rot_differs": bad_n,
        "vac_min_differs": bad_v,
        "games_with_report": len(rep),
        "games_with_usable_snapshot": int(w["snap"]["game_id"].n_unique()),
    }


def a0_reproduction(w: dict[str, Any], root: Path) -> dict[str, Any]:
    """Rebuilt stat frame vs the stored A0 components: row set and each stored feature column to REPRO_TOL (NaN equal)."""
    from nba.eval.lower_tail_eval import load_components
    from nba.props.context_residual import stat_frame

    out: dict[str, Any] = {}
    for stat in STATS:
        c = load_components(stat, root / "reports/lower_tail")
        sf = stat_frame(w["feats"], stat).filter(pl.col("season").is_in(SEASONS))
        keys = pl.DataFrame(
            {"game_id": c["row_gid"].astype(str), "player_id": c["row_pid"].astype(np.int64)}
        ).with_row_index("i")
        j = keys.join(
            sf.select("game_id", pl.col("player_id").cast(pl.Int64), *STORED_COLS.keys()),
            on=["game_id", "player_id"],
            how="left",
        ).sort("i")
        missing = int(j["min10"].is_null().sum())
        rows_stored, rows_frame = keys.height, sf.height
        diffs: dict[str, float] = {}
        for col, sc in STORED_COLS.items():
            a = j[col].to_numpy().astype(float)
            b = c[sc].astype(float)
            ok = np.isnan(a) == np.isnan(b)
            d = np.where(np.isnan(a) | np.isnan(b), 0.0, np.abs(a - b))
            diffs[col] = float(d.max()) if ok.all() else float("inf")
        out[stat] = {
            "rows_stored": rows_stored,
            "rows_rebuilt_in_seasons": rows_frame,
            "stored_rows_missing_in_frame": missing,
            "max_abs_diff": diffs,
            "pass": bool(
                missing == 0 and rows_stored == rows_frame and max(diffs.values()) <= REPRO_TOL
            ),
        }
    return out


def staleness(w: dict[str, Any]) -> dict[str, Any]:
    """Lead of the stored snapshot for rotation Q/D players and how often the status changed between that snapshot and the last one stamped <= tip - 5 (POST-cutoff, descriptive only; never a feature)."""
    games = w["games"]
    late = F.snapshot_with_reason(w["report"], games, lead=5).select(
        "game_id", pl.col("player_id").cast(pl.Int64), pl.col("status").alias("status_t5")
    )
    t = w["tagged"].filter(pl.col("status").is_in(list(F.CASCADE_STATUSES)))
    sn = w["snap"].select("game_id", pl.col("player_id").cast(pl.Int64), "lead_min", "snap_as_of")
    t = t.join(sn, on=["game_id", "player_id"], how="left").join(
        late, on=["game_id", "player_id"], how="left"
    )
    season = dict(games.select("game_id", "season").rows())
    t = t.with_columns(pl.col("game_id").replace_strict(season, default=None).alias("season"))
    out: dict[str, Any] = {}
    for s in sorted(t["season"].drop_nulls().unique().to_list()):
        x = t.filter(pl.col("season") == s)
        per: dict[str, Any] = {}
        for st in F.CASCADE_STATUSES:
            y = x.filter(pl.col("status") == st)
            if y.height == 0:
                continue
            q = np.nanpercentile(y["lead_min"].to_numpy(), [10, 50, 90])
            per[st] = {
                "n": y.height,
                "lead_p10_p50_p90": [float(v) for v in q],
                "status_at_last_snapshot_before_tip-5": {
                    str(k): int(n)
                    for k, n in y.group_by(pl.col("status_t5").fill_null("unlisted"))
                    .len()
                    .sort("status_t5")
                    .rows()
                },
                "share_changed_by_t5": float((y["status_t5"].fill_null("unlisted") != st).mean()),
            }
        out[str(s)] = per
    return out


def lg(c: dict[str, np.ndarray], key: str) -> np.ndarray:
    return c[key]


def week_key(gdate: np.ndarray) -> np.ndarray:
    import datetime as dt

    out = []
    for d in gdate:
        iso = dt.date.fromisoformat(str(d)[:10]).isocalendar()
        out.append(f"{iso[0]}-{iso[1]:02d}")
    return np.array(out)


def clusters3(
    d: np.ndarray,
    m: np.ndarray,
    gid: np.ndarray,
    pid: np.ndarray,
    season: np.ndarray,
    wk: np.ndarray,
) -> dict[str, Any]:
    """sd and design effect of the mean of d on rows m under game, player-season and ISO-week clustering; the binding deff is the largest of the decisive ones."""
    ps = np.array([f"{p}_{s}" for p, s in zip(pid[m], season[m], strict=True)])
    stats = {
        "game": C.cluster_stats(d[m], gid[m]),
        "player_season": C.cluster_stats(d[m], ps),
        "week": C.cluster_stats(d[m], wk[m]),
    }
    decisive = {k: v for k, v in stats.items() if k != "week" or v["clusters"] >= MIN_WEEKS}
    binding = max(decisive, key=lambda k: decisive[k]["deff"])
    return {
        "per_clustering": {
            k: {"clusters": v["clusters"], "deff": v["deff"], "sd_row": v["sd_row"]}
            for k, v in stats.items()
        },
        "binding": binding,
        "sd_row": stats["game"]["sd_row"],
        "deff_binding": decisive[binding]["deff"],
        "top5_player_season_share": float(
            np.sort(np.unique(ps, return_counts=True)[1])[::-1][:5].sum() / max(m.sum(), 1)
        ),
    }


def sizing(w: dict[str, Any], root: Path, rates: dict[str, float], t0: float) -> dict[str, Any]:
    from nba.eval.lower_tail_eval import arm_grid, load_components
    from nba.props.context_residual import to_integer_support
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    pr_lt = root / "reports/lower_tail"
    pic = dict(np.load(pr_lt / "components_pi.npz", allow_pickle=True))
    RF.CACHE = root / "data/ops/recent_role_full_cache"
    RF.LT_DIR = pr_lt
    dates = dict(w["games"].select("game_id", "game_date").rows())
    team = w["feats"].select("game_id", pl.col("player_id").cast(pl.Int64), "team_id")
    reported = set(w["snap"]["game_id"].unique().to_list())
    res: dict[str, Any] = {}
    for stat in STATS:
        cs = load_components(stat, pr_lt)
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        keys = pl.DataFrame(
            {"game_id": cs["row_gid"].astype(str), "player_id": cs["row_pid"].astype(np.int64)}
        ).join(team, on=["game_id", "player_id"], how="left")
        cols = F.build_columns(keys, w["snap"], w["tagged"], rates, reported)
        gid, pid = cs["row_gid"].astype(str), cs["row_pid"].astype(np.int64)
        season, y = cs["row_season"], cs["row_y"]
        st = cols["qc_status"].to_numpy()
        has = ~np.isnan(st)
        st0 = np.nan_to_num(st, nan=-1.0)
        nq = np.nan_to_num(cols["qc_nq"].to_numpy())
        nd = np.nan_to_num(cols["qc_nd"].to_numpy())
        evac_min = np.nan_to_num(cols["qc_evac_min"].to_numpy())
        own_qd = np.isin(st0, (3.0, 4.0))
        tm = has & ((nq + nd) >= 1) & ~own_qd
        own = has & own_qd
        rest = ~tm & ~own
        wk = week_key(np.array([str(dates[g]) for g in gid]))
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        q1 = RF.arm_qi(
            stat, RF.load_heads(stat, "R"), pic, np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
        )
        d = PR.crps_int(q1, y) - PR.crps_int(
            q0, y
        )  # stored FULL - A0: sd and design effect only (frozen, T467)
        grid = arm_grid("mixture", cs, pic, stat) if stat == "pts" else None
        rotation = cs["row_min10"] >= 10.0
        per: dict[str, Any] = {}
        for s in (*SEASONS, "pooled"):
            ms = np.isin(season, SEASONS) if s == "pooled" else season == s
            block: dict[str, Any] = {
                "n_rows": int(ms.sum()),
                "games": int(len(np.unique(gid[ms]))),
            }
            for name, sel in (("teammate_affected", tm), ("own_QD_played", own), ("rest", rest)):
                m = ms & sel
                if m.sum() < 30:
                    block[name] = {"n_rows": int(m.sum()), "too_few": True}
                    continue
                cl = clusters3(d, m, gid, pid, season, wk)
                e_aff = None
                scen: dict[str, Any] = {}
                if stat == "pts" and name == "teammate_affected":
                    # Expected effect of C2 if the world were: teammates gain 0.81 pts per 33 min of a rotation teammate who SITS (T476 magnitude, post hoc,
                    # sign flipped); the arm sees only the expected sitting (select-season rates). Truth G = A0 shifted by f x that shift; f = 1 is the
                    # generous scenario (as RT2's gate), f = 0.5 and 0.25 are the sensitivity rows (estimation noise, a tag that is only a coin flip).
                    for f in SCENARIO_F:
                        b = f * DESC_EFFECT * np.minimum(evac_min / DESC_MPG, 2.0) * rotation
                        sel2 = m & (b != 0)
                        gain = np.zeros(len(y))
                        if sel2.any():
                            gain[sel2] = C.expected_crps_gain(
                                q0[sel2].astype(float),
                                to_integer_support(grid[sel2] + b[sel2, None]),
                            )
                        scen[str(f)] = float(-gain[m].mean())
                    e_aff = scen["1.0"]
                row = C.sizing_row(
                    f"{stat} {name} {s}",
                    cl["sd_row"],
                    cl["deff_binding"],
                    float(m.sum()),
                    FLOOR,
                    e_aff,
                )
                if scen:
                    row["scenarios"] = {
                        k: {
                            "expected_effect": v,
                            "power_of_pass_line": C.pass_power(
                                v, min(FLOOR, -row["halfwidth95"]), row["se"]
                            ),
                        }
                        for k, v in scen.items()
                    }
                row["clusters"] = cl["per_clustering"]
                row["binding_clustering"] = cl["binding"]
                row["top5_player_season_share"] = cl["top5_player_season_share"]
                row["affected_games"] = int(len(np.unique(gid[m])))
                row["mde80_effect"] = float(max(abs(FLOOR), row["halfwidth95"]) + 0.84 * row["se"])
                block[name] = row
            per[str(s)] = block
        # missingness audit (realised minutes buckets: labels used for bucketing only)
        mins = cs["row_minutes"]
        bucket = {
            "<5": mins < 5,
            "5-10": (mins >= 5) & (mins < 10),
            "10-20": (mins >= 10) & (mins < 20),
            ">=20": mins >= 20,
        }
        per["missingness"] = {
            "null_status_rows": int((~has).sum()),
            "null_status_share": float((~has).mean()),
            "affected_teammate_share_by_realised_minutes": {
                b: float(tm[mm].mean()) for b, mm in bucket.items()
            },
            "own_QD_share_by_realised_minutes": {
                b: float(own[mm].mean()) for b, mm in bucket.items()
            },
            "null_share_by_realised_minutes": {
                b: float((~has[mm]).mean()) for b, mm in bucket.items()
            },
            "mean_realised_over_min10_by_class": {
                name: float(
                    np.nanmean(
                        np.where(
                            sel & (cs["row_min10"] > 0),
                            mins / np.maximum(cs["row_min10"], 1e-9),
                            np.nan,
                        )
                    )
                )
                for name, sel in (("teammate_affected", tm), ("own_QD_played", own), ("rest", rest))
            },
        }
        res[stat] = per
        _log(f"sized {stat}", t0)
    return res


def gate_rows(sz: dict[str, Any]) -> list[dict[str, Any]]:
    g: list[dict[str, Any]] = []
    for stat in STATS:
        a = sz[stat][str(REPORT)]["teammate_affected"]
        g.append(
            {
                "check": f"{REPORT}: {stat} teammate-affected rows >= rows needed for half-width {HW_BAR}",
                "value": [a["n"], a["n_for_hw_at_floor"]],
                "pass": bool(a["n"] >= a["n_for_hw_at_floor"]),
                "decides": True,
            }
        )
        g.append(
            {
                "check": f"{REPORT}: {stat} teammate-affected half-width <= {HW_BAR}",
                "value": a["halfwidth95"],
                "pass": bool(a["halfwidth95"] <= HW_BAR),
                "decides": True,
            }
        )
    a = sz["pts"][str(REPORT)]["teammate_affected"]
    g.append(
        {
            "check": f"{REPORT}: pts power of the pass line min(floor, -half-width) >= {POWER_BAR}",
            "value": a["power_one_season"],
            "pass": bool(a["power_one_season"] >= POWER_BAR),
            "decides": True,
        }
    )
    for stat in STATS:
        o = sz[stat][str(REPORT)]["own_QD_played"]
        g.append(
            {
                "check": f"{REPORT}: {stat} own Q/D played rows half-width <= {HW_BAR} (secondary: expected effect ~0)",
                "value": o.get("halfwidth95"),
                "pass": bool(o.get("halfwidth95", 9) <= HW_BAR),
                "decides": False,
            }
        )
    for stat in STATS:
        r = sz[stat][str(REPORT)]["rest"]
        g.append(
            {
                "check": f"{REPORT}: {stat} rest rows half-width <= {HW_BAR} (no-harm guard resolvable)",
                "value": r["halfwidth95"],
                "pass": bool(r["halfwidth95"] <= HW_BAR),
                "decides": True,
            }
        )
    for s in ("pooled", str(SELECT)):
        a = sz["pts"][s]["teammate_affected"]
        g.append(
            {
                "check": f"{s}: pts teammate-affected half-width <= {HW_BAR} (informational)",
                "value": a["halfwidth95"],
                "pass": bool(a["halfwidth95"] <= HW_BAR),
                "decides": False,
            }
        )
    return g


def history_audit(root: Path, t0: float) -> dict[str, Any]:
    """Rotation Q/D counts on 2019-21 (history DB) + 2022-24, select-only context; never the gate."""
    import duckdb

    from research.eval import history_design_gate as H

    H.HIST_DB = root / "data/history/nba_history.duckdb"
    H.HIST_SCHEDULE = root / "data/history/schedule"
    wld = H.load_world()
    gm = wld["games"]
    sql = (
        "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.starter "
        "FROM player_game_stats s JOIN games g ON g.game_id = s.game_id WHERE g.season <= 2024"
    )
    parts = []
    for p in (C.DB, H.HIST_DB):
        cc = duckdb.connect(str(p), read_only=True)
        parts.append(cc.execute(sql).pl())
        cc.close()
    pgs = pl.concat(parts, how="vertical_relaxed")
    gsel = gm.select("game_id", "game_date", "season", "home_team", "away_team", "tip")
    rep = wld["report"].with_columns(pl.lit("").alias("reason"))
    snap = F.snapshot_with_reason(
        rep.select("game_id", "player_id", "status", "reason", "as_of"), gsel
    )
    played, states = F.played_states(gsel, pgs)
    tagged = F.tagged_table(played, states, snap, gsel)
    _log("history tagged table", t0)
    box = pgs
    season = dict(gsel.select("game_id", "season").rows())
    rates = {}
    for s in sorted(set(season.values())):
        rates[str(s)] = F.play_rates(tagged, box, season, int(s))
    return {
        "counts": F.summarize_counts(tagged, gsel),
        "play_rates_by_season": rates,
        "note": "history seasons never enter the gate; 2021-22 cadence differs (hourly reports)",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args(argv)
    root = a.data_root.resolve()
    t0 = time.time()
    w = build_world(root, t0)
    games = w["games"]
    seas = dict(games.select("game_id", "season").rows())
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "snapshot_id": F.SNAPSHOT_ID,
        "capture": {
            str(s): {
                "games": int((games["season"] == s).sum()),
                "games_usable_snapshot": int(
                    games.filter(pl.col("season") == s)["game_id"]
                    .is_in(w["snap"]["game_id"].unique().to_list())
                    .sum()
                ),
                "games_real_tip": int(games.filter(pl.col("season") == s)["tip_real"].sum()),
            }
            for s in sorted(games["season"].unique().to_list())
        },
        "counts": F.summarize_counts(w["tagged"], games),
    }
    # probable rotation counts (descriptive)
    tp = (
        w["tagged"]
        .filter(pl.col("status") == "probable")
        .with_columns(pl.col("game_id").replace_strict(seas, default=None).alias("season"))
    )
    out["rotation_probable"] = {
        str(s): int(tp.filter(pl.col("season") == s).height)
        for s in sorted(tp["season"].unique().to_list())
    }
    out["production_tie"] = production_tie(w)
    _log(f"production tie {out['production_tie']}", t0)
    rates_all = F.play_rates(w["tagged"], w["pgs"], seas, SELECT)
    out["select_season_play_rates"] = rates_all
    out["select_season_play_rates_other_seasons"] = {
        str(s): F.play_rates(w["tagged"], w["pgs"], seas, s) for s in (2022, 2024)
    }
    rates = F.rates_for_cascade(rates_all)
    out["rates_used"] = rates
    out["a0_reproduction"] = a0_reproduction(w, root)
    _log("a0 reproduction done", t0)
    out["staleness"] = staleness(w)
    sz = sizing(w, root, rates, t0)
    out["sizing"] = sz
    out["gate"] = gate_rows(sz)
    out["gate_pass"] = all(x["pass"] for x in out["gate"] if x["decides"])
    out["repro_pass"] = all(v["pass"] for v in out["a0_reproduction"].values())
    out["tie_pass"] = (
        out["production_tie"]["n_out_rot_differs"] == 0
        and out["production_tie"]["vac_min_differs"] == 0
    )
    if a.history:
        out["history_audit"] = history_audit(root, t0)
    out["wall_s"] = time.time() - t0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    print(
        json.dumps(
            {
                "gate": out["gate"],
                "gate_pass": out["gate_pass"],
                "repro_pass": out["repro_pass"],
                "tie_pass": out["tie_pass"],
            },
            indent=1,
            default=float,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
