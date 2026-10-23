# ruff: noqa: E501
"""Build, render and write the accuracy league table (``python -m nba.accuracy_league --date D``).

MONITOR ONLY. Descriptive scores of every pre-tip source on every player-prop line priced before
tip. It makes no claim and is not a decision rule; decisions come only from frozen rules.

Read-only: ``nba.duckdb`` and ``market_asof.duckdb`` open with ``read_only=True``; only
``game_date >= 2026-10-01`` is read (season 2025 is never loaded).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from nba.accuracy_league import scoring, sources
from nba.forward import scorecard as sc

MAIN = Path("/Users/devin/Downloads/nba-prediction")
DEFAULT_SINCE = date(2026, 10, 21)
W_MODEL = 0.25  # blend = W_MODEL * model + (1 - W_MODEL) * Kalshi mid (market as the prior)
BASE = "kalshi"
ORDER = ("kalshi", "pinnacle", "consensus", "model", "blend", "llm", "devin", "devin_call")
LABEL = {
    "kalshi": "Kalshi mid (last snapshot in the 10 min before tip)",
    "pinnacle": "Pinnacle de-vig (scorecard definition: close, else latest pre-tip; own main line)",
    "consensus": "All-books consensus (scorecard definition: median raw prob, de-vigged)",
    "model": "Production props_context_residual P(stat >= k)",
    "blend": "25/75 blend (0.25 model + 0.75 Kalshi mid)",
    "llm": "llm_shadow p_over",
    "devin": "Devin's reads (probabilities)",
    "devin_call": "Devin's reads (hard calls; log loss not defined)",
}
NO_LOGLOSS = frozenset({"devin_call"})
HEADER = (
    "**This is a monitor, not a claim.** It scores every pre-tip source on every player-prop line "
    "it priced, descriptively. It is not a decision rule, it pre-registers nothing, and no CI here "
    "supports a conclusion. Decisions come only from frozen rules. Sources price different line sets, so "
    "standalone scores are NOT comparable across rows; only the paired differences against the Kalshi "
    "mid (on lines both priced) are like-for-like. Lower Brier / log loss is better; a negative "
    "difference means the source beat Kalshi on the shared lines."
)


def _pairs(rows: list[dict[str, Any]], src: str, metric: str) -> dict[str, Any] | None:
    f = scoring.brier if metric == "brier" else scoring.logloss
    sub = [r for r in rows if src in r["p"] and BASE in r["p"]]
    if not sub:
        return None
    dates = [r["date"] for r in sub]
    d = [f(r["p"][src], r["y"]) - f(r["p"][BASE], r["y"]) for r in sub]
    out = scoring.clustered_mean(dates, d)
    out["src_mean"] = float(np.mean([f(r["p"][src], r["y"]) for r in sub]))
    out["kalshi_mean"] = float(np.mean([f(r["p"][BASE], r["y"]) for r in sub]))
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-source scores and paired-vs-Kalshi differences over settled, non-DNP rows."""
    out: dict[str, Any] = {"n_rows": len(rows), "sources": {}}
    for s in ORDER:
        sub = [r for r in rows if s in r["p"]]
        if not sub:
            continue
        dates = [r["date"] for r in sub]
        ps = [r["p"][s] for r in sub]
        ys = [r["y"] for r in sub]
        e: dict[str, Any] = {
            "n": len(sub),
            "dates": len(set(dates)),
            "base_rate": float(np.mean(ys)),
            "mean_p": float(np.mean(ps)),
            "brier": scoring.clustered_mean(
                dates, [scoring.brier(p, y) for p, y in zip(ps, ys, strict=True)]
            ),
            "reliability": scoring.reliability(ps, ys),
        }
        if s not in NO_LOGLOSS:
            e["logloss"] = scoring.clustered_mean(
                dates, [scoring.logloss(p, y) for p, y in zip(ps, ys, strict=True)]
            )
        if s != BASE:
            e["vs_kalshi"] = {"brier": _pairs(rows, s, "brier")}
            if s not in NO_LOGLOSS:
                e["vs_kalshi"]["logloss"] = _pairs(rows, s, "logloss")
        out["sources"][s] = e
    return out


