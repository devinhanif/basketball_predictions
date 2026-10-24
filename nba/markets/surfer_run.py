"""Driver for the market-surfer PAPER replay (offline, read-only). See nba/markets/surfer.py.

    .venv/bin/python -m nba.markets.surfer_run --out reports/market_surfer_paper_2026-10-23

Reads stored raw files under data/raw/live_<night>/ and the settlement table of the live Kalshi
snapshot DB opened read_only=True. No network, no credential, no order endpoint.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from nba.markets.surfer import (
    Market,
    Trade,
    game_bootstrap,
    load_night,
    load_params,
    night_t_interval,
    placebo_means,
    placebo_pools,
    run_all,
    verdict,
)
from nba.parlay.config import load_config


def summarize(trades: list[Trade], P: dict[str, Any]) -> dict[str, Any]:
    ci = P["ci"]
    pnl = [t.pnl for t in trades]
    out: dict[str, Any] = {"n_trades": len(trades)}
    if not trades:
        return out
    out["games"] = len({t.night + t.event for t in trades})
    out["mean_net_per_contract"] = float(np.mean(pnl))
    out["win_rate"] = float(np.mean([p > 0 for p in pnl]))
    series = {
        "net": pnl,
        "hold": [t.pnl_hold for t in trades],
        "net_minus_hold": [t.pnl - t.pnl_hold for t in trades],
    }
    for name, vals in series.items():
        m, lo, hi = game_bootstrap(trades, vals, int(ci["boot"]), int(ci["seed"]), ci["level"])
        nm, nlo, nhi, k = night_t_interval(trades, vals, ci["level"])
        out[name] = {
            "mean": m,
            "game_ci": [lo, hi],
            "night_mean": nm,
            "night_ci": [nlo, nhi],
            "nights": k,
        }
    out["fees_per_contract_mean"] = float(np.mean([t.fee_entry + t.fee_exit for t in trades]))
    out["spread_cost_mean"] = float(np.mean([t.spread_cost for t in trades]))
    out["gross_mid_drift_mean"] = float(np.mean([t.pnl_gross_mid for t in trades]))
    out["exit_reasons"] = {
        r: sum(1 for t in trades if t.reason == r) for r in ("tp", "stop", "time", "settle")
    }
    holds = [t.t_exit - t.t_fill for t in trades if t.t_exit is not None]
    out["median_hold_s"] = float(statistics.median(holds)) if holds else None
    n = int(P["unit_contracts"])
    by_night: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_night[t.night].append(t)
    out["per_night"] = {
        nt: {
            "trades": len(v),
            "games": len({t.event for t in v}),
            "total_net_usd_unit": round(sum(t.pnl for t in v) * n, 4),
            "total_hold_usd_unit": round(sum(t.pnl_hold for t in v) * n, 4),
            "mean_net_per_contract": float(np.mean([t.pnl for t in v])),
            "win_rate": float(np.mean([t.pnl > 0 for t in v])),
            "fees_usd_unit": round(sum(t.fee_entry + t.fee_exit for t in v) * n, 4),
        }
        for nt, v in sorted(by_night.items())
    }
    out["by_series"] = {
        s: {
            "trades": sum(1 for t in trades if t.series == s),
            "mean_net": float(np.mean([t.pnl for t in trades if t.series == s])),
        }
        for s in sorted({t.series for t in trades})
    }
    return out


def main() -> None:
    import duckdb

    ap = argparse.ArgumentParser(description="Paper-only market-surfer replay (offline).")
    ap.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    ap.add_argument("--settle-db", type=Path, default=Path("data/kalshi/kalshi.duckdb"))
    ap.add_argument("--nights", nargs="+", default=["2026-10-21", "2026-10-22"])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    P = load_params()
    fee = load_config().fee
    if not fee.verified:
        raise SystemExit("fee model is not verified; refusing to run")
    con = duckdb.connect(str(a.settle_db), read_only=True)
    rows = con.execute("select ticker, result from kalshi_markets").fetchall()
    con.close()
    results = {str(t): str(r) for t, r in rows}
    a.out.mkdir(parents=True, exist_ok=True)
    markets: dict[str, Market] = {}
    audits: dict[str, Any] = {}
    for night in a.nights:
        ms, audit = load_night(a.raw_root / f"live_{night}", night, P["series"], results)
        markets.update(ms)
        st = audit.pop("snap_times")["KXNBAGAME"]
        gaps = [y - x for x, y in zip(st, st[1:], strict=False)]
        audit["game_series_gap_median_s"] = statistics.median(gaps)
        audit["game_series_gap_p90_s"] = sorted(gaps)[int(0.9 * len(gaps))]
        audits[night] = audit
    trades, missed = run_all(markets, P, fee)
    summ = summarize(trades, P)
    pools = placebo_pools(markets, P)
    pl = [x for x in placebo_means(trades, markets, pools, P, fee) if not math.isnan(x)]
    real_mean = summ.get("mean_net_per_contract", 0.0)
    pstats: dict[str, Any] = {
        "mean_of_means": float(np.mean(pl)) if pl else None,
        "p2_5": float(np.quantile(pl, 0.025)) if pl else None,
        "p97_5": float(np.quantile(pl, 0.975)) if pl else None,
        "real_rank_pct": float(np.mean([x < real_mean for x in pl])) if pl else None,
        "replications": len(pl),
        "pool_sizes": {f"{k[0]}|{k[1]}": len(v) for k, v in sorted(pools.items())},
    }
    if trades:
        v = verdict(
            len(trades),
            len({t.night for t in trades}),
            summ["games"],
            real_mean,
            (summ["net"]["night_ci"][0], summ["net"]["night_ci"][1]),
            (summ["net"]["game_ci"][0], summ["net"]["game_ci"][1]),
            float(pstats["p97_5"]) if pstats["p97_5"] is not None else float("inf"),
            summ["hold"]["mean"],
            P["gate"],
        )
    else:
        v = "insufficient_sample"
    res = {
        "rule_version": P["rule_version"],
        "markets_used": len(markets),
        "missed_fills": missed,
        "audits": audits,
        "summary": summ,
        "placebo": pstats,
        "verdict": v,
    }
    (a.out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str))
    cols = list(Trade.__dataclass_fields__)
    lines = [",".join(cols)] + [",".join(str(getattr(t, c)) for c in cols) for t in trades]
    (a.out / "trades.csv").write_text("\n".join(lines) + "\n")
    print(json.dumps(res, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
