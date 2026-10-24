"""CLI: ``python -m nba.parlay {fit,eval-joint,evaluate,analyze,top,assistant}`` (read-only).

``top --date D`` writes ``reports/parlay/top_parlays_<D>.json`` (docs/contracts/top_parlays_v2.md):
the top three two-leg and three-leg tickets by calibrated joint probability, from stored forecasts
and pre-tip book quotes. It reads two stores read-only and writes only that file.

``evaluate --date D`` reads forward predictions (nba.duckdb, READ_ONLY) and the latest Kalshi
prices (kalshi.duckdb, READ_ONLY), prices every mapped single-leg contract (both sides) and any
requested same-game combos, and prints/writes the contract
``{legs, model_prob, prob_interval, price, fees, ev, ev_low, verdict}``.
Paper trades and the shadow log go to a SEPARATE writable file (``--paper-db``).
No order endpoints, no credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.db.connect import attach_read_only_with_retry, connect_with_retry
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


def load_open_markets(
    con: duckdb.DuckDBPyConnection, before: datetime | None = None
) -> list[MarketRow]:
    """Open (unsettled) Kalshi markets with their latest price. ``con`` has ``kal`` attached.
    ``before`` (naive UTC) keeps only prices stamped strictly earlier (as-of discipline)."""
    cut = "WHERE ts < ? " if before is not None else ""
    rows = con.execute(
        "SELECT m.ticker, m.series_ticker, m.event_ticker, m.title, m.player_id, m.stat, "
        "m.threshold, p.yes_bid, p.yes_ask, p.ts FROM kal.kalshi_markets m "
        "LEFT JOIN (SELECT ticker, arg_max(yes_bid, ts) yes_bid, arg_max(yes_ask, ts) yes_ask, "
        f"max(ts) ts FROM kal.kalshi_prices {cut}GROUP BY ticker) p USING (ticker) "
        "WHERE m.result IS NULL",
        [] if before is None else [before],
    ).fetchall()
    return [MarketRow.from_row(dict(zip(MARKET_COLS, r, strict=True))) for r in rows]


def map_markets(
    markets: list[MarketRow],
    info: SlateInfo,
    unmatched_tickers: list[str] | None = None,
) -> tuple[dict[str, tuple[MarketRow, Leg]], dict[str, int]]:
    """Map markets to legs for the slate; returns (mapped by ticker, skip-reason counts).

    ``unmatched_tickers`` (when given) collects prop markets of THIS slate whose player has no
    reviewed alias, so ``evaluate`` can name them instead of only counting them.
    """
    abbr = load_team_abbr()
    team_of = {(g, p): t for g, c in info.ctxs.items() for p, t in c.team_of.items()}
    skipped: dict[str, int] = {}
    mapped: dict[str, tuple[MarketRow, Leg]] = {}
    for m in markets:
        leg, why = map_market(m, abbr, info.games_by_key, team_of)
        if leg is None:
            skipped[why or "?"] = skipped.get(why or "?", 0) + 1
            if why == "unmatched_player" and unmatched_tickers is not None:
                unmatched_tickers.append(m.ticker)
            continue
        mapped[m.ticker] = (m, leg)
    return mapped, skipped


def pretip_only(
    mapped: dict[str, tuple[MarketRow, Leg]],
    info: SlateInfo,
    now: datetime,
    skipped: dict[str, int],
) -> dict[str, tuple[MarketRow, Leg]]:
    """Keep markets whose game has not tipped (``now < tip``) and whose price is stamped before
    the tip and before ``now``; an in-play price must never enter the shadow track record."""
    kept: dict[str, tuple[MarketRow, Leg]] = {}
    for tk, (m, leg) in mapped.items():
        tip = info.tipoff.get(leg.game_id)
        why = None
        if tip is None:
            why = "tip_unknown"
        elif not now < tip:
            why = "game_tipped"
        elif m.price_ts is not None:
            ts = datetime.fromisoformat(m.price_ts)
            if not (ts < tip and ts < now):
                why = "price_not_pretip"
        if why is not None:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            kept[tk] = (m, leg)
    return kept


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
    mid = ask if bid is None else (ask + bid) / 2.0  # prediction-time proxy for the last fair price
    p_eff = effective_prob(jp_cond, p_nov, policy, cost, last_fair_price=mid)
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
    attach_read_only_with_retry(con, a.nba_db, "nba")
    attach_read_only_with_retry(con, a.kalshi_db, "kal")
    info = load_slate(con, slate, params, prefix="nba.", as_of=a.now, roster_source=a.roster_source)
    now = a.now or datetime.now(UTC).replace(tzinfo=None)
    markets = load_open_markets(con, before=now)
    paper = duckdb.connect(str(a.paper_db))
    ensure_schema(paper)
    tr = track_record(paper)
    model = JointModel(ENGINE_KIND[engine_name], pc, cfg.n_sims, cfg.seed, cfg.t_df)
    unmatched_tickers: list[str] = []
    mapped, skipped = map_markets(markets, info, unmatched_tickers)
    mapped = pretip_only(mapped, info, now, skipped)
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
                now=a.now,
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
    for gid, pid, why in sorted(
        {(str(x["game_id"]), str(x["player_id"]), str(x["reason"])) for x in info.dropped}
    ):
        print(f"dropped player {pid} in {gid}: {why}")
    if unmatched_tickers:  # fail loudly: these props are NOT priced until an alias is reviewed
        print(
            f"WARNING: {len(unmatched_tickers)} prop market(s) on this slate have no reviewed "
            f"player alias and were NOT priced (see docs/KALSHI_READINESS_2026-10-09.md): "
            f"{', '.join(unmatched_tickers[:8])}{' ...' if len(unmatched_tickers) > 8 else ''}",
            file=sys.stderr,
        )
    if a.settle:
        res = connect_with_retry(a.nba_db, read_only=True)
        print(
            f"settled paper={settle_trades(paper, res, policy)} "
            f"shadow={settle_shadow(paper, res, policy)}"
        )
    return 0


def cmd_analyze(a: argparse.Namespace) -> int:
    """Daily props analysis (read-only; writes only the report files)."""
    from nba.parlay.analysis import build_analysis
    from nba.parlay.narrate import narrate
    from nba.parlay.report import build_report, write_report

    slate = date.fromisoformat(a.date)
    an = build_analysis(
        slate,
        a.now,
        nba_db=a.nba_db,
        kalshi_db=a.kalshi_db,
        paper_db=a.paper_db,
        model_path=a.model,
        config=a.config,
        engine=a.engine,
        dnp_policy=a.dnp_policy,
        shadow=not a.no_shadow,
        roster_source=a.roster_source,
    )
    if a.target is not None and a.budget is None:
        raise SystemExit("--target needs --budget")
    rep = build_report(an, top=a.top, budget=a.budget, target=a.target)
    if a.narrate:
        from nba.parlay.assistant.backend import OllamaBackend, OllamaUnavailable
        from nba.parlay.assistant.config import load_assistant_config

        ac = load_assistant_config(a.config)
        ob = OllamaBackend(ac.model, ac.host, seed=ac.seed, timeout_s=ac.timeout_s)
        try:
            ob.ping()
            rep["narration"], rep["narration_note"] = narrate(rep, ob)
        except OllamaUnavailable as e:
            rep["narration_note"] = f"narration skipped: {str(e).splitlines()[0]}"
    md, js = write_report(rep, a.out)
    print(
        f"props analysis {a.date}: games={rep['n_games']} contracts={rep['n_contracts']} "
        f"flagged_positive={rep['n_flagged_positive']} verdict={rep['headline_verdict']}\n"
        f"track record: {rep['gate']['describe']}\nwrote {md} and {js}"
    )
    if rep.get("budget"):
        print(f"budget answer: {rep['budget']['answer']}")
    return 0


def cmd_top(a: argparse.Namespace) -> int:
    from nba.parlay.line_cal import load_map
    from nba.parlay.top_parlays import K_DEFAULT, Gate, build_report
    from nba.parlay.top_source import SourceError, load_legs

    cfg = load_config(a.config)
    calmap = load_map(Path(a.cal_map))
    if not Path(a.model).exists():
        raise SystemExit(f"missing {a.model}; run `python -m nba.parlay fit` first")
    pc = corr_from_json(json.loads(Path(a.model).read_text())["corr"])
    slate = date.fromisoformat(a.date)
    now = a.now or datetime.now(UTC).replace(tzinfo=None)
    nba = connect_with_retry(a.nba_db, read_only=True, max_wait_s=30.0)
    odds = (
        connect_with_retry(a.odds_db, read_only=True, max_wait_s=30.0)
        if Path(a.odds_db).exists()
        else None
    )
    try:
        loaded = load_legs(nba, odds, slate, now=now, calmap=calmap)
    except SourceError as e:
        raise SystemExit(str(e)) from e
    finally:
        nba.close()
        if odds is not None:
            odds.close()
    # M4: the Kalshi shadow log measures a different product; the gate stays shut until the panel
    # has its own settled paper log (contract: counters are producer-asserted)
    gate = Gate()
    gate_note = "gate shut: the top-parlays panel has no paper log of its own yet"
    raw_model = Path(a.model).read_bytes()
    blob = json.loads(raw_model)
    joint_model = {
        "sha256": hashlib.sha256(raw_model).hexdigest(),
        "fit_seasons": blob.get("params", {}).get("fit_seasons"),
        "n_games_corr": blob.get("n_games_corr"),
    }
    caveats = [
        "Leg probabilities come from the frozen LINE_CALIBRATION map fitted through season 2024 on "
        "production forecasts; it has not yet been confirmed on the live log. Legs are calibrated; "
        "the joint is not yet tested.",
        "A leg is offered only if its player's P(play) is at least 0.90; if a player sits, books "
        "usually void the leg, which changes the payout.",
    ]
    m = calmap.meta
    rep = build_report(
        loaded.legs,
        slate_date=a.date,
        made_at=now,
        now=now,
        cfg=cfg,
        pc=pc,
        calibration={
            "rule": m["rule"],
            "sha8": m["rule_sha8"],
            "map_sha8": str(m["map_sha256"])[:8],
            "fit_through": f"{m['fit_through']} (season 2024; season 2025 rows only after a logged "
            "training-use row, Devin)",
            "status": "Stage 1 pass (T493); live confirmation pending. p_cal is the map applied to "
            "the PRODUCTION model's P(over) for every leg (model_source production); the AVG "
            "model's P(over), where a fresh row exists, is shown as p_avg_raw only and never "
            "ranks.",
        },
        gate=gate,
        k=K_DEFAULT if a.k is None else a.k,
        joint_model=joint_model,
        no_slate=loaded.no_slate,
        pre_refused=dict(loaded.dropped),
        notes=[
            *loaded.notes,
            *([gate_note] if gate_note else []),
            f"forecast sources: {dict(loaded.sources)}",
        ],
        extra_caveats=caveats,
    )
    out = Path(a.out_dir) / f"top_parlays_{a.date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1) + "\n")
    n2, n3 = len(rep["sizes"]["2"]), len(rep["sizes"]["3"])
    print(f"wrote {out}: verdict {rep['verdict']}, {n2} two-leg and {n3} three-leg tickets")
    if rep["empty_reason"]:
        print(f"empty_reason: {rep['empty_reason']}")
    return 0


def cmd_size(a: argparse.Namespace) -> int:
    """Read bets.json, print per-bet contracts, expected profit, P(loss) and the floor warning."""
    from nba.parlay.bayes_kelly import format_result, parse_bets, size_bets

    bets, corr = parse_bets(json.loads(Path(a.bets).read_text()))
    res = size_bets(
        bets,
        bankroll=a.bankroll,
        floor=a.floor,
        cap=a.cap,
        corr=corr,
        parlay_cfg_path=a.config,
    )
    print(json.dumps(res.to_dict(), indent=1) if a.json else format_result(res))
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
            p.add_argument("--dnp-policy", choices=DNP_POLICIES, default="last_fair_price")
            p.add_argument("--roster-source", choices=["recent", "official"], default="recent")
            p.add_argument("--combo", action="append", help="comma-separated same-game tickers")
            p.add_argument("--no-log", action="store_true")
            p.add_argument(
                "--now",
                type=lambda t: datetime.fromisoformat(t).replace(tzinfo=None),
                default=None,
                help="override the clock (naive UTC ISO); tests/rehearsals only",
            )
            p.add_argument("--settle", action="store_true")
    p = sub.add_parser("top", help="top-3 two- and three-leg tickets for a slate (read-only)")
    p.add_argument("--date", required=True)
    p.add_argument("--now", type=lambda t: datetime.fromisoformat(t).replace(tzinfo=None))
    p.add_argument("--config", default=str(ROOT / "configs" / "parlay.yaml"))
    p.add_argument("--nba-db", default=str(ROOT / "nba.duckdb"))
    p.add_argument("--odds-db", default=str(ROOT / "data" / "markets" / "market_asof.duckdb"))
    p.add_argument("--model", default=str(DEFAULT_MODEL))
    p.add_argument(
        "--cal-map", default=str(Path(__file__).resolve().parent / "line_cal_map_v1.json")
    )
    p.add_argument("--out-dir", default=str(ROOT / "reports" / "parlay"))
    p.add_argument("--k", type=int, default=None, help="legs kept per book before the search")
    p = sub.add_parser("analyze", help="daily props analysis report (read-only)")
    p.add_argument("--date", required=True)
    p.add_argument("--now", type=lambda t: datetime.fromisoformat(t).replace(tzinfo=None))
    p.add_argument("--out", default=str(ROOT / "reports" / "props_analysis"))
    p.add_argument("--config", default=str(ROOT / "configs" / "parlay.yaml"))
    p.add_argument("--nba-db", "--db", dest="nba_db", default=str(ROOT / "nba.duckdb"))
    p.add_argument("--kalshi-db", default=str(ROOT / "data" / "kalshi" / "kalshi.duckdb"))
    p.add_argument("--paper-db", default=str(ROOT / "data" / "parlay" / "paper_trades.duckdb"))
    p.add_argument("--model", default=str(DEFAULT_MODEL))
    p.add_argument("--engine", choices=list(ENGINE_KIND), default=None)
    p.add_argument("--dnp-policy", choices=DNP_POLICIES, default="last_fair_price")
    p.add_argument("--roster-source", choices=["recent", "official"], default="recent")
    p.add_argument("--no-shadow", action="store_true", help="omit shadow-arm columns")
    p.add_argument("--narrate", action="store_true", help="local-Ollama summary (numguarded)")
    p.add_argument("--budget", type=float, help="also answer: best EV for this many dollars")
    p.add_argument("--target", type=float, help="with --budget: profit target in dollars")
    p.add_argument("--top", type=int, default=10)
    p = sub.add_parser("size", help="Bayesian-Kelly stake sizer for a night's bets (advice only)")
    p.add_argument("--bets", required=True, help="bets.json (see docs/BAYES_KELLY.md)")
    p.add_argument("--bankroll", type=float, required=True)
    p.add_argument("--floor", type=float, required=True, help="soft floor; warns, never blocks")
    p.add_argument("--cap", type=float, required=True, help="nightly loss cap in dollars")
    p.add_argument("--config", default=str(ROOT / "configs" / "parlay.yaml"))
    p.add_argument("--json", action="store_true", help="print JSON instead of the table")
    from nba.parlay.assistant.cli import add_arguments, run

    add_arguments(
        sub.add_parser("assistant", help="read-only local-LLM chat over the engine"), ROOT
    )
    a = ap.parse_args(argv)
    if a.cmd == "assistant":
        return run(a)
    if a.cmd == "size":
        return cmd_size(a)
    if a.cmd == "evaluate":
        Path(a.paper_db).parent.mkdir(parents=True, exist_ok=True)
    return {
        "fit": cmd_fit,
        "eval-joint": cmd_eval_joint,
        "evaluate": cmd_evaluate,
        "analyze": cmd_analyze,
        "top": cmd_top,
    }[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
