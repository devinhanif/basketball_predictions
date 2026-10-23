"""Label-free gate for the HISTORICAL STAGE (SOFTBOOK_VS_PINNACLE_HIST). Prices only: no outcome, no return,
no player_game_stats. odds_history opened read_only; season <= 2024 in SQL (season 2025 never loaded).
Usage: python softbook_pinnacle_hist_gate_2026_10_22.py [REPO_ROOT]"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/devin/Downloads/nba-prediction")
c = duckdb.connect(str(ROOT / "data/odds/odds_history.duckdb"), read_only=True)
MK = "('player_points','player_rebounds','player_assists','player_threes')"
MS = (0.01, 0.02, 0.03, 0.04, 0.05)
c.execute(f"""create temp table q as
  select season, game_id, snapshot_kind sk, book, market, player_id, point,
         max(case when side='over' then price_american end) po, max(case when side='under' then price_american end) pu
  from odds_history where season <= 2024 and market in {MK} and snapshot_kind in ('t60','t5')
    and player_id is not null and point is not null and price_american is not null and side in ('over','under')
  group by all having po is not null and pu is not null""")
c.execute("""create temp table p as select *, (case when po<0 then -po/(-po+100.0) else 100.0/(po+100) end) ro,
  (case when pu<0 then -pu/(-pu+100.0) else 100.0/(pu+100) end) ru from q""")
c.execute("""create temp table pf as select season,game_id,sk,market,player_id,point, ro/(ro+ru) fo, ro+ru-1 vig from p where book='pinnacle'""")
print("Pinnacle two-sided lines, by season/snapshot (whole-number lines shown separately)")
print(c.execute("select season,sk,count(*), sum((point=floor(point))::int) from pf group by 1,2 order by 1,2").fetchall())
print("pinnacle median overround:", c.execute("select median(vig) from pf where season<=2024").fetchone())
print("\nsame-snapshot, same exact half-line matches per retail book (season, snapshot, book -> lines; and by-date n games)")
BOOKS = "('draftkings','fanduel','betmgm','williamhill_us','betrivers','bovada','betonlineag','mybookieag','unibet_us','pointsbetus','fanatics')"
rows = c.execute(f"""select p.season, p.sk, p.book, count(*) n, count(distinct p.game_id) g
  from p join pf using(season,game_id,sk,market,player_id,point)
  where p.book in {BOOKS} and p.point<>floor(p.point) group by all order by 1,2,3""").fetchall()
for r in rows: print(" ", r)
print("\nqualifying BET counts at T-60 (a side is a bet if book raw implied prob < pinnacle fair - m; over and under)")
for m in MS:
    out = c.execute(f"""select p.season, count(*) lines,
         sum((p.ro < pf.fo - {m})::int) overs, sum((p.ru < (1-pf.fo) - {m})::int) unders
      from p join pf using(season,game_id,sk,market,player_id,point)
      where p.sk='t60' and p.book in {BOOKS} and p.point<>floor(p.point) group by 1 order by 1""").fetchall()
    print(f" m={m:.2f}: (season, matched lines, overs, unders) {out}")
print("\nper-book qualifying bets at m=0.02 / 0.03 / 0.05, T-60 (season, book, n_bets)")
for m in (0.02, 0.03, 0.05):
    print(f" m={m}:", c.execute(f"""select p.season, p.book, sum((p.ro<pf.fo-{m})::int + (p.ru<(1-pf.fo)-{m})::int) b
      from p join pf using(season,game_id,sk,market,player_id,point) where p.sk='t60' and p.book in {BOOKS} and p.point<>floor(p.point)
      group by 1,2 having b>0 order by 1,2""").fetchall())
print("\nbets by distinct game dates need game_date (not in odds_history); game count used instead:")
print(c.execute(f"""select p.season, count(distinct p.game_id) from p join pf using(season,game_id,sk,market,player_id,point)
  where p.sk='t60' and p.book in {BOOKS} and p.point<>floor(p.point) and (p.ro<pf.fo-0.03 or p.ru<(1-pf.fo)-0.03) group by 1 order by 1""").fetchall())
print("\nplacebo P1 (fair = FanDuel de-vig, bet at DraftKings, and the reverse): qualifying bets at m=0.03, T-60")
print(c.execute(f"""with ff as (select season,game_id,sk,market,player_id,point, ro/(ro+ru) fo from p where book='fanduel'),
   dd as (select season,game_id,sk,market,player_id,point, ro/(ro+ru) fo from p where book='draftkings')
   select 'dk_at_fd_fair', p.season, count(*), sum((p.ro<ff.fo-0.03)::int + (p.ru<(1-ff.fo)-0.03)::int)
   from p join ff using(season,game_id,sk,market,player_id,point) where p.book='draftkings' and p.sk='t60' and p.point<>floor(p.point) group by 2
   union all
   select 'fd_at_dk_fair', p.season, count(*), sum((p.ro<dd.fo-0.03)::int + (p.ru<(1-dd.fo)-0.03)::int)
   from p join dd using(season,game_id,sk,market,player_id,point) where p.book='fanduel' and p.sk='t60' and p.point<>floor(p.point) group by 2 order by 1,2""").fetchall())
print("\nsecond snapshot (T-5) present for the same Pinnacle line as T-60, by season (for a LINE_MOVE analog):")
print(c.execute("select a.season, count(*), sum((a.point<>b.point)::int) from pf a join pf b on a.season=b.season and a.game_id=b.game_id and a.market=b.market and a.player_id=b.player_id and a.sk='t60' and b.sk='t5' group by 1 order by 1").fetchall())
