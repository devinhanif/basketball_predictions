# MEDIAN_BIAS pre-freeze review (adversary), 2026-10-15

Reviewed: `docs/prereg/MEDIAN_BIAS.md` draft revision 1 (main at 1a9d438, draft commit 4923f80), `docs/research/median_bias_diagnosis_2026-10-15.md`,
`research/scratch/median_bias_diag.py`, `research/eval/median_bias_{eval,gate,rule}.py`, `tests/eval/test_median_bias_harness.py`,
`reports/prereg_median_bias/` (gate.json, rehearsal/oracle, rehearsal/a0), `reports/median_bias_diag_2026-10-15.json`, and for the
data-version question `reports/odds_history/model_rows_{2023,2024}*.{parquet,json}`, `reports/lower_tail/components_*.npz`,
`nba/eval/lower_tail_eval.py`, `nba/eval/context_residual_eval.py`, `nba/props/context_residual.py`, `nba/features/game_tipoff.py`.
Clock: `date` read `Thu Oct 15 08:36:07 CDT 2026` during the attacks. Season 2025 was never loaded. No DuckDB write connection was opened
(one read-only connection, `season <= 2024` in SQL). **No candidate quantile met a real label.** Real 2023-24 labels were read for A0 only,
the arm the draft has already exposed: the re-derivation, the composition test, the G4 reproduction and the plug-in forecast's A0 term.
Every CAND number below comes from synthetic outcomes drawn on the real pinned components.

Verdict: APPROVED WITH EDITS

Draft text 6f4f8648…f908 cannot be frozen as written. Items 1 to 7 change the harness (G1, the G4 order, version pins, input path, a CRPS-harm flag), the
disclosure and the commit procedure, so the frozen text gets a new sha and the harness a new pin. Once they are in, a diff-only recheck
is enough (about 15 minutes). The diagnosis holds and the data version is resolved, so nothing here asks for a new diagnosis.

**Top finding.** Guard G1 cannot fire on a null. Its within-block outcome shuffle makes `y` independent of `c` and `s`, not just of the
cell, so the null gain centres at **-0.82 to -1.11 pp (sd 0.03-0.04) under every truth tried (9 truths, 80 runs)**. The pass line is therefore about -0.96 pp:
any candidate that makes stratum H worse by less than 1 pp passes. It does so on synthetic data. Under the `mix50` truth, CAND moves
the pooled 2024 H gap from +0.96 to -1.36 (gain -0.40 pp) and G1 **passes**. The CRPS half of G1 has the same problem: the null
dCRPS sits at about +0.013, thirteen times the P2 harm limit. A calibration-side shuffle fixes it. Permute the calibration rows'
`min10` within each block, rebuild CAND, keep the outcomes. That null centres at **about 0 (per-truth means -0.005 to +0.016 pp, 18 runs × 8 draws)**,
so it tests what G1 claims to test: that the gain needs the cell structure of the calibration pool.

**Second finding (a forecast, not a score).** The 2023-24 run is close to determined before it starts. Take A0's real cell gaps,
which are already seen, and add CAND's label-free shift per cell from the committed `oracle` rehearsal. The plug-in predicts:
- pooled 2024 H: +1.71 to **-0.48**;
- P1a, P1b, P1c and P3 pass;
- **P5 fails in fg3m lt15 2024** (A0 -0.52, CAND about +1.48, |CAND| - |A0| = +0.96) and narrowly in reb 15to25 (+0.55).

So the most likely word is **PARTIAL**, not the drafter's MEDIAN_FIXED at 0.55. I do **not** recommend loosening P5. That would be
a rule fitted to exposed numbers. Record the forecast as a prior (drafted rows) and let the frozen rule speak.

## Model class and playbook check

Class: "Quantile and distribution layers" (a per-cell empirical conformal map of standardized residuals), on a "GBM context-residual"
base (A0: recency mean + LightGBM residual + scale head, walk-forward month blocks). No neural or market class applies.

