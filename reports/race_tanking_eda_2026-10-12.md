# Tanking, race status and teammate absence: descriptive EDA (2026-10-12)

DESCRIPTIVE, no claim, no rule. Seasons 2023 and 2024 (regular season); 2025 never loaded. Forecasts are the stored
walk-forward A0 (production + lower-tail mixture) pts quantiles from the minutes-spread EDA cache. Errors are
actual minus median, minus the all-row baseline (+0.52); CIs are team-season clustered (1000 draws, seed 0).
Scripts: reports/race_tanking_eda_2026-10-12_scripts/ (scratch, stored as .txt so repo lint skips them).

## Tanking = league bottom-5 record after >= 10 games (Devin's definition)
| phase | core DNP tank / other | bench min vs prior-10 tank / other | pts error tank [CI] | n |
|---|---|---|---|---|
| Oct-Nov | 3.3% / 2.2% | +2.0 / +1.5 | -0.92 [-1.53, -0.26] | 920 |
| Dec-Jan | 4.0% / 2.9% | +1.8 / +0.9 | -0.28 [-0.54, -0.07] | 3,054 |
| Feb | 7.4% / 4.4% | +3.4 / +1.0 | +0.56 [+0.12, +1.05] | 1,191 |
| Mar-Apr | 11.4% / 7.3% | +4.8 / +2.0 | -0.06 [-0.71, +0.55] | 2,225 |
Old flag (game 60+, <.400, outside top 10) catches 29% of these rows; both mark 16 team-seasons. The old flag is not
used in production (minutes game-context is off).

## Last 20 games, race status (seed lines 6/7 and 10/11, clinch/elimination ignoring tiebreakers)
| status | core DNP | core min vs prior-10 | bench min vs prior-10 | pts error [CI] | n |
|---|---|---|---|---|---|
| eliminated | 17.8% | -0.5 | +5.8 | -0.15 [-0.67, +0.33] | 1,947 |
| clinched top 6 | 5.9% | -2.2 | +5.2 | +0.25 [+0.06, +0.53] | 1,431 |
| tight (<= 2 GB of a line) | 3.9% | -0.7 | +0.4 | -0.03 [-0.29, +0.21] | 3,831 |
| alive, not tight | 8.3% | -0.3 | +2.2 | +0.19 [+0.01, +0.37] | 5,373 |
| first 62 games | 3.5% | -0.4 | +1.3 | -0.02 [-0.13, +0.08] | 39,000 |
Core DNPs listed OUT on the latest report before ~6 pm ET (proxy, not real tip-60): eliminated 76%, clinched 73%,
tight 55%, first 62 games 58%; questionable 13-30%; not listed 4-8%.

## Teammate absence (star = 30+ min, out >= 3 games; all rotation teammates, prior-10 >= 10 min; 174 star-seasons)
| window | pts error vs baseline [CI] | n |
|---|---|---|
| star out, games 1-3 | +0.47 [+0.30, +0.65] | 6,597 |
| star back, game 1 | -0.81 [-1.06, -0.55] | 2,039 |
| star back, games 1-3 | -0.52 [-0.68, -0.36] | 6,072 |
FULL does not close either window.
