# Lane E2b (markets-side cleanups), branch agent/market-1117-live-markets, 2026-11-16 night

Behavior-preserving only. No network request, no production DuckDB, no config VALUE changed, no order/credential code.
**Order / signing / credential code found: none.** The only POSTs in my directories are Discord webhook notifications
(`nba/jarvis/sinks.py:69`, `nba/jarvis/cards/run.py:254,264`, `nba/jarvis/cards/players.py:776`); data-vendor keys are
read from env only (`nba/odds/the_odds_api.py:381`, `nba/odds/theoddsapi.py:163`, `nba/markets/fastarb/__main__.py:38`).

## Commits (base 5eb2da75)
| task | commit | what |
|---|---|---|
| 2 | fcd85fa6 | `nba/markets/fees.py` (pure, stdlib only); three old bodies become thin wrappers; differential test |
| 3 | 08c826da | `tests/markets/test_fee_config_agree.py` guard (no config edited) |
| 6 | 6e237372 | comment/docstring drift (jarvis `__init__`, `cli`, `configs/jarvis.yaml`, `configs/cost.yaml`) |
| 8 | 7c708bcc | `nba/kalshi/poll15.py` parsing section -> `poll15_parse.py` (verbatim, re-exported) |
| 8 | 811818df | `nba/jarvis/book.py` summary/kill-switch section -> `book_summary.py` (verbatim, re-exported) |

## Task 1: fee map (pre-change line numbers where they moved)
| function | file:line | venue | formula as code | rounding | rate from | callers |
|---|---|---|---|---|---|---|
| `order_fee` (A) | nba/parlay/ev.py:22 | Kalshi | `M*rate*C*P*(1.0-P)` floats, left to right; `flat`: `M*rate*C` | `ceil(round(raw*100,9))/100` once per order; raw<=0 -> 0.0 | `configs/parlay.yaml` `fee_model` (coefficient 0.07, maker 0.0175, multipliers) | ev.fee_per_contract, budget.order_cost, fastarb arb/ladder (Kalshi legs), jarvis edge (Kalshi + non-bankers), halftime_scorer:200 |
| `FeeSchedule.order_fee` (B) | nba/markets/fastarb/config.py:32 | Polymarket US (+INTL, unverified) | `Decimal(str(coef))*C*Decimal(str(P))*(1-Decimal(str(P)))` | quantize 0.01 HALF_EVEN once per order | `configs/fastarb.yaml` `polymarket_us.fee` / `polymarket_intl.fee` | fastarb arb.fee_functions:23, ladder.leg_fee_fn:75 |
| `_order_fee` bankers branch (C) | nba/jarvis/edge.py:45 | Polymarket US | `Decimal(M)*Decimal(rate)*C`, then `*= Decimal(P)*(1-Decimal(P))` | quantize 0.01 HALF_EVEN | `configs/jarvis.yaml` `fees.polymarket` | edge.fee_per_contract_raw (n=1), edge.order_cost; slate, picks, ingame_run |
| `fee_per_contract` | nba/parlay/ev.py:48 | Kalshi | `A(n)/n` | from A | parlay.yaml | top_parlays:283, parlay `__main__`:248, papertrade:184, ev.evaluate |
| `fee_per_contract_raw` | nba/jarvis/edge.py:28 | both | A or C at n=1; **0.0 outside the open interval (0,1)** | venue's | as above | JARVIS labels, sizing |
| halftime fee | nba/markets/halftime_scorer.py:200 | Kalshi | `round(100*A(ask_c/100, n=1))` -> int cents (cents<->dollars) | A, then Python round | parlay.yaml checked against `jarvis.yaml halftime_overs.fee_pin` (check_fee:52) | halftime/checkpoint scorers |
| `budget.order_cost` | nba/parlay/budget.py:55 | Kalshi | `round(n*ask + A, 2)` | A, then round to 2 dp | parlay.yaml | parlay budget |
| `FeeSpec.per_unit` | nba/markets/decision_scoring.py:47 | sportsbook | 0.0 (vig is in the price) | none | code constant | research scorers |
| `PinnedFee.fee_micro` (D, not mine) | research/market_forward/fee.py:61 | Kalshi | exact `Fraction` micro-dollars per contract, cents domain 0-100 | unrounded, or ceil per CONTRACT (S1) | parlay.yaml, pinned 0.07 | market_forward scorer |

