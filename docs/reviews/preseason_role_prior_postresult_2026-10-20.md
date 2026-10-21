# PRESEASON_ROLE_PRIOR (T500): post-result adversarial review, 2026-10-20

Reviewer: adversary. Branch `agent/adversary-preseason-post` off `main` eafcb9c. Rule frozen 4774b943 (prereg 36c30bb),
harness pin dcaa62ee (36f8dc8). Recorded word: FAIL_ONE_SEASON. Seasons 2023 and 2024 only. Season 2025 was never loaded
(`H.MAX_SEASON == 2024` asserted, `reg.season.max() <= 2024` asserted). Every DuckDB connection was opened `read_only=True`
through the harness's own `connect`. No arm was added and the rule was not touched. The attack scripts are in the session
scratchpad, not in `nba/`: `build_rows.py`, `attack.py`, `planted.py`. Their outputs are `attack_out.txt` and `planted_out.txt`.

**Verdict on the 2024 gains (pts −0.0321, ast −0.0090, fg3m −0.0061): WOUNDED.** There is no leak. The information is
specific to each player: a 500-draw permutation of lambda harms by +0.084. But the pts gain comes from 5 of 115 players
(98.6% of it), it is gone once you cluster by player, and 8-cell BH removes all three clears.

**Verdict on the recorded word FAIL_ONE_SEASON: SURVIVES.** If anything the attacks make it more firmly a fail. The 2023 reb
harm (G2) is also a few-player effect: 3 fringe "preseason starters" carry it.

Verdict: APPROVED (approves recording FAIL_ONE_SEASON with the ledger correction in R1; it does not approve any 2024 cell as a candidate)

## Model class and playbook check

Class: a fixed-weight input blend over the stored LightGBM context-residual forecast. The served quantile grid is rescaled
by lambda and re-served on the integer grid. Playbook sections: "Gradient-boosted trees" and "Quantile and distribution layers".

1. **Information or capacity?** Information. Preseason box scores are pre-tip, and nothing is fitted on 2023 or 2024. The
   attack confirms the information is player-specific: permuting lambda across covered rows harms by +0.084
   [0.043, 0.127] (2024 pts, 500 draws), against the observed −0.032.
2. **Enough data for the class?** Training is not the limit, because nothing is trained. The limit is the scoring slice:
   80 covered mover players in 2024 and 96 in 2023. The effective n is players, not rows (Finding 1).
3. **Valid test?** The test is valid on leakage (planted future, lag, NULL rule) and on integer support. Its inference is
   **anti-conservative for this treatment.** Lambda is constant per player apart from the fade, so the 10 rows of one
   player are not independent. Game clustering treats them as about 10 clusters. The within-game outcome shuffle is
   **not a valid null** for a scale-changing transformation (check 1a): the foreign outcomes of other players reward any
   lambda > 1.
4. **Lineage.** F9b (role prior for young picks, T186-T192) gave a plausible sign and no clear. This review's finding
   applies there too: a per-player prior needs per-player clustering. The guards carried forward are A4, integer support
   and planted future. The rule dropped a shuffled-label guard with reason; check 1a shows the shuffle would have been invalid.
5. **Scored against?** Integer CRPS of the whole distribution, not log loss at the line. Nothing here reaches a market claim.

Class checks: missingness (check 3) shows asymmetry that is pre-tip and served as identity, so it is not a leak. Planted
future passes. Does the arm only re-express the base? A3 (own last 10 games) harms in 7 of 8 cells, so the base already
holds that information. A2's gain is new preseason information, carried by a few large role changes.

## Re-derivation

Per-row arrays were rebuilt from the pinned harness functions (`build_rows.py`, exit 0). Every K10 mover cell reproduces
results.json exactly: 2024 pts A2−A0 −0.03208, n 1,025 rows / 468 games. The A0 integer-grid CRPS equals the CRPS from the
stored `p_ge` to within 3.6e-15 on all 24 cells (mover, stayer and rookie). This is the identity behind integer support.

## Per-check table

All numbers are A2 − A0 integer CRPS on movers' first 10 games with the new team, unless stated. The CI is a 95%
percentile bootstrap (`nba/props/metrics.py::_clustered_boot_means`, 2000 draws, seed 0, unless stated).

