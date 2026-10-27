"""Label-free minimum-data gate for HALFTIME_OVERS_VS_PRICE.

Rule text: docs/prereg/HALFTIME_OVERS_VS_PRICE.md. Counts, per captured night, the Kalshi
player-prop Yes quotes that the rule's halftime entry snapshot would see and how many pass the
entry MECHANICS (quote present, depth, unresolved line, player on the floor in the first half).
No outcome is read: no final box score, no settled market, no second-half state. No model
probability is computed (none exists live yet).

Input: the write-once live capture ``data/raw/live_<date>/`` (manifest.jsonl, espn_scoreboard,
espn_summary, kalshi). Output: one JSON with per-night and pooled counts. Read-only; never opens
nba.duckdb.

    uv run python -m nba.eval.halftime_overs_gate --raw data/raw \
        --out reports/halftime_overs/gate.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: Kalshi prop series in scope -> stat name and the ESPN box column that carries it.
SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
ESPN_COL = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3m": "3PT"}
#: T508 line grid (Kalshi "k+" strike k); off-grid strikes are counted, never entries
#: (no extrapolation).
T508_GRID = {
    "pts": {10, 15, 20, 25, 30},
    "reb": {2, 4, 6, 8, 10},
    "ast": {2, 4, 6, 8},
    "fg3m": {1, 2, 3, 4},
}
ESPN_TO_KALSHI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}

#: Entry snapshot constants (the rule's values; changing one is a new rule).
DECISION_DELAY_S = 120  # decision time t* = first ESPN tick showing STATUS_HALFTIME + 120 s
QUOTE_WINDOW_S = 180  # the Kalshi page used is the first one received in [t*, t* + 180 s]
STATE_MAX_AGE_S = (
    150  # the ESPN state used must be <= 150 s older than the Kalshi page and still show halftime
)
#: Yes ask band in integer cents; outside it the ask is not a tradeable $3-5 hand entry.
ASK_MIN_C, ASK_MAX_C = 2, 97
TAKER_COEF = 0.07  # configs/parlay.yaml fee_model (verified)
COLD_MIN_FGA, COLD_MAX_PCT = 5, 0.30  # cold-first-half slice
BLOWOUT_MARGIN, FOUL_TROUBLE_PF = 15, 3


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def fold(name: str) -> str:
    """Lower-case ASCII letters only, suffixes dropped ("Jr.", "III")."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def fee_one_contract(ask: float, coef: float = TAKER_COEF) -> float:
    """Kalshi taker fee on a ONE-contract order: ceil to the cent of coef * P * (1 - P)."""
    return math.ceil(round(coef * ask * (1.0 - ask) * 100.0, 9)) / 100.0


def strike_of(market: dict[str, Any]) -> int | None:
    fs = market.get("floor_strike")
    if fs is None or market.get("strike_type") != "greater":
        return None
    k = float(fs) + 0.5
    return int(k) if abs(k - round(k)) < 1e-9 else None


def player_of(market: dict[str, Any]) -> str | None:
    title = str(market.get("title") or market.get("yes_sub_title") or "")
    m = re.match(r"^(.*?):\s*\d+\+", title)
    return m.group(1).strip() if m else None


def to_f(v: Any) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def event_key(event_ticker: str) -> str | None:
    """'KXNBAPTS-26OCT25DETPHI' -> '26OCT25DETPHI'."""
    parts = event_ticker.split("-")
    return parts[1] if len(parts) >= 2 else None


@dataclass
class Halftime:
    event_id: str
    away: str
    home: str
    ht_first: datetime
    q3_first: datetime | None
    margin_abs: int | None


def load_manifest(night: Path) -> dict[str, list[tuple[datetime, Path, str]]]:
    by: dict[str, list[tuple[datetime, Path, str]]] = defaultdict(list)
    with (night / "manifest.jsonl").open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("http_status") != 200:
                continue
            by[r["source"]].append((parse_ts(r["received_at"]), night / r["path"], r["key"]))
    for v in by.values():
        v.sort(key=lambda t: t[0])
    return by


def read_gz(p: Path) -> Any:
    with gzip.open(p) as f:
        return json.loads(f.read())


def halftimes(scoreboard: list[tuple[datetime, Path, str]]) -> dict[str, Halftime]:
    out: dict[str, Halftime] = {}
    q3: dict[str, datetime] = {}
    for ts, path, _ in scoreboard:
        try:
            body = read_gz(path)
        except (OSError, ValueError):
            continue
        for ev in body.get("events", []):
            eid = str(ev.get("id"))
            st = ev.get("status", {})
            name = st.get("type", {}).get("name")
            period = int(st.get("period") or 0)
            if name == "STATUS_HALFTIME" and eid not in out:
                comp = ev["competitions"][0]
                teams = {c["homeAway"]: c for c in comp["competitors"]}
                a = teams["away"]["team"]["abbreviation"]
                h = teams["home"]["team"]["abbreviation"]
                try:
                    margin: int | None = abs(
                        int(teams["home"]["score"]) - int(teams["away"]["score"])
                    )
                except (KeyError, ValueError):
                    margin = None
                out[eid] = Halftime(
                    eid, ESPN_TO_KALSHI.get(a, a), ESPN_TO_KALSHI.get(h, h), ts, None, margin
                )
            if eid in out and eid not in q3 and period >= 3 and name != "STATUS_HALFTIME":
                q3[eid] = ts
    for eid, t in q3.items():
        out[eid].q3_first = t
    return out


