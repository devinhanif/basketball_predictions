"""Label-free gate for KALSHI_VS_PINNACLE_PAPER. Reads Kalshi captures, Pinnacle live_book_odds and
forward_predictions (ids, tipoff). No settlement, no outcome, no season-2025 read. Both DBs read_only.
Usage: python kalshi_pinnacle_gate_2026_10_22.py [REPO_ROOT]   (default: main checkout)"""
import glob, gzip, json, math, re, sys, unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/devin/Downloads/nba-prediction")
sys.path.insert(0, str(ROOT))
from nba.explain.names import player_name  # noqa: E402
from nba.explain.slate import read_rosters  # noqa: E402
from nba.props.rosters import DEFAULT_ROSTER_DIR  # noqa: E402

NY = ZoneInfo("America/New_York")
SER = {"KXNBAPTS": "player_points", "KXNBAREB": "player_rebounds", "KXNBAAST": "player_assists", "KXNBA3PT": "player_threes"}
WORD = {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists", "threes": "player_threes"}
nba = duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)
mk = duckdb.connect(str(ROOT / "data/markets/market_asof.duckdb"), read_only=True)
MS = [Fraction(x, 100) for x in (2, 3, 4, 5, 6, 8, 10)]
NS = (5, 10, 15, 20, 30)


def nz(s): return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())
def ts(p): return datetime.strptime(Path(p).name[:15], "%Y%m%dT%H%M%S")  # naive UTC
def prob(a: int) -> Fraction: return Fraction(-a, -a + 100) if a < 0 else Fraction(100, a + 100)
def fee(n: int, a: Decimal, mult=Decimal("0.07")) -> Decimal:  # coefficient from configs/parlay.yaml (read below)
    x = mult * n * a * (1 - a); return (x * 100).to_integral_value(rounding="ROUND_CEILING") / 100


COEF = Decimal(re.search(r"^\s*coefficient:\s*([0-9.]+)", (ROOT / "configs/parlay.yaml").read_text(), re.M).group(1))


def pin_quotes(gid):
    rows = mk.execute("""select player_id, market, received_at, point, side, price_american from live_book_odds
        where book='pinnacle' and game_id=? and market in (select unnest(?)) and player_id is not null and point is not null
        and price_american is not null and side in ('over','under')""", [gid, list(WORD.values())]).fetchall()
    snaps = defaultdict(dict)  # (pid, market) -> {received_at: {(point, side): price}}
    for pid, m, r, pt, sd, pr in rows: snaps[(int(pid), m)].setdefault(r, {})[(pt, sd)] = pr
    return snaps


def day(d: date):
    root = ROOT / f"data/raw/live_{d}"
    games = nba.execute("select game_id, tipoff from forward_predictions where cast(tipoff - interval 5 hour as date)=? group by 1,2", [d]).fetchall()
    tips = {g: t for g, t in games}
    if not tips or not (root / "kalshi").exists(): return None
    gt = {g: (h, a) for g, h, a in nba.execute("select game_id, home_team, away_team from games where game_id in (select unnest(?))", [list(tips)]).fetchall()}
    rr = read_rosters(d, {t for v in gt.values() for t in v}, Path(DEFAULT_ROSTER_DIR))
    pids = {r[0] for r in nba.execute("select distinct player_id from forward_predictions where game_id in (select unnest(?)) and player_id is not null", [list(tips)]).fetchall()}
    name = defaultdict(set)
    for p in pids: name[nz(player_name(p, rr.names))].add(p)
    c = Counter(); lines = []
    for s, mkt in SER.items():
        snaps = sorted((ts(f), f) for f in glob.glob(str(root / "kalshi" / f"{s}_p0" / "*.json.gz")))
        for gid, tip in tips.items():
            pre = [(t, f) for t, f in snaps if tip - timedelta(minutes=10) <= t < tip]
            if not pre: c["game_series_without_entry_snapshot"] += 1; continue
            t0, f0 = pre[-1]; psn = pin_quotes(gid); nydate = tip.replace(tzinfo=timezone.utc).astimezone(NY).strftime("%y%b%d").upper()
            for m in json.load(gzip.open(f0)).get("markets", []):
                mm = re.match(r"^(.+?): (\d+)\+ (points|rebounds|assists|threes)$", m.get("title") or "")
                if not mm or m.get("status") != "active": continue
                if m["event_ticker"].split("-")[1][:7] != nydate: continue
                ids = name.get(nz(mm.group(1)), set())
                ids = {p for p in ids if rr.membership.get(p, set()) & set(gt[gid])}
                if len(ids) != 1:
                    c["name_ambiguous_dropped" if len(ids) > 1 else "name_not_in_this_game(expected)"] += 1; continue
                pid = next(iter(ids)); b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
                if b in (None, "") or a in (None, ""): c["one_sided"] += 1; continue
                b, a = Decimal(b), Decimal(a)
                if not (0 < b < a < 1) or a - b > Decimal("0.05"): c["bad_or_wide_spread"] += 1; continue
                c["kalshi_lines_entry_ok"] += 1
                k = int(mm.group(2)); pt = k - 0.5
                qs = psn.get((pid, WORD[mm.group(3)]), {})
                cand = [r for r in qs if r <= t0 and r < tip]
                if not cand: c["no_pinnacle_snapshot_at_or_before_entry"] += 1; continue
                r = max(cand); q = qs[r]
                if not any(abs(p - round(p)) > 0.25 for p, _ in q): c["pinnacle_whole_number_line_excluded"] += 1; continue
                if (pt, "over") not in q or (pt, "under") not in q: c["pinnacle_no_exact_half_line_pair"] += 1; continue
                po, pu = prob(q[(pt, "over")]), prob(q[(pt, "under")]); fair = po / (po + pu)
                lines.append(dict(d=d, gid=gid, t=t0, age=(t0 - r).total_seconds() / 60, b=b, a=a, fair=fair, stat=mm.group(3), vig=float(po + pu - 1)))
    return len(tips), c, lines


