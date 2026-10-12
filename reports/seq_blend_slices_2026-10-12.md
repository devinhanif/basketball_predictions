# SEQ blend without the gate, guard slices, and overlap with RECENT_ROLE_FULL (DESCRIPTIVE)

Mon Oct 12 2026 (times from `date`). Approved by Devin (DECISIONS e24b56c, item A). Seasons 2023 / 2024 only, stored arrays only (SEQ OOF `data/colab/runs/seq_ensemble/20261011_215342`, lower-tail components, ROUTER_G2 `gates.npz`, RECENT_ROLE_FULL `heads_*_R` / `pi_R` caches). Season 2025 never loaded. The only computation beyond scoring is the existing walk-forward `learned_blend` re-run on the stored members. **Nothing here is a claim or a rule; no rule was frozen; every arm below was chosen after seeing 2023/2024, so none can pass anything on these seasons.** Harness: `research/eval/seq_blend_slices.py` (about 50 s); numbers: `reports/seq_blend_slices_2026-10-12/results.json`.

Format: 2023 / 2024. n rows 27,619 / 27,701 (games clustered for every CI, 2000 draws, seed 0, 95% percentile). CRPS is integer-support, difference is arm minus A0, negative is better. Floor for reference -0.005.

Arms. **A0** production + stored lower-tail mixture. **SEQc** T442's learned blend of [A0 mixture, SEQ-E2], used on every regular-season row (game_id prefix 002), postseason and play-in rows take A0 (this is the constant-gate arm T470 said was never guard-checked). **E2'** the same blend used only where ROUTER_G2's g_eff >= 0.90 (T464; reproduces -0.01105 / -0.01176). **FULL** RECENT_ROLE_FULL (heads and pi refit with the recent-role columns), all rows. **AVG** 50/50 pmf mixture of SEQc and FULL (no fitted weight). **AVG_gated** 50/50 of E2' and FULL. **FSc** learned blend of [FULL continuous mixture, SEQ-E2] on regular rows, postseason takes FULL.

## 1. Points: CRPS vs A0 and every ROUTER_G2 guard

| arm | CRPS - A0 [95% CI] 2023 ; 2024 | minutes<5 slice (n 2,383 / 2,326) | bench | cold start | postseason | 5% quantile coverage (nominal .05) | ROUTER_G2 guards failed |
|---|---|---|---|---|---|---|---|
| SEQc (constant gate) | -0.0108 [-0.0145, -0.0068] ; -0.0143 [-0.0180, -0.0105] | +0.041 / +0.039 | -0.005 / -0.018 | +0.004 / -0.019 | +0.000 / +0.000 | 0.073 / 0.070 | calibration,minutes<5 / minutes<5 |
| E2' (gated) | -0.0111 [-0.0147, -0.0072] ; -0.0118 [-0.0151, -0.0084] | +0.003 / +0.003 | -0.005 / -0.013 | +0.003 / -0.008 | +0.000 / +0.000 | 0.070 / 0.069 | none / none |
| FULL | -0.0084 [-0.0123, -0.0045] ; -0.0083 [-0.0122, -0.0043] | -0.055 / -0.054 | -0.012 / -0.011 | -0.010 / -0.018 | -0.029 / +0.004 | 0.060 / 0.062 | none / none |
| AVG = SEQc + FULL | -0.0184 [-0.0211, -0.0153] ; -0.0181 [-0.0210, -0.0151] | -0.021 / -0.021 | -0.017 / -0.021 | -0.010 / -0.023 | -0.019 / -0.004 | 0.064 / 0.065 | none / none |
| AVG_gated = E2' + FULL | -0.0179 [-0.0206, -0.0149] ; -0.0163 [-0.0192, -0.0134] | -0.038 / -0.037 | -0.016 / -0.018 | -0.009 / -0.017 | -0.019 / -0.004 | 0.063 / 0.064 | none / none |
| FSc learned [FULL, SEQ] | -0.0176 [-0.0223, -0.0127] ; -0.0197 [-0.0241, -0.0151] | +0.002 / -0.008 | -0.015 / -0.025 | -0.003 / -0.030 | -0.029 / +0.004 | 0.071 / 0.070 | calibration / none |

Guard names: g1_calibration (|bias|<=0.5, 80% coverage in [.75,.85], coverage within .02 at .05-.95), g2_slices (no slice n>=300 worse than A0 by >0.01 among regular, postseason, starter, bench, cold start, first 15, teammate OUT), g2b_minutes<5, g3_concentration (top team <=25% of gain, delta without it <=-0.0025), g4_thresholds (DK T-60 and fixed 20/25/30 log loss), g5_proxy_safe (gain <= -0.005 on tip hour >= 19 ET). Slice values are mean CRPS difference vs A0 (positive = worse; guard limit +0.010).

