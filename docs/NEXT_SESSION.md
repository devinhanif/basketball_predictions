# NEXT SESSION, resume here (written 2026-10-25 ~16:30 CDT; fresh session strongly recommended: this one ran ~2 days)

**Phone summary for Devin:**
1. **JARVIS is merged (paper, Discord).** Install when ready, from the repo folder (paste your Discord webhook when asked; it never enters the repo):
   `uv run python -m nba.jarvis test-send --dry-run` then
   `read -rs "JARVIS_DISCORD_WEBHOOK?webhook URL: "; export JARVIS_DISCORD_WEBHOOK; echo; cp ops/launchd/local.nba.jarvis.plist ~/Library/LaunchAgents/ && plutil -replace EnvironmentVariables.JARVIS_DISCORD_WEBHOOK -string "$JARVIS_DISCORD_WEBHOOK" ~/Library/LaunchAgents/local.nba.jarvis.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.nba.jarvis.plist; unset JARVIS_DISCORD_WEBHOOK`
   Update `balance.current_usd` in configs/jarvis.yaml when money moves. Rules: docs/TRADING_CONTEXT.md.
2. **Fast-arb checker MERGED (not installed, not wired).** To turn on: create `.env.fastarb` in the repo folder containing only `THE_ODDS_API_KEY=...`, then `cp ops/launchd/local.nba.fastarb.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.nba.fastarb.plist`. Alerts land in data/ops/ALERTS.md; wiring `confirmed_quotes` into JARVIS QUOTE_SOURCES (so JARVIS can say "strong"/"arbitrage" in Discord) is a separate reviewed step. Read Kalshi + Polymarket US settlement rules before trusting any arbitrage.
   **JARVIS live in-game alerts are merged and running** (BENCHED only Q4 <= 6:00, "may be done", corrections; recap counts how often it was wrong).
   **To-do for JARVIS:** warn on exits when the bid is far below fair (Devin's accidental $0 cash-out, 10-25).
3. **Installed today:** live-capture launcher (runs nightly by itself). Mac must stay on AC on game days (it hibernated 12:13-13:48 on 10-25).
4. **Results:** 10-24 card 0/5; your 46c GSW@PHX combo won. 10-25 bets: tell me fills to update the balance.
5. **Your reads to do:** Kalshi + Polymarket US settlement rules (postponement/OT/DNP) before trusting any cross-venue "arbitrage"; Underdog payout table.
6. **Cleanup (permission-blocked for Claude):** `rm -rf data/rehearsal/oracle_{K,M,M2}_branch* data/rehearsal/oracle_{kgap,fzbool,launch1024,stints,stints2,jarvis,excl}_202610*` ; `rm data/ops/pretip_2026102{1,2,3}.log` ; delete merged remote branches.

# NEXT SESSION, resume here (written 2026-10-24 01:45 CDT, end of overnight "do everything"; fresh session recommended)

**Phone summary for Devin:**
1. **First:** Mac on AC + `caffeinate -dimsu` (10-23's capture was lost to clamshell sleep). First tip today 17:00 CDT, pretip ~15:45. The **in-game capture still starts by hand** tonight (`nba.live` is not on main; see 4c).
2. **Check the 07:00 job** (data/ops/ALERTS.md, morning log): the morning fixes merged at 01:1x (oracle IDENTICAL). Expect hustle/officials/matchups to load for 2026 for the first time and no fetch timeouts. Five old keys may now report "stale pending" and exit 1 (logged; benign if so).
3. **Frozen (paper, forward):** MARKET_ANCHORED_PROPS f7691bbf, T510b 68f0ef84, KALSHI_LONGSHOT_FORWARD 0b50d17e, UNDERS_FORWARD 149be979 (your "gi" read as go).
4. **Merged overnight:** watchdog pre-tip alerts; Kalshi names 196/196 matched; morning-job fixes; scorers for LONGSHOT+UNDERS and T510+T510b; MAP eval harness pinned (eb683cd0). Every one passed adversary review (2-3 rounds each; each round found real defects).
5. **Your decisions (defaults in brackets):**
   (a) **MAP real run** [go, any time outside 07:00-09:00 and pretip; ~6 min compute, takes heavy.lock; command in DECISIONS 569d3cd]
   (b) **T510 drift x lean:** the model leans one way, so a market-wide pre-tip drift reads as "movement toward the model" (synthetic: passes 100%) [adopt reading note D6: judge any PASS with the per-sign split; no new rule]
   (c) **live capture:** merge `nba.live` (agent/market-paper-trader) then install the launcher (agent/steward-live-capture-launchd 23577a1) [yes; the 4 frozen rules need nightly capture, LONGSHOT/UNDERS >= 72% of nights]
   (d) pin aliases 4d176c53 for T510 [yes]; spot-check 8 rookie ids (list in DECISIONS dc8200a); remove the 0.84 fuzzy name accept [yes]
   (e) pre-look interpretation rows: LONGSHOT/UNDERS I-1..I-6, T510 D1-D8, MAP section 9 (all drafted in docs/reviews/*_2026-10-24.md) [approve]
   (f) wire stale-price exclusions into scorecard/monitor [yes]; fix nba/parlay/config.py `bool("false")` [yes]
   (g) cleanup (blocked for Claude by the auto-mode classifier): delete 8 merged remote branches (modeler-market-anchored-props, modeler-t510b-dnp-fair, market-kalshi-longshot-fwd, market-unders-forward, steward-kalshi-snapshot, market-kalshi-unmatched, market-price-runs-scratch, market-longshot-unders-scorer; plus market-t510-scorer, market-t510-r5, steward-morning-fixes, modeler-map-eval); delete oracle copies data/rehearsal/oracle_{K,M,M2}_branch.duckdb; compact the 10 GB / 9.4 GB / 0.5 GB pretip logs.
6. **Answers:** no bet last night (488/488 no edge). Kalshi price runs exist (in-game 15-min momentum) but lose 4-5c/trade after costs. Retro: docs/reviews/retro_2026-10-24.md.

# NEXT SESSION, resume here (written 2026-10-23 21:44 CDT, night 4; fresh session recommended)

**Phone summary for Devin:**
1. **Money:** MIN@ORL 5-leg bought $3.48, cashed $3.91 at halftime (+$0.43); it settled LOST, so the sale saved $3.91. Balance reported ~$30 before the sale (~$33.91 after; confirm, and whether it includes bonus). Recommended NO bets (GSW >16.5, POR >22.5, Luka 5+ 3s, Castle 6+ reb, Castle 2+ 3s): Devin never confirmed fills; Castle 2+ 3s NO lost (2-4 by Q2). Score the rest from the box in the morning.
2. **Strategy v2 (memory bankroll-rules):** $20/night cap, 50/50 steady/swing, Claude gives exact bets, stay the course, floor SOFT (warn). EV-first, misses fine (memory ev-first-not-hit-rate). Every stake now goes through the Bayesian-Kelly sizer; quote Pinnacle, not raw model.
3. **Your decisions waiting:** (a) freeze MARKET_ANCHORED_PROPS (77814a2, gate 23/23) after a second adversary glance; (b) modeler agent default to Opus (or per-draft Opus); (c) "set up JARVIS" (morning brief + pre-tip picks as phone notifications); (d) T510b (DNP settles at fair price on Kalshi, frozen T510 counts it as No); (e) Kalshi long-shot study: one logged holdout touch vs forward-only.
4. **Running at write time (check their hand-backs / logs):** steward: roster-bug fix (agent/steward-roster-unlisted, needs adversary review before merge) + oracle/merge of the Bayesian-Kelly sizer (agent/market-bayes-kelly dce13b8); market: SPREAD_TAIL_NO draft (agent/market-spread-tail-no); market: CALIBRATION_EVERYWHERE plan (agent/market-calibration-everywhere); odds chain (movement 2024 -> 2023 at m360/m180/m90, then 2022 games; logs data/odds/history_pull_*; ~720k credits cap); bet watcher (scratchpad bet_watch.py).
5. **Tonight's research verdicts:** surfer loses (-4.2c/trade); rotation depth small, live miss mostly the roster bug; team rating unscored input (P(pass) 0.03); ROLE_CHANGEPOINT parked; movement phase merged (ed73542); 2022 season + 2.25M cap in odds config (2f3368f). Retro: docs/reviews/retro_2026-10-23.md. New hook blocks guessed DECISIONS times (09bc4d3).
6. **Morning:** 07:00 job settles tonight; Mac must be on AC (it was on battery all day); check data/ops/ALERTS.md for stats.nba.com DNS failures (today's predict rc=5 DEGRADED) and the kalshi-snapshot job (exit 1 since 09:21); pretip log self-copy bloat (13 MB) is an open steward item.

# NEXT SESSION, resume here (written 2026-10-22 ~23:30 CDT, end of night 3)

**Phone summary for Devin:**
1. **Tonight (your money):** CLE@PHI final PHI 114-106 (comeback from 24 down); DEN@OKC final OKC 96-84. All combos lost (4-leg: Harden 3 reb; linked: CLE leg; Cavs roll: CLE leg; OKC control: SGA 17; P2 Jokic: 13 pts/7 reb; P1: SGA 30+). Ride alerts both won (PHI 98.6%, OKC 98.1%). Two Claude process misses (late-Q3 check never set; mid-Q4 timer broken). Update your balance in chat so the scorecard starts.
2. **Your decisions waiting:** (a) "go" to freeze SOFTBOOK_VS_PINNACLE_HIST (T516 Stage A) after adversary review; (b) "wire it": accuracy league table into the nightly job (replay oracle + your go); (c) in-game v2 seven choices ("defaults"); (d) SEASON_BOUNDARY choices (T515, defaults listed); (e) whether to merge the app Bankroll screen (agent/app-analysis-studio 663fc26).
3. **Frozen today (forward, paper):** T510 LINE_MOVE_TOWARD_MODEL, T511 FAV_DIP_PAPER, T514 STAR_FLOOR_PAPER. Drafts: T513 INGAME_WINPROB_V2 (ready to freeze on your choices), T515 SEASON_BOUNDARY (+minutes, + roster-continuity slice), T516 KALSHI_VS_PINNACLE (Stage A historical is the real test; Stage B forward unpowered). Gate stops: T509 TEAM_USAGE_CAP, T512 COLDSTART_SHAREBALL.
4. **Money rules (memory bankroll-rules):** floor $25 x 1.01^months (compounding); risk capital above it; daily 25% budget; SAFE/MEDIUM/RISKY tiers; bonus spends first; Claude recommends, you place every order. Robotrader path: paper -> one-tap -> capped auto (DECISIONS 21:0x).
5. **Tomorrow first:** NBA transaction-log ToS check + pull (out-of-league research, docs/research/out_of_league_effect_2026-10-22.md); score your reads (data/manual/devin_reads.jsonl, tell Claude your calls); every in-game check Claude promises gets a TESTED timer (ESPN summary status has no clock field; use the scoreboard's status.displayClock/period).
6. **Running in background now:** live capture (ends 05:30Z), paper trader + playbook arm (settle at end). Ride watcher exited cleanly. Claude restarts nothing tonight.
Recommend a fresh Claude session tomorrow (this one ran ~13 h with heavy context).

# NEXT SESSION, resume here (written 2026-10-21 ~23:35 CDT; overnight work in progress, see the status lines at the bottom of this block)

**Phone summary for Devin:**
1. **Tonight, real money (your decisions):** about +$10.5 on ~$21 across five Kalshi combos. 6-leg cashed $7.19 (Miller missed 20, holding paid $0); 8-leg cashed $3.91; 4-leg won $6.04; Lakers 3-leg won $12.75; 7-leg (Grimes three) open at write time. Pelicans moneyline lost (model's top game edge; Pacers won). Playbook logged in FAN_KNOWLEDGE.
2. **Your decisions waiting:** (a) reply "freeze T507" to freeze ARCHETYPE_MATCHUP_ALL (adversary READY_TO_FREEZE after four rounds; text f10ed5b1, harness 63b3d9b0; 5 defaults listed in DECISIONS/T507 rows); (b) paper trader v2: confirm 12 h forecast-age cap, the fee-aware exit rule (rarely fires on singles), shrink 0.25, approve 115 alias candidates (configs/kalshi_aliases_candidates_2026-10-21.yaml); (c) Kalshi read-only key connector: needs "amend rule 5" from you; (d) $10.84 bonus credit: convert with 4-5 near-certain single bets pre-tip (90-97c, tight spreads, model agrees).
3. **Today (10-22), to run by hand from the paper-trader worktree** (branch agent/market-paper-trader, not merged; do NOT install its plist yet): capture `python -m nba.live capture --date 2026-10-22` and shakedown `python -m nba.papertrade --nba-db /Users/devin/Downloads/nba-prediction/nba.duckdb --rule-version paper-trader-v2-shakedown run --date 2026-10-22` (exact commands in the paper-trader hand-back in DECISIONS 20:50 thread). Unscored.
4. **App:** studio + Live + Parlay tracker + import links on branch agent/app-analysis-studio (preview: vite 5174 + branch API 8010 from scratchpad wt_app2); not merged; needs review before merge.
5. **Closed tonight:** T505, T506 (matchup idea, stopped at gate); T507 is the one override variant (ready to freeze).

**Status 23:58:** T507 FROZEN on Devin's "freeze that" (prereg 9ada5d5, harness bb764f6). Run attempt 1 crashed on a wiring bug after loading 2023 outcomes into memory (nothing computed or shown; disclosed in DECISIONS 23:52). Amendment 5 (fix + full dry run on synthetic outcomes) in progress; then adversary diff check, re-freeze, run. 7-leg combo lost (Grimes no three); night total about +$8.3. Capture smoke with trades feed PASSED (order books answer without auth; 40,783 trades on first tick; offer log reads combo legs); fixes queued (decimal contracts, trade backlog spread across ticks).
**Status 00:12:** T507 RAN (after two pre-run wiring fixes, re-frozen f380947e/dce06000): **NULL**. 2023 composite +3.84 floor units (worse than production; week-clustered CI [+2.07, +5.87]); every stat worse; scrambled-label refits did as well. 2024 not scored per the rule. Matchup idea closed for good (T505-T507). IN_GAME_LINE_MODEL (T508): gate PASSED; your Q4 correction confirmed (starters keep 96-99% of Q3 minutes in a halftime blowout, lose ~50% of Q4 minutes after a 3rd-quarter blowout); run path + synthetic dryrun + assist repair being finished overnight, then adversary review, then your sign-off.
**Status 00:55:** Playbook arm built (agent/market-paper-trader 4d54754; descriptive, own store, synthetic conservative pricing). Today run, from the worktree scratchpad/wt_pt, three processes: `python -m nba.live capture --date 2026-10-22`; `python -m nba.papertrade --nba-db /Users/devin/Downloads/nba-prediction/nba.duckdb --rule-version paper-trader-v2-shakedown run --date 2026-10-22`; `python -m nba.papertrade playbook-run --date 2026-10-22`. Expect many `no_positive_ev` skips (honest). Open choice for Devin: SAFE 1.2x take-profit measured on the conservative net exit (default) or on the mid. T508 in-game model: adversary NOT_READY (ast leak 0.24%, baseline handicap, inert control); fixes in progress; Devin pre-authorized the freeze after a clean review (DECISIONS 00:45).
**Status 01:20:** IN_GAME_LINE_MODEL (T508) frozen on your pre-authorization (469861e6/e8226ee1) and RUN: **PASS on every pre-registered criterion, provisional.** pts log loss M 0.231 vs pace baseline 0.272 (M - B0 -0.041, CI [-0.046, -0.037], 8x the floor); 2024 -0.040; reb/ast/fg3m pass too. A win this large is suspect by our rules: the adversary's post-result attack (knockouts, shuffled labels, planted future in the minutes stage, a fairer strong baseline, the odd per-checkpoint baseline at 0.44) is running. Do not trust or use it until that verdict (SURVIVES / WOUNDED / BROKEN) lands.
**Status 02:00 (final for the night):** T508 post-result attack: **WOUNDED, not a leak.** The 8x win was ~94% a weak baseline (league-average rate for every player); against a fair player-aware baseline the in-game model's edge is ~0.0025 log loss (under the 0.005 floor), concentrated after minute 30 (~0.005). Not to be used for sizing or exits yet. Your choice when ready: a new forward rule on the second-half residual vs a player-aware baseline, scored on the live 2026 capture (default: not now). New best practice: baselines must know what the model knows. Retro: docs/reviews/retro_2026-10-22_night.md. Recommend starting a fresh Claude session today.
**Overnight (in progress):** IN_GAME_LINE_MODEL draft + gate (agent/modeler-in-game-line); Kalshi trades feed + cash-out offer log in the capture (agent/market-paper-trader). Status lines appended below as they land.

# NEXT SESSION — resume here (written 2026-10-21 ~01:00 CDT, overnight run under Devin's blanket approval; A pending at write time, see the A line)

**Phone-length summary for Devin (read this first):**
1. **All three 10-20 games are final with box scores in `games`**; PHI@NYK settled correctly (106-100 PHI). OKC@SAS (116-104 OKC) settles at the 07:00 job on the real score. The games feed returned a PARTIAL 44-65 for OKC@SAS sixteen minutes after the scoreboard said Final: a third path to the partial-score bug, closed only by A(1).
2. **Merged tonight:** F (calibrated P(over) on the card slider), E (COACH_PHASE, PRESEASON_ROLE_PRIOR, WOWY_STRUCT archived; 10 scratch worktrees removed), B (watchdog game-day probe, live from its next tick; oracle IDENTICAL twice, review APPROVED after edits), the GMC re-derivation (T501), the holdout-filter fix for two harnesses. **A: see below.**
3. **Closed without a run:** C, MARKET_PRIOR_RESIDUAL (T502). Adversary: CLOSE_WITHOUT_RUN; its reachable verdicts cannot tell "our features know something" from "the market is mis-calibrated", and it is a second follow-up to H2. Your override: say "run C" and it gets edits B1-B3/M1-M3, a re-review, the freeze, the run (30 min). Claude's recommendation: leave it closed; pre-register the gamma disagreement test on the live log instead.
4. **CI on main has been red since 2026-10-15** (unit job aborts at collection: the frozen COMBO base normals hash differently on the ubuntu runner than on this Mac). Nobody looked for six days. Fix for your go (live path): freeze the array as a committed file. A test-level skip was withdrawn (the auto-mode classifier called it a CI bypass; it would have weakened the verifier).
5. **GAME_MARKET_CONTEXT null stands at the floor (T501)** but its 10-11 explanation was wrong: removing ALL game context also ties production, so "our proxies already carry it" is withdrawn; a pts gain under about 0.004 is not excluded. New practice: nulls get a positive control.
6. **Your decisions waiting (each is now a GitHub issue: decisions #14-#21, bugs #2-#12, follow-up #13, chore #22; GitHub cleaned 12:1x: remote branches 136 -> 20, issues opened):** (a) CI: the base-normals fix is merged and collection works on ubuntu; the one remaining red test is the F1 python-pin check (your call: run it only inside the F1 harness, or re-pin); (b) DONE: scheduled games reach `games` (merged 78bbfae); (c) DONE: export line_cal + the card reading it (F complete); (d) DONE for the main settle: the box-consistency gate refuses a disagreeing box (merged 3b21ae8); still open: F1a automatic re-pull of a disagreeing box, F1c an acknowledged-exception list for official-source defects; (e) the shadow settlers (pplay, full, avg, combo) have neither the 4 h guard nor the box gate and do not read forward_withdrawals (F2); nba.parlay evaluate/analyze do not read withdrawals; two manual tools (`nba/markets/capture.py backfill_games`, `nba/ingest/availability.game_dates_between`) have no score guard; the prereg Amendments engineering note the adversary drafted for the box gate (docs/reviews/box_consistency_review_2026-10-21.md) awaits your placement, since FORWARD_PREREG is the frozen live rule; (f) run C or leave it closed; (g) four research callers left unfiltered for the holdout (seq_full_mix_rt_export, p2_parity, seq_full_mix_eval, injury_elo_t30: each needs a season cap, not a hard 2024); (i) **F3 from the export review:** the parlay producer (nba/parlay/top_source.quartiles) reads the LINE_CALIBRATION median and spread from the stored 19-point q_grid while the frozen rule text says the 199-point grid; the difference is at most 7.6e-5 in p_cal on 96 real pairs (deterministic, no CI), but it is a deviation from the frozen definition: accept it with a dated DECISIONS row, or switch the producer; (j) **two pre-existing ops bugs found by the early-briefing review (docs/reviews/early_briefing_ops_review_2026-10-21.md):** the pretip `predict` itself hung for 7057 s on 10-20 and 12244 s on 10-19 (a network stall in the forecast run, not only in the post-game fetches), and `acquire_lock` takes over a lock older than 180 min even when its owner is alive, after which the morning job's EXIT trap deletes the pretip job's lock without checking the pid; the Drive uploads have no timeout (6435 s and 9614 s this week). The briefing branch adds alarms on predict, early_forecast and the fetches; the lock takeover needs a pid check (steward, your go); (k) **salary data PULLED 11:48 on your 'Run it' (150 team-season pages, 2,773 salary rows, 511-531 players a season, data/raw/bref_salaries_2026-10-21/teams/, README with the credit); the ROLE_PRIOR_SALARY draft is being written against it (gate on 2023-24 only). Reporter sources: live-only.** Original item: **salary and reporter sources (docs/research/free_sources_salary_reporters_2026-10-21.md):** no free source with a clean licence for either. Your calls: (1) approve or decline a one-page hand pilot of Basketball-Reference's player-page Salaries table (Curry's page, 'Get table as CSV', credit BR, no redistribution; then a restricted 300-350 player run for the mover and rookie cohorts 2022-25 if the pilot is clean); (2) whether to read HoopsHype's and Spotrac's terms in a browser; (3) reporter minutes caps have no free retrospective archive, so that fact can only be a live-only log with our own first_seen_at (planned X feed, Bluesky allow-list); the researcher found that a text match on 'limited to N minutes' would label post-game recaps as pre-tip facts, so the live parser needs future-tense and before-tip filters; (4) the rookie half of a salary prior needs no scraping (rookie-scale pay is a function of draft slot), but production already carries a draft-slot minutes prior for rookies, so salary would add nothing there: the prior is for movers only; (l) **QUESTIONABLE_CASCADE (T503): gate FAILS on the report season for points (0.0062 vs 0.005), passes for reb/ast/fg3m and pooled; your pick: A close / B pooled single test / C floor -0.0065 (not recommended) / D live columns + declared n (Claude and the modeler recommend D); adversary gate read (docs/reviews/QUESTIONABLE_CASCADE_gate_2026-10-21.md): the fail is proxy-dependent but the effect size binds under every design; recommends A + a live-only D; also: 2022-24 per-release report PDFs may be retrievable (your download) to repair the T-60 snapshot gap; (m) **ROLE_PRIOR_SALARY (T504): gate FAILS on power for every stat (pts half-width 0.017 vs 0.005; 94 / 79 mover player-seasons), while salary rank predicts minutes at Spearman 0.72 / 0.70 among non-movers and the crosswalk resolves 98.6% of rows; your pick: (a) close / (b) freeze with MDE 0.024 stated / (c) reset floors (rejected) / (d) show the salary-implied role as unscored context on the page (Claude: a + d). Also eyeball the 2023-24 and 2024-25 cap constants in the draft against the NBA releases; (h) delete the wt_stamp husk by hand (`rm -rf` of a directory under /private/tmp/.../763dc211.../scratchpad/wt_stamp; nothing at risk) and the oracle copies data/rehearsal/oracle_B_*, oracle_B2_*, oracle_A_*.

**01:5x CDT, Devin: "keep going!" -> second wave, done by 03:1x CDT (every merge has a DECISIONS row with its gates):**
- **F complete end to end:** export `line_cal` (LINE_CALIBRATION P(over) at the DK main line, bit-equal to the parlay panel) merged 60d1e12; the card reads it (merged ced3cb8). First real rows render after the 12:35 export job; eyeball at 375 px.
- **Scheduled games into `games`** merged 78bbfae: today's slate is written with NULL scores at the first pretip run and at the 07:00 ingest; stale placeholders deleted after a successful refresh; the data manifest ignores scheduled rows (`scheduled_rows`, `STALE_SCHEDULED`). Coverage's `schedule` check and the probe's slate line should go quiet from the first pretip run (09:20 ET). The first manifest diff lists two new `games` fields as changes, not flags.
- **Box-consistency gate + refusal alert** merged 3b21ae8: settle grades a target only when per-team box points equal the game score; refusals are printed and `settle` exits 4 (fresh) or 5 (a game tipped > 24 h ago still refused: alerts). Runbook for a refused game in docs/DAILY_PIPELINE.md. Live read at 02:5x: 0 refusals, so this morning's settle exits 0.
- **CI fix** merged 18a8e39 (PR #1 merged 08:15Z): the draft PR's ubuntu run collected and ran the whole suite, 4216 passed / 18 skipped / 1 failed; P1 reproduced bit for bit on ubuntu. **CI on main stays red by exactly one test**: tests/research/f1_warm_window `test_pinned_versions_are_the_installed_ones` (the runner and this Mac run Python 3.12.15; the closed F1 harness pins 3.12.13). Your call: move that check inside the F1 harness's run(), or re-pin; it is a test about a closed rule's run-time environment, not about main.
- Three oracle rows per branch pair, all IDENTICAL 89889f6d: B, B2, A, A2, CI, SCHED, SCHED2, BOX, BOX2 (copies under data/rehearsal/ to delete).

**07:23 CDT check (scheduled, read-only): the 07:00 job did the right things.** `start ingest` 07:00:02 BEFORE `start settle` 07:00:11 (A); ingest: games_rows 3, boxscores 0 missing, **scheduled_rows_inserted 11** (today's slate is in `games` with NULL scores), stale_scheduled_removed 0; settle: win_scored 2, prop_scored 304, prop_dnp 92, all four refusal counts 0, rc 0. **OKC@SAS (0022600003) scored on the real final 104-116:** both winner rows y = 0.0 (SAS home lost; injury-Elo had SAS at 0.565, MOV-Elo 0.558), 99 rows per prop target (scored + dnp), elig 258 rows; box 34 rows / 27 played, team points sum 220 = 104 + 116, so the box-agrees gate passed. No row was scored on a partial. coverage_yesterday ALERTED rc 3 on 10-20's known FAILs only (roster_placement 14 misplaced, pairing 0% for games 2 and 3, the 144-min pretip gap): correct, not a regression. Scorecard rebuilt 07:00 with 3 games (272 rows). checkpoint, report, parlay_settle (no_positive_ev_found) rc 0. **At 07:23 the job was still in `fetch_tracking` (started 07:00:20)**, the post-game fetch steps that ran 40 min to 4 h on earlier days, so data_snapshot/diff/check and the backup had not run yet; the 08:00 probe will therefore fire its "morning started but not ended" line, and the 08:17 check reads the manifest result (expect no NULL_SHIFT / COVERAGE_DROP from the 11 scheduled rows; two new `games` fields listed as changes). Follow-up for Devin: a per-step timeout on the fetch_* post-game steps.

**08:17 CDT check (scheduled, read-only).** The game-day probe fired once at 08:01 with two lines, each once, keys dated (`gameday:2026-10-21:battery`, `gameday:2026-10-21:morning`): "on battery; plug in and run caffeinate -dimsu" (the Mac is on battery at 100%, discharging; Claude's caffeinate still running, expires 09:10) and "the morning job started but has not ended (62 min since start)". **No slate line**: the 07:00 ingest had already written the 11 scheduled rows, exactly as the merge intended. No caffeinate line. The morning job was still in its post-game fetch steps at 08:17 (77 min; data_snapshot/diff/check and the backup not yet run): the fetch_* steps need a per-step timeout (follow-up). **OKC@SAS memo picks graded against the box (n = 1 game, no CI):** Fox o4.5 ast HIT (7); Castle o15.5 pts HIT (24); Holmgren o12.5 pts HIT (14), o7.5 reb MISS (7); SGA o29.5 pts HIT (32): 4 of 5. Retractions: SGA ast (4), Castle ast under (1: the under would have hit), Wemby reb under (13: the under would have missed), Wallace 3s over (0: the over would have missed): 3 of 4 retractions avoided a loser. Five box rows show as "Player <id>" in the export's players table (rookies without a name row): cosmetic, app-side follow-up.

**08:32 CDT check (scheduled, read-only): NO FORECASTS FOR TODAY YET, and the cause is operational.** The 07:00 morning job (pid 84653) has been inside `fetch_tracking` (`python -m nba.ingest postgame-fetch --source tracking`, child pid 84743) since 07:00:20, 93 minutes for a pull of yesterday's three games that normally takes seconds; it still holds data/ops/lock, so the 08:20 pretip slot skipped (heartbeat daily-pretip.skip.json: "another job holds data/ops/lock", consecutive_skips 1) and no pretip log exists. The same hang hit fetch_officials on earlier days (40 min to 4 h). Without action the pretip slots keep skipping until the job ends or the lock is cleared as stale at the first slot after 10:00 CDT (3 h rule); the watchdog alerts at 3 consecutive skips (09:20). First tip is 18:00 CDT, so forecasts are not yet at risk, but the slate has had no run. **Devin's options:** (a) kill the hung child (`kill 84743`); the step records rc != 0 and alerts, the job moves on to hustle/officials/matchups, the manifest and the backup, releases the lock, and the 08:50 slot runs; or (b) wait for the stale-lock clear at ~10:20. Claude does not kill another process (steward rule). **Structural fix (follow-up, live path):** a per-step timeout for the fetch_* post-game steps, or release the daily lock before them; data_snapshot/diff/check and the backup therefore have not run either, and the 11 scheduled rows' manifest behaviour is still unverified.

**10:0x CDT: the early-edition briefing is merged (16b911c + ffc4f21).** From tomorrow the 07:00 job writes today's forecast and reports/briefing/<date>_early.md right after the report step and before the slow fetches; every pretip run rewrites <date>_report.md; predict, early_forecast and the post-game fetches now have alarms (1200/1200/1800 s) so a hang alerts and the job moves on instead of holding the lock all morning. Today's first page appears with the first pretip run (the 10:20 slot once the stale lock clears, or sooner if you kill pid 84743). Read it at reports/briefing/2026-10-21_report.md.

**10:22 CDT: first forecast of the day.** The 10:20 pretip slot cleared the stale lock (3 h rule), forecast all 11 games (4,518 rows, 1,564 prop rows, injury report 11:15 ET used, 45 OUT players excluded), and the new `briefing_report` step wrote reports/briefing/2026-10-21_report.md on its first live run; coverage: every check OK, `schedule` 11/11 (the scheduled-games merge). The hung morning job (pid 84653) was still alive at 10:22 with no end heartbeat; when it exits, its trap deletes whatever lock exists (the pre-existing takeover bug, item (j)); the pretip had already released its own.

**12:25 CDT: the 07:00 morning job ended, rc 1, after 5 h 24 min.** Settle and the briefing path were fine (done by 07:00:13); the three slow post-game fetches each hung 1-3 h and failed (tracking 09:41, officials 11:05, matchups 12:19; postgame_load rc 1; issue #23), the backup was written 12:21, and data_check flagged NULL_SHIFT on players_static.draft_status and first_season from the rookie autofill (issue #24), with NO flag on `games`: the scheduled-rows manifest fix held. From tomorrow the alarms cap each fetch at 30 min, so the job should end by about 09:00 CDT and the pretip slots run.

**Morning checklist (~5 min):**
1. `tail -40 data/ops/morning_20261021.log`: expect `start ingest` (today's 11 slate rows inserted, `scheduled_rows_inserted`) then `start settle` (A merged); settle scores 0022600003 winner rows on 104-116 and prints no `refused` line (exit 0); `coverage_yesterday` ALERTS on 10-20's known FAILs (correct, not a regression); scorecard rebuilt with 3 games.
2. `tail -20 data/ops/ALERTS.md`: the new game-day probe fires at the first check after 09:00 ET (08:00 CDT): expect the slate line ("11 of 11 game_ids missing ... expected ... not a fetch failure"), possibly the morning line, and the caffeinate line after Claude's `caffeinate -t 36000` expires at 09:10 CDT. Start your own `caffeinate -dimsu` before that.
3. Grade the OKC@SAS memo picks against the box (Fox o4.5 ast, Castle o15.5, Holmgren o12.5 / o7.5 reb, SGA o29.5; retractions SGA ast, Castle ast under, Wemby reb under, Wallace 3s over) and the Pinnacle Part 3 (not done overnight: the box landed at 00:06).
4. `gh run list --branch main --limit 3`: expect the unit job to run the whole suite now and fail on exactly one test (the F1 python pin); anything else failing is new.

**Overnight record:** every merge has a DECISIONS row with its gates; oracle rows in HOLDOUT_ACCESS_LOG (B eabb2bd and 3cccc18 IDENTICAL; A see below); reviews in docs/reviews/ (gameday_probe_review, refuse_not_report_review, GMC_null_rederivation, MARKET_PRIOR_RESIDUAL_prefreeze); T501, T502 in the ledger; priors and scores in PREDICTIONS_LEDGER; reading log for the two pasted LLM texts (docs/research/outside_llm_strategies_2026-10-20.md). Devin's two questions tonight were answered in chat and recorded: LLM vs ML ("information extraction, not forecasting; backtests impossible for an LLM"), and "how do we know those tests were done accurately" (the GMC re-derivation).

**A (plan item A) MERGED 5ce5f15 at 01:3x CDT:** winner rows need >= 10 played box rows (also in the frozen elig table); the morning job now runs `ingest` before `settle`; misplaced movers are withdrawn by status (forward_withdrawals), never deleted, and skipped by settle, the export and top_source (the frozen elig table still writes them as dnp); top_source and the export take teams from the schedule cache when `games` lacks the game. Oracle IDENTICAL on both heads (e335489, 79b98b8); review BLOCKED -> fixed -> APPROVED. **So the 07:00 job on 10-21 runs the new order: expect `start ingest` before `start settle` in morning_20261021.log.** Follow-ups for you (not built): M2 box-points-equal-score gate for every target (closes the mid-game-box path), M3 a refusal counter + alert, withdrawals permanent per key, shadows and nba.parlay do not read withdrawals, the combo-reader test flake (unordered query in nba/eval/full_shadow_live._LIVE_SQL).

---
# NEXT SESSION — resume here (written 2026-10-20 22:1x CDT, end of opening night)

**State:** main is clean at f0b659c. Opening night ran: 3 games forecast before tip; BOS@DET settled (Detroit 121-113; 352 prop rows scored, Paul George missed by 23 on rate); PHI@NYK and OKC@SAS settle in the 07:07 morning job (settle fix: only >= 4 h after tip). Merged today: settle-after-tip fix (live path, oracle IDENTICAL), app display-history guard, coverage check + live scorecard (wired into pretip/morning), Pinnacle benchmark, preseason tables, PRESEASON_ROLE_PRIOR (T500, FAIL_ONE_SEASON, closed).

**PLAN FOR THE NEXT 16 HOURS (Devin, 10-20 22:4x CDT: "I wanna really use Claude"), in order of value per hour; A-C can run in parallel on separate branches/directories:**
A. **Make the live loop refuse, not report** (steward, live path, oracle + go): (1) settle writes winner rows only when the game has a box score with >= 10 played rows (plus the 4 h guard); (2) the morning job refreshes games/box scores BEFORE settle (ingest step first, or settle after postgame_load); (3) a forecast row is withdrawn (status, not deleted) when the official roster places the player on a team not in that game; (4) top_source/export take home/away from the schedule cache when `games` lacks the slate. Each with a test that fails today. Oracle after each; merge on the pre-authorized go below.
B. **Game-day probe** (steward, nba/ops/coverage.py + watchdog, live path): at 09:00 ET alert if on battery, if the schedule for today is not in `games`, if the morning job did not end, or if `caffeinate` is not running on a game day. Devin: plug in and run `caffeinate -dimsu` before 09:00.
C. **MARKET_PRIOR_RESIDUAL** (modeler → adversary → freeze → run, retrospective 2023/2024, ~4 h of agent time): prior = the earliest stored pre-tip Pinnacle line/price (T-60 in history), our features as the delta; primary = residual-information test (does our disagreement with the market predict outcomes beyond the market), secondary = CRPS/Brier vs A0 and vs market-alone; CIs by game, player-season and week (binding = widest); BH; floors as LINE_CALIBRATION. One rule in flight: start C only after A's code is on branches.
D. **Morning grading** (07:07 job): confirm all three games settled on finals (the 23:50 manual refresh log; `games` finals); read the scorecard (3 games; Pinnacle Part 3); grade the OKC@SAS memo picks and retractions; coverage_yesterday ALERT expected on 10-20's known FAILs.
E. **Cleanup that reduces surface** (archivist): archive closed research under the ledger (TEAM_BUDGET, COACH_PHASE, WOWY, PRESEASON_ROLE_PRIOR harnesses move to research/archive with a README pointing at their T rows); remove the scratch worktrees; `git worktree prune`. No behaviour change; tests must still pass.
F. **App** (app agent, not live path): show the frozen LINE_CALIBRATION P(over) beside the raw P(over) on cards and swipe (the bias correction exists but is only used by the parlay panel); label both.
Not tonight: new pre-game features (ledger says no), the assists family (after C), timing lag (needs 30 dates), the report-time fix merge unless its oracle is re-run on current main.

**Pre-authorizations Devin may give in the kickoff message (so agents are not blocked overnight):** "merge A(1)-(4) and B when ruff/format/mypy/pytest pass, the replay oracle is IDENTICAL 89889f6d, and the adversary review is clean; merge E and F when their tests pass; freeze and run C after the adversary says READY_TO_FREEZE; log every merge in DECISIONS with the oracle sha." Without these, each merge waits for a go.

**FIRST, before anything else:** the 9:50 pm run stored PHI@NYK as a PARTIAL 63-62 in `games`, and the morning job runs `settle` before any ingest, so at 07:07 the Knicks winner rows would be scored on 63-62. Mitigation applied 10-20 23:50 CDT: one manual `nba.daily run --date 2026-10-20` (same job launchd runs; log data/ops/manual_refresh_20261020_2350.log) to ingest the finals and settle game 2 on the real score. Check that log and `select game_id, home_pts, away_pts from games where game_date='2026-10-20'` first thing. If game 3's final is missing, run the same command once more before 07:07. **Structural fix needed (live path, oracle + go):** the morning job must refresh games/box scores BEFORE settle, and settle should require a box score for the game (not only tip + 4 h) before scoring winner rows; a partial score left by the last pretip ingest of the night is otherwise permanent.

**Tomorrow morning (2026-10-21), ~5 minutes:**
1. `tail -40 data/ops/morning_20261021.log`: expect settle to score 0022600002/3; `coverage_yesterday` WILL ALERT on 10-20's known FAILs (schedule gap, 14 stale movers, pairing, job gap, early BOS@DET scores): correct, not a regression; `scorecard` rebuilds reports/forward_scorecard/2026-10-20.md with 3 games (CIs still thin).
2. `uv run --no-sync python -m nba.ops.coverage --date 2026-10-21` before the first tip; if `schedule` still FAILs, the games table is not getting the 2026-27 schedule: fix the fetch (live path, oracle + go).
3. Grade last night's memo picks against the box (Fox o4.5 ast, Castle o15.5, Holmgren o12.5 / o7.5 reb, SGA o29.5; retractions SGA ast, Castle ast under, Wemby reb under, Wallace 3s over) and the Pinnacle Part 3 for OKC@SAS.
4. Mac: keep on AC, lid open; `caffeinate -dimsu` in a Terminal on game days (it slept on battery 10-20 morning).
5. Housekeeping: `git worktree prune` after removing scratch worktrees (wt_stamp, wt_boxapp, wt_final, wt_wire, wt_scorecard, wt_coverage, wt_pinn, wt_preseason, wt_prior, wt_adv_prior, wt_adv_post); Devin deletes data/rehearsal/oracle_[fw]_* and oracle_*_src.duckdb (holdout copies).

**Open decisions for Devin:** merge the live report-time fix (agent/archivist-report-stamp-fix 2dfbd07, oracle IDENTICAL on 10-16); production should withdraw a mover's stale old-team forecast (live path); the line-pairing rule (forecast made_at vs quote received_at); which of the new hypotheses to draft first (gap-regression residual test; assists family A1–A3; timing M2/M3 after ~30 slate dates).

---
# Next session

_Updated 2026-10-14 20:27 CDT. Status overview: PROJECT_STATUS.md. Retro: docs/reviews/retro_2026-10-13.md._


## OVERNIGHT 2026-10-14 -> 10-15 (Devin asleep; nothing merged to main, no holdout, no live DB writes)
A1 merged by Devin (70c52c0). Six agents launched ~22:30 CDT, each on its own branch:
1. agent/modeler-f1-warm-window: apply the 10 F1 pre-freeze edits + data gate numbers; Devin's 3 open calls marked OPEN (follows review b02d403).
2. agent/steward-best-model-minors: morning-report primary-source counts, post-tip `>` vs `>=`, p_play for dropped no-team games; oracle 89889f6d; needs Devin's go by Fri 10-17.
3. agent/modeler-avg-november-prep: gate_rows.py explicit join order (review S1) + staged November refit notebook/runbook + missing-bundle alarm (deadline 11-01 12:00 CT).
4. agent/modeler-pover-bias: SCRATCH diagnostic of "P(over) 3-4 pp low at DK lines" on 2023-24 only; overlap with T477/LC2.
5. agent/archivist-questionable-scope: inventory of QUESTIONABLE/DOUBTFUL statuses (Claude's suggestion, not approved by Devin; scoping only).
6. agent/scribe-retro-1014: retro for 10-14.
Results are summarised here as they land.
- DONE retro (main f3f0391); Devin "yes to everything" -> rule changes e19939a (shell timestamps, dry-run failures in the signing line, binary priors).
- DONE F1 edits (agent/modeler-f1-warm-window 46f015e): all 10 applied; gate G1-G7 PASS on real data (no fit, no 2025). Power at 1.5x floor
  pts 0.68/0.86 (2023/2024), joint pts 0.58, pts at the floor 0.18. Worktree data/ was a real dir (stale model copies); moved to scratchpad,
  now symlinked. Still OPEN for Devin: F1-first order, count floors -0.0025, WOWY E7(e) wording; then the WOWY mirror edits, manifest refresh, sign.
- DONE questionable scope (docs/research/questionable_status_scope_2026-10-15.md): blind spot is production-only; R and AVG shadows already use
  all statuses; PPLAY_INTO_PROPS_LIVE is the test. Q plays 0.51 (0.77 for 3+ ppg), doubtful 0.02, probable 0.93. A historical test can't
  resolve -0.005 (MDE ~0.075 pooled). Recommendation: no new rule. NEW RISK: the stored "T-60" snapshot is typically ~T-180 in history
  (median lead 150-180 min, except 2021-22 hourly); live 2026-27 is a true T-60, a train/serve skew nobody has measured.
- DONE P(over) scratch (docs/research/pover_bias_scratch_2026-10-15.md, 2023-24 only): DK T-60 gap -3.25 pp [-3.61, -2.87], n 106,999 legs /
  2,616 games; same as T477 (Pinnacle -4.3). It is overconfidence (logit slope 0.46), not a mean shift; worst at high lines and min10<15.
  A 2023-fit slope removes it on 2024 (log loss -0.0089 [-0.0102, -0.0076]) but still trails the de-vigged DK price (+0.0026).
  CORRECTION (main session): the agent read "LC2" as line calibration; LC2 is SEQ_LEARNING_CURVE_2. LINE_CALIBRATION rev 2 (Devin's
  "when can we recalibrate???", 92069c5) is still a DRAFT, gate 15/15, never frozen or run, and is exactly this test. EXPOSURE: tonight's
  scratch fit a slope on 2023 and scored 2024, LC's select season; disclose in its data-exposure section before any freeze (Devin's call).
  Agent piped pytest through tail (rule breach; 85 passed). Pre-existing ruff import-order error on main: app/fixtures/build_export_best_real.py.
- LINE_CALIBRATION (Devin "Keys try or line calibration" = go): review BLOCKED (T480 had scored A0 at 2025 lines; SHRINKAGE_ONLY could
  serve CAL) -> fixed -> APPROVED; R1/R2 applied; FROZEN 06d76e3 (a5441242). Stage 1 2024: CALIBRATED, Pinnacle CAL-A0 -0.0117
  [-0.0139, -0.0096], n 37,528 / 1,260 games; CAL 0.6882 < coin 0.6931 but > market 0.6865 (T493, 8938350). Post-result attack running.
  OPEN for Devin: (a) confirm the B2 default was fine (moot here: CAL served); (b) confirmatory stage = 2026-27 live log (Claude) vs 2025 touch.
- Steward minors DONE on agent/steward-best-model-minors d4d6f29 (report source counts + FALLBACK flags; reader >= at tip; p_play for dropped
  games); 3596 tests pass. ORACLE NOT RUN: needs a HOLDOUT_ACCESS_LOG row first (Devin's decision; template row 81). Then merge by Fri 10-17.
- T493 attack SURVIVES (7a3c58a): gain is fg3m/reb/ast, pts only -0.0007; a recency-only line map gets half of CAL-C0. Not a pts signal.
- AVG November prep DONE on agent/modeler-avg-november-prep a5cfe2f (NOT merged: adds nba/daily/avg_shadow/install_check.py):
  gate_rows join pinned + run-time by-key verify (parity on 2023-24, 56,433 rows; 2025 tail unprovable without a logged touch);
  monthly notebook research/colab/jobs/avg_shadow_monthly/avg_shadow_monthly.ipynb + runbook docs/runbooks/AVG_SHADOW_MONTHLY_REFIT.md;
  install_check (deadline 11-01 12:00 CT) built, not wired. DEVIN BEFORE 11-01: (1) gate option A = carry October gate.json (deviates
  from the rule's "gate refit"; Claude: A, logged as a pre-result procedure amendment, because B needs a live-row loader that does not
  exist) vs B; (2) monthly-refit holdout row (draft in runbook; pool contains 2025 labels); (3) go to wire install_check (ops/, nba/daily);
  (4) merge. RISK: no 2026 possessions exist yet; confirm nightly ingest produces them after opening night (runbook step 0).
  Scratch: a 650 MB live-DB copy (nba_copy.duckdb) sits in the agent scratchpad; delete when convenient.
- 00:30-01:00: Devin answers: LC confirm on live log; build parlay panel; replay check (holdout row 3742d6b, running); AVG carry gate;
  draft five follow-ups (Devin's override). Panel MERGED to main 7d824c9 on "put it in the app now"; post-merge review BLOCKED
  (illustration overlays the real 2024-11-12 slate with +EV; no after-tip refusal; positive_ev_found accepted; joint called calibrated):
  fixes 1-17 + contract v2 (gate object) on agent/app-top-parlays-fixes; producer (agent/market-top-parlays) told to write v2.
  Phone server: API 127.0.0.1:8124 + LAN preview http://10.0.0.147:4173 (background tasks in this session).
  Drafts: EXCHANGE_LADDER_CAL (gate measurable 10-21, confirmable 10-27) and WINNER_RECAL (FAIL_POWER 0.15-0.23; Claude: close) merged
  f9f2e39; holdout disclosure e7476b9. Preseason dress rehearsal (copy DB) running: agent/steward-preseason-rehearsal.
- ~01:00-02:00: minors MERGED (oracle IDENTICAL); panel fixes MERGED after re-check APPROVED (5be3d3a); ALT_RUNG_CAL + PARLAY_JOINT_CAL
  drafts merged, pre-freeze reviews running (freeze on APPROVED; harness must be pinned first). COMBO probe MET 12/12 (710 credits,
  1,809,739 left); full pull running (cap 160k, raw only; ingest needs Devin's go). WINNER_RECAL closed.
- PRESEASON REHEARSAL (agent/steward-preseason-rehearsal 94db08c, copy DB data/rehearsal/preseason_2026.duckdb): 14 games, chain ran to
  the export; phone API now serves data/rehearsal/export_2026-10-16. OPENING-NIGHT BREAKS FOUND: (1) export places players only via a
  2026 box row -> opening night would show 0 players/props (fix: agent/steward-export-roster-placement, running; needs merge by Fri 10-17);
  (2) players_static autofill ComputeError (116 rookies no prior) - being checked. No injury report existed at run time; rerun after
  ~17:00 ET 10-15/10-16 (commands in the agent report / branch). No preseason Kalshi prop prices.
- ~02:00 STATE:
  * ALT_RUNG_CAL FROZEN 58de4d4 / pinned ff040aa / RUN: RUNG_REFIT_FIXES (T494), attack WOUNDED: edge = book main line (92% from
    [1,x,zm]); A0 medians sit low (P(Y>book line) 0.479 vs P(Y>A0 median) 0.536) = same as T477/POVER. Proposed new rule: fix the low
    median inside the model (awaiting Devin's go).
  * PARLAY_JOINT_CAL FROZEN 76ea8fc; score RUNNING (first attempt refused on heavy.lock held by the oracle; restarted ~01:55).
  * Opening-night fixes (agent/steward-export-roster-placement ae1ad6c export, c717e17 rookie static): oracle IDENTICAL (fd5fa21).
    AWAITING DEVIN "merge opening-night fixes" (by Fri 10-17).
  * Top-parlays producer (agent/market-top-parlays): review BLOCKED (fixture prices after made_at; Kalshi fee on sportsbook;
    stale moved lines; same-game mislabel; live import); fixes in progress; needs replay oracle before merge (touches nba/parlay/__main__).
    Diversity default: each leg in at most one ticket per size (Devin may override).
  * Combo pull DONE 152,770 credits (b22e1f1); ingest awaits Devin's go. Combo OOF rebuild (R-F) still running.
  * Devin deletes: rm -rf data/rehearsal/oracle_m_* data/rehearsal/oracle_r_*
- ~08:45 STATE:
  * Opening-night fixes MERGED (export roster placement + rookie static).
  * PARLAY_JOINT_CAL RUN (T495): 4/4 CALIBRATED, CAL helps; attack WOUNDED: top-3 picks run 3-5 pts high (CAL tail q>=.65
    overconfident). Panel caveat added to producer (default).
  * TAIL_LEG_CAL FROZEN defa605 / pin 9fb0eec / RUN (T496): NOT_FIXED (tail overconfidence unstable across seasons). Closed.
  * MEDIAN_BIAS: diagnosis = minutes-gradient from one pooled residual shape (+2.7 pp at 30+ min, -4.0 at <15) plus book info;
    review APPROVED WITH EDITS; modeler applying items 1-7 and restating priors (plug-in forecast says PARTIAL). Then diff
    re-check, freeze, pin, run.
  * Data version: model_rows_2024 (Oct 10) vs components (Oct 11) = d872dc7 neutral-site backfill; T493-T496 stand (ledger note).
  * Holdout disclosure 09ac4a5: research loaders read and discard 2025 availability/tip rows; steward fix on
    agent/steward-holdout-filter (oracle if live path touched).
  * Producer fixes still in progress (agent/market-top-parlays); then review + oracle.
  * Combo: pull done; ingest awaits Devin; OOF rebuild (R-F) status unknown (agent stopped mid-run).
  * Session is ~10 h old: start a fresh session from this file.

## 2026-10-14 late evening (after "gnn"; Devin back)
Done (DECISIONS 20:31 and 21:30; main e35dce9):
- **App shows the best-evidenced model (Devin: "get rid of the timeline, best model into the front end asap").** Props from AVG
  (T478, the only 2025-holdout pass) when a fresh pre-tip regular-season row exists and production's inputs are unchanged; else
  production with a stated reason; P(play) from R unless out on the report (0%) or R is behind production. Export 15175c1 + app
  6bea14c MERGED after review BLOCKED x2 -> APPROVED (docs/reviews/best_model_e2e_review_2026-10-14.md). Scoring untouched.
  No AVG/R rows exist yet (preseason); opening night is the first real check. AVG needs the November bundle before 11-01 or the
  app silently falls back to production everywhere.
- **A1 built and APPROVED** (agent/steward-export-gate 92ab046): export gates on morning data steps, Drive-backup failures exempt.
  NOT MERGED: Claude's merge was blocked by auto mode (live path ops/). Devin runs:
  `git merge --no-ff origin/agent/steward-export-gate -m "merge agent/steward-export-gate: export gates on morning data steps (A1)" && git push`
- **WOWY_STRUCT / F1:** power re-measure done from stored grids only (agent/modeler-wowy 941fa76; all cells >= 0.5, pts top-25%
  2023 thin at 0.55, kept). It is a proxy: a 2019+ A0_warm fit IS F1's arm C, so order is F1 edits -> Devin signs F1 -> freeze ->
  F1 run -> strict WOWY re-measure from C's forecasts -> WOWY freeze. F1 pre-freeze review APPROVED WITH EDITS, 10 edits
  (agent/adversary-f1-prefreeze b02d403), not yet applied. Devin open: F1-first order, count floors -0.0025 (Claude: keep),
  WOWY E7(e) wording (only F1 licenses a window change).
- **Parlay context:** EV closed; PARLAY_CONTEXT_PROB gate FAILED on power (best 0.24; agent/market-context-prob 02797f8), not
  frozen, closed. Lead: model P(over) 3-4 pp too low at DK lines (overlaps T477/LC2); check before any new rule.
- Still Devin: AVG meta records (meta bundle, five proofs, counting-start 2026-10-20); COMBO counting start; eyeball the new model
  sentences at 375 px light/dark (Today, Game pages).
- Loose ends (review minors): p_play rows for dropped no-team games; reader production post-tip check `>` vs `>=`; morning report
  should show primary-source counts (pts silently falls back if the _lt model stops logging).

## STOPPED 2026-10-14 ~20:27 CDT (Devin: "gnn")
Done this evening (commits 7ae5b55..c182440 on main; DECISIONS rows for each):
- **Wiring branch MERGED** to main 1dbee39 on Devin's conditional go: steward fixes 3ccacde (S1 budget 420 s, S2 counting_start
  filter in AVG/COMBO/FULL readers, S3 interim kill, T1, M5-M7, A2), main merged 9bf2cf7, oracle_w3 IDENTICAL 89889f6d, checks
  green (479 tests daily/eval/ops/layering). Replay copies deleted by Devin (holdout log bbdb557). Export plist installed by Devin;
  first run SKIPPED correctly (morning rc=1 from the Drive backup), so A1 is now concrete: Claude still recommends gating on data
  steps; it needs a replay + Devin's go by Fri 10-17 or waits until after opening night.
- **Still for Devin after the merge:** AVG meta records (`meta bundle data/avg_shadow/bundles/2026-10`, five proofs,
  `meta counting-start 2026-10-20`), COMBO counting start likewise (never before 10-20). AVG needs a November bundle before 11-01.
- **WOWY_STRUCT** (origin/agent/modeler-wowy c44ee72, NOT frozen): pre-freeze review APPROVED WITH EDITS (f1f0add); 2022 CRPS
  conversion check passed (t_c 0.0050/0.0063/0.0030 stand, all within +-20%, pts leader-out at 0.82); E1-E7 applied; E7 = every
  arm trains from 2019-10 (Devin); mixture refit per block on 2019+ (Devin); G2 = S+R vs A0_warm all rows (Devin); star out =
  largest single OUT player's v_s >= 2020-21 rotation 0.75 quantile (15.13 pts / 3.45 ast; overlaps its cells 91-100%, weak
  guard); heavy absence (sum, 45.0 / 10.4) reported only. Remaining before Devin signs: (1) **Devin's go on the 2019+ power
  re-measure** (forecasts only, NN8; Claude: yes); (2) short adversary pass on the final text incl. the new Vmax column
  (planted-future not re-run on real rows); (3) sign together with F1.
- **F1_WARM_WINDOW** (branch agent/modeler-f1-warm-window): draft RUNNING at stop. Shares fits with WOWY_STRUCT, so F1 must be
  FROZEN BEFORE any WOWY_STRUCT arm is fitted. Mixture choice must match WOWY E7(b) (refit per block).
- **Parlay context copula** (b6e1fca): read. Real but small (36 cells vs ~5 under placebo; best typical +6.1%, below every
  measured cost). Claude recommends closing as a sizing null; Devin has not answered.
- Claude's open suggestion: scope the "questionable" players blind spot (features only count OUT at T-60).
Next session first: F1 draft result -> Devin's go on the power re-measure -> adversary pass -> Devin signs both; A1; parlay null.

## STOPPED 2026-10-14 ~19:15 CDT (Devin ended the session)
Earlier today (recorded in TEST_LEDGER / DECISIONS): LC2 closed (DATA, FLAT); ALT tests all NOT PROFITABLE (~vig); AVG2 frozen and
installed (bundle b8d2b27b, 16:16 CDT; counting start UNSET); COMBO frozen; RAPM untestable; F12 closed; app on/off + badge merged.

This evening:
- **Steward wiring branch agent/steward-opening-wiring (da08b57):** COMBO + AVG2 flags ON in pretip, export launchd job, watchdog
  drill. Replay oracle main vs branch both IDENTICAL 89889f6d (oracle_w_main, oracle_w_branch2). P3 (combo writer ON) IDENTICAL,
  heartbeat ok 96/96 runs, rows on 20/20 dates; combo mean 3.1 s / max 10.6 s per run; 46 MB over 20 dates. Review (adversary,
  origin/agent/adversary-wiring-review d482261): WOUNDED, APPROVED; S1 (no time budget), S2 (readers score rows before
  counting_start, AVG/FULL too), S3 (interim kills on coverage alone), T1 (vacuous test), M1-M7, A2 (kickstart after bootstrap).
  Steward was RESUMED to fix S1-S3, T1, M5-M7, A2; status unknown at stop. Next: read its result, re-run the oracle on the fixed
  branch, merge with Devin's go BEFORE Sat 10-18 (hands-off). Steward worktree data/ is a symlink to main data/ (old local copy in
  scratchpad steward_worktree_data); never stage data/ from it.
- **Devin decision pending A1:** should the app export run when the morning rc=1 came only from Drive backups (today: DNS outage
  09:19, every data step rc 0)? Claude recommends yes (gate on data steps). Devin said "I don't know"; strict rc==0 kept for now.
- **After merge, Devin runs:** `cp ops/launchd/local.nba.app-export.plist ~/Library/LaunchAgents/ && launchctl bootstrap
  gui/$(id -u) ~/Library/LaunchAgents/local.nba.app-export.plist && launchctl kickstart -k gui/$(id -u)/local.nba.app-export`.
  Then the AVG meta records (writes live DB, Devin's go): `meta bundle data/avg_shadow/bundles/2026-10`, the five proofs,
  `meta counting-start 2026-10-20`; COMBO counting start likewise, never before 10-20. AVG needs a November bundle before 11-01.
- **WOWY:** history version NOT frozen (gate fails; big with/without gaps are noise). App shrinks those numbers (merged 20ac848).
  Devin's per-possession idea **WOWY_STRUCT** (draft, origin/agent/modeler-wowy b5a5569): leak audit WOUNDED, no leak
  (origin/agent/adversary-struct-leak 6d3a3bf); 3 cells survive (pts leader-out, pts top-25% fixed 5.04, ast leader-out), primary
  contrast STRUCT beyond role share; permutation null done (8/9 beat all 30 draws). Pre-freeze review RUNNING
  (agent/adversary-wowy-struct-prefreeze); it must reconcile rule line 6 (G1 flat -0.003 vs measured 0.0049 / 0.0063 / 0.003).
  Then Devin signs (Claude: keep ast leader-out), freeze, write harness, reproduce gate from main, run (2023 select, 2024 report).
- **Devin decision pending F1:** props feature frame starts cold in 2022 (n_out_rot differs on 32.6% of 2022 rows vs a
  history-inclusive build; every refit trains on them). Claude recommends a November test (warm frame over data/history + nba.duckdb).
- **Parlay agent agent/market-context-copula (Devin's idea):** does context (teammate out, leader out, blowout) change cross-leg
  residual correlation enough to move a same-game parlay past the vig? Gate/inventory only; RUNNING at stop.
- **Devin deletes (agent rm is blocked), all season-2025 replay copies, unread:** `rm -rf data/rehearsal/oracle_w_src.duckdb
  data/rehearsal/oracle_w_main* data/rehearsal/oracle_w_branch* data/rehearsal/oracle_c2*` (HOLDOUT_ACCESS_LOG rows 05e1b50).
  Wait until the fixed-branch oracle re-run is done; it needs a fresh source copy anyway.
- CI on abcb6e0: lint-type and data-contract green, unit was still running (40+ min); check the latest run on main.
Next session first: steward result -> oracle re-run -> merge (Devin's go) -> export plist -> AVG/COMBO meta; WOWY_STRUCT review
-> Devin signs; parlay agent result; A1 and F1 answers.

## Calendar (corrected 2026-10-15; the old six-day plan was removed at Devin's request: its weekdays were off by one)
Thu 10-15 today; **Fri 10-16 is the last change day**; Sat 10-17 to Mon 10-19 hands-off; Tue 10-20 opening night.

## PAUSED 2026-10-13 ~16:05 CDT (Devin heading home)
- SEQ_LEARNING_CURVE_2: FROZEN 86f31649 (cf2ba61), loader review APPROVED (93dd7c0), Colab run done 98.5 T4 min (pulled to
  data/colab/runs/seq_learning_curve_2/20261013_134937/). Scoring was running on the Mac (modeler; log in the session
  scratchpad); verdicts not yet read. Open: cap diagnostic 5/30 (Colab file) vs 2/30 (scorer), both under the 1/3 limit.
  Next: results section, TEST_LEDGER rows, prior scored, then the adversary's post-result attack. T4 units/hour still unread.
- AVG_SHADOW_LIVE: built, flag OFF, uncommitted; /code-review found 10 issues (one could break the morning briefing);
  steward fixing. P3 FAILED (19-atom G1 calibration, q10 0.126): counting cannot start; repair = new rule id, Devin's call;
  check whether FULL_SHADOW_LIVE has the same 19-atom issue. Do NOT run the AVG rebuild notebook until then.
- Waiting on Devin: Oracle VM + stats.nba.com test; P3 repair decision; old co-author trailers (Claude: leave).

## Start here (handoff 2026-10-13 08:55 CDT)

Session 2026-10-13 morning, commits e333157..7e77efd (plus this one). Nothing is running.

Done:
- History position audit SURVIVES (docs/reviews/history_position_audit_2026-10-13.md). FULL/ROUTER/ENSEMBLE etc. never read history;
  only W_ALL (T478/T483/T485) had 322/77,195 training rows (0.42%) with no position, no eval exposure. Tree path saw NULL, not 0.
- Devin's decisions (DECISIONS 07:06-07:15): P(play) switch waits for the live log (PPLAY_INTO_PROPS_LIVE; the live arm is the
  2022-start R, not R19); T478 AVG to a live shadow; SEQ_LEARNING_CURVE_2 approved for drafting. `sudo pmset -c sleep 0` done
  (verified: AC sleep 0; battery still sleeps at 1 min and lid close sleeps, so plugged in + lid open on game nights).
- Holdout log: disclosure row for the audit's 2025 row-count query (counts only).
- 2013-14 and 2014-15 loaded into the history DB (b8a0db7, data_version 82ce3683ec29; backup
  data/backups/nba_history_pre_2015_19_ingest_20261013T122611Z.duckdb, tag name is the loader default). Static fetch widened to
  2013-21 (b2c20d1) and run by Devin 07:43-08:04.
- BEST_PRACTICES: check a command's scope before handing it to Devin (two wasted Devin runs this morning).
- AVG_SHADOW_LIVE drafted, BLOCKED, fixed, re-review APPROVED (7e77efd; docs/reviews/AVG_SHADOW_LIVE_prefreeze_2026-10-13.md). Not frozen.

SEQ_LEARNING_CURVE_2 gates (draft docs/prereg/SEQ_LEARNING_CURVE_2.md):
- D4 position: 0.33% unknown (13 ids, 550/165,750 rows): PASS (needs <= 1%).
- D3 reconcile (reports/history_2013_2014_boxsample_reconcile.json, 125 games): missing/extra 0 PASS; minutes 99.4% PASS;
  pts/ftm off by one: 2013-14 1.22% (17/1397) FAIL read strictly, 2014-15 0.64%, pooled 0.95%. The rule does not say per season
  or pooled; read it per season (do not pick the passing reading after seeing it) and fix that wording before freeze.
  Every mismatch is derived = official - 1 FT (derived never higher); player 202326 three times; 2015-18 was 0.34%.
- NEXT (Devin agreed, "in some hours"): archivist repairs the 2013-14 free-throw parse (likely an unmapped FT wording, like the
  13 shot codes), offline against the cached boxsample (no network, no Devin command), backup + data_version bump, re-reconcile.
  If 2013-14 still fails: Devin's fallback is 2014-15 only, windows 8 and 9 (E becomes 9 v 8, near-certain FLAT/AMBIGUOUS);
  record it in DECISIONS as Devin's call. Then: normalizer constants for new seasons, the review-gate fix (exact whole-line
  Rule: match; deleted/renamed later review counts as BLOCKED), pre-run loader review with Verdict: APPROVED, freeze.

Waiting on Devin:
1. AVG_SHADOW_LIVE: sign off the rule; approve the drafted holdout-log row (text at the end of the rule: 2025 labels as training
   rows for the Colab rebuild, 2025 OOF consumed only by the T442 gate fit; no 2025 forecast scored). It must be committed BEFORE
   the rebuild. After the rebuild, 2025 is in-sample for AVG: the live log is its only clean test. Then freeze; steward builds
   (4 tables, pretip CPU inference, Colab rebuild with weights saved, monthly refit, scorer, watchdog, 4 parity proofs; oracle
   89889f6d must not move). Before the rebuild, check the live gate's inputs equal T442's exactly (reviewer's open point).
2. Keep the week-12 look floor (Claude: keep). 3. Colab unit rate (configs cost.yaml has null rates).
4. Force-push: e333157 carries a Claude co-author trailer (against the no-attribution rule; pushed). Amend + force-push main, or leave.

Loose ends (recorded, not chased): AVG non-blocking R3-R5; the 2013-15 changelog entry is marked DRAFT; 13 history ids with no
position; one 2014-15 game with no derivable starters/stints; mypy 10 errors in research/eval/pi_transfer_diag.py; "18 arms" typo
in the SEQ_LEARNING_CURVE job description; rclone client_id; nba/truth/bootstrap.py does not exist.

## Before opening night (first games Tue 2026-10-20)

1. DONE: facts store live-season build (fa81e35 on 2026-10-11; holdout guard + 14 tests d32464a on 2026-10-13). NBA_FACTS_NIGHTLY=1 only on the VM.
2. Reporter feed first poll after Devin's X sign-up (X_BEARER_TOKEN in .env, handles in configs/reporters.yaml; `--budget-posts 50`; shadow only).
3. `launchctl list | grep local.nba` shows daily-pretip, daily-morning, lineups, kalshi-snapshot; `uv run python -m nba.registry list`
   shows rung0_injury_elo v2 and props_context_residual v2 as production.
4. Ingest queue on game days (data/ops/ingest_queue_status.md): confirm daily and lineup jobs get through; decide whether to pause it.
5. Maintainer decisions (PROJECT_STATUS section 6): VM, rclone own client_id, checkpoint cleanup, integer-quantile route.

## Opening night 2026-10-20

- Supervised pretip run before the first tip: `uv run python -m nba.daily run --date 2026-10-20 --roster-source official --log-int-variant`, then data/ops/ALERTS.md.
- Mac on AC, lid open (AC sleep is 0; lid close and battery still sleep).
- Morning job: settle/report, no alerts, backup exists. Pretip hourly from 09:30: a forward row per slate game before tip, official roster, none after tip.
- Injury report fetched; lineups snapshots and T-30 shadow rows; T-15 arm decisions and lineups_t15 heartbeat; integer-variant rows;
  parlay shadow rows; Kalshi snapshot writing; P(play) shadow rows (R, C, C'). Run summary `dirty=False`.
- Open the first live odds raw file: 89 markets x 12 points, new shapes parse; watch live_book_odds growth.
- If Kalshi lists player props, alias review before any match is trusted. Next morning: settled rows, forward report, DNPs settle as DNP.

## First two weeks

- Official roster source through 2026-11-03, then `recent` automatically; compare coverage and rookie minutes error.
- Weekly forward health check (steward): calibration, bias per stat, production vs comparison models. No win/loss read on small n.
- Weekly: re-check the 30 missing 2026-27 tip rows (Devin, 2026-10-12).
- PPLAY_INTO_PROPS_LIVE first look fg3m ~Sun 2026-11-15; AVG_SHADOW_LIVE (once frozen) interim kill-only week 4, final Sun 2027-01-10.

## After opening night

- Score shadow arms forward (integer quantiles, T-30 lineups) with paired clustered bootstrap; promotion on live data only.
- Platt P(play) forward check before trusting unconditional parlay probabilities.
- Parlay stays shadow until the deferral gate (30 settled rows, 14 dates) and positive EV at the conservative bound; else "no positive EV found".
- SEQ_MINUTES after the P(play) decision; PBP_HALFTIME last. Pending Devin: RECENT_ROLE_FULL Stage 2 vs waiting for the live log.
- When production moves to the VM: NBA_FACTS_NIGHTLY=1.
- parlayhitters.ai build after 10-20 (designs in docs/BUSINESS_DRAFT_2026-10-11.md, APP_ARCHITECTURE, APP_SECURITY_BASELINE section 12,
  APP_CONTENT_SPEC); nothing touches nba/, ops/, configs/ before then. "jev engineering" = route LLM calls by difficulty (Jev-style router: cheap tier for easy, flagship for hard; Devin 2026-10-13); applies to the app's per-token calls, not the Claude Code build loop; confirm exact vendor URL/plan before buying; test routed vs unrouted on real app questions.

## Loose ends (recorded, not chased)
- AVG2 gate_rows.py (review 2026-10-14 S1): polars joins do not guarantee row order; 2025 tail alignment is verified only indirectly. Make the join order explicit (sort by key) BEFORE the November monthly refit.
- 2026-10-14: nba/explain/data.py load_report maps injured players to teams via arg_max(team_id) over all box rows <= game date, so for early 2026-27 games it reads 2025-26 team ids (team only; no outcomes). Systems use of the burned season; moot after the 20-game rollover (~10-23) folds 2025 into tuning. Better long-term: use the 2026 official roster. Not changed before the 10-17 freeze.
- NEW 2026-10-14 (null audit): possessions `assister_id` is NULL for accented passers (Jokic, Doncic at 0%; 91.2% coverage overall); no closed verdict depends on it; fix in the parser's name matching before any assist-network feature (archivist).
- Null audit re-tests queued for after 10-20, one pre-registered rule each: T185 F12 closing risk (gate failed on pre-v2 stints), then T176 RAPM (pre-v2 lineups; check power first); PBP_GPT T082/T084 UNKNOWN pending a code read.

- team_coaches wrong head coach in 14/87 team-seasons 2022-24, no mid-season changes; no production reader (archivist).
- stints miss 40% of <1-minute appearances; rebuild needs Devin's approval (DECISIONS 2026-10-10 ~23:55).
- 167 player_availability rows with NULL game_id stay NULL by design (postponed > 7 days, ambiguous, Cup final not in games).
- Pickle shim nba/sim/usage_redistribution.py stays until every cache post-dates 2026-10-10.
- Pretip launchd fires an hour ahead (Devin left it, 2026-10-10; covers the last tip).
- Loader-gate R1-R3/R5 (stamp transitive dependency hashes).

Finished items and the pre-prune text: docs/archive/next_session_done_2026-10.md.

## Opening night 2026-10-20: what broke (found 12:25–13:05 CDT, not yet fixed in code)

1. **Mac slept on battery** (18% at 12:25); the 10:26 predict ran until 12:23, the morning job never ended, app-export skipped 12 runs, the morning nba.duckdb backup was skipped. On AC since 12:29.
2. **Schedule fetch failed** (stats.nba.com unreachable while asleep), so tonight's games are not in `games`. `nba/parlay/top_source._prop_rows` joins `games` for home/away and dropped every player (`team_not_in_game`). Fix: fall back to the schedule cache. Live path: needs replay oracle + Devin's go.
3. **Official roster fetch failed at 10:26 → `recent` (last box-score team)**: summer movers (Jaylen Brown, Paul George, Vučević, Mitchell Robinson, Isaiah Joe, …) were forecast for their old teams. The 12:50 run fetched official rosters (30/30) and wrote rows on the new teams, but the export keeps the stale 10:44 rows too (players shown twice). `top_source._teams` also places players by last box-score team, so movers are dropped from the parlay panel. Fix: export and top_source should take team from the official roster cache when present.
4. **Deduped forecasts keep their first `made_at`**, so quotes captured later never pair (`received_at <= made_at`): unchanged players show a reference line, not the posted one; the parlay panel had 0 priced legs. Design question for Devin (re-stamp on unchanged re-run vs. capture a morning line before the first forecast).
5. **App guard**: export ships season 2025 box rows as display history (approved), but `app/api` refused them. Fixed on branch `agent/app-box-history-display` 3678136 (app checks pass, 287); unmerged.
6. Phone preview ran from that branch's worktree on `data/rehearsal/export_2026-10-20` with a scratch roster filter (removed 14 stale game-player rows); nothing in production was changed.
7. **Partial score scored (BOS@DET 0022600001).** The 15:20 CDT pretip ingest stored an in-progress 25-23 and `settle_pending` / `settle_eligible_pending` scored the two winner rows in `forward_scores` and `forward_scores_elig` once, for good (y=1, home win). Devin, 16:55 CDT: Detroit won, so y=1 is the true outcome and the rows stand (right by luck; log loss/Brier equal the final-score values). Fix on branch `agent/steward-settle-final-only` 8add39f (settle only 4 h after tip); merge awaits Devin's go and the oracle. Games 0022600002/3 are exposed until it merges.
8. **Oracle for the settle fix: IDENTICAL 89889f6d** (17:51 CDT, holdout rows ae4e37a/03c8ca7). Merge needs Devin's go. Lesson for BEST_PRACTICES: a fresh worktree already has a real `data/` (the repo tracks data/raw/manual_positions_2026-10-13/), so `ln -s main/data wt/data` lands INSIDE it as data/data; move `data/` aside first, then link, then `ls -la wt/data` must show a symlink before any replay. The first branch run silently fell back to proxy tips and 1 run/date (43,974 rows, DIFFERENT) because of this.
9. **Coverage check merged (item 4):** `uv run --no-sync python -m nba.ops.coverage --date <d> [--json]` (exit 3 on any FAIL). Tonight it flags: schedule gap (2 games not in `games`), 14 stale-team forecasts still the latest rows for their keys (official-roster runs never overwrite a mover's old-team row; production should withdraw them: live-path change, needs oracle + go), 0% line pairing for PHI@NYK, the 10:26→12:50 job gap, early-scored BOS@DET rows. NOT wired into the morning job yet (ops/nba_daily.sh = live path; Devin's go).
10. **Scorecard merged (items 1–3):** `uv run --no-sync python -m nba.forward.scorecard --date <d> [--since 2026-10-20]` → reports/forward_scorecard/<d>.md/.json. Diagnostic only (header says so); CIs are game-clustered and print n/a with one game. Reads 2025-26 team ids for the new-team flag (disclosure row ca55517; `--no-prior-team` turns it off). Agent choices to confirm: median of implied probabilities per side (not American prices), consensus point = most books, close snapshot else latest pre-tip. Not wired into the morning job (Devin's go). Tonight, one game, descriptive: 96% of squared error is per-minute RATE not minutes (George +23.6 from rate, −0.8 from minutes); model P(side) 0.61 vs hit 0.585 and Brier worse than the closing market (0.271 vs 0.256); new-team players under-forecast by +1.4 per stat (n=16).
11. **Wired (18:2x CDT, oracle IDENTICAL):** pretip runs `coverage` (informational); the morning job runs `coverage_yesterday` (alerts on FAIL) and `scorecard --since 2026-10-20` after the settle. First morning run 2026-10-21 ~07:07: expect coverage_yesterday to ALERT on 10-20's known FAILs (schedule gap, stale movers, pairing, job gap, early BOS@DET scores): that is correct, not a regression. Check reports/forward_scorecard/2026-10-20.md is rebuilt with 3 games.
12. **Scorecard benchmark = Pinnacle's own close** (merged; `--benchmark-book`, default pinnacle; consensus kept beside it; movement table t180→close). Tonight: Pinnacle priced 36 of 53 consensus-priced props (68%, above the 50% reversal trigger in DECISIONS 4403ca3). Game 1 vs Pinnacle: model Brier 0.279 vs 0.254 (n=36, one game, no CI). Pinnacle moved only 9% of its prop lines from T-180 to close (all by exactly 1.0).
13. **Full suite on the merged settle fix** (run in the branch worktree, identical nba/ and tests/): 4,092 passed, 2 failed, 3 h 21 m (shared the Mac with two replays). Both failures are environment: `test_pinned_versions_are_the_installed_ones` (worktree venv Python 3.12.15 vs pinned 3.12.13; main's venv has the pin) and `test_env_hook_default_off_and_on` (passes alone; order/env dependent in the full run). Main's own venv has no pytest (dev extras not synced); run suites from a worktree venv or sync dev extras on a non-game day.
14. **PRESEASON_ROLE_PRIOR** (Devin 21:3x CDT "Run test for statistical significance" = sign-off): draft f4b0dea → rev 2 0a86ab5 (harness, rehearsal 60/60, kappa 2022 starter 1.2435 / bench 0.9402, A0 8/8 to 1e-9) → adversary FIX_BEFORE_FREEZE (review merged; R1-R8 text-only; O3/O4 adopted as descriptive outputs, O1/O2 not) → rev 3 pending → freeze commit `prereg(PRESEASON_ROLE_PRIOR): <sha>` → `harness(...)` pin → priors to PREDICTIONS_LEDGER → run (2023 select / 2024 report, 2025 never loaded) → post-result attack → ledger row.
