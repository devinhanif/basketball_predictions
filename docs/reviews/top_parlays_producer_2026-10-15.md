# Adversary review: top-3 parlay producer (origin/agent/market-top-parlays 35447e9), 2026-10-15

Scope: `nba/parlay/top_parlays.py`, `top_source.py`, `line_cal.py`, `line_cal_build.py`, `line_cal_map_v1.json`,
`top_history.py`, `__main__.py` (diff from 9c43882), `app/fixtures/top_parlays_example.json`, against
`docs/contracts/top_parlays_v2.md` and the app validator `app/api/src/parlayhitters_api/top_parlays.py` (both on main ff040aa).
Nothing here is a model claim with a result note; this is a code and contract review of a display producer. No season-2025
row was loaded. Every DuckDB file was opened `read_only=True`. Attack scripts are in the session scratchpad (`tpp/*.py`).

Verdict: BLOCKED

Top finding: the committed fixture is refused by the app (HTTP 422, "a price is stamped after the file was made"). 244 of
1,034 fixture legs (23.6%) are priced after the file's `made_at`, and the pure module `build_report` has no guard against
this. In the live CLI the caller filters quotes to `<= now`, so the live output is not affected. The fixture and the
module's contract are.

## Model class and playbook check

Model class: a display producer over two existing models. A quantile/distribution layer (the LINE_CALIBRATION logistic map
at the line) feeds a joint layer (a Gaussian copula for same-game legs, independence across games). No new model is fitted
or claimed.

1. **Information or capacity?** No new information. The producer re-uses production P(over), the frozen CAL map and stored
   pair correlations. Its value depends on which quotes it uses, and that is where the live risks are (B1, M2).
2. **Enough data for this class?** CAL: 13k-30k fit rows per (book, stat), effective n 12k-27k. That is ample for a
   5-parameter map. Copula: 55k-584k pairs per relation, but the copula's whole gain over independence is about 0.0011 log
   loss (playbook), and the joint is untested (PARLAY_JOINT_CAL is not yet run). The output says "joint not yet tested". Correct.
3. **Test validity.** The map is a byte-exact rebuild of the frozen fit (check 2). The 80% interval is the Kalshi engine's
   logit noise on a pseudo n of 1000, not CAL's measured residual (m7), so `ev_low80` is not a tested quantity.
4. **Lineage and inherited guards.** The guards come from LINE_CALIBRATION (2025 refused, fit strictly before the block,
   tamper-evident sha) and carry over intact. The export's quote rule (`reader.prop_lines`: newest quote per line) also
   carries over. That rule is right for display and wrong for selection (M2). The paper-trade gate comes from the Kalshi
   engine and measures a different product (M4).
5. **What is it scored against?** Nothing yet. EV is computed against a product of leg prices: a real price for cross-game
   parlays, not a real price for same-game ones (M3). The fee is applied as if the ticket were a Kalshi contract (B2).

## Per-check table