`nba/daily/stamps.py` holds no fee math (the `fee` hits are a hasher's `feed`).

Differences (all kept): A ceil-up vs B/C half-even (different venues, by config); A validates price in [0,1] and C>0, B/C
validate nothing (out-of-range price gives a signed fee, -0.0 possible); A has maker/taker and `flat`, B taker only and no
multiplier, C always `rate("taker")` (ignores `default_order_type`) and ignores `formula`; B and C multiply in different
orders under Decimal's 28-digit context (equal on all 159,240 tested cases at M=1, but not provably equal); JARVIS falls
back to ceil_cent if `fees.polymarket.rounding` is absent (`nba/jarvis/config.py:228`) while fast-arb refuses
(`_fee`); JARVIS's Polymarket `verified` uses `bool(...)` (a string "false" reads True, `nba/jarvis/config.py:195`)
while parlay/fast-arb require a real YAML boolean; JARVIS per-contract fee is the 1-contract order fee (conservative),
parlay's is `order_fee(n)/n`; D rounds per contract, A per order; halftime converts cents<->dollars, everything else is 0-1.

## Task 2: done (fcd85fa6)
`ceil_cent_fee` (A), `bankers_cent_fee` (B), `bankers_cent_fee_scaled` (C), each a verbatim body. Wrappers keep their names,
validation and error order. Differential test `tests/markets/test_fees_differential.py` (old bodies inlined as oracles,
`float.hex` / exception type+message equality, wrapper AND pure function each checked): A 159,240 cases, B 159,240, C 159,240,
JARVIS-Kalshi route 74,430; 0 mismatches. Grid: every 1/10000, every cent and tenth-cent in two float spellings plus
`nextafter` neighbours, 0, 0.5, 1, 2,000 seeded uniforms, contracts 1..1,000,000, 7 rates, 5 multipliers, maker/taker/bogus,
`flat`/bogus formulas, out-of-range prices. B vs C cross-check: 0/159,240 differ (informational). **Proposal (Devin's call):**
B vs C - pick one Polymarket evaluation order and one validation policy, then delete the other.

## Task 3: guard done (08c826da), consolidation = proposal
Equal: name, formula, coefficient (0.0695, same float bits), rounding, verified. Keys: `configs/jarvis.yaml:80-87`
`fees.polymarket` (read `nba/jarvis/config.py:190-200`) and `configs/fastarb.yaml:11-19` `polymarket_us.fee` (read
`nba/markets/fastarb/config.py:83-98`; coefficient also used by fastarb `runner.py:284,355,393`, `ladder.py:357,416`,
`ladder_source.py:202`, `jarvis_source.py:211`). Proposal: make jarvis read `polymarket_us.fee` from fastarb.yaml (as Kalshi
is read from parlay.yaml) and drop `fees.polymarket`, only after checking no scorer pins it.

## Task 4: proposal (clients differ; not merged)
| | nba/jarvis/polymarket.py | nba/markets/fastarb/polymarket_us.py (+venues, http) |
|---|---|---|
| HTTP | own `httpx.Client(timeout=jarvis.yaml polymarket.timeout_s=15)`, default httpx UA | injected getter (`PoliteFetcher`/`nba.live.http.Fetcher`, project UA, `TIMEOUT_S`) |
| retries | none | 3 attempts; 429 Retry-After (cap 10 s) + rate halving; 5xx backoff |
| endpoints | `/v1/events/slug/{slug}` | same + `/v1/markets/{slug}/book`, `/bbo` |
| 404 / errors | 404 counted `not_listed`; httpx error -> error name, others continue | any non-200 -> None |
| body | `body.get("event", body)` (falls back to the whole body) | `body.get("event")` only |
| parse | winner market only, JARVIS `Quote`s, `_f` range [0,1], 4 h start check, unmatched listed | proposition `QuoteBook`, all market kinds, levels |
Not a subset on any shared function. Proposal: make JARVIS consume `PolymarketUSClient.event` + `venues.us_quotes` (its own
docstring says so), with a replay on recorded events first.

## Task 5: legacy odds vendor is IN the live closure - report only, no banner
`nba.odds.theoddsapi` is imported by `nba.odds.__main__` (ops/nba_lineups.sh:114 runs `python -m nba.odds capture --kind`),
`capture`, `live_close:63`, `the_odds_api:50` (`NameResolver`, `american_to_prob`), `history_pull:60`, `player_aliases:37`,
`combo_ingest:43`, `nba/pulls/orchestrator.py:43`, `nba/markets/fastarb/pinnacle.py:21`, `fastarb/runner.py:505`; tests in
tests/odds and tests/markets/fastarb. The legacy CLIENT and `configs/odds.yaml` are reached only by `capture` without `--kind`
(removed from nba_daily.sh:235 on 2026-10-10). Proposal: move `NameResolver`, `american_to_prob` to a vendor-neutral
`nba/odds/names.py` re-exported from theoddsapi; then retire the client, `configs/odds.yaml`, the no-`--kind` CLI branch and
tests/odds/test_client.py, test_capture.py, test_parse.py.

## Task 6: done (6e237372)
Discord verified as default (`configs/jarvis.yaml` `delivery.sinks: [discord]`, `nba/jarvis/sinks.py:82-126`). Edited the
docstrings and `jarvis.yaml` header; the `ingame_checkpoints` comment now says turned on 2026-11-10 (0e9e9564);
`cost.yaml:1` says the cost-monitor agent was retired (ccaa3a9a). Proof: `git diff configs/` has only `#` lines; parsed
`yaml.safe_load` of both files equal before/after (2 of 2 objects equal).

## Task 7: proposal only (nothing moved)
| module | live closure | pinned / frozen use | proposal |
|---|---|---|---|
| nba/markets/decision_scoring.py | no | **harness-pinned** by research/archive/wowy_struct/wowy_struct_rule.py:53; imported by nba/eval/q1_starters_live.py:33 (Q1_STARTERS_LIVE forward scorer) and 6 research evals | do NOT move (pin + forward scorer) |
| nba/markets/market_slow.py | no | research/eval/market_shrink_eval.py:23, tests/markets/test_decision_scoring.py:13 | `git mv` to research/markets/; fix those 2 imports |
| nba/parlay/joint_eval.py | YES (parlay `__main__`:269,277) | research/eval/joint_game_set_eval.py:60 | keep until the CLI subcommands go |
| nba/parlay/combo_eval.py | no | tests/parlay/test_combo.py:18,402,601 (source grep) | `git mv` to research/parlay/; update test imports and path |
| nba/parlay/independence_check.py | YES (parlay `__main__`:292, nba/daily/report.py:259) | live-by-DECISION 2026-10-10 | keep |

## Task 8: two splits done, three planned
Coverage from own test dir (after 22:00): book 89%, runner 90%, poll15 89% (`--cov=nba/kalshi`; `--cov=nba.kalshi.poll15`
broke duckdb import at collection, a coverage artifact), the_odds_api 92%, live_close 92%. Split the two whose section moves
cleanly with no test patch targets: poll15 (parsing, 0 patch targets) and book (summary, 0 patch targets). Namespace diff
before/after: only the unused `re` / `random` imports gone. Plans: **the_odds_api**: the parse section needs `SOURCE`,
`parse_ts`, `EventMatchError`; move those with the parse section to `the_odds_api_parse.py`. **live_close**: tests patch
`load_live_config`, `open_store`, `read_slate`; keep those and their callers, move pure parsers only. **runner**: tests patch
`_gate`, `_maybe_recap`, `_tick`, `build_state`, `load_pulls_config`, `todays_rows`; all stay.

## Tests (unpiped, exit codes seen)
tests/jarvis 496 passed 1 skipped (0); tests/kalshi + layering 154 passed (0); tests/markets 289 passed (0); tests/parlay 245
passed 2 skipped (0); tests/odds 170 passed (0); tests/test_layering.py 10 passed (0); tests/research/market_forward 79 passed
(0); closure-pin research tests 3 passed (0). ruff 0, ruff format 0, mypy on nba/{markets,parlay,jarvis,kalshi,odds} 0.

## Decisions (what / why / what would reverse it)
- Kept B and C separate. Why: different evaluation order, not provably equal. Reverse: Devin picks one.
- No banner on theoddsapi.py. Why: the brief only wanted a banner if the module was unused, and it is live. Reverse: the names.py extraction.
- Split poll15 and book, not the 92% odds files. Why: odds parse sections need constants and exceptions moved too, so not a minimal pure move. Reverse: the plans above.
- Extended `tests/kalshi/test_poll15.py`'s no-order grep to `poll15_parse.py`.

## Not verified
Live behaviour (no network by rule). Decimal context is the process default (28 digits) in tests; a caller that changes the
context changes old and new code alike. `tests/kalshi/test_poll15.py` mypy has 2 errors that predate this lane.
