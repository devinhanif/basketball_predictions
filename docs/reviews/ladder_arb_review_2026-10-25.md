# Adversary review: within-venue ladder arbitrage (agent/market-ladder-arb at 70b0147, 2026-10-25)

Scope: commit 70b0147, the only commit on this branch after the main merge 9787f2f. It adds `ladder.py`, `ladder_source.py`
and `ladder_dryrun.py`, the ladder hooks in `runner.py`, `sinks.py`, `props.py`, `__main__.py` and
`configs/fastarb.yaml`, and `tests/markets/fastarb/test_ladder.py`.

Reviewed in a scratch detached worktree at 70b0147, with `data/` symlinked to the main checkout (checked with `ls -la`).
I computed no outcomes and touched no 2025 data. The only DuckDB opens were read-only, on scratch copies of
`data/kalshi/kalshi.duckdb` and `data/lineups/lineups.duckdb`.

Verdict: BLOCKED

(In the caller's terms: **CHANGES_REQUIRED**, one fix with two parts. The Kalshi ladder is sound. The Polymarket US ladder
promises a $1 floor that rests on settlement rules nobody has read, and the alert and record texts say "pays >= $1 either
way" / "whatever happens" next to a void-risk tag that contradicts them.)

## Checks (exact commands; exit codes shown; nothing piped through tail or head)

| # | Check | Command | n | Result | Pass/fail |
|---|---|---|---|---|---|
| V1 | Lint | `ruff check nba/markets/fastarb tests/markets/fastarb` | 22 files | exit 0 | pass |
| V2 | Format | `ruff format --check nba/markets/fastarb tests/markets/fastarb` | 22 files | exit 0 | pass |
| V3 | Types | `mypy nba/markets/fastarb` | 17 files | exit 0 | pass |
| V4 | Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/markets/fastarb tests/test_layering.py` | 106 | 106 passed, exit 0 | pass |
| P1 | Payoff proof (Kalshi) | read `test_every_pair_pays_at_least_one_dollar_in_every_outcome` (`test_ladder.py:100-155`) | 870 final scores x >= 60 pairs | An independent evaluator of each market's English contract checks every canonical pair: moneyline, both teams' spreads, totals, both team totals. Pays >= $1 in every outcome. | pass |
| P2 | Overtime: moneyline vs spread on one scoreboard | read Kalshi `rules_primary` / `rules_secondary`, `data/raw/live_2026-10-25/kalshi_rules/{KXNBAGAME,KXNBASPREAD,KXNBATOTAL,KXNBATEAMTOTAL}/20261025T190000Z.json.gz` | 4 series | All four say "resolve based on the official final result"; spreads say "wins by more than N points", totals "over N total points". One scoreboard, overtime included, so mixing KXNBAGAME and KXNBASPREAD in one margin ladder is sound. | pass |
| P3 | Overtime: Polymarket US | same question for `basketball_team_full_game_winner` vs `..._spread` | 0 rule texts read | **Not verified.** See B1. | **fail (blocker)** |
| P4 | Opposite-team spread mapping | `test_opposite_team_spreads_are_one_ladder`; code `venues.py` `margin_side` (reviewed in the fast-arb round) | 2 | "DET by over 3.5" maps to NO of margin > -3.5; "PHI by over 3.5" maps to YES of margin > 3.5. Correct. | pass |
| F1 | Fee per order, both legs | `ladder.py:218-225` `pair_margin`, `ladder.py:74-85` `leg_fee_fn` | n/a | Each leg's fee is rounded once per order at n contracts: Kalshi through the verified `parlay.yaml` object with the series' own multiplier; Polymarket US banker's cent. | pass |
| F2 | NO ask = 1 - yes_bid | live: `ladder.py:96-97` uses Kalshi's `no_ask_dollars` and `yes_bid_size_fp`; dry run: `ladder_dryrun.py:150` uses `1 - yes_bid` | fixture | On Kalshi a NO buy at p matches resting YES bids at 1 - p (fixture: yes_bid 0.49, no_ask 0.51), and the NO depth is the YES bid size. Correct. | pass |
| C1 | Confirm step reuses the fixed patterns | `ladder.py:368-423` | n/a | Back-to-back re-read of both legs. Refused on a failed read, a window over 5 s, a changed ref, missing or unsized depth, or a re-evaluated verdict other than `alert`. Kalshi re-parse goes through the status-checking parsers. | pass (minor m2) |
| X1 | Props excluded and never fetched | `configs/fastarb.yaml` `ladder.kalshi_extra_series: [KXNBATEAMTOTAL]`, `include_player_props: false`; `grep -n -i -E "...|KXNBAPTS|PROP_SERIES|kalshi_prop_quotes"` over the new modules and runner | 4 files | Prop series are in no fetch list (runner or `ladder_source._kalshi_markets`). `find_ladder_violations` skips them by default, and `is_ladder_alertable` returns `excluded`. `kalshi_prop_quotes` is only a parser (dry run, and confirm parsing a single body). | pass |
| R1 | Read-only | the same grep for `.post(`, `.put(`, `.delete(`, `.patch(`, `portfolio`, `/orders`, `authorization`, `secret`, `private_key`, `nba.duckdb`, `duckdb.connect` | 4 files | One `duckdb.connect(..., read_only=True)` in the dry run. The CLI refuses the live snapshot DB path (`__main__.py`). No verbs other than GET. | pass |
| D1 | Dry-run honesty | `python -m nba.markets.fastarb ladder-dryrun --db <copy of kalshi.duckdb> --lineups <copy of lineups.duckdb> --from 2026-10-20 --to 2026-10-25` | 409 snapshot batches, 1,202,404 price rows | Reproduced the builder's claim exactly. net >= 2c: 0 in every family and phase. net > 0: 2 snapshot-pairs, both `after_tip/margin` (LAL@UTA 10-25 21:44Z +0.8c, 22:15Z +1.0c), each lasting 1 snapshot. Pre-tip best net -1.0c (margin), -2.5c (team total). Header says "APPARENT, UNCONFIRMED ... not profit". 64 markets unmapped, reported. | pass |

The stored thresholds were checked on the copy: spreads, totals and team totals are stored as half-points (for example
`KXNBASPREAD-...-SAC8` 7.5, `KXNBATEAMTOTAL-...-DET99` 98.5), so the dry run maps them. Props are stored as "X+" (25.0),
and `ladder_dryrun.py:124-125` subtracts 0.5, which is correct.

The six standard checks: no model and no outcome scored, so (1) shuffled labels, (2) planted future, (3) missingness and
(6) integer support do not apply. Integer support appears only in the sense that half-point lines cannot be hit by
integer scores (`test_half_point_lines_cannot_land_on_the_line`). (4) Knockout: n/a. (5) Replication: D1 above.

## Findings

### B1 (blocker): the Polymarket US ladder's $1 floor depends on rules nobody has read, and the texts claim it anyway

- `runner.py` `_ladder_tick` (pools `[(K, book), (PM_US, ...)]`) and `ladder_source.py:194-218` both emit Polymarket US
  ladder pairs.
- The PM US margin ladder mixes the winner market and the spread markets. Whether both settle on the same scoreboard
  (overtime included) is exactly what `settlement_rules_verified: false` says is unknown.
- **Counterexample.** If a spread settled on regulation while the winner settled on the final score, the pair
  YES margin > -4.5 (final) + NO margin > -1.5 (regulation) pays **$0** when the away team wins by 6 in overtime.
  Regulation margin is 0 > -1.5, so the NO leg loses; final margin -6 is not > -4.5, so the YES leg loses.
- Kalshi is safe because all four series say "official final result" (P2). Polymarket US is not shown to be.
- **Fix:**
  1. In `runner._ladder_tick` and `ladder_source.confirmed_ladder_arbs`, do not emit a `PM_US` ladder unless
     `fa.settlement_rules_verified` is true. Count it as `ladders_rules_unverified` and log it to `candidates.jsonl`
     with that verdict.
  2. Add a test that a planted, confirmable PM US ladder yields no ALERTS.md line and no `ConfirmedLadderArb` while the
     flag is false.

### B1b (same fix, text): the guarantee wording contradicts the tag

- `sinks.py` `ladder_text` prints "(pays >= $1 either way)" on the same line as the void-risk or RESOLUTION RISK tag.
- `ladder_source.py:62` says "the pair pays at least $1.00 whatever happens".
- Under Kalshi's own rule ("cancelled or not started within 48 hours ... all markets will resolve to a fair price") the
  two legs settle at f1 + (1 - f2), which is below $1 whenever f2 > f1. Nothing in the contract makes the fair prices
  monotone. That is the same reason the builder correctly gives for excluding props; here it is rare, not absent.
- **Fix:** change the wording to "pays >= $1 if the game is played" in both places.
- Separately (minor), `ladder_text` prints the Kalshi void text on a Polymarket line once Polymarket rules are verified.
  Give PM US its own text.

### Minor

- m1. `ladder_source.py:173` and `jarvis_source.py`: `getattr(cfg, "confirmed_max_gap_s", None)` fails open if the
  attribute is absent. Read it directly.
- m2. The PM US confirm (`ladder.py:404-409`) re-parses with the cached event's status and fee, and does not check the
  book's `marketData.state == "MARKET_STATE_OPEN"`, which `jarvis_source._poly_quotes` now does. Add the same book-state
  check.
- m3. `price_band` is not applied to ladders. That is mathematically fine (the floor does not depend on price), but
  tail rungs are where a one-cent tick and fee rounding dominate. State the choice in the config comment.
- m4. `ladder_source._default_fetch` sets `begin_tick(400, 120.0)` once per call. On a large slate later games
  silently get nothing (fails safe).
- m5. Opportunity: the dry run's only net-positive pairs were in play, and the poller stops at tip. Pre-tip, Kalshi's
  ladders were coherent to within fees on 10-20..25. The expected live alert rate pre-tip is about zero, and that is a
  finding, not a defect.

## Playbook check

Model class: **none** (a market-structure tool with no fitted model).

1. **Information or capacity?** Neither; this is an arithmetic and coherence check on one venue's prices.
2. **Data enough?** The dry run covers 6 days, 409 batches and 1.2M price rows. That is enough to say pre-tip ladders
   on Kalshi were coherent within fees in that window. It cannot say anything about news spikes inside a 15-minute
   batch.
3. **Test valid?** The payoff proof uses an independent English-contract evaluator over 870 final scores, which is a
   real test of the mapping. The dry run has a positive control (planted violations alert in
   `test_tick_alerts_a_planted_team_total_ladder_once`). Its non-simultaneity and missing depth are stated.
4. **Lineage.** It reuses fast-arb's parsers, fee objects, confirm pattern, depth gate and consumer-gap guard. Missing:
   the settlement-rules gate for the second venue (B1) and the book-state check (m2).
5. **Scored against?** Nothing; candidates are logged to `candidates.jsonl` for a later false-alert count.

## Corrected family and power note

No inferential family was run, so BH does not apply. The dry-run counts are descriptive, with no CI because nothing is
sampled. The smallest violation the snapshot dry run can see is one that lasts across a 15-minute batch. Shorter ones are
invisible, so "0 pre-tip" means "none at 15-minute resolution", not "none".

## Biggest risk

A Polymarket US "LADDER ... locks +Xc/pair (pays >= $1 either way)" alert built on a winner/spread pair whose contracts
settle on different scoreboards, followed by Devin.

## Not verified

- Polymarket US winner, spread and total settlement text (overtime, postponement).
- Kalshi fee multipliers per series in `configs/parlay.yaml` beyond what the tests pin.
- Live behaviour of the confirm step against the real gateways (no network).

## Drafted DECISIONS row (not appended)

`| 2026-10-25 <time> | Adversary review of agent/market-ladder-arb 70b0147: CHANGES_REQUIRED. Kalshi ladder sound (870-score English-contract proof; all four series settle on the official final result; fees per order per leg; NO ask = 1 - yes_bid; confirm, depth, props excluded and never fetched; read-only; dry run reproduced exactly: 0 pairs >= 2c, 2 in-play net-positive at +0.8/+1.0c). Blocker: Polymarket US ladders emitted while settlement_rules_verified is false (a winner/spread pair can pay $0 if the scoreboards differ), and "pays >= $1 either way/whatever happens" contradicts the void tag. docs/reviews/ladder_arb_review_2026-10-25.md. | Adversary finding. | B1 fixed with the named test and re-checked. |`

## Re-check of fb70aca (diff-only: `git diff 5ef709f fb70aca`)

Verdict: BLOCKED

(In the caller's terms: **CHANGES_REQUIRED, one line.** B1 is fixed, B1b is fixed in every runtime text, and the minors
are addressed. One B1b location named in the first round still promises the unconditional guarantee:
`nba/markets/fastarb/ladder_source.py:62`, the docstring of the `ConfirmedLadderArb` record that JARVIS will consume,
reads "the pair pays at least $1.00 whatever happens".)

### Verification (unpiped)

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check nba/markets/fastarb tests/markets/fastarb` | exit 0 |
| Format | `ruff format --check nba/markets/fastarb tests/markets/fastarb` | 22 files, exit 0 |
| Types | `mypy nba/markets/fastarb` | 17 files, exit 0 |
| Tests | `python -m pytest -q -p no:cacheprovider --no-cov tests/markets/fastarb tests/test_layering.py` | 109 passed, exit 0 (106 before) |
| Wording | `git grep -n -i "whatever happens\|either way" fb70aca -- nba/markets/fastarb` | `ladder_source.py:62` (docstring, still wrong); `ladder.py:376` ("evidence either way", unrelated) |

### Point by point

- **B1: fixed.** `LadderScanner.confirm` (`ladder.py:378-382`) returns `PM_RULES_UNVERIFIED` before the back-to-back
  re-fetch whenever the venue is Polymarket US and `settlement_rules_verified` is false. That path covers both the runner
  and `confirmed_ladder_arbs`. The runner counts it (`ladder:pm_us_rules_unverified`) and logs it to `candidates.jsonl`.
  Tests: `test_polymarket_ladder_confirms_only_when_rules_are_verified` (both flag states) and
  `test_runner_and_jarvis_emit_no_polymarket_ladder_while_unverified`.
  - Precision note: "refused before any fetch" holds only for the confirm re-fetch. The candidate order books for the
    Polymarket pool are still fetched every tick (`runner._ladder_tick` via `pm_candidate_slugs`, and
    `ladder_source.py:197-200`). That fails safe, but it wastes up to `ladder.max_books_per_game` (8) requests per game
    per tick. Minor: skip the Polymarket pool entirely while the flag is false.
- **B1b: fixed in runtime text.** `sinks.ladder_text` now says "(pays >= $1 if the game is played)" and gives Polymarket
  its own `PM_US_RISK`. The `ConfirmedLadderArb.note` says the same for both venues.
  - **Still open:** `ladder_source.py:62` docstring. **Fix:** "Buy `yes_leg` and `no_leg` together: the pair pays at
    least $1.00 if the game is played (see `note` for void rules)." That line alone needs no further review.
- **Minors.**
  - **Fixed in the ladder source:** a missing JARVIS gap attribute now returns `[]` (`ladder_source.py:174`, test
    `test_jarvis_missing_gap_attribute_fails_closed`).
  - **Fixed:** the Polymarket book state must be `MARKET_STATE_OPEN` in confirm (`ladder.py:410-412`).
  - **Fixed:** config comments for the price band and the deadline.
  - **Still open, not blocking:** the same fail-open `getattr` remains in `nba/markets/fastarb/jarvis_source.py`. Apply
    the same one-character change there.
  - **Not blocking:** the config comment says "a ladder pays >= $1 whatever the price". It is about price, not
    settlement, but "if the game is played" would keep the wording consistent.

Drafted DECISIONS row (not appended):
`| 2026-10-25 <time> | Adversary re-check of agent/market-ladder-arb fb70aca: B1 (no Polymarket US ladder while settlement_rules_verified is false; counted, logged, tested both states), B1b runtime wording, open-book check and fail-closed JARVIS gap fixed; 109 tests pass. One line left: ladder_source.py:62 docstring still says "pays at least $1.00 whatever happens". docs/reviews/ladder_arb_review_2026-10-25.md. | Adversary finding. | The docstring corrected. |`
