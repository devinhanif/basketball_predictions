# INGAME_LINE_CAL_FWD: adversary pre-freeze review (2026-11-17)

Reviewer: adversary. Branch `agent/adversary-ingame-rule` off `agent/modeler-live-model` e3029f9b. Draft read: `docs/prereg/INGAME_LINE_CAL_FWD.md`
(sha256 a2ee261d686a..., matches the brief), `nba/eval/ingame_arith_baseline.py`, `tests/eval/test_ingame_arith_baseline.py`,
`docs/reviews/live_model_2026-11-17.md`. Standard: CLAUDE.md (non-negotiables 1, 3, 4, 8), BEST_PRACTICES, MODEL_PLAYBOOK, frozen
`docs/prereg/LINE_CAL_LIVE.md`, T508 / T508-attack rows and `docs/reviews/IN_GAME_LINE_MODEL_post_result_2026-10-22.md`.

**No outcome was read.** No DuckDB file was opened. No season-2025 file was read. The only data read was the schedule parquet
`data/schedule/live_tips_2026-27.parquet` (game ids and tip times; label-free) to re-derive the look projection.

Verdict: BLOCKED
Pre-freeze verdict: **NOT_READY** (4 blockers, 6 majors, 10 minors). The question is sound and the gate passes. The arithmetic matches
the code. What is not ready: the binding baseline set (one member is undefined, and the lineage's fair baseline was left out for a wrong
reason), the logging path is not pinned, and the one look is not closed. Every fix below is text, plus one heavy fit that has to run
before the freeze, and that fit cannot run on a game night.

## Playbook check (of this review)
Model class: two-stage gradient-boosted trees (T508 arm M), scored against an arithmetic NegBin count baseline and a market price.
1. **Information or capacity.** M's edge over arithmetic, if any, is partly *information A1 does not use* (personal fouls, starter flag,
   M's league-rate B0 logit). The draft says the comparator "has every input M has" (Playbook check item 3). That is false (finding B2).
2. **Enough data.** The gate passes and I reproduced it (below). Power is misstated: BEATS has 50%, not 80%, power at the floor (M2).
3. **Test validity.** Paired rows on the same strike are valid. The market arm sees later information than M (M3). The baseline-selection
   CI is valid for BEATS but not for NO_PRACTICAL_GAIN (M1).
4. **Lineage.** T508-attack: M minus the per-checkpoint fair GLM was -0.007 / -0.005 / -0.004 at e = 30 / 36 / 42, i.e. right at
   this floor. The draft drops that baseline because it confuses it with the halftime store's glm_e, which is fitted at e = 24 (B2).
   The undamped-Newton `platt_fit` that diverged in T508 is still in `research/eval/in_game_line_rule.py:306-318`, and the C3 fit would
   naturally reuse it (B1).
5. **Scored against.** Log loss at the Kalshi strike, the same binary event for every arm. Integer support holds: A1 is a NegBin on the
   integer grid and every arm gives P(final >= k). This is not a CRPS-versus-grid issue.
Class checks (GBM): pinned artifact digest (G2), as-of state (the checks below), no refit inside the window. Missing: a pin on the
*inputs* M is served with (B3).

## Checks run (exact commands, exit codes shown unpiped)

