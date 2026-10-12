import duckdb, numpy as np, polars as pl, sys
from nba_api.stats.static import players
S=sys.argv[1]
names={p['id']:p['full_name'] for p in players.get_players()}
c=duckdb.connect('nba.duckdb',read_only=True)
box=pl.from_arrow(c.execute("""select p.game_id,p.player_id,p.team_id,coalesce(p.minutes,0) as "minutes",p.pts,p.starter,g.game_date,g.season
 from player_game_stats p join games g using(game_id) where g.season in (2023,2024)""").arrow())
z=np.load(f'{S}/cache/props_pts.npz')
fc=pl.DataFrame({'game_id':z['gid'],'player_id':z['pid'].astype(np.int64),'A0':z['A0_q50'],'FULL':z['FULL_q50'],'A0_pit':z['A0_pit'],'FULL_pit':z['FULL_pit']})
box=box.with_columns(pl.col('player_id').cast(pl.Int64)).join(fc,on=['game_id','player_id'],how='left').sort(['player_id','game_date'])
# team schedule
tg=box.select(['game_id','team_id','game_date','season']).unique().sort(['team_id','game_date'])
tg=tg.with_columns(pl.int_range(pl.len()).over(['team_id','season']).alias('tgn'))
box=box.join(tg.select(['game_id','team_id','tgn']),on=['game_id','team_id'])
# stars: season-to-date mean minutes over played games before this one >=30 and >=10 played
played=box.filter(pl.col('minutes')>0)
st=played.with_columns(pl.col('minutes').cum_sum().over(['player_id','season','team_id']).alias('cm'),pl.int_range(1,pl.len()+1).over(['player_id','season','team_id']).alias('k'))
st=st.with_columns(((pl.col('cm')-pl.col('minutes'))/(pl.col('k')-1)).alias('avg_prior'))
# star status per team-season: last known avg
stars=st.filter((pl.col('k')>10)&(pl.col('avg_prior')>=30)).select(['player_id','team_id','season']).unique()
# star presence per team game
pres=played.select(['player_id','team_id','tgn','season']).with_columns(pl.lit(1).alias('p'))
out=[]
for (pid,tid,sea) in stars.iter_rows():
    games=tg.filter((pl.col('team_id')==tid)&(pl.col('season')==sea)).sort('tgn')
    me=set(pres.filter((pl.col('player_id')==pid)&(pl.col('team_id')==tid)&(pl.col('season')==sea))['tgn'].to_list())
    if not me: continue
    lo,hi=min(me),max(me)
    seq=[(g in me) for g in range(lo,hi+1)]
    i=0
    while i<len(seq):
        if not seq[i]:
            j=i
            while j<len(seq) and not seq[j]: j+=1
            if j-i>=3 and j<len(seq): out.append((pid,tid,sea,lo+i,lo+j,j-i))  # absent tgn [lo+i, lo+j), returns at lo+j
            i=j
        else: i+=1
spells=pl.DataFrame(out,schema=['star','team_id','season','a0','ret','len'],orient='row')
print('star absence spells >=3 games:',spells.height)
rows=[]
p2=played.select(['player_id','team_id','season','tgn','minutes','pts','A0','FULL','A0_pit','FULL_pit','game_date'])
for star,tid,sea,a0,ret,ln in spells.iter_rows():
    t=p2.filter((pl.col('team_id')==tid)&(pl.col('season')==sea)&(pl.col('player_id')!=star))
    before=t.filter((pl.col('tgn')>=a0-10)&(pl.col('tgn')<a0)).group_by('player_id').agg(pl.col('minutes').mean().alias('mb'),pl.len().alias('nb'))
    during=t.filter((pl.col('tgn')>=a0)&(pl.col('tgn')<ret)).group_by('player_id').agg(pl.col('minutes').mean().alias('md'),pl.len().alias('nd'))
    b=before.join(during,on='player_id').filter((pl.col('nb')>=5)&(pl.col('nd')>=3)).with_columns((pl.col('md')-pl.col('mb')).alias('bump'))
    ben=b.filter(pl.col('bump')>=6)
    after=t.filter((pl.col('tgn')>=ret)&(pl.col('tgn')<ret+3))
    for r in ben.iter_rows(named=True):
        a=after.filter(pl.col('player_id')==r['player_id'])
        for x in a.iter_rows(named=True):
            rows.append({**x,'star':star,'bump':r['bump'],'mb':r['mb'],'md':r['md'],'k':x['tgn']-ret,'len':ln})
R=pl.DataFrame(rows).drop_nulls(['A0'])
print('beneficiary rows on star-return games 1-3:',R.height,' distinct spells:',R.select(['star','tgn']).n_unique())
allp=played.drop_nulls(['A0'])
def summ(d,lab):
    e0=(d['pts']-d['A0']); e1=(d['pts']-d['FULL'])
    print(f"{lab:38s} n={d.height:6d}  mean(actual-median) A0 {e0.mean():+.2f}  FULL {e1.mean():+.2f}  share under median A0 {(d['A0_pit']<0.5).mean():.3f} FULL {(d['FULL_pit']<0.5).mean():.3f}  minutes vs pre-spell {(d['minutes']-d['mb']).mean() if 'mb' in d.columns else float('nan'):+.1f}")
summ(allp,'all played rows (baseline)')
summ(R,'beneficiaries, star back (games 1-3)')
summ(R.filter(pl.col('k')==0),'  star back, game 1')
summ(R.filter(pl.col('bump')>=10),'  bump >= 10 min')
# bootstrap by spell
keys=R.select(['star','team_id','season']).unique()
rng=np.random.default_rng(0); Rk=R.with_columns((pl.col('pts')-pl.col('A0')).alias('e'),(pl.col('pts')-pl.col('FULL')).alias('f'))
grp=Rk.group_by(['star','team_id','season','tgn']).agg(pl.col('e').sum(),pl.col('f').sum(),pl.len().alias('n'))
E=grp['e'].to_numpy();F=grp['f'].to_numpy();N=grp['n'].to_numpy()
bs=[];bf=[]
for _ in range(2000):
    i=rng.integers(0,len(E),len(E)); bs.append(E[i].sum()/N[i].sum()); bf.append(F[i].sum()/N[i].sum())
print('A0 mean err CI', np.percentile(bs,[2.5,97.5]).round(2),' FULL', np.percentile(bf,[2.5,97.5]).round(2), ' clusters',len(E))
R.write_parquet(f'{S}/star_return_rows.parquet')
# example: biggest bump
ex=R.sort('bump',descending=True).unique(['star','team_id','season'],keep='first').sort('bump',descending=True).head(6)
for r in ex.iter_rows(named=True):
    print(names.get(r['player_id'],r['player_id']),'| star:',names.get(r['star'],r['star']),'| season',r['season'],'| pre',round(r['mb'],1),'during',round(r['md'],1),'| spell',r['len'])