| # | Check | Command (scratchpad `tpp/`) | n | Result | Pass |
|---|---|---|---|---|---|
| 1a | Price after tip or after forecast, live loader | read `top_source.py:229,281,396,423-424` | n/a | quotes need `received_at < tipoff`, `< commence_time`, `<= prod made_at` and `<= now`; forecasts need `made_at < tip` and `<= now` | pass |
| 1b | Planted leg priced after `made_at` but before tip, through `build_report` | `planted.py` | 3 legs | the leg is used in a ticket; `refused` counts 0 | **FAIL** (B1) |
| 1c | Fixture through the app's pydantic model | `cd app/api && uv run python validate_fixture.py .../top_parlays_example.json` | 1 file | `REFUSED 1: a price is stamped after the file was made`, exit 1 | **FAIL** (B1) |
| 1d | Fixture legs priced after `made_at` 2025-01-27T23:59Z | `attack_fixture.py` | 1,034 legs | 244 (23.6%), in 6 tip groups 00:00-02:30Z | **FAIL** |
| 1e | CAL fit seasons and cutoff | `line_cal.py:34,128-131,248-249`; `line_cal_build.py:56-57,68-69,133-134` | n/a | only 2023 and 2024; season 2025 refused by name, by date and on load | pass |
| 1f | Fixture walk-forward CAL: planted future (flip every label on or after 2025-01-27) | `planted.py` | 47,719 flipped rows | all 8 cells' betas identical | pass |
| 2 | CAL map v1 reproduces the frozen fit | `python -m nba.parlay.line_cal_build --pairs-dir data/ops/model_clv_pairs --model-dir reports/odds_history --out tpp/map_rebuilt.json; cmp` | 8 cells | byte-identical, map sha 443743a1, exit 0 | pass |
| 2b | Branch `fit_cells` against all Stage-1 blocks in `reports/prereg_line_calibration/select.json` (rule a5441242) | `repro_select.py` | 72 blocks (36 Pinnacle, 36 DK) | max abs beta diff 0.000e+00; n_fit mismatches 0 | pass |
| 3 | Fee on sportsbook tickets | `attack_fixture.py` | 780 two-leg, 9,880 three-leg tickets | the fee lowers EV by a median 5.2 points (size 2) and 8.2 points (size 3). Without it, positive point EV: 469 / 6,244 tickets (with it: 325 / 3,928) | **FAIL** (B2) |
| 4 | Diversity | `attack_fixture.py` | 6 tickets | Zion Williamson fg3m under 0.5 is in 6 of 6 tickets; Risacher fg3m over 0.5 in 4 of 6 | **FAIL** (M5) |
| 5 | Verdict and gate against the app | `top_parlays.py:272-288,471-473` vs app `Gate._check`, `TopParlays._check` | n/a | the logic matches. Thresholds come from config (m5); skill comes from the Kalshi log (M4) | partial |
| 6 | Numguard and banned words | `numguard` run over the fixture's free text | 7 texts | leg reasons and `why`: 0 removals, and a planted "31.7" is caught. Caveat 0 carries 11 unscreened numbers (all correct against `reports/alt_parlay_x/results.json`). No producer-side banned-phrase screen | partial (m4) |
| 7a | Copula under-leg sign | inline: same player, pts over 0.7 with fg3m over or under 0.7 | 20,000 sims | over/over joint 0.5664 vs 0.4900 independent; over/under 0.1358 vs 0.2100 | pass |
| 7b | Copula seeded | `top_parlays.py:196-197` | n/a | seed is cfg.seed plus crc32(book\|game) | pass |
| 7c | Pair correlations reproducible | `shasum data/parlay/joint_model.json` | 1 file | git-ignored, sha a2b4c4a9, fit_seasons 2022-2024. Its sha is not recorded in the fixture or the live output | **FAIL** (m1) |
| 7d | Mixed ticket (two legs in one game plus one other) against the app's 0.05 product check | inline normal CDF | n/a | same-player pts/fg3m at 0.7 each with a 0.9 third leg: off by 0.066 while labelled `same_game: false`, so the app refuses the whole file | **FAIL** (M3) |
| 8 | P(play) floor 0.90; p_cal from production only | `top_source.py:46,372-382,429-431,449-452` | n/a | floor applied to production `p_play`. `p_ge_full` is conditional on playing (`nba/props/forward.py:712`), the same as the CAL fit rows. p_cal is always from production and AVG goes to `p_avg_raw` only (DECISIONS 2026-10-15 00:52 item 3) | pass |
| 9a | Determinism | run `python -m nba.parlay.top_history ...` twice, then `cmp` | 2 runs | identical to each other and to the committed fixture | pass |
| 9b | Runtime | `/usr/bin/time` | 1 slate, 10,660 tickets | 2.9-3.4 s wall | pass |
| L | Layering, live path, lint | `pytest tests/test_layering.py tests/parlay`; `ruff check`; `ruff format --check`; `mypy` | 173 tests | 173 passed, exit 0; ruff 0; format 0; mypy 0. But `evaluate` now imports about 60 more modules (M1) | partial |
| S | Stale main lines picked over current ones | `pairs_2024.parquet`, DraftKings, T-60 vs T-5 | 53,075 keys | 6,611 (12.5%) changed main line between snapshots | **FAIL** (M2) |

Power and multiplicity: n/a. No claim of skill or EV is made, the verdict is `keep_your_money` and the gate is shut. The
diagnostic `tickets_with_positive_point_ev` (4,253 of 10,660) is a selection artifact, not a finding (m8).

## Findings, severity-ranked, each with line and fix

### Blockers

**B1. Prices stamped after `made_at` reach the file, and the app refuses it.**
- `top_history.py:142`: `made_at = min(tipoff) - 1 min`. Each leg keeps its own T-60 stamp, so later games' quotes postdate
  the file.
- `top_parlays.py:119-133`: `refuse_post_tip` checks `price_at >= tipoff` and `now >= tipoff`, but never `price_at > made_at`.
- **Fix:**
  1. Give `refuse_post_tip(legs, now, made_at)` a third refusal, `price_at > made_at`, counted as
     `priced_after_made_at` and passed from `build_report`.
  2. In `top_history.build_fixture`, pass `now=made_at`.
  3. Regenerate the fixture. The effect is measured: 790 of 1,034 legs remain, the size-2 top 3 is unchanged, and size-3
     #3 becomes Zion u0.5 fg3m + Risacher o0.5 fg3m + Jalen Smith u1.5 fg3m (p_cal 0.3700).
