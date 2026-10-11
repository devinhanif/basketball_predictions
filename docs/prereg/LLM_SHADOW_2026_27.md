# LLM_SHADOW_2026_27: a language-model shadow forecaster of prop legs, scored live beside production (pre-registration)

Status: DRAFT 2026-10-11 (not frozen; no call to any model has been made, no 2026-27 forward row exists). COMPANION to
`docs/FORWARD_PREREG_2026_27.md`: it reuses that rule's conventions and n gates and edits nothing in it, and it does not
enter that document's BH family (m = 9). Approval: `docs/DECISIONS.md` 2026-10-11 17:20 ("LLM shadow forecaster for 2026-27 approved").
Freeze evidence once committed: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LLM_SHADOW_2026_27.md | shasum -a 256`.
Seasons: 2026 = 2026-27 regular season only (`nba.daily.season.FORWARD_SEASON`). Seasons 2023-2025 are never used by this arm
for any score (see "Why there is no historical gate"); 2025 is never loaded.
Open fields that block the freeze (the gate refuses a frozen run while any is unfilled):
`MONTHLY_CAP_USD = <<DEVIN FILLS>>`, `PROMPT_SHA256 = 1aac0d67441898ea51b6be22499c79d1e99c2a311284d9e0836ee89a3b83c579` (section 3; filled 17:39 CDT from branch commit 223fa19).

## Why
Devin: could an LLM, reading the same as-of facts the pipeline holds, be a better thinker about a prop line than the production
model? Every architecture-only idea here has lost or tied; the two wins were information. The LLM adds no new pre-tip information
(it is shown only what the pipeline already knows). The one thing it might add is a different READING of the same facts (what a
questionable tag means for minutes, how a back-to-back plus a missing big man shifts rebounds). That is the whole hypothesis, and
it is the reason the prior below is low.

## Hypothesis
H1: given the inputs of section 1, the LLM's P(over) at the DraftKings T-60 main line has lower log loss than production's P(over)
at the same line, by at least 0.002 with a date-clustered 98.18% CI upper bound below 0, over the regular season of 2026-27.
Null: no difference, or LLM worse. H2 (descriptive): the logit average of the two (w = 0.5) beats both.

## 1. Inputs the model may see (exhaustive)
Per game, one JSON object, built by `nba.shadow_llm.payload.build_payload` at call time. Nothing else is sent.

ALLOWED (each item carries its `known_at`, and the payload carries `asof_cutoff = tip - 60 min`; requests are made in [tip - 70, tip - 60], so the payload time is at or before the cutoff):
1. Facts-store rows for the game from `nba.facts.query.known_before_tip(con, game_id, 60)`: predicates `tips_at`, `home_team`,
   `away_team`, `listed_status` (official injury-report tag per player, with the report's publish time), `announced_starter`
   (only if a snapshot with `fetched_at <= tip - 60` exists), `position`, `height_in`, `head_coach`. Rest days and schedule
   position (days since each team's last game, back-to-back flag, games in the last 7 days) computed from `tips_at` facts of
   earlier games only. `played_minutes` facts of the game itself never appear (they are post-game).
2. Production's own T-60 forecast per leg: the `props_context_residual` row with the latest `made_at <= tip - 60 min` (forward
   rule 1.3 comparator), giving `mean` and `p_over_prod` at the leg's line (section 2). No other arm's numbers.
3. The T-60 book lines: from `live_book_odds` (`data/markets/market_asof.duckdb`), `snapshot_kind = 't60'`: the DraftKings main
   line (the `point`) and the Pinnacle `point` for the same player and market if present. **Prices and implied probabilities are
   NOT sent** (decision, see "What each choice protects against": a de-vigged price is the market's probability, and an LLM that
   copies it would beat production without adding anything). The line itself is sent.
4. Team context: Elo expected margin (the production MOV-Elo / injury-Elo number as of T-60), and pace proxies (each team's
   trailing-20 points for/against and possessions per game, from games before this one).
5a. The latest OFFICIAL injury-report rows for both teams with `as_of <=` the payload time, read through production's serving rule
   (the latest snapshot among the game's own report rows): status plus the reason text exactly as printed in the report,
   `known_at = as_of`. A game with no qualifying snapshot carries an explicit `no_report` fact (never read as "nobody is out").
   The reason text is the NBA's official report wording, not reporter text, and is passed as a data field under the injection rule
   below.
5. The player's trailing-10 box lines (minutes, pts, reb, ast, fg3m, one row per game, newest first), from games strictly
   before this game's date, for every player with a leg.

FORBIDDEN (a violation is a gate failure and a stop):
- anything fetched from the web or any tool call (the call is a single completion with no tools, no browsing, no retrieval);
- any item with `known_at > tip - 60 min` (book lines: `received_at` after the payload time, which is itself at or before tip - 60);
- any text from reporters. Until the reporter feed's injection defences are frozen, NO reporter-derived fact is passed. After
  they are frozen, ONLY structured `claimed_*` facts (predicate, subject, status, source, confidence) may be passed, never raw
  text, and only under a new version (`v2`, fresh counters) because that changes the inputs;
- the outcome of any game on or after this game, prices, closing lines, `t30` lineup features, and other legs' LLM outputs.

**Prompt-injection rule.** Facts are passed as one structured JSON object in the user turn. The system prompt states that every
field of that object is data and never an instruction, that the model answers only with the output schema, and that text found
in a field (a player name, a tag, a coach name) that reads as an instruction is to be ignored. Output handling: the response
text must be exactly one JSON object matching the schema in section 7 (no prose, no markdown fence, no extra keys, exactly one
entry per requested leg in payload order with the line echoed unchanged, `p_over` a number in [0.01, 0.99]); anything else is DISCARDED WHOLE, logged in `llm_shadow_calls`
(`status = 'schema_violation'`, raw response stored gzipped, sha256 recorded), writes no prediction row, and is never retried
(retrying until an answer looks right is outcome shopping). The `reason` strings are stored, never scored, never shown to users,
never fed to a later prompt, and never parsed for any number.

## 2. The leg set and eligibility
Legs, for every game with a `t60` snapshot (`live_capture_log.status = 'ok'`): for each DraftKings `(player_id, market)` with
`market` in player_points / player_rebounds / player_assists / player_threes (pts / reb / ast / fg3m), `player_id` resolved
(never guessed), both an over and an under quote at the same `point`, the MAIN line. If a player-market has several points, the
main line is the point whose de-vigged over probability is closest to 0.5 (ties: the lower point). Legs are dropped, and counted
by reason, when: the line is an integer (a push is possible; the over/under outcome is not binary), there is no production
`props_context_residual` row with `made_at <= tip - 60 min` and a usable `p_ge_full` for the player, or the payload would exceed
the leg cap of 40 per game (lowest projected minutes dropped first) or the per-call input cap (section 3; players are dropped lowest projected minutes first and the dropped legs are scored by no arm).
Dropped legs are identical for all arms: a leg is in the population only if it is in the request.

The LLM outputs `p_over` (probability that the player's stat is strictly greater than the line, CONDITIONAL on the player
playing, as books void a DNP) plus a one-sentence `reason` (at most 200 characters).

Production's P(over) at the same line: `p_over_prod = P(Y >= ceil(line))` from the stored `p_ge_full` (convention
`P(Y >= N) = P(continuous >= N - 0.5)`; for a line 24.5 this is `P(Y >= 25)`). A row beyond the stored range gives probability
0 (forward-rule amendment 1). A row with no `p_ge_full` is `unscorable`, excluded from all arms, counted.

**Scoring eligibility (same as FORWARD_PREREG_2026_27 rules 1-2, with the two choices that rule leaves open stated here):**
- PLAYED: the player has `minutes > 0` in `player_game_stats`; DNP / not-in-box legs are excluded identically from every arm and
  counted (`dnp`), exactly as forward rule 1.
- LINE: every leg is scored at the **T-60 line** it was forecast against, whether or not the line moved by the close
  (outcome `y = 1[stat > line]`). Legs whose DraftKings line is UNCHANGED at the `close` snapshot are additionally the CLV
  population (section 4, secondary); a leg with no `close` row at that exact point is "no close available", counted, never
  filled from `t60` (docs/CLOSING_LINE_CAPTURE.md).
- TIMING: requests are made in [tip - 70, tip - 60], so the payload time is at or before tip - 60. The LLM row must have
  `made_at < tipoff` (the shared `append_predictions` guard refuses otherwise) and `made_at <= tip - 30 min` (the response time;
  rows made after tip - 30 are counted as `late` and excluded from the primary). Forward-rule eligibility `made_at <= tip - 60`
  cannot apply to the row because the call takes time after the request; the information cutoff is the payload time instead.
  The LLM's line snapshot is the same T-60 capture production's comparison uses (taken by the capture job in [tip - 65, tip - 55]),
  and the de-vigged-market comparator is that same snapshot. Residual timing difference, stated and not corrected: the LLM's
  payload time lies in [tip - 70, tip - 60] while production's comparator is the latest row with `made_at <= tip - 60`, so the
  two may differ by up to 10 minutes in which injury-report snapshot or run they read; a game whose capture lands after the request
  has no snapshot to read and is logged `no_legs`, counted.
- Population: regular-season games of `season = 2026` only (no preseason, no playoffs). Dry-run rows (version `v1-dry`) never score.

## 3. Routing, budget and cost ledger (constants final)
- Forecast model: `claude-fable-5-1` (the strongest available model, per Jev's routing). Fallback `claude-opus-5-5` only if the
  primary returns a not-available error; every row stores `model_id` and `fallback_used`; a fallback is logged and alerted. The
  fact-extraction cheap model is not part of this arm (later, separate rule). Both model ids are as supplied to this draft and
  have not been verified against the vendor's model list.
- Temperature 0 where the API accepts it (if it rejects the parameter the provider logs the fact once and omits it; the rule
  is "no sampling randomness requested"). `max_tokens` (output) 3,000. One completion per game, no tools, no streaming.
  Transport failures (timeout, 5xx) may be retried up to 2 times with the identical payload before tip - 45 min; schema
  violations are never retried; every attempt's tokens are booked.
- Prompt: `nba/shadow_llm/provider.py` (PROMPT_VERSION "v1", SYSTEM_PROMPT, USER_PREAMBLE, joined by newlines; reproduce with `.venv/bin/python -c "from nba.shadow_llm.provider import prompt_sha256; print(prompt_sha256())"`), `PROMPT_SHA256 = 1aac0d67441898ea51b6be22499c79d1e99c2a311284d9e0836ee89a3b83c579` (filled 17:39 CDT, branch 223fa19; any prompt edit changes it and re-opens the draft); every row
  stores the sha256 of the exact rendered prompt and the sha256 of the payload JSON.
- Token budget (amended pre-freeze, see A4; measured by the steward on a 40-leg game: about 6.3k payload + 0.4k system in, 1.6k out,
  chars/3 bound): planning figure **8,500 tokens per game**, **85,000 tokens per night of 10 games**. HARD nightly ceiling
  **150,000 tokens** (tokens in + out, per ET date; about 17 games). Per-call input cap **8,000 tokens** (estimated before the call;
  over the cap, drop the lowest-projected-minutes players until under; never truncate a fact). When a call would pass the
  nightly ceiling (booked tokens + the call's estimated input + `max_tokens`), the game is skipped, logged `budget_skip`,
  and alerted; games skipped for budget are scored by no arm and counted at every look (latest tips are skipped first).
- Monthly: hard cap `MONTHLY_CAP_USD = <<DEVIN FILLS>>` (calendar month, ET) and a token backstop of **2,500,000 tokens/month** (A5, main session 17:39 CDT: was 2,000,000, only 1.07x a typical 220-game month; 2,500,000 is 1.3x and the config `monthly_token_backstop` is set to match at merge)
  (a typical month is about 220 games x 8,500 = 1.9M, so the backstop is only about 1.07x a typical month and a busy month
  of 235 or more games would reach it: stated, not changed; Devin may raise it before the freeze). Either reached stops the arm for the
  remainder of the month and is recorded as a cost stop (section 4, kill). Dry-run bucket: 600,000 tokens total, separate (a 20-game dry run is about 170,000).
  Season projection: about 1,230 games x 8,500 = 10.5M tokens. Dollars per token are NOT assumed here; the provider config holds
  the price per million tokens for each model id and the ledger converts.
- Cost ledger, like the odds credit ledger: `data/llm/budget/llm_shadow_<ET date>.json` (per night: calls, tokens in/out, USD,
  skips, fallback count) and `data/llm/budget/llm_shadow_month_<YYYY-MM>.json`, written before each call (reservation) and
  corrected after (actual usage from the response). A refusal is logged and appended to `data/ops/ALERTS.md`.
- Real-data check of the leg-count assumption (read-only, counted before drafting; outcomes not looked at): DraftKings `t60`
  rows in `data/odds/odds_history.duckdb`, `season <= 2024`, resolved player, four prop markets, two-sided, half-line:
  2023: 1,316 games, 53,980 legs (41.0 per game); 2024: 1,300 games, 54,705 legs (42.1 per game). So about 41 legs per game
  and about 250 per date at 6.2 games per date; a 41-leg answer of about 35 tokens per leg is about 1,450 output tokens, which
  matches the 1,500 estimate. The harness re-counts this (gate check 7).

## 4. Metrics and rule
Unit: a leg `(game_id, player_id, stat)` forecast by the LLM, with a production comparator, a market comparator, and eligible
under section 2. All three probabilities are clipped to [0.01, 0.99] before scoring (identical for every arm).
Log loss of one leg: `-(y ln p + (1-y) ln(1-p))`. Market comparator: the DraftKings T-60 de-vigged over probability
(`implied_prob` of the over row, proportional de-vig) at the same point.

**Primary.** `D = mean over legs of [LL_llm - LL_prod]`, all four stats pooled (one test; per-stat cells are descriptive).
Negative = LLM better. Also reported: `LL_llm - LL_mkt`, `LL_prod - LL_mkt`.

**CI.** Percentile bootstrap resampling whole GAME DATES (`game_date`, one cluster per calendar date; legs of one night share
news and injury-report timing), paired by construction (mean of per-leg deltas, ratio of sums), B = 10,000,
`numpy.random.default_rng(2026)` re-seeded at every look, level 98.18% (two-sided alpha_look = 0.0182, the forward rule's
Pocock-style level). Same estimator as `nba.daily.checkpoint.date_cluster_boot`. Seed and n logged. n and the number of dates
accompany every number.

**Looks.** Distinct `game_date` count of the arm, calendar order: L14 is monitoring only; L30 is monitoring plus the cost-only early stop (amendment A1); both report pipeline fidelity, schema
violation share, cost, skips and the direction check, and make no efficacy claim. Decision looks: **L60, L120, END** (END = all
regular-season dates, once, within 7 days of the regular season's last game). Each decision look writes ONE immutable
`data/checkpoints/llm_shadow_<look>.json` (counts, deltas, CIs, guards, cost, git SHA, seed, sha256 of the leg table parquet)
and is never regenerated. Weekly briefing numbers are descriptive and not decision inputs (forward rule 10).

**Minimum n before any reading (the forward rule's thresholds, reused).** The pooled test inherits the smallest props gate of
the forward table (T30 pts/reb): at least **60 dates AND 5,000 scored legs** for L60, and **120 dates AND 10,000 legs** for L120
(the ast/fg3m/LT gate). END has no extra gate. At about 250 legs per date both are expected to be met by the date count, not the
leg count. A decision look reached with fewer than the minimum is recorded and yields "keep running". Per-stat cells are not
read below 60 dates and 5,000 legs in the cell (pts/reb) or 120 dates and 10,000 legs (ast, fg3m); at 10 legs per game per stat
the pts and reb cells reach 5,000 legs at about 80 dates, so per-stat cells are descriptive at L60.

**Pass (information only; it opens a conversation with Devin and never promotes, wires or shows anything to users).**
At an eligible decision look ALL of:
- P1. `D <= -0.002` (point estimate);
- P2. the 98.18% date-clustered CI upper bound of `D` is `< 0`;
- P3. `LL_llm <= LL_mkt` at the point estimate (the LLM is no worse than the de-vigged DraftKings T-60 market; amendment A2), with the
  date-clustered CI of `LL_llm - LL_mkt` reported beside it;
- G1. leak audit: `made_at < tipoff` for 100% of scored rows, every payload item at or before its cutoff (checked from the
  stored payload sha256 and the facts store), `fetched`/`received_at` of the lines at or before the payload time (itself at or before tip - 60) for 100% of rows;
- G2. `ok` over attempted games is at least 90% at that look, where attempted = ok + schema_violation + provider_error + input_cap
  (budget_skip, monthly_cap_usd_not_set, monthly_backstop, key_missing, no_legs, as_of_violation, already_written and db_busy_not_written are
  refusals or non-attempts, counted and reported, not in the denominator) (otherwise the sample is a selected subset: "keep running");
- G3. no stat slice with at least 500 scored legs has `D > +0.01`.
Why P3 was replaced (A2): the first draft's P3, `(LL_llm - LL_mkt) <= (LL_prod - LL_mkt)`, is `LL_llm <= LL_prod` after the market term
cancels, on identical legs, so it was implied by P1 (`D <= -0.002 < 0`) and added no test. `LL_llm <= LL_mkt` is a distinct, harder
bar whenever the market beats production (the usual case).

**Kill (cost control; the arm stops and the result is recorded).** At L60 (the first n gate) and at any later decision look:
`D >= +0.005` AND the 98.18% CI lower bound `> 0`. **Early cost stop at L30 (A1):** with at least 30 dates and 2,000 scored legs,
`D >= +0.010` AND the 98.18% CI lower bound `> 0` stops the arm (cost control only, never an efficacy claim). Also: monthly cap (USD or the token backstop) reached, or a leak-audit (G1)
failure that is not repaired by a documented, not result-driven, pipeline fix. A kill is final for the season; a new version
is a new arm with new counters. After END the arm stops unless the END look passed.

**Secondary (descriptive, no pass weight).**
- CLV of the preferred side. Using the book's own RAW (vigged) implied probabilities at T-60, as in MODEL_CLV:
  `edge_over = p - p_over_raw`, `edge_under = (1-p) - p_under_raw`, side = argmax (ties: over). On legs whose DraftKings line is
  unchanged at the real `close` snapshot: CLV = `mult(close) - mult(t60)` of the over probability if over was chosen, the
  negative if under (probability points; `mult` = the stored `implied_prob`). Reported for `p = p_llm` and `p = p_prod` on the
  same legs, their paired difference with a date-clustered 95% CI, and also for legs with `edge >= 0.03` (the MODEL_CLV M1 rule)
  as a threshold variant. Beside each, the shuffle null (the LLM's p permuted within `(game_date, stat)`, 20 seeds, same side
  rule), because MODEL_CLV showed a price-level side choice earns CLV with no model. The forward-rule note stands: the close is
  a benchmark, not a price available at T-60.
- Calibration: ECE and reliability table in 10 equal-width bins on `p_over`, for LLM, production and market, with n per bin.
- Slices: by stat; starter vs bench (`starter10` as the forward rule); report-tagged (own tag questionable/doubtful) vs not;
  first 15 team games vs rest; home vs away; fallback model vs primary. Descriptive.
- Bias: mean predicted P(over) minus realised over rate, by arm.

## 5. Blend arm (descriptive)
`p_blend = sigmoid(0.5 logit(p_llm) + 0.5 logit(p_prod))` (probabilities clipped as above). Reported: `LL_blend - LL_prod`,
`LL_blend - LL_llm`, `LL_blend - LL_mkt`, with the same date-clustered CI. It is not a pass condition and nothing derived from
it may be used to choose a different weight within this arm (w = 0.5 is fixed). "Blend beats both" is a point-estimate statement
about `LL_blend < min(LL_llm, LL_prod)` and carries the CI beside it.

## 6. Minimum-data checks before the freeze (fail = stop; none of them scores the LLM on a past outcome)
Run by `research/eval/llm_shadow_eval.py gate`. Items 1-4 run against a stub provider on a fixture, never a real model.
1. **Plumbing (a).** Stub provider returns production's probabilities plus seeded Gaussian noise (sd 0.03, seed 0) for every
   requested leg. The full path (`run_game`) writes `llm_shadow` rows to `forward_predictions` with `made_at < tipoff`
   for 100% of rows, one row per leg, and the canonical dump (`nba.daily.canonical_dump.canonical_csv`, llm_shadow rows,
   keys + `p_over` + `reason`, sorted, canonical JSON) is byte-identical (sha256 equal) to the same lines built directly from the
   stub's returned answer.
2. **As-of (b).** Every fact in the payload has `known_at <= tip - 60 min`. A planted fact with `known_at = tip + 5 min` (and a
   planted fact at `tip - 30 min`) must be absent from the payload; calling `as_of` for a time after tip must raise
   `PostTipError`.
3. **Refuse after tip (c).** `run_game(..., now = tip + 1 min)` writes zero rows and makes zero provider calls (or the
   shared writer raises `LeakageError`); status `post_tip_refused`, logged.
4. **Schema violation (d).** A provider answer of prose, a fenced JSON block, an extra key, a missing leg, a duplicate leg, or
   `p_over` outside [0.01, 0.99] or NaN, a changed line: zero rows written, `llm_shadow_calls.status = 'schema_violation'`, the raw answer stored, no retry.
5. **Cost on the real provider (e). PENDING.** Measured live by the steward's job once Devin supplies the key: a 20-game
   preseason or opening-week dry run (version `v1-dry`) with the real provider. Pass = mean tokens (in + out) per game <= 10,000
   (150,000 / 15 games) and no game over 11,000 (the input cap plus max_tokens), valid-answer share >= 90%, rows written with `made_at < tip`. Until measured this check is recorded as
   PENDING in the freeze commit and must read PASS before the first regular-season call; if it fails, the per-call input cap or
   the leg set is reduced by a documented, not result-driven, change and the check repeated (fresh dry rows, never scored).
6. **Open fields.** `MONTHLY_CAP_USD` filled; `PROMPT_SHA256` equals the sha256 of `configs/llm_shadow_prompt_v1.txt`.
7. **Leg-count projection** against real data (read-only counts, `season <= 2024`): at least 30 DraftKings main-line legs per
   game on average (observed 41-42), so the L60 gate is met on dates alone.
8. **Integration requirements** (implemented by the steward; each must hold before the first regular-season call).
   (i) `llm_shadow` is excluded from `nba.daily.settle.settle_pending` and the checkpoint scorer, so the forward scorer never scores
   it; requirement 1 is satisfied by this design: the companion rule's own report stage scores the rows from `p_over`. (ii) The
   prediction JSON carries `line` and `p_over` under exactly those names. (iii) The module interface is the one in section 7
   (Provider, build_payload, run_game). The runner returns further statuses than check (d) needs: ok, schema_violation,
   provider_error, input_cap, budget_skip, monthly_cap_usd_not_set, monthly_backstop, key_missing, no_legs, as_of_violation,
   post_tip_refused, already_written, db_busy_not_written. Verified by a test on a DB holding one llm_shadow row.

**Why there is no historical gate (intentional).** The usual gate asks "does the rule produce a sensible result on past data?"
For this arm that is forbidden and uninformative. A backtest on 2022-2025 would score a model whose weights have read those box
scores, props write-ups and results; the contamination cannot be audited away (it cannot be separated from skill, and a "pass"
would measure memory). So no past-season forecast is made, scored, or tuned on, the 2023-2025 seasons play no part in selection,
and the live 2026-27 log is the only test. What replaces the gate is (i) the plumbing and leak tests above on a fixture, (ii)
the count-only data check (leg supply, no outcome), (iii) the n gates of section 4, and (iv) the pending live cost check.
Non-negotiable 8 is met for what this rule needs from data (enough legs per date); it is not met, by design, for "does the model
work", which is the question the season answers.

## 7. Outputs
- **Predictions:** `forward_predictions` in `nba.duckdb` via `append_predictions` (so the shared made_at < tipoff refusal
  applies). One row per leg: `model_name = 'llm_shadow'`, `version = 'v1'` (dry runs `'v1-dry'`), `target` in
  `pts | reb | ast | fg3m`, `player_id`, `tipoff`, `run_id`. `prediction` JSON:
  `{"p_over": float, "line": float, "book": "draftkings", "p_over_prod": float, "reason": str, "prompt_sha256": str,
  "payload_sha256": str, "model_id": str, "fallback_used": bool, "tokens_in": int, "tokens_out": int, "call_id": str,
  "line_received_at": iso}`. Tokens are per CALL repeated on each row; cost is summed per `call_id`, never per row.
- **Calls table** `llm_shadow_calls` in `data/shadow/llm_shadow.duckdb` (one row per attempt): `call_id, game_id, tipoff,
  requested_at, made_at, model_id, fallback_used, status (ok | schema_violation | api_error | budget_skip | post_tip_refused |
  no_legs), tokens_in, tokens_out, usd, n_legs, n_rows_written, payload_sha256, prompt_sha256, response_sha256, raw_file`.
- **Model output schema (as adopted by the steward's module):** `{"forecasts": [{"player_id": int, "stat": "pts|reb|ast|fg3m",
  "line": float, "p_over": float, "reason": str}]}`, one item per payload leg in payload order, `line` echoing the request,
  `p_over` in [0.01, 0.99], `reason` 1 to 200 characters, no other keys.
- **Reports:** `reports/llm_shadow/weekly_<YYYY-MM-DD>.md` (briefing block, descriptive), `reports/llm_shadow/checkpoint_<look>.md`
  plus the immutable `data/checkpoints/llm_shadow_<look>.json`, `reports/llm_shadow/ledger_rows.md` with the TEST_LEDGER rows
  drafted with `T###` placeholders by the weekly report step (the archivist appends; this arm never writes the ledger).
