# Final audit pass of the overnight loop, 2026-10-12 (Mon Oct 12 03:37 CDT 2026)

Scope: everything since the merged audit (6b1c782): ledger T459-T469, DECISIONS 2026-10-12, the two adversary reviews, the pass-2 audit,
the reports and the live-batch / market-widening commits. Read-only: no fit, season 2025 not loaded, no DuckDB opened. I re-computed
every rule sha256 from the committed text (RECENT_ROLE 1bb83990, RECENT_ROLE_HOLDOUT 302bc693, PPLAY_INTO_PROPS_LIVE 3a305482,
PI_RECENT_ROLE 2d4458ef, ROUTER_G2 2dc26443, FORWARD_T15 5c0dec6d, RECENT_ROLE_FULL 73f3f4b0, each at its freeze commit AND at HEAD): all match
the ledger and DECISIONS. Commit times in DECISIONS match git. Credit arithmetic (89 markets x 2 regions x 12 points = 2,136 / game;
459,240 at 215 games) and the 89-market group count re-derive from configs/odds_live.yaml. I re-derived T467 from results.json (pts
-0.00835 / -0.00831, FULL minus placebo -0.0100 / -0.0086, n 27,619 / 27,701).

## New items (7)

### N1 (a) The joint forecast P(play) R x FULL has no venue at all, not just "never scored"
- Evidence: PPLAY_INTO_PROPS_LIVE rule 1 scores both arms on the SAME production conditional grid (`props_cond[stat].q_grid`); only p differs. The
  RECENT_ROLE_FULL Stage 2 text is conditional-on-playing only and "says nothing about P(play)". The shadow stores production's conditional
  grid, not a FULL one. So neither the live log nor Stage 2 can ever score the unconditional forecast that production would actually serve
  (the recent_role_full review lists it as "not verified"). DECISIONS 03:33 names "the live joint P(play) x FULL forecast" as a reversal
  trigger, but nothing produces that number. Size hint (descriptive, 2024 rehearsal in the PPLAY rule): P(play) R vs production is -0.103 pts
  unconditional CRPS, FULL's conditional gain is -0.008, so the joint is dominated by the hurdle and FULL's marginal value on top of R is unknown.