- **Tests:**
  1. A negative test in `tests/parlay/test_top_parlays.py`: a leg priced after `made_at` but before tip is refused and counted.
  2. An integration test in `app/api/tests/test_top_parlays_producer.py`. It validates `app/fixtures/top_parlays_example.json`
     and a committed small producer-run sample (written by `build_report` in `tests/parlay` to
     `app/fixtures/top_parlays_producer_sample.json`) with `TopParlays.model_validate`. It must run in the app's uv
     environment, because the nba venv does not carry `parlayhitters_api`.
- **Design call (asked by the coordinator):** keep one file per slate (contract v2, the app reads one). Regenerate it
  before each tip group, with `made_at` equal to the run clock, legs only from games not yet tipped and quotes stamped
  `<= made_at`. That is what `cmd_top` already does (`__main__.py:497,530`; `top_source.py:423-424`). Wiring will need one
  run per tip group, not one per night.
- Per-tip-group files would need a contract v3 and an app-side merge. They buy nothing over an overwrite: a started game
  cannot be bet, so dropping it from the file is correct.
- One file made before the first tip would serve the 10:30 pm games 3+ hours of stale quotes and forecasts made before
  their injury news. Not recommended.

**B2. The Kalshi fee schedule is charged on sportsbook parlays. This double-counts the margin.**
- `top_parlays.py:235-236` charges `fee_per_contract(implied, cfg.fee)` (Kalshi: ceil(0.07·P·(1-P)) per contract) and
  computes EV as `p / (implied + fee) - 1`.
- A sportsbook ticket has no separate fee: $1 staked pays the product of the decimal odds. The book's margin is already
  inside each leg's price and compounds in the product. The right EV per dollar is `p · Π decimal - 1 = p / implied - 1`.
  Charging the Kalshi fee on top double-counts.
- The coordinator's view is correct: sportsbook tickets carry fee 0, and a Kalshi ticket (a Kalshi combo contract) carries
  Kalshi's fee once, on the combo's own price.
- Effect on the fixture: point EV is understated by a median 5.2 points (two-leg) and 8.2 points (three-leg). The #1
  two-leg ticket shows -5.6% when the true figure is -2.3%.
- The error is in the conservative direction and the verdict cannot open today, but the panel would show a wrong number.
  Devin asked for "EV after fees" (DECISIONS 2026-10-15 00:24 item 2). For a sportsbook, the honest reading is "the
  book's margin is in the price; there is no further fee".
- **Fix:**
  1. Add a venue map in config, `fee_by_venue: {draftkings: none, pinnacle: none, kalshi: kalshi_fee_schedule}`, and
     compute fee 0 for sportsbooks.
  2. `ticket_dict` (`top_parlays.py:405-423`) keeps the `fee` field (0.0) and records the fee model per ticket.
  3. Reword `ticket_why` (`top_parlays.py:377-381`) from "after the fee schedule" to "at the book's price (its margin is
     included)".
  4. Fix the module docstring (`:19-21`) and `docs/PARLAY_ENGINE.md:157-158`.
  5. Rewrite `test_fee_comes_from_the_config_object_not_a_constant` (`tests/parlay/test_top_parlays.py:223`) to pin fee 0
     on a sportsbook ticket and the Kalshi fee on a Kalshi one.

### Major (must fix before the producer is wired; not blocking the fixture)

**M1. The live `evaluate` path now imports the parlay assistant, httpx, ssl, nba.parse and nba.kalshi.**
- `__main__.py:561` puts `from nba.parlay.top_parlays import K_DEFAULT` at the top of `main()`, so it runs for every
  subcommand, including the launchd `evaluate`.
- Measured: importing it after `nba.parlay.__main__` adds about 60 modules, including `nba.parlay.assistant.agent`,
  `.backend`, `httpx`, `ssl`, `nba.parse.*` and `nba.kalshi.*`.
- Any import-time break in those now breaks the live pretip job. No live output changes otherwise: the `evaluate` code is
  untouched and the layering test passes.
- **Fix:** `__main__.py:606` becomes `default=None`. Drop line 561. In `cmd_top`, use `k = a.k if a.k is not None else
  K_DEFAULT` after its lazy import.
- Separately, `numguard` should not drag `nba.parlay.assistant/__init__` (agent, backend) in. Import it from a module that
  does not import the agent, or make `assistant/__init__.py` lazy. That is a follow-up for the owner.

