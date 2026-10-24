# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS minimum-data gate (label-free; rule: docs/prereg/MARKET_ANCHORED_PROPS.md).

    .venv/bin/python -W ignore -m research.eval.market_anchored_props_gate --db <byte copy of odds_history.duckdb> [--k-null 10] [--boot 1000] [--out reports/prereg_market_anchored_props/gate.json]

The pinned extras frames (``reports/prereg_market_anchored_props/extras_{2023,2024}.parquet``, committed) are only hashed against
``core.EXTRAS_PINNED_SHA256``; they are rebuilt only with ``--rebuild-extras`` (the bytes are not reproducible, so a rebuild breaks the pin).

Read-only. ``data/odds/odds_history.duckdb`` is opened ``read_only=True`` with ``season = s`` for s in {2023, 2024} in every SQL; the
pairs frames are read WITHOUT the label columns (``y``, ``over``). Every outcome used below is synthetic (a fixed draw from a planted
world on the real legs), so the nested arms, the placebo and the bootstrap run end to end on real rows and real features with a
known truth. Season 2025 is refused.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from research.eval import market_anchored_props_core as core
from research.eval import market_prior_residual_eval as mpr
from research.eval import market_prior_residual_rule as rule

OUT = Path("reports/prereg_market_anchored_props/gate.json")
SEEDS = {"world_labels": 20261023, "boot": rule.SEED, "placebo": 1000}
MPR_GATE = {  # MARKET_PRIOR_RESIDUAL gate G2, quoted from docs/prereg/MARKET_PRIOR_RESIDUAL.md section 8
    "2023": {"pooled": 35146, "pts": 12189, "reb": 6339, "ast": 6714, "fg3m": 9904},
    "2024": {"pooled": 37528, "pts": 13021, "reb": 6645, "ast": 6771, "fg3m": 11091},
}


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def sha_pins() -> dict[str, Any]:
    out = {}
    for s, want in mpr.PINNED_SHA256.items():
        got = rule.file_sha256(mpr.resolve(mpr.PAIRS_REL.format(season=s)))
        out[str(s)] = {"sha256": got, "pin": want, "equal": got == want}
    return out


def calibrate_gamma(A: dict[str, np.ndarray], target: float, mask: np.ndarray) -> float:
    """gamma with oracle KL(market + gamma*delta || market) = target on the masked legs (bisection)."""
    lo, hi = 0.0, 2.0
    m, d = A["m"][mask], A["delta"][mask]
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if core.oracle_kl(core.sigmoid(m + mid * d), core.sigmoid(m)) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def run_world(
    A: dict[str, np.ndarray],
    eta: np.ndarray,
    seed: int,
    k: int,
    b: int,
    perms: np.ndarray,
    name: str,
) -> dict[str, Any]:
    y = core.draw_labels(eta, seed)
    t0 = time.time()
    sel = core.walk_forward(A, y, 2023, (2023,))
    rep = core.walk_forward(A, y, 2024, (2023, 2024), perms=perms[:k])
    d_sel = core.decompose(A, sel, b=b)
    d_rep = core.decompose(A, rep, b=b)
    mask24 = A["season"] == 2024
    p_true, p_m = core.sigmoid(eta), core.sigmoid(A["m"])
    res = {
        "world": name, "seed": seed, "oracle_kl_2024": core.oracle_kl(p_true[mask24], p_m[mask24]),
        "select_2023": {k_: {kk: v[kk] for kk in ("mean", "lo", "hi", "n")} for k_, v in d_sel.items() if isinstance(v, dict) and "mean" in v},
        "report_2024": {k_: {kk: v[kk] for kk in ("mean", "lo", "hi", "n")} for k_, v in d_rep.items() if isinstance(v, dict) and "mean" in v},
        "verdict": core.verdict(d_rep, d_sel), "seconds": round(time.time() - t0, 1),
        "placebo_draw_means_ll": rep["pl_draw_means"],
    }  # fmt: skip
    gam = rep["coefs"][-1]["beta_mm"][len(core.MR_COLS) : len(core.MR_COLS) + len(core.STATS)]
    res["gamma0_last_month_by_stat"] = gam
    return res