def edges(L):
    for x in L:
        yield "YES", x["fair"] - Fraction(x["a"]), x
        yield "NO", (1 - x["fair"]) - (1 - Fraction(x["b"])), x


if __name__ == "__main__":
    allL = {}
    print(f"fee coefficient from configs/parlay.yaml: {COEF}")
    for d in (date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)):
        r = day(d)
        if r is None: print(f"{d}: no Kalshi capture dir or no forward_predictions games -> 0 (not usable)"); continue
        ng, c, L = r; allL[d] = L
        print(f"{d}: games {ng}; matched lines (Kalshi ok + fresh-any-age Pinnacle exact half pair) {len(L)}; drops {dict(c)}")
    flat = [x for L in allL.values() for x in L]
    ages = sorted(x["age"] for x in flat)
    if ages:
        q = lambda p: ages[min(len(ages) - 1, int(p * len(ages)))]
        print(f"age of matched Pinnacle snapshot vs Kalshi entry (min): n={len(ages)} p50={q(.5):.1f} p90={q(.9):.1f} max={ages[-1]:.1f}")
        print("age counts:", {f"<={n}": sum(a <= n for a in ages) for n in NS})
        print(f"median overround of pinnacle pair {sorted(x['vig'] for x in flat)[len(flat)//2]:.4f}")
    print("\nqualifying trades (rows: N min, m; per date: count) [pre-fee margin f - ask or bid - f >= m]")
    sd = {}
    for n in NS:
        for m in MS:
            per = {d: 0 for d in allL}; ret = []
            for d, L in allL.items():
                for dr, e, x in edges([x for x in L if x["age"] <= n]):
                    if e >= m:
                        per[d] += 1
                        p = Fraction(x["a"]) if dr == "YES" else 1 - Fraction(x["b"])
                        ret.append((float(p), float(e)))
            print(f" N<={n:>2} m={float(m):.2f}: total {sum(per.values()):>3}  per date {[per[d] for d in per]}")
            sd[(n, m)] = ret
    # MDE arithmetic at the leading candidates: per-trade SD of a $10 binary trade bought at p, true prob p+e
    print("\nMDE arithmetic ($ per $10 trade; contracts=floor(10/p); fee per config; SD of binary return; clustering ignored => lower bound on MDE)")
    for key in [(15, Fraction(2, 100)), (15, Fraction(3, 100))]:
        r = sd.get(key, [])
        if not r: print(key, "none"); continue
        sds = []; nets = []
        for p, e in r:
            n = math.floor(10 / p); fe = float(fee(n, Decimal(str(round(p, 2))), COEF))
            tp = p + e; sds.append(n * math.sqrt(tp * (1 - tp))); nets.append(n * e - fe)
        sdm = math.sqrt(sum(s * s for s in sds) / len(sds)); mean_net = sum(nets) / len(nets)
        nights = max(1, len(allL)); per_night = len(r) / sum(1 for d in allL if allL[d])
        n80 = (2.8 * sdm / mean_net) ** 2 if mean_net > 0 else float("inf")
        pos = sum(x > 0 for x in nets)
        print(f" N<={key[0]} m={float(key[1]):.2f}: n={len(r)} rms-SD ${sdm:.2f}; if Pinnacle is exactly right net EV = -fee; if true prob = fair: mean(edge*n - fee)=${mean_net:+.2f}/trade;"
              f" MDE(80% power, 5% one-sided approx 2.8 SE) at n=100: ${2.8*sdm/10:.2f}; n=300: ${2.8*sdm/math.sqrt(300):.2f}; trades needed to detect ${abs(mean_net):.2f}: {n80:,.0f}; trades/night ~{per_night:.1f}; trades with net EV>0 even if Pinnacle exactly right on the margin: {pos}/{len(nets)}")
