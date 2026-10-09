"""``python -m nba.parlay analyze``: the daily props analysis report (markdown + JSON).

Descriptive and read-only: it prices what the existing engine prices and never writes to the
source databases. The only files written are the report outputs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nba.parlay.analysis import (
    DISCLAIMER,
    Analysis,
    Candidate,
    cand_dict,
    enumerate_combos,
    pool_legs,
    price_combos,
)
from nba.parlay.budget import best_for_budget_from, best_for_target_from
from nba.parlay.ev import NO_POSITIVE_EV


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def usd(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{'-' if x < 0 else '+'}${abs(x):.3f}" if signed else f"${x:.3f}"


def _combo_sections(an: Analysis, top: int) -> tuple[list[Candidate], list[Candidate]]:
    pool = pool_legs(an.singles, 10)
    same = price_combos(an, enumerate_combos(pool, 3, 200, "same"))
    cross = price_combos(an, enumerate_combos(pool, 2, 60, "cross"))
    key = lambda c: (-c.rec.ev_low, -c.rec.raw_ev_low, c.key)  # noqa: E731
    return sorted(same, key=key)[:top], sorted(cross, key=key)[:top]


def build_report(
    an: Analysis,
    *,
    top: int = 10,
    budget: float | None = None,
    target: float | None = None,
) -> dict[str, Any]:
    """The JSON report: contract-contract rows, parlays, gate/track record, optional budget."""
    same, cross = _combo_sections(an, top)
    contracts = an.rows
    n_pos = sum(1 for r in contracts if r["verdict"] != NO_POSITIVE_EV)
    rep: dict[str, Any] = {
        "date": an.slate.isoformat(),
        "now": an.now.isoformat(),
        "engine": an.engine,
        "dnp_policy": an.dnp_policy,
        "disclaimer": DISCLAIMER,
        "gate": an.gate_dict(),
        "fee_model": an.cfg.fee.to_json_dict(),
        "interval_level": 1.0 - an.cfg.alpha,
        "n_games": len(an.info.ctxs),
        "n_markets_read": an.n_markets,
        "n_mapped": len(an.mapped),
        "n_contracts": len(contracts),
        "n_flagged_positive": n_pos,
        "headline_verdict": NO_POSITIVE_EV if n_pos == 0 else "positive_ev_at_conservative_bound",
        "skipped": an.skipped,
        "unmatched_tickers": an.unmatched,
        "notes": an.notes,
        "shadow_enabled": an.shadow_enabled,
        "contracts": contracts,
        "same_game_parlays": [cand_dict(c) for c in same],
        "cross_game_parlays": [cand_dict(c) for c in cross],
        "parlay_price_basis": "product of leg asks, NOT a tradable price (no combo contracts "
        "are ingested); same-game legs use the copula, cross-game legs are independent",
        "narration": None,
    }
    if budget is not None and target is None:
        rep["budget"] = best_for_budget_from(an, budget)
    if budget is not None and target is not None:
        rep["budget"] = best_for_target_from(an, budget, target)
    return rep


# --------------------------------------------------------------------------- markdown
def _market_table(rows: list[dict[str, Any]], kind: str) -> list[str]:
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        is_prop = r["stat"] in ("pts", "reb", "ast", "fg3m")
        if is_prop == (kind == "prop"):
            by.setdefault(r["ticker"], {})[r["side"]] = r
    if not by:
        return ["_none priced_", ""]
    lines = [
        "| Market | Model P(yes) [80% int.] raw | Mid / ask yes | Fee | EV yes | EV_low yes | "
        "EV_low no | Weight | Verdict | p_play | raw EV_low yes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def key(item: tuple[str, dict[str, dict[str, Any]]]) -> tuple[float, float, str]:
        sides = item[1].values()
        return (
            -round(max(s["ev_low"] for s in sides), 6),
            -max(s["raw_ev_low"] for s in sides),
            item[0],
        )

    for _t, sides in sorted(by.items(), key=key):
        y = sides.get("yes") or sides["no"]
        n = sides.get("no")
        yes = sides.get("yes")
        verdict = (
            NO_POSITIVE_EV
            if all(s["verdict"] == NO_POSITIVE_EV for s in sides.values())
            else "positive_ev_at_conservative_bound"
        )
        lo, hi = (yes or y)["raw_prob_interval"]
        pyes = (yes or y)["raw_model_prob"] if yes else 1 - y["raw_model_prob"]
        lines.append(
            f"| {y['text'] if yes else 'NOT ' + y['text']} "
            f"| {pct(pyes)} [{pct(lo)}, {pct(hi)}] "
            f"| {pct(yes['mid']) if yes else 'n/a'} / {pct(yes['ask']) if yes else 'n/a'} "
            f"| {usd(yes['fees'], False) if yes else 'n/a'} "
            f"| {usd(yes['ev']) if yes else 'n/a'} | {usd(yes['ev_low']) if yes else 'n/a'} "
            f"| {usd(n['ev_low']) if n else 'n/a'} | {y['model_weight']:.2f} | {verdict} "
            f"| {pct(y['p_play']) if y['p_play'] is not None else '-'} "
            f"| {usd(yes['raw_ev_low']) if yes else 'n/a'} |"
        )
    lines.append("")
    return lines


def _drivers(rows: list[dict[str, Any]], shadow: bool, top: int) -> list[str]:
    ys = [r for r in rows if r["side"] == "yes" and r["drivers"]]
    ys.sort(key=lambda r: -r["raw_ev_low"])
    if not ys:
        return ["_no player-prop drivers available_", ""]
    hdr = (
        "| Prop | DNP risk | Proj min | Ctx shift vs recency (pts) | Teammates OUT (vacated /g) "
        "| Opp team total allowed, last 20 (league) |"
    )
    sep = "|---|---|---|---|---|---|"
    if shadow:
        hdr += " P(>=N) primary | shadow int / lt / t30 (shadow, unconfirmed) |"
        sep += "---|---|"
    out = [hdr, sep]
    for r in ys[:top]:
        d = r["drivers"]
        outs = d["teammates_out"]
        vac = d["vacated_per_game"]
        otxt = (
            ", ".join(o["name"] for o in outs[:4])
            + f" ({vac['pts']:g} pts/{vac['reb']:g} reb/{vac['ast']:g} ast)"
            if outs
            else "none listed"
        )
        oa = d["opp_allowed_last20"]
        allow = f"{oa['team']} {oa['avg']:g} ({oa['league']:g})" if oa["avg"] is not None else "n/a"
        shift = d["ctx_shift_vs_recency"]
        line = (
            f"| {r['text']} | {pct(r['dnp_risk'])} "
            f"| {d['proj_minutes'] if d['proj_minutes'] is not None else 'n/a'} "
            f"| {'n/a' if shift is None else f'{shift:+.2f}'} | {otxt} | {allow} |"
        )
        if shadow:
            sh = r["shadow"] or {"p": {}, "material_disagreement": False}
            cells = " / ".join(pct(sh["p"].get(a)) for a in ("int", "lt", "t30"))
            flag = " **DISAGREE**" if sh["material_disagreement"] else ""
            line += f" {pct(r['raw_model_prob'])} | {cells}{flag} |"
        out.append(line)
    out.append("")
    return out


def _parlay_table(cands: list[dict[str, Any]]) -> list[str]:
    if not cands:
        return ["_none priced (need >= 2 mapped legs)_", ""]
    out = [
        "| Legs | Copula/engine P [80% int.] raw | Independence P | Corr. effect | "
        "Product ask | Fee | EV | EV_low | Weight | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cands:
        lo, hi = c["raw_prob_interval"]
        ind = c["independence_prob"]
        eff = None if ind is None else c["raw_model_prob"] - ind
        out.append(
            f"| {c['text']} | {pct(c['raw_model_prob'])} [{pct(lo)}, {pct(hi)}] | {pct(ind)} "
            f"| {'n/a' if eff is None else f'{100 * eff:+.2f} pts'} | {pct(c['ask'])} "
            f"| {usd(c['fees'], False)} | {usd(c['ev'])} | {usd(c['ev_low'])} "
            f"| {c['model_weight']:.2f} | {c['verdict']} |"
        )
    out.append("")
    return out


def _budget_md(b: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if b["mode"] == "budget":
        out += [
            f"## Best use of ${b['budget_usd']:g} (whole contracts, per-order fee round-up)",
            "",
        ]
    else:
        out += [
            f"## Win at least +${b['target_profit_usd']:g} with ${b['budget_usd']:g} "
            f"(whole contracts, per-order fee round-up)",
            "",
        ]
    out += [f"**Answer: {b['answer']}**  (verdict `{b['verdict']}`)", ""]
    out += [
        f"Options considered: {b['n_options_considered']} ({b['n_parlays_considered']} parlays, "
        f"max {b.get('max_parlay_legs', 3)} legs). Ranked by {b['ranked_by']}.",
        f"Gate: {b['gate']['describe']}; needs >= {b['gate']['min_settled']} rows over "
        f">= {b['gate']['min_settled_dates']} dates with positive skill. Model weight is "
        f"{'0 (defers to the market)' if b['model_weight_zero'] else 'positive'}.",
        "",
    ]

    def block(opts: list[dict[str, Any]], label: str, raw: bool = False) -> list[str]:
        if not opts:
            return [f"_{label}: nothing fits the budget_", ""]
        t = [
            f"**{label}**",
            "",
            "| Option | n | Cost | Profit if hit | P(hit) [80% int.] | EV | EV_low "
            "| EV_low per $ | raw EV_low | Tradable |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for o in opts:
            od = o["order"]
            lo, hi = o["raw_prob_interval"] if raw else o["prob_interval"]
            if raw:  # unshrunk view: raw probability and raw EVs, per-$ on the raw low bound
                o = o | {"model_prob": o["raw_model_prob"]}
                od = od | {
                    "ev": od["raw_ev"],
                    "ev_low": od["raw_ev_low"],
                    "ev_low_per_dollar": od["raw_ev_low"] / od["cost"],
                }
            t.append(
                f"| {o['legs_text']} | {od['contracts']} | {usd(od['cost'], False)} "
                f"| {usd(od['profit_if_hit'], False)} | {pct(o['model_prob'])} "
                f"[{pct(lo)}, {pct(hi)}] | {usd(od['ev'])} | {usd(od['ev_low'])} "
                f"| {pct(od['ev_low_per_dollar'])} | {usd(od['raw_ev_low'])} "
                f"| {'yes' if o['tradable'] else 'no (hypothetical)'} |"
            )
        return [*t, ""]

    out += block(
        b["ranked"],
        "Ranked by "
        + (
            "EV at the low bound per $ staked"
            if b["mode"] == "budget"
            else "P(hit): most likely way to reach the target"
        ),
    )
    if b["mode"] == "target":
        out += block(b["best_ev_per_dollar"], "Secondary: best EV at the low bound per $ staked")
        out += [
            "**Risk/EV frontier** (primary = MOST LIKELY way to make each target; bigger targets "
            "mean lower P(hit); the last two columns are the best EV-per-$ option)",
            "",
            "| Target | Most likely option | P(hit) | Contracts | Cost | EV | EV_low "
            "| EV_low per $ | Best EV/$ option | its P(hit) | its EV_low per $ |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for f in b["frontier"]:
            if not f["reachable"]:
                out.append(f"| +${f['target_profit']:g} | unreachable within budget |||||||||| |")
                continue
            e = f["best_ev_per_dollar"]
            out.append(
                f"| +${f['target_profit']:g} | {f['option']} | {pct(f['p_hit'])} "
                f"| {f['contracts']} | {usd(f['cost'], False)} | {usd(f['ev'])} "
                f"| {usd(f['ev_low'])} | {pct(f['ev_low_per_dollar'])} | {e['option']} "
                f"| {pct(e['p_hit'])} | {pct(e['ev_low_per_dollar'])} |"
            )
        out += ["", f"_{b['parlay_price_note']}_", ""]
    out += block(
        b["raw_hypothesis"]["options"],
        f"Raw unshrunk model view ({b['raw_hypothesis']['label']})",
        raw=True,
    )
    return out


def render_markdown(rep: dict[str, Any]) -> str:
    g = rep["gate"]
    weight_txt = (
        "0.00 until the gate opens: the engine defers to the market, so no positive-EV "
        "verdict is possible."
        if not g["gate_open"]
        else "positive (gate open)."
    )
    lines = [
        f"# Props analysis {rep['date']}",
        "",
        f"> {rep['disclaimer']}",
        "",
        f"- As of: {rep['now']} UTC. Engine: {rep['engine']}; DNP policy: {rep['dnp_policy']}; "
        f"intervals: {pct(rep['interval_level'])}.",
        f"- Sample sizes: {rep['n_games']} game(s) with forward predictions; "
        f"{rep['n_markets_read']} open Kalshi markets read, {rep['n_mapped']} mapped and pre-tip, "
        f"{rep['n_contracts']} contracts priced (both sides). Skipped: {rep['skipped'] or 'none'}.",
        f"- Track record (shadow log vs market): {g['describe']}. Gate needs >= "
        f"{g['min_settled']} settled rows over >= {g['min_settled_dates']} slate dates with "
        f"positive skill; open = {g['gate_open']}.",
        f"- Model weight (market-as-prior): {weight_txt} Raw columns are the unshrunk model, "
        "a hypothesis to test.",
        f"- Fees: {rep['fee_model']['name']} (verified={rep['fee_model']['verified']}), taker, "
        "rounded up per order; EV uses the ASK, never the mid.",
        f"- **Headline verdict: `{rep['headline_verdict']}`** "
        f"({rep['n_flagged_positive']} of {rep['n_contracts']} contracts positive at the low "
        "bound).",
        "",
    ]
    for n in rep["notes"]:
        lines.append(f"- note: {n}")
    if rep["unmatched_tickers"]:
        lines.append(
            f"- WARNING: {len(rep['unmatched_tickers'])} prop market(s) have no reviewed alias and "
            f"were NOT priced: {', '.join(rep['unmatched_tickers'][:8])}"
        )
    lines += ["", "## Game markets", ""]
    lines += _market_table(rep["contracts"], "game")
    lines += ["## Player props", ""]
    lines += _market_table(rep["contracts"], "prop")
    lines += ["## Key drivers" + (" and shadow arms" if rep["shadow_enabled"] else ""), ""]
    lines += _drivers(rep["contracts"], rep["shadow_enabled"], 25)
    lines += [
        "## Same-game parlays (sorted by EV at the low bound)",
        "",
        f"_{rep['parlay_price_basis']}._",
        "",
    ]
    lines += _parlay_table(rep["same_game_parlays"])
    lines += [
        "## Cross-game combos (2 legs, assumed independent per the independence check)",
        "",
    ]
    lines += _parlay_table(rep["cross_game_parlays"])
    if rep.get("budget"):
        lines += _budget_md(rep["budget"])
    if rep.get("narration"):
        lines += [
            "## Plain-English summary (local LLM; numbers checked against the tables)",
            "",
            rep["narration"],
            "",
            f"_{rep['narration_note']}_",
            "",
        ]
    elif rep.get("narration_note"):
        lines += [f"_{rep['narration_note']}_", ""]
    return "\n".join(lines)


def write_report(rep: dict[str, Any], out: Path | str) -> tuple[Path, Path]:
    """Write ``<out>/<date>.md`` and ``.json`` (``out`` may also be a ``.md`` path)."""
    p = Path(out)
    if p.suffix in (".md", ".json"):
        md, js = p.with_suffix(".md"), p.with_suffix(".json")
    else:
        md, js = p / f"{rep['date']}.md", p / f"{rep['date']}.json"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render_markdown(rep))
    js.write_text(json.dumps(rep, indent=1, default=str))
    return md, js


def parse_now(t: str | None) -> datetime | None:
    return None if t is None else datetime.fromisoformat(t).replace(tzinfo=None)