def planted_future(A: dict[str, np.ndarray], y: np.ndarray) -> dict[str, Any]:
    """Flip labels on/after a test month's first day: that month's predictions must not move; flip inside the training window: must move."""
    test_month = np.datetime64("2025-01")  # season label 2024 = the 2024-25 season
    start = test_month.astype("datetime64[D]")
    base = core.walk_forward(A, y, 2024, (2023, 2024))
    in_month = (A["season"] == 2024) & (A["month"] == test_month)
    y1 = y.copy()
    y1[A["date"] >= start] = 1.0 - y1[A["date"] >= start]
    f = core.walk_forward(A, y1, 2024, (2023, 2024))
    y2 = y.copy()
    train_rows = (A["date"] < start) & (A["season"] == 2023)
    y2[train_rows] = 1.0 - y2[train_rows]
    g = core.walk_forward(A, y2, 2024, (2023, 2024))
    same = float(np.nanmax(np.abs(base["lp_mm"][in_month] - f["lp_mm"][in_month])))
    same_mr = float(np.nanmax(np.abs(base["lp_mr"][in_month] - f["lp_mr"][in_month])))
    moved = float(np.nanmax(np.abs(base["lp_mm"][in_month] - g["lp_mm"][in_month])))
    return {
        "test_month": str(test_month),
        "n_rows": int(in_month.sum()),
        "max_abs_diff_future_flip_mm": same,
        "max_abs_diff_future_flip_mr": same_mr,
        "max_abs_diff_train_flip_mm": moved,
    }


def info_budget(A: dict[str, np.ndarray]) -> dict[str, Any]:
    """Label-free: the log-loss gain an oracle of coefficient gamma on delta would earn, per stat and season (0.5 gamma^2 E[p(1-p) delta^2])."""
    out: dict[str, Any] = {}
    for s in (2023, 2024):
        for si, st in enumerate(core.STATS):
            k = (A["season"] == s) & (A["stat_i"] == si)
            p = core.sigmoid(A["m"][k])
            d = A["delta"][k]
            out[f"{s}_{st}"] = {
                "n": int(k.sum()), "sd_delta": float(d.std()), "mean_p1mp_d2": float(np.mean(p * (1 - p) * d * d)),
                "oracle_gain_gamma_0.10": float(-0.5 * 0.10**2 * np.mean(p * (1 - p) * d * d)),
                "oracle_gain_gamma_0.25": float(-0.5 * 0.25**2 * np.mean(p * (1 - p) * d * d)),
                "oracle_gain_gamma_0.50": float(-0.5 * 0.50**2 * np.mean(p * (1 - p) * d * d)),
            }  # fmt: skip
    return out


