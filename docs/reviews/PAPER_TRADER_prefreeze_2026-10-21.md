# PAPER_TRADER pre-freeze review (adversary), 2026-10-21

Verdict: NOT_READY

(This maps to BLOCKED in the review-checklist vocabulary. Do not freeze `paper-trader-v1` as drafted.)

Object: the DRAFT `docs/prereg/PAPER_TRADER.md` (sha256 bdd9f7ae), `configs/papertrade.yaml` (sha256 34d0985b),
`nba/papertrade/*.py`, `tests/papertrade/*.py` on branch `agent/market-paper-trader` (commit 329448f; HEAD 86f2044 only
touches `nba/live/`). Read in worktree `.../scratchpad/wt_pt`. Season 2025 never loaded. No result exists yet, so the
attacks below are on the design, the code paths and synthetic fixtures. Nothing was written outside `docs/reviews/`.

Model class: a decision rule / trading strategy layered on the production props distribution (`props_context_residual`,
gradient-boosted context residual + quantile grid), scored as realized after-fee P&L against market prices. Playbook
check at the end.

## Top finding

The rule can't separate edge from pricing fiction, and it gets looked at every day. Two of the three daily positions
(parlay2, parlay3) are priced at a synthetic product of leg asks, exited at a synthetic product of leg bids, and sized
against leg depth that has nothing to do with a combo order. No such instrument trades at those prices. All three slots
are then pooled into one primary ratio, and `summarize` prints a `claim_eligible` verdict with a CI every day from about
date 50 to date 100. If a false "edge" verdict comes, the most likely route is this: synthetic parlay fills that are
better than any real combo quote push pooled P&L above zero, and a daily look catches the CI while it is above zero.
The "control" that is supposed to catch this is the mirror image of the bet (M2), so it adds no independent protection.

## Findings

### MUST (each blocks the freeze)

**M1. The parlay entry, exit and size prices are not realizable, yet they are pooled into the primary.**
`docs/prereg/PAPER_TRADER.md:35-37`, `:38-41`; `nba/papertrade/rules.py:117-118` (price = product of leg asks, one fee on
the product), `rules.py:162` (leg ask size compared with the combo contract count), `rules.py:218` and
`monitor.py:104-107` (exit at the product of leg bids, taker fee at the product price).
Buying the legs separately pays a sum, not a product. A Kalshi combo is a separate RFQ-quoted instrument, and
`test_no_orders.py:14` rightly forbids `/rfq` and `/quotes`, so its real price is never seen. The prereg calls the
flattery "slight" (`:83-84`) with no measurement. Combo market makers price correlation and margin on top of the legs.
That gap is unmeasured and could plausibly be bigger than the edge this test is able to detect. For a combo of n contracts at
p ~0.05, n is ~150. Checking each leg's ask size against 150 says nothing about combo liquidity.
Exiting a combo leg by leg at the leg bids is also impossible.
Fix (choose one before freeze; safe default first):
(a) The primary metric and the "consider live money" rule use the `single` slot only, the one priced at a real ask and
bid with real size. Parlay slots stay as descriptive, labelled `synthetic` rows with their own n and CI. They never
enter a decision.
(b) If Kalshi publishes public combo (multivariate) market quotes on an allowed GET path, enter and exit parlays only at
those quotes and refuse the slot when none exists. Verify first, against one captured response, that such a path exists.
In either case, edit `:35-41` and `:52-53` to say which positions the primary covers.