- **Code:** `research/eval/llm_shadow_eval.py` (stages `gate`, `report`), tests `tests/research/eval/test_llm_shadow_eval.py`.
  The steward's module: `nba/shadow_llm/`.

**Interface assumed by the harness** (code against this; the harness reports "module pending" and exits 2 if absent):
```
nba/shadow_llm/provider.py   class Provider(Protocol):
                                 model_id: str
                                 def forecast(self, game_payload: dict) -> dict
                             # returns {"raw_text": str, "model_id": str, "prompt_sha256": str,
                             #          "usage": {"tokens_in": int, "tokens_out": int}}
nba/shadow_llm/payload.py    build_payload(game_id, *, now, paths) -> dict
                             # keys: game (game_id, tip_utc, as_of_t_utc, ...), facts: [{..., "known_at": iso}],
                             #       legs: [{player_id, stat, line, line_pinnacle, p_over_prod, mean_prod, ...}], team, trailing
nba/shadow_llm/runner.py     run_game(game_id, *, provider, now, paths) -> dict
                             # {"status": ..., "rows_written": int, "call_id": str}; paths = dict with keys
                             # facts, forward, market, shadow, nba -> Path
```
Maintainer command (the gate is light, read-only on real data, takes no lock):
`.venv/bin/python -W ignore -m research.eval.llm_shadow_eval gate --allow-draft --doc docs/prereg/LLM_SHADOW_2026_27.md`
Weekly report (reads `season = 2026` rows only, read-only connections):
`.venv/bin/python -W ignore -m research.eval.llm_shadow_eval report --doc docs/prereg/LLM_SHADOW_2026_27.md --sha <64 hex>`

