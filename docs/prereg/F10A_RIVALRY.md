# F10A_RIVALRY: does a questionable player sit more often against a rival? (pre-registration)

Status: **DRAFT, STOPPED AT THE MINIMUM-DATA GATE (Sun Oct 11 23:05 CDT 2026).** Not freezable as the research doc drafted it
(non-negotiable 8). Nothing is fitted; no play rate by flag has been computed or seen. The only numbers below are counts and
cluster-robust standard errors. Everything above `=== RESULTS BELOW ===` would be the rule if Devin freezes one of the options in
"What Devin decides"; the main session commits `prereg(F10A_RIVALRY): <sha256>` first.
Season labels are `games.season` (2022 = 2022-23). Season 2025 is the frozen holdout and is never loaded.

## Provenance of the list (verified)
`docs/research/rivalries_2026-10-09.md`, Tier 1 (60 division pairs) and Tier 2 (H1-H20) marked Y by Devin 2026-10-11 22:58 CDT;
frozen at commit `b844256f2866a5f0bd0a9774da3cd2a481dee0ad` (git log, Oct 11 22:58:32 2026); the file is byte-identical at HEAD
(`git diff b844256 -- <file>` empty; blob `b8df8e7c`, sha256 of the committed text `3eefb8df...`). Tier 3 (player histories, F10b)
stays PARKED. The list is encoded in `research/features/rivalry.py` (tests: `tests/research/features/test_rivalry.py`, 5 pass).

## Hypothesis (F10a)
Among players listed **questionable** on the latest official report at or before real tip minus 60 minutes, P(play) is lower when
the opponent is a verified rival ("no need to risk it"), conditional on national TV, back-to-back, month, star status and
games-played pace. Counter-hypothesis recorded now so the sign cannot be flipped later: since 2023-24 the Player Participation
Policy requires stars to be available for national-TV and NBA Cup games, and rival games are over-represented on national TV
(measured below: 31% vs 21% in 2024), so the observed raw sign may be positive.

## Flags (frozen with the list; `research/features/rivalry.py`)
* `div_rival`: the two teams share a division (60 pairs; no realignment in 2022-24).
* `hist_rival`: the pair is in H1-H20 and its AS-OF strength is >= 2. Strength uses only meetings from an EARLIER calendar year than
  the game (granularity: a year-Y series is invisible to every game dated in year Y; conservative, tested). Rubric from the doc:
  3 = three or more meetings including one in the previous 6 years; 2 = a Finals meeting, or two or more in the previous 10 years;
  1 = any; 0 = none. "Overlapping current cores" cannot be observed and is dropped; documented all-time counts the doc gives
  without years enter as `older` (all pre-2015).
* `any_rival` = either. Regular-season games only (`game_id` prefix 002); play-in and playoffs excluded.

## Rows and outcome
Row = (game, player) whose status in the LATEST usable snapshot is `questionable`; usable = `as_of <= tip_et - 60 min`, `tip_source =
real` (3,690 / 3,690 games have a real tip, zero proxy fallbacks), the production rule `latest_pretip_flagged`. The player's side is
his box row, else his latest earlier team (89 rows with no resolvable side dropped, 0.5%). Outcome: `played` = minutes > 0
(DNP rows with NULL minutes count as not playing). The rivalry table is never built from outcomes.

## Minimum-data gate (non-negotiable 8), measured 2026-10-11 on `nba.duckdb` read-only, seasons 2022-24
Reproduce: `.venv/bin/python -m research.eval.f10a_gates` (2 s, no lock; JSON `reports/prereg_f10a/gates.json`).
| season | questionable player-games | non-play events (all rows, not by flag) | players | div_rival rows (games, players) | hist_rival rows (games, players) | any_rival rows |
|---|---|---|---|---|---|---|
| 2022 | 1,670 | 857 | 361 | 334 (147, 190) | 49 (22, 32) | 383 |
| 2023 | 1,877 | 935 | 387 | 350 (159, 194) | 45 (24, 31) | 395 |
| 2024 | 1,906 | 999 | 420 | 367 (164, 208) | 66 (26, 45) | 433 |
* Missingness (kill criterion 1, limit 2 points): game-level report coverage, rival vs other games: 2022 1.000 vs 1.000; 2023 div 0.996
  vs 1.000; 2024 1.000 vs 0.998 (max gap 0.4 points). PASS.
* Confound measured: national-TV share of questionable rows, rival vs other: div_rival 2022 0.17 vs 0.22, 2023 0.22 vs 0.22, 2024 0.31 vs
  0.21; hist_rival 0.47 / 0.60 / 0.55 vs about 0.21; any_rival 2024 0.35 vs 0.19. The confound is real and grows in the policy era.
