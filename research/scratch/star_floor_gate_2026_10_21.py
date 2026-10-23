"""Label-free gate for STAR_FLOOR_PAPER on the 2026-10-21 capture: counts eligible lines only."""
import glob, gzip, json, re, sys, unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import duckdb
sys.path.insert(0, "/Users/devin/Downloads/nba-prediction")
from nba.explain.names import player_name
from nba.explain.slate import read_rosters
from nba.props.rosters import DEFAULT_ROSTER_DIR
c = duckdb.connect("/Users/devin/Downloads/nba-prediction/nba.duckdb", read_only=True)
def nz(s): return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())
COL = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
SER = {"KXNBAPTS": 1, "KXNBAREB": 1, "KXNBAAST": 1, "KXNBA3PT": 1}
def ts(p): return datetime.strptime(Path(p).name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
def run(day: date, root: Path):
    games = c.execute("select game_id, tipoff from forward_predictions where cast(tipoff - interval 5 hour as date)=? group by 1,2", [day]).fetchall()
    tips = {g: t.replace(tzinfo=timezone.utc) for g, t in games}
    gteams = {g: (h, a) for g, h, a in c.execute("select game_id, home_team, away_team from games where game_id in (select unnest(?))", [list(tips)]).fetchall()}
    rr = read_rosters(day, {t for v in gteams.values() for t in v}, Path(DEFAULT_ROSTER_DIR))
    pids = {r[0] for r in c.execute("select distinct player_id from forward_predictions where game_id in (select unnest(?)) and player_id is not null", [list(tips)]).fetchall()}
    name = {nz(player_name(p, rr.names)): p for p in pids}
    last_team = dict(c.execute("""select player_id, arg_max(team_id, g.game_date) from player_game_stats p join games g using(game_id)
        where g.season = 2025 and g.game_date < date '2026-10-01' and p.player_id in (select unnest(?)) group by 1""", [list(pids)]).fetchall())
    files = {s: sorted(glob.glob(str(root / "kalshi" / f"{s}_p0" / "*.json.gz"))) for s in SER}
    n_lines = n_elig = 0; per = []
    for s, fs in files.items():
        if not fs: continue
        snaps = [(ts(f), f) for f in fs]
        for gid, tip in tips.items():
            pre = [f for t, f in snaps if tip - timedelta(minutes=10) <= t < tip]
            if not pre: continue
            body = json.load(gzip.open(pre[-1]))
            for m in body.get("markets", []):
                mm = re.match(r"(.+?): (\d+)\+ (points|rebounds|assists|threes)$", m.get("title") or "")
                if not mm: continue
                pid = name.get(nz(mm.group(1)))
                if pid is None: continue
                memb = rr.membership.get(pid)
                if not memb or not (memb & set(gteams[gid])): continue
                b, a = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
                if b in (None, "") or a in (None, ""): continue
                b, a = float(b), float(a)
                n_lines += 1
                if not (0 < b < a < 1) or a - b > 0.05: continue
                if last_team.get(pid) not in memb: continue
                k = int(mm.group(2)); st = COL[mm.group(3)]
                n, h = c.execute(f"select count(*), sum(({st}>=?)::int) from player_game_stats p join games g using(game_id) where p.player_id=? and g.season between 2022 and 2024 and p.minutes>0", [k, pid]).fetchone()
                if not n or n < 60: continue
                rate = h / n
                per.append((rate, a))
    return len(tips), n_lines, n_elig, per
if __name__ == "__main__":
    g, nl, ne, per = run(date(2026, 10, 21), Path("/Users/devin/Downloads/nba-prediction/data/raw/live_2026-10-21"))
    print(f"same-team, >=60 games, spread<=0.05 lines: {len(per)} of {nl}")
    for hr in (0.95, 0.92, 0.90, 0.85):
        for gap in (0.08, 0.05, 0.03):
            print(f"  hit>={hr:.2f} & ask<=rate-{gap:.2f}: {sum(1 for r,a in per if r>=hr and a<=r-gap)}   (hit>={hr:.2f} any price: {sum(1 for r,a in per if r>=hr)})")
