# Q1: why a better pi moves pts integer CRPS so little (descriptive, no claim)

Date: Mon 2026-10-12 (overnight loop item Q1). Seasons 2023 and 2024 only (asserted in code; 2025 never loaded). Read-only on stored
components (`reports/lower_tail/components_*.npz`) and the PI_RECENT_ROLE pi caches (`data/ops/pi_recent_role_cache`). Script:
`research/eval/pi_transfer_diag.py`; numbers: `reports/pi_transfer_diagnostic_2026-10-12.results.json`. n = 55,320 played rows (5,751 short
rows, 10.4%), game-clustered bootstrap CIs (1000 draws). Short flag = realised minutes < 0.5 x min10. All CRPS are integer-grid, 199-tau.

## Answer in plain words

The cap is the classifier, not the mixing formula and not the short component. The "3% transfer" is almost exactly the fraction of the
classification gap that CAND closed, and the transfer is a little better than proportional. The oracle is an upper bound that uses
realised minutes (unknowable at tip), so -0.148 is not a target any pre-tip pi can approach.

## (1) pi quality (pts rows, all 55,320; same pi for every stat)

| arm | log loss | Brier | AUC | mean pi |
|---|---|---|---|---|
| base rate (constant) | 0.3337 | 0.0932 | 0.500 | 0.104 |
| A0 (stored; CTRL cache is identical) | 0.2482 | 0.0739 | 0.8554 | 0.0914 |
| CAND (recent-role) | 0.2423 | 0.0721 | 0.8638 | 0.0914 |
| ORACLE (realised flag) | 0 | 0 | 1.000 | - |

CAND minus A0 log loss -0.00587 [-0.00699, -0.00473] (game-clustered). CAND closes 2.4% of the A0-to-oracle log-loss gap (and 2.4% of the
Brier gap). Its pts CRPS delta is -0.0053 [-0.0064, -0.0041] pooled (2023 -0.0059, 2024 -0.0046; reproduces T460), which is 3.6% of the
oracle's -0.1479 [-0.1551, -0.1402] (2023 -0.1501, 2024 -0.1457). Transfer per unit of Brier closed is therefore about 1.5x linear, not lost.

Interpolating pi = (1-t) A0 + t ORACLE (descriptive curve): t=0.1 gives log loss 0.191, AUC 0.946 and CRPS -0.0285; t=0.25 gives 0.141, 0.991,
-0.0655; t=0.5 gives 0.083, 1.0, -0.1121. CRPS gain tracks the log-loss gap closed (23% closed -> 19% of the gain; 43% -> 44%; 66% -> 76%). The
mixture is a smooth, roughly linear function of how much of the classification problem you solve. Oracle-like gains need AUC above 0.95.

## (2) Where the oracle gain lives (pts)

| piece | mean CRPS gain over A0 (per all rows) |
|---|---|
| ORACLE total | -0.1479 |
| from the 5,751 short rows (-1.219 per short row; A0 CRPS 2.10 -> 0.88) | -0.1267 (86%) |
| from the 49,569 non-short rows (-0.0236 per row; 3.281 -> 3.258) | -0.0211 (14%) |
| pi=oracle on short rows only (A0 pi elsewhere) | -0.1267 [-0.1337, -0.1196] |
| pi=0 on non-short rows only (A0 pi on short rows) | -0.0211 [-0.0238, -0.0185] |
| CAND: short rows / non-short rows | -0.0036 / -0.0017 |

Non-short gain comes from removing mixture contamination (the short component's mass pulling the lower quantiles down on rows that played
normally). Neither mixture-off nor a constant pi helps: pi=0 everywhere +0.0300 [+0.0263, +0.0335] worse than A0, constant base rate
+0.0239 [+0.0198, +0.0285] worse. So A0's whole classifier is worth about 0.024-0.030, the oracle another 0.148 on top.

Is w_q the cap? No. Oracle pi with an as-of empirical short distribution by role (earlier-block short rows only, starter x min10 tercile)
improves on the oracle with A0's w_q by only -0.0020 [-0.0026, -0.0014] (1.4% of the oracle gain). A leaking ceiling that uses realised minutes
inside the short component gets -0.0029 [-0.0035, -0.0022]. A better short component is worth at most 0.002-0.003 even at a perfect pi.

Other stats, same shape: reb CAND -0.0028 vs oracle -0.0605 (4.6%); ast -0.0012 vs -0.0240 (4.8%); fg3m -0.0006 vs -0.0130 (4.7%). In each,
about 81-85% of the oracle gain is on short rows.

## (3) Sensitivity of the mixture to pi (pts, by A0 pi bucket; constant pi grid 0..1)

| bucket (A0 pi) | n rows | mean A0 pi | observed short rate | CRPS-best constant pi on grid |
|---|---|---|---|---|
| 0: 0.005-0.031 | 27,660 | 0.014 | 0.015 | 0.03 |
| 1: 0.031-0.153 | 16,596 | 0.074 | 0.086 | 0.10 |
| 2: 0.153-0.400 | 8,298 | 0.257 | 0.300 | 0.20 |
| 3: 0.400-0.500 | 2,766 | 0.470 | 0.512 | 0.35 |

