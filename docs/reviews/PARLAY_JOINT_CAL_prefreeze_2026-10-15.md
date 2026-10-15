# PARLAY_JOINT_CAL pre-freeze review (adversary), 2026-10-15

Candidate: `docs/prereg/PARLAY_JOINT_CAL.md` at main (draft commit cd0a08b, unchanged through 7e9a81d), rule hash
`1e9bd01e3063939160ce78c07b6bdecd9cdd2f778f3839a6dbf1e055622f221b`. Gate code `research/eval/parlay_joint_cal_gate.py`, gate
output `reports/prereg_parlay_joint_cal/gate.json`. No scoring harness exists. The question is whether the document pins the
harness tightly enough for it to be written after the freeze, with a harness-pin commit as LINE_CALIBRATION had.
Model class: set and joint models (Gaussian copula for parlays) over a recalibration-at-the-line layer (docs/MODEL_PLAYBOOK.md).
Seasons touched by this review: 2023 and 2024 only. Season 2025 was never loaded. No DuckDB file was opened. No ticket outcome
was computed. Leg labels entered only LINE_CALIBRATION's own `walk_forward`, to reproduce its published Stage 1 numbers and
to produce `p_cal`, exactly as the harness will. Scripts: scratchpad `attack.py` and `d1_power.py` (not committed).

Verdict: BLOCKED

Read this as "blocked until the text is edited", not "the design is broken". Every check against real data passed. The hashes
reproduce, the gate re-runs bit-identically, the CAL legs reproduce T493 exactly, and the CALIBRATED band is reachable even
under stressed correlation. The block exists because five things that move a word-bearing number would be chosen by whoever
writes the harness after the freeze (B1-B5). One of them, the copula cdf tolerance, cannot be met in reasonable compute as
pinned, so the harness author would be forced to change a frozen constant. All five are text edits. After they are applied and
the file is re-hashed, a diff-only re-check (no re-run) is enough to approve.

## Top finding

**The rule pins the copula and the bootstrap in prose that a harness author must turn into code, and the prose leaves several
choices open. Each choice moves the same-game T1 gap, the T2 contrast or every interval.** Specifically:

- **Copula** (PARLAY_JOINT_CAL.md:41, :152). It does not say which residual rows feed `estimate_pair_corr`: every `model_rows`
  player-stat-game (all four stats, played-only) or only priced legs. It does not give the row sort order for the
  `default_rng([2026, 7])` PIT uniforms. The playbook (count models) says "the PIT draw depends on the row order for a fixed
  seed: fix the order in the rule". It does not say how F(y-1) and F(y) are read from `p_ge`. It does not give the sign flip
  for under legs in the orthant probability (`build_corr_matrix` is "stat-level; side is handled at hit time"). It does not say
  whether PSD repair is per ticket or per game.
- **Orthant tolerance** (:41). `abseps = releps = 1e-8` costs 0.35 to 0.48 s per 3-D `scipy.stats.multivariate_normal.cdf`
  call (scipy 1.18.1). The rule needs about 286,000 3-D calls: TOP same-game 3-leg candidates 58,130 + 72,656 (DraftKings) and
  54,309 + 70,148 (Pinnacle), plus 15,285 U-ALL same-game 3-leg tickets x 2 arms. That is about 30 CPU-hours. At 1e-6 a call
  takes 0.0375 s with an error of 1.6e-7 against a 1e-10 reference (about 3 hours in total). The 1e-6 error is four orders
  below the 0.003 Monte Carlo noise the rule cites as its reason.
- **Bootstrap** (:45). `nba/truth/bootstrap.py` does not exist. Two in-repo implementations match the prose and give
  different intervals from the same seed: `research.eval.model_clv_eval.Boot` (multinomial over all clusters, no strata) and
  `research.eval.alt_harness_common.make_boot` / `boot_ratio` (`rng.integers`, one matrix per season, seasons ascending, one
  generator). The prose also leaves open which dates form the cluster universe of a cell (the cell's own dates or all slate
  dates of the season), whether matrices are shared across cells and arms, and the p formulas for T2 and T3.

## Checks run