def box_at(
    summaries: list[tuple[datetime, Path, str]], t: datetime
) -> tuple[datetime, dict[str, Any]] | None:
    """Latest ESPN summary received at or before t (state only; first-half box at halftime)."""
    best = None
    for ts, path, _ in summaries:
        if ts <= t:
            best = (ts, path)
        else:
            break
    if best is None:
        return None
    return best[0], read_gz(best[1])


def box_players(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """folded name -> {min, stat columns, starter, dnp, ejected, pf, team}."""
    out: dict[str, dict[str, Any]] = {}
    for team in summary.get("boxscore", {}).get("players", []):
        abbr = team.get("team", {}).get("abbreviation", "")
        for block in team.get("statistics", []):
            labels = block.get("labels") or block.get("names") or []
            for a in block.get("athletes", []):
                stats = a.get("stats") or []
                row = dict(zip(labels, stats, strict=False)) if stats else {}
                out[fold(a["athlete"]["displayName"])] = {
                    "team": abbr,
                    "row": row,
                    "starter": bool(a.get("starter")),
                    "dnp": bool(a.get("didNotPlay")) or not stats,
                    "ejected": bool(a.get("ejected")),
                }
    return out


def stat_value(row: dict[str, str], stat: str) -> int | None:
    v = row.get(ESPN_COL[stat])
    if v in (None, "", "--"):
        return None
    try:
        return int(str(v).split("-")[0])
    except ValueError:
        return None


def minutes(row: dict[str, str]) -> float:
    try:
        return float(row.get("MIN") or 0)
    except ValueError:
        return 0.0


def is_cold(row: dict[str, str]) -> bool:
    """Cold first half: FG% <= 0.30 on >= 5 field-goal attempts (ESPN first-half box only)."""
    try:
        made, att = (int(x) for x in str(row.get("FG") or "").split("-"))
    except ValueError:
        return False
    return att >= COLD_MIN_FGA and made <= COLD_MAX_PCT * att


def summary_status(summary: dict[str, Any]) -> str | None:
    try:
        return str(summary["header"]["competitions"][0]["status"]["type"]["name"])
    except (KeyError, IndexError):
        return None


def night_counts(night: Path) -> dict[str, Any]:
    man = load_manifest(night)
    sb = man.get("espn_scoreboard", [])
    hts = halftimes(sb)
    summaries: dict[str, list[tuple[datetime, Path, str]]] = defaultdict(list)
    for ts, path, key in man.get("espn_summary", []):
        summaries[key].append((ts, path, key))
    kal: dict[str, list[tuple[datetime, Path, str]]] = defaultdict(list)
    for ts, path, key in man.get("kalshi", []):
        series = key.split("_p")[0]
        if series in SERIES:
            kal[series].append((ts, path, key))

    c: Counter[str] = Counter()
    by_stat: dict[str, Counter[str]] = defaultdict(Counter)
    games: list[dict[str, Any]] = []
    units: set[tuple[str, str, str]] = set()
    unit_by_stat: dict[str, set[tuple[str, str]]] = defaultdict(set)
    c["games_halftime_seen"] = len(hts)
    for eid, ht in sorted(hts.items()):
        t_star = ht.ht_first + timedelta(seconds=DECISION_DELAY_S)
        g: dict[str, Any] = {
            "event_id": eid,
            "game": f"{ht.away}@{ht.home}",
            "ht_first": ht.ht_first.isoformat(),
        }
        if ht.q3_first is not None and ht.q3_first <= t_star:
            g["skip"] = "q3_before_decision_time"
            games.append(g)
            c["games_skip_q3_before_tstar"] += 1
            continue
        g_rows = 0
        g_entries = 0
        g_ok_snapshot = 0
        for series, stat in SERIES.items():
            pages = [
                p
                for p in kal.get(series, [])
                if t_star <= p[0] <= t_star + timedelta(seconds=QUOTE_WINDOW_S)
            ]
            if not pages:
                c["series_no_page_in_window"] += 1
                continue
            first_ts = pages[0][0]
            same_tick = [p for p in pages if (p[0] - first_ts).total_seconds() < 30]
            state = box_at(summaries.get(eid, []), first_ts)
            if state is None or (first_ts - state[0]).total_seconds() > STATE_MAX_AGE_S:
                c["series_state_stale_or_missing"] += 1
                continue
            if summary_status(state[1]) != "STATUS_HALFTIME":
                c["series_state_not_halftime"] += 1
                continue
            g_ok_snapshot += 1
            box = box_players(state[1])
            for _, path, _ in same_tick:
                for m in read_gz(path).get("markets", []):
                    ek = event_key(str(m.get("event_ticker", "")))
                    if ek is None or not ek.endswith(ht.away + ht.home):
                        continue
                    k = strike_of(m)
                    name = player_of(m)
                    if k is None or name is None:
                        continue
                    bs = by_stat[stat]
                    bs["markets_at_snapshot"] += 1
                    g_rows += 1
                    if m.get("status") != "active":
                        bs["x_not_active"] += 1
                        continue
                    ask = to_f(m.get("yes_ask_dollars"))
                    size = to_f(m.get("yes_ask_size_fp")) or 0.0
                    if ask is None or not (ASK_MIN_C <= round(100 * ask) <= ASK_MAX_C) or size < 1:
                        bs["x_no_tradeable_ask"] += 1
                        continue
                    p = box.get(fold(name))
                    if p is None:
                        bs["x_player_unmatched"] += 1
                        continue
                    if p["dnp"] or minutes(p["row"]) <= 0:
                        bs["x_dnp_first_half"] += 1
                        continue
                    if p["ejected"]:
                        bs["x_ejected"] += 1
                        continue
                    cur = stat_value(p["row"], stat)
                    if cur is None:
                        bs["x_stat_missing"] += 1
                        continue
                    if cur >= k:
                        bs["x_already_resolved"] += 1
                        continue
                    bs["mechanics_pass"] += 1
                    if k not in T508_GRID[stat]:
                        bs["x_off_t508_grid"] += 1
                        continue
                    bs["entry_candidates"] += 1
                    g_entries += 1
                    if size * ask >= 5.0:
                        bs["entry_candidates_depth_5usd"] += 1
                    bs["fee_cents_sum"] += round(fee_one_contract(ask) * 100)
                    bs["ask_cents_sum"] += round(ask * 100)
                    if ht.margin_abs is not None and ht.margin_abs >= BLOWOUT_MARGIN:
                        bs["slice_blowout15"] += 1
                        bs[
                            "slice_blowout15_bench"
                            if not p["starter"]
                            else "slice_blowout15_starter"
                        ] += 1
                    try:
                        if int(p["row"].get("PF") or 0) >= FOUL_TROUBLE_PF:
                            bs["slice_fouls3"] += 1
                    except ValueError:
                        pass
                    if is_cold(p["row"]):
                        bs["slice_cold"] += 1
                    units.add((eid, stat, fold(name)))
                    unit_by_stat[stat].add((eid, fold(name)))
        g.update(
            {"snapshots_ok_series": g_ok_snapshot, "markets": g_rows, "entry_candidates": g_entries}
        )
        if g_ok_snapshot:
            c["games_with_snapshot"] += 1
        games.append(g)
    c["player_stat_units"] = len(units)
    for stat, us in unit_by_stat.items():
        by_stat[stat]["player_stat_units"] = len(us)
    return {
        "night": night.name,
        "counts": dict(c),
        "by_stat": {k: dict(v) for k, v in by_stat.items()},
        "games": games,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raw", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("reports/halftime_overs/gate.json"))
    ap.add_argument(
        "--nights", nargs="*", default=None, help="live_<date> names; default all complete nights"
    )
    args = ap.parse_args(argv)
    nights = args.nights or sorted(
        p.name for p in args.raw.glob("live_*") if (p / "manifest.jsonl").exists()
    )
    res = []
    for n in nights:
        r = night_counts(args.raw / n)
        print(
            n,
            r["counts"],
            {s: v.get("entry_candidates", 0) for s, v in r["by_stat"].items()},
            flush=True,
        )
        res.append(r)
    pooled: dict[str, Counter[str]] = defaultdict(Counter)
    for r in res:
        for s, v in r["by_stat"].items():
            pooled[s].update(v)
    out = {
        "rule": "HALFTIME_OVERS_VS_PRICE (DRAFT)",
        "constants": {
            "DECISION_DELAY_S": DECISION_DELAY_S,
            "QUOTE_WINDOW_S": QUOTE_WINDOW_S,
            "STATE_MAX_AGE_S": STATE_MAX_AGE_S,
            "ASK_MIN_C": ASK_MIN_C,
            "ASK_MAX_C": ASK_MAX_C,
            "TAKER_COEF": TAKER_COEF,
            "COLD_MIN_FGA": COLD_MIN_FGA,
            "COLD_MAX_PCT": COLD_MAX_PCT,
            "BLOWOUT_MARGIN": BLOWOUT_MARGIN,
            "FOUL_TROUBLE_PF": FOUL_TROUBLE_PF,
        },
        "label_free": (
            "no final box, no settlement, no second-half state, no model probability read"
        ),
        "nights": res,
        "pooled_by_stat": {k: dict(v) for k, v in pooled.items()},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
