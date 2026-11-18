# Reading log: referee assignments (information program item 6), 2026-11-17

DRAFT intake note (modeler). Declared member of F-INFO as **REF_FOUL_MINUTES**, the one pre-registered follow-up REFEREES is allowed.
**Gate result: FAILS on the effect ceiling, not on data** (section "Gate"). Advice or momentum: *Devin's override* (all eight). Claude's
view: do not freeze; at most one cheap descriptive on the warm-up season.

## Claim (restated)
Crews differ in how many fouls they call. Tonight's crew, announced the morning of the game, changes foul trouble and therefore minutes
(and free throws, so points) for players who foul a lot, and the game total.

## Information, known-at, forecast time, mechanism
Assignments are posted on the league's officials page on the morning of the game (our collector stores the page with its fetch time:
`data/refs/raw/` 566 HTML snapshots on 40 dates from 2026-10-09 to 2026-11-17; `data/refs/refs.duckdb`). A forward row's crew is the
latest snapshot with `fetched_at < tip - 60` (REFEREES forward section). Late crew changes happen; the backtest uses the recorded crew
(a mild optimism REFEREES names). Forecast time: morning and T-60. Mechanism: **minutes** of foul-prone players (foul trouble), a little
**rate** (free throws), **game environment** (total).

## Captured today
`game_officials` 2022 3,999 / 2023 4,016 / 2024 4,031 / 2025 4,031 (holdout, never read) / 2026 616 rows (about 3.0 a game for 205
games); `data/officials/` 5,482 per-game parquet files; history (`data/history`) **0 rows** for 2013-2021.

## What `reports/referees/` in the main checkout is
Tracked: `g0.json`, `missingness.json`, `results.json` (and their sha files) of the frozen REFEREES run of 2026-10-10 (T202, T238-T243).
Untracked: `reports/referees/arms/` (12 files, `A0`-`A3` x stats, about 2.3-2.4 MB each, written 2026-10-10 13:34-13:39), the per-row arm
arrays of that same run. **No new referee work was started**; the untracked folder is a leftover of the October run (the main session or
archivist can commit it or add it to `.gitignore`).

## Outside evidence
Price and Wolfers (QJE 2010; NBER w13206): own-race vs opposite-race crews change personal fouls called, with referee and game fixed
effects; it measures fouls, not forecasts or markets; observational, many seasons (credible for "crews differ", silent on forecast
value). Vendor "crew foul index" pages claim large point swings with no method or out-of-sample test (not credible). A betting publisher
says the market prices most of it (claim, not a test). I found no published out-of-sample test of crew tendencies against closing
lines. Sources: [NBER w13206](https://www.nber.org/papers/w13206.pdf), [BettingPros referee report](https://www.bettingpros.com/nba/referee-report/),
[PillarLab crew foul tendency](https://pillarlab-web.pages.dev/pillars/referee-crew-foul-tendency).

## Prior evidence here
REFEREES (frozen 14613c3a): crew features `crew_pf100`, `crew_fta100`, `crew_pace` appended to production props: pts 2023 -0.0015
[-0.0042, +0.0013], 2024 +0.0020 [+0.0001, +0.0041]; fg3m within 0.0004; game total UNDERPOWERED (T238-T243). Verdict FAIL (null),
closed. CONTEXT_SCREEN_GAMES (T180) did not test crews.

## What is different in the follow-up
REFEREES appended crew rates to every row and scored all rows. The follow-up targets the mechanism directly: an interaction of the
player's own foul rate (as-of PF per 36) with the crew's foul rate, scored on **minutes** of foul-prone players (top about 15% by as-of
PF/36). It is the same information on a narrower population.

## Gate (umbrella section 6)
* n (live, post-12-01): 927 games x about 3.1 foul-prone played rows = about 2,900; h about 0.015 min (per-row sd 0.3 assumed for a
  small-shift arm, design effect 1.5). The data resolve the -0.10 floor.
* e_plan: the REFEREES all-rows pts CI excludes effects beyond about 0.004; if all of it sat on 15% of rows that is at most 0.027 pts
  there, about 0.06 min at about 0.45 pts a minute for bigs (a crude translation, stated as such). **Ceiling about 0.06 min < floor
  0.10 min.** Power at e_plan about 0.
* **Result: NOT FREEZABLE.** The data audit below shows the failure is the information's ceiling: more seasons raise n, not the effect.

## Data audit (non-negotiable 8)
| Data | Seasons held | Rows | Gaps | Repairable? | Cost |
|---|---|---|---|---|---|
| Recorded crews | 2022-2024 (and 2025, holdout), 2026 live | about 4,000 a season | 2013-2021: 0 rows in history | yes: per-game box summary officials, about 11,500 requests | archivist 0.5 day + Devin's download (about 6.5 h at 2 s); nba.com terms read flagged |
| Assigned crews (pre-tip) | 2026 live only (since 2026-10-09) | 566 snapshots, 40 dates | assigned-vs-recorded agreement not yet measured | yes (REFEREES requires >= 95% before any forward claim) | modeler 0.25 day, label-free |
| Player fouls | 2013-2024 box | all | none | n/a | n/a |

## What would change the verdict
A descriptive on **2022 only** (the warm-up season; no rule reports on it), no claim: minutes of foul-prone players by crew-whistle
tercile, and foul-outs and 5-foul games by tercile. If the top-minus-bottom tercile minutes gap for foul-prone players is at least 0.6 min
(a gap whose CRPS value could reach 0.10), draft REF_FOUL_MINUTES with a 2023 select / 2024 report and then a live addendum. Otherwise
the referee question is closed for props. Cost: modeler 0.5 day, maintainer command.

## Playbook check
Class: gradient-boosted residual with one interaction group (crew x player foul rate). (1) Information: yes (morning assignment), but
small. (2) Data: plenty of rows; the limit is effect size. (3) Validity: assigned crew, not recorded, for any live claim; the backtest's
recorded crew is optimistic. (4) Lineage: REFEREES (null), guards carried (A3 leak tripwire, planted future, missingness). (5) Scores
against production minutes CRPS on foul-prone rows; no market comparison (no minutes market).

## Verdict
**SKIP the rule; one optional descriptive (2022 only).** Expected null. Prior: P(freezable after the descriptive) 0.05; P(pass if run) 0.03.

## Two-stage design (coordinator addition, 2026-11-17)
Stage 1 backtest: possible on 2022-24 recorded crews (about 15 min CPU), but REFEREES T238-T243 already was this backtest on all rows
(null) and the follow-up's ceiling is below the floor; not recommended. History holds the recorded crew, not the morning assignment:
mildly flattering (REFEREES' 5% swap stress test). Stage 2 would be a 2025 touch (about 3,900 rows, h about 0.012 min): resolvable, but the
effect ceiling still fails. Time to first answer if Devin insists: about 2 days.