- Cheapest honest next step: (i) before the 2026-10-18 hands-off line, add a shadow-only third conditional grid `props_cond_full` (heads + pi
  with the five columns, which the shadow already builds for R) so the live log CAN score R x FULL vs R x prod vs prod x prod; the scoring rule
  can be frozen after rows exist and before any look (PPLAY's first look is week 4). (ii) Before Devin confirms Stage 2, draft
  RECENT_ROLE_JOINT so ONE 2025 touch of the composite carries both the conditional claim and the unconditional one; otherwise the joint is a
  second touch of a never-evaluated composite. The DNP rows need conditional grids the stored components do not have (the PPLAY rehearsal
  used a proxy), so a 2023/24 joint is descriptive only.
- Venue: shadow (live) + 2025 Stage 2 (one touch). Needs Devin: yes (daily-path code in the window; the Stage 2 decision and its shape).

### N2 (a)+(b) The SEQ-blend claim is stated more strongly than the evidence, and it may overlap FULL
- Evidence: T466, DECISIONS 02:23 and overnight item 4 say the SEQ blend is "guard-clean once playoffs go to production" and that "the router gate
  is not what fixed ENSEMBLE's guard failures". What was measured: E2' (blend used only where g' >= 0.90) passes every guard, with minutes<5 at
  +0.0031 (results.json, E2p|pts, 2024). The ROUTER_G2 rule text puts the UNROUTED E2 harm on the same slice at +0.057 / +0.055. The constant-gate
  arm (the one that equals "SEQ on all regular-season rows") was scored on CRPS only (E2' minus CONST -0.0003 / +0.0025); its slice,
  concentration and calibration guards were never computed. A CRPS-neutral gate can still be the thing that holds the minutes<5 slice: the
  numbers point that way, so the sentence in T466 is unsupported. Descriptive in the same file: unrouted E2 minus A0 -0.0110 / -0.0131, routed
  minus unrouted -0.0001 / +0.0014 (CI spans 0). Separately: the constant gate is the per-block base rate of "minutes >= 5" (about 0.91) against
  TAU 0.90, so a block whose base rate dips under 0.90 routes whole to A0; not checked per block.
  Second point: SEQ reads each player's recent game sequence, which carries the same recent-minutes fact FULL uses; but SEQ's gain sits on rows
  with >= 5 minutes (-0.0174 per row) while FULL's gain is 55% on minutes<5 rows, so they could be complementary or redundant. Untested.
- Venue/limits: SEQ has no 2025 data (`seq_ensemble_export.py` is capped at 2024, jobs TEST_SEASONS (2023, 2024)), no daily inference path, and
  `configs/cost.yaml` shows 75 Colab units. So DECISIONS' "or the live log" is not a real option today; the 2025 route needs a new export, a GPU run
  under a committed rule, and Devin's spend.
- Cheapest honest next step: a descriptive table on the cached 2023/24 arrays (no fit, local CPU): slice and concentration guards for the unrouted
  regular-season-only blend, and the same blend with FULL as its first stage, vs FULL alone. If the SEQ gain over FULL is under the floor, the
  question closes without spending the virgin SEQ 2025 touch or GPU units. Amend the T466 reading (a note, not a rewrite).
- Needs Devin: not for the descriptive; yes for any 2025 or GPU use.

### N3 (b) DECISIONS 00:49 describes the RECENT_ROLE_HOLDOUT pass against the wrong thing
- Evidence: the entry says R "beats the current P(play) feature set by -0.0205" and proposes to "replace the P(play) hurdle's feature set with R".
  The comparator is WORKLOAD's C (LightGBM M2-p + report tag), not the deployed hurdle; the rule itself says "any proposal must say so"
  (RECENT_ROLE_HOLDOUT, "Honest prior"). Against the deployed hurdle the adversary measured -0.0972 [-0.1029, -0.0915] (T462), which is the number the
  production proposal rests on. T463 withdrew the attribution sentence but the 00:49 text and the T458 row's "base" were never reworded; PROJECT_STATUS
  carries neither.
- Cheapest step: one corrective DECISIONS line (append, do not edit the 00:49 entry) saying base = C, deployed comparison -0.097, reverse
  trigger as in 02:03. Venue: main session. Needs Devin: no.

### N4 (c) The run-metadata dirty flag will read "dirty" every night, so it cannot see code drift
- Evidence: `nba/daily/runmeta.py` counts any modified tracked file. `reports/props_analysis/*` is tracked and is rewritten by the daily
  `props_analysis` step (ops/nba_daily.sh:135); git status already shows `reports/props_analysis/2026-10-11.{json,md}` modified. The N15 "dirty
  tree runs live" finding is therefore not closed in practice: the flag will be true after the first morning run and stay true.
- Cheapest step: restrict the porcelain call to code paths (`nba ops configs pyproject.toml uv.lock`), keep the tmp-repo test and add one case with a
  modified file under reports/. Venue: steward, before 2026-10-18. Needs Devin: no.

### N5 (c) The opening-night checklist predates tonight's live batch
- Evidence: docs/NEXT_SESSION.md is stamped 2026-10-11 04:00 (HEAD 2ffd6ff) and docs/PROJECT_STATUS.md says T001-T314 (ledger is at T469). Neither mentions:
  the P(play) shadow now ON in pretip (rows to expect, +10-22 s, 7-day parity warning at R minus production > -0.05), the T-15 arm table, the run
  metadata line in the job log, the 89-market raw file whose parser was written from vendor docs (first live file on 10-20 is the first real test;
  daily guard 48,000), AC power with the lid open (the 10-11 hole was clamshell sleep, `caffeinate` does not stop it), or the pending Stage-2
  decision. Audit pass 2 N15 listed staleness before these items existed.
- Cheapest step: add six lines under "Opening night" and "First two weeks" (which tables should hold rows by 10-21, which log line to read, which file to open
  first); update the PROJECT_STATUS header. Venue: steward. Needs Devin: only the power setting (sudo pmset), already queued.

### N6 (c) The human-readable result reports of the frozen rules are untracked
- Evidence: git status shows untracked `reports/prereg_recent_role_holdout.md` (the holdout pass), `prereg_recent_role.md`, `prereg_recent_role_full.md`,
  `prereg_pi_recent_role.md`, `prereg_router_g2.md`, `prereg_workload.md`, `prereg_team_budget.md`, the three `smoke/` directories, and
  `reports/prereg_router_g2/results_partial.json` (superseded by the committed results.json). Only results.json / gate.json were committed with the results.
  The ledger and adversary reviews cite these reports.
- Cheapest step: commit the seven .md files by explicit path; delete or ignore results_partial.json and smoke/. Venue: main session. Needs Devin: no.
  (I cannot commit; the modeler role has no commit right, so the list is here.)

### N7 (a) Period markets: a forward-only Q1 starter rule is the one place information could beat the line, and nothing forecasts it live
- Evidence: reports/pace_periods_2026-10-12.md B: the de-vigged market beats our Elo-mapped period winner in every period, and Q1 player points ties the
  line on MAE but loses on log loss +0.0144 [0.0092, 0.0197] (n 7,305 legs, 902 games, 2 books, 2024 only); our forecast leans under (mean P(over) 0.437 vs
  0.498 market, over hit 48.8%), a selection mismatch (books list regulars), not a signal. Q1 player props exist in history for 2024 only, so a 2023
  select season is impossible. Q1 points are mostly starters' minutes, which are exactly what the confirmed lineups (T-30, T-15) tell; that is new pre-tip
  information, the lever the project keeps finding. The live capture from 10-20 stores the Q1 prop lines, but no live Q1 forecast exists, so the data
  piles up with nothing to score.
- Gate (measured from the report, real data): the log-loss CI half-width is 0.0053 at 7,305 legs; a half-width of 0.01 needs about 2,000 legs, i.e.
  roughly 250 games at 8 legs per game and 2 books (more books, fewer games). About 5-6 weeks of the live schedule.
- Cheapest honest next step: draft a forward-only, kill-only rule (Q1 direct model with the confirmed-starter flag, scored against the de-vigged line at
  the T-30 / T-15 snapshot, log loss, game-clustered CI, no 2023/24 select season, no 2025) run by an OFFLINE weekly scorer over the live raw odds and
  lineup tables, outside the nightly path, so the hands-off window is not touched. Do not build period-winner or period-total models: the report shows the
  market ahead with no information we lack. Venue: live log. Needs Devin: yes to freeze (it queues work and the market data volume is his plan).

## Checked, nothing new
- (d) Passes that should not be: none found. T468's "provisional" is resolved by T469; T454 (WORKLOAD P) was superseded by T458 and carries its caveat.
  T467/T469 numbers re-derive; the FULL claim "the two components together" is stated correctly in the rule, and the adversary's note that pi adds nothing
  in 2024 once the heads carry the columns is already in T469 (a production adoption would plausibly ship HEADS only; that is a Stage-2-or-later decision, not a new gap).
- The overnight headline "last-game minutes alone also passes" is the adversary's descriptive arm on 2023/24 (not in the rule); it is accurate as
  written in T469, and the Stage 2 recommendation not to score it is already recorded.
- The credit risk (2.30M remaining vs 2.63M a season): already queued as needs-Devin item 2; peak month 459k is 9.2% of a 5M monthly plan, so it only bites if the
  pool does not reset. Reset date unknown to me.
- Pace diagnostic overall mean residuals (pts -0.129, ast -0.057, reb -0.047, CIs exclude 0) are a small level lean noted by the report as uninvestigated;
  over/under decisions use the median, and the existing bias guards passed, so I do not raise it.
- Not re-run: the test suite (2,723 passed per the overnight log), the live collector, any odds capture. Not verified: the vendor shapes of the 89 markets, the
  tie rule of two-way period moneylines, the billing-period reset.

## Proposed rows (drafted, not appended)
- DECISIONS: "RECENT_ROLE_HOLDOUT comparator: base = WORKLOAD C; against the deployed hurdle -0.0972 [-0.1029, -0.0915] (T462). The proposal rests on the deployed number. Reverse as in 2026-10-12 02:03." (N3)
- DECISIONS/ledger note on T466: "The gate's value is on the minutes<5 slice (E2' +0.0031 vs unrouted +0.057 per the rule text), not on CRPS; the constant-gate arm's guards were not computed. SEQ-on-regular-season is untested for guards." (N2)
