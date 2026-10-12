import numpy as np, polars as pl, sys
S=sys.argv[1]
ST=pl.read_parquet(f'{S}/standings_asof.parquet')
b=pl.read_parquet(f'{S}/box_fc.parquet').filter(pl.col('game_id').str.starts_with('002')).with_columns(pl.col('game_date').cast(pl.Date))
b=b.with_columns(pl.when(pl.col('team_id')==pl.col('home_team')).then(pl.col('away_team')).otherwise(pl.col('home_team')).alias('opp_id'))
print('rows',b.height,'minutes==0 share',round((b['minutes']==0).mean(),3))
b=b.sort(['player_id','game_date'])
pm=pl.when(pl.col('minutes')>0).then(pl.col('minutes'))
b=b.with_columns(pm.alias('mp'))
b=b.with_columns(pl.col('mp').shift(1).rolling_mean(10,min_samples=5).over(['player_id','season']).alias('p10_raw'))
b=b.with_columns(pl.col('p10_raw').forward_fill().over(['player_id','season']).alias('p10'))  # as-of mean of prior played minutes (window over all prior rows incl DNP nulls)
b=b.join(ST.drop('season').with_columns([(pl.col(x).cast(pl.Float64)>0) for x in ['cl6','cl10','elim10','elim6']]),on=['team_id','game_date'],how='left')
b=b.join(ST.select('team_id','game_date',pl.col('lg_low').alias('opp_lg_low'),pl.col('GP').alias('opp_GP')),left_on=['opp_id','game_date'],right_on=['team_id','game_date'],how='left')
b=b.with_columns(pl.col('game_date').dt.month().alias('mo'))
b=b.with_columns(pl.when(pl.col('mo').is_in([10,11])).then(pl.lit('1 Oct-Nov')).when(pl.col('mo').is_in([12,1])).then(pl.lit('2 Dec-Jan')).when(pl.col('mo')==2).then(pl.lit('3 Feb')).otherwise(pl.lit('4 Mar-Apr')).alias('phase'))
b=b.with_columns(((pl.col('lg_low')<=5)&(pl.col('GP')>=10)).alias('tank'),
                 ((pl.col('GP')>=60)&(pl.col('wp')<0.40)&(pl.col('rk')>10)).alias('old_flag'),
                 ((pl.col('opp_lg_low')<=5)&(pl.col('opp_GP')>=10)).alias('opp_tank'))
b=b.with_columns(pl.when(pl.col('rem')>20).then(pl.lit('-')).when(pl.col('elim10')).then(pl.lit('eliminated')).when(pl.col('cl6')).then(pl.lit('clinched top6'))
                 .when(pl.min_horizontal('d6','d10')<=2).then(pl.lit('tight (<=2 GB of a line)')).otherwise(pl.lit('alive, not tight')).alias('late'))
core=b.filter(pl.col('p10')>=28); bench=b.filter((pl.col('p10')<15)&(pl.col('p10')>0))
fc=b.filter(pl.col('minutes')>0).drop_nulls('A0')
base_err=(fc['pts']-fc['A0']).mean()
def ci(d,col):
    g=d.group_by(['team_id','season']).agg(pl.col(col).sum().alias('s'),pl.len().alias('n'))
    E=g['s'].to_numpy();N=g['n'].to_numpy();rng=np.random.default_rng(0)
    bs=[(lambda i:E[i].sum()/N[i].sum())(rng.integers(0,len(E),len(E))) for _ in range(1000)]
    return E.sum()/N.sum(),np.percentile(bs,2.5),np.percentile(bs,97.5)
def row(lab,mask):
    c=core.filter(mask); cp=c.filter(pl.col('minutes')>0); bp=bench.filter(mask).filter(pl.col('minutes')>0); f=fc.filter(mask)
    if f.height<50: return
    f=f.with_columns((pl.col('pts')-pl.col('A0')-base_err).alias('e'))
    m,lo,hi=ci(f,'e')
    print(f"{lab:40s} core n {c.height:5d} DNP {(c['minutes']==0).mean():.3f} min-p10 {(cp['minutes']-cp['p10']).mean():+5.1f} | bench min-p10 {(bp['minutes']-bp['p10']).mean():+5.1f} | pts err {m:+.2f} [{lo:+.2f},{hi:+.2f}] n {f.height:5d} under q10 {(f['A0_pit']<.1).mean():.3f} over q90 {(f['A0_pit']>.9).mean():.3f}")
print(f"baseline pts err {base_err:+.2f}; all-row under q10 {(fc['A0_pit']<.1).mean():.3f} over q90 {(fc['A0_pit']>.9).mean():.3f}")
print("\n== Tanking = league bottom-5 record, >=10 games (Devin's definition), by phase")
for ph in ['1 Oct-Nov','2 Dec-Jan','3 Feb','4 Mar-Apr']:
    row(f'{ph} tank',(pl.col('phase')==ph)&pl.col('tank'))
    row(f'{ph} not tank',(pl.col('phase')==ph)&~pl.col('tank'))
print("\n== Old flag (game 60+, <.400, outside top 10) vs new")
row('old flag on',pl.col('old_flag')); row('new tank on, old flag off',pl.col('tank')&~pl.col('old_flag'))
tk=b.filter(pl.col('tank')).select('team_id','season').unique().height; ok=b.filter(pl.col('old_flag')).select('team_id','season').unique().height
print('team-seasons ever flagged: new',tk,' old',ok, '| tank rows caught by old flag', round(b.filter(pl.col('tank'))['old_flag'].mean(),3))
print("\n== Opponent tanking (stars vs bad teams: blowout rest?)")
row('opponent tanking',pl.col('opp_tank')&~pl.col('tank')); row('opponent not tanking',~pl.col('opp_tank')&~pl.col('tank'))
print("\n== Last 20 games of the season, by race status")
for s in ['eliminated','clinched top6','tight (<=2 GB of a line)','alive, not tight']:
    row(s,pl.col('late')==s)
row('first 62 games (reference)',pl.col('late')=='-')
