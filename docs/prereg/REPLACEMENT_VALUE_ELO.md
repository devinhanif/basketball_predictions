# REPLACEMENT_VALUE_ELO: does valuing an absence against the team's actual replacement, instead of a generic replacement level, improve injury-Elo's game-winner forecasts? (pre-registration)

Status: **DRAFT (written Sun 2026-10-25 23:10-23:45 CDT, clock read by `date`), modeler, branch
`agent/modeler-replacement-value` (base `main` 8eafd10). NOT FROZEN and NOT FREEZABLE AS WRITTEN: the
minimum-data gate FAILS on power (G5, G6 below), so under non-negotiable 8 this rule is not frozen and
nothing is fitted.** Model class: ratings (frozen MOV-Elo) with a 2-coefficient ridge-logistic injury
layer. Advice or momentum: *follows advice* (Claude recommended drafting it; Devin said yes, DECISIONS
2026-10-25 23:09), with the caution, stated then and confirmed here, that the absorber is hard to identify
pre-tip. Freezing, if ever: the main session commits `prereg(REPLACEMENT_VALUE_ELO): <sha256>` BEFORE any
arm is fitted. Everything above the line `=== RESULTS BELOW ===` is the rule; its hash is the freeze
evidence: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/REPLACEMENT_VALUE_ELO.md | shasum -a 256`.
Season labels are `games.season` (2022 = 2022-23, 2023 = 2023-24, 2024 = 2024-25; 2025 = the frozen
holdout, never loaded).

## The rule in eight lines (as it would be frozen)
1. **Comparator A0** = production `rung0_injury_elo` v2 refit in the run's own harness (stored OOF is
   NOT reused: gate G4b shows it no longer matches today's features): `build_injury_features`
   (`configs/injury_elo.yaml`, real tip - 60, doubtful weight 0.5), `walk_forward_probs` (month blocks,
   ridge lambda 5, `min_signal_games` 150, clip 0.02-0.98) on the frozen MOV-Elo offset
   (`configs/mov_elo_tuned.yaml`), training from 2022-10-18 as production.
2. **The only change** in every arm is the replacement rate inside the per-player value
   `max(rate_i - r_rep, 0) * mins_i` (production: `r_rep = 0.80 * r_league`). Same flag set, snapshot,
   weights, clip, sign, scale, ridge, blocks, offset.
3. **Family arms** (definitions in Arms): **POOL** (minutes-weighted as-of rate of the team's available
   roster) and **LADDER** (POOL with cumulative, capped absorption of all of tonight's vacated minutes).
   **PQ_POOL / PQ_LADDER** are placebos (another same-date team's replacement rate). **NEXT** (single
   next man up by position) is descriptive only.
4. **Primary metric**: paired log-loss delta (arm minus A0) over all scored games of the season; floor
   **-0.002**; 95% percentile bootstrap clustered by game (one row = one game; 2,000 draws, seed 0, as
   `configs/injury_elo.yaml`); one-sided p from the same draws. **Co-primary**: Brier delta, floor
   **-0.0009** (the injury-Elo Brier/log-loss ratio 0.46 x the log-loss floor).
5. **Select 2023, report 2024.** 2023 family = {POOL, LADDER} x {log loss, Brier} = 4 tests, BH q 0.05.
   An arm is selected if both its metrics pass (point <= floor, CI upper < 0, BH-significant); if both
   arms qualify, the one with the lower 2023 log-loss delta is carried. 2024: the one carried arm, both
   metrics, BH over 2; same floors.
6. **Guards** (2024, and 2023 for selection): G-ID identity: arm minus its placebo, log loss point
   <= -0.001 (else the word is PASS_LEVEL_ONLY); G-SLICE no slice with n >= 100 worse than A0 by more
   than +0.005 log loss (point); G-ECE ECE delta <= +0.005 (T160's guard).
7. **Words**: PASS; PASS_LEVEL_ONLY; GUARD_FAIL; NULL_AT_FLOOR (CI lower bound of the reported arm, or
   of both arms in 2023 if none is selected, > -0.002: an effect at the floor is excluded, direction
   closed); NOT_DETECTED (everything else: no claim, closed under "one follow-up at most").
8. **Reported slice** (not a test): games with >= 2 rotation players OUT (both teams combined, production
   `n_rot_out`), and games where one team has >= 2; plus 0 / 1 / >= 2, >= 3, early (<= 15 games) / rest.

## Question and why now
Injury-Elo values each OUT or DOUBTFUL player against a generic replacement level, 0.80 x the as-of league
per-minute composite. A star missing from a deep team and the same star missing from a thin team cost the
same. The question (Devin, 2026-10-25): replace that generic level with the as-of value of who actually
plays the minutes. Because the absorber cannot be named pre-tip (FAN_KNOWLEDGE 2026-10-10: in 1,651
team-games with one 24+ minute player OUT, the largest gainer is at the same position 43% of the time
vs 38% by chance, top bench by minutes 9% vs 19%, a third are starters), the design does not name one:
the minutes go to a minutes-weighted pool of the available roster (POOL), and a cumulative variant
(LADDER) lets several absences push the minutes down the bench.

## Prior evidence (ledger)
- **T091 / T128**: injury-Elo vs MOV-Elo, log loss -0.00812 [-0.01212, -0.00379], n 3,951 games
  (2022-24, real tip). The ENTIRE injury layer is worth about 0.008; its gain sits in the rot_out = 2+
  slice (-0.0127 [-0.0185, -0.0062], n 2,574; `data/injury_elo_v2/results.json`). A refinement of the
  replacement level is a fraction of that.
- **T176 RAPM** (as-of RAPM value replaces box value inside injury-Elo): +0.0032 [-0.0003, +0.0066],
  n 3,951, REJECT; rot_out 2+ slice +0.0046. The closest lineage: a "better value" inside the same layer
  lost. (RAPM was later found to have run on stints matching box minutes only 47-49%: validity audit.)
- **T160 INJURY_ELO_T30** (later information, same layer): -0.0008 [-0.0044, +0.0026], n 1,315, FAIL at
  floor -0.002. Its empirical half-width 0.0035 matches this gate's model-based 0.0034.
- **T178 WINPROB_FAMILY**: no family or calibrator beats the Elo family. **T319 ELO_HISTORY_WINDOW**:
  null. **T235 / T236**: the market beats injury-Elo by +0.021 / +0.011 log loss; even a pass here would
  not reach the market.
- Props-side cousins: **REPLACEMENT_DELTA** (draft, not frozen, gate failed twice), **T499 WOWY_STRUCT**
  (FAIL_ONE_SEASON; the replacement twist tied in every cell), **T503 QUESTIONABLE_CASCADE** (stopped at
  the gate). Devin's confirmed null (FAN_KNOWLEDGE): the number of teammates OUT does nothing for a star
  because "the coach spreads the usage" - which is the POOL assumption, and it is mostly what A0 already
  does implicitly.

## Arms (all as-of; constants fixed here, none chosen on data)
- **Eligible roster** for team T on date D: players with minutes > 0 for T in one of T's previous 10
  games of the same season whose latest played game before D was for T (trade guard). Updated only after
  a whole date is processed. Available = eligible minus anyone flagged OUT or DOUBTFUL at the snapshot
  (QUESTIONABLE stays available, as in production).
- As-of `rate_j` and `mins_j` are production's shrunk per-minute composite and shrunk minutes
  (`nba.models.injury_elo.asof_values` formulas, games strictly before D).
- **POOL**: `r_rep(T) = sum_j mins_j rate_j / sum_j mins_j` over the available roster.
- **LADDER**: vacated minutes `V = sum_i w_i mins_i` (w 1 OUT, 0.5 DOUBTFUL, flagged players with an as-of
  state on T). The available roster absorbs V in proportion to `mins_j`, nobody above 38 minutes in total
  (water-filling); minutes nobody can absorb are played at `0.80 * r_league` (a call-up).
  `r_rep(T) = (sum_j extra_j rate_j + overflow * 0.80 r_league) / V`. Equal to POOL when no cap binds
  (unit-tested); differs when several absences push starters to the cap.
- **PQ_<arm>** (placebo): the arm's `r_rep` of a different team-game on the same date (sorted team-games
  of the date, cyclic shift by `1 + crc32("<date>|2026") mod (n-1)`). Keeps the level distribution,
  breaks the identity.
- **NEXT** (descriptive): per absent player, the highest-minutes available player outside the top 5 of
  the available roster by `mins_j` whose `players_static.position` group (first token) matches, else the
  first such player of any position, else `0.80 r_league`. Not in the family: FAN_KNOWLEDGE already shows
  the single absorber is not identifiable, and `players_static` is a current snapshot, not as-of.
- Fallback: a team-game with no available roster (season openers) uses `0.80 * r_league` in every arm.

## Data, gate and what it says (non-negotiable 8; label-free; run 2026-10-25 23:24 CDT)
Script `research/eval/replacement_value_elo_gate.py`, output
`reports/prereg_replacement_value_elo/gate.json` (sha256 `bede54ed01fb42a5`, identical on two runs). It reads no outcome:
games are loaded without points, A0's probabilities come from the stored OOF predictions (no labels),
the power numbers are computed from probabilities alone. Season 2025 never loaded (`season <= 2024` in
SQL; schedule files of later seasons not linked). Data: `nba.duckdb` read-only, latest manifest
`20261025T141416Z_10e4373f7ca6` (the DB file was modified after it, at 20:50 CDT; the gate therefore
stamps its own fingerprint of the three input tables, seasons <= 2024: games 3,958, box rows 103,926,
report rows 87,612, sha256 `848b34da`). Real tips: 3,953 of 3,953 games 2022-24 (T091 notice); 3,637 of 3,637 history games 2019-21 (this gate).

| Check | 2023 | 2024 | Pass |
|---|---|---|---|
| G1 scored games >= 1,200 | 1,318 | 1,320 (1,315 in the stored OOF) | yes |
| G1 games with >= 1 rotation OUT (either team) | 1,148 | 1,198 | info |
| G1 games with >= 2 rotation OUT (both teams) >= 300 | 872 | 998 | yes |
| games where one team has >= 2 rotation OUT | 738 | 908 | info |
| games with >= 3 rotation OUT | 578 | 752 | info |
| G2 nonzero-signal games before the first 2023 block >= 150 (`min_signal_games`), every arm | 1,038 (POOL/LADDER; A0 1,263) | - | yes |
| G3 pool fallback share of flagged team-games <= 0.02 | 0.008 | 0.009 | yes |
| G4a A0 recomputed here == production `build_injury_features` (3,958 games) | max diff 0.0 | 0.0 | yes |
| G4b stored OOF (`data/injury_elo_v2`, 2026-10-09) reproduced, max abs residual <= 1e-6 logit | 0.0157 (29% of 3,951 games > 1e-6) | | **no** (validity: the run must refit A0) |
| G5 power at the log-loss floor, one season (two-sided) >= 0.80 | 0.208 | 0.209 | **no** |
| G6 power if the best family arm were EXACTLY true >= 0.80 | 0.224 (POOL) | 0.211 (POOL) | **no** |

**Why G5 is structural, not a data gap.** If a candidate is the true probability and its expected
log-loss gain is G, the per-game gain has mean -KL and variance about 2 KL, so z = sqrt(n |G| / 2). For 80%
power at 95% one needs n |G| >= 15.7: |G| >= 0.0119 in one season (n 1,318), 0.0060 pooled 2023+2024.
The whole injury layer is worth 0.008. With the 2023 BH rank-1 bar the select-then-report pass
probability AT the floor is 0.137 x 0.209 = 0.029. In the >= 2 slice (n 872 / 998) power at the floor
is about 0.15.

**What a perfect candidate would buy (G6, the oracle bound).** Taking A0's own fitted shift
(per-season ridge coefficients recovered from the stored OOF: b 0.324 / 0.294, g 0.236 / 0.150) and
asking what A0 would have estimated if POOL were the truth, a perfectly true POOL improves log loss by
-0.00218 (SE 0.00181, z 1.20) in 2023 and -0.00203 (SE 0.00175, z 1.16) in 2024; LADDER -0.00178 / -0.00173;
slice >= 2 POOL -0.00283 (n 872) / -0.00246 (n 997). So even the best case sits at the floor with power
about 0.2. The placebo's bound is larger (PQ_POOL -0.00332 / -0.00284): the bound measures how far the
forecasts move, not information, which is why the identity guard is needed and why this is a ceiling, not
evidence.

**Divergence and validity (descriptive).** corr(d_out POOL, d_out A0) 0.915 in both seasons (16% of the
variance is new); LADDER 0.930 / 0.926; placebo 0.878 / 0.883: the team-specific part is a thin slice of
an already thin signal. Mean `r_rep / r_league` is 1.00 for POOL and LADDER vs production's 0.80, so most of
any change is the LEVEL (a different replacement_frac), not the identity; G-ID is the guard. Median
available roster 13; LADDER caps bind in 32% / 35% of flagged team-games, overflow 1.1% / 1.2%.
Absorption check on 2022 minutes only (warm-up season, never selected or reported; 29,534 player rows):
correlation of predicted with realised minute changes 0.253 (POOL) / 0.269 (LADDER); the top 5 by as-of
minutes take 58.5% of realised gains vs 57.5% / 56.6% predicted. The level is right; who gets them is
mostly noise, which attenuates any effect toward zero (playbook Q3).

**Capture audit (before declaring the gate failed on data).** Every season held: `nba.duckdb` 2022-2024
(2025 is the holdout; one logged touch is available because this model was never evaluated);
`data/history/nba_history.duckdb` 2013-2021, of which 2019 (1,143 games, 1,141 with a report), 2020 (1,171,
1,170) and 2021 (1,323, 1,323) have official reports and 100% real tips (games with >= 2 rotation OUT:
792 / 936 / 1,161); 2013-2018 hold no official report rows (the history fetch covered 2019-21; whether the NBA's
PDF archive reaches earlier was not verified, and pre-2019 seasons would add a third era). No source adds NBA games: a season is about 1,230 games. Ceiling of clean
pre-2025 data: 2019-2024 = 7,595 games; with the 2025 touch 8,911. Self-consistent power at the floor:
pooled 2019-2024 0.79, plus 2025 0.85 - but only if the true effect equals the floor, and the oracle says a
perfect candidate is AT the floor; a realistic fraction (say -0.0005, a quarter of the bound) needs about
31,000 games. Repairable gaps: none that change the conclusion. **The gate fails on power, after the
audit.**

## Multiplicity, CIs, and what each choice protects against
- Floor -0.002 log loss (T160 precedent; playbook: one season detects 0.004-0.005): protects against
  promoting a statistically real but useless change. Brier co-primary: protects against a log-loss gain
  bought by a few extreme probabilities.
- Game-clustered paired bootstrap: one row per game; date clustering reported as a sensitivity (same-date
  games share league state). BH within season over the declared family: protects against picking the
  luckier of two arms and two metrics.
- Select 2023 / report 2024: nothing is tuned (constants above are fixed a priori), so 2023 only chooses
  between POOL and LADDER; the claim rests on 2024.
- Placebo identity guard: protects against reading a level shift (0.80 -> 1.00 of league rate) as
  "knowing who replaces him".
- Refit A0 in-harness: protects against comparing against a stale stored file (G4b).
- Slice veto and ECE guard: inherited from the production injury-Elo rule and T160.

## Kill criteria
G4a parity fails at run time; any family or placebo arm scored before the freeze commit (rule void); the
planted-future test fails; the run's input fingerprint differs from the gate's (re-run the gate first);
a word other than PASS closes the direction (no other pools, caps, windows, positions, clips or seasons).
One follow-up at most, and only after a PASS: the same arms on the 2025 holdout, logged first.

## 2025 touch
Not proposed. If Devin chooses option B below, the one 2025 touch would be the confirmation step of that
NEW id, logged in HOLDOUT_ACCESS_LOG before the run.

## Unstated details chosen before scoring
Roster window 10 team games, same season; minutes cap 38; starters = top 5 available by as-of minutes
(NEXT only); position group = first token of `players_static.position`; placebo seed 2026; clip at 0 per
player kept (one change from production; a below-replacement absentee is worth 0, never negative);
QUESTIONABLE players stay available; long-term absentees still flagged on the report keep their value
(production behaviour); a flagged player with no as-of state is worth 0; bootstrap 2,000 draws seed 0;
BH q 0.05 one-sided; first date of 2022 dropped (production harness).

## Freeze blockers (nothing runs until Devin says go)
1. **The gate FAILS on power (G5, G6).** No freeze while it does. Options (open choices, below).
2. G4b: the stored injury-Elo v2 OOF no longer matches today's features (29% of games differ, max 0.016
   logit). Not a blocker for this rule (A0 is refit), but the cause is unknown and is an archivist
   question (data changed after 2026-10-09: report re-parse or box repairs?).
3. No run harness is built (the rule is not freezable). The feature module and its tests exist.

## Playbook check
Class: ratings (MOV-Elo) plus a linear / ridge-logistic injury layer.
1. Information or capacity? Neither new information nor capacity: the same report, the same box scores;
   the arm re-expresses who is available into a different replacement level. 84% of the feature's
   variance is shared with A0 (corr 0.915); the 2022 absorption check (corr 0.25-0.27) says the "who" part
   is mostly noise.
2. Enough data for this class? For FITTING, yes (2 coefficients, 1,038+ signal games before 2023). For
   TESTING, no: a ratings-layer change must be about 0.012 log loss to be seen in one season.
3. Test validity: real tips (100%), as-of rosters with a planted-future test, A0 parity exact (G4a), A0
   refit (G4b), a placebo that keeps the level. The power is the invalid part: a run would be
   NOT_DETECTED with probability about 0.8 even if the idea were exactly right. An invalid test is not a
   null, which is why it is not run.
4. Lineage: T176 (better player value inside injury-Elo: lost), T160 (later information inside
   injury-Elo: tied at half-width 0.0035), REPLACEMENT_DELTA / WOWY_STRUCT / QUESTIONABLE_CASCADE (props
   cousins: gate failures and a one-season fail). Guards carried: production slice veto, T160 ECE guard,
   REPLACEMENT_DELTA's placebo idea (here same-date team swap).
5. Scored against: A0 (production injury-Elo), log loss and Brier. Not the market, which already beats A0
   by 0.011-0.021; a pass would not change "keep your money" on game winners.
Ratings checks: adds pre-tip information? Marginally (teammates' as-of rates). Effect plausibly larger
than about 0.002? No: the perfect-candidate bound is about 0.002.

## Open choices for Devin (defaults in bold)
1. **A. Close at the draft (recommended): record the gate as the finding.** B. A NEW id that pools
   2019-2024 (7,595 games; MOV-Elo replayed from 2019, bubble seasons included, no select/report split)
   with the one 2025 touch as confirmation; power about 0.8 only if the effect equals the bound. C. A
   descriptive-only run on 2023-24 (sign and size, no claim, no ledger pass/fail). D. Log POOL as a live
   shadow column in 2026-27 (live path, replay oracle, about 0.2 power by April; not recommended).
2. Pool: **all available players, minutes-weighted** (Devin's suggestion; level matches 2022 absorption)
   vs bench only (ranks 6+).
3. LADDER cap: **38 minutes**.
4. NEXT: **descriptive only** vs a third family arm (family of 6; lower power).
5. Floors: **-0.002 log loss, -0.0009 Brier** (T160 precedent) - lowering them lowers power further.
6. Per-player clip at 0: **kept** (one change from production) vs allowing negative value.

=== RESULTS BELOW ===
(none: not frozen, not run)
