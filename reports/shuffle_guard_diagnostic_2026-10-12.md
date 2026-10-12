# Shuffle-guard diagnostic: why did RECENT_ROLE_FULL's guard fire on 2025?

Written 2026-10-12 (local). Descriptive diagnostic approved by Devin; not a rule, no claim, no verdict changes. Seasons 2023 and 2024 only (2025 was not loaded; the 2025 figure used below is the already-logged T473 number). Machine-readable: `reports/shuffle_guard_diagnostic_2026-10-12.json`. Code: `research/eval/shuffle_guard_diagnostic.py` (fits, cache in `data/ops/shuffle_guard_cache`, git-ignored) and `research/eval/shuffle_guard_analyze.py` (cache-only analysis). Stat: pts (the only stat Stage 2 carried).

## Answer in plain words

The guard construction is a noisy test, not a biased one. With labels permuted, SHUF_FULL minus SHUF_CTRL has a permutation mean near zero (-0.0004 in 2023, -0.0001 in 2024, pooled -0.00026, not distinguishable from 0) and a standard deviation across permutation seeds of 0.0017 to 0.0028, which is as large as or larger than the game-clustered bootstrap CI half-width (about 0.002) that the guard treats as the whole uncertainty. The bootstrap CI covers only test-row sampling; it ignores the fit noise of two different gradient-boosted models trained on noise. So the rule "delta <= -0.001 with CI upper < 0" fires on 19% of null permutations (9 of 48, Wilson 95% [10%, 33%]), and the mirror-image positive fire happens on another 10% (5 of 48). Total false-alarm rate about 29% against a nominal 5%. The observed 2025 value of -0.0021 sits at z about -0.6 (2023 null) to -1.2 (2024 null), the 12th to 27th percentile of the null, roughly the 20th. It is consistent with chance. The guard firing on 2025 is not evidence that FULL's real gain is leakage. It is also not evidence that the gain is clean: it says only that this guard cannot discriminate at this size.

This does not change T473 (the verdict is final and was frozen under the rule as written). It says the frozen guard, as a fixed -0.001 threshold, was miscalibrated for this construction; the placebo guard (FULL minus PL_FULL = -0.0112 on 2025) was the discriminating one.

## 1. Fire rate under the null (SHUF_FULL minus SHUF_CTRL, pts, integer CRPS)

Single-season walks exactly as Stage 2 (month blocks, block index from 0, `default_rng([seed, block])` shared by the heads residual permutation and the pi flag permutation). Validation: season 2024, seed 0 gives +0.0037, identical to the logged rehearsal. (Stage 1's -0.0004 most likely came from its two-season walk, where the 2024 blocks sit at later indices and so get different permutations; I did not verify this against the Stage 1 code path.) 24 seeds per season, each a fresh set of four fits (CTRL and FULL heads, CTRL and FULL pi). Per-seed CI = game-clustered percentile bootstrap, 2000 draws, seed 0 (the repo's `_ci`).

| season | n rows / games | seeds | mean delta | sd across seeds | min / max | mean CI half-width | fire (delta <= -0.001 and CI hi < 0) | opposite fire (delta >= +0.001, CI lo > 0) | share delta <= -0.001 |
|---|---|---|---|---|---|---|---|---|---|
| 2023 | 27,619 / 1,318 | 24 | -0.00043 (se 0.00056) | 0.00276 | -0.0061 / +0.0068 | 0.00255 | 4/24 = 16.7% | 2/24 | 45.8% |
| 2024 | 27,701 / 1,320 | 24 | -0.00010 (se 0.00035) | 0.00173 | -0.0033 / +0.0037 | 0.00192 | 5/24 = 20.8% | 3/24 | 33.3% |
| pooled | 55,320 / 2,638 | 48 | -0.00026 (se 0.00033) | 0.00229 | -0.0061 / +0.0068 | 0.00224 | 9/48 = 18.8% | 5/48 = 10.4% | 39.6% |