def data_capture(db_path: str) -> dict[str, Any]:
    """Counts for the capture audit (non-negotiable 8): raw odds that exist beyond the legs this rule uses. Season in {2023, 2024} in SQL."""
    con = duckdb.connect(db_path, read_only=True)

    def q(sql: str) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in con.execute(sql).fetchall()]

    mk = "('player_points','player_rebounds','player_assists','player_threes')"
    out: dict[str, Any] = {}
    out["last_update_after_snapshot_share_pinnacle_t60"] = {
        str(r[0]): [r[1], r[2]]
        for r in q(
            f"select season, sum(case when last_update > snapshot_ts then 1 else 0 end), count(*) from odds_history where season in (2023, 2024) and snapshot_kind='t60' and book='pinnacle' and market in {mk} group by 1 order by 1"
        )
    }
    out["snapshots_per_game_kind"] = [
        list(r)
        for r in q(
            "select season, snapshot_kind, count(*), count(distinct game_id) from odds_history where season in (2023, 2024) group by 1,2 order by 1,2"
        )
    ]
    out["exchange_books_t60_two_sided_legs_2024"] = [
        list(r)
        for r in q(
            f"""with b as (select game_id, player_id, market, book, point, max(case when side='over' then 1 end) o, max(case when side='under' then 1 end) u from odds_history where season=2024 and snapshot_kind='t60' and market in {mk} and book in ('novig','prophetx') and player_id is not null group by 1,2,3,4,5) select book, count(*), count(distinct game_id) from b where o=1 and u=1 group by 1 order by 1"""
        )
    ]
    out["alt_rungs_t60_by_book_side_2023_2024"] = [
        list(r)
        for r in q(
            "select season, book, side, count(*) from odds_history where season in (2023, 2024) and snapshot_kind='t60' and book in ('draftkings','fanduel') and market in ('player_points_alternate','player_rebounds_alternate','player_assists_alternate','player_threes_alternate') group by 1,2,3 order by 1,2,3"
        )
    ]
    alt = "('player_points_alternate','player_rebounds_alternate','player_assists_alternate','player_threes_alternate')"
    out["stage2_fanduel_two_sided_rungs_off_pinnacle_line"] = [
        list(r)
        for r in q(f"""
        with pin as (select season, game_id, player_id, replace(market,'player_','') mk, point pl, max(case when side='over' then 1 end) o, max(case when side='under' then 1 end) u
                     from odds_history where season in (2023, 2024) and snapshot_kind='t60' and book='pinnacle' and market in {mk} and player_id is not null group by 1,2,3,4,5 having o=1 and u=1),
        fd as (select season, game_id, player_id, replace(replace(market,'player_',''),'_alternate','') mk, point, max(case when side='over' then 1 end) o, max(case when side='under' then 1 end) u
               from odds_history where season in (2023, 2024) and snapshot_kind='t60' and book='fanduel' and market in {alt} and player_id is not null group by 1,2,3,4,5 having o=1 and u=1)
        select f.season, f.mk, count(*), count(distinct (f.game_id, f.player_id)), count(distinct f.game_id) from fd f join pin p using (season, game_id, player_id, mk) where f.point <> p.pl group by 1,2 order by 1,2""")
    ]
    out["exchange_two_sided_on_pinnacle_line_2024"] = [
        list(r)
        for r in q(f"""
        with pin as (select game_id, player_id, market, point pl, max(case when side='over' then 1 end) o, max(case when side='under' then 1 end) u
                     from odds_history where season=2024 and snapshot_kind='t60' and book='pinnacle' and market in {mk} and player_id is not null group by 1,2,3,4 having o=1 and u=1),
        ex as (select game_id, player_id, market, book, point, max(case when side='over' then 1 end) o, max(case when side='under' then 1 end) u
               from odds_history where season=2024 and snapshot_kind='t60' and book in ('novig','prophetx') and market in {mk} and player_id is not null group by 1,2,3,4,5 having o=1 and u=1)
        select e.book, count(*), count(distinct e.game_id) from ex e join pin p using (game_id, player_id, market) where e.point = p.pl group by 1 order by 1""")
    ]
    out["rows_before_2023"] = q("select count(*) from odds_history where season < 2023")[0][0]
    con.close()
    return out


