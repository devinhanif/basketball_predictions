# RECENT_ROLE_FULL Stage 2 results (season 2025, pts only, one run)

Rule sha256 73f3f4b0. Verdict: **NOT A PASS** (a pass is a proposal; Devin decides).
Expected before scoring: delta -0.007, pass probability [0.75, 0.82].

| arm | n rows | n games | integer CRPS / delta | CI | p | p BH | MDE |
|---|---|---|---|---|---|---|---|
| A0 | 28173 | 1321 | 3.144052 | | | | |
| FULL | 28173 | 1321 | 3.132501 | | | | |
| PL_FULL | 28173 | 1321 | 3.143662 | | | | |
| SHUF_CTRL | 28173 | 1321 | 3.292750 | | | | |
| SHUF_FULL | 28173 | 1321 | 3.290660 | | | | |
| FULL_minus_A0 | 28173 | 1321 | -0.01155 | [-0.01586, -0.00756] | 0.0005 | 0.0005 | 0.00415 |
| FULL_minus_PL_FULL | 28173 | 1321 | -0.01116 | [-0.01547, -0.00721] | 0.0005 |  | 0.00413 |
| SHUF_FULL_minus_SHUF_CTRL | 28173 | 1321 | -0.00209 | [-0.00359, -0.00061] | 0.0070 |  | 0.00149 |

| slice | n | FULL - A0 | CI |
|---|---|---|---|
| all | 28173 | -0.01155 | [-0.01586, -0.00756] |
| min<5 | 2277 | -0.08215 | [-0.09859, -0.06762] |
| bench | 16130 | -0.01931 | [-0.02549, -0.01348] |
| playoffs | 1921 | -0.02117 | [-0.03601, -0.00678] |
| cold_start | 485 | -0.03247 | [-0.06084, -0.00568] |
| starter | 12043 | -0.00116 | [-0.00584, 0.00364] |
| short(<0.5*proj) | 2888 | -0.09423 | [-0.11207, -0.07711] |
| not short | 25285 | -0.00211 | [-0.00637, 0.00165] |
| min<10 | 4257 | -0.05772 | [-0.06998, -0.04552] |

```json
{
 "coverage": {
  "A0": {
   "2025": {
    "0.10": 0.11340645298690236,
    "0.20": 0.2139637241330352,
    "0.80": 0.808291626734817,
    "0.90": 0.9054413800447236
   }
  },
  "FULL": {
   "2025": {
    "0.10": 0.11411635253611614,
    "0.20": 0.21446065381748483,
    "0.80": 0.8070138075462322,
    "0.90": 0.9047669754729706
   }
  }
 },
 "pi": {
  "A0": {
   "ece": 0.010714580791544602,
   "logloss": 0.24700321111121418,
   "auc": 0.852451738546224,
   "mean_pi": 0.09179491411492612
  },
  "FULL": {
   "ece": 0.010204513781451856,
   "logloss": 0.2405436475740713,
   "auc": 0.861526718675794,
   "mean_pi": 0.09272888426025462
  },
  "ece_delta": -0.0005100670100927455
 },
 "verdict": {
  "pass": false,
  "primary": true,
  "delta": -0.011551131922246959,
  "ci": [
   -0.015856548285945044,
   -0.007557972396585582
  ],
  "p_bh": 0.0004997501249375312,
  "mde": 0.004149287944679731,
  "floor": -0.005,
  "ece_ok": true,
  "coverage_ok": true,
  "coverage_bad": [],
  "slices_ok": true,
  "slices_worse": [],
  "placebo_ok": true,
  "shuffle_ok": false,
  "gate_ok": true,
  "note": "A pass is a production-change PROPOSAL only (Devin's decision); a miss is read against the stated expectation, not as a broken pipeline."
 }
}
```

Rule pass item 8 (planted-future / control plants) was NOT re-run on season 2025: the walk functions are unchanged since Stage 1, where the plants passed on them.