| # | Check | Command | n | Result (CI) | Pass/fail |
|---|---|---|---|---|---|
| 0 | Re-derive | `python build_rows.py <wt> rows.parquet` | 24 cells | all points equal results.json; A0 equals stored p_ge within 3.6e-15 | pass |
| 1a | Shuffled labels, outcomes permuted within game among all played rows (2024 pts, 200 draws, seed 11) | `python attack.py <sp>` §1a | 1,025 | shuffled mean −0.131 [−0.164, −0.092]; observed −0.032 | **invalid test** (lambda > 1 is rewarded by foreign, higher outcomes; not a null) |
| 1b | Permutation null of lambda within starter flag, covered rows (500 draws, seed 5) | §1b | 1,025 / 1,202 | 2024 null +0.084 [+0.043, +0.127], P(null ≤ obs) = 0/500; 2023 null +0.123 [+0.082, +0.165], 0/500 | pass (information is player-specific; A4's single draw +0.033 is confirmed and conservative) |
| 2a | Planted preseason row dated on the opener | `python planted.py <sp>` | 1 | `assert_known_at` raises | pass |
| 2b | Planted later regular-season rows (48 min, 60 of each stat, 2025-06-30) for every mover, same team and fake new team | planted.py | 115 players | max \|Δlambda\| 2.0e-8 (float32 → float64 upcast in concat); 0 slice changes; 0 gwt changes | pass |
| 2c | Outcome column zeroed | planted.py | all | max \|Δlambda\| 0.0 | pass |
| 2d | Lag: drop each player's last preseason game | planted.py | 1,025 (covered 650 vs 761) | −0.0156 [−0.0402, +0.0094] vs −0.0321 | informative, not a leak: the last exhibition (2024-10-18, opener 10-22) is pre-tip and carries half the gain |
| 3 | Missingness: coverage (pre_n ≥ 2) by outcome group | attack.py §missingness | 1,025 / 1,202 | 2024: y > median 0.793 vs 0.693; actual minutes ≥ 15 0.809 vs < 15 0.648; act > min10 0.758 vs 0.728. 2023: 0.779 / 0.738, 0.779 / 0.737, 0.757 / 0.758 | pass: asymmetric but pre-tip (a preseason game count), and uncovered rows score exactly A0 (Δ = 0), so coverage cannot carry the gain |
| 4a | Knockout: drop the 10 rows with the largest \|y − A0 median\| (2024 pts) | attack.py §2 | 1,015 | −0.0305 [−0.0603, +0.0002] | holds in point; CI touches 0 |
| 4b | Knockout: drop the 10 rows with the largest gain | §2 | 1,015 | −0.0037 [−0.0287, +0.0204] | **fail**: 10 of 1,025 rows carry 88% of the gain |
| 4c | Knockout: drop top-k players by gain (k = 1, 3, 5, 10) | §2 | 1,015 / 995 / 975 / 925 | −0.0251 [−0.0555, +0.0037]; −0.0116 [−0.0394, +0.0143]; −0.0005 [−0.0245, +0.0225]; +0.0164 [−0.0048, +0.0378] | **fail**: 5 players carry 98.6% |
| 4d | Knockout: drop the best team (one team carries 36.8%; the top 3 carry 85.9%) | §2 | 985 | −0.0211 [−0.0499, +0.0076] | fail |
| 4e | Rate channel: A2 − A1 | §5 | 1,025 | 2024 pts −0.0003 [−0.0103, +0.0096]; rate factor mean 1.007, sd 0.037 | the rate channel is inert on pts (see Finding 3) |
| 5a | Replication: seed 1 / seed 7 with 5,000 draws (2024 pts) | §0 | 1,025 / 468 games | −0.0321 [−0.0609, −0.0048] / [−0.0629, −0.0031] | pass |
| 5b | Replication: clustered by date / player / team | §0 | 135 / 115 / 28 clusters | [−0.0650, +0.0005] / **[−0.0759, +0.0122]** / [−0.0799, +0.0163] | **fail** under player clustering |
| 5c | Drop the best month (2024-10: 269 rows, −16.8 of −32.9) | §2 | 756 | −0.0213 [−0.0487, +0.0020] | fail |
| 5d | Season swap: 2023 pts | results.json | 1,202 / 487 | −0.0098 [−0.0362, +0.0179] | sign holds, magnitude does not |
| 5e | Pooled 2023+2024 pts (descriptive) | inline | 2,227 | game-clustered −0.0201 [−0.0399, −0.0003]; player-season-clustered −0.0201 [−0.0459, +0.0066] (251 clusters) | not significant at the correct unit |
| 6 | Integer support and the zq bracket | results.json `bracket_A2_minus_A0` | 8 cells | lo−hi spread ≤ 0.00011 (2023 ast, the largest); 2024 pts −0.03205 / −0.03208 / −0.03211 | pass (confirmed ≤ 0.0001 apart from 2023 ast at 0.00011) |

Player-clustered (115 player clusters in 2024) for the other 2024 clears: ast −0.0090 [−0.0217, +0.0023] (p 0.066), fg3m
−0.0061 [−0.0122, −0.0004] (p 0.018). The 2023 reb harm is +0.0172 [+0.0000, +0.0367] clustered by player (136 clusters).

## Findings (severity ranked)

**Finding 1 (major, inference): game clustering is anti-conservative for a per-player treatment, and at the right unit the
2024 clears go away.** Lambda is a function of the player's preseason, and it is held across his first 10 games apart from
the fade. Each player's 10 rows sit in 10 different games, so `_clustered_boot_means` over `game_id` treats them as nearly
independent.

Clustered by player, the 2024 cells are:

| Stat | A2 − A0 (95% CI) | p |
|---|---|---|
| pts | −0.0321 [−0.0759, +0.0122] | 0.082 |
| ast | −0.0090 [−0.0217, +0.0023] | 0.066 |
| fg3m | −0.0061 [−0.0122, −0.0004] | 0.018 |

BH over the 4 stats at the player unit: fg3m q 0.072, pts and ast q 0.109. Nothing clears.

The family actually run decides across 2 seasons × 4 stats: an "either season" reading is what the word FAIL_ONE_SEASON
tests. BH over those 8 cells puts pts, ast and fg3m 2024 at q 0.052 even with game clustering. None of the three survives.

Where: rule line 6 ("clustered by `game_id`") and `boot()` at `research/eval/preseason_role_prior.py:703`. Fix (for future
per-player-prior rules, not this frozen one): cluster by player-season, or by the coarser of player-season and game (a
two-way cluster). Also add a BEST_PRACTICES line, drafted below.

**Finding 2 (major, concentration): the 2024 pts gain is 5 players' role changes, mostly in the first 3 games.**

| Player id (2024) | Gain (sum) | Lambda | min10 | Actual minutes | Preseason |
|---|---|---|---|---|---|
| 1630700 | −7.46 | 1.21 | 21.0 | 33.1 | starter, 4 games, 91 min |
| 1630643 | −7.30 | 1.39 | 7.4 | 15.3 | bench |
| 1627749 | −6.57 | 0.91 | 35.9 | 33.0 | starter |
| 1629610 | −6.09 | 1.27 | 7.9 | 16.9 | bench, 2 games |
| 1631223 | −5.02 | 1.44 | 12.3 | 18.8 | starter |

The total gain is −32.88 over 1,025 rows. The largest harm is 201942 (+10.53, lambda 0.89; preseason 26.5 mpg × 1.24
against min10 38.9 and actual 38.3). These are genuine pre-season role changes: a bench player becoming a 15-minute player,
or a role player becoming a 33-minute starter. That is the mechanism the rule hoped for. But it is about 5 events, so it
is not a stable average effect.

By games with the new team, the gain is: games 0-2 −18.7 (332 rows), games 3-5 −7.2, games 6-9 −7.0. One team carries
36.8% of the gain, and October 2024 carries 51%. In 2023 the five largest-gain players carry 203% of that season's net gain,
and the five largest-harm players sum to +30.5. Both seasons are a handful of large-|Δ| players, with signs that are close
to a coin flip on the margin.

Player names are not in the repo (`players_static` has no name column). The ids are given; the maintainer can resolve them.

**Finding 3 (minor, mechanism): the rate channel is inert by construction, not cancelling.** On pts the rate factor
`rho_blend / rho_A0` has mean 1.006-1.007 and sd 0.036-0.037; its mean |log| is 0.025-0.026. Its correlation with the
minutes factor is −0.10 (2023) and +0.14 (2024), so there is no cancellation.

The cause is in rule line 4. `rho_pre` is shrunk 100 minutes toward `rho_prev` (S-1 regular season, which A0's rate
already approximates). It is then weighted `m_pre / (m_pre + 120)` × fade, which is ≤ about 0.4 at game 0 and falls to 0
by game 10. Within the 60-100 preseason minutes, the shrink and the second weight compound.

A2 − A1, 2023 / 2024:

| Stat | 2023 | 2024 |
|---|---|---|
| pts | −0.0082 [−0.0183, +0.0017] | −0.0003 [−0.0103, +0.0096] |
| ast | −0.0054 [−0.0094, −0.0015] | −0.0032 [−0.0066, +0.0001] |
| reb | +0.0009 | −0.0017 |
| fg3m | −0.0001 | −0.0011 |

ast is the only stat where the rate channel has the same sign in both seasons. It is post hoc, and only the 2023 ast cell
survives BH over these 8 cells (p 0.003). It is reported here, not a claim.

**Finding 4 (major, record): the "third consecutive 2023-fails, 2024-clears" framing contradicts the ledger.**
`docs/TEST_LEDGER.md` lines 741-742:

- T498 (F1_WARM_WINDOW): 2023 cleared (pts −0.0188), 2024 did not.
- T499 (WOWY_STRUCT): 2023 cleared (pts leader-out −0.0096), 2024 did not.
- T500 is the reverse pattern.

So the real pattern is three FAIL_ONE_SEASON results with no consistent favoured season. That is what independent
small-effect noise under a "both seasons agree" rule produces. T498's structural explanation (A0 is data-poor in 2023,
so a prior helps more in 2023) predicts a **larger** gain in 2023 for T500. The data show the opposite, so that
explanation does not transfer.

