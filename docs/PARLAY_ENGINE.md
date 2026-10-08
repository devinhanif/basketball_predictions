# Same-game parlay engine (read-only, paper-trade only)

No order code, no credentials. Paper trades and a shadow log go to a separate writable file
(`data/parlay/paper_trades.duckdb`); `nba.duckdb` and `kalshi.duckdb` are attached READ_ONLY.

## Model
- **Game latents** (`game_model.py`, fitted on seasons 2022-24 only): margin ~ Normal(b + s*logit(p_injury_elo), sd);
  total ~ Normal(a + c*f, sd) with f = as-of decayed average of both teams' game totals (pace x efficiency), shrunk to the as-of league mean.
  Fit: s = 6.97 pts/logit, margin sd = 13.6 (CLAUDE.md's "~12" is a sim target; the injury-Elo residual sd is 13.6), total sd = 18.8, n = 3951 games.
  Win and spread legs read the same margin draw, so they are coherent (P(win) >= P(win by over 7.5), opposite-team legs are exclusive).
- **Player marginals**: 19-point conditional-on-playing quantile grid from `props_context_residual` (`qdist.py`), P(Y>=t) = 1 - F(t - 0.5).
- **Copula** (`joint.py`): one latent per (margin, total, player-stat). Correlations pooled by relation (same_player / teammate / opponent x stat pair),
  player vs own-team margin, player vs total, margin vs total; shrunk (k = 200) and Higham-repaired. Gaussian or t (df 6). Estimated on 2023-24 OOF
  (context-residual OOF + injury-Elo OOF + box scores), 35 pooled pairs, n >= 1300 games each.
- **DNP** (`dnp_leg_policy` / `--dnp-policy`, default `void`): availability is assumed independent of the other latents.
  `void`: a DNP leg voids the whole contract, stake and fee refunded (EV = P(all play) * (P(hit | play) - cost)).
  `loss`: a DNP leg loses whatever the side. Kalshi's real rule is unconfirmed; both are implemented and settle consistently.
  Blowout (|margin|) minute cuts are NOT captured by the linear copula.

## Joint calibration, 2023-24 OOF (`reports/parlay/joint_calibration.md`)
15,798 synthetic same-game parlays (2-4 legs) over 2,633 games; pre-registered rule in `joint_eval.py` (copula kept iff joint log loss CI excludes 0, game-clustered).
Cross-fit (other season's parameters/correlations); only the 2024 fold is strictly walk-forward.

| comparison (A vs B) | slice | n parlays / games | log loss diff (A-B) | 95% CI |
|---|---|---|---|---|
| Gaussian vs independence | all | 15,798 / 2,633 | -0.00053 | [-0.00106, -0.00003] |
| t vs independence | all | 15,798 / 2,633 | -0.00058 | [-0.00114, -0.00004] |
| t vs Gaussian | all | 15,798 / 2,633 | -0.00005 | [-0.00021, +0.00011] |
| Gaussian vs independence | 2024 walk-forward | 7,890 / 1,315 | -0.00099 | [-0.00177, -0.00030] |
| Gaussian vs independence | 2023 cross-fit | 7,908 / 1,318 | -0.00006 | [-0.00082, +0.00066] |

Level log loss: independence 0.37225, Gaussian 0.37173, t 0.37168. Verdict by the rule: **Gaussian copula kept over independence** (CI upper bound
-0.00003, a hair from zero); **t copula not kept over Gaussian**. The gain is ~0.14% of log loss, concentrated in parlays with a player leg;
game-only parlays (n = 615) show nothing. Do not read this as an edge over the market, only as a better joint than the product of marginals.
Leg marginals are fine (mean pred vs observed: player 0.468/0.479, win 0.492/0.499, total 0.496/0.489).

## Commands
```
uv run python -m nba.parlay fit                      # writes data/parlay/joint_model.json (2022-24 game model + 2023-24 correlations)
uv run python -m nba.parlay eval-joint               # ~75 s, writes reports/parlay/joint_calibration.{md,json}
uv run python -m nba.parlay evaluate --date YYYY-MM-DD [--dnp-policy void|loss] [--engine gaussian_copula|t_copula|independence]
      [--combo TICKER1,TICKER2] [--no-log] [--settle]
```
`evaluate` maps open Kalshi game-winner / spread / total / pts-reb-ast-3PM markets to legs, prices both the YES (at the ask) and NO (at 1 - bid) side with
the verified fee schedule (taker, per-order round-up, per-series multiplier), and emits `{legs, model_prob, prob_interval, price, fees, ev, ev_low, verdict, raw_*}`.
No combo contracts are ingested, so `--combo` prices a hypothetical same-game combo at the PRODUCT OF LEG ASKS (marked not tradable, never logged).
PRA/PR/PA/RA and steals/blocks markets are skipped (no joint marginal for sums yet).

## Market-as-prior: what happens today
The model weight is `n/(n+k)` only if `n >= min_settled` (30) and shrinkage skill vs the market is positive. With zero settled history the weight is 0:
the engine **defers to the market and cannot emit a positive-EV verdict** (verified in `tests/parlay/test_joint.py`). Because paper trades are logged only
for flagged positives, a deadlock would follow, so every evaluated contract is also written to `shadow_predictions` (raw model prob vs market mid, no stake);
`--settle` resolves them against box scores and `track_record` feeds the skill statistic. `raw_ev`/`raw_ev_low` show the unshrunk model EV as a hypothesis only.

## Limits
- Eval is conditional on the player playing (OOF has no p_play) and on parlays whose lines sit near the middle of each distribution.
- Correlations are pooled, not per player; blowout nonlinearity and DNP-teammate usage shifts are not modelled.
- `forward_predictions` in `nba.duckdb` is empty right now, so `evaluate` has not been run against live Kalshi prices; it is exercised end to end on synthetic DBs in the tests.
