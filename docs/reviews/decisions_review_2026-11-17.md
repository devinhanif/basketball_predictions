# DECISIONS.md review: what to redo or rethink (2026-11-17)

Author: archivist. Read-only review of `docs/DECISIONS.md` (1,646 lines) against the project as of Tue 2026-11-17 18:5x CST
(`main` at `04763a5b` when I started; one docs-only commit `2f2453b0` landed during the review). Branch
`agent/archivist-decisions-review`; this file is the only change. No DuckDB file was opened, no season-2025 data was read, no
fit was run. Line numbers are lines of `docs/DECISIONS.md` on `main` at `04763a5b`.
Model class: none (no model drafted, scored or reviewed). Playbook check: not applicable; every performance number below is
quoted from the ledger or a DECISIONS row with its own n and CI, none is new.

Reading coverage: all 1,646 lines read in order. By my count that is about 579 entries (363 table rows beginning `| 2026-`,
61 `## 2026-` entries, 155 `**2026-` entries). I flagged about 50 line anchors (listed in section 5), grouped into the 15 table
rows. The rest I judged closed (a verdict, a merge or a one-off action that nothing later contradicts).

Labels: **FACT** (with source), **ARITHMETIC** (mine, assumptions stated), **OPINION** (mine, argued). The four questions:
Q1 reversal condition met or checkable; Q2 superseded or contradicted; Q3 stalled; Q4 worth rethinking.

---

## 1. Top table (ranked by how much it matters this month)

