# Scorer review: KALSHI_LONGSHOT_FORWARD + UNDERS_FORWARD (research/market_forward, a65d782)

Adversary review, 2026-10-24 (started 2026-10-23 23:4x CDT, written 00:3x CDT, times from `date`). Branch
`agent/market-longshot-unders-scorer` at `a65d782`, reviewed in a separate worktree
(`.claude/worktrees/adversary-lus-review`). The builder's worktree had been switched to `agent/market-t510-scorer`
by another agent, so nothing was read from it. Frozen texts checked on `main` and on the branch:
LONGSHOT `0b50d17e…` and UNDERS `149be979…`. Both hash identically on both.

Verdict: BLOCKED

In the caller's terms this is **CHANGES_REQUIRED**. Four code fixes (C1-C4) and three pre-look DECISIONS interpretation
rows are needed before the scorer is merged as the code of record. No fix touches the frozen texts. The decision
statistics themselves **SURVIVE**. With real n (300 games / 50 dates; 840 / 140), the decision CI's false-PASS and
false-NO_POSITIVE_EV_FOUND rates are at nominal within Monte Carlo error. The "4% undercoverage" does not reproduce (0.6%
at 1,500 replicates).

**Top finding (C1).** The scorer takes settlements from whatever DB copy it is given. LONGSHOT §6 and UNDERS §6 say "no
re-read with later settlements". `cmd_read` (`__main__.py:132-147`) only refuses before the read date. It has no upper
bound, and `data.assemble` (`data.py:305`) trusts `kalshi_markets.result` without looking at `settled_ts`. So a read run
late, or re-run after a SELFTEST_FAILED fix, can score games that were `unsettled_game` at the read. Settlement order
is outcome-dependent (YES settles early, NO after the final): that is the M2 mechanism my pre-freeze review called the
most likely way to fake a PASS. Here it enters through the read date rather than the cutoff.

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model class: none (market-calibration scorer of a fixed hold-to-settlement NO trade; no fit, no promotion).
1. **Information or capacity?** Neither. The only input is the T-60 top-of-book. The scorer must not add information:
   checked for entry time (ASOF `ts <= tip-60`), settlement time (C1 finding) and strata inputs (`made_at`/`received_at`
   cut at tip-60).
2. **Enough data for this class?** No training. The data gates are the frozen n >= 300 games / 50 dates. The self-test's
   behaviour at those n is measured below, because at the preflight's 4 dates it is not meaningful.
3. **Test validity.** The scorer's own validity tests are guard 8.6 (identity, calibrated null, planted edge). I attacked
   them for false-PASS (none found) and for false-void (measured; non-trivial for LONGSHOT, see check 6). An invalid
   test is not a null. A SELFTEST_FAILED look is a non-result.
4. **Lineage and inherited guards.** Inherits M2 (whole-game settlement exclusion), M3 (pinned fee), M8 (schedule-matched
   window), M10 (calibrated null and planted edge) and M11 (SPREAD descriptive) from the pre-freeze review. All are
   implemented. M8's schedule file is not pinned (C2).
5. **Scored against.** Realised net dollars per contract (`r_no`), integer micro-dollars, equal weight per contract. That
   matches both §6 texts.

Class checks: the integer-support check is native here (integer-cent filters, and returns in exact micro-dollars). The
bootstrap is percentile, clustered by game and by date, with the wider deciding, as the text says. The text pins
`default_rng(2026)`, so `nba/truth/bootstrap.py` (which does not exist on this branch; `nba/truth/` holds `metrics.py`
and `walkforward.py` only) cannot be the CI code. Flagged, no action.

## Checks requested by the caller

