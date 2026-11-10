# ruff: noqa: E501
"""Minimum-data gate for GAME_SCRIPTS_FORWARD (docs/prereg/GAME_SCRIPTS_FORWARD.md, DRAFT).

Read-only. Three parts:

A. Season 2026, LABEL-FREE. Games per ET week (schedule from the facts store; finals counted from
   ``nba.duckdb`` by presence of a score, the score itself is never read), production win-probability
   stamps before the real tip, the model's P(blowout) / P(close) from those stamps (shootout and grind
   are a constant band by construction, computed analytically), expected script games per week, and
   DraftKings main-line legs per game. No 2026 margin, total or player stat is read.
B. Seasons 2023-2024, LABELLED (already exposed by combo_eval; never 2025): realised script shares,
   the model's mean P, Brier of P(script) against a constant, and the per-game / per-date spread that
   sizes the CI at the look.
C. Projection: the ET date by which N post-freeze games are final, from the schedule.

Usage:
  python -W ignore -m research.eval.game_scripts_forward_gate --data-root /Users/devin/Downloads/nba-prediction \
      --until 2026-11-09 --freeze 2026-11-12 --out <json>
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import polars as pl

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
STATS = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
BLOWOUT_ABS = 15
CLOSE_ABS = 5
TOTAL_DELTA = 12.0
HIST = (2023, 2024)
SEED = 20261110


def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def p_blowout(mu_m: float, sd_m: float) -> float:
    t = BLOWOUT_ABS - 0.5
    return _phi((-t - mu_m) / sd_m) + 1.0 - _phi((t - mu_m) / sd_m)


def p_close(mu_m: float, sd_m: float) -> float:
    t = CLOSE_ABS + 0.5
    return _phi((t - mu_m) / sd_m) - _phi((-t - mu_m) / sd_m)


def p_shootout(mu_t: float, sd_t: float) -> float:
    return 1.0 - _phi((math.ceil(mu_t + TOTAL_DELTA) - 0.5 - mu_t) / sd_t)


def p_grind(mu_t: float, sd_t: float) -> float:
    return _phi((math.floor(mu_t - TOTAL_DELTA) + 0.5 - mu_t) / sd_t)


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _q(a: np.ndarray, qs: tuple[float, ...]) -> list[float]:
    return [round(float(x), 4) for x in np.quantile(a, qs)] if len(a) else []


# ---------------------------------------------------------------- part A (2026, label-free)


def schedule_2026(facts: Path) -> pl.DataFrame:
    con = duckdb.connect(str(facts), read_only=True)
    try:
        rows = con.execute(
            "SELECT subject, arg_max(value_text, known_at) FROM facts "
            "WHERE predicate = 'tips_at' AND subject LIKE 'game:00226%' GROUP BY 1"
        ).fetchall()
    finally:
        con.close()
    out = []
    for subj, v in rows:
        tip = datetime.fromisoformat(v).replace(tzinfo=UTC)
        out.append(
            {"game_id": subj.split(":", 1)[1], "tip_utc": tip, "et_date": tip.astimezone(ET).date()}
        )
    return pl.DataFrame(out)


def part_a(root: Path, until: date, params: dict[str, Any]) -> dict[str, Any]:
    sched = schedule_2026(root / "data/facts/facts.duckdb")
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    try:
        finals = con.execute(
            "SELECT game_id, game_date FROM games WHERE season = 2026 "
            "AND home_pts IS NOT NULL AND home_pts > 0 AND game_date <= ?",
            [until],
        ).pl()
        wp = con.execute(
            "SELECT game_id, made_at, tipoff, json_extract(prediction, '$.p_home')::DOUBLE AS p_home "
            "FROM forward_predictions WHERE model_name = 'rung0_injury_elo' AND target = 'win_prob_home'"
        ).pl()
        prod = con.execute(
            "SELECT game_id, player_id, target AS stat, max(made_at) AS made_at, any_value(tipoff) AS tipoff "
            "FROM forward_predictions WHERE model_name = 'props_context_residual' AND made_at < tipoff "
            "GROUP BY 1, 2, 3"
        ).pl()
    finally:
        con.close()
    finals = finals.with_columns(pl.col("game_id").cast(pl.Utf8))
    sched_until = sched.filter(pl.col("et_date") <= until)
    # finals per ET week (presence of a score only)
    fin = finals.join(sched.select("game_id", "et_date"), on="game_id", how="left")
    fin_weeks: dict[str, int] = {}
    for d in fin["et_date"].to_list():
        if d is None:
            continue
        k = week_start(d).isoformat()
        fin_weeks[k] = fin_weeks.get(k, 0) + 1
    sched_weeks: dict[str, int] = {}
    for d in sched["et_date"].to_list():
        k = week_start(d).isoformat()
        sched_weeks[k] = sched_weeks.get(k, 0) + 1

    # latest production win-prob stamp strictly before the real tip
    pre = wp.filter(pl.col("made_at") < pl.col("tipoff")).sort("made_at")
    latest = pre.group_by("game_id").agg(
        pl.col("made_at").last(), pl.col("tipoff").last(), pl.col("p_home").last()
    )
    latest = latest.join(
        sched.select("game_id", "tip_utc", "et_date"), on="game_id", how="left"
    ).filter(pl.col("et_date") <= until)
    tip_naive = [
        t.replace(tzinfo=None) if t is not None else None for t in latest["tip_utc"].to_list()
    ]
    tip_match = sum(
        1
        for a, b in zip(latest["tipoff"].to_list(), tip_naive, strict=True)
        if b is not None and a == b
    )
    lead = np.array(
        [
            (t - m).total_seconds() / 60.0
            for m, t in zip(latest["made_at"].to_list(), latest["tipoff"].to_list(), strict=True)
        ]
    )
    b, s, sd_m = params["margin_b"], params["margin_s"], params["margin_sd"]
    sd_t = params["total_sd"]
    mu_m = np.array([b + s * logit(p) for p in latest["p_home"].to_list()])
    pb = np.array([p_blowout(m, sd_m) for m in mu_m])
    pc = np.array([p_close(m, sd_m) for m in mu_m])
    # shootout / grind: P depends on mu_t only through the integer rounding; band over the fractional part
    fr = np.linspace(0.0, 1.0, 1001)[:-1]
    ps_band = np.array([p_shootout(220.0 + f, sd_t) for f in fr])
    pg_band = np.array([p_grind(220.0 + f, sd_t) for f in fr])
    n_wp = latest.height
    exp_per_game = {
        "blowout": float(pb.mean()),
        "close": float(pc.mean()),
        "shootout": float(ps_band.mean()),
        "grind": float(pg_band.mean()),
    }
    bins_b = np.histogram(pb, bins=[0, 0.3, 0.35, 0.4, 0.5, 0.6, 1.0])[0].tolist()
    bins_c = np.histogram(pc, bins=[0, 0.15, 0.2, 0.25, 0.3, 1.0])[0].tolist()

    # DraftKings main-line legs per game (label-free): two-sided, non-alternate, x.5, received before tip
    # and at or before the production forecast's made_at for that (game, player, stat).
    mcon = duckdb.connect(str(root / "data/markets/market_asof.duckdb"), read_only=True)
    try:
        dk = mcon.execute(
            "SELECT game_id, player_id, market, point, received_at, tipoff, side FROM live_book_odds "
            "WHERE book = 'draftkings' AND market IN ('player_points','player_rebounds','player_assists','player_threes') "
            "AND slate_date >= DATE '2026-10-20' AND slate_date <= ? AND player_id IS NOT NULL "
            "AND received_at < tipoff",
            [until],
        ).pl()
    finally:
        mcon.close()
    dk = dk.with_columns(
        pl.col("market").replace_strict(STATS).alias("stat"), pl.col("game_id").cast(pl.Utf8)
    )
    j = dk.join(
        prod.with_columns(pl.col("game_id").cast(pl.Utf8)).rename(
            {"made_at": "prod_made_at", "tipoff": "prod_tip"}
        ),
        on=["game_id", "player_id", "stat"],
        how="inner",
    ).filter(pl.col("received_at") <= pl.col("prod_made_at"))
    # newest snapshot per key, then require a two-sided x.5 point
    newest = j.group_by(["game_id", "player_id", "stat"]).agg(
        pl.col("received_at").max().alias("r")
    )
    j2 = j.join(newest, on=["game_id", "player_id", "stat"]).filter(
        pl.col("received_at") == pl.col("r")
    )
    pairs = j2.group_by(["game_id", "player_id", "stat", "point"]).agg(
        pl.col("side").n_unique().alias("ns")
    )
    pairs = pairs.filter((pl.col("ns") >= 2) & ((pl.col("point") % 1.0 - 0.5).abs() < 1e-9))
    # main = one point per key; keys with several two-sided points are counted once (main ambiguity is the export's rule)
    legs = pairs.group_by(["game_id", "player_id", "stat"]).agg(pl.len().alias("npoints"))
    legs_g = legs.group_by("game_id").agg(pl.len().alias("legs"))
    gd = sched.select("game_id", "et_date")
    by_date_legs = {
        str(d): [int(ng), int(nl)]
        for d, ng, nl in legs_g.join(gd, on="game_id", how="left")
        .group_by("et_date")
        .agg(pl.len().alias("g"), pl.col("legs").sum().alias("l"))
        .sort("et_date")
        .iter_rows()
    }
    by_date_sched = {
        str(d): int(n)
        for d, n in sched_until.group_by("et_date").agg(pl.len()).sort("et_date").iter_rows()
    }
    dk_games_by_date = {
        str(d): int(n)
        for d, n in dk.join(gd, on="game_id", how="left")
        .group_by("et_date")
        .agg(pl.col("game_id").n_unique())
        .sort("et_date")
        .iter_rows()
    }
    games_with_prod = prod.select("game_id").unique().height
    legs_per_game = legs_g["legs"].to_numpy() if legs_g.height else np.array([])

    return {
        "label_free": True,
        "until": until.isoformat(),
        "scheduled_games_through_until": sched_until.height,
        "final_games_through_until": finals.height,
        "finals_per_et_week": dict(sorted(fin_weeks.items())),
        "scheduled_per_et_week": dict(sorted(sched_weeks.items())),
        "scheduled_season_total": sched.height,
        "winprob_stamped_games": n_wp,
        "winprob_tip_equals_schedule_tip": tip_match,
        "winprob_lead_minutes_q": dict(
            zip(("min", "p10", "p50", "p90", "max"), _q(lead, (0, 0.1, 0.5, 0.9, 1.0)), strict=True)
        ),
        "p_blowout_q": dict(
            zip(("min", "p10", "p50", "p90", "max"), _q(pb, (0, 0.1, 0.5, 0.9, 1.0)), strict=True)
        ),
        "p_close_q": dict(
            zip(("min", "p10", "p50", "p90", "max"), _q(pc, (0, 0.1, 0.5, 0.9, 1.0)), strict=True)
        ),
        "p_blowout_bins_[0,.3,.35,.4,.5,.6,1]": bins_b,
        "p_close_bins_[0,.15,.2,.25,.3,1]": bins_c,
        "p_shootout_band": [
            round(float(ps_band.min()), 4),
            round(float(ps_band.max()), 4),
            round(float(ps_band.mean()), 4),
        ],
        "p_grind_band": [
            round(float(pg_band.min()), 4),
            round(float(pg_band.max()), 4),
            round(float(pg_band.mean()), 4),
        ],
        "expected_share_per_game": {k: round(v, 4) for k, v in exp_per_game.items()},
        "dk_main_legs": {
            "games_with_production_props": games_with_prod,
            "games_with_legs": legs_g.height,
            "legs_total": legs.height,
            "legs_per_game_q": dict(
                zip(("p10", "p50", "p90"), _q(legs_per_game, (0.1, 0.5, 0.9)), strict=True)
            ),
            "legs_per_game_mean": round(float(legs_per_game.mean()), 2)
            if len(legs_per_game)
            else None,
            "keys_with_several_two_sided_points": int(legs.filter(pl.col("npoints") > 1).height),
            "by_stat": {r[0]: r[1] for r in legs.group_by("stat").agg(pl.len()).iter_rows()},
            "by_et_date_[games_with_legs,legs]": by_date_legs,
            "scheduled_by_et_date": by_date_sched,
            "dk_any_pretip_line_games_by_et_date": dk_games_by_date,
            "note": "before DNP removal; DNP legs are voided at scoring",
        },
    }


# ---------------------------------------------------------------- part B (2023-24, labelled, exposed)


def _asof_mu_t(g: pl.DataFrame, params: dict[str, Any]) -> pl.DataFrame:
    from nba.parlay.game_model import asof_total_features

    f = asof_total_features(g, params["halflife"], params["prior_k"])
    return g.join(f, on="game_id", how="left").with_columns(
        (
            params["total_a"]
            + params["total_c"] * pl.col("total_feat").fill_null(params["league_total"])
        ).alias("mu_t")
    )


def _boot_ci(
    vals: np.ndarray, clusters: np.ndarray, rng: np.random.Generator, b: int = 2000
) -> tuple[float, float, float]:
    """Mean and 95% percentile CI, cluster bootstrap."""
    u, inv = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inv, weights=vals, minlength=len(u))
    cnt = np.bincount(inv, minlength=len(u)).astype(float)
    idx = rng.integers(0, len(u), size=(b, len(u)))
    m = sums[idx].sum(1) / cnt[idx].sum(1)
    return float(vals.mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def part_b(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    con = duckdb.connect(str(root / "nba.duckdb"), read_only=True)
    try:
        g = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts FROM games "
            "WHERE season <= 2024 AND home_pts IS NOT NULL AND home_pts > 0"
        ).pl()
    finally:
        con.close()
    g = _asof_mu_t(g, params)
    p = pl.concat(
        [
            pl.read_parquet(root / f"reports/odds_history/game_rows_{s}.parquet").select(
                "game_id", "p"
            )
            for s in HIST
        ]
    )
    g = g.filter(pl.col("season").is_in(list(HIST))).join(p, on="game_id", how="inner")
    rng = np.random.default_rng(SEED)
    sd_m, sd_t = params["margin_sd"], params["total_sd"]
    out: dict[str, Any] = {
        "label_free": False,
        "seasons": list(HIST),
        "note": "2023-24 only; exposed by combo_eval; game model fit 2022-24 (in-sample here)",
    }
    per: dict[str, Any] = {}
    shares: dict[str, list[float]] = {k: [] for k in ("blowout", "close", "shootout", "grind")}
    for season in HIST:
        d = g.filter(pl.col("season") == season)
        margin = (d["home_pts"] - d["away_pts"]).to_numpy().astype(float)
        total = (d["home_pts"] + d["away_pts"]).to_numpy().astype(float)
        mu_t = d["mu_t"].to_numpy()
        mu_m = np.array(
            [params["margin_b"] + params["margin_s"] * logit(x) for x in d["p"].to_list()]
        )
        dates = np.array([str(x) for x in d["game_date"].to_list()])
        ys = {
            "blowout": (np.abs(margin) >= BLOWOUT_ABS).astype(float),
            "close": (np.abs(margin) <= CLOSE_ABS).astype(float),
            "shootout": (total >= np.ceil(mu_t + TOTAL_DELTA)).astype(float),
            "grind": (total <= np.floor(mu_t - TOTAL_DELTA)).astype(float),
        }
        ps = {
            "blowout": np.array([p_blowout(m, sd_m) for m in mu_m]),
            "close": np.array([p_close(m, sd_m) for m in mu_m]),
            "shootout": np.array([p_shootout(m, sd_t) for m in mu_t]),
            "grind": np.array([p_grind(m, sd_t) for m in mu_t]),
        }
        row: dict[str, Any] = {
            "n_games": int(d.height),
            "n_dates": int(len(np.unique(dates))),
            "mean_total_minus_mu_t": round(float((total - mu_t).mean()), 2),
        }
        for k in ys:
            y, pr = ys[k], ps[k]
            shares[k].append(float(y.mean()))
            gap = y - pr
            gm, glo, ghi = _boot_ci(gap, dates, rng)
            sd_game = float(gap.std(ddof=1))
            # date design effect: bootstrap variance of the mean over dates vs iid games
            u, inv = np.unique(dates, return_inverse=True)
            sums = np.bincount(inv, weights=gap - gap.mean())
            var_date = float((sums**2).sum()) / len(gap) ** 2
            deff = var_date / (sd_game**2 / len(gap))
            row[k] = {
                "realised_share": round(float(y.mean()), 4),
                "mean_p": round(float(pr.mean()), 4),
                "gap_obs_minus_p": [round(gm, 4), round(glo, 4), round(ghi, 4)],
                "sd_per_game": round(sd_game, 4),
                "date_design_effect": round(deff, 3),
                "brier_model": round(float(((pr - y) ** 2).mean()), 5),
            }
        per[str(season)] = row
    # Brier against a constant, other season's realised share (the forward rule's C0 is the pooled 2023-24 share)
    for i, season in enumerate(HIST):
        other = HIST[1 - i]
        d = g.filter(pl.col("season") == season)
        margin = (d["home_pts"] - d["away_pts"]).to_numpy().astype(float)
        mu_m = np.array(
            [params["margin_b"] + params["margin_s"] * logit(x) for x in d["p"].to_list()]
        )
        dates = np.array([str(x) for x in d["game_date"].to_list()])
        for k, y, pr in (
            (
                "blowout",
                (np.abs(margin) >= BLOWOUT_ABS).astype(float),
                np.array([p_blowout(m, sd_m) for m in mu_m]),
            ),
            (
                "close",
                (np.abs(margin) <= CLOSE_ABS).astype(float),
                np.array([p_close(m, sd_m) for m in mu_m]),
            ),
        ):
            c0 = per[str(other)][k]["realised_share"]
            diff = (pr - y) ** 2 - (c0 - y) ** 2
            m, lo, hi = _boot_ci(diff, dates, rng)
            per[str(season)][k]["dbrier_vs_const_other_season"] = [
                round(m, 5),
                round(lo, 5),
                round(hi, 5),
            ]
            per[str(season)][k]["dbrier_sd_per_game"] = round(float(diff.std(ddof=1)), 5)
            # the rule's discrimination comparator: a constant equal to the window mean of the model's own P
            # (label-free), so the difference is resolution only, not calibration-in-the-large
            own = (pr - y) ** 2 - (pr.mean() - y) ** 2
            m2, lo2, hi2 = _boot_ci(own, dates, rng)
            per[str(season)][k]["dbrier_vs_own_mean_p"] = [
                round(m2, 5),
                round(lo2, 5),
                round(hi2, 5),
            ]
            per[str(season)][k]["dbrier_own_sd_per_game"] = round(float(own.std(ddof=1)), 5)
            # reliability, fixed bins
            edges = [0, 0.3, 0.35, 0.4, 0.5, 1.0] if k == "blowout" else [0, 0.2, 0.25, 0.3, 1.0]
            rel = []
            for a, bb in zip(edges[:-1], edges[1:], strict=True):
                msk = (pr >= a) & (pr < bb)
                if msk.sum():
                    rel.append(
                        [
                            a,
                            bb,
                            int(msk.sum()),
                            round(float(pr[msk].mean()), 3),
                            round(float(y[msk].mean()), 3),
                        ]
                    )
            per[str(season)][k]["reliability"] = rel
    out["per_season"] = per
    out["pooled_share_2023_24"] = {k: round(float(np.mean(v)), 4) for k, v in shares.items()}
    return out


# ---------------------------------------------------------------- part C (projection)


def part_c(
    sched: pl.DataFrame, freeze: date, coverage: float, ns: tuple[int, ...]
) -> dict[str, Any]:
    after = sched.filter(pl.col("et_date") > freeze).sort("et_date")
    days = after.group_by("et_date").agg(pl.len().alias("n")).sort("et_date")
    cum = 0.0
    hit: dict[str, str | None] = {str(n): None for n in ns}
    for d, n in days.iter_rows():
        cum += n * coverage
        for k in ns:
            if hit[str(k)] is None and cum >= k:
                hit[str(k)] = d.isoformat()
    return {
        "freeze_et_date": freeze.isoformat(),
        "window_starts": (freeze + timedelta(days=1)).isoformat(),
        "coverage_assumed": coverage,
        "scheduled_after_freeze": after.height,
        "date_reaching_n": hit,
    }


def power_table(
    sd: float, deff: float, ns: tuple[int, ...], delta: float, gaps: tuple[float, ...]
) -> dict[str, Any]:
    """P(95% CI of the gap inside +-delta) under a true gap, normal approximation with a date design effect."""
    out: dict[str, Any] = {}
    for n in ns:
        se = sd * math.sqrt(max(deff, 1.0) / n)
        hw = 1.96 * se
        row = {"hw": round(hw, 4)}
        for gtrue in gaps:
            # CI inside iff |gap_hat| < delta - hw
            room = delta - hw
            pr = 0.0 if room <= 0 else _phi((room - gtrue) / se) - _phi((-room - gtrue) / se)
            row[f"p_pass_gap_{gtrue}"] = round(pr, 3)
            # P(two-sided test rejects gap = 0)
            row[f"p_reject0_gap_{gtrue}"] = round(
                _phi((-hw - gtrue) / se) + 1 - _phi((hw - gtrue) / se), 3
            )
        out[str(n)] = row
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--freeze", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    root = Path(a.data_root)
    params = json.loads((root / "data/parlay/joint_model.json").read_text())["params"]
    until = date.fromisoformat(a.until)
    print("part A (2026, label-free) ...", flush=True)
    pa = part_a(root, until, params)
    print("part B (2023-24, labelled, exposed) ...", flush=True)
    pb = part_b(root, params)
    sched = schedule_2026(root / "data/facts/facts.duckdb")
    cov = pa["winprob_stamped_games"] / max(pa["scheduled_games_through_until"], 1)
    ns = (300, 450, 600, 750, 900)
    pc = part_c(sched, date.fromisoformat(a.freeze), min(cov, 1.0), ns)
    pw: dict[str, Any] = {}
    for k in ("blowout", "close", "shootout", "grind"):
        sd = max(pb["per_season"][str(s)][k]["sd_per_game"] for s in HIST)
        deff = max(pb["per_season"][str(s)][k]["date_design_effect"] for s in HIST)
        pw[k] = {
            "sd": sd,
            "deff": deff,
            "delta_0.05": power_table(sd, deff, ns, 0.05, (0.0, 0.02, 0.04)),
        }
    for k in ("blowout", "close"):
        sd = max(pb["per_season"][str(s)][k]["dbrier_sd_per_game"] for s in HIST)
        pw[k]["dbrier_hw_at_n"] = {str(n): round(1.96 * sd / math.sqrt(n), 5) for n in ns}
        sd2 = max(pb["per_season"][str(s)][k]["dbrier_own_sd_per_game"] for s in HIST)
        pw[k]["dbrier_own_hw_at_n"] = {str(n): round(1.96 * sd2 / math.sqrt(n), 5) for n in ns}
    out = {
        "schema": "game_scripts_forward_gate/1",
        "made_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "params_fit_seasons": params["fit_seasons"],
        "A_2026_label_free": pa,
        "B_history_2023_24": pb,
        "C_projection": pc,
        "power": pw,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