**M2. Superseded main lines are kept, and selection picks the stalest favourable one.**
- `top_source.py:419-426` keeps the newest quote per (book, market, line, side). A line DraftKings moved off (24.5 to
  26.5) stays in `newest` with its old price.
- `dedupe` (`top_parlays.py:136-145`) then keeps the line with the higher p_cal, which is systematically the withdrawn
  line that moved against the model.
- In 2024, 12.5% of DK main lines (6,611 of 53,075 keys) moved between T-60 and T-5.
- This is the export reader's rule (`nba/export/reader.py:466-469`). Showing every line there is harmless; selecting from
  them is not.
- **Fix:** in `top_source.load_legs`, keep only quotes whose `received_at` equals the newest `received_at` for that
  (book, market, player) at or before the forecast. Test: two lines for one player and book, the newer one wins even when
  the older has the higher p_cal.

**M3. Same-game handling.**
- (a) `Ticket.same_game` (`top_parlays.py:175-177`) is true only when all legs share one game. A three-leg ticket with two
  same-game legs is `same_game: false`, but its joint is a copula joint. The app's 0.05 product check (app
  `top_parlays.py:108-111`) then refuses the whole file. Demonstrated: same-player pts/fg3m at 0.7 each plus a 0.9 leg is
  off by 0.066.
- **Fix (a):** `same_game = len({game_id}) < len(legs)`, with a contract note ("any two legs share a game").
- (b) For any ticket with a shared game, `book_implied` is a product the book does not offer: DraftKings prices same-game
  parlays with its own correlation adjustment. The contract admits this ("not a tradable price").
- **Fix (b):** exclude those tickets from `any_pos` (`top_parlays.py:472`) and label their EV "not a book price" until
  same-game prices are captured.

**M4. The gate measures a different product.**
- `__main__.py:468-483` opens the gate from `paper_trades.duckdb`, the Kalshi single-leg shadow log (raw model vs Kalshi
  mid; `shadow.py:159-177`).
- The panel shows calibrated DraftKings/Pinnacle parlays. Kalshi single-leg skill could open `positive_ev_found` on
  sportsbook tickets with no record of their own.
- **Fix:** until a top-parlays paper log with its own settled rows exists, `cmd_top` passes `Gate()` (shut, counters 0)
  with a note. The gate's counters should come from that log once it exists (contract residual: "producer-asserted").

**M5. Diversity (proposed rule; Devin's call).**
- The fixture's top 3 per size are near-copies: Zion Williamson fg3m under 0.5 is in all six tickets. Five of the six hit
  together; that is correlated luck shown as three choices.
- The suggested "at most 1 shared leg between any two tickets" does nothing for two-leg tickets: two distinct two-leg
  tickets can share at most one leg anyway. For three-leg tickets it still leaves Zion in all three.
- Recommended rule: within a size, each leg appears in at most one shown ticket. Greedy by rank; the rank-1 ticket is
  unchanged.

  | Size | Rank | Tickets under the recommended rule (fixture, after the B1 fix) | p_cal |
  |---|---|---|---|
  | 2 | 1 | Zion u0.5 + Risacher o0.5 | 0.567 |
  | 2 | 2 | Sabonis o0.5 + Mitchell o0.5 | 0.436 |
  | 2 | 3 | J. Smith u1.5 fg3m + A. Thompson u2.5 ast | 0.416 |
  | 3 | 1 | unchanged | 0.375 |
  | 3 | 2 | | 0.274 |
  | 3 | 3 | | 0.251 |

- Under the weaker "at most 1 shared" rule, three-leg ranks 2-3 become 0.359 and 0.339 and Zion stays in all three.
- Cost: the shown tickets are no longer "the three most probable". Say so in `ranking`.
- Implement it in `search` (`top_parlays.py:239-255`). Keep the full sorted list per size, then select greedily.

### Minor

- **m1.** Record the copula input. Neither the fixture (`top_history.py:168-173`) nor the live output (`cmd_top`
  calibration/diagnostics) records `joint_model.json`'s sha256 (now a2b4c4a9...) or its `fit_seasons`.
  - Add `diagnostics.joint_model = {sha256, fit_seasons, n_games_corr}`. The fixture rebuild test should also assert the sha.
  - The fixture's correlations were fit on 2022-2024, which includes the fixture's own season. That is in-sample, and
    negligible here (teammate fg3m/fg3m rho 0.0017; no same-player pair in the shown tickets). Say so in the fixture notes.
- **m2.** `top_history.py:137` fits CAL on rows before the slate day (2025-01-27), not before the frozen rule's
  calendar-month block (2025-01-01), yet labels it `sha8 a5441242`. The fit is leak-free (check 1f) but is not the rule's
  object. Use `block_start = first of the month`, or label it "day-level walk-forward variant of a5441242".
- **m3.** `top_parlays.py:52-56`: the T490 caveat drops "alternate-line overs ... (BetRivers)" and "These were raw
  chances; the chances shown here are from calibrated legs". The contract gives the producer's wording. As written, it
  extends a BetRivers alternate-line finding to DraftKings main lines. Use the contract text verbatim.
- **m4.** Free-text screening.
  - `build_report` does not run numguard over `caveats`, `calibration.status`, `ranking` or `empty_reason`
    (`top_parlays.py:474-485`), though the contract says the producer screens them. Caveat 0 carries 11 numbers that
    numguard cannot source from the file. They are correct against `reports/alt_parlay_x/results.json`, so pass that
    file's numbers as `extra`.
  - Add a producer-side mirror of the app's banned-phrase regex, so a bad phrase fails at write time rather than as an app 422.
- **m5.** `Gate.to_dict` (`top_parlays.py:280-288`) emits `cfg.min_settled` and `cfg.min_settled_dates`. The app accepts
  only 30 and 14. Hard-code the contract values, or assert equality.
- **m6.** The fixture is real hindsight data (a real date, game ids and names) with `illustration: true`. Contract v2
  defines illustration as synthetic, with dates and ids no real store uses. It sits outside the app's served directory
  (`app/fixtures/top_parlays/`), so it is not served today. Either keep it out and say so in the contract, or add a
  `hindsight` kind in a v3. The extra `p_avg_raw`, `drivers`, `fee`, `hindsight`, `diagnostics` and `fixture` fields
  are ignored by the app; list them in the contract.
- **m7.** `ticket_dict` (`top_parlays.py:400-404`) builds the 80% range from Beta(pseudo n=1000) plus the Kalshi engine's
  logit sd. That is not CAL's measured residual. `ev_low80 > 0` needs about a 50% edge (fixture: [0.372, 0.735] around
  0.567), so it is effectively unreachable. It is conservative. Label it "configured noise, not tested".
