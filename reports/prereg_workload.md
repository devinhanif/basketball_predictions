# WORKLOAD results

Rule: docs/prereg/WORKLOAD.md, frozen sha256 261fabce. Select 2023, report 2024, game-clustered 95% CI (2000 draws, seed 0), BH over the six primary cells per season.

| cell | season | n rows / games | base | candidate | delta [CI] | p_bh | B-PL | cov80 | bias/ECE |
|---|---|---|---|---|---|---|---|---|---|
| P (log loss) | 2023 | 33093 / 1318 | 0.2832 | 0.2665 | -0.0167 [-0.0191, -0.0143] | 0.001 | -0.0154 | nan | +0.0079 |
| P (log loss) | 2024 | 33127 / 1320 | 0.2815 | 0.2658 | -0.0157 [-0.0182, -0.0132] | 0.001 | -0.0129 | nan | +0.0084 |
| M (minutes CRPS) | 2023 | 27619 / 1318 | 3.4674 | 3.4165 | -0.0509 [-0.0594, -0.0421] | 0.001 | -0.0344 | 0.810 | +0.4220 |
| M (minutes CRPS) | 2024 | 27701 / 1320 | 3.4266 | 3.3648 | -0.0617 [-0.0708, -0.0528] | 0.001 | -0.0425 | 0.801 | +0.1258 |
| pts (integer CRPS) | 2023 | 27619 / 1318 | 3.1402 | 3.1331 | -0.0071 [-0.0110, -0.0035] | 0.001 | +0.0006 | 0.794 | -0.2800 |
| pts (integer CRPS) | 2024 | 27701 / 1320 | 3.1768 | 3.1686 | -0.0082 [-0.0119, -0.0048] | 0.001 | -0.0044 | 0.783 | -0.1775 |
| reb (integer CRPS) | 2023 | 27619 / 1318 | 1.2882 | 1.2865 | -0.0017 [-0.0033, -0.0001] | 0.032 | -0.0017 | 0.802 | -0.0760 |
| reb (integer CRPS) | 2024 | 27701 / 1320 | 1.3114 | 1.3068 | -0.0046 [-0.0060, -0.0032] | 0.001 | -0.0036 | 0.800 | -0.0682 |
| ast (integer CRPS) | 2023 | 27619 / 1318 | 0.9020 | 0.9000 | -0.0020 [-0.0031, -0.0008] | 0.001 | -0.0011 | 0.798 | -0.0747 |
| ast (integer CRPS) | 2024 | 27701 / 1320 | 0.8985 | 0.8964 | -0.0022 [-0.0033, -0.0011] | 0.001 | -0.0014 | 0.793 | -0.0680 |
| fg3m (integer CRPS) | 2023 | 27619 / 1318 | 0.5662 | 0.5657 | -0.0005 [-0.0013, +0.0002] | 0.155 | +0.0002 | 0.801 | -0.0151 |
| fg3m (integer CRPS) | 2024 | 27701 / 1320 | 0.5902 | 0.5897 | -0.0004 [-0.0011, +0.0003] | 0.252 | -0.0003 | 0.792 | -0.0012 |

## Verdict by cell

