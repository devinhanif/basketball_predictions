# TDA_REGIME_DIAGNOSTIC: does a persistent-homology regime score flag under-covering prop intervals better than simple flags? (pre-registration)

Status: **DRAFT, NOT FROZEN.** Written by the modeler, Mon 2026-11-16 23:31 CST (`date`), branch
`agent/modeler-1117-math-diagnostics`. No fit, no score, no outcome read. Reading-log note:
`docs/research/tda_regime_diagnostic_2026-11-17.md`. The modeler recommends PARKING this rule (P(pass) 0.04); it is drafted so
the question is answerable if Devin wants it.

**Devin must confirm before a freeze:**
1. That a reliability note on the distribution page is wanted at all (Explore lens only).
2. The three baselines B1-B3 and the intersection rule (TDA must beat every one).
3. Floor 0.03 coverage-gap difference; flag rate 10%; window 10 vs reference 30; eligibility >= 40 played games.
4. Select 2023 / report 2024 on out-of-fold rows; no 2025 touch ever; live 2026-27 rows descriptive only.
5. A new dependency (`ripser`, MIT) for H1, or H0-only (scipy) if not.
6. The gate count (G1) is run by a counts-only script BEFORE the freeze, on the data_version the run will use.

How it would become the rule: the main session commits `prereg(TDA_REGIME_DIAGNOSTIC): <sha256>` of everything above
`=== RESULTS BELOW ===`, hashed with `awk '/^=== RESULTS BELOW ===$/{exit} {print}' <file> | shasum -a 256`, after the gate
script has been run and its output pasted into "Gate check".

## Question
For production prop forecasts (`props_context_residual`), does a topological regime score pick rows whose stored 80% interval
[q10, q90] under-covers more than rows picked, at the same rate, by each of three simple flags?

## Unit and population
- Unit: one out-of-fold production row (player, game, stat), regular season, player played (the T460/T467 population), stats
  pts, reb, ast, fg3m. Seasons: 2023 select, 2024 report. SQL filter `season IN (2023, 2024)`; `season <= 2024` everywhere.
- Eligible: >= 40 played games (minutes > 0) dated before the row's game date and within 400 days, read from `box_scores` as of
  the forecast's `made_at`. Ineligible rows are counted, not scored.

## Scores (each computed as of `made_at`; truncation identity required: deleting every game dated on or after the row's date leaves
every score bit-identical)
- **TDA:** per player, feature vectors (minutes, starter, pts, reb, ast, fg3m), each standardised by the league mean and sd of the
  prior season. A = last 10 played games, R = the 30 played games before them. Vietoris-Rips persistence diagrams H0 and H1 of A
  and of R (ripser, max dimension 1); score = Wasserstein-1 distance H0 + Wasserstein-1 distance H1.
- **B1:** sd of minutes over the last 10 played games.
- **B2:** played games since the player's starter flag last changed (reversed, so a recent change ranks high).
- **B3:** mean Euclidean distance to the 10 nearest training rows of the forecasting model, on its standardised feature vector
  (same as-of fit the forecast used).
- Flag for each score: top 10% within (season, stat), ties broken by (game_id, player_id).

## Primary metric and pass rule
- Coverage indicator per row: randomised integer PIT u = F(y-1) + V (F(y) - F(y-1)), V ~ U(0, 1), seed 0, rows ordered by
  (season, stat, game_id, player_id); covered iff 0.10 <= u <= 0.90. F is the stored production grid on the integer support.
- Gap G(score) = coverage(unflagged) - coverage(flagged). Contrast D_b = G(TDA) - G(B_b), b = 1, 2, 3.
- CI: paired percentile bootstrap over games, B = 2,000, seed 0, 95%; date-clustered CI reported beside it.
- Multiplicity: one family of 12 (4 stats x 3 baselines), Benjamini-Hochberg q = 0.05 per season.
- **Pass for a stat:** in BOTH 2023 and 2024, every D_b >= 0.03 with CI lower bound > 0 and BH-significant. Pass for the
  diagnostic: at least one stat passes; the note is shown only for passing stats.
- Secondary (reported, not gating): CRPS on the integer grid divided by the row's own grid sd, flagged / unflagged ratio.

## Prechecks (label-free, before any outcome is read)
- P0 collinearity: if Spearman(TDA, B_b) >= 0.9 for any b on 2023, STOP: the TDA score re-expresses that baseline.
- P1 flag stability: share of flagged rows whose flag survives a +1-game lag >= 0.5, else STOP (the score is noise).

## Slices (fixed, reported, never gating)
Starters vs bench (as-of starter flag); rows with < 82 prior played games; October-November vs later.

## Kill criteria and stopping
Gate failure: not frozen. Precheck stop: recorded as a null, closed. Any fail: closed, no variant (window, flag rate, homology
dimension, feature set are not re-tried). One pre-registered follow-up at most. No 2025 touch is ever earned: a display note
makes no claim about a model.

## What each choice protects against
Fixed flag rate: a detector that fires on everything (era probe AUC 0.876-0.998, T443/T483). Intersection over three
baselines: picking the weakest comparator. Integer PIT, fixed order: continuous-vs-integer support artifacts (T114-T118).
Truncation identity: a lookahead in the as-of window. Game clustering: anti-conservative row CIs. Two seasons: a 2023-only pass.
Collinearity precheck: an expensive renaming of minutes volatility.

## Gate check (non-negotiable 8)
- **G1 (required, not run):** per season-stat >= 5,000 eligible rows, >= 500 flagged rows, >= 600 games, >= 25 dates. Documented
  population: 27,619 (2023) and 27,701 (2024) rows per stat (`docs/TEST_LEDGER.md:690, 704`). Eligible share needed >= 18%;
  measured: **UNKNOWN** (needs a counts-only read of `nba.duckdb`, not opened tonight). Expected to pass (P 0.85).
- Export proxy, NOT the gate (frozen export 2026-11-16, data_version 764432af8422, counts only, `season <> 2025`): of 506
  players with a primary prop row, stored non-2025 box history quantiles 6 / 12 / 14 / 27 / 44 games (10/25/50/75/90%); 335 < 20,
  75 < 10. The export's box window is 365 days with 2025-26 filtered out, so this understates `nba.duckdb`.
- Live 2026-27: not units (4,354 scored production pts rows on 26 dates, counts only; latest-row rule, continuous CRPS, no T-60
  flag exported, LINE_CAL_LIVE lock from 2026-10-30).
- Power at the documented n: about 2,760 flagged rows per season-stat; coverage SE about 0.010 per flag (design effect 1.3
  assumed); P(detect) about 0.6 at a true D of 0.03, about 0.95 at 0.045.

## Prior (to PREDICTIONS_LEDGER before any result)
P(G1 passes) 0.85; P(P0 stops the rule) 0.35; P(pass) 0.04; expected max-over-stats D vs best baseline 0.00, 80% interval
[-0.03, +0.01].

=== RESULTS BELOW ===