def collect(
    nba_con: Any,
    odds_con: Any | None,
    day: date,
    since: date,
    *,
    kalshi_root: Path,
    reads_path: Path,
    names: Any,
    read_rosters: Any,
    roster_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every priced line with each source's P(stat >= k) and, once settled, the outcome."""
    games = sources.load_games(nba_con, since, day)
    gmap = {g.game_id: g for g in games}
    gids = list(gmap)
    meta: dict[str, Any] = {"games": len(games), "kalshi_drops": {}, "reads_drops": {}}
    prod = sources.load_forward(nba_con, gids, sources.PROD)
    llm = sources.load_forward(nba_con, gids, sources.LLM)
    box = sources.load_box(nba_con, gids)
    settled = {g.game_id for g in games if g.settled and any(k[0] == g.game_id for k in box)}

    pids_by_game: dict[str, set[int]] = defaultdict(set)
    for gid, pid, _ in prod:
        pids_by_game[gid].add(pid)
    for gid, pid, _ in llm:
        pids_by_game[gid].add(pid)

    lines: dict[sources.Key, dict[str, float]] = defaultdict(dict)
    extra: dict[sources.Key, dict[str, float]] = {}
    names_by_game: dict[str, dict[str, set[int]]] = {}
    cache: dict[str, Any] = {}
    for g in games:
        rr = read_rosters(g.et_date, set(g.teams), roster_dir)
        nm: dict[str, set[int]] = defaultdict(set)
        for pid in pids_by_game[g.game_id]:
            nm[sources.nz(names(pid, rr.names))].add(pid)
        names_by_game[g.game_id] = nm
        kl, drops = sources.kalshi_lines(g, kalshi_root, nm, rr.membership, cache)
        for key, v in kl.items():
            lines[key][BASE] = v["mid"]
            extra[key] = v
        for why, n in drops.items():
            meta["kalshi_drops"][why] = meta["kalshi_drops"].get(why, 0) + n

    if odds_con is not None and gids:
        book = sc.load_book_lines(odds_con, gids, day, sc.BENCHMARK_BOOK)
        cons = sc.load_closing_lines(odds_con, gids, day)
        for src, found in (("pinnacle", book), ("consensus", cons)):
            for (gid, pid, stat), ln in found.items():
                k = sources.half_line_k(ln["line"])
                if k is not None:
                    lines[(gid, pid, stat, k)][src] = float(ln["p_over_devig"])
    for (gid, pid, stat), pred in llm.items():
        got = sources.llm_p(pred)
        if got:
            lines[(gid, pid, stat, got[0])]["llm"] = got[1]
    reads, meta["reads_drops"] = sources.load_reads(reads_path, gmap, names_by_game)
    for key, rd in reads.items():
        lines[key]["devin" if rd["kind"] == "p" else "devin_call"] = rd["p"]

    rows = []
    for key in sorted(lines):
        gid, pid, stat, k = key
        p = dict(lines[key])
        pm = sources.model_p(prod[(gid, pid, stat)], k) if (gid, pid, stat) in prod else None
        if pm is not None:
            p["model"] = pm
            if BASE in p:
                p["blend"] = W_MODEL * pm + (1.0 - W_MODEL) * p[BASE]
        status, y = "unsettled", None
        if gid in settled:
            b = box.get((gid, pid))
            if b is None or b["minutes"] is None or b["minutes"] <= 0 or b[stat] is None:
                status = "dnp"
            else:
                status, y = "scored", float(b[stat] >= k)
        rows.append({"key": key, "game_id": gid, "player_id": pid, "stat": stat, "k": k,
                     "date": gmap[gid].et_date.isoformat(), "p": p, "y": y, "status": status})  # fmt: skip
    meta["unsettled_games"] = sorted(g.game_id for g in games if g.game_id not in settled)
    return rows, meta


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in ORDER:
        sub = [r for r in rows if s in r["p"]]
        by_date: dict[str, int] = defaultdict(int)
        for r in sub:
            by_date[r["date"]] += 1
        out[s] = {
            "priced": len(sub),
            "scored": sum(r["status"] == "scored" for r in sub),
            "dnp": sum(r["status"] == "dnp" for r in sub),
            "unsettled": sum(r["status"] == "unsettled" for r in sub),
            "priced_by_date": dict(sorted(by_date.items())),
        }
    return out


def build_report(
    rows: list[dict[str, Any]], meta: dict[str, Any], day: date, since: date
) -> dict[str, Any]:
    scored = [r for r in rows if r["status"] == "scored"]
    w7 = day - timedelta(days=6)
    last7 = [r for r in scored if date.fromisoformat(r["date"]) >= max(w7, since)]
    return {
        "header": HEADER,
        "date": day.isoformat(),
        "since": since.isoformat(),
        "last7_from": max(w7, since).isoformat(),
        "blend_w_model": W_MODEL,
        "labels": LABEL,
        "meta": meta,
        "universe_lines": len(rows),
        "coverage": coverage(rows),
        "cumulative": summarize(scored),
        "last7": summarize(last7),
    }


# ------------------------------------------------------------------ rendering


def _n(x: float | None, d: int = 4) -> str:
    return "n/a" if x is None or x != x else f"{x:.{d}f}"


def _cell(c: dict[str, Any] | None, d: int = 4) -> str:
    if not c:
        return "-"
    return f"{_n(c['value'], d)} [{_n(c['lo'], d)}, {_n(c['hi'], d)}]"


def _table(view: dict[str, Any]) -> list[str]:
    out = ["| Source | n | dates | mean p | base rate | Brier [95% CI] | Log loss [95% CI] |",
           "|---|---|---|---|---|---|---|"]  # fmt: skip
    for s, e in view["sources"].items():
        out.append(f"| {s} | {e['n']} | {e['dates']} | {_n(e['mean_p'], 3)} | {_n(e['base_rate'], 3)} | "
                   f"{_cell(e['brier'])} | {_cell(e.get('logloss')) if 'logloss' in e else 'n/a'} |")  # fmt: skip
    out += ["", "Paired difference vs Kalshi mid on shared lines (source minus Kalshi; negative = source better; "
            "date-clustered 95% bootstrap CI):", "",
            "| Source | shared n | dates | Brier diff [CI] | Log loss diff [CI] |", "|---|---|---|---|---|"]  # fmt: skip
    for s, e in view["sources"].items():
        v = e.get("vs_kalshi")
        if not v or not v.get("brier"):
            continue
        b, ll = v["brier"], v.get("logloss")
        out.append(f"| {s} | {b['n']} | {b['dates']} | {_cell(b)} | {_cell(ll) if ll else 'n/a'} |")
    return out


def render(rep: dict[str, Any]) -> str:
    cov = rep["coverage"]
    L = [f"# Accuracy league table, {rep['date']}", "", rep["header"], "",
         f"Cumulative: slate dates {rep['since']} to {rep['date']} (America/New_York). "
         f"Last 7 days: {rep['last7_from']} to {rep['date']}. Universe: {rep['universe_lines']} lines "
         "(game, player, stat, k) priced by at least one source before tip. A Kalshi k+ line is the "
         "sportsbook over at k - 0.5; whole-number sportsbook lines are skipped. "
         "Scored only on settled games, players with minutes > 0 (DNP lines are counted, not scored).",
         "", "## Coverage (lines priced)", "",
         "| Source | priced | scored | DNP | unsettled | what it is |", "|---|---|---|---|---|---|"]  # fmt: skip
    for s in ORDER:
        c = cov[s]
        L.append(
            f"| {s} | {c['priced']} | {c['scored']} | {c['dnp']} | {c['unsettled']} | {rep['labels'][s]} |"
        )
    dates = sorted({d for s in ORDER for d in cov[s]["priced_by_date"]})
    L += ["", "| Source | " + " | ".join(dates) + " |", "|---|" + "---|" * len(dates)]
    for s in ORDER:
        L.append(
            f"| {s} | " + " | ".join(str(cov[s]["priced_by_date"].get(d, 0)) for d in dates) + " |"
        )
    m = rep["meta"]
    L += ["", f"Games in range: {m['games']}. Not yet settled: {', '.join(m['unsettled_games']) or 'none'}.",
          f"Kalshi lines dropped (counted): {json.dumps(m['kalshi_drops'], sort_keys=True)}.",
          f"Devin's reads rejected (counted): {json.dumps(m['reads_drops'], sort_keys=True)}.",
          "", "## Cumulative", ""]  # fmt: skip
    L += _table(rep["cumulative"])
    L += ["", "### Reliability, cumulative (forecast mean -> observed frequency, n)", "",
          "| Bin | " + " | ".join(rep["cumulative"]["sources"]) + " |",
          "|---|" + "---|" * len(rep["cumulative"]["sources"])]  # fmt: skip
    for b in range(scoring.N_BINS):
        cells = []
        for e in rep["cumulative"]["sources"].values():
            r = e["reliability"][b]
            cells.append("-" if r["n"] == 0 else f"{r['mean_p']:.2f} -> {r['obs']:.2f} ({r['n']})")
        L.append(
            f"| {b / scoring.N_BINS:.1f}-{(b + 1) / scoring.N_BINS:.1f} | "
            + " | ".join(cells)
            + " |"
        )
    L += ["", f"## Last 7 days ({rep['last7_from']} to {rep['date']})", ""]
    L += (
        _table(rep["last7"])
        if rep["last7"]["sources"]
        else ["No settled scored lines in the window."]
    )
    L += ["", "Log loss clips p to [0.001, 0.999]. Blend weight is a constant, not fitted. Nothing here "
          "changes a rule, a model or a position."]  # fmt: skip
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ CLI


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--since", default=DEFAULT_SINCE.isoformat())
    ap.add_argument("--out-dir", default="reports/accuracy_league")
    ap.add_argument("--nba-db", default=str(sc.NBA_DB))
    ap.add_argument("--market-db", default=str(sc.MARKET_DB))
    ap.add_argument("--kalshi-root", default=str(MAIN / "data" / "raw"))
    ap.add_argument("--reads", default=str(MAIN / "data" / "manual" / "devin_reads.jsonl"))
    a = ap.parse_args(argv)
    day, since = date.fromisoformat(a.date), date.fromisoformat(a.since)
    from nba.explain import names
    from nba.explain.slate import read_rosters
    from nba.props.rosters import DEFAULT_ROSTER_DIR

    nba_con = sc.connect_ro(Path(a.nba_db))
    odds_con = sc.connect_ro(Path(a.market_db)) if Path(a.market_db).exists() else None
    rows, meta = collect(
        nba_con, odds_con, day, since, kalshi_root=Path(a.kalshi_root), reads_path=Path(a.reads),
        names=names.player_name, read_rosters=read_rosters, roster_dir=Path(DEFAULT_ROSTER_DIR),
    )  # fmt: skip
    rep = build_report(rows, meta, day, since)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.date}.json").write_text(json.dumps(rep, indent=2, sort_keys=True, default=str))
    (out / f"{a.date}.md").write_text(render(rep))
    print(f"wrote {out / (a.date + '.md')} lines={rep['universe_lines']}")
    return 0


__all__ = ["build_report", "collect", "main", "render", "summarize"]
