# Advanced rating systems audit (reading-log intake), 2026-10-27

Status: DRAFT reading-log note. No fit, no download, no holdout touch. Sources are public methodology pages
read with WebFetch/WebSearch on 2026-10-27; anything marked "unverified" was not confirmed on a primary page.
Work type: *Devin's request* (2026-10-27), research only.

## Question

Does any public NBA advanced rating or projection system give us pre-tip information that we cannot rebuild
from data we already hold, **and** publish the value as it stood before each game date (as-of history), so a
2023/2024 backtest does not leak end-of-season information?

What we hold (docs/PROJECT_STATUS.md, nba/ingest): box scores, play-by-play, shots, stints/lineups and
possessions, NBA.com tracking and hustle (2022-24 coverage 3,953/3,953 games), play types, officials, matchups,
official injury reports with publish timestamps (PDFs from 2018-12-20), T-30 confirmed lineups (forward only),
salaries (bref pull 2026-10-21), coaches, rest/travel, the-odds-api archive of props and game lines 2023-25 at
T-60/T-5, Kalshi snapshots. Production seasons 2022+ in `nba.duckdb`; 2019-21 in `data/history/`.

## Bottom line

**None of these systems clears both bars cleanly for a fair test.** Most of them are functions of box
score, play-by-play on/off (RAPM) and tracking, all of which we hold and have already tested as as-of
inputs to the props model (ADVANCED_RATES T424-T439, TRACKING_SCREEN T119-T123/T174-T175, FOUR_FACTORS
T264-T279, RAPM T176, WOWY_STRUCT T499). The pieces that really are new are: (a) long career history from before
2019 (DARKO from 1996-97, EPM from 2001-02); (b) proprietary weights tuned on 20+ seasons of RAPM; (c) EPM's
game-level minutes projections updated with injury and status news. (c) is the only kind of information that
has ever won here, but it overlaps heavily with the official report we already gate on, and the
dunksandthrees terms prohibit using API data for betting or trading models without a written agreement. One
system (DARKO) is free and has a dated Time Machine; it gets a PARK with a draft rule whose prior pass
probability is low. EPM is PARK because of its license. Everything else is SKIP.

## Per-system intake

Legend for "new info": NEW = not reconstructible from our data; PARTIAL = reconstructible in principle but
uses history or tuning we lack; NONE = a function of inputs we hold.

