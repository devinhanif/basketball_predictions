"""Label-free gate for LINE_MOVE_TOWARD_MODEL and FAV_DIP on the 10-21 capture: counts only, no outcomes."""
import gzip, json, re, sys, glob, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import duckdb
sys.path.insert(0, "/Users/devin/Downloads/nba-prediction")
from nba.explain.names import player_name
R = Path("/Users/devin/Downloads/nba-prediction/data/raw/live_2026-10-21")
def ts(p): return datetime.strptime(Path(p).name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
c = duckdb.connect("/Users/devin/Downloads/nba-prediction/nba.duckdb", read_only=True)
tips = {r[0]: r[1].replace(tzinfo=timezone.utc) for r in c.execute("select distinct game_id, tipoff from forward_predictions where tipoff between '2026-10-21 20:00' and '2026-10-22 06:00'").fetchall()}
print("games with forecasts:", len(tips), "tips:", sorted({t.strftime('%H:%M') for t in tips.values()}))
# model probs for props (latest run before tip-60), keyed by normalized name
def norm(s): return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())
rows = c.execute("""select game_id, player_id, target, prediction, made_at from (select *, row_number() over (partition by game_id, player_id, target order by made_at desc) rn
  from forward_predictions where model_name='props_context_residual' and game_id in (select unnest(?)) and made_at <= tipoff - interval 60 minute and player_id is not null) where rn=1""", [list(tips)]).fetchall()
M = {}
for g, pid, t, pred, made in rows:
    d = json.loads(pred) if isinstance(pred, str) else pred
    M[(norm(player_name(pid, {})), t)] = (d, made.replace(tzinfo=timezone.utc), tips[g])
print("prop forecasts:", len(M), "latest made_at:", max(v[1] for v in M.values()))
stat = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"}
snaps = defaultdict(list)  # ticker -> [(t, bid, ask, title)]
for s in stat:
    for f in sorted(glob.glob(str(R / "kalshi" / f"{s}_p0" / "*.json.gz"))):
        t = ts(f); body = json.load(gzip.open(f))
        for m in body.get("markets", []):
            b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
            snaps[m["ticker"]].append((t, float(b) if b not in (None, "") else None, float(a) if a not in (None, "") else None, m.get("title", ""), s))
nfiles = {s: len(glob.glob(str(R / "kalshi" / f"{s}_p0" / "*.json.gz"))) for s in stat}
print("snapshot files per series:", nfiles)
first_snap = min(x[0] for v in snaps.values() for x in v); print("first prop snapshot:", first_snap)
ok = gap5 = matched = 0; per_game = defaultdict(int)
for tk, ss in snaps.items():
    m = re.match(r"(.+?): (\d+)\+", ss[0][3]); 
    if not m: continue
    key = (norm(m.group(1)), stat[ss[0][4]])
    if key not in M: continue
    d, made, tip = M[key]; matched += 1
    pre = [x for x in ss if x[0] < tip]
    ref = [x for x in pre if x[0] >= made and x[0] >= tip - timedelta(minutes=150)]
    if not ref or not pre: continue
    r0, r1 = ref[0], pre[-1]
    good = lambda x: x[1] is not None and x[2] is not None and 0 < x[1] < x[2] < 1 and x[2] - x[1] <= 0.10
    if not (good(r0) and good(r1)) or (r1[0] - r0[0]) < timedelta(minutes=30): continue
    ok += 1
    k = int(m.group(2)); cnt, n = d["p_ge_full"]["c"], d["p_ge_full"]["n"]
    p = (cnt[k-1]/n if k-1 < len(cnt) else 0) * d.get("p_play", 1)
    if abs(p - (r0[1]+r0[2])/2) >= 0.05: gap5 += 1; per_game[tip] += 1
print(f"prop markets matched to a forecast {matched}; with two-sided quotes (spread<=0.10) at ref and close >=30 min apart: {ok}; of those |model-mid|>=0.05: {gap5}")
# FAV_DIP gate: game markets in-game snapshots + ESPN period availability
gf = sorted(glob.glob(str(R / "kalshi" / "KXNBAGAME_p0" / "*.json.gz")))
print("KXNBAGAME snapshots:", len(gf), "from", ts(gf[0]), "to", ts(gf[-1]), "median gap min:", sorted((ts(b)-ts(a)).total_seconds()/60 for a,b in zip(gf,gf[1:]))[len(gf)//2])
sb = sorted(glob.glob(str(R / "espn_scoreboard" / "*" / "*.json.gz")) + glob.glob(str(R / "espn_scoreboard" / "*.json.gz")))
print("ESPN scoreboard snapshots:", len(sb), ("from " + str(ts(sb[0])) + " to " + str(ts(sb[-1]))) if sb else "")