def calibrate_coef(
    A: dict[str, np.ndarray], z: np.ndarray, target: float, mask: np.ndarray
) -> float:
    """c with oracle KL(sigmoid(m + c z) || market) = target on the masked legs (bisection, c >= 0)."""
    lo, hi = 0.0, 20.0
    m, zz = A["m"][mask], z[mask]
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if core.oracle_kl(core.sigmoid(m + mid * zz), core.sigmoid(m)) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def run(
    k_null: int,
    boot: int,
    out: Path,
    reps: int = 6,
    db_path: str | None = None,
    extras_dir: str = "reports/prereg_market_anchored_props",
    rebuild_extras: bool = False,
) -> dict[str, Any]:
    t_start = time.time()
    db = db_path or str(mpr.resolve(core.ODDS_DB))
    if rebuild_extras:
        core._log(f"REBUILDING the extras frames from {db} (the new bytes will not match the pins)")
        extras_sha = core.build_extras(str(rule.ROOT / extras_dir), db, overwrite=True)
    else:
        core._log("hashing the pinned extras frames (no rebuild)")
        extras_sha = {
            str(s): core.check_extras_pin(s, mpr.resolve(core.EXTRAS_REL.format(season=s)))
            for s in (2023, 2024)
        }
    core._log("loading legs")
    legs, counts = core.load_legs()
    A = core.to_arrays(legs)
    n = len(A["m"])
    checks: list[dict[str, Any]] = []
    res: dict[str, Any] = {
        "counts": counts,
        "n_legs": n,
        "pins": sha_pins(),
        "seeds": SEEDS,
        "extras_sha256": extras_sha,
    }

    # G1/G2 legs and clusters against the MARKET_PRIOR_RESIDUAL gate
    per: dict[str, dict[str, int]] = {}
    for sv in (2023, 2024):
        per[str(sv)] = {
            "pooled": int((A["season"] == sv).sum()),
            **{
                st: int(((A["season"] == sv) & (A["stat_i"] == i)).sum())
                for i, st in enumerate(core.STATS)
            },
        }
    res["legs_by_season_stat"] = per
    for s in ("2023", "2024"):
        checks.append(
            chk(
                f"G1 legs {s} pooled equals MARKET_PRIOR_RESIDUAL G2",
                per[s]["pooled"],
                f"== {MPR_GATE[s]['pooled']}",
                per[s]["pooled"] == MPR_GATE[s]["pooled"],
            )
        )
        checks.append(
            chk(
                f"G1 per-stat legs {s} >= 5,000",
                {k_: per[s][k_] for k_ in core.STATS},
                ">= 5000",
                all(per[s][k_] >= 5000 for k_ in core.STATS),
            )
        )
    clus = {}
    for sv in (2023, 2024):
        m_ = A["season"] == sv
        clus[str(sv)] = {
            "games": int(len(np.unique(A["game_idx"][m_]))),
            "player_seasons": int(len(np.unique(A["ps_idx"][m_]))),
            "weeks": int(len(np.unique(A["week_idx"][m_]))),
        }
        c = clus[str(sv)]
        checks.append(
            chk(
                f"G2 clusters {sv} (game/player-season/week)",
                c,
                ">= 1000 / 200 / 20",
                c["games"] >= 1000 and c["player_seasons"] >= 200 and c["weeks"] >= 20,
            )
        )
    res["clusters"] = clus

    # G3 covariate support
    cov: dict[str, Any] = {}
    for sv in (2023, 2024):
        for si, st in enumerate(core.STATS):
            k = (A["season"] == sv) & (A["stat_i"] == si)
            cov[f"{sv}_{st}"] = {
                "n": int(k.sum()), "share_u1_available": float(1 - A["miss_u1"][k].mean()), "share_spread_available": float((A["bl"][k] > 0).mean()),
                "share_hi": float(A["hi"][k].mean()), "n_bench": int(A["bench"][k].sum()), "n_tmout": int(A["tmout"][k].sum()),
                "n_bench_and_tmout": int((A["bench"][k] * A["tmout"][k]).sum()), "mean_disp10_when_available": float(A["disp10"][k][A["miss_u1"][k] == 0].mean()),
            }  # fmt: skip
    res["covariate_support"] = cov
    min_cells = min(min(v["n_bench"], v["n_tmout"]) for v in cov.values())
    min_u1 = min(v["share_u1_available"] for v in cov.values())
    min_sp = min(v["share_spread_available"] for v in cov.values())
    checks.append(
        chk(
            "G3 smallest bench / teammate-out cell over stat-seasons",
            min_cells,
            ">= 300",
            min_cells >= 300,
        )
    )
    checks.append(
        chk(
            "G3 soft-book gap u1 available (min share over stat-seasons)",
            round(min_u1, 4),
            ">= 0.80",
            min_u1 >= 0.80,
        )
    )
    checks.append(
        chk("G3 game spread available (min share)", round(min_sp, 4), ">= 0.95", min_sp >= 0.95)
    )
    checks.append(
        chk(
            "G3 hi-line share in [0.10, 0.60] for every stat-season",
            {k_: round(v["share_hi"], 3) for k_, v in cov.items()},
            "in [0.10, 0.60]",
            all(0.10 <= v["share_hi"] <= 0.60 for v in cov.values()),
        )
    )

    # G4 delta support
    dl = {
        "share_clipped": float((np.abs(A["a"] - A["m"]) > core.DCLIP).mean()),
        "sd": float(A["delta"].std()),
        "p01": float(np.percentile(A["delta"], 1)),
        "p99": float(np.percentile(A["delta"], 99)),
    }
    res["delta"] = dl
    checks.append(
        chk(
            "G4 share of legs with |a-m| beyond the clip",
            round(dl["share_clipped"], 4),
            "<= 0.10",
            dl["share_clipped"] <= 0.10,
        )
    )
    res["information_budget"] = info_budget(A)

    # G5 known-at
    lead = rule.lead_minutes(legs["snapshot_ts"].to_numpy(), legs["tipoff_utc"].to_numpy())
    res["lead_minutes"] = {
        "min": float(np.min(lead)),
        "median": float(np.median(lead)),
        "max": float(np.max(lead)),
    }
    checks.append(
        chk(
            "G5 minimum lead of the Pinnacle snapshot before the real tip",
            round(float(np.min(lead)), 2),
            ">= 60 min",
            float(np.min(lead)) >= 60.0,
        )
    )

    # worlds
    perms = core.derangements(A, max(k_null, 1), seed=SEEDS["placebo"])
    mask24 = A["season"] == 2024
    g11 = calibrate_gamma(A, 0.0011, mask24)
    g30 = calibrate_gamma(A, 0.0030, mask24)
    res["planted_gamma"] = {"oracle_0.0011": g11, "oracle_0.0030": g30}
    spec = [
        ("W0_null", core.world_eta(A, "null"), 10),
        ("W_mkt_only", core.world_eta(A, "mkt"), 20),
        ("W_mod_oracle_0.0011", core.world_eta(A, "mod", g11), 30),
        ("W_mod_oracle_0.0030", core.world_eta(A, "mod", g30), 40),
        ("W_both", core.world_eta(A, "both", g11), 50),
    ]
    off_coef: dict[str, float] = {}
    for i, kind in enumerate(core.OFF_SPAN_KINDS):
        z = core.off_span_z(A, kind)
        off_coef[kind] = calibrate_coef(A, z, 0.002, mask24)
        spec.append((f"W_off_{kind}_0.0020", A["m"] + off_coef[kind] * z, 60 + 10 * i))
    res["off_span_coef"] = off_coef
    worlds: dict[str, list[dict[str, Any]]] = {}
    for name, eta, off in spec:
        worlds[name] = []
        for r in range(reps):
            core._log(f"world {name} replicate {r + 1}/{reps}")
            worlds[name].append(
                run_world(A, eta, SEEDS["world_labels"] + off + r, k_null, boot, perms, name)
            )
    res["worlds"] = worlds

    def rate(name: str, f: Any) -> float:
        return float(np.mean([bool(f(x)) for x in worlds[name]]))

    def mean_of(name: str, comp: str, season: str = "report_2024") -> float:
        return float(np.mean([x[season][comp]["mean"] for x in worlds[name]]))

    summary: dict[str, dict[str, Any]] = {
        name: {
            "oracle_kl_2024": float(np.mean([x["oracle_kl_2024"] for x in worlds[name]])),
            "mean_C_cal": mean_of(name, "C_cal"), "mean_C_mktx": mean_of(name, "C_mktx"), "mean_C_mod_first": mean_of(name, "C_mod_first"),
            "mean_C_mkt": mean_of(name, "C_mkt"), "mean_C_mkt_after": mean_of(name, "C_mkt_after"), "mean_C_mod": mean_of(name, "C_mod"), "mean_C_total": mean_of(name, "C_total"), "mean_C_pl": mean_of(name, "C_pl"),
            "select_mean_C_total": mean_of(name, "C_total", "select_2023"),
            "model_words": {w_: sum(1 for x in worlds[name] if x["verdict"]["model"] == w_) for w_ in sorted({x["verdict"]["model"] for x in worlds[name]})},
            "market_words": {w_: sum(1 for x in worlds[name] if x["verdict"]["market"] == w_) for w_ in sorted({x["verdict"]["market"] for x in worlds[name]})},
        }
        for name in worlds
    }  # fmt: skip
    res["world_summary"] = summary

    # planted future on the null world's labels
    y0 = core.draw_labels(core.world_eta(A, "null"), SEEDS["world_labels"] + 10)
    pf = planted_future(A, y0)
    res["planted_future"] = pf
    checks.append(
        chk(
            "G6 planted future: predictions of the test month unchanged when labels on/after it are flipped (MM and MR)",
            [pf["max_abs_diff_future_flip_mm"], pf["max_abs_diff_future_flip_mr"]],
            "== 0",
            pf["max_abs_diff_future_flip_mm"] == 0.0 and pf["max_abs_diff_future_flip_mr"] == 0.0,
        )
    )
    checks.append(
        chk(
            "G6 flipping labels inside the training window moves them",
            pf["max_abs_diff_train_flip_mm"],
            "> 0.01",
            pf["max_abs_diff_train_flip_mm"] > 0.01,
        )
    )

    # G7 positive and negative controls (detection rates over replicate label draws on the same legs)
    detected = ("DETECTED_BELOW_INFO_FLOOR", "INFO", "BET_FLOOR_PASS")
    fp0 = rate(
        "W0_null",
        lambda x: (
            x["verdict"]["model"] != "NONE"
            or x["verdict"]["market"] != "NONE"
            or x["verdict"]["override"] != ""
        ),
    )
    checks.append(
        chk(
            "G7a null world: false-positive rate of any word (replicates)",
            [fp0, reps],
            "<= 0.20",
            fp0 <= 0.20,
        )
    )
    cost = [mean_of("W0_null", "C_total"), mean_of("W0_null", "C_total", "select_2023")]
    checks.append(
        chk(
            "G7a null world: overfit cost C_total (MM minus M), mean over replicates, 2024 / 2023",
            [round(c, 5) for c in cost],
            "<= +0.0010",
            max(cost) <= 0.0010,
        )
    )
    mk_hit = rate("W_mkt_only", lambda x: x["verdict"]["market"].startswith("MISCALIBRATION"))
    mk_false = rate("W_mkt_only", lambda x: x["verdict"]["model"] in detected)
    checks.append(
        chk(
            "G7b market-only world: market word fires / model word falsely fires (rates)",
            [mk_hit, mk_false, round(summary["W_mkt_only"]["oracle_kl_2024"], 5)],
            "fires >= 0.80 and false attribution <= 0.20",
            mk_hit >= 0.80 and mk_false <= 0.20,
        )
    )
    fa = max(
        rate(n_, lambda x: x["verdict"]["market"].startswith("MISCALIBRATION"))
        for n_ in ("W_mod_oracle_0.0011", "W_mod_oracle_0.0030")
    )
    checks.append(
        chk(
            "G7f model-informed worlds: the market word falsely fires (max rate over the two worlds)",
            fa,
            "<= 0.20",
            fa <= 0.20,
        )
    )
    off_rows = {}
    for kind in core.OFF_SPAN_KINDS:
        nm = f"W_off_{kind}_0.0020"
        off_rows[kind] = [
            rate(nm, lambda x: x["verdict"]["model"] in detected),
            rate(nm, lambda x: x["report_2024"]["C_mod"]["hi"] < 0),
            round(summary[nm]["mean_C_mod"], 5),
            round(summary[nm]["mean_C_mkt"], 5),
        ]
    checks.append(
        chk(
            "G7g off-span market errors (bench, tmout, disp10, curvature in m, line level; each oracle 0.0020, model inert): "
            "[false model word rate, C_mod CI<0 rate, mean C_mod, mean C_mkt] per kind",
            off_rows,
            "false model word <= 0.20 and C_mod CI<0 <= 0.20 in every world",
            all(v[0] <= 0.20 and v[1] <= 0.20 for v in off_rows.values()),
        )
    )
    d11 = rate("W_mod_oracle_0.0011", lambda x: x["verdict"]["model"] in detected)
    checks.append(
        chk(
            "G7c planted model information at oracle 0.0011 (H2's largest weight): detection rate; mean C_mod; oracle",
            [
                d11,
                round(summary["W_mod_oracle_0.0011"]["mean_C_mod"], 5),
                round(summary["W_mod_oracle_0.0011"]["oracle_kl_2024"], 5),
            ],
            "detection >= 0.80",
            d11 >= 0.80,
        )
    )
    d30 = rate("W_mod_oracle_0.0030", lambda x: x["verdict"]["model"] in ("INFO", "BET_FLOOR_PASS"))
    checks.append(
        chk(
            "G7d planted model information at oracle 0.0030 (the bet floor): reaches INFO or BET (rate); mean C_mod",
            [d30, round(summary["W_mod_oracle_0.0030"]["mean_C_mod"], 5)],
            ">= 0.80",
            d30 >= 0.80,
        )
    )
    b2 = rate(
        "W_both",
        lambda x: (
            x["verdict"]["model"] in detected
            and x["verdict"]["market"].startswith("MISCALIBRATION")
        ),
    )
    checks.append(
        chk(
            "G7e both planted: both words fire (rate); mean C_mkt, C_mod",
            [
                b2,
                round(summary["W_both"]["mean_C_mkt"], 5),
                round(summary["W_both"]["mean_C_mod"], 5),
            ],
            ">= 0.80",
            b2 >= 0.80,
        )
    )

    # G8 power: binding half-widths under the null world
    hw = {
        k_: round(
            float(
                np.mean(
                    [
                        0.5 * (x["report_2024"][k_]["hi"] - x["report_2024"][k_]["lo"])
                        for x in worlds["W0_null"]
                    ]
                )
            ),
            5,
        )
        for k_ in ("C_mod", "C_mkt", "C_mkt_after", "C_total", "C_pl")
    }
    res["null_halfwidths_2024"] = hw
    checks.append(
        chk(
            "G8 binding half-width of C_mod under the null (envelope of game / player-season / week)",
            hw["C_mod"],
            "<= 0.0010 (a third of the bet floor)",
            hw["C_mod"] <= 0.0010,
        )
    )
    res["checks"] = checks
    res["all_pass"] = all(c["pass"] for c in checks)
    res["data_capture"] = data_capture(db)
    res["seconds"] = round(time.time() - t_start, 1)
    res["git_head"] = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        cwd=rule.ROOT,
    ).stdout.strip()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=float))
    core._log(f"wrote {out}; all_pass={res['all_pass']}; {res['seconds']} s")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k-null", type=int, default=20)
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument(
        "--db",
        default=None,
        help="a byte copy of odds_history.duckdb (never the live file while a pull runs)",
    )
    ap.add_argument(
        "--rebuild-extras",
        action="store_true",
        help="overwrite the pinned extras frames from --db (breaks the pin: the bytes are not reproducible)",
    )
    a = ap.parse_args(argv)
    run(a.k_null, a.boot, Path(a.out), a.reps, a.db, rebuild_extras=a.rebuild_extras)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
