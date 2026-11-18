# CAL_BY_TIME: does the frozen T-60 line map keep production's P(over) honest when the line is read in the morning, at T-60, at T-30 and at T-15? (pre-registration)

Status: **DRAFT, revision 1, branch `agent/modeler-calibration-program`. Revision 0 committed Tue 2026-11-17 19:58 CST; revision 1 adds
section 11 (what a 2023-24 backtest by slot needs, where it would flatter, and a per-slot recommendation) after Devin's two-stage decision
relayed the same evening. Sections 1-10 are the stage-2 forward rule; under the recommendation, a slot enters stage 2 only if its stage-1
backtest passes, and t30 is forward-only. Not frozen. No map fitted, no outcome read, no CAL or RAW score on any 2026 leg.**
- **How it becomes the rule:** the main session commits `prereg(CAL_BY_TIME): <sha256>` of everything above `=== RESULTS BELOW ===`
  (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/CAL_BY_TIME.md | shasum -a 256`), after: the T-30 gate count (section 5,
  G3) is measured by the maintainer, a harness exists and passes a synthetic dry run, an adversary pre-freeze read returns READY, and
  Devin confirms section 9.
- **Origin:** Devin 2026-11-17 ("calibrated always"). **Follows advice:** CALIBRATION_EVERYWHERE plan section 1 ("T-60 only: live
  production forecasts are written once a morning ... a timing difference the maps never saw"); CAL_SERVING_CHECKS C2 (68.6% of tonight's
  served values use a line read 2 or more hours before tip).
- **Season labels:** `games.season`. Report rows: season 2026 only. Season 2025 is never loaded. The map is the frozen LINE_CALIBRATION map
  (payload sha 443743a1, fit on 2023-24, fit_through 2025-06-30); nothing is refit.

## 1. Question and hypothesis
The map was fitted on (production forecast as of T-60, book main line at the T-60 snapshot). The system serves the same forecast (write-on-
change: unchanged since its `made_at` through the last covering run) against lines read at other times: the morning snapshot today, the
newest pre-tip line after the pairing fix (median lead 13 min under the extended pairing, measured tonight). Lines move between the morning
and T-15 as news arrives; the map's z term (line vs median in spread units) and its intercept were learned only where the T-60 line sat.
- **H1 (per slot s):** at slot s, CAL is calibrated in the large (realised over rate within 0.03 of mean CAL, game-clustered 95% CI inside
  [-0.03, +0.03]) and beats RAW on Brier by at least the floor.
- **Null:** the map holds at T-60 only; at other slots it is miscalibrated (most likely at T-15, where the line has absorbed news the
  morning forecast has not, and in the morning, where lines are softer).
- **Not claimed either way:** an edge. An honest probability is not a better one than the price.

## 2. The rule in ten lines
1. **Legs.** Production (`props_context_residual`) prop rows scored by `forward_scores` (`status = 'scored'`: settled, played; DNP voided as
   books void them), season 2026, stats pts / reb / ast / fg3m, one leg per (game, player, stat, slot).
2. **Slots and lines.** For slot s in {morning, t60, t30, t15}: the line is the book's main x.5 point (`nba.forward.scorecard._main_point`:
   two-sided point whose raw prices are closest to even) from the newest snapshot of kind s with `received_at` strictly before tip (live
   capture `market_asof.duckdb`). Lead bars: morning >= 240 min; t60 in [45, 90]; t30 in [20, 45); t15 in [5, 20).
3. **Forecast.** The production row that was the latest for the key at the line's `received_at` (`made_at <= received_at` and not
   superseded by then). A leg whose only production row is newer than the line is dropped and counted.
4. **Book.** Pinnacle primary (the map's primary book and LINE_CAL_LIVE's); DraftKings secondary (the book the app serves).
5. **Arms.** RAW = `top_source.p_over(pred, line)` clipped to [0.5/199, 1 - 0.5/199]. CAL = `CalMap.apply(book, stat, RAW, line, q50,
   (q75 - q25)/1.349, bench_input)`, map payload sha 443743a1 (refused otherwise), quartiles from the stored 19-point `q_grid`;
   `bench_input` per section 9 choice 1 (default: `starter10` as of the date, the fitted definition).
6. **Window.** Slates (ET) strictly after the later of (a) this rule's freeze date and (b) the date LINE_CAL_LIVE's look is run. No slate
   inside LINE_CAL_LIVE's window is ever read by this rule.
7. **One look.** The first date on which every slot in the family has >= 200 games with >= 1 leg and the slot's leg minimum (section 5),
   counting only fully settled dates; backstop 2027-03-15 (slots short of the minimum then are INSUFFICIENT_DATA). Real clock only.
8. **Primary metrics per slot (Pinnacle):** M1 = mean(y - CAL) (calibration in the large); M2 = mean per-leg Brier, CAL minus RAW. 95%
   paired percentile bootstrap over games (B 2,000, seed 0); a date-clustered CI beside it (the game CI binds).
9. **Verdict per slot.** HOLDS: M1's CI inside [-0.03, +0.03] AND M2 <= **-0.0015** with CI upper < 0 (BH-adjusted, below). HONEST_ONLY:
   M1 passes, M2 fails. NOT_HONEST: M1's CI lies wholly outside [-0.03, +0.03], or |M1| > 0.03 with CI excluding 0. INCONCLUSIVE:
   otherwise. INSUFFICIENT_DATA: below the slot minimum at the backstop.
10. **Multiplicity.** Family A = the four slot M2 p-values (one-sided, `(1 + #{boot of RAW - CAL <= 0}) / 2,001`), BH at q 0.05. M1 is an
   equivalence check per slot at 95% (two one-sided tests at 2.5% each), not BH-adjusted; the DraftKings rows are labels with their own
   BH (m 4), sharing nothing with family A.

## 3. Why these choices (what each protects against)
| choice | protects against |
|---|---|
| window after LINE_CAL_LIVE's look | a second look at LINE_CAL_LIVE (the T-60 Pinnacle cell here is a forward replication on new games only) |
| forecast = the row valid at the line's time | pairing a forecast with a line it could not have known, or a later forecast with an earlier line |
| equivalence margin 0.03 | calling a slot "honest" on a CI that is merely wide; 0.03 is LINE_CAL_LIVE's side bar (P2/P3) |
| floor -0.0015 Brier | a gain too small to matter; it is LINE_CAL_LIVE's floor (the Brier value of a 4 pp bias near 0.45) |
| per-slot verdicts, BH over the four M2 | four chances to find one "good" slot |
| Pinnacle primary, DK labels | judging the map on a book it was not primarily fit on; DK is still reported because it is what the app shows |
| starter10 bench input (default) | testing a serving bug instead of the map (CAL_SERVING_CHECKS C3) |
| DNP voided | scoring a played-only map on a mixture it never saw |
| one look, minimums, backstop, real clock | peeking, a test that never ends, a verdict forced by a typed date |

## 4. Slices and secondary results (labels only, game-clustered CI, n and games on every row)
- S1 per stat (BH m 4 per slot); S2 side by RAW (under-lean / over-lean); S3 starter / bench (served `starter_rate` and fitted
  `starter10`, both); S4 line moved between the morning and the slot (|line_s - line_morning| >= 1) vs not; S5 RAW outside the map's x band;
  S6 recency-fallback rows; S7 calendar month; S8 reliability in ten equal-width bins, per slot; S9 Brier decomposition (reliability,
  resolution, uncertainty; Murphy 1973) for RAW and CAL per slot, so sharpness is read beside calibration; S10 CAL and RAW minus Pinnacle's
  de-vigged price at the same snapshot (reference only); S11 **surfaced legs**: legs the parlay panel's top tickets or a JARVIS card showed
  at that slot (conditional calibration where decisions are made; T495 found the panel's top picks run 3-5 pts high), descriptive.
- D1 descriptive slope: logistic regression of y on logit CAL per slot (intercept, slope, game-clustered CI). Description, never served.

## 5. Minimum-data gate (non-negotiable 8), measured Tue 2026-11-17 19:48-19:52 CST, label-free
Command and data: `reports/prereg_calibration_program/gate.py` on a scratch copy of the app export (sha256 `750c736e...`, data_version
`6edbcef0c89d`, slates 2026-11-10..17, 56 games), output `gate_draft.json` (sha256 `3629b223...`); the schedule parquet
`data/schedule/live_tips_2026-27.parquet` (1,200 regular-season games, 10-20..04-11); LINE_CAL_LIVE section 5 and the CALIBRATION_EVERYWHERE
plan section 4.2 for the t60 rates the export cannot show (the export keeps only the newest quote per pairing time). No outcome read; power
uses synthetic labels drawn from the 500 real stated CAL values (mean 0.486, 5th-95th 0.405-0.565).

| check | measured | bar | pass |
|---|---|---|---|
| G1 morning legs per game with a line (Pinnacle / DK) | 15.2 / 20.4, in 18 / 19 of 37 games with lines (coverage about 0.49 / 0.51) | >= 15 legs/game | yes |
| G2 t60 legs per game (Pinnacle / DK) | 31.4 at coverage 0.838 (LINE_CAL_LIVE gate, 10-20..28) / 49 at coverage 0.64 (CAL_EVERYWHERE, 10-20..23); tonight's export shows t60 pairings in only 9 games because no forecast run pairs at T-60, the capture still holds the snapshot | >= 15 | yes (borrowed rates, re-measure at freeze) |
| G3 t30 legs per game | **not measured**: no run pairs at T-30, so the export cannot show it; `t30` snapshots exist in the capture (`prop_lines.snapshot_kind`) | >= 15 | **BLOCKED until measured on `market_asof.duckdb` (maintainer, label-free)** |
| G4 t15 legs per game (Pinnacle / DK) | 34.9 / 50.8, in 35 of 37 games with lines (coverage about 0.95) | >= 15 | yes |
| G5 power, M1 equivalence at 200 games | half-width 0.0124 (31 legs/game), 0.0154 (20), 0.0096 (51). P(HOLDS-M1 | true gap 0) 0.99 / 0.94 / 1.00; | gap 0.015: 0.66 / 0.47 / 0.88; P(false pass | gap 0.04) 0.00 at every size | P(pass | gap 0) >= 0.8 | yes |
| G6 power, M2 | LINE_CAL_LIVE: MDE about -0.0031 Brier at 200 games and 31 legs/game; the floor binds below about -0.0024 | reported | n/a |
| G7 projected look (200 games per slot from 2026-12-04) | Pinnacle t60 2027-01-12; DK t15 2027-01-08; DK t60 01-22; morning (coverage 0.51) **2027-02-01**; t30 unknown | before the backstop 03-15 | yes for 3 slots; t30 unknown |
| G8 capture audit | seasons held for live lines: 2026 only (live capture); 2023-24 `odds_history` holds t60 and t5 snapshots only (no morning, no t30, no t15), so no historical rehearsal by slot exists; 2025 is the holdout | listed | n/a |

Slot leg minimum: 3,000 legs (morning) and 5,000 (t60, t30, t15). **Gate: PASS for morning, t60 and t15; t30 BLOCKED (not measured).**
If G3 is still unmeasured at freeze, t30 is removed from the family (m 3) by that count alone, before any outcome exists.

## 6. Interactions with frozen tests (none of this becomes a second look)
- **LINE_CAL_LIVE:** window starts after its look; it never reads a slate in 2026-10-30..D; the map is the one LINE_CAL_LIVE scores, so a
  NOT_TRANSPORTED there makes HOLDS here unlikely; this rule still runs (it answers a different question on new games) and says so.
  LINE_CAL_LIVE's PARTIAL follow-up LINE_CAL_SIDE (one id, a side term, next 200 games) would share games with this window; if it is
  frozen, the two share legs but answer different questions, and the overlap is disclosed in both results (no pass word moves).
- **Display lock for this rule's window:** no surface shows CAL beside outcome, per slot, for slates in this rule's window until its look
  (section 9 choice 3). The LINE_CAL_LIVE window's slates may be shown after LINE_CAL_LIVE's look.
- **FORWARD_PREREG_2026_27** scores CRPS, not these slots; **UNDERS_FORWARD** and **BEST_PRICE_FORWARD** read prices and ROI, not CAL. No
  shared pass word. **PPLAY_INTO_PROPS_LIVE**: no overlap (played legs only here).

## 7. Kill criteria, attack list, what a result changes
- Stop unscored if: the map sha differs; any season other than 2026 enters; more than 5% of legs at a slot have the forecast newer than the
  line (rule 3) or a line after tip; the harness's executed code differs from the freeze commit.
- **Attack before belief (adversary, after the look):** re-derivation from stored fields; a second bootstrap seed; drop the top five
  player-seasons; team clustering; label shuffle within date (M1 must move off 0 only by chance); planted-future check (a line after tip
  must be refused); the slot definitions shifted by 5 min.
- **HOLDS at a slot:** the app may label CAL at that slot "calibrated" (CAL_SERVING_CHECKS C2 `time_scope` lifts for that slot). Devin's go.
- **HONEST_ONLY:** shown as "calibrated, no better than raw at this time". **NOT_HONEST:** that slot's calibrated chance is withdrawn (shows
  nothing, per Devin's rule); a refit by slot is a new id, drafted only after this result, fit on live 2026 legs of earlier slates only.
- One pre-registered follow-up at most (a slot term in the map), then closed.

## 8. Playbook check (docs/MODEL_PLAYBOOK.md; class: quantile and distribution layers, recalibration at the line)
1. **Information or capacity?** Neither. It checks whether an existing correction transports across line times. The information lever is
   the later line itself (news priced in); this rule asks only whether the stated chance stays honest when paired with it.
2. **Enough data for the class?** Nothing fitted; the test needs about 200 games per slot (G5).
3. **Valid test?** Real tips; as-of pairing of forecast and line (rule 3); played legs; integer support exact at x.5 lines; same legs for
   RAW and CAL; synthetic-label power on real stated values. Weak point: no historical rehearsal by slot is possible (G8), and the t60 rates
   are borrowed from October.
4. **Lineage.** CALIBRATED_GRID (T339) -> LINE_CALIBRATION (T493) -> LINE_CAL_LIVE (T524, frozen) -> this. Carried: map sha refusal,
   game-clustered paired bootstrap seed 0 B 2,000, date CI beside, x band slice, starter/bench, fallback slice, Pinnacle primary.
   Dropped: the shuffled-label null as a pass condition (nothing is fit). Added: slots, equivalence margin, Brier decomposition, surfaced legs.
5. **What it scores against.** Calibration in the large and Brier at the line against RAW; the de-vigged price is a reference row only.

## 9. Devin's choices (my recommended default first)
1. **Bench input:** *default `starter10` as of the date* (the fitted definition; CAL_SERVING_CHECKS C3 makes it the served one). Alternative:
   `starter_rate` (what is served today).
2. **Slots:** *default morning, t60, t30 (if G3 passes), t15.* Alternative: drop morning (cards would then show nothing before T-60).
3. **Display lock for this window:** *default: yes, CAL beside outcome hidden for this window's slates until the look* (about 2027-02-01).
   Alternative: no lock, and this rule becomes descriptive (no verdict word).
4. **Book:** *default Pinnacle primary, DK labels* (the map's design). Alternative: DK primary (what the app shows), Pinnacle labels.
5. **Backtest scope (revision 1):** see section 11; *default partial backtests for morning and t15, t30 forward-only, no pull.*
Answers to 1-5: pending (recorded here when the main session relays them).

## 10. Priors (drafted for PREDICTIONS_LEDGER, not appended)
P(t60 slot HOLDS) 0.45; P(t15 slot HOLDS) 0.40; P(morning slot HOLDS) 0.30; P(at least one slot NOT_HONEST) 0.35; P(the look happens by
2027-02-15) 0.70. Expected Pinnacle M1 at t15 +0.01 (80% interval -0.02 to +0.04): later lines have absorbed late scratches the
morning forecast has not, which pushes the realised over rate away from CAL in the direction of the news.
Stage 1 (section 11), drafted with revision 1: P(morning passes the m360 backtest) 0.55; P(t15 passes the t5 backtest) 0.40.

## 11. Backtest by slot on 2023-24: what it needs, where it flatters, and the recommendation (revision 1)
**Lines.** `odds_history` holds 2023-24 prop lines at t60 and t5 (the original pull), plus the paid `movement` phase (Devin 2026-10-23,
"pay for the pull"): kinds m360, m180, m90 (m1440 dropped after the probe; DECISIONS 2026-10-23 23:1x), 9 books including Pinnacle and
DraftKings, 2023-24 only. Its ledger (`data/odds/budget/the_odds_api_movement_ledger.json`, read tonight) shows 283,040 credits over 7,993
calls, last updated 2026-10-28. **Not verified:** that those rows were ingested into `odds_history.duckdb` and how many prop legs each kind
holds (no DuckDB under `data/` was opened). There is **no t30 and no t15** in history; t5 (median lead about 9 min) exists.

**A pull for the missing kinds (only if Devin wants T-30 or a true T-15 backtest).** `configs/odds_history.yaml`: historical cost 10 x markets x
regions per event; the movement phase bills 9 books as one region (`bookmakers=`). Props only (4 markets): 10 x 4 x 1 = 40 credits + 1 events
call = 41 per game-snapshot. Games: 1,318 (2023) + 1,320 (2024) = 2,638. One kind: 2,638 x 41 = **108,158 credits**; two kinds (m30, m15):
**216,316** (equal to MOVEMENT_PULL_PLAN's "props only, two kinds" row, a cross-check). Probe first: 20 games x 2 kinds x 41 = 1,640. The
movement cap (1,000,000; 283,040 spent) has room; the key was swapped on 11-16 and the new key's remaining balance is unknown to me. The
config needs `m30: 30` and `m15: 15` added to `movement_snapshots` (a market config, not mine; `history_pull` rejects unknown kinds, so the
code needs nothing else). Exact commands for Devin (agents cannot download), after that config change is reviewed and merged:
```
cd /Users/devin/Downloads/nba-prediction
.venv-1/bin/python -m nba.odds history-pull --phase movement --season 2024 --kinds m30,m15 --scope props --dry-run
.venv-1/bin/python -m nba.odds history-pull --phase movement --season 2024 --kinds m30,m15 --scope props --max-games 20
.venv-1/bin/python -m nba.odds history-pull --phase movement --season 2024 --kinds m30,m15 --scope props
.venv-1/bin/python -m nba.odds history-pull --phase movement --season 2023 --kinds m30,m15 --scope props
```
Label-free count of what is already held (maintainer; read-only; seasons filtered; odds_history holds prices, not outcomes):
```
cd /Users/devin/Downloads/nba-prediction && .venv-1/bin/python -c "import duckdb;c=duckdb.connect('data/odds/odds_history.duckdb',read_only=True);print(c.execute(\"select season, snapshot_kind, book, count(distinct game_id||'/'||player_id||'/'||market) legs, count(distinct game_id) games from odds_history where season in (2023,2024) and book in ('pinnacle','draftkings') and market in ('player_points','player_rebounds','player_assists','player_threes') group by all order by all\").fetchall())"
```

**Forecasts.** Stored 2023-24 production forecasts exist at one information state only: `reports/odds_history/model_rows_{2023,2024}.parquet`,
monthly-block out-of-fold rows built with the T-60 injury report. No stored production forecast exists at a morning or a T-30/T-15
information state. The only other-lead forecasts offline are LINEUPS_KNOWN's T-30 arms on 2024 (T085, T522), which use box-score starters as
the "known lineup", a leak for this purpose (T522-a: the gain sits on late scratches the box score reveals). A morning replay would need
production rebuilt with the morning report (`nba.daily.replay_season`, seasons <= 2024; not costed here).

**The map.** The served map was fit on every 2023-24 t60 leg, so a 2023-24 backtest must use LINE_CALIBRATION's walk-forward month-block fits
(fit on t60 legs of earlier months, applied to slot-s lines of the scored month), never the served map. Season 2024 only (2023 has no earlier
fit rows). 2024 outcomes are exposed (T477, T493), so a backtest selects; it does not claim.

**Where a backtest flatters or misleads.**
- Morning (m360 as the proxy; the live morning snapshot is about 10:00 ET, often 8-9 h before tip; m360 is 6 h): pairing an m360 line with
  the stored T-60 forecast **flatters** the morning slot, because the forecast knows the T-60 report the live morning forecast does not.
- T-15 (t5 as the proxy): the line has absorbed lineup confirmations and late scratches; the stored forecast has not. For the production
  forecast this roughly matches the live situation (production does not read lineups), and is slightly **pessimistic** because t5 is later
  than T-15 and live pretip runs may add a later report. For any lineup-aware arm (`_t30`, `_t15`, `_late`) history has no confirmed
  lineups, so a backtest would either lack the information the live forecast has (pessimistic) or use box-score starters (a leak that
  flatters). Those arms carry no map today and are out of scope here.
- T-30: no history at all without the pull.

**Recommendation per slot (stage 1 = the backtest, stage 2 = sections 1-10).**
| slot | recommendation | stage-1 data | why |
|---|---|---|---|
| morning | **partial backtest**, then forward | m360 lines (held, if ingested) + T-60 forecasts, walk-forward maps, 2024 | cheap; flatters (forecast too informed), so a stage-1 fail is strong evidence and a pass is weak |
| t60 | **backtest exists** (T493, walk-forward, 2024); forward is LINE_CAL_LIVE plus this rule's replication | none new | nothing to add |
| t30 | **forward-only** | none (a pull costs 108,158 credits) | no forecast run serves T-30 today (tonight's export has no T-30 pairing); 108k credits for a slot nobody is shown is not worth it |
| t15 | **partial backtest via t5**, then forward | t5 lines (held) + T-60 forecasts, walk-forward maps, 2024 | the proxy is pessimistic for production; a pass is meaningful, a fail weaker |

**Stage-1 rule (frozen with this text if Devin accepts):** per slot (morning via m360, t15 via t5), Pinnacle primary, 2024, walk-forward
maps: pass = mean(y - CAL) CI inside [-0.03, +0.03] and Brier CAL - RAW <= -0.0015 with CI upper < 0; game-clustered, B 2,000, seed 0, date
CI beside; BH over the two slots for the Brier part. A slot that fails stage 1 does not enter stage 2 (its calibrated chance shows nothing at
that time, per Devin's rule); a slot that passes enters the forward look of sections 1-10; t60 and t30 enter stage 2 directly.
**Stage-1 gate:** BLOCKED until the count command above is run (I could not open `odds_history.duckdb`). Bar: >= 600 games and 15,000 legs
per slot in 2024. Arithmetic if m360 and t5 hold close to t60's 37,528 Pinnacle legs on 1,260 games: gap half-width about 1.96 x 0.5 /
sqrt(37,528) x sqrt(1.3) = 0.0058, far inside +-0.03; Brier half-width about half of T493's log-loss 0.0022, so 0.0011 and an 80%-power MDE
about 0.0016, at the floor. **Cost:** numpy on stored rows plus the slot lines, minutes on the Mac outside game windows, no credits unless
the pull is chosen. **2025 touch: not proposed** (LINE_CALIBRATION's 2025 stage was declined in favour of the live log, T493; this rule tests
the same map, which is not a never-evaluated model in spirit: its 2025 main-line behaviour was partly seen, T480).
**Devin's choice 5 (new):** *default: partial backtests for morning and t15, t30 forward-only, no pull.* Alternative: buy m30 and m15
(216,316 credits) for a full four-slot backtest. Answer: pending.

=== RESULTS BELOW ===

(none)