**M2. The market-favourite control is the strategy's mirror image, so the condition "(a) minus (c) > 0" is redundant
with "(a) > 0".** `PAPER_TRADER.md:47-50`, `:66-68`; `rules.py:171-177`; `settle.py:131`.
When the strategy buys the underdog side (the usual case, because the biggest model-vs-market gaps sit against the
favourite), the control buys the other side of the same market. The per-contract payoffs then sum to minus the spread.
Worked case (tonight's Giannis 6+ AST: YES 0.66/0.68, strategy NO at 0.34, control YES at 0.68):
E[a] per $ > 0 iff q < 0.660, and E[a] - E[c] > 0 iff q < 0.667, where q = P(YES).
The two conditions differ by under one point of probability. When the strategy itself buys the favourite, the control is
the same position and a - c is exactly 0 (minus exit effects), which only dilutes the comparison. So the control does not
test "beats a no-view trader with the same costs". In effect the live-money rule reduces to "(a) CI > 0 plus the 25%
rule".
Fix: replace (c) with a seeded placebo. Take the same screen-passing pool for that scan (liquidity, band, tip lead), draw
the same slot structure with uniformly random leg and side (seed = hash(scan_id)), enter at the same asks, hold to end.
Its expectation is minus costs and it is independent of the model. Add a closing-line value secondary (entry ask vs the
held side's last pre-tip mid, per contract, date-clustered CI). It has far lower variance than P&L and is the only
metric here with power for a 3-5% edge (see the power note). Keep the old (c) as descriptive only, if at all.

**M3. No look schedule: optional stopping is built in.** `PAPER_TRADER.md:59-61`, `:66-71`; `settle.py:139` (prints
`claim_eligible` whenever the floor is met); `__main__.py:136` (`run` writes a summary every slate).
"At the minimum sample", plus a close "at 100 slate dates", leaves every date in between as a possible look.
Simulation (scratch `peek.py`, 4,000 reps, seed 20261021, prices 0.45/0.20/0.09, 3 positions/date, normal cluster CI):
- true per-$ edge 0: one look at date 51 gives 1.0% false "CI > 0"; daily looks over dates 51-100 give 4.1%.
- true edge -0.05: 0.6% vs 1.9%.
Daily peeking inflates the false-positive rate about 3-4x.
Fix: name two looks.
- L1 at the first slate date where settled, non-void, in-scope strategy positions reach 150 and dates reach 30.
- L2 at 100 dates or season end, whichever is first.
- Alpha split 0.005 at L1 / 0.045 at L2 (two-sided equivalents; or O'Brien-Fleming), with the bootstrap quantiles set to match.
- Futility close at L1 if the upper bound is < 0 (allowed; futility stops don't inflate type I).
- Between looks, `summarize` prints only n, the point estimate and `not_a_look`, never a CI or `claim_eligible`.
- Add a test that the CI is withheld off-look.

**M4. The summary has no scope filter, so smoke or pre-freeze rows and other rule versions get counted.**
`settle.py:102-111` selects all `results`/`positions` regardless of `rule_version` or date. Tonight's smoke run and any
fixture run against the default `data/papertrade/papertrade.duckdb` would enter the primary.
Fix: filter on `positions.rule_version = cfg.rule_version` AND `slate_date >= <freeze date>` written into the prereg.
Test it with a planted pre-freeze row and a planted `v0` row, which must be excluded.

**M5. The exit rule is a spread trigger, not an "edge gone" trigger, and it handicaps the primary by design.**
`rules.py:200-235` (`:217` hold = mid once a leg has started; `:233` absolute 1c margin, exit fee ignored).
Fixture attack (scratch `exit_attack.py`, worktree rules, verified fee object):
- A single whose game has tipped has hold = fair = mid, so it exits at the first post-tip tick whenever the spread is
  2c or less (mid 0.3/0.5/0.7, spread 0.01-0.02: `exit`; 0.03+: `hold`).
- Each such exit gives up 2.0-2.75c per contract versus the expected hold value (half spread plus a 1.5-1.75c exit fee
  that the decision ignores), which is about 4-9% of stake at those prices.
- For parlay3, the 1c margin is absolute against a ~0.09 combo price: it fires at a 6.5% relative gap and holds at 16%.
So arm (a) in liquid markets is "buy at T-75 ask, sell at the first in-game bid". It mostly measures pre-tip drift minus
two fees. The prereg's power section (`:78-80`, sd 1.5-2 per $) describes hold-to-end, not this arm.
Making (a) the primary tilts the test toward a false null ("invalid test is not a null"). It can't produce a false edge,
but it does misstate what Devin approved ("exit when the edge is gone").
Fix: compare net-of-fee proceeds, `est - exit_fee/n >= max(fair, hold) - margin`, and make the margin relative
(`margin = 0.01 * max(fair, hold)` or a fixed fraction of cost). State plainly in the prereg that after tip the rule uses
market mids only (no model update), so it can't express "edge gone". Then make (b) hold-to-end, singles only (M1), the
primary, with (a) - (b) as the exit-rule secondary. Devin's call, since he asked for the exit rule. Safe default: (b) primary.

**M6. The minimum-data gate was never checked against the real data (non-negotiable 8).** `PAPER_TRADER.md:22-25`,
`:59-61`. On 2026-10-21, 1,916 of 2,078 markets had no reviewed alias, so the tradable universe is about 8% of markets.
On top of that come spread <= 5c, ask in [0.10, 0.90], P(play) >= 0.90 and EV >= 3%.
Nobody has shown that 150 in-scope positions (or about 150 singles, if M1(a) is taken) are reachable before 100 dates or
season end.
Fix: replay the scan screen read-only on the captured `data/raw/live_2026-10-21/kalshi` snapshots plus tonight's
forecasts. Report passing legs per slot, then project positions per date under the current alias table.
If the floor is unreachable, write that down and change the rule before freezing it, not after.
Also state how alias-table promotions mid-season are treated (they change the selection universe; see S3).

**M7. Rule integrity isn't enforceable.** The rule is defined by code that is still being edited on this branch:
`nba/live/kalshi_pull.py:quote` sets every price, and `http.py` changed at 86f2044 after the draft commit.
`scan.py:291-295` logs `cfg.params()` and a version string, but no code or input hash.
Fix: the freeze record lists sha256 of the prereg, `configs/papertrade.yaml`, the `fee_model` block of
`configs/parlay.yaml`, `nba/papertrade/*.py`, `nba/live/kalshi_pull.py` and `nba/live/http.py`. `scan` recomputes the
digest and refuses to open positions on a mismatch (the T507 Amendment 3 pattern). Each scan row also records the
alias-table sha256 and the forecast `version`/`made_at` of every traded leg.
The freeze commit must precede the first scan.

**M8. The priors are incomplete.** `PAPER_TRADER.md:93` reads "Pending priors (main session)". Fill them in and copy all
priors into `docs/PREDICTIONS_LEDGER.md` before the freeze commit.

### SHOULD

**S1. The shrink weight of 0.5 contradicts the repo's own evidence.** It is selecting model errors, and tonight's smoke
shows it. `configs/papertrade.yaml:6`, `PAPER_TRADER.md:26`, `:81-82`.
- ODDS_HISTORY H2 fitted w at 0.10-0.35 per cell (ledger line 309). T502 says H2's largest fitted w is 0.25.
- T480: raw P(over) at lines has a calibration slope of 0.15-0.19.
- MARKET_LINE_CLASSIFIER: "one shrinkage weight is the right amount of us".
- T419 MARKET_SHRINK: no_positive_ev_found; production ROI@5% was negative on 3 of 4 stats.
Tonight's Giannis 6+ AST: the stored row (made 15:20 UTC, about 8 h before the 23:30 tip) has P = 0.337, mean 4.65
against `mean_recency` 5.35, while the market is at 0.675. Shrunk at 0.5, NO shows about +40% EV per $, yet everything
above says the gap is mostly model error.
None of this fakes a positive result (realized P&L is scored at real prices). It does make (a) a near-certain loser,
which is a test of nothing new.
Fix: either set `shrink` to a value fixed from evidence before freeze (e.g. 0.25, H2's largest fitted w; or use the T493
line-calibrated probability if it is served), or keep 0.5 and add the stated expectation (negative) and its source to
the prereg.

**S2. Forecast staleness is unbounded.** `forecasts.py:72-79` takes the newest pre-tip row, which tonight was a single
morning run (one Giannis row at 15:20 UTC). The market at T-75 already knows the afternoon injury report and minutes
news; the stored `p_play` filter (`scan.py:129`) uses the morning value.
Fix: record `made_at` age per leg (already in `PropForecast`; persist it to `position_legs`). Pre-register either a
refusal threshold (e.g. forecast older than 4 h at scan) or a stratified descriptive cut.

**S3. The selection universe drifts mid-test.** Alias promotions and production refits or promotions change which
markets are eligible and what the model says. Fix: per-scan alias hash and model version (M7). Pre-register that the test
measures "production as served that night", and that a model promotion during the window is reported as a breakpoint
(descriptive), never as a reason to restart the count.

**S4. The threshold mapping trusts `floor_strike` alone.** `scan.py:110` (title threshold discarded), `:132`
(`k = floor(strike)+1`). Correct on the sample checked (`KXNBA3PT-26OCT21MILWAS-WASTYOUNG3-5`: floor_strike 4.5,
strike_type `greater`, title "5+", so k = 5), but a `greater_or_equal` or integer strike would silently shift by one.
Fix: require `strike_type == "greater"` and agreement between the title's N and k, else skip with reason
`threshold_mismatch`. Add a test.

**S5. The concentration rule is ill-defined.** `PAPER_TRADER.md:67-68` "no single date > 25% of the total P&L" has no
meaning when the total is near 0, and with offsetting negative dates the contribution can exceed 100%.
Fix: max positive date P&L / net total <= 0.25 when the total is > 0, AND the date-clustered CI still excludes 0 after
dropping the best date (the repo's drop-best-month replication check).

**S6. Entry quotes come from the `/markets` listing, not the book.** `scan.py:147-163`, `scan.py:65-73`. A stale listing
ask that is better than the live book flatters entries.
Fix: for each chosen leg, GET `/markets/{ticker}/orderbook` (an allowed path) at entry, and enter at the book's best ask
with depth. Refuse if the book ask is worse than the listing ask by more than 1c. Store the book timestamp.

**S7. Combo DNP and settlement treatment is assumed.** `settle.py:26-36`, `:74` multiplies a fair-price leg value into
the parlay payout. That matches the single-leg rule text (DECISIONS: active DNP settles at last fair price), but how a
combo settles is unverified.
This becomes moot if M1(a) is taken. If not, verify the combo rule text before freeze.
Also state in the prereg whether void and waiting positions count toward the 150 (code: `settle.py:111` counts all
strategy positions; `stats.py:48` counts settled only).

**S8. The no-order test is grep-only.** `test_no_orders.py:20-45` is good but static.
Fix: add a behavioural negative test. Run scan, monitor and settle end to end over an `httpx.MockTransport` that fails
on any `request.method != "GET"` or any path outside `ALLOWED_PATHS`.

### NIT

- N1. `stats.py:21-48` is a second CI implementation. `nba/truth/bootstrap.py` does not exist yet, so this is
  acceptable for now; port it when truth/bootstrap lands.
- N2. `rules.py:118`, `rules.py:240`: `order_fee` is called without `series`. Harmless while `fee_multiplier: {}`, wrong
  the day a multiplier is added. Pass `leg.series` (singles) explicitly.
- N3. `settle.py:128-133`: a - c uses only positions with a settled control while (a) uses all of them, so the sets
  differ. Report both n.
- N4. Out of scope for this review: the branch also carries launchd templates and `nba/live/` changes. Those touch the
  live path and need the replay oracle plus Devin's go per CLAUDE.md.

## Checks run

| check | command / artifact | n | result | pass/fail |
|---|---|---|---|---|
| unit tests | `PYTHONPATH=<wt> .venv-1/bin/python -m pytest -q -p no:cacheprovider tests/papertrade` | 24 tests | 24 passed; exit 1 only from the repo-wide 70% coverage gate on a subset run | pass |
| planted future: forecast after tip / after scan | `tests/papertrade/test_papertrade.py:202`, `forecasts.py:76` (`made_at <= now AND made_at < tipoff`) | fixture | rows at or after tip ignored | pass |
| entry at or after tip | `test_papertrade.py:180`, `scan.py:178-184` | fixture | refused and logged | pass |
| GET-only / no order path | `test_no_orders.py`; read `nba/live/http.py` (httpx `.get` only), `kalshi_pull.BASE` | static | no order, credential or POST path found | pass (static only, S8) |
| threshold mapping | captured `data/raw/live_2026-10-21/kalshi/KXNBA3PT_p0/20261021T185650Z.json.gz` | 1 market | 4.5 `greater` "5+" maps to k=5 | pass (S4 guard missing) |
| fee object | `configs/parlay.yaml:2-15` `verified: true`, `config.py:53` refuses unverified | n/a | verified-flag enforced; source is user-provided 2026-10-08 schedule | pass |
| exit rule behaviour | scratch `exit_attack.py` on worktree `rules.py` | 15 single + 4 parlay fixtures | exits at spread <= 2c right after tip; costs 2.0-2.75c/contract vs hold | fail (M5) |
| control independence | algebra on Giannis quotes | 1 market | a-c>0 iff q<0.667 vs a>0 iff q<0.660 | fail (M2) |
| optional stopping | scratch `peek.py`, seed 20261021, 4,000 reps | 100 dates x 3 | FP 1.0% (one look) to 4.1% (daily) at edge 0 | fail (M3) |
| model-vs-market gap | read-only `nba.duckdb` `forward_predictions`, player 203507 ast, tip 2026-10-21 23:30 | 1 row | P(>=6) 0.337 vs market 0.675; one morning row | informs S1/S2 |
| shuffled labels, missingness, knockout, integer support | n/a: no results exist; nothing was fitted | n/a | n/a | not applicable pre-result |

## Power / MDE and family

- Primary: one test, but under M3 it becomes two looks with a split alpha. Secondary: Holm across (a)-(b) and the
  replacement placebo or CLV comparison.
- Simulated power with one look at 150 positions over 50 dates (same settings as `peek.py`): 2.9% at a true +5% per $
  edge, 27% at +30%.
- The prereg's own half-width of 0.25-0.3 per $ agrees. This P&L test can only close "no loud edge". It can't confirm a
  realistic edge, and a pass is more likely luck, peeking or pricing fiction than skill.
- CLV per leg (M2 fix) is the metric with a chance at a few-percent effect. That is why it should be a pre-registered
  secondary, not an afterthought.

## Most likely route to a false "edge" verdict

Synthetic parlay pricing (M1) lifts the pooled per-$ ratio. Daily summaries with `claim_eligible` (M3) catch a positive
excursion. The mirror control (M2) passes automatically whenever (a) does, and pre-freeze or smoke rows (M4) can add
weight. Fix M1-M4 and that route is closed. What remains is low power, which is honest as long as it is labelled.

## Recommended changes (not applied by the adversary)

In order: M1(a) singles-only primary; M2 placebo plus CLV; M3 two looks; M4 scope filter plus test; M5 exit decision net
of fee with a relative margin, (b) as primary; M6 gate replay on the 10-21 capture; M7 digest refusal; M8 priors. Then
S1-S8. The first trade on 2026-10-22 can still run as an unscored shakedown if it is logged as `rule_version:
paper-trader-shakedown` and excluded by M4. That is the safe default if the freeze slips.

## Playbook check

1. Information or capacity: neither is added. The trader takes production's morning distribution and bets where it
   disagrees with a T-75 market that holds later information (S2). Repo record: information wins, and this adds none.
2. Enough data for the class: the strategy fits nothing. The props model's data appetite is unchanged. The binding
   constraint is sample size in positions (M6, power note).
3. Test validity: not valid as drafted. The comparator is a mirror (M2), pricing is unrealizable on 2/3 of positions
   (M1), the exit rule handicaps the primary (M5), and nothing stops optional stopping (M3). An invalid test is not a null.
4. Lineage: essentially ODDS_HISTORY H2 / MARKET_SHRINK (T419) at a larger, unfitted weight, run forward at Kalshi asks.
   Inherited guards: real-tip gating and fees are kept. H2's fitted-weight evidence (w <= 0.35) was dropped without
   reason (S1).
5. Scored against: realized after-fee P&L at market prices, a line-level question. Our CRPS gains have not reached the
   line (T477/T480), so the prior expectation is negative. That is consistent with the market agent's own prior of
   -0.10 per $.

Distribution-layer class check: the probability used at the line is the raw `p_ge_full`, not the T493 line-calibrated
map. A reliability table at the traded thresholds should be part of the descriptive output (the candidates table already
stores everything needed).

## Not verified

- Whether Kalshi exposes public combo quotes on an allowed GET path (M1(b)).
- The combo DNP rule (S7).
- Listing vs order-book staleness (S6).
- The real fill rate of slots (M6 is the check).
- The smoke run's location and whether its rows sit in any DB that will be summarized.
- `nba/live/` behaviour beyond `quote`, `http.get` and `BASE`; the market agent is still editing it.

## Drafted rows (not appended)

DECISIONS (draft): "2026-10-21 | PAPER_TRADER pre-freeze review NOT_READY (docs/reviews/PAPER_TRADER_prefreeze_2026-10-21.md):
primary restricted to singles at real asks; mirror control replaced by seeded placebo + CLV secondary; two named looks with
split alpha; rule_version and freeze-date scope filter; exit decision net of fee with relative margin; gate replay on the
10-21 capture before freeze. Why: synthetic parlay pricing plus daily looks is a route to a false edge. Reverse if: Kalshi
public combo quotes are shown to exist on a GET path (parlays may then re-enter the primary at those quotes)."