Where: T500 ledger row, "Third consecutive rule where 2023 fails and 2024 clears (T498, T499, T500)". Fix: "Third
consecutive FAIL_ONE_SEASON (T498, T499 cleared 2023 only; T500 cleared 2024 only): no favoured season." The same
sentence is in the brief and probably in the retro queue.

**Finding 5 (minor, 2023 reb harm): kappa_starter inflating fringe preseason starters.** The 2023 reb rows decompose by
preseason starter flag and lambda class:

| Rows | n | Players | Sum Δ | Mean lambda | Actual − min10 | y − A0 mean |
|---|---|---|---|---|---|---|
| Starters, lambda > 1.02 | 244 | 28 | **+21.91** | 1.22 | +1.10 | −0.39 |
| Bench, lambda > 1.02 | 190 | 26 | +6.47 | — | — | — |
| Bench, lambda < 0.98 | 135 | 24 | −6.90 | — | — | — |

The cell total is +20.7, so the starters-up group is more than the whole harm. The top three harm players have min10 of
5.6-7.0, preseason mpg of 17-20 (×1.24 for starters) and lambda 1.62-1.71. Their actual minutes stayed at min10 (7.4,
5.0, 2.5). These are camp bodies who started exhibitions.

Dropping the top-3 harm players gives +0.0041 [−0.0049, +0.0138]. Over the 2023 covered movers, kappa-adjusted preseason
minutes over-predicted by:

- starters: +1.76 against actual −0.09;
- bench: −0.04 against actual −2.48.

In 2024 the bias was near 0 (starters +0.31 against +0.14). So yes: kappa_starter plus a few low-min10 exhibition
starters, in a season where movers' minutes fell (−1.25 against min10).

**Finding 6 (minor, scope): the brief's shuffled-label check is invalid here.** It is recorded as invalid, not as a pass.
For the six-check record, the valid null for this design is check 1b (the lambda permutation), and it passes.

## Why 2023 and 2024 disagree: updated read

There is no structural reason I can find. The input regime is the same across seasons:

| Season | Exhibitions | Mean minutes per row | Starter rows | Bench rows | Games per player-team |
|---|---|---|---|---|---|
| 2022 | 65 | 17.0 | 22.4 | 14.0 | 3.31 |
| 2023 | 64 | 16.9 | 21.8 | 14.3 | 3.28 |
| 2024 | 69 | 16.8 | 22.1 | 13.9 | 3.50 |

Coverage is 75.8% and 74.2%. The preseason ends 2-4 days before the opener in both seasons. The covered composition is
similar (47 starters / 49 bench players in 2023, against 44 / 36 in 2024). The correlation of (mpg_adj − min10) with
(actual − min10) is 0.30 and 0.36.

The mean bias differs: 2023 movers' preseason over-promised minutes by about 2 against actual, while 2024 was almost
unbiased. Above all, each season's net is a handful of large single-player role surprises (5 players carry ≥ 98% in
2024 and 203% in 2023).

The other slices do not share the season pattern:

- stayers are harmed in both seasons: pts +0.0165 [−0.0053, +0.0398] and +0.0246 [+0.0017, +0.0470];
- rookies show the reverse asymmetry: pts −0.0017 [−0.0507, +0.0424] in 2023 and +0.0545 [+0.0086, +0.0978] (a harm) in 2024.

A real 2024-favouring structure (data, exhibition rules, A0 quality) would show up across slices. It does not. The A0
data-poverty story (T498) predicts the opposite sign. My read: the true mover effect is somewhere around −0.01 to −0.02
pts (pooled −0.0201 [−0.0459, +0.0066], player-season clustered, 251 clusters). It is genuine role information delivered
in a few large events per season. The seasons disagree because 80-96 players per season is too few to average those
events out.

## Corrected family and power note