| # | Check | Command (repo root; scratch = session scratchpad) | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Rule hash | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PARLAY_JOINT_CAL.md \| shasum -a 256` (exit 0) | 181 lines | `1e9bd01e…221b`, matches; also on main 7e9a81d | PASS |
| 2 | Pinned file hashes | `shasum -a 256 research/eval/parlay_joint_cal_gate.py reports/prereg_parlay_joint_cal/gate.json data/ops/model_clv_pairs/pairs_202{3,4}.parquet reports/odds_history/model_rows_202{3,4}.parquet` (exit 0) | 6 files | all six equal the values at :65, :69 | PASS |
| 3 | `select.json` pin | `shasum -a 256 reports/prereg_line_calibration/select.json` | 1 | file is `2df7fb05…`. The `a5441242…` at :157 is the LC rule hash stored inside it, so the file itself is unpinned | FAIL (minor, M2) |
| 4 | Non-negotiable 8: gate re-run on real data | `.venv/bin/python -W ignore research/eval/parlay_joint_cal_gate.py --data-root <main> --out <scratch>/gate_rerun.json` (exit 0, 92 s), then a recursive JSON diff | 4 books x cells x seasons | identical except `date_stamp` and 36 3-leg `copula_gain_*` values (max abs diff 6.5e-8; `equicorr_joint` calls `mvn.cdf` unseeded). 16 of 16 ticket fingerprints and every count and width identical | PASS |
| 5 | Preflight: CAL legs reproduce T493 | `attack.py`: `lc.walk_forward(D, D["over"], 2024, book, "cal")`, mean `lc.ll` against `select.json` | DK 53,752; Pin 37,528 | DK CAL 0.6877927828557955 (diff 0.0), A0 0.6972881640134292 (0.0); Pin CAL 0.6882240137145901 (0.0), A0 0.6999188355584878 (0.0) | PASS (to 1e-9) |
| 6 | Identity rule = LC's own | `attack.py`: LC `walk_forward` 2023 block info against `gate.json` `cal_identity` | 2023 blocks | DK 11,612 of 53,247 identity legs, Pin 10,479 of 35,146; 0 block mismatches; 0 identity blocks in 2024 | PASS |
| 7 | Fingerprints from LC-served legs | `attack.py`: gate construction on LC-eligible legs | 16 (book x cell x season) | 16 of 16 match `gate.json` | PASS |
| 8 | Proxy widths against real CAL marginals | `attack.py`: `sim_halfwidth` with q = max(p_cal, 1 - p_cal), 300 sims | DK 2024 | cross2 0.0156 (proxy 0.0154); cross3 0.0135 (0.0123); same2 0.0143 (0.0145); same3 0.0120 (0.0121); Pinnacle and 2023 within 10% | PASS |
| 9 | CALIBRATED band against date-correlated outcomes (label-free stress) | `attack.py`: shared game factor r_g in {0, .05, .10, .20} and date factor r_d in {0, .02, .05}, sign by side, 300 sims | DK 2024 U-ALL | 95% HW ranges: cross2 0.0146-0.0173, cross3 0.0130-0.0141, same2 0.0150-0.0172, same3 0.0117-0.0132. All far below the 0.050-0.073 "conservative" bound (:84); CALIBRATED is reachable when \|G\| <~ 0.013 | PASS |
| 10 | T2 power, label-free | `d1_power.py`: E[D1] and HW if CAL legs were the truth (independent legs) | DK 2024: 3,658 / 3,260 / 4,227 / 4,238 | E[D1] -0.0098 / -0.0080 / -0.0101 / -0.0089; model-only HW 0.0046 / 0.0045 / 0.0042 / 0.0042. P(D1 <= -0.005 at E) about 0.90-0.99 | PASS (floor reachable) |
| 11 | Side definition of the RAW arms | `attack.py`, `d1_power.py` | DK 2024 legs 53,752 | CAL flips the side of 12.4% of legs (2023 11.7%; Pin 12.9% / 13.7%); 22.6% / 35.5% / 21.2% / 31.5% of 2024 tickets hold a leg whose RAW P of the CAL side is < 0.5 | FAIL (spec, B3) |
| 12 | Planted future | `load_legs` on a scratch copy of `pairs_2024` with one DK T-60 snapshot 5 min after the real tip; `load_legs(..., 2025, ...)` | 1 planted row | "PLANT REJECTED: a T-60 snapshot closer than 60 minutes"; "2025 REJECTED before read". Both guards are bare `assert` (stripped by `python -O`). The copula fit (:41) and J (:42) have no stated planted-future test | PASS (construction) / FAIL (harness tests unpinned, M6) |
| 13 | Missingness | polars counts on `pairs_{2023,2024}` T-60 DK/Pin and `model_rows` | 88,393 / 91,280 legs; 110,476 / 110,332 rows | 0 null `p_model`, prices, `starter10`; `_dnp` 0; `status` 0 everywhere; no leg dropped for a missing team; `model_rows` minutes > 0 on every row (played-only; 9,532 / 9,216 rows under 5 min kept). The only outcome-linked filter is played-only, which is declared (:24) and chosen by Devin | PASS |
| 14 | Integer support | `attack.py`: every eligible line x.5 | all eligible legs | true in every book and season; no pushes | PASS |
| 15 | Orthant cdf | `attack.py` and timing: fresh `default_rng(2026)` per call, 3 repeats; other seed; shared generator | 3-D example | deterministic per fresh rng; seed effect 3.9e-9; a shared generator changes the value by 3.3e-9 between calls; 0.35-0.48 s per call at 1e-8 | FAIL (B5) |
| 16 | Draft tests and lint | `pytest -q tests/research/eval/test_parlay_joint_cal_gate.py --no-cov`; `ruff check`; `ruff format --check`; `mypy research/eval/parlay_joint_cal_gate.py` | 7 tests | 7 passed, exit 0 (with coverage on, exit 1 from the repo-wide 70% floor on a one-file run); ruff 0; format 0; mypy 0 | PASS |

Not applicable before a result: shuffled labels, knockout and replication (no outcome exists). Their pre-result analogue is a
synthetic-label rehearsal, which the rule does not require (M6).

## Answers to the questions asked