## 8. Prior pass probability (mine, with reasons)
- Beat production on log loss by the pass rule (P1-P3, G1-G3) at any decision look: **about 8%** (Devin's 10%, shaded by the power
  note). Reasons: the LLM receives production's number and the lines, so it is production plus an edit; edits made without a
  calibration loop are noisy; a no-skill edit with a typical deviation of 0.08 in probability from production costs about
  `0.5 x 0.08^2 / 0.25 = +0.013` log loss, which is larger than any plausible gain. Power: with an assumed per-leg paired SD of
  0.16 (an ASSUMPTION; no LLM output exists to measure it), about 5,000 legs and date clustering, SE is about 0.003, so the
  98.18% CI excludes 0 only if the point estimate is about -0.007 or better at L60 (about -0.005 at L120). P1's -0.002 floor
  is therefore not the binding condition; P2 is.
- Kill fires (D >= +0.005 with CI lower > 0) by L120: **about 40%**, for the same reason: a no-skill edit is a measurable loss.
  Expect a stopped arm by about mid-season, costing about 60 nights x 53,000 average tokens (6.2 games x 8,500) = 3.2M tokens before L60.
- Neither pass nor kill by END (no reading): about 50%.
- Blend (descriptive) has a lower point log loss than BOTH LLM and production: **about 30%**, because averaging in logit space
  shrinks the LLM's noise toward production while keeping any different reading of the injury facts.