* Power: 80%-power minimum detectable difference in play rate (2.8 x cluster-robust SE of the flag slope; contains no flag-level outcome):
  | sample | div_rival (game / player clusters) | hist_rival | any_rival |
  |---|---|---|---|
  | 2023 alone | 8.8 / 7.9 points | 18.1 / 26.0 | 8.5 / 8.7 |
  | 2024 alone (report) | 8.9 / 7.5 | 21.0 / 17.7 | 8.4 / 7.3 |
  | 2023-24 pooled | 6.3 / 5.6 | 14.7 / 16.2 | 6.0 / 5.9 |
  | 2022-24 pooled | 5.0 / 4.7 | 12.0 / 13.7 | 4.8 / 5.1 |
* Floor arithmetic: a log-loss gain of 0.005 per questionable row from a flag on about 19% of rows needs roughly d^2 x 0.19 / (2 p(1-p))
  >= 0.005, i.e. a play-rate difference d of about 11 points at p = 0.5. The doc's "4 points" descriptive floor is below every MDE above.
* **Verdict: FAIL.** The drafted design (select 2023, report 2024, three flags, floor 4 points) cannot be passed or failed by this data:
  `hist_rival` has 45-66 rows a season (MDE 12-21 points), and even `div_rival` pooled over all three seasons detects 4.7-5.0 points, not 4.
  The doc's own power note (about 3,000 questionable rows, plus a clean report season) is not met by a single report season. Per
  non-negotiable 8 this rule is not frozen as drafted, and the modeler stops here.

## What Devin decides (options, with the safe default)
1. **Stop (default).** Record F10a as "underpowered on 2022-24; prior weak (participation policy pushes the other way)". Add `div_rival`,
   `hist_rival` columns to the 2026-27 daily P(play) shadow log (about 1,900 questionable rows a season, about 360 division rows; MDE about 9
   points in one season, about 5 after three), and revisit only if 300+ forward division non-play events exist. Cost: zero.
2. **Descriptive only.** Freeze a no-pass-rule table: P(play | questionable) by `div_rival` x national TV x policy era (2022 pre-policy,
   2023-24 post-policy), pooled 2022-24, player-clustered and game-clustered CIs, `hist_rival` reported as "insufficient sample" (n < 100).
   It can motivate nothing but the forward log. This passes its own gate because it makes no detection claim.
3. **Powered test, single flag.** One flag (`div_rival`), pooled 2022-24 as one sample (no separate report season, so it can never earn
   more than a shadow column), practical floor **5 points** (MDE 4.7-5.0, the smallest floor the data can see) and a log-loss floor of -0.005
   that, as computed, needs about an 11-point effect. I judge a true 5-point or larger sitting effect unlikely, so this option mostly buys a clean null.
Controls for options 2-3 (the confound the doc names): `is_national_tv`, back-to-back (`team_context.b2b`), calendar month,
games-played pace (player's as-of games / team games vs 65 per 82), policy era, and star status. The policy's star definition
(All-Star / All-NBA in the last three seasons) is not in the database; the declared stand-in is "as-of top-2 team player by minutes per
appearance over the previous 10 games" (`status.py::star_pairs` definition). That stand-in is imperfect and is stated, not hidden.

## If option 3 is frozen: the rule
* Primary: paired per-row log-loss difference, (controls + `div_rival`) minus (controls), logistic P(play), month-block walk-forward with an
  identical recipe for both arms, rows pooled 2022-24; 2000-draw game-clustered percentile bootstrap, seed 0; sensitivity: player-clustered.
* Floors: log loss <= -0.005 AND a descriptive play-rate difference of at least 5 points with CI excluding 0.
* Family: {`div_rival`} alone (one cell); `hist_rival` and `any_rival` descriptive. F2's 65-game-pace is a separate family.
* Slices (descriptive; n < 100 printed as insufficient): star proxy vs not, national TV vs not, home vs away, policy era, first 15 games vs rest,
  post-All-Star vs before, Rockets games.
* Kill: report coverage gap > 2 points; any slice with n >= 100 worse than +0.01; a positive raw sign is recorded as the TV/policy counter-hypothesis
  and not flipped; any edit to the table after anyone sees a flag-level outcome voids the run.
* Guards: planted-future (a series ending in the game's own year never flags, tested), shuffled-flag placebo (flag permuted across games within
  season, expect delta >= -MDE), report-time gate real tip - 60. Season 2025: no touch. Decision score: not applicable (P(play) leg is not priced).

## What each choice protects against
* Counting before looking at outcomes by flag: the list was frozen first and the gate shows only n and SE.
* As-of strength by calendar year: the 2025-26 series that made several pairs "strength 3" cannot flag 2022-24 games.
* National TV, policy era and star proxy as controls: crediting a TV effect to "rivalry" (or hiding a rivalry effect under it).
* Stopping at the gate: a rule whose MDE (5-21 points) exceeds its own floor (4 points) cannot fail, so a null would be uninformative.

=== RESULTS BELOW ===

(not run; no flag-level outcome has been computed)
