"""Deterministic rendering of tool results. Every figure shown comes from these dicts."""

from __future__ import annotations

from typing import Any

DISCLAIMER = (
    "Analysis only, not financial advice. Estimated edges are noisy; nothing here places "
    "orders or recommends real-money bets."
)


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def usd(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "n/a"
    if signed:
        return f"{'-' if x < 0 else '+'}${abs(x):.3f}"
    return f"${x:.3f}"


def _interval(r: dict[str, Any]) -> str:
    lo, hi = r["prob_interval"]
    return f"[{pct(lo)}, {pct(hi)}] ({pct(r.get('interval_level'))} interval)"


def _priced_block(r: dict[str, Any]) -> list[str]:
    lines = [
        f"  legs ({r['n_legs']}, {r['n_games']} game(s)): " + " AND ".join(r["legs_text"]),
        f"  engine: {r['engine']}   price basis: {r['price_basis']}   "
        f"DNP policy: {r['dnp_policy']}",
        f"  model prob (after market prior): {pct(r['model_prob'])}   interval: {_interval(r)}",
    ]
    if not r["priced"]:
        lines += [
            f"  joint if all players play: {pct(r['joint_prob_if_all_play'])}   "
            f"independence: {pct(r['independent_prob_if_all_play'])}",
            f"  verdict: {r['verdict']}   ({r['note']})",
        ]
        return lines
    spread = "n/a" if r.get("spread") is None else usd(r["spread"])
    lines += [
        *(
            ["  note: model weight is 0, so the interval collapses onto the market mid"]
            if r["model_weight"] == 0
            else []
        ),
        f"  price (ask): {usd(r['price'])}   fees: {usd(r['fees'])}   bid/ask spread: {spread}",
        f"  EV: {usd(r['ev'], True)}   EV at low bound (ev_low): {usd(r['ev_low'], True)}",
        f"  verdict: {r['verdict']}",
        f"  raw unshrunk model (hypothesis only): prob {pct(r['raw_model_prob'])}, "
        f"raw_ev {usd(r['raw_ev'], True)}, raw_ev_low {usd(r['raw_ev_low'], True)}",
        f"  market-prior model weight: {r['model_weight']:.2f} "
        f"(settled track-record n={r['n_settled_track']}, min_settled={r['min_settled']})",
        f"  joint if all play {pct(r['joint_prob_if_all_play'])} vs independence "
        f"{pct(r['independent_prob_if_all_play'])} (correlation effect "
        f"{100 * r['correlation_effect']:+.2f} pts); P(all play) {pct(r['p_all_play'])}",
    ]
    if r.get("kelly", 0) > 0:
        lines.append(
            f"  informational capped fractional-Kelly: {pct(r['kelly'])} of bankroll "
            "(noisy edge estimate; not a recommendation)"
        )
    fee = r["fee_model"]
    lines.append(
        f"  fee model: {fee['name']} (verified={fee['verified']}, {fee['order_type']}, "
        f"coefficient {fee['taker_coefficient']})"
    )
    if r["n_sims"]:
        lines.append(f"  Monte Carlo draws: {r['n_sims']}")
    return lines


def _slate(r: dict[str, Any]) -> list[str]:
    lines = [
        f"  {r['date']}: {r['n_games']} game(s); open Kalshi markets {r['n_open_kalshi_markets']}, "
        f"mapped {r['n_mapped_markets']}"
    ]
    for g in r["games"]:
        lines.append(
            f"  {g['matchup']} ({g['game_id']}): P(home win) {pct(g['p_home_win'])} "
            f"[{g['win_model']}], total mean {g['total_mean']:.1f}, "
            f"{g['n_players_with_predictions']} players with predictions"
        )
        for p in g["top_players"][:5]:
            pm = "n/a" if p["pts_mean"] is None else f"{p['pts_mean']:.1f}"
            lines.append(
                f"      {p['name']} ({p['team']}): pts mean {pm}, P(play) {pct(p['p_play'])}"
            )
    lines += [f"  note: {n}" for n in r["notes"]]
    return lines


def _evaluate(r: dict[str, Any]) -> list[str]:
    lines = [
        f"  {r['date']}: games {r['n_games']}; open markets {r['n_open_markets']}; mapped "
        f"{r['n_mapped']}; skipped {r['skipped']}",
        f"  contracts evaluated (both sides): {r['n_contracts_evaluated']}; flagged positive-EV: "
        f"{r['n_flagged_positive_ev']}",
    ]
    if r["no_positive_ev_found"]:
        lines.append(f"  verdict: {r['verdict_for_rest']} (for every evaluated contract)")
    if r["model_weight_zero"]:
        lines.append(
            f"  engine DEFERS TO THE MARKET: settled track record n={r['n_settled_track']} < "
            f"min_settled={r['min_settled']}, so model weight is 0."
        )
    lines.append(f"  top {len(r['top'])} ranked by {r['ranked_by']}:")
    for t in r["top"]:
        lo, hi = t["prob_interval"]
        lines.append(
            f"   - {t['ticker']} {t['side'].upper()}: ask {usd(t['price'])} fees {usd(t['fees'])}; "
            f"model {pct(t['model_prob'])} [{pct(lo)}, {pct(hi)}]; EV {usd(t['ev'], True)}, "
            f"ev_low {usd(t['ev_low'], True)}; verdict {t['verdict']}; "
            f"raw {pct(t['raw_model_prob'])}, "
            f"raw_ev_low {usd(t['raw_ev_low'], True)}"
        )
    return lines


def _explain(r: dict[str, Any]) -> list[str]:
    s = r["stored_prediction"]
    q = f"q10 {s.get('q10')}, q50 {s.get('q50')}, q90 {s.get('q90')}"
    lines = [
        f"  {r['player']} ({r['team']}, {r['matchup']}) {r['stat']} >= {r['threshold']:g}",
        f"  P(stat >= N | plays): {pct(r['p_ge_if_plays'])}   P(play): {pct(r['p_play'])}   "
        f"unconditional (DNP counted as miss): {pct(r['p_ge_unconditional_no_refund'])}",
        f"  stored: mean {s.get('mean')}, {q}, proj_minutes {s.get('proj_minutes')}, "
        f"n_games {s.get('n_games')}, bucket {s.get('bucket')}",
        f"  routed_to: {r['routed_to']}   fallback_reason: {r['fallback_reason']}   "
        f"made_at: {r['made_at']}",
        f"  game context: {r['game_context']}",
        f"  {r['teammates_out_note']}",
    ]
    k = r["kalshi"]
    if k is None:
        lines.append(f"  Kalshi: {r['kalshi_note']}")
    else:
        lines.append(
            f"  Kalshi {k['ticker']}: yes bid {usd(k['yes_bid'])}, yes ask {usd(k['yes_ask'])} "
            f"at {k['price_ts']}"
        )
    return lines


def _what_if(r: dict[str, Any]) -> list[str]:
    lines = ["  BEFORE:", *_priced_block(r["before"]), "  AFTER:", *_priced_block(r["after"])]
    d = r["delta"]

    def f(k: str, scale: float = 100.0, unit: str = " pts") -> str:
        return "n/a" if d[k] is None else f"{scale * d[k]:+.2f}{unit}"

    lines.append(
        f"  DELTA: joint prob {f('joint_prob_if_all_play')}; correlation effect "
        f"{f('correlation_effect')}; EV {f('ev', 1.0, ' $')}; ev_low {f('ev_low', 1.0, ' $')}"
    )
    return lines


def _track(r: dict[str, Any]) -> list[str]:
    lines = [f"  stat={r['stat']}: settled n={r['n_settled']} (min_settled={r['min_settled']})"]
    if r.get("insufficient_sample"):
        lines.append(f"  {r['message']}")
    if r["n_settled"]:
        lo, hi = r["observed_hit_rate_ci95"]
        lines.append(
            f"  mean model prob {pct(r['mean_model_prob'])} vs market mid "
            f"{pct(r['mean_market_mid'])} vs observed {pct(r['observed_hit_rate'])} "
            f"(95% CI [{pct(lo)}, {pct(hi)}])"
        )
    if r.get("brier_model") is not None:
        sk = r["skill_vs_market"]
        lines.append(
            f"  Brier model {r['brier_model']:.4f} vs market {r['brier_market']:.4f}; skill "
            f"{'n/a' if sk is None else f'{sk:+.4f}'}"
        )
    if r.get("paper_trades_settled"):
        lines.append(
            f"  paper trades settled n={r['paper_trades_settled']}: expected EV sum "
            f"{usd(r['paper_expected_value_sum'], True)} vs realized "
            f"{usd(r['paper_realized_pnl_sum'], True)}"
        )
    return lines


def _budget(r: dict[str, Any]) -> list[str]:
    from nba.parlay.report import _budget_md

    return [ln for ln in _budget_md(r) if ln] + [f"  {DISCLAIMER}"]


def render_result(name: str, result: dict[str, Any]) -> str:
    if "error" in result:
        return f"[{name}] ERROR: {result['error']}"
    body: list[str]
    if name == "list_slate":
        body = _slate(result)
    elif name == "evaluate_slate":
        body = _evaluate(result)
    elif name == "price_parlay":
        body = _priced_block(result)
    elif name == "explain_leg":
        body = _explain(result)
    elif name == "what_if":
        body = _what_if(result)
    elif name == "track_record":
        body = _track(result)
    elif name in ("best_for_budget", "best_for_target"):
        body = _budget(result)
    elif name == "log_paper_trade":
        body = [
            f"  logged paper trade {result['trade_id']} (verdict {result['verdict']}); "
            "no money involved"
        ]
    else:
        body = ["  (no renderer)"]
    return f"[{name}]\n" + "\n".join(body)