- CLV: LLM preferred-side CLV greater than production's on legs with edge >= 0.03, date-clustered 95% CI above 0: **about 5%**
  (MODEL_CLV: production's own CLV is about +0.002 and mostly a price-level artifact; the LLM, seeing lines but not prices,
  has less to work with).
- Unverified assumption: the per-leg paired SD (0.16) and the model's calibration are guesses until the L14 monitoring read.

## 9. What each choice protects against
- Live-only, no backtest: protects against scoring memory of box scores as skill.
- Facts as structured JSON, data-never-instructions system prompt, whole-answer discard, no retry: protects against prompt
  injection through names/tags/claims, and against retry-until-it-looks-right shopping.
- No reporter text: raw text is the injection channel and the reporter feed's defences are not frozen.
- Lines but no prices: protects against the LLM copying the market's probability and "beating" production with no information.
- Production's number is in the prompt: the arm answers "does a reader improve on the model", the only comparison worth a
  conversation; it is stated, because it means the LLM is not an independent forecaster.
- Scored at the T-60 line, conditional on playing: protects against a line move choosing the population and against DNP-void
  rules differing across arms.
- Pooled primary, per-stat descriptive: one test, so no multiplicity games; per-stat cells cannot rescue a failed pool.
- Date clustering and ratio-of-sums bootstrap, Pocock level: same as the forward rule (shared news per night).
- Floor and CI both: a significant 0.0005 is not worth a conversation; a point estimate with an interval across 0 is noise.
- Kill at L60 (plus the L30 cost stop) and the token ceilings: protects the budget from a known-loser arm and from a runaway prompt.
- Dry-run rows tagged `v1-dry`, separate bucket: protects the scored population from calibration-of-the-plumbing rows.
- Pass is "information only": protects against promoting on a single season of a non-deterministic vendor model.