1. **Information or capacity?** Neither. `min10` is already an A0 input, and the map has no fitted parameter. The draft says so
   (§3, §11.1). The part of the bias that *is* information (the book's role expectation) is left out on purpose, and its expected
   residual is stated (§2). Correct framing.
2. **Enough data for this class?** Yes. The smallest (block, cell) pool is 854 rows against MIN_BIN 200, the fallback share is 0.0,
   and stratum H has 12,334-12,489 rows per stat-season (`check` re-run, exit 0).
3. **Test valid?** Partly.
   - Valid: integer support for every arm, same rows, same calibration window, MIDMED zero for a calibrated integer forecast
     (unit-tested; re-derived below), paired clustered bootstrap.
   - Invalid as written: G1 (item 1). G4 is not enforced before CAND is scored (item 2).
   - Exposure is understated: CAND's lower half and its median location have already met real labels (item 3).
4. **Lineage and inherited guards.**
   - LOWER_TAIL `mondrian_minutes` *is* CAND below tau 0.5 (same edges and MIN_BIN). It was scored on both seasons: T138, T141,
     T144, T147 (2023) and T150, T153, T156, T159 (2024), integer CRPS -0.0007 to -0.0032.
   - PARLAY_JOINT_CAL's `PINNED_VERSIONS` check (`research/eval/parlay_joint_cal_eval.py:67,875`) was not inherited (item 4).
   - The ALT_RUNG_CAL pin pattern was inherited, without `--allow-empty` (item 5).
5. **What it scores against:** A0 on the median gap (MIDMED) and integer CRPS. No price, which is correct.

## Process checks

| # | check | command / evidence | result | pass |
|---|---|---|---|---|
| A | sha of the text above the marker | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MEDIAN_BIAS.md \| shasum -a 256` (exit 0) | `6f4f864879fdd42cdbd9d6e881d25209100b8ca48f910d837c655f4c45b3f908`, equal to the caller's 6f4f8648…f908 and to `median_bias_rule.frozen_sha()`. The whole-file sha is 1e2df0b4… (not the rule sha) | yes |
| B | every input pin | `shasum -a 256 reports/lower_tail/components_{pts,reb,ast,fg3m}.npz data/ops/model_clv_pairs/pairs_202{3,4}.parquet reports/prereg_median_bias/gate.json` (exit 0) | all seven equal the rule's lines 11-17 | yes |
| C | harness implements every promised output (§4, §5, §10) | read `median_bias_eval.py:268-520` | MIDMED, integer CRPS, P(Y > continuous median) for A0/CAND on DK, slices stat × cell × season × DK, the paired \|gap\| contrasts, BH per stat, P1-P5, G1-G4, ON_LINES, IMPROVES_CRPS: all present. Missing: package versions and the uv.lock sha in `results.json` (item 4) | partial (minor) |
| D | refusals and preflight run before any label | `main` `:553-567`: `require_ready` (freeze + pin) → `preflight` (sha256 + counts, no `row_y`) → `a0_pins_declared` (gate.json sha) → `a0_repro` (first `row_y` read) → `evaluate` | order right. **But a G4 mismatch does not stop the run**: it only flips the word to GUARD_FAIL after CAND has been scored on real labels (item 2) | partial |
| E | every number-moving import is inside the pin | `median_bias_eval` imports only `median_bias_gate` and `median_bias_rule` (both pinned) plus numpy. `gate.dk_population` uses pandas/pyarrow. No `nba.*` import on the run path; the copies are equal-tested against production (`test_constants_and_helpers_equal_production`, `test_a0_grid_equals_production_raw_quantiles`) | in the pin. numpy 2.5.3 / pandas 3.0.6 / pyarrow 25.0.1 neither checked nor recorded. uv.lock (sha256 97e3413e…) lists numpy twice (2.4.6, 2.5.3) | partial (item 4) |
| F | freeze and pin commits ordered, `--allow-empty` | `median_bias_rule.py:144` enforces pin-descends-from-freeze. Printed command `:190` and docstring `:14`: `git add … && git commit -m "harness(MEDIAN_BIAS): <pin>"` | order enforced. The harness files are already committed (4923f80), so that command exits 1 "nothing to commit" and no pin commit is made. The freeze commit has the same problem. Pin round-trip checked: disk blobs equal `git show HEAD:<file>` blobs (True); pin today `8fd35194…` | yes, with a fix (item 5) |
| G | each word in its own world | committed rehearsals: `oracle` → MEDIAN_FIXED, `a0` → NOT_FIXED. PARTIAL, FIXED_BUT_HARMS_CRPS and GUARD_FAIL never shown. `test_words_follow_the_frozen_rule` never drives PARTIAL through P1c, P3 or P5, nor harm + guard | my simulation reaches PARTIAL, NOT_FIXED, GUARD_FAIL and MEDIAN_FIXED on the real components (table below). FIXED_BUT_HARMS_CRPS is never reached in 76 runs (readings below; item 7). Proposed unit test `test_median_bias_words_cover.py` (4 tests, every word and every PARTIAL branch, inclusive boundaries): **4 passed, exit 0** | partial (item 6) |
| H | lint, types, tests (unpiped) | `ruff check` (4 files) exit 0; `ruff format --check` exit 0; `mypy` (3 files) exit 0; `pytest -q tests/eval/test_median_bias_harness.py` **18 passed, exit 1**: the repo-wide 70% coverage floor, not a failure; `--no-cov`: 18 passed, exit 0 | | yes |
| I | label-free preflight on the real inputs | `.venv/bin/python -W ignore -m research.eval.median_bias_eval check --data-root /Users/devin/Downloads/nba-prediction` exit 0 at 08:36:04 | inputs and every count equal; min pool 854; fallback 0.0 | yes |
| J | G4 reproduces on this machine (A0 only) | `gate.a0_pins` vs `gate.json:a0_pins` | max \|ΔCRPS\| **0.0** over 8 stat-seasons; DK P(Y > A0 median) 0.5362760399629903 equal, n 106,999 | yes |
| K | 2025 never touched by the harness | `load_comp` asserts `row_season.max() <= 2024` (`median_bias_gate.py:75`); the components hold 2023-24 only; `capture_audit` SQL `season <= 2024` | harness clean. **The optional regen is not** (data-version section) | yes (harness) |
| L | scope | 4923f80 touches 12 files, all named by the task (prereg, note, scratch script, gate, harness, tests, reports, one PREDICTIONS row) | no creep | yes |

## Science checks

### Is the diagnosis right? (A0 only, re-derived)

Script `<scratchpad>/a0_rederive.py`: `.venv/bin/python -I -W ignore a0_rederive.py a0_rederive.json`, exit 0, 6.4 s, 0.73 GB. It reads
the pinned components with `row_y` and builds the A0 integer grid with the harness's own `a0_grid`/`to_int`/`midmed`. Game-clustered
Poisson bootstrap, B = 1,000, seed 2026, 2,638 games.

| cut | n (stat-rows) | A0 MIDMED gap, pp [95% CI] | draft says |
|---|---|---|---|
| all | 221,280 | -0.11 [-0.36, +0.13] | -0.11 [-0.35, +0.14] |
| DK line / no line | 106,999 / 114,281 | +3.11 [+2.76, +3.45] / -3.12 [-3.48, -2.77] | +3.11 / -3.12 |
| min10 < 15 / 15-24 / 24-30 / 30+ | 51,052 / 63,824 / 47,660 / 58,744 | -3.96 / -0.77 / +1.41 / +2.73 | same |
| H pooled 2023 / 2024 | 49,956 / 49,336 | +2.81 [+2.26, +3.34] / **+1.71 [+1.20, +2.23]** | "about 1.7" |
| H 2024 pts / reb / ast / fg3m | 12,334 each | +2.88 / +1.23 / +0.93 / +1.81 | same |

The gradient re-derives to the digit. MIDMED is right by construction: `E[R] = S(m+1) + ½(S(m) - S(m+1))`
(`median_bias_eval.py:134-140`), so it is zero for any forecast calibrated at its own integer median.

**Is the DK-vs-no-line split just the minutes mix?** No. Direct standardisation: the no-line rows are reweighted to the DK rows'
stratum mix (common strata only, game-clustered B = 200).

| strata | DK gap | no-line, standardised | within-stratum DK - no-line [95% CI] | DK coverage |
|---|---|---|---|---|
| (raw, no standardisation) | +3.11 | -3.12 | +6.23 | 1.000 |
| rule cells × stat | +3.11 | -1.67 | +4.78 [+3.95, +5.57] | 1.000 |
| 1-minute min10 bins × stat | +3.12 | -1.52 | +4.64 [+3.55, +5.86] | 0.998 |
| 2-min min10 × stat × starter × level tertile | +3.11 | -1.24 | +4.35 [+2.92, +5.83] | 0.999 |
| … × prior-games bin | +3.10 | -1.16 | +4.26 [+2.87, +5.79] | 0.995 |

Minutes composition explains about a quarter of the 6.2 pp split (6.23 to 4.64). Starter, level and experience take it to 4.3. The rest
lives inside strata the model can see, which supports the draft's "book information the model lacks". The draft's own numbers
(+3.15 vs -1.46 at 30+) are right.

**One tension the draft does not state.** The real A0 H gaps are *smaller* than the pure cell mechanism implies. Under the `cell`
truth (outcomes from the row's own cell pool) A0's synthetic H gap is +3.27 (2023) / +2.29 (2024). The real ones are +2.81 / +1.71.
H is about 83% DK rows (82.6% of 99,292 H stat-rows), and the draft's story says those carry an extra *positive* information bias, so the real gap should be
*above* the cell-only gap, not below it. Two readings fit: the calibration-window cell skew (the newest 15% of training rows)
overstates the test-time cell effect, or the information term is not positive inside H. Either way, **CAND is expected to overshoot
in H** (plug-in -0.48 pp in 2024), and the sign flips in reb and ast (plug-in about -1.1). The draft's prior (§9: "+0.3 pp, 80% -0.6
to +1.2") has the wrong sign.

### Exposure: has the candidate effectively been seen?

Yes, more than the Status line says. "No candidate quantile has been scored on a real outcome" (line 3) is not accurate:
- **The lower half of CAND was scored.** `mondrian_minutes` (`nba/props/lower_tail.py:127-134`, tau < 0.5, same `MINUTES_EDGES` and
  `MIN_BIN`) is CAND for tau < 0.5. Its integer CRPS on 2023 and 2024 is in the ledger (T138/T141/T144/T147 and
  T150/T153/T156/T159; -0.0007 to -0.0032). The draft's CRPS prior (pts -0.0035) is that number.
- **CAND's median location met real labels.** `research/scratch/median_bias_diag.py:344-366` computes `cellmed`, which is exactly
  CAND's standardized tau = 0.5 point per row. It is averaged over DK and no-line rows and set against the observed median z of
  real 2023-24 outcomes. The "62% / 81% / 64% / 60%" recovery figures and the "about 1.5 pp will remain" expectation come from it.
  That is a partial score of CAND's median by population, both seasons.
- **The A0 cell gaps of both seasons plus a label-free rehearsal pin the result.** See the plug-in section next.

The exposure is tolerable because the draft already names the 2026-27 live log as the confirmatory test (§5, §9). But the text must
say what was seen, and the 2023-24 word must be read as a mechanical confirmation (item 3).

### Plug-in forecast of the run (A0 real gaps + label-free CAND shift; no CAND quantile meets a real label)

Script `<scratchpad>/plugin.py`, exit 0. For each stat × season × cell: plug-in CAND = real A0 gap + (CAND - A0) under the
committed `oracle` rehearsal. The P5 column is |plug-in CAND| - |A0| (the rule allows at most +0.5).

| season | cell (pooled over stats) | real A0 | plug-in CAND |
|---|---|---|---|
| 2023 | lt15 / 15to25 / H | -4.76 / -0.81 / +2.81 | -0.68 / +0.37 / -0.10 |
| 2024 | lt15 / 15to25 / H | -3.17 / -0.50 / +1.71 | +0.68 / -0.26 / **-0.48** |

| 2024 stat × cell | real A0 | oracle shift | plug-in CAND | P5 term |
|---|---|---|---|---|
| H pts / reb / ast / fg3m | +2.88 / +1.23 / +0.93 / +1.81 | -3.13 / -2.31 / -2.05 / -1.30 | -0.24 / **-1.08** / **-1.12** / +0.51 | -2.64 / -0.15 / +0.18 / -1.30 |
| **fg3m lt15** | -0.52 | +2.01 | +1.48 | **+0.96 (P5 fails)** |
| **reb 15to25** | -0.10 | -0.55 | -0.65 | **+0.55 (P5 fails)** |
| fg3m 15to25 | -0.14 | +0.72 | +0.58 | +0.44 |

What follows if the shift carries over:
- P1a passes (|-0.48| <= 1.0).
- P1b passes (a 1.23 pp reduction against a half-width of about 0.4).
- P1c passes, but reb and ast sit about 0.4 pp from the 1.5 cap.
- P3 passes (2023 H +2.81 → -0.10; all four stats smaller).
- **P5 fails**, so the word is **PARTIAL**.
- Per-stat words: pts and fg3m "fixed"; reb and ast not (|gap| > 1.0).

Approximation error: I estimate about ±0.3 pp per cell (the synthetic outcome density at the two medians against the real one);
not measured. That is enough to make fg3m lt15 a firm fail and reb 15to25 a coin.

### Was stratum H chosen after seeing the gaps?

Partly. The edges (15, 25) and MIN_BIN are inherited from LOWER_TAIL, so the *cell* is not tuned. Making the top cell the *primary
endpoint* came after the four-bin gradient (-3.96 … +2.73) was on the table. The stated reason (H is where line players live; 82.6% of H
stat-rows have a DK line) is sound. Is it flattering? Not in the plug-in: H 2024 lands at -0.48, against lt15 +0.68 and 15to25
-0.26 (pooled over stats). H is not where CAND looks best, and P5 polices the other cells. Verdict: disclose (item 3); no change.

### Are the thresholds reachable? Word-level power under synthetic truths

Script `<scratchpad>/power_sim.py`. It uses the harness's own `cand_grid`, `placebo_cells`, `midmed`, `crps_int`, `score`, `null_draws`
and `words` on the pinned components (`row_y` never loaded). G1 uses K = 20 null draws (the rule uses 50); G3 and G4 are set to pass
(no real labels); B = 5,000 as in the rule. 8 seeds per truth. Command, run under `data/ops/heavy.lock` (pid file written):
`.venv/bin/python -I -W ignore power_sim.py power.json cell,pooled,mix75,mix50,info05,harm,pooledH 8 20`, exit 0, about
10 s per run, 0.9 GB.

Truths (outcomes `y = max(0, round(c + s·z*))`, z* drawn per block):
- `cell`: the row's own cell pool (CAND exactly right; the harness's `oracle`).
- `pooled`: the pooled pool (A0 exactly right; the harness's `a0`).
- `mix75` / `mix50`: own cell pool with probability 0.75 / 0.50, else pooled. The test-time cell effect is 75% / 50% as strong as
  the calibration window says. `mix75` is the closest to the real A0 gaps (H 2024 +1.7).
- `info05`: `cell` plus 0.05 sd on DK-line rows (the draft's two-component story).
- `harm`: reflected cell pool, `2·median(cell pool) - draw`. Intended: CAND's median right, skew reversed. In practice the integer
  map moved the medians and CAND *gained* CRPS, so this is not a harm world (the `shiftpool` truths are).
- `pooledH`: pooled everywhere except H rows, which use the H pool.

| truth | what it means | runs | words | P1a / P1b / P1c / P2 / P3 / P5 pass | pooled H 2024, A0 → CAND (mean pp) | G1 pass | G2 pass | calibration-shuffle null mean (pp) |
|---|---|---|---|---|---|---|---|---|
| `cell` | CAND exactly right | 8 | MEDIAN_FIXED 7, PARTIAL 1 | 8 / 8 / 8 / 8 / 8 / 7 | +2.29 → +0.03 | 8 | 8 | +0.012 |
| `pooled` | A0 exactly right | 8 | NOT_FIXED 8 | 0 / 0 / 0 / 0 / 0 / 0 | +0.01 → -2.37 | 0 | 8 | +0.010 |
| `mix75` | 75% strength, matches the real A0 H gap | 8 | MEDIAN_FIXED 2, PARTIAL 4, GUARD_FAIL 1 (G2), NOT_FIXED 1 | 8 / 7 / 7 / 8 / 8 / 4 | +1.71 → -0.63 | 8 | 4 | +0.003 |
| `mix50` | half strength (CAND overshoots) | 8 | NOT_FIXED 7, PARTIAL 1 | 3 / 1 / 3 / 3 / 0 / 0 | +1.21 → -1.15 | **8** | 5 | +0.002 |
| `info05` | cell + 0.05 sd on DK rows | 8 | PARTIAL 8 | 0 / 8 / 0 / 8 / 8 / 2 | +3.66 → +1.37 | 8 | 8 | +0.012 |
| `harm` | reflected skew (meant as harm; CAND *improved* CRPS, pts -0.017) | 8 | NOT_FIXED 6, PARTIAL 2 | 7 / 2 / 0 / 8 / 5 / 0 | +1.37 → -0.86 | 8 | 3 | +0.016 |
| `pooledH` | fix true in H only | 8 | PARTIAL 8 | 8 / 8 / 8 / **0** / 8 / 0 | +2.28 → +0.02 | 8 | 8 | +0.011 |
| `shiftpool` | pooled shape re-centred on the cell median (K = 10) | 4 | PARTIAL 4 | 4 / 4 / 4 / **0** / 4 / 0 | +3.08 → +0.63 | 4 | 4 | +0.003 |
| `shiftpool2` | the same at 1.6× spread (K = 10) | 4 | PARTIAL 4 | 4 / 4 / 4 / 4 / 4 / 0 | +1.55 → +0.20 | 4 | 3 | -0.005 |

Second batch command: `.venv/bin/python -I -W ignore power_sim2.py power2.json shiftpool,shiftpool2 4 10`, exit 0, under heavy.lock.

Readings:
- **The rule separates the extremes cleanly.** CAND right: MEDIAN_FIXED 7/8 [Wilson 0.53, 0.98]. A0 right: NOT_FIXED 8/8.
- **In the world that matches the real A0 gaps (`mix75`) the word is a coin**: MEDIAN_FIXED 2/8 [0.07, 0.59], PARTIAL 4/8
  [0.22, 0.78]. A PARTIAL from the real run is therefore not evidence against the mechanism.
- **PARTIAL covers four different worlds**: overshoot (`mix75`, `mix50`), a leftover information term (`info05`), a fix only in H
  (`pooledH`) and the wrong shape (`shiftpool`). The single named follow-up (the nine-cell map, minutes × centre tertile) fits
  only the `info05`-like world, with CAND's gap still the same sign as A0's (item 7).
- **FIXED_BUT_HARMS_CRPS never appears: 0 of 76 runs [0, 0.05].** Wherever P2 failed (`pooledH`, `shiftpool`), P5 failed too,
  because the same mis-shifted cells cause both. CRPS harm will surface as **PARTIAL with P2 failing**. The rule then contradicts
  itself: the kill criterion says any P2 failure closes the idea, the follow-up clause says PARTIAL earns the nine-cell map (item 7).
- **GUARD_FAIL appears only through G2** (`mix75` seed 5): CAND's net reduction was 0.77 pp and the placebo's 0.27. G1 never
  decides a word.
- The `cell` world also shows P5 tripping on noise once in 8 (pts 15to25). In a perfect world, P5 alone costs about 1 word in 8.

### Is CRPS no-harm (P2) a real guard?

It fires: under `pooled`, P2 fails in 8 of 8 runs (points +0.0022 to +0.0087, every upper limit above +0.001), and under `pooledH` and `shiftpool` in every run. It is **asymmetric by stat**, though. The 2024 dCRPS CI half-width is about
pts 0.0021, reb 0.0010, ast 0.0006, fg3m 0.0005 (stable across all 9 truths), so `hi <= +0.001` really means "pts must *improve* by about 0.001", while ast and fg3m may get slightly worse. That is
acceptable and conservative for pts. The text should say so (item 3), because "no harm" in pts is a gain requirement.

### Does the 3-sd shuffle null fire on a null?

No (top finding; item 1). Across 80 runs the G1 null gain mean is -0.82 to -1.12 pp with sd 0.03-0.04, whatever the truth. G1
passes in 72 of 80 runs and fails only when CAND worsens H by more than about 1 pp (`pooled`), where P1b already fails, so it never decides a word. Its one
non-redundant effect is the wrong one: it lets a worsening of up to about 1 pp through. The calibration-side shuffle null
(`cal_shuffle_null` in `power_sim.py`) centres at about 0 in every truth. Per-truth means run from -0.005 to +0.016 pp, and single-run means from -0.016 to +0.026 pp over 18 runs × 8 draws. G1's null sits at -0.82 to -1.11.

### Planted future, knockout, missingness

- **Planted future.** CAND reads no outcome (unit test, and `power_sim` builds it with `labels=False`). The calibration pool is
  earlier than its block by construction: `lower_tail_eval.py:112-113` (`train = game_date < start`), `:131`
  (`split_calibration(train)`). G3 only catches an exact (c, s) duplicate, so it is a weak detector. The real protection is that
  code path, which I read.
- **Knockout.** The candidate is one variable (`min10`). Its as-of logic is `_sel(pre, 10.0, "min")` over the pre-game player
  state (`nba/props/context_residual.py:476-485`), the same input A0 already uses.
- **Missingness.** `row_min10`, `cal_min10` and `cal_z` have **0 NaN** in all four stats, and every row has n_prior >= 5. Note that
  `cell_of` would put a NaN into cell 2 (H) through `searchsorted`, so a later NaN min10 would land silently in the primary stratum.
  Add an assert (item 6).
- **Lag-one-game.** Not run: it needs CAND on real labels. Queued for the post-result attack.

## DATA VERSION: which 2024 A0 is production, and why the two differ

**Cause, located (label-free, `<scratchpad>/drift2.py`, exit 0).**
- `model_rows_2024.parquet` (Oct 10 10:18, `git_sha a2e7609`) records `"components_matched": 27583, "max_abs_dc": 0.0`. It matched the
  components file *of that morning*.
- That file was then **overwritten in place** on Oct 11 15:08 (file mtimes 15:08-15:10; `lower_tail_eval collect` is its only writer). The components are untracked
  (`git status`: `??`), so the Oct 10 version no longer exists anywhere.
- Between the two runs, commit **d872dc7 (Oct 10 19:06)** "F2 neutral-site games applied (+10 games, +273 box rows, 148 report rows
  linked)" changed `nba.duckdb`. 940be24 (Oct 11 03:28, 21 orphan report rows resolved) also landed in between.
- The fingerprint matches a data backfill and nothing else:

| season / block | rows matched | share with c differing | share with s differing | share with min10 differing |
|---|---|---|---|---|
| 2023, blocks 0-8 | 27,619 / 27,619 | 0.000 | 0.000 | 0.000 |
| 2024 block 9 (Oct 2024) | 1,407 | 0.000 | 0.000 | 0.000 |
| 2024 blocks 10 / 11 / 12 (Nov-Jan) | 4,624 / 3,970 / 4,793 | 0.085 / 0.204 / 0.270 | 0.095 / 0.207 / 0.231 | 0.049 / 0.104 / 0.161 |
| 2024 blocks 13-17 (Feb-Jun 2025) | 12,789 | 1.000 | 1.000 | 0.21-0.87 |

- The 118 new rows are 10 games (0022400002, …147, …155, …206, …621, …633, …664, …837, …1229, …1230) in blocks 10-13.
- In blocks 10-12 only rows whose recency features include those games move. From block 13 the new rows sit in the mean head's
  training portion, so every forecast moves.
- Mean |Δc| in 2024: pts 0.111, reb 0.048, ast 0.034, fg3m 0.024.

**Which matches current main production?** The components, as far as can be checked without the regen:
- current DB `n_prior >= 5` played rows 27,619 / 27,701 equal the components (gate `capture_audit`);
- recency features equal the DB exactly (diagnosis: `hl10 == m_recency`, max |Δ| 0.0, 55,320 rows per stat);
- `data/schedule/raw_2023-24.parquet` and `raw_2024-25.parquet` are unchanged since Oct 8;
- the only A0-path code commit since is 4ea569a (Oct 12). It changes `ReportTriggerConfig`'s own default, but `ContextResidualConfig`
  has pinned `tip_source="real"` since 76cc177 (Oct 9, `context_residual.py:100-104`), so A0 is unaffected;
- **not verified:** values in `player_availability` for <= 2024 games and in `players_static.position` after Oct 11 15:08. `pulled_at`
  is a source time, not an ingest time. c717e17 (rookie static conform) targets 2026 rookies.

**The regen was NOT run.** It would load season-2025 rows:
- `nba/eval/context_residual_eval.py:139-142` reads `player_availability` with no season filter;
- `nba/features/game_tipoff.py:55-59` globs every `data/schedule/raw_*.parquet`, including `raw_2025-26.parquet`.

Both are dropped later by the join with <= 2024 games (`context_residual.py:415`), and no outcome is involved. So no number is
affected, but "never loads season 2025" (`lower_tail_eval.py:4`; rule §10's optional check) is false. The same path built the Oct 10
and Oct 11 components and therefore every components-based run (LOWER_TAIL, model_rows, T493-T495 inputs). Item 8 (steward).

**What the drift means.**
- *T493 LINE_CALIBRATION, T494 ALT_RUNG_CAL, T495 PARLAY_JOINT_CAL* (and T496 TAIL_LEG_CAL, frozen and run at 08:37 today; its re-check
  review says it is tied to the Oct-10 snapshot) were fitted and scored on the pre-d872dc7 2024 A0: 27,583 rows,
  10 neutral-site games missing, blocks 13-17 from a model trained without 118 rows. Their 2023 halves are identical to today's.
  Their verdicts stand as statements about the pinned object. The effect of the drift on them is not measured but small (mean |Δc|
  0.02-0.11 against s of about 1-6). No re-run is needed, and a re-run on the new version would be a new question. Add a note to each
  ledger row: "A0 2024 = model_rows 2026-10-10 (before d872dc7)".
- *The parlay CAL map* was fitted on that older A0. It is served on the live A0, which refits daily on all data, so it is always
  applied to a different A0 than it was fitted on. The Oct 10/11 drift is small next to that standing gap and is not a reason to
  refit.
- *This rule* pins the newer, current-DB version, which is the right choice. Its "reproduced 0.5363" is a match of T494's three-digit
  figure across two A0 versions, not a reproduction of T494's rows. Say so.
- *Process:* untracked inputs overwritten in place are the root cause. The pin protects this rule (a rerun of `collect` makes `run`
  refuse), but it would also make the rule unrunnable (item 6).

## Items (severity; file:line; exact fix)

1. **Blocker: G1 is not a null.**
   - Where: `research/eval/median_bias_eval.py:400-430` (`null_draws`), `:494-501`; rule §5 "Guards. G1", §8 "PLACEBO and the
     within-block shuffle".
   - Fix: replace the within-(stat, block) outcome permutation with a calibration-side cell shuffle. For each of K = 50 draws
     (seed 2026 + draw), permute `cal_min10` within each `cal_blk`, rebuild CAND with `cand_grid`, and score it on the same 2024
     outcomes.
   - Pass rule: `obs_gain > null_mean + 3·null_sd` and `obs_dcrps < null_dcrps_mean - 3·null_dcrps_sd`.
   - Text: rewrite the G1 bullet to say "the gain needs the calibration pool's cell structure". Keep the old shuffle only as a
     descriptive field, never in the word.
   - Add a unit test: on a planted world where cells share one pool, the new null's mean is within 0.1 pp of 0.
2. **Major: G4 does not stop the run.**
   - Where: `median_bias_eval.py:566-567`.
   - Fix: compute `rep = a0_repro(...)` and `raise RuntimeError` if `not rep["pass"]` *before* `evaluate(...)`. A0 mismatch means
     wrong inputs or libraries, and CAND must not then be scored on real labels.
   - Text: §5 G4 should say "refusal before any candidate is built".
3. **Major: exposure and expectations misstated.**
   - Where: `docs/prereg/MEDIAN_BIAS.md:3` (Status), §9 header and P1a row, §8 "CRPS no-harm".
   - Replace line 3's "No candidate quantile has been scored on a real outcome" with: "CAND's lower half (tau < 0.5) is LOWER_TAIL
     `mondrian_minutes`, scored on integer CRPS in both seasons (T138-T159); CAND's standardized median was compared with real
     2023-24 outcomes by DK population in the diagnosis (`median_bias_diag.py:344-366`); the 2023-24 word is a mechanical
     confirmation and the 2026-27 live log is the test."
   - In §9, either change the P1a prior's centre to the plug-in (-0.5 pp, overshoot) or add a row "plug-in forecast (adversary): H
     2024 -0.48; P5 fails in fg3m lt15", so the predictions ledger scores both.
   - In §8, add: "for pts, `hi <= +0.001` requires a point gain of about 0.001 (CI half-width about 0.0021)".
   - Also state that 0.5363 equals T494's value on a different A0 version.
4. **Major: library versions unpinned.**
   - Where: `median_bias_eval.py` (no version check), rule pins lines 10-18.
   - Fix: add `PINNED_VERSIONS = {"numpy": "2.5.3", "pandas": "3.0.6", "pyarrow": "25.0.1"}`, checked in `preflight` (refuse on
     mismatch), and write `versions`, `git_head` and the uv.lock sha256 into `results.json`.
   - Add a pin line `UV_LOCK_SHA256: 97e3413ef0d45adeb613c23359b2ea834d00c908d57fc5f46418db3de4b4287d`. This is the inherited
     PARLAY_JOINT_CAL guard.
5. **Major: freeze and pin commands fail as printed.**
   - Where: `median_bias_rule.py:14` and `:190`, rule §10 "Pin procedure", rule header "How it becomes the rule".
   - Use `git commit --allow-empty -m "prereg(MEDIAN_BIAS): <sha>"` and `git commit --allow-empty -m "harness(MEDIAN_BIAS): <pin>"`.
     The rule text and harness are already in the tree. Same fix as TAIL_LEG_CAL item 7.
6. **Major: pinned inputs live in an overwrite-in-place, untracked directory.**
   - Where: `median_bias_gate.py:40` (`COMP_FILES`); `nba/eval/lower_tail_eval.py:556` (default `--out-dir` is the same
     directory).
   - Fix: copy the four npz files to a write-once path, e.g. `data/pinned/median_bias/components_<stat>.npz`, `chmod a-w`, with a
     backup line. Point `COMP_FILES` there (the sha lines do not change). Re-run the gate (gate.json records the path, so
     `GATE_SHA256` changes) and the rehearsals.
   - Also add, in the same edit:
     - `assert np.isfinite(comp["row_min10"]).all() and np.isfinite(comp["cal_min10"]).all()` in `load_comp`;
     - the proposed `test_median_bias_words_cover.py` cases in `tests/eval/test_median_bias_harness.py`;
     - two rehearsal truths (`mix50` → NOT_FIXED/PARTIAL, `harm`) to `--truth`, so each word has its own world in the committed
       rehearsal.
7. **Major: CRPS harm surfaces as PARTIAL, and the text then contradicts itself.**
   - Where: rule §5 "Kill criteria" (any P2 failure closes the idea) and "The one allowed follow-up … only if the word here is
     `PARTIAL`"; `median_bias_eval.py:470-479` (`words` puts no P2 information on PARTIAL).
   - In the simulation, every world with CRPS harm also failed P5, so it came back PARTIAL (`pooledH` 8/8, `shiftpool` 4/4), and
     FIXED_BUT_HARMS_CRPS never appeared (0/76).
   - Fix (text and `words`): add `CRPS_HARM = not all(P2)` as a flag on **every** word. State the precedence: "a PARTIAL with
     CRPS_HARM closes the idea; the follow-up is open only to a PARTIAL without CRPS_HARM whose pooled 2024 H gap keeps A0's sign
     (under-correction)". An overshoot (sign flipped) is not what the nine-cell map addresses.
   - Add the matching unit cases. This is a reading rule for words, not a threshold change.
8. **Major (steward, `nba/`, not this rule's run path): the components path loads season-2025 rows.**
   - Where: `nba/eval/context_residual_eval.py:139-142` and `nba/features/game_tipoff.py:47-59` (via
     `flagged_from_availability`, `context_residual.py:424-427`).
   - Fix: when `max_season` is set, query availability as
     `... FROM player_availability a JOIN games g USING (game_id) WHERE a.game_id IS NOT NULL AND g.season <= {max_season}`, and
     pass `attach_real_tips(rows, tips)` a tips frame restricted to `raw_<= 2024-25>` files.
   - Add a test that plants a 2025 availability row and a 2025 schedule file and asserts neither enters memory.
   - Then the optional regen can run. Whether to log a retrospective HOLDOUT_ACCESS_LOG row (2025 pre-tip statuses and tip times
     loaded and discarded by every components run, no outcomes) is Devin's call. Drafted below, not appended.
9. **Minor: G2 is fragile when CAND overshoots.**
   - Where: `median_bias_eval.py:502-508`.
   - When CAND's net reduction is 0.5-1.0 pp, a placebo reduction of 0.15-0.25 pp by chance trips the 25% ratio. That gave
     GUARD_FAIL in `mix75` seed 5.
   - No change recommended now: a change would be informed by the forecast. Disclose it as a known path to GUARD_FAIL.
10. **Minor:** own `Boot` class (`median_bias_eval.py:148-177`) instead of `truth/bootstrap.py`. That module does not exist yet
   (`nba/truth/` has `metrics.py`, `walkforward.py`). Flag only. Date-clustered CIs are a declared limit (§4).

## Freeze and pin steps (after items 1-7 and a diff-only recheck)

1. Apply items 1-7. Regenerate `gate.json` (new path) and update `GATE_SHA256`. Re-run `rehearse --truth oracle|a0|mix50|harm`.
   `ruff check`, `ruff format --check`, `mypy`, `pytest -q --no-cov tests/eval/test_median_bias_harness.py`, each unpiped with its
   exit code.
2. Commit the edits by path on the modeler branch, merge, and send me the diff for the recheck (G1, the G4 order, versions, path).
3. `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MEDIAN_BIAS.md | shasum -a 256`. Devin confirms.
4. Freeze: `git commit --allow-empty -m "prereg(MEDIAN_BIAS): <sha>"`. Push.
5. Pin: `.venv/bin/python -m research.eval.median_bias_rule pin`, then
   `git commit --allow-empty -m "harness(MEDIAN_BIAS): <pin>"` (a descendant of the freeze commit, no harness edits in between).
   Push.
6. Append the PREDICTIONS rows (drafted below) before the run.
7. Run once under `data/ops/heavy.lock` with the §10 command. Then the post-result attack: lag-one-game min10, a second placebo seed,
   and the drop-best-month replication.

## Corrected family and power note

- Family as run: one pooled primary (P1, no correction), 4 per-stat BH words (BH over 4), intersection-union for P2, P5 on 12 point
  estimates with no inference, P4 secondary, 2 informational flags. The BH family is right as stated; no family was hidden.
- Power: 95% half-widths, game-clustered: the pooled H |gap| contrast ±0.33-0.40 pp per season (rehearsal), per-stat H gap ±0.8 pp, per-cell ±1.0-1.2 pp, 2024 dCRPS pts ±0.0021, reb ±0.0010, ast ±0.0006, fg3m ±0.0005. Word level (Wilson 95%): MEDIAN_FIXED 7/8 [0.53, 0.98] when CAND is exactly right; 2/8 [0.07, 0.59] in the world matching the real A0 gaps, where PARTIAL is 4/8; NOT_FIXED 8/8 when A0 is right; FIXED_BUT_HARMS_CRPS 0/76. The run tells 'CAND right' from 'A0 right'. It does not reliably tell MEDIAN_FIXED from PARTIAL in the realistic world, so 'underpowered between the two' is the honest reading of a PARTIAL.

## Single most likely failure mode if a positive word is wrong

CAND "fixes" H by moving every high-minutes row's median by the same calibration-window amount. H is a mix of DK rows (still too low)
and no-line rows (pushed too high), so a pooled H gap near 0 can hide two opposite biases. Roughly (the A0 gap of no-line 30+ rows, -1.46 pp,
plus the H shift of about -2.2 pp under the `cell` truth), those rows go to about -3.7 pp. A MEDIAN_FIXED word would then mean "calibrated on average in H", not "calibrated for the players who
carry lines". P4 (DK population) is the check to read next to it, and it is secondary.

## What was not verified

- Whether current production's `player_availability` and `players_static.position` values for 2023-24 equal those of Oct 11 15:08
  (the regen was not run; item 8).
- The plug-in forecast assumes CAND's per-cell shift on real outcomes equals its shift on synthetic `cell` outcomes. That is
  accurate to about ±0.3 pp per cell by my estimate, not measured.
- G1 replacement null sized from 18 runs × 8 draws over 9 truths (rule: 50 draws).
- Date-clustered intervals.
- The 77 unresolved DK name keys (direction unknown; P4 only).

## Drafted rows (not appended; the main session's to append)

- **PREDICTIONS_LEDGER** (adversary, 2026-10-15, before any CAND label; NOT blind: A0 gaps and label-free shifts seen):
  `| 2026-10-15 | MEDIAN_BIAS (adversary priors after the pre-freeze review) | adversary: plug-in forecast pooled 2024 H gap CAND -0.48 pp (A0 +1.71); P(word MEDIAN_FIXED) 0.25, PARTIAL 0.45, NOT_FIXED 0.15, GUARD_FAIL 0.10, FIXED_BUT_HARMS_CRPS 0.05; P(P5 all 12 cells) 0.35 (fg3m lt15 2024 forecast +0.96); P(P1a) 0.85; P(P1c) 0.75 (reb/ast forecast about -1.1); P(per-stat fixed) pts 0.85, fg3m 0.65, reb 0.30, ast 0.30 | docs/reviews/MEDIAN_BIAS_prefreeze_2026-10-15.md | pending | |`
- **TEST_LEDGER notes** (append to the notes of T493, T494, T495, T496): "A0 2024 = `model_rows_2024.parquet` of 2026-10-10 (before d872dc7,
  +10 neutral-site games); current walk-forward A0 differs in 2024 blocks 10-17 (mean |Δc| pts 0.11); 2023 identical; verdict
  unaffected as a statement about the pinned object (MEDIAN_BIAS pre-freeze review)."
- **DECISIONS** (Devin's call): "Components-path loads of 2025 pre-tip rows (availability statuses, schedule tip times; no outcomes)
  found 2026-10-15; filter at the query (item 8). Reverse if: never; this is hygiene."
- **HOLDOUT_ACCESS_LOG** (Devin's call whether to log; drafted only): "2026-10-15 retrospective: every
  `context_residual_eval.load_inputs` / `build_game_tipoff` caller since 2026-10-08 loaded season-2025 `player_availability` rows and
  the 2025-26 schedule, then dropped them by join with <= 2024 games; no 2025 outcome, feature or score was computed. Fix: item 8."

## Appendix A: proposed word-coverage test (ran from scratch: `pytest -q --no-cov test_median_bias_words_cover.py`, 4 passed, exit 0)

To be merged into `tests/eval/test_median_bias_harness.py` by the main session, with item 7's CRPS_HARM cases added.

```python
"""Proposed addition to tests/eval/test_median_bias_harness.py: every word of the frozen rule, every PARTIAL branch, in its own world."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from research.eval import median_bias_eval as ev
from research.eval import median_bias_gate as gate

OK = {k: {"pass": True} for k in ("G1_null", "G2_placebo", "G3_leak", "G4_a0_repro")}


def season(gap_a0: float = 3.0, gap_c: float = 0.4, p: float = 0.001, dcrps: float = -0.003, hi: float = -0.001, red_hi: float | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for st in gate.STATS:
        d[st] = {
            "H": {"A0": {"point": gap_a0}, "CAND": {"point": gap_c}, "p_bh": p},
            "cells": {c: {"A0": {"point": 1.0}, "CAND": {"point": 0.5}} for c in gate.CELLS},
            "crps_int": {"CAND_minus_A0": {"point": dcrps, "lo": dcrps - 0.001, "hi": hi}},
        }
    red = abs(gap_c) - abs(gap_a0)
    d["pooled"] = {
        "H": {"A0": {"point": gap_a0}, "CAND": {"point": gap_c}, "abs_CAND_minus_A0": {"point": red, "hi": red + 0.3 if red_hi is None else red_hi}, "p_CAND_abs_not_smaller": p},
        "dk_gain_pp": {"point": 1.5, "lo": 1.3, "hi": 1.7},
    }
    return d


def sc(rep: dict[str, Any] | None = None, con: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"by_season": {"2024": rep or season(), "2023": con or season()}}


def word(s: dict[str, Any], g: dict[str, Any] = OK) -> str:
    return str(ev.words(s, g)["word"])


def test_fixed_and_harm_and_guard() -> None:
    assert word(sc()) == "MEDIAN_FIXED"
    assert word(sc(season(dcrps=0.0005, hi=0.0011))) == "FIXED_BUT_HARMS_CRPS"
    for g in ("G1_null", "G2_placebo", "G3_leak", "G4_a0_repro"):
        bad = copy.deepcopy(OK)
        bad[g]["pass"] = False
        assert word(sc(), bad) == "GUARD_FAIL"
        assert word(sc(season(dcrps=0.0005, hi=0.0011)), bad) == "GUARD_FAIL"  # harm + guard: the guard wins


def test_every_partial_branch() -> None:
    assert word(sc(season(gap_c=1.2))) == "PARTIAL"  # P1a: pooled |gap| above 1.0
    r = season()
    r["reb"]["H"]["CAND"]["point"] = 1.6  # P1c: one stat above the 1.5 cap
    assert word(sc(r)) == "PARTIAL"
    assert word(sc(con=season(red_hi=0.01))) == "PARTIAL"  # P3: 2023 pooled reduction CI crosses 0
    c = season()
    for st in ("pts", "reb"):
        c[st]["H"]["CAND"]["point"] = 3.5  # P3: only 2 of 4 stats smaller in 2023
    assert word(sc(con=c)) == "PARTIAL"
    r = season()
    r["ast"]["cells"]["lt15"] = {"A0": {"point": 0.2}, "CAND": {"point": 0.8}}  # P5: a cell worse by 0.6 pp
    assert word(sc(r)) == "PARTIAL"


def test_not_fixed_branches_and_flags() -> None:
    assert word(sc(season(p=0.06))) == "NOT_FIXED"  # reduction not significant
    assert word(sc(season(gap_a0=0.8, gap_c=0.4))) == "NOT_FIXED"  # reduction 0.4 < 0.5
    bad = copy.deepcopy(OK)
    bad["G1_null"]["pass"] = False
    assert word(sc(season(p=0.5)), bad) == "NOT_FIXED"
    w = ev.words(sc(season(gap_c=1.2)), OK)
    assert w["word"] == "PARTIAL" and not w["ON_LINES"] and not w["IMPROVES_CRPS"]  # flags only with MEDIAN_FIXED


def test_boundaries_are_inclusive_as_written() -> None:
    assert word(sc(season(gap_c=1.0))) == "MEDIAN_FIXED"  # P1a: <= 1.0
    assert word(sc(season(hi=0.001))) == "MEDIAN_FIXED"  # P2: <= +0.001
    assert word(sc(season(p=0.05))) == "MEDIAN_FIXED"  # P1b: p <= 0.05
    assert word(sc(season(gap_a0=0.9, gap_c=0.4))) == "MEDIAN_FIXED"  # reduction exactly 0.5


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-cov", "-p", "no:cacheprovider"]))
```

## Appendix B: proposed G1 null (item 1), as run in the simulation

```python
def cal_shuffle_null(comps, arms, season, k, seed):
    """Permute the CALIBRATION rows' min10 within each block, rebuild CAND, score it on the same outcomes.
    The pooled-H |gap| reduction under this null centres at about 0 (9 truths, per-truth means -0.005 to +0.016 pp)."""
    rng = np.random.default_rng(seed)
    gains = np.empty(k)
    for i in range(k):
        s0 = sc = cnt = 0.0
        for st in STATS:
            comp = dict(comps[st])
            cm10 = comp["cal_min10"].copy()
            for b in np.unique(comp["cal_blk"]):
                ix = np.flatnonzero(comp["cal_blk"] == b)
                cm10[ix] = rng.permutation(cm10[ix])
            comp["cal_min10"] = cm10
            a = arms[st]
            lo = np.flatnonzero(a["season"] == season)
            H = a["cell"][lo] == H_CELL
            qn = to_int(cand_grid(comp)[lo])
            y = a["y"][lo]
            s0 += float(midmed(a["qi"]["A0"][lo], y)[H].sum())
            sc += float(midmed(qn, y)[H].sum())
            cnt += float(H.sum())
        gains[i] = 100.0 * (abs(s0 / cnt) - abs(sc / cnt))
    return gains
```

The CRPS half is computed the same way: the mean over stats of `crps_int(qn, y) - crps_int(qi_A0, y)`.

## Attack scripts (session scratchpad, not in the repo)

`a0_rederive.py` (A0-only re-derivation and composition), `drift.py` / `drift2.py` (version localisation, label-free), `plugin.py`
(the plug-in forecast: A0 labels plus the committed rehearsal), `power_sim.py` / `power_sim2.py` (synthetic truths), `summ.py`,
`test_median_bias_words_cover.py`. Results: `a0_rederive.json`, `power.json`, `power2.json`, `power.log`, `power2.log`.

## Re-check of revision 2 (diff only), 2026-10-15 09:03 CDT

Reviewed: `origin/agent/modeler-median-bias-2` at 24d5820 (diff against a4a0670), from a detached worktree. Season 2025 was not loaded, no
real label was read, and no DuckDB file was opened.

Verdict: APPROVED

| check | command / evidence | result | pass |
|---|---|---|---|
| rule sha | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MEDIAN_BIAS.md \| shasum -a 256` (exit 0) | `1313805518c13682d1e82931f0b7a112a482967213729e044bdb97555f6383eb`, equal to the caller's 13138055…83eb | yes |
| harness pin | `.venv/bin/python -m research.eval.median_bias_rule pin` (exit 0) | `fd8b91c83ebfd755f6ef797b489dab19d50e401be261f374b8b00c82def7028b`, equal to the provisional fd8b91c8; the printed command carries `--allow-empty` | yes |
| input pins | `shasum -a 256 data/pinned/median_bias/*.npz uv.lock reports/prereg_median_bias/gate.json` | components unchanged (same four shas as rev 1, now at the read-only `-r--r--r--` copies under the git-ignored `data/`); `GATE_SHA256 62e5cce5…` and `UV_LOCK_SHA256 97e3413e…` equal | yes |
| lint / types / tests (unpiped) | `ruff check` (4 files) exit 0; `ruff format --check` exit 0; `mypy` (3 files) exit 0; `pytest -q --no-cov tests/eval/test_median_bias_harness.py` **27 passed, exit 0** | the new tests cover: the null centred near 0, the null removing a real cell gain, the mix50/harm truths, CRPS_HARM and FOLLOWUP precedence, the G4 refusal before CAND, the version/lock refusal, and Appendix A | yes |
| label-free preflight | `… median_bias_eval check --data-root /Users/devin/Downloads/nba-prediction` exit 0 at 09:03:26 | counts equal; versions numpy 2.5.3 / pandas 3.0.6 / pyarrow 25.0.1 recorded; lock sha checked | yes |

**Items 1-7, as applied.**
1. **G1.** `cal_shuffle_null` (`median_bias_eval.py`) permutes `cal_min10` within each block on the 2024 rows and their pools; row
   order is kept by `report_subset`. In the committed rehearsals the null now sits at -0.006 to -0.026 pp (sd about 0.10), against
   about -1.1 pp for the old shuffle, which is kept as a descriptive field outside every word.
   - It fires where it should: `a0` (observed -2.01) and `mix50` (observed gain +0.32 just clears the gain line of about +0.30, and the
     CRPS half fails: +0.0001 against a required < about -0.0001).
   - It passes `oracle` (+2.19 against about +0.27). **Fixed.**
   - Note: the CRPS half asks only that CAND be no worse than a random-cell map (null dCRPS +0.0003, sd 0.0001-0.0002), not for a
     real CRPS gain. That is the right size.
2. **G4.** `main` raises before `evaluate` when `a0_repro` fails, and a unit test plants the failure. **Fixed.**
3. **Exposure, plug-in and P2 asymmetry.** All in the Status line, §8 and §9. The rev-1 priors are kept and the revision is labelled
   "revised after seeing a label-derived forecast". **Fixed.**
4. **Versions and lock.** Pinned and refused in the preflight, and recorded in `results.json`. **Fixed.**
5. **`--allow-empty`.** In both commands, the docstring and §10. **Fixed.**
6. **Inputs.** Write-once components with the NaN assert, the words tests merged, and `mix50`/`harm` rehearsals committed. **Fixed.**
7. **CRPS_HARM and FOLLOWUP_OPEN.** Implemented as specified. §5 states that PARTIAL plus harm closes the idea and that overshoot
   earns nothing. **Fixed.**

**`harm` defined as my `pooledH` world: acceptable.** It is the world that shows PARTIAL + CRPS_HARM closing the idea, which is the
practical CRPS-harm path (P2 fails in pts and reb, P5 holds in 7 of 12 cells, FOLLOWUP_OPEN false). The text says so. It also says
plainly that FIXED_BUT_HARMS_CRPS and GUARD_FAIL are exercised only by unit tests, which matches the simulation (0 of 76 natural
runs reach FIXED_BUT_HARMS_CRPS). The rehearsal's H numbers are identical to `oracle`'s (2.29 → 0.09) because H draws from the same
pool with the same seed. That is expected, not a bug.

**New contradictions looked for: none blocking.** Minor, no change needed: when CAND's pooled H gap is exactly 0, `FOLLOWUP_OPEN` is
false (the sign product is 0). That only arises for a PARTIAL driven by P1c, P3 or P5 with a closed gap, and the nine-cell map would
not address that case anyway.

### Freeze and pin steps
1. Merge `agent/modeler-median-bias-2` (24d5820) into `main`, and push.
2. From the main checkout, `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MEDIAN_BIAS.md | shasum -a 256` must print
   `1313805518c13682d1e82931f0b7a112a482967213729e044bdb97555f6383eb`. Devin confirms.
3. `git commit --allow-empty -m "prereg(MEDIAN_BIAS): 1313805518c13682d1e82931f0b7a112a482967213729e044bdb97555f6383eb"`, then push.
4. `.venv/bin/python -m research.eval.median_bias_rule pin` must print
   `pin fd8b91c83ebfd755f6ef797b489dab19d50e401be261f374b8b00c82def7028b`. Then
   `git commit --allow-empty -m "harness(MEDIAN_BIAS): fd8b91c83ebfd755f6ef797b489dab19d50e401be261f374b8b00c82def7028b"`, with no
   harness edit after step 3, then push.
5. Confirm the rev-2 PREDICTIONS row is on main, then run once:
   `until mkdir data/ops/heavy.lock 2>/dev/null; do sleep 120; done; trap 'rmdir data/ops/heavy.lock' EXIT; .venv/bin/python -W ignore -m research.eval.median_bias_eval run --data-root /Users/devin/Downloads/nba-prediction --sha 1313805518c13682d1e82931f0b7a112a482967213729e044bdb97555f6383eb`.
6. Post-result attack: lag-one-game `min10`, a second placebo seed, and drop the best month. Compare the word with the plug-in
   forecast (PARTIAL via P5 in fg3m lt15).
