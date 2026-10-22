# Pre-freeze review: INGAME_WINPROB_V2 (T513), 2026-10-22

Reviewed: `docs/prereg/INGAME_WINPROB_V2.md` at branch `agent/market-ingame-v2` head 9dac1c3; gate `nba/ride/v2_gate.py`,
parser `nba/ride/feed_state.py`, output `reports/ingame_v2/gate.json` (sha256 5f5ee4ac, matches the draft).
Adversary role. Every check below is label-free: seasons 2022-24 regular season only, no final score, winner or arm fit was read
or computed, and season 2025 was never named or loaded. All DuckDB connections were `read_only=True`.

**NOT_READY.** Eleven MUSTs. Two of them are real look-ahead in the state builder that the planned guards would not catch, or
would only catch at run time (M2, M3). One is the gate reading final-buzzer states (M1). The rest are choices the harness could make
in more than one way. The gate still PASSES after correction (M1), so the rule can be frozen once the text below is in.

Verdict: BLOCKED

## Playbook check
Model class: logistic (candidate A2 and comparators V1r, A1, C0), with a GBM capacity control (A3).
1. **Information or capacity.** Information: possession, fouls, timeouts, the five on the floor, pace. The draft does this correctly. A3 is a control, not the candidate.
2. **Enough data.** Labels are per game: 1,230 independent outcomes train the 2023 select fit, across about 58k correlated states. A logistic model with about 30 terms can manage that. The lineup rating is weaker in 2022 (89.9% of player-games have 5 or more prior games, against 98% later), so the training season and the test season have different missingness. This is disclosed, and it is knowable before tip.
3. **Test validity.** Not yet valid. The state builder in the gate looks ahead (M2). The lineup look-ahead is not exercised by any guard (M3). The lag guard is ill-posed (M4). Inference is under-specified (M7).
4. **Lineage.** T508's stint disclosure is inherited in words only. T508's reviewer already wrote that "neither the planted-future nor the truncation test exercises it", and this draft repeats the same gap while claiming that guard 7(b) covers it (line 55).
5. **Scored against.** V1r and A1 in 2022-24. ESPN and the Kalshi mid only in the shadow stage. Correct.
Class checks: no column is constant within a date (pass). As-of status of features: possession, fouls and timeouts are causal per event (pass). The state index is not causal (M2). The lineup is look-ahead (M3).

## Checks run
Scripts are in the scratchpad (`lookahead.py`, `mono.py`, `cells.py`, `tipcheck.py`, `dbeq.py`), each run with `.venv/bin/python <script>`, and every run exited 0.