| # | Decision (date, name, line) | Q | What is true today | Action and whose call |
|---|---|---|---|---|
| 1 | 2026-10-07 "Frozen holdout = season 2025 ... rollover to 2026 once >= 20 games" (L10; same sentence in CLAUDE.md and HOLDOUT_ACCESS_LOG rule 3) | 1, 2, 4 | The trigger fired long ago: 205 games scored by 11-16 (`reports/forward_scorecard/2026-11-16.md`: "rows scored 18144 (205 games)"; threshold 20, `nba/daily/season.py:15`); DECISIONS L1435 already counts 28 games through 10-23. No DECISIONS or HOLDOUT_ACCESS_LOG row records applying, deferring or declining it. `nba/daily/report.py:489` is written to print "HOLDOUT ROLLOVER TRIGGER FIRED" every morning (I could not read a generated report to confirm it printed). Meanwhile 2026 labels are already training rows (monthly AVG refit, HOLDOUT_ACCESS_LOG 11-08 row; FORWARD_PREREG section 9 (iii)). | **Rethink, then close with a row.** Devin's call (holdout). My default in section 4, row P1: 2026 is the live forward test, not a never-tuned holdout; 2025 stays burned; rule 3 is closed as "trigger fired 10-2x, not applied, reason". |
| 2 | 2026-10-14 "app shows the best model: AVG" (L1198) and 2026-11-08 "Carry Oct gate" for the November AVG refit (L1549) | 1, 2, 3 | Reversal condition of L1198 ("AVG rows missing or stale on game nights") is met: watchdog "avg_shadow is serving stale weights for 2026-11 (newest bundle 2026-10)" every 6 h through 11-17 17:54 (`data/ops/ALERTS.md`); `data/avg_shadow/bundles/` holds only `2026-10`; handoff REPORT section 7: 15,276 AVG rows stale, 6,604 shown rows on the fallback. The November Colab run (Devin's, ~5 min, staged in `data/colab/avg_shadow_monthly/2026-11/run`) was still not done 9 days after L1549. The notebook and runbook exist only on unmerged `agent/steward-avg-monthly-refit` (556 commits behind main). ARITHMETIC: rows run about 1,470/day (L1549: 17,544 valid Oct rows over 12 days); 18 stale days of about 83 by the 01-10 look is about 22%, over the rule's 10% limit (AVG_SHADOW_LIVE_2.md line 88), even if the bundle is installed tomorrow. | **Redo the refit now, and tell Devin the 01-10 PASS is already out of reach.** Devin (Colab run; the verdict note must state the stale share and the L1549 deviation). Also due: December bundle by 12:00 CT on 12-01, and L1549's own condition (reviewed live-row gate loader before 12-01) has no work behind it. |
| 3 | 2026-11-10 GAME_SCRIPTS_FORWARD "freeze waits for agent/market-game-scripts to merge" (L1599, L1602; scope L1593) | 3 | Not merged, not frozen, nothing scoring it. `agent/market-game-scripts` tip `f04d1116` (11-10 16:52), 3 commits ahead of, 298 behind `main`, review "adversary fixes M1 M2 m1-m7" applied, no merge row since. `agent/modeler-game-scripts-forward` tip `a7760b7d` (rev 2, rule `563a1c67`), 3 ahead, 321 behind; `docs/prereg/GAME_SCRIPTS_FORWARD.md` is not on `main`. It is also the branch that writes `combo_<night>.json`: `main` has no combo stage in `nba/export/` (only `sits_core`, `teammate_sits`, `line_cal_fields` ...), so the in-flight combo screen reads fixtures only. | **Decide: merge the export branch (live path: oracle + Devin's go) and freeze, or close the forward test and keep every script number "exploratory" for the season.** Devin's go (live export path). OPINION: freeze the rule text now; the harness can follow (T510 precedent). |
| 4 | 2026-10-22 LINE_MOVE_TOWARD_MODEL T510 and T510b (L1381, L1434; scorer merged L1448) | 3 | Horizon (>= 300 qualifying markets AND 14 complete dates from 10-22) was projected for about 11-04 to 11-06 (prereg; L1434 says 11-06 for T510b). Nothing has scored it: `reports/line_move/` holds only `dryrun_record.json`; ledger rows T510 still read "No result yet"; the scorer is manual ("Not in the nightly path", `research/line_move/__main__.py`). Live capture has day directories for every date 10-22..11-17 except 11-03. | **Run `preflight` then `score --rule T510` on a DB copy (main session), result to the ledger.** A PASS is a blend-weight proposal, Devin's; the per-sign split reading note (L1465) applies. |
| 5 | Frozen forward rules with no scorer: PPLAY_INTO_PROPS_LIVE (L736), FAV_DIP_PAPER (L1381), STAR_FLOOR_PAPER (L1395) | 3 | `research/eval/pplay_into_props_live.py` has only a `rehearse` entry; its fg3m final look was due about Sun 11-15 (prereg: fg3m n 2,200, week 4) and no ledger row exists. FAV_DIP and STAR_FLOOR texts say "harness is written after this freeze"; `main` has only the `research/scratch/*gate*` scripts; look about late Nov. The paper trader that would log their trades (`agent/market-paper-trader`) is unmerged. | **Write the three scorers (adversary-checked against the frozen text) or log a row saying which looks will be skipped.** Main session builds; Devin only if a look is dropped. |
| 6 | In-game and halftime paper logs without a frozen rule: halftime scorer installed 10-27 (L1533), in-game checkpoints ON 11-10 (L1590), T508 "no promotion, no live use, no sizing" (L1375), INGAME_WINPROB_V2 frozen 10-23 (L1405), ESPN poller ON at 15 s (L1590) | 2, 3 | L1375 and L1590 disagree: T508 arm M was WOUNDED (about 94% of its gain was a weak baseline; ledger T508-attack) and is now logged every 5 game-minutes and edited onto Discord cards. HALFTIME_OVERS_VS_PRICE is still a DRAFT (`docs/prereg/HALFTIME_OVERS_VS_PRICE.md`), three weeks of paper rows accrue without a frozen scoring rule (L1365's own standard: freeze before the first scored trade). INGAME_WINPROB_V2 is frozen at `3c04619` on unmerged `agent/market-ingame-v2`; its text is not on main; no row since 10-23. ESPN_FRESH_POLLER_NIGHT1 is a DRAFT while the poller has run since 11-10. | **Freeze (or explicitly declare descriptive-only) each log before more rows accrue; supersede L1375's "no live use" with a row.** Devin (outward-facing display, his override L1585/L1590); main session drafts. |
| 7 | LINE_CAL_LIVE (L1543) and CALIBRATION_EVERYWHERE (L1427, L1429), bench-input mismatch (L1542), display lock (L1582) | 3 | One look at 200 games / 5,000 legs, projected about 2026-12-01 (prereg; PROJECT_STATUS section 3); holdout-log row first, heartbeat green 3 days, run at `bb064e7`. CALIBRATION_EVERYWHERE is "awaiting Devin's go" since 10-23 (L1429); `CAL_FORWARD` never drafted; the prereg's own table says so (`LINE_CAL_LIVE.md:23`). L1542's loose end stands: the card, parlay panel and export serve the map with `starter_rate` where it was fit on `starter10` (12.80% disagreement on the map's 72,674 fit legs). Odds key outage 11-14..16 (row 8) removes about 3 nights of Pinnacle t60 legs. | **Leave the look date; do the three look prerequisites this week; park CAL_EVERYWHERE with a row until the look reads.** Devin (starter10 fix is live path). |
| 8 | 2026-10-14 odds plan kept all season (L1101; plan size L806) and 2026-10-24 "~280k credits drawn by something other than live capture (unexplained)" (L1517) | 3, 4 | PROJECT_STATUS section 7.1: key deactivated from 11-14, `401 DEACTIVATED_KEY` for about 3 nights; Devin replaced the key 11-16 ~18:22. `data/ops/ALERTS.md` shows `odds capture all rc=4` every 5 min until 18:19 on 11-16 and none after; whether rows are being written is unverified. L1517's unexplained draw was never closed. BEST_PRICE_FORWARD (L1565: six US books + Kalshi) and LINE_CAL_LIVE (Pinnacle t60) both depend on this capture. | **Confirm the key writes rows and read the credit meter against the 5M/month plan; close L1517 with a row.** Devin (money, vendor account). |
| 9 | 2026-10-14 "Production moves to a home mini PC" (L1083-L1086; amendment: Mac stays until the box exists) | 3, 4 | Nothing bought; the MacBook is still production. Sleep/battery incidents since: 10-11 collector hole (L781), 10-23 dark capture (L1433), 10-25 hibernation (L1485), 11-16 and 11-17 watchdog "ACTION NEEDED ... Mac can sleep" (`ALERTS.md` 11-17 18:44), and the 11-17 morning heartbeat stale (`ALERTS.md` 11-17 11:43). | **Decide: buy the box, or write the rule "AC power, lid open, `caffeinate -dimsu` on game days" and stop treating each sleep as news.** Devin (money, production). |
| 10 | Data-source terms: Basketball-Reference (coaches L583, "BR excluded" L1001, salaries L1348-L1349), nba.com s.9 / ESPN / headshots (L1560-L1562, L1585, L1597) | 2, 4 | L1001 (10-12) says BR "terms forbid automated access and AI/model use" and excludes it, yet the 10-11 coach source is BR (L583; `nba/data/head_coach_games_reference.csv` is committed in a PUBLIC repo, L1353) and 152 BR pages were pulled 10-21 (L1348-L1349). The archivist check of 10-27 could not read the live terms and left "delete if terms forbid" undischarged (`docs/reviews/bref_terms_check_2026-10-27.md` section 0). `data/raw/bref_salaries_2026-10-21` (106 MB, 457 files) still exists. L1562 accepts terms risk "before any revenue ... terms review"; no row records that review. | **Devin reads the two Sports Reference pages and the nba.com s.9 text, then one row: keep, delete, or license.** Devin (legal, outward-facing). Default if no answer: stop using BR in anything new. |
| 11 | 2026-10-25 fast-arb merged, not installed (L1486, L1507, L1515) and JARVIS "arbitrage first" goal | 3 | Merged 10-25; still "built but not installed" (PROJECT_STATUS section 5); `.env.fastarb` does not exist; Devin's two reads (Kalshi and Polymarket US settlement rules, L1492, L1507) have no row; JARVIS cannot say "arbitrage" or "strong" without the feed (L1500). 3 weeks. | **Do the reads and install, or log "shelved, reason".** Devin (money, install, terms). |
| 12 | Options waiting on Devin with no later row: QUESTIONABLE_CASCADE A-D (L1350), ROLE_PRIOR_SALARY a-d (L1352), MINUTES_DECISION six choices (L1546), REPORTER_FEED X ceiling (L1535), SHARP_WALLETS steps 2-3 (L1522), D1 tripwire audit (L1536), T-30 arm future (L1385) | 3 | All older than 3 weeks except MINUTES_DECISION (15 days). T-30: 211 of 212 decisions skipped (PROJECT_STATUS section 2), so D1 is moot: the arm logs almost nothing. | **One batch row: park or close each, with the default stated.** Devin; Claude drafts (section 4, row P3). |
| 13 | 2026-10-11 LLM shadow "frozen before opening night" (L513, L547, L865) | 3 | Never frozen (`LLM_SHADOW_2026_27.md` status DRAFT), `NBA_LLM_SHADOW` unset, 0 rows, `MONTHLY_CAP_USD` unset (L865). Opening night was 29 days ago. | **Park with a row (section 4, row P4).** Devin (API spend). |
| 14 | 2026-10-21 "CI red since 10-15 ... standing failure" (L1331, L1343) and PROJECT_STATUS section 7.3 "CI is not green" | 2 | Closed by commit `04763a5b`: "CI run 44192515903 green on Linux incl. the full suite, 6177 passed". PROJECT_STATUS (rewritten 11-16) still says none of the last 40 runs passed and "No commit to main since 2026-11-10"; `main` has merged since (L1645, `61e3a741`). | **Close with a row (section 4, row P2).** Main session. |
| 15 | CLAUDE.md "What the data has taught us" / How we build vs later rows | 2 | CLAUDE.md says history is 2019-2021 in `data/history/` (reversed by L1000, L1068: 2013-2018 loaded); and "`season=2026` becomes the holdout once it has >= 20 games" (see row 1). The "keep your money" line stands for the model, while JARVIS names a play by Devin's override (L1487) and cards print "No over has positive EV after fees tonight" (L1575 M6). Consistent, but CLAUDE.md carries no pointer to either. | **Two-line CLAUDE.md edit after row 1 is decided.** Devin/main session (CLAUDE.md is not mine). |

---

## 2. Findings by question

### Q1. Reversal condition met or now checkable

**L10 holdout rollover (top-table row 1).** FACT: the rule says "rollover to 2026 once >= 20 games are played".
`docs/DAILY_PIPELINE.md:127` says the report states the action and that "the pipeline never edits the log; do that by hand".
`HOLDOUT_ACCESS_LOG.md` rule 3 (line 26) calls it a "standing TODO, re-check every session". `FORWARD_PREREG_2026_27.md`
section 9 says rollover "does not relax or reset anything" and that walk-forward retraining on played 2026 rows is allowed.
I searched DECISIONS, the holdout log, NEXT_SESSION, PROJECT_STATUS, the ledger and `git log --grep rollover`: no row decides it.
What would change if it were applied: `docs/prereg/` rules that reopen "after the holdout rollover" (PRESEASON_ROLE_PRIOR, L1324);
every gate that says "2025 touch spent" would be re-labelled; the hard-coded `season <= 2024` guards in research harnesses
(L1330, L1336) would not move by themselves. OPINION: applying rule 3 as written is incoherent now. A season that forward
rules score daily, that displays outcomes next to forecasts, and that monthly refits train on cannot be a "never tuned
confirmatory" holdout. The clean statement is: 2026 is the live forward log (the only clean test, as CLAUDE.md already says);
2025 stays burned; a new virgin holdout does not exist until 2027 data. Decide that in one row.

**L1198 best model for the app.** Reversal "AVG rows missing or stale on game nights": met since 11-01 (row 2).

**L1549 Carry Oct gate.** Its reversal is "a reviewed live-row loader ... before 2026-12-01". 14 days left, no branch for it.

**L1543 LINE_CAL_LIVE.** Look projected about 12-01, prerequisites in the prereg (holdout row first; green ingest heartbeat 3 days; run from a worktree at `bb064e7` with `--data-root` on main; commit results at once). Checkable now; the stale-bench-input disclosure (L1542) is part of the rule.

**L1381 / L1434 horizons.** T510/T510b horizon passed (row 4). FAV_DIP / STAR_FLOOR horizons (100 trades AND 14 dates, about late Nov) are near.

**FORWARD_PREREG looks.** L14 was recorded 2026-11-02 (`data/checkpoints/L14_2026-11-02.json`, nightly `checkpoint --look auto`
wired at `ops/nba_daily.sh:255`). L30 for the INT/LT/T-30 families and FORWARD_T15's L30 (prereg: about 2026-11-20) are due in
days; the machinery works, nothing to do but not to peek.

**L863 / L916 official-roster window** to 2026-11-30: a keep-or-flip decision for Devin in 13 days (PROJECT_STATUS section 8). It needs a row either way.

**L1562 terms risk accepted "before any revenue, paid tier, or public launch".** No revenue yet, so not triggered; but the
betting switch (L1645) is "OFF until Devin has done the legal read" and the public-link PBP override (L1585) is the nearest
outward-facing item. Keep the row visible.

### Q2. Superseded or contradicted without a row saying so

1. **L1375 vs L1590 (T508 live use).** See top-table row 6. FACT: L1375 "no promotion, no live use, no sizing on it"; L1590 turns
   the T508 arm M ON for every 5 game-minutes (paper log + silent card edit; "no new ping or stake"), and the 11-10 override
   (L1585) streams "checkpoint model numbers" on the public app link. No row reconciles them.
2. **L1001 vs L583 vs L1348.** See top-table row 10.
3. **L14 "History starts 2019-20, not 2013"** is reversed by L1000 (acknowledged there) and by 2013-15 data (L1068), but CLAUDE.md still states 2019-21.
4. **L1198 "AVG is the app's display model"** is now half true: stale AVG rows are replaced by production (the fallback in the same row). The `/best-model` behaviour changed from the row's premise on 11-01 with no row.
5. **L1331/L1343 CI red** is closed by `04763a5b`; PROJECT_STATUS section 7.3 is stale (row 14).
6. **L31, answer Q5 ("archive the Colab tooling and drop colab-runner (no GPU jobs planned)").** GPU jobs are now routine (monthly AVG refit, neural family, SEQ runs) and `research/colab/jobs/avg_shadow_monthly` lives on an unmerged branch; the tooling was archived but the live path still depends on a hand-run Colab (see Q4).
7. **PROJECT_STATUS section 2 table** says nothing about `props_avg_live2` being stale; it is only in section 7.

### Q3. Stalled

| Item | Started | Last row | State |
|---|---|---|---|
| GAME_SCRIPTS_FORWARD | L1593 11-10 | L1602 11-10 | unmerged, unfrozen (row 3) |
| T510 / T510b scoring | L1381 10-22 | L1448 10-24 | scorer merged, never run (row 4) |
| PPLAY_INTO_PROPS_LIVE, FAV_DIP, STAR_FLOOR scorers | L736, L1381, L1395 | none | no harness (row 5) |
| INGAME_WINPROB_V2 | L1405 10-22 | L1405 | frozen on `agent/market-ingame-v2` (unmerged), stage-1 builders never reported |
| HALFTIME_OVERS_VS_PRICE | L1520 10-27 | L1533 10-27 | draft; its scorer runs live |
| ESPN_FRESH_POLLER_NIGHT1 | L1572 11-10 | L1573 | draft; poller ON since 11-10 |
| CALIBRATION_EVERYWHERE / CAL_FORWARD | L1427 10-23 | L1429 | "awaiting Devin's go" |
| AVG November refit; live-row gate loader | L1549 11-08 | L1549 | not run / not built (row 2) |
| MINUTES_DECISION (ROLE_SHIFT part 1) | L1545 11-02 | L1546 | draft on `agent/modeler-role-shift` (unmerged), six choices open |
| QUESTIONABLE_CASCADE, ROLE_PRIOR_SALARY options | L1350, L1352 10-21 | none | open options (row 12) |
| REPORTER_FEED install | L1535 10-27 | none | merged, not installed; needs X ceiling |
| fast-arb install | L1486, L1507 | none | row 11 |
| LLM shadow | L513 10-11 | L865 10-12 | row 13 |
| mini PC | L1083 10-14 | L1086 | row 9 |
| `agent/archivist-report-stamp-fix` `2dfbd07` (report known-at fix; "awaiting Devin's merge decision" L1353) | L1315 10-16 | L1353 10-21 | 1 ahead / 1,285 behind main; the history re-stamp "deferred to after opening night" never reopened |
| HOLDOUT rollover | L10 | none | row 1 |

Branches named in rows and NOT merged into `main` (`git branch -a --no-merged main`): `agent/market-game-scripts`,
`agent/modeler-game-scripts-forward`, `agent/market-ingame-v2`, `agent/steward-avg-monthly-refit`, `agent/market-paper-trader`,
`agent/market-calibration-everywhere`, `agent/modeler-role-shift`, `agent/modeler-halftime-overs`, `agent/modeler-avg-november-prep`,
`agent/archivist-report-stamp-fix`, `agent/steward-export-gate-semantics`, `agent/modeler-season-boundary`,
`agent/market-bayes-kelly`, `agent/market-jarvis-settled-fair`, plus about 40 more adversary/modeler branches. Most are drafts with
a closed verdict or a review; the ones that matter are the first six. I did not check which unmerged branches' commits are
pinned by a frozen rule's sha (that would make deletion unsafe).

GAME_SCRIPTS_FORWARD trace, as asked. (1) Branch `agent/market-game-scripts`: not merged (above). (2) Rule: rev 2, text sha
`563a1c67`, status DRAFT, only on `agent/modeler-game-scripts-forward`. (3) Scoring: nothing; L1602 says the freeze waits for the
merge, and L1599 re-picked O3 ("score the APP's number, season-2026 games only") after B1 found the harness scored the parlay
engine's `mu_t`, not the app's. (4) Consequence: L1593 says every per-script number is "exploratory until (4) reads";
(4) cannot read, so the word "exploratory" cannot lift. OPINION: the dependency is backwards. Non-negotiable 1 says the rule
is committed before any result; the rule text does not need the export branch merged. Waiting on the merge keeps the script
numbers visible (combo screen, 11-17) while the test that would grade them sits unfrozen, and every night of delay is a night of
2026 outcomes the rule cannot honestly count (it starts "from the first ET date after the freeze").

### Q4. Worth rethinking (OPINION; evidence cited)

1. **Rollover as a concept (row 1).** See Q1.
2. **A shadow that needs Devin's hands every month (L1073, L1549).** The monthly Colab refit has now failed to happen once
   (November) and the rule's 10% stale limit turns that into a near-certain INCONCLUSIVE at the January look. CLAUDE.md's budget
   for Devin is "a few minutes a day". Evidence: ALERTS.md alerts since 11-01; L1549 itself ("the >10%-stale limit is already
   close"). Options for Devin: run it now and add a hard reminder on the 1st; or move the refit to CPU (T478's own pass was
   pts -0.0133 [-0.0158, -0.0109], n 28,173 rows / 1,321 games, ledger T478, so the value is real if the live arm can run); or
   accept INCONCLUSIVE and say so in a row. Do not quietly let the staleness decide.
3. **Paper logs without a rule (row 6).** The project's own history (L1255 TAIL_LEG_CAL frozen without sign-off; L1365
   "pre-registered scoring frozen before its first trade") says logs without a frozen reading become claims later. Three weeks
   of halftime rows and one of checkpoint rows exist; a late freeze is still honest only if it states the first row date and
   that rows before it are exposed.
4. **Display of a WOUNDED model on public surfaces (L1375, L1585, L1590).** T508's "pass" was 94% baseline weakness (ledger
   T508-attack: M minus the fair baseline about -0.0024 to -0.0025 log loss, floor 0.005). Showing arm M numbers on cards and a
   public link without that caveat in the label reads as an edge the ledger denies. Cheap fix: label every in-game number
   "model estimate, not tested against a fair baseline above the floor".
5. **Production on a laptop (row 9).** Four months of the same failure mode, each logged as a new incident.
6. **JARVIS "always a play" vs "keep your money" (L1487, L1575, CLAUDE.md).** Already logged as Devin's override and softened
   by L1575 M6, so I do not flag it as a defect; it is a CLAUDE.md pointer (row 15), not a reversal.
7. **Issue tracker as the home for open decisions (L1353: "every follow-up or decision becomes an issue").** The decisions
   waiting list in PROJECT_STATUS section 8 and NEXT_SESSION is the same list again. I could not check GitHub (see section 6).
   If the issues are stale, the rule from L1353 has not earned its keep; if they are current, PROJECT_STATUS should link them
   instead of restating.

---

## 3. For the combo build (decision rows that constrain or should shape it)

The build as described: leads with "if the game plays out this way, these legs are more or less likely"; calibrated chance only;
how often each script has happened; per-player counts under each script from season 2026 only; the posted line's movement,
not interpreted. Branch `agent/app-combo-screen` (`c64a8b14`, 1 ahead of main) adds `app/api/.../combo.py`, a route,
`app/fixtures/combo/combo_2024-11-12.json`, error codes.

| Row (line) | What it requires of the build |
|---|---|
| 2026-11-09 combo mode merged (L1567), contract `docs/contracts/combo_export_v1.md`, review `docs/reviews/combo_math_2026-11-10.md` | Pure probability: no price, fee, EV, stake or Kelly anywhere in payload or UI (the API route refuses keys with those words; keep that). Every script labelled "exploratory" (all four; grind's run-2 pass is not confirmatory, review B1). Legs with calibrated P > 0.75 or raw >= 0.80 labelled "above the tested range"; any k >= 3 combo labelled "model estimate, not checked against results"; target search band 0.35-0.75; raw shown only in Analyst mode, beside calibrated; probabilities are conditional on playing, with `p_all_play` / `p_joint_incl_dnp` shown or "unknown". |
| 2026-11-10 12:52 four pieces (L1593) | Per-script numbers stay "exploratory until (4) reads". Piece (4), the forward calibration test, does not exist (row 3 above), so the label does not lift this season. Per-case "picks" and the live script tracker were Devin's override; none is in this build. |
| 2026-11-10 13:42 / 16:29 GAME_SCRIPTS_FORWARD (L1599, L1602) | The rule scores "the APP's number" with `mu_t` from season-2026 games only (2 dp). Main's `combo_export_v1` takes `mu_t` as an input (`nba/parlay/combo_build.py:167`) and has no nightly producer on `main`; the producer with the season-only fix is on unmerged `agent/market-game-scripts`. The build needs the producer merged, or it shows fixtures only. Confirm before wiring that `mu_t` never reads season 2025 (`game_model.py` fits on seasons <= 2024; the as-of total feature's source for 2026 games is the open question). |
| 2026-11-10 02:51 LINE_CAL_LIVE display hygiene (L1582), 2026-11-09 23:02 disclosure (L1576), frozen rule (L1543) | No prop forecast beside its outcome for slates from 2026-10-30 until the look (about 12-01). Raw P is removed from every payload. Per-player counts "from season 2026 only" are outcomes: any count that includes games on or after 2026-10-30 and sits next to a calibrated chance for the same player and stat re-creates the exposure. Either show counts only from 2026-10-20..29, or show them on a surface without that player's chance, or wait for the look. (My reading of L1582; the rule says "chance/call next to outcomes", and a count of hits at a line is a prop outcome.) Calibration tab label: "raw model, before calibration" (L1576). |
| 2026-10-15 23:42 display-only exception for 2025 box scores (L1306; log row `HOLDOUT_ACCESS_LOG.md:96`), API refusal unless declared (L1354) | The display table holds 2025 box rows (the export's `box_history_seasons`). Per-script counts must filter `season = 2026` in the API, not in the browser. Three analyst reads of that display table for hit-rate memos are already disclosed (log rows 10-20 x2, 11-09); the combo screen must not become a fourth. No model may be scored on those rows. |
| 2026-11-10 unlogged read incident (`HOLDOUT_ACCESS_LOG.md:210`) | Five players' conditional hit rates on pooled 2022-26 data (Tatum 30+, Embiid 10+, Maxey 15+, Brown 15+, LeBron 10+; wins / losses / 15+ wins) were seen. The log says the follow-up "player-win dependence" pre-registration "must not use these numbers as priors" and must note them in any 2025 touch. FACT: I found no such pre-registration or branch (searched `docs/prereg`, ledgers, branches). The 2026-only per-player script counts are the clean replacement and should be built with a visible n per cell and no thresholds chosen from those five. Draft the pre-registration before the screen shows any win-conditional count as a finding. |
| GAME_SCRIPTS context: realised share of shootout/grind by season (combo contract) | "How often each script has happened": show the 2026 realised count with n, beside the prior and the 22%-31% historical range the contract cites; the grind definition (+-12 around the expected total, band amended after run 1) is exploratory by definition (review B1). Do not display a script frequency from season 2025. |
| 2026-11-10 TEAMMATE_SITS display rule (L1589; T526, T526-a) | If the screen mentions who sits: pts direction plus one team-level number, rebounds direction only ("uncertain size"), ast/fg3m never; finished games sent without per-pick numbers (LINE_CAL_LIVE lock). |
| LINE_MOVE preregs: T510 / T510b (L1381, L1434, scorer L1448), reading note (L1465), drift x lean (L1443), MODEL_CLV (L472-L480) | Show the line's movement as numbers (earlier line, later line, time stamps) only. No "moved toward the model / toward the player / sharp money" wording: T510 and T510b have no result (row 4), a market-wide pre-tip drift reads as "movement toward the model" under the model's lean (synthetic: passes 150/150 and 149/150), and MODEL_CLV found the model adds nothing to line-move direction (the Pinnacle-vs-DK gap alone predicts it; closed, T340-T381). Data gap: the export's `prop_lines` keeps the newest quote per (book, market, line, side) (L1117), so no history exists for a movement display; the betting API is "one quote per line, newest provably before tip" (L1624). A movement field needs an export change (not in the combo contract). |
| App never computes EV (`app/ARCHITECTURE.md:3`), "pages do no arithmetic" (L1616, row 13), `PROPOSAL_export_fields_2026-11-17.md:6` ("the app never computes a probability") | FACT of a conflict: the combo contract section 5 is "what the browser computes (normative)": the joint (Genz/qmc), lift and target search run in the browser; the route docstring says "the browser does the math". Reconcile before merge: either the exception is recorded in a DECISIONS row (the copula is model code ported with golden vectors, no EV), or the joint moves server-side with the four browser-computed numbers the handoff already proposes moving (REPORT section 6). Either way no EV, fee or price. |
| CALIBRATION_EVERYWHERE (L1427, L1429; plan on `agent/market-calibration-everywhere`, never merged) | Not available as an accessor. "Calibrated chance only" must come from the existing frozen maps (LINE_CAL main-line map T493; rung map T494) with their scope limits: only 266 of 1,001 Kalshi prop markets fell inside a validated scope on 10-20..23 (L1429). Combo legs at lines other than the DK main line are extrapolations; show the combo contract's above-tested-range label, not a bare number. The served bench input differs from the fit input (L1542): disclose. |
| Display of one model among several (L1198, L1614-L1615 rows 11-12) | Say which model each chance came from; production is the fallback when AVG is stale (now, row 2). Do not show running scores of frozen arms before their look (scorecard rule, L1614). |
| Terms and photos (L1597, L1560-L1562) | Player photos load from cdn.nba.com in the browser (Devin's call); a new screen must reuse `Avatar.tsx`, copy nothing. No revenue or public launch before a terms review (L1562). |

Not found and therefore not constraining: any row on combo UI copy beyond the above; no row sets a default lens for combo
mode (L1607 says Learn shows no prices, which suits a no-price screen).

---

## 4. Proposed DECISIONS rows (DRAFT; the main session commits; Devin's calls are named)

Format follows the table: `| date | decision | why | what would reverse it |`. Dates left for the committer to stamp from `date`.

P1 (needs Devin's yes; closes L10 and HOLDOUT_ACCESS_LOG rule 3):
```
| 2026-11-17 | Holdout rollover (L10, HOLDOUT_ACCESS_LOG rule 3): the trigger fired (205 completed 2026-27 games by 11-16; 20 required) and is closed as NOT APPLIED. Season 2026 is the live forward log, the only clean test (CLAUDE.md), not a never-tuned holdout; 2025 stays burned and is not folded into a tuning pool; no virgin holdout exists until 2027 data. Rules that reopen "after the rollover" (L1324 PRESEASON_ROLE_PRIOR) are re-read as "after a new holdout season exists". The morning report's TRIGGER FIRED line is replaced by this row's text. | Forward rules score 2026 daily and monthly refits train on 2026 labels (HOLDOUT_ACCESS_LOG 11-08), so the "never tune before first confirmatory use" clause of rule 3 cannot hold; folding 2025 into tuning would remove the only season every spent touch is measured against. | Devin prefers to fold 2025 into the tuning pool (then every 2025-touch row is re-labelled "tuning" and the `season <= 2024` guards are reviewed one by one, never widened by one edit). |
```
P2 (simple closure; main session):
```
| 2026-11-17 | CI red (L1331, L1343) closed: `04763a5b` records CI run 44192515903 green on Linux with the full suite (6,177 passed). PROJECT_STATUS section 7.3 and its "no commit to main since 2026-11-10" are stale and are refreshed at the next status rewrite. | The verifier runs again; the standing failure was the F1 pin test, now skipped (handoff REPORT section 7). | A later red run on `main`. |
```
P3 (needs Devin's yes; each default is reversible):
```
| 2026-11-17 | Open options older than 3 weeks parked by default: T-30 arm continues as a logged shadow with no decision weight (211 of 212 decisions skipped; D1 audit proposal L1536 withdrawn as moot); QUESTIONABLE_CASCADE: close the historical test, live-only option D stays a draft (L1350); ROLE_PRIOR_SALARY: close at the gate, option (d) salary-implied role display not built (L1352); REPORTER_FEED_LIVE: not installed, no X spend until Devin names a monthly ceiling (L1535); SHARP_WALLETS_PERSIST: parked until Devin reads Polymarket's terms (L1522). MINUTES_DECISION (L1546) stays open for Devin's six choices. | Each has waited with no row; non-negotiable 7 wants the direction written down, and parking is reversible. | Devin picks another option for any item (a new row; the draft rules are on branches). |
```
P4 (needs Devin; spend):
```
| 2026-11-17 | LLM shadow forecaster (L513, L547, L865) parked: not frozen, `NBA_LLM_SHADOW` unset, no rows; `MONTHLY_CAP_USD` stays unset so no call can be made. | It was meant to be frozen before opening night; 29 days on no rule, no key cap and no row exist, and its test is forward-only. | Devin sets the cap and asks for the freeze (then it needs a rule frozen before its first scored row). |
```
P5 (needs Devin's yes; supersedes L1375's "no live use" for display):
```
| 2026-11-17 | In-game and halftime numbers (T508 arm M checkpoints, halftime scorer, ESPN poller) are DESCRIPTIVE paper logs until a rule is frozen: no claim, no promotion, no stake, no alert. Every surface labels them "model estimate; T508 beat a fair baseline by less than the floor (ledger T508-attack)". HALFTIME_OVERS_VS_PRICE and ESPN_FRESH_POLLER_NIGHT1 are frozen or dropped before 2026-12-01; rows before the freeze commit are marked exposed. INGAME_WINPROB_V2 (frozen `3c04619` on `agent/market-ingame-v2`) is merged for the record or closed. | L1375 and L1590 disagree; logs without a frozen reading become claims later (L1255, L1365). | A frozen rule reads the logs, or Devin withdraws the display. |
```
P6 (Devin's call; records the staleness):
```
| 2026-11-17 | AVG_SHADOW_LIVE_2: 2026-11 refit not run by 11-17 (18 stale days and counting). ARITHMETIC: at about 1,470 rows a day the stale share at the 2027-01-10 look is about 22% even if the bundle is installed 11-18, over the 10% INCONCLUSIVE limit (rule line 88); the second look at season end is borderline. The 12-01 December bundle and L1549's live-row gate loader are still owed. Choice: (a) run the November Colab refit now and calendar a reminder for the 1st of each month; (b) move the refit to CPU under a new rule id; (c) accept INCONCLUSIVE and say so. App display of AVG (L1198) follows the fallback until the bundle is installed. | The rule's own stale limit makes the January PASS unreachable, and nothing written says so. | The stale share is recomputed from the table and is below 10%. |
```
P7 (needs Devin's go; export change):
```
| 2026-11-17 | GAME_SCRIPTS_FORWARD: freeze the rule text (rev 2, `563a1c67`) now and let the harness follow, or close the forward test; either way `agent/market-game-scripts` merges only with the oracle and Devin's go. Until a frozen rule reads them, every script number stays "exploratory" and the combo screen shows season-2026 counts with n only. | Non-negotiable 1: the rule is committed before results; the export merge is an implementation step, not a precondition of the rule. | The export branch fails the oracle or review; Devin closes the test. |
```

---

## 5. Counts

- Entries read: all of `docs/DECISIONS.md` (1,646 lines), about 579 entries.
- Line anchors flagged in this review: L10, L14, L513, L547, L583, L736, L806, L863, L865, L916, L1001, L1083, L1101, L1198, L1306, L1331, L1343, L1350, L1352, L1375, L1381, L1395, L1405, L1427, L1429, L1434, L1443, L1448, L1486, L1507, L1517, L1520, L1522, L1533, L1535, L1536, L1542, L1543, L1546, L1549, L1560-L1562, L1572, L1573, L1585, L1590, L1593, L1599, L1602, L1645.
- Rows read and judged closed (verdict, merge or one-off with no later contradiction): the rest.

## 6. What I could not check, and why

- **Anything that needs `nba.duckdb` or another DuckDB file** (told not to open them): the real count of completed 2026 games (I used `reports/forward_scorecard/2026-11-16.md`: 205 games), the AVG stale share (I used L1549's 11-08 counts and the handoff's 15,276), T510's qualifying-market count and complete-date count, whether the pilot's 11-03 capture hole had games, whether odds rows are being written after the new key, whether the morning report printed the rollover line.
- **GitHub issues** (L1353's tracker): I did not use `gh`; the state of issues #2-#24 is unchecked.
- **launchd installed/loaded state** and `data/ops/schedule_tz`: read from PROJECT_STATUS only.
- **CI status**: taken from the text of commit `04763a5b`; I did not query Actions.
- **Documents read only in part**: `TEST_LEDGER.md` (verdict rows T506-T526 plus greps for specific ids, not all 779 lines); `NEXT_SESSION.md` (top block and greps); `docs/prereg/` (status lines and the sections cited, not every file); `docs/BEST_PRACTICES.md` and `docs/MODEL_PLAYBOOK.md` (not read; no model was reviewed); `docs/PREDICTIONS_LEDGER.md` (open rows only); `REPORT_JARVIS.md` (not re-read; REPORT.md and the retro were).
- **Unmerged branch contents**: I read file lists, commit counts and a few files with `git show`; I did not run their tests.
- **Whether any unmerged branch is pinned by a frozen rule's sha**: not checked.
