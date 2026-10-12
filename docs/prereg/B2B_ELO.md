# B2B_ELO: a back-to-back term on top of production injury-Elo (pre-registration)

Status: **DRAFT (written by Mon Oct 12 13:59 CDT 2026, clock read by `date`), modeler. Not frozen. The minimum-data gate FAILS on precision (one season cannot resolve the expected effect at the house standard), and 2023/24 have already been used out-of-sample for this exact arm (descriptive); the modeler recommends NOT freezing a 2023/24 rule. Options for a clean test are below; they are Devin's call.** Added by the coordinator 2026-10-12. If frozen, the main session commits `prereg(B2B_ELO): <sha256>` BEFORE any arm is fitted. Everything above `=== RESULTS BELOW ===` is the rule. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/B2B_ELO.md | shasum -a 256`. Seasons: 2023 = 2023-24, 2024 = 2024-25, 2025 never loaded.

## The rule in five lines (as it would be frozen)
1. Candidate B = production injury-Elo logit as an offset + `beta x b2b_diff`, `b2b_diff = b2b_home - b2b_away`, `b2b_team = 1` iff the team has a game dated the previous calendar day (schedule fact, `games`); beta refit walk-forward on games strictly before each month block, exactly like production's (b, g) (ridge, same lambda, same block calendar), starting from 2022.
2. Comparator = stored production injury-Elo OOF (`data/injury_elo_realtip/oof_predictions.parquet`), reproduced to 1e-9 first.
3. Metric: game log loss (primary), Brier (secondary, reported), B minus production, paired, percentile bootstrap clustered by game (2000 draws, seed 0; date clustering reported); floor **-0.002**, CI upper < 0. One test, no family.
4. Slices (reported, cannot rescue): close games (|p - 0.5| < 0.2), early season (either team in its first 25 games), games with a nonzero b2b difference. Guards: shuffled b2b (b2b_diff permuted across games within month) must not reach -0.001 beyond a permutation null (K >= 50); ECE no worse by > +0.005; planted future (a later game changes no flag).
5. Verdict venue: see "Recommendation" (not 2023/24).

## Hypothesis
The market prices fatigue on the second night of a back-to-back; MOV-Elo has no rest term, and production's injury layer catches only the b2b rest that reaches the report as OUT. A b2b term on top of injury-Elo should close part of the market's log-loss advantage.

## Descriptive evidence already seen (main session, scratch, 2023/24; stored injury-Elo `game_rows_{2023,2024}.parquet` and Pinnacle T-60 `game_market`)
* Market logit minus ours moves with the b2b difference by -0.255 logit (OLS, HC1 se 0.013).
* Games where either team is on a b2b carry 40% of the market's log-loss advantage over injury-Elo.
* A recalibration + b2b term fitted on one season and applied to the other gains **-0.0029 (2023 -> 2024) and -0.0040 (2024 -> 2023)** log loss; b2b coefficient -0.36 / -0.34.

## Forking paths (read first)
The descriptive cross-season fit IS essentially this arm (plus a recalibration), already scored against the outcomes of both 2023 and 2024, each out-of-sample. A frozen rule on 2023/24 would re-measure a known result: the select/report split protects nothing here. **2023/24 are spent for this question.** The coefficient and its sign came from the same seasons.

## Earlier tests against Elo (ledger check) and why there is no conflict
* PROJECT_STATUS ("Game-context features ... did not beat Elo") and RESULTS_2026-10-08 s4 refer to the rung-1/2 feature-ML models (logistic / GBM on bundled as-of team features including `rest_days`, travel, national TV, standings, tanking; `nba/features/team_features.py`, `nba/features/game_context.py`) compared with plain Elo as REPLACEMENTS for it. Those models also had to relearn team strength, which Elo does better; no frozen rule isolated rest, and no ledger row gives a rest-only effect or CI.
* CONTEXT_SCREEN_GAMES (T180, 14 tests vs injury-Elo, residual form) had no rest or b2b variable (location, tip time, market size, days into season, national TV).
* ELO_HISTORY_WINDOW (T318-T319) and INJURY_ELO_T30 (T160-T161) did not touch rest.
* **Verdict: no earlier frozen test answered "does a b2b term on top of injury-Elo help?"** The difference is the comparator and the form (an offset term on injury-Elo, not a bundled model replacing Elo), not a real conflict. PPLAY_CONTEXT (T316) found team b2b-sit habits add nothing to P(play), which is a different target.

## Minimum-data gate (non-negotiable 8), measured Mon Oct 12 13:57 CDT 2026
Command: `.venv/bin/python -W ignore -m research.eval.b2b_elo_gate` (read-only, `season <= 2024`; JSON `reports/prereg_b2b_elo/gate.json`). Outcome-free: the hypothesised arm is production's logit + beta x b2b_diff with beta = -0.35 (the descriptive coefficient) and -0.175 (halved for the winner's curse); expected gain if it were the truth = KL(p_arm || p_prod) per game; per-game sd = the arm-implied Bernoulli sd (stored injury-minus-MOV sd 0.142 / 0.131 as a conservative bound).
* Games 1,318 / 1,315; either team on a b2b 365 / 379; nonzero b2b difference 308 / 308 (home only 146 / 150, away only 162 / 158); close games 772 / 731; early 384 / 384; no null flags.
* beta -0.35: expected **-0.0028 / -0.0028** per game (close -0.0032 / -0.0031; early -0.0031 / -0.0031), consistent with the descriptive -0.0029 / -0.0040. Arm-implied sd 0.075 / 0.075, date deff 1.08 / 1.23 -> **half-width 0.0042 / 0.0045 (> 0.002)**, so the pass line is delta <= -0.0042 / -0.0045 -> power **0.26 / 0.23 per season, about 0.06 for both.** With the conservative sd: 0.10 / 0.11.
* beta -0.175: expected -0.0007 / -0.0007, power about 0.09 per season.
* Games needed for a half-width of 0.002 at sd 0.075, deff 1.15: about 6,200 (4.7 seasons).
* Gate checks: half-width FAIL both seasons; power FAIL both seasons; >= 300 games with a b2b difference PASS (308 / 308).

## Gate verdict and recommendation
**FAIL**: the effect the descriptive look implies (-0.003 per game) is real-looking but below what one season can resolve (half-width 0.0042-0.0045), and 2023/24 are already used. Do not freeze a 2023/24 rule. Clean options, Devin's call:
1. **Forward 2026-27, one-sided, floor 0** (does the term help at all?): about 1,230 regular-season games, one-sided 95% bound needs delta <= about -0.0038; power about 0.33 at -0.0028. Weak.
2. **One logged 2025 touch + the 2026-27 log, pooled, declared now** (injury-Elo + b2b has never touched 2025; the HOLDOUT_ACCESS_LOG has no B2B_ELO row): about 2,550 games, one-sided bound about -0.0026; power about 0.55 at -0.0028. The best available, still a coin flip, and it spends the 2025 touch for this model.
3. **Do not test; log descriptively** the market-minus-model b2b lean in the morning report and revisit after 2026-27 with 2027-28 as the report season.
My vote: 3, or 2 if Devin wants a win-model question this season; the effect is about a third of the market's advantage at best, and "keep your money" does not change on it.

## Unstated details chosen before scoring
b2b = any game dated the previous calendar day (regular season, play-in or playoffs); the flag for a team's first game of a season is 0; the early slice uses the team-game index from box rows of earlier games; close = |p_prod - 0.5| < 0.2.

=== RESULTS BELOW ===

(not run)
