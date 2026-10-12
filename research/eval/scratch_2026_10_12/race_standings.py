import duckdb, numpy as np, polars as pl, sys
from nba.features.game_context import TEAM_CONFERENCE
S=sys.argv[1]
c=duckdb.connect('nba.duckdb',read_only=True)
g=pl.from_arrow(c.execute("select game_id,game_date,season,home_team,away_team,home_pts,away_pts from games where season in (2023,2024) and game_id like '002%' order by game_date").arrow())
g=g.with_columns(pl.col('game_date').cast(pl.Date))
# team-game long
L=pl.concat([g.select('game_id','game_date','season',pl.col('home_team').alias('team_id'),pl.col('away_team').alias('opp_id'),(pl.col('home_pts')>pl.col('away_pts')).alias('win')),
             g.select('game_id','game_date','season',pl.col('away_team').alias('team_id'),pl.col('home_team').alias('opp_id'),(pl.col('away_pts')>pl.col('home_pts')).alias('win'))])
L=L.with_columns(pl.col('team_id').replace_strict(TEAM_CONFERENCE,default=None).alias('conf'))
rows=[]
for sea in (2023,2024):
    Ls=L.filter(pl.col('season')==sea).sort('game_date')
    teams=Ls['team_id'].unique().to_list()
    for d in Ls['game_date'].unique().sort().to_list():
        prior=Ls.filter(pl.col('game_date')<d).group_by('team_id').agg(pl.col('win').sum().alias('W'),pl.len().alias('GP'))
        st=pl.DataFrame({'team_id':teams}).join(prior,on='team_id',how='left').fill_null(0)
        st=st.with_columns(pl.col('team_id').replace_strict(TEAM_CONFERENCE,default=None).alias('conf'),(pl.col('GP')-pl.col('W')).alias('Lo'),(82-pl.col('GP')).alias('rem'),
                           (pl.col('W')/pl.col('GP').clip(1)).alias('wp'))
        st=st.with_columns(pl.col('wp').rank('ordinal',descending=True).over('conf').alias('rk'),pl.col('wp').rank('average',descending=False).alias('lg_rank_low'))
        for conf in ('E','W'):
            cst=st.filter(pl.col('conf')==conf).sort('rk')
            W=cst['W'].to_numpy();Lo=cst['Lo'].to_numpy();rem=cst['rem'].to_numpy();mx=W+rem
            for i,r in enumerate(cst.iter_rows(named=True)):
                k=i+1
                line6=((W[5]+W[6])/2,(Lo[5]+Lo[6])/2); line10=((W[9]+W[10])/2,(Lo[9]+Lo[10])/2)
                gb=lambda ln:abs(((ln[0]-r['W'])+(r['Lo']-ln[1]))/2)
                others7=[mx[j] for j in range(6,len(W)) if j!=i]; others11=[mx[j] for j in range(10,len(W)) if j!=i]
                cl6 = k<=6 and r['W']>max(others7)
                cl10= k<=10 and r['W']>max(others11)
                elim10 = r['W']+r['rem'] < W[9] if k>10 else False
                elim6 = r['W']+r['rem'] < W[5] if k>6 else False
                rows.append(dict(season=sea,game_date=d,team_id=r['team_id'],GP=r['GP'],rem=r['rem'],wp=r['wp'],rk=k,lg_low=r['lg_rank_low'],
                                 d6=gb(line6),d10=gb(line10),cl6=cl6,cl10=cl10,elim10=elim10,elim6=elim6))
ST=pl.DataFrame(rows)
ST.write_parquet(f'{S}/standings_asof.parquet'); print(ST.shape)