| check | command | n | result | pass |
|---|---|---|---|---|
| draft hash | `shasum -a 256 docs/prereg/INGAME_LINE_CAL_FWD.md` | 1 file | a2ee261d... | yes |
| A1 tests | `.venv-1/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/eval/test_ingame_arith_baseline.py` | 8 tests | 8 passed, exit 0 (with the repo coverage gate on, a single-file run exits 1 on "coverage 70% not reached"; not a code defect) | yes |
| ruff / format / mypy | `ruff check` / `ruff format --check` / `mypy nba/eval/ingame_arith_baseline.py` | 2 files | exit 0 / 0 / 0 | yes |
| formula: text vs code | line by line, s3 vs `ingame_arith_baseline.py:49-78`; hand value in `test_hand_computed_value` | n/a | identical (r0, r, s0, s, g3, g4, q, R, lam, NegBin n = lam/(v-1), p = 1/v, sf(need-1)) | yes |
| as-of of A1 inputs | code read: `checkpoint_store.py` schema, `checkpoint_scorer.py:293-332`, `halftime_entry.consider_market` | n/a | A1 reads only stored row fields (cur, minutes, e_used, margin, pregame_mean, rec_minutes, k). All are at the state or pre-tip. `pregame_mean` / `rec_minutes` = last 10 played games with `game_date < slate` (`halftime_scorer.RecencyProvider`) | yes |
| row arithmetic, s9 | sums of the s9 table | 18,866 rows | 6,409 + 6,664 + 5,793 = 18,866; 12,631 / 18,866 = 0.6695; 3,839 / 18,866 = 0.2035; 370 rows/game | yes |
| G_req | (2.241 + 0.842)^2 x SD^2 / 0.005^2 | n/a | 86 / 152 / 238 / 414 / 770 at SD 0.015..0.045 | yes (but see M2 on what "power" means) |
| look projection | schedule parquet, cumulative games from first post-freeze night, >= 240 games and >= 30 dates | 974 games / 126 nights from 11-20 | trigger **2026-12-30** (night 31, 240 games), as stated. First night 11-25: **2027-01-04**. First night 12-02: **2027-01-10**. At 0.9 capture: 2027-01-03; at 0.8: 2027-01-07. 736 games (SD 0.044) reached 2027-03-12, night 97 <= 100 | yes (dates for later freezes off by one, m2) |
| quote timing | `checkpoint_scorer._quote` lines 294-305 | n/a | `known_at` = first Kalshi page **at or after** the state summary, up to 90 s later. The quote is never before the state | finding M3 |
| display paths | `nba/jarvis/cards/text.py:257-281`, `cards/run.py:199,527`, `app/api/.../live.py:709-776`, `nba/export/desk.py:490-511` | n/a | Live card: p_model + ask/bid of the top 3 overs. Final card: "Last in-game check; its prices are not current." (no numbers). App: hidden at `post`; reader accepts `version == 1` only. Checkpoint overs are never logged as settled picks (picks are logged pre-tip only). No outcome join exists on the checkpoint store | yes today; must be frozen (M4) |
| game-scripts-2 diff | `git diff 55ccf5ca agent/market-game-scripts-2 -- nba/jarvis/ingame_checkpoints.py nba/markets/checkpoint_scorer.py` | 2 files | additive: `publish_snapshot` after `store.close()`, and `latest_states` filled after the scoring loop. No change to rows, eligibility, `known_at` or `within_prereg_window` | row-neutral by reading (not replay-verified) |
| INGAME_WINPROB_V2 footprint | `git diff --stat $(git merge-base main agent/market-ingame-v2) agent/market-ingame-v2` | 78 files | `nba/ride`, `nba/papertrade`, `nba/live/*` (a capture module), no checkpoint file | no collision, except a capture change (m10) |

---

## Blockers

### B1. A1e (C3 = yes, settled) is a binding candidate with no definition, code, command or coefficients (s3 A1e, s13.3)
*How it misleads:* A\* includes A1e, but nothing fixes what A1e is: which historical rows, how the forward checkpoints 30/35/40 map onto
T508's checkpoints 30/36/42, per stat or pooled, the clip before the logit, the optimizer, or a convergence rule. A1r returns exactly 0
when `lam = 0` (for example fg3m with `pregame_mean = 0`), so an unclipped logit is -inf. `research/eval/in_game_line_rule.platt_fit`
is the undamped 25-step Newton that diverged in T508 (coefficients about 1e11, attack row 6), and the code still has it. Whoever runs
"the maintainer's heavy command" decides A1e's strength after the freeze. No command exists today.
*Fix (replace the s3 A1e bullet):*
> * **A1e, arithmetic, historically calibrated (C3 = yes, Devin 2026-11-17).** `p = sigmoid(a[s,c] + b[s,c] * logit(clip(A1r, 0.005, 0.995)))`, one (a, b) per stat s and forward checkpoint c, fitted on T508 units (regular season 2019-2024, game-id prefix `002`, `n_prior >= 5`, lines on `in_game_line_rule.LINES` with `current < line`, state as of the checkpoint, target `final >= line` including overtime) at T508 checkpoint 30 for c = 30, 36 for c = 35 and 42 for c = 40. A1r on those rows uses the harness's `pregame mean` and `rec_minutes` and the actual elapsed minutes. Fit: unpenalized maximum likelihood, `scipy.optimize.minimize(method="L-BFGS-B")` from (0, 1), `gtol 1e-10`. The fit asserts convergence, |a| < 20 and 0 < b < 20, and asserts `max(season) <= 2024` on its rows. On any failure A1e is absent for that cell (counted and disclosed), never refitted. Code `nba/eval/ingame_arith_fit.py` with tests; output `reports/prereg_ingame_line_cal_fwd/a1_fits.json` (coefficients, n rows / games per cell, row digest, git sha), whose sha256 is in the freeze commit. No refit, ever, inside this rule.

