"""CLI: ``python -m nba.parlay {fit,eval-joint,evaluate,assistant}``. Read-only; paper log only.

``evaluate --date D`` reads forward predictions (nba.duckdb, READ_ONLY) and the latest Kalshi
prices (kalshi.duckdb, READ_ONLY), prices every mapped single-leg contract (both sides) and any
requested same-game combos, and prints/writes the contract
``{legs, model_prob, prob_interval, price, fees, ev, ev_low, verdict}``.
Paper trades and the shadow log go to a SEPARATE writable file (``--paper-db``).
No order endpoints, no credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from nba.parlay.config import ParlayConfig, load_config
from nba.parlay.ev import NO_POSITIVE_EV, Recommendation, evaluate, fee_per_contract
from nba.parlay.game_model import GameModelParams
from nba.parlay.joint import (
    DNP_POLICIES,
    GameCtx,
    JointModel,
    corr_from_json,
    effective_prob,
    leg_marginal,
    p_all_play,
)
from nba.parlay.kalshi_map import MarketRow, load_team_abbr, map_market
from nba.parlay.legs import Leg
from nba.parlay.papertrade import log_trade, settle_trades
from nba.parlay.shadow import (
    TrackRecord,
    ensure_schema,
    log_shadow,
    settle_shadow,
    track_record,
)
from nba.parlay.slate import SlateInfo, load_slate

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "data" / "parlay" / "joint_model.json"
CTX_OOF = ROOT / "reports" / "context_residual" / "oof_context_residual.parquet"
ELO_OOF = ROOT / "data" / "injury_elo" / "oof_predictions.parquet"
ENGINE_KIND = {"independence": "independence", "gaussian_copula": "gaussian", "t_copula": "t"}


def _price_ok(p: float | None) -> bool:
    return p is not None and 0.0 < p < 1.0


MARKET_COLS = (
    "ticker",
    "series_ticker",
    "event_ticker",
    "title",
    "player_id",
    "stat",
    "threshold",
    "yes_bid",
    "yes_ask",
    "ts",
)


def load_open_markets(con: duckdb.DuckDBPyConnection) -> list[MarketRow]:
    """Open (unsettled) Kalshi markets with their latest price. ``con`` has ``kal`` attached."""
    rows = con.execute(
        "SELECT m.ticker, m.series_ticker, m.event_ticker, m.title, m.player_id, m.stat, "
        "m.threshold, p.yes_bid, p.yes_ask, p.ts FROM kal.kalshi_markets m "
        "LEFT JOIN (SELECT ticker, arg_max(yes_bid, ts) yes_bid, arg_max(yes_ask, ts) yes_ask, "
        "max(ts) ts FROM kal.kalshi_prices GROUP BY ticker) p USING (ticker) "
        "WHERE m.result IS NULL"
    ).fetchall()
    return [MarketRow.from_row(dict(zip(MARKET_COLS, r, strict=True))) for r in rows]


def map_markets(
    markets: list[MarketRow], info: SlateInfo
) -> tuple[dict[str, tuple[MarketRow, Leg]], dict[str, int]]:
    """Map markets to legs for the slate; returns (mapped by ticker, skip-reason counts)."""
    abbr = load_team_abbr()
    team_of = {(g, p): t for g, c in info.ctxs.items() for p, t in c.team_of.items()}
    skipped: dict[str, int] = {}
    mapped: dict[str, tuple[MarketRow, Leg]] = {}
    for m in markets:
        leg, why = map_market(m, abbr, info.games_by_key, team_of)
        if leg is None:
            skipped[why or "?"] = skipped.get(why or "?", 0) + 1
            continue
        mapped[m.ticker] = (m, leg)
    return mapped, skipped


def side_quote(m: MarketRow, side: str) -> tuple[float | None, float | None]:
    """(ask, bid) for buying ``side`` of market ``m``; NO side is priced off the YES book."""
    if side == "yes":
        return m.yes_ask, m.yes_bid
    return (
        None if m.yes_bid is None else 1 - m.yes_bid,
        None if m.yes_ask is None else 1 - m.yes_ask,
    )


@dataclass(frozen=True)
class SingleContract:
    ticker: str
    market: MarketRow
    side: str
    legs: list[Leg]
    ask: float
    mid: float
    rec: Recommendation


def price_singles(
    mapped: dict[str, tuple[MarketRow, Leg]],
    info: SlateInfo,
    model: JointModel,
    cfg: ParlayConfig,
    policy: str,
    tr: TrackRecord,
    skipped: dict[str, int],
) -> list[SingleContract]:
    """Price both sides of every mapped single-leg market (the body of ``evaluate``)."""
    out: list[SingleContract] = []
    for tk, (m, leg) in sorted(mapped.items()):
        ctx = {leg.game_id: info.ctxs[leg.game_id]}
        for side in ("yes", "no"):
            ask, bid = side_quote(m, side)
            if not _price_ok(ask):
                skipped["no_price"] = skipped.get("no_price", 0) + 1
                continue
            assert ask is not None
            legs = [replace(leg, side="yes" if side == "yes" else "no")]
            rec = price_contract(
                legs,
                ctx,
                model,
                ask,
                bid,
                cfg,
                policy,
                series=m.series,
                n_settled=tr.n_settled,
                skill=tr.gated_skill(cfg.min_settled_dates),
                engine_name="marginal",
            )
            mid = ask if bid is None else (ask + bid) / 2
            out.append(SingleContract(tk, m, side, legs, ask, mid, rec))
    return out


def price_contract(
    legs: list[Leg],
    ctxs: dict[str, GameCtx],
    model: JointModel,
    ask: float,
    bid: float | None,
    cfg: ParlayConfig,
    policy: str,
    *,
    series: str,
    n_settled: int,
    skill: float,
    engine_name: str,
) -> Recommendation:
    """Model joint prob (DNP policy applied) -> full EV contract against ``ask``."""
    if len(legs) == 1:
        jp_cond = leg_marginal(legs[0], ctxs[legs[0].game_id])
        hits, n = 0, 0
        p_nov = p_all_play(legs, ctxs)
    else:
        out = model.joint(legs, ctxs)
        jp_cond, hits, n, p_nov = out.p_cond, out.hits, out.n_sims, out.p_nov
    cost = ask + fee_per_contract(ask, cfg.fee, series=series, order_type=cfg.order_type)
    p_eff = effective_prob(jp_cond, p_nov, policy, cost)
    if n:
        hits = round(p_eff * n)
    return evaluate(
        legs,
        p_eff,
        hits,
        n,
        ask,
        cfg,
        bid=bid,
        engine=engine_name,
        n_settled=n_settled,
        skill=skill,
        series=series,
    )


def cmd_fit(a: argparse.Namespace) -> int:
    from nba.parlay.joint_eval import fit_production

    blob = fit_production(a.nba_db, a.ctx_oof, a.elo_oof, a.model)
    print(f"wrote {a.model}: params={blob['params']}, corr pairs={len(blob['corr'])}")
    return 0


def cmd_eval_joint(a: argparse.Namespace) -> int:
    from nba.parlay.joint_eval import run_eval

    cfg = load_config(a.config)
    res = run_eval(
        a.nba_db,
        a.ctx_oof,
        a.elo_oof,
        a.out_dir,
        n_sims=a.n_sims,
        shrink_k=cfg.corr_shrink_k,
        t_df=cfg.t_df,
        seed=cfg.seed,
    )
    print(f"wrote {a.out_dir}/joint_calibration.md; n_parlays={res['n_parlays']}")
    # Cross-game section: do legs from DIFFERENT games on one date behave independently?
    from nba.parlay.independence_check import load_independence_cfg, run_historical

    ic = run_historical(
        a.nba_db, a.ctx_oof, a.elo_oof, a.out_dir, load_independence_cfg(a.config), seed=cfg.seed
    )
    md = Path(a.out_dir) / "joint_calibration.md"
    md.write_text(md.read_text() + "\n# Cross-game legs (independence assumption)\n\n" + ic["md"])
    for label, r in ic["seasons"].items():
        print(f"cross-game {label}: flagged={r['flagged'] or 'none'}")
    return 0


def _load_model(path: Path) -> tuple[GameModelParams, Any]:
    if not path.exists():
        raise SystemExit(f"missing {path}; run `python -m nba.parlay fit` first")
    blob = json.loads(path.read_text())
    return GameModelParams.from_json(json.dumps(blob["params"])), corr_from_json(blob["corr"])


def cmd_evaluate(a: argparse.Namespace) -> int:
    cfg = load_config(a.config)
    policy = a.dnp_policy
    engine_name = a.engine or cfg.engine
    if engine_name not in ENGINE_KIND:
        raise SystemExit(f"engine {engine_name!r} not supported here; use {list(ENGINE_KIND)}")
    params, pc = _load_model(Path(a.model))
    slate = date.fromisoformat(a.date)
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{a.nba_db}' AS nba (READ_ONLY)")
    con.execute(f"ATTACH '{a.kalshi_db}' AS kal (READ_ONLY)")
    info = load_slate(con, slate, params, prefix="nba.")
    markets = load_open_markets(con)
    paper = duckdb.connect(str(a.paper_db))
    ensure_schema(paper)
    tr = track_record(paper)
    model = JointModel(ENGINE_KIND[engine_name], pc, cfg.n_sims, cfg.seed, cfg.t_df)
    mapped, skipped = map_markets(markets, info)
    out: list[dict[str, Any]] = []
    n_logged = 0
    for sc in price_singles(mapped, info, model, cfg, policy, tr, skipped):
        rec, tk, side, legs = sc.rec, sc.ticker, sc.side, sc.legs
        out.append(
            rec.to_dict()
            | {
                "ticker": tk,
                "side": side,
                "price_ts": sc.market.price_ts,
                "dnp_policy": policy,
                "title": sc.market.title,
            }
        )
        if not a.no_log:
            log_shadow(
                paper,
                shadow_id=f"{a.date}|{tk}|{side}",
                ticker=tk,
                side=side,
                legs=legs,
                raw_model_prob=rec.raw_model_prob,
                market_mid=sc.mid,
                price=sc.ask,
                engine="marginal",
            )
            if rec.verdict != NO_POSITIVE_EV:
                log_trade(paper, rec, cfg.fee, trade_id=f"{a.date}|{tk}|{side}")
                n_logged += 1
    for combo in a.combo or []:
        tks = combo.split(",")
        if any(t not in mapped for t in tks):
            print(f"combo skipped (unmapped ticker): {combo}", file=sys.stderr)
            continue
        legs = [mapped[t][1] for t in tks]
        if len({leg.game_id for leg in legs}) != 1:
            print(f"combo skipped (not same game): {combo}", file=sys.stderr)
            continue
        ask_p = 1.0
        for t in tks:
            ask_p *= mapped[t][0].yes_ask or 1.0
        rec = price_contract(
            legs,
            {legs[0].game_id: info.ctxs[legs[0].game_id]},
            model,
            ask_p,
            None,
            cfg,
            policy,
            series=mapped[tks[0]][0].series,
            n_settled=tr.n_settled,
            skill=tr.gated_skill(cfg.min_settled_dates),
            engine_name=engine_name,
        )
        out.append(
            rec.to_dict()
            | {
                "ticker": combo,
                "side": "yes",
                "dnp_policy": policy,
                "price_basis": "product_of_leg_asks_NOT_A_TRADABLE_PRICE",
            }
        )  # noqa: E501
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"evaluate_{a.date}.json").write_text(json.dumps(out, indent=1, default=str))
    n_pos = sum(1 for d in out if d["verdict"] != NO_POSITIVE_EV)
    top = sorted(out, key=lambda d: -d["raw_ev_low"])[:5]
    print(
        f"slate {a.date}: games with forward predictions={len(info.ctxs)}; open Kalshi markets "
        f"read={len(markets)}; mapped={len(mapped)}; skipped={skipped}"
    )
    print(
        f"contracts evaluated (both sides)={len(out)}; flagged positive-EV={n_pos}; "
        f"verdict for the rest={NO_POSITIVE_EV}; paper trades logged={n_logged}"
    )
    print(f"track record: {tr.describe()}; min_settled={cfg.min_settled}")
    if tr.n_settled < cfg.min_settled:
        print(
            "model weight = 0: the engine DEFERS TO THE MARKET until >= min_settled settled "
            "shadow rows show positive skill vs the market; no positive-EV verdict is possible "
            "before then."
        )
    for d in top:
        print(
            f"  raw (unshrunk, hypothesis only) {d['ticker']} {d['side']}: model "
            f"{d['raw_model_prob']:.3f} vs ask {d['price']:.3f}, raw ev_low "
            f"{d['raw_ev_low']:+.3f}, verdict {d['verdict']}"
        )
    for note in info.notes:
        print("note:", note)
    if a.settle:
        res = duckdb.connect(str(a.nba_db), read_only=True)
        print(
            f"settled paper={settle_trades(paper, res, policy)} "
            f"shadow={settle_shadow(paper, res, policy)}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parlay", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fit", "eval-joint", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=str(ROOT / "configs" / "parlay.yaml"))
        p.add_argument("--nba-db", default=str(ROOT / "nba.duckdb"))
        p.add_argument("--model", default=str(DEFAULT_MODEL))
        p.add_argument("--ctx-oof", default=str(CTX_OOF))
        p.add_argument("--elo-oof", default=str(ELO_OOF))
        if name == "eval-joint":
            p.add_argument("--out-dir", default=str(ROOT / "reports" / "parlay"))
            p.add_argument("--n-sims", type=int, default=10000)
        if name == "evaluate":
            p.add_argument("--date", required=True)
            p.add_argument("--kalshi-db", default=str(ROOT / "data" / "kalshi" / "kalshi.duckdb"))
            p.add_argument(
                "--paper-db", default=str(ROOT / "data" / "parlay" / "paper_trades.duckdb")
            )
            p.add_argument("--out-dir", default=str(ROOT / "reports" / "parlay"))
            p.add_argument("--engine", choices=list(ENGINE_KIND), default=None)
            p.add_argument("--dnp-policy", choices=DNP_POLICIES, default="void")
            p.add_argument("--combo", action="append", help="comma-separated same-game tickers")
            p.add_argument("--no-log", action="store_true")
            p.add_argument("--settle", action="store_true")
    from nba.parlay.assistant.cli import add_arguments, run

    add_arguments(
        sub.add_parser("assistant", help="read-only local-LLM chat over the engine"), ROOT
    )
    a = ap.parse_args(argv)
    if a.cmd == "assistant":
        return run(a)
    if a.cmd == "evaluate":
        Path(a.paper_db).parent.mkdir(parents=True, exist_ok=True)
    return {"fit": cmd_fit, "eval-joint": cmd_eval_joint, "evaluate": cmd_evaluate}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