```json
{
 "cells": {
  "P": {
   "select_2023": true,
   "rule1_2024": true,
   "guards": {
    "no_slice_worse": true,
    "cold_start": true,
    "ece": true
   },
   "slices_worse": {},
   "beats_placebo": true,
   "oracle_bound_ok": null,
   "gate_ok": true,
   "knockout_drop": {
    "LOAD": {
     "gain_drop": 5.093521805738294e-05,
     "carries_by_drop": true
    },
    "SCHEDULE": {
     "gain_drop": -0.015124525238233044,
     "carries_by_drop": false
    },
    "TRAVEL": {
     "gain_drop": -0.015881383763410924,
     "carries_by_drop": false
    }
   },
   "tracking_claim": false,
   "pass_before_attack": true,
   "passed": false,
   "shuffled_ok": true,
   "one_family_carries": true
  },
  "M": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "no_slice_worse": true,
    "cold_start": true,
    "cov80": true,
    "bias": true
   },
   "slices_worse": {},
   "beats_placebo": false,
   "oracle_bound_ok": null,
   "gate_ok": true,
   "knockout_drop": {
    "LOAD": {
     "gain_drop": 0.0005389771972809473,
     "carries_by_drop": true
    },
    "SCHEDULE": {
     "gain_drop": -0.06255445132028795,
     "carries_by_drop": false
    },
    "TRAVEL": {
     "gain_drop": -0.061826271949009054,
     "carries_by_drop": false
    }
   },
   "tracking_claim": false,
   "pass_before_attack": false,
   "passed": false
  },
  "pts": {
   "select_2023": true,
   "rule1_2024": true,
   "guards": {
    "no_slice_worse": true,
    "cold_start": true,
    "cov80": true,
    "bias": true
   },
   "slices_worse": {},
   "beats_placebo": true,
   "oracle_bound_ok": true,
   "gate_ok": true,
   "knockout_drop": {
    "LOAD": {
     "gain_drop": -0.0002839713820898657,
     "carries_by_drop": true
    },
    "SCHEDULE": {
     "gain_drop": -0.008704342330394944,
     "carries_by_drop": false
    },
    "TRAVEL": {
     "gain_drop": -0.008033900658618478,
     "carries_by_drop": false
    }
   },
   "tracking_claim": false,
   "pass_before_attack": true,
   "passed": false,
   "shuffled_ok": true,
   "one_family_carries": true
  },
  "reb": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "no_slice_worse": true,
    "cold_start": true,
    "cov80": true,
    "bias": true
   },
   "slices_worse": {},
   "beats_placebo": true,
   "oracle_bound_ok": true,
   "gate_ok": true,
   "knockout_drop": {
    "LOAD": {
     "gain_drop": 6.492600227466353e-05,
     "carries_by_drop": true
    },
    "SCHEDULE": {
     "gain_drop": -0.003941937154658092,
     "carries_by_drop": false
    },
    "TRAVEL": {
     "gain_drop": -0.0037655659242677056,
     "carries_by_drop": false
    }
   },
   "tracking_claim": false,
   "pass_before_attack": false,
   "passed": false
  },
  "ast": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "no_slice_worse": true,
    "cold_start": true,
    "cov80": true,
    "bias": true
   },
   "slices_worse": {},
   "beats_placebo": false,
   "oracle_bound_ok": true,
   "gate_ok": true,
   "knockout_drop": {
    "LOAD": {
     "gain_drop": 1.621349671394202e-05,
     "carries_by_drop": true
    },
    "SCHEDULE": {
     "gain_drop": -0.002359749285739451,
     "carries_by_drop": false
    },
    "TRAVEL": {
     "gain_drop": -0.002032605115818017,
     "carries_by_drop": false
    }
   },
   "tracking_claim": false,
   "pass_before_attack": false,
   "passed": false
  },
  "fg3m": {
   "select_2023": false,
   "rule1_2024": false,
   "guards": {
    "no_slice_worse": true,
    "cold_start": true,
    "cov80": true,
    "bias": true
   },
   "slices_worse": {},
   "beats_placebo": false,
   "oracle_bound_ok": true,
   "gate_ok": true,
   "knockout_drop": {
    "LOAD": {
     "gain_drop": -0.00012007705091929784,
     "carries_by_drop": false
    },
    "SCHEDULE": {
     "gain_drop": 0.0001330999911000786,
     "carries_by_drop": true
    },
    "TRAVEL": {
     "gain_drop": 0.00019568503646698082,
     "carries_by_drop": true
    }
   },
   "tracking_claim": false,
   "pass_before_attack": false,
   "passed": false
  }
 },
 "close": false
}
```

## D3 oracle minutes (descriptive)

```json
{
 "pts": {
  "B_minus_A0": -0.008243718948939773,
  "A3W_minus_A3": -0.0009211811286045091,
  "reading": "through minutes"
 },
 "reb": {
  "B_minus_A0": -0.00458216334447795,
  "A3W_minus_A3": 6.255769773955144e-05,
  "reading": "no B gain"
 }
}
```

## Readings of ambiguous rule text