## 10. Known threats to validity (declared in advance)
1. Vendor model drift: the provider alias may change under us. `model_id` is stored per call; a change of id mid-season splits the
   analysis into epochs, both reported, and a pooled claim needs the same sign in both (forward rule 10.5).
2. Temperature 0 is not bit-exact determinism for hosted models; the stored raw answer, not a re-run, is the record.
3. The LLM's pre-cutoff knowledge of players is an input (everything it knows is pre-tip); it cannot be audited or removed,
   which is why no past season may be used. The model's training cutoff is unverified here; it must predate the first
   regular-season tip or the arm is invalid; the provider records the vendor-stated cutoff in the first
   calls table row.
4. The residual timing difference of up to 10 minutes between the LLM payload time and production's T-60 comparator (section 2).
5. The population is conditional on playing and on DK having a two-sided main line: not the population of all forecasts.
6. Cost stops and budget skips can leave a non-random subset (latest tips first); counted at every look.
7. Heavy tails in paired deltas with few dates make bootstrap CIs optimistic; the date and leg minimums are the guard.

## Unstated details chosen before scoring
Clip [0.01, 0.99]; main-line definition (closest to 0.5 de-vig, ties lower); integer lines dropped; the playing condition; scoring
at the T-60 line; `made_at <= tip - 30` eligibility; P3 as amended (LL_llm <= LL_mkt); Pinnacle line sent but not its price; the official report rows are sent with reason text as printed; `reason` max 200
characters; whole-answer discard; no schema-violation retry; transport retries <= 2 before tip - 45; shuffle null 20 seeds;
hierarchical gates: dates AND legs; END once within 7 days; alpha 0.0182 kept at 3 decision looks (conservative versus the
forward rule's K = 4).

## Confirmed design decision (not an amendment)
- Inputs are LINES ONLY: no prices and no de-vigged probabilities of any book are sent to the model (main session, 2026-10-11
  17:24 CDT, rejecting the proposal to send the de-vigged DK price). Production's `p_over` and `mean` per leg ARE sent. Protects
  against the model copying the market's probability and "beating" production with no information (section 9).

## Pre-freeze amendments (decided by the main session 2026-10-11 17:24 CDT, before any call or result; the doc is still DRAFT)
- A1 ACCEPTED (cost): cost-only early stop at L30 (30 dates, at least 2,000 legs): `D >= +0.010` with 98.18% CI lower > 0 stops the arm,
  saving about 1.6M tokens (30 nights x 53,000) if the arm is as noisy as the prior says.
- A2 ACCEPTED (P3): the algebraically implied P3 is replaced with `LL_llm <= LL_mkt` at the point estimate, CI reported.
- A4 ACCEPTED (budget constants, decided by the main session 2026-10-11 17:35 CDT from the steward's measurement): planning
  8,500 tokens per game (was 5,500), nightly hard ceiling 150,000 (was 110,000), dry-run bucket 600,000 (was 400,000), season projection
  10.5M (was 6.8M); input cap 8,000, max_tokens 3,000 and the 2,000,000 monthly backstop unchanged; cost check threshold 10,000 mean
  per game (was 7,000). Also in A4: the official injury-report rows (input 5a), the G2 attempted-games definition, the
  request window [tip - 70, tip - 60] replacing the 5-minute asymmetry, and the integration statement (item 8).
- A3 (send the de-vigged DK price) REJECTED; see the confirmed design decision above.

=== RESULTS BELOW ===

(none; no call has been made)