**2024 exposure.** The disclosure exists (:100, :123, :178) but understates. The drafter saw more than "Stage 1 aggregates": `select.json`
holds the 2024 DraftKings CAL reliability by decile and calibration in the large (CAL -0.0003 [-0.0054, +0.0048], n 53,752;
bins 0.4 and 0.5: predicted 0.467 / 0.529 against observed 0.465 / 0.531). With cross-game independence near 0 (T492), the 2024
cross-game T1 result is close to foreseeable. The informative 2024 cells are same-game (copula) and TOP (selection). T490, the
source of the D1 algebra (:53), also ran on 2023-24 and 2024-25. The design is honest; the label is not prominent enough (M4).

**Random tickets and the panel slice against contract v2 and the producer.** U-ALL is fully pinned: code, seeds, at most 20
per date, no refill, fingerprints, all reproduced. TOP is pinned as this rule's own deterministic panel proxy, and the
"exact for a product" claim (:33) is tested. But the rule calls it the panel itself, and it is not:

- Contract v2 (`docs/contracts/top_parlays_v2.md`, now on main) uses `schema: "top_parlays/2"`. The app refuses v1. It adds a
  required `illustration` flag and ranks ties by higher EV.
- The unfinished producer (`.claude/worktrees/market-top-parlays`, uncommitted `nba/parlay/top_parlays.py`, `top_source.py`)
  does the following:
  - keeps the top K = 40 legs per book globally, not per game;
  - ranks across every book that has a CAL map (no ticket mixes books);
  - uses the newest pre-tip main-line quote, not T-60;
  - computes the same-game joint by Monte Carlo with `entity_corr` from a production `PairCorr`, not by a walk-forward
    `estimate_pair_corr` orthant.

  Under Devin's "widest candidate set" default that is acceptable, but :35 ("the union ranking is the panel's own"), :41
  ("served arm") and :111 (`schema top_parlays/1`) must change (M1, M3).

**CALIBRATED bands against gate widths.** The bands are reachable: realised half-widths should be 0.012-0.017 under any
plausible shared shock (check 9), so CALIBRATED needs |G| below about 0.013-0.018. The "conservative" bound (0.050-0.073)
assumes a perfectly correlated slate. The stress shows that does not happen, because over and under sides cancel a shared
game or date shock. The rule's choice to let the realised interval decide is right. T490's "1.0 to 1.1 times" (:84) was not
re-verified.

**Three BH families.** Family A m = 4 (one two-sided p per cell serving OVER and UNDER), B m = 4 (one-sided), C m = 2. That
matches the family actually run: 10 word-bearing tests. Pinnacle and descriptive rows are rightly outside. Gaps: the T3 p is
never defined (one- or two-sided); T2's one-sided orientation is not written as a formula; CALIBRATED is an
interval-inside-margin (TOST-like) claim and is not BH-controlled, giving at most 2.5% per cell and at most 10% over four
cells of a false CALIBRATED at |G| = 0.03. That should be stated (B2).

**Floors after counts, before outcomes.** G1-G3 are disclosed (:84). The TOP floor -0.05 was set against the gate widths
(:55) and the D1 floor -0.005 against algebra built from T490's raw overconfidence (:53). Both came after counts and after
seeing related 2024 outcomes (T490, T493), and neither is in the :84 disclosure (M5). The ±0.02 / ±0.03 margins are carried
from ALT_LINE_70 / ALT_PARLAY_X, which is fine.

**Copula specification.** Incomplete; see the top finding and B4. It is sound in substance: walk-forward by month, shrinkage
pinned at 200, played-only rows, zero correlation with no history.

**Planted future and missingness.** Pass on what exists (checks 12, 13). The harness's own negative tests are not required
by the rule (M6).

## Findings (file:line, exact fix)

### Blockers

- **B1. PARLAY_JOINT_CAL.md:45 (bootstrap code unpinned).** Replace the first sentence with: "CI everywhere:
  `research.eval.alt_harness_common.make_boot(sizes, 5000, 2026)` with `sizes = {2023: D23, 2024: D24}`, where D_s is the
  number of distinct CAL-served DraftKings slate dates of season s (171, 212). One generator draws seasons in ascending order,
  and the same matrices are shared by every cell, arm, statistic and book (Pinnacle uses its own dates and its own
  `make_boot(…, 2026)`). A cell with no ticket on a date contributes 0 to that date's totals. Statistics are ratio estimators
  on date totals (`boot_ratio`). The interval is `percentile_interval`; T1 and T3 p is `p_two_sided`; T2 p is `p_le0` applied
  to the bootstrap of -D1 (small when D1 < 0). A one-season statistic uses only that season's matrix." Pin
  `alt_harness_common.py` by sha256 in the Construction identity section (:156).
- **B2. :55, :62 (p for T3 undefined; CALIBRATED multiplicity unstated).** Add to :55: "p is `p_two_sided` on the pooled
  bootstrap of G_top." Add to :62: "CALIBRATED is decided by the interval inside ±0.03 and is not BH-adjusted; at most 2.5%
  per cell of a false CALIBRATED when |G| = 0.03, at most 10% over the four cells."
- **B3. :21, :39 (side of the RAW arms).** Replace :39 with: "RAW-IND: product over legs of the raw production probability
  of the CAL side, P_raw(side_cal) = p_model if p_cal >= 0.5 else 1 - p_model, clipped to [0.5/199, 1 - 0.5/199]. On 12.4% of
  2024 DraftKings legs this is below 0.5 (CAL flipped the side); part of D1 is that flip, by design." Add the same for
  RAW-COP. Keep "what T490 called the model's 70%" only for the descriptive RAW-ranked top-3 (:60), and define that slice as
  ranking by RAW-IND with the RAW side.