| System | Inputs | We hold the inputs? | New info | As-of history | License / cost | Closed idea re-tested | Verdict |
|---|---|---|---|---|---|---|---|
| **DARKO** (Medvedovsky, Patton; darko.app) | Box, tracking and game-level data from B-Ref/NBA.com, pbp through pbpstats, 1996-97 on. Per-stat exponential decay plus a modified Kalman filter, combined by GBDT. Adjusts for rest, travel, home, opponent, age (per-stat aging curves), time of season and team change. Rookie prior from age, draft slot and height. DPM adds on/off, weighted up as career possessions grow. No NCAA, summer league or preseason. | Yes for 2019+; no for 1996-2018 | PARTIAL: pre-2019 career, tuned aging curves. Minutes are "based on recent playing time and role", with no injury-report input stated. | **Yes (dated).** Time Machine sets the leaderboard, player pages and Fantasy Lab to a past date; CSV download per view. The about page says "any day since 1996-97"; the home page says "any week". Granularity unverified. No bulk daily archive. | Free; no terms page found (darko.app/terms 404). Terms unknown, so ask the author before bulk use. | Recency and aging (production base), rookie prior (F9b T186-T201, T181), mover prior (T504), RAPM (T176), advanced rates (T424-T439) | **PARK** (rule sketch 1) |
| **EPM** (Dunks & Threes) | Box SPM fit to RAPM 2003-04 to 2022-23; tracking SPM for defense 2017-18 on; per-stat decayed "Estimated Skills" (decay tuned by differential evolution); daily RAPM since 2001-02 with SPM as the Bayesian prior. Per-game projected minutes "updated as tip-off approaches using injury and status information"; per-game box projections; game predictions; 30k season sims. | Inputs mostly yes; RAPM fitting history no | PARTIAL plus NEW in one place: injury- and status-updated game minutes (their own read of the news, possibly beyond the official report's timing) | **Yes for ratings:** "EPM and Estimated Skills cover every day since 2001-02 (5,153 dates)", strictly before the date. **Per-game minutes and box projections history: unverified** (no public statement that past projections are stored as of a time). | Paid API (price not listed publicly; v1 API kept through 2026-27). Terms: no scraping; no redistribution; and "API data may not be used to train or operate predictive models for commercial decisions ... including betting, trading, pricing, or risk management systems" without a written agreement. | Same as DARKO; the minutes piece would be compared with MINUTES_V2 (T162-T173) and the report gating that won production | **PARK** (license; rule sketch 2 only if Devin gets written permission) |
| **LEBRON** (BBall Index) | Box prior (boxPIPM weights stabilized by offensive archetypes) plus luck-adjusted RAPM | Yes (box, pbp, stints) | NONE beyond archetype labels (their own clustering of our inputs) | Season-level tables; no daily as-of history found | LEBRON data and app described as free; other leaderboards paywalled; terms not read | RAPM T176, advanced rates | **SKIP** |
| **RAPTOR** (538) | Box plus tracking (Second Spectrum) plus on/off | Yes | NONE | Season-level CSVs in the fivethirtyeight/data GitHub and R package; no daily as-of. Stopped updating in 2023 (Disney), so no 2023-24 coverage. Neil Paine's "Estimated RAPTOR" is a paywalled substitute (Substack), season-level. | 538 data CC BY 4.0 (from memory, unverified); Paine paid | RAPM, tracking | **SKIP** (no as-of, no coverage of our seasons) |
| **BPM 2.0 / VORP** (Basketball-Reference) | Box score plus team rating, regression weights | Yes | NONE (a closed-form function of box score) | Season-to-date only, no daily archive; it can be rebuilt as-of from our box | Sports Reference data-use policy: no aggressive automated access; no use to train AI models; custom data at $5,000 or more. Note: we hold a bref salary pull (T504), so the archivist should check that use against this policy. | T518 (replacement value), advanced rates | **SKIP** |
| **ESPN RPM / xRAPM** (Engelmann, Ilardi) | RAPM with a box-score prior (exact prior inputs never published), aging, score state | Yes, except the prior's weights | PARTIAL (prior weights) | No as-of history; ESPN status for 2024-25 unverified (reportedly pulled after Engelmann joined Dallas). ESPN "NET Points" is a newer box-based metric. | ESPN terms; no API | RAPM T176 | **SKIP** |
| **PIPM** (Goldstein) | Box prior plus luck-adjusted on/off | Yes | NONE | Discontinued; superseded by LEBRON | n/a | RAPM, on/off | **SKIP** |
| **Cleaning the Glass** | NBA pbp with garbage time and end-of-quarter heaves removed; on/off; four factors | Yes (pbp, possessions.score_diff) | NONE (its garbage-time filter is a rule we can apply to our own pbp) | Season-to-date views; no dated archive found | Subscription (price not found); terms not read | FOUR_FACTORS T264-T279, WOWY_STRUCT T499 | **SKIP** (one idea worth keeping: the garbage-time definition as a *data* filter, not a rating) |
| **PBPStats** (Darryl Blackport) | Possession parsing of NBA pbp; open-source `pbpstats` library | Yes (same pbp) | NONE | Game-level results, so as-of is computable | Free; library open source | n/a | **SKIP as a rating**; possible cross-check for our stints and possessions (archivist's call) |
| **NBA.com advanced, tracking, hustle, play types (Synergy feed), Second Spectrum public derivatives** | Proprietary tracking, published as per-game and season tables | **Yes, held** | NONE (already ingested) | Game logs give as-of | NBA.com terms; we already ingest | TRACKING_SCREEN, ADVANCED_RATES (play types included) | **SKIP** (already tested null) |
| **BBall Index talent grades** (percentile grades for skills such as perimeter shooting and rim protection) | Tracking, Synergy and box, combined by their weights; largely stat-derived, with any manual scouting component unverified | Mostly yes | PARTIAL at most | Season-level; no dated archive found | Paid | TRACKING_SCREEN, ADVANCED_RATES | **SKIP** |
| **Craft / Box Creation** (Ben Taylor, Thinking Basketball) | Closed-form estimates from box score (creation, passing) | Yes | NONE | Rebuildable as-of | Formula public | ADVANCED_RATES playmaking family (PPR) | **SKIP** |
| **DFS projection sites** (Stokastic, RotoGrinders, FantasyLabs, Razzball, SportsDataIO) | Analysts' minutes calls plus usage models plus late news | Partly (news is the part we lack) | **NEW** (human minutes calls, late scratches) | **No free dated archive found**; paid tiers show current slates only. SportsDataIO-style feeds may sell history (unverified, paid). | Paid; most terms bar redistribution or automated use | MINUTES_V2, report gating | **SKIP for a backtest** (no as-of); the only valid test would be our own forward capture, which is a separate decision and probably restricted by terms |
| **Markets** (props and game lines) | Everything above, plus news | **Held** (odds archive 2023-25, Kalshi) | Already the strongest "rating": the market beat us on every props cell (T203-T237) | Yes (our T-60/T-5 archive) | Held | ODDS_HISTORY, MARKET_ANCHORED_PROPS | Already the benchmark and prior; no new system needed |

### Credibility notes
- No system in this list publishes an out-of-sample, walk-forward, proper-score comparison against a
  recency-plus-report baseline at the player-game level. DARKO and EPM publish accuracy claims for *ratings*
  (predicting future RAPM or margin), not for game-level prop distributions. That is a different target from ours.
- EPM's "as of a date, excluding games that day" is exactly the as-of semantics we need for ratings. Whether
  the **per-game minutes** projection is stored with a knowable-at time is the one thing that would matter,
  and it is unverified.
- DARKO's minutes come from recent playing time and role. Nothing on its about page says it reads the
  injury report. So its per-game projection probably knows less than our gated minutes model at T-60. That
  is the opposite of what we need.

## Prior evidence here (lineage)

- **RAPM / on/off / lineup ratings:** T176 RAPM in injury-Elo, rejected. But the playbook validity audit
  marks it INVALID: it ran on stints that matched box minutes only 47-49% of the time. WOWY_STRUCT T499 passed
  in 2023 and failed in 2024 (pts leader-out). F11 and F8 lineup composition closed (T244-T263). Any DPM, EPM,
  LEBRON, RPM or RAPTOR test is partly a **re-test of RAPM**. The honest version of that re-test is a *valid*
  RAPM on the rebuilt stints, which needs no outside rating.
- **Replacement value / team-level use:** T518 stopped at the gate on power (0.21 at the -0.002 log-loss
  floor; even an oracle reaches 0.22). Feeding any player rating into win probability is closed by power, not
  by the rating's quality.
- **Box / advanced / tracking rates:** ADVANCED_RATES (T424-T439), placebo better than real in 14 of 15
  cells; TRACKING_SCREEN (T119-T123, T174-T175); FOUR_FACTORS (T264-T279). BPM, VORP, Craft, Box Creation,
  CTG and BBall Index grades are re-tests of these.
- **Priors for thin-history players:** F9b rookie pedigree (T186-T201, closed); T181 rookie mpg pedigree
  (provisional, positive for mpg); T504 salary role prior stopped at the gate (power: 1,202 / 1,025 mover rows;
  pts half-width about 0.017 against a 0.005 floor); T179 time decay never tested. DARKO's and EPM's rookie
  and mover priors re-test these, and inherit T504's power problem if the test is restricted to movers.
- **Minutes:** MINUTES_V2 (T162-T173) is minutes-only and leaves props unchanged; MINUTES_HAZARD lost. The
  production wins came from the official report and T-30 lineups.

## Playbook check

Model class: an external rating used as an **as-of feature for the gradient-boosted context residual**
(props), or as a **minutes-model input**. Ratings-class use (win probability) is closed by T518's power.

1. **Information or capacity?** Mostly capacity: these systems are other people's models over inputs we hold.
   The information exceptions are pre-2019 career history (DARKO, EPM) and injury-updated game minutes (EPM;
   DFS sites without as-of history).
2. **Enough data for the class?** The GBM residual trains on about one season at a 2023 select. That suits a
   single added column. A thin-history cohort (rookies, movers, returners), where long-memory priors would
   matter, is too small to power a -0.005 floor (T504).
3. **Test validity.** It needs as-of snapshots dated strictly before tip. DARKO's Time Machine is dated, but
   the granularity (day or week) is unverified. If it is weekly, use the last snapshot dated before the
   game; that is conservative but never leaky. The integer grid for counts on every arm. A within-player-season
   permuted placebo (ADVANCED_RATES showed placebos can beat real columns). A planted-future check: shift the
   snapshot one week forward and expect a fake gain, which shows the harness can detect leakage.
4. **Lineage.** RAPM (T176, invalid stints), ADVANCED_RATES, TRACKING_SCREEN, F9b, T504. Inherited guards:
   placebo, missingness audit (rating NULL for rookies before game 1 is an as-of cause; audit its asymmetry
   against tonight's minutes, as with the opp_adjusted_ridge leak), real tip times.
5. **What it scores against.** Production context_residual v2 on the same rows, CRPS on the integer grid. A
   CRPS gain is not an edge at the line (T477); the market already beats production.

## Ranked short list (at most 3)

**1. DARKO dated per-100 skill projections as a long-memory prior (PARK; low prior).**
Draft rule sketch `DARKO_ASOF_PRIOR` (DRAFT, not frozen, gate not measured). *Hypothesis:* DARKO's as-of
per-100 projections for pts/reb/ast/fg3m add information to the context residual beyond our recency
averages, mainly through pre-2019 career and aging. *Target:* prop residual (actual minus recency) for pts
(primary), reb, ast, fg3m. Arm B is production plus three DARKO columns (the per-100 projection for the stat,
projected MPG, DPM), taken from the last snapshot dated strictly before the game date. Arm A is production. A
placebo arm permutes the DARKO columns within player-season. *Metric:* paired integer-grid CRPS (B - A),
game-clustered bootstrap. *Floor:* pts -0.005, reb -0.003, ast -0.002, fg3m -0.001 (the T504 floors), and B
must also beat the placebo. *Select 2023, report 2024*, both required. BH over the four stats. *Slices
(descriptive):* players with fewer than 20 prior games in our data; veterans whose careers start before 2019.
*Minimum-data gate (to measure before freezing):* snapshots for at least 95% of 2023 and 2024 game dates, or
weekly; player-id crosswalk resolved at 98% or more of rows; terms permit the use (author's answer in
writing). *Kill:* fails the floor in either season, or the placebo matches. No 2025 touch unless both seasons
pass and the adversary survives. *What it protects against:* the placebo catches "more columns" gains; the
both-seasons requirement catches 2023-only luck; the dated snapshot catches end-of-season leakage. *Prior:*
P(pass) about 0.05, expected pts effect about -0.001. ADVANCED_RATES says decayed box rates are already
captured by our recency base, and DARKO is largely that construction. *Cost to get data:* Devin downloads
about 25-50 CSVs per season by hand from the Time Machine (agents cannot curl). Granularity and terms come
first.

**2. EPM as-of game minutes projections (PARK; blocked by license).**
Draft rule sketch `EPM_ASOF_MINUTES` (DRAFT; do not pursue without written permission). *Hypothesis:* EPM's
per-game projected minutes, as they stood at T-60, carry injury and status information beyond the official
report gating and MINUTES_V2. *Target:* minutes CRPS given plays (primary), then the pts residual. *Metric:*
paired CRPS, game-clustered. *Floor:* minutes -0.25 min (about a third of the MINUTES_V2 gain over P), and pts
-0.005 to count as a props claim. *Select 2023, report 2024.* *Gate:* a stored projection timestamp before the
real tip for at least 90% of rows. This is unverified and probably fails: no public statement says past
per-game projections are archived with a time. *Kill:* no timestamped archive, or no written permission
covering betting use. *Why parked:* the dunksandthrees terms forbid using API data to train or operate
betting or trading models without a written agreement, and the API is paid (price not public).
parlayhitters.ai would make any use commercial.

**3. None.** No third system has both new pre-tip information and as-of history. DFS projections have new
information but no dated archive. Everything else is rebuildable from our data and repeats a closed test.

**Plain answer:** a *fair, licensed* test of genuinely new information is currently available from **none**
of these systems. DARKO is the only free dated source, and it mostly re-expresses what we hold. EPM has the
right kind of information but the wrong license and unverified minutes history.

**What would do more than any of these:** a valid RAPM on the rebuilt stints (the T176 re-test the playbook
already flags as owed), and more T-30 lineup and late-news capture. Both are ours to build and need no
outside license.

## Could not verify
- DARKO Time Machine granularity (day or week); DARKO terms of use; whether Fantasy Lab projections are
  shown as of past dates.
- EPM API price; whether past per-game minutes and box projections are stored with timestamps.
- ESPN RPM's current status; the 538 data license; CTG and BBall Index prices and terms.
- Whether any BBall Index grade includes manual scouting.

## Sources
- DARKO home and about: https://www.darko.app/ , https://www.darko.app/about , https://www.darko.app/projections
- EPM methodology and terms: https://dunksandthrees.com/about/epm , https://dunksandthrees.com/terms
- LEBRON: https://www.bball-index.com/lebron-introduction/ , https://www.bball-index.com/lebron-box-prior/
- RAPTOR status: https://flowingdata.com/2025/03/09/access-fivethirtyeight-resources-while-theyre-still-around/ ,
  https://github.com/rudeboybert/fivethirtyeight , https://neilpaine.substack.com/p/nba-estimated-raptor-player-ratings
- Sports Reference data use: https://www.sports-reference.com/data_use.html
- RPM: https://en.wikipedia.org/wiki/Adjusted_Plus_Minus , https://jeremiasengelmann.substack.com/p/espn-just-released-net-points-a-new
- CTG garbage time: https://cleaningtheglass.com/stats/guide/garbage_time
- DFS projections (no archive found): https://www.stokastic.com/nba/dfs-projections
