# Market surfer, paper only: results (2026-10-23)

Rule: `docs/prereg/MARKET_SURFER_PAPER.md` (`market-surfer-paper-v1`, params `configs/market_surfer.yaml`). The rule was committed
(d25dd4c, clarified 250e61e, both before any run) before any price series was read. Nothing was changed after the result. Paper
only: offline replay of stored raw files, no network call, no credential, no order endpoint (a test greps for that).
Label: Devin's override of Claude's advice (FAN_KNOWLEDGE "exit velocity": at fair prices exit timing adds no EV).

Command: `.venv/bin/python -m nba.markets.surfer_run --out reports/market_surfer_paper_2026-10-23` (a few minutes).
Outputs: `reports/market_surfer_paper_2026-10-23/results.json`, `trades.csv` (every trade).

## Verdict

**`no_positive_ev_found`.** By the frozen rule, 1,378 round trips on 2 nights and 13 games lose money after spread and fees.
"Keep your money" stands for this idea. A clean no.

Strictly, the pre-registered upgrade to `negative_ev_supported` needs at least 20 games, and we have 13, so it is not awarded
(the game-clustered CI does exclude zero, see below; that is reported, not claimed).

## Minimum-data gate (run gate, checked before the run)

| Check | Needed | Found | Pass |
|---|---|---|---|
| Complete nights | >= 2 | 2 (10-21, 10-22); 10-23 not yet in `data/raw` | yes |
| Median / p90 snapshot gap (KXNBAGAME) | <= 90 s / <= 180 s | 10-21: 68 / 165 s (max gap 6,679 s); 10-22: 60 / 60 s | yes |
| Trades | >= 100 | 1,378 | yes |
| Combos with captured quotes | needed to test combos | none (no cash-out offer log in either night) | combos untestable |
| Claim gate (positive claim only): nights >= 5, trades >= 300, games >= 20 | | nights 2, trades 1,378, games 13 | FAIL (nights, games) |

Unmapped events: none. Markets dropped for a `scalar` result: 27 (10-21), counted. Order books and the 10-21/10-22 quarter/half
series were out of scope as declared.

## Numbers (per contract, dollars; n = trades; unit = 10-contract accounting unit, not a stake)

| Metric | Value | n | 95% CI |
|---|---|---|---|
| Mean net P&L per trade | -0.0420 | 1,378 trades, 13 games, 2 nights | game-clustered [-0.0645, -0.0274]; night-clustered t(1 df) [-0.254, +0.177] (k = 2, not usable) |
| Hold-to-settle, same entries (entry fee only) | +0.0055 | same | game [-0.0719, +0.0628] |
| Strategy minus hold (paired) | -0.0474 | same | game [-0.0984, +0.0187] |
| Placebo (random eligible entry, same night and series, same exits), 500 reps | mean of means -0.0515 | 500 replications | 2.5 / 97.5 percentile [-0.0609, -0.0417] |
| Real mean vs placebo | 97th percentile of the placebo means; below the 97.5 line (-0.0417 vs -0.0420) | | placebo band is draw noise only, not game-clustered |
| Win rate (net > 0) | 37.8% | 1,378 | not clustered |
| Fees paid per contract (entry + exit) | 0.0281 | | |
| Spread cost per contract | 0.0235 | | |
| Mid-to-mid drift per contract | +0.0096 | | |
| Missed fills (t+1 ask moved > 2c, gap, thin size) | 524 (27% of 1,902 decisions) | | |
| Exit reasons | take-profit 520, stop 605, time 164, settle 89 | | |
| Median hold | 537 s | | |

Decomposition: the mid drift (+0.96c) is small and positive, spread (-2.35c) plus fees (-2.81c) turn it into -4.2c. The exit
rule did not help: strategy minus hold is -4.7c (CI includes zero). Take-profit trades average +4.4c net, stops -12.5c, time
exits -5.1c.

Per night (10-contract unit, fees included; n = trades): 10-21: 835 trades, 11 games, win rate 32.8%, net -$461.85, fees
$237.05, hold-to-settle same entries -$285.92. 10-22: 543 trades, 2 games, win rate 45.5%, net -$116.33, fees $150.73,
hold-to-settle same entries +$361.22. Two nights and a 2-game second night: these per-night figures are descriptive, not a
distribution. By series: KXNBASPREAD (535 trades) -3.9c, KXNBATOTAL (501) -3.7c, KXNBATEAMTOTAL (116) -3.6c, KXNBAPTS (97)
-8.5c, KXNBAGAME (70) -5.3c; the other series have fewer than 25 trades each (KXNBAAST +3.4c on 19 trades is noise).

