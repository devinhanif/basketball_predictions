# Information program: eight new sources of pre-tip information as one declared family (2026-11-17)

Modeler, branch `agent/modeler-info-program`. **Advice or momentum: Devin's override** ("do all of the 8", DECISIONS 2026-11-17 19:43,
against the main session's recommendation to concentrate on items 1 and 2). Nothing was fitted; no outcome was joined to any forecast;
season 2025 was never read; no DuckDB under the main checkout's `data/` was opened (counts come from manifests, file listings, ledger
rows and a read-only copy of `data/export/latest/export.duckdb` in my scratch directory, season 2026 rows only).

Files: umbrella DRAFT `docs/prereg/INFO_PROGRAM_2026_27.md`; intake notes `docs/research/info_0{1..8}_*_2026-11-17.md`; reading-log
rows in `docs/research/README.md`.

**Design revised the same night** (coordinator addition from Devin's "why can't we backtest any of this"): every member with a
trustworthy history is a **two-stage** test. Stage 1 = backtest, fit or select on 2023, report on 2024 walk-forward: **selection, never a
claim**. Stage 2 = the claim: ONE logged touch of the frozen 2025 holdout (Devin's decision each time; the archivist logs the row before
the run; the modeler never reads 2025) and/or the forward look on post-freeze 2026 rows for how the live system serves it. BH across the
family is over stage-2 claims only. Members without usable history go straight to a forward stage 2. No fit runs tonight (game night).

## One-page summary

| # | Item (member) | Knowable when | Captured today | Stage 1 backtest (2023 -> 2024)? | Stage 2 gate | Time to first answer | Effort | Prior P(pass) | Order |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Later lineups and availability (owned: T-15 frozen, LATE_REPORT, REPORTER_FEED_LIVE, REPORTER_LEAD_TRIAL) | fives median 16.8 min pre-tip; report every 15 min; reporters morning and 75-100 min | T-15 205 decisions (0.86 logged); late 59; reporters 0 in season | **No honest backtest**: no lineup-feed timing, 2022-24 reports two stamps a day on a clock 30 min early, no first-seen reporter history (all would flatter) | T-15 frozen and gated; LATE_REPORT_FORWARD and REPORTER_LEAD_TRIAL **rules missing** | T-15 L60 about 2026-12-22; others weeks after their freeze | modeler 1 d; Devin 15 min + install/key | owned (LATE prior 0.30 on file) | **1st** (rules only, no fit) |
| 2 | In-game state (owned: live-model lane) | during the game, about 60 s lag | pbp 2013-2024, capture 27 nights | possible on pbp (T508) but flatters without feed delay | owned (T513) | owned | owned | owned | own lane |
| 3 | Market numbers as inputs (**PROP_LINE_INPUT**; spread/total CLOSED) | lines from the morning; input only if received before made_at | history 2023-24 props bought (pts 2024: 13,021 Pinnacle / 17,993 consensus t60 legs, true T-60 per T502); live 13 books, t60 thin | **Yes, now, no new data** (30-60 min CPU, Mac outside game windows) | **CONDITIONAL on stage 1**: 2025 touch h about 0.008-0.009, so the 2024 effect must be <= about -0.009; forward look needs <= -0.010 to -0.015 | **about 3 working days** | modeler 2 d; archivist 0.25 d; steward 1 d (serving) | stage 1 0.55; claim 0.35 | **2nd** |
| 4 | Restrictions and return (**RETURN_RAMP**; limits owned by REPORTER_FEED_LIVE; b2b/load CLOSED) | box scores before the day; report reason hours before tip | box 2013-2024, 2026; reasons 2019-2024, 2026; bias -3.80 / -2.84 min (2023-24, seen) | **Yes, now, no new data** (10-20 min CPU); reason column only from stamps <= tip - 90; a 2024 pass reads "consistent with" | **YES** for a 2025 touch (about 875 rows, h 0.110 min, power 0.71); forward look borderline (power 0.54-0.63) | **about 4 working days** | modeler 2.5 d; steward 0.5 d | stage 1 0.55; claim 0.30 | **2nd** (with 3) |
| 5 | Roster moves (owned: MINUTES_DECISION; mover cold start) | announcement; roster snapshot next morning | roster snapshots from 2026-10-10; no transactions table | MINUTES_DECISION is already a 2023 -> 2024 backtest; mover backtest would flatter (move known only after game 1) | MINUTES_DECISION owned (gate pass); cold start **NOT FREEZABLE** (T512) | owned | archivist 0.5 d (transaction log) | owned | own lane |
| 6 | Referees (**REF_FOUL_MINUTES**) | morning assignment page | crews 2022-24 about 4,000 rows a season, 2026 616; assignment pages 566 on 40 dates; history 0 | possible on data, **not run**: REFEREES T238-T243 already was this backtest (null) | **NOT FREEZABLE**: ceiling about 0.06 min < 0.10 floor (effect, not data) | about 2 days if insisted | optional descriptive 0.5 d | 0.03 | last (optional) |
| 7 | Schedule (**SEASON_STAKES**; travel/altitude/rest CLOSED) | as-of standings, the morning | games 2013-2024, 2026 | possible on data, **not freezable**: about 2,500 rows a season, h 0.0086 > 0.005 | **NOT FREEZABLE** in any single season (2025 touch or forward) | n/a | optional descriptive 1.5 d | 0.04 | last (optional) |
| 8 | Kalshi prop moves (**KALSHI_NEWS_MOVES**) | our receive clock, about 60 s, from about T-2 h | 27 capture nights from 10-21; 15-min snapshots from 10-08; prints from 11-16; 19% markets unresolved | **Impossible** (no history; 2025-26 prices are holdout-season data) | **PENDING Stage 0**; forward h 0.0037 resolves the floor | Stage 0 about 1 week; claim about April 2027 | modeler 1 d; Devin 15 min (aliases) | 0.10 | **3rd** (Stage 0) |

## Which members are freezable

* **Freezable now:** RETURN_RAMP (stage 1 on 2023 -> 2024; stage 2 a 2025 touch passes its gate; confirm the label-free row count and the
  games-2-3 bias first, because the ramp factor 2.5 is an assumption). Owned and already frozen: FORWARD_T15_2026_27.
* **Conditionally freezable:** PROP_LINE_INPUT (its stage-2 freeze is decided mechanically by the stage-1 2024 effect: <= about -0.009
  for the 2025 touch, <= -0.010 to -0.015 for the forward look); KALSHI_NEWS_MOVES (forward only; decided by Stage 0 counts: about 0.08
  correctly flagged post-T-15 scratches a game with no false alarms).
* **Not freezable:** REF_FOUL_MINUTES (the information's ceiling, about 0.06 min, is below the 0.10 floor; more seasons add n, not effect);
  SEASON_STAKES (about 2,500 stake rows in any one season, h about 0.009 > 0.005, planning effect at or below the floor); the mover cold
  start of item 5 (T512: about 4x the rows needed, not reachable with every repair named). Each has its audit of non-negotiable 8 in its
  note and a stated route that would change it.

## The family (umbrella sections 2-8)
One stage-2 primary per member; membership fixed by **2027-01-15** from label-free gates and stage-1 results (selection on 2023-24, which
the claims never use); **BH q = 0.05** over the stage-2 p-values only; an early word only at p <= 0.05/m; secondaries descriptive within
the member. Floors are the existing ones (props -0.005 integer CRPS; minutes -0.10 min; P(play) -0.005 nats). Baseline = production at the
same forecast time (T-15: the T-15 arm, else LATE_REPORT, else T-60), plus the market where the input is a market number, plus a free
information-equivalent (RETURN_RAMP vs production plus a constant shift). Every member: too-good bound, single-group flag, missingness
audit, known-at on our clock, and the attacks (shuffled labels, planted future, placebo information, knockout, replication, positive
control). **No member joins any outcome from a slate on or after 2026-10-30 to a forecast before LINE_CAL_LIVE's look is recorded.**

**One thing Devin and the archivist must settle before any stage-2 touch:** PROJECT_STATUS (2026-11-16) says "season 2025 is burned"; the
halftime scorer read 2025 box scores under a logged disclosure (DECISIONS 2026-10-27 18:41); the nightly export carries 2025 box rows. If a
2025 touch is no longer a clean confirmation for these never-evaluated members, stage 2 is the forward look only, and RETURN_RAMP's forward
gate is borderline (power 0.54-0.63 at a 2026-12-01 freeze).

## Order of work (cheap backtests first)

| Step | What | Depends on | Modeler | Archivist / steward / adversary | Devin | Where it runs | Parallel with |
|---|---|---|---|---|---|---|---|
| 0 | Umbrella: adversary pre-freeze read, then freeze | this branch | 0.25 d | adversary 0.5 d | 20 min | docs | 1 |
| 1 | Item 1 rules (no fit): freeze LATE_REPORT_FORWARD before any of its outcomes are joined; write REPORTER_LEAD_TRIAL | none | 1 d | steward: install the built reporter job | 15 min + key and cap | docs | 0, 2 |
| 2 | **RETURN_RAMP stage 1**: label-free counts and games-2-3 bias (2022, stored npz), member rule frozen, harness on the fixture, then the 2023 -> 2024 run | umbrella frozen | 2 d | adversary pre-freeze and post-result 1 d; maintainer command | 5 min | Mac CPU 10-20 min, outside game windows, `heavy.lock` | 3 |
| 3 | **PROP_LINE_INPUT stage 1**: member rule frozen, harness, 2023 -> 2024 run at T-60 on bought history | umbrella frozen | 2 d | archivist 0.25 d (2023-24 odds history pins); adversary 1 d | 5 min | Mac CPU 30-60 min, about 2-3 GB, outside game windows, `heavy.lock`; not Colab | 2 |
| 4 | Stage-2 decisions: for each stage-1 pass, the 2025 touch row (Devin's yes, archivist logs, maintainer runs) and/or a forward shadow (steward 0.5-1 d each, replay oracle, Devin's go) | 2, 3 | 0.5 d | archivist 0.25 d per touch; steward 1.5 d | 5 min per decision | Mac, outside game windows | 5 |
| 5 | KALSHI_NEWS_MOVES Stage 0: thresholds frozen first, then counts and leads on capture copies (label-free) | aliases; live-model clock work | 1 d | market agent: copy paths | 15 min aliases | Mac, outside game windows (up to 780 MB a slate) | 4 |
| 6 | Optional descriptives that could reopen REF (2022, 0.5 d) and SEASON_STAKES (2019-21, 1.5 d) | none | 2 d | maintainer commands | 5 min | Mac | any |
| 7 | Transaction log from daily roster diffs (plumbing) | none | 0 | archivist 0.5 d | 0 | ops | any |

MINUTES_DECISION (item 5) and the in-game lane (item 2) run in their own lanes; cost mode allows two agents at a time, so they share the
queue with steps 2-5. Totals: modeler about 8-9 days, archivist about 1-1.5 days, steward about 2 days, adversary about 2.5 days, Devin
about 1.5-2 hours over the weeks. **Times to first answer:** RETURN_RAMP and PROP_LINE_INPUT stage 1 in about a week of calendar time after
the umbrella freezes (no download needed); a 2025-touch claim a few days after Devin's yes; KALSHI Stage 0 about a week, its claim about
April 2027; forward looks months. The forward gate numbers assume a 2026-12-01 freeze; each week of delay removes about 50 games; by a
2026-12-22 freeze (about 771 games left) RETURN_RAMP's forward power is 0.46-0.55.

**Expected nulls, and why they are still worth a row:** REF_FOUL_MINUTES (REFEREES already bounds the effect), SEASON_STAKES (76% of the
behaviour is already on the report, T476), KALSHI_NEWS_MOVES (post-T-15 scratches missing from both the report and the lineup feed are
rare). The step-6 descriptives (2 days) would close REF and STAKES with numbers rather than an argument. PROP_LINE_INPUT will probably
pass on CRPS and **cannot** produce an edge at the line it reads (T517): a pass improves display, alt rungs and combos, not bets.

## Priors for docs/PREDICTIONS_LEDGER.md (proposed rows, not appended; written before any result)

| Date | Rule / question | Prior | Where written |
|---|---|---|---|
| 2026-11-17 | INFO_PROGRAM: PROP_LINE_INPUT | P(stage 1 passes: 2024 point <= -0.005, CI upper < 0, 2023 same sign, guards hold) 0.55; P(2024 effect <= -0.009, so a 2025-touch stage 2 freezes) 0.50; P(stage-2 claim passes BH) 0.35; expected 2024 pts integer-CRPS delta -0.012 (80% -0.030 to -0.002); P(log loss at the main line better than the de-vigged market on the same legs) 0.05 | docs/reviews/info_program_2026-11-17.md |
| 2026-11-17 | INFO_PROGRAM: RETURN_RAMP | P(label-free count keeps the gate and it freezes) 0.70; P(stage 1 passes on 2024) 0.55; P(stage-2 claim passes) 0.30; expected 2024 minutes CRPS delta on returner games 1-3 -0.12 min (80% -0.30 to -0.02); P(production's games-2-3 mean bias is at least 1.5 min) 0.50 | same |
| 2026-11-17 | INFO_PROGRAM: KALSHI_NEWS_MOVES | P(Stage 0: the material move precedes the report change on at least half of matched events) 0.45; P(stage 2 freezable) 0.35; P(pass) 0.10; expected all-rows P(play) log-loss delta -0.001 (80% -0.004 to +0.001) | same |
| 2026-11-17 | INFO_PROGRAM: REF_FOUL_MINUTES | P(2022 descriptive shows a crew-tercile minutes gap >= 0.6 min for foul-prone players) 0.05; P(pass if ever run) 0.03 | same |
| 2026-11-17 | INFO_PROGRAM: SEASON_STAKES | P(2019-21 descriptive shows production under-predicting DNP on stake rows by >= 2 pp) 0.15; P(pass if ever run) 0.04 | same |
| 2026-11-17 | INFO_PROGRAM family | P(at least 2 members reach a frozen stage 2 by 2027-01-15) 0.55; P(at least one stage-2 claim passes BH at the family close) 0.40; P(a passing member survives the adversary attack) 0.70 given a pass | same |
| 2026-11-17 | REPORTER_LEAD_TRIAL (owned elsewhere; for that lane to restate) | P(median reporter lead over the official report's first stamp with the same status >= 10 min, CI excluding 0, within the $50 cap) 0.55 | same |

## Proposed rows (drafted here, not appended)

* **DECISIONS** (main session): "2026-11-17 2x:xx | INFO_PROGRAM_2026_27 drafted (agent/modeler-info-program), two-stage per the
  coordinator's addition: stage 1 backtest 2023 -> 2024 (selection), stage 2 the claim (one logged 2025 touch, Devin's decision, and/or the
  forward look); one family F-INFO of up to five members, BH q 0.05 over stage-2 claims, membership by 2027-01-15. Freezable: RETURN_RAMP;
  conditional: PROP_LINE_INPUT (stage 1), KALSHI_NEWS_MOVES (Stage 0); not freezable: REF_FOUL_MINUTES, SEASON_STAKES, mover cold start.
  Spread/total, travel/altitude/rest, b2b/load not re-proposed (T389-T414, T451-T454, T521). | Devin's override ('do all of the 8') and his
  backtest question; non-negotiable 8 per member. | A member's label-free gate fails at its freeze; Devin narrows the list; 2025 judged no
  longer clean (stage 2 becomes forward only)."
* **TEST_LEDGER**: none (no test ran). Optional gate-stop rows for the two not-freezable members: "REF_FOUL_MINUTES (draft in
  INFO_PROGRAM_2026_27, not frozen) | NOT FREEZABLE: ceiling about 0.06 min (from T238-T239) below the 0.10 floor; data resolve the floor;
  no fit, no outcome read" and "SEASON_STAKES (same) | NOT FREEZABLE: about 2,500 stake rows a season, h about 0.009 nats > 0.005; e_plan
  0.0006-0.006 from T476; no fit, no outcome read".
* **Holdout log**: none now. What a stage-2 row would say, if Devin says yes after a stage-1 pass (the archivist logs it before the run):
  "INFO <MEMBER> (frozen <sha8>, stage 1 PASS T5xx): one touch of season 2025 for this never-evaluated model; primary <metric> on <rows>,
  arm minus production; run by the maintainer; read once; no refit after."

## Devin's choices (with my recommended defaults)

1. **Stage-2 venue per member.** Default: the one logged 2025 touch is the F-INFO primary for PROP_LINE_INPUT and RETURN_RAMP (it is the
   claim the backtest earns, decided by Devin after stage 1), and the forward look is descriptive for serving. Alternative: forward only
   (if 2025 is judged no longer clean; RETURN_RAMP is then borderline).
2. **Is 2025 still a clean confirmation?** (PROJECT_STATUS says "burned".) Archivist and Devin; default: check HOLDOUT_ACCESS_LOG for any
   read touching these members' populations; if none, the touch is clean for never-evaluated models.
3. **Freeze the two failed members anyway?** Default no (non-negotiable 8). Run the REF 2022 descriptive (0.5 d); defer STAKES.
4. **PROP_LINE_INPUT at all**, knowing a pass is not an edge. Default: run stage 1 (cheap; answers "does the market's line sharpen our
   distribution?"); skip the forward shadow if Devin only cares about bets.
5. **Family settings.** Default q 0.05, membership date 2027-01-15, early word at p <= 0.05/m.
6. **KALSHI thresholds** (6c median move, at least 3 markets, p_play factor 0.5). Default as drafted, frozen before Stage 0 reads a page.
7. **Live T-60 line coverage.** Default: the forward look at T-15 on the consensus of books received before the cutoff (no new spend).
8. **LATE_REPORT_FORWARD.** Default: freeze its scoring rule now, before any of its rows are joined to an outcome.
9. **`reports/referees/arms/`** (untracked, 12 files, about 28 MB): default add to `.gitignore`; they are the October REFEREES run's arrays.

## Playbook check (program level; per-member checks are in each note)
Classes touched: gradient-boosted residuals (PROP_LINE_INPUT, REF_FOUL_MINUTES, SEASON_STAKES), quantile/distribution layers with fixed
transforms (RETURN_RAMP, KALSHI_NEWS_MOVES). (1) Information or capacity: every member adds a pre-tip fact; none adds capacity.
(2) Data: history fits are small (a few coefficients or one input group); Kalshi has no history, so nothing is fitted there. (3) Validity:
same-time production baseline; integer grid; receive-clock known-at; per-player clustering where the treatment is per player; positive
controls; an invalid test would be reported as invalid, not as a null. (4) Lineage: each member names the closed tests it is not (GMC,
WORKLOAD, T521, REFEREES, T512) and what differs. (5) Scored against production CRPS / log loss and, for market inputs, against the market
at the line.

## What is not verified
* The returner ramp factor (2.5 games per return) and the games-2-3 bias: assumptions; the gate turns NO if they are low.
* Per-row standard deviations used for h (0.25-0.3 for small-shift arms; 1.35 min for RETURN_RAMP from a simulation; the PROP_LINE_INPUT
  h is scaled from the T-30 arm's SE table and is a lower bound for higher-minute line rows).
* Live line density: measured in a week that includes the odds-key outage (11-14 to 11-16); the new key's effect is unconfirmed.
* I did not parse any Kalshi capture page; Kalshi counts are the market agent's (branch `agent/market-prop-candles`, not merged).
* Season projection: 1,230 games, 205 in `games` for 2026 by 2026-11-17 (manifest 6edbcef0c89d; whether that includes tonight's 5 games is
  not checked), about 7.5 a night, T-15 logged share 0.86 (survey of 2026-11-16).
* The REFEREES pts-to-minutes translation is crude (stated in the note).
* Outside evidence: abstracts and snippets only; no full paper was read. No peer-reviewed study of market reaction speed to injury news was
  found.
* LATE_REPORT_FORWARD's draft text: not in the repo (only the prior row); I did not see it.
* Whether season 2025 is still a clean confirmation (PROJECT_STATUS calls it burned); the 2025 row counts are projected from 2024, not
  counted.

Sources (web): [NBER w13206](https://www.nber.org/papers/w13206.pdf), [BettingPros referee report](https://www.bettingpros.com/nba/referee-report/),
[PillarLab crew foul tendency](https://pillarlab-web.pages.dev/pillars/referee-crew-foul-tendency), [Arizona experts: travel and back-to-backs](https://experts.arizona.edu/en/publications/impacts-of-travel-distance-and-travel-direction-on-back-to-back-g/),
[PubMed 34170248](https://www.pubmed.gov/34170248/), [Frontiers in Psychology 2021](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2021.630769/full),
[LSports blog](https://www.lsports.eu/pt-br/?p=26756), [Odds Reference](https://oddsreference.pages.dev/sports/learn/line-movement).