| # | Check | Command / evidence | n | Result | Pass |
|---|---|---|---|---|---|
| a | Map from frozen text to code | Read every module line by line against both frozen sections (builder's map reproduced and spot-checked) | 13 modules, 20 files | The map is complete except the strata line convention (C4) and the settlement as-of (C1) | partial |
| b | Entry never after tip-60 | `data.py:187-190` ASOF `m.cut >= p.ts`; `kalshi_prices.ts` is `TIMESTAMP` (naive UTC, `information_schema` on a read-only copy); probe is naive UTC. Test `test_entry_is_latest_row_at_or_before_t_minus_60_never_later` | n/a | Pass on the stored `ts`. Caveat: `ts` is the snapshot run's start stamp (`snapshot.py:124`). Pages land later: raw-page mtime minus stamp, median 15 s, p90 26 s, max 17.7 min over 280 runs 10-20..24 (`lag.py`). The price is still the price we would have paid at its true observation time, so this is not outcome leakage. Recorded as m4 | pass |
| c | Fee fails closed | `fee.py:83-97` refuses on unverified, a non-`p_one_minus_p` formula, a coefficient other than exactly 7/100, a default multiplier other than 1, or any universe series multiplier other than 1. Tests: `test_refuses_unverified_fee`, `test_refuses_changed_coefficient`, `test_refuses_changed_formula_default_multiplier_and_series_multiplier`, `test_maker_coefficient_is_not_what_is_pinned` | n/a | Pass. One edge: `nba/parlay/config.py:103` coerces `verified` with `bool()`, so a YAML string `"false"` reads as True (m1) | pass |
| d | Settlement game-drop and 2-day lag | `data.py:304-346` (any eligible contract unsettled drops the whole game; SPREAD included for LONGSHOT); `__main__.py:137` cutoff = read date - 2 days (2027-01-13, 2027-04-12). Tests `test_game_with_any_eligible_contract_unsettled_is_dropped_whole`, `test_longshot_unsettled_spread_contract_drops_the_game` | n/a | Drop and lag pass. The as-of of "settled at the read" is not enforced (C1) | fail (C1) |
| e | CIs clustered by game and date, wider decides | `stats.py:77-83`, `score.py:163-197`; game draws then date draws from `default_rng(2026)` created in `engine.score_look:148`; the self-tests use separate generators `[2026,1]`/`[2026,2]`, so they do not consume the decision stream | n/a | Pass. "Wider" is total width (`hi-lo`), the literal reading of "the WIDER of the two" (note m6) | pass |
| f | Alpha 0.005 / 0.02 | `spec.py:62`; `percentile_interval` uses `[alpha, 1-alpha]`, so 99% / 96% two-sided | n/a | Pass | pass |
| g | Paper Kelly cannot affect the verdict | `engine.py:191-195` decides before any Kelly, band or sensitivity code runs; `paper_kelly` (`score.py:356`) writes only `res.kelly`; it consumes the rng only after `decide`; no import of `nba/parlay/bayes_kelly.py`; LONGSHOT has no Kelly | n/a | Pass | pass |
| h | No return on an in-window game before a read date | `cmd_read` refuses before `LOOK_READ_DATE` (ET clock); `cmd_preflight` caps the cutoff at 2026-10-24 and refuses if any scored date is >= 2026-10-25; `dryrun` and tests are synthetic (fixture tip 2026-10-27 is a fabricated capture) | n/a | Pass. `score_look` is a public function with no date guard, so the guard is process-level, as for every frozen rule here | pass |
| i | The "4% replicate undercoverage" in the LONGSHOT null | Reproduced exactly with the builder's own `_replicates` (world seed 11, replicate seed 77, 100 replicates, B = 1,000): `lo_excl` 0.04. Re-run with seed 78 at 1,500 replicates: 0.006. My independent script at B = 5,000, 3,000 replicates: 0.0067 | 100 / 1,500 / 3,000 replicates | 0.04 [Wilson 0.016, 0.098] was a 100-replicate draw. At 1,500 replicates: 0.006 [0.003, 0.011], against 0.005 nominal | pass (artifact of n = 100) |
| j | Item 13, schedule hash | `git show <freeze commit>:data/schedule/live_tips_2026-27.parquet` returns nothing at 36e85f1, 3be5b8e, a65d782 or main. `.gitignore:3 /data/` ignores it. On disk: sha256 `9702c830abe987ffea696af800aeb9d1bdc88d275756d4333c19663fca40d86d`, mtime 2026-10-23 20:50:23 CDT (before the 23:01 freeze), 1,200 rows, 775 distinct tips, 2026-10-20T19:00Z..2027-04-12T00:30Z | 1,200 games | The text says the file is "as committed at freeze (sha256 written in the FREEZE RECORD)". It was never committed and no hash was written. The scorer reads whatever file is at the path | fail (C2) |

### The six adversary checks, as they apply to a scorer (synthetic worlds only; no real outcome in either window was read)

| Check | Command | n | Result (Wilson 95%) | Pass |
|---|---|---|---|---|
| 1 Shuffled / calibrated null: does the decision CI hold its level at real n? | `attack_typeI.py <rule> 300 0.005 4000 5000 11` (truth planted at `E[r_no]` = 0, the PASS boundary) | 300 games, 50 dates, 4,000 replicates | LONGSHOT P(lo > truth) 0.0053 [0.0034, 0.0080], P(PASS) 0.0000. UNDERS P(lo > truth) 0.0070 [0.0048, 0.0101], P(PASS) 0.0070 [0.0048, 0.0101]; nominal 0.005 | pass (UNDERS at most about 1.4x nominal) |
| 1b Same at Read 2 | `attack_typeI.py <rule> 840 0.02 2000 5000 12` | 840 games, 140 dates, 2,000 replicates | LONGSHOT P(lo > truth) 0.0180 [0.013, 0.025], P(PASS) 0. UNDERS 0.0210 [0.0156, 0.0283], P(PASS) 0. Nominal 0.02 | pass |
| 1c False NO_POSITIVE_EV_FOUND at truth = floor | same runs, edge +0.010 | as above | LONGSHOT R1 P(NPEF) 0.0043; UNDERS R1 0.0040; LONGSHOT R2 0.0160; UNDERS R2 0.0165 [0.0118, 0.0231] | pass |
| 1d P(PASS) at a true +1c | same runs | as above | LONGSHOT R1 0.505, UNDERS R1 0.324, LONGSHOT R2 0.499, UNDERS R2 0.516. PASS also needs the POINT estimate `R >= 0.010`, so at a true R of exactly +1c, P(PASS) cannot exceed about 0.5 at any n. The frozen texts' "power to clear the bound at a true +1c: 0.97-0.99 at Read 2" is about the bound only, not about P(PASS). The wording came from my own pre-freeze template (R-L1). It is a reader's note, outside the scorer, and the frozen text is not changed (see Power note) | info |
| 2 Planted future | `test_entry_is_latest_row_at_or_before_t_minus_60_never_later` (a later row is planted and ignored); strata `test_z_bins_and_pinnacle_gap_use_only_rows_at_or_before_t_minus_60`; settlement-time plant: none exists | n/a | Entry and strata pass. A settlement planted after the read date is ACCEPTED (C1) | fail (C1) |
| 3 Missingness | Exclusion reasons by outcome: `no_entry_snapshot`, `bid_zero`, `wide_spread`, `above_15c` use only the entry snapshot. `unsettled_game` is whole-game. `scalar_no_value` is contract-level but text-mandated and capped by guard 8.3. The coverage denominator is by clock. One-sided books in the pre-window capture: ask < bid 0, ask = 0 with bid >= 1c 0, sub-cent 0, ask = 100c 9,751, of 928,494 live rows 10-20..24 (counts only) | 928,494 rows | No outcome-dependent exclusion except through C1 | pass |
| 4 Knockout | n/a to a scorer. Guard 8.2 leave-one-out is implemented as the frozen PASS veto (`score.py:335-340`) | n/a | n/a | n/a |
| 5 Replication | Builder's 4% re-run at another seed and more replicates (check i); self-test at independent seeds (check 6 below) | see rows | Holds | pass |
| 6 Integer support | Integer cents and exact micro-dollars throughout; `test_exact_five_cent_spread_is_eligible_float_columns_never_compared`; pre-window capture has 0 sub-cent quotes | n/a | Pass | pass |

### Guard 8.6 false-void rate (how easy is it to void a look whose scorer is correct?)

| Run | n | Void rate | Detail |
|---|---|---|---|
| LONGSHOT, 300 games / 50 dates, fixed seed `[2026,1]`/`[2026,2]`, 40 worlds | 40 | 0.050 [0.014, 0.165] | null lo-exclusion mean 0.070 (range 0.050-0.100); plant mean 0.073, max 0.100 |
| UNDERS, same | 40 | 0.000 [0, 0.088] | null mean 0.064 (0.050-0.085); plant max 0.080 |
| LONGSHOT, 840 games / 140 dates | 15 | 0.067 [0.012, 0.298] | null mean 0.072, max 0.115 |
| UNDERS, 840 / 140 | 15 | 0.067 [0.012, 0.298] | null mean 0.069, max 0.095 |
| LONGSHOT, 300 / 50, independent self-test seed per world | 60 | 0.000 [0, 0.060] | null mean 0.053 (range 0.020-0.085); plant mean 0.051, max 0.080 |
| UNDERS, 300 / 50, independent self-test seed per world | 60 | 0.000 [0, 0.060] | null mean 0.051 (range 0.020-0.090); plant mean 0.051, max 0.090 |
| LONGSHOT, 24 games / 4 dates (preflight shape) | 3 | 0.667 | null mean 0.095; reproduces the preflight failure |

The fixed-seed runs reuse the same 200 uniform vectors on near-identical synthetic ladder sets, so those 40 worlds are not
independent draws of the self-test. Their 7% mean exclusion belongs to that one seed and structure, not to the CI. With an
independent seed per world, the null exclusion averages 5.1-5.3% (nominal 5%) and 0 of 120 worlds void. A direct
measurement of the decision CI at alpha 0.05 (3,000 independent outcome draws, one LONGSHOT world) gives 0.046-0.057.
The self-test sets still land on the band edges: minimum 2.0% and maximum 9.0% in 60 worlds each. That fits a binomial
K = 200 at p = 0.05, which puts about 1.7% outside [2%, 9%] for (b) and about 0.6% above 9% for (c). **Expect roughly a
2-3% chance per rule-look that a correct scorer prints SELFTEST_FAILED on these worlds, about 10% across the four
rule-looks.** Real game and date shocks may raise this. K and the band are frozen, so no reading can lower it without
weakening the check. The 4-date preflight failure is the expected behaviour of a 4-cluster bootstrap. It is not evidence
about real looks.

## The 17 builder interpretations, against the frozen text

"Matches" = the builder's reading follows the frozen text. "Silent" = the text does not decide; my recommendation is the
reading that best protects against a wrong PASS without making a look easier to void. Row ids refer to the DECISIONS
rows drafted at the end.

| # | Topic | Frozen text | Builder | Adversary | Row |
|---|---|---|---|---|---|
| 1 | Side of the "5% bound" (8.6b) | "The decision-CI one-sided 5% bound must exclude the known true E[r_no] in 2% to 9% of sets": side not named | Lower bound gates; upper rate printed | **Silent, builder's reading adopted.** The lower bound is the only bound a PASS uses (§6). Gating on both sides would double the void chance and protect only NPEF | I-1 |
| 2 | Planted-edge judging (8.6c) | "point estimate must fall within 0.5c of the known true R, and the decision CI must cover it": aggregation over K sets not stated | Mean over the 200 sets within 0.5c; P(lo > truth) <= 9% | **Silent, builder's reading adopted.** A per-set reading voids every look: the per-set SD of R is about 0.25-0.45c, so P(\|err\| > 0.5c) in some set of 200 is about 1. The <= 9% ceiling mirrors (b)'s upper band, on the PASS side. Add the plant upper-side rate to the printout (display only; m5). The mean check is a bias check only: truth and estimate share `fee_micro`, so it cannot catch a fee error; the pin does that | I-1 |
| 3 | Fee exception (schedule change with URL + effective date) | Allowed by §3 / §5 | Not implemented; refuses on any difference | **Matches in direction (stricter).** It can block a look, never fake one. If Kalshi changes fees mid-season, implement the exception as an adversary-reviewed code change before the read; it applies only to entries on or after the effective date | I-2 |
| 4 | Identity units | "integer hundredths of a cent" | Exact integer micro-dollars | **Matches the intent, stricter.** `0.07*P(1-P)` is not integral in hundredths of a cent (0.95 -> 33.25), so the literal unit is impossible | I-2 |
| 5 | DNPs in synthetic sets | "DNPs are not simulated" | Held at their real scalar payoff | **Matches.** "Not simulated" on "the look's own contracts" means kept fixed, not removed | none |
| 6 | Self-test seeds | No seeds given | `default_rng([2026,1])` outcomes, `default_rng([2026,2])` bootstrap, B = 10,000 | **Silent, builder's seeds adopted.** They must be fixed in DECISIONS before Read 1 and never changed. A failure is investigated. If no code defect is found, the look stays void: no re-seed, no larger K, no other band. At Read 2 a void closes the rule as SELFTEST_FAILED with no verdict. Re-seeding after a fail would turn the guard into a re-roll | I-3 |
| 7 | KXNBAGAME ladders | Ladders named: player-stat, team spread/team total, game total | Each side its own ladder | **Silent, no effect.** Under LONGSHOT's 15c cap the two complementary sides cannot both be eligible (their mids sum to about 1). UNDERS has no game markets. Accept | none |
| 8 | Game-drop scope in LONGSHOT | "A game with any eligible contract unsettled ... excluded WHOLE" | Includes descriptive KXNBASPREAD | **Matches.** SPREAD contracts are eligible under §1.6; only §1.7 makes them descriptive | none |
| 9 | Multi-reason exclusion counts | Counts "by reason" | First failing reason in text order; `null_ask` separate | **Silent, descriptive only.** Accept | none |
| 10 | LONGSHOT print at INSUFFICIENT_SAMPLE | §6: "INSUFFICIENT_SAMPLE ... no direction is claimed, only the counts and the projected date". §7: "Every look reports the full table whether or not it can decide" | Full table (R, CIs) | **Does not match. Change (C3).** §6 is the specific clause for this verdict. §7's "cannot decide" is KEEP_SHADOWING/INCONCLUSIVE, which still print the full table. UNDERS §6 says the same and the builder already applies it there. Printing an R under a no-direction verdict invites reading a direction the rule forbids | I-4 |
| 11 | Leave-one-out veto scope | "PASS additionally requires ... If any leave-one-out R is <= 0, the verdict is KEEP_SHADOWING ..." | Veto applies to a would-be PASS only | **Matches.** Read unconditionally, the second sentence would make NO_POSITIVE_EV_FOUND unreachable under no edge (every LOO R is negative there), which contradicts §6's first-class NPEF. The sentence completes the PASS condition | I-4 |
| 12 | UNDERS LOO axis | "leaving out each stat" | §3 stat axis (pts, reb, ast, fg3m, stl, blk, combo) | **Matches.** A prop with NULL `stat` becomes a `""` group (m3) | none |
| 13 | Schedule-file hash | "as committed at freeze (sha256 written in the FREEZE RECORD; 1,200 games)" | Not verifiable; asks the maintainer | **Text says it exists. It does not, and the file was never committed.** Record the on-disk identity as a dated pre-look note and pin it in code (C2). Evidence that it is the freeze-time file: mtime 20:50:23 CDT is before the 23:01 freeze; 1,200 rows match the text; the 28/28 match of 10-20..23 is reproduced by the builder's preflight. mtime is not proof of content; it is the best evidence available, and it is recorded before any look | I-5 |
| 14 | Cup games | "excluded and counted" | All under `no_schedule_match` | **Matches.** Add a by-ET-date breakdown of unmatched games so Cup-week (12-04..11) exclusions are visible (m2) | none |
| 15 | Coverage-gap dates | "names the dates with gaps" | Dates under 70% | **Silent, accept** (same threshold as the guard) | none |
| 16 | SPREAD_TAIL_NO overlap count | "reported ... with the overlap count" | Eligible KXNBASPREAD count, labelled as an upper bound | **Silent, accept.** The label must say "upper bound" in the printout | none |
| 17 | Read-date settlements | §6: "no re-read with later settlements"; 8.3: a scalar value may be fetched within 7 days of the read | Relies on the maintainer copying the DB on the read date | **Does not match: the code must enforce it (C1).** A market counts as settled only if `kalshi_markets.settled_ts` <= 23:59:59 ET on the read date. 100% of the 13,089 settled NBA markets in the current copy have `settled_ts`. Only the scalar VALUE may come from pages up to read date + 7 | I-6 |

## Findings, severity-ranked, each with its line and fix

**Required before merge (C = change).**

- **C1 (blocker): settlement is not taken as of the read date.** `__main__.py:126-147` has no upper bound on `today`.
  `data.py:154-160` does not select `settled_ts`. `data.py:305` takes `m.result` as is. Fix:
  1. `read_markets` also selects `settled_ts`.
  2. `assemble(..., settled_by: datetime | None)` sets `res = None` when `settled_ts is None or settled_ts > settled_by`,
     and the same for a raw-page `result` whose page dir is after the read date.
  3. `cmd_read` passes `settled_by` = the read date 23:59:59 America/New_York, converted to naive UTC.
  4. The scalar-value lookup alone may scan raw settled pages up to read date + 7 (guard 8.3), and only for tickers already
     `scalar` by `settled_by`.
  5. The report prints `settled_by`, the DB copy's sha256 and the raw-page date range.

  Negative test: a fixture market with `result='no'` and `settled_ts` one minute after `settled_by` makes its game
  `unsettled_game`.
- **C2 (major): the schedule file is not pinned.** Add `SCHEDULE_SHA256 =
  "9702c830abe987ffea696af800aeb9d1bdc88d275756d4333c19663fca40d86d"` to `spec.py`. `data.load_schedule_tips` (`data.py:138`)
  hashes the file and raises unless it matches, so both `read` and `preflight` refuse on a different file. Negative test
  with a modified parquet. Devin or the main session should copy the file write-once to
  `data/raw/schedule_2026-10-23/live_tips_2026-27.parquet` (immutable raw) with the same hash, and the default `--schedule`
  should point there.
- **C3 (major, text mismatch): LONGSHOT prints R at INSUFFICIENT_SAMPLE.** `engine.py:197`: replace
  `if spec.rule_id == "UNDERS_FORWARD" and res.verdict.name == "INSUFFICIENT_SAMPLE":` with
  `if res.verdict.name == "INSUFFICIENT_SAMPLE":`. Flip the LONGSHOT expectation in
  `tests/research/market_forward/test_selftest_engine.py:138` to `(LONGSHOT, True)`, and rename the test. Better still,
  compute nothing directional: move the INSUFFICIENT check ahead of `pooled()` so no R is computed at all.
- **C4 (major for a frozen descriptive output; no verdict effect): UNDERS §3 strata use the wrong line.**
  `kalshi_markets.threshold` for props is the "N+" integer (`nba/kalshi/thresholds.py:76,108`). UNDERS §1.4 defines the line
  as `floor_strike` (x.5) = N - 0.5. `strata.py:154`, `:156` and `:159` use `threshold` as the line, so z is shifted by
  0.5/sd. `round(N - pin)` (`:159`) also banker's-rounds -0.5 to 0, so a true D = -1 (Kalshi 10.5 vs Pinnacle 11.5) is
  printed as D = 0. That contaminates the FAN_KNOWLEDGE cross cell, which is printed first. Fix: `line = threshold - 0.5`
  for prop and combo series, then `D = int(line - p[1])` (exact: both are x.5), `z = (line - mean) / sd`,
  `line_minus_q50 = line - q50`. Change the fixtures in `tests/research/market_forward/test_strata_pipeline.py:63` to
  integer N thresholds, and add a D = -1 case.

**Should fix (m = minor).**

- m1 `fee.py:86`: re-read `configs/parlay.yaml` raw and require `fee_model.verified is True` (a YAML boolean), because
  `nba/parlay/config.py:103` coerces the string `"false"` to True.
- m2 `data.py:270-275`: count `no_schedule_match` by ET date, and print the 2026-12-04..11 subtotal as "Cup week".
- m3 `data.py:357`: a prop with NULL `stat` goes to `unknown` and is counted, not to `""`.
- m4 Record in DECISIONS (I-6 covers it) that the entry `ts` is the snapshot run's start stamp. The observation lag is
  median 15 s, max 17.7 min, which is not outcome leakage.
- m5 `selftest.py:115-116`: also compute and print the plant upper-side rate `P(hi < truth)` (display only).
- m6 Keep "wider" as total width; it is the text. Print both one-sided bounds of both clusterings, so a reader can see
  when the narrower clustering has the lower lower-bound.
- m7 `data.py:329-330`: treat `ask_c < bid_c` or `ask_c == 0` as `wide_spread` (zero occurrences in 928,494 pre-window rows;
  defensive).

## Corrected family and power note

The family is unchanged: {LONGSHOT, UNDERS} × {Read 1, Read 2}, one-sided 0.005 + 0.02 per rule, family 0.05. Within each
rule m = 1. Measured false-PASS at the PASS boundary on synthetic nested-ladder worlds:
- LONGSHOT Read 1: 0.0000 (P(lo > 0) 0.0053).
- UNDERS Read 1: 0.0070 [0.0048, 0.0101].
- LONGSHOT Read 2: 0.0000 (P(lo > 0) 0.018).

Even if UNDERS ran at 1.4x nominal at both looks, the family would be about 0.06 rather than 0.05. That is not a change
I recommend. A frozen level is never adjusted after the fact, and the excess is within Monte Carlo error at the upper CI
end only. The synthetic worlds lack game-level shocks, so real-data coverage could be worse; the self-test (b) is the
look-time check of exactly that. P(PASS) at a true +1c on these worlds: LONGSHOT 0.505 / 0.499, UNDERS 0.324 / 0.516 (Read 1 / Read 2). Because PASS
also needs the point estimate at or above the floor, P(PASS) at a true edge exactly at the floor is capped near 0.5 at any
n. The frozen "power to clear the bound" figures (0.97-0.99 at Read 2) describe the bound condition alone; P(PASS) at a
true +1.3c or more is the number that approaches them. This is a reading note for the reports, not a rule change.

## The single biggest risk

C1. A PASS is most easily faked by which games are scored, and the scorer currently lets the DB copy's date decide that.
A late read or a re-run after a SELFTEST_FAILED fix would admit late settlements. Late settlements are disproportionately
NO outcomes on "k or more" markets (NO settles after the final), and NO outcomes are exactly the outcomes that pay a NO
buyer.

## Not verified

- Real-data self-test behaviour at a real look (n >= 300 / 50): only synthetic worlds with nesting inside each ladder. They
  have no game or date shocks, so (b)'s exclusion on real data may be higher; that is what (b) is for.
- The full-window raw-page scan runtime (about 2,500 files per day) was not timed.
- The strata loaders against real `prop_predictions` for 2026 (the builder reports the table is empty for 2026 in the copy it
  saw).
- Whether the schedule file's content is byte-identical to the one the freeze text measured. mtime and row count are
  consistent; a hash was never taken at freeze.
- `kalshi_markets.settled_ts` semantics (Kalshi's settlement time or our sweep time). Either is no later than our
  knowledge, which is all C1 needs.

## Commands (scratch, not in the repo; all DuckDB `read_only=True`; exits unpiped)

Scratch dir: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/214e8eaf-cfca-4a90-ae80-a0349b39bd8b/scratchpad/`.
Python: `.venv-1/bin/python`, `PYTHONPATH` = the review worktree.

- Frozen hashes, branch and main, via the freeze-record awk command: `0b50d17e…` x2 and `149be979…` x2, exit 0.
- `ruff check research/market_forward tests/research/market_forward` exit 0; `ruff format --check` (same paths) exit 0
  (20 files); `mypy research/market_forward` exit 0 (13 files). `pytest -q -p no:cacheprovider tests/research/market_forward`:
  68 passed, exit 1 (the repo-wide 70% coverage fail-under, applied to a subset run). The same with `--no-cov`: 68 passed,
  exit 0.
- `python -I lag.py data/kalshi/raw` → 280 runs, lag median 14.9 s, p90 25.7 s, max 1,059 s, exit 0.
- `cp data/kalshi/kalshi.duckdb <scratch>/kalshi_copy.duckdb`, then pre-window quote-shape counts, `ts` type, and `settled_ts`
  nulls (yes 5,146 / no 7,904 / scalar 39, 0 null). Counts only, exit 0.
- Schedule: `git show <commit>:data/schedule/live_tips_2026-27.parquet | shasum -a 256` gives the empty-input hash at 36e85f1,
  3be5b8e, a65d782 and main; `shasum -a 256` on disk gives `9702c830…`; `git check-ignore -v` gives `.gitignore:3:/data/`;
  polars row count 1,200. Exit 0.
- `attack_typeI.py` (LONGSHOT / UNDERS × Read 1 / Read 2), `attack_calnull.py` (builder-config null at B = 5,000 and
  B = 1,000), `attack_selftest.py` (fixed seeds), `attack_selftest_seeds.py` (independent seeds), and `_replicates` re-run
  inline. Exit 0 for each. Outputs are in `typeI.out`, `calnull.out`, `alpha05.out`, `selftest.out`, `selftest_seeds.out`.

No real outcome inside either scoring window was read. The only outcome-adjacent reads were settlement counts, the
`settled_ts` null count and quote-shape counts on 10-20..24 (outside both windows). No season-2025 row was read.

## Drafted DECISIONS rows (pre-look interpretation notes; not appended; the frozen texts are not edited)

Format `| date time | what | why | what would reverse it |`. These go in before Read 1 (2027-01-15) and before any in-window
return is computed. A row added after a look would be a rule change, and is not allowed.

- **I-1** `| 2026-10-24 hh:mm | Interpretation note (pre-look) for KALSHI_LONGSHOT_FORWARD 0b50d17e and UNDERS_FORWARD 149be979, guard 8.6(b)/(c): the "one-sided 5% bound" is the LOWER bound of the decision CI at one-sided 0.05 (the [5%, 95%] percentile interval of the wider clustering). (b) passes iff 2% <= P(lower > true E[r_no]) <= 9% over the K = 200 sets. (c) passes iff \|mean over the 200 sets of R - mean true R\| <= 0.005 AND P(lower > true R) <= 9%. Upper-side rates are printed and never gate. | The text names neither side nor how the 200 sets are aggregated. The lower bound is the only bound a PASS uses; a per-set 0.5c test voids every look at the stated SE. | Never after a look. A new rule could gate both sides. |`
- **I-2** `| 2026-10-24 hh:mm | Interpretation note (pre-look), both rules, fee and identity: the scorer refuses on ANY difference from the pinned fee (0.07, p_one_minus_p, M = 1). The text's schedule-change exception is enabled only by an adversary-reviewed code change made before the read date, applying only to entries on or after the effective date. The 8.6(a) identity is asserted in exact integer micro-dollars (finer than the text's hundredths of a cent, in which the unrounded fee is not integral). | Fail closed: a refused look can be re-run under a reviewed fix; a wrong fee cannot be undone. | Kalshi changes NBA fees before a read: implement the exception under review. |`
- **I-3** `| 2026-10-24 hh:mm | Interpretation note (pre-look), both rules, guard 8.6 seeds: synthetic outcome sets use numpy default_rng([2026, 1]), the self-test bootstrap uses default_rng([2026, 2]), B = 10,000, K = 200, fixed now. A SELFTEST_FAILED look is investigated. If a code defect is found it is fixed under an adversary-reviewed change and the look is re-run on the read-date snapshot. If none is found the look stays void: no re-seed, no other K, B or band. A void at Read 2 closes the rule with no verdict. Measured on synthetic worlds: a correct scorer voids about 2-3% of rule-looks, about 10% over all four (adversary review 2026-10-24). | Re-seeding after a fail would make the guard a re-roll; the void chance is set by the frozen K and band and is accepted. | Never after a look. |`
- **I-4** `| 2026-10-24 hh:mm | Interpretation note (pre-look), both rules, verdict display and LOO: at INSUFFICIENT_SAMPLE (counts, capture or settlement), BOTH rules print counts, exclusion reasons, guard values and the projected date only, with no R, CI or slice (LONGSHOT §6 is the specific clause; §7's "full table whether or not it can decide" covers KEEP_SHADOWING / INCONCLUSIVE). The guard-8.2 leave-one-out veto applies only to a would-be PASS; it never overrides NO_POSITIVE_EV_FOUND or INSUFFICIENT_SAMPLE. | Read unconditionally, the LOO sentence makes NO_POSITIVE_EV_FOUND unreachable under no edge; printing R under a no-direction verdict invites a direction the rule forbids. | Never after a look. |`
- **I-5** `| 2026-10-24 hh:mm | Interpretation note (pre-look), both rules §1.3: the window file "as committed at freeze" was never committed (data/ is gitignored) and no hash was written in either FREEZE RECORD. The window file is fixed as data/schedule/live_tips_2026-27.parquet with sha256 9702c830abe987ffea696af800aeb9d1bdc88d275756d4333c19663fca40d86d (1,200 rows; mtime 2026-10-23 20:50:23 CDT, before the 23:01 freeze), copied write-once to data/raw/schedule_2026-10-23/. The scorer refuses on any other hash. | The window must not be editable after results exist; this is the best freeze-time evidence available, recorded before any look. | Evidence that the file changed between 23:01 and this row: then the window is the file as of the freeze, if recoverable; otherwise Devin decides, before any look. |`
- **I-6** `| 2026-10-24 hh:mm | Interpretation note (pre-look), both rules §5/§6, "settled at the read": a market counts as settled only if kalshi_markets.settled_ts <= 23:59:59 America/New_York on the read date (2027-01-15, 2027-04-14), whenever the scorer is run. Only a scalar's settlement_value may come from raw settled pages up to read date + 7 (guard 8.3). The entry snapshot is the kalshi_prices row by its stored ts (the snapshot run's start stamp; pages land a median 15 s, at most 17.7 min, later). | "No re-read with later settlements", made independent of when the DB copy is taken; late settlements are disproportionately NO on "k or more" markets. | Never after a look. |`

---

## Re-review (diff only), 2026-10-24 (after 00:41 CDT)

Diff read: `git diff 172be16 700fcff` on `agent/market-longshot-unders-scorer` (13 files, +365/-41). No new attacks. The checks
were that each fix does what C1-C4, m1-m3, m5 and m7 asked, and that the revision adds no new contradiction.

Verdict: APPROVED

| Item | Where | Result |
|---|---|---|
| C1 settled_by | `__main__.settled_by_for` (23:59:59 America/New_York, then naive UTC); `read_markets` selects `settled_ts`; `assemble` accepts a DB result only if `settled_ts <= settled_by`, else a raw result only if its page dir is <= the read date; raw scan runs to read date + 7, but a post-read page can only supply a scalar VALUE for a ticker already `scalar` by the read; report prints settled_by, the DB sha256 and the raw dir range | Done. Tests: one minute after settled_by makes the game unsettled; an unstamped DB result and a post-read raw page do not settle; a scalar value is allowed after the read only if already scalar |
| C2 schedule pin | `spec.SCHEDULE_SHA256 = 9702c830…`; `load_schedule_tips` refuses on any other hash; default `--schedule` is `data/raw/schedule_2026-10-23/live_tips_2026-27.parquet` | Done. The copy exists, is read-only (`-r--r--r--`) and hashes to `9702c830…` (`shasum -a 256`, exit 0). Negative test present |
| C3 counts-only | `engine.py`: n below the minimum returns before `pooled()` for both rules; guard-driven INSUFFICIENT (8.1 / 8.3) drops `pooled` | Done; stronger than asked (no R is computed at all on the n path) |
| C4 strata line | `strata.py`: `line = threshold - 0.5` for prop and combo; `D = int(line - pin)` (exact); z and `line - q50` use the line | Done. Fixtures moved to integer N, and game rows keep `floor_strike` |
| m1 | `fee.py`: raw YAML `verified is True` | Done in the scorer; the live loader's `bool()` coercion is unchanged (Devin's path; noted) |
| m2 / m3 / m5 / m7 | Unmatched games by ET date with a Cup-week subtotal; NULL-stat prop goes to `unknown` and is counted; plant upper-side rate printed; `ask < bid` or `ask == 0` counted as `wide_spread` | Done, each with a test |

Builder's flags:
1. Preflight runs with `settled_by=None`. Accepted: it covers only games before the window (cutoff 2026-10-24), prints counts only, and the
   scoring path always passes settled_by.
2. C3 returns before `pooled()`. Accepted (better).
3. Raw-page results are judged by "the page's ET day dir". This is a minor wording error, and it errs in the safe direction. Raw dirs are named
   by UTC date (`nba/kalshi/snapshot.py:76`, `now` in UTC), not ET. Comparing the UTC dir date with the ET read date rejects pages written
   20:00-23:59 ET on the read date. That can only add `unsettled_game` whole-game drops, never admit a later settlement. It also matters only
   when the DB `settled_ts` is missing, and it is populated on 13,089 of 13,089 settled NBA markets in the current copy. Optional fix: correct the
   docstring at `data.py` `assemble` to "UTC page dir".
4. m1 is guarded in the scorer only. Accepted.

New contradictions: none found. The I-1..I-6 DECISIONS rows (above) are still to be appended by the main session before Read 1;
I-5's write-once copy now exists at the stated path.

Verification (unpiped, this worktree at 700fcff, `.venv-1`): `ruff check` exit 0; `ruff format --check` exit 0 (20 files); `mypy
research/market_forward` exit 0 (13 files); `pytest -q -p no:cacheprovider --no-cov tests/research/market_forward` 79 passed,
exit 0.