| # | Focus | Command / what | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | On-floor look-ahead | `lookahead.py 60`: for grid states in Q2-Q4, counted the stint on-floor players who are period openers with no evidence (an action or the out side of a sub) at or before T | 60 games/season, 28,200 player-slots/season | 6.50 / 6.64 / 6.40% of slots and 21.8 / 22.4 / 22.3% of team-states depend on events after T | **FAIL** (not covered by any guard) |
| 2 | Prior timing | `tipcheck.py`: `made_with_data_through` compared with the real tip (`build_game_tipoff`) | 3,683 games | realtip: 0 stamps after tip-60, minimum lead 60 min. Old `injury_elo`: 225 stamps after the real tip (the smoke swap was a genuine leak fix). Prior is walk-forward by month block (`injury_elo_eval.walk_forward_probs`). Live: 165/165 `forward_predictions` rung0 rows have made_at < tipoff (16 games) | pass (see M10, S3) |
| 3 | Possession / state causality | `mono.py`: replicated gate line 234 (`searchsorted` on an elapsed vector in action order) and compared it with the causal prefix | 100 games of 2023, 4,700 states | elapsed goes backwards 3.25 times/game (>5 s). In 74 states (1.6%) the gate prefix includes events after T (median 42.5 s ahead); margin differs in 53 (1.1%). `update_possession` itself is causal | **FAIL** |
| 4 | Rating as-of | Read the rule text and `star_cov` | n/a | the window function is `rows ... 1 preceding` ordered by date: strictly prior. The rating code is not written; the definition is ambiguous (M8) | pass on intent / ambiguous |
| 5 | Backup DB | `dbeq.py`: count plus `sum(hash(...))` for games, stints, possessions, player_game_stats (prefixes 0022[234]) on the backup 1022, nba.duckdb and the backup 1021 | 3,690 games; 204,746 stints; 734,846 poss; 96,442 pgs | identical on all four tables, all three files | pass |
| 6 | Smoke fixes | Read the diffs as described; checked whether any fix touches an arm, a label or a threshold | n/a | All four fixes are gate measurement or timing fixes. None reads an outcome. The PN token and the realtip swap are correct. Not verifiable: that THRESH predates the smoke (one commit, e8a5f1c) | pass (unverifiable part disclosed) |
| 7 | Grid / buzzer states | `cells.py`: recounted the cells on 60..2820 with the causal prefix | 3,690 games | code `GRID = range(60, 2881, 60)` has **48 states, including T=2880** (the final regulation margin, which is the outcome for non-tied games). Corrected counts: states >= 95: 7,981 / 9,422 / 9,309; games >= 95: 934 / 977 / 968; games >= 98: **801 / 862 / 850** (draft: 960 / 998 / 990); [0.75, 0.95): 16,593 / 16,315 / 16,521. Still PASS | **FAIL** (text and code), gate PASS after fix |
| 8 | Comeback guard | binomial / Wilson at n = 850 | 850 games | Wilson upper for 10/850 = 2.15%. The guard fails at k >= 16. P(false fail \| true 1.2%) = 0.055; power at a true 2.0% = 0.63, at 2.5% = 0.90. Point clause (observed > predicted + 1 pp): P(false fail) = 0.008, power at 2.0% = 0.34 | decidable as a no-harm bound; cannot certify 1 pp calibration |
| 9 | Live cadence | Rule 10 text | 396 gaps | Contradiction: snapshots up to 180 s stale are admitted, but the condition requires a cadence <= 60 s that the capture (median 142 s) fails. The meaning of "staler" is undefined | **FAIL** (M9) |
| 10 | Tests | `uv run --frozen pytest tests/ride/test_feed_state.py tests/ride/test_v2_gate.py` | n/a | could not run: pytest and ruff are not installed in the worktree env (exit 2). No test covers grid bounds or out-of-order clocks | not verified |

## MUSTs (exact replacement text)

**M1. Grid includes the buzzer.** `nba/ride/v2_gate.py:33` `GRID = tuple(range(60, 2881, 60))` → `GRID = tuple(range(60, 2821, 60))  # 47 states; 2880 is the final regulation margin (an outcome)`. Rerun the gate and replace the cell numbers in line 37 with the rerun values (expected, from my recount: 7,981 / 9,422 / 9,309; 934 / 977 / 968; 801 / 862 / 850; 16,593 / 16,315 / 16,521). In rule 8, replace "about 12 per season (1.2% of about 990 games)" with "about 10 per season (1.2% of about 850 games)". In the docstring, replace "data/injury_elo/oof_predictions.parquet" with "data/injury_elo_realtip/oof_predictions.parquet".

**M2. State index is not causal.** Rule 1, replace "State = last event with elapsed <= T, ordered by action number." with: "State at T = the longest prefix of the game's events in action-number order in which every event has elapsed <= T. The first event with elapsed > T and every event after it are excluded, even a later-logged event with elapsed <= T. Every feature (score, possession, fouls, timeouts, substitutions, pace, lineup) is computed from that prefix only. `np.searchsorted` on the unsorted elapsed vector is not used. A unit test plants an out-of-order event and asserts the prefix stops before it." (The gate uses the same `searchsorted` at line 234. 1.6% of states currently see up to about 40 s ahead.)