- **Family:** 2 seasons × 4 stats of A2 (the word depends on both seasons). With game clustering, BH over 8 puts every
  2024 clear at q 0.052: none survives. At the player unit, nothing survives even within 2024 (q ≥ 0.072). A1, A3, A4, K5,
  K20, rookies and stayers were run and reported, and no verdict depends on them. Counting them in a larger family only
  strengthens the result.
- **Power:** the 2024 pts game-clustered SE is about 0.0151, so the MDE (two-sided 5%, 80% power) is about 2.8 × SE ≈
  0.042. At the player unit the SE is about 0.022 and the MDE about 0.062. The observed −0.032 and the pooled −0.020 are
  both below the MDE at the correct unit. "Underpowered, cannot conclude" is the honest status of the mover effect; it is
  not "no effect". The rule's own power table (half-width pts 0.0104) came from a simulation with 0.3 game-shared noise
  and no player-shared noise, so it understated the CI by roughly 2×.

## The single biggest risk

The risk is that someone reads 2024 pts −0.032 [−0.062, −0.003] as "preseason works, 2023 was unlucky" and promotes A1 or
A2. The interval is game-clustered over a player-level treatment. At the player unit it is [−0.076, +0.012], and 5
players are the whole effect.

## What was not verified

- Player names; there is no name table in the repo.
- Whether the preseason box scores include a few international or neutral-site exhibitions. The rule says they are
  excluded; I did not audit game sites.
- The 38 overtime team-games (inherited unverified).
- Whether `mu` scales with minutes. This is the rule's own stated assumption, and no alternative was run, by design.
- A two-way (player × game) cluster bootstrap. I ran each one-way cluster separately; two-way would be at least as wide
  as the wider one.
- ruff, mypy and pytest: no code changed on this branch.

## Recommended changes (main session acts; none applied here)

- **R1:** correct the T500 ledger row's "third consecutive 2023 fails, 2024 clears" (Finding 4). Add "Post-result attack:
  2024 gains WOUNDED (player-clustered pts −0.0321 [−0.0759, +0.0122]; 5 players carry 98.6%); FAIL_ONE_SEASON SURVIVES."
- **R2:** BEST_PRACTICES, a new line: "A prior or feature that is constant per player across rows is clustered by player
  (or player-season) as well as by game; power simulations include player-shared noise. Incident: T500 2024 clears were
  game-clustered; at the player unit none cleared."
- **R3:** do not count A1 or A2 as a candidate in any production, live, or briefing decision.
- **R4:** check T498, T499 and F9b for the same per-player clustering issue before anything cites them as "same sign,
  unresolved". T499's leader-out treatment is per team-game, so it is likely fine. F9b's role prior is per player.

## Follow-up worth its cost?

**No new retrospective rule.** There is no 2019-2021 A0 OOF, so no third pre-2025 season exists without rebuilding A0. A
pre-registered follow-up would have to be forward:

- frozen A2 unchanged (no constant changes, no switch to A1, which would be arm selection after the result);
- 2026 movers' first 10 games, excluding opening night 2026-10-20 (it motivated the hypothesis);
- decided on a player-season-clustered CI, with one look after the 10th game of the last covered mover.

At about 80-100 covered players its MDE is about 0.06 pts, so P(clear) is low (my prior: about 0.15). It costs almost
nothing because the arrays are already computed nightly. It is worth it only if it can run as a descriptive watch on the
live log at no added build cost. Otherwise, close it and let the live log speak. My recommendation is to close and log
the watch, not to write a rule.

## Drafted rows (not appended)

- Ledger (T500 amendment, notes column): "Post-result attack (docs/reviews/preseason_role_prior_postresult_2026-10-20.md):
  no leak (planted future, lag, NULL rule pass; lambda permutation null +0.084 [0.043, 0.127], 0/500 ≤ obs). The 2024
  clears are game-clustered over a per-player treatment. Player-clustered: pts −0.0321 [−0.0759, +0.0122], ast −0.0090
  [−0.0217, +0.0023], fg3m −0.0061 [−0.0122, −0.0004]; BH over 8 cells q 0.052. 5 players carry 98.6% of 2024 pts.
  WOUNDED; FAIL_ONE_SEASON stands. Correction: T498/T499 cleared 2023 only, T500 2024 only; no favoured season."
- DECISIONS (draft): "2026-10-20. PRESEASON_ROLE_PRIOR closed after its post-result attack. Why: the gain is real role
  information but concentrated in about 5 players a season, and below the MDE at the player unit. Reverse if a forward
  2026 mover watch with the frozen A2 gives a player-clustered CI upper bound < 0."
- BEST_PRACTICES (draft): R2 above.