- **B4. :41, :152 (copula unpinned).** Add:
  - Residual rows are every `model_rows_{2023,2024}` row (all four stats, every player; played-only by construction) with
    `game_date` strictly before the block's first day.
  - y is `model_rows.y`. With S = `p_ge` (S[k-1] = P(Y >= k), k = 1..len(S), S[k-1] = 0 beyond the vector):
    F(y) = 1 - S[y] and F(y-1) = 1 - S[y-1], with F(-1) = 0.
  - PIT uniforms are one `default_rng([2026, 7]).random(N)` draw over all rows of both seasons sorted by (game_date, game_id,
    player_id, stat), reused by every block.
  - `ResidualRow(team_id = model_rows.team_id)`; relations are `same_player` / `teammate` / `opponent` from `_relation`.
  - Per ticket: R from `build_corr_matrix` on the ticket's legs (per-ticket `nearest_correlation`); D = diag(+1 over, -1
    under); P(all hit) = Phi_m(Phi^-1(q_1), …, Phi^-1(q_m); D R D), with q the arm's side probabilities.
  - Assert before each block's fit that no residual row has `game_date >= block start`.
- **B5. :41 (infeasible tolerance; generator semantics).** Replace "`rng=default_rng(2026)`, `abseps = releps = 1e-8`"
  with "a fresh `default_rng(2026)` per call, `abseps = releps = 1e-6`, default `maxpts`; measured error 1.6e-7 on a 3-D
  case; about 0.04 s per 3-D call, about 3 CPU-hours over the ~286,000 3-D calls". Also state that the scoring job takes
  `data/ops/heavy.lock` and checkpoints per (book, season, cell) (:167 already promises this).

### Major

- **M1. :111, :5 (contract version).** `top_parlays/1` becomes `top_parlays/2` (`docs/contracts/top_parlays_v2.md`). Add:
  "files with `illustration: true` are excluded and counted"; slices add `book` (the producer ranks across books) and lead time
  (the producer uses the newest pre-tip quote, not T-60; CAL is a T-60 object).
- **M2. :114, :169-173 (open questions in a frozen rule).** Replace "Open questions for Devin" with "Answered (DECISIONS
  2026-10-15 00:58 and item 3 of 00:24-00:39)": (1) widest candidate set; (2) condition on playing, and the live log reports
  the void rate; (3) production legs, AVG reference only (DECISIONS line 1231), so the :114 hazard becomes "if a future panel
  serves AVG legs, the `model_source` slice shows it"; (4) DraftKings primary. Also pin the `select.json` file sha256
  `2df7fb056f4b12de314b407eeaada816a87772fa544055edf639c529fcb85b6c` at :157 and say that `a5441242…` is the rule hash inside
  it.
- **M3. :35, :41, :161 (TOP is a proxy of the panel).** Replace "so the union ranking is the panel's own" with "a
  deterministic proxy of the panel under the widest candidate set. The producer differs: global top-40 legs per book, all CAL
  books, newest pre-tip quote, Monte Carlo copula with production correlations. The live log scores the producer's real
  list." In :41, "served arm" becomes "the arm this rule treats as served". The :161 kill action should read "proposal about
  the panel" only if the live TOP agrees in sign.
- **M4. :8, :100, :123, :133 (exposure).** Add to :100: "and the 2024 DraftKings CAL reliability table and calibration in the
  large (-0.0003, n 53,752) in `select.json`, which make the 2024 cross-game T1 close to foreseeable". Require every 2024 word
  to be printed as "<WORD> (2024, exposed: LC select season)". Rename "select season 2023" to "consistency season 2023"
  (nothing is selected).
