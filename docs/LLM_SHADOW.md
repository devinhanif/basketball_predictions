# LLM shadow forecaster (`llm_shadow`)

Approved by Devin 2026-10-11 (`docs/DECISIONS.md`, 17:20 entry). A nightly comparison arm: a Claude model
receives only the as-of facts the pipeline already holds for a game and writes P(over) for a fixed set of
player-prop legs, stamped before tip and refused after tip like every other forward prediction. It is never
a production input and never shown to users. Scoring is the companion rule
(`docs/prereg/LLM_SHADOW_2026_27.md`); backtests are forbidden for this arm (the weights have read past box
scores), so the live log is the only test.

**Off by default.** With `NBA_LLM_SHADOW` unset nothing runs and the tree behaves exactly as before.

## What it does

One 5-minute lineups tick (`ops/nba_lineups.sh`) calls `python -m nba.shadow_llm run --date <ET date>
--provider anthropic`. For each game of the slate whose real tip is in `[now+60, now+70]` minutes, has
production's T-60 rows and has no `llm_shadow` row yet, it:

1. builds the payload at `t = now` (must be <= tip - 60; the payload is refused otherwise), sha256 recorded;
2. checks the guards (key present, `MONTHLY_CAP_USD` set, nightly ceiling, monthly backstop) BEFORE any call;
3. sends one request (temperature 0, `max_tokens` 3000, model `claude-fable-5-1`, fallback `claude-opus-5-5` on
   a not-found error, logged; the temperature parameter is omitted and logged if the API rejects it);
4. parses the answer strictly: anything but the exact schema is a `schema_violation`, discarded, never retried;
5. writes one `forward_predictions` row per leg only if `made_at` (our clock after the response) is
   `<= tip - 30 min` and `< tip`; otherwise `post_tip_refused`, nothing written.

Rows: `model_name='llm_shadow'`, `version='v1'`, `target` in `pts|reb|ast|fg3m` (the existing prop target names),
`player_id`, `prediction` = `{p_over, line, book, reason, prompt_sha256, payload_sha256, model_id, tokens_in,
tokens_out, tokens_scope, p_over_prod, pinnacle_line}`. `p_over` is the probability that the stat is strictly
above the DraftKings main line (half-point lines only). Every attempt, including refusals, is a row in
`llm_shadow_calls` in `data/shadow/llm_shadow.duckdb` (its own file; the budget is a sum over it).

`nba.daily.settle.settle_pending` skips `llm_shadow` (its rows carry `p_over`, not a predictive mean/std);
the checkpoint scorer selects by model name and never reads it. Scoring is the companion rule's job.

## Inputs

Allowed (each carries its `known_at`; `assert_as_of` raises on any stamp after `t`):

- facts-store rows with `known_at <= t` (`nba.facts.query.as_of`, which itself refuses post-tip): game, the two
  teams, the leg players;
- production's T-60 numbers from `forward_predictions` of the same night (`made_at <= t`): injury-Elo and MOV-Elo
  win probabilities, per leg `p_over_prod`, `mean_prod`, `std_prod`, projected minutes, P(play);
- the DraftKings main line and the Pinnacle line for each leg, POINT VALUES ONLY, and spread/total points; from
  `live_book_odds` (T-60 snapshot, `received_at <= t`);
- team context from completed games strictly before the game date: rest days, back-to-back, trailing-10 pace
  and ratings (`team_game_advanced`);
- each leg player's trailing-10 box lines strictly before the game date.

Forbidden: prices, implied or de-vigged probabilities (the model must not be able to copy the market), integer
lines, anything from the web, anything dated after `t`, closing lines, any post-tip fact, outcomes of the game,
season-2025 holdout evaluation data outside what the live pipeline already reads for its own features.

## Cost per night

Measured on the rehearsal DB copy with the stub (`chars / 3` as the token estimate, a conservative bound; the
real count comes back in `usage` and is booked): about 6.2-6.3k input tokens for the payload plus 0.4k system
prompt for a 40-leg game, about 1.6k output tokens: roughly 8.3k tokens per game, input-capped at 8,000. The
planning allowance in the rule is 5,500 per game; at 8.3k the 110,000 ceiling covers about 13 games, so a full
slate may hit `budget_skip` on the latest tips. Dollars are not computed here: `configs/cost.yaml` has no
Anthropic rate, and a price is never invented (see "To enable"). Token counts only until Devin adds a rate.

## To enable (two lines, plus the cap)

1. Put `ANTHROPIC_API_KEY=...` in `.env` (gitignored; read from the environment at call time only, never logged,
   never stored, scrubbed from every error string).
2. Put `NBA_LLM_SHADOW=1` in `.env` (or the job environment).

The real provider is refused (exit 4, logged) until `MONTHLY_CAP_USD` in `configs/llm_shadow.yaml` is set by Devin
and the optional dependency group `llm` is installed (`uv sync --extra llm`). The facts store (`data/facts/facts.duckdb`) must be
rebuilt before the T-60 window for the injury report to be in it (`NBA_FACTS_NIGHTLY=1` rebuilds it in the
morning job only); a game whose facts are unavailable is skipped, not sent blind.

## Reading it in the weekly report

```sql
-- coverage and spend per night (shadow DB)
SELECT slate_date, status, count(*) n, sum(tokens_in) tin, sum(tokens_out) tout
FROM llm_shadow_calls WHERE provider_real GROUP BY ALL ORDER BY 1, 2;
-- the arm beside production at the same lines (nba.duckdb; join to outcomes via player_game_stats)
SELECT game_id, player_id, target, json_extract(prediction,'$.p_over') p_llm,
       json_extract(prediction,'$.p_over_prod') p_prod, json_extract(prediction,'$.line') line
FROM forward_predictions WHERE model_name = 'llm_shadow';
```

Watch: games with no `ok` row (coverage), `schema_violation` rate, `budget_skip` count, `post_tip_refused`.
Verdicts (log loss at the lines, CLV, calibration, kill criterion) come only from the companion rule.

## Kill switch

Unset `NBA_LLM_SHADOW` (remove the line from `.env`): the very next tick does nothing. Existing rows stay
(append-only). Nothing else needs unloading; no scheduler is added by this feature.

## Field map to the pre-registration harness

`Provider.forecast(game_payload) -> {raw_text, model_id, prompt_sha256, usage}`; `build_payload(game_id, *, now,
paths) -> dict` (`facts`, `legs`); `runner.run_game(game_id, *, provider, now, paths) -> {status, rows_written}`
(`ok | schema_violation | post_tip_refused` plus refusal codes `budget_skip`, `monthly_cap_usd_not_set`,
`monthly_backstop`, `key_missing`, `provider_error`, `no_legs`, `input_cap`, `as_of_violation`,
`already_written`, `db_busy_not_written`); `paths` keys `facts, forward, market, shadow, nba`.