* Rule: Cells S: 'A0 = production + lower-tail mixture ... B = A0 + 27 columns appended through stat_feature_names'
  Reading: A0 is the stored-components mixture arm (3.140236 / 3.176840 for pts). B, the placebo, the knockouts and the no-tracking arm are the production model with extra columns, passed through the SAME lower-tail mixture (A0's stored row mixing weights `row_pi` and per-block quantile weights `wq_*`), so every S arm is on the mixture support. Raw (non-mixture) production is not an arm.
* Rule: Cell P comparator C = 'P1 (M2-p) + the report-tag-by-strength and morning/evening flags of PPLAY_CONTEXT (`build_terms`, without the b2b-habit terms)'
  Reading: C = BASE_FEATURES + TERM_TAG['T-60'] (terc, tag60_code, q60, d60, q60_x_terc, d60_x_terc) + TERM_REPORT (on_morning_report, on_evening_report). The b2b-habit terms team_b2b_sit, star and b2b_star are NOT in C or W.
* Rule: Placebo: 'the arm beats its family placebo by a point <= -0.003 (props, P) / -0.05 min (minutes)'
  Reading: One placebo arm (all 27 columns permuted: LOAD within player-season, SCHEDULE and TRAVEL within team-season). The condition is judged on the REPORT season 2024 (B - PL <= margin); 2023 is reported. Per-family placebos are not run.
* Rule: Pass rule 4: 'if removing one family removes >= 80% of the gain, or one family alone keeps >= 80%'
  Reading: Gains are 2024 mean differences vs the base. Removal arms (KO_LOAD, KO_SCHEDULE, KO_TRAVEL) run for every cell in `arms`; the 'family alone' arms (AL_*) run in `attack` for cells that pass rules 1-3 only. Flag 'one family carries it' = any KO leaves <= 20% of the full gain OR any AL keeps >= 80%.
* Rule: 'LOAD-without-tracking (LOAD_MIN only)' and 'A tracking claim needs LOAD(full) - LOAD_MIN <= floor/2 on the same rows'
  Reading: NT = the full candidate minus the 7 LOAD_TRACK columns (LOAD_MIN, SCHEDULE and TRAVEL stay). Tracking claim holds when 2024 mean(B - NT) <= floor/2 for the cell. Descriptive unless a cell otherwise passes.
* Rule: Pass rule 3: 'the arm does not beat its own oracle bound (A3 for props, realised minutes for M)'
  Reading: Props: B's 2024 mean CRPS must exceed the stored A3 (production + realised minutes, reports/minutes_v2/props_<stat>_A3.npz) mean on the same season. P and M: not applicable (an oracle for realised minutes is zero CRPS; for P(play) the realised outcome); recorded as n/a, not as a pass.
* Rule: 'shuffled-label arm gives an effect >= -MDE'
  Reading: Labels permuted within the training set of each month block (props: residuals; P: played flag; M: minutes). MDE is the half-width of the cell's own 2024 B-vs-base CI. Shuffled arm passes if its 2024 mean effect >= -MDE.
* Rule: Star slice 'as-of top-3 team minutes per appearance over 10 games'
  Reading: Slice only (never a feature): the three highest as-of `min10` (cell P: avg minutes given played) among the scored rows of the team-game. Starter = as-of start rate >= 0.5 (starter10; cell P starter_rate_prior).
* Rule: M2 reproduction tolerance (the rule requires A0 to reproduce; M2 and C are new fits)
  Reading: M2 (refit here) is compared with the stored reports/minutes_v2/minutes_scores.npz M2 CRPS on the rows both have (stored 2024 has 27,583 rows, the data now 27,701, so the comparison is on the key intersection, >= 99% of rows required): 2023 within 1e-6 min, 2024 within 0.005 min (PPLAY_CONTEXT's frozen amendment for the 12-game data clean-up). A failure stops `arms` before any candidate arm is fitted. C has no stored reference and is not reproduced.
* Rule: Select 2023, report 2024 and BH
  Reading: Rule 1 for 2023 has no BH requirement; for 2024 BH p <= 0.05 over the six primary cells {P, M, pts, reb, ast, fg3m} (candidate vs base), computed within each season. Two-sided clustered-bootstrap p (2000 draws, seed 0, cluster = game_id).
* Rule: Planted tests and missingness audit 'on the frozen frames'
  Reading: `gate` audits the 55,320 A0 rows (props/minutes rows) and the cell-P rows (including DNP as its own bucket) with threshold max(2 pp, 3 x placebo); planted same-game / planted-future tests run on the REAL played table with non-inert controls. Any failure stops everything (kill criterion).
* Rule: Decision score D2
  Reading: Descriptive only: DraftKings T-60 legs (nba.markets.decision_scoring) re-priced with each S cell's A0 and B integer grid. Errors are recorded, never fatal.
* Rule: D3 oracle-minutes pair for pts and reb
  Reading: A3 = A0 features + realised minutes (`minutes_oracle`) and A3W = A3 + 27 columns, both through the mixture; the verdict line is 'effect runs through minutes' if B beats A0 but A3W - A3 > floor/2, else flagged.