- **M5. :84 (disclosure scope).** Extend the parenthesis to: "G1-G3, the T3 floor (-0.05, set at 1.4 gate half-widths) and
  the T2 floor (-0.005, from algebra on T490's raw overconfidence, 2023-24 and 2024-25) were all set after the counts and after
  T490 and T493 outcomes were seen, before any outcome of this rule."
- **M6. :157 (required harness negative tests).** Add steps (5)-(7):
  - (5) A synthetic-label rehearsal. Leg outcomes are drawn from the CAL-COP joint with shared legs and 2024 standing in.
    It must return no OVERCONFIDENT or UNDERCONFIDENT word in any cell, and CALIBRATED or INCONCLUSIVE otherwise.
  - (6) Planted future. A residual row dated inside the block, with |z| = 8, leaves that block's `PairCorr` unchanged. A J
    ticket dated inside its block is refused.
  - (7) Guards raise rather than `assert` (`load_legs` :76, :88 of the gate code is unchanged by the freeze, so the harness
    re-checks `max(season) <= 2024` and lead >= 60 with `if …: raise`).

### Minor

- **m1. :34.** The same-game 8-leg approximation: report how many TOP same-game tickets use a game's 8th-ranked leg. A
  non-trivial count means the boundary bit.
- **m2. :23.** Say that eligibility is LC `walk_forward`'s `identity` flag, which this review verified equal to the gate's
  `_blocks` on real data (0 mismatches). The harness asserts the equality.
- **m3. Gate code `equicorr_joint`.** Unseeded `mvn.cdf` makes the descriptive copula-gain numbers irreproducible beyond
  about 1e-7. That is harmless (descriptive, frozen output), but note it at :151.

## Power and corrected family

- Family: A (T1) 4, B (T2) 4, C (T3) 2: 10 word-bearing tests, as declared. Descriptive rows and Pinnacle are outside.
- T1 (DK 2024, realised HW expected 0.012-0.017, se 0.006-0.009): OVERCONFIDENT is limited by its -0.02 floor; 80% power at
  |G| about 0.026-0.028. CALIBRATED at a true G = 0 has probability >= 0.9; at |G| = 0.01 about 0.6-0.75.
- T2: E[D1 | CAL true] -0.008 to -0.010 against model-only HW about 0.0045 (independent tickets; date clustering may widen
  it). The -0.005 floor is reached with probability about 0.90-0.99.
- T3: as stated (:55): -0.05 is flagged about half the time, -0.07 about 85%. No CALIBRATED word, correctly.
- D2 (copula against independence) and J: underpowered, cannot conclude. Correctly descriptive (playbook: monitor or pool).

## Playbook check

Class: set and joint models (copula for parlays) over a recalibration-at-the-line layer; J is a two-parameter calibration
layer.
1. **Information or capacity:** neither; it tests whether a transformation of calibrated legs stays calibrated. Agreed.
2. **Enough data for this class:** yes for the gap words (3,260-4,238 tickets and 163-212 dates per cell; widths confirmed
   with real CAL marginals and under stress); no for D2 and J. The playbook's class check ("one season detects about 0.002,
   above the plausible effect") is honoured by keeping them descriptive.
3. **Test validity:**
   - Real tip with >= 60 min asserted and planted-future rejected.
   - Played-only population, declared.
   - Integer support: x.5 lines everywhere.
   - Paired arms on identical tickets, once B3 pins the side.
   - The CAL legs reproduce T493 exactly.
   - The PIT row order is unfixed, a playbook-named failure (B4).
   - The comparator does not have all of production's information: the producer's copula and candidate rules differ (M3).
   - The 2024 report season is exposed (M4).
4. **Lineage and inherited guards:** LINE_CALIBRATION (map, identity rule, walk-forward, shuffle guards inherited and
   attacked in T493); ALT_PARLAY_X / T490 / T492 (construction, date clusters, words). The bootstrap lineage is named only in
   prose; B1 makes it code.
5. **Scored against:** joint calibration in the large and joint log loss on a binary event; no price, which is correct for
   a probability rule.

An invalid test is not a null: nothing here makes a future result invalid once B1-B5 are pinned.

## The one biggest risk if a result is wrong

A same-game T1 word (the cells that are not already foreseeable) produced by a copula the harness author assembled after the
freeze: wrong PIT order, missing the under-leg sign flip, or a different residual row set. That would produce a plausible
CALIBRATED or OVERCONFIDENT that no frozen text can check. B4 closes it.

## Not verified

- T490's "realised intervals 1.0-1.1 times model-only" (:84).
- The size of the same-game 8-leg approximation (m1).
- Runtime of `estimate_pair_corr`'s Python pair loop over about 220,000 rows per block.
- The main-line selection upstream of the paired frame (inherited from MODEL_CLV / LC; attacked in T493).
- The truth-layer settlement path for the live log (does not exist yet).
- The unfinished producer may change before opening night.

## Recommended changes (not applied by me)

Apply B1-B5 and M1-M6 to `docs/prereg/PARLAY_JOINT_CAL.md`, re-hash, and send the diff for a re-check (diff-only, no re-run).
m1-m3 can go in the same edit. No code change is required before the freeze: the gate code and `gate.json` stay as pinned.

## Drafted rows (not appended)

- PREDICTIONS_LEDGER (adversary prior, stated before any result): P(any DK cell OVERCONFIDENT) 0.10; P(cross 2-leg
  CALIBRATED) 0.70 (higher than the modeler's 0.55 because 2024 leg-level CITL is -0.0003 and the realised HW should be
  about 0.016); P(all four T2 CAL HELPS THE JOINT) 0.75; P(harness preflight stop) 0.05.
- DECISIONS: none (no direction-setting choice in a review).

---

# Re-check of revision 2 (adversary), 2026-10-15

Candidate: `origin/agent/modeler-parlay-joint-cal-2` at 2a9bf05. Rule hash recomputed with the rule's own `awk … | shasum -a 256`:
`59f3b08d62ea7766a06d2b6f9b715b92e2ff9d84366fb542f1d209910a7a974f` (exit 0), which matches. New files: harness
`research/eval/parlay_joint_cal_eval.py`, pin module `research/eval/parlay_joint_cal_rule.py`, tests, and rehearsal outputs.
No real outcome was read by me: I ran no `score` stage, read no `y`, and recomputed no ticket hit. Season 2025 was never
loaded.

Verdict: BLOCKED

The design changes are right, and every review item is applied in substance. Five gaps remain, all small: four in the
harness and one in the text. Three of the code gaps concern what the preflight checks and when it runs, which is exactly what
this re-check was asked to confirm. The harness is pinned at the freeze, so the code fixes must land before the freeze
commit. After the fixes, a diff-only re-check is enough (no re-run).

## Item-by-item against the review

| Item | Text (rev 2 line) | Harness | Status |
|---|---|---|---|
| B1 bootstrap | :52 | `Clusters`: `ahc.make_boot({s: n_dates}, 5000, 2026)` per book, one generator, matrices shared, `boot_ratio`, `percentile_interval`, `p_two_sided`, `p_le0(-boot)`; dates are the book's CAL-served slate dates | done |
| B2 T3 p, CALIBRATED multiplicity | :62, :69 | `word_t3`, `bh_q` over 2 sizes | done |
| B3 RAW on the CAL side | :40, :42 | `q_raw_side = clip(where(p_cal >= .5, pm, 1 - pm), 0.5/199)`; RAW-ranked top-3 uses the RAW side | done |
| B4 copula | :43-:46 | `cdf_pair` (index convention checked by hand and by test), `pit_z` (one draw, sorted rows), `GameStats` (tested equal to `estimate_pair_corr` to 1e-9), `ticket_R` (= `build_corr_matrix`, tested), `orthant` with D R D (side sign tested against Monte Carlo) | done |
| B5 tolerance | :47 | 1e-6, fresh `default_rng(2026)` per call | done; the checkpoint sentence is not implemented (R4) |
| M1 contract v2 | :126 | n/a | done at :126; :6 and :12 still cite `top_parlays_v1` (R4) |
| M2 answers, select.json pin, hazard | :23, :129, :185-189 | `SELECT_SHA` asserted | done; the "00:52 item 3" citation is correct (my review's citation was wrong) |
| M3 TOP is a proxy | :36, :41, :177, :192 | n/a | done |
| M4 exposure | :9, :115 | `EXPOSED` appended to every word | done |
| M5 floors disclosed | :91 | n/a | done |
| M6 negative tests | :173 (5)-(7) | rehearsal stage; planted-future and guard-raise tests | done (but see R1-R3 for (1) and (3)) |
| m1 / m2 / m3 | :112, :24, :171 | `n_boundary8`; identity-flag equality raised in `book_legs` | done |

## Findings (exact line, exact fix)

### Blocking

- **R1. The preflight does not run before outcomes are read** (`parlay_joint_cal_eval.py` `run()`, the lines from
  `rows = read_rows(root, real)` to `pf.require()`). In `score` mode the harness reads every real `model_rows.y`, builds
  the copula statistics from it (`build_game_stats`) and fits CAL on the real `over` (`load_cal`). Only then does it call
  `pf.require()`, which refuses on a wrong `alt_harness_common`, `select.json` or gate sha. Nothing is scored or written
  first, but outcomes are read ahead of hash checks that need none. Fix, in this order:
  1. `pf.require()` right after the sha checks, plus the new input checks of R2.
  2. `load_cal`, which reads the leg labels the CAL preflight needs.
  3. The CAL and A0 reproduction (R3), then `pf.require()` again.
  4. Only then `read_rows(root, real)` and `build_game_stats`.
- **R2. The input data are not pinned by the harness** (rule :72, :76; `run()`). The four input sha256 values (`pairs_2023`
  050dba3e…, `pairs_2024` 560a3808…, `model_rows_2023` 002e53ef…, `model_rows_2024` ec47dcdf…) and `gate.json`
  (4b57377d…) are pinned in the text but never asserted. The CAL preflight indirectly verifies the leg columns. It does not
  verify `model_rows.y` or `team_id` for the non-leg rows, and those drive the copula, which is the served arm of both
  same-game cells. Fix: add one `pf.add(..., ahc.file_sha256(path) == <pin>)` per file, before step 1 of R1. The gate's
  `load_legs` already computes the pairs and rows hashes, but `book_legs` discards them.
- **R3. The A0 reproduction promised at :173 (3) is missing.** `run()` checks the CAL log loss only. Its comment says "the
  A0 comparison needs p_model", and `p_model` is in the frame. Fix: in the same loop, add
  `abs(mean(lc.ll(clip(pm, lc.EPS, 1 - lc.EPS), over)) - sel[book]["ll"]["A0"]) < 1e-9`, as I ran in the first review
  (difference 0.0).
- **R4. Text inside the hash contradicts the harness or itself.**
  - :5 says "The scoring harness does not exist; it is written after the freeze"; :4 and :107 say it exists. Replace it
    with the R5 procedure.
  - :183 says "Scoring: **no command exists** until the freeze". Replace it with the real command:
    `cd <main> && .venv/bin/python -W ignore research/eval/parlay_joint_cal_eval.py score --data-root /Users/devin/Downloads/nba-prediction --sha <sha>`.
    Say that it is a background job, since it exceeds the 600 s watchdog.
  - :47 says the job "checkpoints per (book, season, cell)", but the harness writes `<book>.json` only at the end of
    `evaluate_book`. Either implement it or delete the sentence.
  - :171 says numpy and polars versions "are recorded by the harness", but nothing records them. Record numpy, polars and
    scipy in `result`. `scipy.stats.multivariate_normal.cdf`'s algorithm and its `rng` argument are version-dependent, so
    a scipy mismatch should at least print loudly.
  - :6 and :12: `top_parlays_v1` becomes `top_parlays_v2`.
- **R5. The freeze-commit procedure is unstated** (:5). `freeze_commit` needs a commit whose subject is
  `prereg(PARLAY_JOINT_CAL): <sha>` and whose copy of the document hashes to `<sha>`. `require_harness_pinned` compares the
  disk files to that commit's blobs. The status line ("DRAFT … Not frozen") is above the marker, so editing it in the freeze
  commit changes the hash and the freeze fails. The LINE_CALIBRATION precedent (06d76e3) was an empty commit with the
  document left as DRAFT. Fix: state at :5, "merge the revised branch into main; then
  `git commit --allow-empty -m "prereg(PARLAY_JOINT_CAL): <sha>"`; the status line is not edited (it is inside the hash);
  every file in `HARNESS_FILES` must be in that commit's tree."

### Not blocking

- **The rehearsal departs from my spec, and that is acceptable, in fact better.** My spec, drawing outcomes from the CAL-COP
  joint, would have used the harness's own correlation estimate as the truth (circular), and that estimate needs real `y`.
  The modeler's truth is an exact latent Gaussian factor copula: same team 0.09, same player plus 0.10, opponents 0, with the
  production pmf exact. That tests the PIT, the pair estimator, the D R D sign and the orthant end to end against a known
  dependence, under estimation error. Results under the null:
  - The null gaps have mixed signs: DraftKings 2024 -0.0072 / -0.0097 / +0.0032 / -0.0050 and Pinnacle -0.0029 /
    +0.0135 / -0.0110 / -0.0033, n 3,239-4,238 tickets per cell. There is no systematic bias.
  - D1 is within ±0.0005 in every cell, so CAL DOES NOT HELP is correct.
  - No OVERCONFIDENT or UNDERCONFIDENT word appears.

  One weakness: under the null, CAL is close to the identity. The positive control (slope 0.5) covers a non-trivial CAL. Its
  cross 2-leg gap of -0.0169 [-0.0327, -0.0003] (n 3,658) is one cell at about 2 se in one draw; the other three are
  -0.0064 / +0.0005 / -0.0028. Optional: a second null seed, `default_rng([2026, 12])`, as a descriptive row.
- **The pin covers every file that moves a word.** Every number-moving function the harness calls is in the eight pinned
  files: its own construction, LINE_CALIBRATION's `walk_forward`, `attach_grid`, `read_grid`, `ll` and `fit_logistic`, the
  `alt_harness_common` bootstrap, p and `bh_q` (so there is no `bh_adjust`-type gap: BH is `ahc.bh_q`), and
  `copula.PairCorr` and `nearest_correlation`. Unpinned transitive imports are import-time only and move no number here:
  `nba.eval.context_residual_eval.bh_adjust` and `model_clv_eval.Boot` (unused), `f11_lineup_context`,
  `minutes_hazard_eval` and `odds_history_eval` (imported by `model_clv_eval`), and `nba.parlay.legs` (a type). What the pin
  does not cover is data and libraries (R2, R4).
- **The runtime claim is optimistic but bounded.** I timed the harness's own `orthant` at 1e-6 on 3-leg tickets with
  q in [0.50, 0.62] and mixed sides, 40 draws per level:

  | Correlation strength | Median per call | p90 per call |
  |---|---|---|
  | \|rho\| ~0.03 | 1.7 ms | 3.8 ms |
  | ~0.08 | 7.3 ms | 13.7 ms |
  | ~0.15 | 10.6 ms | 20.1 ms |
  | ~0.3 | 19.5 ms | 26.4 ms |
  | ~0.5 | 16.3 ms | 27.7 ms |

  At about 286,000 3-leg calls that is roughly 45-95 minutes, plus 2-leg calls and fits. The stated "45 to 60 minutes" holds
  only if the real correlations are as weak as the rehearsal's. The 3-hour worst case (:47) is the honest bound. Either way
  it is a background job under `heavy.lock`.
- **Guards:** the harness re-checks season (`guard_season`) and lead time (`guard_lead`) with `raise`, so `python -O`
  stripping the gate's `assert`s is covered. `guard_fit_before` in `GameStats.paircorr` and `j_arm` is true by construction
  (the selection is already `< block`). It is harmless, and the planted-future test checks the outcome (`PairCorr`
  unchanged), which is the right test.

## Commands and numbers (re-check)

| Check | Command | Result |
|---|---|---|
| Rule hash | `git show origin/agent/modeler-parlay-joint-cal-2:docs/prereg/PARLAY_JOINT_CAL.md \| awk '/^=== RESULTS BELOW ===$/{exit} {print}' \| shasum -a 256` | 59f3b08d…974f, exit 0 |
| `alt_harness_common.py` pin | `shasum -a 256` on the branch and on main | 44fd28db… on both, equal to :172 |
| Tests | `pytest -q --no-cov tests/research/eval/test_parlay_joint_cal_eval.py tests/research/eval/test_parlay_joint_cal_gate.py` | 15 passed, exit 0 |
| Lint | `ruff check` / `ruff format --check` / `mypy` on the harness, the rule module and the tests | 0 / 0 / 0 |
| Rehearsal outputs | read `reports/prereg_parlay_joint_cal/rehearsal*/` | as above; preflight 5 of 5 ok; verdict pass; stride 20 |
| Orthant timing | harness `orthant` at 5 correlation levels, 40 draws each | table above |
| Freeze precedent | `git show --stat 06d76e3` | empty commit, document left as DRAFT |

## Biggest remaining risk

A `score` run on drifted inputs. `model_rows` regenerated since the pin would silently change the same-game copula, and no
check would fire because the CAL preflight does not touch `y`. R2 closes it.

## Not verified

- The full-density TOP runtime and the real correlation strengths (they need real `y`).
- Whether the scoring job fits in the session watchdog (it does not; it must be a background job).
- The live harness, which is still written after the freeze, as stated.

---

# Diff-only re-check of R1-R5 (revision 3), 2026-10-15

Candidate: `origin/agent/modeler-parlay-joint-cal-2` at 63af759 (diff against 2a9bf05). Rule hash recomputed:
`a02ae78eb3b7e9474887d96b8861bb870bf2b23e78eb67296a89607b6d9982d9`, which matches. No real outcome was read: the only
`score` call I made used an unfrozen sha and was refused at the first line.

Verdict: APPROVED

| Item | Fix in 63af759 | Checked by | Status |
|---|---|---|---|
| R1 order | `run()` in score mode: freeze and harness pin; then input, gate, `alt_harness_common`, `select.json` and version checks; `pf.require()`; then `load_cal` (leg labels); CAL and A0 check; `pf.require()`; only then `read_rows(y)` and `build_game_stats` | reading the diff, and the new test `test_score_reads_no_label_before_the_hash_checks` (fails every pin, asserts neither `read_rows` nor `load_cal` was called) | done |
| R2 input pins | `INPUT_PINS`: pairs_2023, pairs_2024, model_rows_2023, model_rows_2024 and gate.json, checked before any label | recomputed against the real files: all 5 equal | done |
| R3 A0 | A0 = mean `lc.ll(clip(p_model, EPS))` against `select.json` to 1e-9, both books; `p_model` added to the frame | same formula I reproduced exactly (difference 0.0) in the first review | done |
| R4 stale text | :5 rewritten; :6 and :12 cite v2; :47 states the runtime (45-95 min, 3 h worst case, background) and drops the checkpoint claim ("writes `<book>.json` when a book completes"); :171 states the version pins (numpy 2.5.3, polars 1.44.2, scipy 1.18.1), which the harness records and enforces in score mode; :183 gives the real command and the in-`score` order | text diff; `versions()` equals the pins in this venv | done |
| R5 freeze procedure | :5: merge, then an empty `prereg(PARLAY_JOINT_CAL): <sha>` commit; the status line is not edited; `HARNESS_FILES` must be in that tree | matches `rule.freeze_commit` / `require_harness_pinned` and the LC precedent 06d76e3 | done |

Commands (run on a detached checkout of 63af759, since removed):
- `pytest -q --no-cov tests/research/eval/test_parlay_joint_cal_eval.py tests/research/eval/test_parlay_joint_cal_gate.py`: 16 passed, exit 0.
- `ruff check`, `ruff format --check`, `mypy` on the harness, the pin module and the tests: 0 / 0 / 0.
- `ev.run(<main>, real=True, …, sha="a"*64)`: refused with "text above the marker hashes to a02ae78e, not aaa…" before any read.
- `git diff --stat 63af759 main --` on the six pinned helper files (`alt_harness_common`, `line_calibration_eval`, `line_calibration_rule`, `model_clv_eval`, `nba/parlay/copula.py`, the gate): empty, so merging changes none of them.
- Smoke rehearsal `reports/prereg_parlay_joint_cal/rehearsal_smoke_r2/report.json` (stride 40, synthetic): 10 of 10 preflight checks ok, including the 5 new input pins and the versions. Verdict pass: T1 CALIBRATED and T2 CAL DOES NOT HELP in all four cells, T3 INCONCLUSIVE.

New contradictions: none that moves a number. One stale label remains. The status line (:3) still reads "DRAFT, revision 2, written … 01:15:14", and the header bullets describe revisions 1 and 2 only. Git history (63af759) is the record of revision 3. Leave it, or fix it before the freeze and re-hash; a one-line provenance edit needs no further review.

## Exact freeze steps (main session)

1. Merge `origin/agent/modeler-parlay-joint-cal-2` at 63af759 into `main` without editing `docs/prereg/PARLAY_JOINT_CAL.md` above the marker or any file in `HARNESS_FILES`. That is the default; the optional status-line fix above would change the sha to be used in steps 2 and 3.
2. On `main`, run `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PARLAY_JOINT_CAL.md | shasum -a 256` and confirm
   `a02ae78eb3b7e9474887d96b8861bb870bf2b23e78eb67296a89607b6d9982d9`. Confirm all eight `HARNESS_FILES` exist in the tree.
3. `git commit --allow-empty -m "prereg(PARLAY_JOINT_CAL): a02ae78eb3b7e9474887d96b8861bb870bf2b23e78eb67296a89607b6d9982d9"`, then push.
4. Score, as a background job, from the main checkout:
   `.venv/bin/python -W ignore -m research.eval.parlay_joint_cal_eval score --data-root /Users/devin/Downloads/nba-prediction --sha a02ae78eb3b7e9474887d96b8861bb870bf2b23e78eb67296a89607b6d9982d9`
   It takes 45-95 minutes, 3 hours at worst, and holds `heavy.lock`. Season 2025 is never loaded, so no holdout row is needed.
