"""Label-free, movement-free gate for LINE_MOVE_TOWARD_MODEL_DNP_FAIR (T510b) on the live capture 2026-10-22..23.

Counts only: no outcome, no mid_close - mid1 movement statistic is computed (T510 forbids reading movement before its
freeze, and T510b inherits that). Mirrors the T510 selection text. Read-only on nba.duckdb.

Run from the main checkout venv:
  /Users/devin/Downloads/nba-prediction/.venv/bin/python -I research/scratch/gate_line_move_dnp_fair_2026_10_23.py
"""

import glob
import gzip
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import yaml

MAIN = Path("/Users/devin/Downloads/nba-prediction")
sys.path.insert(0, str(MAIN))
from nba.explain.names import player_name  # noqa: E402

NY = ZoneInfo("America/New_York")
DATES = ["2026-10-21", "2026-10-22", "2026-10-23"]  # 10-21 = gate night (counts only, never scored); 10-23 has no pre-tip capture
STAT = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def ts(p: str) -> datetime:
    return datetime.strptime(Path(p).name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def good(q) -> bool:
    return q is not None and q[1] is not None and q[2] is not None and 0 < q[1] < q[2] < 1 and q[2] - q[1] <= 0.10


con = duckdb.connect(str(MAIN / "nba.duckdb"), read_only=True)
rows = con.execute(
    """select f.run_id, f.game_id, f.tipoff, f.target, f.player_id, f.prediction, f.made_at, f.model_name
       from forward_predictions f
       where f.model_name in ('props_context_residual','props_recency_v1') and f.player_id is not null
         and f.target in ('pts','reb','ast','fg3m')"""
).fetchall()
Bmap = {}
Mcand = defaultdict(list)
for run_id, gid, tip, tgt, pid, pred, made, mn in rows:
    d = json.loads(pred) if isinstance(pred, str) else pred
    tip = tip.replace(tzinfo=timezone.utc)
    made = made.replace(tzinfo=timezone.utc)
    if mn == "props_recency_v1":
        Bmap[(run_id, gid, pid, tgt)] = d
    elif made <= tip - timedelta(minutes=60):
        Mcand[(gid, pid, tgt)].append((made, run_id, tip, d))
M = {}
for k, v in Mcand.items():
    made, run_id, tip, d = max(v, key=lambda x: x[0])
    et = tip.astimezone(NY).date().isoformat()
    M[(k[1], k[2], et)] = (d, made, run_id, tip, k[0])
print("M forecasts (latest per game-player-target, made_at <= tip-60):", len(M))

aliases = {norm(k): v for k, v in yaml.safe_load(open(MAIN / "configs/kalshi_aliases.yaml"))["aliases"].items()}
pids = {pid for (pid, _, _) in M}
by_name = {norm(player_name(p, {})): p for p in pids}


def pk(name: str):
    return aliases.get(norm(name), by_name.get(norm(name)))


def p_ge(d, k: int) -> float | None:
    f = d.get("p_ge_full")
    if not f:
        return None
    c, n = f["c"], f["n"]
    return (c[k - 1] / n) if k - 1 < len(c) else 0.0


snaps = defaultdict(list)
for s in STAT:
    for dd in DATES:
        for f in sorted(glob.glob(str(MAIN / f"data/raw/live_{dd}/kalshi/{s}_p0/*.json.gz"))):
            t = ts(f)
            for m in json.load(gzip.open(f)).get("markets", []):
                b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
                snaps[m["ticker"]].append(
                    (t, float(b) if b not in (None, "") else None, float(a) if a not in (None, "") else None, m.get("title", ""), s)
                )

drops = Counter()
by_psg = defaultdict(list)  # (pid, stat, et_date) -> candidate rungs passing S0
for tk, ss in snaps.items():
    ss.sort()
    m = re.match(r"(.+?): (\d+)\+", ss[0][3])
    if not m:
        drops["title_unparsed"] += 1
        continue
    pid = pk(m.group(1))
    stat = STAT[ss[0][4]]
    date_code = tk.split("-")[1][:7]  # 26OCT22
    et = datetime.strptime(date_code, "%y%b%d").date().isoformat()
    if pid is None or (pid, stat, et) not in M:
        drops["no_player_or_forecast_match"] += 1
        continue
    d, made, run_id, tip, gid = M[(pid, stat, et)]
    if et not in DATES:
        continue
    k = int(m.group(2))
    s0c = [x for x in ss if x[0] >= max(made, tip - timedelta(minutes=150)) and x[0] < tip]
    if not s0c:
        drops["no_S0_snapshot"] += 1
        continue
    q0 = s0c[0]
    if not good(q0):
        drops["S0_quote_fails_filter"] += 1
        continue
    by_psg[(pid, stat, et)].append((k, tk, ss, q0))

per_date = defaultdict(Counter)
ex = []
for (pid, stat, et), cands in by_psg.items():
    k, tk, ss, q0 = min(cands, key=lambda c: (abs((c[3][1] + c[3][2]) / 2 - 0.5), c[0]))
    d, made, run_id, tip, gid = M[(pid, stat, et)]
    c = per_date[et]
    c["psg_with_S0_rung"] += 1
    s1c = [x for x in ss if x[0] >= q0[0] + timedelta(minutes=15) and x[0] < tip]
    cl = [x for x in ss if x[0] < tip]
    if not s1c or not cl:
        c["drop_no_S1_or_close"] += 1
        continue
    q1, qc = s1c[0], cl[-1]
    if not (good(q1) and good(qc)) or (qc[0] - q1[0]) < timedelta(minutes=30):
        c["drop_S1_close_filter"] += 1
        continue
    mid0 = (q0[1] + q0[2]) / 2
    pc = p_ge(d, k)
    B = Bmap.get((run_id, gid, pid, stat))
    if pc is None:
        c["drop_null_p_ge_full"] += 1
        continue
    pplay = d.get("p_play", 1.0)
    pM = pc * pplay  # T510: DNP counts as No
    pMf = pc * pplay + (1 - pplay) * mid0  # T510b: DNP settles at the fair price, proxied by the S0 mid
    if B is None or p_ge(B, k) is None:
        c["drop_no_B"] += 1
        pB = pBf = None
    else:
        pB = p_ge(B, k) * B.get("p_play", 1.0)
        pBf = p_ge(B, k) * B.get("p_play", 1.0) + (1 - B.get("p_play", 1.0)) * mid0
    c["scored_markets"] += 1
    mn = con.execute("select minutes from player_game_stats where game_id=? and player_id=?", [gid, pid]).fetchall()
    dnp = (not mn) or mn[0][0] is None or mn[0][0] == 0
    c["player_dnp_box_availability_only"] += dnp  # availability count for the premise check; no stat outcome, no movement
    q510 = abs(pM - mid0) >= 0.05
    q510b = abs(pMf - mid0) >= 0.05
    c["p1_T510"] += q510
    c["p1_T510b"] += q510b
    c["p1_both"] += q510 and q510b
    c["p1_only_T510"] += q510 and not q510b
    c["p1_only_T510b"] += q510b and not q510
    c["p1_sign_flip"] += (q510 and q510b) and ((pM - mid0) * (pMf - mid0) < 0)
    if pB is not None:
        p2_510 = q510 or abs(pB - mid0) >= 0.05
        p2_510b = q510b or abs(pBf - mid0) >= 0.05
        c["p2_T510"] += p2_510
        c["p2_T510b"] += p2_510b
    if pplay < 0.9:
        c["pplay_lt_0.9_scored"] += 1
        c["pplay_lt_0.9_p1_T510"] += q510
        c["pplay_lt_0.9_p1_T510b"] += q510b
    c["stat_" + stat + "_p1_T510b"] += q510b
    if len(ex) < 8 and q510 != q510b:
        ex.append((et, stat, k, round(pc, 3), round(pplay, 3), round(mid0, 3), round(pM, 3), round(pMf, 3)))

print("drops before per-date:", dict(drops))
tot = Counter()
for et in DATES:
    print(et, dict(per_date[et]))
    tot.update(per_date[et])
print("TOTAL", dict(tot))
print("examples where qualification differs (date, stat, k, p_cond, p_play, mid0, p_M, p_M_fair):")
for e in ex:
    print("  ", e)