**M3. On-floor five: the look-ahead is untested.** Rule 2, after "on-floor strength difference L (sum of as-of ratings of the five)", insert: "computed from the **causal five**: at state T in period k >= 2, the openers are the players whose first appearance in the period at or before T is an action counted by `nba.parse.lineups._is_evidence` or the out side of a substitution; players subbed in at or before T are added and players subbed out are removed; any slots still open are filled from the previous period's closing five, using events <= T only. Q1 openers are the box starters (announced before tip). The stints table is never read by the state builder. A2s (L, S, F from `stints`) is a descriptive sensitivity row." Rule 7(b), append: "The truncation identity covers L, S and F: the causal five built from the truncated events equals the five built from the full game at every checkpoint <= the cut." Line 55, delete "lineup stints are a look-ahead reconstruction and" from the sentence on the truncation identities. (Evidence: 6.4-6.6% of on-floor slots, and 22% of team-states, depend on events after T.)

**M4. Guard 7(f) is ill-posed.** Feeding A1 and A2 a state that is s seconds stale and scoring against the final outcome equals scoring at grid T-s, so the guard measures nothing about latency. Replace 7(f) with: "(f) Removed as a guard. Latency is a live-use question, measured in the shadow stage (rule 10) by the A2 - V1r difference on snapshots binned by staleness 0-60 / 60-120 / 120-180 s (descriptive)."

**M5. Fitter.** Rule 2, after "every arm logistic unless stated", insert: "fitted with `nba.ride.winprob.fit_logistic` (v1's Newton fitter, l2 = 1e-6, iters = 50), one unweighted row per (game, state). V1r's columns are `nba.ride.winprob.features(margin, secs_left)` exactly. All features are from the home side (margin = home - away; 'own' = home, 'opponent' = away). The design-matrix builder (every column and interaction) is committed with the freeze and its sha256 is in the freeze line. A change to it is a new id."

**M6. C0 permutation.** Replace "permuted across games within (season, time bin, margin bin of width 5)" with: "permuted jointly (each row's block of non-V1r, non-prior columns moves together) across games within (season, elapsed bin of 180 s, home-margin bin of width 5 with open ends at <= -25 and >= +25), one permutation, `numpy.random.default_rng(0)`, applied to train and test rows alike. The C0 fit uses A2's exact design."

**M7. Inference.** Rule 5, replace "game-clustered bootstrap (2,000 draws, seed 0)" with: "game-clustered paired bootstrap: games resampled with replacement, 2,000 draws, `numpy.random.default_rng(0)`, the same draws for every arm and contrast; 95% CI = 2.5/97.5 percentiles; one-sided p = (1 + #{draws with difference >= 0}) / 2,001." Rule 6, append: "A contrast not scored in 2024 enters BH with p = 1. A contrast PASSES iff the 2023 point <= floor, the 2024 point <= floor, the 2024 CI upper < 0, and BH-adjusted p < 0.05." Rule 9, replace "dryrun CI half-width" with "median over the 20 null seeds of the 2024 CI half-width".

**M8. Rating definition.** Rule 3, replace with: "Rating = Hollinger game score per minute over the player's 40 most recent regular-season games with minutes > 0, dated strictly before the game date, any season in `nba.duckdb` (2022 onward; `data/history` not used). Shrunk: (sum GmSc + 10 × 20 × L) / (sum MIN + 10 × 20), where L = the league's sum GmSc / sum MIN over all regular-season player-games dated strictly before the game date in the same window. Fewer than 5 such games: rating = L. The share at L is reported by season. S = the difference between the teams in the top-2 ratings among each team's on-floor five. F = the difference in the number of on-floor players with >= 4 personal fouls."

