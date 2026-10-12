# REB_LIVE_2026_27: does the rebounds lead survive the live forward log? (pre-registration)

Status: DRAFT 2026-10-11. Not frozen. Nothing is fitted or scored by this document. It freezes only after the pre-freeze gate
(G-FREEZE below) passes on live capture counts, and only by a hand that can commit, before any live outcome is looked at.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/REB_LIVE_2026_27.md | shasum -a 256`.
Seasons: the test set is season 2026 (2026-27) forward log ONLY. Seasons 2023-2025 are never used to test this rule (2023-24 is
where the lead came from; 2025 stays the frozen holdout and is never loaded).

## Why
`reports/decision_scoring_2026-10-11.md` (descriptive): DraftKings T-60 rebounds, bets where the production model's EV after the actual
American price is >= 5%: ROI +1.9% [+0.5%, +3.4%], n 15,620 bets over 2,603 games (2023 +2.4%, 2024 +1.2% [-1.0%, +3.3%]). It is
1 of 20 pooled cells, uncorrected, found by looking. Expect a winner's-curse shrink: the true figure is probably below +1.9%. Prior
that the live log shows a real positive ROI (lower CI bound above 0 at season end): about 12%, mine. A clean "no" is a result we are proud of.

## Hypothesis and fixed selection rule
H: on the live 2026-27 log, the bets selected by the rule below earn a positive ROI per unit staked.
- **Model**: the production props model as served (`props_context_residual`, the T-60 primary arm, stat `reb`), read from the stored
  `forward_predictions` row; P(Y >= k) from the stored survival grid (`p_ge` / `p_ge_full`). Never refit, never the `_t30`
  comparison arm, no later row. The row used is the latest with `made_at` <= the DraftKings snapshot's `requested_at`, and `made_at` < tip.
- **Price**: DraftKings, `player_rebounds`, the `t60` snapshot of `live_book_odds` (`snapshot_kind='t60'`, `received_at` < tip, written by
  `nba.odds capture`). Main line = the DK line with both sides quoted whose de-vigged P(over) is closest to 0.5 (same
  rule as `research/eval/odds_history_eval.main_lines`; ties to the lower line). Half-point lines only; whole lines are skipped and counted.
- **Side and EV**: `docs/DECISION_SCORING.md`: EV_side = p_side x decimal_odds - 1 - fee (fee 0.0, sportsbook), actual American prices
  (not de-vigged). Bet the side with the larger EV when it is >= 0.05. One unit each, for scoring only.
- **Not eligible, counted and reported**: no matching production row, no DK t60 snapshot (unmatched, refused, post-tip), player
  unresolved, whole line, DNP (a DNP leg is dropped, as in the historical pairs; the dropped count is reported by cause).
- **Metric**: ROI per unit staked (`docs/DECISION_SCORING.md`), game-clustered bootstrap 95% CI (2000 draws, seed 0), n, hit rate,
  mean decimal price; beside it the same-count market-lean and random placebo, and the model's chosen-side probability bins.

## Sample size (computed from the 2023-24 variance, stated before any live data)
Measured on the 15,620 historical bets: per-bet SD 0.950, game-clustered SE 0.0076, design effect 1.00, so sigma_eff = 0.95 per bet.
- CI half-width = 1.96 x 0.95 / sqrt(n): n 5,000 -> +/-2.6%; 7,000 -> +/-2.2%; 8,668 -> +/-2.0%; 17,690 -> +/-1.4%.
- To tell a true +2% from 0 with 80% power (lower bound above 0) needs **n = 17,690**; to have the CI half-width equal 2% needs
  **n = 8,668**. Neither is reachable this season. Live capacity: about 49 games/week, 5.4 (2024) to 6.5 (2023) selected bets per game
  = **265 to 320 bets/week**, so at 100% capture a full 25-week regular season gives about 6,600 to 8,000 bets. 17,690 would take
  55 to 67 weeks (two to three seasons).
- Therefore the rule is built to what this season can reach, and says so: **n_min = 5,000 settled bets**, reached at week 16 (320/wk) to
  week 19 (265/wk) at full capture, i.e. mid-February to early March, and at week 22-25 at 85% capture. Power at season end (n about 7,000):
  if the true ROI is +2% the lower CI bound clears 0 about 42% of the time; at +3% about 70%; at +1% about 15%. **A season-end "not
  confirmed" is not "no edge"**, and the rule is written so nobody reads it that way.

## Decision rule (two looks, one confirmatory test)
- **Look 1 (interim, kill only)**: the first Monday settle after cumulative settled n >= 5,000. May kill; cannot declare success.
- **Look 2 (final)**: the settle after the last regular-season game. Success is tested here only, on all regular-season bets (playoffs
  excluded; not forecast-comparable volume).
- **Success (lead survives; still not a promotion, which is Devin's)**: at Look 2, n >= 5,000 AND ROI >= +1.0% (practical floor) AND the
  95% game-clustered CI lower bound > 0. One confirmatory test, alpha 0.05, no multiplicity (single cell by construction).
- **Kill (drop the rebounds lead; no variants; one pre-registered follow-up at most, in 2027-28 if success was merely "inconclusive")**:
  at Look 1 or Look 2 with n >= 5,000, the 95% CI upper bound < +1.0% (the floor is ruled out), or at Look 2 ROI < 0.
- **Inconclusive**: anything else at Look 2. Reported with n and CI; no claim; the pre-registered next step is only to keep the capture running.
- **Voided (not failed)**: if projected final n < 5,000 at the Week-6 check (gate G5), the rule is declared not executable this season
  (non-negotiable 8) and nothing is scored.
- Looks are on the settle schedule only; no peeking at ROI between looks (the dashboards must not print this cell's ROI before Look 1).
- Reporting beside the verdict, never changing it: ROI by month (descriptive), market-lean and random placebo at the same n, the
  chosen-side probability bins, hit rate vs the model's mean probability, clv of the bets to the `close` capture (descriptive).

## Gate checked against the real forward data path (2026-10-11, nothing scored)
| # | check | measured today | result |
|---|---|---|---|
| G1 | capture flag | `NBA_CLOSE_CAPTURE=1` is a line in `.env` and the hook in `ops/nba_lineups.sh` runs `close` then `t60` every 5 min; the flag is **already on** (morning briefing 2026-10-11). It is off by default in code, and turning it on or off is Devin's call | ON, not mine to change |
| G2 | DK t60 reb prices captured to date | `live_capture_log`: 6 attempts, all `unmatched` ("no unique vendor event"), all for preseason game ids 0012600040/41; `live_book_odds`: **0 rows** | **UNVERIFIED**: no priced row has ever been written. The first regular-season night is the real test |
| G3 | DK and rebounds in the pull | `configs/odds_live.yaml` regions `us,eu`; markets include `player_rebounds`; history shows DK quotes rebounds | plausible, verify on night 1 |
| G4 | production P(over) from the stored row | the stored `_t30` row carries `p_ge` and `p_ge_full` for reb, so P(Y>=k) at any half-point line is recoverable; the primary T-60 row's schema must be confirmed identical on night 1 (today only 136 preseason `_t30` rows exist, no primary regular-season rows) | **UNVERIFIED for the primary arm** |
| G5 | reachable n | 5.4 to 6.5 selected bets/game x ~1,230 games = 6,600 to 8,000 at full capture; 5,000 needs capture x eligibility >= 4.1 bets/game over the whole season | reachable on paper |
| G6 | scheduling | preseason ids (001) are skipped by the slate but the capture still tried them (harmless, 0 rows) | note |

**G-FREEZE (the rule is frozen only if all hold, measured by counts, never by outcomes, on the first 14 regular-season days)**: (a) DK
t60 snapshot captured and matched for >= 90% of games; (b) a production primary reb row exists for >= 95% of those players-games, with p_ge
recoverable; (c) selected bets per captured game (EV >= 5% count, no outcomes looked at) >= 4.5, so projected final n = bets so far + 4.5 x
games remaining x capture >= 5,500 (10% margin over 5,000). If any fails, the rule stays DRAFT and is not frozen this season (non-negotiable 8);
the capture keeps logging and the question moves to 2027-28 with the corrected plan. Counting selected bets is not looking at results;
no ROI or hit rate is computed before the freeze commit.

## What Devin must switch on (his decisions, not done here)
1. `NBA_CLOSE_CAPTURE=1` in `.env` (already present), the-odds-api key `THE_ODDS_API_KEY` in `.env` (present), `local.nba.lineups` launchd job loaded.
2. Nothing else: no new job, no key beyond the existing one. Cost about 14 credits per game-snapshot (docs/CLOSING_LINE_CAPTURE.md).
3. Confirm the freeze after G-FREEZE passes (about 2026-11-04 if the season opens 2026-10-21).

## What each choice protects against
- Live log only: the lead was found on 2023-24, so any rerun there is not a test.
- Rule fixed to the production row and the DK t60 snapshot as captured: protects against choosing the book, line or model after seeing returns.
- Floor +1.0% and CI lower > 0 at one look: protects against calling a positive point estimate an edge; one confirmatory test, no multiplicity.
- Interim look is kill-only: protects against optional stopping on a lucky streak.
- The honest power statement: protects against reading "not confirmed" as "refuted", and against claiming the rule can see +2% when it cannot.
- Count-only freeze gate: protects against freezing a rule that cannot reach n (non-negotiable 8) and against peeking.

## Outputs (exact; code to be written after the freeze, under `nba/markets/` and `research/eval/`, none exists yet)
`reports/reb_live_2026_27/results.json`, `reports/reb_live_2026_27.md`, ledger rows with T### placeholders. Season 2025 never loaded; no write
connection to `nba.duckdb`; the live store is opened read-only.

=== RESULTS BELOW ===