Estimated true fire probability for a 2025-sized test (2025 CI half-width 0.00149, narrower than the 2023/24 average because the 2025 fit happened to be less noisy): 0.21 to 0.35 depending on which season's null and the normal vs empirical estimate (empirical 0.29; normal with the pooled mean and sd 0.30; a zero-mean version 0.26). Working figure: about 20 to 30% per season, i.e. the guard would fire on roughly one honest shuffled pipeline in four.

Where the observed 2025 value sits: z = -0.60 in the 2023 null, -1.15 in the 2024 null; one-sided null probability of a delta at or below -0.0021 is 0.27 (2023), 0.12 (2024), 0.21 (pooled).

Mean across seeds: pooled -0.00026, se 0.00033 (|t| 0.8); per season 0.8 and 0.3 standard errors. A structural non-zero mean is not detected; if one exists it is smaller than about 0.0007 (2 se) and about a seventh of the permutation sd.

## 2. Component decomposition (same four fits, 2x2 of heads and pi)

heads-only = (shuffled FULL heads, CTRL pi) minus (CTRL, CTRL); pi-only = (CTRL heads, FULL pi) minus (CTRL, CTRL).

| season | contrast | mean | sd across seeds | fire share |
|---|---|---|---|---|
| 2023 | both (the guard) | -0.00043 | 0.00276 | 16.7% |
| 2023 | heads only | -0.00034 | 0.00244 | 12.5% |
| 2023 | pi only | -0.00019 | 0.00076 | 16.7% |
| 2024 | both | -0.00010 | 0.00173 | 20.8% |
| 2024 | heads only | -0.00009 | 0.00141 | 12.5% |
| 2024 | pi only | -0.00003 | 0.00039 | 0.0% |

The label-free difference lives in the heads: they carry about 78% (2023) and 67% (2024) of the variance of the guard statistic (sd squared), against under 10% for pi alone. Neither component has a mean distinguishable from zero. Pi alone still fires 17% of the time in 2023 because its sd (0.0008) is close to the -0.001 tolerance.

## 3. Mechanism: can the five columns change the forecast in a way CRPS rewards when labels are permuted?

No stable way. Evidence, all averaged over the seeds unless noted:

- Mixture weight: the pi trained on permuted flags learns the marginal short rate and nothing else. Mean pi is 0.0942 (CTRL) and 0.0942 (FULL) in 2023, 0.0996 and 0.0997 in 2024, against an observed short rate of 0.106 and 0.102. The correlation of (pi_FULL - pi_CTRL) with the real short flag is +0.002 (2023) and -0.001 (2024); with the real production pi, +0.002 and -0.005. The extra columns add only noise to pi (sd of pi 0.022 vs 0.023 in 2023; 0.0164 vs 0.0162 in 2024).
- Heads and scale: mean scale 4.704 vs 4.701 (2023), 4.665 vs 4.666 (2024); mean shift in the centre c_FULL - c_CTRL is -0.002 and +0.001 points; its correlation with the real residual y minus c_CTRL is +0.03 and +0.02 (tiny; the real residual is not the training target, the centre is the real baseline m plus a noise-fit offset).
- w_q is not retrained in these arms (taken from production's `collect_pi` in all arms), so it cannot be the channel.
- Location of the difference (seed-averaged, per-row CRPS, guard contrast), 2023 / 2024: min<5 rows +0.0004 / +0.0009 (positive, i.e. the opposite of the story that short-minutes rows carry a free gain); bench -0.0007 / -0.0003; starters -0.0001 / +0.0002; short-flag rows +0.0011 / +0.0006; by `wl_min_last`: 0-5 -0.0004 / +0.0016, 5-15 -0.0007 / -0.0011, 15-25 -0.0011 / -0.0001, 25-32 -0.0008 / -0.0010, 32+ +0.0008 / +0.0010. Signs flip between seasons in the first and last buckets; nothing is concentrated where a minutes feature should help.
- Extreme seeds show that the per-seed deltas are driven by noise in the tail rows: the five most negative 2023 seeds put -0.0100 on min<5 rows and -0.0076 on `wl_min_last` 0-5, the five most positive put +0.0113 and +0.0072 there. Same rows, opposite signs, depending on the seed. Short-minutes rows have the highest CRPS leverage, so a noisy fit there moves the total.
- Noise-column control (8 seeds per season): replace the five columns by a row-permuted copy of themselves (same marginal distribution, no link to the row), shuffled labels with the same seed, contrast with (CTRL, CTRL). 2023: mean -0.0011, sd 0.0021, fire 2/8; 2024: mean +0.0001, sd 0.0018, fire 1/8, opposite fire 1/8. The real-column arm on the same eight seeds: mean -0.0015, sd 0.0025, fire 2/8 (2023); mean -0.0003, sd 0.0021, fire 1/8 (2024). Columns with no information produce the same dispersion and the same fire rate. The dispersion comes from fitting a larger feature set to pure noise, not from the content of the columns.
- Independent permutations (heads seed s, pi seed s+1000) give the same picture: 2023 mean -0.0003, sd 0.0027, fire 12.5%; 2024 mean -0.0003, sd 0.0014, fire 16.7%. So the shared permutation index is not what creates the dispersion (it does not matter for the mean either).

## 4. Conclusion

- A fire at -0.0021 is consistent with chance under this construction. Estimated fire probability about 19% (pooled, observed rate), about 20 to 30% at the 2025 CI width.
- The construction is not shown to be biased for minutes-type features: the permutation mean is -0.0003 (se 0.0003), the extra columns give no rewardable forecast change, and meaningless columns reproduce the spread. What is wrong is the threshold: a fixed -0.001 plus a bootstrap CI treats a quantity whose own permutation sd is about 0.002 to 0.003 as if its only uncertainty were test-row sampling.
- Consequences: the placebo guard and the real-label margin were the informative evidence in T473 (FULL minus PL_FULL = -0.0112); the shuffle guard as frozen has roughly a 1-in-4 false-alarm rate in either direction on a single season, so it neither confirms nor refutes leakage by itself. This note changes no verdict.
- What this does not show: it uses pts only, seasons 2023 and 2024 only, one tree configuration. The 2025 season itself was not re-run (spent). Its permutation null could differ (its own CI half-width was narrower). The mean-zero finding is a null with se 0.0003, not a proof.

## 5. Recommendation for future guards (proposal only, nothing frozen)

1. Compare against a permutation null, not a fixed threshold. Run K >= 50 shuffles (cost here about 50 seconds each per season) on the SELECT season(s) before the rule is frozen, and freeze the fire threshold as a quantile of that null (for example the guard fails only if the real-label gain is below the 1st percentile of the null, or if |shuffled delta| exceeds 2 null sd). On the 2023/2024 null, 2 sd is about 0.0046, and the 2025 observation would not have fired.
2. Report the null sd next to the bootstrap CI and refuse to treat a within-fit game bootstrap as the guard's uncertainty; the quantity under test includes fit noise.
3. Make the guard a paired noise-control instead of CTRL vs FULL on shuffled labels: FULL vs the same arm with the five columns row-permuted (the noise-column control above), under REAL labels. That is the question the guard is trying to ask (does the gain come from the column content), it is the placebo logic already used (PL_FULL), and its null is centred and does not mix label noise into the comparison.
4. If shuffled labels are kept, average over several permutation seeds (the mean of K deltas has sd about 0.0023/sqrt(K)) and use the mean against a tolerance sized to that. Option to test (not tested here): shuffle residuals within player-season, which keeps per-player level and scale learnable (a harder, more realistic null for minutes features) but would change the calibration mix.
5. Express the Stage 1 vs rehearsal difference (-0.0004 vs +0.0037) as two draws from the same null (a seed sweep), not as a discrepancy to explain.

## Data gate and provenance

Rows per season (27,619 and 27,701) equal the logged Stage 1 / rehearsal n. Frame built from `nba.duckdb` read-only with `season <= 2024` filtered again in the script; heavy.lock created and removed by this run. Cost: about 36 minutes (fits) plus 6 minutes (noise-column control), one process. Verification outputs are in the hand-back.
