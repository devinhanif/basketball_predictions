# Ledger audit, pass 2 (2026-10-12): everything pass 1 did not read

Scope: docs/reviews/*.md, docs/research/*.md, docs/FAN_KNOWLEDGE.md, docs/PROJECT_STATUS.md, docs/NEXT_SESSION.md, and the committed reports under
reports/ that carry no ledger row (lineups_known*, injury_elo_t30, model_miss_*, over_anatomy, paper_user, joint_atlas, odds_history, props_analysis,
lower_tail, minutes_v2, referees, parlay/*, market_shrink, decision_scoring, era_asymmetry, fan_answers). Pass 1 (ledger_audit_2026-10-12.md) is not repeated.
Read-only. No fit. Season 2025 not loaded. The only data touched: `data/lineups/lineups.duckdb` and `data/kalshi/raw` (read-only), and two counts on
`nba.duckdb` with `season <= 2023/2024` (N4). Scratch scripts are in the session scratchpad, not in the repo.

Key: (a) finding or next step written down and never turned into a rule or row; (b) component lacking information another component has;
(c) question to Devin never answered; (d) loose end opening night (2026-10-20) depends on.

## Items, most valuable first

### N1 (d) The Mac sleeps on game days; the setting is `sleep 1`
- Source: docs/NEXT_SESSION.md "Before opening night" item 6 and PROJECT_STATUS section 7 ("Mac must not sleep"); docs/reviews/live_path_review_2026-10-09.md "Not verified" (no caffeinate/pmset in install_launchd.sh).
- Evidence (this run): `pmset -g` shows `sleep 1 (sleep prevented by caffeinate, Claude, powerd)`, i.e. system sleep after 1 minute, held awake only by this session's assertions. `pmset -g log` on 2026-10-11: Maintenance Sleep at 19:50:40 and 20:06:16 CDT "Using Batt (Charge:18%)". data/ops/ALERTS.md the same evening: "lineups stale" and "kalshi-snapshot stale" both at 19:50 (last start 18:51 for each, the signature of one sleep). The "daily-pretip stale" alerts at 21:48 and 22:18 predate the watchdog-zone fix (a100006, 23:21) and may be that bug, so they are not counted as evidence. `ops/install_launchd.sh` has no caffeinate or pmset; only the ingest scripts use `caffeinate -i`.
- Why: a sleeping Mac loses T-30 snapshots (5-minute poll), Kalshi snapshots and pretip rows, none recoverable. Also on battery at 18%.
- Cheapest honest next step: Devin (needs sudo) `sudo pmset -a sleep 0` (or `pmset -c sleep 0` and keep the charger in) from 2026-10-19, or wrap each launchd job in `caffeinate -s`. Check `pmset -g | grep sleep` on 10-19.
- Venue: Devin / steward, ops. No rule.

### N2 (b, d) The T-30 arm will see about one game in five, not most games; a sibling arm must exist before the first 2026-27 prediction
- Source: docs/FORWARD_PREREG_2026_27.md section 1 item 9 ("Frozen arms ... any other change creates a new model_name with fresh counters") and section 2 (population = games with a logged T-30 decision); docs/reviews/phase2_cleanup_review_2026-10-11.md table "Why the other skips happen"; nba/daily/t30.py (cutoff is `snapshot_fetched_at < tip - 30`).
- Evidence (this run, `data/lineups/lineups.duckdb`, 13 preseason games, 2026-10-09..11, 5-minute polling, tips from status text): minutes before tip at which each team first shows 5 Confirmed announced starters (the corrected F1 rule: a Confirmed row with `announced_starter`):
  0002 MIA 79 / MIN 18; 0006 14 / 14; 0013 18 / 18; 0014 GSW 88 / SAC 21; 0015 26 / 26; 0037 18 / 18; 0038 41 / 41; 0039 20 / 20; 0040 11 / 21; 0041 42 / 53.
  Both fives confirmed at least 30 minutes before tip: 2 of 10 games (0038, 0041). At least 15 minutes: 8 of 10. This agrees with the 9 skips plus 1 log in `forward_t30_decisions` once the 0041 false skip is corrected.
- Why: (i) the T30 paired population is the early-announcing 20% of games (the preregistration reports the skipped share but does not say the premise "the five is known at T-30" holds for a minority); (ii) the value to a bettor lives at T-15..T-5, when 80% are known; (iii) arms freeze at the first 2026-27 prediction, so a T-15 sibling created in November has half a season of counters and cannot be compared on the same dates.
- Caveats: preseason, 10 games, one feed, fetched_at not source_ts, 5-minute grain; regular-season announcement habits may differ. The number to take from this is "check before 10-20", not "20%".
- Cheapest honest next step: before 2026-10-20, a companion rule (new id, not an edit of FORWARD_PREREG) for `props_context_residual_t15` (same code, cutoff `snapshot_fetched_at < tip - 15`, own counters, not in the m=9 family, descriptive until a look is declared); and log the first-Confirmed-five lead time per team-game as a column (already derivable from the store).
- Venue: modeler drafts the rule; steward wires the cutoff; main session commits. Nothing to touch the existing arm.

### N3 (b) Kalshi's DNP rule is in our stored market metadata and matches neither engine policy
- Source: docs/PARLAY_ENGINE.md:15-17 ("Kalshi's real rule is unconfirmed"; policies `void` default, `loss`); docs/KALSHI_READINESS_2026-10-09.md:180,191; nba/parlay/joint.py `effective_prob` (void/loss only).
- Evidence: `data/kalshi/raw/kalshi-markets/hist:KXNBAPTS:first:100.json`, field `rules_secondary` of the first market: "If a player is active but never takes the court, the market settles to the last fair market price before game start. Once a player enters the game the market settles based on the player's points recorded." grep for "last fair" / "never takes the court" / "rules_secondary" in docs/, nba/, configs/, reports/ finds nothing. Inactive (not active) players are not covered by that sentence.
- Why: every parlay EV in `nba.parlay evaluate` and props_analysis assumes either a refund at cost (`void`, default, the optimistic one) or a full loss. An active DNP settles at the market's own last price, so the DNP leg's EV is (last fair - ask), about the spread, not zero and not a loss. The shadow log settles DNPs from the box score (KALSHI_READINESS:191 already warns the two can disagree).
- Cheapest honest next step: record the text in PARLAY_ENGINE.md; add a third policy `last_price` (payoff on DNP = mid before tip) and make it the default for Kalshi; confirm the inactive case from one live prop series on 10-20 (`rules_secondary` is in every market object we store).
- Venue: steward (nba/parlay is not modeler territory). No fit needed.

### N4 (b, d) The parlay slate assigns players to their last-game team and drops everyone else: about a quarter of opening-week rotation players
- Source: nba/parlay/slate.py:78 (`latest_team` = team of the player's last game before the slate) and :103 (`if t not in (home, away): continue`, silently dropping the player's distributions). The forward prop rows come from the official roster, which knows the current team.
- Evidence (nba.duckdb, read-only, seasons 2023 and 2024 only, players with >= 10 minutes in the first seven days of the regular season, first appearance of the season): 2023-24: 310 players, 20 with no earlier history (rookies), 65 whose first-game team differs from the last earlier team. 2024-25: 308, 23, 56. So 27% and 26% of opening-week rotation players would be absent from the parlay board (or, when their old team is the opponent, placed on the wrong side with the margin correlation sign flipped) until they have played one game. The 2026 summer moves in docs/research/rivalries_2026-10-09.md (LeBron PHI, Giannis MIA, unverified there) are the same class.
- Why: opening night and the first week are exactly when the parlay shadow log and the first Kalshi prop markets start; the shadow log would be thin and non-random (movers and rookies missing).
- Cheapest honest next step: take `team_of` from the forward row (it carries the roster team) instead of `latest_team`; test on the 2024 replay oracle slate (golden hash does not cover parlay).
- Venue: steward; a 10-line change inside the shadow path.

### N5 (b) The parlay/joint engine reads only the T-60 production row; it never sees T-30, the lower-tail mixture, or p_play_r
- Source: nba/parlay/slate.py:22 (`PROPS_MODEL = "props_context_residual"`), :65 (`elif model == PROPS_MODEL`), :60 (later `made_at` overwrites earlier); nba/parlay/independence_check.py:47.
- Evidence: the shadow arms (`_t30`, `_lt`, `_int`) and the frozen PPLAY shadow write other model_names, which `load_slate` ignores. PROJECT_STATUS section 3 lists T-30 as the one lever the data keeps pointing to.
- Why: the parlay shadow log is the only place the live system meets prices, and it prices with the least informed forecast. The market-skill gate (N6) therefore measures the weakest model, and a T-30-informed leg price can never be checked against a Kalshi quote.
- Cheapest honest next step: no code before 10-20. Declare, in the parlay shadow documentation, that skill-vs-market is T-60-only; after the T30 arm has 60 dates, add a second shadow track using `_t30` rows with `made_at` after T-30 (same schema), labelled separately.
- Venue: steward + modeler (a future companion rule).

### N6 (b) The market-as-prior gate is a point estimate at n = 30, and `calibration_logit_sd = 0.15` is far below the measured tail error
- Source: nba/parlay/ev.py:65-74 (`shrink_to_market`, weight n/(n+k) once `skill > 0`); nba/parlay/shadow.py:106-113 (`gated_skill` uses the point estimate after 14 dates); configs/parlay.yaml:24 (`calibration_logit_sd: 0.15`), :27 (`k: 100`), :31 (`min_settled_for_claims: 30`).
- Evidence: skill = 1 - Brier_model / Brier_market with no interval. ODDS_HISTORY and the confusion/decision-scoring reports show the model is worse than the market in every stat and book (Brier +0.005 to +0.008, CIs above 0). With 30 rows the sampling noise of that ratio is far larger than the true gap; assuming a per-row paired Brier SD of about 0.1 (assumed, not measured), SE is about 0.018, so P(skill > 0) at n = 30 is roughly one in three when the truth is -0.005 to -0.008, and the model would then get weight 30/130 = 0.23. The confidence error is systematic, not noise: FAN_KNOWLEDGE (the "can the model say no" entry) records a model claim of 80-95% being right 54-63%, and 90-95% claimed right 54% (n 48): a logit gap of 0.6 to 2.3, against an added symmetric logit sd of 0.15 in the interval.
- Why: the gate that decides whether the product may ever say "positive EV" can open by luck; the "conservative bound" is not conservative for the legs that rank highest (the most over-confident bin).
- Cheapest honest next step: open the gate on the lower bound of a date-clustered CI of the paired Brier (or log loss) difference, not the point estimate; widen or replace the interval for legs above 70% claimed with the measured shrink. Both are config and a few lines; no new data. (DECISIONS wording to draft: "gate on the CI, not the point".)
- Venue: steward for code; modeler drafts the frozen gate wording; Devin approves because it sits on the "what should I bet" answer.

### N7 (a) Two reviews asked for a label-group NULL check in `datamanifest check`; it was never built
- Source: docs/reviews/leak_audit_2026-10-09.md "Proposals" (second bullet) and redteam_ctxres_v3_leakfree_2026-10-09.md recommendation 5; nba/datamanifest/diff.py:12-30 (`leak_candidates` tests played vs unplayed NULL rate only).
- Evidence: the ridge leak (NULL iff tonight's minutes < 5) passed the label view at the 0.2 threshold because only 8% of rows were NULL (label gaps 0.13-0.17); both reviews proposed a standardised NULL-vs-label gap at about 0.1. data_audit_2026-10-11 section 3b reports only the minutes-bucket scan. Pass 1 lists the harness defects; this is the guard that would have caught the original leak, and no ledger row or DECISIONS row records that it was declined or deferred.
- Why: CLAUDE.md names the NULL-pattern leak as the one that slipped through three experiments; every new frame (RECENT_ROLE columns, PPLAY shadow, WORKLOAD) is checked by per-prereg missingness tests, but the shared tool still has only one of the two checks.
- Cheapest honest next step: add the label-group check to `leak_candidates` (needs one label column per file in `configs/datamanifest.yaml`), with the ridge parquet as its planted positive test.
- Venue: archivist (nba/datamanifest); modeler supplies the planted case.

### N8 (a) F10A decision "rivalry flags go into the 2026-27 P(play) shadow log" has no implementation
- Source: docs/DECISIONS.md:681-685 (2026-10-11 23:25, "option 1"); docs/research/rivalries_2026-10-09.md line 3 (Tier 1 and 2 frozen Y by Devin, 22:58).
- Evidence: grep -i rival under nba/, configs/ and ops/ finds nothing; nba/daily/pplay_shadow has no pair column; docs/prereg/PPLAY_INTO_PROPS_LIVE.md (frozen 3a305482) lists no rivalry column. The Tier 1/2 pair list is prose in a markdown table.
- Why: the decision reopens F10A at "300+ forward division non-play events under a new rule", but the list as a machine-readable, hash-frozen file does not exist, and a flag computed after the fact from a mutable list invites the very choice-after-results the freeze was for. The flag is static per team pair, so it can be joined retroactively only if the list is frozen now.
- Cheapest honest next step: commit `configs/rivalries_2026-27.yaml` (pairs, strength, source row) with its sha256 in DECISIONS, before the first game; add the join as a column in the shadow's feature builder (no model use).
- Venue: modeler writes the yaml from the Y rows; steward adds the column; main session commits.

### N9 (a) "Returning teammate costs the star about 1.2 points" was left as "a candidate column for the T-30 arm" with nothing logged
- Source: docs/FAN_KNOWLEDGE.md:509 ("NOT KNOWABLE AT T-60 ... the information arrives with the active list at T-30"; the T-60 flag fires 4,345 times but the flagged player plays 16.5%); reports/model_miss_questions_2026-10-10.md Q6 (n 685, -1.17, diff -1.37 [-2.00, -0.70]); Devin answered yes (FAN_KNOWLEDGE "Devin's answers", item 1).
- Evidence: nba/daily/t30.py and nba/props/lineup_features.py contain no returning-teammate feature or column (grep "returning|ret_" finds none). The T30 arm is frozen, so adding the feature there is a new arm.
- Why: this is the one confirmed-by-Devin, effect-sized (about a point on stars) lever whose information first exists in the data the collector is already writing; with no logged flag, the only test next season is a retrospective one.
- Cheapest honest next step: no model change. Log, per T-30 decision, the pre-tip definition from FAN_KNOWLEDGE (rotation teammate, >= 3 missed team games, listed active at T-30) as a descriptive column; draft the rule (star residual vs the T30 forecast, floor 0.5 pts, date-clustered CI, n sized from the 685) for a look in December.
- Venue: modeler drafts; steward logs the column.

### N10 (c, d) Live odds capture and REB_LIVE depend on a plan that DECISIONS says Devin will cancel
- Source: docs/DECISIONS.md "(7) the-odds-api.com plan: Devin cancels after the month" (2026-10-11 ~00:50) against DECISIONS 2026-10-11 23:05 (12-point schedule, 168 credits a game, "START 100K $59/month is the smallest that fits") and docs/prereg/REB_LIVE_2026_27.md:74-77 (needs `THE_ODDS_API_KEY`, G-FREEZE about 2026-11-04, n >= 5,000 bets over the season); PROJECT_STATUS section 3 says the live benchmark is Kalshi only.
- Evidence: three docs describe three states (cancelled; kept on a smaller plan; required for REB_LIVE). configs/cost.yaml has no line for the odds vendor, so the cost monitor cannot see it. Kalshi has listed player props only from 2025-12-25 and not on 2025-10-21 (historical_odds_2026-10-09.md row "Kalshi (free)"); KALSHI_LIVE_2026-10-08.md:45-47 says props appear game day close to tip. With neither feed there is no live props price for any arm.
- Why: REB_LIVE and the paper "edge vs market" question both end the day the key stops; the plan's month ends around 2026-11-10 (history pull 2026-10-10).
- Cheapest honest next step: Devin decides before 2026-11-04: keep a START plan through the season (about $59 a month, his money) or retire REB_LIVE and say so in DECISIONS; add the vendor line to configs/cost.yaml. On 10-20, count the Kalshi prop markets listed and write the number in the nightly check.
- Venue: Devin (money).

### N11 (a) Red-team recommendation 4 (the 437 missing team sections in the injury-report backfill) was never run, and the parser drops "Not Yet Submitted" teams
- Source: docs/reviews/redteam_production_2026-10-09.md:150 ("Investigate the 437 missing team sections ... then rerun both models' evals") and section 3 item 1 (36% of rotation DNPs on no snapshot); nba/parse/availability.py:518-520 and :817 (placeholder rows are skipped entirely); docs/PROJECT_STATUS.md section 5 (still "backfill gap").
- Evidence: no document after 2026-10-09 mentions the 437 or a per-team presence check (grep "team section" and "437" in DATA_CHANGELOG, data_audit, DECISIONS). `player_availability` has no team column, so "this team did not file" and "this team filed with nobody listed" are indistinguishable downstream. A crude proxy count on nba.duckdb (2022-24 regular season, 3,690 games, team from each player's last prior game): 3,687 games have report rows, 126 (3.4%) have rows for only one side; healthy teams legitimately have none, so this is an upper bound on defects, not a defect rate. fan_answers B counts 14% of rotation DNPs as "not on the report at all".
- Why: the injury report is the information both production wins came from; the live model cannot tell a missing team section from a clean one, in training or at serve time.
- Cheapest honest next step: store the set of team abbreviations present per snapshot (the PDF has a team column) and a flag `team_filed`; measure the 437 DNPs against it (descriptive, no 2025). Only then decide whether a feature is worth a rule.
- Venue: archivist for the table; modeler for the descriptive.

### N12 (a) Train/serve skew in injury-report freshness was flagged and never measured
- Source: docs/reviews/redteam_production_2026-10-09.md section 3 item 2 ("Watch forward calibration"); data_audit 5d (82% of stored snapshots at :00, 18% at :45; two scrape slots a day in the backfill); docs/FORWARD_PREREG_2026_27.md:69 (declared slices: starter vs bench, teammate-out report status, first 15 games, home/away; no report-age slice).
- Evidence: models were fit on at most two snapshots per day; the live puller sees them roughly every 15 minutes up to tip - 60. The review called the direction conservative; nobody checked.
- Why: a feature distribution (`n_out_rot`, `vac_*`, injury-Elo `d_out`) that shifts at serve time can move calibration without any change in skill, and would be read as model decay.
- Cheapest honest next step: add "age of the report row used, in hours" as a descriptive slice to the first checkpoint tables (rows already carry made_at and the used snapshot `as_of`); no rule.
- Venue: steward (report), modeler (reading).

### N13 (a) Count-stat upper tails are too fat in the model; only points were ever tested for tails
- Source: reports/over_anatomy_2026-10-11.md line 9 (realised/predicted P(y > line+k): ast 0.85, 0.72, 0.55 at k = 4, 6, 8; reb 0.90, 0.83 at k = 6, 8; fg3m 0.88, 0.76 at k = 3, 4); ledger T129-T135 (PTS_TAIL, pts only); FAN_KNOWLEDGE "can the model confidently say no".
- Evidence: the table above, n about 30k per stat, game-clustered CIs in the report; no rule or ledger row addresses reb/ast/fg3m upper thresholds. Kalshi props are "N+" ladders, so the far thresholds are exactly the contracts it lists; the joint engine reads them from the same grid.
- Why: pass 1 covers the lower tail and fg3m P(>=1); the upper tail error is the same grid, opposite sign, and it flatters every high-N parlay leg.
- Cheapest honest next step: one descriptive table of threshold log loss and realised/predicted at far thresholds on 2023/2024 OOF (already stored), then a one-line pre-registration if the gap survives a placebo (CALIBRATED_GRID says a shuffled control does as well, so the placebo must be a threshold-matched one).
- Venue: modeler, 2023 select / 2024 report, no 2025 touch.

### N14 (c) Open candidate questions to Devin that have no answer and no rule (list)
1. "Late scratches after T-30" and "road stars +0.36 (away +0.25 vs home -0.11)": two of the six questions in docs/MORNING_BRIEFING_2026-10-11.md:28-29; FAN_KNOWLEDGE:556-566 headed "answers to the six questions" lists five answers and neither of these. The first sets where the T-30 gain sits between the red team's bounds (redteam_lineups_known: 1,519 game-time decisions resolved after T-60).
2. model_miss_questions Q7.3 (widen the minutes spread for 24-30 minute players and underdogs: short rate 3.2% vs 1.1%, 2.6% vs 1.7%), Q8.2 (deep-bench pts forecast 0.32 high, CI [-0.40, -0.23], n 13,559, "pre-register as a one-line fix") and Q10.3 (bench ceiling lines, n 331). Devin's 2026-10-10 answers cover Q6, Q7 and Q9 (both turned into a request for named examples), Q8 item 1 (deep-bench minutes) and Q10 item 6 only.
3. The monthly dollar cap for the LLM shadow (configs/llm_shadow.yaml `MONTHLY_CAP_USD: null`; docs/prereg/LLM_SHADOW_2026_27.md:11 still `<<DEVIN FILLS>>`).
4. `NBA_FACTS_NIGHTLY=1` in the launchd environment (ops/nba_daily.sh:139-145; briefing "Needs you" 1). Unset, the explainer's "what was known and when" has no 2026-27 report, lineup or box facts.
5. The X sign-up: docs/MORNING_BRIEFING_2026-10-11.md:30 says "stays skipped per your word"; docs/PROJECT_STATUS.md:103 and NEXT_SESSION still list it as open. Minutes restrictions (Devin's own lever) therefore have no live source on 10-20.
6. Hardening review m4 (official roster cached per date: a 10:30 ET fetch serves the whole day, including waivers on cut-down day) and the 2026-11-03 switch from `official` to `recent` rosters: "accept or key by hour" and "keep or switch" were never decided. OPENING_WEEK_ROSTERS.md measured only 14 days (official 0.967 coverage vs recent 0.842 over days 1-14); nothing measures the difference after day 14, and the switch changes the population of every arm mid-test on 11-04.
7. data_audit F3: only 1,200 of 1,230 2026-27 tip rows exist (ids 1201-1230 absent), skipped by instruction; the "re-check weekly" safe default has no owner or schedule.

### N15 (d) Smaller opening-night loose ends
- Dirty tree runs live: docs/reviews/hardening_review_2026-10-09.md M-C. nba/registry/metadata.py:60-64 and nba/daily/checkpoint.py:623-630 record `git rev-parse HEAD` only; git status now shows nba/daily/{__main__,pipeline,report}.py modified and uncommitted while launchd executes the working tree. Fix: record a dirty flag in the checkpoint snapshot and commit before 2026-10-18 (the planned hands-off window).
- `ReportTriggerConfig` still defaults to `tip_source="proxy19"` with the docstring "default, production" (nba/features/injury_report.py:77-81); phase2_cleanup_review recommended changing it and the red team asked the same on 10-09. Live paths force `real` through `serve_pretip_flagged`; research harnesses that use the default admit post-tip reports (research/props/context_features_v2.py:863, research/features/game_sets.py:411, research/eval/usage_redistribution_eval.py:400). One-line fix in nba/features (modeler territory) plus a test.
- The PPLAY shadow runs `predict_slate_context` a second time inside the pretip lock (nba/daily/pipeline.py, `_pplay_shadow`, uncommitted); hardening review M-B showed the :20/:50 pretip already collides with T-30 ticks. Measure the added seconds on the supervised run before it is switched on; the replay oracle cannot see lock contention.
- Stale living docs: PROJECT_STATUS header says T001-T314 and 2026-10-11 04:00; NEXT_SESSION roadmap item 5 ("Platt P(play) forward check") refers to a layer switched off on 10-11; docs/research/README.md log lists 2 of the 5 notes in the folder (harness paper, knowledge graph, rivalries missing).
- Fan-hypothesis queue with no ledger or prereg row at all (grep of TEST_LEDGER and docs/prereg): F3 (absence length for the vacated-stats feature), F4 (FT% as a shot-diet tell), F5 (role-growth slope for players under four seasons; the HOU observations), F16 (season stakes), F18 (trade reason). F1 (foul trouble) was closed descriptively by Q7 (7 of 581 short nights have 5 fouls) but not recorded as closed. F7 (playoffs differ) is covered by pass 1's postseason route.

## Checked, nothing new

- Phase 2 F1 (view vs t30 lineup dedupe): fixed in c53cc31; the preseason numbers in N2 use the corrected rule.
- Live-path review B1, M1-M7, m1-m3, m5-m7 and the hardening review M-A, M-B, m-1..m-13: resolved or documented as follow-ups in docs/DAILY_PIPELINE.md (probe tri-state, overlap section, rc codes); not re-opened. Remaining follow-ups there are listed in N14.6 and N15.
- redteam_lineups_known: bounds restated in the ledger (pass 1); its recommendation 3 (record announcement time and inactive time) is what the collector does; recommendation 4 became OWN_TAG (null).
- redteam_ctxres_v3: integer support became the shadow arm, the schedule look-ahead was fixed (c9b00d1), recommendation 5 is N7.
- redteam_odds_history: the intercept-only correction was tested as MARKET_SHRINK (T415-T419, no pass); the reb blend became REB_LIVE (see N10 for its dependency).
- stint_rebuild, stint_reconciliation, possession_order, data_audit F2/F4/F6-F12: rebuilt and gated; F5 coach table waits on a fetch the sandbox refused (documented, research-only readers).
- alias_review_steals: no steals model exists; the two names left open (J. Green, Thompson) are already in PROJECT_STATUS section 6.
- import_graph, agents_2026-10-10, replay_oracle: consistent with the layering test and the current golden 89889f6d; the oracle replays 20 dates of season 2025 and cannot exercise T-30, facts, reporter or the PPLAY shadow, as already stated there.
- docs/research: harness paper (no hypothesis, practice only), knowledge graph (scaffolding, built as the facts store), historical odds (executed), rivalries (only N8 is open).
- joint_atlas vs the copula: `_pair_rho` (nba/parlay/joint.py:190) uses one `own_margin` rho per stat with the sign of home/away. The atlas finds underdog-team player overs vs "favourite covers" at ratio 0.82 and favourite-team players near 1.0-1.09. A sign-flipped single rho implies a symmetric pair (+x favourite, -x underdog), which is not contradicted by those numbers (CIs touch 1 on the favourite side); no mismatch demonstrated, so not listed.
- injury_elo_t30, minutes_v2, lower_tail, referees, era_asymmetry, lineups_known design note: closed or already in pass 1; era_asymmetry feeds HISTORY_WINDOW_3.
- over_anatomy part 1, paper_user, decision_scoring, confusion matrices: descriptive and labelled so; the under lean they find is the market-wide price bias pass 1 dismissed.
- FAN_KNOWLEDGE rounds 1-9 hypotheses: each is either a ledger row (F8, F9b, F11, FOUR_FACTORS, PTS_COMPONENTS, MINUTES_HAZARD, PPLAY_CONTEXT, OWN_TAG, MODEL_CLV, GAME_MARKET_CONTEXT, XGB) or in N14/N15.
- Kalshi snapshot "unknown_team_abbr: 48" in reports/props_analysis/2026-10-11.md: the unmatched events are exhibition games against non-NBA clubs (e.g. LIOPOR); benign.

## Not verified

- The N2 lead times are from three preseason days at a 5-minute poll; regular-season behaviour is untested.
- Whether Kalshi settles an INACTIVE player's market at the last price, voids it, or resolves No (the stored text covers only "active but never takes the court").
- The assumed per-row Brier SD in N6.
- The N11 proxy count uses each player's last prior team and over-counts healthy teams; it is an upper bound.
- Anything on the VM, the X API, the LLM shadow path, and the facts store contents.