**M9. Shadow cadence.** In rule 10, replace "snapshots staler than 180 s are excluded and counted" and "snapshot cadence <= 60 s for the games used" with: "Staleness of a snapshot = fetch time minus the `wallclock` of its newest play. Snapshots with staleness > 180 s are excluded and counted. Cadence of a game = the median gap between its consecutive in-progress fetches. Counting toward n starts on the first date the 60-s poller (open choice 2) is live. A game counts toward n >= 100 only if its cadence <= 60 s. Failing games are listed with their cadence. If more than 20% of games fail cadence, the stage reports 'cadence not met' and v2 cannot touch an alert."

**M10. Prior rows (history and live).** Rule 1, append: "Prior = rung0_injury_elo p from `data/injury_elo_realtip` (every stamp is at or before the real tip - 60 min, verified). The 7 of 3,690 games without one are dropped from every arm. Live prior = the latest `forward_predictions` row with model_name 'rung0_injury_elo' and made_at <= tipoff - 60 min. A game without one is not scored for A1/A2 and is counted."

**M11. Remaining ambiguities.** Rule 9(ii), replace "a planted possession worth 1.0 point" with "home possession adds +1.0 to the home margin inside the generating V1r logit (away -1.0, loose 0)". Rule 7(d), append: "Groups: possession; fouls (team fouls, F); timeouts; lineup (L, S); pace; OT. Share = (LL(A2 refit without the group) - LL(A2)) / (LL(A1) - LL(A2)), 2024 pooled." The timeouts row, append: "in OT states the timeout columns are 0." Pace, append: "pace = (possession-ending events so far + 6 × league rate per minute) / (minutes elapsed + 6), league rate from the training seasons."

## SHOULDs
- S1. Move the OT flag into A1 (it is clock information). Otherwise P2 credits a clock feature to "information beyond the prior".
- S2. History treats a Jump Ball as `other` (possession unknown until the first event). Live `classify_play` treats it as `jump` (possession assigned). This is training/serving skew at period starts. Use one rule in both, or report the share of states affected.
- S3. Gate `prior_cov` (line 280) checks `hour >= 11`, which is a lower bound and not a check that the stamp is before tip. Replace it with `made_with_data_through <= tip_et - 60 min` via `build_game_tipoff` (realtip passes 3,683/3,683).
- S4. The `power` block in gate.json is identical for all three seasons (same seed, same n), so it says nothing. Drop it or seed it by season.
- S5. Record the DB path and file sha256 in gate.json. The backup is equivalent today (verified), but the artifact should not depend on a review to prove it.
- S6. Bootstrap code: use `nba/truth/bootstrap.py` if it supports paired game clusters. Do not write a new CI routine.
- S7. State the comeback guard's operating characteristics in rule 8: P(false fail) 0.055 at a true 1.2%, power 0.63 at 2.0%. The point clause has power 0.34 at 2.0%. It is a no-harm bound, not a calibration certificate.

## Single most likely failure mode
If P2/P3 "win", the most likely cause is the lineup group (L, S, F) carrying gain through the opener look-ahead: about 22% of team-states know who is on the floor only because of later events. Of all the groups, it is the one whose as-of logic is not causal. M3 makes it causal.

## Not verified
- That THRESH and the cell bars were written before the smoke. There is a single commit, and the smoke output was not kept.
- pytest, ruff and mypy on the gate files: the tools are missing from the worktree env.
- The timezone of `forward_predictions.tipoff` (mixed 19:00 and 01:30 values).
- Where MOV-Elo's frozen k and home edge were tuned (possibly on 2022-24).
- The look-ahead share was measured on 60 sampled games/season (seed 0), not all 3,690.

## Drafted rows (not appended)
- TEST_LEDGER T513 note: "pre-freeze adversary 2026-10-22: NOT_READY; 11 MUSTs (grid buzzer state, non-causal state index 1.6% of states, lineup look-ahead 22% of team-states, ill-posed lag guard, inference/fitter/permutation/rating/cadence/prior definitions); gate PASS holds after the grid fix (games >= 98%: 801/862/850); review docs/reviews/prefreeze_ingame_v2_2026-10-22.md."