- SEQc 2023: bias -0.202, 80% coverage 0.784, top-team share 0.130, delta without top team -0.0097, DK T-60 log loss -0.0026 (n 14354), proxy-safe slice -0.0093.
- SEQc 2024: bias -0.170, 80% coverage 0.783, top-team share 0.111, delta without top team -0.0131, DK T-60 log loss -0.0015 (n 15882), proxy-safe slice -0.0116.
- AVG 2023: bias -0.189, 80% coverage 0.791, top-team share 0.075, delta without top team -0.0175, DK T-60 log loss -0.0028 (n 14354), proxy-safe slice -0.0168.
- AVG 2024: bias -0.142, 80% coverage 0.785, top-team share 0.075, delta without top team -0.0173, DK T-60 log loss -0.0020 (n 15882), proxy-safe slice -0.0158.

### Reading
- **T470 is confirmed.** The SEQ blend on all regular-season rows gains -0.0108 / -0.0143 on points (twice the floor) but gives back +0.041 / +0.039 on the rows with under 5 realised minutes (guard limit +0.010) and fails the 5%-quantile coverage check in 2023 (7.3% of outcomes below the 5% quantile). Routing postseason to A0 alone does not make it guard-clean; the gate is what held that slice (E2': +0.003). The gate adds nothing to CRPS (E2' minus SEQc -0.0003 / +0.0025, i.e. the gate is slightly worse on CRPS in 2024) but it is what makes the arm usable. Cold start in 2023 is +0.0036 for SEQc (inside the guard).
- FULL has the opposite profile: it gains most on the near-DNP rows (-0.055 / -0.054) and is weaker on rows with 5+ minutes. That complementarity is the whole story of section 3.

## 2. Count stats briefly (CRPS vs A0, 2023 ; 2024)

| stat | SEQc | E2' | FULL | AVG | FSc | SEQc guards failed (23 / 24) | AVG guards failed |
|---|---|---|---|---|---|---|---|
| reb | -0.0025 / -0.0042 | -0.0028 / -0.0029 | -0.0025 / -0.0036 | -0.0053 / -0.0062 | -0.0049 / -0.0069 | concentration,minutes<5 / minutes<5 | none / none |
| ast | -0.0015 / -0.0026 | -0.0017 / -0.0020 | -0.0020 / -0.0020 | -0.0036 / -0.0037 | -0.0030 / -0.0040 | concentration / concentration | none / none |
| fg3m | -0.0026 / -0.0032 | -0.0023 / -0.0026 | -0.0011 / -0.0000 | -0.0032 / -0.0027 | -0.0036 / -0.0032 | concentration / none | none / none |

All count-stat gains are below the -0.005 floor in size except AVG on reb (-0.0053 / -0.0062, at or past it; descriptive, post-hoc). The `g3_concentration` failure on SEQc for count stats is the delta-without-top-team <= -0.0025 test, which a gain of -0.0025 to -0.0033 cannot meet by arithmetic; it is not evidence of concentration (top-team shares are 8-12%). The g2b near-DNP failure appears for reb in both seasons (+0.015).

## 3. Overlap with RECENT_ROLE_FULL

Correlation of per-row CRPS gains over A0 (A0 minus arm), SEQc vs FULL, Pearson, game-clustered bootstrap CI (1000 draws):

| stat | all rows 2023 | all rows 2024 | rows with minutes>=5 2024 | rows with minutes<5 2024 |
|---|---|---|---|---|
| pts | 0.135 [0.115, 0.153] | 0.187 [0.164, 0.208] | 0.198 [0.175, 0.221] | 0.093 [0.029, 0.164] |
| reb | 0.121 [0.099, 0.141] | 0.171 [0.147, 0.197] | 0.184 [0.157, 0.213] | 0.105 [0.031, 0.179] |
| ast | 0.141 [0.117, 0.164] | 0.215 [0.192, 0.239] | 0.220 [0.195, 0.244] | 0.157 [0.064, 0.246] |
| fg3m | 0.136 [0.116, 0.158] | 0.184 [0.163, 0.205] | 0.187 [0.165, 0.209] | 0.125 [0.053, 0.198] |

The gains are nearly uncorrelated (r 0.12-0.22), so the two carry different information: SEQ improves rows that play, FULL improves who barely plays.

Does SEQ add anything once FULL is in? Points, CRPS difference with game-clustered CI (2023 ; 2024):

| contrast | pts | reb | ast | fg3m |
|---|---|---|---|---|
| AVG - A0 | -0.0184 / -0.0181 | -0.0053 / -0.0062 | -0.0036 / -0.0037 | -0.0032 / -0.0027 |
| AVG - FULL (what SEQ adds to FULL) | -0.0100 / -0.0098 | -0.0028 / -0.0026 | -0.0016 / -0.0017 | -0.0021 / -0.0026 |
| AVG - SEQc (what FULL adds to SEQ) | -0.0076 / -0.0038 | -0.0028 / -0.0020 | -0.0021 / -0.0011 | -0.0006 / +0.0005 |
| FSc - FULL (learned blend, SEQ on top of FULL) | -0.0092 / -0.0114 | -0.0024 / -0.0034 | -0.0010 / -0.0020 | -0.0026 / -0.0032 |
| AVG_gated - FULL | -0.0095 / -0.0080 | -0.0027 / -0.0018 | -0.0016 / -0.0014 | -0.0019 / -0.0023 |
| AVG - (SEQc 50/50 with A0): FULL vs an uninformative partner | -0.0077 / -0.0072 | -0.0028 / -0.0032 | -0.0020 / -0.0019 | -0.0011 / -0.0004 |
| AVG - (SEQc 50/50 with FULL placebo) | -0.0074 / -0.0067 | -0.0026 / -0.0025 | -0.0014 / -0.0016 | -0.0008 / -0.0005 |