- **m8.** `diagnostics.tickets_with_positive_point_ev` (4,253 of 10,660) is a winner's-curse count. The side and lines
  are chosen where the model, which loses to the market on log loss (select.json: Pinnacle CAL 0.6882 vs MKT 0.6865),
  disagrees with the price. Do not surface it in the app.
- **m9.** Stale references to v1: `top_parlays.py:1` ("top_parlays/1"), `top_history.py:1`, `__main__.py:3` and
  `docs/PARLAY_ENGINE.md:144` cite contract v1. Point them to v2.

## The single most likely failure mode

The first wired live run that hits M2 or M3. A withdrawn DraftKings line with a model-favourable stale price ranks #1, or
a mixed ticket built on a same-player pair gets the file refused on game night. Both are invisible in the fixture: the
fixture has one T-60 line per key and no same-player pair in its top tickets.

## What was not verified

- **Live end-to-end run.** `live_book_odds` holds 0 rows for 2026 (preseason and regular), so `top_source.load_legs` was
  only read and exercised through the branch's synthetic tests.
- **Replay oracle for the `__main__.py` live-path change.** Not run. The only behavioural delta to `evaluate` is the import
  in M1.
- **Real DraftKings same-game prices** against the product; none are captured.
- **The calibration of the joint** (PARLAY_JOINT_CAL is still to run).
- **`choose_primary` parity with `nba.export.best_model.mark_primary`.** Relied on the branch's pinning test
  (`test_choose_primary_matches_the_export`), which passed.

## Recommended changes (not applied by the adversary)

B1, B2 (blocking); M1-M4 before wiring into `ops/`; M5 is Devin's choice; m1-m9 any time.

## Drafted rows (not appended)

- DECISIONS (draft): "2026-10-15: top-parlays producer fee: sportsbook tickets carry no fee (margin in price); Kalshi
  combos carry Kalshi's fee once on the combo price. Why: the Kalshi schedule on a book parlay double-counts the margin
  (median EV understated 5.2 / 8.2 points on the 2025-01-27 fixture). Reverses: a book that charges an explicit per-ticket
  fee."
- DECISIONS (draft, Devin's call): "Top-parlays diversity: within a size, a leg appears in at most one shown ticket
  (greedy by rank). Why: the top 3 shared a leg in 6 of 6 fixture tickets. Reverses: Devin preferring the strict top-3
  by probability."
- No ledger row: no test was run and no claim was made.