Short rows' CRPS falls steeply with pi (bucket 0: 7.40 at pi=0, 6.32 at 0.1, 3.20 at 0.5, 2.02 at 1) and non-short rows rise (3.76 -> 3.79 at 0.1 ->
5.37 at 0.5). The mixture CRPS is quadratic in pi, so near the right pi it is flat (bucket 1: 2.872 at 0.03, 2.862 at 0.10, 2.896 at 0.2 - a 3x
range in pi moves CRPS by 0.01), and a short row only gets large relief once pi is above about 0.3. This is a "flat near the optimum, steep far
from it" curve: a ranking improvement from AUC 0.855 to 0.864 shifts a few probabilities by a few points inside the flat zone. It is not that
the components overlap too much or w_q is mis-sized; the same curve with perfect labels is 20x steeper (pi jumps 0 -> 1 on the row that needs it).
Of the 5,751 short rows, 59% already have A0 pi > 0.2; the miss is the other 41% (bucket 0 and 1: 1,846 short rows) that look ordinary pre-tip.

## (4) Calibration of the short component (realised pts on short rows vs m_row x w_q)

The short rows' points are 61.9% exact zeros (median 0, mean 1.28, p90 4). Coverage P(y <= q) on short rows at the 10%, 50% and 90% quantile is
0.619, 0.619 and 0.892: the 10th and 50th quantiles are both 0 (integer point mass), so the "excess" is the zero mass, and the 90% quantile is
slightly generous (0.892 vs 0.90 is fine). By realised minutes the component is well sized for min<5 rows (n=3,957; 0.727 at q10/q50, 0.895 at q90),
somewhat wide for 5-10 minutes (n=1,434; 0.432, 0.879) and too narrow for 10+ minutes (n=360; 0.175, 0.914). The component is a fair
representation of the rows that matter and cannot be the cap (see also the 0.002 ceiling above).

## (5) Clipping and floors in the mixture path

`PI_CLIP = (0.005, 0.5)` in `predict_pi`; no further clip inside `mixture_quantiles`. A0 pi sits at the floor on 0.5% of rows and at the cap
on 2.2%. Bucket 3 (n=2,766) has an observed short rate of 0.51 against a mean pi of 0.47, with the best constant at 0.35-0.5, so the 0.5 cap is
not binding in CRPS terms. `MIN_CENTRE = 0.5` floors m in the ratio. The normal component uses only non-short calibration z (this is why
pi=0 on non-short rows is better than A0). The ORACLE arm in the harness feeds pi in {0,1} with no clip, so it is the ideal mixture
(weight 1 on the short component) and not a clipped one. Observed short rates exceed mean pi in every bucket (0.015/0.014, 0.086/0.074,
0.300/0.257, 0.512/0.470): pi is mildly under-predicted for CRPS use (best constants above the mean in low buckets), worth small second-order
amounts only (about 0.005-0.01 per bucket).

## Where the cap is

1. Classifier information. Short is mostly unknowable pre-tip (in-game injury, ejection, blowout rest). A0 AUC 0.855 and CAND 0.864 are near
   what the pre-tip features give; the oracle needs AUC 1.0. CAND does what a +0.009 AUC and 2.4% Brier gain should do: about -0.005.
2. Not the short component (0.002 ceiling), not clipping, not the mixing formula (linear in the gap closed).
3. So "genuinely little to gain at realistic pi accuracy": the floor -0.005 was cleared by closing 2.4% of the Brier gap; each additional 1%
   of Brier closed is about -0.0015 to -0.002 pts CRPS.

## Cheapest structural change to raise transfer (candidate rule, NOT run)

Re-fit the same pi on information that arrives after the 60-minute pre-tip frame - the T-30/T-15 confirmed lineup, starter flag and
late-scratch indicators (the stored T-15 arm in the daily pipeline already ships those columns) - and score with the unchanged mixture and w_q,
integer CRPS vs A0, pts primary, same 2023/2024 split, floor -0.005, game-clustered, family = 4 stats with BH. Because transfer is about
linear in Brier closed, the rule can state its own gate: pi must close at least 2.4% of the Brier gap (about AUC +0.009) before CRPS can
clear the floor, and it can be checked on the pi alone before any CRPS is computed. I could not verify here how much of the short flag the
confirmed lineup removes (a flagged-out player is not a "plays but short" row in the first place, so the flag's information may partly be in the
play/no-play head, not pi); this should be the first gate check. Architecture changes to the short component, the mixing formula or the clip
are not worth a rule (<= 0.003 combined by this evidence).

Not verified: no leakage audit of the CAND pi itself (T460 did that); the interpolation arms are descriptive shapes, not achievable classifiers;
CTRL pi cache is identical to stored A0 pi, so there is no separate control refit to compare.