## What the sample can and cannot say

- Trades are not independent: 535 + 501 + 116 of them are spread, total and team-total rungs of the same 13 games, so the
  game-clustered CI is the honest one and the effective n is closer to 13 than 1,378.
- Two nights: the night-clustered CI is shown only because the prereg asked for it; with k = 2 it is [-0.25, +0.18] and says
  nothing.
- Placebo lead, not a claim: momentum entries were about 1c per trade less bad than random entries (rank 97th percentile,
  just under the frozen 97.5 line) and hold-to-settle of the same entries is about zero. Both are inside the game-clustered
  noise (hold CI +-7c). The frozen forward replication is the same command on nights 10-23 onward; nothing here is to be
  tuned meanwhile.
- Snapshots are 60-68 s apart and the fill is assumed one snapshot later. That is a stated conservative assumption, not a
  measured latency. 27% of decisions missed the fill; the result is sensitive to this and no sensitivity was run (not
  pre-registered).
- Combos were not tested (no captured quotes), so this says nothing about combo cash-out timing. Maker orders were not simulated.
- Not covered: whether a person with a model edge would do better by exiting early; that is the paper trader's arm.

## Honest no-edge note

Prices are close to fair for the horizon of a few minutes. The round trip costs about 5c per contract (spread 2.3c, fees 2.8c)
and the average price move captured is under 1c, so at a 10-20 minute horizon fees plus spread beat the signal. Fast in-and-out
trading on Kalshi NBA contracts, as specified, is a way to pay the fee twice. The answer to "should the surfer trade?" is keep your
money; the data says the same as the prior.

## Proposed rows (drafted, NOT appended)

TEST_LEDGER (next free id; T516 was the last seen, renumber on append):
`| T5xx | MARKET_SURFER_PAPER (docs/prereg/MARKET_SURFER_PAPER.md, rule market-surfer-paper-v1, frozen at d25dd4c/250e61e, branch agent/market-surfer-paper) | Verdict no_positive_ev_found. Run gate PASS (2 nights, cadence 60-68 s, 1,378 trades); claim gate FAIL (nights 2 of 5, games 13 of 20). Net -4.20c per contract per trade (game-clustered 95% CI -6.45 to -2.74c; n = 1,378 trades, 13 games, 2 nights; night CI k=2 unusable). Hold-to-settle of same entries +0.55c (CI -7.2 to +6.3c); strategy minus hold -4.7c (CI -9.8 to +1.9c); placebo mean -5.15c (2.5/97.5 pct -6.09/-4.17c), real at the 97th pct. Fees 2.81c + spread 2.35c per contract vs mid drift +0.96c. Combos untestable (no captured quotes). Replicate on nights 10-23 onward with the same command; no tuning. Season 2025 never loaded; nba.duckdb never opened. |`

PREDICTIONS_LEDGER (prior stated in the prereg before the run):
`| 2026-10-23 | SCORED: MARKET_SURFER_PAPER prior (market agent, before any series was read) | P(positive net mean per trade surviving the claim gate) 0.03; expected net per trade -3c to -5c. Result: -4.2c (CI -6.4 to -2.7c, 13 games); positive-claim gate not reached. Prior held. |`

DECISIONS (draft):
`| 2026-10-23 | Market surfer tested on paper (Devin's override of the exit-velocity advice); verdict no_positive_ev_found on 2 nights, 1,378 trades: round trips lose about 4c per contract after spread and fees; the exit rule did not beat hold. Combos untestable, forward replication on 10-23+ with the same frozen command. | Devin's request; follows "markets are a prior, not a target". | Replication on >= 5 nights, >= 300 trades, >= 20 games passing the positive gate and Devin's decision; or captured combo/cash-out quotes (new rule). |`

## Maintainer commands

Replicate on later nights once `data/raw/live_<night>/` and the settled results are in `data/kalshi/kalshi.duckdb`:
`.venv/bin/python -m nba.markets.surfer_run --nights 2026-10-23 2026-10-24 --out reports/market_surfer_paper_<date>`
(from the repo root, read-only; a minute or two per night).