### B2. The binding set omits the lineage's fair baseline, for a wrong reason, and the playbook check overstates the comparator (s3, Playbook 3-4, s10, s12)
*How it misleads:* the draft drops "glm_e (fitted at e = 24 only; off its support at 30-40)". That describes the halftime store's
`p_glm_e`. The T508-attack's glm_e was a **per-checkpoint** logistic on [logit B0p, logit B0, logit B1, log1p(need)], fitted at every
e including 30/36/42. Against it, M's residual was -0.007 / -0.005 / -0.004 at e = 30 / 36 / 42 (pts, 2023), which is the floor. A1e
is a 2-parameter recalibration of one of those four inputs, so it is weaker by construction. A P1 BEATS against {A1r, A1p, A1e} could
again be mostly baseline weakness: T508's exact failure, and DECISIONS L1375 asked for "a player-aware baseline". The modeler's
priors are calibrated on the glm_e residual, not on A\*, so they are optimistic about how hard P1 is. Separately, "the comparator has
every input M has" is false: M also uses personal fouls, the starter flag and the league-rate B0 logit (T508 s2).
*Fix (preferred; same heavy command as B1, small marginal cost):* add to s3:
> * **A1g, the T508-attack's fair GLM, refitted per forward checkpoint.** `p = sigmoid(w[s,c] . [1, logit A1r, logit B0, logit B1, log1p(k - cur)])`, all logits clipped at [0.005, 0.995]. B0 is T508's league-rate B0 with T508's pinned league rates. B1 = `P(X >= k) / P(X >= cur)`, X ~ NegBin(mean `pregame_mean`, VMR as A1). Fitted on the B1 rows and checkpoint mapping, with the same optimizer and asserts; coefficients in the same `a1_fits.json`. Every input is in the stored row.
and change A\* to "the one of {A1r, A1p, A1e, A1g}". In Playbook item 3, replace "The comparator has every input M has (section 3)."
with: "A1 has M's pre-game inputs and the current count, minutes, clock and margin. It does not use personal fouls or the starter flag;
A1g adds M's league-rate B0 logit and the pre-game B1. A gain of M over A\* can therefore be foul or starter information, which is
legitimate in-game information, not leakage." Restate the s12 priors against A\*.
*Fallback if Devin declines A1g:* replace the s4 drop reason with the correct one ("the halftime store's glm_e is fitted at e = 24;
the T508-attack's per-checkpoint glm_e is not rebuilt here by Devin's choice"), and add to s11: "A P1 BEATS reads as 'beats player-aware
arithmetic'. It is not 'beats the T508-attack fair baseline', against which the historical residual at e >= 30 was at the floor."
Either way, the text as written cannot be frozen.

### B3. The logging path is not pinned; G2 pins only the artifact (s1, s6 G2, s11)
*How it misleads:* M's served number depends on more than the artifact. It depends on `configs/jarvis.yaml`
`halftime_overs.recency_min_game_date` (today `null`, so the last 10 games can include season-2025 final lines as features) and
`min_prior`. It depends on the candidate filters in `halftime_entry.consider_market` (`ASK_MIN_C, ASK_MAX_C = 2, 97`, `MIN_DEPTH`,
`GRID`, ejected and DNP exclusions) and on the scorer's `LATE_MAX_S`, `QUOTE_MAX_AFTER_S`, `STALE_PLAY_S`, `LIVE_MAX_LAG_S`,
`PREREG_WINDOW_S`. Any of these can change mid-window, through a merge or a config edit, and change M's inputs, A1's inputs or which
rows exist, with no digest change and no void. LINE_CAL_LIVE pinned its served functions; this draft does not.
*Fix (new s1 bullet and G2 text):*
> * **Pinned logging path.** The freeze commit records the sha256 of `nba/markets/checkpoint_scorer.py`, `nba/markets/checkpoint_store.py`, `nba/markets/halftime_scorer.py`, `nba/markets/halftime_entry.py`, `nba/markets/halftime_model.py`, `nba/jarvis/ingame_checkpoints.py`, and the values of `halftime_overs.{artifact_digest, recency_min_game_date, fee_pin}` and `ingame_checkpoints.{enabled, store}` in `configs/jarvis.yaml`. The scorer lists every commit on `main` after the freeze that touches a pinned path, with the first slate it affects. **G2 (widened):** a post-freeze row logged under a changed pinned path or value voids the read (new rule id), *unless* the adversary certified the change row-neutral before it merged. Row-neutral means `python -m nba.markets.checkpoint_scorer replay` on two burned capture nights, before and after the change, gives identical `checkpoint_scored` rows apart from `written_at` (label-free). The certificate is cited by commit.

### B4. The one look is not closed (s5, s13 "scorer written after the freeze")
*How it misleads:* (a) Nothing says rows from nights after the trigger night are excluded. Rows keep accruing during the "2 days
later" and until someone notices, and the scorer author would choose the window. (b) Nothing makes the scorer refuse to run early, and
nothing stops a dry run on real outcomes. (c) There is no results marker and no hash procedure ("the sha of everything above the
results line" needs a line). (d) "Scored 2 days later" does not say what happens when a settlement is still missing.
*Fix (replace s5):*
> **One look.** D = the first ET slate date after the freeze date on which the cumulative eligible primary rows (every s1 filter) span >= 240 distinct `event_id` AND >= 30 distinct slate dates, computed from the store alone (counts only). **The scored window is slate dates in (freeze date, D]; rows after D are never scored.** The scorer computes D itself from the store and refuses unless the real ET clock date is >= D + 2. It refuses if `reports/prereg_ingame_line_cal_fwd/results.json` exists. Its `dryrun` uses synthetic outcomes only. A row with neither a Kalshi settlement nor a STATUS_FINAL box at the run is dropped (counted). Backstop: if D does not occur by 2027-04-11, no outcome is read, and the word for both primaries is INSUFFICIENT_SAMPLE on 2027-04-13. The freeze hashes every byte above the line `=== RESULTS BELOW ===` (added as the last line of this text), together with the files in s13.

---

## Majors

### M1. "Best of three after the outcomes" is valid for BEATS but not for NO_PRACTICAL_GAIN, and its scope is unstated (s3 A\*, s4)
Picking the minimum-loss baseline on the scored rows biases LL(A\*) down. That is conservative for BEATS. It is **anti-conservative
for NO_PRACTICAL_GAIN**, and NO_PRACTICAL_GAIN on P1 drives a recommendation in s11 ("the app's live chance should be the
arithmetic"). The draft also does not say whether A\* is chosen pooled or per stat.
*Fix (replace the A\* bullet and add to s4):*
> * **A\*.** Pooled over the four stats and three checkpoints. **BEATS (P1)** iff, for **every** candidate j, `mean(LL(M) - LL(A_j)) <= -0.005` and its 97.5% upper bound < 0 (an intersection-union test: no multiplicity correction is needed). **NO_PRACTICAL_GAIN (P1)** uses `D* = mean LL(M) - min_j mean LL(A_j)`, with `argmin` **recomputed inside every bootstrap replicate**; the word is given iff the 97.5% lower bound of D* > -0.005. A\* (the argmin on the scored rows) is what is reported and what the G4/G5 slices use.

### M2. Power is stated for the wrong event; P2 has no sample statement (s9)
The formula gives 80% power for "upper bound < 0" when the true effect is -0.005. BEATS also needs the point <= -0.005, so at a true
effect equal to the floor P(BEATS) = **0.50**. At 240 games and SD 0.025 (se 0.00161, 97.5% half-width 0.0036), BEATS has 80% power
only at a true effect of about **-0.0064**. At SD 0.044 that becomes about -0.0088. P(NO_PRACTICAL_GAIN | true 0) = 0.80. This is
consistent with the modeler's prior (P(BEATS) 0.25 at an expected -0.004), but the text claims otherwise. The "robust to SD 0.044"
sentence concerns a look count that is not this rule's. The date-clustered interval (30 clusters) has no planning value. P2 runs on
67% of rows (0.50 on the worst night) with no count of its own.
*Fix (replace the paragraph after the power table):*
> At the fixed look (240 games) and the planning SD 0.025, P(BEATS) is 0.50 if the true effect is exactly -0.005 and 0.80 if it is -0.0064; P(NO_PRACTICAL_GAIN) is 0.80 if the true effect is 0. At SD 0.044 the 80% BEATS effect is -0.0088. The SD only changes these probabilities, never the look date. With 30 date clusters the percentile bootstrap may under-cover; the wider interval rule (s4) is the guard. **P2** is scored on games with >= 1 two-sided primary row; if fewer than 200 such games exist at D, the P2 word is INSUFFICIENT_SAMPLE. The main session adds the two-sided game count from the s9 store query (`count(distinct event_id) filter (bid_c >= 1 and ask_c - bid_c <= 10)`) before the freeze.

### M3. The market arm is later than the model's state; and a zero bid counts as "present" (s3 Market, s10)
In the store, `known_at` is the Kalshi page **at or after** the state summary, up to 90 s later (`checkpoint_scorer._quote`). The
state itself trails ESPN's play time by a median of 67 s (Part 1). The market had moved before our summary showed the play on 92% of
material events. So the mid carries roughly 1 to 2.5 minutes more information than M's input. This favours the market. A P2 BEATS is
therefore conservative, but "NO_PRACTICAL_GAIN on P2 = the market already knows it" (s11) overstates: it is "the market at our tick
beats M on a stale state". Also, `bid_c` is stored as `round(100 * yes_bid)`, so a book with no bid can be stored as 0 and pass "bid
present", which makes the mid half the ask: a one-sided book posing as a price. The rows themselves exist only where the Yes ask was
2-97c with depth and the market was `active` at the quote time. That is selection by post-state market information (for example,
halted markets after an injury), the same for every arm. Fees are irrelevant: no trade is claimed. The text should say both things.
*Fix (replace the Market bullet; add to s10 and s11):*
> * **Market:** on rows with `bid_c >= 1` and `ask_c - bid_c <= 10`, `mid = (bid_c + ask_c) / 200`, clipped to [0.005, 0.995] like every arm. The quote is the page at `known_at`, which is 0-90 s **after** the state M was served on, and that state trails the play by about a minute (live_model Part 1). The mid therefore knows more than M; P2 compares M as delivered with the market as quoted at our tick, not two forecasts on equal information. A BEATS on P2 is conservative; a NO_PRACTICAL_GAIN is not evidence that the market is better on equal information. Fees do not enter: no trade is claimed. Descriptive (S19, never a verdict): the mid from the newest captured page of the same ticker received at or before the state's `last_play_at`, where one exists.
> s10 addition: "Rows exist only where the Yes ask is 2-97c with depth and the market is active at the quote; that selection uses post-state market information and is the same for every arm."

### M4. Display and interim-score rules must be frozen as obligations, including the app's version-2 reader (s8)
Today no path computes a score: the checkpoint store has no outcome column, checkpoint overs are never logged as picks, the final card
strips the numbers, and the app hides `post` games. But nothing in the frozen text binds the *next* change. Three such changes are
already planned or possible. (1) HALFTIME_OVERS_VS_PRICE H2 reads the same rows' outcomes if anyone freezes it. (2) The post-freeze
scorer, if run early (B4). (3) **The app's live score card (new fact, 2026-11-17).** Merging `agent/market-game-scripts-2` makes JARVIS
write snapshot **version 2**; `app/api/.../live.py:_load_snapshot` accepts only `version == 1`, so the merge alone shows nothing. Devin
wants the reader changed so the score card shows the model's chance labelled "Experimental, not yet tested" with its age. That is
allowed and harmless to the test only under the conditions below. The C5 text also still says "replacing or beside", but C5 = the
default ("replacing").
*Fix (replace s8's first and last bullets, add four):*
> * **Allowed:** for a game in progress, the newest checkpoint's `p_model` per (player, line), its checkpoint, game clock and age (`now - known_at`, shown in minutes), the player's current box line, under the exact label **"Experimental, not yet tested"**. This **replaces** "UNPROVEN: ..." in the app (`CHECKPOINT_LABEL`), the snapshot contract and the card block (C5).
> * **The app's version-2 reader** (score card): it may accept `version` 1 or 2 and must (a) keep hiding every model number once the game state is `post`, (b) show no number older than 180 s as current (show the age, or hide it), (c) copy only the v1 whitelisted fields plus the `script` block, never a price, (d) keep no history: no stored series of past checkpoints, no server log of `p_model` values per game, nothing that could be joined to finals later. The reader change is reviewed against this section before it ships. It touches no pinned path (B3), so it is not a rule change.
> * **No other rule, job or agent reads outcomes of post-freeze checkpoint rows before the look.** HALFTIME_OVERS_VS_PRICE stays an unfrozen draft (C6). Any new rule that would score these rows waits for this rule's results or uses only rows after D.
> * The checkpoint scorer and the JARVIS tick write only `checkpoint_scored` / `checkpoint_skipped` rows, the snapshot JSON and counts (stored, fires, refusals). Anyone may read counts; nobody joins outcomes.
> * A finished game's Discord card keeps only "Last in-game check; its prices are not current." (today's text, `cards/text.py:264-265`). Restoring numbers on final cards is a display change that breaks this rule.
> * (last bullet, unchanged meaning) A model change (new artifact) during the window ends this rule's rows (G2); the app may show the new model only under a new rule id.

### M5. Unstated details the post-freeze scorer would otherwise choose (new section "Unstated details fixed before any scored number", as LINE_CAL_LIVE s12)
> - Clip [0.005, 0.995] on every arm, applied before LL; A1 is computed unclipped and clipped by the scorer.
> - Rows are weighted equally (a player with more listed strikes weighs more; the market chooses the listing). Unit for G4 = player (`player_id`, or `player_name` when null).
> - "Game" = distinct `event_id`; "date" = `slate_date` (ET).
> - Bootstrap: one multinomial resample matrix over sorted `event_id`s, `default_rng(2026)`, B = 10,000, shared by P1, P2 and every printed contrast. Dates: a second matrix over sorted `slate_date`s, `default_rng(2027)`. **"The wider decides" = the union: the upper bound is the larger of the two uppers and the lower bound the smaller of the two lowers.** One-sided slice p = (1 + #{boot of LL(A\*) - LL(M) <= 0}) / 10,001.
> - A1p join: `forward_predictions` for the same `player_id` and game, the latest `made_at <= tip - 60 min`, with the tip from `data/schedule/live_tips_2026-27.parquet` (`gameDateTimeUTC`). If `player_id` is null or there is no row, A1r's value is used (counted).
> - Freeze date = the ET calendar date of the freeze commit's **committer** time on `main`; the main session merges with `--no-ff` (a rebase or squash moves it), as LINE_CAL_LIVE s2.4.
> - Season 2026 is not a holdout (CLAUDE.md, 2026-11-17), so no HOLDOUT_ACCESS_LOG row is needed for the look. M's and A1's recency inputs can include season-2025 final lines as **features** (`recency_min_game_date: null`); that is not an evaluation and is pinned by B3.

### M6. No attack list (CLAUDE.md: every claimed win is attacked)
*Fix (new s6b):*
> **Attacks at the look, before any word is believed (descriptive, reported beside the words):** (1) shuffled labels within (slate date x checkpoint x stat): M - A\* must be 0 within noise; (2) planted future for M: re-serve M on 50 rows with the state replaced by the next summary after the checkpoint; the values must change, and must not change when an unrelated later file is added; (3) missingness: refusal and exclusion rates (`state_stale`, `checkpoint_missed_late`, `refused_late_write`, `n_prior < 5`, `extrapolated`, ask outside 2-97c, `x_not_active`) by outcome group (y, starter, |margin| >= 20, stat above or below the line's median) on post-freeze games; an asymmetry not knowable at the state is a leak; (4) replication: first half vs second half of the window by date, drop the best night, drop the best team; the sign must hold; (5) the knockout analogue: M minus A1g (or A1e) by foul-trouble slice (S16); a gain that lives only there is foul information, to be named as such.

---

## Minors (exact text)
- **m1. s13 / Open choices.** Replace the heading "Open choices for Devin (defaults taken in the text)" with "Choices (settled: Devin, 2026-11-17, all six defaults)". Delete "optional," and "If C3 is declined, A1e does not exist." from s3, and "(maintainer's heavy command before the freeze; digest pinned in the freeze commit)" (superseded by B1).
- **m2. s9 projection.** "(2027-01-03 / 01-05 if frozen by 11-24; 01-09 / 01-11 if frozen by 12-01)" -> "(trigger 2027-01-04, scored 01-06, if frozen by 11-24; trigger 01-10, scored 01-12, if frozen by 12-01; schedule parquet recount)".
- **m3. s9.** Replace "(it would still pass at SD <= about 0.044)" with "(the gate does not depend on SD; at SD 0.044, 736 games would be reached by 2027-03-12, night 97 of 126)".
- **m4. s4 words.** Add "`NO_PRACTICAL_GAIN` includes the case where M is worse; the point and CI are always printed".
- **m5. s3 A1.** Add: "A1 models regulation only (`R <= 48 - e`); overtime production, which M learned, is missing from A1. G9 shows its size."
- **m6. s3.** Optional, free under the intersection rule: add B1-grid = `P_A0(Y >= k) / P_A0(Y >= cur)` from the production grid as a candidate.
- **m7. G6.** "changes A1r on > 90% of rows" passes trivially (e always changes). Replace it with: "A1 is called only with the stored row's fields (a test asserts the scorer constructs `CheckpointState` from those columns and nothing else)".
- **m8. s6 G7** also for P2 (it says G7 applies to P2; restate that freshness is judged on `play_age_s` of the state, not on the quote).
- **m9. Status line.** At the freeze: "FROZEN <date time from `date`> ...; adversary pre-freeze review docs/reviews/INGAME_LINE_CAL_FWD_prefreeze_2026-11-17.md and its re-read".
- **m10. Capture dependency.** Add to s1: "The live capture (cadence, endpoints) is not pinned; a capture change (for example a merge of `nba/live/capture.py` from `agent/market-ingame-v2`, or the scorer reading the fresh poller) is listed by the scorer with its first slate; reading the fresh poller in the scorer is a pinned-path change (B3)."

## Conflicts and order of work
- **`agent/market-game-scripts-2` merge: before the freeze.** Its diff to `ingame_checkpoints.py` and `checkpoint_scorer.py` is
  additive and, by reading, row-neutral (snapshot written after `store.close()`; `latest_states` filled after the loop). Merging first
  means the B3 pins are the merged files and no post-freeze certificate is needed. If the merge slips past the freeze, it needs the
  B3 row-neutrality replay before it lands. It does **not** change what is logged.
- **App version-2 reader (score card): after the freeze,** reviewed against the frozen s8 (M4). It is display-only and touches no
  pinned path, so it does not move the window. Shipping it before the freeze would not harm the test (all pre-freeze rows are burned),
  but it would ship a display whose obligations are not yet binding, and the DECISIONS row that supersedes L1375 "no live use" should
  come first. Order: merge game-scripts-2 -> freeze -> DECISIONS display row -> reader v2 and the label change -> review against s8.
- **INGAME_WINPROB_V2** (frozen, unmerged): separate code (`nba/ride`, `nba/papertrade`), no shared pinned file. Its `nba/live/capture.py`
  could change the capture (m10).
- **HALFTIME_OVERS_VS_PRICE:** stays an unfrozen draft (C6). Its H2 would read these rows' outcomes, hence the M4 bullet.
- **ESPN_FRESH_POLLER_NIGHT1:** no overlap while the scorer does not read `fresh/` (B3, m10).

## Before the freeze, in order
1. Main session merges `agent/market-game-scripts-2` (row-neutral by reading; optional confirmation: `checkpoint_scorer replay` on one burned night before and after, rows identical except `written_at`).
2. Modeler revises the text with B1-B4, M1-M6 and m1-m10, and writes `nba/eval/ingame_arith_fit.py` with tests (A1e and, if accepted, A1g; spec in B1/B2).
3. Devin answers the one new choice: A1g in the binding set (recommended) or the fallback text (B2).
4. **The C3 fit runs before the freeze, off game night** (heavy, about the T508 harness's 7-8 minutes; not during live games on this 8 GB Mac). Command shape (the module does not exist yet; the maintainer runs it from the modeler's worktree with `data/` symlinked per the worktree-data rule):
   `cd /Users/devin/Downloads/<modeler worktree> && /Users/devin/Downloads/nba-prediction/.venv-1/bin/python -W ignore -m nba.eval.ingame_arith_fit fit --seasons 2019 2020 2021 2022 2023 2024 --map 30:30 35:36 40:42 --out reports/prereg_ingame_line_cal_fwd/a1_fits.json`
   It must assert `max(season) <= 2024` and refuse to overwrite.
5. Adversary re-read of the revision (new contradictions, not only these fixes).
6. Main session runs the s9 store count when no game is live (copy first), adding the P2 two-sided game count (M2), and appends both to the text.
7. Priors (restated against A\*) to `docs/PREDICTIONS_LEDGER.md`; freeze commit on `main` with `--no-ff`: `prereg(INGAME_LINE_CAL_FWD): <sha256 of the bytes above === RESULTS BELOW ===>`, recording the sha256 of the A1 module, its tests, the fit module, `a1_fits.json` and the B3 pinned paths and values.
8. DECISIONS display row (the modeler's draft, with the M4 conditions), then the app reader change.
Realistic freeze: 2026-11-19 only if the fit runs on 11-18 during the day and the re-read is the same day. Otherwise the first night is 11-25 and the trigger 2027-01-04.

## Single most likely failure mode if a P1 BEATS is later claimed
The binding arithmetic is weaker than the fair baseline the project already built (B2). M "beats arithmetic" by about the size of the
T508-attack residual's gap, and the win is read as model skill. Second: a mid-window input change, such as the recency window or the
candidate filters, shifts M's served numbers with no digest change (B3).

## What I could not verify
- The live store's own counts and its two-sided game count (I may not open it).
- That the game-scripts-2 merge is row-neutral by replay (by reading only).
- How strong A1e or A1g would be historically (needs the heavy fit; not on game night).
- Whether Kalshi ever reports `yes_bid = 0` rather than null for an empty bid in this capture (the stored `bid_c` could be 0 either way; the fix covers both).
- ESPN and Kalshi clock agreement beyond Part 1's numbers.

## Proposed rows (drafted, not appended)
**PREDICTIONS_LEDGER (adversary):**
| 2026-11-17 | INGAME_LINE_CAL_FWD as revised (A\* incl. A1g) | adversary: P(P1 BEATS) 0.15; P(P1 NO_PRACTICAL_GAIN) 0.45; P(P2 BEATS) 0.03; P(frozen by 2026-11-24) 0.55 | docs/reviews/INGAME_LINE_CAL_FWD_prefreeze_2026-11-17.md | pending | |

**DECISIONS:** none from this review. The order of the game-scripts merge and the reader change is a recommendation for the main session.