Points with CIs: AVG - FULL -0.0100 [-0.0126, -0.0073] ; -0.0098 [-0.0124, -0.0072]; FSc - FULL -0.0092 [-0.0131, -0.0053] ; -0.0114 [-0.0147, -0.0080]; AVG - SEQc -0.0076 [-0.0103, -0.0050] ; -0.0038 [-0.0064, -0.0012].

Yes, SEQ adds a lot on points once FULL is in: -0.0100 / -0.0098 (five times the floor each season, CI upper -0.0073 / -0.0072), and the gain is close to additive (SEQc -0.0108 / -0.0143 plus FULL -0.0084 / -0.0083 against AVG -0.0184 / -0.0181). On the count stats SEQ adds -0.0026 to -0.0028 (reb), -0.0016 / -0.0017 (ast), -0.0021 / -0.0026 (fg3m): real by CI, below the floor. Part of any 50/50 mixture's gain is the mixture itself, so the control matters: SEQc mixed 50/50 with A0 (no new information) reaches -0.0107 / -0.0109 and mixed with FULL's past-only placebo -0.0110 / -0.0114 (the placebo alone is +0.0017 / +0.0003 vs A0); replacing the partner with the real FULL moves it to -0.0184 / -0.0181, i.e. FULL's recent-role information is worth -0.0077 / -0.0072 [-0.0096, -0.0057 ; -0.0093, -0.0052] on top. On the near-DNP rows AVG is -0.021 / -0.021 (SEQc alone +0.041 / +0.039), so FULL repairs the slice that the gate had to repair.

FSc (learned blend of [FULL, SEQ]) gets -0.0176 / -0.0197 on points but fails the 2023 coverage guard (the 5% quantile again) (disclosure: a first run of this script used FULL's integer grid as the blend's first stage; the gate then collapsed to FULL on reb/ast/fg3m in 2024, a support artefact, T114-T118 style; I replaced it with the continuous mixture grid, which is T442's own first-stage support, before reading any conclusion, and only the continuous version is reported). AVG has no fitted parameter and passes every guard in all four stats and both seasons, so it is the cleaner reading.

## 4. Recommendation (plain words)

**Yes, the SEQ network is worth its single 2025 check, but only as an addition to RECENT_ROLE_FULL, not as a stand-alone or gated replacement for A0.**

1. On its own, SEQ is a genuine -0.011 to -0.014 points gain that cannot ship ungated: it loses 0.04 CRPS on near-DNP rows and misses the 5%-quantile coverage check. The ROUTER_G2 gate fixed that, but it is a lot of machinery (a second LightGBM walk) for no CRPS gain.
2. FULL already does what the gate was for (it gains most on rows that barely play), and SEQ gains where FULL does not (r about 0.14-0.19). Their 50/50 pmf mixture gains -0.0184 / -0.0181 on points (CI upper -0.0153 / -0.0152), with every ROUTER_G2 guard passing in both seasons on all four stats and a flat bench / starter / cold-start / postseason profile. No gate, no learned weight on top of E2's existing blend.
3. **Exact construction to pre-register:** AVG = 0.5 * pmf(SEQc) + 0.5 * pmf(FULL), integer grid, where SEQc is E2's learned walk-forward blend of [A0 mixture, SEQ-E2] on regular-season rows with postseason and play-in rows taking A0, and FULL is RECENT_ROLE_FULL exactly as frozen. Primary cell: points, floor -0.005, comparator A0 and ALSO FULL (SEQ's marginal over FULL, which is the question; floor -0.005 on AVG - FULL), game-clustered CI, both seasons; family of four stats with BH, count stats expected below floor. Required controls: a past-only SEQ placebo in the same mixture, AVG vs SEQc-with-A0 (the mixture effect), SHUF-C. Guards as ROUTER_G2 (incl. minutes<5 and postseason).
4. **Honest limits.** Everything above is post-hoc on the two seasons that a new rule would use as select and report: I looked at six constructions here (SEQc, E2', FULL, AVG, AVG_gated, FSc) and the 50/50 weight and "mixture not learned blend" were picked after seeing them, so a pass on 2023/2024 would be a selection among arms, not a clean test; the one real out-of-sample test is the 2025 touch. SEQ 2025 needs a Colab run (cost, notebook link, full budgets; Devin's call), and FULL's own 2025 touch is a separate decision. A part of the SEQc-FULL gain is a mixture/dispersion effect (SEQc + A0 alone gives -0.0107), which the AVG - FULL contrast and the placebo-partner row separate from information but a formal control must repeat on a frozen rule. The count stats do not justify the SEQ check on their own (reb is the only one near the floor).
5. If Devin prefers not to spend the Colab run: do not ship SEQc ungated; E2' stays the only guard-clean SEQ form and it already closed (T466) with reb/ast/fg3m below floor.
